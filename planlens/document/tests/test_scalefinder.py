"""``find_scales`` on every kind of page: logs, plots, plans, profiles, charts.

Each fixture states its scale; the finder must recover it, say how it tied
the labels to the drawing, ask for label values where a scan has no text,
refuse a page not drawn to scale, and compare a plan's scales rather than
choose one silently.
"""

import pytest

from planlens.document.scalefinder import find_scales, parse_label_value
from planlens.testing.visual_scale_fixtures import (
    LogVariant, PlanVariant, PlotVariant, build_chart_family, build_log,
    build_pit_sketch, build_plan, build_plot, build_profile,
)


def _with_values(fx):
    doc = fx.open()
    ps = find_scales(doc, 0)
    pend = ps.needing_values()
    if pend:
        ps = find_scales(doc, 0, values={s.id: fx.values_for(s.label_boxes)
                                         for s in pend})
    return doc, ps


@pytest.mark.parametrize("label,expect", [
    ("1.0", (1.0, "number")), ("10", (10.0, "number")),
    ("-2.5", (-2.5, "number")), ("1,250", (1250.0, "number")),
    ("12+50", (1250.0, "station")), ("STA 3+00", (300.0, "station")),
    ("N 2,100", (2100.0, "northing")), ("E 1,000", (1000.0, "easting")),
    ("13-", (13.0, "number")), ("B-1", None), ("SAND", None)])
def test_printed_labels_are_read_as_values(label, expect):
    assert parse_label_value(label) == expect


# -- logs -------------------------------------------------------------------------------

def test_a_scan_with_no_text_asks_for_its_label_values():
    fx = build_log(LogVariant("t_nv", skew_deg=0.4, seed=21))
    ps = find_scales(fx.open(), 0)
    pend = ps.needing_values()
    assert len(pend) == 1
    sc = pend[0]
    assert sc.id == "p0.depth" and not sc.usable
    assert len(sc.label_boxes) == len(fx.labels)
    # in run order, top to bottom
    ys = [(b[1] + b[3]) / 2.0 for b in sc.label_boxes]
    assert ys == sorted(ys)
    assert any("pass values" in w for w in sc.warnings)


@pytest.mark.parametrize("variant,rule", [
    (LogVariant("t_base", skew_deg=0.3, seed=22), "frames"),
    (LogVariant("t_ticks", ticks=True, frame_labelled=False, skew_deg=-0.5,
                seed=23), "ticks"),
    (LogVariant("t_unframed", frame_labelled=False, skew_deg=0.2, seed=24),
     "centred_assumed"),
    (LogVariant("t_right", ruler="right", label_anchor="top", skew_deg=0.9,
                seed=25), "frames"),
])
def test_a_scanned_ruler_is_fitted_and_tied_to_the_drawing(variant, rule):
    fx = build_log(variant)
    _doc, ps = _with_values(fx)
    sc = ps.get("p0.depth")
    assert sc is not None and sc.usable
    assert sc.anchor_rule_kind == rule
    assert sc.b == pytest.approx(fx.scales["depth"]["per_point"], rel=2e-3)
    assert sc.angle_deg == pytest.approx(variant.skew_deg, abs=0.02)
    if rule == "centred_assumed":
        assert sc.confidence <= 0.76
        assert any("could not be checked" in w for w in sc.warnings)
    else:
        assert sc.confidence == pytest.approx(0.85)


def test_the_depth_column_is_found_from_its_labels_not_its_place():
    # sample numbers in their own column step evenly too; the ruler is the
    # column with the longer run that agrees with the frame lines
    fx = build_log(LogVariant("t_samples", samples_regular=True,
                              ruler="inside", seed=26))
    ps = find_scales(fx.open(), 0)
    sc = ps.needing_values()[0]
    col = fx.scales["depth"]["column"]
    for b in sc.label_boxes:
        assert col[0] - 2 <= (b[0] + b[2]) / 2.0 <= col[2] + 2


def test_ocr_text_gives_the_values_and_the_pixels_the_positions():
    fx = build_log(LogVariant("t_ocr", text_source="ocr", skew_deg=0.5,
                              seed=27))
    ps = find_scales(fx.open(), 0)
    assert not ps.needing_values()
    sc = ps.get("p0.depth")
    assert sc.usable and sc.confidence == pytest.approx(0.90)
    assert "pixels" in sc.provenance["positions_from"]
    assert sc.b == pytest.approx(fx.scales["depth"]["per_point"], rel=2e-3)


