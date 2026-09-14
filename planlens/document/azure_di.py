"""Azure Document Intelligence as a planlens text source — optional, never required.

Azure DI's ``prebuilt-layout`` model reads text optically (so it works on
scanned pages), and reports what the PDF text layer cannot: paragraph roles
(title, sectionHeading, pageHeader, pageFooter, pageNumber, footnote), tables
with explicit header cells, checkbox states and per-word confidence. It costs
money per page and is a separate service, so planlens never calls it and never
imports an Azure package. The caller runs the analysis — with the Funhouse SDK,
the Azure SDK or a REST call — and hands the result here::

    from planlens.document import open_document
    from planlens.document.azure_di import AzureLayout, pages_needing_ocr

    doc = open_document("submittal.pdf")
    pages = pages_needing_ocr(doc)                 # only pay for what needs it
    layout = AzureLayout.analyze(fh_doc.analyze_layout, pdf_bytes, pages=pages)
    doc = open_document("submittal.pdf", text_source=layout)

Accepted input: the ``analyzeResult`` object (camelCase, as REST and the Funhouse
SDK's ``as_dict()`` return it) or the same with snake_case keys, with polygons
as flat ``[x1, y1, ...]`` lists, ``[[x, y], ...]`` pairs or ``[{"x":, "y":}]``
points.

**Coordinates.** Azure reports each page's width, height and unit (inches for
PDF, pixels for images). Whether its frame is the displayed page or the
unrotated one is decided PER PAGE by matching sizes against the PDF page — a
36x24 in result on a sheet stored portrait with /Rotate 90 is the displayed
frame, 24x36 is the unrotated one — and a page where neither matches is scaled
to fit with a warning. Output is in the planlens displayed frame like every
other text source.

**Page numbers.** When only some pages were analysed, Azure may number them as
the document does or from 1. :class:`AzureLayout` is told which pages were sent
and reconciles the two, refusing a result it cannot place rather than putting
text on the wrong page.
"""

from __future__ import annotations

import bisect
import math
import re
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from planlens.document.frame import bbox_union, to_display_point
from planlens.document.model import (
    SOURCE_AZURE_DI, Table, TextBlock, TextLine,
)
from planlens.document.pdf_text import _rotation
from planlens.document.tables import _drop_empty_columns

Point = Tuple[float, float]

#: Words below this confidence are counted in the page stats.
LOW_CONFIDENCE = 0.8

#: Relative size mismatch tolerated when deciding a page's frame.
_SIZE_TOL = 0.02


def _snake(name: str) -> str:
    return re.sub(r"(?<!^)(?=[A-Z])", "_", name).lower()


def _k(d: Any, camel: str, default: Any = None) -> Any:
    """``d[camel]``, else ``d[snake_case(camel)]``, else ``default``."""
    if not isinstance(d, dict):
        return default
    if camel in d:
        return d[camel]
    return d.get(_snake(camel), default)


def _points(polygon: Any) -> List[Point]:
    if not polygon:
        return []
    first = polygon[0]
    if isinstance(first, dict):
        return [(float(p["x"]), float(p["y"])) for p in polygon]
    if isinstance(first, (list, tuple)):
        return [(float(p[0]), float(p[1])) for p in polygon]
    vals = [float(v) for v in polygon]
    return list(zip(vals[0::2], vals[1::2]))


def _spans(item: Any) -> List[Tuple[int, int]]:
    spans = _k(item, "spans")
    if spans is None:
        one = _k(item, "span")
        spans = [one] if one else []
    out = []
    for s in spans:
        off = int(_k(s, "offset", 0))
        out.append((off, off + int(_k(s, "length", 0))))
    return out


