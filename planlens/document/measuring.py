"""``measure`` — snap to the drawn thing near a rough box, read it through the page's scale.

A position read off an image by eye is approximate: on the 2026-10-06 field
session an agent placed 31 layer boundaries by eye a median 0.20 m off,
while code measured the same lines to about +/-0.02 m. So the work is split:

1. **The caller names the thing and gives a rough box** — a ``bbox`` in PDF
   points (displayed frame), from a zoomed look converted in code or from a
   text box. ``pad`` is how far the box may be off (the view's location
   error); the box padded by it is the search window.
2. **Code snaps** to the drawn thing of the asked ``kind`` inside the window,
   restricted to the column or plot region the box is in (a blow-count row
   rule is never taken for a stratum line). One candidate: taken. More than
   one: ALL are returned with their distances and none is chosen. None: the
   box's own position is read, marked ``unsnapped``, with the location error
   as its uncertainty.
3. **Code converts** through the scale whose region holds the snapped
   position (or the one named): the value with its +/- (95 %), the
   confidence, the scale and how it was found, what the thing snapped to and
   how far it moved.

Called with no box it lists the page's scales (:func:`inventory`). With no
scale on the page the position comes back in points with
``scale_known: false`` — points never become metres without a scale.

Kinds: ``line`` (the drawn rule nearest the box), ``lines`` (every rule in
the box, each with its extent, so a short hatch run shows as one), ``point``
(a symbol or plotted marker), ``edge`` (the top / bottom / left / right of
the ink in the box), ``curve`` with ``at`` (where a curve crosses an axis
value), ``distance`` with ``to`` (between two snapped points, through a
plan's distance scale), ``text`` (a text line's exact box).

Nothing here calls a model. numpy and PyMuPDF only.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.scales import (
    PageScales, Reading, Scale, display_value, rotate_point, unrotate_point,
)

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

__all__ = ["measure", "inventory", "KINDS", "STRATUM_COVER", "WIDE_VIEW_PT",
           "default_pad"]

KINDS = ("line", "lines", "point", "edge", "curve", "distance", "text")

#: A line must cross this fraction of the region it is snapped in (loggrid's
#: stratum rule).
STRATUM_COVER = 0.55

#: A window wider than this from a small box is said to be large, and every
#: candidate is listed (the marks rule of cf43c90, applied to measuring).
WIDE_VIEW_PT = 300.0

#: A box that may be this far off (or more) is not snapped at all: one line
#: found in a window that wide is no evidence it is the line meant. Measured
#: (design E5): boxes read off whole-sheet 0-999 grids were 57-91 pt off and
#: their windows held the right line only half the time; pixel boxes are
#: 1-6 pt off and zooms about 1 pt.
LARGE_PAD_PT = 25.0


def default_pad(box: Sequence[float]) -> float:
    """How far a box with no stated location error may be off: 3 pt, more
    for a larger box (a quarter of its smaller side)."""
    w = abs(box[2] - box[0])
    h = abs(box[3] - box[1])
    return max(3.0, 0.25 * min(w, h))


def _norm_box(where: Any) -> Optional[BBox]:
    if where is None:
        return None
    if isinstance(where, dict):
        where = where.get("bbox")
    if where is None:
        return None
    b = [float(v) for v in where]
    if len(b) != 4:
        raise ValueError("a box is [x0, y0, x1, y1] in PDF points")
    return (min(b[0], b[2]), min(b[1], b[3]), max(b[0], b[2]),
            max(b[1], b[3]))


def _r(v: Optional[float], n: int = 3) -> Optional[float]:
    if v is None:
        return None
    out = round(float(v), n)
    return 0.0 if out == 0 else out


def _rb(b: Optional[Sequence[float]], n: int = 1):
    return None if b is None else [_r(v, n) for v in b]


# ---------------------------------------------------------------------------
# Inventory
# ---------------------------------------------------------------------------

def inventory(ps: PageScales) -> Dict[str, Any]:
    """The page's scales, as the no-box call returns them."""
    out = ps.to_dict()
    waiting = ps.needing_values()
    if waiting:
        out["needs_values"] = [
            {"scale": s.id, "labels": [_rb(b) for b in s.label_boxes]}
            for s in waiting]
    if not ps.frames:
        out["note"] = ("no scale on this page: positions can be measured in "
                       "page points only")
    return out


# ---------------------------------------------------------------------------
# Scale choice
# ---------------------------------------------------------------------------

def _scales_for(ps: PageScales, axis: str, point: Point,
                named: Optional[str] = None) -> Tuple[List[Scale], List[Scale]]:
    """``(usable, pending)`` scales along ``axis`` whose region holds ``point``.

    Ordered best first: confidence, then anchors.
    """
    usable: List[Scale] = []
    pending: List[Scale] = []
    for s in ps.scales():
        if named and s.id != named:
            continue
        if s.axis != axis:
            continue
        if not named and not s.contains(*point, slack=4.0):
            continue
        (pending if s.needs_values else usable).append(s)
    usable.sort(key=lambda s: (-s.confidence, -len(s.anchors)))
    return usable, pending


def _distance_scale(ps: PageScales, named: Optional[str] = None
                    ) -> Optional[Scale]:
    for f in ps.frames:
        if f.kind != "plan":
            continue
        if named and named in {s.id for s in f.scales.values()}:
            return next(s for s in f.scales.values() if s.id == named)
        prim = f.provenance.get("primary")
        if prim:
            for s in f.scales.values():
                if s.id == prim and s.usable:
                    return s
    for s in ps.scales():
        if s.axis == "distance" and s.usable and (not named or s.id == named):
            return s
    return None


