"""The schedule-marks check, on made-up drawing sets.

Truth is the fixture's own statement of what it drew
(:mod:`planlens.testing.schedule_set_fixtures`): a door schedule printed
over two sheets with plain-number marks, a window schedule (W-1), an
equipment table with no title (EF-2), room numbers in the doors' form, a
keynote list, a legend, a sheet index and revision blocks that are not
schedules; then the same set with a sheet scanned, and with no schedule.
The guards for plain numbers and for families of marks are tested on
sheets drawn here.
"""

import functools
import re

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.schedules import (  # noqa: E402
    CANNOT_TELL, MAYBE_UNREAD, NO_MARK, NO_ROW, _range_holds, _ranges_in,
    mark_form, plain, too_short,
)
from planlens.testing.schedule_set_fixtures import (  # noqa: E402
    TRUTH, TRUTH_A102_SCANNED, build_schedule_set, table,
)
from planlens.testing.sheet_set_fixtures import sketch_pdf  # noqa: E402


@functools.lru_cache(maxsize=None)
def _built(scanned=(), schedules=True):
    return build_schedule_set(scanned=scanned, schedules=schedules)


@functools.lru_cache(maxsize=None)
def _checked(scanned=(), schedules=True):
    gt = _built(scanned, schedules)
    with open_document(gt.pdf) as doc:
        return gt, doc.schedule_marks()


def _items(result):
    return sorted((i.mark, i.status, i.side, i.sheet) for i in result.items)


# -- the made-up set ---------------------------------------------------------

def test_every_item_is_the_truth_and_no_other():
    gt, result = _checked()
    assert _items(result) == sorted(gt.truth) == sorted(TRUTH)
    counts = result.counts()
    assert counts == {"schedules": 3, "rows": 16, "rows_found": 10,
                      NO_ROW: 2, NO_MARK: 2, MAYBE_UNREAD: 0,
                      CANNOT_TELL: 4}
    assert result.unread == []


def test_the_schedules_and_their_rows_are_read_whatever_their_names():
    gt, result = _checked()
    got = [(s.title, tuple(s.sheets), tuple(s.marks))
           for s in result.schedules]
    assert got == gt.schedules
    door, window, fans = result.schedules
    # a schedule printed over two sheets is one, under its first title
    assert door.pages == [3, 4]
    assert door.name == "DOOR SCHEDULE (A-601, A-602)"
    # each schedule's mark form is learnt from its own rows
    assert [shown for _rx, shown in door.forms()] == ["999"]
    assert [shown for _rx, shown in window.forms()] == ["W-9"]
    assert [shown for _rx, shown in fans.forms()] == ["EF-9"]
    # a table with no title is a schedule by its mark heading, named by
    # its headings
    assert fans.title is None
    assert fans.name == "table headed TAG, SERVES, CFM, ... (M-101)"
    assert fans.to_dict() == {
        "schedule": "table headed TAG, SERVES, CFM, ... (M-101)",
        "pages": "5", "rows": 3, "forms": ["EF-9"], "rows_found": 2}


def test_each_item_gives_its_sheet_both_pages_its_schedule_and_why():
    _gt, result = _checked()
    by_mark = {i.mark: i.to_dict(result.names()) for i in result.items}
    w4 = by_mark["W-4"]
    assert w4["status"] == NO_ROW and w4["side"] == "mark"
    assert (w4["sheet"], w4["page"], w4["pdf_page"]) == ("A-101", 1, 2)
    assert w4["schedule"] == "WINDOW SCHEDULE (A-601)"
    assert w4["why"] == (
        "W-4 is drawn on A-101 (page 1) and fits the form W-9 of the "
        "schedule's marks, which has no row W-4 (its rows: W-1, W-2, W-3, "
        "W-5)")
    x0, y0, x1, y1 = w4["bbox"]                     # drawn at 1200, 250
    assert x0 <= 1201 < x1 and y0 < 249 < y1
    row = by_mark["104"]
    assert row["status"] == NO_MARK and row["side"] == "row"
    # a row's place is where the schedule prints it
    assert (row["sheet"], row["page"], row["pdf_page"]) == ("A-601", 3, 4)
    assert row["schedule"] == "DOOR SCHEDULE (A-601, A-602)"
    assert "every sheet's text was read" in row["why"]


