"""A drawing set's callouts, and whether each one leads anywhere.

A callout sends the reader to a view on a sheet: detail 7 on A-501, section
2 on A-301. On the sheet it is a reference bubble, a circle with the view
number over the sheet number, two separate pieces of text; in a note it is
written on one line, "SEE 3/A-501". Checking a set's callouts by eye is
slow and is exactly the kind of check that gets skipped. With the sheet
index (:mod:`planlens.document.sheets`) it is a lookup.

WHAT IS READ, from the text and where it sits (nothing is looked at):

* **A bubble**: a piece of text that is a sheet number and nothing else
  (of the shape of the document's own numbers), with a short view number
  or letter centred directly over it, in lettering at least as large.
  Lettering in any reading direction is read: upright, up or down the
  sheet, upside down.
* **A view** is a bubble that names the sheet it is on AND has a title
  beside it (the nearest wording to its right, on its level, in the
  direction its lettering reads): the view's own title mark. The views
  recognised on a sheet are what a callout to that sheet is checked
  against. A bubble naming its own sheet with no title beside it is a
  callout to that sheet, checked like any other.
* **A callout** is any other upright bubble, and any "view/sheet" written
  on one line. A turned bubble naming another sheet is not read as a
  callout.
* **A view mark not read as one**: a view number standing alone (its
  sheet half not read, or a bubble holding the view number only) with a
  title beside it, or a turned bubble naming its own sheet with no title
  beside it. Where a sheet has one that is not among its recognised
  views, the views recognised there may not be all it holds.

WHAT IS SAID of each callout:

* ``resolved``: the sheet is in the document and a mark for that view was
  recognised on it;
* ``unresolved``: there is no such sheet in this document though its
  discipline's sheets are here and every sheet that could be it was read,
  or the sheet is here, view marks were recognised on it, none for that
  view, and nothing else on it reads like a view mark;
* ``cannot_tell``, with the reason: the sheet is not in the index but a
  sheet that could not be indexed may be it; or no sheet of its
  discipline is in this document (the set may be issued in parts); or a
  sheet number is read on more than one page; or the sheet is here but has
  no text layer, or no view marks were recognised on it, or it has a view
  mark that was not read as one.

It reads what the text layer holds. A sheet with no text layer has no
callouts here, and the result says how many sheets that was. Whether a
detail SUITS the place it is called from is a reviewer's judgment and is
not attempted.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.sheets import (
    _LETTERED, _DECIMAL, HOW_LABEL, HOW_SIZE, HOW_SLOT, SheetIndex,
    SheetRow, WHOLE_SHEET_NUMBER, _reading, _size, discipline,
    normalize_number, number_shape, sheet_index)

BBox = Tuple[float, float, float, float]

#: A view's number or letter in a bubble: 7, 12, A, AA, A1, 3B. Two
#: different letters are a word ("NO", "OF"), not a view.
VIEW_ID = re.compile(r"^(?:[A-Z]?\d{1,2}[A-Z]?|[A-Z]|([A-Z])\1)$")
#: A callout written on one line: 3/A-501, A / S-201.
TEXT_CALLOUT = re.compile(
    rf"(?<![\w./-])([A-Z]?\d{{1,2}}[A-Z]?|[A-Z]|([A-Z])\2)\s*/\s*"
    rf"({_LETTERED}|{_DECIMAL})(?![\w/-]|\.\d)")

RESOLVED, UNRESOLVED, CANNOT_TELL = "resolved", "unresolved", "cannot_tell"

#: A bubble's view number is lettered at least this share of its sheet
#: number's size (as large, less what a size read off a box can be out).
VIEW_SIZE_SHARE = 0.9


@dataclass
class Callout:
    """One callout: where it is drawn, what it points to, and whether that
    is there."""
    page: int
    sheet: Optional[str]
    view: str
    target_sheet: str
    form: str
    bbox: BBox
    status: str = RESOLVED
    why: Optional[str] = None
    target_page: Optional[int] = None

    @property
    def label(self) -> str:
        return f"{self.view}/{self.target_sheet}"

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"page": self.page}
        if self.sheet:
            out["sheet"] = self.sheet
        out.update(callout=self.label, status=self.status)
        if self.why:
            out["why"] = self.why
        if self.target_page is not None:
            out["target"] = {"sheet": self.target_sheet,
                             "page": self.target_page}
        out["bbox"] = [int(round(v)) for v in self.bbox]
        if self.form != "bubble":
            out["form"] = self.form
        return out


@dataclass
class CrossReferences:
    """A document's callouts and the views they were checked against.
    ``views`` maps a sheet number to the view numbers recognised on it,
    ``view_titles`` ``(sheet, view)`` to the title beside the view's mark,
    ``unread_marks`` a sheet number to the view numbers of marks on it that
    read like a view's but were not recognised as one."""
    callouts: List[Callout] = field(default_factory=list)
    views: Dict[str, List[str]] = field(default_factory=dict)
    view_titles: Dict[Tuple[str, str], str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)
    unread_marks: Dict[str, List[str]] = field(default_factory=dict)

    def counts(self) -> Dict[str, int]:
        out = {"callouts": len(self.callouts), RESOLVED: 0, UNRESOLVED: 0,
               CANNOT_TELL: 0}
        for c in self.callouts:
            out[c.status] += 1
        return out

    def with_status(self, status: str) -> List[Callout]:
        return [c for c in self.callouts if c.status == status]


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _direction(line) -> Optional[int]:
    """The way ``line``'s lettering reads, as 0 (upright), 90 (up the
    sheet), 180 (upside down) or 270 (down the sheet); ``None`` for
    lettering at a slant."""
    rot = getattr(line, "rotation", 0.0)
    if rot is None:
        return 0
    turn = float(rot) % 360.0
    for way in (0, 90, 180, 270, 360):
        if abs(turn - way) < 20.0:
            return way % 360
    return None