def _scale_summary(s: Scale) -> Dict[str, Any]:
    d = {"id": s.id, "quantity": s.quantity, "unit": s.unit,
         "transform": s.transform,
         "anchors": len(s.anchors),
         "residual_pt": _r(s.residual_pt, 2),
         "anchor_rule": s.anchor_rule,
         "confidence": round(s.confidence, 2)}
    if s.transform == "linear" and s.usable:
        d["per_point"] = _r(s.b, 6)
    fb = {k: v for k, v in (s.provenance or {}).items()
          if not str(k).startswith("_")}
    if fb.get("values_from"):
        d["values_from"] = fb["values_from"]
    if fb.get("positions_from"):
        d["positions_from"] = fb["positions_from"]
    if s.warnings:
        d["warnings"] = list(s.warnings)
    return {k: v for k, v in d.items() if v is not None}


def _value_dict(rd: Reading) -> Dict[str, Any]:
    d = {rd.quantity: _r(rd.value, 6), "plus_minus": _r(rd.plus_minus, 6),
         "unit": rd.unit, "confidence": round(rd.confidence, 2),
         "display": rd.display, "plus_minus_pt": _r(rd.plus_minus_pt, 2)}
    if rd.warnings:
        d["warnings"] = list(rd.warnings)
    return {k: v for k, v in d.items() if v is not None}


# ---------------------------------------------------------------------------
# The geometry under a box
# ---------------------------------------------------------------------------

def _deskew_box(facts, box: BBox) -> BBox:
    pts = [facts.deskew(x, y) for x in (box[0], box[2]) for y in (box[1], box[3])]
    return (min(p[0] for p in pts), min(p[1] for p in pts),
            max(p[0] for p in pts), max(p[1] for p in pts))


def _region_bands(facts, orient: str, centre: Point, dbox: BBox,
                  dwin: BBox) -> Tuple[List[Tuple[float, float]], str]:
    """The bands a line must cross: the columns (or rows) the box is in.

    For a horizontal line, the bands between the long vertical rules that
    reach the box's height: the one the box centre stands in, any other the
    box itself overlaps, and any NARROW one inside the search window (a
    boring stick on a profile is a few points wide, and a box read beside
    it should still find its contacts). With no rules, the box itself.
    """
    cu, cv = facts.deskew(*centre)
    along = cv if orient == "h" else cu
    perp = facts.vlines() if orient == "h" else facts.hlines()
    edges: List[float] = []
    for L in perp:
        pos, lo, hi = facts.line_span(L)
        if not (lo - 2.0 <= along <= hi + 2.0) or hi - lo < 20.0:
            continue
        edges.append(pos)
    edges.sort()
    uniq: List[float] = []
    for e in edges:
        if uniq and e - uniq[-1] < 2.0:
            continue
        uniq.append(e)
    blo = dbox[0] if orient == "h" else dbox[1]
    bhi = dbox[2] if orient == "h" else dbox[3]
    wlo = dwin[0] if orient == "h" else dwin[1]
    whi = dwin[2] if orient == "h" else dwin[3]
    c = cu if orient == "h" else cv
    pad = max(0.0, blo - wlo)
    bands: List[Tuple[float, float]] = []
    home = None
    for a, b in zip(uniq, uniq[1:]):
        if b - a < 4.0:
            continue
        if a <= c <= b:
            home = (a, b)
            continue
        overlaps_box = min(b, bhi) - max(a, blo) > 0.5
        narrow_in_win = (b - a) <= 2.0 * pad + 1.0 and a >= wlo - 0.5 \
            and b <= whi + 0.5
        if overlaps_box or narrow_in_win:
            bands.append((a, b))
    if home is not None:
        return [home] + bands, "the column the box stands in"
    if bands:
        return bands, "a column the box reaches"
    return [(blo, bhi)], "the box"


def _line_candidates(facts, orient: str, window: BBox,
                     bands: Sequence[Tuple[float, float]],
                     cover: float = STRATUM_COVER) -> List[Dict[str, Any]]:
    """Rules of ``orient`` inside ``window`` crossing ``cover`` of a band."""
    dwin = _deskew_box(facts, window)
    out = []
    pool = list(facts.hlines() if orient == "h" else facts.vlines())
    narrowest = min(b - a for a, b in bands)
    if narrowest * cover < 18.0:
        # A narrow region (a boring stick on a profile, a strip column): its
        # lines are shorter than the page-wide finder keeps, so look again
        # in the window itself for short ones.
        pool += _short_lines(facts, orient, window,
                             max(2.0, cover * narrowest))
    for L in pool:
        pos, lo, hi = facts.line_span(L)
        if orient == "h":
            if not (dwin[1] <= pos <= dwin[3]):
                continue
        else:
            if not (dwin[0] <= pos <= dwin[2]):
                continue
        if not any(min(hi, b) - max(lo, a) >= cover * max(1.0, b - a)
                   for a, b in bands):
            continue
        out.append({"line": L, "pos": pos, "lo": lo, "hi": hi})
    # one rule seen twice (a thick or doubled stroke) is one candidate
    out.sort(key=lambda c: c["pos"])
    dedup: List[Dict[str, Any]] = []
    for c in out:
        if dedup and abs(c["pos"] - dedup[-1]["pos"]) < 1.0:
            if (c["hi"] - c["lo"]) > (dedup[-1]["hi"] - dedup[-1]["lo"]):
                dedup[-1] = c
            continue
        dedup.append(c)
    return dedup


