"""Fit the model-space -> plotted-page transform from anchor geometry.

A plotted sheet relates to its CAD model space by a similarity transform
restricted to what plotting actually does: a uniform scale (the plot
scale), an axis rotation (0/90/180/270 — landscape drawings on portrait
pages and vice versa), and a translation. Fitting that transform lets
native DXF entities (leader vertices, dimension defpoints, text inserts)
be located on the plotted PDF — the basis for ground-truth scoring and
for cross-referencing a CAD file against its own plot.

Verified against the Mecklenburg ground-truth pairs (2026-09-04): sheet
21.01 is a landscape model plotted 90-degrees onto a portrait page;
3001 / 10.31A are drawn at real-world size (thousands of inches) and
plotted near 1:300; a translation-only fit fails on all three.

The fit is anchor-voting RANSAC-style, needing no correspondence input:
anchor points (model coords known exactly) are matched against IR
line/polyline endpoints under candidate (rotation, scale) hypotheses;
the (rotation, scale, offset) cell with the most consistent votes wins
and is refined by a least-squares scale+offset solve on the matched
pairs. Returns a dict with the transform, the vote/match counts, and an
``apply`` mapping — or ``None`` when no hypothesis earns enough votes.

DEGENERATE-SCALE GUARD (was a docstring-only caveat until 2026-09-05):
``n_matched``/``rms`` are NOT trustworthy fit-quality signals when a
candidate scale shrinks the anchor cloud to a small fraction of the
page — dense linework matches anything there: random anchors used to
return "matched 10/10, rms < 1 pt" near scale ~ 1.0 on a 1:72 plot,
and noisy anchors could win with the WRONG rotation rather than
returning ``None``. :func:`fit_plot_transform` now REJECTS every scale
hypothesis whose scaled anchor-cloud diagonal spans less than
``min_extent_frac`` (default 5%) of the drawing diagonal, and reports
the winning hypothesis's ``extent_frac`` so callers can apply a
stricter bar. A genuinely tiny anchor cloud (all anchors inside one
small detail) needs an explicit lower ``min_extent_frac`` from the
caller — the honest trade for making random-anchor "fits" impossible
by default. With exact CAD anchors spanning the sheet (the scoring use
case) the fit is excellent — 0.02-0.03 pt rms, 100% anchors matched on
the validation sheets.
"""

from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]

#: Rotations plotting actually produces (degrees, counter-clockwise).
_ROTATIONS = (0, 90, 180, 270)


def _rot_xy(x: float, y: float, rot: int) -> Point:
    if rot == 0:
        return (x, y)
    if rot == 90:
        return (-y, x)
    if rot == 180:
        return (-x, -y)
    return (y, -x)  # 270


def _extent(pts: Sequence[Point]) -> Tuple[float, float, float, float]:
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _ir_shaft_endpoints(ir, min_length: float) -> List[Point]:
    eps: List[Point] = []
    for e in ir.entities:
        if e.KIND in ("line", "polyline") and e.length() >= min_length:
            pts = e.points()
            if len(pts) >= 2:
                eps.append(tuple(pts[0]))
                eps.append(tuple(pts[-1]))
                # Interior bend points anchor too (leader bends are drawn).
                for p in pts[1:-1]:
                    eps.append(tuple(p))
    return eps


def _vote_offset(anchors_rs: Sequence[Point], endpoints: Sequence[Point],
                 bin_pt: float = 2.0) -> Tuple[int, Point, int, int]:
    """Best (votes, offset, total_votes, n_bins) for a scale hypothesis.

    One anchor may vote for many offsets (every endpoint within the page
    span); only the TRUE offset accumulates votes from many different
    anchors, so the modal bin identifies it. Bins are coarse (2 pt) to
    absorb plot line-weight and fit noise; refinement happens later.
    ``total_votes``/``n_bins`` parameterize the chance-collision null the
    caller tests the modal bin against (see :func:`_modal_significant`).
    """
    votes: Counter = Counter()
    for (ax, ay) in anchors_rs:
        seen = set()
        for (ex, ey) in endpoints:
            key = (round((ex - ax) / bin_pt), round((ey - ay) / bin_pt))
            # Each anchor votes a given bin at most once — a dense glyph
            # cluster of endpoints must not let one anchor stuff the box.
            if key not in seen:
                seen.add(key)
                votes[key] += 1
    if not votes:
        return 0, (0.0, 0.0), 0, 0
    (kx, ky), n = votes.most_common(1)[0]
    return n, (kx * bin_pt, ky * bin_pt), sum(votes.values()), len(votes)


#: Bonferroni-style significance budget for the modal-bin test, shared
#: across every (rotation, scale) hypothesis a fit tries. 1e-6 keeps a
#: genuine fit (whose modal bin collects most anchors against a chance
#: mean near 1) unequivocally in, while the dense-sheet chance extreme
#: (modal ~ Poisson tail of ~1.7 across ~1e5 bins) stays out.
_SIGNIFICANCE = 1e-6


