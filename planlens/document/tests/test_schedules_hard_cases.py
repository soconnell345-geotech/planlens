"""The schedule-marks check on the cases a real set holds and the first
fixture did not: CAD lettering kept as hidden annotations, a plan drawn as
an image, a view title under a schedule, a cell holding two marks, a
sub-heading row, a keynote list titled below, a consultant's revision
table, drawing text on a header's line, doors numbered as their rooms, a
scanned sheet away from the schedule, and dense sheets. Made-up sheets
only (app BACKLOG PL-42, review of 2026-11-14)."""

import random

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.schedules import (  # noqa: E402
    CANNOT_TELL, MAYBE_UNREAD, NO_MARK, NO_ROW, _header_row, _Piece,
    _Words, _cell_marks,
)
from planlens.testing.schedule_set_fixtures import table  # noqa: E402
from planlens.testing.sheet_set_fixtures import (  # noqa: E402
    _as_image, sketch_pdf,
)


def _result(pdf):
    with open_document(pdf) as doc:
        return doc.schedule_marks()


def _items(result):
    return sorted((i.mark, i.status) for i in result.items)


def _sheets(*painters):
    """Sheets A-101, A-102 ... each with a strip title block, then its
    painter."""
    def frame(paint, n, of):
        def draw(s, i):
            s.strip_block(f"A-10{n}", f"SHEET {n}", n, of)
            paint(s)
        return draw
    return sketch_pdf([frame(p, n + 1, len(painters))
                       for n, p in enumerate(painters)])


def _fans(s, rows=("EF-1", "EF-2", "EF-3")):
    table(s, 150, 220, "EXHAUST FAN SCHEDULE", ("MARK", "SERVES", "CFM"),
          [(r, "TOILETS", "400") for r in rows], (90, 120, 60))


def _tags(marks, y=600, size=7, bold=False):
    def paint(s):
        for k, m in enumerate(marks):
            s.centred(400 + 200 * k, y, m, size, bold)
    return paint


def _hidden(marks, y=600):
    """Tags kept as AutoCAD keeps SHX lettering: strokes drawn, the text
    only in a hidden 'AutoCAD SHX Text' annotation."""
    def paint(s):
        for k, m in enumerate(marks):
            x = 400 + 200 * k
            s.page.draw_line((x - 10, y - 3), (x + 10, y + 3), width=0.4)
            if m is None:
                continue
            a = s.page.add_freetext_annot(
                pymupdf.Rect(x - 18, y - 5, x + 18, y + 4), m, fontsize=6)
            a.set_info(title="AutoCAD SHX Text", content=m)
            a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
            a.update()
    return paint


# -- 1: marks the text layer does not hold -----------------------------------

def test_tags_kept_as_hidden_cad_text_are_read():
    pdf = _sheets(_hidden(["EF-1", "EF-2", "EF-3", "EF-4"]), _fans)
    result = _result(pdf)
    assert _items(result) == [("EF-4", NO_ROW)]
    assert result.schedules[0].rows_found == 3


def test_tags_drawn_as_lines_leave_the_rows_open():
    """No text and no hidden text for the tags: the schedule's rows are
    found nowhere, which says the plan was not read, not that every row is
    missing. (The plan has a note in text, so the sheet itself is read.)"""
    def plan(s):
        s.centred(700, 300, "ROOF PLAN NOTES: VERIFY ON SITE", 8)
        _hidden([None, None, None])(s)
    pdf = _sheets(plan, _fans)
    result = _result(pdf)
    assert _items(result) == [("EF-1", CANNOT_TELL), ("EF-2", CANNOT_TELL),
                              ("EF-3", CANNOT_TELL)]
    assert "only 0 of the 3 rows of this schedule are found" in \
        result.items[0].why
    assert not result.with_status(NO_MARK)