class _Read:
    """A line as it is read: its box in the frame its lettering reads in
    (x along the line, y from line to line), and the page's box."""
    __slots__ = ("line", "text", "bbox", "page_box", "size", "way")

    def __init__(self, line, way: int):
        self.line = line
        self.text = _clean(line.text)
        self.page_box: BBox = tuple(float(v) for v in line.bbox)
        self.bbox: BBox = _reading(self.page_box, way)
        self.size = _size(line)
        self.way = way


def _union(a: BBox, b: BBox, pad: float = 0.0) -> BBox:
    return (min(a[0], b[0]) - pad, min(a[1], b[1]) - pad,
            max(a[2], b[2]) + pad, max(a[3], b[3]) + pad)


def _overlap(a: BBox, b: BBox) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def _over(view, sheet_line) -> Optional[float]:
    """How well ``view`` sits over ``sheet_line`` as the upper half of that
    bubble (smaller is better), else ``None``: centred on it and just
    above. Being off centre counts double the gap between them: the two
    halves of a bubble share a centre, and a stray number nearby does not."""
    vx0, vy0, vx1, vy1 = view.bbox
    sx0, sy0, sx1, sy1 = sheet_line.bbox
    width = max(sx1 - sx0, 1.0)
    off = abs((vx0 + vx1) / 2.0 - (sx0 + sx1) / 2.0)
    if off > max(4.0, 0.35 * width):
        return None
    if (vy0 + vy1) / 2.0 >= (sy0 + sy1) / 2.0:
        return None
    gap = sy0 - vy1
    if gap < -0.5 * (sy1 - sy0) - 2.0 or gap > 1.0 * (vy1 - vy0) + 4.0:
        return None
    return 2.0 * off + abs(gap)


def text_callouts(text: str) -> List[Tuple[str, str]]:
    """The callouts written on one line in ``text``, as ``(view, sheet)``.
    "W/ S-201" is "with S-201", not view W."""
    return [(m.group(1), m.group(3)) for m in TEXT_CALLOUT.finditer(text)
            if m.group(1) != "W"]