def _modal_significant(n: int, total: int, n_bins: int) -> bool:
    """Is a modal vote count ``n`` significant vs the chance null?

    On a dense sheet every anchor votes thousands of offset bins, and the
    MAXIMUM of ~1e5 chance-mean-lambda Poisson bins reaches 8-10 votes by
    pure collision — measured: 10 random anchors on a real 10k-entity
    sheet "matched 8/10 with rms < 1 pt" through the old min-votes gate.
    The modal bin is only evidence of a real transform when its count is
    far outside the Poisson(lambda = total/n_bins) tail after a union
    bound over the bins: Chernoff  P(X >= n) <= exp(-lam) (e lam / n)^n,
    require  n_bins * P < _SIGNIFICANCE.
    """
    if n_bins <= 0 or n <= 0:
        return False
    lam = total / n_bins
    if n <= lam:
        return False
    # log of the Chernoff bound + union bound over bins.
    log_p = -lam + n * (1.0 + math.log(lam / n)) + math.log(n_bins)
    return log_p < math.log(_SIGNIFICANCE)


def _matched_pairs(anchors_rs, endpoints, offset, tol):
    pairs = []
    for a in anchors_rs:
        tx, ty = a[0] + offset[0], a[1] + offset[1]
        best, bd = None, None
        for e in endpoints:
            d = math.hypot(e[0] - tx, e[1] - ty)
            if d <= tol and (bd is None or d < bd):
                best, bd = e, d
        if best is not None:
            pairs.append((a, best, bd))
    return pairs