def test_a_plan_drawn_as_an_image_leaves_its_rows_open():
    """A raster plan under a text title block: the sheet has text, so it is
    read, but its tags are in the image."""
    def raster(s):
        pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 60, 40), 0)
        pix.clear_with(200)
        s.page.insert_image(pymupdf.Rect(40, 40, 2100, 1540), pixmap=pix)
        s.centred(400, 600, "EF-1", 7)
        s.centred(600, 600, "EF-2", 7)
    pdf = _sheets(raster, _fans)
    result = _result(pdf)
    assert result.pictured == [0]
    assert _items(result) == [("EF-3", MAYBE_UNREAD)]
    assert "have their drawing in an image" in result.items[0].why
    assert any("drawing in an image" in n for n in result.notes)


# -- 2: the column stops at the table's end ----------------------------------

def test_a_view_title_under_a_schedule_is_no_row():
    def schedule(s):
        table(s, 150, 240, "FOOTING SCHEDULE",
              ("MARK", "SIZE", "DEPTH"),
              [("F1", "1500", "450"), ("F2", "1800", "500"),
               ("F3", "2100", "600")], (60, 80, 60))
        s.view_title(170, 345, "2", "A-102", "FOOTING SCHEDULE")
    pdf = _sheets(_tags(["F1", "F2", "F3"], bold=True), schedule)
    result = _result(pdf)
    assert result.schedules[0].marks == ["F1", "F2", "F3"]
    assert result.items == []


def test_the_column_stops_at_a_change_of_lettering_and_a_wide_step():
    def schedule(s):
        table(s, 150, 240, "FOOTING SCHEDULE", ("MARK", "SIZE", "DEPTH"),
              [("F1", "1500", "450"), ("F2", "1800", "500")], (60, 80, 60))
        s.text(154, 330, "F9", 12, bold=True)      # larger, just below
        s.text(154, 420, "F8", 8)                 # same size, far below
    pdf = _sheets(_tags(["F1", "F2", "F8", "F9"]), schedule)
    result = _result(pdf)
    assert result.schedules[0].marks == ["F1", "F2"]


# -- 3: cells that are not one mark ------------------------------------------

def test_a_cell_holding_two_marks_gives_both_and_the_column_goes_on():
    pdf = _sheets(_tags(["EF-1", "EF-2", "EF-3", "EF-4", "EF-5"]),
                  lambda s: _fans(s, ("EF-1", "EF-2", "EF-3, EF-4",
                                      "EF-5")))
    result = _result(pdf)
    assert result.schedules[0].marks == ["EF-1", "EF-2", "EF-3", "EF-4",
                                         "EF-5"]
    assert result.items == []


def test_a_sub_heading_row_is_passed_over():
    rows = ["101A", "102A", "SECOND FLOOR", "201A", "202A"]
    pdf = _sheets(_tags(["101A", "102A", "201A", "202A"], bold=True),
                  lambda s: table(s, 150, 220, "DOOR SCHEDULE",
                                  ("NO.", "WIDTH", "HEIGHT"),
                                  [(r, "", "") if " " in r else
                                   (r, "915", "2135") for r in rows],
                                  (90, 60, 60)))
    result = _result(pdf)
    assert result.schedules[0].marks == ["101A", "102A", "201A", "202A"]
    assert result.schedules[0].unread_cells == []
    assert result.items == []


def test_a_cell_not_read_as_marks_leaves_a_mark_with_no_row_open():
    pdf = _sheets(_tags(["W-1", "W-2", "W-4"]),
                  lambda s: table(s, 150, 220, "WINDOW SCHEDULE",
                                  ("MARK", "WIDTH", "HEIGHT"),
                                  [("W-1", "900", "1200"),
                                   ("W-2", "900", "1200"),
                                   ("TYPE 3 PER SPEC", "600", "600")],
                                  (90, 60, 60)))
    result = _result(pdf)
    assert result.schedules[0].unread_cells == ["TYPE 3 PER SPEC"]
    assert result.schedules[0].to_dict()["cells_not_read"] == [
        "TYPE 3 PER SPEC"]
    assert _items(result) == [("W-4", CANNOT_TELL)]
    assert "could not be read as marks" in result.items[0].why


