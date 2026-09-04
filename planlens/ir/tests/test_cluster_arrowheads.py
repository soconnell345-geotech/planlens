"""Tests for the fill-cluster arrowhead leg (Phase 3).

Real agency plots render arrowheads as clusters of tiny strokes (solid-
fill micro-dots, hatch fans) instead of closed triangles — verified on
the Mecklenburg ground-truth pairs. These fixtures plant both cluster
styles plus the two documented false-positive stressors (uniform stipple
texture, running SHX-style lettering at a line end) and pin that the
detector proposes the real ones and rejects the decoys.
"""

import math

import pytest

fitz = pytest.importorskip("fitz")

from planlens.ir import from_pdf_vector, queries as q
from planlens.ir.tests.leader_fixtures import (
    PAGE_HEIGHT, PAGE_WIDTH, _draw_shaft, _pt, _to_ir,
)


def _tiny_seg(page, x, y, dx=0.1, dy=0.0):
    s = page.new_shape()
    s.draw_line(_pt((x, y)), _pt((x + dx, y + dy)))
    s.finish(color=(0, 0, 0), width=0.5, closePath=False)
    s.commit()


def build_cluster_arrowhead_pdf(tmp_path, include_text: bool = True):
    """One dot-cluster leader + one hatch-fan leader + two decoys.

    Returns (path, gt) with gt tip/tail coords in IR (bottom-left) frame.
    Geometry is sized against the fixture's own shaft statistics: shafts
    ~150 pt, so the default max_arrowhead_size lands ~35-40 pt and the
    cluster core radius ~15 pt.
    """
    doc = fitz.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)

    # --- Leader 1: dot-cluster arrowhead (micro-dot fill style). ---------
    tail1, bend1, tip1 = (400.0, 120.0), (300.0, 150.0), (200.0, 150.0)
    _draw_shaft(page.new_shape(), [tail1, bend1, tip1])
    # 12 micro-dots in a compact blob straddling the tip.
    for i in range(12):
        ang = i * 2.39996  # golden-angle spiral: irregular, compact
        r = 0.9 + 2.2 * (i / 12.0)
        _tiny_seg(page, tip1[0] + r * math.cos(ang),
                  tip1[1] + r * math.sin(ang))
    if include_text:
        page.insert_text(_pt((408.0, 118.0)), "DOT CALLOUT", fontsize=9)

    # --- Leader 2: hatch-fan arrowhead (short parallel strokes). ---------
    tail2, bend2, tip2 = (430.0, 300.0), (330.0, 330.0), (220.0, 330.0)
    _draw_shaft(page.new_shape(), [tail2, bend2, tip2])
    # 6 strokes perpendicular to the shaft, stacked along its last ~3.5 pt,
    # widths tapering toward the tip like a filled triangle's raster. Sized
    # to the fixture's own arrowhead scale (max_arrowhead_size ~7 pt here,
    # so the cluster core radius is ~2.8 pt).
    for i in range(6):
        back = 0.5 + 0.55 * i
        half = 0.3 + 0.25 * i
        x = tip2[0] + back
        s = page.new_shape()
        s.draw_line(_pt((x, tip2[1] - half)), _pt((x, tip2[1] + half)))
        s.finish(color=(0, 0, 0), width=0.4, closePath=False)
        s.commit()
    if include_text:
        page.insert_text(_pt((438.0, 298.0)), "FAN CALLOUT", fontsize=9)

    # --- Decoy A: long line ending inside a UNIFORM stipple field. -------
    # Density inside the core matches the surrounding annulus, so the
    # density-spike gate must reject it.
    _draw_shaft(page.new_shape(), [(120.0, 620.0), (260.0, 620.0)])
    for gx in range(20):
        for gy in range(14):
            _tiny_seg(page, 200.0 + gx * 9.0, 560.0 + gy * 9.0)

    # --- Decoy B: running lettering-like stroke row at a line end. -------
    # A 100-pt ribbon of glyph-scale strokes along the line axis: the
    # union-bbox aspect gate must reject the high-aspect core strip.
    _draw_shaft(page.new_shape(), [(120.0, 700.0), (300.0, 700.0)])
    for i in range(50):
        _tiny_seg(page, 302.0 + i * 2.0, 700.0, dx=0.0, dy=1.6)

    path = str(tmp_path / "cluster_arrowheads.pdf")
    doc.save(path)
    doc.close()
    return path, {
        "tip1_ir": _to_ir(*tip1), "tail1_ir": _to_ir(*tail1),
        "tip2_ir": _to_ir(*tip2), "tail2_ir": _to_ir(*tail2),
        "decoy_a_end_ir": _to_ir(260.0, 620.0),
        "decoy_b_end_ir": _to_ir(300.0, 700.0),
    }


def _near(p, xy, tol=4.0):
    return math.hypot(p["tip_xy"][0] - xy[0], p["tip_xy"][1] - xy[1]) <= tol


class TestClusterArrowheads:
    @pytest.fixture(scope="class")
    def result(self, tmp_path_factory):
        path, gt = build_cluster_arrowhead_pdf(
            tmp_path_factory.mktemp("clusters"))
        ir = from_pdf_vector(filepath=path)
        return gt, q.find_leaders(ir)

    def test_dot_cluster_leader_found(self, result):
        gt, props = result
        hits = [p for p in props if _near(p, gt["tip1_ir"])]
        assert hits, "dot-cluster leader not proposed"
        assert hits[0]["evidence"]["arrowhead_kind"] == "fill_cluster"
        assert hits[0]["confidence"] >= 0.5
        assert "DOT CALLOUT" in (hits[0]["text"] or "")

    def test_hatch_fan_leader_found(self, result):
        gt, props = result
        hits = [p for p in props if _near(p, gt["tip2_ir"])]
        assert hits, "hatch-fan leader not proposed"
        assert hits[0]["evidence"]["arrowhead_kind"] == "fill_cluster"
        assert hits[0]["confidence"] >= 0.5

    def test_stipple_field_rejected(self, result):
        gt, props = result
        assert not [p for p in props
                    if _near(p, gt["decoy_a_end_ir"], tol=6.0)
                    and p["evidence"]["arrowhead_kind"] == "fill_cluster"], \
            "uniform stipple at a line end must not become an arrowhead"

    def test_lettering_row_rejected(self, result):
        gt, props = result
        assert not [p for p in props
                    if _near(p, gt["decoy_b_end_ir"], tol=6.0)
                    and p["evidence"]["arrowhead_kind"] == "fill_cluster"], \
            "running lettering at a line end must not become an arrowhead"


class TestNoTextRenormalization:
    def test_confidence_renormalized_and_flagged(self, tmp_path):
        path, gt = build_cluster_arrowhead_pdf(tmp_path, include_text=False)
        ir = from_pdf_vector(filepath=path)
        props = [p for p in q.find_leaders(ir) if _near(p, gt["tip1_ir"])]
        assert props, "leader must still be proposed without a text layer"
        p = props[0]
        assert p["evidence"].get("text_unavailable") is True
        # Renormalized over alignment+simplicity: a clean 1-bend leader
        # must clear the 0.5 band despite the missing text component.
        assert p["confidence"] >= 0.5
