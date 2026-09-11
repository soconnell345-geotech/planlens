"""Point-pattern measures over a set of located things.

Deliberately PURE: every function here takes bare coordinates, not a
:class:`~planlens.ir.results.DrawingIR`. That makes the maths testable on
hand-written points with no drawing in sight, and it keeps the geometry
honest — nothing in this module can accidentally depend on how a symbol was
found.

WHY THIS LIVES IN planlens rather than in a discipline package. Every trade
asks the same question about a different noun: borings, piles, columns,
trees, light poles, parking stalls, sprinkler heads. "How far apart are
these?" is a drawing-review primitive. What stays with the discipline is the
JUDGEMENT — *is 45 ft adequate boring spacing for this structure?* — which
needs standards and project context this module deliberately knows nothing
about.

THE DESIGN DECISION THAT MATTERS. There is no key named ``average_spacing``
or ``mean_spacing`` anywhere in this API, at any level. "Average spacing" is
genuinely ambiguous, and on a realistic scatter the readings differ by more
than a factor of two:

- **nearest neighbour** — how far to the closest other point. What you feel
  walking the site.
- **density equivalent** — ``sqrt(area / n)``, the side of the square each
  point would occupy on a regular grid filling the same area. What a
  guideline means by "borings at 50-ft centers".
- **pairwise** — the mean of every inter-point distance. Almost never what
  anyone wants, reported because it is what a naive implementation computes
  and a reader should be able to see how far off it is.

Returning one of these under a friendly name would make the choice
invisible. So the caller gets all three, each carrying its own
``definition``, plus ``convention_spread`` telling them how much the choice
costs on THIS point set. If the spread ratio is 1.05 the choice is
immaterial; if it is 2.35 the answer depends entirely on which convention
was meant, and that is a fact the caller needs before quoting a number.

Dependencies: numpy only. scipy is not a planlens dependency and is not
needed — all-pairs distances are trivially vectorised at drawing scale, and
the convex hull is Andrew's monotone chain in about thirty lines.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.ir.measure import Quantity, combine_confidence

__all__ = [
    "convex_hull",
    "polygon_area",
    "min_area_rect",
    "nearest_neighbour",
    "point_pattern_stats",
    "stats_by_class",
    "spacing_summary",
]

Point = Tuple[float, float]

#: Above this the convex hull is judged to overstate the occupied area,
#: because the points do not fill their own hull. Not a tuned constant: it
#: is the point at which the hull and the tightest enclosing rectangle
#: disagree by more than a third, which for a point set means a concave or
#: linear footprint (borings along a street frontage, an L-shaped site).
#: Reported as a FLAG, never used to change a number.
_HULL_FILL_MIN = 0.60

#: An enclosing rectangle this much longer than it is wide means a
#: essentially linear arrangement, where an area-based spacing is
#: meaningless however it is computed. Also only ever a flag.
_LINEAR_ASPECT = 3.0


# ---------------------------------------------------------------------------
# geometry primitives
# ---------------------------------------------------------------------------

def _dedupe(points: Sequence[Point], tol: float
            ) -> Tuple[List[Point], List[int], int]:
    """Merge coincident points.

    A symbol plotted twice (a revision drawn over its original, a block
    inserted at the same spot) would otherwise contribute a
    nearest-neighbour distance of zero and drag the mean down hard. Silent
    zeros here are a wrong engineering answer, so duplicates are merged and
    the count is reported.
    """
    kept: List[Point] = []
    keep_idx: List[int] = []
    merged = 0
    for i, p in enumerate(points):
        dup = False
        for q in kept:
            if math.hypot(p[0] - q[0], p[1] - q[1]) <= tol:
                dup = True
                break
        if dup:
            merged += 1
        else:
            kept.append((float(p[0]), float(p[1])))
            keep_idx.append(i)
    return kept, keep_idx, merged


def _cross(o: Point, a: Point, b: Point) -> float:
    return (a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])


def convex_hull(points: Sequence[Point]) -> List[Point]:
    """Convex hull, counter-clockwise, without the closing repeat.

    Andrew's monotone chain: sort, then build the lower and upper chains.
    Exact in floating point for the comparisons that matter (the sign of a
    cross product), and no dependency.

    Fewer than three distinct points, or all points collinear, returns the
    input's distinct points — the caller must treat a degenerate hull as
    "no area", not as zero area, because those mean different things.
    """
    pts = sorted(set((float(x), float(y)) for x, y in points))
    if len(pts) <= 2:
        return pts
    lower: List[Point] = []
    for p in pts:
        while len(lower) >= 2 and _cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)
    upper: List[Point] = []
    for p in reversed(pts):
        while len(upper) >= 2 and _cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)
    hull = lower[:-1] + upper[:-1]
    return hull if len(hull) >= 3 else pts


def polygon_area(verts: Sequence[Point]) -> float:
    """Absolute shoelace area. Fewer than 3 vertices is 0.0."""
    n = len(verts)
    if n < 3:
        return 0.0
    s = 0.0
    for i in range(n):
        x1, y1 = verts[i]
        x2, y2 = verts[(i + 1) % n]
        s += x1 * y2 - x2 * y1
    return abs(s) * 0.5


def min_area_rect(points: Sequence[Point]
                  ) -> Optional[Dict[str, float]]:
    """Smallest-area enclosing rectangle, by rotating calipers on the hull.

    Used only as a SECOND OPINION on the hull area. The minimum-area
    rectangle must contain the hull, so when the hull fills only a small
    fraction of it the points are arranged concavely or linearly and an
    area-based spacing should be read with suspicion. Reporting both lets
    the caller see that; picking one silently would not.
    """
    hull = convex_hull(points)
    if len(hull) < 3:
        return None
    best: Optional[Dict[str, float]] = None
    n = len(hull)
    for i in range(n):
        x1, y1 = hull[i]
        x2, y2 = hull[(i + 1) % n]
        ex, ey = x2 - x1, y2 - y1
        L = math.hypot(ex, ey)
        if L == 0.0:
            continue
        ux, uy = ex / L, ey / L          # along the edge
        vx, vy = -uy, ux                 # normal to it
        us = [p[0] * ux + p[1] * uy for p in hull]
        vs = [p[0] * vx + p[1] * vy for p in hull]
        w = max(us) - min(us)
        h = max(vs) - min(vs)
        area = w * h
        if best is None or area < best["area"]:
            long_side, short_side = (w, h) if w >= h else (h, w)
            best = {
                "area": area,
                "length": long_side,
                "width": short_side,
                "aspect": (long_side / short_side) if short_side > 0 else
                          float("inf"),
                "angle_deg": math.degrees(math.atan2(uy, ux)) % 180.0,
            }
    return best


def nearest_neighbour(points: Sequence[Point]
                      ) -> Tuple[List[float], List[int]]:
    """For each point, the distance to its closest other point and its index.

    Exact all-pairs. At drawing scale (n in the tens to low thousands) that
    is a few million float operations — far cheaper than the alternative's
    complexity, and it avoids adding scipy for a k-d tree that would not be
    measurably faster below n of a few thousand.
    """
    n = len(points)
    if n < 2:
        return [], []
    try:
        import numpy as np
        a = np.asarray(points, dtype=float)
        d2 = ((a[:, None, :] - a[None, :, :]) ** 2).sum(-1)
        np.fill_diagonal(d2, np.inf)
        idx = d2.argmin(axis=1)
        return list(np.sqrt(d2[np.arange(n), idx])), [int(i) for i in idx]
    except ImportError:                       # pragma: no cover
        dists: List[float] = []
        nbrs: List[int] = []
        for i, p in enumerate(points):
            best_d, best_j = float("inf"), -1
            for j, q in enumerate(points):
                if i == j:
                    continue
                d = math.hypot(p[0] - q[0], p[1] - q[1])
                if d < best_d:
                    best_d, best_j = d, j
            dists.append(best_d)
            nbrs.append(best_j)
        return dists, nbrs


def _pairwise(points: Sequence[Point]) -> List[float]:
    out: List[float] = []
    n = len(points)
    for i in range(n):
        for j in range(i + 1, n):
            out.append(math.hypot(points[i][0] - points[j][0],
                                  points[i][1] - points[j][1]))
    return out


def _stats(vals: Sequence[float]) -> Dict[str, float]:
    if not vals:
        return {}
    s = sorted(vals)
    n = len(s)
    mean = sum(s) / n
    var = sum((v - mean) ** 2 for v in s) / n
    mid = s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])
    return {"mean": mean, "median": mid, "min": s[0], "max": s[-1],
            "std": math.sqrt(var)}


# ---------------------------------------------------------------------------
# the public measure
# ---------------------------------------------------------------------------

def point_pattern_stats(
    points: Sequence[Point],
    ids: Optional[Sequence[str]] = None,
    *,
    unit: str = "pt",
    units_per_input: Optional[float] = None,
    unit_name: Optional[str] = None,
    unit_confidence: float = 1.0,
    unit_rel_uncertainty: float = 0.0,
    unit_basis: str = "",
    point_confidence: float = 1.0,
    dedupe_tol: float = 1e-6,
) -> Dict[str, Any]:
    """Every spacing convention for one point set, each named and defined.

    ``units_per_input`` converts from the coordinates' own units into
    ``unit_name``. Omit it and every length comes back in ``unit`` with
    ``scale_known: False`` — the module will not guess that page points are
    feet.

    Confidence on every reported length is
    ``min(point_confidence, unit_confidence)``: a spacing is exactly as
    trustworthy as the least trustworthy thing beneath it, which is almost
    always the scale rather than the geometry.
    """
    raw = [(float(x), float(y)) for x, y in points]
    kept, keep_idx, merged = _dedupe(raw, dedupe_tol)
    kept_ids = ([str(ids[i]) for i in keep_idx]
                if ids is not None and len(ids) == len(raw)
                else [f"p{i}" for i in keep_idx])
    n = len(kept)

    scale_known = units_per_input is not None
    out_unit = (unit_name or unit) if scale_known else unit
    conf = combine_confidence(point_confidence, unit_confidence)

    def L(v: float) -> Quantity:
        """One length, carried with everything it rests on."""
        q = Quantity(v, unit, confidence=point_confidence,
                     scale_known=scale_known)
        if scale_known:
            q = q.scaled(float(units_per_input), out_unit,
                         factor_confidence=unit_confidence,
                         factor_rel_uncertainty=unit_rel_uncertainty,
                         basis=unit_basis)
        return q

    def A(v: float) -> Quantity:
        """One area — the scale enters squared, so does its uncertainty."""
        q = Quantity(v, f"{unit}^2", confidence=point_confidence,
                     scale_known=scale_known)
        if scale_known:
            q = q.scaled(float(units_per_input) ** 2, f"{out_unit}^2",
                         factor_confidence=unit_confidence,
                         factor_rel_uncertainty=2.0 * unit_rel_uncertainty,
                         basis=unit_basis)
        return q

    result: Dict[str, Any] = {
        "n": n,
        "n_input": len(raw),
        "n_duplicates_merged": merged,
        "units": out_unit,
        "scale_known": scale_known,
        "confidence": conf,
        "proposal_only": True,
        "notes": [],
    }
    if merged:
        result["notes"].append(
            f"{merged} coincident point(s) merged within {dedupe_tol:g} "
            f"{unit} — duplicates would report a spacing of zero.")

    if n < 2:
        result["notes"].append(
            "Fewer than two distinct points: no spacing is defined.")
        result["nearest_neighbour"] = None
        result["density_equivalent"] = None
        result["pairwise"] = None
        result["convention_spread"] = None
        return result

    # --- convention 1: nearest neighbour ---------------------------------
    nn_d, nn_i = nearest_neighbour(kept)
    nn = _stats(nn_d)
    result["nearest_neighbour"] = {
        "definition": ("distance from each point to its single closest "
                       "neighbour; the spacing you experience on the ground"),
        "mean": L(nn["mean"]).to_dict(),
        "median": L(nn["median"]).to_dict(),
        "min": L(nn["min"]).to_dict(),
        "max": L(nn["max"]).to_dict(),
        "std": L(nn["std"]).to_dict(),
        "per_point": [
            {"id": kept_ids[i], "nearest_id": kept_ids[nn_i[i]],
             "distance": L(nn_d[i]).to_dict()}
            for i in range(n)],
    }

    # --- convention 2: density equivalent --------------------------------
    hull = convex_hull(kept)
    hull_area = polygon_area(hull)
    obb = min_area_rect(kept)
    if n < 3 or hull_area <= 0.0:
        result["density_equivalent"] = None
        result["notes"].append(
            "Convex hull is degenerate (fewer than three non-collinear "
            "points), so an area-based spacing is undefined — not zero.")
    else:
        fill = (hull_area / obb["area"]) if obb and obb["area"] > 0 else 1.0
        aspect = obb["aspect"] if obb else 1.0
        overstates = fill < _HULL_FILL_MIN or aspect > _LINEAR_ASPECT
        result["density_equivalent"] = {
            "definition": ("sqrt(hull_area / n) — the side of the square "
                           "each point would occupy if the points were on a "
                           "regular grid filling their convex hull; this is "
                           "what a guideline means by 'points at N-ft centers'"),
            "value": L(math.sqrt(hull_area / n)).to_dict(),
            "hull_area": A(hull_area).to_dict(),
            "hull_vertices": [[round(x, 4), round(y, 4)] for x, y in hull],
            "enclosing_rect_area": A(obb["area"]).to_dict() if obb else None,
            "hull_fill_fraction": round(fill, 4),
            "enclosing_rect_aspect": round(aspect, 3),
            "hull_may_overstate_area": bool(overstates),
        }
        if overstates:
            result["notes"].append(
                "The points do not fill their own convex hull (fill "
                f"{fill:.2f}, aspect {aspect:.1f}): the footprint is concave "
                "or nearly linear, so the density-equivalent spacing is "
                "optimistic. Prefer the nearest-neighbour reading here.")

    # --- convention 3: all pairs -----------------------------------------
    pw = _stats(_pairwise(kept))
    result["pairwise"] = {
        "definition": ("mean of all n(n-1)/2 inter-point distances; reported "
                       "for comparison because it is what a naive "
                       "implementation computes, and it is rarely what is "
                       "meant"),
        "mean": L(pw["mean"]).to_dict(),
        "min": L(pw["min"]).to_dict(),
        "max": L(pw["max"]).to_dict(),
    }

    # --- how much the choice costs ---------------------------------------
    candidates = [nn["mean"]]
    if result["density_equivalent"] is not None:
        candidates.append(math.sqrt(hull_area / n))
    candidates.append(pw["mean"])
    lo, hi = min(candidates), max(candidates)
    result["convention_spread"] = {
        "min": L(lo).to_dict(),
        "max": L(hi).to_dict(),
        "ratio": round(hi / lo, 3) if lo > 0 else None,
        "note": ("the conventions above differ by this factor on this point "
                 "set — state which one you used"),
    }

    # --- arrangement, with its caveat ------------------------------------
    if result["density_equivalent"] is not None and nn["mean"] > 0:
        expected = 0.5 * math.sqrt(hull_area / n)
        R = nn["mean"] / expected if expected > 0 else None
        if R is not None:
            reading = ("clustered" if R < 0.9 else
                       "regular" if R > 1.1 else "random")
            result["clustering"] = {
                "clark_evans_R": round(R, 3),
                "reading": reading,
                "definition": ("observed mean nearest-neighbour distance "
                               "divided by the value expected for the same "
                               "density under complete spatial randomness"),
                "caveat": ("edge effects are uncorrected, which biases R "
                           "upward at small n; treat as indicative only"),
            }
    return result


def stats_by_class(
    points: Sequence[Point],
    classes: Sequence[str],
    ids: Optional[Sequence[str]] = None,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Spacing for the whole set AND for each class within it.

    "The average spacing of the borings" may mean every exploration on the
    sheet or only the current program's. The slice costs nothing to compute
    and the ambiguity is real, so both are returned rather than guessed.
    """
    out: Dict[str, Any] = {
        "all": point_pattern_stats(points, ids, **kwargs),
        "by_class": {},
    }
    seen: Dict[str, List[int]] = {}
    for i, c in enumerate(classes):
        seen.setdefault(str(c), []).append(i)
    for cls, idxs in sorted(seen.items()):
        sub_ids = [str(ids[i]) for i in idxs] if ids is not None else None
        out["by_class"][cls] = point_pattern_stats(
            [points[i] for i in idxs], sub_ids, **kwargs)
    return out