def test_the_marks_a_cell_holds():
    import re
    shapes = [re.compile(r"EF\-\d{1,2}")]
    assert _cell_marks("EF-3, EF-4", shapes) == ["EF-3", "EF-4"]
    assert _cell_marks("EF-3 THRU EF-5", shapes) == ["EF-3", "EF-4", "EF-5"]
    assert _cell_marks("EF-3 (ALT)", shapes) == ["EF-3"]
    assert _cell_marks("SECOND FLOOR", shapes) == []
    assert _cell_marks("LEVEL 2", shapes) == []


# -- 5: lists that are not schedules -----------------------------------------

def test_an_untitled_table_s_rows_drawn_nowhere_cannot_be_told():
    """A table headed TAG with no SCHEDULE title: a tag drawn with no entry
    is a finding; an entry drawn nowhere may be a list's. (A note in text:
    a plan whose only text is three tags may be lettered as lines.)"""
    def plan(s):
        s.centred(700, 300, "FINISH NOTES: VERIFY ON SITE", 8)
        _tags(["P-1", "P-2", "P-4"])(s)
    pdf = _sheets(plan,
                  lambda s: table(s, 150, 220, None,
                                  ("TAG", "COLOUR", "FINISH"),
                                  [("P-1", "WHITE", "EGG"),
                                   ("P-2", "GREY", "EGG"),
                                   ("P-3", "BLUE", "SEMI")], (60, 80, 60)))
    result = _result(pdf)
    assert _items(result) == [("P-3", CANNOT_TELL), ("P-4", NO_ROW)]
    row = next(i for i in result.items if i.mark == "P-3")
    assert "no SCHEDULE title" in row.why


def test_a_keynote_list_titled_below_is_not_a_schedule():
    def notes(s):
        table(s, 150, 220, None, ("NO.", "NOTE", "REF"),
              [("K1", "SEALANT", "07 92 00"), ("K2", "GUARD", "10 26 00"),
               ("K3", "BASE", "09 65 13")], (50, 260, 90))
        s.view_title(170, 320, "4", "A-102", "KEYNOTES")
    pdf = _sheets(_tags(["K1", "K2"]), notes)
    result = _result(pdf)
    assert result.schedules == [] and result.items == []


def test_a_consultant_revision_table_on_a_few_sheets_is_the_title_block():
    """MARK / DATE / DESCRIPTION at one place on 3 of 8 sheets, under no
    title: a consultant's revision table, not a schedule."""
    def cons(s):
        for dx, head in ((0, "MARK"), (40, "DATE"), (110, "DESCRIPTION")):
            s.text(1800 + dx, 1380, head, 6, bold=True)
        for k, r in enumerate(("P1", "P2", "C1")):
            s.text(1800, 1392 + 12 * k, r, 6)
            s.text(1840, 1392 + 12 * k, f"2031-0{k + 1}-01", 6)
            s.text(1910, 1392 + 12 * k, "ISSUED", 6)
    pdf = _sheets(*([cons] * 3 + [lambda s: None] * 5))
    result = _result(pdf)
    assert result.schedules == []


def test_drawing_text_on_a_header_s_line_is_cut_off():
    lines = []
    doc = pymupdf.open()
    page = doc.new_page(width=2448, height=1584)
    for x, text in ((150, "MARK"), (210, "SIZE"), (270, "DEPTH"),
                    (520, "GWB"), (560, "TYP")):
        page.insert_text((x, 300), text, fontsize=8)
    from planlens.document.pdf_text import extract_text
    lines, _b, _s = extract_text(page, 0)
    doc.close()
    pieces = [_Piece(ln) for ln in lines]
    anchor = next(p for p in pieces if p.text == "MARK")
    assert [p.text for p in _header_row(anchor, pieces)] == [
        "MARK", "SIZE", "DEPTH"]


