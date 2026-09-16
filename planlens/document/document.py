"""A whole document: page map, per-page content, search, markups, text.

:class:`Document` is the entry point of :mod:`planlens.document`. It opens a PDF
once and extracts lazily — nothing is read until a page is asked for, and each
page is read once. The cheap per-page measurements behind :meth:`Document.page_map`
cost about 5 s on a real 260-page submittal; table detection, the expensive
step, runs only inside :meth:`Document.page`.

Page numbers are 0-based throughout (``page_map`` also reports each page's own
label, e.g. a Bluebeam sheet name, where the PDF has one).

A ``text_source`` can replace the PDF text layer for the pages it covers — for
example :class:`planlens.document.azure_di.AzureLayout` built from an Azure
Document Intelligence result, for scanned pages or for its paragraph roles and
tables. Annotations (markups and hidden CAD text) always come from the PDF.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

from planlens.document.annotations import (
    attach_appearance_text, drop_cad_text_already_in_layer,
    extract_annotations,
)
from planlens.document.classify import classify_page
from planlens.document.frame import bbox_union
from planlens.document.model import (
    SOURCE_AZURE_DI, SOURCE_CAD_HIDDEN, SOURCE_OCR, SOURCE_PDF_TEXT, Markup,
    PageContent, PageSummary,
    TextBlock, TextLine,
)
from planlens.document.pdf_text import extract_text
from planlens.document.scale import page_viewports
from planlens.document.structure import (
    BAND_MAX_CHARS, band_texts, content_hash, divider_title, printed_numbers,
    segments,
)
from planlens.document.tables import extract_tables

PageSpec = Union[None, int, Sequence[int], range, str]


def parse_pages(spec: PageSpec, n_pages: int) -> List[int]:
    """0-based page list from ``None`` (all), an int, a sequence, or ``"0-4,9"``.

    Out-of-range pages raise ``IndexError`` naming the valid range, rather than
    being silently dropped.
    """
    if spec is None:
        return list(range(n_pages))
    if isinstance(spec, int):
        out = [spec]
    elif isinstance(spec, str):
        out = []
        for part in spec.replace(" ", "").split(","):
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                out.extend(range(int(a), int(b) + 1))
            else:
                out.append(int(part))
    else:
        out = [int(p) for p in spec]
    bad = [p for p in out if p < 0 or p >= n_pages]
    if bad:
        raise IndexError(
            f"page(s) {bad} out of range: this document has {n_pages} pages, "
            f"numbered 0-{n_pages - 1}")
    seen = set()
    return [p for p in out if not (p in seen or seen.add(p))]


def _image_coverage(page) -> float:
    """Fraction of the page covered by placed images (overlaps may overcount;
    capped at 1)."""
    try:
        infos = page.get_image_info()
    except Exception:  # pragma: no cover - defensive
        return 0.0
    if not infos:
        return 0.0
    import fitz
    unrot = page.rect * page.derotation_matrix
    area = abs(unrot.width * unrot.height) or 1.0
    covered = 0.0
    for info in infos:
        r = fitz.Rect(info.get("bbox", (0, 0, 0, 0))) & unrot
        if not r.is_empty:
            covered += abs(r.width * r.height)
    return min(1.0, covered / area)


def _drawing_facts(page) -> Tuple[int, int, int, List[str]]:
    """``(n_paths, ruling_h, ruling_v, layers)`` from ONE pass over the page.

    The page map used to call ``get_cdrawings()`` twice — once to count paths,
    once to count rules — so the layer names each path already carries cost
    nothing to collect here: they come out of the same call, and folding the
    second call away pays for it several times over.

    ``layers`` are the distinct optional-content groups (a PDF's layers) the
    page's line-work sits in, in first-seen order. PyMuPDF reports the empty
    string for a path in no group, which is not a layer name and is dropped.
    """
    layers: Dict[str, None] = {}
    drawings = page.get_cdrawings()
    h, v = _ruling_lines(drawings)
    for d in drawings:
        name = d.get("layer")
        if name:
            layers.setdefault(str(name), None)
    return len(drawings), h, v, list(layers)


def _ruling_lines(drawings) -> Tuple[int, int]:
    """Counts of long horizontal and vertical drawn lines (rules)."""
    h = v = 0
    for d in drawings:
        for it in d.get("items", ()):
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                if abs(y1 - y0) < 0.5 and abs(x1 - x0) > 20:
                    h += 1
                elif abs(x1 - x0) < 0.5 and abs(y1 - y0) > 20:
                    v += 1
            elif it[0] == "re":
                r = it[1]
                if r[2] > 20 and r[3] < 2:
                    h += 1
                elif r[3] > 20 and r[2] < 2:
                    v += 1
    return h, v


def _heading(lines: List[TextLine],
             blocks: Sequence[TextBlock] = ()) -> Optional[str]:
    """A title/section-heading block when the text source marks roles, else
    the largest-font line of at least 3 characters (first one on ties)."""
    for b in blocks:
        if b.role in ("title", "sectionHeading") and b.text:
            t = " ".join(b.text.split())
            return t if len(t) <= 100 else t[:97] + "..."
    best = None
    for ln in lines:
        if ln.source != SOURCE_PDF_TEXT or len(ln.text) < 3 or not ln.size:
            continue
        if best is None or ln.size > best.size + 0.1:
            best = ln
    if best is None:
        return None
    return best.text if len(best.text) <= 100 else best.text[:97] + "..."


#: Image formats accepted as a one-page document (converted to PDF on open).
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif",
                    ".webp", ".pnm", ".pgm", ".ppm")

#: Rendering never returns more pixels than this (a 300 dpi D-size sheet is
#: 70 megapixels — enough to take a notebook driver down, and far more than a
#: vision model uses); the dpi is lowered to fit.
DEFAULT_MAX_PIXELS = 4_000_000

#: Full-page renders target this long side in pixels when no dpi is given.
DEFAULT_PAGE_LONG_SIDE_PX = 2000


def _is_pdf(head: bytes) -> bool:
    return head.lstrip()[:5] == b"%PDF-"


def _open_any(filepath: Optional[str], content: Optional[bytes]):
    """Open a PDF, or an image as a one-page PDF. Returns (doc, kind)."""
    import fitz
    if content is not None:
        if _is_pdf(content[:1024]):
            return fitz.open(stream=content, filetype="pdf"), "pdf"
        img = fitz.open(stream=content)          # format sniffed by MuPDF
    else:
        with open(filepath, "rb") as fh:
            head = fh.read(1024)
        if _is_pdf(head):
            return fitz.open(filepath), "pdf"
        img = fitz.open(filepath)
    if img.page_count < 1:
        img.close()
        raise ValueError("not a PDF and not a readable image")
    pdf = fitz.open("pdf", img.convert_to_pdf())
    img.close()
    return pdf, "image"


class Document:
    """A PDF (or an image, as a one-page document) opened for review.

    Use as a context manager or call :meth:`close`. ``source_kind`` is
    ``"pdf"`` or ``"image"``; an image becomes a one-page PDF on open, so every
    page-level operation — the page map, rendering, the displayed frame —
    applies to it the same way, and a scan photographed with a phone is
    reviewed like a scanned page.
    """

    def __init__(self, filepath: Optional[str] = None,
                 content: Optional[bytes] = None,
                 text_source: Any = None, name: Optional[str] = None):
        if filepath is None and content is None:
            raise ValueError("pass filepath or content")
        self._doc, self.source_kind = _open_any(filepath, content)
        self.source = name or (str(filepath) if filepath else "<bytes>")
        self.text_source = text_source
        self._text: Dict[int, tuple] = {}
        self._annots: Dict[int, tuple] = {}
        self._summaries: Dict[int, PageSummary] = {}
        self._pages: Dict[tuple, PageContent] = {}
        self._hashes: Dict[str, int] = {}
        self._segments: Optional[List[Dict[str, Any]]] = None

    # -- lifecycle ---------------------------------------------------------
    def close(self) -> None:
        self._doc.close()

    def __enter__(self) -> "Document":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- document-level facts ---------------------------------------------
    @property
    def n_pages(self) -> int:
        return self._doc.page_count

    @property
    def metadata(self) -> Dict[str, str]:
        return {k: v for k, v in (self._doc.metadata or {}).items()
                if v and k != "format"}

    def toc(self) -> List[Dict[str, Any]]:
        """The PDF outline (bookmarks): level, title, 0-based page."""
        return [{"level": lvl, "title": title, "page": page - 1}
                for lvl, title, page, *_ in self._doc.get_toc(simple=True)
                if page >= 1]

    def label(self, index: int) -> Optional[str]:
        try:
            lab = self._doc[index].get_label()
        except Exception:  # pragma: no cover - defensive
            return None
        return lab or None

    # -- cached raw extraction ----------------------------------------------
    def _page_text(self, index: int, words: bool = False):
        key = (index, words)
        if key not in self._text:
            page = self._doc[index]
            src = self.text_source
            if src is not None and src.covers(index):
                lines, blocks, tables, stats, warns = src.extract(
                    page, index, words=words)
                self._text[key] = (lines, blocks, tables, stats, warns,
                                   src.name)
            else:
                lines, blocks, stats = extract_text(page, index, words=words)
                self._text[key] = (lines, blocks, None, stats, [],
                                   SOURCE_PDF_TEXT)
        return self._text[key]

    def _page_annots(self, index: int):
        if index not in self._annots:
            page = self._doc[index]
            markups, cad = extract_annotations(page, index)
            attach_appearance_text(page, index, markups)
            self._annots[index] = (markups, cad)
        return self._annots[index]

    # -- page map -----------------------------------------------------------
    def summary(self, index: int) -> PageSummary:
        if index in self._summaries:
            return self._summaries[index]
        page = self._doc[index]
        doc_for_scale = self._doc
        lines, blocks, _tables, stats, _w, src_name = self._page_text(index)
        markups, cad = self._page_annots(index)
        cad, _ = drop_cad_text_already_in_layer(cad, lines)
        n_paths, ruling_h, ruling_v, layers = _drawing_facts(page)
        coverage = _image_coverage(page)
        n_chars = int(stats.get("n_text_chars", 0))
        n_cad_chars = sum(len(c.text) for c in cad)
        kind, evidence = classify_page(
            page.rect.width, page.rect.height, n_chars, n_cad_chars, n_paths,
            coverage, text_is_optical=src_name in (SOURCE_AZURE_DI, SOURCE_OCR),
            ruling_h=ruling_h, ruling_v=ruling_v)
        if stats.get("n_unmapped_chars"):
            evidence["unmapped_chars"] = stats["n_unmapped_chars"]
        if src_name != SOURCE_PDF_TEXT:
            evidence["text_source"] = src_name
        width, height = page.rect.width, page.rect.height
        n_words = sum(len(ln.text.split()) for ln in lines)
        area_in2 = (width / 72.0) * (height / 72.0) or 1.0
        heading = _heading(lines, blocks)
        top, bottom = band_texts(lines, height)
        numbers = printed_numbers(top + bottom)
        scales: List[str] = []
        if kind == "drawing_sheet":
            from planlens.pdf.scale import parse_scale_annotations
            seen = set()
            for c in parse_scale_annotations([{"text": ln.text}
                                              for ln in lines]):
                prov = " ".join(str(c.get("provenance", "")).split())[:40]
                if prov and prov not in seen:
                    seen.add(prov)
                    scales.append(prov)
            scales = scales[:4]
        # The calibration the FILE stores, not a reading of a printed note.
        # Measured on the real 260-page submittal: 0.02 s for every page, so it
        # belongs on the cheap page map rather than behind a full page read.
        viewports, vp_warnings = page_viewports(doc_for_scale, page, index)
        if vp_warnings:
            evidence["viewport_warnings"] = vp_warnings[:3]
        s = PageSummary(
            page=index, width=width, height=height,
            rotation=int(page.rotation), label=self.label(index), kind=kind,
            evidence=evidence, heading=heading,
            n_text_chars=n_chars, n_markups=len(markups), n_cad_text=len(cad),
            n_words=n_words, text_density=n_words / area_in2,
            n_images=len(page.get_image_info()) if coverage else 0,
            ruling_h=ruling_h, ruling_v=ruling_v, layers=layers,
            rotated_text_fraction=(
                sum(1 for ln in lines if ln.rotation) / len(lines)
                if lines else 0.0),
            header=(" | ".join(top)[:BAND_MAX_CHARS] or None),
            footer=(" | ".join(bottom)[:BAND_MAX_CHARS] or None),
            printed_page=numbers.get("printed_page"),
            printed_of=numbers.get("printed_of"),
            sheet=numbers.get("sheet"),
            scales=scales,
            viewports=viewports,
            divider_title=divider_title(
                heading, n_words, [ln.text for ln in lines[:3]]),
        )
        if kind != "blank" and (n_words >= 15 or n_paths >= 30):
            key = content_hash(kind, lines, n_paths)
            first = self._hashes.setdefault(key, index)
            if first != index:
                s.duplicate_of = first
        self._summaries[index] = s
        self._segments = None
        return s

    def page_map(self, pages: PageSpec = None) -> List[PageSummary]:
        """One :class:`PageSummary` per page — kind, size, heading, counts.

        Reading the whole document also assigns each page its ``segment``
        (see :meth:`segments`)."""
        wanted = parse_pages(pages, self.n_pages)
        out = [self.summary(i) for i in wanted]
        if len(wanted) == self.n_pages:
            self.segments()
        return out

    def segments(self) -> List[Dict[str, Any]]:
        """The document's constituent documents, from running headers /
        footers, printed numbering, dividers, page size and drawing sheets
        (:func:`planlens.document.structure.segments`). Reads every page."""
        if self._segments is None:
            summaries = [self.summary(i) for i in range(self.n_pages)]
            self._segments = segments(summaries)
        return self._segments

    def render_thumbnails(self, pages: PageSpec = None, columns: int = 6,
                          thumb_px: int = 140, per_sheet: int = 48
                          ) -> List[Tuple[bytes, Dict[str, Any]]]:
        """Contact sheets: every page as a small thumbnail with its number
        and kind beneath, laid out in a grid like a viewer's page panel — so a
        model can take in a long document at a glance and pick out the plan,
        the logs, the tables. Pages with review markups get a red frame.
        Returns one ``(png, info)`` per sheet of ``per_sheet`` pages."""
        import fitz
        wanted = parse_pages(pages, self.n_pages)
        columns = max(1, min(int(columns), 12))
        thumb = max(60, min(int(thumb_px), 400))
        gap, label_h = 10, 14
        cell_w, cell_h = thumb + gap, thumb + gap + label_h
        out: List[Tuple[bytes, Dict[str, Any]]] = []
        for start in range(0, len(wanted), per_sheet):
            chunk = wanted[start:start + per_sheet]
            rows = (len(chunk) + columns - 1) // columns
            sheet = fitz.open()
            canvas = sheet.new_page(width=columns * cell_w + gap,
                                    height=rows * cell_h + gap)
            for n, index in enumerate(chunk):
                col, row = n % columns, n // columns
                x0 = gap + col * cell_w
                y0 = gap + row * cell_h
                src = self._doc[index]
                z = thumb / max(src.rect.width, src.rect.height)
                pix = src.get_pixmap(matrix=fitz.Matrix(z, z), annots=True)
                w, h = pix.width, pix.height
                ox, oy = x0 + (thumb - w) / 2.0, y0 + (thumb - h) / 2.0
                rect = fitz.Rect(ox, oy, ox + w, oy + h)
                canvas.insert_image(rect, pixmap=pix)
                summary = self.summary(index)
                color = (0.85, 0.1, 0.1) if summary.n_markups else (0.6, 0.6, 0.6)
                canvas.draw_rect(rect, color=color,
                                 width=1.5 if summary.n_markups else 0.5)
                label = f"{index} {summary.kind}"
                if summary.label and summary.label != str(index + 1):
                    label += f" ({summary.label[:12]})"
                canvas.insert_text((x0, y0 + thumb + label_h - 3), label,
                                   fontsize=7, color=(0, 0, 0))
            png = canvas.get_pixmap(dpi=72).tobytes("png")
            sheet.close()
            out.append((png, {
                "pages": chunk, "columns": columns, "thumb_px": thumb,
                "width_px": int(columns * cell_w + gap),
                "height_px": int(rows * cell_h + gap),
                "legend": ("each thumbnail is labelled '<page> <kind>'; a red "
                           "frame means the page carries review markups")}))
        return out

    # -- full page content ------------------------------------------------
    def page(self, index: int, words: bool = False,
             tables: bool = True) -> PageContent:
        """Everything on one page: text lines/blocks, tables, markups."""
        (index,) = parse_pages(index, self.n_pages)
        key = (index, words, tables)
        if key in self._pages:
            return self._pages[key]
        page = self._doc[index]
        lines, blocks, src_tables, stats, warns, src_name = self._page_text(
            index, words=words)
        markups, cad = self._page_annots(index)
        cad, n_dup = drop_cad_text_already_in_layer(cad, lines)
        warnings = list(warns)
        sources = [src_name] if lines else []
        if cad:
            sources.append(SOURCE_CAD_HIDDEN)
        stats = dict(stats)
        if cad:
            stats["n_cad_text"] = len(cad)
        if n_dup:
            stats["n_cad_text_duplicates_dropped"] = n_dup
        if stats.get("n_unmapped_chars"):
            warnings.append(
                f"{stats['n_unmapped_chars']} characters use a font with no "
                f"Unicode map and read as U+FFFD — OCR this page for them")
        table_list = []
        if src_tables is not None:
            table_list = list(src_tables)
        elif tables:
            table_list, twarn = extract_tables(page, index)
            warnings.extend(twarn)
        summary = self.summary(index)
        pc = PageContent(
            page=index, width=page.rect.width, height=page.rect.height,
            rotation=int(page.rotation), label=summary.label,
            kind=summary.kind, lines=list(lines) + cad, blocks=list(blocks),
            tables=table_list, markups=list(markups), text_sources=sources,
            stats=stats, warnings=warnings)
        if not pc.lines and summary.evidence.get("needs_ocr"):
            pc.warnings.append(
                "no text layer on this page and it is mostly image — "
                "use an OCR or Azure Document Intelligence text source")
        self._pages[key] = pc
        return pc

    # -- search -------------------------------------------------------------
    def search(self, pattern: str, pages: PageSpec = None, regex: bool = False,
               case_sensitive: bool = False, include_markups: bool = True,
               max_hits: int = 200, context_chars: int = 60) -> Dict[str, Any]:
        """Find text across pages. Matches may span line breaks within a block.

        Each hit names its page (and label), the matched line ids, a snippet and
        the union box of those lines, so the caller can render or open exactly
        that spot. Markup comments, authors and subjects are searched too.
        """
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
        hits: List[Dict[str, Any]] = []
        per_page: Dict[int, int] = {}
        truncated = False
        page_list = parse_pages(pages, self.n_pages)
        for index in page_list:
            lines, blocks, *_ = self._page_text(index)
            markups, cad = self._page_annots(index)
            cad, _ = drop_cad_text_already_in_layer(cad, lines)
            by_id = {ln.id: ln for ln in lines}
            groups: List[List[TextLine]] = [
                [by_id[i] for i in b.line_ids if i in by_id] for b in blocks]
            in_blocks = {i for b in blocks for i in b.line_ids}
            groups.extend([ln] for ln in lines if ln.id not in in_blocks)
            groups.extend([c] for c in cad)
            for group in groups:
                if not group:
                    continue
                joined, spans, pos = [], [], 0
                for ln in group:
                    spans.append((pos, pos + len(ln.text), ln))
                    joined.append(ln.text)
                    pos += len(ln.text) + 1
                text = " ".join(joined)
                for m in rx.finditer(text):
                    if len(hits) >= max_hits:
                        truncated = True
                        break
                    involved = [ln for a, b, ln in spans
                                if a < m.end() and b > m.start()]
                    a = max(0, m.start() - context_chars)
                    b = min(len(text), m.end() + context_chars)
                    hit = {
                        "page": index,
                        "match": m.group(0),
                        "snippet": ("..." if a else "") + text[a:b]
                                   + ("..." if b < len(text) else ""),
                        "line_ids": [ln.id for ln in involved],
                        "bbox": [round(v, 1) for v in
                                 bbox_union([ln.bbox for ln in involved])],
                    }
                    if involved and involved[0].source != SOURCE_PDF_TEXT:
                        hit["source"] = involved[0].source
                    label = self.label(index)
                    if label:
                        hit["label"] = label
                    hits.append(hit)
                    per_page[index] = per_page.get(index, 0) + 1
            if include_markups and not truncated:
                for mk in markups:
                    hay = " | ".join(x for x in (mk.text, mk.author,
                                                 mk.subject) if x)
                    m = rx.search(hay)
                    if not m:
                        continue
                    if len(hits) >= max_hits:
                        truncated = True
                        break
                    hits.append({
                        "page": index, "match": m.group(0),
                        "snippet": mk.text[:2 * context_chars + 40],
                        "markup_id": mk.id, "author": mk.author,
                        "bbox": [round(v, 1) for v in mk.bbox],
                        "source": "pdf_annotation"})
                    per_page[index] = per_page.get(index, 0) + 1
            if truncated:
                break
        return {"pattern": pattern, "n_hits": len(hits),
                "pages_with_hits": per_page, "truncated": truncated,
                "hits": hits}

    # -- reading advice / rendering ------------------------------------------
    def advice(self, index: int, content: bool = True) -> List[str]:
        """What the text tools cannot give for this page (see
        :mod:`planlens.document.advice`). ``content=False`` uses only the cheap
        page summary."""
        from planlens.document.advice import page_advice
        (index,) = parse_pages(index, self.n_pages)
        return page_advice(self.summary(index),
                           self.page(index) if content else None)

    def render(self, index: int, bbox: Optional[Sequence[float]] = None,
               dpi: Optional[float] = None, pad_frac: float = 0.1,
               marks: Optional[Sequence[Sequence[Any]]] = None,
               max_pixels: int = DEFAULT_MAX_PIXELS) -> Tuple[bytes, Dict[str, Any]]:
        """Render a page, or a region of it, to PNG for a model to look at.

        ``bbox`` and ``marks`` are in the displayed frame this package uses
        everywhere (PDF points, top-left origin) — a box from a text line, a
        table or a markup renders as-is. ``marks`` are ``(x, y, label)``
        numbered circles for set-of-marks prompting. With no ``dpi`` a full
        page renders at about :data:`DEFAULT_PAGE_LONG_SIDE_PX` on its long
        side and a region at 200 dpi; either is lowered to stay under
        ``max_pixels``. Returns ``(png_bytes, info)`` where ``info`` has the
        clip actually rendered, the dpi used and the pixel size.
        """
        import fitz
        from planlens.ir.render import clip_rect_for_bbox, render_region
        (index,) = parse_pages(index, self.n_pages)
        page = self._doc[index]
        pr = page.rect
        clip = clip_rect_for_bbox(tuple(bbox) if bbox is not None else None,
                                  (pr.x0, pr.y0, pr.x1, pr.y1), pad_frac)
        cw, ch = clip[2] - clip[0], clip[3] - clip[1]
        if dpi is None:
            dpi = (72.0 * DEFAULT_PAGE_LONG_SIDE_PX / max(cw, ch, 1.0)
                   if bbox is None else 200.0)
            dpi = max(36.0, dpi)
        px = (cw * dpi / 72.0) * (ch * dpi / 72.0)
        if px > max_pixels:
            dpi = dpi * (max_pixels / px) ** 0.5
        dpi = float(dpi)
        if marks:
            # Drawing marks writes into the page; render from a fresh copy so
            # the cached document (and its text extraction) stays untouched.
            png = render_region(content=self._doc.tobytes(), page=index,
                                bbox=tuple(bbox) if bbox is not None else None,
                                dpi=int(round(dpi)), pad_frac=pad_frac,
                                marks=[tuple(m) for m in marks], frame="page")
        else:
            pix = page.get_pixmap(dpi=int(round(dpi)), clip=fitz.Rect(clip))
            png = pix.tobytes("png")
        info = {"page": index, "clip": [round(v, 1) for v in clip],
                "dpi": round(dpi, 1),
                "width_px": int(round(cw * dpi / 72.0)),
                "height_px": int(round(ch * dpi / 72.0))}
        return png, info

    # -- markups / text -----------------------------------------------------
    def markups(self, pages: PageSpec = None,
                author: Optional[str] = None) -> List[Markup]:
        """Every review markup on the given pages, optionally by one author
        (case-insensitive substring)."""
        out = []
        for index in parse_pages(pages, self.n_pages):
            for mk in self._page_annots(index)[0]:
                if author and author.lower() not in (mk.author or "").lower():
                    continue
                out.append(mk)
        return out

    def text(self, pages: PageSpec = None,
             include_hidden_cad_text: bool = True) -> str:
        """Plain text of the given pages with a header line per page."""
        parts = []
        for index in parse_pages(pages, self.n_pages):
            lines, *_ = self._page_text(index)
            cad = []
            if include_hidden_cad_text:
                cad, _ = drop_cad_text_already_in_layer(
                    self._page_annots(index)[1], lines)
            label = self.label(index)
            head = f"=== page {index}" + (f" ({label})" if label else "") + " ==="
            body = "\n".join(ln.text for ln in list(lines) + cad)
            parts.append(head + ("\n" + body if body else "\n[no text]"))
        return "\n".join(parts)


def open_document(source: Union[str, bytes], text_source: Any = None,
                  name: Optional[str] = None) -> Document:
    """Open a PDF from a path or bytes."""
    if isinstance(source, (bytes, bytearray)):
        return Document(content=bytes(source), text_source=text_source,
                        name=name)
    return Document(filepath=source, text_source=text_source, name=name)