def spacing_summary(stats: Dict[str, Any]) -> Dict[str, Any]:
    """The conventions as labelled answer candidates, with when to use each.

    This is the shape an LLM should be handed. It cannot pick arbitrarily
    because no candidate is privileged and every one states the question it
    answers; and ``choice_matters`` tells it whether the decision is
    consequential on this particular point set.
    """
    cands: List[Dict[str, Any]] = []
    nn = stats.get("nearest_neighbour")
    de = stats.get("density_equivalent")
    pw = stats.get("pairwise")
    if nn:
        cands.append({
            "convention": "nearest_neighbour_mean",
            "value": nn["mean"],
            "when_to_use": ("the usual reading of 'how far apart are they' — "
                            "use for coverage and for gaps between adjacent "
                            "explorations"),
        })
    if de:
        cands.append({
            "convention": "density_equivalent",
            "value": de["value"],
            "when_to_use": ("comparing against a published grid-spacing "
                            "guideline such as 'borings at 50-ft centers'"),
        })
    if pw:
        cands.append({
            "convention": "pairwise_mean",
            "value": pw["mean"],
            "when_to_use": ("rarely appropriate; included so a naive "
                            "computation can be recognised for what it is"),
        })
    spread = stats.get("convention_spread") or {}
    ratio = spread.get("ratio")
    return {
        "n": stats.get("n"),
        "units": stats.get("units"),
        "scale_known": stats.get("scale_known"),
        "answer_candidates": cands,
        "choice_matters": bool(ratio is not None and ratio > 1.15),
        "convention_spread_ratio": ratio,
        "notes": stats.get("notes", []),
        "proposal_only": True,
    }
