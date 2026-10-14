"""``log_grid``'s samples: each sample's interval read off its drawn box.

Many logs print no sample depth as a number: a sample is a box in the sample
columns, drawn against the depth scale. A field session lost most of a
report's SPT records because the grid said nothing about the boxes. Every
answer here is stated by :func:`planlens.testing.visual_scale_fixtures.build_log`
(``sample_boxes``), never computed through the code under test.
"""

from __future__ import annotations

import pytest

from planlens.document.loggrid import (
    Cell, Column, Ruler, Sample, _PageGrid, _build_samples, log_grid,
)
from planlens.testing import build_imperial_log, build_metric_log
from planlens.testing.visual_scale_fixtures import (
    LogVariant, build_log, log_variants,
)


def _read(fx):
    doc = fx.open()
    g = log_grid(doc, [0])
    if g.needs_values:
        g = log_grid(doc, [0], values={0: fx.values_for(
            g.needs_values[0]["labels"])})
    return g


def _check_every_box(fx, g, read):
    assert len(g.samples) == len(fx.samples), [s.to_dict() for s in g.samples]
    for truth, s in zip(fx.samples, g.samples):
        assert s.label == truth["id"]
        assert s.plus_minus is not None
        assert abs(s.top - truth["top"]) <= s.plus_minus, (truth, s)
        assert abs(s.bottom - truth["bottom"]) <= s.plus_minus, (truth, s)
        assert s.read == read
        cells = dict(s.cells)
        assert cells.get("sample_type") == truth["type"]
        if truth["blows"]:
            assert cells.get("blows") == truth["blows"]


@pytest.mark.parametrize("variant", [
    LogVariant("smp_v_centre", raster=False, sample_boxes=True,
               label_anchor="centre"),
    LogVariant("smp_v_ticks", raster=False, sample_boxes=True, ticks=True,
               ruler="inside"),
    LogVariant("smp_v_feet", raster=False, sample_boxes=True, unit="ft",
               step=5.0, n_steps=8, pt_per_step=56.0,
               contacts=(6.2, 17.5, 29.0), label_anchor="centre"),
])
def test_a_vector_log_s_boxes_are_its_sample_intervals(variant):
    fx = build_log(variant)
    g = _read(fx)
    _check_every_box(fx, g, "vector")
    # exact to a fraction of the ruler's step on a drawn page
    assert all(s.plus_minus < 0.02 * variant.step for s in g.samples)
    d = g.samples[1].to_dict()
    assert d["read"] == "drawn lines, vector"
    assert d["id"] == "S-1" and d["cells"]["blows"] == "4 6 8"
    # the refusal: a short box, its own bottom
    s4 = next(s for s in g.samples if s.label == "S-4")
    assert s4.bottom - s4.top == pytest.approx(0.25 * variant.step,
                                               abs=2 * s4.plus_minus)


@pytest.mark.parametrize("variant", [
    LogVariant("smp_ocr", sample_boxes=True, text_source="ocr",
               skew_deg=0.5, seed=71),
    LogVariant("smp_di_rot", sample_boxes=True, text_source="azure_di",
               rotate270=True, skew_deg=-0.4, seed=72),
])
def test_a_scan_s_boxes_are_read_from_its_pixels(variant):
    fx = build_log(variant)
    g = _read(fx)
    _check_every_box(fx, g, "pixels")
    assert g.samples[0].to_dict()["read"] == "drawn lines, scan pixels"


@pytest.mark.parametrize("raster", [False, True])
def test_lines_that_are_not_a_box_s_own_do_not_cut_one(raster):
    """A water line from the middle of the sample columns out to the
    remarks, a dashed line right across the form: neither cuts a box. A
    contact drawn on from the description across the sample columns opens
    the box that starts there (S-5 has no top line of its own)."""
    fx = build_log(LogVariant("smp_cross", raster=raster, sample_boxes=True,
                              sample_crossers=True, seed=73,
                              skew_deg=0.3 if raster else 0.0,
                              text_source="ocr" if raster else None))
    g = _read(fx)
    _check_every_box(fx, g, "pixels" if raster else "vector")


def test_a_log_with_no_boxes_gives_no_samples():
    """Values set at a depth with no box round them, a scan whose headers
    have no text: no samples rather than wrong ones."""
    for gt in (build_imperial_log(), build_metric_log()):
        from planlens.document import Document
        assert log_grid(Document(content=gt.pdf), None).samples == []
    for v in log_variants():
        assert _read(build_log(v)).samples == [], v.name
    textless = build_log(LogVariant("smp_textless", sample_boxes=True,
                                    seed=74))
    assert _read(textless).samples == []


