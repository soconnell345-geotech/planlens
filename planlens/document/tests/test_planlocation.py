"""Plan locations (BACKLOG PL-25): symbol centres off the vector drawing, and a
right-handed page-to-local grid that refuses a mirrored control set."""

import json
import math

import pytest

from planlens.document import Document
from planlens.document.planlocation import (
    PlanGridError, check_plan_points, find_plan_symbols, fit_plan_grid,
    grid_from_north)
from planlens.testing import PlanVariant, build_plan
from planlens.testing.plan_location_fixtures import build_plan_location_sheet


@pytest.fixture(scope="module", params=[0, 90])
def sheet(request):
    gt = build_plan_location_sheet(rotation=request.param)
    doc = Document(content=gt.pdf, name="plan.pdf")
    yield gt, doc
    doc.close()


def test_every_symbol_centre_exact_with_its_label_and_type(sheet):
    gt, doc = sheet
    found = find_plan_symbols(doc, 0)
    by = found.by_label()
    for t in gt.symbols:
        s = by[t.label]
        assert math.dist(s.center, t.center) < 0.05, t.label
        assert s.shape == t.shape and s.filled == t.filled, t.label
        assert s.confidence >= 0.7
    # one style per program; the triangle's size is its drawn box
    styles = found.styles()
    assert styles["circle 12.6 pt"] == 5
    assert styles["circle-cross 10.2 pt"] == 2
    assert styles["filled circle 9.5 pt"] == 2
    assert styles["square 9.0 pt"] == 1


def test_decoys(sheet):
    gt, doc = sheet
    found = find_plan_symbols(doc, 0)
    labels = {s.label for s in found.symbols}
    assert "MH" not in labels and "TANK" not in labels
    # the tank (80 pt) is no symbol; the manhole is a symbol with no label
    assert all(s.size <= 36 for s in found.symbols)
    assert sum(1 for s in found.symbols if s.label is None) == 1
    assert [u["text"] for u in found.unmatched_labels] == gt.orphan_labels


def test_label_not_symbol_and_same_point_are_flagged(sheet):
    gt, doc = sheet
    found = find_plan_symbols(doc, 0)
    b1 = found.by_label()["B-1"]
    lb = b1.label_bbox
    on_label = ((lb[0] + lb[2]) / 2, (lb[1] + lb[3]) / 2)
    tp1 = found.by_label()["TP-1"]
    flags = check_plan_points([
        {"name": "B-1", "page": on_label},
        {"name": "TP-1", "page": tp1.center},
        {"name": "EB-1", "page": tp1.center},
    ], found)
    kinds = {(tuple(f["names"]), f["flag"]) for f in flags}
    assert (("B-1",), "on a label, not its symbol") in kinds
    assert (("TP-1", "EB-1"), "same point") in kinds
    assert (("EB-1",), "on another label's symbol") in kinds
    off = next(f for f in flags if f["flag"].startswith("on a label"))
    assert off["off_by_pt"] > 5


def test_existing_plan_fixture_borings():
    fx = build_plan(PlanVariant("grid", ne_grid=True))
    truth = {r.tag.split(".")[0]: r for r in fx.readings if r.kind == "point"}
    with Document(content=fx.pdf, name="plan.pdf") as doc:
        found = find_plan_symbols(doc, 0)
    by = found.by_label()
    assert set(by) == set(truth)
    for name, r in truth.items():
        assert math.dist(by[name].center, r.at_pt) < 0.05
    # and through a grid fitted on three of them, the other two land within
    # a hundredth of a foot
    ctl = [{"name": n, "page": by[n].center,
            "local": [truth[n].values["easting"], truth[n].values["northing"]]}
           for n in ("B-1", "B-2", "B-3")]
    grid = fit_plan_grid(ctl, units="ft")
    assert grid.right_handed and grid.handedness_tested
    for n in ("B-4", "B-5"):
        e, nn = grid.to_local(*by[n].center)
        assert abs(e - truth[n].values["easting"]) < 0.01
        assert abs(nn - truth[n].values["northing"]) < 0.01


def test_grid_from_controls_is_right_handed_and_exact(sheet):
    gt, doc = sheet
    grid = fit_plan_grid(gt.controls, units="m")
    chk = grid.check()
    assert chk["right_handed"] and grid.handedness_tested
    assert abs(chk["north_on_page_deg"] - gt.north_deg) < 1e-6
    assert abs(grid.scale - gt.m_per_pt) < 1e-9
    assert grid.rms < 1e-6 and grid.mirror_rms > 10
    by = find_plan_symbols(doc, 0).by_label()
    for t in gt.symbols:
        e, n = grid.to_local(*by[t.label].center)
        assert abs(e - t.easting) < 0.02 and abs(n - t.northing) < 0.02
        x, y = grid.to_page(t.easting, t.northing)
        assert math.dist((x, y), t.center) < 0.05


