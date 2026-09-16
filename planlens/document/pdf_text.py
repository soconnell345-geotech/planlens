"""The PDF text layer, read with PyMuPDF, in the displayed frame.

This is the default text source. It returns lines (with true reading direction,
font, size, colour and exact boxes) and the blocks PyMuPDF groups them into.
Order is the PDF's content-stream order, which for word-processor output is
reading order and for CAD plots is drafting order.

Text is read through a DISPLAY LIST of the page, for two reasons. MuPDF runs a
display list in the displayed orientation, so boxes and reading directions come
out in the displayed frame with no conversion (verified 2026-09-13 on /Rotate
0, 90, 180 and 270 and on an offset crop box: boxes identical to rotating
ordinary ``get_text`` output with ``page.rotation_matrix``, and rendered ink
inside every box). And a display list can leave annotations out — see
:func:`_textpage`.

What it cannot do, and says so: a page with no text layer (scanned, or plotted
with stroked SHX lettering) returns no lines; characters in a font without a
Unicode map come back as U+FFFD and are counted in ``stats`` so a caller knows
to OCR the page instead of trusting the string.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import math

from planlens.document.frame import bbox_union
from planlens.document.model import TextBlock, TextLine

_UNMAPPED = "�"

#: Fraction of a page's characters that may come back unmapped (U+FFFD)
#: before the text layer stops being worth reading at all. Above it,
#: ``PageSummary.text_reliable`` is False and the page is offered to OCR.
#:
#: MEASURED, not chosen, over five real documents (202, 455, 97, 94 and 260
#: pages, 1,108 pages in all). 52 of those pages carry any unmapped character
#: whatever, and they fall into two populations that do not come near each
#: other:
#:
#: * **a stray glyph** — 30 pages at 0.0007 to 0.0064: a bullet, a degree
#:   sign, a logo character in a heading. The prose around it is perfect and
#:   nothing should be said about the page.
#: * **a broken encoding** — 22 pages at 0.248, 0.436-0.469 and 0.968-0.994:
#:   analysis-program printouts and a laboratory checklist whose font carries
#:   no usable map. At 0.44 every space is U+FFFD; at 0.99 the whole page is.
#:
#: The floor sits in the empty 38x span between them: fifteen times the
#: worst benign page, two and a half times below the mildest broken one.
MAX_UNMAPPED_FRACTION = 0.10


def _text_flags():
    import fitz
    # No TEXT_PRESERVE_IMAGES: image blocks are not text and cost memory.
    return (fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
            | fitz.TEXT_MEDIABOX_CLIP)


def _hex(color: Any) -> str:
    try:
        return "#%06x" % (int(color) & 0xFFFFFF)
    except (TypeError, ValueError):
        return "#000000"


#: Directions within this many degrees of 0/90/180/270 are reported as exactly
#: that. CAD plots write near-cardinal text a few tenths of a degree off (a
#: real sheet had 32 lines at 359.9 and 4 at 0.3); the tenths carry no meaning
#: and make identical labels look different.
CARDINAL_SNAP_DEG = 0.5


def _rotation(direction) -> float:
    """A displayed-frame reading direction (y down) -> degrees CCW as seen."""
    ang = math.degrees(math.atan2(-float(direction[1]),
                                  float(direction[0]))) % 360.0
    nearest = round(ang / 90.0) * 90.0
    if abs(ang - nearest) <= CARDINAL_SNAP_DEG:
        ang = nearest
    ang = round(ang % 360.0, 1)
    return 0.0 if ang >= 359.95 else ang


def _textpage(page, flags):
    """A text page from the page's display list, in the displayed frame.

    It is built from the page content ONLY. PyMuPDF's ordinary
    ``get_text`` also reads the text drawn by annotation appearance streams, so
    a reviewer's FreeText comment would come back as if it were part of the
    drawing — verified on a real submittal, where deleting a Bluebeam callout
    removed its words from ``get_text()``. Review markups reach the reader
    through :mod:`planlens.document.annotations` instead, attributed.
    """
    import fitz
    stext = page.get_displaylist(annots=False).get_textpage(flags)
    tp = stext if isinstance(stext, fitz.TextPage) else fitz.TextPage(stext)
    # PyMuPDF checks the text page belongs to the page it is used with; a text
    # page made from a display list does not record one.
    tp.parent = page
    return tp


def extract_text(page, page_index: int, words: bool = False
                 ) -> Tuple[List[TextLine], List[TextBlock], Dict[str, Any]]:
    """Lines, blocks and text statistics for one ``fitz.Page``.

    ``words=True`` also attaches each line's words with their own boxes — useful
    for pinpointing a value inside a long line, and roughly doubles the payload.
    Text drawn by annotations is excluded; see :func:`_textpage`.
    """
    flags = _text_flags()
    tp = _textpage(page, flags)
    raw = page.get_text("dict", textpage=tp)

    lines: List[TextLine] = []
    blocks: List[TextBlock] = []
    key_to_line: Dict[Tuple[int, int], TextLine] = {}
    n_chars = 0
    n_unmapped = 0

    for block in raw.get("blocks", []):
        if block.get("type") != 0:
            continue
        bnum = block.get("number", len(blocks))
        block_id = f"p{page_index}.b{len(blocks)}"
        block_lines: List[TextLine] = []
        for li, line in enumerate(block.get("lines", [])):
            spans = line.get("spans", [])
            text = "".join(s.get("text", "") for s in spans).strip()
            if not text:
                continue
            n_chars += len(text)
            n_unmapped += text.count(_UNMAPPED)
            # Font attributes from the span carrying the most characters.
            dom = max(spans, key=lambda s: len(s.get("text", "").strip()))
            sflags = int(dom.get("flags", 0))
            font = dom.get("font") or None
            bold = bool(sflags & 16) or ("bold" in (font or "").lower())
            italic = bool(sflags & 2) or ("italic" in (font or "").lower())
            tl = TextLine(
                id=f"p{page_index}.t{len(lines)}",
                page=page_index,
                text=text,
                bbox=tuple(float(v) for v in line["bbox"]),
                rotation=_rotation(line.get("dir", (1, 0))),
                size=float(dom.get("size", 0.0)) or None,
                font=font,
                bold=bold,
                italic=italic,
                color=_hex(dom.get("color", 0)),
                block=block_id,
            )
            lines.append(tl)
            block_lines.append(tl)
            key_to_line[(bnum, li)] = tl
        if block_lines:
            blocks.append(TextBlock(
                id=block_id,
                page=page_index,
                bbox=bbox_union([ln.bbox for ln in block_lines]),
                line_ids=[ln.id for ln in block_lines],
                text="\n".join(ln.text for ln in block_lines),
            ))

    if words and lines:
        # Same text page -> same block/line numbering as the "dict" pass.
        for x0, y0, x1, y1, w, bno, lno, _wno in page.get_text(
                "words", textpage=tp):
            tl = key_to_line.get((bno, lno))
            if tl is None:
                continue
            if tl.words is None:
                tl.words = []
            tl.words.append((w, (float(x0), float(y0), float(x1),
                                 float(y1))))

    stats = {"n_text_chars": n_chars, "n_lines": len(lines)}
    if n_unmapped:
        stats["n_unmapped_chars"] = n_unmapped
    return lines, blocks, stats