def test_a_vector_ruler_snaps_to_its_ticks():
    fx = build_log(LogVariant("t_vec", raster=False, ticks=True,
                              label_anchor="top"))
    ps = find_scales(fx.open(), 0)
    sc = ps.get("p0.depth")
    assert sc.anchor_rule_kind == "ticks"
    assert sc.confidence == pytest.approx(0.95)
    assert sc.residual_pt < 0.05


@pytest.mark.parametrize("raster", [True, False])
def test_a_sketch_not_drawn_to_scale_is_refused(raster):
    fx = build_pit_sketch(raster=raster)
    ps = find_scales(fx.open(), 0)
    assert not [s for s in ps.scales() if s.usable and s.axis == "y"]


def test_one_misread_label_is_dropped_and_two_are_refused():
    fx = build_log(LogVariant("t_misread", skew_deg=0.2, seed=28))
    doc = fx.open()
    sc = find_scales(doc, 0).needing_values()[0]
    good = fx.values_for(sc.label_boxes)
    one = list(good)
    one[4] = one[4] + 2.0
    s1 = find_scales(doc, 0, values={sc.id: one}).get(sc.id)
    assert s1.usable and s1.provenance["dropped"]
    assert any("broke the even run" in w for w in s1.warnings)
    two = list(good)
    two[2] += 3.0
    two[7] -= 2.5
    s2 = find_scales(doc, 0, values={sc.id: two}).get(sc.id)
    assert not s2.usable
    assert any("refused" in w for w in s2.warnings)


def test_a_plain_list_of_values_goes_to_the_one_scale_waiting():
    fx = build_log(LogVariant("t_list", seed=29))
    doc = fx.open()
    sc = find_scales(doc, 0).needing_values()[0]
    ps = find_scales(doc, 0, values=fx.values_for(sc.label_boxes))
    assert ps.get(sc.id).usable


def test_the_result_is_cached_per_document_and_values():
    fx = build_log(LogVariant("t_cache", raster=False))
    doc = fx.open()
    assert find_scales(doc, 0) is find_scales(doc, 0)


# -- plots, profiles, charts -------------------------------------------------------------

def test_a_grading_plot_has_a_log_x_axis_and_a_linear_y_axis():
    fx = build_plot(PlotVariant("t_grading_v", "grading"))
    ps = find_scales(fx.open(), 0)
    x, y = ps.get("p0.plot1.x"), ps.get("p0.plot1.y")
    assert x.transform == "log10" and y.transform == "linear"
    assert x.b == pytest.approx(fx.scales["plot.x"]["per_point"], rel=1e-4)
    assert y.b == pytest.approx(fx.scales["plot.y"]["per_point"], rel=1e-4)
    assert x.anchor_rule_kind == "gridlines"
    assert x.quantity == "particle size" and x.unit == "mm"


def test_a_gridline_broken_by_a_title_does_not_shift_the_values():
    fx = build_plot(PlotVariant("t_broken_v", "broken"))
    ps = find_scales(fx.open(), 0)
    y = ps.get("p0.plot1.y")
    assert y.b == pytest.approx(fx.scales["plot.y"]["per_point"], rel=1e-4)
    assert y.residual_pt < 0.05


def test_a_raster_grading_plot_needs_values_and_then_fits():
    fx = build_plot(PlotVariant("t_grading_s", "grading", raster=True,
                                skew_deg=0.3, seed=43))
    doc = fx.open()
    first = find_scales(doc, 0)
    assert {s.id for s in first.needing_values()} >= {"p0.plot1.x",
                                                      "p0.plot1.y"}
    _doc, ps = _with_values(fx)
    x = ps.get("p0.plot1.x")
    assert x.usable and x.transform == "log10"
    assert x.b == pytest.approx(fx.scales["plot.x"]["per_point"], rel=2e-3)


def test_a_dotted_grid_on_a_scan_is_found_by_projection():
    fx = build_plot(PlotVariant("t_dotted_s", "dotted", raster=True,
                                skew_deg=0.2, seed=44))
    _doc, ps = _with_values(fx)
    x = ps.get("p0.plot1.x")
    assert x is not None and x.usable
    assert x.b == pytest.approx(fx.scales["plot.x"]["per_point"], rel=2e-3)


