"""PDF annotations: review markups, and the hidden text CAD programs leave behind.

Two very different things live in a PDF's annotation list, and this module
separates them.

**Review markups.** Comments, callouts, clouds, arrows, stamps and shapes that a
reviewer placed with Bluebeam, Acrobat or similar. On a design submittal these
ARE the review record: the comment, who wrote it, when, and — for callouts and
arrows — the exact spot on the sheet it points at. They come back as
:class:`Markup` objects.

**Hidden CAD text.** When AutoCAD plots lettering in an SHX font it draws the
letters as vector strokes, so the PDF has no text layer for them — and it adds an
invisible Square annotation titled ``"AutoCAD SHX Text"`` over each string,
holding the real characters. Found on a real 260-page submittal (320 of them
across its drawing sheets) and on one of the ten validation sheets (66). They come
back as :class:`TextLine` with ``source="cad_hidden_text"``: the characters are
exact, the box is AutoCAD's, and the reading direction is not recorded
(``rotation=None``).

Coordinates are converted from PyMuPDF's unrotated space to the displayed frame.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from planlens.document.frame import bbox_iou, to_display_bbox, to_display_point
from planlens.document.model import SOURCE_CAD_HIDDEN, Markup, TextLine

#: The ``/T`` (title/author) AutoCAD writes on its hidden SHX-text annotations.
CAD_HIDDEN_TEXT_TITLE = "AutoCAD SHX Text"

#: Annotation types that are not markups: links, form widgets and popup windows.
_NOT_MARKUPS = {"Link", "Widget", "Popup"}

_PDF_DATE = re.compile(
    r"D:(\d{4})(\d{2})?(\d{2})?(\d{2})?(\d{2})?(\d{2})?"
    r"(?:([Zz+\-])(\d{2})?'?(\d{2})?'?)?")


def pdf_date_to_iso(raw: Optional[str]) -> Optional[str]:
    """``D:20260825160934-04'00'`` -> ``2026-08-25T16:09:34-04:00``.

    Returns the raw string unchanged when it does not parse, and None for empty.
    """
    if not raw:
        return None
    m = _PDF_DATE.match(raw.strip())
    if not m:
        return raw
    y, mo, d, h, mi, s, tz, tzh, tzm = m.groups()
    out = f"{y}-{mo or '01'}-{d or '01'}"
    if h:
        out += f"T{h}:{mi or '00'}:{s or '00'}"
        if tz in ("Z", "z"):
            out += "Z"
        elif tz in ("+", "-"):
            out += f"{tz}{tzh or '00'}:{tzm or '00'}"
    return out


def _clean(text: Optional[str]) -> str:
    if not text:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n").strip()


def _hex_rgb(rgb) -> Optional[str]:
    if not rgb or len(rgb) < 3:
        return None
    return "#%02x%02x%02x" % tuple(
        max(0, min(255, int(round(float(c) * 255)))) for c in rgb[:3])


def _name_key(doc, xref: int, key: str) -> Optional[str]:
    kind, val = doc.xref_get_key(xref, key)
    if kind == "name" and val:
        return val.lstrip("/")
    return None


def _points_at(annot, kind: str, has_callout: bool, verts) -> Optional[Tuple[float, float]]:
    """The spot a markup explicitly points to, or None.

    Callout (FreeText with /CL): the PDF spec puts the callout line's START at
    the target, which is where its line ending is drawn. Line / PolyLine: the
    end that carries an arrowhead (/LE) when exactly one end does.
    """
    if not verts:
        return None
    if kind == "FreeText" and has_callout:
        return verts[0]
    if kind in ("Line", "PolyLine"):
        try:
            start_le, end_le = annot.line_ends
        except Exception:
            return None
        if end_le and not start_le:
            return verts[-1]
        if start_le and not end_le:
            return verts[0]
    return None


def extract_annotations(page, page_index: int
                        ) -> Tuple[List[Markup], List[TextLine]]:
    """Review markups and hidden CAD text lines for one ``fitz.Page``."""
    doc = page.parent
    markups: List[Markup] = []
    cad_lines: List[TextLine] = []
    xref_to_id: Dict[int, str] = {}
    irt_of: Dict[str, int] = {}

    for annot in page.annots() or []:
        kind = annot.type[1]
        if kind in _NOT_MARKUPS:
            continue
        info = annot.info or {}
        author = info.get("title") or None
        bbox = to_display_bbox(page, annot.rect)

        if author == CAD_HIDDEN_TEXT_TITLE:
            text = " ".join(_clean(info.get("content")).split())
            if text:
                cad_lines.append(TextLine(
                    id=f"p{page_index}.c{len(cad_lines)}",
                    page=page_index, text=text, bbox=bbox, rotation=None,
                    color=None, source=SOURCE_CAD_HIDDEN))
            continue

        xref = annot.xref
        has_callout = doc.xref_get_key(xref, "CL")[0] == "array"
        verts = None
        if kind in ("Line", "Polygon", "PolyLine", "Ink") or (
                kind == "FreeText" and has_callout):
            raw = annot.vertices
            if raw:
                flat = []
                for v in raw:
                    # Ink returns one list per stroke; everything else points.
                    if v and isinstance(v[0], (list, tuple)):
                        flat.extend(v)
                    else:
                        flat.append(v)
                verts = [to_display_point(page, p[0], p[1]) for p in flat]

        colors = annot.colors or {}
        mk = Markup(
            id=f"p{page_index}.m{len(markups)}",
            page=page_index,
            kind=kind,
            bbox=bbox,
            text=_clean(info.get("content")),
            author=author,
            subject=info.get("subject") or None,
            intent=_name_key(doc, xref, "IT"),
            created=pdf_date_to_iso(info.get("creationDate")),
            modified=pdf_date_to_iso(info.get("modDate")),
            vertices=verts,
            points_at=_points_at(annot, kind, has_callout, verts),
            color=_hex_rgb(colors.get("stroke")) or _hex_rgb(colors.get("fill")),
            xref=xref,
        )
        markups.append(mk)
        xref_to_id[xref] = mk.id
        kind_irt, val_irt = doc.xref_get_key(xref, "IRT")
        if kind_irt == "xref" and val_irt:
            try:
                irt_of[mk.id] = int(val_irt.split()[0])
            except ValueError:
                pass

    by_id = {m.id: m for m in markups}
    for mid, parent_xref in irt_of.items():
        parent_id = xref_to_id.get(parent_xref)
        if parent_id:
            by_id[mid].in_reply_to = parent_id
            by_id[parent_id].replies.append(mid)

    # A callout or arrow whose tip lands inside another markup's box is aimed
    # at that markup — on a real submittal, the contractor's reply callouts
    # point into the reviewer's comment boxes. Explicit geometry, not
    # proximity: the tip must be INSIDE the box. The smallest containing box
    # wins, so a comment inside a large snapshot stamp resolves to the comment.
    # A markup's own reply-linked companions are skipped: Bluebeam ties the
    # highlight cloud a responder draws around the original comment to the
    # response box by /IRT, and the tip lands inside that cloud too — naming
    # it would say "this reply points at its own cloud".
    for m in markups:
        if m.points_at is None:
            continue
        px, py = m.points_at
        best = None
        for other in markups:
            if (other is m or other.in_reply_to == m.id
                    or m.in_reply_to == other.id):
                continue
            x0, y0, x1, y1 = other.bbox
            if x0 <= px <= x1 and y0 <= py <= y1:
                area = (x1 - x0) * (y1 - y0)
                if best is None or area < best[0]:
                    best = (area, other.id)
        if best is not None:
            m.points_to_markup = best[1]
    return markups, cad_lines


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text).lower()


def drop_cad_text_already_in_layer(cad_lines: List[TextLine],
                                   layer_lines: List[TextLine],
                                   min_iou: float = 0.3) -> Tuple[List[TextLine], int]:
    """Remove hidden CAD strings the text layer already carries at the same spot.

    A drawing can mix TrueType lettering (in the text layer) with SHX lettering
    (hidden annotations only); where a string is in both, the text-layer copy
    wins because it has a real reading direction and font. Match = same
    characters ignoring whitespace/case AND overlapping boxes.
    """
    if not cad_lines or not layer_lines:
        return cad_lines, 0
    kept = []
    dropped = 0
    for c in cad_lines:
        key = _norm(c.text)
        dup = any(_norm(l.text) == key and bbox_iou(c.bbox, l.bbox) >= min_iou
                  for l in layer_lines)
        if dup:
            dropped += 1
        else:
            kept.append(c)
    return kept, dropped


#: Longest appearance text kept on a markup. A Bluebeam "Snapshot" stamp
#: copies a whole region of another drawing, text included.
MAX_APPEARANCE_CHARS = 400


def attach_appearance_text(page, page_index: int,
                           markups: List[Markup]) -> None:
    """Give each markup the text its own appearance draws, when that adds anything.

    Text drawn by annotations is excluded from the page's text layer (see
    :func:`planlens.document.pdf_text.extract_text`). Each markup's drawn text
    is read from THAT annotation's appearance alone, so overlapping markups
    cannot swap text. It is kept only when it differs from the markup's comment
    field — a review stamp's wording, or what a snapshot stamp copied — and
    not when a callout merely draws its own comment.
    """
    for m in markups:
        if m.xref is None:
            continue
        try:
            annot = page.load_annot(m.xref)
            drawn = annot.get_text("text") if annot is not None else ""
        except Exception:  # pragma: no cover - malformed appearance
            continue
        drawn = " ".join((drawn or "").split())
        if not drawn or _norm(drawn) == _norm(m.text):
            continue
        if len(drawn) > MAX_APPEARANCE_CHARS:
            drawn = drawn[:MAX_APPEARANCE_CHARS - 3] + "..."
        m.appearance_text = drawn
