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
from typing import (
    TYPE_CHECKING, Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union,
)

if TYPE_CHECKING:  # for typing only — the scanner is imported where it is used
    from planlens.document.quantities import QuantityMention

from planlens.document.annotations import (
    attach_appearance_text, drop_cad_text_already_in_layer,
    extract_annotations,
)
from planlens.document.classify import classify_page
from planlens.document.frame import bbox_union
from planlens.document.imagehash import (
    DUP_HASH_DISTANCE, hamming, page_dhash, wants_image_hash,
)
from planlens.document.model import (
    SOURCE_AZURE_DI, SOURCE_CAD_HIDDEN, SOURCE_OCR, SOURCE_PDF_TEXT, Markup,
    PageContent, PageSummary,
    TextBlock, TextLine,
)
from planlens.document.pdf_text import MAX_UNMAPPED_FRACTION, extract_text
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

#: The highest dpi a render sized to an image budget will use: a region small
#: enough to need more is shown as large as this makes it, not larger (past
#: this a vector drawing gains nothing and a scan only shows bigger pixels).
MAX_RENDER_DPI = 1200.0

#: JPEG quality for renders sent to a model as JPEG — Anthropic's zoom-tool
#: cookbook value: fine lettering survives, the file is a fraction of a PNG's.
DEFAULT_JPEG_QUALITY = 92

#: Image formats :meth:`Document.render` makes (``auto``: the smaller of the two).
RENDER_FORMATS = ("png", "jpeg", "auto")

#: Lowest rapidfuzz partial-ratio score (0-100) a fuzzy search hit may have.
#: MEASURED, not chosen — see "Forgiving search" in DESIGN.md: real drawing
#: callouts from a submittal's sheets, each corrupted by one substituted
#: letter, one dropped letter and one transposition, against the count of
#: unrelated lines the same threshold lets through.
DEFAULT_FUZZY_MIN_SCORE = 80


def _load_rapidfuzz():
    """The ``rapidfuzz`` package, or a clear instruction.

    Import-guarded here rather than at module import because approximate
    matching is one option on one method: a caller who never asks for it must
    not pay an import to open a PDF. ``rapidfuzz`` is a core dependency as of
    0.4.0, so reaching the error means an install that was trimmed by hand.
    """
    try:
        from rapidfuzz import fuzz
    except ImportError as exc:  # pragma: no cover - exercised by monkeypatch
        raise ImportError(
            'fuzzy search needs the package rapidfuzz: '
            'pip install rapidfuzz') from exc
    return fuzz