# -- 6: doors numbered as their rooms ----------------------------------------

def test_a_lone_number_where_doors_are_numbered_as_rooms_cannot_be_told():
    def plan(s):
        for k, n in enumerate(("201", "202", "203")):
            s.centred(450 + 300 * k, 400, "OFFICE", 8)
            s.centred(450 + 300 * k, 411, n, 8)       # room name and number
            s.centred(450 + 300 * k, 560, n, 8)       # door tag, alike
        s.centred(1500, 400, "206", 8)                # a closet's number
    pdf = _sheets(plan, lambda s: table(
        s, 150, 220, "DOOR SCHEDULE", ("NO.", "WIDTH", "HEIGHT"),
        [(n, "915", "2135") for n in ("201", "202", "203")], (60, 60, 60)))
    result = _result(pdf)
    assert _items(result) == [("206", CANNOT_TELL)]
    assert "rooms numbered as their doors" in result.items[0].why


# -- 7: a scanned sheet away from the schedule -------------------------------

def test_a_mark_with_no_row_is_left_open_by_a_scan_beside_or_away():
    """PL-43 limit 2: a schedule can continue on a sheet away from its
    other parts, so a scan anywhere leaves a mark with no row open (it was
    ``no_row`` with the scan away before); with every sheet read it is
    ``no_row``."""
    def noted(s):
        s.centred(700, 300, "GENERAL NOTES: VERIFY ON SITE", 8)
    pdf = _sheets(lambda s: (noted(s), _tags(["W-1", "W-2", "W-4"])(s)),
                  lambda s: table(s, 150, 220, "WINDOW SCHEDULE",
                                  ("MARK", "WIDTH", "HEIGHT"),
                                  [("W-1", "900", "1200"),
                                   ("W-2", "900", "1200")], (90, 60, 60)),
                  noted, noted)
    read = _result(pdf)
    assert _items(read) == [("W-4", NO_ROW)]
    far = _result(_as_image(pdf, [3]))
    item = next(i for i in far.items if i.mark == "W-4")
    assert item.status == MAYBE_UNREAD
    assert item.why.endswith(
        "; but 1 sheet(s) could not be read (page(s) 3): a part of the "
        "schedule there would not be seen, so whether it has a row the "
        "text does not tell; view them")
    near = _result(_as_image(pdf, [2]))
    item = next(i for i in near.items if i.mark == "W-4")
    assert item.status == MAYBE_UNREAD
    assert "sit beside the schedule's pages" in item.why


# -- 4: the lettering test, fast and the same --------------------------------

class _Line:
    def __init__(self, text, bbox):
        self.text, self.bbox = text, bbox


def _named_slowly(line, lines):
    x0, y0, x1, y1 = line.bbox
    h = max(1.0, y1 - y0)
    cx = (x0 + x1) / 2.0
    for other in lines:
        if other is line or sum(c.isalpha() for c in other.text) < 3:
            continue
        ox0, oy0, ox1, oy1 = other.bbox
        if not ox0 - 0.5 * h <= cx <= ox1 + 0.5 * h:
            continue
        gap = max(oy0 - y1, y0 - oy1)
        if -0.3 * h <= gap <= 1.0 * h:
            return True
    return False


def test_the_word_lookup_answers_as_the_page_wide_scan_did():
    rnd = random.Random(3)
    lines = []
    for _ in range(1500):
        x, y = rnd.uniform(0, 600), rnd.uniform(0, 600)
        h = rnd.choice((6.0, 8.0, 12.0))
        text = rnd.choice(("OFFICE", "101", "GWB", "12", "STAIR"))
        lines.append(_Line(text, (x, y, x + 6 * len(text), y + h)))
    words = _Words(lines)
    numbers = [ln for ln in lines if ln.text.isdigit()]
    assert [words.named(ln) for ln in numbers] == \
        [_named_slowly(ln, lines) for ln in numbers]
    assert any(words.named(ln) for ln in numbers)
