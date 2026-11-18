"""A table that runs on from the foot of one page to the head of the next.

Tables are read page by page, so a table printed over two pages comes back
as two tables, and the second has its column titles on the page before
(PL-09). This links the two, for a host that asks (``Document(...,
continued_tables=True)``, off by default): each table gets a note naming the
other. The tables themselves, their rows and their ids, are unchanged.

The later table continues the earlier one only when all of these hold:

* the earlier table is the last on its page and nothing of the page's body
  is printed below it (a note or a paragraph under a table ends it);
* the later table is the first on the next page and nothing of the body is
  printed above or beside it but a line saying it is continued or a line
  printed above or beside the earlier table too (its caption, its column
  titles outside the grid);
* both have the same number of columns and the same left and right edges;
* the running header and footer are the same on both pages (page numbers
  aside; the table's own lines in those bands left out): the logs of two
  borings, one page each, are two tables even when their forms match,
  wherever the page prints the boring's number outside the grid;
* the later table repeats the earlier one's first row (the column titles),
  or a line above it or its own first row says it is continued.

Two pages that merely use the same ruled form are therefore not linked
unless one of them says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import List, Optional, Sequence, Set

from planlens.document.structure import BAND_FRACTION

#: How far the two tables' left and right edges may differ, in points.
EDGE_TOLERANCE_PT = 6.0

_RE_CONTINUED = re.compile(r"\b(?:continued|cont'?d\b|cont\.)", re.I)
_RE_PAGE_NUMBER = re.compile(
    r"\b(?:page|sheet|pg\.?|p\.)\s*\d+(?:\s*(?:of|/)\s*\d+)?", re.I)


@dataclass(frozen=True)
class TableContinuation:
    """The later table (``table``) continues the earlier (``continues``)."""

    table: str
    continues: str
    page: int
    from_page: int
    #: The later table's first row repeats the earlier table's.
    header_repeated: bool


def _norm(text: Optional[str]) -> str:
    return " ".join((text or "").split()).lower()


def _centre_y(ln) -> float:
    return (ln.bbox[1] + ln.bbox[3]) / 2.0


def _inside(ln, bbox) -> bool:
    cx = (ln.bbox[0] + ln.bbox[2]) / 2.0
    cy = _centre_y(ln)
    return (bbox[0] - 2 <= cx <= bbox[2] + 2
            and bbox[1] - 2 <= cy <= bbox[3] + 2)


def _in_band(ln, height: float) -> bool:
    return (ln.bbox[3] <= BAND_FRACTION * height
            or ln.bbox[1] >= (1.0 - BAND_FRACTION) * height)


def _running_bands(pc, table) -> List[str]:
    """The page's running header and footer, page numbers aside, without
    the lines of ``table`` (a table may start or end inside a band)."""
    out = []
    for ln in pc.lines:
        if (ln.bbox is not None and _in_band(ln, pc.height)
                and not _inside(ln, table.bbox)):
            text = _RE_PAGE_NUMBER.sub("page #", _norm(ln.text))
            if text and not text.replace("page #", "").strip(" #.-|"
                                                             ).isdigit():
                out.append(text)
    return sorted(out)


def _body_outside(pc, table):
    return [ln for ln in pc.lines
            if (ln.text or "").strip() and not _in_band(ln, pc.height)
            and not _inside(ln, table.bbox)]


def _row_key(row) -> tuple:
    return tuple(_norm(c) for c in (row or ()))


def _says_continued(texts: Sequence[str]) -> bool:
    return any(_RE_CONTINUED.search(t or "") for t in texts)


def table_continuation(earlier, later) -> Optional[TableContinuation]:
    """How the first table of ``later`` (a page's content) continues the
    last table of ``earlier`` (the page before), or None."""
    if not earlier.tables or not later.tables:
        return None
    if any(ln.bbox is None and (ln.text or "").strip()
           for ln in list(earlier.lines) + list(later.lines)):
        return None            # a line placed nowhere: cannot tell
    if abs(earlier.width - later.width) > 1.0:
        return None
    a = max(earlier.tables, key=lambda t: t.bbox[3])
    b = min(later.tables, key=lambda t: t.bbox[1])
    if a.n_cols < 2 or a.n_cols != b.n_cols:
        return None
    if (abs(a.bbox[0] - b.bbox[0]) > EDGE_TOLERANCE_PT
            or abs(a.bbox[2] - b.bbox[2]) > EDGE_TOLERANCE_PT):
        return None
    if any(_centre_y(ln) > a.bbox[3] for ln in _body_outside(earlier, a)):
        return None
    carried: Set[str] = {_norm(ln.text) for ln in _body_outside(earlier, a)}
    above = [ln.text for ln in _body_outside(later, b)
             if _centre_y(ln) <= b.bbox[3]]       # above it or beside it
    if any(not (_RE_CONTINUED.search(t) or _norm(t) in carried)
           for t in above):
        return None
    if _running_bands(earlier, a) != _running_bands(later, b):
        return None
    repeated = bool(a.rows and b.rows and any(_row_key(a.rows[0]))
                    and _row_key(a.rows[0]) == _row_key(b.rows[0]))
    if a.header and b.header and _row_key(a.header) == _row_key(b.header):
        repeated = True
    top_band = [ln.text for ln in later.lines if ln.bbox is not None
                and ln.bbox[3] <= BAND_FRACTION * later.height]
    if not (repeated or _says_continued(above + top_band)
            or _says_continued([c or "" for c in (b.rows[0] if b.rows
                                                  else ())])):
        return None
    return TableContinuation(table=b.id, continues=a.id, page=b.page,
                             from_page=a.page, header_repeated=repeated)


def with_continuation_notes(pc, before=None, after=None):
    """``pc`` with a note on each of its tables that runs on from the page
    ``before`` or on to the page ``after`` (page contents, or None)."""
    notes = {}
    if before is not None:
        link = table_continuation(before, pc)
        if link is not None:
            tail = ("its first row repeats that table's column titles"
                    if link.header_repeated else
                    "the column titles are on that page, not repeated here")
            notes.setdefault(link.table, []).append(
                f"continues table {link.continues} from page "
                f"{link.from_page}: {tail}")
    if after is not None:
        link = table_continuation(pc, after)
        if link is not None:
            notes.setdefault(link.continues, []).append(
                f"continues on page {link.page} as table {link.table}")
    if not notes:
        return pc
    tables = [replace(t, notes=list(t.notes) + notes[t.id])
              if t.id in notes else t for t in pc.tables]
    return replace(pc, tables=tables)
