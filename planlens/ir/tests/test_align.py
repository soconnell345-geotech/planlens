"""Tests for planlens.ir.align — the model->plot transform fit."""

import math

import pytest

fitz = pytest.importorskip("fitz")

from planlens.ir import from_pdf_vector
from planlens.ir.align import _rot_xy, fit_plot_transform
from planlens.ir.tests.leader_fixtures import build_synthetic_leader_pdf


def _inverse_model(pts, rot, scale, offset):
    """Model-space points that map onto ``pts`` under (rot, scale, offset)."""
    inv_rot = (360 - rot) % 360
    out = []
    for (x, y) in pts:
        ux, uy = (x - offset[0]) / scale, (y - offset[1]) / scale
        out.append(_rot_xy(ux, uy, inv_rot))
    return out


@pytest.fixture(scope="module")
def leader_ir(tmp_path_factory):
    path, gt = build_synthetic_leader_pdf(
        tmp_path_factory.mktemp("align"), n_leaders=4, include_decoys=True)
    ir = from_pdf_vector(filepath=path)
    chains_page = [[tuple(L.tip_xy), tuple(L.bend_xy), tuple(L.tail_xy)]
                   for L in gt["leaders"]]
    anchor_page_pts = [p for c in chains_page for p in c]
    return ir, anchor_page_pts, chains_page


class TestFitPlotTransform:
    @pytest.mark.parametrize("rot,scale,offset", [
        (0, 1.0, (0.0, 0.0)),           # identity
        (0, 72.0, (12.0, -30.0)),       # paper-scale inches + shift
        (270, 72.0, (30.0, 40.0)),      # the Mecklenburg configuration
        (90, 12.0, (-5.0, 100.0)),      # 1"=6' style plot scale
    ])
    def test_recovers_known_transform(self, leader_ir, rot, scale, offset):
        ir, page_pts, chains_page = leader_ir
        model = _inverse_model(page_pts, rot, scale, offset)
        chains = [_inverse_model(c, rot, scale, offset)
                  for c in chains_page]
        fit = fit_plot_transform(model, ir, anchor_chains=chains,
                                 min_votes=4)
        assert fit is not None, "no fit found"
        for mp, pp in zip(model, page_pts):
            ax, ay = fit["apply"](mp)
            assert math.hypot(ax - pp[0], ay - pp[1]) < 1.0, (
                f"anchor mapped {ax, ay}, expected {pp} "
                f"(fit rot={fit['rotation_deg']} s={fit['scale']:.3f})")

    def test_returns_none_for_garbage_anchors(self, leader_ir):
        ir, _, _ = leader_ir
        # Anchors on a lattice unrelated to any drawing geometry, far
        # off-page under every plausible extent-derived scale.
        model = [(1e6 + 977.0 * i, -1e6 + 1013.0 * i * i) for i in range(8)]
        fit = fit_plot_transform(model, ir, min_votes=6)
        assert fit is None or fit["n_matched"] < 3

    def test_too_few_anchors(self, leader_ir):
        ir, page_pts, _ = leader_ir
        assert fit_plot_transform(page_pts[:2], ir) is None


class TestDegenerateScaleGuard:
    """The extent guard + modal-significance test (2026-09-05).

    Verified against the real ground-truth sheets offline: before the
    guard, 10 random anchors on a dense 10k-entity plot returned
    "matched 8/10, rms < 1 pt"; after it, 0/60 random-anchor trials fit
    while all three true fits (rms 0.02-0.03 pt) still pass. These
    committed tests reproduce the property on a synthetic dense sheet.
    """

    @pytest.fixture(scope="class")
    def dense_ir(self, tmp_path_factory):
        # A dense sheet: hundreds of short segments everywhere, so a
        # degenerate-scale or chance-offset hypothesis has plenty of
        # endpoints to "match".
        import random

        import fitz

        from planlens.ir.tests.leader_fixtures import (
            PAGE_HEIGHT, PAGE_WIDTH, _draw_shaft)

        rng = random.Random(11)
        doc = fitz.open()
        page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        for _ in range(600):
            x, y = rng.uniform(20, 880), rng.uniform(20, 640)
            a = rng.uniform(0, 6.283)
            L = rng.uniform(2, 12)
            s = page.new_shape()
            _draw_shaft(s, [(x, y), (x + L * math.cos(a),
                             y + L * math.sin(a))])
        path = str(tmp_path_factory.mktemp("dense") / "dense.pdf")
        doc.save(path)
        doc.close()
        return from_pdf_vector(filepath=path)

    def test_random_anchors_do_not_fit(self, dense_ir):
        import random
        rng = random.Random(3)
        for _ in range(5):
            fake = [(rng.uniform(0, 11), rng.uniform(0, 8.5))
                    for _ in range(10)]
            assert fit_plot_transform(fake, dense_ir) is None

    def test_true_fit_reports_extent_frac(self, leader_ir):
        ir, page_pts, chains_page = leader_ir
        model = _inverse_model(page_pts, 270, 72.0, (30.0, 40.0))
        chains = [_inverse_model(c, 270, 72.0, (30.0, 40.0))
                  for c in chains_page]
        fit = fit_plot_transform(model, ir, anchor_chains=chains,
                                 min_votes=4)
        assert fit is not None
        assert fit["extent_frac"] >= 0.05
