"""Where an anchored markup lands — the live checks of 2026-10-07.

Three faults, each found on Funhouse and pinned here on synthetic pages:

* a ``view`` + ``image_box`` anchor read off a view far wider than the mark
  (a whole sheet) put rings 20-90 pt from 10 pt tags, and one read as the
  WHOLE view (``[0, 0, 999, 999]`` of a zoom) put a ring round the whole
  window: both are now refused per mark, with the reason and what to do;
* a quote matched inside a CAD notes column stored as ONE hidden string (no
  word boxes) was anchored at the column's left edge, half-way down — 50-80
  pt above the quoted line: the mark now goes on the printed row the quote
  is on, found from the ink;
* a sticky note on a rotated page hung one icon size (16 pt) off its spot.
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import Document  # noqa: E402
from planlens.document.markup_writer import (  # noqa: E402
    VIEW_ANCHOR_MAX_PT, _undoubled, write_markups,
)

AI = "planlens test"


# -- view + image_box: how wide a view a box may come from --------------------

@pytest.fixture
def sheet():
    doc = fitz.open()
    page = doc.new_page(width=1224, height=792)
    page.draw_rect(fitz.Rect(640, 515, 662, 535), color=(0, 0, 0))
    data = doc.tobytes()
    doc.close()
    return data


def _write(source, tmp_path, specs, name="out.pdf"):
    return write_markups(source, str(tmp_path / name), specs, author=AI)


def test_a_tag_sized_box_from_a_whole_sheet_view_is_refused(sheet, tmp_path):
    whole = [0, 0, 1224, 792]
    ibox = [523, 650, 541, 676]                       # ~22 x 20 pt on it
    report = _write(sheet, tmp_path, [
        {"kind": "circle", "page": 0, "comment": "tag", "label": "GCE",
         "view": whole, "image_box": ibox}])
    assert report.n_written == 0 and report.n_skipped == 1
    reason = report.skipped[0]["reason"]
    assert "1224 x 792 pt view" in reason
    assert "Zoom on the thing first" in reason and "ZOOM's view" in reason
    assert report.skipped[0]["index"] == 0


def test_the_whole_view_as_the_box_is_refused_at_any_size(sheet, tmp_path):
    zoom = [600.0, 480.0, 700.0, 560.0]
    report = _write(sheet, tmp_path, [
        {"kind": "circle", "page": 0, "comment": "tag",
         "view": zoom, "image_box": [0, 0, 999, 999]}])
    assert report.n_written == 0
    reason = report.skipped[0]["reason"]
    assert "whole 100 x 80 pt view" in reason and "bbox" in reason


def test_a_box_from_a_zoom_goes_on(sheet, tmp_path):
    zoom = [600.0, 480.0, 700.0, 560.0]                # 100 x 80 pt
    ibox = [400, 437, 619, 687]                        # the tag in it
    report = _write(sheet, tmp_path, [
        {"kind": "circle", "page": 0, "comment": "tag",
         "view": zoom, "image_box": ibox}])
    assert report.n_written == 1 and report.n_skipped == 0
    tx0, ty0, tx1, ty1 = report.written[0].target
    assert abs(tx0 - 640) < 0.5 and abs(ty1 - 535) < 0.5


def test_a_view_at_the_limit_still_places_a_small_mark(sheet, tmp_path):
    side = VIEW_ANCHOR_MAX_PT
    view = [500.0, 400.0, 500.0 + side, 400.0 + side]
    ibox = [round(140 / side * 999), round(115 / side * 999),
            round(162 / side * 999), round(135 / side * 999)]
    report = _write(sheet, tmp_path, [
        {"kind": "box", "page": 0, "comment": "tag",
         "view": view, "image_box": ibox}])
    assert report.n_written == 1


def test_a_large_mark_from_a_wide_view_goes_on(sheet, tmp_path):
    # A title block read off the whole sheet: a third of the sheet across,
    # so a tenth-of-the-view error is still well inside it.
    report = _write(sheet, tmp_path, [
        {"kind": "box", "page": 0, "comment": "title block",
         "view": [0, 0, 1224, 792], "image_box": [650, 750, 999, 999]}])
    assert report.n_written == 1


def test_a_callout_text_box_beside_its_tip_is_not_judged(sheet, tmp_path):
    report = _write(sheet, tmp_path, [
        {"kind": "callout", "page": 0, "comment": "check this tag",
         "points_at": [651, 525], "view": [0, 0, 1224, 792],
         "image_box": [600, 500, 700, 540]}])
    assert report.n_written == 1


def test_a_refused_mark_does_not_stop_the_others(sheet, tmp_path):
    report = _write(sheet, tmp_path, [
        {"kind": "box", "page": 0, "comment": "a", "bbox": [640, 515, 662, 535]},
        {"kind": "circle", "page": 0, "comment": "b",
         "view": [0, 0, 1224, 792], "image_box": [523, 650, 541, 676]},
        {"kind": "note", "page": 0, "comment": "c", "point": [80, 80]}])
    assert report.n_written == 2
    assert [s["index"] for s in report.skipped] == [1]


# -- quotes on a multi-row CAD block -------------------------------------------

#: Rows of a notes column as PRINTED, and the gap after each (pt).
NOTE_ROWS = [("NOTES:", 14.0),
             ("1. ENSURE FLUSH CONDITIONS AT CURB", 9.0),
             ("RAMP TO GUTTER TRANSITION.", 14.0),
             ("2. TYPICALLY, THE SIDEWALK RUNNING SLOPE", 9.0),
             ("SHALL NOT EXCEED THE GENERAL GRADE", 9.0),
             ("ESTABLISHED FOR THE ADJACENT STREET.", 14.0),
             ("3. CROSS SLOPE CANNOT EXCEED 2.1% MAX.", 9.0),
             ("RAMP SLOPE CANNOT EXCEED 8.33% MAX.", 9.0)]

#: How AutoCAD stores that column in its hidden SHX text: ONE string, the
#: first line of each numbered paragraph written twice.
HIDDEN = ("NOTES: 1. ENSURE FLUSH CONDITIONS AT CURB ENSURE FLUSH CONDITIONS "
          "AT CURB RAMP TO GUTTER TRANSITION. 2. TYPICALLY, THE SIDEWALK "
          "RUNNING SLOPE TYPICALLY, THE SIDEWALK RUNNING SLOPE SHALL NOT "
          "EXCEED THE GENERAL GRADE ESTABLISHED FOR THE ADJACENT STREET. 3. "
          "CROSS SLOPE CANNOT EXCEED 2.1% MAX. CROSS SLOPE CANNOT EXCEED 2.1% "
          "MAX. RAMP SLOPE CANNOT EXCEED 8.33% MAX.")

LETTER_W, LETTER_H, ADVANCE = 3.2, 5.0, 4.5


def _notes_sheet(rotation: int = 0):
    """A sheet whose notes column is lettering DRAWN as strokes (no text
    layer) with the whole column as one hidden CAD string, like sheet 10.31A
    — on a rotated page, drawn so that it reads upright AS DISPLAYED, the way
    a CAD program plots a landscape sheet. Returns the PDF and each printed
    row's band (displayed y0, y1)."""
    from planlens.document.frame import from_display_bbox
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.set_rotation(rotation)
    x0, y = 60.0, 100.0
    bands = []
    for text, gap in NOTE_ROWS:
        indent = 0.0 if text[0].isdigit() or text.startswith("NOTES") else 12.0
        for i, ch in enumerate(text):
            if ch != " ":
                cx = x0 + indent + i * ADVANCE
                page.draw_rect(fitz.Rect(*from_display_bbox(
                    page, (cx, y, cx + LETTER_W, y + LETTER_H))),
                    color=(0, 0, 0), width=0.6)
        bands.append((y, y + LETTER_H))
        y += LETTER_H + gap
    hidden = page.add_rect_annot(fitz.Rect(*from_display_bbox(
        page, (x0 - 2, 98, 260, y))))
    hidden.set_info(title="AutoCAD SHX Text", content=HIDDEN)
    hidden.set_border(width=0)
    hidden.update(opacity=0)
    data = doc.tobytes()
    doc.close()
    return data, bands


