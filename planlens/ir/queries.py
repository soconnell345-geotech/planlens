"""
Query interface over a :class:`planlens.ir.results.DrawingIR`.

This is the surface an LLM (or the funhouse adapter) calls to request *slices*
of a drawing instead of interpreting pixels: spatial windows, angle bands, text
matches, layer/color groups, nearest-entity lookups, and a couple of geotech
heuristics. Every function takes a ``DrawingIR`` and returns compact, JSON-able
Python (lists/dicts of primitives) — entity *references* (id + a small summary),
never full coordinate dumps. Use ``get_entities(ir, ids)`` to pull exact
coordinates for a shortlist the query narrowed down.

The deterministic extractor owns the coordinates; these queries only slice and
summarize them. Anything labelled a *proposal* (e.g. candidate_ground_surface)
is a heuristic suggestion for the caller to confirm, never an assertion.
"""

from __future__ import annotations

import math
import re
from typing import Any, Dict, List, Optional, Tuple

from planlens.ir.results import (
    Arc, Circle, DrawingIR, Entity, Line, Polyline, Region, TextItem, _r,
)

Point = Tuple[float, float]


# ---------------------------------------------------------------------------
# Compact references
# ---------------------------------------------------------------------------

def _ref(e: Entity) -> Dict[str, Any]:
    """A compact, JSON-able summary of an entity (no full coordinate dump)."""
    d: Dict[str, Any] = {
        "id": e.id,
        "type": e.KIND,
        "source": e.source,
        "confidence": _r(e.confidence, 3),
    }
    if e.layer is not None:
        d["layer"] = e.layer
    if e.color is not None:
        d["color"] = e.color
    if e.bbox is not None:
        d["bbox"] = [_r(v) for v in e.bbox]
    if isinstance(e, Line):
        d["length"] = _r(e.length())
        d["angle_deg"] = _r(e.angle_deg(), 2)
    elif isinstance(e, Polyline):
        d["n_vertices"] = len(e.vertices)
        d["length"] = _r(e.length())
        d["closed"] = bool(e.closed)
    elif isinstance(e, Arc):
        d["radius"] = _r(e.radius)
        d["length"] = _r(e.length())
    elif isinstance(e, Circle):
        d["center"] = [_r(e.center[0]), _r(e.center[1])]
        d["radius"] = _r(e.radius)
    elif isinstance(e, TextItem):
        d["content"] = e.content
        d["position"] = [_r(e.position[0]), _r(e.position[1])]
    elif isinstance(e, Region):
        d["n_vertices"] = len(e.boundary)
        d["area"] = _r(e.area())
    return d


def _refs(entities) -> List[Dict[str, Any]]:
    return [_ref(e) for e in entities]


# ---------------------------------------------------------------------------
# Geometry helpers
# ---------------------------------------------------------------------------

def _iter_segments(ir: DrawingIR):
    """Yield (entity, seg_index, p0, p1) for every Line/Polyline segment."""
    for e in ir.entities:
        if isinstance(e, Line):
            yield e, 0, tuple(e.start), tuple(e.end)
        elif isinstance(e, Polyline):
            pts = e.vertices
            for i in range(len(pts) - 1):
                yield e, i, tuple(pts[i]), tuple(pts[i + 1])
            if e.closed and len(pts) > 2:
                yield e, len(pts) - 1, tuple(pts[-1]), tuple(pts[0])


def _seg_angle(p0: Point, p1: Point) -> float:
    """Segment orientation folded to [0, 180)."""
    a = math.degrees(math.atan2(p1[1] - p0[1], p1[0] - p0[0])) % 180.0
    return a


def _bbox_intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _bbox_contains(outer, inner) -> bool:
    return (outer[0] <= inner[0] and outer[1] <= inner[1]
            and outer[2] >= inner[2] and outer[3] >= inner[3])


def _point_seg_dist(px: float, py: float, ax: float, ay: float,
                    bx: float, by: float) -> float:
    """Shortest distance from (px, py) to segment (a)-(b)."""
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy)


def _point_to_entity_dist(x: float, y: float, e: Entity) -> float:
    """Shortest distance from (x, y) to an entity's actual geometry.

    Point-to-segment for lines/polylines/region edges, point-to-ring for
    circles, insertion-point distance for text, sampled distance for arcs.
    """
    if isinstance(e, Line):
        return _point_seg_dist(x, y, e.start[0], e.start[1],
                               e.end[0], e.end[1])
    if isinstance(e, Polyline):
        pts = e.vertices
        if len(pts) == 1:
            return math.hypot(x - pts[0][0], y - pts[0][1])
        segs = list(zip(pts, pts[1:]))
        if e.closed and len(pts) > 2:
            segs.append((pts[-1], pts[0]))
        return min(_point_seg_dist(x, y, a[0], a[1], b[0], b[1])
                   for a, b in segs) if segs else math.inf
    if isinstance(e, Region):
        pts = e.boundary
        if len(pts) < 2:
            return math.inf
        segs = list(zip(pts, pts[1:] + pts[:1]))
        return min(_point_seg_dist(x, y, a[0], a[1], b[0], b[1])
                   for a, b in segs)
    if isinstance(e, Circle):
        return abs(math.hypot(x - e.center[0], y - e.center[1]) - e.radius)
    if isinstance(e, TextItem):
        return math.hypot(x - e.position[0], y - e.position[1])
    pts = e.points()  # arcs and any fallback: nearest sampled point
    if not pts:
        return math.inf
    return min(math.hypot(px - x, py - y) for px, py in pts)


# ---------------------------------------------------------------------------
# Spatial queries
# ---------------------------------------------------------------------------