def pages_to_azure_range(pages: Iterable[int]) -> str:
    """0-based planlens pages -> Azure's 1-based page-range string ("1-3,5")."""
    nums = sorted({int(p) + 1 for p in pages})
    if not nums:
        raise ValueError("no pages given")
    parts, start, prev = [], nums[0], nums[0]
    for n in nums[1:] + [None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        parts.append(str(start) if start == prev else f"{start}-{prev}")
        if n is not None:
            start = prev = n
    return ",".join(parts)


def pages_needing_ocr(doc) -> List[int]:
    """Pages the page map flags as image-only with no text layer."""
    return [s.page for s in doc.page_map() if s.evidence.get("needs_ocr")]


class _PageFrame:
    """Maps one Azure page's coordinates into the planlens displayed frame."""

    def __init__(self, di_page: Dict[str, Any], page):
        self.warnings: List[str] = []
        w = float(_k(di_page, "width") or 0.0)
        h = float(_k(di_page, "height") or 0.0)
        unit = (_k(di_page, "unit") or "inch").lower()
        dw, dh = float(page.rect.width), float(page.rect.height)
        self.page = page
        if w <= 0 or h <= 0:
            raise ValueError("Azure page has no width/height")

        def close(a: float, b: float) -> bool:
            return abs(a - b) <= _SIZE_TOL * max(abs(a), abs(b), 1e-9)

        if unit == "inch":
            as_disp = close(w * 72.0, dw) and close(h * 72.0, dh)
            as_unrot = close(w * 72.0, dh) and close(h * 72.0, dw)
        else:
            # Pixels: only the aspect ratio can be matched.
            as_disp = close(w / h, dw / dh)
            as_unrot = close(w / h, dh / dw)
        if as_disp or not as_unrot:
            self.mode = "displayed"
            self.sx, self.sy = dw / w, dh / h
            if not as_disp:
                self.warnings.append(
                    f"Azure page size {w:g}x{h:g} {unit} matches neither the "
                    f"displayed ({dw / 72:.2f}x{dh / 72:.2f} in) nor the "
                    f"unrotated page; scaled to fit the displayed page")
        else:
            self.mode = "unrotated"
            self.sx, self.sy = dh / w, dw / h

    def point(self, x: float, y: float) -> Point:
        px, py = x * self.sx, y * self.sy
        if self.mode == "displayed":
            return (px, py)
        return to_display_point(self.page, px, py)

    def polygon(self, polygon: Any) -> List[Point]:
        return [self.point(x, y) for x, y in _points(polygon)]


def _bbox(pts: Sequence[Point]):
    if not pts:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _direction(pts: Sequence[Point]) -> Optional[float]:
    """Reading direction from a quad's first edge (top-left -> top-right)."""
    if len(pts) < 2:
        return None
    dx, dy = pts[1][0] - pts[0][0], pts[1][1] - pts[0][1]
    n = math.hypot(dx, dy)
    if n == 0:
        return None
    return _rotation((dx / n, dy / n))


class AzureLayout:
    """An Azure Document Intelligence layout result, usable as ``text_source``."""

    name = SOURCE_AZURE_DI

    def __init__(self, result: Dict[str, Any],
                 analyzed_pages: Optional[Sequence[int]] = None):
        if not isinstance(result, dict):
            raise TypeError("pass the analysis result as a dict "
                            "(e.g. poller.result().as_dict())")
        r = _k(result, "analyzeResult", result)
        self.model_id = _k(r, "modelId")
        self.api_version = _k(r, "apiVersion")
        di_pages = _k(r, "pages") or []
        numbers = [int(_k(p, "pageNumber")) for p in di_pages]
        index_of = self._reconcile(numbers, analyzed_pages)
        self._pages: Dict[int, Dict[str, Any]] = {}
        self._number_to_index: Dict[int, int] = {}
        for p, n in zip(di_pages, numbers):
            self._pages[index_of[n]] = p
            self._number_to_index[n] = index_of[n]
        self._paragraphs = _k(r, "paragraphs") or []
        self._tables = _k(r, "tables") or []

    @staticmethod
    def _reconcile(numbers: List[int],
                   analyzed: Optional[Sequence[int]]) -> Dict[int, int]:
        if analyzed is None:
            return {n: n - 1 for n in numbers}
        wanted = sorted({int(p) for p in analyzed})
        if set(numbers) <= {p + 1 for p in wanted}:
            return {n: n - 1 for n in numbers}           # document numbering
        if sorted(numbers) == list(range(1, len(numbers) + 1)) and \
                len(numbers) <= len(wanted):
            return {n: wanted[n - 1] for n in numbers}   # renumbered from 1
        raise ValueError(
            f"cannot place Azure pages {numbers} onto the analysed pages "
            f"{wanted}: neither the document's numbering nor 1..n")

    @classmethod
    def analyze(cls, analyze_fn: Callable[..., Any], content: Any,
                pages: Optional[Sequence[int]] = None,
                **kwargs: Any) -> "AzureLayout":
        """Run ``analyze_fn(content, page_range=..., **kwargs)`` and wrap it.

        ``analyze_fn`` is whatever the caller uses to reach Azure — for example
        the Funhouse SDK's ``fh_doc.analyze_layout``. ``pages`` are 0-based
        planlens pages; ``None`` analyses the whole document.
        """
        page_range = pages_to_azure_range(pages) if pages is not None else None
        result = analyze_fn(content, page_range=page_range, **kwargs)
        if result is None:
            raise RuntimeError(
                "the Azure analysis returned nothing (the Funhouse SDK returns "
                "None when its budget check refuses the call)")
        if hasattr(result, "as_dict"):
            result = result.as_dict()
        return cls(result, analyzed_pages=pages)

    # -- text-source protocol ---------------------------------------------------
    @property
    def pages(self) -> List[int]:
        return sorted(self._pages)

    def covers(self, index: int) -> bool:
        return index in self._pages

    def extract(self, page, index: int, words: bool = False):
        """(lines, blocks, tables, stats, warnings) for one covered page."""
        di_page = self._pages[index]
        frame = _PageFrame(di_page, page)
        warnings = list(frame.warnings)

        # Lines, with their words found through the content offsets.
        line_items = _k(di_page, "lines") or []
        lines: List[TextLine] = []
        starts: List[int] = []
        ranges: List[Tuple[int, int, int]] = []     # (start, end, line index)
        for li in line_items:
            pts = frame.polygon(_k(li, "polygon"))
            ln = TextLine(
                id=f"p{index}.a{len(lines)}", page=index,
                text=(_k(li, "content") or "").strip(), bbox=_bbox(pts),
                rotation=_direction(pts), source=SOURCE_AZURE_DI)
            if not ln.text or ln.bbox is None:
                continue
            for a, b in _spans(li):
                ranges.append((a, b, len(lines)))
            lines.append(ln)
        ranges.sort()
        starts = [a for a, _b, _i in ranges]

        def owner(offset: int) -> Optional[int]:
            k = bisect.bisect_right(starts, offset) - 1
            if k >= 0 and ranges[k][0] <= offset < ranges[k][1]:
                return ranges[k][2]
            return None

        n_words = 0
        n_low = 0
        min_conf = None
        line_conf: Dict[int, float] = {}
        for w in _k(di_page, "words") or []:
            n_words += 1
            conf = _k(w, "confidence")
            if conf is not None:
                conf = float(conf)
                min_conf = conf if min_conf is None else min(min_conf, conf)
                if conf < LOW_CONFIDENCE:
                    n_low += 1
            sp = _spans(w)
            i = owner(sp[0][0]) if sp else None
            if i is None:
                continue
            if conf is not None:
                line_conf[i] = min(line_conf.get(i, 1.0), conf)
            if words:
                wb = _bbox(frame.polygon(_k(w, "polygon")))
                if wb is not None:
                    if lines[i].words is None:
                        lines[i].words = []
                    lines[i].words.append(((_k(w, "content") or ""), wb))
        for i, conf in line_conf.items():
            # A line is only as sure as its least certain word.
            lines[i].confidence = conf

        for sm in _k(di_page, "selectionMarks") or []:
            pts = frame.polygon(_k(sm, "polygon"))
            if not pts:
                continue
            state = (_k(sm, "state") or "").lower()
            lines.append(TextLine(
                id=f"p{index}.a{len(lines)}", page=index,
                text="[x]" if state == "selected" else "[ ]",
                bbox=_bbox(pts), rotation=0.0, source=SOURCE_AZURE_DI,
                confidence=float(_k(sm, "confidence", 1.0) or 1.0)))

        # Paragraphs -> blocks (only the part on this page).
        line_starts = sorted((a, i) for a, _b, i in ranges)
        blocks: List[TextBlock] = []
        for para in self._paragraphs:
            regions = [reg for reg in (_k(para, "boundingRegions") or [])
                       if self._number_to_index.get(
                           int(_k(reg, "pageNumber", 0))) == index]
            if not regions:
                continue
            bbox = bbox_union([_bbox(frame.polygon(_k(reg, "polygon")))
                               for reg in regions])
            ids = []
            for a, b in _spans(para):
                lo = bisect.bisect_left(line_starts, (a, -1))
                for s, i in line_starts[lo:]:
                    if s >= b:
                        break
                    ids.append(lines[i].id)
            block = TextBlock(
                id=f"p{index}.ab{len(blocks)}", page=index, bbox=bbox,
                line_ids=list(dict.fromkeys(ids)),
                text=(_k(para, "content") or "").strip(),
                role=_k(para, "role"), source=SOURCE_AZURE_DI)
            for lid in block.line_ids:
                for ln in lines:
                    if ln.id == lid:
                        ln.block = block.id
            blocks.append(block)

        tables = self._page_tables(index, frame)

        stats: Dict[str, Any] = {
            "n_text_chars": sum(len(ln.text) for ln in lines
                                if ln.text not in ("[x]", "[ ]")),
            "n_lines": len(lines),
            "n_words": n_words,
            "azure_frame": frame.mode,
        }
        if min_conf is not None:
            stats["min_word_confidence"] = round(min_conf, 3)
        if n_low:
            stats["n_low_confidence_words"] = n_low
        angle = _k(di_page, "angle")
        if angle is not None and abs(float(angle)) > 5.0:
            warnings.append(f"Azure reports the page content skewed "
                            f"{float(angle):.1f} deg")
        return lines, blocks, tables, stats, warnings

    def _page_tables(self, index: int, frame: _PageFrame) -> List[Table]:
        out: List[Table] = []
        for t in self._tables:
            regions = _k(t, "boundingRegions") or []
            placed = [self._number_to_index.get(int(_k(reg, "pageNumber", 0)))
                      for reg in regions]
            if not placed or placed[0] != index:
                continue          # a table lives on the page it starts on
            n_rows = int(_k(t, "rowCount", 0))
            n_cols = int(_k(t, "columnCount", 0))
            grid: List[List[Optional[str]]] = [
                [None] * n_cols for _ in range(n_rows)]
            header_rows = set()
            body_rows = set()
            for c in _k(t, "cells") or []:
                r0 = int(_k(c, "rowIndex", 0))
                c0 = int(_k(c, "columnIndex", 0))
                if r0 >= n_rows or c0 >= n_cols:
                    continue
                grid[r0][c0] = (_k(c, "content") or "").strip()
                if _k(c, "kind") == "columnHeader":
                    header_rows.add(r0)
                else:
                    body_rows.add(r0)
            notes = ["header cells marked by Azure"] if 0 in header_rows else []
            header = None
            if 0 in header_rows and 0 not in body_rows:
                header, grid = grid[0], grid[1:]
                if len(header_rows) > 1:
                    notes.append(f"{len(header_rows)} header rows; only the "
                                 f"first is returned as header")
            later = sorted({p for p in placed[1:] if p is not None and p != index})
            if later:
                notes.append(f"table continues on page(s) {later}")
            caption = _k(t, "caption")
            if caption:
                notes.append(f"caption: {(_k(caption, 'content') or '').strip()}")
            grid, dropped = _drop_empty_columns(grid, header)
            if dropped:
                notes.append(f"{len(dropped)} empty column(s) omitted")
                if header is not None:
                    header = [v for i, v in enumerate(header) if i not in dropped]
            bbox = bbox_union([_bbox(frame.polygon(_k(reg, "polygon")))
                               for reg, p in zip(regions, placed) if p == index])
            out.append(Table(
                id=f"p{index}.atbl{len(out)}", page=index, bbox=bbox,
                n_rows=len(grid), n_cols=max((len(r) for r in grid), default=0),
                rows=grid, header=header, source=SOURCE_AZURE_DI, notes=notes))
        return out