def _short_lines(facts, orient: str, window: BBox, min_len: float
                 ) -> List[Any]:
    """Short rules inside a window: drawn segments, or found in its pixels."""
    from planlens.document.raster import RasterLine
    out: List[Any] = []
    for (x0, y0, x1, y1, w) in facts.segments:
        if orient == "h" and abs(y1 - y0) <= 0.35 and x1 - x0 >= min_len:
            if window[0] - 1 <= (x0 + x1) / 2 <= window[2] + 1 and                     window[1] <= y0 <= window[3]:
                out.append(RasterLine("h", a=(y0 + y1) / 2.0, b=0.0, lo=x0,
                                      hi=x1, thickness_pt=w, coverage=1.0,
                                      source="vector"))
        elif orient == "v" and abs(x1 - x0) <= 0.35 and y1 - y0 >= min_len:
            if window[1] - 1 <= (y0 + y1) / 2 <= window[3] + 1 and                     window[0] <= x0 <= window[2]:
                out.append(RasterLine("v", a=(x0 + x1) / 2.0, b=0.0, lo=y0,
                                      hi=y1, thickness_pt=w, coverage=1.0,
                                      source="vector"))
    if facts.raster is not None:
        from planlens.document.raster import crop, find_lines
        sub = crop(facts.raster, (window[0] - 2, window[1] - 2,
                                  window[2] + 2, window[3] + 2))
        if sub is not None:
            slope = math.tan(math.radians(facts.angle_deg))
            lines, _ = find_lines(sub, orientation=orient,
                                  min_len_pt=min_len, skew=slope,
                                  piece_pt=min(2.0, min_len))
            out.extend(lines)
    return out


def _point_on_line(facts, L, orient: str, centre: Point) -> Point:
    """The point of a found line across from the box centre."""
    if orient == "h":
        x = min(max(centre[0], L.lo), L.hi)
        return (x, L.at(x))
    y = min(max(centre[1], L.lo), L.hi)
    return (L.at(y), y)


def _point_candidates(facts, window: BBox) -> List[Dict[str, Any]]:
    """Symbols and markers inside the window: filled vector marks, solid
    raster blobs, and compact ink with the lines taken out."""
    out = []
    for bb in facts.fills:
        cx, cy = (bb[0] + bb[2]) / 2.0, (bb[1] + bb[3]) / 2.0
        if window[0] <= cx <= window[2] and window[1] <= cy <= window[3]:
            out.append({"centre": (cx, cy), "bbox": bb, "kind": "symbol",
                        "pm": 0.05})
    if facts.raster is not None:
        from planlens.document.raster import components, solid_blobs
        ras = facts.raster
        found = []
        # A plotted marker is 3-6 pt across; a curve, a gridline, or a frame
        # with a curve hugging it is under 2 pt thick and does not survive a
        # 2 pt erosion. Cores too long for one marker (two markers joined by
        # a stroke) cannot be placed, but they are not nothing: they are
        # kept as unresolved candidates, so a lone neighbour is never taken
        # for the mark the box was about.
        kept = solid_blobs(ras, region=window, core_pt=2.0, min_core_px=2,
                           max_aspect=2.2)
        for bl in solid_blobs(ras, region=window, core_pt=2.0, min_core_px=2):
            cx, cy = bl.centroid
            if not (window[0] <= cx <= window[2]
                    and window[1] <= cy <= window[3]):
                continue
            if any(bl.bbox[0] - 0.5 <= k.centroid[0] <= bl.bbox[2] + 0.5
                   and bl.bbox[1] - 0.5 <= k.centroid[1] <= bl.bbox[3] + 0.5
                   for k in kept):
                continue
            found.append({"centre": (cx, cy), "bbox": bl.bbox,
                          "kind": "unresolved", "pm": 99.0})
        for bl in kept:
            cx, cy = bl.centroid
            if window[0] <= cx <= window[2] and window[1] <= cy <= window[3]:
                # The centre of the eroded core IS the marker's centre: a
                # curve or a gridline running through the marker is thinner
                # than the erosion and does not drag it.
                found.append({"centre": bl.centroid,
                              "bbox": bl.bbox, "kind": "marker",
                              "pm": math.hypot(0.5 * ras.pixel_pt, 0.4)})
        if not found:
            mask = facts.mask_without_lines()
            for bl in components(ras, mask=mask, region=window, min_px=8):
                w, h = bl.width, bl.height
                if not (1.5 <= w <= 14.0 and 1.5 <= h <= 14.0):
                    continue
                if max(w, h) > 2.5 * min(w, h):
                    continue
                cx, cy = bl.centre
                if not (window[0] <= cx <= window[2]
                        and window[1] <= cy <= window[3]):
                    continue
                found.append({"centre": _ink_centre(facts, bl.bbox),
                              "bbox": bl.bbox, "kind": "symbol",
                              "pm": math.hypot(0.5 * ras.pixel_pt, 0.25)})
        out.extend(found)
    # one mark found twice (a vector fill drawn under a stroke)
    dedup: List[Dict[str, Any]] = []
    for c in out:
        if any(math.dist(c["centre"], d["centre"]) < 1.5 for d in dedup):
            continue
        dedup.append(c)
    return dedup


def _ink_centre(facts, box: BBox) -> Point:
    """Ink-weighted centre of a mark (sub-pixel)."""
    import numpy as np
    ras = facts.raster
    c0, r0, c1, r1 = ras.box_to_pixel((box[0] - 0.5, box[1] - 0.5,
                                       box[2] + 0.5, box[3] + 0.5))
    sub = ras.inkw[r0:r1, c0:c1]
    if sub.size == 0:
        return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    sub = np.clip(sub - np.percentile(sub, 25), 0, None)
    tot = sub.sum()
    if tot <= 0:
        return ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    ys, xs = np.mgrid[r0:r1, c0:c1]
    cx = float(((xs + 0.5) * sub).sum() / tot)
    cy = float(((ys + 0.5) * sub).sum() / tot)
    return ras.to_page(cx, cy)


