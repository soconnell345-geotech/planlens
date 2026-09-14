"""planlens.document against a synthetic review document with hand-derived truth.

The sheet page is stored portrait with /Rotate 90, so every coordinate test
here fails if the displayed frame is not honoured. Truth comes from
``planlens.testing.document_fixtures``, which derives displayed coordinates by
hand rather than with PyMuPDF's rotation matrix; the ink test additionally
checks a reported box against the rendered page.
"""

import json

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import (  # noqa: E402
    SOURCE_CAD_HIDDEN, open_document, parse_pages,
)
from planlens.document.frame import from_ir_point, to_ir_point  # noqa: E402
from planlens.ir.render import ir_to_page_point  # noqa: E402
from planlens.testing.document_fixtures import (  # noqa: E402
    build_synthetic_review_document,
)


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


@pytest.fixture
def doc(gt):
    d = open_document(gt.pdf, name="synthetic")
    yield d
    d.close()


def _line(page, text):
    matches = [ln for ln in page.lines if ln.text == text]
    assert matches, f"no line {text!r} in {[l.text for l in page.lines]}"
    return matches[0]


def _markup(page, author, kind):
    matches = [m for m in page.markups if m.author == author and m.kind == kind]
    assert len(matches) == 1, [(m.author, m.kind) for m in page.markups]
    return matches[0]


# -- document map -------------------------------------------------------------

def test_page_map_kinds_and_evidence(doc, gt):
    pm = {s.page: s for s in doc.page_map()}
    assert pm[gt.narrative_page].kind == "text"
    assert pm[gt.narrative_page].heading == gt.heading
    sheet = pm[gt.sheet_page]
    assert sheet.kind == "drawing_sheet"
    assert sheet.rotation == 90
    assert sheet.to_dict()["size_in"] == [36.0, 24.0]
    assert sheet.label == gt.sheet_label
    assert pm[gt.blank_page].kind == "blank"
    assert pm[gt.scanned_page].kind == "scanned"
    assert pm[gt.scanned_page].evidence.get("needs_ocr") is True
    for s in pm.values():
        assert s.evidence["rule"]


def test_toc_is_zero_based(doc, gt):
    assert [(t["level"], t["title"], t["page"]) for t in doc.toc()] == gt.toc


def test_parse_pages():
    assert parse_pages("0-2,4,1", 6) == [0, 1, 2, 4]
    assert parse_pages(None, 3) == [0, 1, 2]
    assert parse_pages(2, 3) == [2]
    with pytest.raises(IndexError, match="numbered 0-2"):
        parse_pages([0, 5], 3)


# -- text in the displayed frame ------------------------------------------------