def _segment_scale_votes(anchor_chains, ir, max_segments: int = 4000
                         ) -> List[float]:
    """Scale candidates from truth-segment vs IR-segment length ratios.

    Rotation-invariant and crop-immune: every plotted anchor chain's
    segment has an IR counterpart of length ``scale x model length``, so
    the true scale accumulates votes in a log-binned ratio histogram while
    unrelated pairings diffuse. Returns the top vote-bin centers.
    """
    truth_lens = []
    for chain in anchor_chains or ():
        for a, b in zip(chain, chain[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L > 1e-9:
                truth_lens.append(L)
    if not truth_lens:
        return []
    ir_lens = []
    for e in ir.entities:
        if e.KIND not in ("line", "polyline"):
            continue
        pts = e.points()
        for a, b in zip(pts, pts[1:]):
            L = math.hypot(b[0] - a[0], b[1] - a[1])
            if L > 1e-9:
                ir_lens.append(L)
            if len(ir_lens) >= max_segments:
                break
        if len(ir_lens) >= max_segments:
            break
    votes: Counter = Counter()
    for tl in truth_lens:
        seen = set()
        for il in ir_lens:
            key = round(math.log10(il / tl), 2)  # ~2.3% bins
            if key not in seen:
                seen.add(key)
                votes[key] += 1
    return [10 ** k for k, _ in votes.most_common(5)]


def fit_plot_transform(anchors: Sequence[Point], ir,
                       anchor_chains: Optional[Sequence[Sequence[Point]]]
                       = None,
                       scale_hints: Optional[Sequence[float]] = None,
                       min_votes: int = 6,
                       match_tol: float = 3.0,
                       min_extent_frac: float = 0.05
                       ) -> Optional[Dict[str, Any]]:
    """Fit (rotation, scale, offset) mapping model-space anchors onto ``ir``.

    Parameters
    ----------
    anchors : sequence of (x, y)
        Model-space points known to lie on PLOTTED LINEWORK — leader /
        multileader vertices and dimension defpoints are ideal. Text
        inserts are poor anchors on SHX plots (no corresponding endpoint).
    ir : DrawingIR
        The ingested plotted page.
    anchor_chains : optional
        The same anchors as connected vertex CHAINS (e.g. each leader's
        vertex list). Their segment lengths drive rotation-invariant scale
        voting — the strongest scale evidence available; pass them whenever
        the caller has chains.
    scale_hints : optional
        Extra scale candidates (pt per model unit) to try alongside the
        derived ones. 72.0 (paper-scale inches) and 1.0 (already-in-page
        coordinates) are always tried.
    min_votes : int
        Minimum modal-bin votes for a hypothesis to be considered at all.
    match_tol : float
        Point tolerance for the final matched-pair refinement.
    min_extent_frac : float
        Degenerate-scale guard (see module docstring): a scale hypothesis
        is considered only when it maps the anchor-cloud diagonal to at
        least this fraction of the drawing diagonal. Below it, dense
        linework matches anything and ``n_matched``/``rms`` lie. Lower it
        explicitly (with care) to fit an anchor cloud that genuinely
        occupies a tiny corner of the sheet.

    Returns ``{rotation_deg, scale, offset, votes, n_matched, rms,
    extent_frac, apply}`` for the best hypothesis, or ``None`` if nothing
    reaches ``min_votes`` at an admissible extent (the honest answer for
    an unfittable sheet).
    """
    anchors = [tuple(a) for a in anchors]
    if len(anchors) < 3:
        return None
    bb = ir.bbox()
    if bb is None:
        return None
    ir_w, ir_h = bb[2] - bb[0], bb[3] - bb[1]
    page_diag = math.hypot(ir_w, ir_h)
    endpoints = _ir_shaft_endpoints(ir, min_length=page_diag * 0.005)
    if not endpoints:
        return None

    ratio_seeds = _segment_scale_votes(anchor_chains, ir)

    # Degenerate-scale guard: the anchor-cloud diagonal (rotation-
    # invariant) scaled by a hypothesis must span a meaningful fraction
    # of the drawing diagonal, else dense linework matches anything and
    # votes/rms mean nothing (see module docstring).
    a_ext = _extent(anchors)
    cloud_diag = math.hypot(a_ext[2] - a_ext[0], a_ext[3] - a_ext[1])
    if cloud_diag <= 0:
        return None
    min_scale = min_extent_frac * page_diag / cloud_diag

    best: Optional[Dict[str, Any]] = None
    for rot in _ROTATIONS:
        rot_pts = [_rot_xy(a[0], a[1], rot) for a in anchors]
        ext = _extent(rot_pts)
        tw, th = max(ext[2] - ext[0], 1e-9), max(ext[3] - ext[1], 1e-9)
        # Scale seeds: segment-ratio votes (strongest), extent-derived
        # content-fills-window guesses, paper-scale-inches (72), identity
        # (1.0), and any caller hints. Duplicates collapse.
        seeds = {round(s, 6) for s in
                 [*ratio_seeds, ir_w / tw, ir_h / th, ir_w / th, ir_h / tw,
                  72.0, 1.0, *(scale_hints or [])] if 1e-4 < s < 1e5}
        for s in seeds:
            if s < min_scale:
                continue
            scaled = [(p[0] * s, p[1] * s) for p in rot_pts]
            n, off, total, n_bins = _vote_offset(scaled, endpoints)
            if n < min_votes:
                continue
            # A vote-weak hypothesis (a small anchor cloud on a dense
            # sheet cannot beat the chance null by count alone) is not
            # discarded yet: it gets one more chance below via the
            # EXACTNESS branch — vector plots are numerically exact, so a
            # genuine transform refines to sub-0.1-pt residuals on nearly
            # every anchor, while chance pairs scattered inside a 3-pt
            # tolerance have no reason to co-refine anywhere near that.
            vote_significant = _modal_significant(n, total, n_bins)
            # Refine: least-squares scale+offset on matched pairs (rotation
            # held fixed), iterated once — scale from the ratio of matched
            # spans, offset from the mean residual.
            s_ref, off_ref = s, off
            pairs = _matched_pairs([(p[0] * s_ref, p[1] * s_ref)
                                    for p in rot_pts],
                                   endpoints, off_ref, match_tol * 2)
            for _ in range(2):
                if len(pairs) < 3:
                    break
                # Solve min sum |s*p + t - e|^2 over s (scalar) and t.
                pa = [(a[0] / s_ref, a[1] / s_ref) for a, _, _ in pairs]
                pe = [e for _, e, _ in pairs]
                n_p = len(pairs)
                ma = (sum(p[0] for p in pa) / n_p, sum(p[1] for p in pa) / n_p)
                me = (sum(p[0] for p in pe) / n_p, sum(p[1] for p in pe) / n_p)
                num = sum((a[0] - ma[0]) * (e[0] - me[0])
                          + (a[1] - ma[1]) * (e[1] - me[1])
                          for a, e in zip(pa, pe))
                den = sum((a[0] - ma[0]) ** 2 + (a[1] - ma[1]) ** 2
                          for a in pa)
                if den <= 0:
                    break
                s_new = num / den
                if s_new <= 0:
                    break
                off_new = (me[0] - s_new * ma[0], me[1] - s_new * ma[1])
                s_ref, off_ref = s_new, off_new
                pairs = _matched_pairs([(p[0] * s_ref, p[1] * s_ref)
                                        for p in rot_pts],
                                       endpoints, off_ref, match_tol)
            if not pairs:
                continue
            if s_ref < min_scale:
                continue  # refinement drifted into the degenerate regime
            rms = math.sqrt(sum(d * d for _, _, d in pairs) / len(pairs))
            if not vote_significant:
                # EXACTNESS branch (see above): nearly all anchors matched
                # AND plot-exact residuals, or the hypothesis dies. The
                # thresholds are measured: genuine vector-plot fits sit at
                # 0.02-0.03 pt rms with 100% matched; the best chance fit
                # observed across random-anchor trials on a dense real
                # sheet was 0.38 pt rms at 80% matched.
                if (len(pairs) < 0.9 * len(anchors)
                        or rms > 0.05 * match_tol):
                    continue
            cand = {"rotation_deg": rot, "scale": s_ref, "offset": off_ref,
                    "votes": n, "n_matched": len(pairs), "rms": round(rms, 3),
                    "extent_frac": round(s_ref * cloud_diag / page_diag, 4)}
            key = (cand["n_matched"], -cand["rms"])
            if best is None or key > (best["n_matched"], -best["rms"]):
                best = cand

    if best is None:
        return None

    rot, s, off = best["rotation_deg"], best["scale"], best["offset"]

    def apply(pt: Point) -> Point:
        r = _rot_xy(pt[0], pt[1], rot)
        return (r[0] * s + off[0], r[1] * s + off[1])

    best["apply"] = apply
    return best
