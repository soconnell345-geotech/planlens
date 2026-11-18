"""A drawing set's schedules, and the marks on its sheets checked against them.

A schedule lists the marks a set tags on its sheets: door 204, window W-3,
footing F2, fan coil unit FCU-1-4, panel LP-4A. Two checks follow, and both
cross sheets: every mark tagged on a sheet should have a row, and every row
should be tagged somewhere. A reader who reads the plans and the schedules
in different places compares nothing; this module compares them over the
whole set, from the text layer, without knowing any kind of mark.

WHAT IS A SCHEDULE. Nothing here knows a mark type or a schedule's name.
On each sheet that has a usable text layer, a table is a schedule when, in
upright lettering:

* its title (a line above its column headings, past a row of group
  headings, or one below its last row) says SCHEDULE, it has at least two
  column headings, and its first column holds marks; or
* it has no such title, its mark column's heading says it holds marks
  (MARK, TAG, NO., NUMBER, ID or #, alone or after a word: "DOOR NO.",
  "EQUIPMENT TAG"), it has at least three column headings and two rows,
  and no line above it or below it (a view title under it) calls it a
  legend, a key or keynote list, notes, revisions, issues, abbreviations,
  symbols, an index or a drawing list.

The mark column is the one under a heading that says it holds marks, else
the first column. It is read down from the heading while the lettering
keeps its size (within a fifth of the first row's) and the cells their
spacing (no step over about twice the rows'). A cell that is one mark
("101", "F1", "FCU-1-1", "LP-4A", "W-3", "MDP") is a row; a cell holding
several marks of the column's forms ("EF-3, EF-4", "W-1 THRU W-3") gives
each; a cell of words (a sub-heading, "SECOND FLOOR") is passed over; a
cell with figures and no mark of the column's forms is passed over and
kept as not read. A sheet number of the set, a callout bubble or a mark of
one or two characters after longer ones (a view title under the table)
ends the column. A column whose values repeat (a TYPE column) is not a
mark column. Heading rows printed at the same place on most sheets, and an
untitled mark heading at one place on three sheets or more, are the title
block's (a revision table), not a schedule; a heading row is cut at a gap
much wider than its others (drawing text on its line); a table whose marks
are mostly the set's own sheet numbers is a drawing list. A schedule
printed in parts (a second sheet titled "... (CONTINUED)", or a second
block of the same columns) is one schedule.

THE MARK'S FORM is learnt from each schedule's own rows. Letters before the
first digit are kept as they are; each run of digits may be one digit
longer than the rows' ("F1" to "F3" read as F and one or two digits, so F4
and F10 fit, FCU-3-4 fits FCU-1-1); letters after the digits stand for any
letters. A mark with no letter before its digits ("101", "204B") is a plain
number: room numbers, dimensions and detail numbers share that form, so it
is handled apart (below). A mark of one or two characters with no digit, or
no letter ("A", "3", "12") is not looked for as a mark with no row, nor
counted found where it is drawn: grid lines, view numbers, keynotes and
circuits read the same. The schedule says how many rows that leaves
(``rows_not_looked_for``), and a row of one drawn alone on no sheet is
``cannot_tell``.

WHAT IS SAID. Each item is one fact about this file, with the sheet and the
page (0-based ``page``, 1-based ``pdf_page``) and the schedule:

* ``no_row``: a mark drawn on a sheet fits the form of a schedule's marks
  and no schedule of that form has a row for it;
* ``no_mark``: a row of a schedule titled SCHEDULE whose mark is drawn on
  no sheet, as a piece of text of its own, outside the schedules, and named
  in no note or range, where at least half the schedule's rows are found,
  every sheet's drawing is read, and, where rows found are read only from
  hidden CAD text, every sheet that could show the mark carries such text;
* ``may_be_on_unread_sheet``: a row not found where some sheets' drawing is
  not read: they could not be read (no text layer, or one that does not
  read as the sheet does), have their drawing in an image, or have no text
  outside their title block (their drawing lettered as lines with no text
  kept); and a mark with no row where there is such a sheet, beside the
  schedule's pages or away from them (a part of the schedule may be
  there). The item names those pages;
* ``cannot_tell``: the text does not settle it; the ``why`` says what was
  seen (a row's mark found only inside a note or a range; a row of a table
  with no SCHEDULE title, which may be a list whose entries need not all be
  drawn; a row of a schedule fewer than half of whose rows are found in
  the sheets' text, as where its marks are drawn as lines; a row of a
  schedule whose rows found are read only from the hidden text a CAD
  program keeps with lettering drawn as lines, where a read sheet that
  could show it (of the discipline of the sheets those rows are on, or
  with no number read) carries none of that text, since a mark drawn as
  lines there is not read; a row of one or two characters drawn alone on no
  sheet; a mark named only in a note; a plain number whose lettering does
  not settle it, or settled only by which lettering stands alone, or
  borrowed from other sheets; more marks of a form with no row than the
  form's rows found, so something else shares the form; a mark with no
  row where a cell of its schedule's mark column could not be read as
  marks).

A mark counts as drawn where a piece of text is that mark and nothing else,
on any sheet, in any reading direction, outside every schedule, not in a
callout bubble and not the sheet's own number. Lettering a CAD program keeps
only as hidden annotations ("AutoCAD SHX Text", its glyphs drawn as lines)
is read with the text.

A PLAIN NUMBER is a mark only by its lettering. On each sheet the schedule's
marks found there set the lettering its marks are drawn in: the font, the
size, and whether a word is lettered directly over or under them (a room's
name over its number). A number of the form with no row is ``no_row`` only
where at least two of the schedule's marks are on that sheet in one
lettering, it is in that lettering, and it is the only such number on the
sheet; two or three such are each ``cannot_tell``; more are one
``cannot_tell`` for the sheet (room numbers or dimensions lettered alike).
A number in another lettering is not counted. Where the schedule's numbers
are drawn on a sheet in two letterings or more (door numbers that are the
rooms' numbers too), the one that stands alone is taken as its marks' (a
name over a number labels the named thing), but a number with no row in
it is then ``cannot_tell`` (a room numbered as its door may be lettered so
too); where none or several stand alone, each number of the form in those
letterings with no row is ``cannot_tell``.
A row whose number is found
on a sheet only in another lettering than the schedule's other marks there
is ``cannot_tell``. On a sheet that shows fewer than two of a schedule's
marks, a number of the form is looked for only in the lettering the marks
have on the sheets that settle it (all one lettering, none left open), on
a sheet of their discipline, and only as ``cannot_tell``: that sheet does
not confirm the lettering (a floor whose doors the schedule leaves out
reads the same as room numbers or dimensions lettered alike).

It reads what the text layer holds. A sheet with no usable text has no
schedule and no marks here; the result lists those sheets; a row that could
be drawn on one is never called missing, nor a mark's row (a part of its
schedule may be there, beside its other pages or away from them). A
readable sheet with no text outside its title block, and one whose drawing
is an image, are taken the same way.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from collections import Counter
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from planlens.document.crossrefs import _direction, _page_runs
from planlens.document.sheets import (SheetIndex, SheetRow, _in_band,
                                      discipline, normalize_number)

BBox = Tuple[float, float, float, float]

#: The word that says a table is a schedule.
SCHEDULE_WORD = re.compile(r"\bSCHEDULES?\b", re.I)
#: A column heading that says the column holds marks, alone or after up to
#: two words ("MARK", "NO.", "DOOR NO.", "EQUIPMENT TAG").
MARK_HEADING = re.compile(
    r"^(?:[A-Z][A-Z.'/&-]*\s+){0,2}(?:MARK|TAG|NO\.?|NUMBER|NUM\.?|ID|#)$",
    re.I)
#: A title that names a table something other than a schedule (it counts
#: only where the title does not say SCHEDULE).
NOT_A_SCHEDULE = re.compile(
    r"\b(?:LEGENDS?|KEY\s*NOTES?|KEYS?|NOTES|REVISIONS?|ISSUES?|"
    r"ABBREVIATIONS?|SYMBOLS?|INDEX|DRAWING\s+LIST|SHEET\s+LIST)\b", re.I)
#: A line that sends the reader elsewhere is a note, not a title.
_REFERRAL = re.compile(r"^\s*(?:SEE|REFER|PER|AS|FOR)\b", re.I)
#: "CONTINUED" and its short forms, left out of a title to join the parts
#: of one schedule.
_CONTINUED = re.compile(r"\(?\s*\b(?:CONTINUED|CONT'?D|CONT)\b\.?\s*\)?",
                        re.I)
#: One mark as a schedule's row prints it: letters and digits, in up to four
#: runs joined by "-", "." or "/".
MARK = re.compile(r"^[A-Z0-9]{1,6}(?:[-./][A-Z0-9]{1,6}){0,3}$")
#: Words that fill a cell without being a mark.
_NOT_MARKS = frozenset({"TYP", "TYP.", "SIM", "SIM.", "N/A", "NA", "NONE",
                        "ALL", "ETC", "SEE", "NO", "NO.", "YES", "VARIES"})
#: The pieces of a mark: letter runs, digit runs, separators.
_RUNS = re.compile(r"[A-Z]+|\d+|[-./]")
#: Candidate marks inside a line of text.
_TOKENS = re.compile(r"(?<![A-Z0-9])[A-Z0-9]+(?:[-./][A-Z0-9]+)*(?![A-Z0-9])")
#: A range written out: "101 TO 108", "F1 THRU F4", "W-1 - W-6".
_RANGE_WORDS = re.compile(
    r"(?<![A-Z0-9])([A-Z0-9]+(?:[-./][A-Z0-9]+)*)\s*"
    r"(?:\bTO\b|\bTHRU\b|\bTHROUGH\b|\s[-\u2013]\s|\u2013)\s*"
    r"([A-Z0-9]+(?:[-./][A-Z0-9]+)*)(?![A-Z0-9])")

NO_ROW = "no_row"
NO_MARK = "no_mark"
MAYBE_UNREAD = "may_be_on_unread_sheet"
CANNOT_TELL = "cannot_tell"
#: Every status, in the order results list them.
STATUSES = (NO_ROW, NO_MARK, MAYBE_UNREAD, CANNOT_TELL)
#: ``side`` of an item: a mark drawn on a sheet, or a schedule's row.
SIDE_MARK, SIDE_ROW = "mark", "row"

#: A heading row printed at the same place on at least this share of the
#: readable sheets (and on three or more) is the title block's.
STATIC_SHARE = 0.5
#: An untitled heading at one place on this many sheets is the title
#: block's (a consultant's revision table), however few sheets that is.
STATIC_MIN = 3
#: A family of marks of one form is in doubt where more of them have no row
#: than this share of the form's rows found (and at least three).
FAMILY_SHARE = 0.5
#: A schedule whose rows are found on the sheets fewer than this share of
#: the time is not taken as read there: its marks may be drawn as lines.
FOUND_SHARE = 0.5
#: A readable sheet this much covered by images has its drawing in an
#: image: marks drawn there are not in its text.
PICTURED = 0.5
#: A readable sheet with fewer characters of text than this outside its
#: title block (hidden CAD text counted) has its drawing lettered as lines
#: with no text kept, if lettered at all: marks drawn there are not in its
#: text. The toolkit's ``FIELD_TEXT_MIN_CHARS`` (PL-39) is the same measure
#: for a text search (which does not count hidden CAD text).
FIELD_CHARS = 10


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def mark_of(text: str) -> str:
    """``text`` as a mark is compared: upper case, no spaces."""
    return re.sub(r"\s+", "", str(text or "")).upper()


def _letters(text: str) -> int:
    return len(re.findall(r"[A-Za-z]", text))


# ---------------------------------------------------------------------------
# The form of a schedule's marks
# ---------------------------------------------------------------------------

def plain(mark: str) -> bool:
    """Whether ``mark`` is a plain number: no letter before its digits."""
    return bool(mark) and mark[0].isdigit()


def too_short(mark: str) -> bool:
    """A mark of one or two characters with no digit or no letter ("A",
    "AB", "3", "12"): grid lines, view numbers, keynotes and circuits read
    the same, so it is not looked for."""
    return len(mark) <= 2 and (mark.isdigit() or mark.isalpha())


def mark_form(mark: str) -> Optional[Tuple[str, str]]:
    """``(regex, shown)`` of the marks shaped like ``mark``, or ``None`` for
    a mark that is matched only as itself (letters alone, or too short).
    Letters before the first digit are kept; each digit run may be one
    digit longer (for a plain number, exactly as long); letters after a
    digit stand for any letters. ``shown`` writes a digit as 9 and a letter
    standing for any as A ("FCU-9-9", "999A")."""
    if too_short(mark) or not any(c.isdigit() for c in mark):
        return None
    pattern, shown = [], []
    seen_digit = False
    is_plain = plain(mark)
    for run in _RUNS.findall(mark):
        if run.isalpha():
            if seen_digit:
                pattern.append(f"[A-Z]{{{len(run)}}}")
                shown.append("A" * len(run))
            else:
                pattern.append(re.escape(run))
                shown.append(run)
        elif run.isdigit():
            seen_digit = True
            n = len(run)
            pattern.append(rf"\d{{{n}}}" if is_plain else rf"\d{{1,{n + 1}}}")
            shown.append("9" * n)
        else:
            pattern.append(re.escape(run))
            shown.append(run)
    return "".join(pattern), "".join(shown)


# ---------------------------------------------------------------------------
# The schedules
# ---------------------------------------------------------------------------

class _Piece:
    """An upright line of text as a table is read: its box, size, text."""
    __slots__ = ("line", "text", "bbox", "size", "h")

    def __init__(self, line):
        self.line = line
        self.text = _clean(line.text)
        self.bbox: BBox = tuple(float(v) for v in line.bbox)
        self.h = max(1.0, self.bbox[3] - self.bbox[1])
        self.size = float(getattr(line, "size", None) or self.h * 0.8)

    @property
    def cy(self) -> float:
        return (self.bbox[1] + self.bbox[3]) / 2.0


def _like_size(a: _Piece, b: _Piece) -> bool:
    return 0.6 <= a.size / b.size <= 1.7


def _title_like(text: str) -> bool:
    return (_letters(text) >= 3 and len(text.split()) <= 8
            and not _REFERRAL.match(text))


def _header_row(anchor: _Piece, pieces: Sequence[_Piece]) -> List[_Piece]:
    """The column headings on ``anchor``'s level: pieces centred on its
    line, of a like size, each within reach of the next; cut at a gap much
    wider than the others (drawing text on the same line)."""
    level = [p for p in pieces
             if abs(p.cy - anchor.cy) <= 0.5 * anchor.h
             and _like_size(p, anchor)]
    level.sort(key=lambda p: p.bbox[0])
    at = level.index(anchor)
    reach = 30.0 * anchor.size
    row = [anchor]
    for step in (1, -1):
        i, last = at + step, anchor
        while 0 <= i < len(level):
            p = level[i]
            gap = (p.bbox[0] - last.bbox[2]) if step > 0 else \
                (last.bbox[0] - p.bbox[2])
            if gap > reach:
                break
            row.append(p)
            last = p
            i += step
    row.sort(key=lambda p: p.bbox[0])
    if len(row) >= 3:
        gaps = [row[i + 1].bbox[0] - row[i].bbox[2]
                for i in range(len(row) - 1)]
        wide = max(3.0 * median(gaps), 8.0 * anchor.size)
        lo = hi = row.index(anchor)
        while lo > 0 and gaps[lo - 1] <= wide:
            lo -= 1
        while hi < len(gaps) and gaps[hi] <= wide:
            hi += 1
        row = row[lo:hi + 1]
    return row


def _split_last(mark: str) -> Optional[Tuple[str, int, str, int]]:
    """``(head, number, tail, width)`` of ``mark`` about its last digit
    run ("FCU-1-3" -> ("FCU-1-", 3, "", 1)); ``None`` with no digit."""
    found = list(re.finditer(r"\d+", mark))
    if not found:
        return None
    last = found[-1]
    return (mark[:last.start()], int(last.group(0)), mark[last.end():],
            len(last.group(0)))


def _expand(a: str, b: str, most: int = 60) -> List[str]:
    """The marks of the range ``a`` to ``b`` ("W-1", "W-3" -> W-1, W-2,
    W-3); just the two ends where they do not make a range."""
    sa, sb = _split_last(a), _split_last(b)
    if (sa is None or sb is None or sa[0] != sb[0] or sa[2] != sb[2]
            or not 0 < sb[1] - sa[1] <= most):
        return [a, b]
    return [f"{sa[0]}{str(n).zfill(sa[3])}{sa[2]}"
            for n in range(sa[1], sb[1] + 1)]


def _cell_marks(text: str, shapes: Sequence[Any]) -> List[str]:
    """The marks a cell of a mark column holds that fit ``shapes`` (the
    forms of the column's rows so far; before any, a mark with a figure):
    "EF-3, EF-4" gives both, "W-1 THRU W-3" each of W-1 to W-3, "W-3
    (ALT)" W-3, "SECOND FLOOR" none."""
    def fits(tok: str) -> bool:
        if tok in _NOT_MARKS or not MARK.match(tok) or too_short(tok):
            return False
        if shapes:
            return any(rx.fullmatch(tok) for rx in shapes)
        return any(c.isdigit() for c in tok)

    out: List[str] = []
    for a, b in _ranges_in(text):
        if fits(a) and fits(b):
            out.extend(_expand(a, b))
    out.extend(tok for tok in _TOKENS.findall(text) if fits(tok))
    return list(dict.fromkeys(out))


def _column(anchor: _Piece, header: Sequence[_Piece],
            pieces: Sequence[_Piece], known: FrozenSet[str] = frozenset(),
            bubbles: Sequence[BBox] = ()
            ) -> Tuple[List[Tuple[str, _Piece]], List[_Piece], List[str]]:
    """The marks under ``anchor``: the cells below it over its span, read
    down while their lettering and spacing hold. A cell that is one mark is
    a row; a cell holding several marks of the column's forms gives each; a
    cell of words only (a sub-heading such as "SECOND FLOOR") is passed
    over; a cell with figures and no mark of the column's forms is passed
    over and kept as not read. A sheet number of the set or a callout
    bubble ends the column (a view title printed under the table), as does
    a lettering size a fifth off the first row's, a step of more than
    about twice the rows' spacing, or a mark of one or two characters
    after longer ones (a view number). ``(marks, cells, unread)``: ``(mark,
    cell)`` pairs, every cell read, and the text of the cells not read."""
    pad = 0.5 * anchor.size
    lo, hi = anchor.bbox[0] - pad, anchor.bbox[2] + pad
    held = set(map(id, header))
    below = sorted((p for p in pieces
                    if id(p) not in held
                    and p.bbox[1] >= anchor.bbox[3] - 0.3 * anchor.h
                    and p.bbox[0] < hi and p.bbox[2] > lo
                    and _like_size(p, anchor)),
                   key=lambda p: (p.bbox[1], p.bbox[0]))
    marks: List[Tuple[str, _Piece]] = []
    cells: List[_Piece] = []
    unread: List[str] = []
    shapes: Dict[str, Any] = {}
    last_top = anchor.bbox[1]
    steps: List[float] = []
    first: Optional[_Piece] = None
    for p in below:
        step = p.bbox[1] - last_top
        same_row = bool(cells) and step < 0.5 * min(p.h, cells[-1].h)
        if not same_row:
            if not steps:
                limit = 4.5 * anchor.h
            elif len(steps) == 1:
                limit = max(3.2 * steps[0], 2.5 * anchor.h)
            else:
                limit = max(2.2 * median(steps[1:]), 1.6 * anchor.h)
            if step > limit:
                break
        if first is not None and abs(p.size / first.size - 1.0) > 0.2:
            break
        token = p.text.upper()
        if mark_of(token) in known or _boxed(p.bbox, bubbles):
            break
        if " " not in token and MARK.match(token) and token not in _NOT_MARKS:
            if marks and too_short(token) and not too_short(marks[-1][0]):
                break                     # a view number under the table
            got = [token]
        else:
            got = _cell_marks(token, list(shapes.values()))
        if got:
            for m in got:
                m = mark_of(m)
                marks.append((m, p))
                form = mark_form(m)
                if form is not None and form[0] not in shapes:
                    shapes[form[0]] = re.compile(form[0])
            if first is None:
                first = p
        elif re.search(r"\d", token):
            if not marks:
                break                     # the column does not hold marks
            unread.append(p.text)
        elif not marks and len(cells) >= 2:
            break
        cells.append(p)
        if not same_row:
            steps.append(step)
            last_top = p.bbox[1]
    while cells and not any(c is cells[-1] for _m, c in marks) \
            and cells[-1].text not in unread:
        cells.pop()                       # words after the last row
    return marks, cells, unread


def _titles_near(header: Sequence[_Piece], cells: Sequence[_Piece],
                 pieces: Sequence[_Piece]) -> List[_Piece]:
    """The lines that may title the table, best first: a line saying
    SCHEDULE above its headings (past a row of group headings), the
    nearest wording just above them, a line saying SCHEDULE just below its
    last row, the nearest wording below it (a view title under the table,
    past its bubble)."""
    held = set(map(id, header)) | set(map(id, cells))
    x0 = min(p.bbox[0] for p in header) - 5.0
    x1 = max(p.bbox[2] for p in header) + 5.0
    top = min(p.bbox[1] for p in header)
    h = max(p.h for p in header)

    def over(p: _Piece) -> bool:
        return id(p) not in held and p.bbox[0] < x1 and p.bbox[2] > x0

    out: List[_Piece] = []
    named = [p for p in pieces
             if over(p) and SCHEDULE_WORD.search(p.text)
             and _title_like(p.text)
             and top - 7.0 * h - 12.0 <= p.bbox[3] <= top + 0.5]
    if named:
        out.append(max(named, key=lambda p: p.bbox[3]))
    above = [p for p in pieces
             if over(p) and _letters(p.text) >= 3
             and top - 3.5 * h - 6.0 <= p.bbox[3] <= top + 0.5]
    if above:
        out.append(max(above, key=lambda p: p.bbox[3]))
    if cells:
        bottom = max(p.bbox[3] for p in cells)
        below = sorted((p for p in pieces
                        if over(p) and _title_like(p.text)
                        and bottom - 0.5 <= p.bbox[1] <= bottom + 8.0 * h),
                       key=lambda p: p.bbox[1])
        out.extend([p for p in below if SCHEDULE_WORD.search(p.text)][:1])
        out.extend(below[:1])
    return list({id(p): p for p in out}.values())


@dataclass
class _Part:
    """One block of a schedule as printed on one page."""
    page: int
    title: Optional[str]
    header: Tuple[str, ...]
    mark_heading: str
    marks: List[Tuple[str, BBox]]
    bbox: BBox
    #: The heading row's place, for telling the title block's tables.
    place: Tuple[int, int]
    #: The mark heading's place (an untitled table's place on the sheet).
    anchor_place: Tuple[int, int] = (0, 0)
    #: The cells of its mark column that could not be read as marks.
    skipped: List[str] = field(default_factory=list)

    @property
    def title_key(self) -> Optional[str]:
        if not self.title:
            return None
        key = _CONTINUED.sub(" ", self.title.upper())
        return " ".join(re.sub(r"[^A-Z0-9 ]+", " ", key).split()) or None


def _table_box(header: Sequence[_Piece], cells: Sequence[_Piece],
               title: Optional[_Piece], pieces: Sequence[_Piece]) -> BBox:
    """The box a table's text is in: from its title (or headings) to the
    last cell of its mark column, across its headings and the cells
    started under them."""
    x0 = min([p.bbox[0] for p in header] + [p.bbox[0] for p in cells]) - 2.0
    last = max(header, key=lambda p: p.bbox[0])
    y0 = min(p.bbox[1] for p in header)
    y1 = max([p.bbox[3] for p in cells] or [last.bbox[3]]) + 2.0
    x1 = last.bbox[2]
    for p in pieces:
        if y0 - 1.0 <= p.cy <= y1 and x0 <= p.bbox[0] <= last.bbox[2] + 2.0:
            x1 = max(x1, p.bbox[2])
    if title is not None:
        y0 = min(y0, title.bbox[1])
        y1 = max(y1, title.bbox[3])
    return (x0, y0 - 2.0, x1 + 2.0, y1)


def _parts_on(page: int, lines: Sequence[Any],
              known: FrozenSet[str] = frozenset(),
              bubbles: Sequence[BBox] = ()) -> List[_Part]:
    """The schedule blocks printed upright on one page. ``known`` are the
    set's sheet numbers and ``bubbles`` the page's callout bubbles: neither
    is a row."""
    pieces = [_Piece(ln) for ln in lines
              if _direction(ln) == 0 and _clean(ln.text)]
    anchors: List[Tuple[_Piece, bool]] = []
    for p in pieces:
        text = p.text.upper()
        if MARK_HEADING.match(text):
            anchors.append((p, True))
        if SCHEDULE_WORD.search(text) and _title_like(text):
            # the column headings under a title saying SCHEDULE
            under = [q for q in pieces
                     if q is not p and q.bbox[1] >= p.bbox[3] - 0.5
                     and q.bbox[1] <= p.bbox[3] + 3.5 * p.h + 6.0
                     and q.bbox[0] < p.bbox[0] + max(p.bbox[2] - p.bbox[0],
                                                     40.0 * p.size)
                     and q.bbox[2] > p.bbox[0] - 5.0]
            if under:
                first = min(under, key=lambda q: (q.bbox[1], q.bbox[0]))
                row = _header_row(first, pieces)
                heading = [q for q in row if MARK_HEADING.match(q.text)]
                anchors.append((heading[0] if heading else row[0], False))
    parts: List[_Part] = []
    seen: set = set()
    for anchor, by_heading in anchors:
        if not by_heading and (
                mark_of(anchor.text) in known
                or (MARK.match(anchor.text.upper())
                    and any(c.isdigit() for c in anchor.text))):
            continue        # a row of data under the title, not headings
        header = _header_row(anchor, pieces)
        key = frozenset(map(id, header))
        if key in seen:
            continue
        marks, cells, unread = _column(anchor, header, pieces, known,
                                       bubbles)
        if not marks:
            continue
        values = [m for m, _p in marks]
        if len(values) >= 3 and len(set(values)) < 0.8 * len(values):
            continue                        # values repeat: not marks
        near = _titles_near(header, cells, pieces)
        title = next((t for t in near if SCHEDULE_WORD.search(t.text)
                      and _title_like(t.text)), None)
        titled = title is not None
        if not titled and near:
            title = near[0]
        if titled:
            ok = len(header) >= 2
        else:
            ok = (bool(MARK_HEADING.match(anchor.text)) and len(header) >= 3
                  and len(set(values)) >= 2
                  and not any(NOT_A_SCHEDULE.search(t.text) for t in near))
        if not ok:
            continue
        seen.add(key)
        hx, hy = header[0].bbox[0], header[0].bbox[1]
        parts.append(_Part(
            page=page, title=title.text if titled else None,
            header=tuple(p.text.upper() for p in header),
            mark_heading=anchor.text,
            marks=[(m, p.bbox) for m, p in marks],
            bbox=_table_box(header, cells, title, pieces),
            place=(int(hx // 24), int(hy // 24)),
            anchor_place=(int(anchor.bbox[0] // 24),
                          int(anchor.bbox[1] // 24)),
            skipped=list(unread)))
    return parts


@dataclass
class ScheduleRow:
    """One row of a schedule: its mark, and where the row is printed."""
    mark: str
    page: int
    bbox: BBox


@dataclass
class Schedule:
    """One schedule, joined from the parts it is printed in."""
    title: Optional[str]
    header: Tuple[str, ...]
    mark_heading: str
    rows: List[ScheduleRow] = field(default_factory=list)
    #: ``(page, box)`` of each part as printed.
    parts: List[Tuple[int, BBox]] = field(default_factory=list)
    #: Each part's sheet number (``None`` where its sheet has none).
    sheets: List[Optional[str]] = field(default_factory=list)
    #: The rows found drawn on a sheet, and the rows not looked for (marks
    #: of one or two characters: never counted found, and one drawn alone
    #: on no sheet is ``cannot_tell``).
    rows_found: int = 0
    rows_not_looked_for: int = 0
    #: Cells of its mark column that could not be read as marks.
    unread_cells: List[str] = field(default_factory=list)

    @property
    def pages(self) -> List[int]:
        return sorted({p for p, _b in self.parts})

    @property
    def marks(self) -> List[str]:
        return [r.mark for r in self.rows]

    def forms(self) -> List[Tuple[str, str]]:
        """``(regex, shown)`` of each form its rows hold, once each."""
        out: Dict[str, str] = {}
        for r in self.rows:
            form = mark_form(r.mark)
            if form is not None:
                out.setdefault(form[0], form[1])
        return list(out.items())

    @property
    def name(self) -> str:
        """The schedule as a reader names it: its title (or its headings)
        and the sheets it is printed on."""
        if self.title:
            base = self.title
        else:
            base = "table headed " + ", ".join(self.header[:3]) + (
                ", ..." if len(self.header) > 3 else "")
        where = []
        for sheet, (page, _b) in zip(self.sheets, self.parts):
            label = sheet or f"page {page}"
            if label not in where:
                where.append(label)
        return f"{base} ({', '.join(where)})"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"schedule": self.name,
                               "pages": _page_runs(self.pages, most=8),
                               "rows": len(self.rows)}
        shown = sorted({s for _r, s in self.forms()})
        if shown:
            out["forms"] = shown
        out["rows_found"] = self.rows_found
        if self.rows_not_looked_for:
            out["rows_not_looked_for"] = self.rows_not_looked_for
        if self.unread_cells:
            out["cells_not_read"] = self.unread_cells[:3]
        return out


def _joined(parts: Sequence[_Part], number_of: Dict[int, Optional[str]]
            ) -> List[Schedule]:
    """The schedules the parts make: parts with one title (CONTINUED aside)
    are one schedule; a part with no title joins one with its headings,
    on its own page first."""
    out: List[Schedule] = []
    by_title: Dict[str, Schedule] = {}

    def add(s: Schedule, part: _Part) -> None:
        s.parts.append((part.page, part.bbox))
        s.sheets.append(number_of.get(part.page))
        s.unread_cells.extend(part.skipped)
        held = set(s.marks)
        for mark, box in part.marks:
            if mark not in held:
                s.rows.append(ScheduleRow(mark, part.page, box))
                held.add(mark)

    ordered = sorted(parts, key=lambda p: (p.page, p.bbox[1], p.bbox[0]))
    for part in ordered:
        key = part.title_key
        if key is None:
            continue
        s = by_title.get(key)
        if s is None:
            s = Schedule(part.title, part.header, part.mark_heading)
            by_title[key] = s
            out.append(s)
        add(s, part)
    for part in ordered:
        if part.title_key is not None:
            continue
        same = [s for s in out if s.header == part.header]
        here = [s for s in same if part.page in s.pages]
        target = (here or same or [None])[0]
        if target is None:
            target = Schedule(None, part.header, part.mark_heading)
            out.append(target)
        add(target, part)
    return out


# ---------------------------------------------------------------------------
# The marks on the sheets
# ---------------------------------------------------------------------------

@dataclass
class _Seen:
    """A mark seen on a sheet: alone, or inside a line of text."""
    page: int
    sheet: Optional[str]
    bbox: BBox
    line: Any
    text: str


@dataclass
class MarkItem:
    """One fact of the check: a mark drawn with no row, a row drawn
    nowhere, either where unread sheets leave it open, or what the text
    does not settle."""
    status: str
    side: str
    schedules: List[int]
    why: str
    mark: Optional[str] = None
    page: Optional[int] = None
    sheet: Optional[str] = None
    bbox: Optional[BBox] = None
    #: For an item about many marks on one sheet: some of them, and how many.
    marks: Optional[List[str]] = None
    n_marks: Optional[int] = None

    def to_dict(self, names: Sequence[str]) -> Dict[str, Any]:
        out: Dict[str, Any] = {"status": self.status, "side": self.side}
        if self.mark is not None:
            out["mark"] = self.mark
        if self.marks:
            out["marks"] = list(self.marks)
            out["n_marks"] = self.n_marks
        if self.sheet:
            out["sheet"] = self.sheet
        if self.page is not None:
            out["page"] = self.page
            out["pdf_page"] = self.page + 1
        out["schedule"] = "; ".join(names[i] for i in self.schedules)
        out["why"] = self.why
        if self.bbox is not None:
            out["bbox"] = [int(round(v)) for v in self.bbox]
        return out


@dataclass
class ScheduleMarks:
    """The schedules of a document, and its marks checked against them."""
    schedules: List[Schedule] = field(default_factory=list)
    items: List[MarkItem] = field(default_factory=list)
    #: The sheets with no usable text: nothing on them was read.
    unread: List[SheetRow] = field(default_factory=list)
    n_sheets: int = 0
    notes: List[str] = field(default_factory=list)
    #: Readable sheets whose drawing is an image: marks there are not read.
    pictured: List[int] = field(default_factory=list)
    #: Readable sheets with (next to) no text outside their title block:
    #: their drawing is lettered as lines with no text kept, if at all, so
    #: marks there are not read (PL-43).
    unlettered: List[int] = field(default_factory=list)

    def counts(self) -> Dict[str, int]:
        out = {"schedules": len(self.schedules),
               "rows": sum(len(s.rows) for s in self.schedules),
               "rows_found": sum(s.rows_found for s in self.schedules)}
        out.update((status, 0) for status in STATUSES)
        for item in self.items:
            out[item.status] += 1
        return out

    def with_status(self, status: str) -> List[MarkItem]:
        return [i for i in self.items if i.status == status]

    def names(self) -> List[str]:
        return [s.name for s in self.schedules]


def _boxed(box: BBox, boxes: Sequence[BBox]) -> bool:
    cx, cy = (box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0
    return any(b[0] <= cx <= b[2] and b[1] <= cy <= b[3] for b in boxes)


class _Words:
    """The lines of one page holding a word (three letters or more),
    sorted by their top, to find a word over or under a line quickly."""

    def __init__(self, lines: Sequence[Any]):
        boxes = sorted((tuple(float(v) for v in ln.bbox) for ln in lines
                        if _letters(str(ln.text or "")) >= 3),
                       key=lambda b: b[1])
        self.boxes = boxes
        self.tops = [b[1] for b in boxes]
        self.tallest = max((b[3] - b[1] for b in boxes), default=0.0)

    def named(self, line) -> bool:
        """Whether a word is lettered directly over or under ``line``, as a
        room's name is over its number."""
        x0, y0, x1, y1 = (float(v) for v in line.bbox)
        h = max(1.0, y1 - y0)
        cx = (x0 + x1) / 2.0
        lo = bisect_left(self.tops, y0 - h - self.tallest)
        hi = bisect_right(self.tops, y1 + h)
        for ox0, oy0, ox1, oy1 in self.boxes[lo:hi]:
            if (ox0, oy0, ox1, oy1) == (x0, y0, x1, y1):
                continue
            if not ox0 - 0.5 * h <= cx <= ox1 + 0.5 * h:
                continue
            gap = max(oy0 - y1, y0 - oy1)
            if -0.3 * h <= gap <= 1.0 * h:
                return True
        return False


class _Letterings:
    """The lettering of a seen mark (font, size, whether a word is over or
    under it), worked out once per line; each page's words once."""

    def __init__(self, pages: Dict[int, Sequence[Any]]):
        self.pages = pages
        self._held: Dict[int, Tuple[str, float, bool]] = {}
        self._words: Dict[int, _Words] = {}

    def of(self, seen: _Seen) -> Tuple[str, float, bool]:
        key = id(seen.line)
        if key not in self._held:
            ln = seen.line
            size = getattr(ln, "size", None)
            size = round(float(size), 1) if size else round(
                (seen.bbox[3] - seen.bbox[1]) * 0.8, 1)
            words = self._words.get(seen.page)
            if words is None:
                words = self._words[seen.page] = _Words(
                    self.pages.get(seen.page, ()))
            self._held[key] = (str(getattr(ln, "font", None) or ""), size,
                               words.named(ln))
        return self._held[key]


def _lettering_text(key: Tuple[str, float, bool]) -> str:
    font, size, named = key
    return (f"{size:g} pt{' ' + font if font else ''}"
            + (" under or over a word" if named else ""))


def _shown_marks(marks: Sequence[str], most: int = 6) -> str:
    if len(marks) <= most:
        return ", ".join(marks)
    return (", ".join(marks[:most - 2]) + f", ... {marks[-1]} "
            f"({len(marks)} rows)")


def _range_holds(mark: str, a: str, b: str) -> bool:
    """Whether the range ``a`` to ``b`` holds ``mark``: the three alike but
    for their last digit run, and that run between."""
    parts = [_split_last(m) for m in (mark, a, b)]
    if None in parts:
        return False
    (h, n, t, _w), (ha, na, ta, _wa), (hb, nb, tb, _wb) = parts
    return h == ha == hb and t == ta == tb and na < nb and na <= n <= nb


def _ranges_in(text: str) -> List[Tuple[str, str]]:
    """The ranges written in ``text``: "101 TO 108", "F1-F4" (two marks of
    one shape joined by a dash), "W-1 - W-6"."""
    out = [(m.group(1), m.group(2)) for m in _RANGE_WORDS.finditer(text)]
    for tok in _TOKENS.findall(text):
        if tok.count("-") == 1:
            a, b = tok.split("-")
            if a and b and re.sub(r"\d", "9", a) == re.sub(r"\d", "9", b):
                out.append((a, b))
    return out


def schedule_marks(doc, index: SheetIndex, refs=None) -> ScheduleMarks:
    """The schedules of ``doc`` (a :class:`planlens.document.Document`), and
    the marks on its sheets checked against them, in both directions.
    ``index`` is its sheet index; ``refs`` its cross references (their
    callout bubbles are not marks), read if not given."""
    out = ScheduleMarks(n_sheets=len(index.rows))
    if not index.rows:
        out.notes.append("no drawing sheets in this document")
        return out
    out.unread = list(index.unread)
    number_of = {r.page: r.number for r in index.rows}
    known = frozenset(normalize_number(r.number) for r in index.indexed)
    from planlens.document.annotations import drop_cad_text_already_in_layer
    from planlens.document.document import _image_coverage
    pages: Dict[int, List[Any]] = {}
    #: the lines read from hidden CAD text (their lettering drawn as lines),
    #: and the sheets that carry any such text (plotted with it)
    copied: set = set()
    hidden_on: set = set()
    for row in index.rows:
        if not row.readable:
            continue
        try:
            lines = list(doc._page_text(row.page)[0])
        except Exception:                             # noqa: BLE001
            lines = []
        try:
            # lettering a CAD program keeps only as hidden annotations
            # ("AutoCAD SHX Text"), its glyphs drawn as lines
            cad = list(doc._page_annots(row.page)[1])
            if cad:
                hidden_on.add(row.page)
                kept = drop_cad_text_already_in_layer(cad, lines)[0]
                copied.update(id(ln) for ln in kept)
                lines += kept
        except Exception:                             # noqa: BLE001
            pass
        if lines:
            pages[row.page] = lines
        try:
            if _image_coverage(doc._doc[row.page]) >= PICTURED:
                out.pictured.append(row.page)
            else:
                summary = doc.summary(row.page)
                w, h = float(summary.width), float(summary.height)
                field_chars = sum(len(_clean(ln.text)) for ln in lines
                                  if not _in_band(ln.bbox, w, h))
                if field_chars < FIELD_CHARS:
                    # its drawing is lettered as lines with no text kept,
                    # if at all: a mark drawn there is not read (PL-43)
                    out.unlettered.append(row.page)
        except Exception:                             # noqa: BLE001
            pass
    if refs is None:
        refs = doc.cross_references()
    callouts: Dict[int, List[BBox]] = {}
    for c in getattr(refs, "callouts", ()):
        callouts.setdefault(c.page, []).append(tuple(c.bbox))

    # -- the schedules ------------------------------------------------------
    parts: List[_Part] = []
    for page, lines in pages.items():
        parts.extend(_parts_on(page, lines, known, callouts.get(page, ())))
    places = Counter((p.header, p.place) for p in parts)
    floor = max(STATIC_MIN, STATIC_SHARE * len(pages))
    # an untitled table whose mark heading is at one place on three sheets
    # or more is the title block's, whatever drawing text joins its headings
    heads = Counter((p.mark_heading.upper(), p.anchor_place)
                    for p in parts if p.title is None)
    parts = [p for p in parts
             if places[(p.header, p.place)] < floor       # a title block's
             and not (p.title is None and heads[
                 (p.mark_heading.upper(), p.anchor_place)] >= STATIC_MIN)
             and sum(m in known for m, _b in p.marks)     # a drawing list
             < 0.5 * len(p.marks)]
    out.schedules = _joined(parts, number_of)
    if not out.schedules:
        out.notes.append(
            f"no schedule was found in the text of the {len(pages)} "
            f"sheet(s) that could be read" + (
                f"; {len(out.unread)} sheet(s) could not be read"
                if out.unread else ""))
        return out

    # -- what is drawn on the sheets ---------------------------------------
    tables: Dict[int, List[BBox]] = {}
    for p in parts:
        tables.setdefault(p.page, []).append(p.bbox)
    alone: Dict[str, List[_Seen]] = {}
    in_text: Dict[str, List[_Seen]] = {}
    ranges: List[Tuple[str, str, _Seen]] = []
    for row in index.rows:
        lines = pages.get(row.page)
        if not lines:
            continue
        skip = tables.get(row.page, []) + callouts.get(row.page, []) + (
            [row.bbox] if row.bbox is not None else [])
        for ln in lines:
            text = _clean(ln.text).upper()
            if not text:
                continue
            box = tuple(float(v) for v in ln.bbox)
            if _boxed(box, skip):
                continue
            seen = _Seen(row.page, row.number, box, ln, text)
            if " " not in text and MARK.match(text):
                if text not in known:
                    alone.setdefault(text, []).append(seen)
                for a, b in _ranges_in(text):
                    ranges.append((a, b, seen))
                continue
            for tok in set(_TOKENS.findall(text)):
                if tok not in known:
                    in_text.setdefault(tok, []).append(seen)
            for a, b in _ranges_in(text):
                ranges.append((a, b, seen))
    # the ranges by the shape of their ends, to look a row up in them
    by_shape: Dict[Tuple[str, str], List[Tuple[int, int, _Seen]]] = {}
    for a, b, seen in ranges:
        sa, sb = _split_last(a), _split_last(b)
        if sa and sb and sa[0] == sb[0] and sa[2] == sb[2] and sa[1] < sb[1]:
            by_shape.setdefault((sa[0], sa[2]), []).append(
                (sa[1], sb[1], seen))

    def in_ranges(mark: str) -> List[_Seen]:
        sm = _split_last(mark)
        if sm is None:
            return []
        return [seen for na, nb, seen in by_shape.get((sm[0], sm[2]), ())
                if na <= sm[1] <= nb]

    letterings = _Letterings(pages)
    blind = sorted(r.page for r in out.unread)
    blind_text = _page_runs(blind)
    pictured = sorted(out.pictured)
    unlettered = sorted(out.unlettered)
    #: every sheet whose drawing is not read: a mark or a part of a
    #: schedule there is not seen
    closed = sorted(set(blind) | set(pictured) | set(unlettered))
    names = out.names()
    items: List[MarkItem] = []

    def on_sheet(seen: _Seen) -> str:
        return (f"{seen.sheet} (page {seen.page})" if seen.sheet
                else f"page {seen.page}")

    def closed_text() -> List[str]:
        """The sheets whose drawing is not read, by why, for an item."""
        why = []
        if blind:
            why.append(f"{len(blind)} sheet(s) could not be read (page(s) "
                       f"{blind_text})")
        if pictured:
            why.append(f"{len(pictured)} sheet(s) have their drawing in an "
                       f"image, whose marks are not read (page(s) "
                       f"{_page_runs(pictured)})")
        if unlettered:
            why.append(f"{len(unlettered)} sheet(s) have no text outside "
                       f"their title block, so their drawing is lettered as "
                       f"lines if at all, and not read (page(s) "
                       f"{_page_runs(unlettered)})")
        return why

    def sheets_text(pages_: Sequence[int], most: int = 4) -> str:
        labels = []
        for p in sorted(set(pages_)):
            label = number_of.get(p) or f"page {p}"
            if label not in labels:
                labels.append(label)
        return ", ".join(labels[:most]) + (
            f" and {len(labels) - most} more" if len(labels) > most else "")

    tallies: Dict[Tuple[FrozenSet[int], int], Counter] = {}

    def counts_on(sid_set, page, leave: Optional[str] = None
                  ) -> Counter:
        """The letterings of the plain-number rows of ``sid_set`` drawn on
        ``page``, the row ``leave`` left out."""
        key = (frozenset(sid_set), page)
        if key not in tallies:
            held: Counter = Counter()
            for mark in {m for sid in key[0]
                         for m in out.schedules[sid].marks if plain(m)}:
                for seen in alone.get(mark, ()):
                    if seen.page == page:
                        held[letterings.of(seen)] += 1
            tallies[key] = held
        held = tallies[key]
        if leave is None or leave not in alone:
            return held
        held = Counter(held)
        for seen in alone[leave]:
            if seen.page == page:
                held[letterings.of(seen)] -= 1
        return +held

    def settle(held: Counter) -> Tuple[Optional[Tuple[str, float, bool]],
                                       List[Tuple[str, float, bool]], bool]:
        """``(lettering, letterings, tie)`` of a schedule's marks on a sheet
        from the letterings its rows are drawn in there: the one lettering
        holding two or more; or, where several do, the only one standing
        alone (``tie``: things numbered as the marks may share it); else
        ``None`` and the letterings that leave it open."""
        strong = [k for k, n in held.items() if n >= 2]
        if len(strong) == 1:
            return strong[0], [], False
        alone_ones = [k for k in strong if not k[2]]
        if len(alone_ones) == 1:
            return alone_ones[0], sorted(strong), True
        return None, sorted(strong), False

    # -- each row: is its mark drawn on a sheet? ---------------------------
    any_copies = False
    for sid, s in enumerate(out.schedules):
        missing: List[MarkItem] = []
        told = 0
        #: rows found only in hidden CAD text, and the pages rows are found
        copies = 0
        found_on: List[int] = []
        for row in s.rows:
            mark = row.mark
            if too_short(mark):
                # not counted found (a grid line or a keynote reads the
                # same), but one drawn alone on no sheet is told (PL-43)
                s.rows_not_looked_for += 1
                if not alone.get(mark):
                    missing.append(MarkItem(
                        CANNOT_TELL, SIDE_ROW, [sid], "", mark=mark,
                        page=row.page, sheet=number_of.get(row.page),
                        bbox=row.bbox))
                continue
            drawn = alone.get(mark, [])
            unlike: List[Tuple[_Seen, Tuple, Tuple]] = []
            if drawn and plain(mark):
                kept = []
                for seen in drawn:
                    lead, _open, _tie = settle(counts_on({sid}, seen.page,
                                                         leave=mark))
                    mine = letterings.of(seen)
                    if lead is not None and lead != mine:
                        unlike.append((seen, mine, lead))
                    else:
                        kept.append(seen)
                drawn = kept
            if drawn:
                s.rows_found += 1
                found_on.extend(seen.page for seen in drawn)
                if all(id(seen.line) in copied for seen in drawn):
                    copies += 1
                continue
            item = MarkItem(CANNOT_TELL, SIDE_ROW, [sid], "", mark=mark,
                            page=row.page, sheet=number_of.get(row.page),
                            bbox=row.bbox)
            named_in = in_text.get(mark, [])
            ranged = in_ranges(mark)
            if unlike:
                seen, mine, lead = unlike[0]
                item.why = (
                    f"row {mark} is found on {on_sheet(seen)} only lettered "
                    f"unlike this schedule's other marks there "
                    f"({_lettering_text(mine)}, the others "
                    f"{_lettering_text(lead)}): it may be another thing's "
                    f"number; view it")
            elif named_in or ranged:
                seen = (named_in or ranged)[0]
                how = "named in a line of text" if named_in else \
                    "inside a range written"
                item.why = (
                    f"row {mark} is drawn alone on no sheet, but is {how} "
                    f"on {on_sheet(seen)}: \"{seen.text[:70]}\"; view it")
            else:
                missing.append(item)
                continue
            told += 1
            items.append(item)
        looked = len(s.rows) - s.rows_not_looked_for
        thin = looked >= 2 and s.rows_found + told < FOUND_SHARE * looked
        #: where rows are read from hidden CAD text, the read sheets that
        #: could show a mark (of the discipline of the sheets its rows are
        #: found on, or with no number read) and carry none of that text:
        #: plotted apart, a mark drawn as lines there is not read (PL-43)
        bare: List[int] = []
        if copies and missing:
            theirs = {discipline(number_of[p]) for p in found_on
                      if number_of.get(p)}
            bare = [p for p in sorted(pages)
                    if p not in hidden_on and p not in closed
                    and (not theirs or not number_of.get(p)
                         or discipline(number_of[p]) in theirs)]
        for item in missing:
            mark = item.mark
            if blind and not (pictured or unlettered):
                item.status = MAYBE_UNREAD
                item.why = (
                    f"row {mark} is drawn on none of the sheets that could "
                    f"be read; {len(blind)} sheet(s) could not be read "
                    f"(page(s) {blind_text}) and may show it")
            elif closed:
                item.status = MAYBE_UNREAD
                item.why = (f"row {mark} is drawn in the text of no sheet; "
                            + " and ".join(closed_text()) + ", and may show "
                            "it")
            elif s.title is None:
                item.why = (
                    f"row {mark} is drawn on no sheet, but the table it is "
                    f"in has no SCHEDULE title: it may be a list whose "
                    f"entries need not all be drawn; view it")
            elif thin:
                item.why = (
                    f"row {mark} is drawn on no sheet, but only "
                    f"{s.rows_found + told} of the {looked} rows of this "
                    f"schedule are found in the sheets' text: its marks may "
                    f"be drawn as lines or in an image; view a plan")
            elif copies and bare:
                # its marks are drawn as lines, read from the text a CAD
                # program keeps hidden with them; a sheet that could show
                # it carries none of that text, as if plotted without it,
                # and a mark drawn as lines there is not read (PL-43).
                # Where every such sheet carries it, no_mark stands.
                one = len(bare) == 1
                any_copies = True
                item.why = (
                    f"row {mark} is drawn on no sheet as a mark of its own, "
                    f"but {copies} of this schedule's {s.rows_found} rows "
                    f"found are drawn as lines, read only from the hidden "
                    f"text a CAD program keeps with them (on "
                    f"{sheets_text(found_on)}), and {sheets_text(bare)}, "
                    f"which could show it, {'carries' if one else 'carry'} "
                    f"none of that text: plotted without it, a mark drawn "
                    f"as lines there is not read, so the text does not tell "
                    f"whether it is drawn; view {sheets_text(bare)}")
            elif too_short(mark):
                item.why = (
                    f"row {mark} is drawn alone on no sheet; but a mark of "
                    f"one or two characters is not counted found where it "
                    f"is drawn (a grid line, keynote or view number reads "
                    f"the same), so the text does not show that this "
                    f"schedule's marks are drawn as text at all; view a "
                    f"plan")
            else:
                item.status = NO_MARK
                item.why = (
                    f"row {mark} is drawn on no sheet as a mark of its own, "
                    f"and named in no note or range; every sheet's text was "
                    f"read")
            items.append(item)

    # -- each mark drawn: has it a row? ------------------------------------
    rows_of = [set(s.marks) for s in out.schedules]
    every_row = set().union(*rows_of)
    forms: List[Tuple[Any, str, int]] = []
    for sid, s in enumerate(out.schedules):
        for rx, shown in s.forms():
            forms.append((re.compile(rx), shown, sid))

    def fits(token: str) -> FrozenSet[int]:
        return frozenset(sid for rx, _sh, sid in forms if rx.fullmatch(token))

    def forms_of(sids) -> str:
        return ", ".join(sorted({sh for _rx, sh, sid in forms
                                 if sid in sids}))

    def rows_text(sids) -> str:
        marks = [m for sid in sorted(sids)
                 for m in out.schedules[sid].marks]
        return _shown_marks(marks)

    lettered: Dict[Tuple[FrozenSet[int], int], List[Tuple[str, _Seen]]] = {}
    family: Dict[FrozenSet[int], List[Tuple[str, List[_Seen]]]] = {}
    for token, seens in alone.items():
        if token in every_row:
            continue
        sids = fits(token)
        if not sids:
            continue
        if plain(token):
            for seen in seens:
                lettered.setdefault((sids, seen.page), []).append(
                    (token, seen))
        else:
            family.setdefault(sids, []).append((token, seens))

    def found_rows(sids) -> int:
        return len({m for sid in sids for m in out.schedules[sid].marks
                    if m in alone})

    def no_row(token: str, seen: _Seen, sids, why: str) -> MarkItem:
        """A mark drawn with no row: ``no_row``, unless a cell of the
        schedule's mark column could not be read (its row may be there:
        ``cannot_tell``) or a sheet's drawing is not read, beside the
        schedule's pages or away from them (a part of the schedule may be
        there: ``may_be``; PL-43)."""
        status, tail = NO_ROW, ""
        cells = [c for sid in sorted(sids)
                 for c in out.schedules[sid].unread_cells]
        near = sorted({p for p in closed for sid in sids
                       for q in out.schedules[sid].pages if abs(p - q) <= 1})
        if cells:
            status = CANNOT_TELL
            tail = (f"; but a cell of the schedule's mark column could not "
                    f"be read as marks (\"{cells[0][:40]}\"), and its row "
                    f"may be there: view it")
        elif near and near == closed == blind:
            status = MAYBE_UNREAD
            tail = (f"; page(s) {_page_runs(near)} could not be read and "
                    f"sit beside the schedule's pages: a part of the "
                    f"schedule there would not be seen")
        elif closed:
            # a schedule continues on a sheet away from its other parts as
            # well as beside them; the text cannot say none is there
            status = MAYBE_UNREAD
            beside = (f"; page(s) {_page_runs(near)} sit beside the "
                      f"schedule's pages" if near else "")
            tail = (f"; but " + " and ".join(closed_text()) + beside
                    + ": a part of the schedule there would not be seen, "
                    "so whether it has a row the text does not tell; view "
                    "them")
        return MarkItem(status, SIDE_MARK, sorted(sids), why + tail,
                        mark=token, page=seen.page, sheet=seen.sheet,
                        bbox=seen.bbox)

    for sids, tokens in family.items():
        found = found_rows(sids)
        doubt = (len(tokens) >= 3 and len(tokens) > FAMILY_SHARE * found)
        for token, seens in sorted(tokens):
            by_page: Dict[int, _Seen] = {}
            for seen in seens:
                by_page.setdefault(seen.page, seen)
            for seen in by_page.values():
                if doubt:
                    items.append(MarkItem(
                        CANNOT_TELL, SIDE_MARK, sorted(sids),
                        f"{len(tokens)} different marks of the form "
                        f"{forms_of(sids)} are drawn with no row, against "
                        f"{found} of its rows drawn: something the schedule "
                        f"does not list may share the form; view it",
                        mark=token, page=seen.page, sheet=seen.sheet,
                        bbox=seen.bbox))
                else:
                    items.append(no_row(
                        token, seen, sids,
                        f"{token} is drawn on {on_sheet(seen)} and fits the "
                        f"form {forms_of(sids)} of the schedule's marks, "
                        f"which has no row {token} (its rows: "
                        f"{rows_text(sids)})"))
    # a mark named in a note, of a form with letters, that is drawn nowhere
    for token, seens in in_text.items():
        if token in every_row or token in alone or plain(token):
            continue
        sids = fits(token)
        if not sids:
            continue
        seen = seens[0]
        items.append(MarkItem(
            CANNOT_TELL, SIDE_MARK, sorted(sids),
            f"{token} fits the form {forms_of(sids)} of the schedule's marks "
            f"and has no row; it is named in a line of text on "
            f"{on_sheet(seen)} (\"{seen.text[:70]}\"), not drawn as a mark",
            mark=token, page=seen.page, sheet=seen.sheet, bbox=seen.bbox))
    lent: Dict[FrozenSet[int], Optional[Tuple[Tuple, List[int]]]] = {}

    def borrowed(sids) -> Optional[Tuple[Tuple, List[int]]]:
        """``(lettering, pages)`` of the plain-number marks of ``sids``
        where every sheet that settles their lettering settles the same
        one, and no sheet leaves it open; else ``None``."""
        if sids not in lent:
            keys: Dict[Tuple, List[int]] = {}
            open_somewhere = False
            on = sorted({seen.page for sid in sids
                         for m in out.schedules[sid].marks if plain(m)
                         for seen in alone.get(m, ())})
            for p in on:
                key, open_keys, _tie = settle(counts_on(sids, p))
                if key is not None:
                    keys.setdefault(key, []).append(p)
                elif open_keys:
                    open_somewhere = True
            lent[sids] = (next(iter(keys.items()))
                          if len(keys) == 1 and not open_somewhere else None)
        return lent[sids]

    def lend_to(sids, page, held: Counter
                ) -> Optional[Tuple[Tuple, List[int]]]:
        """The lettering a sheet showing fewer than two of the marks of
        ``sids`` borrows from the sheets that settle it: only on a sheet
        of their discipline, and not where its one mark is lettered
        otherwise (PL-43)."""
        got = borrowed(sids)
        number = number_of.get(page)
        if got is None or not number:
            return None
        key, from_pages = got
        theirs = {discipline(number_of[p]) for p in from_pages
                  if number_of.get(p)}
        if discipline(number) not in theirs:
            return None
        if any(k != key for k in held):
            return None
        return got

    #: the numbers told from a sheet whose own marks set their lettering,
    #: and the sheets whose marks set none, looked at after them
    reported: set = set()
    lean: List[Tuple[FrozenSet[int], int, List[Tuple[str, _Seen]],
                     Counter]] = []
    for (sids, page), found in sorted(lettered.items(),
                                      key=lambda kv: (kv[0][1],
                                                      sorted(kv[0][0]))):
        held = counts_on(sids, page)
        key, open_keys, tie = settle(held)
        if key is None and not open_keys:
            lean.append((sids, page, found, held))
            continue
        wanted = [key] if key is not None else open_keys
        alike = [(t, seen) for t, seen in found
                 if letterings.of(seen) in wanted]
        if not alike:
            continue
        reported.update((sids, t) for t, _s in alike)
        where = on_sheet(alike[0][1])
        shown = forms_of(sids)
        kinds = "; ".join(f"{_lettering_text(k)}: {held[k]}"
                          for k in open_keys)
        if key is None:
            # the schedule's numbers are drawn in two letterings or more
            # here, none the only one standing alone
            lead_in = (f"the schedule's numbers on {where} are drawn in "
                       f"{len(open_keys)} letterings ({kinds}), so which "
                       f"are its marks the text does not tell")
        elif tie:
            # the one standing alone was taken; things numbered as the
            # marks (rooms numbered as their doors) may share it
            lead_in = (f"the schedule's numbers on {where} are drawn in "
                       f"{len(open_keys)} letterings ({kinds}); the one "
                       f"standing alone ({_lettering_text(key)}) is taken "
                       f"as its marks', but things numbered as its marks "
                       f"(rooms numbered as their doors) may be lettered "
                       f"so too")
        else:
            lead_in = None
        if key is not None and not tie and len(alike) == 1:
            token, seen = alike[0]
            items.append(no_row(
                token, seen, sids,
                f"{token} is drawn on {where} in the lettering of the "
                f"{held[key]} marks of the schedule found there "
                f"({_lettering_text(key)}) and has no row (its rows: "
                f"{rows_text(sids)}); a plain number: view it to confirm "
                f"it is that schedule's mark"))
        elif len(alike) <= 3:
            tokens = ", ".join(t for t, _s in alike)
            one = len(alike) == 1
            for token, seen in alike:
                why = (f"{lead_in}; {tokens} {'has' if one else 'have'} "
                       f"no row: view {'it' if one else 'them'}"
                       if lead_in else
                       f"{len(alike)} numbers of the form {shown} on {where} "
                       f"are in the lettering of the schedule's marks there "
                       f"and have no row ({tokens}): each may be a mark the "
                       f"schedule misses or another number lettered alike; "
                       f"view them")
                items.append(MarkItem(
                    CANNOT_TELL, SIDE_MARK, sorted(sids), why,
                    mark=token, page=seen.page, sheet=seen.sheet,
                    bbox=seen.bbox))
        else:
            seen = alike[0][1]
            tokens = sorted({t for t, _s in alike})
            why = (f"{lead_in}; {len(tokens)} numbers of the form {shown} "
                   f"in those letterings have no row; view the sheet"
                   if lead_in else
                   f"{len(alike)} numbers of the form {shown} on {where} are "
                   f"in the lettering of the schedule's marks there and have "
                   f"no row: something else on the sheet (room numbers, "
                   f"dimensions) is lettered alike, so none is called a "
                   f"missing row; view the sheet")
            items.append(MarkItem(
                CANNOT_TELL, SIDE_MARK, sorted(sids), why,
                page=seen.page, sheet=seen.sheet, marks=tokens[:6],
                n_marks=len(tokens)))
    for sids, page, found, held in lean:
        # fewer than two of the schedule's marks here set no lettering;
        # one the marks have on every sheet that settles it is borrowed,
        # and what it finds is never more than cannot_tell (PL-43). A
        # number already told from a sheet whose own marks set its
        # lettering is not told again here: one mark, one item.
        got = lend_to(sids, page, held)
        if got is None:
            continue
        key, from_pages = got
        alike = [(t, seen) for t, seen in found
                 if letterings.of(seen) == key and (sids, t) not in reported]
        if not alike:
            continue
        where = on_sheet(alike[0][1])
        lettering = (f"the lettering this schedule's marks have on "
                     f"{sheets_text(from_pages)} "
                     f"({_lettering_text(key)})")
        short_of = (f"{where} shows fewer than two of the schedule's "
                    f"marks, so it does not confirm that lettering here")
        tokens = sorted({t for t, _s in alike})
        if len(alike) <= 3:
            for token, seen in alike:
                items.append(MarkItem(
                    CANNOT_TELL, SIDE_MARK, sorted(sids),
                    f"{token} is drawn in {lettering} and has no row "
                    f"(its rows: {rows_text(sids)}); but {short_of}: "
                    f"it may be a mark the schedule misses or another "
                    f"number lettered alike; view it",
                    mark=token, page=seen.page, sheet=seen.sheet,
                    bbox=seen.bbox))
        else:
            items.append(MarkItem(
                CANNOT_TELL, SIDE_MARK, sorted(sids),
                f"{len(tokens)} numbers of the form {forms_of(sids)} "
                f"on {where} are in {lettering} and have no row; but "
                f"{short_of}: they may be marks the schedule misses (a "
                f"floor it leaves out) or other numbers lettered alike; "
                f"view the sheet",
                page=alike[0][1].page, sheet=alike[0][1].sheet,
                marks=tokens[:6], n_marks=len(tokens)))

    order = {s: i for i, s in enumerate(STATUSES)}
    out.items = sorted(items, key=lambda i: (
        order[i.status], i.page if i.page is not None else -1,
        i.bbox[1] if i.bbox else 0.0, i.bbox[0] if i.bbox else 0.0,
        i.mark or ""))
    if any(plain(m) for s in out.schedules for m in s.marks):
        out.notes.append(
            "a plain number (no letter before its digits) is a schedule's "
            "mark only by its lettering: it is looked for in the lettering "
            "of that schedule's marks on a sheet that shows two or more of "
            "them in one lettering, and room numbers, dimensions and detail "
            "numbers of that form in that lettering make it cannot_tell; on "
            "another sheet of the same discipline, one in the lettering the "
            "marks have on the sheets that settle it is cannot_tell")
    if any(s.rows_not_looked_for for s in out.schedules):
        out.notes.append(
            "a mark of one or two characters with no digit or no letter is "
            "not looked for as a mark with no row, nor counted found where "
            "it is drawn (grid lines, view numbers, keynotes and circuits "
            "read the same); a row of one drawn alone on no sheet is "
            "cannot_tell: see rows_not_looked_for")
    if out.unread:
        out.notes.append(
            f"{len(out.unread)} sheet(s) could not be read (page(s) "
            f"{blind_text}): a schedule or a mark on them is not in this "
            f"check, so a row not found is {MAYBE_UNREAD}, and so is a mark "
            f"with no row (a part of its schedule may be there)")
    if pictured:
        out.notes.append(
            f"{len(pictured)} sheet(s) have their drawing in an image "
            f"(page(s) {_page_runs(pictured)}): a mark drawn in the image is "
            f"not read, so a row not found is {MAYBE_UNREAD}, and so is a "
            f"mark with no row")
    if unlettered:
        out.notes.append(
            f"{len(unlettered)} sheet(s) have no text outside their title "
            f"block (page(s) {_page_runs(unlettered)}): their drawing is "
            f"lettered as lines if at all, and a mark drawn there is not "
            f"read, so a row not found is {MAYBE_UNREAD}, and so is a mark "
            f"with no row")
    if any_copies:
        out.notes.append(
            "where a schedule's marks found are drawn as lines and read "
            "only from the hidden text a CAD program keeps with them, and a "
            "sheet that could show its marks carries none of that text "
            "(sheets plotted apart), a row not found is cannot_tell: a mark "
            "drawn as lines without that text is not read; where every such "
            "sheet carries it, a row not found is no_mark")
    if any(s.unread_cells for s in out.schedules):
        out.notes.append(
            "a schedule's mark column has a cell that could not be read as "
            "marks (see cells_not_read): a mark of its form drawn with no "
            "row is cannot_tell, since its row may be in that cell")
    return out


__all__ = ["Schedule", "ScheduleRow", "ScheduleMarks", "MarkItem",
           "schedule_marks", "mark_form", "mark_of", "plain", "too_short",
           "NO_ROW", "NO_MARK", "MAYBE_UNREAD", "CANNOT_TELL", "STATUSES",
           "SIDE_MARK", "SIDE_ROW", "SCHEDULE_WORD", "MARK_HEADING",
           "NOT_A_SCHEDULE"]