@pytest.mark.parametrize("raster", [False, True])
def test_a_plot_drawn_with_ticks_only(raster):
    fx = build_plot(PlotVariant("t_ticks", "linear_ticks", raster=raster,
                                skew_deg=-0.4 if raster else 0.0, seed=45))
    _doc, ps = _with_values(fx)
    for axis in ("x", "y"):
        sc = ps.get(f"p0.plot1.{axis}")
        assert sc is not None and sc.usable
        assert sc.b == pytest.approx(fx.scales[f"plot.{axis}"]["per_point"],
                                     rel=3e-3)


def test_two_charts_on_one_page_are_two_frames():
    fx = build_plot(PlotVariant("t_two", "two_charts"))
    ps = find_scales(fx.open(), 0)
    plots = [f for f in ps.frames if f.kind == "plot"]
    assert len(plots) == 2
    transforms = sorted(f.scales["x"].transform for f in plots)
    assert transforms == ["linear", "log10"]


def test_a_depth_axis_increasing_downward():
    fx = build_plot(PlotVariant("t_rev", "reversed_y"))
    ps = find_scales(fx.open(), 0)
    assert ps.get("p0.plot1.y").b > 0


def test_a_profile_reads_stations_and_elevations_separately():
    fx = build_profile(raster=False)
    ps = find_scales(fx.open(), 0)
    frame = next(f for f in ps.frames if f.kind == "profile")
    x, y = frame.scales["x"], frame.scales["y"]
    assert x.quantity == "station"
    assert x.b == pytest.approx(fx.scales["profile.x"]["per_point"], rel=1e-4)
    assert y.b == pytest.approx(fx.scales["profile.y"]["per_point"], rel=1e-4)
    # vertical exaggeration: the two scales differ five-fold
    assert abs(x.b / y.b) == pytest.approx(5.0, rel=1e-3)


def test_a_log_log_chart_on_a_scan():
    fx = build_chart_family("loglog", raster=True, skew_deg=-0.3, seed=72)
    _doc, ps = _with_values(fx)
    assert ps.get("p0.plot1.x").transform == "log10"
    assert ps.get("p0.plot1.y").transform == "log10"


# -- plans -----------------------------------------------------------------------------

def _plan(v):
    _doc, ps = _with_values(build_plan(v))
    return next(f for f in ps.frames if f.kind == "plan"), ps


def test_a_stated_note_and_a_bar_that_agree():
    frame, _ps = _plan(PlanVariant("t_agree"))
    assert frame.provenance["primary"] == "p0.plan.bar"
    stated = frame.scales["p0.plan.stated"]
    assert stated.confidence == pytest.approx(0.90)
    assert any("agree" in w for w in frame.warnings)


def test_a_replotted_sheet_keeps_its_bar_and_flags_its_note():
    frame, _ps = _plan(PlanVariant("t_replot", replot=0.5))
    bar = frame.scales["p0.plan.bar"]
    assert frame.provenance["primary"] == "p0.plan.bar"
    assert bar.b == pytest.approx(2 * 20.0 / 72.0, rel=1e-3)
    assert any("disagrees" in w and "50.0 %" in w for w in frame.warnings)


def test_the_stored_scale_wins_where_there_is_one():
    frame, _ps = _plan(PlanVariant("t_stored", bar=None, note=False,
                                   stored=True))
    assert frame.provenance["primary"] == "p0.plan.stored"


def test_a_scale_bar_on_a_scan():
    frame, _ps = _plan(PlanVariant("t_scan_bar", raster=True, skew_deg=0.3))
    bar = frame.scales["p0.plan.bar"]
    assert bar.usable and bar.axis == "distance"
    assert bar.b == pytest.approx(20.0 / 72.0, rel=6e-3)


def test_grid_coordinates_are_scales_of_their_own():
    _frame, ps = _plan(PlanVariant("t_ne", ne_grid=True))
    assert ps.get("p0.easting").b == pytest.approx(20.0 / 72.0, rel=1e-4)
    assert ps.get("p0.northing").b == pytest.approx(-20.0 / 72.0, rel=1e-4)
