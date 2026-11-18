"""Marks on an IMAGE file: its views judged by the scale they were sent at,
and a copy of another image under the same name (app BACKLOG VIS-26).

Offline, with made-up pictures drawn here and a test host whose
``ImageSupport`` decodes them with Pillow (the app's does the same).
"""

from __future__ import annotations

import io
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")
Image = pytest.importorskip("PIL.Image")

from planlens import review  # noqa: E402
from planlens.review import marked_copy  # noqa: E402
from planlens.review.tests.conftest import (_close_toolkits,  # noqa: E402
                                            make_tools)
from planlens.testing import build_synthetic_review_document  # noqa: E402

SIGNER = "Test Reviewer via ReviewApp (AI draft)"


class _Picture:
    def __init__(self, image, data):
        self.image = image
        self.width, self.height = image.size
        self.data = data


def _open_image(data, name):
    from PIL import ImageOps
    im = ImageOps.exif_transpose(Image.open(io.BytesIO(bytes(data))))
    return _Picture(im.convert("RGB"), bytes(data)), None


def _as_pdf(picture):
    buf = io.BytesIO()
    picture.image.save(buf, format="PNG")
    doc = pymupdf.open()
    page = doc.new_page(width=picture.width, height=picture.height)
    page.insert_image(page.rect, stream=buf.getvalue(), keep_proportion=False)
    return doc.tobytes()


def _images(sent_scale=None):
    return review.ImageSupport(open_image=_open_image, as_pdf=_as_pdf,
                               sent_scale=sent_scale)


