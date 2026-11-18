"""Columns of prose on a page, and whether the file's order runs across them.

planlens hands a page's lines in the order the file draws them (content-stream
order). For word-processor output that is the reading order, column by
column. Some writers draw a page set in columns ROW by row instead: the first
line of the left column, then the first line of the right column, then the
second line of the left. Read in that order the page is two texts shuffled
together, and nothing said so (PL-09).

:func:`order_runs_across_columns` finds that case, and the document layer
says so in the page's warnings: a repair, not a new reading, because the lines
it hands are unchanged. :func:`column_order` is the new reading, for a host
that asks for it: the same lines, column by column. Nothing calls it by
default.

What counts as a column is measured on lines of prose only (long enough to be
words, horizontal as displayed): a vertical gap that no more than a fifth of
them cross, at least four lines on each side, both sides beside each other on
the page, and each side's lines sharing a left or a right edge. Short pieces
(labels, cells, page numbers) and scattered callouts on a drawing do not make
columns, and a line that crosses a gap (a heading over both columns) is a
break between two runs of columns, not a member of one.

The order is said to run across the columns when consecutive lines of prose
sit in different columns on the same row at least four times and for at least
a quarter of the column lines. A page drawn column by column moves between
columns a few times (at the foot of each column) and never on the same row.
"""

from __future__ import annotations

from bisect import bisect_left, bisect_right
from statistics import median
from typing import List, Optional, Sequence

#: A line of prose: at least this many characters and words. A table cell,
#: a form label or a page number is shorter.
PROSE_MIN_CHARS = 20
PROSE_MIN_WORDS = 4
#: Lines of prose a column needs, on each side of a gap.
COLUMN_MIN_LINES = 4
#: The share of prose lines that may cross a gap (headings and full-width
#: paragraphs over the columns).
SPANNING_MAX_FRACTION = 0.2
#: The narrowest gap between columns, as a share of the median line height
#: (0.6 of a 13 pt line is 8 pt; word spaces are a quarter of that).
GUTTER_MIN_LINE_HEIGHTS = 0.6
#: A column's lines share a left or a right edge, to this tolerance, for at
#: least this share of them. Paragraph indents leave most lines on the edge;
#: callouts scattered over a drawing share none.
EDGE_TOLERANCE_PT = 3.0
EDGE_MIN_FRACTION = 0.6
#: Two sides are beside each other when their heights overlap by at least
#: this share of the shorter one (not one block above the other).
SIDE_BY_SIDE_MIN = 0.5
#: How often consecutive prose lines must sit in different columns on the
#: same row before the order is said to run across them.
ACROSS_MIN = 4
ACROSS_MIN_FRACTION = 0.25
#: A line crosses a gap when it reaches past its centre by more than this.
CROSS_TOLERANCE_PT = 1.0
#: Most columns a page is split into (a gap is looked for inside each side).
MAX_COLUMNS = 4


def _horizontal(line) -> bool:
    """Horizontal as displayed, with a box (a text source may give a line
    none, which then keeps its place after the rest)."""
    if getattr(line, "bbox", None) is None:
        return False
    rot = float(getattr(line, "rotation", 0.0) or 0.0) % 360.0
    return rot < 1.0 or rot > 359.0


def _is_prose(line) -> bool:
    text = (line.text or "").strip()
    return (_horizontal(line) and len(text) >= PROSE_MIN_CHARS
            and len(text.split()) >= PROSE_MIN_WORDS)


def _shares_an_edge(lines) -> bool:
    for edge in (0, 2):
        values = sorted(ln.bbox[edge] for ln in lines)
        best = 0
        for i, v in enumerate(values):
            j = bisect_right(values, v + EDGE_TOLERANCE_PT)
            best = max(best, j - i)
        if best >= EDGE_MIN_FRACTION * len(lines):
            return True
    return False


def _side_by_side(left, right) -> bool:
    ly0, ly1 = min(ln.bbox[1] for ln in left), max(ln.bbox[3] for ln in left)
    ry0, ry1 = min(ln.bbox[1] for ln in right), max(ln.bbox[3] for ln in right)
    overlap = min(ly1, ry1) - max(ly0, ry0)
    shorter = min(ly1 - ly0, ry1 - ry0)
    return shorter > 0 and overlap >= SIDE_BY_SIDE_MIN * shorter