def test_mirrored_controls_are_refused_or_warned():
    gt = build_plan_location_sheet()
    swapped = [{"name": c["name"], "page": c["page"],
                "local": [c["local"][1], c["local"][0]]}
               for c in gt.controls]
    with pytest.raises(PlanGridError) as err:
        fit_plan_grid(swapped)
    assert "MIRRORED" in str(err.value)
    assert err.value.details["mirror_rms"] < 1e-6
    assert err.value.details["right_handed_rms"] > 10
    grid = fit_plan_grid(swapped, on_mirror="warn")
    assert not grid.right_handed and grid.rms < 1e-6
    assert any("MIRRORED" in w for w in grid.warnings)
    assert not grid.check()["right_handed"]


def test_two_controls_cannot_test_handedness():
    gt = build_plan_location_sheet()
    grid = fit_plan_grid(gt.controls[:2])
    assert grid.right_handed and not grid.handedness_tested
    assert any("not tested" in w for w in grid.warnings)
    assert grid.defaults_used == {"on_mirror": "refuse"}


def test_stated_scale_checked():
    gt = build_plan_location_sheet()
    grid = fit_plan_grid(gt.controls, scale=gt.m_per_pt * 1.1)
    assert any("differs from the stated" in w for w in grid.warnings)


def test_grid_from_north_arrow_matches_the_fit():
    gt = build_plan_location_sheet()
    grid = grid_from_north(gt.north_from, gt.north_to, gt.m_per_pt,
                           gt.origin_page, gt.origin_local)
    assert grid.check()["right_handed"]
    for t in gt.symbols:
        e, n = grid.to_local(*t.center)
        assert abs(e - t.easting) < 1e-6 and abs(n - t.northing) < 1e-6


def test_the_field_sessions_rotation_is_the_mirror():
    # The hand-written rotation from the trace review: north (-0.888, 0.460)
    # to the lower left, "east" (0.460, 0.888) to the lower right -- 90 deg
    # COUNTER-clockwise from north as viewed, so every easting has the wrong
    # sign. Fed back as controls, it is refused.
    gt = build_plan_location_sheet()
    phi = math.radians(gt.north_deg)
    n_hat = (math.sin(phi), -math.cos(phi))
    e_hat = (n_hat[1], -n_hat[0])          # the wrong "east"
    ox, oy = gt.origin_page
    s = gt.m_per_pt
    ctl = []
    for t in gt.symbols[:4]:
        dx, dy = t.center[0] - ox, t.center[1] - oy
        ctl.append({"name": t.label, "page": t.center,
                    "local": [s * (dx * e_hat[0] + dy * e_hat[1]),
                              s * (dx * n_hat[0] + dy * n_hat[1])]})
    with pytest.raises(PlanGridError):
        fit_plan_grid(ctl)


def test_tools_end_to_end():
    from planlens.tools import ReviewToolkit
    gt = build_plan_location_sheet()
    tk = ReviewToolkit(resolve_source=lambda s: gt.pdf)
    try:
        h = tk.call("open_document", {"source": "plan.pdf"})["handle"]
        res = json.loads(tk.call_json("plan_symbols", {"handle": h, "page": 0}))
        assert res["pdf_page"] == 1
        labels = {s.get("label") for s in res["symbols"]}
        assert {t.label for t in gt.symbols} <= labels
        bad = tk.call("plan_symbols", {"handle": h, "page": 3})
        assert "error" in bad
        pts = [{"name": s["label"], "page": s["center"]}
               for s in res["symbols"] if s.get("label")]
        out = tk.call("plan_grid", {"controls": gt.controls, "points": pts,
                                    "handle": h, "page": 0, "units": "m"})
        assert out["handedness"] == "right" and out["check"]["right_handed"]
        assert "point_flags" not in out
        swapped = [{"page": c["page"], "local": c["local"][::-1]}
                   for c in gt.controls]
        refused = tk.call("plan_grid", {"controls": swapped})
        assert "MIRRORED" in refused["error"]
        assert refused["right_handed_rms"] > 10
        north = list(gt.north_from) + list(gt.north_to)
        out = tk.call("plan_grid", {
            "north": north, "scale": gt.m_per_pt,
            "origin": {"page": list(gt.origin_page),
                       "local": list(gt.origin_local)}})
        assert out["check"]["right_handed"]
        assert "error" in tk.call("plan_grid", {})
    finally:
        tk.close()


