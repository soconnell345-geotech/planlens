"""Text first, eyes second — and the tools say when.

Every result that touches a page the text cannot represent must say so and say
how to look; the render tools must produce a usable, bounded image in the
displayed frame; an image file must open as a one-page document.
"""

import json
import os

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import Document, open_document, page_advice  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402
from planlens.tools import DEFAULT_VISION_HINT, ReviewToolkit  # noqa: E402

UPLOAD = "review.pdf"
HOST_HINT = "view with look_at_page(source, page)"


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


@pytest.fixture
def kit(gt, tmp_path):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf, vision_hint=HOST_HINT,
                      output_dir=str(tmp_path / "png"))
    yield k
    k.close()


def call(kit, name, **args):
    text = kit.call_json(name, args)
    assert len(text) <= kit.max_chars
    return json.loads(text)


# -- advice ------------------------------------------------------------------------

def test_page_advice_names_the_pages_text_cannot_represent(gt):
    with open_document(gt.pdf) as doc:
        assert doc.advice(gt.narrative_page) == []
        assert doc.advice(gt.blank_page) == []
        sheet = doc.advice(gt.sheet_page)
        assert any("drawing sheet" in a for a in sheet)
        assert any("point at a spot" in a for a in sheet)
        scan = doc.advice(gt.scanned_page)
        assert len(scan) == 1 and "no text layer" in scan[0]


def test_sparse_grid_advice():
    doc = fitz.open()
    p = doc.new_page()
    xs = [72 + 40 * i for i in range(9)]
    ys = [100 + 20 * i for i in range(7)]
    for x in xs:
        p.draw_line((x, ys[0]), (x, ys[-1]))
    for y in ys:
        p.draw_line((xs[0], y), (xs[-1], y))
    p.insert_text((xs[0] + 4, ys[0] + 14), "N")       # one cell of 48 filled
    data = doc.tobytes()
    doc.close()
    with open_document(data) as d:
        assert any("sparse grid" in a for a in d.advice(0))


# -- the look lines in results ----------------------------------------------------------

def test_open_document_lists_pages_to_view(kit, gt):
    out = call(kit, "open_document", source=UPLOAD)
    assert out["source"] == UPLOAD
    assert out["kind"] == "pdf"
    assert out["pages_to_view"] == f"{gt.sheet_page},{gt.scanned_page}"
    assert out["pages_to_view_note"].endswith(HOST_HINT)


def test_read_document_flags_scan_and_sheet_but_not_prose(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    scan = call(kit, "read_document", handle=handle, pages=gt.scanned_page)["text"]
    assert "! look: image-only page with no text layer" in scan
    assert HOST_HINT in scan
    sheet = call(kit, "read_document", handle=handle, pages=gt.sheet_page)["text"]
    assert "! look: drawing sheet" in sheet
    prose = call(kit, "read_document", handle=handle,
                 pages=gt.narrative_page)["text"]
    assert "! look:" not in prose


def test_page_map_marks_pages_to_look_at(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "document_page_map", handle=handle)
    flagged = {r["page"] for r in out["rows"] if r.get("look")}
    assert flagged == {gt.sheet_page, gt.scanned_page}
    assert HOST_HINT in out["look"]


def test_search_miss_points_at_unsearchable_pages(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "search_document", handle=handle, pattern="not on any page")
    assert out["n_hits"] == 0
    assert out["pages_not_searchable_as_text"] == f"{gt.sheet_page},{gt.scanned_page}"
    assert "not absence" in out["hint"] or "may be on one" in out["hint"]
    hit = call(kit, "search_document", handle=handle, pattern="Boring",
               pages=[gt.narrative_page, gt.table_page])
    assert "pages_not_searchable_as_text" not in hit    # none in that range


def test_markups_result_says_where_to_look(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "document_markups", handle=handle, pages=gt.sheet_page)
    assert "points at" in out["look"] and HOST_HINT in out["look"]


def test_default_hint_names_the_render_tools(gt):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf)
    try:
        handle = call(k, "open_document", source=UPLOAD)["handle"]
        text = call(k, "read_document", handle=handle, pages=gt.scanned_page)["text"]
        assert DEFAULT_VISION_HINT in text
        assert "render_page(" in DEFAULT_VISION_HINT
    finally:
        k.close()


# -- rendering -----------------------------------------------------------------------------

