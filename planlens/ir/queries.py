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
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

from planlens.ir.results import (
    Arc, Circle, Dimension, DrawingIR, Entity, Leader, Line, Polyline,
    Region, TextItem, _r,
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
    elif isinstance(e, Leader):
        d["n_vertices"] = len(e.vertices)
        d["has_arrowhead"] = bool(e.has_arrowhead)
        if e.text is not None:
            d["text"] = e.text
    elif isinstance(e, Dimension):
        d["n_defpoints"] = len(e.defpoints)
        if e.text is not None:
            d["text"] = e.text
        if e.measurement is not None:
            d["measurement"] = _r(e.measurement, 6)
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


#: Slenderness the open-3 candidate gate licenses: base <= 0.55 x leg.
#: Every measured real arrowhead sits at 0.33; 0.55 is the loosest shape
#: still admitted as "looks like a plotted arrowhead".
_OPEN3_BASE_RATIO = 0.55

#: How far ABOVE the sheet-statistic arrowhead scale a shape-verified
#: candidate may run. Single home for the multiplier: the open-3 size cap
#: and the dimension leg's coarse end-arrow prune are the SAME statement
#: ("the estimate is a statistic, and it ran ~20% under the real plotted
#: arrows on one validation sheet"), so they must move together — the
#: prune has to reach every candidate the gate admits or it silently
#: becomes the attachment test.
_LOOSE_SIZE_SCALE = 1.5

#: How far BELOW the sheet-statistic arrowhead scale a shape may fall
#: and still be read as a terminator: bbox / vertex-set diagonal >= 0.5x.
#: Measured real arrows sit at 0.75-1.0x while stipple/glyph junk is
#: overwhelmingly smaller (1160 of 1311 junk chains on the worst sheet
#: fell below half scale). Single home for the floor: the open-3 chevron
#: gate and the oriented-tipless test (:func:`_arrow_attach`) make the
#: same statement, so they must move together.
_MIN_ARROW_SIZE_SCALE = 0.5


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
            # (c - b <= 0.2c), a slender base (a <= 0.55c
            # (:data:`_OPEN3_BASE_RATIO`) — a tip angle up to ~32 deg;
            # every measured real arrow is 0.33), and
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
            if (c3 <= 0 or a3 > _OPEN3_BASE_RATIO * c3
                    or (c3 - b3) > 0.2 * c3):
                continue
            if (e.bbox is None
                    or _bbox_diag(e.bbox)
                    < _MIN_ARROW_SIZE_SCALE * max_arrowhead_size):
                continue
            # The shape gate above carries far more discrimination than
            # "small closed polyline", so this class earns a looser SIZE
            # cap: the sheet-statistic scale estimate ran ~20% under the
            # real plotted arrows on one validation sheet (12.3 pt arrows
            # vs a 10.0 pt estimate), which silently rejected every leader
            # arrowhead there. :data:`_LOOSE_SIZE_SCALE` is the single home
            # for that multiplier.
            size_cap = _LOOSE_SIZE_SCALE * max_arrowhead_size
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
        # KNOWN SHARP EDGE: a concave swallowtail / barbed arrowhead
        # (apex, two barbs, a rear notch cut deep) scores below 0.02 and
        # is never a candidate, so that terminator style is DELETED at
        # every min_confidence rather than capped (round-4 verification
        # finding 6; documented in the README, not fixed here).
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


#: A drawn arrowhead's own axis must point OUTWARD along the line it
#: terminates within 30 degrees to be accepted as a DIMENSION arrow (and to
#: escape the leader-flow letterform cap). The value is the arrow's visible
#: character, not a fixture fit: a plotted arrowhead's tip half-angle is
#: ~9-16 deg (measured 9.4 deg on every Mecklenburg real arrow), so at 30
#: deg off-axis the arrow visibly points at something ELSE. Measured
#: populations (2026-09-05, printed in score_compositions.py's ledger):
#: every true dimension arrow scores +1.000 (one manual-dim end +0.887);
#: the arrowhead-stealing impostors score -0.998..+0.208.
_ARROW_AXIS_MIN = math.cos(math.radians(30.0))

#: How far an arrowhead's centroid may sit from the shaft end it
#: terminates, as a fraction of the arrowhead scale: a shaft ENDS at its
#: arrowhead — base-anchored or apex-anchored, the endpoint sits within
#: ~half an arrow length of the candidate centroid (measured 2.2-5.4 pt
#: across every genuine ground-truth leader at a 9.31 pt scale), while a
#: letter stroke merely passes NEAR a letterform chevron (measured p50
#: 9.6 pt on the worst zero-annotation sheet). Single home for the
#: constant: the leader-flow detach cap and the dimension leg's end-arrow
#: assignment radius are the SAME physical statement, so they must move
#: together.
#:
#: Where the candidate's OWN axial length is the larger scale, that is
#: what the bound uses, and 0.75 is derivable there rather than assumed:
#: the vertex centroid of a triangle sits 1/3 of the way from its base to
#: its apex, so a legitimately terminated shaft end is at most 2/3 of the
#: candidate's own length from the centroid (apex-anchored; 1/3
#: base-anchored). 0.75 is that 2/3 plus 12.5% for plot jitter and for
#: 4-vertex shapes whose vertex centroid is not the area centroid — an
#: apex-anchored arrow lands EXACTLY on 2/3, so a bound of 2/3 with no
#: slack is a coin flip in floating point.
_ATTACH_SCALE = 0.75


def _limiting_apex_margin(base_ratio: float) -> float:
    """Apex-vote margin of the LOOSEST arrowhead the candidate gate admits.

    The gate's slenderness statement is ``base <= base_ratio * leg``
    (:data:`_OPEN3_BASE_RATIO`); this re-expresses that same statement in
    the currency :func:`_arrow_geometry` actually votes in, so the
    pointed/blunt watershed introduces no constant of its own. For the
    limiting isoceles triangle (leg 1, base ``base_ratio``, apex A, base
    corners B/C): the apex stands ``h`` from the midpoint of BC, a base
    corner stands ``sqrt(h^2/4 + 9 base^2/16)`` from the midpoint of the
    other two, and the margin is how much the winner leads by.
    """
    h = math.sqrt(1.0 - (base_ratio / 2.0) ** 2)
    d_base = math.sqrt(h * h / 4.0 + 9.0 * base_ratio ** 2 / 16.0)
    return 1.0 - d_base / h


#: Decisiveness the apex vote must show before a QUADRILATERAL-or-larger
#: candidate is credited with a tip (~0.341, derived — not typed — by
#: :func:`_limiting_apex_margin` from :data:`_OPEN3_BASE_RATIO`). A shape
#: whose farthest-from-the-others vertex leads by less than the loosest
#: admitted arrowhead's own lead has no determinable tip: every centrally
#: symmetric quad (square, rectangle, rhombus, parallelogram — the box
#: terminator family, and the letterform tiles and hatch cells that
#: mimic it) and every regular polygon leads by EXACTLY 0.
_APEX_VOTE_MARGIN = _limiting_apex_margin(_OPEN3_BASE_RATIO)


class _ArrowGeom(NamedTuple):
    """Geometry INTRINSIC to a drawn arrowhead candidate (no shaft in it).

    ``apex``/``axis``: see :func:`_intrinsic_apex_axis`. ``base`` is the
    centroid of the remaining distinct vertices, so ``|apex - base|`` is
    the candidate's own axial length — the scale its own attachment is
    measured in. ``pointed`` says whether the shape HAS a tip at all (see
    :func:`_arrow_geometry`). ``half_width`` is the half-extent
    perpendicular to the axis: the arrowhead's own spine tolerance.
    """

    apex: Point
    axis: Point
    base: Point
    pointed: bool
    half_width: float


class _Attach(NamedTuple):
    """How a candidate attaches at one shaft end (see :func:`_arrow_attach`).

    ``score`` is the alignment evidence the candidate contributes at that
    end; ``apex`` the defpoint to publish — ALWAYS publishable as it
    stands, because an off-spine apex is PROJECTED onto the shaft's
    terminal ray here (see :func:`_arrow_attach`); ``state`` one of
    ``"ok"`` (pointed, points outward) / ``"oriented"`` (tipless, but its
    long axis lies along the line — sign-blind evidence, a cluster's
    standing) / ``"blunt"`` (tipless and isotropic — alignment
    unobservable) / ``"contradicted"`` (pointed, points elsewhere);
    ``attached`` whether the shaft end is within the scale-aware bound of
    the candidate; ``on_spine`` whether the shaft runs INTO the arrowhead
    along its own spine — a statement about the QUALITY of that published
    coordinate and nothing else (see :func:`_end_tier`); ``signed`` the
    raw signed axis alignment (``None`` for candidates with no
    vertex-level axis at all, i.e. fill clusters).

    Read ``signed`` only when ``state`` is ``"ok"`` or
    ``"contradicted"``: a tipless shape's axis runs to whichever corner
    its TIED apex vote elected, so the number is not reproducible under
    vertex reordering and both callers treat it as absent (see
    :func:`_arrow_geometry`).
    """

    score: float
    apex: Point
    state: str
    attached: bool
    on_spine: bool
    signed: Optional[float]


def _distinct_vertices(verts: List[Point],
                       rel_tol: float = 0.02) -> List[Point]:
    """``verts`` with coincident corners merged, keeping path order.

    A candidate can carry the SAME corner twice: an arrowhead outline
    traced from its apex and closed back onto it ingests as
    ``[apex, b1, b2, apex]`` (verified on the ground-truth DXF side —
    sheet 21.01 carries two such candidates, and a LWPOLYLINE that
    repeats its closing vertex ingests verbatim). The apex vote's premise
    is "the vertex farthest from the centroid of the OTHER vertices", and
    a repeated vertex makes "the other vertices" false: the duplicate
    drags that centroid onto the apex itself and the elected apex flips to
    a BASE corner, inverting the axis. Tolerance is RELATIVE to the
    candidate's own size — "the same corner" is a statement about the
    arrowhead, and these shapes run 2-13 pt at plot scale but ~1 unit at
    DXF model scale, so no absolute epsilon serves both.
    """
    if len(verts) < 2:
        return verts
    xs = [p[0] for p in verts]
    ys = [p[1] for p in verts]
    tol = max(rel_tol * max(max(xs) - min(xs), max(ys) - min(ys)), 1e-9)
    out: List[Point] = []
    for p in verts:
        if not any(math.hypot(p[0] - q[0], p[1] - q[1]) <= tol for q in out):
            out.append(p)
    return out if len(out) >= 2 else verts


def _arrow_geometry(verts: List[Point]) -> _ArrowGeom:
    """Intrinsic apex/axis/base/pointedness/half-width of a drawn candidate.

    The apex is the vertex farthest from the centroid of the other
    DISTINCT vertices (:func:`_distinct_vertices`) — for a slender arrow
    triangle that is the tip between the two long legs, verified against
    every ground-truth arrow, where it reproduces the CAD defpoint; the
    axis points from that centroid to the apex, i.e. the direction the
    arrow POINTS.

    ``pointed`` asks whether the shape has a determinable TIP at all, and
    it asks it of the apex vote's OWN margin: the winner must lead the
    runner-up by :data:`_APEX_VOTE_MARGIN`, which is the lead the loosest
    arrowhead the candidate gate admits already shows. That is the same
    statement the gate makes about slenderness, re-expressed — it adds no
    constant — and it is invariant under the three things a plotted
    terminator is free to do: vertex ORDER (a max and a runner-up over a
    set), TRANSLATION and ROTATION (differences of centroids only), and
    SCALE (the lead is a fraction). Every centrally symmetric quad and
    every regular polygon leads by exactly 0, which is the box/tile/blob
    family this is here to catch.

    **Scope, deliberately narrow.** Only 4-or-more-vertex candidates can
    be called blunt. A 3-vertex candidate always keeps its tip, because
    for a triangle this test can do no better than the signed direction
    test that already judges it: an equilateral filled triangle — a REAL
    drafted arrowhead style — is a perfect tie, and calling the tie blunt
    moved its published defpoint 4.04 pt off its own drawn corner. The
    terminator styles this branch actually reaches are therefore
    BOX-LIKE QUADS (and their 5-gon relatives): a dot terminator ingests
    as a Circle or a fill cluster and an oblique tick as a 2-vertex Line,
    so neither is an :func:`_arrowhead_candidates` candidate at all.

    A blunt terminator has no pointing direction, so asking whether its
    axis runs along the line is a category error — which is what
    ``pointed`` lets the attach predicate avoid. Note which way this
    errs: a shape wrongly called pointed simply faces the signed
    direction test, which is what every drawn candidate used to face.
    """
    pts = _distinct_vertices(verts)
    ranked = []
    for i, v in enumerate(pts):
        others = [p for j, p in enumerate(pts) if j != i]
        if not others:
            continue
        c = _centroid(others)
        ranked.append((math.hypot(v[0] - c[0], v[1] - c[1]), i, v, c))
    ranked.sort(key=lambda t: t[0], reverse=True)
    d_win, _i, apex, base = ranked[0]
    axis = _unit_vec(apex[0] - base[0], apex[1] - base[1])
    nax = (-axis[1], axis[0])
    half_width = max(abs((v[0] - apex[0]) * nax[0]
                         + (v[1] - apex[1]) * nax[1]) for v in pts)
    pointed = True
    if len(pts) >= 4 and d_win > 0:
        pointed = (d_win - ranked[1][0]) / d_win >= _APEX_VOTE_MARGIN
    return _ArrowGeom(apex, axis, base, pointed, half_width)


def _intrinsic_apex_axis(verts: List[Point]) -> Tuple[Point, Point]:
    """(apex, unit axis) intrinsic to a DRAWN arrowhead candidate.

    The documented pair of :func:`_arrow_geometry` — intrinsic on purpose:
    the earlier best-vertex-vs-shaft trick (:func:`_triangle_alignment`)
    picks whichever vertex axis best matches the claiming line, which lets
    ANY triangle "align" >= cos(30 deg) with ANY crossing stroke (three
    vertex axes ~60 deg apart always bracket every direction) — the root
    cause of dimension proposals stealing true leader arrowheads on the
    ground-truth sheets.
    """
    g = _arrow_geometry(verts)
    return g.apex, g.axis


def _arrow_attach(verts: List[Point], kind: str, sdir: Point, tip: Point,
                  max_arrowhead_size: float,
                  geom: Optional[_ArrowGeom] = None) -> _Attach:
    """How a candidate attaches at a shaft end with outward direction ``sdir``.

    ONE predicate owns this physics for both composition legs — the leader
    flow reads ``signed``/``attached`` for its letterform caps, the
    dimension flow reads all of it — because the leader detach cap and the
    dimension end-arrow radius were the same statement written twice.

    - **direction**: a dimension arrow points OUTWARD along its dimension
      line, in every arrowhead style, so a POINTED candidate's intrinsic
      apex axis must satisfy ``axis . sdir >= _ARROW_AXIS_MIN``. An arrow
      pointing backward, or off at an angle (a leader arrowhead the line
      merely grazes, a letterform chevron), is ``"contradicted"`` — scored
      0.0 here and CAPPED by the caller, never deleted.
    - **attachment**: the candidate centroid within
      :data:`_ATTACH_SCALE` of the arrowhead scale — but of *this
      candidate's own* axial length when that is larger, because a drawn
      arrow's centroid sits 2/3 of its own length behind its apex. The
      sheet-statistic estimate is only a floor: the open-3 candidate gate
      deliberately admits shapes up to
      :data:`_LOOSE_SIZE_SCALE` x it (the estimate ran ~20% under the
      real plotted arrows on one validation sheet), and those candidates
      would otherwise be unable to attach to anything. A fill cluster has
      no axial length of its own, so it gets the sheet-scale bound alone
      — and that is an EXACT bound it must face, not a coarse prune: a
      cluster is exempt from the direction and spine tests, so without it
      the caller's prune radius would silently BE the whole attachment
      test for the cluster family (a stipple splash 10 pt off a shaft end
      scored 0.91 that way). The bound is loose against the cluster
      builder's own anchoring rule (centroid within 0.6x the scale of the
      endpoint it was built at), so it only ever bites when a cluster is
      offered to a DIFFERENT shaft's end than the one that created it.
    - **spine**: a dimension line runs INTO its arrowhead along the
      arrowhead's own spine — at the apex in the apex-anchored style, at
      the base center in the base-anchored multileader style, on the spine
      either way. The tolerance is the candidate's own drawn half-width,
      so it introduces no constant and scales with the arrow; it is
      independent of the direction test, so an arrow legitimately drafted
      at an angle to its line still passes.

      **This is a COORDINATE-QUALITY test, not an identity test** — the
      distinction :func:`_end_tier` rests on. An apex laterally off the
      line is produced by two unrelated situations: an arrow that
      genuinely belongs but was drafted crooked (hand-drafted, or a
      rotated block), and a foreign arrow that merely sits near this
      end. The spine test cannot tell them apart; the DIRECTION test can,
      because only the former points outward along this line. So an
      off-spine apex is not refused and does not surrender the end — it
      is PROJECTED onto the shaft's terminal ray (the same
      :func:`_reach_on_ray` machinery the cluster leg uses). The lateral
      component is the drafting wobble and is the only untrustworthy
      part; the AXIAL component is the real measurement and is kept. The
      earlier substitution — publish the shaft's own tip — is right only
      where the arrows sit INSIDE the line; on the split leg the arrows
      sit outside their half-shafts, so it moved the published defpoint
      by a whole arrow length (measured 7.2 pt on the ground-truth
      geometry).

    Terminator styles with no tip take the road fill clusters take —
    sign-blind :func:`_cluster_alignment`, and an apex on the shaft's
    terminal ray, clamped never to fall behind the tip. Two states come
    out of it: ``"oriented"`` when the shape's own long axis runs along
    the line (a diamond, a flat-tipped closed arrow, an oblong tick —
    sign-blind evidence of belonging, exactly a cluster's), and
    ``"blunt"`` when the shape is isotropic (a box, a tile, a pentagon
    dot) and so has no axis to consult at all — the caller treats THAT
    alignment channel as UNOBSERVABLE rather than contradicted, and holds
    the construct below the call threshold.

    Fill clusters keep the sign-blind cluster alignment and are exempt
    from the spine test: they are CONSTRUCTED straddling a shaft
    endpoint, so it is vacuous.
    """
    if kind == "fill_cluster":
        centroid = _centroid(verts)
        attached = (math.hypot(centroid[0] - tip[0], centroid[1] - tip[1])
                    <= _ATTACH_SCALE * max_arrowhead_size)
        return _Attach(_cluster_alignment(verts, sdir)[0],
                       _reach_on_ray(verts, sdir, tip), "ok", attached, True,
                       None)
    g = geom if geom is not None else _arrow_geometry(verts)
    centroid = _centroid(verts)
    h = math.hypot(g.apex[0] - g.base[0], g.apex[1] - g.base[1])
    attached = (math.hypot(centroid[0] - tip[0], centroid[1] - tip[1])
                <= _ATTACH_SCALE * max(max_arrowhead_size, h))
    signed = g.axis[0] * sdir[0] + g.axis[1] * sdir[1]
    if not g.pointed:
        # TIPLESS terminator (box, diamond, flat-tipped arrow, blob): no
        # tip, so no pointing direction — the signed test is meaningless
        # on it and the shape has no intrinsic spine either. Fall back to
        # the shaft's own axis for both: sign-blind cluster alignment and
        # a spine tolerance from the shape's half-extent across that ray.
        #
        # The published coordinate is the centroid projected on the
        # terminal ray, NEVER BEHIND THE TIP. Two drafted anatomies meet
        # here and the clamp serves both: a box or dot block is inserted
        # centred AT the defpoint, so its centroid IS the defpoint and the
        # line runs to the shape's middle (projection = tip, clamp
        # inert); a diamond or flat-tipped closed arrow is drawn INSIDE
        # the line with its far corner at the defpoint, so its centroid
        # sits half a terminator behind the line's end — and the line's
        # end is drawn fact. Unclamped, the diamond published 3 pt short
        # at each end of a 100 pt dimension (measured: 94.0 for 100.0).
        # A tipless shape wholly BEYOND the tip (an arrows-outside
        # placement) still publishes its projected centroid: a centred
        # block is exact there, a tip-at-defpoint block reads half its
        # own length short, and nothing in the geometry says which it is.
        nax = (-sdir[1], sdir[0])
        half = max(abs((v[0] - centroid[0]) * nax[0]
                       + (v[1] - centroid[1]) * nax[1]) for v in verts)
        spine = abs((tip[0] - centroid[0]) * nax[0]
                    + (tip[1] - centroid[1]) * nax[1])
        along = max(0.0, (centroid[0] - tip[0]) * sdir[0]
                    + (centroid[1] - tip[1]) * sdir[1])
        apex = (tip[0] + sdir[0] * along, tip[1] + sdir[1] * along)
        # ORIENTED vs BLUNT (the policy half). A tipless shape is
        # "oriented" — admitted with a fill cluster's standing: scored on
        # its sign-blind alignment, named for arbitration, never ranked
        # above a directional arrowhead — when THREE things hold, each a
        # statement the arrowhead gates already make elsewhere:
        #
        # 1. its own LONG AXIS runs along the line (the cluster gate's
        #    elongation and the signed test's cos-30 cone): a diamond or
        #    flat-tipped arrow lying along the line it ends says,
        #    sign-blind, that it terminates THIS line rather than one
        #    crossing it, which sees that axis at ~90 deg;
        # 2. it is COMMENSURATE with the sheet's arrowheads — vertex-set
        #    diagonal >= :data:`_MIN_ARROW_SIZE_SCALE` x the arrowhead
        #    scale, the open-3 chevron gate's own floor. Measured on the
        #    corpus (round-5 verification, D3): every shape the rank
        #    admitted without this floor was a 0.6-2 pt SHX glyph fragment
        #    — 6 of 6 junk, 0 of 6 terminators — and with a real arrow at
        #    the other end and witnesses, such a fragment CALLED at 1.0;
        # 3. it TAPERS toward the end it marks, measured in the SHAPE'S
        #    OWN frame (its PCA long axis, turned to face the line's
        #    end): its lateral half-width at the far extreme of that axis
        #    is at most :data:`_OPEN3_BASE_RATIO` of its full half-width
        #    — the slenderness the chevron gate asks of a base against
        #    its legs, asked here of the marking end against the body. A
        #    diamond narrows to a vertex, a flat-tipped arrow to a short
        #    edge; a RECTANGLE does not narrow at all, however it is
        #    turned, and a rectangle at each end of a line with periodic
        #    ticks is a graphic SCALE BAR, which the rank otherwise read
        #    as a 0.944 dimension. (Measured in the shaft's frame instead,
        #    a diamond drafted 20 deg crooked failed and a rectangle whose
        #    diagonal happened to lie along the line passed.)
        #
        # Everything else stays "blunt": an ISOTROPIC shape (a box, a
        # tile, a pentagon dot) has no axis to consult, so its alignment
        # channel is UNOBSERVABLE, not merely sign-blind; a sub-scale
        # fragment and an untapered oblong are glyph strokes and tiles.
        # Blunt is capped below the call threshold unconditionally and
        # withheld from arbitration, because a plain rectangle at the far
        # end of a crossing line otherwise founded a 0.915 dimension that
        # claimed a real leader's arrowhead. What the oriented rank still
        # admits is that scene with an arrow-scale DIAMOND lying along
        # the crossing line — which is, by every observable, a dimension.
        # Pinned both ways in test_tipless_terminators.
        align, deg = _cluster_alignment(verts, sdir)
        oriented = deg >= 0.0 and align >= _ARROW_AXIS_MIN
        if oriented:
            xs = [v[0] for v in verts]
            ys = [v[1] for v in verts]
            oriented = (math.hypot(max(xs) - min(xs), max(ys) - min(ys))
                        >= _MIN_ARROW_SIZE_SCALE * max_arrowhead_size)
        if oriented:
            ax = _pca_axis(verts)[0]
            if ax[0] * sdir[0] + ax[1] * sdir[1] < 0:
                ax = (-ax[0], -ax[1])
            lat = [abs((v[0] - centroid[0]) * -ax[1]
                       + (v[1] - centroid[1]) * ax[0]) for v in verts]
            proj = [(v[0] - centroid[0]) * ax[0] + (v[1] - centroid[1]) * ax[1]
                    for v in verts]
            pmax = max(proj)
            band = 0.02 * max(pmax - min(proj), 1e-9)
            far_half = max(w for w, p in zip(lat, proj) if p >= pmax - band)
            oriented = far_half <= _OPEN3_BASE_RATIO * max(lat)
        return _Attach(align, apex, "oriented" if oriented else "blunt",
                       attached, spine <= half, signed)
    nax = (-g.axis[1], g.axis[0])
    spine = abs((tip[0] - g.apex[0]) * nax[0]
                + (tip[1] - g.apex[1]) * nax[1])
    on_spine = spine <= g.half_width
    # Off spine, publish the apex PROJECTED on the shaft's terminal ray
    # (see the spine bullet above): keep the axial measurement, discard
    # the lateral wobble. On spine the projection is a no-op to within
    # the arrow's own half-width, so the drawn apex is published as-is.
    apex = g.apex if on_spine else _reach_on_ray([g.apex], sdir, tip)
    if signed < _ARROW_AXIS_MIN:
        # CAP, DON'T DELETE: no alignment evidence (0.0 — absent, not
        # negative), and the caller ranks it below every uncorroborated
        # construct. It stays a proposal.
        return _Attach(0.0, apex, "contradicted", attached, on_spine,
                       signed)
    return _Attach(signed, apex, "ok", attached, on_spine, signed)


def _reach_on_ray(verts: List[Point], sdir: Point, tip: Point) -> Point:
    """Farthest reach of ``verts`` ON the shaft's terminal ray from ``tip``.

    Two callers, one statement — "keep the axial measurement, drop the
    lateral offset". For a fill cluster the whole splash is offered (see
    below); for a POINTED candidate whose apex sits off the line's spine,
    the apex alone is offered and this projects it (:func:`_arrow_attach`).

    A stroke-cluster arrowhead is a stipple SPLASH of 0.06-3 pt fragments
    with no vertex-level tip — which is why :func:`_cluster_alignment` is
    sign-blind. What the splash DOES carry is how far the fill reaches
    ALONG the line it terminates; laterally the defpoint is ON the
    dimension line by construction, because that line is what the arrow
    terminates. Taking the farthest member CENTER instead published an
    arbitrary stipple dot up to 4.5 pt off the line as the defpoint
    (measured over the 33 cluster-ended proposals on the ground-truth
    corpus: p50 0.72 pt, max 4.48 pt off-axis; on-ray it is p90 0.008 pt).
    A cluster sitting entirely behind the tip clamps to the tip itself —
    the shaft-endpoint fallback, reached by construction.
    """
    reach = max(0.0, max((v[0] - tip[0]) * sdir[0] + (v[1] - tip[1]) * sdir[1]
                         for v in verts))
    return (tip[0] + sdir[0] * reach, tip[1] + sdir[1] * reach)


def _end_tier(att: _Attach, kind: str) -> int:
    """SOUNDNESS rank of a candidate at one shaft end — 0 best, 2 worst.

    This is the IDENTITY question, and only the identity question: does
    this candidate belong to THIS line? Two observations answer it, and
    they are the two :func:`_arrow_attach` makes about the candidate
    itself —

    - **direction** — a real terminator points OUTWARD along the line it
      terminates; a foreign arrowhead's axis is uncorrelated with it. A
      contradicted candidate says, in its own geometry, that it belongs
      to something else: tier 2.
    - **attachment** — a shaft ENDS at its terminator. A detached
      candidate is one this line merely passes near: tier 1.

    Everything else the attach test reports is about the QUALITY of the
    published coordinate, not about identity, and must not be ranked
    here. ``on_spine`` above all: an apex off the line's axis is equally
    the signature of a genuinely-owned arrow drafted crooked and of a
    foreign one, so demoting on it handed the end to whatever farther
    candidate happened to be straight — including a neighbouring real
    leader's arrowhead, which ``exclude_dimensions`` then DELETED from
    the leader flow at every ``min_confidence`` (measured: a 120 pt
    dimension whose right arrow is drafted 10 deg off axis lost its end
    to a leader arrow 4.0-6.4 pt farther out, published its defpoint
    1.6-4.0 pt off, and took the real leader with it). The wobble is
    handled where it belongs — by projecting the coordinate
    (:func:`_reach_on_ray`) and by the cap ladder.

    ABSENCE OF EVIDENCE IS NOT EVIDENCE, so a candidate on which the
    direction test cannot be RUN — a blunt or oriented terminator, or a
    fill cluster, none of which carries a pointing direction — sits at
    tier 1, the "evidence unobservable" rank. Between tier 1 and tier 0
    the better SEATED candidate wins (:func:`_award_end`, which explains
    why a tier-first rule with a distance exception was measured wrong);
    the tier itself decides only a tie, and keeps tier 2 from ever
    winning on distance. What protects a directional arrow from a
    sign-blind splash is therefore its seat, not its tier: a real arrow
    seats at 0.0 in either drafted style and cannot be beaten, and the
    splash round 4 measured 5 pt off an end (which then took the end
    from a real drawn arrow and lifted the construct from 0.45 to 0.908
    — CALLED) seats ~3.5 pt by its nearest member and loses to that
    arrow.

    WITHIN a tier candidates are ordered by :func:`_seat_distance` —
    where the shaft end sits relative to the candidate's own apex, base
    centre or nearest member — then by centroid distance, never by
    centroid distance alone, which systematically read the true
    arrows-inside arrow as farther than junk beside it.
    """
    if att.state == "contradicted":
        return 2
    if (att.state in ("blunt", "oriented") or kind == "fill_cluster"
            or not att.attached):
        return 1
    return 0


def _seat_distance(verts: List[Point], kind: str, sdir: Point, tip: Point,
                   geom: Optional[_ArrowGeom]) -> float:
    """How far the shaft end sits from where THIS candidate says it is.

    The distance that orders candidates at one end. Not the centroid
    distance: a drawn arrow's centroid sits 2/3 of its own length behind
    its apex, so the TRUE arrows-inside terminator — apex exactly on the
    line's end — measures ~4.8 pt "away" by centroid at corpus scale,
    and glyph junk parked beside or just beyond the end measured nearer
    (verified fixtures: a chevron at apex (103, 3) took the end from the
    arrow whose apex was the end, and one at (108, 0) took it and
    published 108 for a 100 pt line).

    A shaft meets an arrowhead in two drafted ways and only two — at the
    APEX (arrows inside, the dimension line runs under the head to the
    defpoint) or at the BASE CENTRE (arrows outside, and the base-anchored
    multileader style; measured on the ground-truth plots, where the
    half-shafts stop at the arrow bases). The seat of a pointed candidate
    is the nearer of the two: 0.0 for a real terminator in either style,
    a real gap for a foreign one. A fill cluster's seat is the distance
    from the shaft end to its NEAREST member: the end is IN the splash
    when that is ~0, whatever the splash's shape. Its centroid was the
    wrong measure for the corpus's own anatomy — a stipple arrowhead's
    centroid sits 0.5-2 pt from the end it terminates (the splash is
    lopsided, or reaches outward one arrow length), and measured against
    the centroid a real cluster lost its end to a foreign chevron seated
    0.94 pt beyond it at every realistic offset (round-5 verification,
    D1). A tipless shape seats at its centroid (a centred box block) or
    at either axial extreme (a diamond drawn inside the line, whose far
    corner is the line's end) — whichever is nearest.
    """
    if kind == "fill_cluster" or geom is None:
        return min(math.hypot(v[0] - tip[0], v[1] - tip[1]) for v in verts)
    c = _centroid(verts)
    d_c = math.hypot(c[0] - tip[0], c[1] - tip[1])
    if geom.pointed:
        return min(math.hypot(geom.apex[0] - tip[0], geom.apex[1] - tip[1]),
                   math.hypot(geom.base[0] - tip[0], geom.base[1] - tip[1]))
    proj = [((v[0] - tip[0]) * sdir[0] + (v[1] - tip[1]) * sdir[1], i, v)
            for i, v in enumerate(verts)]
    v_near, v_far = min(proj)[2], max(proj)[2]
    return min(d_c,
               math.hypot(v_near[0] - tip[0], v_near[1] - tip[1]),
               math.hypot(v_far[0] - tip[0], v_far[1] - tip[1]))


#: The per-end candidate loop may skip a candidate whose centroid
#: distance minus its own radius already exceeds the best SOUND seat
#: found so far (see the loop in :func:`find_dimensions`). Exact by the
#: triangle inequality, so this is a performance switch, not a behaviour
#: switch — and the test suite runs scenes with it off to keep it that
#: way.
_EXACT_SEAT_PRUNE = True


def _award_end(finalists: Dict[int, Dict[str, Any]]
               ) -> Optional[Dict[str, Any]]:
    """The candidate that owns an end, from the best-seated of each tier.

    Between the two SOUND tiers — directional (0) and direction
    unobservable (1) — the better SEATED candidate wins, and an exact tie
    goes to the directional one. A contradicted candidate (tier 2) wins
    only when nothing sound is there at all; it never beats a sound
    candidate on distance, however near (round 2's blocker 2: a nearer
    letterform chevron shadowing the true arrow).

    Why seat alone decides between tiers 0 and 1, and not a
    tier-first rule with a distance exception: the round-5 repair tried
    "tier 0 wins unless tier 1 is seated ten times nearer", and the
    independent verification showed that window to be 0.05-0.4 pt wide
    against real chevron seats — the corpus's own stipple-rendered
    arrowheads, seated 0.5-2 pt from the end they terminate, lost that
    end to a foreign chevron seated 0.94 pt beyond it at every realistic
    offset, put the foreign arrowhead into ``arrowhead_ids``, and let
    ``exclude_dimensions`` delete the leader that owned it (the tip,
    which ranked by nearness alone, got every case right). The
    principle round 4 wanted the tier for — a sign-blind splash must not
    take an end from the arrow that is genuinely seated on it — is
    delivered by the SEAT metric instead: a real arrow seats at 0.0 in
    either drafted style and cannot be beaten; the splash that round 4
    measured 5 pt off an end seats ~3.5 pt by its nearest member and
    loses to a crooked arrow seated 1.5-2.5 pt (pinned both ways in
    test_end_ownership). What remains, documented there: a splash whose
    nearest member sits closer to the end than a crooked arrow's base
    centre does take the end.
    """
    if not finalists:
        return None
    sound = finalists.get(0)
    blind = finalists.get(1)
    if sound is not None and blind is not None:
        return blind if blind["seat"] < sound["seat"] else sound
    return finalists[min(finalists)]


def _ending_near_from_grid(grid: "_EndpointGrid", point: Point,
                           radius: float,
                           limit: Optional[int] = None) -> List[Dict[str, Any]]:
    """Same result shape/order as :func:`entities_ending_near`, via a
    prebuilt :class:`_EndpointGrid` (the fast path for composition loops).

    ``limit`` returns only the ``limit`` nearest hits — the SAME hits, in
    the SAME order, as slicing the full result would give (one stable
    sort on distance either way), but the reference dicts are built only
    for those. The witness search reads 50 hits per tip on sheets where
    dashed and stippled linework put a thousand endpoints in range;
    building and rounding a thousand dicts to keep fifty was 64 % of
    sheet 3001's default-threshold run (profiled 2026-09-11).
    """
    hits = list(grid.near(point, radius))
    hits.sort(key=lambda t: t[4])
    if limit is not None:
        hits = hits[:limit]
    out = []
    for e, label, pt, other, d in hits:
        ref = _ref(e)
        ref["end"] = label
        ref["end_point"] = [_r(pt[0]), _r(pt[1])]
        ref["other_end"] = [_r(other[0]), _r(other[1])]
        ref["distance"] = _r(d)
        out.append(ref)
    return out


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

    **Letterform discipline (two confidence CAPS, never deletions).** On
    SHX-plotted sheets every letter is stroked line-work, and lettering-
    heavy sheets used to drown the caller in letterform proposals at
    default confidence (measured 295 above-0.5 proposals on a
    zero-annotation ground-truth notes sheet, the top ones title-block
    letters). Two structural facts about a real leader cap any proposal
    that violates them to 0.45 (below the conventional 0.5 threshold,
    still visible to a caller who lowers ``min_confidence``):

    - **arrow direction** (``evidence.arrow_direction_violation``): a
      drawn arrowhead POINTS outward along its shaft's terminal
      direction — signed intrinsic-apex-axis alignment >= cos(30 deg)
      (:func:`_intrinsic_apex_axis`; measured +1.000 on every genuine
      ground-truth leader, near-random for letter chains vs neighboring
      strokes);
    - **arrow attachment** (``evidence.arrow_detached``): the shaft
      endpoint sits within :data:`_ATTACH_SCALE` of the arrowhead scale
      — or of the candidate's OWN axial length when that is larger — of
      the candidate centroid, because a shaft ENDS at its arrowhead
      (measured 2.2-5.4 pt on every genuine ground-truth leader at a
      9.31 pt scale) while a letter stroke merely passes near a
      letterform chevron (measured p50 9.6 pt). The own-length term only
      ever widens the bound for a candidate the gate admitted as a
      shape-verified arrowhead LARGER than the sheet estimate, and it is
      geometry, not slack: see :data:`_ATTACH_SCALE`. Shared with the
      dimension leg (:func:`_arrow_attach`); a fill cluster has no own
      length and keeps the sheet-scale bound;
    - **blunt terminator** (``evidence.blunt_terminator``): a candidate
      with no determinable tip (:func:`_arrow_geometry` — in practice a
      box-like quad) carries no leader direction at all, so it is capped
      outright rather than judged on an axis its tied apex vote picked
      arbitrarily. ``signed_axis_alignment`` is withheld for those, since
      the number would not be reproducible under vertex reordering.

    Measured together on the ground-truth corpus (2026-09-05): the worst
    lettering sheet fell 295 -> 2 above-default proposals with recall
    unchanged (see score_compositions.py's ledger).

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

    # NATIVE leaders (DXF LEADER/MULTILEADER ingested as Leader entities):
    # the source declares them, so they surface at confidence 1.0 with
    # evidence "native_dxf" — no composition needed. Composed proposals
    # whose tip lands on a native tip are CAPPED as duplicates below;
    # composition still runs for anything drawn manually (plain line +
    # triangle) that never became a LEADER entity.
    native_props: List[Dict[str, Any]] = []
    native_tips: List[Tuple[Point, str]] = []
    for L in ir.entities:
        if not isinstance(L, Leader) or not L.vertices:
            continue
        pts = [tuple(p) for p in L.vertices]
        tip, tail = pts[0], pts[-1]
        native_tips.append((tip, L.id))
        native_props.append({
            "tip_xy": [_r(tip[0]), _r(tip[1])],
            "tail_xy": [_r(tail[0]), _r(tail[1])],
            "vertices": [[_r(p[0]), _r(p[1])] for p in pts],
            "arrowhead_id": None,
            "shaft_id": L.id,
            "text": L.text,
            "text_id": None,
            "text_distance": None,
            "confidence": 1.0,
            "evidence": {"path": "native_dxf",
                         "has_arrowhead": bool(L.has_arrowhead)},
            "proposal_only": True,
        })

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

        # The letterform caps read the SHARED attach predicate
        # (:func:`_arrow_attach`) for their physics, but keep their own
        # fold-blind :func:`_triangle_alignment` SCORE: a leader may be
        # drawn to either end of its arrowhead (see that function), so the
        # leader flow scores sign-blind and only CAPS on the signed test.
        att = _arrow_attach(verts, arrow_kind, shaft_dir, tip_xy,
                            max_arrowhead_size)
        arrow_blunt = False
        if arrow_kind == "fill_cluster":
            align_score, align_deg = _cluster_alignment(verts, shaft_dir)
            signed_axis = None
        else:
            align_score, align_deg = _triangle_alignment(verts, shaft_dir)
            # SIGNED arrow-direction check (the letterform cap): a real
            # leader arrowhead POINTS outward along its shaft's terminal
            # direction — in the apex-anchored chevron style AND the
            # base-anchored multileader style alike, the intrinsic apex
            # axis (:func:`_intrinsic_apex_axis`) satisfies
            # ``axis . shaft_dir ~ +1`` (measured +1.000 on every genuine
            # ground-truth leader). A letterform chevron paired with a
            # neighboring stroke has a RANDOM axis-vs-stroke direction, so
            # most lettering junk fails the signed test (measured: 295 ->
            # 81 above-default proposals on the worst zero-annotation
            # sheet). Violations are CAPPED below the 0.5 call threshold,
            # not deleted — proposals stay visible to a caller who lowers
            # min_confidence, mirroring the dimension no-text cap. A
            # TIPLESS candidate (:func:`_arrow_geometry`) has no tip, so
            # its axis is whichever corner the tied apex vote happened to
            # elect and its signed value carries no information: it is
            # capped OUTRIGHT here rather than judged on that number,
            # which is both deterministic (the elected corner is not) and
            # the conservative reading — a leader is drawn to a POINTING
            # arrowhead in the styles this corpus contains, so exempting
            # blunt shapes would uncap letterform tiles. The dimension
            # leg's "oriented" distinction (a tipless shape lying along
            # the line) is deliberately NOT honoured here: a dimension
            # terminator only has to mark an END, which a sign-blind
            # shape can do, while a leader arrowhead has to POINT AT
            # something, which it cannot.
            signed_axis = (None if att.state in ("blunt", "oriented")
                           else att.signed)
            arrow_blunt = att.state in ("blunt", "oriented")

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
        arrow_backward = (signed_axis is not None
                          and signed_axis < _ARROW_AXIS_MIN)
        # DETACHED-ARROW cap (the other half of the letterform story): a
        # leader shaft ENDS at its arrowhead. See :func:`_arrow_attach` for
        # the bound (79 of the worst zero-annotation sheet's 81
        # sign-passing junk proposals fall beyond it) and for why cluster
        # candidates are exempt. Same cap-not-delete discipline.
        arrow_detached = not att.attached
        if arrow_backward or arrow_detached or arrow_blunt:
            confidence = min(confidence, _UNCORROBORATED_CAP)
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
                **({"signed_axis_alignment": _r(signed_axis, 3)}
                   if signed_axis is not None else {}),
                **({"arrow_direction_violation": True}
                   if arrow_backward else {}),
                **({"blunt_terminator": True} if arrow_blunt else {}),
                **({"arrow_detached": True} if arrow_detached else {}),
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

    if native_tips:
        # A composed proposal landing on a native tip is the SAME leader read
        # twice. Cap it (:func:`_capped_by_native`) instead of dropping it.
        proposals = [
            _capped_by_native(
                p, _native_tip_at(native_tips, p["tip_xy"],
                                  max_arrowhead_size))
            for p in proposals]
        proposals = [p for p in proposals
                     if p["confidence"] >= min_confidence]
        proposals.extend(p for p in native_props
                         if p["confidence"] >= min_confidence)

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

#: Ceiling for a construct whose defining precondition is actively
#: CONTRADICTED — an arrowhead whose own axis points somewhere other than
#: along the line claiming it. Strictly below :data:`_UNCORROBORATED_CAP`,
#: and that ORDERING is the whole of the number's justification: evidence
#: that is missing outranks evidence that says no. Same shape of argument
#: that put 0.45 below the conventional 0.5 call threshold. Nothing is
#: dropped — such a proposal is still returned to a caller who lowers
#: ``min_confidence`` this far, flagged ``arrow_direction_violation``.
_CONTRADICTED_CAP = 0.25


def _native_tip_at(native_tips: List[Tuple[Point, str]], xy: Point,
                   tol: float) -> Optional[str]:
    """Id of the native leader whose tip coincides with ``xy``, else None."""
    for tip, nid in native_tips:
        if math.hypot(xy[0] - tip[0], xy[1] - tip[1]) <= tol:
            return nid
    return None


def _native_span_at(native_spans: List[Tuple[Point, Point, str]],
                    a_xy: Point, b_xy: Point, tol: float) -> Optional[str]:
    """Id of the native dimension measuring the SAME span, else None.

    BOTH ends must match one native's two defpoints, in either order. A
    single shared endpoint is two dimensions sharing a witness line, which
    is ordinary drafting rather than duplication — matching on loose
    defpoints instead deleted a composed construct whenever any one of its
    ends fell near any native defpoint, however different the spans.
    """
    def near(u: Point, v: Point) -> bool:
        return math.hypot(u[0] - v[0], u[1] - v[1]) <= tol
    for p_xy, q_xy, nid in native_spans:
        if ((near(a_xy, p_xy) and near(b_xy, q_xy))
                or (near(a_xy, q_xy) and near(b_xy, p_xy))):
            return nid
    return None


def _capped_by_native(proposal: Dict[str, Any],
                      native_id: Optional[str]) -> Dict[str, Any]:
    """``proposal`` capped when a native declaration supersedes it.

    The source's own annotation entity is authoritative, so a composed
    reading of the same construct is REDUNDANT — and redundant is not
    wrong. CAP, DON'T DELETE: the proposal stays visible to a caller who
    lowers ``min_confidence``, carrying the id of the native that outranked
    it, and only the native is CALLED at the default threshold. Deleting it
    cost a 0.978-confidence, witness-corroborated construct outright at
    ``min_confidence=0.0``, and DXF block explosion has since made
    block-nested natives common enough that the delete kept getting
    more expensive.
    """
    if native_id is None:
        return proposal
    out = dict(proposal)
    out["confidence"] = min(out["confidence"], _UNCORROBORATED_CAP)
    out["evidence"] = {**out["evidence"], "superseded_by_native": native_id}
    return out


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
      (:func:`_arrowhead_candidates`) attached at EACH endpoint — the
      drafting style whose value text sits ABOVE the line. The length
      floor applies to the construct's EXTENT (the distance between the
      two published ends), not to the shaft: in the arrows-outside style
      the shaft stops at the arrow bases and under-reports the extent, so
      a real narrow dimension plots a SHORT line between two outward
      arrows (ground-truth 'T=' style: a 9.8 pt shaft spanning 24.2 pt).
      A construct under that floor is CAPPED, not dropped. Proposal ends
      are the arrows' intrinsic APEX points — the CAD defpoints — falling
      back to the shaft's own end at any end the shaft does not enter
      along the arrowhead's spine (that fallback is real and
      load-bearing, and caps the proposal).
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
      the gap. A shaft whose OTHER end merely has a contradicted
      candidate still founds a half by design: a real half has nothing at
      its inner end, and what junk happens to sit there says nothing
      about the half. A half whose own arrow is off its spine is founded
      on the SHAFT'S END instead and capped — refusing it deleted every
      real split dimension whose arrow is drafted more than
      ``asin(base / 2 leg)`` off the axis, which is 4.8 deg for the
      slenderest heads and 16 deg for the bluntest: an inverted incentive
      and a deletion the cap ladder exists to avoid. A shaft whose ONLY
      candidate is contradicted also founds a half, so that the split
      leg's :data:`_CONTRADICTED_CAP` is a rung the code can reach.

    **Arrow attachment is SIGNED and ON-SPINE** (:func:`_arrow_attach`):
    a POINTED candidate attaches at a shaft end when its intrinsic apex
    axis points OUTWARD along the shaft within 30 deg
    (:data:`_ARROW_AXIS_MIN`) and the shaft runs INTO it along its own
    spine — a dimension arrow points along its dimension line and is
    entered along its middle, by construction. The direction test is what
    stops a dimension proposal from STEALING a leader's arrowhead via a
    crossing witness line or a grazing stroke (the root cause of 4 of the
    4 residual ground-truth leader misses before Phase 3.2:
    ``exclude_dimensions`` dropped true leaders whose triangles a false
    dimension had claimed with the old fold-blind best-vertex alignment,
    which cannot fall below ~cos(30 deg) for ANY triangle vs ANY
    direction). Neither test DELETES: a contradicted arrow scores 0.0 and
    caps the proposal at :data:`_CONTRADICTED_CAP`, an off-spine or
    detached end publishes the shaft's own end and caps at
    :data:`_UNCORROBORATED_CAP`. Fill-cluster candidates have no vertex
    apex and keep the sign-blind cluster alignment (they still face the
    attachment bound; nothing about them is exempt from every test).

    **TIPLESS terminators** — in practice BOX-LIKE QUADRILATERALS, the
    only tipless family :func:`_arrowhead_candidates` can actually
    deliver (see :func:`_arrow_geometry` for why, and for why a dot or
    an oblique tick never reaches here at all) — have no pointing
    direction. They split two ways (:func:`_arrow_attach`):

    - **oriented** — the shape's own long axis runs along the line (a
      diamond, a flat-tipped closed arrow, an oblong tick). That is
      sign-blind evidence that it terminates THIS line rather than one
      crossing it, exactly what a fill cluster's alignment carries, so
      it is admitted with a cluster's standing: scored on that
      alignment, named in ``arrowhead_ids``, called when the rest of the
      construct corroborates it, and never ranked above a directional
      arrowhead (:func:`_end_tier`). Its id is also listed under
      ``evidence.oriented_terminator_ids``.
    - **blunt** — isotropic (a box, a square tile, a pentagon dot): no
      axis to consult, so the alignment channel is UNOBSERVABLE rather
      than contradicted and the remaining channels are renormalized.
      Such a construct is held below the call threshold UNCONDITIONALLY
      and never named in ``arrowhead_ids``: witnesses and a value text
      can corroborate that something is measured here, but nothing about
      a shape with no axis can corroborate that it terminates THIS line
      rather than one crossing it, so it must never win an
      ``exclude_dimensions`` arbitration against a real arrowhead. Its id
      stays visible under ``evidence.blunt_terminator_ids``.

    Both publish the tip-clamped centroid projection as their end (see
    :func:`_arrow_attach` for the two anatomies that clamp serves).

    **Who owns an end** (:func:`_end_tier`, :func:`_seat_distance`,
    :func:`_award_end`): every candidate within the attach prune is
    ranked by soundness tier — directional and attached; direction
    unobservable (cluster, tipless, detached); contradicted — and,
    within a tier, by SEAT: the shaft end's distance from the
    candidate's own apex or base centre (0.0 for a real terminator in
    either drafted style) or, for a fill cluster, from its nearest
    member (0.0 when the end is in the splash) — not from its centroid,
    which read the true arrows-inside arrow as 2h/3 away and a real
    stipple arrowhead as 0.5-2 pt away, and let glyph junk beside or
    just beyond the end take it. The best-seated of the directional and
    sign-blind tiers are then compared once and the BETTER SEATED wins
    (a tie to the directional one), so a stipple-rendered arrowhead
    that a line actually ends in is not lost to a foreign chevron seated
    beyond it — which was putting the foreign arrowhead into
    ``arrowhead_ids`` and deleting the leader that owned it through
    ``exclude_dimensions``. A contradicted candidate never wins on
    distance.

    Scoring (each leg): **alignment** (0.40) — the signed attach
    alignment above (mean over both arrows; the split leg also
    multiplies by the halves' collinearity); **text** (0.30) — nearest
    TextItem to the construct midpoint (continuous) or the GAP CENTER
    (split) within ``text_radius``; **extension lines** (0.30) — at each
    APEX (the defpoint, where witness lines actually cross), any OTHER
    entity of length >= 0.5x ``max_arrowhead_size`` ending within
    ``search_radius`` whose terminal direction is >= 55 deg off the shaft
    axis (the length floor keeps stipple/hatch micro-fragments from
    counting as witness lines — measured 392 sub-3-pt "witnesses" on one
    real stippled sheet before the floor).

    NO-TEXT sheets (SHX-stroked plots): confidence renormalizes over the
    observable components as before, but a CONTINUOUS proposal is capped at
    ``0.45`` (:data:`_UNCORROBORATED_CAP`, below the conventional 0.5 call
    threshold) unless corroborated by witness lines at BOTH ends — on such
    sheets an uncorroborated two-triangles-on-a-line score is dominated by
    hatch/stipple misreads (measured ~0 precision at 0.9+ renormalized
    confidence on the worst validation sheet). Split-shaft proposals are
    exempt from THAT cap: the paired structure is itself the
    corroboration. The direction/spine/attachment/blunt/extent caps above
    apply to both legs.

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

    # NATIVE dimensions (DXF DIMENSION ingested as Dimension entities):
    # surfaced at confidence 1.0 with evidence "native_dxf" — the ends are
    # the entity's own defpoints (dimension-line point + the far measured
    # origin when present). Composition still runs for manually drafted
    # dimensions; a proposal measuring a native's own span is capped.
    native_props: List[Dict[str, Any]] = []
    native_spans: List[Tuple[Point, Point, str]] = []
    for D in ir.entities:
        if not isinstance(D, Dimension) or not D.defpoints:
            continue
        dps = [tuple(p) for p in D.defpoints]
        a_xy = dps[0]
        b_xy = dps[1] if len(dps) > 1 else dps[0]
        native_spans.append((a_xy, b_xy, D.id))
        mid = (0.5 * (a_xy[0] + b_xy[0]), 0.5 * (a_xy[1] + b_xy[1]))
        native_props.append({
            "end_a_xy": [_r(a_xy[0]), _r(a_xy[1])],
            "end_b_xy": [_r(b_xy[0]), _r(b_xy[1])],
            "midpoint_xy": [_r(mid[0]), _r(mid[1])],
            "length": _r(math.hypot(b_xy[0] - a_xy[0], b_xy[1] - a_xy[1])),
            "angle_deg": (_r(_seg_angle(a_xy, b_xy), 2)
                          if a_xy != b_xy else None),
            "shaft_id": D.id,
            "arrowhead_ids": [],
            "extension_line_ids": [],
            "text": D.text,
            "text_id": None,
            "text_distance": None,
            "confidence": 1.0,
            "evidence": {
                "path": "native_dxf",
                "defpoints": [[_r(p[0]), _r(p[1])] for p in dps],
                **({"measurement": _r(D.measurement, 6)}
                   if D.measurement is not None else {}),
                **({"dimtype": D.dimtype} if D.dimtype is not None else {}),
            },
            "proposal_only": True,
        })

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
    # End-arrow assignment radius: a COARSE prune only (see the attach
    # comment in the per-end loop below). Sized at the loosest bound
    # :func:`_arrow_attach` can grant — the candidate gate admits shapes
    # up to :data:`_LOOSE_SIZE_SCALE` x the sheet scale estimate, and the
    # exact per-candidate bound uses the candidate's own length — so the
    # prune can never clip an attachment the exact test would have
    # allowed. Every candidate inside it still faces that exact bound,
    # cluster candidates included; this radius is never the last word.
    attach_radius = min(search_radius,
                        _ATTACH_SCALE * _LOOSE_SIZE_SCALE
                        * max_arrowhead_size)

    arrowheads = list(_arrowhead_candidates_all(ir, max_arrowhead_size,
                                                min_shaft_length))
    if not arrowheads:
        return sorted((p for p in native_props
                       if p["confidence"] >= min_confidence),
                      key=lambda p: p["confidence"], reverse=True)
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

    # A candidate's intrinsic geometry depends only on its vertices, but
    # _arrow_near yields the same candidate to BOTH ends of every nearby
    # shaft (measured 426 apex elections for 108 distinct candidates on one
    # ground-truth sheet). Memoize per candidate id; the shaft-dependent
    # part of the attach test is what stays per-call.
    geom_cache: Dict[str, _ArrowGeom] = {}

    def _geom_of(cand, verts, kind):
        if kind == "fill_cluster":
            return None
        g = geom_cache.get(cand.id)
        if g is None:
            g = geom_cache[cand.id] = _arrow_geometry(verts)
        return g

    # Per-candidate RADIUS (farthest vertex — or cluster member — from the
    # vertex centroid), the one number the exact seat prune below needs.
    # A cluster's seat is its NEAREST member, which can sit a whole
    # radius nearer than its centroid: giving clusters a radius of 0
    # (as when their seat was the centroid) pruned a real stipple
    # arrowhead whose centroid was 1 pt off its end behind a foreign
    # chevron seated 0.94 — the exact defect the seat change exists for.
    radius_cache: Dict[str, float] = {}

    def _radius_of(cand, verts, kind):
        r = radius_cache.get(cand.id)
        if r is None:
            c = _centroid(verts)
            r = radius_cache[cand.id] = max(
                math.hypot(v[0] - c[0], v[1] - c[1]) for v in verts)
        return r

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
        for hit in _ending_near_from_grid(end_grid, tip, search_radius,
                                          limit=50):
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
            # The best-SEATED candidate of each soundness tier; the end
            # is then awarded between them by :func:`_award_end`. Per-tier
            # minima over (seat, centroid distance) and one comparison,
            # so the outcome does not depend on the order candidates
            # arrive in (short of an exact tie on both keys).
            finalists: Dict[int, Dict[str, Any]] = {}
            s_best: Optional[float] = None   # best SOUND seat so far
            sdir = _shaft_terminal_dir(shaft_pts, end_label)
            for cand, verts, c, kind in _arrow_near(tip):
                d = math.hypot(c[0] - tip[0], c[1] - tip[1])
                # A dimension arrow TOUCHES the line end it belongs to (its
                # base sits AT the end, putting the centroid ~half an
                # arrow-length away) — the full leader-flow search_radius
                # let glyph junk 1.4 arrow-lengths away hijack an end and
                # misclassify a split half-shaft as two-arrowed. This is a
                # coarse prune only; :func:`_arrow_attach` applies the
                # exact per-candidate bound below.
                if d > attach_radius:
                    continue
                # EXACT seat prune (performance only — it never changes
                # the award, and test_end_ownership pins that by running
                # with it off). The old nearest-first prune skipped any
                # candidate FARTHER BY CENTROID than a tier-0 incumbent;
                # under seat ordering that skipped the true arrows-inside
                # arrow itself (centroid 2h/3 behind the end it is seated
                # on) whenever junk sat nearer by centroid. The bound
                # that IS exact: every seat point (apex, base centre,
                # vertex extreme, nearest member, centroid) lies within
                # the candidate's own radius R of its centroid, so
                # seat >= d - R. A candidate with d - R STRICTLY above the
                # best sound seat so far loses to that incumbent under
                # :func:`_award_end` whatever its tier (a tie goes to the
                # directional side, which the strict test leaves alone),
                # and tier 2 never wins over a sound candidate at all.
                if _EXACT_SEAT_PRUNE and s_best is not None:
                    if d - _radius_of(cand, verts, kind) > s_best:
                        continue
                geom = _geom_of(cand, verts, kind)
                att = _arrow_attach(verts, kind, sdir, tip,
                                    max_arrowhead_size, geom=geom)
                # SOUNDNESS = identity only (:func:`_end_tier`): tier 2
                # when the candidate's own axis says it belongs to
                # something else, tier 1 when it is detached or carries
                # no direction to test at all. Coordinate quality
                # (``on_spine``) is NOT ranked here — it caps and
                # projects below.
                tier = _end_tier(att, kind)
                # SEAT, not centroid distance, orders candidates within
                # a tier (:func:`_seat_distance`): the true arrows-inside
                # arrow sits 2h/3 from the tip by centroid, and glyph
                # junk beside or just beyond the end was nearer by that
                # measure while its apex and base were both farther (a
                # chevron at apex (103, 3) took the end from the arrow
                # whose apex IS the end; one at (108, 0) took it and
                # published 108 for a 100 pt line at 0.942).
                seat = _seat_distance(verts, kind, sdir, tip, geom)
                cur = finalists.get(tier)
                # Within a tier: seat, then alignment, then centroid
                # distance. The extra keys are what make an exact seat
                # tie order-independent (two chevrons base-seated at the
                # same end, one 20 deg skewed: the one pointing more
                # exactly along the line wins whichever is enumerated
                # first; centroid distance ties both at h/3).
                key = (seat, -att.score, d)
                if cur is not None and key >= cur["key"]:
                    continue
                if tier < 2 and (s_best is None or seat < s_best):
                    s_best = seat
                finalists[tier] = {
                    "cand": cand, "d": d, "seat": seat, "key": key,
                    "score": att.score,
                    "kind": kind, "verts": verts, "sdir": sdir,
                    "tip": tip, "apex": att.apex, "state": att.state,
                    "on_spine": att.on_spine, "attached": att.attached,
                    "tier": tier}
            per_end.append((end_label, tip, _award_end(finalists)))

        n_arrowed = sum(1 for _, _, b in per_end if b is not None)
        # A CONTRADICTED end does not make a shaft two-arrowed for the
        # purpose of founding a split half: by construction a split half
        # has nothing at its inner end, and an arrow whose own axis points
        # elsewhere is not this shaft's arrow. Counting one would let a
        # single stray letterform chevron near a real half's inner end
        # destroy the half (measured: 235 shafts on the ground-truth
        # corpus carry exactly one sound and one contradicted end). The
        # shaft still falls through to the continuous leg below, where the
        # contradicted reading is published CAPPED rather than deleted —
        # both readings reach the caller, neither is asserted.
        cands = [b for _, _, b in per_end if b is not None]
        sound = [b for b in cands if b["state"] != "contradicted"]
        # The FOUNDING candidate: the one sound end, or — when the shaft's
        # only candidate is contradicted — that one, so the split leg's
        # advertised :data:`_CONTRADICTED_CAP` is a rung the code can
        # actually reach and not a docstring promise. Founding on the
        # contradicted reading asserts nothing: the pairing publishes it
        # below every merely-uncorroborated construct.
        founder = None
        if len(sound) == 1:
            founder = sound[0]
        elif not sound and len(cands) == 1:
            founder = cands[0]

        if founder is not None and sep >= half_min_length:
            # SPLIT-LEG CANDIDATE: one arrowed (outer) end. The arrow must
            # sit BEYOND the shaft end pointing outward (its centroid past
            # the tip along the terminal direction) — an arrow behind the
            # tip is some other construct's arrow this shaft merely grazes.
            b = founder
            centroid = _centroid(b["verts"])
            tip, sdir = b["tip"], b["sdir"]
            if ((centroid[0] - tip[0]) * sdir[0]
                    + (centroid[1] - tip[1]) * sdir[1]) > 0:
                inner = (shaft_pts[0] if tip == shaft_pts[-1]
                         else shaft_pts[-1])
                # Apex from the attach test (:func:`_arrow_attach`): the
                # candidate's intrinsic tip (for a cluster: its farthest
                # reach on the shaft's terminal ray) — the CAD defpoint.
                # OFF SPINE it is that apex PROJECTED on the terminal ray,
                # which matters most HERE: a split half's arrow sits
                # OUTSIDE its half-shaft, so the true defpoint is beyond
                # the shaft's tip and the old substitute-the-tip fallback
                # moved the published point by a whole arrow length
                # (measured 7.2 pt on the ground-truth arrow geometry,
                # on constructs HEAD published at ~0.95 with the CAD
                # defpoints exactly right). Refusing the half instead
                # deleted every real split dimension whose arrow is
                # drafted more than asin(base/2 leg) off the axis:
                # measured 0.964/0.963 at 0/5 deg and NOTHING from 10 deg
                # out, where the documented direction cone reaches 30.
                halves.append({
                    "shaft": shaft, "cand": b["cand"], "kind": b["kind"],
                    "a_score": b["score"], "outer": tip, "inner": inner,
                    "out": sdir,
                    "apex": b["apex"],
                    "state": b["state"], "on_spine": b["on_spine"],
                    "attached": b["attached"],
                    "members": set(getattr(b["cand"], "member_ids", ())),
                })

        if n_arrowed < 2:
            continue
        if per_end[0][2]["cand"].id == per_end[1][2]["cand"].id:
            continue  # the two arrowheads must be DISTINCT
        kinds2 = [b["kind"] for _, _, b in per_end]
        states2 = [b["state"] for _, _, b in per_end]

        # Proposal ends = the arrows' intrinsic APEX points — the CAD
        # defpoints the construct measures between. Witness lines cross at
        # the defpoints, so the witness check runs there.
        #
        # OFF SPINE, this leg publishes the SHAFT'S OWN END instead, and
        # that differs from the split leg on purpose — the two constructs
        # place their arrows differently against the drawn line. HERE the
        # shaft spans the whole measurement with its arrows inside pointing
        # outward, so its end IS the defpoint to within an arrow's width,
        # and it is drawn fact. On the split leg the half-shafts are stubs
        # and the arrows sit OUTSIDE them, so the defpoint is genuinely
        # beyond the tip and the projected apex is the only honest answer
        # (substituting the tip there moved the published point a whole
        # arrow length — measured 7.2 pt).
        #
        # Projecting here instead would recover only HALF of the defect
        # this fallback exists for: the glyph chevron of the original
        # report sits at apex (108, 5) beside a shaft ending at (100, 0),
        # so projection on the terminal ray still publishes (108, 0) and
        # still reports length 108 for a 100 pt line. Discarding the
        # lateral wobble does not make an axial outlier true.
        apexes = []
        off_spine = False
        for _lab, tip, b in per_end:
            apexes.append(b["apex"] if b["on_spine"] else tip)
            off_spine = off_spine or not b["on_spine"]

        # The min_shaft_length floor is a floor on the dimension's EXTENT
        # — the thing the construct measures — and the shaft was only ever
        # a proxy for it. In the arrows-outside plot style the shaft stops
        # at the arrow bases and under-reports that extent, which is what
        # killed real narrow dimensions (ground-truth sheet 3001's 'T='
        # construct: a 9.8 pt shaft spanning 24.2 pt between its
        # defpoints). Measuring the extent instead retires the old
        # both-ends-are-triangles bypass, which had no floor at all and
        # admitted 32 letter-stroke pairs spanning 7-16 pt at 0.976
        # confidence.
        #
        # Note honestly what max() does and does not do against HEAD: it
        # is stricter for the [triangle, triangle] pairs HEAD's bypass
        # exempted from any floor at all, and LOOSER for every other kind
        # pair, where an apex span longer than the shaft now clears a
        # floor the bare shaft did not (a 12 pt shaft between two clusters
        # reaching 20 pt apart is admitted where HEAD deleted it). And it
        # CAPS rather than deletes either way: an 18.0 pt both-triangle
        # dimension HEAD published at 0.968 was being dropped at every
        # min_confidence, which is the trade the cap ladder exists to
        # avoid. Below the floor the construct stays visible in the
        # observational band and is never called.
        extent = max(sep, math.hypot(apexes[0][0] - apexes[1][0],
                                     apexes[0][1] - apexes[1][1]))
        below_extent_floor = extent < min_shaft_length

        # The cap ladder's CEILING is fully determined by what has been
        # measured so far, so hoist it above the witness search: a caller
        # asking only for called constructs would drop every capped one at
        # the bottom of this loop anyway, and everything below here —
        # witness hunting most of all — is what cap-not-delete made
        # expensive (sheet 3001's dims@0.5 ran 0.2 s -> 8.5 s before this
        # hoist, 0.4 s after; the observational 0.3 band still pays).
        # Same predicate as the caps below, in the same order.
        detached = any(not b["attached"] for _, _, b in per_end)
        if "contradicted" in states2:
            ceiling = _CONTRADICTED_CAP
        elif ("blunt" in states2 or off_spine or detached
                or below_extent_floor):
            ceiling = _UNCORROBORATED_CAP
        else:
            ceiling = 1.0
        if ceiling < min_confidence:
            continue

        align = sum(b["score"] for _, _, b in per_end) / len(per_end)

        # Extension (witness) lines: something ELSE terminating near each
        # apex, roughly perpendicular to the shaft's terminal axis at that
        # end. Cluster members must not double as witness lines.
        used_ids = {shaft.id} | {b["cand"].id for _, _, b in per_end}
        for _, _, b in per_end:
            used_ids |= set(getattr(b["cand"], "member_ids", ()))
        ext_ids: List[str] = []
        ext_ends = 0
        for (end_label, tip, _b), apex in zip(per_end, apexes):
            shaft_dir = _shaft_terminal_dir(shaft_pts, end_label)
            if _witness_at(apex, shaft_dir, used_ids, ext_ids):
                ext_ends += 1
        ext_score = ext_ends / 2.0

        mid = (0.5 * (apexes[0][0] + apexes[1][0]),
               0.5 * (apexes[0][1] + apexes[1][1]))
        text_hit, text_dist = _nearest_text(mid)
        text_score = _text_proximity_score(text_dist, text_radius)

        # BLUNT-ONLY constructs (box-like quad terminators at both ends)
        # make the alignment channel UNOBSERVABLE, not contradicted — the
        # terminator has no direction to agree or disagree with. Score the
        # observable channels instead, the same renormalization the
        # no-text branch already does for its own missing channel. The
        # ceiling still holds such a construct below the call threshold,
        # so this renormalization only orders it AMONG the capped ones —
        # which is what a caller reading the observational band wants.
        blunt_only = all(s == "blunt" for s in states2)
        if texts:
            if blunt_only:
                confidence = round((0.30 * text_score
                                    + 0.30 * ext_score) / 0.60, 3)
            else:
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
            raw = ext_score if blunt_only else (0.40 * align
                                                + 0.30 * ext_score) / 0.70
            # "A DRAWN shape" means a POINTING one: an oriented tipless
            # terminator is drawn but sign-blind, so on a no-text sheet
            # it corroborates no more than a cluster does. (For the
            # blunt and contradicted states this reads identically to
            # the older kind-only test — both are capped harder anyway.)
            if ext_ends < 2 or not any(
                    b["kind"] == "triangle" and b["state"] == "ok"
                    for _, _, b in per_end):
                raw = min(raw, _UNCORROBORATED_CAP)
            confidence = round(raw, 3)
        # CAP, DON'T DELETE, in the ranking order the caps state. ANY
        # blunt terminator holds the construct in the observational band,
        # with NO escape hatch: a blunt shape is non-directional by
        # construction, so witnesses and a value text corroborate that
        # SOMETHING is measured here, never that this shape terminates
        # THIS line. Letting corroboration lift such a pair over the call
        # threshold published a plain rectangle plus a "30'" note as a
        # 0.915 dimension that claimed a genuine leader's arrowhead, and
        # two plain rectangles alone as 0.96. Likewise an off-spine or
        # detached end published drawn geometry instead of a defpoint, and
        # an extent under the floor is glyph-scale; and a CONTRADICTED
        # arrow — one whose own axis says it belongs to something else —
        # ranks below every construct that is merely uncorroborated.
        # ``ceiling`` above is this same ladder, hoisted for the caller
        # who is not asking for capped constructs at all.
        confidence = min(confidence, ceiling)
        if confidence < min_confidence:
            continue

        a_xy, b_xy = apexes[0], apexes[1]
        proposals.append({
            "end_a_xy": [_r(a_xy[0]), _r(a_xy[1])],
            "end_b_xy": [_r(b_xy[0]), _r(b_xy[1])],
            "midpoint_xy": [_r(mid[0]), _r(mid[1])],
            "length": _r(math.hypot(b_xy[0] - a_xy[0], b_xy[1] - a_xy[1])),
            "angle_deg": _r(_seg_angle(a_xy, b_xy), 2),
            "shaft_id": shaft.id,
            # ARBITRATION CHANNEL — ``find_leaders(exclude_dimensions=
            # True)`` drops a leader whose arrowhead a dimension names
            # here, so only a DIRECTIONAL terminator may be named. A blunt
            # shape is non-directional by construction: it carries no
            # evidence that it belongs to THIS line rather than to one
            # merely crossing it, so it can never win an arbitration
            # against an arrowhead that does carry that evidence. It stays
            # discoverable under ``evidence.blunt_terminator_ids``.
            "arrowhead_ids": [b["cand"].id for _, _, b in per_end
                              if b["state"] != "blunt"],
            "extension_line_ids": sorted(set(ext_ids)),
            "text": text_hit["content"] if text_hit else None,
            "text_id": text_hit["id"] if text_hit else None,
            "text_distance": _r(text_dist) if text_dist is not None else None,
            "confidence": confidence,
            "evidence": {
                "path": "continuous",
                "arrowhead_kinds": kinds2,
                "alignment_score": _r(align, 3),
                "text_proximity_score": _r(text_score, 3),
                "extension_line_score": _r(ext_score, 3),
                "n_extension_ends": ext_ends,
                **({"arrow_direction_violation": True}
                   if "contradicted" in states2 else {}),
                **({"arrow_off_spine": True} if off_spine else {}),
                **({"arrow_detached": True} if detached else {}),
                **({"below_extent_floor": True}
                   if below_extent_floor else {}),
                **({"blunt_terminators": True} if blunt_only else {}),
                **({"blunt_terminator_ids":
                    [b["cand"].id for _, _, b in per_end
                     if b["state"] == "blunt"]}
                   if "blunt" in states2 else {}),
                **({"oriented_terminator_ids":
                    [b["cand"].id for _, _, b in per_end
                     if b["state"] == "oriented"]}
                   if "oriented" in states2 else {}),
                **({} if texts else {"text_unavailable": True}),
            },
            "proposal_only": True,
        })

    proposals.extend(_pair_split_halves(
        halves, max_arrowhead_size, texts, _witness_at, _nearest_text,
        text_radius, min_confidence))

    if native_spans:
        # Duplication is the same SPAN, not a shared endpoint
        # (:func:`_native_span_at`), and a duplicate is CAPPED, not deleted.
        proposals = [
            _capped_by_native(
                p, _native_span_at(native_spans, p["end_a_xy"],
                                   p["end_b_xy"], max_arrowhead_size))
            for p in proposals]
        proposals = [p for p in proposals
                     if p["confidence"] >= min_confidence]
        proposals.extend(p for p in native_props
                         if p["confidence"] >= min_confidence)

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

    The cap ladder here is REAL, not advertised: ``halves`` carries the
    founding candidate's own ``state`` (``"ok"`` / ``"blunt"`` /
    ``"contradicted"``) plus its ``on_spine`` and ``attached`` flags, and
    a shaft whose only candidate is contradicted still founds a half
    precisely so :data:`_CONTRADICTED_CAP` has something to bind on
    (measured on the ground-truth corpus: 226 halves offered on sheet
    3001, of which 61 contradicted and 4 blunt — none of which reaches
    the 0.3 observational band, which is the point).

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
            # A drawn shape means a POINTING one (same reading as the
            # continuous leg): an oriented tipless half is sign-blind.
            if not any(h["kind"] == "triangle" and h["state"] == "ok"
                       for h in (hi, hj)):
                raw = min(raw, _UNCORROBORATED_CAP)
            confidence = round(raw, 3)
        # Same cap-not-delete ladder the continuous leg applies. It
        # matters more here: the split leg's whole discrimination is that
        # the two arrows OPPOSE along a shared axis, which a blunt
        # terminator cannot corroborate and a contradicted one denies. A
        # half founded on its shaft's END rather than on an arrow apex
        # (the off-spine fallback) is published on drawn geometry, so it
        # is capped the same way — the alternative, refusing the half, is
        # what deleted real split dimensions drafted 10-30 deg off axis.
        states2 = (hi["state"], hj["state"])
        if ("blunt" in states2
                or not (hi["on_spine"] and hj["on_spine"])
                or not (hi["attached"] and hj["attached"])):
            confidence = min(confidence, _UNCORROBORATED_CAP)
        if "contradicted" in states2:
            confidence = min(confidence, _CONTRADICTED_CAP)
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
            # Arbitration channel — blunt terminators withheld for the
            # reason the continuous leg gives at its own ``arrowhead_ids``.
            "arrowhead_ids": [h["cand"].id for h in (hi, hj)
                              if h["state"] != "blunt"],
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
                **({"arrow_direction_violation": True}
                   if "contradicted" in states2 else {}),
                **({"arrow_off_spine": True}
                   if not (hi["on_spine"] and hj["on_spine"]) else {}),
                **({"arrow_detached": True}
                   if not (hi["attached"] and hj["attached"]) else {}),
                **({"blunt_terminators": True}
                   if "blunt" in states2 else {}),
                **({"blunt_terminator_ids": [h["cand"].id for h in (hi, hj)
                                             if h["state"] == "blunt"]}
                   if "blunt" in states2 else {}),
                **({"oriented_terminator_ids":
                    [h["cand"].id for h in (hi, hj)
                     if h["state"] == "oriented"]}
                   if "oriented" in states2 else {}),
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
