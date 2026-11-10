"""A drawing set's callouts, and whether each one leads anywhere.

A callout sends the reader to a view on a sheet: detail 7 on A-501, section
2 on A-301. On the sheet it is a reference bubble, a circle with the view
number over the sheet number, two separate pieces of text; in a note it is
written on one line, "SEE 3/A-501". Checking a set's callouts by eye is
slow and is exactly the kind of check that gets skipped. With the sheet
index (:mod:`planlens.document.sheets`) it is a lookup.

WHAT IS READ, from the text and where it sits (nothing is looked at):

* **A bubble**: a piece of text that is a sheet number and nothing else,
  with a short view number or letter directly over it.
* **A view** is a bubble that names the sheet it is on: the view's own
  title mark. The views recognised on a sheet are what a callout to that
  sheet is checked against.
* **A callout** is any other bubble, and any "view/sheet" written on one
  line.

WHAT IS SAID of each callout:

* ``resolved``: the sheet is in the set and holds that view;
* ``unresolved``: there is no such sheet in the set, or the sheet is there
  and holds recognised views but not that one;
* ``cannot_tell``: the sheet is there but no view marks were recognised on
  it (a set that titles its views another way, or a sheet with no text
  layer), or the sheet is not in the index while some sheets could not be
  indexed (it may be one of them).

It reads what the text layer holds. A sheet with no text layer has no
callouts here, and the result says how many sheets that was. Whether a
detail SUITS the place it is called from is a reviewer's judgment and is
not attempted.
"""

from __future__ import annotations

import re
from bisect import bisect_left, bisect_right
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from planlens.document.sheets import (
    _LETTERED, _DECIMAL, SheetIndex, WHOLE_SHEET_NUMBER, normalize_number,
    number_shape, sheet_index)

BBox = Tuple[float, float, float, float]

#: A view's number or letter in a bubble: 7, 12, A, AA, A1, 3B. Two
#: different letters are a word ("NO", "OF"), not a view.
VIEW_ID = re.compile(r"^(?:[A-Z]?\d{1,2}[A-Z]?|[A-Z]|([A-Z])\1)$")
#: A callout written on one line: 3/A-501, A / S-201.
TEXT_CALLOUT = re.compile(
    rf"(?<![\w./-])([A-Z]?\d{{1,2}}[A-Z]?|[A-Z]|([A-Z])\2)\s*/\s*"
    rf"({_LETTERED}|{_DECIMAL})(?![\w/-]|\.\d)")

RESOLVED, UNRESOLVED, CANNOT_TELL = "resolved", "unresolved", "cannot_tell"


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
    ``view_titles`` ``(sheet, view)`` to the title beside the view's mark."""
    callouts: List[Callout] = field(default_factory=list)
    views: Dict[str, List[str]] = field(default_factory=dict)
    view_titles: Dict[Tuple[str, str], str] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

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


