"""``measure``: snap to the drawn thing near a rough box, read it through the scale.

The rules under test are the design's: one candidate is taken, several are
listed and none chosen, none means the box itself is read with its location
error; a box that may be far off is never snapped; no scale means points.
"""

import math

import pytest

from planlens.document.measuring import LARGE_PAD_PT, measure
from planlens.document.scalefinder import find_scales
from planlens.testing.visual_scale_fixtures import (
    LogVariant, PlanVariant, PlotVariant, build_chart_family, build_log,
    build_plan, build_plot, build_profile,
)


def _values(fx, doc):
    ps = find_scales(doc, 0)
    return {s.id: fx.values_for(s.label_boxes) for s in ps.needing_values()}


def _num(v):
    return next(x for k, x in v.items()
                if k not in ("plus_minus", "unit", "confidence", "display",
                             "plus_minus_pt", "warnings"))


@pytest.fixture(scope="module")
def scan_log():
    fx = build_log(LogVariant("m_scan", skew_deg=0.5, dashed=True, seed=51))
    doc = fx.open()
    return fx, doc, _values(fx, doc)


def test_a_stratum_line_reads_its_depth_with_its_uncertainty(scan_log):
    fx, doc, vals = scan_log
    for rd in fx.readings:
        res = measure(doc, 0, where=list(rd.box_pt), kind="line", pad=4,
                      values=vals)
        v = res["value"]
        assert abs(_num(v) - rd.value) <= v["plus_minus"], rd.value
        assert v["plus_minus"] < 0.03
        assert res["snapped_to"]["moved_pt"] < 1.5
        assert res["alternatives"] == []
        assert res["scale"]["id"] == "p0.depth"
        if rd.tag == "dashed":
            assert res["snapped_to"]["dashed"]


def test_every_line_in_the_box(scan_log):
    fx, doc, vals = scan_log
    col = fx.scales["depth"]["description"]
    res = measure(doc, 0, where=list(col), kind="lines", pad=0, values=vals)
    depths = sorted(_num(c["value"]) for c in res["lines"]
                    if c.get("value"))
    for rd in fx.readings:
        assert min(abs(d - rd.value) for d in depths) < 0.03


def test_two_lines_in_the_window_are_listed_and_none_chosen(scan_log):
    fx, doc, vals = scan_log
    a, b = fx.readings[0], fx.readings[1]
    box = [a.box_pt[0], a.box_pt[1], a.box_pt[2], b.box_pt[3]]
    res = measure(doc, 0, where=box, kind="line", pad=4, values=vals)
    assert res.get("ambiguous") and res["value"] is None
    assert len(res["alternatives"]) >= 2


def test_a_box_that_may_be_far_off_is_never_snapped(scan_log):
    fx, doc, vals = scan_log
    rd = fx.readings[0]
    res = measure(doc, 0, where=list(rd.box_pt), kind="line",
                  pad=LARGE_PAD_PT + 1, values=vals)
    assert res["value"] is None and res.get("ambiguous")
    assert any("window is large" in w for w in res["warnings"])


def test_nothing_to_snap_to_reads_the_box_with_its_error(scan_log):
    fx, doc, vals = scan_log
    rd = fx.readings[0]
    x, y = rd.at_pt
    box = [x - 20, y + 15, x + 20, y + 16]      # between two contacts
    res = measure(doc, 0, where=box, kind="line", pad=3, values=vals)
    assert res.get("unsnapped")
    assert res["value"]["confidence"] <= 0.6
    assert res["value"]["plus_minus_pt"] >= 3.0


def test_a_scan_waiting_for_label_values_says_so(scan_log):
    fx, doc, _vals = scan_log
    rd = fx.readings[0]
    res = measure(doc, 0, where=list(rd.box_pt), kind="line", pad=4)
    assert res["value"] is None
    assert res["needs_values"]["scale"] == "p0.depth"
    assert len(res["needs_values"]["labels"]) == len(fx.labels)


def test_with_no_box_the_scales_are_listed(scan_log):
    _fx, doc, _vals = scan_log
    res = measure(doc, 0)
    assert res["scales"]["frames"][0]["id"] == "p0.depth"
    assert res["scales"]["needs_values"][0]["scale"] == "p0.depth"