def _png(w, h, colour="white"):
    im = Image.new("RGB", (w, h), colour)
    for x in range(w // 3, w // 3 + 20):          # something to mark
        for y in range(h // 3, h // 3 + 12):
            im.putpixel((x, y), (0, 0, 0))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    return buf.getvalue()


def _jpeg_turned(w, h):
    """A JPEG stored ``w`` x ``h`` whose EXIF says it is shown turned a
    quarter clockwise (orientation 6), as a phone held upright writes."""
    im = Image.new("RGB", (w, h), "white")
    ex = Image.Exif()
    ex[0x0112] = 6
    buf = io.BytesIO()
    im.save(buf, format="JPEG", exif=ex.tobytes())
    return buf.getvalue()


@pytest.fixture
def folder(tmp_path):
    work = tmp_path / "conv" / "files"
    work.mkdir(parents=True)
    return work


def _annotate(folder, name, data, markups, images, **kw):
    tools = make_tools(None, folder, {name: data}, signer=SIGNER,
                       images=images)
    args = {"handle": name, "markups": markups, **kw}
    return json.loads(tools["annotate_document"].invoke(args))


#: A small thing (48 x 36 px) boxed on the whole 1200 x 900 image's view.
WIDE = {"kind": "circle", "page": 0, "comment": "check this",
        "view": [0, 0, 1200, 900], "image_box": [333, 333, 373, 373]}


def test_a_wide_image_view_sent_at_full_resolution_places_the_mark(folder):
    out = _annotate(folder, "shot.png", _png(1200, 900), [WIDE],
                    _images(lambda w, h: 1.0))
    assert out.get("n_written") == 1, out
    x0, y0, x1, y1 = out["written"][0]["bbox"]
    # round the box the view + image_box name (400, 300, 448, 336 in the
    # image's pixels), as the writer draws a circle round any box
    assert (x0 + x1) / 2 == pytest.approx(424.0, abs=1.5), out["written"][0]
    assert (y0 + y1) / 2 == pytest.approx(318.0, abs=1.5), out["written"][0]
    assert x0 <= 400.0 and x1 >= 448.0 and y0 <= 300.0 and y1 >= 336.0


def test_a_wide_image_view_sent_shrunk_is_refused_in_pixels(folder):
    out = _annotate(folder, "shot.png", _png(1200, 900), [WIDE],
                    _images(lambda w, h: 0.5))
    assert out.get("nothing_written") is True, out
    why = out["skipped"][0]["reason"]
    assert "1200 x 900 px view of the image" in why
    assert "shrunk to 0.50" in why and "full resolution" in why
    assert " pt " not in why


def test_with_no_scale_from_the_host_the_width_rule_stands_in_pixels(
        folder):
    out = _annotate(folder, "shot.png", _png(1200, 900), [WIDE], _images())
    assert out.get("nothing_written") is True, out
    why = out["skipped"][0]["reason"]
    assert "1200 x 900 px view" in why and "300 px or less" in why
    assert " pt " not in why


def test_a_narrow_image_view_is_never_refused_for_its_width(folder):
    zoom = {**WIDE, "view": [300, 250, 600, 450],
            "image_box": [300, 300, 400, 400]}
    out = _annotate(folder, "shot.png", _png(1200, 900), [zoom],
                    _images(lambda w, h: 0.1))
    assert out.get("n_written") == 1, out


def test_a_whole_view_image_box_is_refused_in_pixels(folder):
    whole = {**WIDE, "view": [300, 250, 600, 450],
             "image_box": [0, 0, 999, 999]}
    out = _annotate(folder, "shot.png", _png(1200, 900), [whole],
                    _images(lambda w, h: 1.0))
    assert out.get("nothing_written") is True, out
    why = out["skipped"][0]["reason"]
    assert why.startswith("image_box is the whole 300 x 200 px view"), why


def test_an_anchor_object_is_judged_and_converted_too(folder):
    nested = {"kind": "box", "page": 0, "comment": "here",
              "anchor": {"view": WIDE["view"],
                         "image_box": WIDE["image_box"]}}
    out = _annotate(folder, "shot.png", _png(1200, 900), [nested],
                    _images(lambda w, h: 1.0))
    assert out.get("n_written") == 1, out


def test_judge_image_views_keeps_indices_and_leaves_others_alone():
    marks = [{"kind": "note", "page": 0, "comment": "a", "point": [5, 5]},
             dict(WIDE),
             {"kind": "callout", "page": 0, "comment": "c",
              "points_at": [10, 10], "view": [0, 0, 1200, 900],
              "image_box": [0, 0, 20, 20]}]
    out, reasons = marked_copy.judge_image_views(marks, lambda w, h: 1.0)
    assert out[0] is marks[0] and out[2] is marks[2]
    assert "view" not in out[1] and "image_box" not in out[1]
    assert out[1]["bbox"][0] == pytest.approx(400.0, abs=2)
    assert reasons == {}
    out, reasons = marked_copy.judge_image_views(marks, lambda w, h: 0.4)
    assert out[1] is marks[1] and list(reasons) == [1]


def test_a_pdf_page_is_judged_exactly_as_before(folder, tmp_path):
    """A PDF's marks never meet the image rule: the same wide-view mark is
    refused with the writer's own words, whatever the host's scale says,
    and the result is the same with and without a scale."""
    pdf = build_synthetic_review_document().pdf
    mark = {**WIDE, "view": [0, 0, 612, 792]}
    outs = []
    for i, images in enumerate((None, _images(lambda w, h: 1.0))):
        work = tmp_path / f"c{i}" / "files"
        work.mkdir(parents=True)
        _close_toolkits()
        outs.append(_annotate(work, "set.pdf", pdf, [mark], images))
    assert outs[0] == outs[1]
    assert " pt box was read off a 612 x 792 pt view" in \
        outs[0]["skipped"][0]["reason"]


# -- A copy of another image under the same name ----------------------------

def _one_mark(w, h):
    return [{"kind": "box", "page": 0, "comment": "here",
             "bbox": [w // 3, h // 3, w // 3 + 20, h // 3 + 12]}]


def test_a_second_image_under_the_same_name_gets_its_own_copy(folder):
    first = _annotate(folder, "photo.png", _png(800, 600), _one_mark(800, 600),
                      _images())
    assert first.get("n_written") == 1, first
    copy = folder / "photo_marked.pdf"
    before = copy.read_bytes()
    _close_toolkits()
    # A second upload under the same name, twice the size (same shape: the
    # old copy's page would have been taken as this image at 0.5 pt a px).
    second = _annotate(folder, "photo.png", _png(1600, 1200, "lightgrey"),
                       _one_mark(1600, 1200), _images())
    assert second.get("n_written") == 1, second
    assert second["output_path"].endswith("photo_marked_2.pdf"), second
    assert "marked copy of another image" in second["saved_elsewhere"]
    assert "1600 x 1200 px" in second["saved_elsewhere"]
    assert copy.read_bytes() == before
    with pymupdf.open(str(folder / "photo_marked_2.pdf")) as doc:
        page = doc[0]
        assert (page.rect.width, page.rect.height) == (1600, 1200)
        r = [a.rect for a in page.annots()][0]
        assert (r.x0 + r.x1) / 2 == pytest.approx(543, abs=6)
        assert (r.y0 + r.y1) / 2 == pytest.approx(406, abs=6)
    # the same image again adds to its own copy, not the first one's
    _close_toolkits()
    third = _annotate(folder, "photo.png", _png(1600, 1200, "lightgrey"),
                      _one_mark(1600, 1200), _images())
    assert third["output_path"].endswith("photo_marked_2.pdf"), third
    assert third.get("appended_to_existing") is True


def test_remove_on_a_copy_of_another_image_writes_nothing(folder):
    _annotate(folder, "photo.png", _png(800, 600), _one_mark(800, 600),
              _images())
    copy = folder / "photo_marked.pdf"
    before = copy.read_bytes()
    _close_toolkits()
    out = _annotate(folder, "photo.png", _png(1600, 1200, "lightgrey"),
                    _one_mark(1600, 1200), _images(),
                    output_path="photo_marked.pdf", remove=["p0.m0"])
    assert "marked copy of another image" in out.get("error", ""), out
    assert copy.read_bytes() == before


def test_a_copy_fits_its_image_however_the_turn_was_stored(tmp_path):
    data = _jpeg_turned(400, 300)
    picture, _ = _open_image(data, "phone.jpg")
    assert (picture.width, picture.height) == (300, 400)
    # planlens' own page of the photo (MuPDF applies the EXIF turn; the
    # image is held as stored) and the host's upright copy both fit
    img = pymupdf.open(stream=data)
    own = tmp_path / "own.pdf"
    own.write_bytes(img.convert_to_pdf())
    upright = tmp_path / "upright.pdf"
    upright.write_bytes(_as_pdf(picture))
    assert marked_copy._copy_fits_image(str(own), picture)
    assert marked_copy._copy_fits_image(str(upright), picture)
    other, _ = _open_image(_png(400, 300), "other.png")
    assert not marked_copy._copy_fits_image(str(upright), other)
    bigger, _ = _open_image(_png(600, 800), "bigger.png")
    assert not marked_copy._copy_fits_image(str(upright), bigger)