def _upright(line) -> bool:
    rot = getattr(line, "rotation", 0.0)
    return rot is None or abs(float(rot) % 360.0) < 20.0 \
        or abs(float(rot) % 360.0) > 340.0


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
    """Whether ``line`` is one row of a list of sheet numbers (a cover
    sheet's index, a drawing list): another sheet number sits straight
    above or below it, a line's spacing away, on the same left edge or the
    same centre. Two bubbles one over the other are further apart than
    that: the lower one's view number is between their sheet numbers."""
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
    """The view's title: the nearest text to the right of its mark, on the
    mark's own level."""
    x0, y0, x1, y1 = mark
    best = None
    for ln in lines:
        text = _clean(ln.text)
        if len(re.findall(r"[A-Za-z]", text)) < 3:
            continue
        lx0, ly0, lx1, ly1 = ln.bbox
        if lx0 < x1 - 1.0 or lx0 - x1 > 6.0 * (x1 - x0):
            continue
        if ly1 < y0 or ly0 > y1:
            continue
        if best is None or (lx0, ly0) < (best.bbox[0], best.bbox[1]):
            best = ln
    return _clean(best.text)[:120] if best is not None else None


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

    def sheet_like(token: str) -> bool:
        token = normalize_number(token)
        return bool(WHOLE_SHEET_NUMBER.match(token)) and (
            token in known or number_shape(token) in shapes)

    found: List[Callout] = []
    for row in index.rows:
        try:
            lines = list(doc._page_text(row.page)[0])
        except Exception:                             # noqa: BLE001
            lines = []
        if not lines:
            continue
        own = normalize_number(row.number) if row.number else None
        upright = [ln for ln in lines if _upright(ln)]
        sheet_lines = [ln for ln in upright if sheet_like(_clean(ln.text))]
        view_lines = sorted(
            (ln for ln in upright if VIEW_ID.match(_clean(ln.text))
             and not sheet_like(_clean(ln.text))),
            key=lambda ln: ln.bbox[0] + ln.bbox[2])
        # twice each view number's centre, in order: a bubble's two halves
        # share a centre, so only the view numbers near it are tried
        centres = [ln.bbox[0] + ln.bbox[2] for ln in view_lines]
        used: set = set()
        for sl in sheet_lines:
            if row.bbox is not None and _overlap(sl.bbox, row.bbox):
                continue                      # the title block's own number
            if _listed(sl, sheet_lines):
                continue                      # a row of a list of sheets
            mid = sl.bbox[0] + sl.bbox[2]
            reach = 2.0 * max(4.0, 0.35 * (sl.bbox[2] - sl.bbox[0])) + 0.01
            over = [(fit, i)
                    for i in range(bisect_left(centres, mid - reach),
                                   bisect_right(centres, mid + reach))
                    if i not in used
                    for fit in [_over(view_lines[i], sl)] if fit is not None]
            if not over:
                continue
            _fit, i = min(over)
            used.add(i)
            vl = view_lines[i]
            view = _clean(vl.text)
            target = normalize_number(_clean(sl.text))
            pad = 0.6 * max(vl.bbox[3] - vl.bbox[1], sl.bbox[3] - sl.bbox[1])
            box = _union(vl.bbox, sl.bbox, pad)
            if own is not None and target == own:
                held = refs.views.setdefault(row.number, [])
                if view not in held:
                    held.append(view)
                    title = _title_beside(box, lines)
                    if title:
                        refs.view_titles[(row.number, view)] = title
                continue
            found.append(Callout(row.page, row.number, view,
                                 _clean(sl.text), "bubble", box))
        for ln in lines:
            for view, target in text_callouts(ln.text):
                if not sheet_like(target):
                    continue
                found.append(Callout(row.page, row.number, view, target,
                                     "text",
                                     tuple(float(v) for v in ln.bbox)))

    unread = len(index.unread)
    unnamed = len(index.not_indexed)
    blind = {r.page for r in index.unread}
    for c in found:
        target = normalize_number(c.target_sheet)
        if target not in known:
            if unnamed:
                c.status = CANNOT_TELL
                c.why = (f"no sheet {c.target_sheet} among the sheets "
                         f"indexed, and {unnamed} sheet(s) could not be "
                         f"indexed")
            else:
                c.status = UNRESOLVED
                c.why = f"there is no sheet {c.target_sheet} in this set"
            continue
        c.target_page = known[target]
        number = index.number_of(c.target_page) or c.target_sheet
        held = refs.views.get(number) or []
        if c.target_page in blind:
            c.status = CANNOT_TELL
            c.why = (f"{number} has no text layer, so its views cannot be "
                     f"checked from the text")
        elif not held:
            c.status = CANNOT_TELL
            c.why = (f"no view marks were recognised on {number}, so its "
                     f"views cannot be checked from the text")
        elif c.view not in held:
            c.status = UNRESOLVED
            c.why = f"{number} holds views {_compact(held)}, not {c.view}"
    refs.callouts = sorted(found, key=lambda c: (c.page, c.bbox[1],
                                                 c.bbox[0]))
    for held in refs.views.values():
        held.sort(key=lambda v: (0, int(v), "") if v.isdigit()
                  else (1, 0, v))
    if unread:
        refs.notes.append(
            f"{unread} sheet(s) have no usable text: their callouts are not "
            f"in this list")
    if not found:
        refs.notes.append("no callouts were recognised in the text")
    return refs


__all__ = ["Callout", "CrossReferences", "cross_references",
           "text_callouts", "VIEW_ID", "TEXT_CALLOUT", "RESOLVED",
           "UNRESOLVED", "CANNOT_TELL"]