def test_a_plain_number_is_a_mark_only_in_the_marks_lettering():
    """Door 105 is drawn alone in the doors' bold lettering with no row:
    a mark with no row. The rooms' numbers (110, 120, 130 under their
    names) share the form and are never items. On A-102 the rooms are
    numbered as the doors are, so 203 is found there only as a room's
    number: the text cannot tell the door is tagged."""
    _gt, result = _checked()
    marks = {i.mark: i for i in result.items}
    assert not {"110", "120", "130", "201", "202", "600", "915"} & set(marks)
    door = marks["105"]
    assert door.status == NO_ROW
    assert ("in the lettering of the 3 marks of the schedule found there "
            "(7 pt Helvetica-Bold)") in door.why
    assert "a plain number: view it" in door.why
    room = marks["203"]
    assert room.status == CANNOT_TELL and room.side == "row"
    assert room.why.startswith(
        "row 203 is found on A-102 (page 2) only lettered unlike this "
        "schedule's other marks there (8 pt Helvetica under or over a "
        "word, the others 7 pt Helvetica-Bold)")


def test_a_row_named_in_a_note_or_inside_a_range_cannot_be_told():
    _gt, result = _checked()
    marks = {i.mark: i for i in result.items}
    assert "inside a range written on A-102 (page 2)" in marks["204"].why
    assert "named in a line of text on A-102 (page 2)" in marks["205"].why
    assert "named in a line of text on M-101 (page 5)" in marks["EF-3"].why
    for mark in ("204", "205", "EF-3"):
        assert marks[mark].status == CANNOT_TELL


def test_what_is_not_a_schedule_is_left_alone():
    """The cover's sheet index, the revision blocks, the keynote list and
    the legend are tables with a mark-like first column: none is a
    schedule, so the legend's S-3 drawn on the roof plan is no item and
    its rows S-1 and S-2, drawn nowhere, are none either."""
    _gt, result = _checked()
    rows = {m for s in result.schedules for m in s.marks}
    assert not {"G-001", "A-101", "0", "1", "2", "3", "S-1", "S-2"} & rows
    assert not {"S-1", "S-2", "S-3", "1", "2"} & {
        i.mark for i in result.items}


def test_a_scanned_sheet_leaves_open_what_could_be_on_it():
    """A-102 scanned: nothing found only there is called missing, and no
    mark is called unscheduled, since a part of a schedule could be on
    it. EF-3 is still named in a note on a sheet that was read."""
    gt, result = _checked(scanned=(2,))
    assert _items(result) == sorted(TRUTH_A102_SCANNED)
    assert [r.page for r in result.unread] == [2]
    assert not result.with_status(NO_ROW) + result.with_status(NO_MARK)
    row = next(i for i in result.items if i.mark == "201")
    assert row.why == (
        "row 201 is drawn on none of the sheets that could be read; 1 "
        "sheet(s) could not be read (page(s) 2) and may show it")
    mark = next(i for i in result.items if i.mark == "W-4")
    assert mark.why.endswith(
        "; page(s) 2 could not be read and sit beside the schedule's "
        "pages: a part of the schedule there would not be seen")
    assert any("could not be read" in n for n in result.notes)


def test_a_set_with_no_schedule_says_so_and_flags_nothing():
    _gt, result = _checked(schedules=False)
    assert result.schedules == [] and result.items == []
    assert result.counts()["schedules"] == 0
    assert result.notes == [
        "no schedule was found in the text of the 6 sheet(s) that could be "
        "read"]


def test_a_document_with_no_drawing_sheets_is_an_empty_result():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "DOOR SCHEDULE", fontsize=12)
    page.insert_text((72, 100), "MARK   WIDTH   HEIGHT", fontsize=9)
    pdf = doc.tobytes()
    doc.close()
    with open_document(pdf) as d:
        result = d.schedule_marks()
    assert result.schedules == [] and result.items == []
    assert result.notes == ["no drawing sheets in this document"]


def test_the_scanned_whole_set_lists_every_sheet_unread():
    from planlens.testing.sheet_set_fixtures import _as_image
    scan = _as_image(_built().pdf, range(6))
    with open_document(scan) as doc:
        result = doc.schedule_marks()
    assert result.items == [] and result.schedules == []
    assert [r.page for r in result.unread] == list(range(6))
    assert result.notes == [
        "no schedule was found in the text of the 0 sheet(s) that could be "
        "read; 6 sheet(s) could not be read"]