def _gutter(lines, min_gap: float, n_all: int) -> Optional[float]:
    """The centre of the best gap that splits ``lines`` into two columns."""
    if len(lines) < 2 * COLUMN_MIN_LINES:
        return None
    x0s = sorted(ln.bbox[0] for ln in lines)
    x1s = sorted(ln.bbox[2] for ln in lines)
    n = len(lines)
    best = None
    for edge in x1s:                      # a gap opens at a line's right end
        n_left = bisect_right(x1s, edge)
        i_right = bisect_left(x0s, edge)
        n_right = n - i_right
        if n_left < COLUMN_MIN_LINES or n_right < COLUMN_MIN_LINES:
            continue
        gap = x0s[i_right] - edge
        if gap < min_gap:
            continue
        crossing = n - n_left - n_right
        if crossing > SPANNING_MAX_FRACTION * n_all:
            continue
        key = (crossing, -gap)
        if best is None or key < best[0]:
            best = (key, edge + gap / 2.0)
    if best is None:
        return None
    centre = best[1]
    left = [ln for ln in lines if ln.bbox[2] <= centre]
    right = [ln for ln in lines if ln.bbox[0] >= centre]
    if not _side_by_side(left, right):
        return None
    return centre


def _split(group, min_gap: float, n_all: int, depth: int):
    """The gutters inside ``group``, or None when a column it would make
    shares no edge (lines scattered over a drawing, not set in columns)."""
    g = (_gutter(group, min_gap, n_all)
         if depth < MAX_COLUMNS - 1 else None)
    if g is None:
        return [] if _shares_an_edge(group) else None
    left = _split([ln for ln in group if ln.bbox[2] <= g], min_gap, n_all,
                  depth + 1)
    right = _split([ln for ln in group if ln.bbox[0] >= g], min_gap, n_all,
                   depth + 1)
    if left is None or right is None:
        return None
    return left + [g] + right


def column_gutters(lines: Sequence) -> List[float]:
    """The x of each gap between columns of prose, left to right; ``[]``
    when the page's prose is set in one column (or there is too little)."""
    prose = [ln for ln in lines if _is_prose(ln)]
    if len(prose) < 2 * COLUMN_MIN_LINES:
        return []
    min_gap = GUTTER_MIN_LINE_HEIGHTS * median(
        ln.bbox[3] - ln.bbox[1] for ln in prose)
    gutters = _split(prose, min_gap, len(prose), 0)
    return sorted(gutters or [])


def _column_of(line, gutters: Sequence[float]) -> Optional[int]:
    """The column a line sits in, or None when it crosses a gap."""
    x0, x1 = line.bbox[0], line.bbox[2]
    for g in gutters:
        if x0 < g - CROSS_TOLERANCE_PT and x1 > g + CROSS_TOLERANCE_PT:
            return None
    centre = (x0 + x1) / 2.0
    return sum(1 for g in gutters if g <= centre)


def _same_row(a, b) -> bool:
    overlap = min(a.bbox[3], b.bbox[3]) - max(a.bbox[1], b.bbox[1])
    shorter = min(a.bbox[3] - a.bbox[1], b.bbox[3] - b.bbox[1])
    return shorter > 0 and overlap >= 0.5 * shorter


def order_runs_across_columns(lines: Sequence) -> Optional[int]:
    """The number of columns when the lines, in the order given, alternate
    between columns of prose row by row; None when they read column by
    column, or the prose is in one column."""
    gutters = column_gutters(lines)
    if not gutters:
        return None
    placed = [(ln, _column_of(ln, gutters)) for ln in lines if _is_prose(ln)]
    placed = [(ln, c) for ln, c in placed if c is not None]
    across = sum(1 for (a, ca), (b, cb) in zip(placed, placed[1:])
                 if ca != cb and _same_row(a, b))
    if across >= ACROSS_MIN and across >= ACROSS_MIN_FRACTION * len(placed):
        return len(gutters) + 1
    return None


def across_columns_warning(n_columns: int) -> str:
    """The page warning for a page whose order runs across its columns."""
    return (f"reading order not reconstructed: the text is set in "
            f"{n_columns} columns and the file draws its lines across them, "
            f"so the lines alternate between the columns — read each column "
            f"down by its boxes")


def column_order(lines: Sequence) -> List:
    """The same lines, column by column (a new reading: only for a host that
    asks).

    A line that crosses a gap (a heading over the columns) is a break: the
    columns above it are read first, each top to bottom, then the line, then
    the columns below it. Lines that are not horizontal keep their order,
    after the rest, as do lines with no box. With one column the order
    given is returned unchanged.
    Line ids are not renumbered, so a citation still names the same line.
    """
    gutters = column_gutters(lines)
    if not gutters:
        return list(lines)
    flat = [ln for ln in lines if _horizontal(ln)]
    turned = [ln for ln in lines if not _horizontal(ln)]
    spanning = sorted((ln for ln in flat if _column_of(ln, gutters) is None),
                      key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    tops = [(ln.bbox[1] + ln.bbox[3]) / 2.0 for ln in spanning]

    def key(ln):
        mid = (ln.bbox[1] + ln.bbox[3]) / 2.0
        col = _column_of(ln, gutters)
        above = bisect_left(tops, mid)
        if col is None:
            return (2 * above + 1, 0, ln.bbox[1], ln.bbox[0])
        return (2 * above, col, ln.bbox[1], ln.bbox[0])

    return sorted(flat, key=key) + turned