def _row_of(y: float, bands) -> int:
    return next(i for i, (a, b) in enumerate(bands) if a - 1.0 <= y <= b + 1.0)


def test_the_hidden_column_is_one_string_with_no_word_boxes():
    data, _bands = _notes_sheet()
    with Document(content=data) as doc:
        lines = [ln for ln in doc.page(0, words=True, tables=False).lines
                 if "RAMP SLOPE" in ln.text]
    assert len(lines) == 1 and not lines[0].words


@pytest.mark.parametrize("rotation", [0, 270])
@pytest.mark.parametrize("quote,row", [
    ("RAMP SLOPE CANNOT EXCEED 8.33% MAX.", 7),
    ("CROSS SLOPE CANNOT EXCEED 2.1% MAX.", 6),
    ("ESTABLISHED FOR THE ADJACENT STREET", 5),
    ("RAMP TO GUTTER TRANSITION", 2),
    ("SHALL NOT EXCEED THE GENERAL GRADE", 4),
])
def test_a_quote_in_a_multi_row_block_lands_on_its_own_row(tmp_path, quote,
                                                           row, rotation):
    data, bands = _notes_sheet(rotation)
    report = _write(data, tmp_path, [
        {"kind": "callout", "page": 0, "comment": "c", "quote": quote},
        {"kind": "highlight", "page": 0, "comment": "h", "quote": quote},
        {"kind": "note", "page": 0, "comment": "n", "quote": quote}])
    assert report.n_written == 3, report.skipped
    callout, highlight, note = report.written
    assert _row_of(callout.points_at[1], bands) == row
    hy = (highlight.bbox[1] + highlight.bbox[3]) / 2.0
    assert _row_of(hy, bands) == row
    assert highlight.bbox[3] - highlight.bbox[1] < 12.0      # one row, not the block
    assert _row_of(note.bbox[1], bands) == row