def _curve_crossings(facts, scale: Scale, s_at: float, window: BBox
                     ) -> List[Dict[str, Any]]:
    """Where drawn curves cross ``along = s_at`` of ``scale`` in the window.

    Vector: every curve segment spanning the line, interpolated. Raster: the
    ink runs met walking across the axis just beside the line (gridlines
    taken out), averaged over columns either side so a curve crossing a
    gridline is still seen.
    """
    dwin = _deskew_box(facts, window)
    across_lo = dwin[1] if scale.axis == "x" else dwin[0]
    across_hi = dwin[3] if scale.axis == "x" else dwin[2]
    out: List[Dict[str, Any]] = []
    ang = facts.angle_deg
    for path in facts.curves:
        pts = [p for p in path if not math.isnan(p[0])]
        prev = None
        for p in path:
            if math.isnan(p[0]):
                prev = None
                continue
            if prev is not None:
                a = rotate_point(prev[0], prev[1], ang)
                b = rotate_point(p[0], p[1], ang)
                ua, va = (a[0], a[1]) if scale.axis == "x" else (a[1], a[0])
                ub, vb = (b[0], b[1]) if scale.axis == "x" else (b[1], b[0])
                if min(ua, ub) <= s_at <= max(ua, ub) and ua != ub:
                    t = (s_at - ua) / (ub - ua)
                    v = va + t * (vb - va)
                    if across_lo <= v <= across_hi:
                        out.append({"across": v, "pm": 0.05,
                                    "kind": "curve"})
            prev = p
        del pts
    if facts.raster is not None:
        out.extend(_raster_crossings(facts, scale, s_at, across_lo,
                                     across_hi))
    out.sort(key=lambda c: c["across"])
    dedup: List[Dict[str, Any]] = []
    for c in out:
        if dedup and abs(c["across"] - dedup[-1]["across"]) < 1.2:
            continue
        dedup.append(c)
    for c in dedup:
        if scale.axis == "x":
            c["point"] = unrotate_point(s_at, c["across"], ang)
        else:
            c["point"] = unrotate_point(c["across"], s_at, ang)
    return dedup


def _raster_crossings(facts, scale: Scale, s_at: float, lo: float,
                      hi: float) -> List[Dict[str, Any]]:
    """Curve crossings of ``along = s_at`` found in pixels.

    The walk runs across the axis at many small offsets either side of
    ``s_at`` (the value read is often ON a gridline, so it is never walked
    along). Every short run of ink met is a point ``(offset, centre)``. A
    gridline crossing the walk leaves a FLAT trail of points at its own
    position; a curve leaves a trail that slopes as the curve does. Trails
    are found as straight lines through the points (every pair tried, the
    line with the most points within half a point taken, then the next),
    gridline trails are dropped, and each curve trail is fitted (a gentle
    quadratic when both sides are seen) and read at offset 0. Points where
    the curve meets a gridline fall off the trail and do no harm.
    """
    import numpy as np
    ras = facts.raster
    z = ras.z
    ang = facts.angle_deg
    cross_orient = "h" if scale.axis == "x" else "v"
    walk_orient = "v" if scale.axis == "x" else "h"
    ext = _deskew_box(facts, scale.extent)
    span = (ext[2] - ext[0]) if cross_orient == "h" else (ext[3] - ext[1])
    grid_pos = []
    for L in (facts.hlines() if cross_orient == "h" else facts.vlines()):
        pos, a, b = facts.line_span(L)
        if b - a >= 0.6 * span and a - 2 <= s_at <= b + 2:
            grid_pos.append(pos)
    span_w = (ext[3] - ext[1]) if walk_orient == "v" else (ext[2] - ext[0])
    under = []
    for L in (facts.vlines() if walk_orient == "v" else facts.hlines()):
        pos, a, b = facts.line_span(L)
        if b - a >= 0.6 * span_w and abs(pos - s_at) <=                 L.thickness_pt / 2.0 + 10.0 / z:
            under.append((pos, L.thickness_pt / 2.0 + 1.0 / z))
    step = 0.5 / z
    n = int((hi - lo) / step) + 1
    if n < 3:
        return []
    vs = lo + np.arange(n) * step
    offsets = []
    for side in (-1.0, 1.0):
        got, d = 0, 1.0
        while d <= 16.0 and got < 12:
            off = side * d / z
            d += 0.5
            if any(abs(s_at + off - u) <= h for u, h in under):
                continue
            offsets.append(off)
            got += 1
    pts: List[Tuple[float, float]] = []
    for off in offsets:
        s = s_at + off
        if scale.axis == "x":
            pp = [unrotate_point(s, v, ang) for v in vs]
        else:
            pp = [unrotate_point(v, s, ang) for v in vs]
        cols = np.array([ras.to_pixel(*q) for q in pp])
        ci = np.clip(np.floor(cols[:, 0]).astype(int), 0, ras.shape[1] - 1)
        ri = np.clip(np.floor(cols[:, 1]).astype(int), 0, ras.shape[0] - 1)
        w = ras.inkw[ri, ci].astype(np.float64)
        w = np.clip(w - np.percentile(w, 50) - 25.0, 0, None)
        on = w > 0
        if not on.any():
            continue
        d_ = np.diff(np.concatenate(([0], on.astype(np.int8), [0])))
        for a, b in zip(np.nonzero(d_ == 1)[0], np.nonzero(d_ == -1)[0]):
            seg = w[a:b]
            if seg.sum() <= 0 or (b - a) * step > 8.0:
                continue
            pts.append((off, float((seg * vs[a:b]).sum() / seg.sum())))
    out: List[Dict[str, Any]] = []
    left = list(pts)
    tol = 0.5
    while len(left) >= 4:
        best = None
        for i in range(len(left)):
            for j in range(i + 1, len(left)):
                (o1, c1), (o2, c2) = left[i], left[j]
                if abs(o2 - o1) < 0.5 / z:
                    continue
                k = (c2 - c1) / (o2 - o1)
                if abs(k) > 6.0:
                    continue
                inl = [q for q in left if abs(q[1] - (c1 + k * (q[0] - o1)))
                       <= tol]
                if best is None or len(inl) > len(best[0]):
                    best = (inl, k)
        if best is None or len(best[0]) < 4:
            break
        inl, _k = best
        left = [q for q in left if q not in inl]
        offs = np.array([q[0] for q in inl])
        cs = np.array([q[1] for q in inl])
        # a gridline's own trail: flat (by least squares, not by the pair
        # that happened to seed it), and where a gridline is
        k = float(np.polyfit(offs, cs, 1)[0]) if offs.std() > 0 else 0.0
        if abs(k) < 0.08 and any(abs(float(np.median(cs)) - g) <= 0.8
                                 for g in grid_pos):
            continue
        if len(set(np.round(offs, 4))) < 4:
            continue
        two_sided = offs.min() < 0 < offs.max()
        deg = 2 if (len(inl) >= 8 and two_sided) else 1
        coef = np.polyfit(offs, cs, deg)
        centre = float(np.polyval(coef, 0.0))
        resid = cs - np.polyval(coef, offs)
        rms = float(np.sqrt(np.mean(resid ** 2)))
        pm = math.hypot(2.0 * max(rms, 0.1) / math.sqrt(max(1.0,
                                                            len(inl) / 4.0)),
                        0.5 / z)
        if not two_sided:
            pm = math.hypot(pm, 0.4)       # extrapolated from one side
        out.append({"across": centre, "kind": "curve", "pm": pm,
                    "n": len(inl)})
    # A bending curve can leave its trail as two straight pieces; crossings
    # under 1.5 pt apart are one crossing, its uncertainty covering both.
    out.sort(key=lambda c: c["across"])
    merged: List[Dict[str, Any]] = []
    for c in out:
        if merged and c["across"] - merged[-1]["across"] <= 1.5:
            m = merged[-1]
            wa, wb = m["n"], c["n"]
            centre = (m["across"] * wa + c["across"] * wb) / (wa + wb)
            half = abs(c["across"] - m["across"]) / 2.0
            m.update({"across": centre, "n": wa + wb,
                      "pm": math.hypot(max(m["pm"], c["pm"]), half)})
            continue
        merged.append(dict(c))
    return merged


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def _read(scale: Scale, point: Point, snap_pm: float, snap_conf: float,
          warnings: Sequence[str] = ()) -> Reading:
    return scale.read_point(point[0], point[1], snap_pt=snap_pm,
                            snap_confidence=snap_conf, warnings=warnings)