def fuzzy_search_available() -> bool:
    """Whether :meth:`Document.search` can be asked for ``fuzzy=True`` here.

    A caller offering fuzzy search as ADVICE — "your exact search found
    nothing, try this" — must know whether the advice is followable before it
    gives it. Telling a model to retry with an option that will raise is worse
    than saying nothing.
    """
    try:
        _load_rapidfuzz()
    except ImportError:
        return False
    return True


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
        #: Page -> its text content hash, for the pages that have one.
        self._content_keys: Dict[int, str] = {}
        #: Summaries carrying a picture hash, in the order they were read.
        self._image_hashes: List[PageSummary] = []
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

    def tobytes(self) -> bytes:
        """The document as PDF bytes — what was opened, re-serialized.

        A caller that writes a MARKED-UP copy needs the PDF itself and has
        only the open document: re-resolving whatever string it was opened
        under is not the same thing, because one document can be reached by
        several names. An image source comes back as the one-page PDF it was
        converted to on open, which is what such a caller wants.
        """
        return self._doc.tobytes()

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
        unmapped = int(stats.get("n_unmapped_chars", 0) or 0)
        if unmapped:
            evidence["unmapped_chars"] = unmapped
        unmapped_fraction = (unmapped / n_chars) if n_chars else 0.0
        text_reliable = unmapped_fraction <= MAX_UNMAPPED_FRACTION
        if not text_reliable:
            # The page HAS a text layer; it just does not say what the page
            # says. That is the same problem as having none — the words must
            # be read off the picture — so it joins `needs_ocr`, which is
            # what `pages_needing_ocr` and the advice already read.
            evidence["text_unreliable"] = True
            evidence["needs_ocr"] = True
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
            text_reliable=text_reliable,
            unmapped_fraction=unmapped_fraction,
        )
        if kind != "blank" and (n_words >= 15 or n_paths >= 30):
            key = content_hash(kind, lines, n_paths)
            self._content_keys[index] = key
            first = self._hashes.setdefault(key, index)
            if first != index:
                s.duplicate_of, s.duplicate_rule = first, "text"
        if wants_image_hash(kind, n_chars, bool(evidence.get("needs_ocr"))):
            # The text rule is blind on a page whose text is a footer or
            # nothing at all. Cost is why this is gated rather than run on
            # every page: measured on a real 260-page submittal, hashing every
            # page costs 2.3-3.3 s against 0.45-0.64 s for the 13 that qualify.
            s.image_hash = page_dhash(page)
            if s.image_hash is not None:
                first = self._image_duplicate(s)
                self._image_hashes.append(s)
                if first is not None and s.duplicate_of is None:
                    s.duplicate_of, s.duplicate_rule = first, "image"
        self._summaries[index] = s
        self._segments = None
        return s

    def _image_duplicate(self, s: PageSummary) -> Optional[int]:
        """The earliest page whose PICTURE matches this one's, or None.

        A match needs the same kind and the same displayed size as well as a
        hash within :data:`~planlens.document.imagehash.DUP_HASH_DISTANCE`.
        Both guards do real work: the hash squeezes every page into the same
        8x8 grid, so a letter page and a D-size sheet are compared on equal
        terms unless the size says not to, and two kinds of page that happen
        to share a silhouette are not the same page.

        **The picture never overrules the words.** If both pages carry enough
        text for the text rule to have hashed them, and those hashes differ,
        the pages are not duplicates however alike they look. This is what
        stops a drawing set being collapsed into its first sheet: sheets off
        one border and title block differ by a sheet number and a few labels,
        which is most of what a reviewer needs and almost none of the ink.
        The picture speaks where the text is SILENT, not where it disagrees.
        """
        best: Optional[int] = None
        size = (round(s.width, 1), round(s.height, 1))
        mine = self._content_keys.get(s.page)
        for other in self._image_hashes:
            if other.page >= s.page or other.kind != s.kind:
                continue
            if (round(other.width, 1), round(other.height, 1)) != size:
                continue
            theirs = self._content_keys.get(other.page)
            if mine is not None and theirs is not None and mine != theirs:
                continue
            if hamming(other.image_hash, s.image_hash) <= DUP_HASH_DISTANCE:
                if best is None or other.page < best:
                    best = other.page
        return best

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
    def _search_groups(self, index: int
                       ) -> Tuple[List[List[TextLine]], List[Markup]]:
        """The runs of text a search matches WITHIN, plus the page's markups.

        One group per text block (its lines joined, so a phrase broken across
        a line break still matches), one per line outside any block, one per
        hidden CAD string. Exact and fuzzy search share this so that turning
        fuzzy on widens how a candidate is compared and never which candidates
        are compared.
        """
        lines, blocks, *_ = self._page_text(index)
        markups, cad = self._page_annots(index)
        cad, _ = drop_cad_text_already_in_layer(cad, lines)
        by_id = {ln.id: ln for ln in lines}
        groups: List[List[TextLine]] = [
            [by_id[i] for i in b.line_ids if i in by_id] for b in blocks]
        in_blocks = {i for b in blocks for i in b.line_ids}
        groups.extend([ln] for ln in lines if ln.id not in in_blocks)
        groups.extend([c] for c in cad)
        return [g for g in groups if g], markups

    @staticmethod
    def _join_group(group: Sequence[TextLine]
                    ) -> Tuple[str, List[Tuple[int, int, TextLine]]]:
        """``(joined text, [(start, end, line), ...])`` — one space per join."""
        spans, pos, parts = [], 0, []
        for ln in group:
            spans.append((pos, pos + len(ln.text), ln))
            parts.append(ln.text)
            pos += len(ln.text) + 1
        return " ".join(parts), spans

    def _line_hit(self, index: int, text: str,
                  spans: Sequence[Tuple[int, int, TextLine]],
                  start: int, end: int, matched: str,
                  context_chars: int) -> Dict[str, Any]:
        involved = [ln for a, b, ln in spans if a < end and b > start]
        a = max(0, start - context_chars)
        b = min(len(text), end + context_chars)
        hit = {
            "page": index,
            "match": matched,
            "snippet": ("..." if a else "") + text[a:b]
                       + ("..." if b < len(text) else ""),
            "line_ids": [ln.id for ln in involved],
            "bbox": [round(v, 1) for v in
                     bbox_union([ln.bbox for ln in involved])],
        }
        label = self.label(index)
        if label:
            hit["label"] = label
        return hit

    def search(self, pattern: str, pages: PageSpec = None, regex: bool = False,
               case_sensitive: bool = False, include_markups: bool = True,
               max_hits: int = 200, context_chars: int = 60,
               fuzzy: bool = False,
               min_score: int = DEFAULT_FUZZY_MIN_SCORE) -> Dict[str, Any]:
        """Find text across pages. Matches may span line breaks within a block.

        Each hit names its page (and label), the matched line ids, a snippet and
        the union box of those lines, so the caller can render or open exactly
        that spot. Markup comments, authors and subjects are searched too.

        ``fuzzy=True`` matches approximately instead of exactly (see
        :meth:`_search_fuzzy`), for text that was read optically or plotted as
        strokes and so carries letter errors. It needs the optional package
        ``rapidfuzz`` and ignores ``regex``.
        """
        if fuzzy:
            return self._search_fuzzy(
                pattern, pages=pages, case_sensitive=case_sensitive,
                include_markups=include_markups, max_hits=max_hits,
                context_chars=context_chars, min_score=min_score)
        flags = 0 if case_sensitive else re.IGNORECASE
        rx = re.compile(pattern if regex else re.escape(pattern), flags)
        hits: List[Dict[str, Any]] = []
        per_page: Dict[int, int] = {}
        truncated = False
        page_list = parse_pages(pages, self.n_pages)
        for index in page_list:
            groups, markups = self._search_groups(index)
            for group in groups:
                text, spans = self._join_group(group)
                for m in rx.finditer(text):
                    if len(hits) >= max_hits:
                        truncated = True
                        break
                    hit = self._line_hit(index, text, spans, m.start(),
                                         m.end(), m.group(0), context_chars)
                    involved = [ln for a, b, ln in spans
                                if a < m.end() and b > m.start()]
                    if involved and involved[0].source != SOURCE_PDF_TEXT:
                        hit["source"] = involved[0].source
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

    def _search_fuzzy(self, pattern: str, pages: PageSpec = None,
                      case_sensitive: bool = False,
                      include_markups: bool = True, max_hits: int = 200,
                      context_chars: int = 60,
                      min_score: int = DEFAULT_FUZZY_MIN_SCORE
                      ) -> Dict[str, Any]:
        """Approximate search: the same candidates, compared by similarity.

        A sheet's lettering does not always survive into characters intact.
        OCR reads B for R, SHX strokes recovered by an optical pass drop a
        letter, a typist transposes two. An exact search for the word the
        reviewer has in mind then returns nothing at all, which reads as
        absence — the one answer a review tool must never give wrongly.

        Every candidate is scored with ``rapidfuzz``'s partial ratio: the best
        alignment of the query anywhere inside the candidate, 0-100, so a short
        query still scores against a long line. Hits at or above ``min_score``
        are returned best first, then by page. Every hit carries its ``score``
        and its ``source``, because a reader deciding whether an 88 is the word
        they meant needs to know it came off a scan rather than the text layer.

        Unlike the exact path this scores every candidate on every requested
        page before answering — ordering by score means there is no such thing
        as the first ``max_hits`` — so a fuzzy search over a long document
        costs a full pass.
        """
        fuzz = _load_rapidfuzz()
        needle = pattern if case_sensitive else pattern.lower()
        if not needle:
            return {"pattern": pattern, "n_hits": 0, "pages_with_hits": {},
                    "truncated": False, "hits": [], "fuzzy": True,
                    "min_score": min_score}
        cutoff = float(min_score)
        scored: List[Tuple[float, int, Dict[str, Any]]] = []

        def align(hay: str) -> Optional[Tuple[float, int, int]]:
            """``(score, start, end)`` of the query's best place in ``hay``.

            ``partial_ratio`` slides the SHORTER string over the longer one,
            whichever that turns out to be — so a one-character line on a
            drawing sheet scores 100 against any query containing that
            character. Measured on a real submittal, that alone put 120 hits
            under words the document does not contain, at EVERY threshold up
            to 95: the sliding was hiding the mismatch in the part of the
            query nobody compared. A candidate shorter than the query is
            therefore scored whole, against the whole query.
            """
            probe, query = hay, pattern
            if not case_sensitive:
                low = hay.lower()
                if len(low) == len(hay):
                    probe, query = low, needle
                # Otherwise the case fold changed the string's LENGTH (a rare
                # Unicode fold), and the alignment offsets it returns would
                # point at the wrong characters of the original — so that one
                # candidate is compared as written rather than mislocated.
            if len(probe) < len(query):
                score = fuzz.ratio(query, probe, score_cutoff=cutoff)
                return (score, 0, len(probe)) if score else None
            al = fuzz.partial_ratio_alignment(query, probe,
                                              score_cutoff=cutoff)
            return None if al is None else (al.score, al.dest_start,
                                            al.dest_end)

        for index in parse_pages(pages, self.n_pages):
            groups, markups = self._search_groups(index)
            for group in groups:
                text, spans = self._join_group(group)
                if not text:
                    continue
                al = align(text)
                if al is None:
                    continue
                score, start, end = al
                hit = self._line_hit(index, text, spans, start, end,
                                     text[start:end], context_chars)
                involved = [ln for a, b, ln in spans if a < end and b > start]
                hit["score"] = round(score, 1)
                hit["source"] = (involved[0].source if involved
                                 else SOURCE_PDF_TEXT)
                scored.append((score, index, hit))
            if include_markups:
                for mk in markups:
                    hay = " | ".join(x for x in (mk.text, mk.author,
                                                 mk.subject) if x)
                    if not hay:
                        continue
                    al = align(hay)
                    if al is None:
                        continue
                    score, start, end = al
                    scored.append((score, index, {
                        "page": index,
                        "match": hay[start:end],
                        "snippet": mk.text[:2 * context_chars + 40],
                        "markup_id": mk.id, "author": mk.author,
                        "bbox": [round(v, 1) for v in mk.bbox],
                        "score": round(score, 1),
                        "source": "pdf_annotation"}))
        scored.sort(key=lambda row: (-row[0], row[1]))
        kept = [row[2] for row in scored[:max(1, int(max_hits))]]
        per_page: Dict[int, int] = {}
        for hit in kept:
            per_page[hit["page"]] = per_page.get(hit["page"], 0) + 1
        return {"pattern": pattern, "n_hits": len(kept),
                "pages_with_hits": per_page,
                "truncated": len(scored) > len(kept), "hits": kept,
                "fuzzy": True, "min_score": min_score}

    # -- quantities ---------------------------------------------------------
    def quantities(self, pages: PageSpec = None,
                   kinds: Optional[Sequence[str]] = None,
                   units: Optional[Sequence[str]] = None,
                   include_markups: bool = True,
                   max_mentions: int = 500
                   ) -> List["QuantityMention"]:
        """Every value-with-unit the document STATES, located on its page.

        The other half of a review: :mod:`planlens.ir` measures what the plan
        DRAWS, and this reads what the narrative, the schedule and the
        reviewer's comment CLAIM, so the two can be compared. A bare number is
        not a mention (see :mod:`planlens.document.quantities`), and nothing
        is converted — the reader decides that with
        :meth:`planlens.ir.measure.Quantity.to`.

        Filters are by ``kinds`` (``length``, ``pressure_or_stress``,
        ``elevation``, ...) and by ``units`` as this module spells them
        (``ft``, ``psf``, ``kN``), both case-insensitive. Mentions come back in
        page then reading order, capped at ``max_mentions``.
        """
        from planlens.document.quantities import (
            filter_mentions, scan_text,
        )
        cap = max(0, int(max_mentions))
        out: List["QuantityMention"] = []
        for index in parse_pages(pages, self.n_pages):
            if len(out) >= cap:
                break
            found: List["QuantityMention"] = []
            groups, markups = self._search_groups(index)
            label_source = None
            for group in groups:
                text, spans = self._join_group(group)
                if not text:
                    continue
                label_source = group[0].source
                for men in scan_text(text):
                    start, end = men.span
                    involved = [ln for a, b, ln in spans
                                if a < end and b > start]
                    men.page = index
                    men.line_ids = [ln.id for ln in involved]
                    men.bbox = tuple(
                        round(v, 1) for v in
                        bbox_union([ln.bbox for ln in involved])) if involved \
                        else None
                    men.source = (involved[0].source if involved
                                  else label_source or SOURCE_PDF_TEXT)
                    found.append(men)
            if include_markups:
                for mk in markups:
                    for men in scan_text(mk.text or ""):
                        men.page = index
                        men.bbox = tuple(round(v, 1) for v in mk.bbox)
                        men.source = "pdf_annotation"
                        men.markup_id = mk.id
                        found.append(men)
            # Filtered per page, not at the end: a capped run over a long
            # document must fill its cap with mentions the caller ASKED for,
            # not stop early on pages full of the kinds they filtered out.
            out.extend(filter_mentions(found, kinds=kinds, units=units))
        return out[:cap]

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
               max_pixels: Optional[int] = None,
               budget: Any = None, fmt: str = "png",
               jpeg_quality: int = DEFAULT_JPEG_QUALITY,
               ) -> Tuple[bytes, Dict[str, Any]]:
        """Render a page, or a region of it, to an image for a model to look at.

        ``bbox`` and ``marks`` are in the displayed frame this package uses
        everywhere (PDF points, top-left origin) — a box from a text line, a
        table or a markup renders as-is. ``marks`` are ``(x, y, label)``
        numbered circles for set-of-marks prompting.

        Size. With an image ``budget`` (an
        :class:`~planlens.document.budget.ImageBudget` or a name in
        :data:`~planlens.document.budget.BUDGETS`) the image is the largest
        the model looks at WITHOUT shrinking it: with no ``dpi`` the page or
        region fills the budget — a small region is re-rendered from the PDF
        at whatever dpi that takes, up to :data:`MAX_RENDER_DPI` — and a given
        ``dpi`` is lowered if it would overshoot. Without a budget a full page
        renders at about :data:`DEFAULT_PAGE_LONG_SIDE_PX` on its long side
        and a region at 200 dpi. Either way the render stays under
        ``max_pixels`` (default :data:`DEFAULT_MAX_PIXELS`, or the budget's
        own area when that is larger).

        ``fmt`` is ``"png"``, ``"jpeg"`` (at ``jpeg_quality``) or ``"auto"``,
        which keeps whichever is smaller — for images going to a model, where
        a conversation of zooms runs into request-size limits. A vector
        drawing (white paper, thin lines) is about half the size as PNG, a
        scan much smaller as JPEG, so ``auto`` picks right for both and never
        blurs line art with JPEG artefacts it did not need. Returns
        ``(image_bytes, info)``; ``info`` has the clip actually rendered, the
        dpi, the pixel size of the image as made, its format (``png`` or
        ``jpeg``, after ``auto``) and the budget's name.
        """
        import fitz
        from planlens.document.budget import fit_size, resolve_budget
        from planlens.ir.render import (
            DEFAULT_MARK_RADIUS, _draw_marks, clip_rect_for_bbox)
        if fmt not in RENDER_FORMATS:
            raise ValueError(f"fmt must be one of {RENDER_FORMATS}, got {fmt!r}")
        bud = resolve_budget(budget)
        cap = DEFAULT_MAX_PIXELS if max_pixels is None else int(max_pixels)
        if bud is not None and max_pixels is None:
            cap = max(cap, bud.max_pixels)
        (index,) = parse_pages(index, self.n_pages)
        page = self._doc[index]
        pr = page.rect
        clip = clip_rect_for_bbox(tuple(bbox) if bbox is not None else None,
                                  (pr.x0, pr.y0, pr.x1, pr.y1), pad_frac)
        cw, ch = clip[2] - clip[0], clip[3] - clip[1]
        if bud is not None:
            tw, th = fit_size(cw, ch, bud)
            fit_dpi = 72.0 * min(tw / cw, th / ch)
            dpi = fit_dpi if dpi is None else min(float(dpi), fit_dpi)
            dpi = min(dpi, MAX_RENDER_DPI)
        elif dpi is None:
            dpi = (72.0 * DEFAULT_PAGE_LONG_SIDE_PX / max(cw, ch, 1.0)
                   if bbox is None else 200.0)
            dpi = max(36.0, dpi)
        px = (cw * dpi / 72.0) * (ch * dpi / 72.0)
        if px > cap:
            dpi = dpi * (cap / px) ** 0.5
        # The dpi is used exactly (a zoom matrix, not get_pixmap's whole-number
        # dpi), so the image is the size asked for: a whole-number dpi made
        # a "2000 px" page 2016 px, and a budget must be met to the pixel.
        dpi = float(dpi)

        src, target = self._doc, page
        if marks:
            # Drawing marks writes into the page; render from a fresh copy so
            # the cached document (and its text extraction) stays untouched.
            src = fitz.open(stream=self._doc.tobytes(), filetype="pdf")
            target = src[index]
            _draw_marks(target, [tuple(m) for m in marks], DEFAULT_MARK_RADIUS,
                        (1.0, 0.0, 0.0), fill_color=None,
                        text_color=(1.0, 0.0, 0.0))
        try:
            pix = target.get_pixmap(matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                                    clip=fitz.Rect(clip))
            # The clip's pixel rect rounds outward, so an exact fit can come
            # out a pixel over — and one pixel can cost a row of patches.
            for _ in range(4):
                if (pix.width * pix.height <= cap
                        and (bud is None or bud.fits(pix.width, pix.height))):
                    break
                dpi *= 1.0 - 2.0 / max(pix.width, pix.height)
                pix = target.get_pixmap(
                    matrix=fitz.Matrix(dpi / 72.0, dpi / 72.0),
                    clip=fitz.Rect(clip))
            if fmt == "auto":
                png = pix.tobytes("png")
                jpg = pix.tobytes("jpeg", jpg_quality=int(jpeg_quality))
                data, fmt = (jpg, "jpeg") if len(jpg) < len(png) else (png, "png")
            elif fmt == "jpeg":
                data = pix.tobytes("jpeg", jpg_quality=int(jpeg_quality))
            else:
                data = pix.tobytes("png")
            width_px, height_px = pix.width, pix.height
        finally:
            if src is not self._doc:
                src.close()     # the marks are discarded, never saved
        info = {"page": index, "clip": [round(v, 1) for v in clip],
                "dpi": round(dpi, 1), "width_px": width_px,
                "height_px": height_px, "format": fmt}
        if bud is not None:
            info["budget"] = bud.name
        return data, info

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