def test_lettering_that_reads_up_the_page_keeps_the_block(tmp_path):
    """The same column drawn upright on the unrotated page and then turned:
    AS DISPLAYED its lettering reads up the page, so there are no rows of
    lettering across it and the object's own box is kept, never a guess."""
    data, _bands = _notes_sheet()
    doc = fitz.open(stream=data, filetype="pdf")
    doc[0].set_rotation(90)
    turned = doc.tobytes()
    doc.close()
    with Document(content=turned) as read:
        line = next(ln for ln in read.page(0, words=True, tables=False).lines
                    if "RAMP SLOPE" in ln.text)
    report = _write(turned, tmp_path, [
        {"kind": "highlight", "page": 0, "comment": "h",
         "quote": "CROSS SLOPE CANNOT EXCEED 2.1% MAX."}])
    box = report.written[0].target or report.written[0].bbox
    lx0, ly0, lx1, ly1 = line.bbox
    assert abs((box[2] - box[0]) - (lx1 - lx0)) < 2.0
    assert abs((box[3] - box[1]) - (ly1 - ly0)) < 2.0


def test_a_one_row_object_keeps_its_box(tmp_path):
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for i, ch in enumerate("RAMP SLOPE UP TO 7.5%"):
        if ch != " ":
            page.draw_rect(fitz.Rect(100 + i * ADVANCE, 300,
                                     100 + i * ADVANCE + LETTER_W, 305),
                           color=(0, 0, 0), width=0.6)
    hidden = page.add_rect_annot(fitz.Rect(98, 298, 200, 307))
    hidden.set_info(title="AutoCAD SHX Text", content="RAMP SLOPE UP TO 7.5%")
    hidden.set_border(width=0)
    hidden.update(opacity=0)
    data = doc.tobytes()
    doc.close()
    with Document(content=data) as read:
        x0, y0, _x1, y1 = next(
            ln.bbox for ln in read.page(0, words=True, tables=False).lines
            if "7.5%" in ln.text)
    report = _write(data, tmp_path, [
        {"kind": "callout", "page": 0, "comment": "c", "quote": "UP TO 7.5%"}])
    tip = report.written[0].points_at
    # the object's own left edge, half-way down: as before, nothing to split
    assert abs(tip[0] - x0) < 0.01 and abs(tip[1] - (y0 + y1) / 2.0) < 0.01


def test_a_doubled_first_line_is_read_once():
    assert _undoubled("1. A B C A B C D E.") == "1. A B C D E."
    assert _undoubled("it is what it is") == "it is what it is"   # two words
    assert _undoubled(HIDDEN).count("ENSURE FLUSH") == 1


# -- sticky notes on rotated pages --------------------------------------------

@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_note_hangs_from_its_spot_on_every_rotation(tmp_path, rotation):
    doc = fitz.open()
    page = doc.new_page(width=600, height=400)
    page.insert_text((50, 50), "hello")
    page.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    out = str(tmp_path / f"note_{rotation}.pdf")
    report = write_markups(data, out, [
        {"kind": "note", "page": 0, "comment": "x", "point": [100, 120]}],
        author=AI)
    x0, y0, _x1, _y1 = report.written[0].bbox
    assert abs(x0 - 100) < 0.5 and abs(y0 - 120) < 0.5
    with Document(filepath=out) as read:
        mine = [m for m in read.markups() if m.author == AI]
    assert abs(mine[0].bbox[0] - 100) < 0.5 and abs(mine[0].bbox[1] - 120) < 0.5