@pytest.mark.parametrize("max_chars", [1000, 2500])
def test_tool_results_fit_the_limit(max_chars):
    from planlens.tools import ReviewToolkit
    gt = build_plan_location_sheet()
    tk = ReviewToolkit(resolve_source=lambda s: gt.pdf, max_chars=max_chars)
    try:
        h = tk.call("open_document", {"source": "plan.pdf"})["handle"]
        for name, args in (("plan_symbols", {"handle": h, "page": 0}),
                           ("plan_grid", {"controls": gt.controls})):
            out = json.loads(tk.call_json(name, args))
            assert "error" not in out, out
        out = json.loads(tk.call_json("plan_symbols",
                                      {"handle": h, "page": 0}))
        if len(out["symbols"]) < 12:
            assert "truncated" in out
    finally:
        tk.close()


def _busy_sheet(rotation=0):
    """Five labelled borings among what a real site plan also draws: 40
    filled triangles (arrowheads) in 40 sizes and 4,000 short segments."""
    import pymupdf
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    for k in range(5):
        x, y = 150 + 200 * k, 200
        page.draw_circle((x, y), 5.0, color=(0, 0, 0), width=0.5)
        page.insert_text((x + 9, y - 6), f"B-{k + 1}", fontsize=7)
    for k in range(40):
        s = 3.0 + 0.23 * k
        x, y = 60 + 28 * k, 500
        page.draw_polyline([(x, y), (x + s, y), (x + s / 2, y - s), (x, y)],
                           color=(0, 0, 0), fill=(0, 0, 0))
    shape = page.new_shape()
    for k in range(4000):
        x, y = 40 + (k % 100) * 11.5, 560 + (k // 100) * 5.5
        shape.draw_line((x, y), (x + 8, y + 1))
    shape.finish(color=(0.5, 0.5, 0.5), width=0.2)
    shape.commit()
    if rotation:
        page.set_rotation(rotation)
    pdf = doc.tobytes()
    doc.close()
    return pdf


def test_a_busy_sheet_is_not_compared_all_against_all(monkeypatch):
    # The field session's site plan (257,000 paths, 331,000 segments) took
    # 473 s when every symbol was tested against every segment; 8 s with
    # the segments bucketed round each symbol.
    from planlens.document import planlocation as pl
    calls = []
    real = pl._through_centre

    def counted(seg, c, r):
        calls.append(1)
        return real(seg, c, r)
    monkeypatch.setattr(pl, "_through_centre", counted)
    with Document(content=_busy_sheet(), name="busy.pdf") as doc:
        found = find_plan_symbols(doc, 0)
    assert {f"B-{k}" for k in range(1, 6)} <= set(found.by_label())
    assert len(found.symbols) == 45
    assert len(calls) < 2000        # all against all: 45 x 4,000


@pytest.mark.parametrize("rotation", [0, 90])
def test_many_styles_are_cut_to_fit_and_keep_every_shape(rotation):
    from planlens.tools import ReviewToolkit
    pdf = _busy_sheet(rotation)
    tk = ReviewToolkit(resolve_source=lambda s: pdf)
    try:
        h = tk.call("open_document", {"source": "busy.pdf"})["handle"]
        raw = tk.call_json("plan_symbols", {"handle": h, "page": 0})
        out = json.loads(raw)
        assert "error" not in out and len(raw) <= tk.max_chars
        assert out["styles"]["circle 10.0 pt"] == 5
        assert len(out["styles"]) <= 24
        assert "filled triangle 40" in out["styles_cut"]
        assert {s.get("label") for s in out["symbols"]} >= {
            f"B-{k}" for k in range(1, 6)}
    finally:
        tk.close()


def test_plan_grid_cuts_a_long_point_list_to_fit():
    from planlens.tools import ReviewToolkit
    gt = build_plan_location_sheet()
    tk = ReviewToolkit(resolve_source=lambda s: gt.pdf)
    try:
        pts = [{"name": f"B-{k}", "page": [100 + k, 200 + k]}
               for k in range(200)]
        raw = tk.call_json("plan_grid", {"controls": gt.controls,
                                         "points": pts})
        out = json.loads(raw)
        assert "error" not in out and len(raw) <= tk.max_chars
        assert 0 < len(out["points"]) < 200
        assert f"{200 - len(out['points'])} more points" in out["truncated"]
        h = tk.call("open_document", {"source": "plan.pdf"})["handle"]
        bad = tk.call("plan_grid", {"controls": gt.controls, "points": pts,
                                    "handle": h, "page": 5})
        assert "out of range" in bad["error"]
    finally:
        tk.close()
