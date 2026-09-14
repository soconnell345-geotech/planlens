"""Data model of planlens.document — what a reviewer's LLM gets to read.

Every coordinate is in the displayed-page frame (see :mod:`planlens.document.frame`):
PDF points, origin top-left, y down, /Rotate applied. Every item carries a
``source`` saying which extractor produced it, so a caller can always tell a
text-layer string from hidden CAD text, an OCR read or an Azure read.

Serialization is deliberately compact — ``to_dict`` rounds coordinates to 0.1 pt
and omits empty fields — because these payloads are read by a language model on
a token budget.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]


def _r(v: Optional[float], n: int = 1) -> Optional[float]:
    if v is None:
        return None
    out = round(float(v), n)
    return 0.0 if out == 0 else out


def _rb(b: Optional[Sequence[float]]) -> Optional[List[float]]:
    return None if b is None else [_r(v) for v in b]


def _rp(pts: Optional[Sequence[Sequence[float]]]) -> Optional[List[List[float]]]:
    return None if pts is None else [[_r(p[0]), _r(p[1])] for p in pts]


def _compact(d: Dict[str, Any]) -> Dict[str, Any]:
    """Drop None, empty containers and False flags."""
    return {k: v for k, v in d.items()
            if v is not None and v is not False and v != [] and v != {}}


# ---------------------------------------------------------------------------
# Text
# ---------------------------------------------------------------------------

#: Values of ``TextLine.source``.
SOURCE_PDF_TEXT = "pdf_text"            # the PDF's own text layer
SOURCE_CAD_HIDDEN = "cad_hidden_text"   # AutoCAD "SHX Text" annotations
SOURCE_OCR = "ocr"                      # planlens.ocr (RapidOCR)
SOURCE_AZURE_DI = "azure_di"            # Azure Document Intelligence layout


@dataclass
class TextLine:
    """One line of text: a run of characters sharing a baseline and direction.

    ``rotation`` is degrees counter-clockwise AS DISPLAYED (0 = ordinary text,
    90 = reads bottom-to-top). ``None`` means the extractor cannot know it —
    hidden CAD text records only a box, never a direction.
    """
    id: str
    page: int
    text: str
    bbox: BBox
    rotation: Optional[float] = 0.0
    size: Optional[float] = None
    font: Optional[str] = None
    bold: bool = False
    italic: bool = False
    color: Optional[str] = None
    block: Optional[str] = None
    source: str = SOURCE_PDF_TEXT
    confidence: float = 1.0
    words: Optional[List[Tuple[str, BBox]]] = None

    def to_dict(self, words: bool = False) -> Dict[str, Any]:
        d = {
            "id": self.id,
            "text": self.text,
            "bbox": _rb(self.bbox),
            "rotation": _r(self.rotation) if self.rotation else None,
            "size": _r(self.size),
            "font": self.font,
            "bold": self.bold,
            "italic": self.italic,
            "color": self.color if self.color not in (None, "#000000") else None,
            "block": self.block,
            "source": self.source if self.source != SOURCE_PDF_TEXT else None,
            "confidence": (_r(self.confidence, 3)
                           if self.confidence < 1.0 else None),
        }
        if self.rotation is None:
            d["rotation"] = "unknown"
        if words and self.words:
            d["words"] = [[w, _rb(b)] for w, b in self.words]
        return _compact(d)


@dataclass
class TextBlock:
    """A group of lines the extractor considers one unit (usually a paragraph).

    ``role`` is set only when a backend reports it (Azure DI: title,
    sectionHeading, pageHeader, pageFooter, pageNumber, footnote) — the PDF
    text layer carries no roles, and none are guessed.
    """
    id: str
    page: int
    bbox: BBox
    line_ids: List[str] = field(default_factory=list)
    text: str = ""
    role: Optional[str] = None
    source: str = SOURCE_PDF_TEXT

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id,
            "bbox": _rb(self.bbox),
            "role": self.role,
            "text": self.text,
            "line_ids": self.line_ids,
            "source": self.source if self.source != SOURCE_PDF_TEXT else None,
        })


# ---------------------------------------------------------------------------
# Tables
# ---------------------------------------------------------------------------

@dataclass
class Table:
    """A detected table. ``rows`` excludes the header row when one was found.

    ``None`` cells are cells a merged neighbour covers; they are kept rather
    than collapsed to "" so the grid shape stays true to the page.
    """
    id: str
    page: int
    bbox: BBox
    n_rows: int
    n_cols: int
    rows: List[List[Optional[str]]] = field(default_factory=list)
    header: Optional[List[Optional[str]]] = None
    source: str = "pymupdf_find_tables"
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id,
            "bbox": _rb(self.bbox),
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "header": self.header,
            "rows": self.rows,
            "source": self.source,
            "notes": self.notes,
        })

    def to_markdown(self) -> str:
        def cell(v):
            return "" if v is None else str(v).replace("\n", " ").replace("|", "\\|")
        grid = ([self.header] if self.header else []) + self.rows
        if not grid:
            return ""
        width = max(len(r) for r in grid)
        lines = []
        for i, r in enumerate(grid):
            r = list(r) + [None] * (width - len(r))
            lines.append("| " + " | ".join(cell(v) for v in r) + " |")
            if i == 0:
                lines.append("|" + "---|" * width)
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Markups (review annotations)
# ---------------------------------------------------------------------------

@dataclass
class Markup:
    """A review markup: a comment, cloud, arrow, stamp or shape a person placed.

    ``points_at`` is where the markup directs attention when the PDF says so
    explicitly — the tip of a callout leader or the arrowhead end of an arrow.
    It is ``None`` when the annotation carries no such geometry; it is never
    inferred from proximity. ``points_to_markup`` names the markup whose box
    contains that tip (a reply callout aimed at a reviewer's comment).
    ``in_reply_to`` comes only from the PDF's own reply link (/IRT) —
    Bluebeam also uses it to tie a cloud to its comment box; ``replies`` is
    its inverse. ``appearance_text`` is text the markup DRAWS that its comment
    field does not hold — the wording of a stamp, for instance.
    """
    id: str
    page: int
    kind: str
    bbox: BBox
    text: str = ""
    author: Optional[str] = None
    subject: Optional[str] = None
    intent: Optional[str] = None
    created: Optional[str] = None
    modified: Optional[str] = None
    vertices: Optional[List[Point]] = None
    points_at: Optional[Point] = None
    points_to_markup: Optional[str] = None
    appearance_text: Optional[str] = None
    color: Optional[str] = None
    in_reply_to: Optional[str] = None
    replies: List[str] = field(default_factory=list)
    source: str = "pdf_annotation"
    #: PDF object number of the annotation (not serialized): lets a caller
    #: reload it with ``page.load_annot(xref)``.
    xref: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id,
            "kind": self.kind,
            "intent": self.intent,
            "subject": self.subject,
            "author": self.author,
            "text": self.text,
            "appearance_text": self.appearance_text,
            "created": self.created,
            "modified": self.modified if self.modified != self.created else None,
            "bbox": _rb(self.bbox),
            "points_at": (None if self.points_at is None
                          else [_r(self.points_at[0]), _r(self.points_at[1])]),
            "points_to_markup": self.points_to_markup,
            "vertices": _rp(self.vertices),
            "color": self.color,
            "in_reply_to": self.in_reply_to,
            "replies": self.replies,
        })


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

#: Values of ``PageSummary.kind`` — coarse on purpose, with evidence attached.
PAGE_KINDS = ("text", "drawing_sheet", "figure", "scanned", "blank", "mixed")


@dataclass
class PageSummary:
    """One row of a document's page map: cheap to compute for every page."""
    page: int
    width: float
    height: float
    rotation: int = 0
    label: Optional[str] = None
    kind: str = "mixed"
    evidence: Dict[str, Any] = field(default_factory=dict)
    heading: Optional[str] = None
    n_text_chars: int = 0
    n_markups: int = 0
    n_cad_text: int = 0

    def to_dict(self) -> Dict[str, Any]:
        d = _compact({
            "page": self.page,
            "label": self.label,
            "kind": self.kind,
            "size_in": [_r(self.width / 72.0), _r(self.height / 72.0)],
            "rotation": self.rotation,
            "heading": self.heading,
            "n_text_chars": self.n_text_chars,
            "n_markups": self.n_markups,
            "n_cad_text": self.n_cad_text,
            "evidence": self.evidence,
        })
        d["page"] = self.page   # page 0 must survive _compact
        return d


@dataclass
class PageContent:
    """Everything extracted from one page, in the displayed frame."""
    page: int
    width: float
    height: float
    rotation: int = 0
    label: Optional[str] = None
    kind: Optional[str] = None
    lines: List[TextLine] = field(default_factory=list)
    blocks: List[TextBlock] = field(default_factory=list)
    tables: List[Table] = field(default_factory=list)
    markups: List[Markup] = field(default_factory=list)
    text_sources: List[str] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def line(self, line_id: str) -> Optional[TextLine]:
        for ln in self.lines:
            if ln.id == line_id:
                return ln
        return None

    def text(self, include_hidden_cad_text: bool = True) -> str:
        """The page's text in extraction order, one line per line."""
        return "\n".join(
            ln.text for ln in self.lines
            if include_hidden_cad_text or ln.source != SOURCE_CAD_HIDDEN)

    def to_dict(self, lines: bool = True, blocks: bool = False,
                tables: bool = True, markups: bool = True,
                words: bool = False) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "page": self.page,
            "frame": "displayed_pt_top_left",
            "size_pt": [_r(self.width), _r(self.height)],
            "rotation": self.rotation,
        }
        if self.label:
            d["label"] = self.label
        if self.kind:
            d["kind"] = self.kind
        if self.text_sources:
            d["text_sources"] = self.text_sources
        if self.stats:
            d["stats"] = self.stats
        if lines:
            d["lines"] = [ln.to_dict(words=words) for ln in self.lines]
        if blocks:
            d["blocks"] = [b.to_dict() for b in self.blocks]
        if tables and self.tables:
            d["tables"] = [t.to_dict() for t in self.tables]
        if markups and self.markups:
            d["markups"] = [m.to_dict() for m in self.markups]
        if self.warnings:
            d["warnings"] = self.warnings
        return d
