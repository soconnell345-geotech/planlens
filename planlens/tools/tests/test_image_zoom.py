"""Zoom on what was seen: a box read off an image goes back onto its page.

The loop a vision model runs — look at a page, spot something, zoom on it —
needs the model's box (in the image it looked at) turned into page points.
The toolkit remembers what every image it rendered shows, so render_region
takes ``image`` + ``image_box`` in the convention the model's family reads
locations in, and the budget makes the image the model saw the image we hold.
"""

import json

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document.budget import BUDGETS  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

UPLOAD = "review.pdf"


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def make_kit(gt, tmp_path, **kw):
    return ReviewToolkit(resolve_source=lambda key: gt.pdf,
                         output_dir=str(tmp_path / "img"), **kw)


@pytest.fixture
def kit(gt, tmp_path):
    k = make_kit(gt, tmp_path, image_budget="openai-original",
                 image_format="jpeg")
    yield k
    k.close()


def call(kit, name, **args):
    return json.loads(kit.call_json(name, args))


def to_px(point, out):
    """Where a page point lands on a rendered image, in its pixels."""
    x0, y0, x1, y1 = out["clip"]
    return ((point[0] - x0) * out["width_px"] / (x1 - x0),
            (point[1] - y0) * out["height_px"] / (y1 - y0))


def test_a_budgeted_render_is_jpeg_and_says_how_to_zoom(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "render_page", handle=handle, page=gt.sheet_page)
    assert out["image_path"].endswith(".jpg")
    with open(out["image_path"], "rb") as fh:
        assert fh.read(3) == bytes([0xFF, 0xD8, 0xFF])
    assert BUDGETS["openai-original"].fits(out["width_px"], out["height_px"])
    assert f"{out['width_px']}x{out['height_px']} px" in out["note"]
    assert "box_units='norm1000'" in out["note"]
    assert out["budget"] == "openai-original"


def test_a_grid_box_zooms_on_the_spot(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    page = call(kit, "render_page", handle=handle, page=gt.sheet_page)
    tx, ty = gt.reviewer_target
    x0, y0, x1, y1 = page["clip"]
    g = lambda v, lo, hi: (v - lo) / (hi - lo) * 999.0
    box = [g(tx - 30, x0, x1), g(ty - 30, y0, y1),
           g(tx + 30, x0, x1), g(ty + 30, y0, y1)]
    out = call(kit, "render_region", image=page["image_path"], image_box=box,
               box_units="norm1000")
    assert out["handle"] == handle and out["page"] == gt.sheet_page
    assert out["bbox"] == pytest.approx([tx - 30, ty - 30, tx + 30, ty + 30],
                                        abs=0.05)
    assert out["dpi"] > page["dpi"]


def test_a_pixel_box_from_a_zoom_zooms_again(kit, gt):
    """Zoom on a zoom: the second box is in the FIRST zoom's pixels."""
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    tx, ty = gt.reviewer_target
    first = call(kit, "render_region", handle=handle, page=gt.sheet_page,
                 bbox=[tx - 200, ty - 200, tx + 200, ty + 200])
    (ax, ay), (bx, by) = to_px((tx - 10, ty - 10), first), to_px((tx + 10, ty + 10), first)
    second = call(kit, "render_region", image=first["image_path"],
                  image_box=[ax, ay, bx, by], box_units="px")
    assert second["bbox"] == pytest.approx([tx - 10, ty - 10, tx + 10, ty + 10],
                                           abs=0.05)
    assert second["dpi"] >= first["dpi"]


def test_the_default_box_units_follow_the_budget(gt, tmp_path):
    k = make_kit(gt, tmp_path, image_budget="claude")
    try:
        handle = call(k, "open_document", source=UPLOAD)["handle"]
        page = call(k, "render_page", handle=handle, page=gt.sheet_page)
        assert "box_units='px'" in page["note"]
        w, h = page["width_px"], page["height_px"]
        out = call(k, "render_region", image=page["image_path"],
                   image_box=[0, 0, w, h])                 # no box_units: px
        assert out["bbox"] == pytest.approx(page["clip"], abs=0.1)
    finally:
        k.close()


def test_image_box_mistakes_are_instructions(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    page = call(kit, "render_page", handle=handle, page=gt.sheet_page)
    img = page["image_path"]
    assert "not an image this session rendered" in call(
        kit, "render_region", image="elsewhere.png", image_box=[0, 0, 9, 9])["error"]
    assert "not both" in call(kit, "render_region", image=img, image_box=[0, 0, 9, 9],
                              bbox=[0, 0, 9, 9])["error"]
    assert "go together" in call(kit, "render_region", image=img)["error"]
    assert "is not the page" in call(kit, "render_region", image=img, page=0,
                                     image_box=[0, 0, 9, 9])["error"]
    assert "box_units" in call(kit, "render_region", image=img,
                               image_box=[0, 0, 9, 9], box_units="mm")["error"]
    assert "image_box must be" in call(kit, "render_region", image=img,
                                       image_box=[0, 0, 9])["error"]
    assert "needs handle, page and bbox" in call(kit, "render_region",
                                                 handle=handle)["error"]


def test_the_toolkit_refuses_an_unknown_budget_or_format(gt):
    with pytest.raises(ValueError, match="unknown image budget"):
        ReviewToolkit(resolve_source=lambda key: gt.pdf, image_budget="gpt-9")
    with pytest.raises(ValueError, match="image_format"):
        ReviewToolkit(resolve_source=lambda key: gt.pdf, image_format="gif")


def test_host_render_bytes_use_the_toolkit_settings(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    data, info = kit.render(handle, gt.sheet_page)
    assert data[:3] == bytes([0xFF, 0xD8, 0xFF])
    assert info["budget"] == "openai-original"


def test_a_render_warns_when_its_lettering_is_too_small(gt, tmp_path):
    import fitz as _fitz
    pdf = _fitz.open()
    page = pdf.new_page(width=1224, height=792)            # a half-size sheet
    for i in range(20):
        page.insert_text((72 + 50 * (i % 4), 100 + 20 * i),
                         "#5 BARS @ 1'-0\" MAX.", fontsize=5)
    data = pdf.tobytes()
    pdf.close()
    k = ReviewToolkit(resolve_source=lambda key: data,
                      output_dir=str(tmp_path / "img"),
                      image_budget="gpt-4.1-high")
    try:
        handle = call(k, "open_document", source="sheet.pdf")["handle"]
        page_out = call(k, "render_page", handle=handle, page=0)
        assert page_out["text_px"] < 6
        assert "too small to read reliably" in page_out["note"]
        assert "read_document(handle, page=0)" in page_out["note"]
        assert "Do not report it as unreadable before zooming" in page_out["note"]
        # The window it suggests brings the lettering up to size.
        import re
        window = float(re.search(r"window about (\d+) pt", page_out["note"]).group(1))
        zoom = call(k, "render_region", handle=handle, page=0,
                    bbox=[300, 200, 300 + window, 200 + window], pad_frac=0.0)
        assert zoom["text_px"] >= 14
        assert "too small" not in zoom["note"]
    finally:
        k.close()