def _convert(ps: PageScales, axis: str, point: Point, snap_pm: float,
             snap_conf: float, named: Optional[str], warnings: List[str]
             ) -> Dict[str, Any]:
    """One position along one axis, converted (or refused, honestly)."""
    usable, pending = _scales_for(ps, axis, point, named)
    if usable:
        sc = usable[0]
        rd = _read(sc, point, snap_pm, snap_conf)
        out = {"value": _value_dict(rd), "scale": _scale_summary(sc),
               "_reading": rd}
        if len(usable) > 1:
            others = []
            for o in usable[1:3]:
                ro = _read(o, point, snap_pm, snap_conf)
                others.append({"scale": o.id, o.quantity: _r(ro.value, 6),
                               "plus_minus": _r(ro.plus_minus, 6)})
            out["other_scales"] = others
        return out
    if pending:
        sc = pending[0]
        return {"value": None, "needs_values": {
            "scale": sc.id, "labels": [_rb(b) for b in sc.label_boxes],
            "note": "the label values of this scale are not read yet: pass "
                    "values=[...] (one per label box, in this order)"},
                "position_pt": _rb(point, 2)}
    # Outside every scale's region, or no scale at all.
    named_scale = ps.get(named) if named else None
    any_axis = [s for s in ps.scales() if s.axis == axis and s.usable]
    if named_scale is not None or any_axis:
        ext = (named_scale or any_axis[0]).extent
        return {"value": None, "scale_known": False,
                "position_pt": _rb(point, 2),
                "refused": f"the position is outside the region the scale "
                           f"governs ({_rb(ext)}); it is given in points"}
    return {"value": None, "scale_known": False,
            "position_pt": _rb(point, 2),
            "note": "no scale on this page: the position is in page points"}