def _listed(line, sheet_lines) -> bool:
    """Whether ``line`` is one row of a list (a cover sheet's index of
    sheet numbers, a numbered list of notes, a revision table's marks):
    another of ``sheet_lines`` sits straight above or below it, a line's
    spacing away, on the same left edge or the same centre. Two bubbles one
    over the other are further apart than that: the lower one's view number
    is between their sheet numbers."""
    x0, y0, x1, y1 = line.bbox
    h = max(y1 - y0, 1.0)
    for other in sheet_lines:
        if other is line:
            continue
        ox0, oy0, ox1, oy1 = other.bbox
        same_column = (abs(ox0 - x0) <= 0.3 * h
                       or abs((ox0 + ox1) - (x0 + x1)) / 2.0 <= 0.3 * h)
        gap = max(oy0 - y1, y0 - oy1)
        if same_column and -0.2 * h <= gap <= 1.2 * h:
            return True
    return False


def _compact(views: List[str]) -> str:
    """View numbers as a reader writes them: "1 to 6", or "A, B, 3"."""
    if views and all(v.isdigit() for v in views):
        nums = sorted({int(v) for v in views})
        if len(nums) > 2 and nums == list(range(nums[0], nums[-1] + 1)):
            return f"{nums[0]} to {nums[-1]}"
        return ", ".join(str(n) for n in nums)
    return ", ".join(sorted(set(views)))


def _title_beside(mark: BBox, lines) -> Optional[str]:
    """The view's title: the nearest wording (three letters or more) to
    the right of its mark, on the mark's own level. ``mark`` and the lines'
    boxes are in the frame their lettering reads in."""
    x0, y0, x1, y1 = mark
    best = None
    for ln in lines:
        text = ln.text
        if len(re.findall(r"[A-Za-z]", text)) < 3:
            continue
        lx0, ly0, lx1, ly1 = ln.bbox
        if lx0 < x1 - 1.0 or lx0 - x1 > 6.0 * (x1 - x0):
            continue
        if ly1 < y0 or ly0 > y1:
            continue
        if best is None or (lx0, ly0) < (best.bbox[0], best.bbox[1]):
            best = ln
    return best.text[:120] if best is not None else None


@dataclass
class _Bubble:
    """A view number over a sheet number, as drawn on one page."""
    view: str
    sheet: str
    #: The bubble's box on the page (both halves, padded).
    box: BBox
    #: The way its lettering reads (0 upright; see :func:`_direction`).
    way: int
    #: The title beside its mark, if any.
    title: Optional[str]


def _page_marks(lines: Sequence[Any], sheet_like,
                skip: Optional[BBox] = None
                ) -> Tuple[List[_Bubble], List[Tuple[str, str]]]:
    """``(bubbles, lone)`` of one page. ``bubbles``: every view number
    centred over a sheet number (``sheet_like``) in lettering at least as
    large, in each reading direction, each with the title beside it if
    any; a sheet number in a list of them, or inside ``skip`` (the title
    block's own number), is not one. ``lone``: ``(view, title)`` for each
    view number that is in no bubble, not in a list, and has a title
    beside it."""
    by_way: Dict[int, List[_Read]] = {}
    for ln in lines:
        way = _direction(ln)
        if way is not None and _clean(ln.text):
            by_way.setdefault(way, []).append(_Read(ln, way))
    bubbles: List[_Bubble] = []
    lone: List[Tuple[str, str]] = []
    for way, read in by_way.items():
        sheet_lines = [r for r in read if sheet_like(r.text)]
        view_lines = sorted(
            (r for r in read if VIEW_ID.match(r.text)
             and not sheet_like(r.text)),
            key=lambda r: r.bbox[0] + r.bbox[2])
        # twice each view number's centre, in order: a bubble's two halves
        # share a centre, so only the view numbers near it are tried
        centres = [r.bbox[0] + r.bbox[2] for r in view_lines]
        used: set = set()
        for sl in sheet_lines:
            if skip is not None and _overlap(sl.page_box, skip):
                continue                      # the title block's own number
            if _listed(sl, sheet_lines):
                continue                      # a row of a list of sheets
            mid = sl.bbox[0] + sl.bbox[2]
            reach = 2.0 * max(4.0, 0.35 * (sl.bbox[2] - sl.bbox[0])) + 0.01
            over = [(fit, i)
                    for i in range(bisect_left(centres, mid - reach),
                                   bisect_right(centres, mid + reach))
                    if i not in used
                    and view_lines[i].size >= VIEW_SIZE_SHARE * sl.size
                    for fit in [_over(view_lines[i], sl)] if fit is not None]
            if not over:
                continue
            _fit, i = min(over)
            used.add(i)
            vl = view_lines[i]
            pad = 0.6 * max(vl.bbox[3] - vl.bbox[1], sl.bbox[3] - sl.bbox[1])
            title = _title_beside(_union(vl.bbox, sl.bbox, pad), read)
            bubbles.append(_Bubble(vl.text, sl.text,
                                   _union(vl.page_box, sl.page_box, pad),
                                   way, title))
        for i, vl in enumerate(view_lines):
            if i in used or _listed(vl, view_lines):
                continue
            title = _title_beside(vl.bbox, read)
            if title:
                lone.append((vl.text, title))
    return bubbles, lone