def test_a_marker_reads_both_axes():
    fx = build_plot(PlotVariant("m_grading", "grading", raster=True,
                                skew_deg=0.3, seed=52))
    doc = fx.open()
    vals = _values(fx, doc)
    checked = 0
    for rd in fx.readings:
        if rd.kind != "point":
            continue
        res = measure(doc, 0, where=list(rd.box_pt), kind="point", pad=2,
                      values=vals)
        if res.get("ambiguous") or res.get("unsnapped"):
            continue
        x = res["value"]["p0.plot1.x"]
        y = res["value"]["p0.plot1.y"]
        assert abs(_num(x) - rd.values["plot.x"]) <= x["plus_minus"]
        assert abs(_num(y) - rd.values["plot.y"]) <= y["plus_minus"]
        checked += 1
    assert checked >= 10


@pytest.mark.parametrize("builder", [
    lambda: build_plot(PlotVariant("m_rev", "reversed_y", raster=True,
                                   skew_deg=-0.6, seed=53)),
    lambda: build_chart_family("semilog", raster=True, seed=54),
    lambda: build_plot(PlotVariant("m_logy_v", "log_y")),
])
def test_a_curve_is_read_where_it_crosses_an_axis_value(builder):
    fx = builder()
    doc = fx.open()
    vals = _values(fx, doc)
    read = 0
    for rd in fx.readings:
        if rd.kind != "curve":
            continue
        k, v = next(iter(rd.at.items()))
        res = measure(doc, 0, where=list(rd.box_pt), kind="curve",
                      at={k.split(".")[-1]: v}, pad=3, values=vals)
        if res.get("ambiguous"):
            continue
        assert not res.get("unsnapped"), rd.at
        val = res["value"]
        assert abs(_num(val) - rd.value) <= val["plus_minus"], rd.at
        read += 1
    assert read >= 3


def test_a_curve_needs_its_axis_value():
    fx = build_plot(PlotVariant("m_noat", "grading"))
    with pytest.raises(ValueError):
        measure(fx.open(), 0, where=[100, 100, 110, 110], kind="curve")


def test_a_plan_distance_through_the_bar():
    fx = build_plan(PlanVariant("m_plan", replot=0.5))
    doc = fx.open()
    for rd in fx.readings:
        res = measure(doc, 0, where=list(rd.box_pt), kind="distance",
                      to=list(rd.to_box_pt), pad=2)
        v = res["value"]
        assert v["unit"] == "ft"
        assert abs(v["distance"] - rd.value) <= v["plus_minus"]
        assert res["scale"]["id"] == "p0.plan.bar"
        assert any("disagrees" in w for w in res.get("reconciled", []))


def test_a_point_on_a_plan_names_the_scale_and_the_distance_call():
    """Live smoke 2a (2026-10-09, F17): on a plan whose bar was found at
    0.95, ``measure(kind="point")`` said ``scales: []`` and
    ``scale_known: false``, and the agent worked the distance out by hand.
    A plan's scale measures distances: a point has no value of its own, but
    the scale IS known, and the result says how to measure between two."""
    fx = build_plan(PlanVariant("m_plan_point", replot=0.5))
    doc = fx.open()
    rd = fx.readings[0]
    for pad, where in ((2, rd.box_pt),
                       # nothing to snap to: the box itself is read
                       (2, (rd.box_pt[0] + 60, rd.box_pt[1] + 60,
                            rd.box_pt[2] + 60, rd.box_pt[3] + 60))):
        res = measure(doc, 0, where=list(where), kind="point", pad=pad)
        assert res["value"] is None
        assert res["scale_known"] is True, res
        assert [s["id"] for s in res["scales"]] == ["p0.plan.bar"]
        assert "kind='distance'" in res["note"] and "to=" in res["note"]
        assert len(res["position_pt"]) == 2
    # and the distance it points to reads through that scale
    dist = measure(doc, 0, where=list(rd.box_pt), kind="distance",
                   to=list(rd.to_box_pt), pad=2)
    assert dist["scale"]["id"] == "p0.plan.bar"
    assert abs(dist["value"]["distance"] - rd.value) <= \
        dist["value"]["plus_minus"]


def test_a_point_off_every_scale_still_says_points():
    """No scale at all: a point is in page points and says the scale is not
    known (the plan note is for a plan only)."""
    import fitz
    d = fitz.open()
    p = d.new_page()
    p.draw_circle((300, 300), 2, fill=(0, 0, 0))
    from planlens.document import Document
    doc = Document(content=d.tobytes())
    res = measure(doc, 0, where=[296, 296, 304, 304], kind="point", pad=2)
    assert res["value"] is None and res.get("scale_known") is False
    assert "distance" not in res.get("note", "")