def measure(doc, page: int, where: Any = None, kind: str = "line", *,
            at: Optional[Dict[str, float]] = None, to: Any = None,
            scale: Optional[str] = None, values: Any = None,
            pad: Optional[float] = None, side: str = "top",
            orientation: Optional[str] = None, dpi: float = 200.0
            ) -> Dict[str, Any]:
    """Measure a position on ``page`` (0-based) through the page's own scale.

    ``where`` is a box ``[x0, y0, x1, y1]`` (or ``{"bbox": [...]}``) in PDF
    points, displayed frame, round the thing; ``pad`` the box's location error
    in points (default :func:`default_pad`). With no ``where`` the page's
    scales are listed. ``kind`` is one of :data:`KINDS`; ``at`` is
    ``{axis: value}`` for ``curve`` (axis: ``x``, ``y``, a scale id or a
    quantity); ``to`` a second box for ``distance``; ``scale`` an id from the
    listing; ``values`` label values for a scale that needs them (see
    :func:`planlens.document.scalefinder.find_scales`); ``side`` the edge
    for ``edge``; ``orientation`` ``h`` / ``v`` to say which way a line runs
    when the box does not.
    """
    from planlens.document.scalefinder import find_scales
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {list(KINDS)}")
    box = _norm_box(where)
    if box is None:
        ps = find_scales(doc, page, values=values, dpi=dpi)
        return {"page": ps.page, "scales": inventory(ps)}
    p = float(pad) if pad is not None else default_pad(box)
    window = (box[0] - p, box[1] - p, box[2] + p, box[3] + p)
    # Only the frames that can hold the box are looked for (a whole-page
    # analysis already made serves too): the speed of a measure on a dense
    # scanned page is then the speed of its pixels, not of its hundreds of
    # ruled boxes.
    ps = find_scales(doc, page, values=values, dpi=dpi, near=window)
    facts = ps.extra["facts"]
    centre = ((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
    warnings: List[str] = []
    list_only = False
    if kind != "lines" and (p >= LARGE_PAD_PT or (
            (window[2] - window[0] > WIDE_VIEW_PT
                              or window[3] - window[1] > WIDE_VIEW_PT)
                             and min(box[2] - box[0], box[3] - box[1])
                             < 0.25 * max(window[2] - window[0],
                                          window[3] - window[1]))):
        list_only = True
        warnings.append("the search window is large for the thing asked "
                        "about (a box read off a whole page?): every "
                        "candidate in it is listed and none is chosen; zoom "
                        "on the thing and measure again for one answer")
    result: Dict[str, Any] = {"page": ps.page, "kind": kind,
                              "where": _rb(box, 2), "pad_pt": _r(p, 2)}
    if kind in ("line", "lines"):
        result.update(_measure_lines(ps, facts, kind, box, window, centre, p,
                                     scale, orientation, warnings, list_only))
    elif kind == "point":
        result.update(_measure_point(ps, facts, box, window, centre, p, scale,
                                     warnings, list_only))
    elif kind == "edge":
        result.update(_measure_edge(ps, facts, window, centre, p, scale, side,
                                    warnings))
    elif kind == "curve":
        result.update(_measure_curve(ps, facts, window, centre, p, at, scale,
                                     warnings, list_only))
    elif kind == "distance":
        result.update(_measure_distance(ps, facts, box, window, centre, p, to,
                                        scale, warnings, list_only))
    else:
        result.update(_measure_text(ps, facts, window, centre, p, scale,
                                    warnings))
    if ps.warnings and not result.get("value"):
        warnings.extend(w for w in ps.warnings if w not in warnings)
    result["warnings"] = warnings
    return _clean(result)


#: Keys a result always carries, null or not: "no value" is an answer.
_KEEP_NULL = ("value", "snapped_to")


def _clean(d: Any) -> Any:
    if isinstance(d, dict):
        return {k: _clean(v) for k, v in d.items()
                if not k.startswith("_")
                and (v is not None or k in _KEEP_NULL)}
    if isinstance(d, list):
        return [_clean(v) for v in d]
    return d


def _orient_for(ps: PageScales, box: BBox, centre: Point,
                orientation: Optional[str], kind: str = "line") -> str:
    if orientation in ("h", "v"):
        return orientation
    if kind == "lines":
        # a box round a REGION (a description column): its lines run across
        # the axis of the scale that governs it
        ys, _ = _scales_for(ps, "y", centre)
        xs, _ = _scales_for(ps, "x", centre)
        if ys and not xs:
            return "h"
        if xs and not ys:
            return "v"
    w, h = box[2] - box[0], box[3] - box[1]
    if w >= 1.5 * h:
        return "h"
    if h >= 1.5 * w:
        return "v"
    ys, _ = _scales_for(ps, "y", centre)
    xs, _ = _scales_for(ps, "x", centre)
    if ys and not xs:
        return "h"
    if xs and not ys:
        return "v"
    return "h" if w >= h else "v"


def _measure_lines(ps, facts, kind, box, window, centre, pad, named,
                   orientation, warnings, list_only=False) -> Dict[str, Any]:
    orient = _orient_for(ps, box, centre, orientation, kind)
    axis = "y" if orient == "h" else "x"
    dbox = _deskew_box(facts, box)
    if kind == "lines":
        bands = [((dbox[0], dbox[2]) if orient == "h"
                  else (dbox[1], dbox[3]))]
        region_note = "the box"
        win = (box[0] - 1.0, box[1] - 1.0, box[2] + 1.0, box[3] + 1.0)
    else:
        bands, region_note = _region_bands(facts, orient, centre, dbox,
                                           _deskew_box(facts, window))
        win = window
    cands = _line_candidates(facts, orient, win, bands)
    pix = facts.pixel_pt
    out_c = []
    for c in cands:
        L = c["line"]
        pt = _point_on_line(facts, L, orient, centre)
        snap_pm = L.plus_minus_pt(pix) if L.source == "pixels" else 0.05
        conv = _convert(ps, axis, pt, snap_pm, 0.95, named, [])
        moved = abs(c["pos"] - (facts.deskew(*centre)[1] if orient == "h"
                                else facts.deskew(*centre)[0]))
        item = {"bbox": _rb(L.bbox, 2), "moved_pt": _r(moved, 2),
                "dashed": True if L.dashed else None,
                "extent_pt": _rb((c["lo"], c["hi"]), 1),
                "found_in": L.source}
        item.update({k: v for k, v in conv.items() if k != "scale"})
        item["_scale"] = conv.get("scale")
        out_c.append(item)
    res: Dict[str, Any] = {"orientation": "horizontal" if orient == "h"
                           else "vertical",
                           "region": f"lines crossing {int(STRATUM_COVER * 100)}"
                                     f" % of {region_note}"}
    if kind == "lines":
        res["lines"] = [{k: v for k, v in c.items() if k != "_scale"}
                        for c in out_c]
        res["n"] = len(out_c)
        scales = {c["_scale"]["id"]: c["_scale"] for c in out_c
                  if c.get("_scale")}
        if scales:
            res["scale"] = next(iter(scales.values()))
        if not out_c:
            warnings.append("no drawn line crosses the box")
        return res
    if len(out_c) == 1 and not list_only:
        c = out_c[0]
        res["value"] = c.get("value")
        res["snapped_to"] = {"kind": "line", "bbox": c["bbox"],
                             "moved_pt": c["moved_pt"],
                             "dashed": c.get("dashed"),
                             "found_in": c["found_in"]}
        res["alternatives"] = []
        if c.get("_scale"):
            res["scale"] = c["_scale"]
        for k in ("needs_values", "refused", "scale_known", "position_pt",
                  "other_scales", "note"):
            if k in c:
                res[k] = c[k]
        return res
    if len(out_c) > 1 or (out_c and list_only):
        res["value"] = None
        res["ambiguous"] = True
        res["alternatives"] = [{k: v for k, v in c.items() if k != "_scale"}
                               for c in sorted(out_c,
                                               key=lambda c: c["moved_pt"])]
        if len(out_c) > 1:
            warnings.append(f"{len(out_c)} lines fit the box; none was "
                            f"chosen: give a closer box (from a zoom) to "
                            f"pick one")
        sc = out_c[0].get("_scale")
        if sc:
            res["scale"] = sc
        return res
    # nothing to snap to: the box itself, with its location error
    conv = _convert(ps, axis, centre, pad, 0.6, named, [])
    res.update({k: v for k, v in conv.items()})
    res["snapped_to"] = None
    res["unsnapped"] = True
    warnings.append("no drawn line was found in the window: the box's own "
                    "centre was read, with its location error as the "
                    "uncertainty")
    return res


def _measure_point(ps, facts, box, window, centre, pad, named, warnings,
                   list_only=False) -> Dict[str, Any]:
    cands = _point_candidates(facts, window)
    res: Dict[str, Any] = {}

    def read_both(pt: Point, pm: float, conf: float) -> Dict[str, Any]:
        vals: Dict[str, Any] = {}
        scales: Dict[str, Any] = {}
        for axis in ("x", "y"):
            conv = _convert(ps, axis, pt, pm, conf, None, [])
            if conv.get("value"):
                q = conv["scale"]["id"]
                vals[q] = conv["value"]
                scales[q] = conv["scale"]
            elif conv.get("needs_values"):
                vals.setdefault("needs_values", []).append(
                    conv["needs_values"])
        return {"values": vals, "scales": scales}

    if len(cands) == 1 and not list_only and cands[0]["kind"] != "unresolved":
        c = cands[0]
        got = read_both(c["centre"], c["pm"], 0.95)
        res["value"] = got["values"] or None
        res["scales"] = list(got["scales"].values())
        res["snapped_to"] = {"kind": c["kind"], "bbox": _rb(c["bbox"], 2),
                             "at_pt": _rb(c["centre"], 2),
                             "moved_pt": _r(math.dist(c["centre"], centre), 2)}
        res["alternatives"] = []
        if not got["values"]:
            res["position_pt"] = _rb(c["centre"], 2)
            res["scale_known"] = False
        return res
    if len(cands) > 1 or (cands and list_only):
        res["value"] = None
        res["ambiguous"] = True
        alts = []
        for c in sorted(cands, key=lambda c: math.dist(c["centre"], centre)):
            got = read_both(c["centre"], c["pm"], 0.95)
            alts.append({"bbox": _rb(c["bbox"], 2),
                         "at_pt": _rb(c["centre"], 2),
                         "moved_pt": _r(math.dist(c["centre"], centre), 2),
                         "values": got["values"] or None})
        res["alternatives"] = alts
        warnings.append(f"{len(cands)} marks fit the box; none was chosen")
        return res
    got = read_both(centre, pad, 0.6)
    res["value"] = got["values"] or None
    res["scales"] = list(got["scales"].values())
    res["unsnapped"] = True
    res["position_pt"] = _rb(centre, 2)
    warnings.append("no mark was found in the window: the box's own centre "
                    "was read, with its location error as the uncertainty")
    return res


def _measure_edge(ps, facts, window, centre, pad, named, side, warnings
                  ) -> Dict[str, Any]:
    side = side if side in ("top", "bottom", "left", "right") else "top"
    boxes: List[BBox] = []
    if facts.raster is not None:
        from planlens.document.raster import components
        for bl in components(facts.raster, mask=facts.mask_without_lines(),
                             region=window, min_px=6):
            boxes.append(bl.bbox)
    for bb in [r for r, _f in facts.rects] + list(facts.fills):
        if (window[0] <= (bb[0] + bb[2]) / 2 <= window[2]
                and window[1] <= (bb[1] + bb[3]) / 2 <= window[3]):
            boxes.append(bb)
    res: Dict[str, Any] = {"side": side}
    if not boxes:
        conv = _convert(ps, "y" if side in ("top", "bottom") else "x",
                        centre, pad, 0.6, named, [])
        res.update(conv)
        res["unsnapped"] = True
        warnings.append("no ink in the window: the box's centre was read")
        return res
    b = min(boxes, key=lambda bb: math.dist(((bb[0] + bb[2]) / 2,
                                             (bb[1] + bb[3]) / 2), centre))
    if side == "top":
        pt, axis = ((b[0] + b[2]) / 2.0, b[1]), "y"
    elif side == "bottom":
        pt, axis = ((b[0] + b[2]) / 2.0, b[3]), "y"
    elif side == "left":
        pt, axis = (b[0], (b[1] + b[3]) / 2.0), "x"
    else:
        pt, axis = (b[2], (b[1] + b[3]) / 2.0), "x"
    pm = max(0.5 * facts.pixel_pt, 0.1) if facts.raster is not None else 0.05
    conv = _convert(ps, axis, pt, pm, 0.9, named, [])
    res.update(conv)
    res["snapped_to"] = {"kind": "edge", "bbox": _rb(b, 2),
                         "at_pt": _rb(pt, 2)}
    if len(boxes) > 1:
        res["note"] = f"{len(boxes)} pieces of ink in the window; the one " \
                      f"nearest the box was used"
    return res


def _axis_scale(ps: PageScales, key: str, centre: Point
                ) -> Optional[Scale]:
    """The scale ``at`` names: an id, a quantity, or ``x`` / ``y``."""
    for s in ps.scales():
        if s.id == key and s.usable:
            return s
    cands = [s for s in ps.scales() if s.usable
             and (s.quantity == key or s.axis == key
                  or s.id.endswith("." + key))]
    inside = [s for s in cands if s.contains(*centre, slack=6.0)]
    pool = inside or cands
    if not pool:
        return None
    return max(pool, key=lambda s: (s.confidence, len(s.anchors)))


def _other_axis(ps: PageScales, sc: Scale, point: Point) -> Optional[Scale]:
    frame = ps.frame_of(sc.id)
    want = "y" if sc.axis == "x" else "x"
    if frame is not None:
        for s in frame.scales.values():
            if s.axis == want and s.usable:
                return s
    usable, _ = _scales_for(ps, want, point)
    return usable[0] if usable else None


def _measure_curve(ps, facts, window, centre, pad, at, named, warnings,
                   list_only=False) -> Dict[str, Any]:
    if not at or not isinstance(at, dict):
        raise ValueError("curve needs at={axis: value}, the axis value to "
                         "read the curve at")
    key, val = next(iter(at.items()))
    sc = _axis_scale(ps, str(key), centre)
    if sc is None:
        return {"value": None, "scale_known": False,
                "note": f"no fitted scale answers to {key!r} on this page"}
    s_at = sc.position_of(float(val))
    if s_at is None:
        return {"value": None, "refused": f"{val} is outside the domain of "
                                          f"{sc.id}"}
    other = _other_axis(ps, sc, centre)
    cands = _curve_crossings(facts, sc, s_at, window)
    res: Dict[str, Any] = {"at": {sc.id: float(val)}}
    if other is None:
        res["value"] = None
        res["note"] = "the curve's other axis has no fitted scale"
        return res
    at_pm = sc.plus_minus_pt_at(s_at)

    def read(c) -> Dict[str, Any]:
        rd = _read(other, c["point"], math.hypot(c["pm"], at_pm), 0.9)
        return _value_dict(rd)

    if len(cands) == 1 and not list_only:
        c = cands[0]
        res["value"] = read(c)
        res["scale"] = _scale_summary(other)
        res["snapped_to"] = {"kind": "curve", "at_pt": _rb(c["point"], 2),
                             "moved_pt": _r(math.dist(c["point"], centre), 2)}
        res["alternatives"] = []
        return res
    if len(cands) > 1 or (cands and list_only):
        res["value"] = None
        res["ambiguous"] = True
        res["alternatives"] = [{"at_pt": _rb(c["point"], 2),
                                "moved_pt": _r(math.dist(c["point"], centre),
                                               2),
                                "value": read(c)}
                               for c in sorted(cands, key=lambda c:
                                               math.dist(c["point"], centre))]
        res["scale"] = _scale_summary(other)
        warnings.append(f"{len(cands)} curves cross {sc.id} = {val} in the "
                        f"window; none was chosen")
        return res
    rd = _read(other, centre, pad, 0.6)
    res["value"] = _value_dict(rd)
    res["scale"] = _scale_summary(other)
    res["unsnapped"] = True
    warnings.append("no curve was found crossing that value in the window: "
                    "the box's own centre was read")
    return res


def _measure_distance(ps, facts, box, window, centre, pad, to, named,
                      warnings, list_only=False) -> Dict[str, Any]:
    tbox = _norm_box(to)
    if tbox is None:
        raise ValueError("distance needs to=[x0, y0, x1, y1], the second box")
    tpad = pad
    twin = (tbox[0] - tpad, tbox[1] - tpad, tbox[2] + tpad, tbox[3] + tpad)
    tcentre = ((tbox[0] + tbox[2]) / 2.0, (tbox[1] + tbox[3]) / 2.0)
    ends = []
    for win, c0 in ((window, centre), (twin, tcentre)):
        cands = _point_candidates(facts, win)
        if len(cands) == 1 and not list_only                 and cands[0]["kind"] != "unresolved":
            ends.append((cands[0]["centre"], cands[0]["pm"], "snapped"))
        elif len(cands) > 1 or (cands and list_only):
            return {"value": None, "ambiguous": True,
                    "alternatives": [{"at_pt": _rb(c["centre"], 2)}
                                     for c in cands],
                    "note": "one end of the distance fits several marks"}
        else:
            ends.append((c0, pad, "unsnapped"))
    (p1, pm1, s1), (p2, pm2, s2) = ends
    d_pt = math.dist(facts.deskew(*p1), facts.deskew(*p2))
    pm_pt = math.hypot(pm1, pm2)
    sc = _distance_scale(ps, named)
    res: Dict[str, Any] = {"ends": [{"at_pt": _rb(p1, 2), "how": s1},
                                    {"at_pt": _rb(p2, 2), "how": s2}],
                           "length_pt": _r(d_pt, 2)}
    if sc is None:
        res["value"] = None
        res["scale_known"] = False
        res["note"] = "no distance scale on this page: the length is in points"
        return res
    v = sc.b * d_pt
    pm = math.hypot(abs(sc.b) * math.hypot(pm_pt, sc.plus_minus_pt_at(None)),
                    abs(v) * sc.rel_uncertainty)
    conf = min(sc.confidence, 0.95 if s1 == s2 == "snapped" else 0.6)
    res["value"] = {"distance": _r(v, 6), "plus_minus": _r(pm, 6),
                    "unit": sc.unit, "confidence": round(conf, 2),
                    "display": display_value(v, pm, None)}
    res["scale"] = _scale_summary(sc)
    frame = next((f for f in ps.frames if f.kind == "plan"), None)
    if frame is not None and frame.warnings:
        res["reconciled"] = list(frame.warnings)
    return res


def _measure_text(ps, facts, window, centre, pad, named, warnings
                  ) -> Dict[str, Any]:
    hits = []
    for ln in facts.texts:
        b = ln.bbox
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if window[0] <= cx <= window[2] and window[1] <= cy <= window[3]:
            hits.append(ln)
    if not hits:
        return {"value": None, "note": "no text line in the window"}
    ln = min(hits, key=lambda l: math.dist(((l.bbox[0] + l.bbox[2]) / 2,
                                            (l.bbox[1] + l.bbox[3]) / 2),
                                           centre))
    b = ln.bbox
    pt = ((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
    res = {"text": ln.text, "snapped_to": {"kind": "text", "bbox": _rb(b, 2)}}
    conv = _convert(ps, "y", pt, (b[3] - b[1]) / 2.0, 0.9, named, [])
    res.update({k: v for k, v in conv.items()})
    if len(hits) > 1:
        res["note"] = f"{len(hits)} text lines in the window; the nearest " \
                      f"was used"
    return res