def titled_bubbles(lines: Sequence[Any], sheet_like) -> List[Tuple[str, str]]:
    """``(view, sheet)`` of every bubble on a page that has a title beside
    it (a view's title mark, in any reading direction): what
    :mod:`planlens.document.sheets` numbers a sheet by when its title block
    does not."""
    return [(b.view, b.sheet) for b in _page_marks(lines, sheet_like)[0]
            if b.title]


def _could_be(row: SheetRow, target: str, prints_numbers: bool) -> bool:
    """Whether a sheet that could not be indexed could be sheet ``target``:
    one with no readable text always could; a readable one only if it
    prints that number in its title-block bands, where the document shows
    that its title blocks print numbers (``prints_numbers``: some sheet's
    number was read from its title block). In a set whose title blocks
    print none, any sheet could be any number."""
    return (not row.readable or not prints_numbers
            or target in row.tokens)


def _pages(pages: Sequence[int], most: int = 4) -> str:
    shown = ", ".join(str(p) for p in pages[:most])
    return shown + (f" and {len(pages) - most} more"
                    if len(pages) > most else "")


def _discipline_text(number: str) -> str:
    d = discipline(number)
    return f"{d} sheets" if d.isalpha() else f"sheets numbered {d}.x"


def cross_references(doc, index: Optional[SheetIndex] = None
                     ) -> CrossReferences:
    """The callouts of ``doc`` (a :class:`planlens.document.Document`),
    each checked against the sheet index and the views recognised on the
    sheet it names. ``index`` is the document's sheet index, read if not
    given."""
    index = index if index is not None else sheet_index(doc)
    refs = CrossReferences()
    known: Dict[str, int] = {}
    for row in index.indexed:
        known.setdefault(normalize_number(row.number), row.page)
    shapes = {number_shape(n) for n in known}
    disciplines = {discipline(n) for n in known}
    doubled = index.duplicates()

    def sheet_like(token: str) -> bool:
        token = normalize_number(token)
        return bool(WHOLE_SHEET_NUMBER.match(token)) and (
            token in known or number_shape(token) in shapes)

    found: List[Callout] = []
    unread: Dict[str, set] = {}
    for row in index.rows:
        try:
            lines = list(doc._page_text(row.page)[0])
        except Exception:                             # noqa: BLE001
            lines = []
        if not lines:
            continue
        own = normalize_number(row.number) if row.number else None
        bubbles, lone = _page_marks(lines, sheet_like, row.bbox)
        for b in bubbles:
            target = normalize_number(b.sheet)
            if own is not None and target == own:
                if b.title:
                    held = refs.views.setdefault(row.number, [])
                    if b.view not in held:
                        held.append(b.view)
                        refs.view_titles[(row.number, b.view)] = b.title
                    continue
                if b.way != 0:
                    # a turned mark naming this sheet with no title found:
                    # it may be a view, and is not read as a callout
                    unread.setdefault(row.number, set()).add(b.view)
                    continue
            if b.way != 0:
                continue                 # a turned callout: not read here
            found.append(Callout(row.page, row.number, b.view, b.sheet,
                                 "bubble", b.box))
        if row.number and lone:
            unread.setdefault(row.number, set()).update(v for v, _t in lone)
        for ln in lines:
            for view, target in text_callouts(ln.text):
                if not sheet_like(target):
                    continue
                found.append(Callout(row.page, row.number, view, target,
                                     "text",
                                     tuple(float(v) for v in ln.bbox)))
    for number, marks in unread.items():
        left = marks - set(refs.views.get(number, ()))
        if left:
            refs.unread_marks[number] = sorted(left)

    blind = {r.page for r in index.unread}
    prints_numbers = any(r.how in (HOW_SLOT, HOW_LABEL, HOW_SIZE)
                         for r in index.rows)
    could: Dict[str, List[int]] = {}
    for c in found:
        target = normalize_number(c.target_sheet)
        if target not in could:
            could[target] = [r.page for r in index.not_indexed
                             if _could_be(r, target, prints_numbers)]
        _judge(c, index, refs, known, disciplines, doubled, blind,
               could[target])
    refs.callouts = sorted(found, key=lambda c: (c.page, c.bbox[1],
                                                 c.bbox[0]))
    for held in refs.views.values():
        held.sort(key=lambda v: (0, int(v), "") if v.isdigit()
                  else (1, 0, v))
    if blind:
        refs.notes.append(
            f"{len(blind)} sheet(s) have no usable text: their callouts are "
            f"not in this list")
    if not found:
        refs.notes.append("no callouts were recognised in the text")
    return refs


