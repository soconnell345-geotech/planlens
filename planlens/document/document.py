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
from typing import Any, Dict, Iterable, List, Optional, Sequence, Union

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


class Document:
    """A PDF opened for review. Use as a context manager or call :meth:`close`."""

    def __init__(self, filepath: Optional[str] = None,
                 content: Optional[bytes] = None,
                 text_source: Any = None, name: Optional[str] = None):
        import fitz
        if filepath is None and content is None:
            raise ValueError("pass filepath or content")
        if content is not None:
            self._doc = fitz.open(stream=content, filetype="pdf")
        else:
            self._doc = fitz.open(filepath)
        self.source = name or (str(filepath) if filepath else "<bytes>")
        self.text_source = text_source
        self._text: Dict[int, tuple] = {}
        self._annots: Dict[int, tuple] = {}
        self._summaries: Dict[int, PageSummary] = {}
        self._pages: Dict[tuple, PageContent] = {}

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
        lines, blocks, _tables, stats, _w, src_name = self._page_text(index)
        markups, cad = self._page_annots(index)
        cad, _ = drop_cad_text_already_in_layer(cad, lines)
        n_paths = len(page.get_cdrawings())
        coverage = _image_coverage(page)
        n_chars = int(stats.get("n_text_chars", 0))
        n_cad_chars = sum(len(c.text) for c in cad)
        kind, evidence = classify_page(
            page.rect.width, page.rect.height, n_chars, n_cad_chars, n_paths,
            coverage, text_is_optical=src_name in (SOURCE_AZURE_DI, SOURCE_OCR))
        if stats.get("n_unmapped_chars"):
            evidence["unmapped_chars"] = stats["n_unmapped_chars"]
        if src_name != SOURCE_PDF_TEXT:
            evidence["text_source"] = src_name
        s = PageSummary(
            page=index, width=page.rect.width, height=page.rect.height,
            rotation=int(page.rotation), label=self.label(index), kind=kind,
            evidence=evidence, heading=_heading(lines, blocks),
            n_text_chars=n_chars, n_markups=len(markups), n_cad_text=len(cad))
        self._summaries[index] = s
        return s

    def page_map(self, pages: PageSpec = None) -> List[PageSummary]:
        """One :class:`PageSummary` per page — kind, size, heading, counts."""
        return [self.summary(i) for i in parse_pages(pages, self.n_pages)]

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
