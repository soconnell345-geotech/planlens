"""The Scale primitive: fits, regular runs, log decades, uncertainty, adapters.

Numbers only — nothing here reads a page. Truth is stated by hand.
"""

import math

import pytest

from planlens.document import scales as S
from planlens.ir.measure import Quantity


# -- fits -------------------------------------------------------------------------

def test_a_linear_run_fits_exactly():
    pts = [(100.0 + 46.0 * k, 1.0 + k) for k in range(10)]
    fit = S.fit_points(pts)
    assert fit is not None
    assert fit.b == pytest.approx(1 / 46.0)
    assert fit.a + fit.b * 100.0 == pytest.approx(1.0)
    assert fit.max_res_pt < 1e-9
    assert fit.step_pt == pytest.approx(46.0)


def test_a_log_run_fits_in_log10():
    pts = [(120.0 + 120.0 * k, 10.0 ** k) for k in range(4)]
    fit = S.fit_points(pts, "log10")
    assert fit is not None
    assert fit.b == pytest.approx(1 / 120.0)


def test_fewer_than_three_anchors_is_refused():
    assert S.fit_points([(0.0, 0.0), (10.0, 1.0)]) is None


def test_steps_of_ten_times_the_base_are_not_a_ruler():
    # "0.00, 2.00, 2.20": printed contacts on a sketch, not a scale
    assert S.fit_points([(0.0, 0.0), (190.0, 2.0), (216.0, 2.2)]) is None


def test_a_missed_label_leaves_a_double_step_that_is_allowed():
    pts = [(46.0 * k, float(k)) for k in (1, 2, 3, 5, 6, 7, 8)]
    fit = S.fit_points(pts)
    assert fit is not None and fit.n == 7


def test_one_misread_value_is_dropped_and_named():
    pos = [46.0 * k for k in range(10)]
    vals = [float(k) for k in range(10)]
    vals[5] = 7.0
    fit = S.fit_labels(pos, vals, max_excluded=1)
    assert fit is not None
    assert fit.dropped == [5]
    assert fit.b == pytest.approx(1 / 46.0)


def test_two_misread_values_are_refused_when_capped():
    pos = [46.0 * k for k in range(10)]
    vals = [float(k) for k in range(10)]
    vals[2] = 9.5
    vals[7] = 1.5
    assert S.fit_labels(pos, vals, max_excluded=1) is None


def test_unread_values_are_skipped_not_guessed():
    pos = [46.0 * k for k in range(6)]
    vals = [0.0, None, 2.0, 3.0, 4.0, 5.0]
    fit = S.fit_labels(pos, vals)
    assert fit is not None and sorted(fit.kept) == [0, 2, 3, 4, 5]


def test_a_residual_over_a_fifth_of_a_step_is_refused():
    pts = [(0.0, 0.0), (46.0, 1.0), (92.0 + 25.0, 2.0), (138.0, 3.0)]
    assert S.fit_points(pts, allow_drop=0) is None


# -- spacing -----------------------------------------------------------------------

def test_values_go_to_lines_by_spacing_not_by_count():
    # eleven gridlines 0..100 with the one at 50 lost (the E6 trap)
    pos = [300.0 + 32.0 * k for k in range(11) if k != 5]
    idx = S.index_by_spacing(pos)
    assert idx is not None
    assert idx["step"] == pytest.approx(32.0)
    assert idx["index"] == [0, 1, 2, 3, 4, 6, 7, 8, 9, 10]


def test_an_off_grid_line_is_named():
    pos = [0.0, 20.0, 40.0, 47.0, 60.0, 80.0]
    idx = S.index_by_spacing(pos)
    assert idx["off_grid"] == [3]


def _log_lines(origin, decade, n_decades, falling=False, drop=()):
    out = []
    for e in range(n_decades):
        for m in range(1, 10):
            out.append(e + math.log10(m))
    out.append(float(n_decades))
    pos = [origin + (-q if falling else q) * decade for q in out]
    return [p for i, p in enumerate(pos) if i not in drop]


def test_log_decades_from_the_pattern_alone():
    pos = _log_lines(100.0, 82.0, 5)
    dec = S.log_decades(pos)
    assert dec is not None
    assert dec["decade"] == pytest.approx(82.0, abs=1e-6)
    assert dec["error"] < 1e-6
    assert len(dec["majors"]) == 6


def test_log_decades_falling_down_the_page():
    pos = _log_lines(600.0, 113.3, 3, falling=True)
    dec = S.log_decades(pos)
    assert dec is not None and dec["falling"]
    assert dec["decade"] == pytest.approx(113.3, abs=1e-6)


def test_log_decades_with_lines_lost_in_every_decade():
    pos = _log_lines(100.0, 90.0, 4, drop=(3, 12, 21, 30))
    dec = S.log_decades(pos)
    assert dec is not None
    assert dec["decade"] == pytest.approx(90.0, rel=1e-3)


def test_an_even_grid_is_not_a_log_decade():
    assert S.log_decades([10.0 * k for k in range(12)]) is None