def entities_in_bbox(ir: DrawingIR, x_min: float, y_min: float,
                     x_max: float, y_max: float,
                     mode: str = "intersect",
                     entity_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Entities whose bbox intersects (or is contained in) a query window.

    ``mode``: "intersect" (default) or "contain". ``entity_type`` optionally
    restricts to one kind ("line"/"polyline"/"arc"/"circle"/"text"/"region").
    """
    win = (min(x_min, x_max), min(y_min, y_max),
           max(x_min, x_max), max(y_min, y_max))
    hits = []
    for e in ir.entities:
        if entity_type and e.KIND != entity_type:
            continue
        if e.bbox is None:
            continue
        ok = (_bbox_contains(win, e.bbox) if mode == "contain"
              else _bbox_intersects(win, e.bbox))
        if ok:
            hits.append(e)
    return _refs(hits)


def nearest_entity(ir: DrawingIR, x: float, y: float,
                   entity_type: Optional[str] = None,
                   k: int = 1) -> List[Dict[str, Any]]:
    """The ``k`` entities nearest to point (x, y), closest first.

    Each result carries a ``distance`` field (shortest distance to the entity's
    geometry; 0 if the point lies on it).
    """
    scored = []
    for e in ir.entities:
        if entity_type and e.KIND != entity_type:
            continue
        scored.append((_point_to_entity_dist(x, y, e), e))
    scored.sort(key=lambda t: t[0])
    out = []
    for dist, e in scored[:max(1, k)]:
        ref = _ref(e)
        ref["distance"] = _r(dist)
        out.append(ref)
    return out


# ---------------------------------------------------------------------------
# Endpoint-anchored queries (B4) — "what TERMINATES here?"
# ---------------------------------------------------------------------------

def _arc_endpoint(e: Arc, angle_deg: float) -> Point:
    a = math.radians(angle_deg)
    return (e.center[0] + e.radius * math.cos(a),
            e.center[1] + e.radius * math.sin(a))


def _entity_endpoints(e: Entity) -> List[Tuple[str, Point]]:
    """The named terminal points of an entity's geometry, if it has any.

    ``[("start", pt), ("end", pt)]`` for Line/Polyline/Arc/Region (Polyline and
    Region use the first/last vertex of their vertex/boundary list — labelled
    "start"/"end" even when ``closed`` is True; a closed ring's endpoints are
    less meaningful, but the labels stay consistent so callers can treat every
    result uniformly). Circle and TextItem have no endpoints (a circle is a
    closed loop with no ends; text has an insertion point, not a terminus) and
    return ``[]``.
    """
    if isinstance(e, Line):
        return [("start", tuple(e.start)), ("end", tuple(e.end))]
    if isinstance(e, Polyline):
        if not e.vertices:
            return []
        return [("start", tuple(e.vertices[0])), ("end", tuple(e.vertices[-1]))]
    if isinstance(e, Arc):
        return [("start", _arc_endpoint(e, e.start_angle)),
                ("end", _arc_endpoint(e, e.end_angle))]
    if isinstance(e, Region):
        if not e.boundary:
            return []
        return [("start", tuple(e.boundary[0])), ("end", tuple(e.boundary[-1]))]
    return []


def entities_ending_near(ir: DrawingIR, point: Point, radius: float,
                         entity_types: Optional[List[str]] = None
                         ) -> List[Dict[str, Any]]:
    """Entities with an ENDPOINT (not just bbox/full-geometry) within radius.

    Unlike :func:`entities_in_bbox`/:func:`nearest_entity` (bbox or nearest-
    point-on-geometry), this checks only an entity's TERMINAL points — the
    natural query for "what line/polyline/arc TERMINATES here" (a leader's
    arrow tip, a callout's tail, a dimension's end). ``entity_types``
    optionally restricts to a set of KINDs (e.g. ``["line", "polyline"]``);
    Circle and TextItem never match (see :func:`_entity_endpoints`).

    Returns one entry per matching ``(entity, endpoint)`` pair, closest first:
    the usual entity reference plus ``end`` ("start"|"end" — which endpoint
    matched), ``end_point`` (the matched endpoint, exact coordinates),
    ``other_end`` (the entity's OTHER endpoint — e.g. the far end a leader
    points FROM), and ``distance``.
    """
    px, py = point
    scored = []
    for e in ir.entities:
        if entity_types and e.KIND not in entity_types:
            continue
        endpoints = _entity_endpoints(e)
        if len(endpoints) < 2:
            continue
        for i, (label, pt) in enumerate(endpoints):
            d = math.hypot(px - pt[0], py - pt[1])
            if d > radius:
                continue
            _, other_pt = endpoints[1 - i]
            ref = _ref(e)
            ref["end"] = label
            ref["end_point"] = [_r(pt[0]), _r(pt[1])]
            ref["other_end"] = [_r(other_pt[0]), _r(other_pt[1])]
            ref["distance"] = _r(d)
            scored.append((d, ref))
    scored.sort(key=lambda t: t[0])
    return [ref for _, ref in scored]


# ---------------------------------------------------------------------------
# Angle / length queries (operate on Line + Polyline segments)
# ---------------------------------------------------------------------------

def lines_by_angle(ir: DrawingIR, min_deg: float, max_deg: float
                   ) -> List[Dict[str, Any]]:
    """Line/polyline segments whose orientation (folded to [0,180)) is in band.

    Returns one entry per matching segment: ``{entity_id, type, seg_index,
    angle_deg, start, end, length}``.
    """
    lo, hi = min_deg % 180.0, max_deg % 180.0
    out = []
    for e, i, p0, p1 in _iter_segments(ir):
        ang = _seg_angle(p0, p1)
        inside = (lo <= ang <= hi) if lo <= hi else (ang >= lo or ang <= hi)
        if inside:
            out.append({
                "entity_id": e.id, "type": e.KIND, "seg_index": i,
                "angle_deg": _r(ang, 2),
                "start": [_r(p0[0]), _r(p0[1])],
                "end": [_r(p1[0]), _r(p1[1])],
                "length": _r(math.hypot(p1[0] - p0[0], p1[1] - p0[1])),
            })
    return out


def horizontal_lines(ir: DrawingIR, tol_deg: float = 2.0) -> List[Dict[str, Any]]:
    """Segments within ``tol_deg`` of horizontal (0 deg)."""
    out = []
    for e, i, p0, p1 in _iter_segments(ir):
        ang = _seg_angle(p0, p1)
        dev = min(ang, 180.0 - ang)
        if dev <= tol_deg:
            out.append({
                "entity_id": e.id, "type": e.KIND, "seg_index": i,
                "angle_deg": _r(ang, 2),
                "start": [_r(p0[0]), _r(p0[1])], "end": [_r(p1[0]), _r(p1[1])],
                "length": _r(math.hypot(p1[0] - p0[0], p1[1] - p0[1])),
            })
    return out


def vertical_lines(ir: DrawingIR, tol_deg: float = 2.0) -> List[Dict[str, Any]]:
    """Segments within ``tol_deg`` of vertical (90 deg)."""
    out = []
    for e, i, p0, p1 in _iter_segments(ir):
        ang = _seg_angle(p0, p1)
        if abs(ang - 90.0) <= tol_deg:
            out.append({
                "entity_id": e.id, "type": e.KIND, "seg_index": i,
                "angle_deg": _r(ang, 2),
                "start": [_r(p0[0]), _r(p0[1])], "end": [_r(p1[0]), _r(p1[1])],
                "length": _r(math.hypot(p1[0] - p0[0], p1[1] - p0[1])),
            })
    return out


def polylines_longer_than(ir: DrawingIR, min_length: float
                          ) -> List[Dict[str, Any]]:
    """Polylines whose total path length >= ``min_length`` (longest first)."""
    hits = [e for e in ir.entities
            if isinstance(e, Polyline) and e.length() >= min_length]
    hits.sort(key=lambda e: e.length(), reverse=True)
    return _refs(hits)


# ---------------------------------------------------------------------------
# Text queries
# ---------------------------------------------------------------------------

def text_items(ir: DrawingIR, pattern: Optional[str] = None
               ) -> List[Dict[str, Any]]:
    """Text items, optionally filtered by a regex ``pattern`` (case-insensitive).

    If ``pattern`` is not a valid regex it is treated as a literal substring.
    """
    texts = [e for e in ir.entities if isinstance(e, TextItem)]
    if pattern:
        try:
            rx = re.compile(pattern, re.IGNORECASE)
            texts = [e for e in texts if rx.search(e.content)]
        except re.error:
            low = pattern.lower()
            texts = [e for e in texts if low in e.content.lower()]
    return _refs(texts)


def text_near(ir: DrawingIR, entity_id: str, radius: float
              ) -> List[Dict[str, Any]]:
    """Text items whose insertion point is within ``radius`` of an entity.

    Distance is measured from the text insertion point to the target entity
    (0 if the point lies inside the target's bbox). Sorted nearest first.
    """
    target = ir.by_id(entity_id)
    if target is None:
        return [{"error": f"Unknown entity_id '{entity_id}'"}]
    out = []
    for e in ir.entities:
        if not isinstance(e, TextItem):
            continue
        d = _point_to_entity_dist(e.position[0], e.position[1], target)
        if d <= radius:
            ref = _ref(e)
            ref["distance"] = _r(d)
            out.append((d, ref))
    out.sort(key=lambda t: t[0])
    return [ref for _, ref in out]


def text_anchored_geometry(ir: DrawingIR, pattern: str,
                           radius: Optional[float] = None,
                           entity_types: Optional[List[str]] = None
                           ) -> List[Dict[str, Any]]:
    """Text matches plus any geometry that TERMINATES at/near them.

    Composes :func:`text_items` (find text matching ``pattern``) with
    :func:`entities_ending_near` around each match's insertion point — the
    "find text X -> the leader/line terminating there -> the far end it
    points at" primitive: search for any tail label (the pattern is always a
    runtime parameter — nothing here is specific to one string), read off the
    connected geometry's far endpoint as the callout's target location, then
    hand that location to a vision zoom for a description.

    ``radius`` defaults to a PER-MATCH value derived from that text item's
    own height (``3 * height`` in the IR's coordinate units) when not given,
    since text size is the natural proxy for "how close counts as touching
    this label" and it scales correctly in BOTH page space (points) and model
    space (meters). Only when the text height is unknown/zero does a fixed
    5.0-unit fallback apply (a fixed floor must not bind when height is
    known — 5.0 is a few text-heights in page points but a huge reach in
    model-space meters).

    Returns one entry per text match: ``{text, anchor, connected,
    points_at, proposal_only: True}``. ``connected`` is the full
    :func:`entities_ending_near` result (possibly empty); ``points_at`` is the
    ``other_end`` of the CLOSEST connected entity (the far endpoint — where
    the geometry is aimed FROM the text), or ``None`` if nothing connects.
    Geometric adjacency alone does not prove a leader relationship (a
    coincidental nearby line-end also matches) — this is a PROPOSAL, confirm
    against the drawing (e.g. via a vision zoom on ``points_at``) before
    treating it as fact.
    """
    matches = text_items(ir, pattern)
    out = []
    for m in matches:
        e = ir.by_id(m["id"])
        anchor = tuple(e.position) if isinstance(e, TextItem) else None
        if anchor is None:
            out.append({"text": m, "anchor": None, "connected": [],
                        "points_at": None, "proposal_only": True})
            continue
        h = e.height or 0.0
        r = radius if radius is not None else (3.0 * h if h > 0 else 5.0)
        connected = entities_ending_near(ir, anchor, r, entity_types=entity_types)
        points_at = connected[0]["other_end"] if connected else None
        out.append({
            "text": m,
            "anchor": [_r(anchor[0]), _r(anchor[1])],
            "search_radius": _r(r),
            "connected": connected,
            "points_at": points_at,
            "proposal_only": True,
        })
    return out


# ---------------------------------------------------------------------------
# Layer / color groups
# ---------------------------------------------------------------------------

def entities_on_layer(ir: DrawingIR, layer: str) -> List[Dict[str, Any]]:
    """Entities on a given layer (exact match)."""
    return _refs([e for e in ir.entities if e.layer == layer])


def entities_by_color(ir: DrawingIR, color: str) -> List[Dict[str, Any]]:
    """Entities of a given color (case-insensitive exact match)."""
    low = color.lower()
    return _refs([e for e in ir.entities
                  if e.color is not None and e.color.lower() == low])


# ---------------------------------------------------------------------------
# Selective coordinate retrieval
# ---------------------------------------------------------------------------

def get_entities(ir: DrawingIR, ids: List[str]) -> List[Dict[str, Any]]:
    """Full ``to_dict`` (exact coordinates) for a shortlist of entity ids."""
    out = []
    for eid in ids:
        e = ir.by_id(eid)
        if e is not None:
            out.append(e.to_dict())
        else:
            out.append({"id": eid, "error": "not found"})
    return out


# ---------------------------------------------------------------------------
# Heuristics (PROPOSALS — caller confirms)
# ---------------------------------------------------------------------------

def candidate_ground_surface(ir: DrawingIR) -> Dict[str, Any]:
    """Propose the entity most likely to be the ground surface.

    Heuristic ONLY (never an assertion): among Line/Polyline entities, pick the
    one with the widest horizontal (x) extent — a ground surface typically runs
    left-to-right across the section. Ties broken toward the upper (higher-y)
    candidate. The caller/LLM must confirm against the drawing; soil properties
    never come from a drawing.
    """
    paths = [e for e in ir.entities if isinstance(e, (Line, Polyline))]
    if not paths:
        return {"candidate": None,
                "note": "No line/polyline entities to propose from.",
                "proposal_only": True}

    page_w = ir.width or 0.0
    best = None
    best_score = None
    for e in paths:
        bb = e.bbox
        if bb is None:
            continue
        width = bb[2] - bb[0]
        mid_y = 0.5 * (bb[1] + bb[3])
        score = (width, mid_y)  # widest, then highest
        if best_score is None or score > best_score:
            best_score, best = score, e

    if best is None:
        return {"candidate": None, "note": "No usable geometry.",
                "proposal_only": True}

    bb = best.bbox
    width = bb[2] - bb[0]
    coverage = (width / page_w) if page_w > 0 else None
    ref = _ref(best)
    return {
        "candidate": ref,
        "x_range": [_r(bb[0]), _r(bb[2])],
        "y_range": [_r(bb[1]), _r(bb[3])],
        "width": _r(width),
        "page_width_coverage": _r(coverage, 3) if coverage is not None else None,
        "note": ("Longest left-to-right path — a PROPOSAL for the ground "
                 "surface. Confirm against the drawing before use."),
        "proposal_only": True,
    }


def _bbox_diag(bbox) -> float:
    return math.hypot(bbox[2] - bbox[0], bbox[3] - bbox[1])


class _EndpointGrid:
    """Uniform-grid index over entity ENDPOINTS for radius lookups.

    The composition heuristics ask "what terminates near this point?" once
    per candidate; a linear scan makes that O(candidates x entities), which
    hits a wall on dense real sheets (~10k entities on a Mecklenburg
    standard detail). The grid keeps each lookup to the 3x3 neighborhood of
    cells around the query point. Cell size = the largest radius the caller
    intends to query (queries beyond it fall back to widening the cell
    scan, still correct).
    """

    def __init__(self, ir: DrawingIR, cell: float,
                 entity_types: Optional[List[str]] = None):
        self.cell = max(cell, 1e-9)
        self.cells: Dict[Tuple[int, int], list] = {}
        for e in ir.entities:
            if entity_types and e.KIND not in entity_types:
                continue
            endpoints = _entity_endpoints(e)
            if len(endpoints) < 2:
                continue
            for i, (label, pt) in enumerate(endpoints):
                other = endpoints[1 - i][1]
                key = (int(pt[0] // self.cell), int(pt[1] // self.cell))
                self.cells.setdefault(key, []).append((e, label, pt, other))

    def near(self, point: Point, radius: float):
        """Yield (entity, end_label, end_point, other_end, distance) within
        radius, unsorted."""
        px, py = point
        reach = max(1, int(math.ceil(radius / self.cell)))
        cx, cy = int(px // self.cell), int(py // self.cell)
        for gx in range(cx - reach, cx + reach + 1):
            for gy in range(cy - reach, cy + reach + 1):
                for e, label, pt, other in self.cells.get((gx, gy), ()):
                    d = math.hypot(px - pt[0], py - pt[1])
                    if d <= radius:
                        yield e, label, pt, other, d


def _centroid(pts: List[Point]) -> Point:
    n = len(pts)
    return (sum(p[0] for p in pts) / n, sum(p[1] for p in pts) / n)


def _unit_vec(dx: float, dy: float) -> Point:
    m = math.hypot(dx, dy)
    return (dx / m, dy / m) if m > 1e-12 else (0.0, 0.0)


def _default_max_arrowhead_size(ir: DrawingIR) -> float:
    """~25% of the drawing's typical (median) open shaft-segment length.

    Scaled off the drawing's own line-work rather than its overall bbox: the
    bbox can be inflated by unrelated far-away content (a title block, a
    second detail), which would make a bbox-fraction threshold too permissive
    or too tight depending on what else is on the sheet. The median length of
    Line / open-Polyline entities is a steadier proxy for "how big is a
    typical shaft here" — an arrowhead is normally a small fraction of that.
    Falls back to a bbox-diagonal fraction (drawings with no line-work at
    all), then a fixed floor.
    """
    lengths = [e.length() for e in ir.entities
              if (isinstance(e, Line)
                  or (isinstance(e, Polyline) and not e.closed))
              and e.length() > 0]
    # Page-diagonal floor: on SHX-plotted sheets (lettering stroked as
    # thousands of tiny glyph segments) the median shaft length collapses to
    # glyph-stroke scale and would exclude every real arrowhead — verified
    # against the Mecklenburg ground-truth plots (median segment 2.2 pt on a
    # sheet whose real arrowheads are ~7 pt). 1% of the page diagonal keeps
    # the threshold at plausible plotted-arrowhead scale regardless of how
    # much lettering floods the statistics.
    diag_floor = 0.0
    bb = ir.bbox()
    if bb is not None:
        diag_floor = _bbox_diag(bb) * 0.01
    if lengths:
        lengths.sort()
        median = lengths[len(lengths) // 2]
        return max(median * 0.25, diag_floor, 1.0)
    if diag_floor > 0:
        return max(diag_floor * 3.0, 1.0)
    return 20.0


def _arrowhead_candidates(ir: DrawingIR, max_arrowhead_size: float):
    """Yield (entity, vertices) for small closed 3-5-vertex Polyline/Region.

    This is what a filled-triangle arrowhead becomes on ingest — verified
    empirically against ``from_pdf_vector`` (see the module docstring below
    :func:`find_leaders`): PDF-vector ingest never emits a ``Region`` (only
    Line/Polyline/TextItem), so a filled triangle arrives as a CLOSED
    Polyline with 3 vertices and a small bbox. A DXF HATCH-based arrowhead
    can arrive as a small ``Region`` instead, so both are checked (keeps this
    usable once B1's DXF LEADER ingest lands).
    """
    for e in ir.entities:
        size_cap = max_arrowhead_size
        if isinstance(e, Polyline) and e.closed and 3 <= len(e.vertices) <= 5:
            verts = e.vertices
        elif isinstance(e, Region) and 3 <= len(e.boundary) <= 5:
            verts = e.boundary
        elif isinstance(e, Polyline) and not e.closed and len(e.vertices) == 3:
            # OPEN 3-vertex chain: real plotters omit a WHOLE EDGE of the
            # arrow triangle, not just a closing sliver — verified on the
            # Mecklenburg ground-truth plots (2026-09-05) in BOTH flavors:
            # native DIMENSION arrowheads there are [base-corner,
            # base-corner, apex] with a LEG missing (gap = 0.75x the drawn
            # path), while the same sheet's LEADER arrowheads are narrow
            # chevrons [barb, apex, barb] with the BASE missing. The old
            # closing-gap rule accepted only the chevron flavor — the root
            # cause of dimension recall 1/16. SHX lettering and stipple
            # texture stroke thousands of 3-vertex V/L chains, so accepting
            # every near-triangle floods composition with junk (measured
            # 68 -> 1477 leader proposals on one sheet); instead the
            # implied triangle must LOOK like a plotted arrowhead, gap
            # placement free: sorted edges a <= b <= c with near-equal legs
            # (c - b <= 0.2c), a slender base (a <= 0.55c — a tip angle up
            # to ~32 deg; every measured real arrow is 0.33), and
            # arrowhead-scale SIZE (bbox diagonal >= 0.5x
            # ``max_arrowhead_size`` — measured real arrows sit at
            # 0.75-1.0x while stipple/glyph junk is overwhelmingly
            # smaller: 1160 of 1311 junk chains on the worst sheet fell
            # below half scale).
            verts = e.vertices
            pts3 = [tuple(p) for p in verts]
            edges = sorted([
                math.hypot(pts3[0][0] - pts3[1][0], pts3[0][1] - pts3[1][1]),
                math.hypot(pts3[1][0] - pts3[2][0], pts3[1][1] - pts3[2][1]),
                math.hypot(pts3[0][0] - pts3[2][0], pts3[0][1] - pts3[2][1]),
            ])
            a3, b3, c3 = edges
            if c3 <= 0 or a3 > 0.55 * c3 or (c3 - b3) > 0.2 * c3:
                continue
            if (e.bbox is None
                    or _bbox_diag(e.bbox) < 0.5 * max_arrowhead_size):
                continue
            # The shape gate above carries far more discrimination than
            # "small closed polyline", so this class earns a looser SIZE
            # cap: the sheet-statistic scale estimate ran ~20% under the
            # real plotted arrows on one validation sheet (12.3 pt arrows
            # vs a 10.0 pt estimate), which silently rejected every leader
            # arrowhead there.
            size_cap = 1.5 * max_arrowhead_size
        elif isinstance(e, Polyline) and not e.closed and len(e.vertices) == 4:
            # OPEN 4-vertex near-ring: a filled arrowhead's outline drawn
            # without repeating the first point (the same reason a PDF "re"
            # rectangle ingests as an open 4-corner polyline) — accept only
            # when the implied closing gap is small relative to the drawn
            # path; a glyph zigzag has a large gap and is rejected.
            verts = e.vertices
            per = e.length()
            gap = math.hypot(verts[0][0] - verts[-1][0],
                             verts[0][1] - verts[-1][1])
            if per <= 0 or gap > 0.35 * per:
                continue
        else:
            continue
        if e.bbox is None or _bbox_diag(e.bbox) > size_cap:
            continue
        pts = [tuple(p) for p in verts]
        # Non-degeneracy: a real arrowhead encloses area (an equilateral
        # triangle scores ~0.048 area/perimeter^2, our reference arrowhead
        # ~0.045); a flat sliver — a dash artifact or a near-collinear
        # glyph stroke whose ends happen to sit close — scores ~0.
        s = 0.0
        for a, b in zip(pts, pts[1:] + pts[:1]):
            s += a[0] * b[1] - b[0] * a[1]
        area = abs(s) * 0.5
        per = sum(math.hypot(b[0] - a[0], b[1] - a[1])
                  for a, b in zip(pts, pts[1:] + pts[:1]))
        if per <= 0 or area / (per * per) < 0.02:
            continue
        yield e, pts


class _ClusterCand:
    """A fill/stroke-cluster arrowhead candidate (not a single entity).

    Real agency plots frequently render a filled arrowhead as a CLUSTER of
    tiny strokes — solid-fill micro-dots (~0.06 pt segments) or a hatch fan
    of short parallel strokes — rather than one closed triangle. Verified on
    the Mecklenburg ground-truth plots (2026-09-04): sheet 21.01's native
    LEADER tips carry clusters of 3-25 line fragments of 0.06-3 pt within a
    3-7 pt span, while sheet 10.31A's multileader arrowheads ARE closed
    triangles. Both representations feed the same composition scoring.
    """

    __slots__ = ("id", "member_ids", "bbox")

    def __init__(self, rep_id: str, member_ids: List[str], bbox):
        self.id = "cluster:" + rep_id
        self.member_ids = member_ids
        self.bbox = bbox


def _pca_axis(pts: List[Point]) -> Tuple[Point, float]:
    """(unit major axis, elongation ratio major/minor) of a point set."""
    n = len(pts)
    if n < 2:
        return (1.0, 0.0), 1.0
    mx = sum(p[0] for p in pts) / n
    my = sum(p[1] for p in pts) / n
    sxx = sum((p[0] - mx) ** 2 for p in pts) / n
    syy = sum((p[1] - my) ** 2 for p in pts) / n
    sxy = sum((p[0] - mx) * (p[1] - my) for p in pts) / n
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    disc = max(0.0, tr * tr * 0.25 - det)
    l1 = tr * 0.5 + math.sqrt(disc)
    l2 = max(tr * 0.5 - math.sqrt(disc), 1e-12)
    if abs(sxy) > 1e-12:
        ax = _unit_vec(l1 - syy, sxy)
    elif sxx >= syy:
        ax = (1.0, 0.0)
    else:
        ax = (0.0, 1.0)
    return ax, math.sqrt(l1 / l2)


def _cluster_arrowhead_candidates(ir: DrawingIR, max_arrowhead_size: float,
                                  min_shaft_length: float):
    """Yield (cand, verts, "fill_cluster") for stroke-cluster arrowheads.

    Shaft-endpoint-driven by design: rather than clustering every tiny
    fragment on the sheet (SHX lettering strokes thousands of them — 3,417
    tiny segments on ground-truth sheet 21.01), only the neighborhoods of
    LONG line/polyline endpoints are examined, because an arrowhead exists
    only at the end of a shaft. Gates, each empirically motivated:

    - **membership**: >= 3 small open fragments (length and bbox diagonal
      <= 0.6 x max_arrowhead_size) centered within 0.4 x max_arrowhead_size
      (the core radius) of the shaft endpoint;
    - **density spike** (the stipple/glyph guard): core fragment density
      must be >= 2x the local background density in the surrounding annulus
      (0.4x .. 1.0x max_arrowhead_size) — see the inline comment;
    - **anchoring**: cluster centroid within 0.6 x max_arrowhead_size of the
      endpoint (the arrow straddles its tip).

    ``verts`` are member bbox centers (the same role triangle vertices play
    for centroid/apex math downstream). Deduplicated by representative
    (lexicographically smallest) member id, so a tip shared by two shafts
    yields one candidate.
    """
    small_cap = 0.6 * max_arrowhead_size
    small = []
    for e in ir.entities:
        if e.KIND not in ("line", "polyline"):
            continue
        if isinstance(e, Polyline) and e.closed:
            continue
        b = e.bbox
        if b is None or _bbox_diag(b) > small_cap:
            continue
        if e.length() > small_cap:
            continue
        small.append((e, (0.5 * (b[0] + b[2]), 0.5 * (b[1] + b[3]))))
    if not small:
        return

    cell = max(max_arrowhead_size, 1e-9)
    cells: Dict[Tuple[int, int], list] = {}
    for e, c in small:
        cells.setdefault((int(c[0] // cell), int(c[1] // cell)), []).append(
            (e, c))

    def _near(pt, radius):
        cx, cy = int(pt[0] // cell), int(pt[1] // cell)
        reach = int(math.ceil(radius / cell))
        out = []
        for gx in range(cx - reach, cx + reach + 1):
            for gy in range(cy - reach, cy + reach + 1):
                for e, c in cells.get((gx, gy), ()):
                    if math.hypot(c[0] - pt[0], c[1] - pt[1]) <= radius:
                        out.append((e, c))
        return out

    r_core = 0.4 * max_arrowhead_size
    r_out = 1.0 * max_arrowhead_size
    area_core = math.pi * r_core * r_core
    area_ann = math.pi * (r_out * r_out - r_core * r_core)
    seen: set = set()
    for shaft in ir.entities:
        if shaft.KIND not in ("line", "polyline"):
            continue
        if isinstance(shaft, Polyline) and shaft.closed:
            continue
        if shaft.length() < min_shaft_length:
            continue
        pts = shaft.points()
        if len(pts) < 2:
            continue
        for tip in (tuple(pts[0]), tuple(pts[-1])):
            members = _near(tip, r_core)
            if len(members) < 3:
                continue
            # DENSITY-SPIKE gate (the stipple/glyph guard): an arrowhead is
            # a local fragment-density spike AT a shaft endpoint. Uniform
            # stipple texture and running SHX lettering carry comparable
            # density in the surrounding annulus and are rejected; a clean
            # background (no annulus fragments) passes outright. Verified
            # against ground-truth sheet 21.01, whose arrowheads sit INSIDE
            # stippled regions — an absolute isolation cap rejects every
            # real tip there, while background stipple holds the core/
            # annulus density ratio near 1.
            wider = _near(tip, r_out)
            n_ann = len(wider) - len(members)
            d_core = len(members) / area_core
            d_ann = n_ann / area_ann
            if d_ann > 0 and d_core / d_ann < 2.0:
                continue
            xs0 = [c for _, c in members]
            cx = sum(p[0] for p in xs0) / len(xs0)
            cy = sum(p[1] for p in xs0) / len(xs0)
            if math.hypot(cx - tip[0], cy - tip[1]) > 0.6 * max_arrowhead_size:
                continue
            bbs = [e.bbox for e, _ in members]
            union = (min(b[0] for b in bbs), min(b[1] for b in bbs),
                     max(b[2] for b in bbs), max(b[3] for b in bbs))
            # ASPECT gate (the running-lettering guard): an arrowhead's
            # fragment cloud is roughly as wide as it is long (dot blob,
            # hatch fan), while SHX text passing through the core is a
            # high-aspect ribbon (a 30 x 2 pt strip of glyph strokes) that
            # the density gate alone cannot always reject.
            uw = max(union[2] - union[0], 1e-9)
            uh = max(union[3] - union[1], 1e-9)
            if max(uw, uh) / min(uw, uh) > 6.0:
                continue
            rep = min(e.id for e, _ in members)
            if rep in seen:
                continue
            seen.add(rep)
            cand = _ClusterCand(rep, [e.id for e, _ in members], union)
            yield cand, [c for _, c in members], "fill_cluster"


def _arrowhead_candidates_all(ir: DrawingIR, max_arrowhead_size: float,
                              min_shaft_length: float):
    """Both arrowhead representations, tagged: (cand, verts, kind).

    ``kind`` is ``"triangle"`` (closed/open small 3-5-gon — the Phase-1/2
    model) or ``"fill_cluster"`` (stroke-cluster — the Phase-3 leg). The
    composition functions score both identically and record the kind in
    each proposal's evidence.
    """
    for cand, verts in _arrowhead_candidates(ir, max_arrowhead_size):
        yield cand, verts, "triangle"
    yield from _cluster_arrowhead_candidates(ir, max_arrowhead_size,
                                             min_shaft_length)


def _triangle_alignment(verts: List[Point], shaft_dir: Point
                        ) -> Tuple[float, float]:
    """(score, angle_deg): best fold-blind vertex-axis alignment vs shaft.

    For each vertex, take the direction from the centroid of the OTHER
    vertices to it (the axis a true apex defines) and score the sign-blind
    |cos| against the shaft's terminal direction; the best vertex wins.
    Sign-blind and best-vertex on purpose: a leader drawn to the arrow APEX
    and a multileader whose shaft stops at the arrow BASE (verified style
    on ground-truth sheet 10.31A) put the shaft endpoint at opposite ends
    of the same triangle — apex-nearest-endpoint selection misreads the
    base-anchored style and scored those real arrowheads ~0.1.
    """
    best, best_deg = 0.0, 90.0
    for v in verts:
        others = [p for p in verts if p != v]
        if not others:
            continue
        c = _centroid(others)
        d = _unit_vec(v[0] - c[0], v[1] - c[1])
        fold = _fold_alignment(d, shaft_dir)
        if fold > best:
            best = fold
            best_deg = math.degrees(math.acos(max(-1.0, min(1.0, fold))))
    return best, round(best_deg, 1)


def _cluster_alignment(verts: List[Point], shaft_dir: Point
                       ) -> Tuple[float, float]:
    """(score, angle_deg) for a fill-cluster arrowhead vs the shaft axis.

    An elongated cluster (a hatch-fan or streak of dots) should lie along
    the shaft; alignment is the sign-blind |cos| of its PCA major axis vs
    the shaft's terminal direction. A near-isotropic dot blob carries no
    direction information, so it scores a NEUTRAL 0.7 — neither rewarded
    nor punished for shape (documented; keeps dot-style arrowheads viable
    without inflating glyph blobs, which the isolation gate already drops).
    """
    axis, elong = _pca_axis(verts)
    if elong < 1.6:
        return 0.7, -1.0
    fold = _fold_alignment(axis, shaft_dir)
    return fold, round(math.degrees(math.acos(max(-1.0, min(1.0, fold)))), 1)


def _ending_near_from_grid(grid: "_EndpointGrid", point: Point,
                           radius: float) -> List[Dict[str, Any]]:
    """Same result shape/order as :func:`entities_ending_near`, via a
    prebuilt :class:`_EndpointGrid` (the fast path for composition loops)."""
    scored = []
    for e, label, pt, other, d in grid.near(point, radius):
        ref = _ref(e)
        ref["end"] = label
        ref["end_point"] = [_r(pt[0]), _r(pt[1])]
        ref["other_end"] = [_r(other[0]), _r(other[1])]
        ref["distance"] = _r(d)
        scored.append((d, ref))
    scored.sort(key=lambda t: t[0])
    return [ref for _, ref in scored]


def _alignment_score(u: Point, v: Point) -> Tuple[float, float]:
    """(score, angle_deg) — 1.0 at 0 deg (parallel), 0.0 at >=90 deg apart."""
    dot = max(-1.0, min(1.0, u[0] * v[0] + u[1] * v[1]))
    angle = math.degrees(math.acos(dot))
    return max(0.0, 1.0 - angle / 90.0), angle


def _text_proximity_score(dist: Optional[float], radius: float) -> float:
    if dist is None or radius <= 0:
        return 0.0
    return max(0.0, 1.0 - dist / radius)


def _chain_simplicity_score(n_vertices: int) -> float:
    """1.0 for a plain 2-point shaft (a Line); decreasing with more bends."""
    if n_vertices <= 2:
        return 1.0
    return max(0.3, 1.0 - 0.15 * (n_vertices - 2))


def find_leaders(ir: DrawingIR, max_arrowhead_size: Optional[float] = None,
                 search_radius: Optional[float] = None,
                 text_radius: Optional[float] = None,
                 min_confidence: float = 0.0,
                 exclude_dimensions: bool = False,
                 dimension_confidence: float = 0.5) -> List[Dict[str, Any]]:
    """PROPOSE leader constructs (bent arrow + tail text) from primitives.

    A "leader" (AutoCAD sense): a shaft (Line, or a Polyline with one or more
    bends) ending in an arrowhead at one end ("points at" something) and text
    at the other end ("tail"). Neither PDF vector nor an un-augmented DXF
    model space has a LEADER entity type for PDF; this composes one from the
    shared primitives, as a confidence-scored PROPOSAL — never asserted, same
    house pattern as :func:`candidate_ground_surface`.

    Heuristic (documented, not hidden):

    1. **Arrowhead candidates** — small closed 3-5-vertex Polyline/Region
       entities (see :func:`_arrowhead_candidates`). "Small" = bbox diagonal
       <= ``max_arrowhead_size`` (default: see
       :func:`_default_max_arrowhead_size` — ~25% of the drawing's typical
       shaft-segment length, falling back to a bbox-diagonal fraction; scales
       with the drawing instead of assuming absolute units).
    2. **Shaft** — the nearest Line/open-Polyline endpoint (excluding other
       arrowhead candidates) within ``search_radius`` (default
       ``1.5 * max_arrowhead_size``) of the candidate's centroid, via
       :func:`entities_ending_near`. ``tip_xy`` is that SHAFT endpoint (the
       deterministic source of truth), not the triangle's own apex vertex.
    3. **Alignment** — the shaft's terminal-segment direction vs. the
       arrowhead's own apex-from-base direction (apex = candidate vertex
       nearest the shaft endpoint; base = centroid of the other vertices).
       Parallel directions score high; the composed proposal is otherwise
       shape-blind (no assumption about arrowhead style beyond "small closed
       3-5-gon").
    4. **Tail text** — nearest TextItem to the shaft's FAR endpoint (the end
       away from the arrowhead), within ``text_radius`` (default
       ``4 * max_arrowhead_size``).
    5. **Confidence** = ``0.45*alignment + 0.35*text_proximity +
       0.20*chain_simplicity`` (weights chosen so a well-aligned, clearly
       labelled, simple 1-bend leader scores near 1.0; each factor is 0..1,
       see ``evidence`` in each proposal for the breakdown).

    Known false-positive source (by design, not a bug): a dimension line's
    end arrow is geometrically identical to a leader arrowhead (a small
    filled triangle at a line end), and a dimension VALUE sitting near the
    line's other end can score as "tail text" — a true dimension is
    geometrically a one-arrow leader and can score HIGH here (~0.78 on the
    reference fixture), so thresholding alone does not remove it. Pass
    ``exclude_dimensions=True`` to run :func:`find_dimensions` first and drop
    any leader proposal whose arrowhead is claimed by a dimension proposal
    with confidence >= ``dimension_confidence`` (a dimension arrowhead pairs
    with a SECOND arrowhead across a shared shaft — structure a true leader
    never has). Confirm visually (e.g. ``planlens.ir.render.render_region``
    on ``tip_xy``) before treating a proposal as a true annotation leader.

    Returns proposals sorted by confidence (descending), each:
    ``{tip_xy, tail_xy, vertices, arrowhead_id, shaft_id, text, text_id,
    text_distance, confidence, evidence, proposal_only: True}``, filtered to
    ``confidence >= min_confidence`` (default 0 = return every candidate).
    """
    max_arrowhead_size = max_arrowhead_size or _default_max_arrowhead_size(ir)
    search_radius = (search_radius if search_radius is not None
                     else max_arrowhead_size * 1.5)
    text_radius = (text_radius if text_radius is not None
                   else max_arrowhead_size * 4.0)

    # A leader's shaft is LONG relative to its arrowhead — by construction
    # (the default max_arrowhead_size is a fraction of typical shaft
    # length). Gating on it keeps glyph-scale micro-strokes on SHX-plotted
    # sheets from pairing into thousands of junk proposals.
    min_shaft_length = 2.0 * max_arrowhead_size

    grid = _EndpointGrid(ir, cell=search_radius,
                         entity_types=["line", "polyline"])
    texts = [e for e in ir.entities if isinstance(e, TextItem)]
    proposals = []
    for cand, verts, arrow_kind in _arrowhead_candidates_all(
            ir, max_arrowhead_size, min_shaft_length):
        member_ids = set(getattr(cand, "member_ids", ()))
        centroid = _centroid(verts)
        shaft_hits = _ending_near_from_grid(grid, centroid, search_radius)
        shaft_hits = [h for h in shaft_hits if h["id"] != cand.id
                     and h["id"] not in member_ids
                     and not (h["type"] == "polyline" and h.get("closed"))
                     and h.get("length", 0.0) >= min_shaft_length]
        if not shaft_hits:
            continue
        shaft_ref = shaft_hits[0]
        shaft = ir.by_id(shaft_ref["id"])
        shaft_pts = shaft.points()
        if len(shaft_pts) < 2:
            continue

        tip_xy = tuple(shaft_ref["end_point"])
        far_xy = tuple(shaft_ref["other_end"])
        if shaft_ref["end"] == "end":
            term_a, term_b = shaft_pts[-2], shaft_pts[-1]
        else:
            term_a, term_b = shaft_pts[1], shaft_pts[0]
        shaft_dir = _unit_vec(term_b[0] - term_a[0], term_b[1] - term_a[1])

        if arrow_kind == "fill_cluster":
            align_score, align_deg = _cluster_alignment(verts, shaft_dir)
        else:
            align_score, align_deg = _triangle_alignment(verts, shaft_dir)

        text_hit, text_dist = None, None
        for t in texts:
            d = math.hypot(t.position[0] - far_xy[0],
                           t.position[1] - far_xy[1])
            if d <= text_radius and (text_dist is None or d < text_dist):
                text_hit = {"content": t.content, "id": t.id, "distance": _r(d)}
                text_dist = d
        text_score = _text_proximity_score(text_dist, text_radius)

        n_vertices_shaft = shaft_ref.get("n_vertices", 2)
        simplicity = _chain_simplicity_score(n_vertices_shaft)

        if texts:
            confidence = round(0.45 * align_score + 0.35 * text_score
                               + 0.20 * simplicity, 3)
        else:
            # No text layer at all (SHX-stroked plot): tail text is
            # unknowable from vector geometry, so renormalize confidence
            # over the observable components instead of forever capping
            # every proposal at 0.65. The evidence flag records this.
            confidence = round((0.45 * align_score + 0.20 * simplicity)
                               / 0.65, 3)
        if confidence < min_confidence:
            continue

        proposals.append({
            "tip_xy": [_r(tip_xy[0]), _r(tip_xy[1])],
            "tail_xy": [_r(far_xy[0]), _r(far_xy[1])],
            "vertices": [[_r(p[0]), _r(p[1])] for p in shaft_pts],
            "arrowhead_id": cand.id,
            "shaft_id": shaft.id,
            "text": text_hit["content"] if text_hit else None,
            "text_id": text_hit["id"] if text_hit else None,
            "text_distance": _r(text_dist) if text_dist is not None else None,
            "confidence": confidence,
            "evidence": {
                "arrowhead_kind": arrow_kind,
                "alignment_score": _r(align_score, 3),
                "alignment_deg": _r(align_deg, 1),
                "text_proximity_score": _r(text_score, 3),
                "chain_simplicity_score": _r(simplicity, 3),
                "n_shaft_vertices": n_vertices_shaft,
                **({} if texts else {"text_unavailable": True}),
            },
            "proposal_only": True,
        })

    if exclude_dimensions:
        dims = find_dimensions(ir, max_arrowhead_size=max_arrowhead_size,
                               search_radius=search_radius,
                               text_radius=text_radius,
                               min_confidence=dimension_confidence)
        claimed = {aid for d in dims for aid in d["arrowhead_ids"]}
        proposals = [p for p in proposals
                     if p["arrowhead_id"] not in claimed]

    proposals.sort(key=lambda p: p["confidence"], reverse=True)
    return proposals


# ---------------------------------------------------------------------------
# Composition family (Phase 2) — dimensions, title block, bubbles, rev clouds.
# All are confidence-scored PROPOSALS over the shared primitives, same house
# pattern as find_leaders/candidate_ground_surface: never asserted facts.
# ---------------------------------------------------------------------------

def _fold_alignment(u: Point, v: Point) -> float:
    """|cos| alignment of two directions, sign-blind (1.0 = collinear)."""
    return abs(u[0] * v[0] + u[1] * v[1])


#: Ceiling for a no-text-renormalized CONTINUOUS dimension proposal that
#: lacks witness-line corroboration at both ends. Deliberately below the
#: conventional 0.5 call threshold: with text unobservable, alignment alone
#: is dominated by hatch/stipple misreads (see find_dimensions docstring).
_UNCORROBORATED_CAP = 0.45


def _median(vals: List[float]) -> float:
    s = sorted(vals)
    return s[len(s) // 2] if s else 0.0


def _median_text_height(ir: DrawingIR) -> float:
    hs = [e.height for e in ir.entities
          if isinstance(e, TextItem) and (e.height or 0) > 0]
    return _median(hs)


def _shaft_terminal_dir(shaft_pts: List[Point], end_label: str) -> Point:
    """Unit direction of the shaft's terminal segment, pointing OUT the end."""
    if end_label == "end":
        a, b = shaft_pts[-2], shaft_pts[-1]
    else:
        a, b = shaft_pts[1], shaft_pts[0]
    return _unit_vec(b[0] - a[0], b[1] - a[1])


def find_dimensions(ir: DrawingIR, max_arrowhead_size: Optional[float] = None,
                    search_radius: Optional[float] = None,
                    text_radius: Optional[float] = None,
                    min_confidence: float = 0.0) -> List[Dict[str, Any]]:
    """PROPOSE dimension constructs — continuous OR split-shaft.

    A dimension line: a straight shaft with an arrowhead at EACH end, usually
    bracketed by perpendicular extension (witness) lines terminating near the
    tips, with the dimension VALUE text near the shaft midpoint. The
    two-arrowheads structure is what distinguishes it from a leader (one
    arrowhead) — which also makes this the disambiguator for
    :func:`find_leaders`'s documented dimension false-positive source (see
    its ``exclude_dimensions`` option).

    Two detection legs (both verified against the Mecklenburg native-DXF
    ground-truth plots, 2026-09-05):

    - **Continuous** (``evidence.path = "continuous"``): one Line/open-
      Polyline shaft with an arrowhead candidate
      (:func:`_arrowhead_candidates`) within ``search_radius`` of EACH
      endpoint — the drafting style whose value text sits ABOVE the line.
    - **Split-shaft** (``evidence.path = "split_shaft"``): TWO collinear
      half-shafts around a centered text gap, each carrying ONE arrowhead at
      its OUTER end pointing outward — how native CAD dimensions plot when
      the value text is centered IN the line (the dominant real-sheet style;
      the continuous model alone scored 1/16 on native truth). Pairing
      requires fold-collinear axes, opposed outward arrow directions, small
      lateral offset, and a gap sized between 0.1x and 6x
      ``max_arrowhead_size``; each half pairs with at most one partner
      (nearest-gap-first greedy). Proposal ends are the two arrow APEX
      points (the CAD defpoints), and ``evidence`` names both halves and
      the gap.

    Scoring (each leg): **alignment** (0.40) — sign-blind |cos| of arrowhead
    axis vs shaft terminal axis (mean over both arrows; the split leg also
    multiplies by the halves' collinearity); **text** (0.30) — nearest
    TextItem to the shaft midpoint (continuous) or the GAP CENTER (split)
    within ``text_radius``; **extension lines** (0.30) — at each tip, any
    OTHER entity of length >= 0.5x ``max_arrowhead_size`` ending within
    ``search_radius`` whose terminal direction is >= 55 deg off the shaft
    axis (the length floor keeps stipple/hatch micro-fragments from
    counting as witness lines — measured 392 sub-3-pt "witnesses" on one
    real stippled sheet before the floor).

    NO-TEXT sheets (SHX-stroked plots): confidence renormalizes over the
    observable components as before, but a CONTINUOUS proposal is capped at
    ``0.45`` (below the conventional 0.5 call threshold) unless corroborated
    by witness lines at BOTH ends — on such sheets an uncorroborated
    two-triangles-on-a-line score is dominated by hatch/stipple misreads
    (measured ~0 precision at 0.9+ renormalized confidence on the worst
    validation sheet). Split-shaft proposals are exempt: the paired
    structure is itself the corroboration.

    Defaults follow :func:`find_leaders` (``max_arrowhead_size`` from
    :func:`_default_max_arrowhead_size`; ``search_radius`` = 1.5x;
    ``text_radius`` = 4x). Returns proposals sorted by confidence:
    ``{end_a_xy, end_b_xy, midpoint_xy, length, angle_deg, shaft_id,
    arrowhead_ids, extension_line_ids, text, text_id, text_distance,
    confidence, evidence, proposal_only: True}``.
    """
    max_arrowhead_size = max_arrowhead_size or _default_max_arrowhead_size(ir)
    search_radius = (search_radius if search_radius is not None
                     else max_arrowhead_size * 1.5)
    text_radius = (text_radius if text_radius is not None
                   else max_arrowhead_size * 4.0)

    # Same shaft-vs-arrowhead scale gate as find_leaders (see there): keeps
    # glyph-scale micro-strokes from pairing into junk dimension proposals.
    min_shaft_length = 2.0 * max_arrowhead_size
    # A split HALF-shaft can be much shorter than a full dimension shaft
    # (measured 7.7-8.6 pt vs a 9.3 pt arrowhead scale on the real sheets),
    # so the split leg uses its own floor; the pairing structure carries the
    # discrimination a length gate provides for the continuous leg.
    half_min_length = 0.5 * max_arrowhead_size
    # A witness line is a REAL line: it overshoots the dimension line by at
    # least an arrowhead-scale amount. Without a floor, stipple/hatch
    # micro-fragments (0.06-3 pt) ending near a tip count as "witness
    # lines" and corroborate junk (measured on ground-truth sheet 3001).
    min_witness_length = 0.5 * max_arrowhead_size
    # End-arrow assignment radius: see the attach comment in the per-end
    # loop below.
    attach_radius = min(search_radius, 0.75 * max_arrowhead_size)

    arrowheads = list(_arrowhead_candidates_all(ir, max_arrowhead_size,
                                                min_shaft_length))
    if not arrowheads:
        return []
    arrow_ids = {e.id for e, _, _ in arrowheads}

    # Grid the arrowhead centroids (dense sheets carry thousands of shafts;
    # a per-shaft linear scan over candidates is O(n*m) and hits a wall).
    cell = max(search_radius, 1e-9)
    acells: Dict[Tuple[int, int], list] = {}
    for cand, verts, kind in arrowheads:
        c = _centroid(verts)
        key = (int(c[0] // cell), int(c[1] // cell))
        acells.setdefault(key, []).append((cand, verts, c, kind))

    def _arrow_near(tip):
        cx, cy = int(tip[0] // cell), int(tip[1] // cell)
        for gx in range(cx - 1, cx + 2):
            for gy in range(cy - 1, cy + 2):
                yield from acells.get((gx, gy), ())

    end_grid = _EndpointGrid(ir, cell=search_radius,
                             entity_types=["line", "polyline"])
    texts = [e for e in ir.entities if isinstance(e, TextItem)]
    # O(1) entity lookup: DrawingIR.by_id is a linear scan, and dashed
    # linework can put thousands of endpoints near one tip (profiled at
    # 49 s of by_id on a real 10k-entity sheet).
    ent_by_id = {e.id: e for e in ir.entities}

    def _witness_at(tip: Point, axis_dir: Point, used_ids: set,
                    ext_ids: List[str]) -> bool:
        """Any OTHER real line terminating near ``tip`` roughly
        perpendicular (>= 55 deg) to ``axis_dir``. Appends ids, returns
        whether one was found. Nearest 50 endpoint hits suffice —
        dashed/stippled linework can put thousands of endpoints in range,
        and a real extension line terminates AT the tip."""
        found = False
        for hit in _ending_near_from_grid(end_grid, tip, search_radius)[:50]:
            if hit["id"] in used_ids or hit.get("closed"):
                continue
            if hit.get("length", 0.0) < min_witness_length:
                continue
            other = ent_by_id.get(hit["id"])
            if other is None:
                continue
            opts = other.points()
            if len(opts) < 2:
                continue
            odir = _shaft_terminal_dir(opts, hit["end"])
            if _fold_alignment(axis_dir, odir) <= math.cos(
                    math.radians(55.0)):
                ext_ids.append(hit["id"])
                found = True
        return found

    def _nearest_text(pt: Point):
        hit, dist = None, None
        for t in texts:
            d = math.hypot(t.position[0] - pt[0], t.position[1] - pt[1])
            if d <= text_radius and (dist is None or d < dist):
                hit, dist = {"content": t.content, "id": t.id}, d
        return hit, dist

    proposals = []
    halves: List[Dict[str, Any]] = []  # single-arrow records, split leg
    for shaft in ir.entities:
        if isinstance(shaft, Line):
            pass
        elif isinstance(shaft, Polyline) and not shaft.closed:
            pass
        else:
            continue
        if shaft.id in arrow_ids:
            continue
        shaft_pts = shaft.points()
        if len(shaft_pts) < 2:
            continue
        # A dimension shaft is essentially STRAIGHT: gate on the end-to-end
        # separation (a glyph squiggle has a long PATH but near-coincident
        # endpoints) and on separation/path-length straightness.
        # (Curved/angular dimensions are out of scope — documented
        # limitation.)
        sep = math.hypot(shaft_pts[-1][0] - shaft_pts[0][0],
                         shaft_pts[-1][1] - shaft_pts[0][1])
        if sep < half_min_length or sep < 0.9 * shaft.length():
            continue
        ends = [("start", shaft_pts[0]), ("end", shaft_pts[-1])]

        per_end = []
        for end_label, tip in ends:
            best = None
            sdir = _shaft_terminal_dir(shaft_pts, end_label)
            for cand, verts, c, kind in _arrow_near(tip):
                d = math.hypot(c[0] - tip[0], c[1] - tip[1])
                # A dimension arrow TOUCHES the line end it belongs to (its
                # base sits AT the end, putting the centroid ~half an
                # arrow-length away) — the full leader-flow search_radius
                # let glyph junk 1.4 arrow-lengths away hijack an end and
                # misclassify a split half-shaft as two-arrowed.
                if d <= attach_radius and (best is None or d < best[1]):
                    if kind == "fill_cluster":
                        a_score = _cluster_alignment(verts, sdir)[0]
                    else:
                        a_score = _triangle_alignment(verts, sdir)[0]
                    best = (cand, d, a_score, kind, verts, sdir, tip)
            per_end.append((end_label, tip, best))

        n_arrowed = sum(1 for _, _, b in per_end if b is not None)

        if n_arrowed == 1 and sep >= half_min_length:
            # SPLIT-LEG CANDIDATE: one arrowed (outer) end. The arrow must
            # sit BEYOND the shaft end pointing outward (its centroid past
            # the tip along the terminal direction) — an arrow behind the
            # tip is some other construct's arrow this shaft merely grazes.
            b = next(b for _, _, b in per_end if b is not None)
            cand, _d, a_score, kind, verts, sdir, tip = b
            centroid = _centroid(verts)
            if ((centroid[0] - tip[0]) * sdir[0]
                    + (centroid[1] - tip[1]) * sdir[1]) > 0:
                inner = (shaft_pts[0] if tip == shaft_pts[-1]
                         else shaft_pts[-1])
                # Apex = candidate vertex farthest along the outward axis
                # (for a cluster: the farthest member center) — the CAD
                # defpoint the arrow points at.
                apex = max(verts, key=lambda v: v[0] * sdir[0]
                           + v[1] * sdir[1])
                halves.append({
                    "shaft": shaft, "cand": cand, "kind": kind,
                    "a_score": a_score, "outer": tip, "inner": inner,
                    "out": sdir, "apex": apex,
                    "members": set(getattr(cand, "member_ids", ())),
                })
            continue

        if n_arrowed < 2 or sep < min_shaft_length:
            continue
        if per_end[0][2][0].id == per_end[1][2][0].id:
            continue  # the two arrowheads must be DISTINCT

        align = sum(b[2] for _, _, b in per_end) / len(per_end)

        # Extension (witness) lines: something ELSE terminating near each
        # tip, roughly perpendicular to the shaft's terminal axis at that
        # end. Cluster members must not double as witness lines.
        used_ids = {shaft.id} | {b[0].id for _, _, b in per_end}
        for _, _, b in per_end:
            used_ids |= set(getattr(b[0], "member_ids", ()))
        ext_ids: List[str] = []
        ext_ends = 0
        for end_label, tip, _b in per_end:
            shaft_dir = _shaft_terminal_dir(shaft_pts, end_label)
            if _witness_at(tip, shaft_dir, used_ids, ext_ids):
                ext_ends += 1
        ext_score = ext_ends / 2.0

        mid = (0.5 * (ends[0][1][0] + ends[1][1][0]),
               0.5 * (ends[0][1][1] + ends[1][1][1]))
        text_hit, text_dist = _nearest_text(mid)
        text_score = _text_proximity_score(text_dist, text_radius)

        if texts:
            confidence = round(0.40 * align + 0.30 * text_score
                               + 0.30 * ext_score, 3)
        else:
            # No text layer (SHX plot) — same renormalization rationale as
            # find_leaders: score the observable components. But capped
            # below the conventional 0.5 call threshold unless witness
            # lines corroborate BOTH ends AND at least one arrowhead is a
            # DRAWN shape (triangle): with text unobservable, an
            # uncorroborated two-triangles-on-a-line is dominated by
            # hatch/stipple misreads (measured ~0 precision at 0.9+
            # renormalized confidence on ground-truth sheet 3001), and a
            # cluster-only pair is any stipple splash at a long line's two
            # ends — page borders score 0.74+ that way, witnesses and all.
            raw = (0.40 * align + 0.30 * ext_score) / 0.70
            kinds = [b[3] for _, _, b in per_end]
            if ext_ends < 2 or "triangle" not in kinds:
                raw = min(raw, _UNCORROBORATED_CAP)
            confidence = round(raw, 3)
        if confidence < min_confidence:
            continue

        a_xy, b_xy = ends[0][1], ends[1][1]
        proposals.append({
            "end_a_xy": [_r(a_xy[0]), _r(a_xy[1])],
            "end_b_xy": [_r(b_xy[0]), _r(b_xy[1])],
            "midpoint_xy": [_r(mid[0]), _r(mid[1])],
            "length": _r(math.hypot(b_xy[0] - a_xy[0], b_xy[1] - a_xy[1])),
            "angle_deg": _r(_seg_angle(a_xy, b_xy), 2),
            "shaft_id": shaft.id,
            "arrowhead_ids": [b[0].id for _, _, b in per_end],
            "extension_line_ids": sorted(set(ext_ids)),
            "text": text_hit["content"] if text_hit else None,
            "text_id": text_hit["id"] if text_hit else None,
            "text_distance": _r(text_dist) if text_dist is not None else None,
            "confidence": confidence,
            "evidence": {
                "path": "continuous",
                "arrowhead_kinds": [b[3] for _, _, b in per_end],
                "alignment_score": _r(align, 3),
                "text_proximity_score": _r(text_score, 3),
                "extension_line_score": _r(ext_score, 3),
                "n_extension_ends": ext_ends,
                **({} if texts else {"text_unavailable": True}),
            },
            "proposal_only": True,
        })

    proposals.extend(_pair_split_halves(
        halves, max_arrowhead_size, texts, _witness_at, _nearest_text,
        text_radius, min_confidence))

    proposals.sort(key=lambda p: p["confidence"], reverse=True)
    return proposals


def _pair_split_halves(halves: List[Dict[str, Any]],
                       max_arrowhead_size: float, texts,
                       witness_at, nearest_text, text_radius: float,
                       min_confidence: float) -> List[Dict[str, Any]]:
    """Pair single-arrow half-shafts into split-shaft dimension proposals.

    See :func:`find_dimensions` (split-shaft leg) for the model. Pairing
    gates, each against the verified real-sheet geometry: fold-collinear
    axes (|cos| >= 0.997, ~4 deg), OPPOSED arrow directions, lateral
    offset <= max(0.15 x arrowhead scale, 1.0), and one of the two real
    plotted arrangements:

    - **outward** — arrows at the OUTER ends pointing away from each
      other, inner ends facing across a text-sized gap (0.1x-6x the
      arrowhead scale; measured ~1.5x on the ground-truth sheets);
    - **inward** — arrows OUTSIDE the extension lines pointing at each
      other (the narrow-dimension style; verified on ground-truth sheet
      10.31A), apexes facing across the dimension extent (up to 40x the
      arrowhead scale) with the half-shafts trailing outward. The larger
      span is licensed by a harder corroboration rule: on a no-text sheet
      an inward pair is DROPPED unless witness lines corroborate BOTH
      apexes (the terminator geometry the style guarantees).

    Nearest-gap-first greedy: each half joins at most one pair — a
    dimension string shares defpoints between neighbors, and the smallest
    valid gap is the half's own gap, not the span across a neighboring
    dimension.
    """
    out: List[Dict[str, Any]] = []
    if len(halves) < 2:
        return out
    max_gap = 6.0 * max_arrowhead_size
    min_gap = 0.1 * max_arrowhead_size
    max_span = 40.0 * max_arrowhead_size
    max_lateral = max(0.15 * max_arrowhead_size, 1.0)

    # Halves are few (dozens on the dense validation sheets) — a direct
    # pair scan is cheap, and the inward style's partners can sit a whole
    # dimension-extent apart, which defeats a text-gap-sized grid.
    pairs = []
    for i, hi in enumerate(halves):
        for j in range(i + 1, len(halves)):
            hj = halves[j]
            if hi["cand"].id == hj["cand"].id:
                continue
            fold = _fold_alignment(hi["out"], hj["out"])
            if fold < 0.997:
                continue
            if (hi["out"][0] * hj["out"][0]
                    + hi["out"][1] * hj["out"][1]) >= 0:
                continue  # arrows must OPPOSE
            vx = hj["inner"][0] - hi["inner"][0]
            vy = hj["inner"][1] - hi["inner"][1]
            lateral = abs(vx * hi["out"][1] - vy * hi["out"][0])
            if lateral > max_lateral:
                continue
            inner_gap = -(vx * hi["out"][0] + vy * hi["out"][1])
            ax = hj["apex"][0] - hi["apex"][0]
            ay = hj["apex"][1] - hi["apex"][1]
            apex_span = ax * hi["out"][0] + ay * hi["out"][1]
            if min_gap <= inner_gap <= max_gap:
                pairs.append((inner_gap, fold, i, j, "outward"))
            elif min_gap <= apex_span <= max_span and inner_gap < 0:
                pairs.append((apex_span, fold, i, j, "inward"))

    pairs.sort(key=lambda t: t[0])
    used: set = set()
    for gap, fold, i, j, style in pairs:
        if i in used or j in used:
            continue
        hi, hj = halves[i], halves[j]
        align = 0.5 * (hi["a_score"] + hj["a_score"]) * fold

        used_ids = ({hi["shaft"].id, hj["shaft"].id,
                     hi["cand"].id, hj["cand"].id}
                    | hi["members"] | hj["members"])
        ext_ids: List[str] = []
        ext_ends = 0
        for h in (hi, hj):
            if witness_at(h["apex"], h["out"], used_ids, ext_ids):
                ext_ends += 1
        ext_score = ext_ends / 2.0
        if style == "inward" and not texts and ext_ends < 2:
            continue  # see docstring: inward's wide span needs witnesses
        used.add(i)
        used.add(j)

        # Text sits in the inner gap (outward) or between the apexes
        # (inward) — the mid-construct point either way.
        gap_center = (0.5 * (hi["inner"][0] + hj["inner"][0]),
                      0.5 * (hi["inner"][1] + hj["inner"][1]))
        if style == "inward":
            gap_center = (0.5 * (hi["apex"][0] + hj["apex"][0]),
                          0.5 * (hi["apex"][1] + hj["apex"][1]))
        text_hit, text_dist = nearest_text(gap_center)
        text_score = _text_proximity_score(text_dist, text_radius)

        if texts:
            confidence = round(0.40 * align + 0.30 * text_score
                               + 0.30 * ext_score, 3)
        else:
            # Renormalized like the continuous leg. The paired split
            # structure (collinear opposed-arrow halves around a
            # text-sized gap, or witness-corroborated inward arrows) is
            # itself the corroboration the continuous cap asks for — but
            # only when at least one arrowhead is a DRAWN shape; a
            # cluster-only pair stays capped (same stipple rationale).
            raw = (0.40 * align + 0.30 * ext_score) / 0.70
            if hi["kind"] != "triangle" and hj["kind"] != "triangle":
                raw = min(raw, _UNCORROBORATED_CAP)
            confidence = round(raw, 3)
        if confidence < min_confidence:
            continue

        a_xy, b_xy = hi["apex"], hj["apex"]
        mid = (0.5 * (a_xy[0] + b_xy[0]), 0.5 * (a_xy[1] + b_xy[1]))
        out.append({
            "end_a_xy": [_r(a_xy[0]), _r(a_xy[1])],
            "end_b_xy": [_r(b_xy[0]), _r(b_xy[1])],
            "midpoint_xy": [_r(mid[0]), _r(mid[1])],
            "length": _r(math.hypot(b_xy[0] - a_xy[0], b_xy[1] - a_xy[1])),
            "angle_deg": _r(_seg_angle(a_xy, b_xy), 2),
            "shaft_id": hi["shaft"].id,
            "arrowhead_ids": [hi["cand"].id, hj["cand"].id],
            "extension_line_ids": sorted(set(ext_ids)),
            "text": text_hit["content"] if text_hit else None,
            "text_id": text_hit["id"] if text_hit else None,
            "text_distance": _r(text_dist) if text_dist is not None else None,
            "confidence": confidence,
            "evidence": {
                "path": "split_shaft",
                "arrangement": style,
                "half_shaft_ids": [hi["shaft"].id, hj["shaft"].id],
                "gap": _r(gap, 2),
                "gap_center_xy": [_r(gap_center[0]), _r(gap_center[1])],
                "arrowhead_kinds": [hi["kind"], hj["kind"]],
                "alignment_score": _r(align, 3),
                "collinearity": _r(fold, 4),
                "text_proximity_score": _r(text_score, 3),
                "extension_line_score": _r(ext_score, 3),
                "n_extension_ends": ext_ends,
                **({} if texts else {"text_unavailable": True}),
            },
            "proposal_only": True,
        })
    return out


def _circle_fit(verts: List[Point]) -> Tuple[Point, float, float]:
    """Centroid-based circle fit: (center, mean radius, rms/r roundness).

    ``rms/r`` near 0 = circle-like; a square scores ~0.1, elongated shapes
    higher. Cheap and adequate for classification (not metrology).
    """
    c = _centroid(verts)
    rs = [math.hypot(p[0] - c[0], p[1] - c[1]) for p in verts]
    rmean = sum(rs) / len(rs)
    if rmean <= 0:
        return c, 0.0, math.inf
    rms = math.sqrt(sum((r - rmean) ** 2 for r in rs) / len(rs))
    return c, rmean, rms / rmean


#: Roundness (rms/r) threshold for treating a closed ring as a circle.
_CIRCLE_RMS_MAX = 0.08


def _circle_like_candidates(ir: DrawingIR, max_radius: float,
                            min_vertices: int = 6):
    """Yield (entity, center, radius) for circles + circle-like closed rings.

    Native ``Circle`` entities pass directly. Closed Polylines/Regions with
    >= ``min_vertices`` vertices qualify when their centroid circle-fit
    roundness is under :data:`_CIRCLE_RMS_MAX` — with bezier-sampled PDF
    ingest a drawn circle arrives as a ~32-vertex closed ring that fits
    almost exactly. (A pre-bezier-sampling 4-vertex diamond is deliberately
    excluded by ``min_vertices``: indistinguishable from a real diamond.)
    """
    for e in ir.entities:
        if isinstance(e, Circle):
            if e.radius <= max_radius:
                yield e, tuple(e.center), e.radius
            continue
        if isinstance(e, Polyline) and e.closed:
            verts = e.vertices
        elif isinstance(e, Region):
            verts = e.boundary
        else:
            continue
        if len(verts) < min_vertices:
            continue
        c, r, rms = _circle_fit([tuple(p) for p in verts])
        if r > 0 and r <= max_radius and rms <= _CIRCLE_RMS_MAX:
            yield e, c, r


def find_bubble_callouts(ir: DrawingIR, max_radius: Optional[float] = None,
                         text_max_chars: int = 4,
                         min_confidence: float = 0.0) -> List[Dict[str, Any]]:
    """PROPOSE bubble callouts: a small circle with short centered text.

    Covers the "number in a circle" family — keynotes, grid bubbles, detail/
    section marks. Candidates are native ``Circle`` entities and circle-like
    closed rings (:func:`_circle_like_candidates`), radius <=
    ``max_radius`` (default 4x the drawing's median text height, falling
    back to 25.0 units when no sized text exists). Each is scored on

    - **roundness** (0.35) — circle-fit quality (native circles = 1.0);
    - **text centering** (0.45) — a TextItem of <= ``text_max_chars``
      (stripped) whose insertion point lies within ~0.9r of the center;
    - **size plausibility** (0.20) — radius between 0.8x and 4x the median
      text height (when known; neutral 0.5 otherwise).

    Classification hints (evidence, not assertions): a Line/Polyline whose
    endpoint lands on the ring (within 0.2r) marks a probable
    ``grid_bubble``/attached callout; a chord passing within 0.25r of the
    center marks a probable ``detail_callout`` (split circle); otherwise
    ``keynote``. Bubbles with no text still surface at reduced confidence.

    Returns proposals sorted by confidence: ``{center_xy, radius, kind,
    text, text_id, entity_id, attached_line_ids, confidence, evidence,
    proposal_only: True}``.
    """
    med_h = _median_text_height(ir)
    if max_radius is None:
        max_radius = 4.0 * med_h if med_h > 0 else 25.0

    grid = _EndpointGrid(ir, cell=max(max_radius * 1.2, 1e-9),
                         entity_types=["line", "polyline"])
    all_texts = [t for t in ir.entities if isinstance(t, TextItem)]
    lines_by_id = {e.id: e for e in ir.entities if isinstance(e, Line)}

    proposals = []
    for e, center, radius in _circle_like_candidates(ir, max_radius):
        if isinstance(e, Circle):
            roundness = 1.0
        else:
            verts = e.vertices if isinstance(e, Polyline) else e.boundary
            _, _, rms = _circle_fit([tuple(p) for p in verts])
            roundness = max(0.0, 1.0 - rms / _CIRCLE_RMS_MAX)

        # Short text centered in the bubble.
        text_hit = None
        best_d = None
        for t in all_texts:
            if len(t.content.strip()) > text_max_chars:
                continue
            d = math.hypot(t.position[0] - center[0],
                           t.position[1] - center[1])
            if d <= 0.9 * radius and (best_d is None or d < best_d):
                text_hit, best_d = t, d
        text_score = (max(0.0, 1.0 - best_d / max(radius, 1e-9))
                      if text_hit is not None else 0.0)

        if med_h > 0:
            size_score = 1.0 if 0.8 * med_h <= radius <= 4.0 * med_h else 0.4
        else:
            size_score = 0.5

        # Attached line-work: endpoints on the ring; chords through center.
        # Both checks run off the shared endpoint grid (a chord's endpoints
        # both sit within 1.2r of the center too) — a linear scan per
        # candidate is O(candidates x entities) and glyph 'o's on dense
        # SHX sheets make candidates plentiful.
        attached: List[str] = []
        kind = "keynote"
        near_ring_ids = set()
        for hit in _ending_near_from_grid(grid, center, radius * 1.2):
            if hit["id"] == e.id or hit.get("closed"):
                continue
            ex, ey = hit["end_point"]
            ring_dev = abs(math.hypot(ex - center[0], ey - center[1]) - radius)
            if ring_dev <= 0.2 * radius:
                near_ring_ids.add(hit["id"])
                if hit["id"] not in attached:
                    attached.append(hit["id"])
        if attached:
            kind = "grid_bubble"
        for lid in near_ring_ids:
            ln = lines_by_id.get(lid)
            if ln is None:
                continue
            d_center = _point_seg_dist(center[0], center[1],
                                       ln.start[0], ln.start[1],
                                       ln.end[0], ln.end[1])
            on_ring = (abs(math.hypot(ln.start[0] - center[0],
                                      ln.start[1] - center[1]) - radius)
                       <= 0.2 * radius
                       and abs(math.hypot(ln.end[0] - center[0],
                                          ln.end[1] - center[1]) - radius)
                       <= 0.2 * radius)
            if on_ring and d_center <= 0.25 * radius:
                kind = "detail_callout"
                break

        confidence = round(0.35 * roundness + 0.45 * text_score
                           + 0.20 * size_score, 3)
        if confidence < min_confidence:
            continue
        proposals.append({
            "center_xy": [_r(center[0]), _r(center[1])],
            "radius": _r(radius),
            "kind": kind,
            "text": text_hit.content if text_hit is not None else None,
            "text_id": text_hit.id if text_hit is not None else None,
            "entity_id": e.id,
            "attached_line_ids": attached,
            "confidence": confidence,
            "evidence": {
                "roundness_score": _r(roundness, 3),
                "text_centering_score": _r(text_score, 3),
                "size_score": _r(size_score, 3),
            },
            "proposal_only": True,
        })

    proposals.sort(key=lambda p: p["confidence"], reverse=True)
    return proposals


def _turn_angles(verts: List[Point]) -> List[float]:
    """Signed turn angle (degrees) at each vertex of a closed ring."""
    n = len(verts)
    out = []
    for i in range(n):
        a, b, c = verts[i - 1], verts[i], verts[(i + 1) % n]
        v1 = (b[0] - a[0], b[1] - a[1])
        v2 = (c[0] - b[0], c[1] - b[1])
        if math.hypot(*v1) < 1e-9 or math.hypot(*v2) < 1e-9:
            continue
        cross = v1[0] * v2[1] - v1[1] * v2[0]
        dot = v1[0] * v2[0] + v1[1] * v2[1]
        out.append(math.degrees(math.atan2(cross, dot)))
    return out


def find_revision_clouds(ir: DrawingIR, min_arcs: int = 3,
                         min_confidence: float = 0.0) -> List[Dict[str, Any]]:
    """PROPOSE revision clouds and revision-delta markers. BEST-EFFORT tier.

    Cloud shapes are drafter-practice-dependent (scallop size, closure,
    single path vs separate arcs), so every proposal here sits in a LOW
    confidence band (<= ~0.65) by design — treat these as "worth a look",
    and confirm with a vision zoom.

    Two detection paths:

    - **Native arcs** (DXF): chains of ``Arc`` entities joined endpoint-to-
      endpoint (tolerance 25% of the mean radius); a chain of >=
      ``min_arcs`` arcs is a cloud proposal (closed chains score higher).
    - **Scalloped rings** (bezier-sampled PDF): a closed Polyline with >= 12
      vertices whose turn-angle sequence shows smooth low-angle runs (median
      |turn| 2-25 deg) broken by >= 2 cusp junctions — a spike of the
      OPPOSITE sign to the dominant turning direction, the signature of
      adjacent outward bumps meeting in a concave notch (empirically
      verified against PyMuPDF ``draw_curve`` scallops; a rounded rectangle
      has same-sign corners and is rejected).

    Revision DELTAS: a small closed triangle (3-4 vertices) with short text
    (<= 3 chars) centered within ~1.5x its size and NO line-work terminating
    at it (an arrowhead has a shaft; a delta marker does not).

    Returns proposals sorted by confidence: ``{kind: "cloud"|"revision_delta",
    bbox | center_xy, entity_ids, text?, confidence, evidence,
    proposal_only: True}``.
    """
    proposals: List[Dict[str, Any]] = []

    # --- Path A: native Arc chains (DXF ingest) ---
    arcs = [e for e in ir.entities if isinstance(e, Arc)]
    if len(arcs) >= min_arcs:
        arc_ends = {}
        for a in arcs:
            eps = _entity_endpoints(a)
            arc_ends[a.id] = [pt for _, pt in eps]
        mean_r = sum(a.radius for a in arcs) / len(arcs)
        tol = max(mean_r * 0.25, 1e-6)
        # Union-find over arcs sharing an endpoint.
        parent = {a.id: a.id for a in arcs}

        def _find(x):
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x

        for i, a in enumerate(arcs):
            for b in arcs[i + 1:]:
                if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= tol
                       for p in arc_ends[a.id] for q in arc_ends[b.id]):
                    parent[_find(a.id)] = _find(b.id)
        groups: Dict[str, List[Arc]] = {}
        for a in arcs:
            groups.setdefault(_find(a.id), []).append(a)
        for members in groups.values():
            if len(members) < min_arcs:
                continue
            boxes = [m.bbox for m in members if m.bbox]
            bb = (min(b[0] for b in boxes), min(b[1] for b in boxes),
                  max(b[2] for b in boxes), max(b[3] for b in boxes))
            # Closed-ish: every endpoint shared with another arc in the chain.
            shared = 0
            total = 0
            for m in members:
                for p in arc_ends[m.id]:
                    total += 1
                    if any(o is not m and any(
                            math.hypot(p[0] - q[0], p[1] - q[1]) <= tol
                            for q in arc_ends[o.id]) for o in members):
                        shared += 1
            closure = shared / total if total else 0.0
            confidence = round(min(0.65, 0.30 + 0.03 * len(members)
                                   + 0.15 * closure), 3)
            if confidence < min_confidence:
                continue
            proposals.append({
                "kind": "cloud",
                "bbox": [_r(v) for v in bb],
                "entity_ids": [m.id for m in members],
                "confidence": confidence,
                "evidence": {"n_arcs": len(members),
                             "endpoint_closure": _r(closure, 3),
                             "path": "native_arcs"},
                "proposal_only": True,
            })

    # --- Path B: scalloped closed rings (bezier-sampled PDF polylines) ---
    for e in ir.entities:
        if not (isinstance(e, Polyline) and e.closed
                and len(e.vertices) >= 12):
            continue
        turns = _turn_angles([tuple(p) for p in e.vertices])
        if len(turns) < 12:
            continue
        med = _median([abs(t) for t in turns])
        if not (2.0 <= med <= 25.0):
            continue
        neg = sum(1 for t in turns if t < 0)
        dom_sign = -1.0 if neg >= len(turns) / 2 else 1.0
        cusps = [t for t in turns
                 if abs(t) > 2.5 * med and (t * dom_sign) < 0]
        if len(cusps) < 2:
            continue
        confidence = round(min(0.60, 0.30 + 0.05 * len(cusps)), 3)
        if confidence < min_confidence:
            continue
        proposals.append({
            "kind": "cloud",
            "bbox": [_r(v) for v in e.bbox] if e.bbox else None,
            "entity_ids": [e.id],
            "confidence": confidence,
            "evidence": {"n_cusps": len(cusps),
                         "median_abs_turn_deg": _r(med, 1),
                         "path": "scalloped_ring"},
            "proposal_only": True,
        })

    # --- Revision deltas: small labelled triangles with no shaft ---
    max_size = _default_max_arrowhead_size(ir)
    delta_grid = _EndpointGrid(ir, cell=max(max_size * 1.5, 1e-9),
                               entity_types=["line", "polyline"])
    for cand, verts in _arrowhead_candidates(ir, max_size):
        if len(verts) > 4:
            continue
        c = _centroid(verts)
        size = _bbox_diag(cand.bbox) if cand.bbox else 0.0
        if size <= 0:
            continue
        # A delta has NO line-work terminating at it (an arrowhead does).
        shaft_hits = [h for h in _ending_near_from_grid(
            delta_grid, c, size * 1.5)
            if h["id"] != cand.id and not h.get("closed")]
        if shaft_hits:
            continue
        text_hit, best_d = None, None
        for t in ir.entities:
            if not isinstance(t, TextItem):
                continue
            if len(t.content.strip()) > 3:
                continue
            d = math.hypot(t.position[0] - c[0], t.position[1] - c[1])
            if d <= 1.5 * size and (best_d is None or d < best_d):
                text_hit, best_d = t, d
        if text_hit is None:
            continue
        confidence = round(min(0.6, 0.35 + 0.25 * max(
            0.0, 1.0 - best_d / (1.5 * size))), 3)
        if confidence < min_confidence:
            continue
        proposals.append({
            "kind": "revision_delta",
            "center_xy": [_r(c[0]), _r(c[1])],
            "entity_ids": [cand.id],
            "text": text_hit.content,
            "text_id": text_hit.id,
            "confidence": confidence,
            "evidence": {"marker_size": _r(size, 2),
                         "text_distance": _r(best_d, 2)},
            "proposal_only": True,
        })

    proposals.sort(key=lambda p: p["confidence"], reverse=True)
    return proposals


def _rect_like(e: Entity) -> Optional[Tuple[List[Point], float]]:
    """(vertices, rectangularity 0..1) for closed 4-5-vertex rings, else None.

    Rectangularity = polygon area / bbox area (1.0 = an axis-aligned
    rectangle; a rotated rectangle or triangle scores lower). Open 4-vertex
    polylines are ACCEPTED with an implied closing segment: a PDF ``re``
    rectangle ingests as an open 4-corner polyline (empirically verified —
    the corner list carries no closing repeat), and the shoelace area with
    implied closure still scores a true rectangle at 1.0 while an open
    zigzag scores low.
    """
    if isinstance(e, Polyline) and (
            (e.closed and 4 <= len(e.vertices) <= 5)
            or (not e.closed and len(e.vertices) == 4)):
        verts = [tuple(p) for p in e.vertices]
    elif isinstance(e, Region) and 4 <= len(e.boundary) <= 5:
        verts = [tuple(p) for p in e.boundary]
    else:
        return None
    if e.bbox is None:
        return None
    bw = e.bbox[2] - e.bbox[0]
    bh = e.bbox[3] - e.bbox[1]
    if bw <= 0 or bh <= 0:
        return None
    s = 0.0
    for a, b in zip(verts, verts[1:] + verts[:1]):
        s += a[0] * b[1] - b[0] * a[1]
    area = abs(s) * 0.5
    return verts, area / (bw * bh)


def find_title_block(ir: DrawingIR, edge_frac: float = 0.40,
                     min_confidence: float = 0.0) -> List[Dict[str, Any]]:
    """PROPOSE the sheet's title block region and hand back its text payload.

    Standard drafting puts the title block along the sheet's RIGHT edge or
    BOTTOM-RIGHT corner: a rectangle (often subdivided into cells) dense
    with short text (sheet number, title, revision, scale, firm). Heuristic:

    1. Page extent = ``ir.width/height`` when set, else the entity bbox.
    2. Candidate outer rectangles: closed 4-5-vertex rings with
       rectangularity >= 0.8 (:func:`_rect_like`) whose bbox lies in the
       right or bottom ``edge_frac`` band of the sheet and touches within 5%
       of a sheet edge, spanning >= 5% of the sheet area's linear scale.
    3. Best candidate = highest score of **edge adjacency** (0.35, how close
       to the sheet corner/edge), **text density** (0.35, text items inside,
       saturating at 6), **nesting** (0.30, other rectangles fully inside,
       saturating at 4).
    4. Fallback (no rectangles at all — e.g. raster trace): the bbox of a
       >= 4-item text cluster in the bottom-right corner band, at low
       confidence.

    Returns at most a few proposals sorted by confidence, each:
    ``{region_bbox, entity_id | None, n_nested_rects, texts:
    [{content, position, text_id}, ...] (top-to-bottom), confidence,
    evidence, proposal_only: True}``. The ``texts`` payload IS the metadata
    read — hand it (or a render of ``region_bbox``) to the LLM to parse
    sheet number / title / revision semantics.
    """
    if ir.width and ir.height:
        page = (0.0, 0.0, ir.width, ir.height)
    else:
        page = ir.bbox()
        if page is None:
            return []
    pw, ph = page[2] - page[0], page[3] - page[1]
    if pw <= 0 or ph <= 0:
        return []

    rects = []
    for e in ir.entities:
        rl = _rect_like(e)
        if rl is None or rl[1] < 0.8:
            continue
        rects.append(e)

    texts = [t for t in ir.entities if isinstance(t, TextItem)]

    def _edge_adjacency(bb) -> float:
        d_right = abs(page[2] - bb[2]) / pw
        d_bottom = abs(bb[1] - page[1]) / ph
        return max(0.0, 1.0 - 2.0 * min(d_right, d_bottom))

    proposals = []
    page_area = pw * ph
    for e in rects:
        bb = e.bbox
        w, h = bb[2] - bb[0], bb[3] - bb[1]
        if w * h < 0.0025 * page_area:      # < 5% linear scale: a cell, not a block
            continue
        if w * h > 0.90 * page_area:        # the sheet border itself
            continue
        in_right = bb[0] >= page[0] + (1.0 - edge_frac) * pw
        in_bottom = bb[3] <= page[1] + edge_frac * ph
        if not (in_right or in_bottom):
            continue
        inside_texts = [t for t in texts
                        if bb[0] <= t.position[0] <= bb[2]
                        and bb[1] <= t.position[1] <= bb[3]]
        nested = [r for r in rects
                  if r is not e and r.bbox is not None
                  and _bbox_contains(bb, r.bbox)
                  and (r.bbox[2] - r.bbox[0]) * (r.bbox[3] - r.bbox[1])
                  < 0.95 * w * h]
        text_score = min(1.0, len(inside_texts) / 6.0)
        nest_score = min(1.0, len(nested) / 4.0)
        edge_score = _edge_adjacency(bb)
        confidence = round(0.35 * edge_score + 0.35 * text_score
                           + 0.30 * nest_score, 3)
        if confidence < min_confidence or not inside_texts:
            continue
        inside_texts.sort(key=lambda t: (-t.position[1], t.position[0]))
        proposals.append({
            "region_bbox": [_r(v) for v in bb],
            "entity_id": e.id,
            "n_nested_rects": len(nested),
            "texts": [{"content": t.content,
                       "position": [_r(t.position[0]), _r(t.position[1])],
                       "text_id": t.id} for t in inside_texts],
            "confidence": confidence,
            "evidence": {"edge_adjacency_score": _r(edge_score, 3),
                         "text_density_score": _r(text_score, 3),
                         "nesting_score": _r(nest_score, 3),
                         "path": "rectangle"},
            "proposal_only": True,
        })

    if not proposals:
        # Fallback: text cluster in the bottom-right corner band.
        corner = [t for t in texts
                  if t.position[0] >= page[0] + (1.0 - edge_frac) * pw
                  and t.position[1] <= page[1] + edge_frac * ph]
        if len(corner) >= 4:
            xs = [t.position[0] for t in corner]
            ys = [t.position[1] for t in corner]
            bb = (min(xs), min(ys), max(xs), max(ys))
            corner.sort(key=lambda t: (-t.position[1], t.position[0]))
            proposals.append({
                "region_bbox": [_r(v) for v in bb],
                "entity_id": None,
                "n_nested_rects": 0,
                "texts": [{"content": t.content,
                           "position": [_r(t.position[0]),
                                        _r(t.position[1])],
                           "text_id": t.id} for t in corner],
                "confidence": 0.3,
                "evidence": {"n_corner_texts": len(corner),
                             "path": "text_cluster_fallback"},
                "proposal_only": True,
            })

    proposals.sort(key=lambda p: p["confidence"], reverse=True)
    return proposals[:3]


def summary_stats(ir: DrawingIR) -> Dict[str, Any]:
    """Counts by type/layer, page metadata, extent, and scale/provenance.

    Also reports whether the sheet carries any extractable TEXT — real
    agency PDFs are frequently plotted with SHX (stroked) lettering, which
    has NO text layer at all: every glyph is vector line-work, so
    text_items/text_anchored_geometry/pattern searches return nothing and
    reading the lettering needs the raster/OCR leg (or vision on a region
    snip). ``has_text`` + ``text_note`` make that visible up front instead
    of letting a text query silently come back empty.
    """
    bb = ir.bbox()
    n_text = sum(1 for e in ir.entities if isinstance(e, TextItem))
    out = {
        "source": ir.source,
        "n_entities": len(ir.entities),
        "counts_by_type": ir.counts_by_type(),
        "counts_by_layer": ir.counts_by_layer(),
        "page": {
            "width": _r(ir.width), "height": _r(ir.height),
            "units": ir.units, "coordinate_space": ir.coordinate_space,
            "origin": ir.origin,
        },
        "scale": _r(ir.scale, 9) if ir.scale is not None else None,
        "scale_provenance": ir.scale_provenance,
        "bbox": [_r(v) for v in bb] if bb is not None else None,
        "warnings": list(ir.warnings),
        "has_text": n_text > 0,
    }
    if n_text == 0 and ir.entities:
        out["text_note"] = (
            "No extractable text on this sheet — likely SHX/stroked "
            "lettering (each glyph is plain line-work). Text queries "
            "(text_items, text_anchored_geometry, pattern search) will "
            "return nothing; geometry queries and construct proposals "
            "still work. Read lettering via a region snip + vision, or "
            "the raster/OCR leg.")
    return out
