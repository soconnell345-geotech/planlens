"""One page's drafting constructs, read from its drawn geometry.

BACKLOG DR-44 (with PL-13): the drawing-geometry layer (:mod:`planlens.ir`)
as a fallback an agent can reach for when reading a drawing's text and
looking at it have not settled a question, never a step of a workflow. It
reads the page's own vector line-work and text (``from_pdf_vector``) and
proposes the constructs a reviewer argues about: dimensions, leaders,
callout bubbles, revision clouds and the title block
(:func:`planlens.ir.queries.find_dimensions` and its siblings).

What it promises:

* every result says what it is measured from (the PDF's drawn geometry, not
  a picture of the page) and what the sheet is: a vector drawing, one whose
  lettering is drawn as lines (its values then come back empty), or mostly a
  picture with some lines over it;
* a page with no drawn geometry at all (a scan) is refused in plain words
  (:class:`NoDrawnGeometry`): it is read by looking instead;
* every point is in the displayed-page frame (:mod:`planlens.document.frame`:
  PDF points, origin top-left, y down), the frame ``render_region`` and
  ``annotate_document`` take, so an item can be looked at or marked without
  a conversion;
* each item is a PROPOSAL found by its shape, with the finder's confidence;
  a length is the drawn length in PDF points, never converted (``measure``
  with a scale converts).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The constructs this reads, in the order a result lists them.
KINDS = ("dimensions", "leaders", "callouts", "revision_clouds",
         "title_block")

#: What every result is measured from.
MEASURED_FROM = ("the PDF's drawn geometry: its own vector lines and text, "
                 "not a picture of the page")

#: The frame of every point in a result.
FRAME = ("PDF points on the page as displayed, origin top-left, y down "
         "(the frame render_region and annotate_document take)")

#: What an item is.
PROPOSAL_NOTE = ("each item is a construct recognised by its shape: exact "
                 "where it is drawn, but confirm one on a render before "
                 "relying on it")

#: Pictures covering at least this share of the page make it a scan or a
#: drawing pasted as an image, whatever lines are drawn over it.
PICTURE_SHARE = 0.5

Point = Tuple[float, float]


class NoDrawnGeometry(ValueError):
    """The page has no drawn geometry to read: a scan, or a picture of a
    drawing. ``hint`` says how it is read instead."""

    def __init__(self, page: int):
        super().__init__(
            f"page {page} has no drawn geometry (no vector lines): it is a "
            "scan or a picture of a drawing, which this tool cannot read")
        self.hint = ("read it by looking (render_region shows a part of it "
                     "large) or with find_like")


def kinds_asked(find: Any) -> Tuple[str, ...]:
    """The constructs ``find`` names (a name or a list of names; empty means
    all), in :data:`KINDS` order; a ValueError names the ones it takes."""
    if find in (None, "", [], ()):
        return KINDS
    names = [find] if isinstance(find, str) else list(find)
    wanted = {str(n).strip().lower() for n in names}
    unknown = sorted(wanted - set(KINDS))
    if unknown:
        raise ValueError(f"find takes {list(KINDS)}; not {unknown}")
    return tuple(k for k in KINDS if k in wanted)


def page_ir(document, page: int):
    """The :class:`planlens.ir.DrawingIR` of ``document``'s page ``page``
    (0-based), read from its vector line-work and text."""
    from planlens.ir import from_pdf_vector
    return from_pdf_vector(content=document.pdf_bytes(), page=int(page))


def _picture_share(fpage) -> float:
    """The share of the page its pictures cover (their areas summed, at most
    1: overlapping tiles count twice, which only matters past the
    threshold)."""
    area = abs(fpage.rect.width * fpage.rect.height)
    if area <= 0:
        return 0.0
    try:
        infos = fpage.get_image_info()
    except Exception:  # noqa: BLE001 - a page MuPDF cannot list has none
        return 0.0
    covered = 0.0
    for info in infos:
        try:
            x0, y0, x1, y1 = (float(v) for v in info.get("bbox"))
        except (TypeError, ValueError):
            continue
        covered += abs(x1 - x0) * abs(y1 - y0)
    return min(1.0, covered / area)


def _pt(fpage, xy: Sequence[float]) -> List[float]:
    from planlens.document.frame import from_ir_point
    x, y = from_ir_point(fpage, float(xy[0]), float(xy[1]))
    return [round(x, 1), round(y, 1)]


def _box(fpage, bbox: Sequence[float]) -> List[float]:
    a = _pt(fpage, (bbox[0], bbox[1]))
    b = _pt(fpage, (bbox[2], bbox[3]))
    return [min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]),
            max(a[1], b[1])]


def _inside(p: Sequence[float], region: Sequence[float]) -> bool:
    return region[0] <= p[0] <= region[2] and region[1] <= p[1] <= region[3]


def _touches(box: Sequence[float], region: Sequence[float]) -> bool:
    return not (box[2] < region[0] or box[0] > region[2]
                or box[3] < region[1] or box[1] > region[3])


def _conf(item: Dict[str, Any]) -> float:
    try:
        return float(item.get("confidence") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _rows(kind: str, ir, fpage) -> List[Tuple[List[Any], Dict[str, Any]]]:
    """``(where, row)`` per construct of ``kind``: ``where`` is the row's
    points (a list of ``[x, y]``) or its box (``[x0, y0, x1, y1]``), what a
    ``bbox`` filter tests."""
    from planlens.ir import queries
    out: List[Tuple[List[Any], Dict[str, Any]]] = []
    if kind == "dimensions":
        for d in sorted(queries.find_dimensions(ir), key=_conf, reverse=True):
            a, b = _pt(fpage, d["end_a_xy"]), _pt(fpage, d["end_b_xy"])
            mid = _pt(fpage, d["midpoint_xy"])
            out.append(([a, b, mid], {
                "value": d.get("text"), "from": a, "to": b,
                "length_pt": round(float(d.get("length") or 0.0), 1),
                "confidence": d.get("confidence")}))
    elif kind == "leaders":
        # a dimension's arrow is not a leader: the dimensions say it
        for d in sorted(queries.find_leaders(ir, exclude_dimensions=True),
                        key=_conf, reverse=True):
            tip, tail = _pt(fpage, d["tip_xy"]), _pt(fpage, d["tail_xy"])
            out.append(([tip, tail], {
                "text": d.get("text"), "tip": tip, "tail": tail,
                "confidence": d.get("confidence")}))
    elif kind == "callouts":
        for d in sorted(queries.find_bubble_callouts(ir), key=_conf,
                        reverse=True):
            c = _pt(fpage, d["center_xy"])
            out.append(([c], {
                "kind": d.get("kind"), "text": d.get("text"), "center": c,
                "radius_pt": round(float(d.get("radius") or 0.0), 1),
                "confidence": d.get("confidence")}))
    elif kind == "revision_clouds":
        # a cloud has a box; a revision delta (the small triangle with the
        # revision's number) has a centre and its text
        for d in sorted(queries.find_revision_clouds(ir), key=_conf,
                        reverse=True):
            if d.get("bbox") is not None:
                box = _box(fpage, d["bbox"])
                out.append((box, {"kind": d.get("kind"), "bbox": box,
                                  "confidence": d.get("confidence")}))
            elif d.get("center_xy") is not None:
                c = _pt(fpage, d["center_xy"])
                out.append(([c], {"kind": d.get("kind"),
                                  "text": d.get("text"), "center": c,
                                  "confidence": d.get("confidence")}))
    elif kind == "title_block":
        for d in sorted(queries.find_title_block(ir), key=_conf,
                        reverse=True):
            box = _box(fpage, d["region_bbox"])
            out.append((box, {
                "bbox": box,
                "texts": [t.get("content") for t in d.get("texts") or ()
                          if t.get("content")],
                "confidence": d.get("confidence")}))
    return out


def _kept(where: List[Any], region: Optional[Sequence[float]]) -> bool:
    if region is None:
        return True
    if where and not isinstance(where[0], list):       # a box
        return _touches(where, region)
    return any(_inside(p, region) for p in where)


def _sheet(page: int, ir, fpage) -> Dict[str, Any]:
    """What the sheet is, or :class:`NoDrawnGeometry`."""
    from planlens.ir import TextItem
    n_text = sum(1 for e in ir.entities if isinstance(e, TextItem))
    n_drawn = len(ir.entities) - n_text
    share = _picture_share(fpage)
    if n_drawn == 0:
        raise NoDrawnGeometry(page)
    if share >= PICTURE_SHARE:
        said = (f"mostly a picture (a scan, or a drawing pasted as an "
                f"image) with {n_drawn} drawn item(s) over it: only those "
                "were read; the picture is read by looking")
    elif n_text == 0:
        said = ("a vector drawing whose lettering is drawn as lines (no "
                "text layer): values and labels that would come from text "
                "are empty; read them on a render")
    else:
        said = ("a vector drawing: its lines and lettering are the PDF's "
                "own, as a CAD plot's are")
    sheet: Dict[str, Any] = {"is": said, "drawn_items": n_drawn,
                             "text_items": n_text}
    if share > 0:
        sheet["picture_share"] = round(share, 2)
    return sheet


def drawing_geometry(document, page: int, find: Any = None,
                     region: Optional[Sequence[float]] = None,
                     ir=None) -> Dict[str, Any]:
    """The constructs ``find`` names (default all, :data:`KINDS`) on
    ``document``'s page ``page`` (0-based), read from its drawn geometry.

    ``region`` (a displayed-frame box) keeps the items that touch it.
    ``ir`` is the page's :func:`page_ir`, when the caller holds it. Raises
    :class:`NoDrawnGeometry` for a page with no drawn geometry, ValueError
    for a ``find`` it does not take."""
    kinds = kinds_asked(find)
    page = int(page)
    fpage = document._doc[page]
    if ir is None:
        ir = page_ir(document, page)
    out: Dict[str, Any] = {
        "page": page, "measured_from": MEASURED_FROM, "frame": FRAME,
        "sheet": _sheet(page, ir, fpage)}
    counts: Dict[str, int] = {}
    unread = {"dimensions": 0, "leaders": 0}
    for kind in kinds:
        rows = [row for where, row in _rows(kind, ir, fpage)
                if _kept(where, region)]
        if kind == "title_block":
            rows = rows[:1]          # the likeliest; one sheet has one
        counts[kind] = len(rows)
        out[kind] = rows
        if kind == "dimensions":
            unread[kind] = sum(1 for r in rows if not r.get("value"))
        elif kind == "leaders":
            unread[kind] = sum(1 for r in rows if not r.get("text"))
    out["counts"] = counts
    notes = [PROPOSAL_NOTE]
    if out["sheet"]["text_items"] and any(unread.values()):
        notes.append(
            f"{unread['dimensions']} dimension(s) and {unread['leaders']} "
            "leader(s) have no text beside them in the text layer: their "
            "lettering may be drawn as lines, or set farther off; read it "
            "on a render")
    if "dimensions" in kinds:
        notes.append("length_pt is the drawn length in PDF points, not "
                     "converted; measure with a scale converts it")
    out["notes"] = notes
    return out


__all__ = ["KINDS", "MEASURED_FROM", "FRAME", "PROPOSAL_NOTE",
           "PICTURE_SHARE", "NoDrawnGeometry", "kinds_asked", "page_ir",
           "drawing_geometry"]