# -- the interval rules, on a page stated by hand -----------------------------

def _page(page, lines, cells, top=100.0, bottom=600.0, start=0.0):
    """One page: a ruler of 50 pt a metre starting at ``start`` at y=top, a
    sample-number column, a blows column, and the given lines and cells."""
    ruler = Ruler(page=page, kind="depth", column_id=f"p{page}d",
                  slope=0.02, intercept=start - 0.02 * top, residual=0.0,
                  step=1.0, ticks=(), unit="m", confidence=0.95)
    cols = [Column(id=f"p{page}d", page=page, x0=0, x1=30, name="depth",
                   names=("depth",), confidence=0.9),
            Column(id=f"p{page}n", page=page, x0=30, x1=60, name="sample_id",
                   names=("sample_id",), confidence=0.9),
            Column(id=f"p{page}b", page=page, x0=60, x1=120, name="blows",
                   names=("blows",), confidence=0.9)]
    pg = _PageGrid(page=page, width=200.0, height=700.0, columns=cols,
                   ruler=ruler, body_top=top, body_bottom=bottom)
    pg.sample_lines = [(y, ruler.value_at(y), 0.01, crosses)
                       for y, crosses in lines]
    for col, y, text in cells:
        x0 = 35.0 if col == "n" else 65.0
        pg.cells.append(Cell(page=page, column=("sample_id" if col == "n"
                                                else "blows"),
                             column_id=f"p{page}{col}", text=text,
                             bbox=(x0, y - 3.0, x0 + 20.0, y + 3.0),
                             depth=ruler.value_at(y),
                             depth_top=ruler.value_at(y - 3.0),
                             depth_bottom=ruler.value_at(y + 3.0)))
    return pg


def test_a_box_cut_by_the_sheet_break_is_one_sample():
    # sheet 1: S-1 from 9.0 m (y 550) to the foot; sheet 2 starts at 10.0 m
    # and the same box runs on to 10.45 m (y 122.5), its blows printed there
    p1 = _page(0, [(550.0, True)], [("n", 575.0, "S-1")])
    p2 = _page(1, [(122.5, True)], [("b", 110.0, "5 7 9")], start=10.0)
    samples, warnings = _build_samples([p1, p2])
    assert len(samples) == 1 and not warnings
    s = samples[0]
    assert s.label == "S-1" and dict(s.cells) == {"blows": "5 7 9"}
    assert s.top == pytest.approx(9.0) and s.bottom == pytest.approx(10.45)
    assert s.pages == (0, 1) and s.ends == ("line", "line")


def test_a_box_at_the_sheet_edge_says_so():
    p = _page(0, [(150.0, True)], [("n", 120.0, "S-1"),
                                   ("b", 120.0, "3 4 5")])
    (s,), _ = _build_samples([p])
    assert s.ends == ("sheet_top", "line")
    assert s.to_dict()["ends"] == ["sheet_top", "line"]


def test_a_number_straddling_a_line_under_the_blows_only_is_one_number():
    # the box 200-260 is closed under the blows at 230 only; its number is
    # printed in two lines, "SS" above 230 and "7" below
    p = _page(0, [(200.0, True), (230.0, False), (260.0, True)],
              [("n", 215.0, "SS"), ("b", 215.0, "50/5\""),
               ("n", 240.0, "7")])
    (s,), _ = _build_samples([p])
    assert s.label == "SS 7"
    assert (s.top, s.bottom) == (pytest.approx(2.0), pytest.approx(2.6))


def test_one_box_holding_two_sample_numbers_is_not_read():
    p = _page(0, [(200.0, True), (300.0, True)],
              [("n", 220.0, "S-1"), ("n", 280.0, "S-2")])
    samples, warnings = _build_samples([p])
    assert samples == []
    assert any("2 sample numbers" in w for w in warnings)


def test_an_empty_box_is_the_gap_between_samples():
    p = _page(0, [(200.0, True), (220.0, True), (260.0, True)],
              [("n", 210.0, "S-1")])
    samples, _ = _build_samples([p])
    assert [s.label for s in samples] == ["S-1"]


def test_sample_to_dict_is_compact():
    s = Sample(page=3, top=1.0449, bottom=1.4941, label="S-1",
               cells=(("blows", "12 14 16"),), pages=(3,), plus_minus=0.0064,
               read="vector", confidence=0.9)
    assert s.to_dict() == {"page": 3, "top": 1.04, "bottom": 1.49,
                           "plus_minus": 0.006, "id": "S-1",
                           "cells": {"blows": "12 14 16"},
                           "read": "drawn lines, vector", "confidence": 0.9}