# -- the mark's form ---------------------------------------------------------

@pytest.mark.parametrize("mark, shown, fits, misses", [
    ("F1", "F9", ["F4", "F10"], ["F100", "FF1", "1"]),
    ("FCU-1-1", "FCU-9-9", ["FCU-3-4", "FCU-12-1"], ["FCU-1", "AHU-1-1"]),
    ("LP-4A", "LP-9A", ["LP-4B", "LP-12C"], ["LP-4", "LP-4AB"]),
    ("W-3", "W-9", ["W-4", "W-12"], ["W3", "WD-3"]),
    ("EF-2", "EF-9", ["EF-7"], ["E-2", "EF-2A"]),
    ("101", "999", ["109", "407"], ["1010", "10", "101A"]),
    ("204B", "999A", ["205C"], ["205", "20B"]),
])
def test_a_marks_form_is_learnt_from_the_mark(mark, shown, fits, misses):
    rx, got = mark_form(mark)
    assert got == shown
    for token in [mark] + fits:
        assert re.fullmatch(rx, token), token
    for token in misses:
        assert not re.fullmatch(rx, token), token


def test_marks_with_no_form_are_matched_only_as_themselves():
    for mark in ("A", "AB", "3", "12", "MDP"):
        assert mark_form(mark) is None
    assert too_short("A") and too_short("12") and not too_short("F1")
    assert not too_short("MDP")
    assert plain("101") and plain("204B") and not plain("W-1")


def test_a_range_holds_the_marks_between_its_ends():
    assert _range_holds("204", "202", "205")
    assert _range_holds("F2", "F1", "F3")
    assert _range_holds("FCU-1-3", "FCU-1-1", "FCU-1-4")
    assert not _range_holds("206", "202", "205")
    assert not _range_holds("FCU-2-3", "FCU-1-1", "FCU-1-4")
    assert not _range_holds("204", "205", "202")
    assert ("202", "205") in _ranges_in("DOORS 202 TO 205 BY OTHERS")
    assert ("101", "108") in _ranges_in("SEE 101-108")
    assert ("W-1", "W-6") in _ranges_in("W-1 THRU W-6")
    # a project number and a date are not ranges
    assert _ranges_in("PROJECT NO. 24-117") == []
    assert all(not _range_holds("1", a, b)
               for a, b in _ranges_in("ISSUE 1  -  2026-03-02"))


# -- the guards, on sheets drawn here ----------------------------------------

def _plan_and_schedule(plan_marks, schedule_rows, extra=(),
                       header=("NO.", "ROOM", "WIDTH"), title="SCHEDULE"):
    """A plan (A-101) with ``plan_marks`` as ``(x, y, text, size, bold)``
    and ``extra`` pieces the same way, then a sheet (A-601) printing a
    table of ``schedule_rows`` under ``header`` and ``title``."""
    def plan(s, i):
        s.strip_block("A-101", "FIRST FLOOR PLAN", 1, 2)
        for x, y, text, size, bold in list(plan_marks) + list(extra):
            s.text(x, y, text, size, bold=bold)

    def schedule(s, i):
        s.strip_block("A-601", "SCHEDULES", 2, 2)
        table(s, 150, 220, title, header,
              [(r,) + ("X",) * (len(header) - 1) for r in schedule_rows],
              [80] * len(header))

    return sketch_pdf([plan, schedule])


def _result(pdf):
    with open_document(pdf) as doc:
        return doc.schedule_marks()


def _door_tags(numbers, y=600):
    return [(300 + 150 * i, y, n, 7, True) for i, n in enumerate(numbers)]


def test_two_or_three_numbers_in_the_marks_lettering_cannot_be_told():
    pdf = _plan_and_schedule(_door_tags(["101", "102", "103", "105",
                                         "106", "107"]),
                             ["101", "102", "103", "104"],
                             title="DOOR SCHEDULE")
    result = _result(pdf)
    plan_side = [i for i in result.items if i.side == "mark"]
    assert sorted(i.mark for i in plan_side) == ["105", "106", "107"]
    assert {i.status for i in plan_side} == {CANNOT_TELL}
    assert "3 numbers of the form 999 on A-101" in plan_side[0].why