def test_upright_text_on_rotated_sheet(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    ln = _line(page, gt.sheet_text_upright)
    assert ln.rotation == 0.0
    ox, oy = gt.sheet_text_upright_origin
    x0, y0, x1, y1 = ln.bbox
    assert abs(x0 - ox) < 2.0                 # starts at its insertion point
    assert y0 < oy <= y1 + 1.0                # baseline inside the box
    assert x1 - x0 > 3 * (y1 - y0)            # reads horizontally as displayed


def test_reported_box_holds_the_rendered_ink(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    x0, y0, x1, y1 = _line(page, gt.sheet_text_upright).bbox
    src = fitz.open("pdf", gt.pdf)
    pix = src[gt.sheet_page].get_pixmap(dpi=72, colorspace=fitz.csGRAY,
                                        clip=fitz.Rect(x0, y0, x1, y1))
    dark = sum(1 for v in pix.samples if v < 128)
    src.close()
    assert dark >= 30, f"only {dark} dark pixels inside the reported box"


def test_vertical_text_reports_its_direction(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    ln = _line(page, gt.sheet_text_vertical)
    assert ln.rotation == gt.sheet_text_vertical_rotation
    x0, y0, x1, y1 = ln.bbox
    assert (y1 - y0) > 2 * (x1 - x0)


def test_words_carry_boxes_inside_their_line(doc, gt):
    page = doc.page(gt.narrative_page, words=True, tables=False)
    with_words = [ln for ln in page.lines if ln.words]
    assert with_words
    for ln in with_words:
        for _w, (wx0, wy0, wx1, wy1) in ln.words:
            assert ln.bbox[0] - 0.5 <= wx0 and wx1 <= ln.bbox[2] + 0.5
            assert ln.bbox[1] - 0.5 <= wy0 and wy1 <= ln.bbox[3] + 0.5


# -- markups ---------------------------------------------------------------------

def test_comment_text_is_not_mistaken_for_sheet_text(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    body = page.text()
    assert "CONFIRM THE PILE" not in body
    assert "Embedment revised" not in body
    rev = _markup(page, gt.reviewer, "FreeText")
    assert rev.text == gt.reviewer_comment


def test_callout_points_at_its_target(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    rev = _markup(page, gt.reviewer, "FreeText")
    assert rev.intent == "FreeTextCallout"
    assert rev.created == "2026-08-25T16:09:34-04:00"
    tx, ty = gt.reviewer_target
    assert abs(rev.points_at[0] - tx) < 1.0 and abs(rev.points_at[1] - ty) < 1.0


def test_reply_thread_structure(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    rev = _markup(page, gt.reviewer, "FreeText")
    reply = _markup(page, gt.contractor, "FreeText")
    cloud = _markup(page, gt.contractor, "Polygon")
    # The reply's tip lands inside its own cloud AND the reviewer's box; the
    # cloud is its own companion, so the reviewer's comment is what it names.
    assert reply.points_to_markup == rev.id
    assert cloud.in_reply_to == reply.id
    assert reply.replies == [cloud.id]


def test_arrow_points_at_its_head(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    arrow = _markup(page, gt.contractor, "Line")
    hx, hy = gt.arrow_tip
    assert abs(arrow.points_at[0] - hx) < 1.0
    assert abs(arrow.points_at[1] - hy) < 1.0


def test_markups_filter_by_author(doc, gt):
    assert len(doc.markups(author="contractor")) == 3
    reviewer = doc.markups(author="Reviewer")
    assert [(m.page, m.kind) for m in reviewer] == [
        (gt.narrative_page, "Stamp"), (gt.sheet_page, "FreeText")]


def test_stamp_wording_is_kept_as_appearance_text(doc, gt):
    page = doc.page(gt.narrative_page, tables=False)
    stamp = _markup(page, gt.stamp_author, "Stamp")
    assert stamp.text == ""
    assert stamp.appearance_text == gt.stamp_wording
    assert gt.stamp_wording not in page.text()      # not in the text layer
    # A callout that merely draws its own comment gets no appearance_text.
    sheet = doc.page(gt.sheet_page, tables=False)
    assert _markup(sheet, gt.reviewer, "FreeText").appearance_text is None


def test_near_cardinal_directions_snap():
    from planlens.document.pdf_text import _rotation
    assert _rotation((1.0, 0.0)) == 0.0
    assert _rotation((0.99999, 0.0017)) == 0.0      # 359.9 deg
    assert _rotation((0.0, -1.0)) == 90.0
    assert _rotation((0.7071, -0.7071)) == 45.0     # real angles untouched
    assert _rotation((0.9848, 0.1736)) == 350.0


# -- hidden CAD text -------------------------------------------------------------

def test_hidden_cad_text_is_read_and_deduplicated(doc, gt):
    page = doc.page(gt.sheet_page, tables=False)
    cad = [ln for ln in page.lines if ln.source == SOURCE_CAD_HIDDEN]
    assert [c.text for c in cad] == [gt.hidden_cad_text]
    assert cad[0].rotation is None
    assert cad[0].to_dict()["rotation"] == "unknown"
    for got, want in zip(cad[0].bbox, gt.hidden_cad_box):
        assert abs(got - want) < 2.0
    assert page.stats["n_cad_text_duplicates_dropped"] == 1
    assert SOURCE_CAD_HIDDEN in page.text_sources


# -- tables ------------------------------------------------------------------------

def test_ruled_table_rows(doc, gt):
    page = doc.page(gt.table_page)
    assert len(page.tables) == 1
    table = page.tables[0]
    assert table.rows == gt.table_rows
    assert table.header is None          # an in-grid first row is not promoted
    md = table.to_markdown()
    assert md.splitlines()[0] == "| Boring | Depth (ft) | N-value |"


# -- search --------------------------------------------------------------------------

def test_search_matches_across_a_line_break(doc, gt):
    res = doc.search("40-foot centers")
    assert res["n_hits"] >= 1
    assert all(h["page"] == gt.narrative_page for h in res["hits"])
    assert any(len(h["line_ids"]) == 2 for h in res["hits"])


def test_search_regex_and_page_limit(doc, gt):
    res = doc.search(r"B-\d", regex=True, pages=[gt.table_page])
    assert res["n_hits"] == 4
    assert set(res["pages_with_hits"]) == {gt.table_page}


def test_search_reaches_markups_not_leaked_text(doc, gt):
    res = doc.search("embedment", pages=[gt.sheet_page])
    assert res["n_hits"] == 2
    assert all(h.get("markup_id") for h in res["hits"])


def test_search_reaches_hidden_cad_text(doc, gt):
    res = doc.search("hidden note")
    assert res["n_hits"] == 1
    assert res["hits"][0]["source"] == SOURCE_CAD_HIDDEN


# -- misc ------------------------------------------------------------------------------

def test_scanned_page_says_what_to_do(doc, gt):
    page = doc.page(gt.scanned_page)
    assert not page.lines
    assert any("OCR" in w for w in page.warnings)


def test_page_payload_is_json_and_names_its_frame(doc, gt):
    d = doc.page(gt.sheet_page).to_dict()
    assert d["frame"] == "displayed_pt_top_left"
    json.dumps(d)


def test_text_has_page_headers(doc, gt):
    txt = doc.text(pages=[gt.narrative_page, gt.blank_page])
    assert txt.startswith("=== page 0 (1) ===")
    assert "=== page 3 (4) ===\n[no text]" in txt


def test_ir_frame_round_trip_on_rotated_page(gt):
    src = fitz.open("pdf", gt.pdf)
    page = src[gt.sheet_page]
    for xd, yd in [(0.0, 0.0), (300.0, 400.0), (2500.0, 1700.0)]:
        xi, yi = to_ir_point(page, xd, yd)
        back = from_ir_point(page, xi, yi)
        assert abs(back[0] - xd) < 1e-6 and abs(back[1] - yd) < 1e-6
        via_render = ir_to_page_point(xi, yi, page)
        assert abs(via_render[0] - xd) < 1e-6 and abs(via_render[1] - yd) < 1e-6
    src.close()
