"""Tables, detected with PyMuPDF's ``page.find_tables()``, in the displayed frame.

``find_tables`` reads ruling lines and text alignment from the vector content, so
it works on boring logs, calculation printouts and schedules that were produced
digitally; it cannot see tables inside a scanned image (use an OCR/Azure text
source for those). It is the slowest per-page step measured — about 0.1 s on a
typical page and over 1 s on a dense drawing sheet (30 s across a real 260-page
submittal) — so the document layer runs it only when a page is opened, never
while building the page map.
"""

from __future__ import annotations

import contextlib
import io
from typing import List, Optional, Tuple

from planlens.document.frame import to_display_bbox
from planlens.document.model import Table


def _cell(v) -> Optional[str]:
    if v is None:
        return None
    return str(v).strip()


def extract_tables(page, page_index: int) -> Tuple[List[Table], List[str]]:
    """Detected tables for one ``fitz.Page`` plus any warnings."""
    warnings: List[str] = []
    try:
        # PyMuPDF prints an advisory about its optional layout package on first
        # use; keep it out of stdout, which a tool harness may be capturing.
        with contextlib.redirect_stdout(io.StringIO()):
            found = page.find_tables()
    except Exception as exc:  # pragma: no cover - defensive
        return [], [f"table detection failed: {type(exc).__name__}: {exc}"]

    tables: List[Table] = []
    for t in found.tables:
        try:
            grid = [[_cell(v) for v in row] for row in t.extract()]
        except Exception as exc:  # pragma: no cover - defensive
            warnings.append(f"table {len(tables)} could not be read: {exc}")
            continue
        notes: List[str] = []
        # The header is reported only when PyMuPDF found it OUTSIDE the grid
        # (column titles printed above the ruling). Its in-grid guess is
        # "row 0", which on forms such as boring logs is a title strip, not
        # column names — so row 0 stays in ``rows`` and the reader decides.
        header = None
        h = getattr(t, "header", None)
        if h is not None and getattr(h, "external", False):
            names = [_cell(v) for v in (h.names or [])]
            if any(names):
                header = names
                notes.append("header read from text above the table grid")
        n_cells, n_empty = _cell_counts(grid)
        grid, dropped = _drop_empty_columns(grid, header)
        if dropped:
            notes.append(f"{len(dropped)} empty column(s) omitted")
            if header is not None:
                header = [v for i, v in enumerate(header) if i not in dropped]
        tables.append(Table(
            id=f"p{page_index}.tbl{len(tables)}",
            page=page_index,
            bbox=to_display_bbox(page, t.bbox),
            n_rows=len(grid),
            n_cols=max((len(r) for r in grid), default=0),
            rows=grid,
            header=header,
            notes=notes,
            n_cells=n_cells,
            n_empty_cells=n_empty,
        ))
    return tables, warnings


def _cell_counts(grid: List[List[Optional[str]]]):
    cells = [c for row in grid for c in row]
    return len(cells), sum(1 for c in cells if not c)


def _drop_empty_columns(grid: List[List[Optional[str]]],
                        header: Optional[List[Optional[str]]]):
    """Remove columns with no text in any row (or the header).

    Ruled forms produce many sliver columns from decorative lines; they carry
    no content and multiply the tokens a reader has to scan. The count is
    reported in the table's notes.
    """
    if not grid:
        return grid, set()
    width = max(len(r) for r in grid)
    empty = set()
    for c in range(width):
        if header is not None and c < len(header) and header[c]:
            continue
        if all((c >= len(r)) or not r[c] for r in grid):
            empty.add(c)
    if not empty or len(empty) == width:
        return grid, set()
    return [[v for i, v in enumerate(r) if i not in empty] for r in grid], empty
