"""A made-up drawing set with schedules and the marks they list, for the
schedule-marks check (:mod:`planlens.document.schedules`).

Six 34 x 22 in sheets, each with a title block down the right edge and a
revision block (NO. / DATE / DESCRIPTION, under "REVISIONS"):

0. G-001 COVER SHEET: a sheet index (NO. / TITLE / REV, titled SHEET
   INDEX) and general notes.
1. A-101 FIRST FLOOR PLAN: rooms tagged with a name over a number (110,
   120, 130); doors 101, 102, 103 and 105 tagged alone in bold; windows
   W-1, W-2 and W-4; dimensions (600, 915, 4200) in plain lettering; grid
   bubbles; a callout bubble to A-601.
2. A-102 SECOND FLOOR PLAN: rooms 201, 202 and 203 (the doors' own numbers,
   as many firms number them); doors 201 and 202; windows W-2 and W-3; a
   note "DOORS 202 TO 205 ...".
3. A-601 DOOR SCHEDULE: the door schedule (NO. / ROOM / WIDTH / HEIGHT /
   TYPE) rows 101 to 104, and the window schedule (MARK / WIDTH / HEIGHT /
   GLAZING) rows W-1, W-2, W-3 and W-5.
4. A-602 DOOR SCHEDULE (CONTINUED): rows 201 to 205.
5. M-101 ROOF PLAN: fans EF-1 and EF-2 tagged; an equipment table with no
   title (TAG / SERVES / CFM / HP) rows EF-1 to EF-3; a note naming EF-3; a
   keynote list (NO. / DESCRIPTION / SPEC, titled KEYNOTES) with keynote
   tags 1 and 2; a legend (MARK / DESCRIPTION / NOTE, titled LEGEND) rows
   S-1 and S-2 with an S-3 tagged on the plan.

What the check finds in it is :data:`TRUTH` (and, with A-102 scanned,
:data:`TRUTH_A102_SCANNED`): two marks drawn with no row (105, a plain
number in the doors' lettering; W-4), two rows drawn nowhere (104, W-5),
and four rows the text cannot settle (203 is on A-102 only as a room's
number; 204 is only inside the note's range; 205 and EF-3 are only named
in notes). The room numbers, the dimensions, the keynotes, the legend's
marks, the sheet index and the revision blocks are never items.
``schedules=False`` draws the same set with no schedule at all.

Every name, number and date here is made up.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from planlens.testing.sheet_set_fixtures import SketchSheet, _as_image

SHEETS = (("G-001", "COVER SHEET"), ("A-101", "FIRST FLOOR PLAN"),
          ("A-102", "SECOND FLOOR PLAN"), ("A-601", "DOOR SCHEDULE"),
          ("A-602", "DOOR SCHEDULE (CONTINUED)"), ("M-101", "ROOF PLAN"))
DOOR_HEADER = ("NO.", "ROOM", "WIDTH", "HEIGHT", "TYPE")
DOORS_FIRST = ("101", "102", "103", "104")
DOORS_SECOND = ("201", "202", "203", "204", "205")
WINDOWS = ("W-1", "W-2", "W-3", "W-5")
FANS = ("EF-1", "EF-2", "EF-3")

#: ``(mark, status, side, sheet)`` of every item the check must give.
TRUTH = (
    ("105", "no_row", "mark", "A-101"),
    ("W-4", "no_row", "mark", "A-101"),
    ("104", "no_mark", "row", "A-601"),
    ("W-5", "no_mark", "row", "A-601"),
    ("203", "cannot_tell", "row", "A-602"),
    ("204", "cannot_tell", "row", "A-602"),
    ("205", "cannot_tell", "row", "A-602"),
    ("EF-3", "cannot_tell", "row", "M-101"),
)
#: The same with A-102 (page 2) scanned: what was found only there, and
#: every mark or row not found, may be on it; EF-3 is still named in a note.
TRUTH_A102_SCANNED = (
    ("105", "may_be_on_unread_sheet", "mark", "A-101"),
    ("W-4", "may_be_on_unread_sheet", "mark", "A-101"),
    ("104", "may_be_on_unread_sheet", "row", "A-601"),
    ("W-3", "may_be_on_unread_sheet", "row", "A-601"),
    ("W-5", "may_be_on_unread_sheet", "row", "A-601"),
    ("201", "may_be_on_unread_sheet", "row", "A-602"),
    ("202", "may_be_on_unread_sheet", "row", "A-602"),
    ("203", "may_be_on_unread_sheet", "row", "A-602"),
    ("204", "may_be_on_unread_sheet", "row", "A-602"),
    ("205", "may_be_on_unread_sheet", "row", "A-602"),
    ("EF-3", "cannot_tell", "row", "M-101"),
)


@dataclass
class ScheduleSetGT:
    """The built set: its PDF, its sheets, and what the check must say."""
    pdf: bytes
    sheets: Tuple[Tuple[str, str], ...]
    truth: Tuple[Tuple[str, str, str, str], ...]
    scanned: Tuple[int, ...] = ()
    #: ``(title or None, sheets, marks)`` of each schedule drawn.
    schedules: List[Tuple[Optional[str], Tuple[str, ...], Tuple[str, ...]]] \
        = field(default_factory=list)


def table(s: SketchSheet, x: float, y: float, title: Optional[str],
          header: Sequence[str], rows: Sequence[Sequence[str]],
          widths: Sequence[float], size: float = 8.0,
          row_h: float = 17.0) -> None:
    """A ruled table: ``title`` above it, the header row, then the rows,
    each cell its own piece of text."""
    if title:
        s.text(x, y - 8, title, 12, bold=True)
    total = sum(widths)
    for r, row in enumerate([tuple(header)] + [tuple(r) for r in rows]):
        top = y + r * row_h
        s.page.draw_line((x, top), (x + total, top), width=0.5)
        cx = x
        for c, cell in enumerate(row):
            if cell:
                s.text(cx + 4, top + row_h - 5, cell, size, bold=r == 0)
            cx += widths[c]
    bottom = y + (len(rows) + 1) * row_h
    s.page.draw_line((x, bottom), (x + total, bottom), width=0.9)
    cx = x
    for w in list(widths) + [0]:
        s.page.draw_line((cx, y), (cx, bottom), width=0.4)
        cx += w


def _frame(s: SketchSheet, nth: int) -> None:
    number, title = SHEETS[nth]
    s.strip_block(number, title, nth + 1, len(SHEETS))
    x0 = s.w - 36.0 - 288.0
    # the revision block's own headings, under "REVISIONS" (y 700)
    for dx, head in ((14, "NO."), (44, "DATE"), (110, "DESCRIPTION")):
        s.text(x0 + dx, 714, head, 6, bold=True)


def _grid(s: SketchSheet) -> None:
    for x, letter in ((300.0, "A"), (900.0, "B"), (1500.0, "C")):
        s.page.draw_circle((x, 150), 14.0, width=0.7)
        s.centred(x, 153.5, letter, 10)
    for y, digit in ((200.0, "1"), (900.0, "2")):
        s.page.draw_circle((250, y), 14.0, width=0.7)
        s.centred(250, y + 3.5, digit, 10)


def _room(s: SketchSheet, cx: float, y: float, name: str, number: str):
    s.centred(cx, y, name, 8)
    s.centred(cx, y + 11.0, number, 8)


def _door(s: SketchSheet, cx: float, y: float, number: str) -> None:
    s.page.draw_circle((cx, y - 2.5), 10.0, width=0.6)
    s.centred(cx, y, number, 7, bold=True)


def _cover(s: SketchSheet) -> None:
    table(s, 150, 220, "SHEET INDEX", ("NO.", "TITLE", "REV"),
          [(n, t, "0") for n, t in SHEETS], (90, 360, 50))
    s.text(800, 200, "GENERAL NOTES", 12, bold=True)
    s.text(800, 222, "1. ALL DIMENSIONS ARE IN MILLIMETRES.", 8)
    s.text(800, 236, "2. DOOR AND WINDOW MARKS REFER TO THE SCHEDULES ON "
                     "A-601 AND A-602.", 8)


def _first_floor(s: SketchSheet) -> None:
    _grid(s)
    for cx, name, number in ((500, "OFFICE", "110"), (900, "LOBBY", "120"),
                             (1300, "STORAGE", "130")):
        _room(s, cx, 400, name, number)
    for cx, y, number in ((450, 600, "101"), (850, 600, "102"),
                          (1250, 600, "103"), (1500, 800, "105")):
        _door(s, cx, y, number)
    for x, mark in ((400, "W-1"), (800, "W-2"), (1200, "W-4")):
        s.text(x, 250, mark, 6)
    for x, y, dim in ((600, 1000, "915"), (1000, 1000, "4200"),
                      (1400, 1000, "600")):
        s.text(x, y, dim, 7)
    s.bubble(1700, 500, "2", "A-601")
    s.view_title(330, 1300, "1", "A-101", "FIRST FLOOR PLAN")


def _second_floor(s: SketchSheet) -> None:
    _grid(s)
    for cx, name, number in ((500, "OFFICE", "201"), (900, "OFFICE", "202"),
                             (1300, "COPY", "203")):
        _room(s, cx, 400, name, number)
    for cx, number in ((450, "201"), (850, "202")):
        _door(s, cx, 600, number)
    for x, mark in ((800, "W-2"), (1200, "W-3")):
        s.text(x, 250, mark, 6)
    s.text(1600, 300, "DOORS 202 TO 205 ARE SHOWN ON THE ENLARGED PLAN "
                      "BY OTHERS.", 7)
    s.view_title(330, 1300, "1", "A-102", "SECOND FLOOR PLAN")


def _door_rows(numbers: Sequence[str]) -> List[Tuple[str, ...]]:
    return [(n, "OFFICE", "915", "2135", "A") for n in numbers]


def _schedule_first(s: SketchSheet) -> None:
    table(s, 150, 220, "DOOR SCHEDULE", DOOR_HEADER,
          _door_rows(DOORS_FIRST), (60, 170, 80, 80, 60))
    table(s, 900, 220, "WINDOW SCHEDULE",
          ("MARK", "WIDTH", "HEIGHT", "GLAZING"),
          [(w, "1200", "1500", "DOUBLE") for w in WINDOWS],
          (70, 80, 80, 120))
    s.text(150, 600, "DOOR NOTES", 12, bold=True)
    s.text(150, 622, "1. DOOR TYPE A: SINGLE, FLUSH.", 8)


def _schedule_second(s: SketchSheet) -> None:
    table(s, 150, 220, "DOOR SCHEDULE (CONTINUED)", DOOR_HEADER,
          _door_rows(DOORS_SECOND), (60, 170, 80, 80, 60))


def _roof(s: SketchSheet, schedules: bool) -> None:
    _grid(s)
    for x, mark in ((500, "EF-1"), (900, "EF-2")):
        s.page.draw_rect((x - 20, 380, x + 20, 420), width=0.8)
        s.centred(x, 440, mark, 7, bold=True)
    for x, n in ((600, "1"), (1000, "2")):
        s.page.draw_circle((x, 700), 9.0, width=0.6)
        s.centred(x, 703, n, 7)
    s.centred(1300, 520, "S-3", 7)
    s.text(1500, 1100, "EF-3 IS ABOVE THE CEILING OF THE CORE.", 8)
    if schedules:
        table(s, 1500, 220, None, ("TAG", "SERVES", "CFM", "HP"),
              [(f, "TOILETS", "400", "0.5") for f in FANS],
              (70, 120, 70, 50))
    table(s, 1500, 600, "KEYNOTES", ("NO.", "DESCRIPTION", "SPEC"),
          [("1", "ROOF DRAIN", "22 14 00"), ("2", "CURB", "07 72 00"),
           ("3", "HATCH", "07 72 33")], (40, 200, 80))
    table(s, 1500, 800, "LEGEND", ("MARK", "DESCRIPTION", "NOTE"),
          [("S-1", "SIGN, WALL", ""), ("S-2", "SIGN, DOOR", "")],
          (60, 200, 80))
    s.view_title(330, 1300, "1", "M-101", "ROOF PLAN")


def build_schedule_set(scanned: Sequence[int] = (),
                       schedules: bool = True) -> ScheduleSetGT:
    """The set as a PDF with its truth. ``scanned`` pages are turned into
    images with no text layer; ``schedules=False`` leaves every schedule
    out (the truth is then empty)."""
    import pymupdf
    doc = pymupdf.open()
    try:
        for nth in range(len(SHEETS)):
            s = SketchSheet(doc)
            _frame(s, nth)
            if nth == 0:
                _cover(s)
            elif nth == 1:
                _first_floor(s)
            elif nth == 2:
                _second_floor(s)
            elif nth == 3 and schedules:
                _schedule_first(s)
            elif nth == 4 and schedules:
                _schedule_second(s)
            elif nth == 5:
                _roof(s, schedules)
        pdf = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    finally:
        doc.close()
    scanned = tuple(sorted(int(i) for i in scanned))
    if scanned:
        pdf = _as_image(pdf, scanned)
    if not schedules:
        truth: Tuple = ()
    elif scanned == (2,):
        truth = TRUTH_A102_SCANNED
    elif not scanned:
        truth = TRUTH
    else:
        raise ValueError("the truth is written for no scan or A-102 only")
    gt = ScheduleSetGT(pdf, SHEETS, truth, scanned)
    if schedules:
        gt.schedules = [
            ("DOOR SCHEDULE", ("A-601", "A-602"),
             DOORS_FIRST + DOORS_SECOND),
            ("WINDOW SCHEDULE", ("A-601",), WINDOWS),
            (None, ("M-101",), FANS)]
    return gt


__all__ = ["ScheduleSetGT", "build_schedule_set", "table", "SHEETS",
           "TRUTH", "TRUTH_A102_SCANNED"]