def _pairs_reference(ps, tol):
    """The plain-loop pair search as it was before it was vectorised
    (2026-10-08): the vectorised one must give the same answer to the bit."""
    n = len(ps)
    mant = [math.log10(k) for k in range(1, 10)] + [1.0]
    min_gap = min((b - a for a, b in zip(ps, ps[1:]) if b - a > 1e-6),
                  default=0.0)
    min_dec = max(20.0, 0.8 * min_gap / 0.0458)
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            dec = ps[j] - ps[i]
            if dec < min_dec:
                continue
            for falling in (False, True):
                major = ps[j] if falling else ps[i]
                sign = -1.0 if falling else 1.0
                hits = 0
                worst = 0.0
                for p in ps:
                    x = sign * (p - major) / dec
                    d = math.floor(x)
                    frac = x - d
                    dist = min(abs(frac - m) for m in mant)
                    if dist * dec <= max(0.6, tol * dec):
                        hits += 1
                        worst = max(worst, dist)
                if best is None or hits > best[0] or (
                        hits == best[0] and worst < best[1]):
                    best = (hits, worst, (j if falling else i), dec, falling)
    if best is None or best[0] < 10 or best[0] < 0.85 * n:
        return None
    hits, worst, mi, dec, falling = best
    return (worst, mi, dec, falling)


@pytest.mark.parametrize("seed", range(12))
def test_the_pair_search_is_the_plain_loop_vectorised(seed):
    import random
    rng = random.Random(seed)
    kind = seed % 4
    if kind == 0:      # a log grid with lines lost and jitter
        pos = _log_lines(50.0 + rng.uniform(0, 80), rng.uniform(60, 140),
                         rng.randint(2, 4),
                         drop=tuple(rng.sample(range(30), 4)),
                         falling=bool(seed % 2))
        pos = [p + rng.gauss(0, 0.2) for p in pos]
    elif kind == 1:    # an even grid
        pos = [20.0 + 13.7 * k + rng.gauss(0, 0.3) for k in range(14)]
    elif kind == 2:    # rows of text projected as lines: irregular
        pos = sorted(rng.uniform(0, 600) for _ in range(rng.randint(10, 30)))
    else:              # a clean log grid
        pos = _log_lines(100.0, rng.uniform(70, 120), rng.randint(2, 3))
    pos = sorted(pos)
    for tol in (0.015, 0.035):
        assert S._log_decades_by_pairs(pos, tol) == _pairs_reference(pos, tol)


# -- uncertainty, confidence, display --------------------------------------------------

def _scale(**kw):
    base = dict(id="p0.depth", page=0, extent=(0, 0, 600, 800),
                quantity="depth", unit="m", axis="y", a=-4.0, b=0.02,
                n_fit=10, s_mean=400.0, sss=10 * 140.0 ** 2, rms_dof_pt=0.2,
                anchor_pt=0.3, pixel_pt=0.18, confidence=0.9)
    base.update(kw)
    return S.Scale(**base)


def test_plus_minus_grows_away_from_the_anchors():
    sc = _scale()
    assert sc.plus_minus_pt_at(400.0) < sc.plus_minus_pt_at(900.0)


def test_a_reading_carries_value_plus_minus_and_confidence():
    sc = _scale()
    rd = sc.reading(400.0, snap_pt=0.3, snap_confidence=0.8)
    assert rd.value == pytest.approx(4.0)
    assert rd.plus_minus == pytest.approx(0.02 * rd.plus_minus_pt)
    assert rd.confidence == pytest.approx(0.8)
    assert rd.contains(4.0 + 0.9 * rd.plus_minus)
    assert not rd.contains(4.0 + 1.1 * rd.plus_minus)


def test_a_log_reading_is_uncertain_multiplicatively():
    sc = _scale(transform="log10", a=-3.0, b=1 / 82.0)
    v1 = sc.value_plus_minus(100.0, 1.0)
    v2 = sc.value_plus_minus(182.0, 1.0)
    assert v2 == pytest.approx(10.0 * v1)


def test_a_pending_scale_reads_nothing_and_says_so():
    sc = _scale(needs_values=True, b=0.0)
    rd = sc.reading(400.0)
    assert rd.value is None and not rd.scale_known
    assert "label values" in rd.warnings[0]


def test_the_deskewed_frame_round_trips():
    for ang in (-1.0, 0.0, 0.37):
        u, v = S.rotate_point(123.4, 567.8, ang)
        x, y = S.unrotate_point(u, v, ang)
        assert (x, y) == pytest.approx((123.4, 567.8))


def test_a_rule_drawn_on_a_skewed_sheet_reads_one_value_along_it():
    ang = 0.6
    k = math.tan(math.radians(ang))
    sc = _scale(angle_deg=ang)
    y0 = 400.0
    vals = [sc.value_at(sc.along(x, y0 + k * x)) for x in (50.0, 300.0, 550.0)]
    assert max(vals) - min(vals) < 1e-9