def _judge(c: Callout, index: SheetIndex, refs: CrossReferences,
           known: Dict[str, int], disciplines: set,
           doubled: Dict[str, List[int]], blind: set,
           could: List[int]) -> None:
    """Set ``c.status`` and ``c.why``: ``unresolved`` only where the text
    shows it; where it cannot, ``cannot_tell`` with the reason. ``could``
    is the pages of the sheets not indexed that could be the target."""
    target = normalize_number(c.target_sheet)
    if target not in known:
        c.status = CANNOT_TELL
        if doubled:
            number, pages = next(iter(sorted(doubled.items(),
                                             key=lambda kv: kv[1][0])))
            more = (f" (and {len(doubled) - 1} more number(s) read on two "
                    f"pages or more)" if len(doubled) > 1 else "")
            c.why = (f"no sheet {c.target_sheet} among the sheets indexed, "
                     f"but {number} is read on pages {_pages(pages)}{more}: "
                     f"one of those may be {c.target_sheet}")
        elif could:
            c.why = (f"no sheet {c.target_sheet} among the sheets indexed, "
                     f"and {len(could)} sheet(s) that could not be indexed "
                     f"may be it (page(s) {_pages(could)})")
        elif discipline(target) not in disciplines:
            c.why = (f"no {_discipline_text(target)} in this document; the "
                     f"set may be issued in parts, so {c.target_sheet} may "
                     f"be in another document")
        else:
            c.status = UNRESOLVED
            c.why = f"there is no sheet {c.target_sheet} in this document"
        return
    c.target_page = known[target]
    number = index.number_of(c.target_page) or c.target_sheet
    held = refs.views.get(number) or []
    if target in doubled:
        c.status = CANNOT_TELL
        c.why = (f"{number} is read on pages {_pages(doubled[target])}, so "
                 f"which sheet the callout names cannot be told")
    elif c.target_page in blind:
        c.status = CANNOT_TELL
        c.why = (f"{number} has no text layer, so its views cannot be "
                 f"checked from the text")
    elif not held:
        c.status = CANNOT_TELL
        c.why = (f"no view marks were recognised on {number}, so its "
                 f"views cannot be checked from the text")
    elif c.view not in held:
        other = refs.unread_marks.get(number)
        if other:
            c.status = CANNOT_TELL
            c.why = (f"view marks recognised on {number}: {_compact(held)}; "
                     f"none for {c.view}, but {number} also has marks that "
                     f"read like a view's and were not recognised as one "
                     f"(view {_compact(other)}), so its views may not all "
                     f"have been recognised")
        else:
            c.status = UNRESOLVED
            c.why = (f"view marks recognised on {number}: {_compact(held)}; "
                     f"none for {c.view}")


__all__ = ["Callout", "CrossReferences", "cross_references",
           "titled_bubbles", "text_callouts", "VIEW_ID", "TEXT_CALLOUT",
           "RESOLVED", "UNRESOLVED", "CANNOT_TELL"]
