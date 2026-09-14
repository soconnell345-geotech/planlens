"""When text is not enough: advice on how to read a page.

The text tools are cheap and exact, so they come first. But a scanned figure,
a boring log, a plan sheet — the pages a reviewer most needs — are pictures
with labels on them, and a list of labels in drafting order is not the page.
A model with vision can simply look. The point of this module is that the
extractor already knows when that is true, and says so in every result that
touches such a page, instead of leaving the model to guess from a thin
transcript that the transcript is thin.

:func:`page_advice` returns short, plain statements ("image-only page: view
it"). The tool layer appends the host's own instructions for HOW to view
(:attr:`planlens.tools.ReviewToolkit.vision_hint`).
"""

from __future__ import annotations

from typing import List, Optional

from planlens.document.model import (
    SOURCE_AZURE_DI, SOURCE_OCR, PageContent, PageSummary,
)

#: A table whose cells are mostly empty is a ruled form read as a grid; the
#: grid is real but the reading is not the page.
SPARSE_TABLE_EMPTY_FRACTION = 0.5


def page_advice(summary: PageSummary,
                content: Optional[PageContent] = None) -> List[str]:
    """Plain statements on what the text tools cannot give for this page."""
    out: List[str] = []
    ev = summary.evidence or {}
    kind = summary.kind

    if kind == "scanned":
        if ev.get("needs_ocr"):
            out.append("image-only page with no text layer: the text tools "
                       "cannot read it — view it; OCR or Azure text can be "
                       "attached but is optional")
        else:
            out.append("image-only page whose text was read optically "
                       "(confidence per line): view it to confirm anything "
                       "that matters")
    elif kind == "drawing_sheet":
        out.append("drawing sheet: the text lines are its labels in drafting "
                   "order, not a reading of the sheet — view the sheet (or a "
                   "region of it), and use drawing-geometry tools for "
                   "measurements")
    elif kind == "figure":
        out.append("figure or form page: text lines are labels and cell "
                   "contents in drafting order — view the page to read it as "
                   "laid out")
    elif kind == "mixed" and ev.get("image_coverage", 0) >= 0.2:
        out.append("page carries images: their content is not in the text — "
                   "view the page for them")

    if ev.get("unmapped_chars"):
        out.append(f"{ev['unmapped_chars']} characters could not be decoded "
                   f"from the font (shown as U+FFFD) — view the page for "
                   f"those")

    if content is not None:
        for t in content.tables:
            if (t.n_cells >= 12
                    and t.empty_fraction >= SPARSE_TABLE_EMPTY_FRACTION):
                out.append(f"table {t.id} is a sparse grid "
                           f"({t.n_empty_cells}/{t.n_cells} cells empty as "
                           f"detected): a ruled form read as cells — view "
                           f"the page for the layout")
        low = content.stats.get("n_low_confidence_words")
        if low and any(s in (SOURCE_AZURE_DI, SOURCE_OCR)
                       for s in content.text_sources):
            out.append(f"{low} words read optically at low confidence — "
                       f"view the page before quoting them")
        aimed = [m for m in content.markups if m.points_at is not None]
        if aimed:
            out.append(f"{len(aimed)} markup(s) point at a spot on the page "
                       f"(points_at) — view that region to see what is being "
                       f"commented on")
    return out