def test_many_numbers_in_the_marks_lettering_are_one_item_for_the_sheet():
    dims = [(300 + 90 * i, 900, str(450 + 10 * i), 7, True)
            for i in range(8)]
    pdf = _plan_and_schedule(_door_tags(["101", "102", "103"]),
                             ["101", "102", "103"], extra=dims,
                             title="DOOR SCHEDULE")
    result = _result(pdf)
    assert len(result.items) == 1
    item = result.items[0]
    assert item.status == CANNOT_TELL and item.mark is None
    assert item.n_marks == 8 and item.marks == ["450", "460", "470",
                                                "480", "490", "500"]
    assert "something else on the sheet" in item.why
    shown = item.to_dict(result.names())
    assert shown["n_marks"] == 8 and "bbox" not in shown


def test_two_letterings_standing_alone_leave_a_plain_number_open():
    """The doors' numbers drawn in bold, and the same numbers again in a
    larger plain lettering standing alone (rooms numbered as the doors):
    neither is the only one standing alone, so 105 cannot be told."""
    rooms = [(300 + 150 * i, 400, n, 9, False)
             for i, n in enumerate(["101", "102", "103", "105"])]
    pdf = _plan_and_schedule(_door_tags(["101", "102", "103"]),
                             ["101", "102", "103"], extra=rooms,
                             title="DOOR SCHEDULE")
    result = _result(pdf)
    assert [(i.mark, i.status) for i in result.items] == [
        ("105", CANNOT_TELL)]
    assert "drawn in 2 letterings" in result.items[0].why


def test_a_numbers_lettering_needs_two_marks_on_the_sheet():
    """One scheduled door on the sheet sets no lettering: the unscheduled
    number beside it is not looked at. (A note in text: a plan whose only
    text is two tags may be lettered as lines, PL-43.)"""
    pdf = _plan_and_schedule(_door_tags(["101", "109"]), ["101", "102"],
                             extra=[(700, 300, "GENERAL NOTES: VERIFY ON "
                                     "SITE", 8, False)],
                             title="DOOR SCHEDULE")
    result = _result(pdf)
    assert [(i.mark, i.status) for i in result.items] == [("102", NO_MARK)]


def test_a_family_of_marks_with_no_rows_is_in_doubt():
    tags = [(300 + 150 * i, 600, t, 7, False)
            for i, t in enumerate(["W-1", "W-6", "W-7", "W-8"])]
    pdf = _plan_and_schedule(tags, ["W-1", "W-2"],
                             header=("MARK", "WIDTH", "HEIGHT"),
                             title="WINDOW SCHEDULE")
    result = _result(pdf)
    plan_side = sorted((i.mark, i.status) for i in result.items
                       if i.side == "mark")
    assert plan_side == [("W-6", CANNOT_TELL), ("W-7", CANNOT_TELL),
                         ("W-8", CANNOT_TELL)]
    first = next(i for i in result.items if i.side == "mark")
    assert first.why.startswith(
        "3 different marks of the form W-9 are drawn with no row, against "
        "1 of its rows drawn")
    assert [(i.mark, i.status) for i in result.items
            if i.side == "row"] == [("W-2", NO_MARK)]


def test_a_heading_row_on_every_sheet_is_the_title_blocks():
    """A NO. / DATE / DESCRIPTION block at the same place on four sheets,
    with no title over it, is a revision block, not a schedule."""
    def sheet(s, i):
        s.strip_block(f"A-10{i + 1}", "PLAN", i + 1, 4)
        table(s, 1700, 1200, None, ("NO.", "DATE", "DESCRIPTION"),
              [("1", "2031-01-12", "ISSUED"), ("2", "2031-02-12", "ISSUED")],
              (40, 80, 160))
    result = _result(sketch_pdf([sheet] * 4))
    assert result.schedules == [] and result.items == []


def test_an_untitled_table_needs_a_mark_heading_and_three_columns():
    def sheet(s, i):
        s.strip_block("A-601", "SCHEDULES", 1, 1)
        # two columns under a mark heading: a legend's shape, not taken
        table(s, 150, 220, None, ("MARK", "DESCRIPTION"),
              [("P-1", "PAINT"), ("P-2", "STAIN")], (60, 200))
        # three columns under a heading that is not a mark heading
        table(s, 150, 500, None, ("ITEM", "SIZE", "NOTE"),
              [("B-1", "100", "X"), ("B-2", "200", "X")], (60, 80, 80))
    result = _result(sketch_pdf([sheet]))
    assert result.schedules == []