def test_render_page_writes_a_bounded_png(kit, gt, tmp_path):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "render_page", handle=handle, page=gt.sheet_page)
    assert out["image_path"].startswith(str(tmp_path / "png"))
    with open(out["image_path"], "rb") as fh:
        head = fh.read(8)
    assert head == b"\x89PNG\r\n\x1a\n"
    assert max(out["width_px"], out["height_px"]) == 2000
    assert out["width_px"] * out["height_px"] <= 4_000_000
    assert out["clip"] == [0.0, 0.0, 2592.0, 1728.0]


def test_render_region_shows_the_marked_spot(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    tx, ty = gt.reviewer_target
    out = call(kit, "render_region", handle=handle, page=gt.sheet_page,
               bbox=[tx - 40, ty - 40, tx + 40, ty + 40],
               marks=[[tx, ty, "A"]], pad_frac=0.0)
    assert out["dpi"] == 200.0
    assert out["marks"] == [[tx, ty, "A"]]
    pix = fitz.Pixmap(out["image_path"])
    # The circle drawn at the target sits at the crop's centre, which the
    # fixture left blank apart from a small ring: the crop must contain ink
    # in the fixture's black AND the mark's red.
    samples = pix.samples
    n = pix.n
    reds = sum(1 for i in range(0, len(samples), n)
               if samples[i] > 200 and samples[i + 1] < 80 and samples[i + 2] < 80)
    assert reds > 50, "the mark was not drawn in the crop"
    pix = None


def test_render_region_rejects_bad_boxes(kit, gt):
    handle = call(kit, "open_document", source=UPLOAD)["handle"]
    out = call(kit, "render_region", handle=handle, page=0, bbox=[1, 2, 3])
    assert "bbox must be" in out["error"]
    out = call(kit, "render_region", handle=handle, page=0, bbox=[0, 0, 9, 9],
               marks=[["x"]])
    assert "marks must be" in out["error"]


def test_render_caps_pixels(gt):
    with open_document(gt.pdf) as doc:
        png, info = doc.render(gt.sheet_page, dpi=300, max_pixels=1_000_000)
        assert info["width_px"] * info["height_px"] <= 1_000_000
        assert info["dpi"] < 300


def test_rendering_with_marks_leaves_the_document_text_untouched(gt):
    with open_document(gt.pdf) as doc:
        before = [ln.text for ln in doc.page(gt.sheet_page).lines]
        doc.render(gt.sheet_page, bbox=(0, 0, 400, 400), marks=[(100, 100, "9")])
        fresh = Document(content=gt.pdf)
        assert [ln.text for ln in fresh.page(gt.sheet_page).lines] == before
        fresh.close()


def test_toolkit_render_returns_bytes_for_hosts(gt):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf)
    try:
        handle = call(k, "open_document", source=UPLOAD)["handle"]
        png, info = k.render(handle, gt.scanned_page)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and info["page"] == gt.scanned_page
    finally:
        k.close()


# -- image input -------------------------------------------------------------------------------

def test_an_image_opens_as_a_one_page_scanned_document(gt, tmp_path):
    src = fitz.open("pdf", gt.pdf)
    png = src[gt.scanned_page].get_pixmap(dpi=40).tobytes("png")
    jpg = src[gt.sheet_page].get_pixmap(dpi=20).tobytes("jpeg")
    src.close()
    path = tmp_path / "photo.jpg"
    path.write_bytes(jpg)
    uploads = {"scan.png": png}
    k = ReviewToolkit(resolve_source=lambda key: uploads.get(key) or key)
    try:
        out = call(k, "open_document", source="scan.png")
        assert out["kind"] == "image" and out["n_pages"] == 1
        assert out["pages_by_kind"] == {"scanned": "0"}
        assert out["pages_to_view"] == "0"
        text = call(k, "read_document", handle=out["handle"])["text"]
        assert "! look: image-only page" in text
        out = call(k, "open_document", source=str(path))
        assert out["kind"] == "image"
        img = call(k, "render_page", handle=out["handle"], page=0)
        assert os.path.isfile(img["image_path"])
    finally:
        k.close()


def test_garbage_is_refused_with_a_reason(tmp_path):
    k = ReviewToolkit(resolve_source=lambda key: b"not a document at all")
    try:
        out = call(k, "open_document", source="x.bin")
        assert "could not open" in out["error"]
    finally:
        k.close()


def test_tight_limit_keeps_handle_and_map(gt):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf, max_chars=1000)
    try:
        out = call(k, "open_document", source=UPLOAD)
        assert out["handle"] and out["pages_by_kind"] and out["pages_to_view"]
    finally:
        k.close()