def test_grid_coordinates_of_a_symbol():
    fx = build_plan(PlanVariant("m_ne", ne_grid=True))
    doc = fx.open()
    pts = [r for r in fx.readings if r.kind == "point"]
    assert pts
    for rd in pts:
        res = measure(doc, 0, where=list(rd.box_pt), kind="point", pad=2)
        e = res["value"]["p0.easting"]
        n = res["value"]["p0.northing"]
        assert abs(_num(e) - rd.values["easting"]) <= e["plus_minus"]
        assert abs(_num(n) - rd.values["northing"]) <= n["plus_minus"]


def test_a_short_contact_on_a_narrow_boring_stick():
    fx = build_profile(raster=False)
    doc = fx.open()
    for rd in [r for r in fx.readings if r.tag == "boring_contact"]:
        res = measure(doc, 0, where=list(rd.box_pt), kind="line", pad=2)
        v = res["value"]
        assert abs(_num(v) - rd.value) <= v["plus_minus"]


def test_no_scale_on_the_page_means_points():
    import fitz
    d = fitz.open()
    p = d.new_page()
    p.draw_line((100, 300), (400, 300))
    from planlens.document import Document
    doc = Document(content=d.tobytes())
    res = measure(doc, 0, where=[200, 298, 300, 302], kind="line", pad=3)
    assert res.get("scale_known") is False
    assert res["value"] is None
    assert "page points" in res.get("note", "")


def test_a_text_line_is_its_own_box():
    fx = build_log(LogVariant("m_text", raster=False))
    doc = fx.open()
    lab = fx.labels[2]
    res = measure(doc, 0, where=list(lab.box), kind="text", pad=1)
    assert res["text"] == lab.text
    assert abs(_num(res["value"]) - lab.value) < 0.5


def test_the_top_edge_of_a_mark():
    fx = build_plan(PlanVariant("m_edge"))
    doc = fx.open()
    rd = fx.readings[0]
    res = measure(doc, 0, where=list(rd.box_pt), kind="edge", side="top",
                  pad=2)
    assert res["snapped_to"]["kind"] == "edge"
    assert res["snapped_to"]["at_pt"][1] < rd.at_pt[1]


def test_label_values_given_as_printed_keep_the_print_resolution():
    """Values read off a scan's label crops as PRINTED ("4.0") carry the
    print's resolution: the reading is never written finer than it."""
    fx = build_log(LogVariant("m_printed", skew_deg=0.4, seed=57))
    doc = fx.open()
    pend = find_scales(doc, 0).needing_values()[0]
    texts = []
    for v in fx.values_for(pend.label_boxes):
        texts.append(next(lb.text for lb in fx.labels if lb.value == v))
    assert all("." in t for t in texts)            # printed as "4.0"
    rd = fx.readings[1]
    res = measure(doc, 0, where=list(rd.box_pt), kind="line", pad=4,
                  values={pend.id: texts})
    v = res["value"]
    assert abs(_num(v) - rd.value) <= v["plus_minus"]
    shown = v["display"].split(" ")[0]
    assert len(shown.split(".")[1]) <= 1           # never finer than 0.1


def test_a_box_restricts_the_frame_search_and_reads_the_same():
    """``near``: only the frames holding the box are looked for, and the
    reading is the one a whole-page analysis gives."""
    fx = build_plot(PlotVariant("m_near", "dotted", raster=True,
                                skew_deg=0.2, seed=58))
    whole = fx.open()
    vals = _values(fx, whole)
    find_scales(whole, 0, values=vals)             # the whole page, cached
    fresh = fx.open()
    compared = 0
    for rd in fx.readings:
        if rd.kind != "point":
            continue
        a = measure(whole, 0, where=list(rd.box_pt), kind="point", pad=2,
                    values=vals)
        b = measure(fresh, 0, where=list(rd.box_pt), kind="point", pad=2,
                    values=vals)
        assert a.get("value") == b.get("value")
        compared += 1
    assert compared >= 3
    near = find_scales(fresh, 0, values=vals,
                       near=list(fx.readings[0].box_pt))
    assert len(near.frames) <= len(find_scales(whole, 0,
                                               values=vals).frames)