def test_confidence_follows_the_ladder():
    assert S.confidence_for("vector", "text", True) == S.CONF_VECTOR
    assert S.confidence_for("pixels", "azure_di", True) == S.CONF_PIXELS_TEXT
    assert S.confidence_for("pixels", "caller", True) == S.CONF_PIXELS_CALLER
    assert S.confidence_for("pixels", "caller", False) == pytest.approx(
        S.CONF_PIXELS_CALLER - S.CONF_UNRESOLVED_PENALTY)
    assert S.confidence_for("model_boxes", "caller", True) == \
        S.CONF_MODEL_BOXES


@pytest.mark.parametrize("text,res", [("1.0", 0.1), ("20", 1.0),
                                      ("0.001", 0.001), ("12+50", 1.0),
                                      ("abc", None)])
def test_printed_resolution(text, res):
    assert S.printed_resolution(text) == res


def test_display_is_never_finer_than_the_print():
    assert S.display_value(3.4321, 0.018, 0.1) == "3.4 +/- 0.018"
    assert S.display_value(3.4321, 0.018, None) == "3.432 +/- 0.018"
    assert S.display_value(3.4321, 0.25, 0.01) == "3.4 +/- 0.3"
    # the uncertainty is rounded up, never down
    assert S.display_value(3.4321, 0.0121, None) == "3.432 +/- 0.013"


def test_a_reading_becomes_a_quantity_with_an_absolute_plus_minus():
    rd = _scale().reading(400.0, snap_pt=0.3)
    q = rd.to_quantity()
    assert q.units == "m" and q.plus_minus == pytest.approx(rd.plus_minus)
    assert q.range()[1] - q.value == pytest.approx(rd.plus_minus)


def test_no_scale_means_page_points():
    rd = _scale(needs_values=True, b=0.0).reading(400.0)
    q = rd.to_quantity()
    assert q.units == "pt" and not q.scale_known


# -- adapters ---------------------------------------------------------------------------

def test_a_loggrid_ruler_is_a_scale():
    from planlens.document.loggrid import Ruler
    r = Ruler(page=3, kind="depth", column_id="p3c2", slope=0.05,
              intercept=-8.5, residual=0.01, step=5.0,
              ticks=((270.0, 5.0), (370.0, 10.0), (470.0, 15.0)), unit="ft",
              confidence=0.95)
    sc = S.scale_from_ruler(r, (40, 170, 570, 650))
    assert sc.id == "p3.depth" and sc.axis == "y" and sc.unit == "ft"
    assert sc.value_at(370.0) == pytest.approx(10.0)
    assert sc.anchor_rule_kind == "centred_assumed"
    assert len(sc.anchors) == 3


def test_a_stated_ratio_is_a_distance_scale_at_true_plot_size():
    sc = S.scale_from_stated('SCALE: 1" = 20\'', 0, (0, 0, 1224, 792))
    assert sc is not None and sc.unit == "ft" and sc.axis == "distance"
    assert sc.b == pytest.approx(20.0 / 72.0)
    assert sc.confidence == S.CONF_STATED
    assert any("true size" in w for w in sc.warnings)


def test_a_bare_ratio_is_in_metres_and_says_so():
    sc = S.scale_from_stated("1:100", 0, (0, 0, 100, 100))
    assert sc.unit == "m"
    assert sc.b == pytest.approx(100 * 0.0254 / 72.0)


def test_the_stored_viewport_is_a_scale_and_the_identity_is_not():
    import fitz
    from planlens.document.scale import page_viewports
    from planlens.testing import (build_synthetic_scaled_sheet_pdf,
                                  build_synthetic_uncalibrated_sheet_pdf)
    for builder, expect in ((build_synthetic_scaled_sheet_pdf, True),
                            (build_synthetic_uncalibrated_sheet_pdf, False)):
        gt = builder()
        doc = fitz.open(stream=gt.pdf, filetype="pdf")
        vps, _ = page_viewports(doc, doc[0], 0)
        sc = S.scale_from_viewport(vps[0])
        assert (sc is not None) is expect
        if sc is not None:
            assert sc.confidence == S.CONF_STORED
            assert sc.b == pytest.approx(gt.x_per_point)


def test_two_points_and_a_distance():
    sc = S.scale_from_two_points(0, (100.0, 100.0), (400.0, 500.0), 50.0,
                                 "m", plus_minus_pt=0.5)
    assert sc.b == pytest.approx(50.0 / 500.0)
    assert sc.rel_uncertainty == pytest.approx(math.sqrt(2) * 0.5 / 500.0)


def test_quantity_absolute_plus_minus_survives_conversion():
    q = Quantity(10.0, "ft", plus_minus=0.5)
    m = q.to("m")
    assert m.plus_minus == pytest.approx(0.5 * 0.3048)
    assert q.to_dict()["plus_minus"] == 0.5
    both = Quantity(10.0, "ft", rel_uncertainty=0.03, plus_minus=0.4)
    assert both.half_width == pytest.approx(0.5)
    assert Quantity(10.0, "pt").plus_minus is None
