"""Page pixels as geometry: skew, ruled lines, blobs, markers.

On synthetic scans whose truth is stated by the fixture (turned, blurred,
noised, JPEG'd, some stored /Rotate 270): the skew to a hundredth of a
degree, every stratum line to half a pixel, dashed contacts labelled dashed,
the depth labels as blobs, plotted markers to under half a point.
"""

import math

import pytest

from planlens.document import raster as R
from planlens.testing.visual_scale_fixtures import (
    LogVariant, PlotVariant, build_log, build_plot,
)

#: Half a pixel at the analysis resolution (200 dpi), in points.
HALF_PX = 0.5 * 72.0 / R.DEFAULT_DPI


def _lines(fx):
    doc = fx.open()
    ras = R.render(doc._doc[0], R.DEFAULT_DPI)
    lines, slope = R.find_lines(ras)
    return doc, ras, lines, slope


@pytest.fixture(scope="module")
def skewed_logs():
    out = []
    for v in (LogVariant("t_skew_pos", skew_deg=0.6, seed=3),
              LogVariant("t_skew_neg_rot", skew_deg=-0.8, rotate270=True,
                         ruler="inside", seed=4),
              LogVariant("t_noisy_dashed", skew_deg=1.0, dpi=150, jpeg=60,
                         noise=9.0, dashed=True, seed=5)):
        fx = build_log(v)
        out.append((fx,) + _lines(fx))
    return out


def test_the_skew_is_measured_from_the_ink(skewed_logs):
    for fx, _doc, _ras, _lines_, slope in skewed_logs:
        assert math.degrees(math.atan(slope)) == pytest.approx(
            fx.skew_deg, abs=0.01)


def test_every_stratum_line_is_found_to_half_a_pixel(skewed_logs):
    for fx, _doc, _ras, lines, _slope in skewed_logs:
        hs = [L for L in lines if L.orientation == "h"]
        for rd in fx.readings:
            x, y = rd.at_pt
            near = [L for L in hs if L.lo - 1 <= x <= L.hi + 1
                    and abs(L.at(x) - y) < 3.0]
            assert near, (fx.name, rd.value)
            best = min(near, key=lambda L: abs(L.at(x) - y))
            assert abs(best.at(x) - y) <= HALF_PX, (fx.name, rd.value)


def test_a_dashed_contact_comes_back_dashed(skewed_logs):
    fx, _doc, _ras, lines, _slope = skewed_logs[2]
    for rd in fx.readings:
        x, y = rd.at_pt
        best = min((L for L in lines if L.orientation == "h"
                    and L.lo - 1 <= x <= L.hi + 1),
                   key=lambda L: abs(L.at(x) - y))
        assert best.dashed == (rd.tag == "dashed"), rd.value


def test_the_column_rules_are_found(skewed_logs):
    for fx, _doc, _ras, lines, _slope in skewed_logs:
        long_v = [L for L in lines if L.orientation == "v"
                  and L.length > 300 and not L.dashed]
        # eight rules make the seven columns of the form
        xs = sorted({round(L.position) for L in long_v})
        merged = [x for i, x in enumerate(xs) if i == 0 or x - xs[i - 1] > 2]
        assert len(merged) == 8, (fx.name, merged)


def test_a_line_carries_a_half_pixel_floor_on_its_uncertainty(skewed_logs):
    _fx, _doc, ras, lines, _slope = skewed_logs[0]
    for L in lines[:20]:
        assert L.plus_minus_pt(ras.pixel_pt) >= 0.5 * ras.pixel_pt - 1e-9


def test_the_depth_labels_are_blobs(skewed_logs):
    fx, doc, ras, lines, _slope = skewed_logs[0]
    mask = R.erase_lines(ras, lines)
    col = fx.scales["depth"]["column"]
    blobs = R.text_blobs(ras, mask=mask, region=(col[0] + 2, col[1] + 2,
                                                 col[2] - 2, col[3] - 2))
    labels = [b for b in blobs if 3.0 <= b.height <= 12.0]
    # a label can arrive in two pieces ("7" and ".0"); none goes missing
    assert len(fx.labels) <= len(labels) <= len(fx.labels) + 2
    for lab in fx.labels:
        cy = (lab.box[1] + lab.box[3]) / 2.0
        assert min(abs(b.centre[1] - cy) for b in labels) < 0.6


def test_a_crop_finds_the_same_line():
    fx = build_log(LogVariant("t_crop", skew_deg=0.3, seed=7))
    doc = fx.open()
    ras = R.render(doc._doc[0], R.DEFAULT_DPI)
    rd = fx.readings[0]
    x, y = rd.at_pt
    sub = R.crop(ras, (x - 60, y - 8, x + 60, y + 8))
    lines, _ = R.find_lines(sub, orientation="h", skew=math.tan(
        math.radians(fx.skew_deg)), min_len_pt=40)
    assert any(abs(L.at(x) - y) <= HALF_PX for L in lines)


def test_markers_are_found_to_under_half_a_point():
    fx = build_plot(PlotVariant("t_grading", "grading", raster=True,
                                skew_deg=0.3, seed=42))
    doc = fx.open()
    ras = R.render(doc._doc[0], R.DEFAULT_DPI)
    errs = []
    for rd in fx.readings:
        if rd.kind != "point":
            continue
        b = rd.box_pt
        got = R.solid_blobs(ras, region=(b[0] - 3, b[1] - 3, b[2] + 3,
                                         b[3] + 3), core_pt=2.0,
                            min_core_px=2, max_aspect=2.2)
        if got:
            errs.append(min(math.dist(g.centroid, rd.at_pt) for g in got))
    assert len(errs) >= 12
    assert max(errs) < 0.5


def test_picture_pages_are_told_from_vector_pages():
    scan = build_log(LogVariant("t_is_raster", seed=8))
    vec = build_log(LogVariant("t_is_vector", raster=False))
    assert R.is_raster_page(scan.open()._doc[0])
    assert not R.is_raster_page(vec.open()._doc[0])


def test_a_clip_render_knows_where_it_is():
    fx = build_log(LogVariant("t_clip", seed=9))
    page = fx.open()._doc[0]
    ras = R.render(page, 200.0, clip=(100.3, 200.7, 300.1, 260.9))
    x, y = ras.to_page(0.0, 0.0)
    assert x <= 100.3 + 1e-6 and y <= 200.7 + 1e-6
    assert x > 100.3 - 1.0 / ras.z and y > 200.7 - 1.0 / ras.z


def test_findlike_still_reads_ink_through_the_moved_helper():
    from planlens.document import findlike
    fx = build_log(LogVariant("t_ink", raster=False))
    ink = findlike._ink(fx.open()._doc[0], 72.0)
    assert ink.shape[1] == 595 and ink.max() > 200
