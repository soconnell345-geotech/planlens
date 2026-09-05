"""Phase-3.2 tests: signed arrow-direction attach + leader letterform caps.

Fixture geometry is copied (rounded) from the Mecklenburg ground-truth
plots — the measured real anatomy behind each rule, not invented shapes:

- the ARBITRATION STEAL (sheet 10.31A): a long witness line whose two ends
  happen to touch two multileader arrowheads scored 0.833 as a continuous
  dimension and, via ``exclude_dimensions``, silently dropped both TRUE
  leaders (the mechanism behind all 4 residual Phase-3.1 leader misses);
- the SHORT both-arrowed dimension (sheet 3001, the 'T=' construct): a
  9.7 pt shaft between two outward arrows whose apexes sit on the witness
  ticks — killed by the old min-shaft-length floor;
- the LETTERFORM false positives (sheet 2000a): letter chains paired with
  neighboring strokes at high confidence (295 above-default proposals on
  a sheet with zero annotations).
"""

from __future__ import annotations

import math

import pytest

from planlens.ir import queries as q
from planlens.ir.results import DrawingIR, Line, Polyline

MAS = 9.31  # the validation sheets' arrowhead scale (points)


def _ir(entities):
    ir = DrawingIR(units="pt", coordinate_space="page", origin="bottom_left",
                   source="pdf_vector", width=612.0, height=792.0)
    for i, e in enumerate(entities):
        e.id = f"e{i}"
        ir.add(e)
    return ir


def _arrow_base_leg(apex, direction, leg=7.3, base=2.4):
    """[base-corner, base-corner, apex] open chain (native-dim flavor)."""
    dx, dy = direction
    px, py = -dy, dx
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[(bx + px * base / 2, by + py * base / 2),
                              (bx - px * base / 2, by - py * base / 2),
                              apex], closed=False)


def _arrow_chevron(apex, direction, leg=7.3, base=2.4):
    """[barb, apex, barb] open chain (native-leader flavor)."""
    dx, dy = direction
    px, py = -dy, dx
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[(bx + px * base / 2, by + py * base / 2),
                              apex,
                              (bx - px * base / 2, by - py * base / 2)],
                    closed=False)


# ---------------------------------------------------------------------------
# Intrinsic apex/axis + signed attach unit behavior
# ---------------------------------------------------------------------------

class TestIntrinsicApexAxis:
    @pytest.mark.parametrize("builder", [_arrow_base_leg, _arrow_chevron])
    @pytest.mark.parametrize("direction", [(1.0, 0.0), (0.0, -1.0),
                                           (-0.8, 0.6)])
    def test_apex_recovered_both_flavors(self, builder, direction):
        arrow = builder((100.0, 100.0), direction)
        apex, ax = q._intrinsic_apex_axis(
            [tuple(v) for v in arrow.vertices])
        assert apex == pytest.approx((100.0, 100.0), abs=1e-9)
        assert ax[0] * direction[0] + ax[1] * direction[1] == \
            pytest.approx(1.0, abs=0.01)

    def test_outward_arrow_attaches(self):
        arrow = _arrow_base_leg((100.0, 100.0), (1.0, 0.0))
        att = q._dim_arrow_attach([tuple(v) for v in arrow.vertices],
                                  "triangle", (1.0, 0.0))
        assert att is not None
        score, apex = att
        assert score == pytest.approx(1.0, abs=0.01)
        assert apex == pytest.approx((100.0, 100.0), abs=1e-9)

    def test_backward_arrow_rejected(self):
        # An arrow pointing BACK along the claiming line (a leader
        # arrowhead a stroke merely grazes) is not that line's arrow.
        arrow = _arrow_base_leg((100.0, 100.0), (1.0, 0.0))
        att = q._dim_arrow_attach([tuple(v) for v in arrow.vertices],
                                  "triangle", (-1.0, 0.0))
        assert att is None

    def test_perpendicular_arrow_rejected(self):
        arrow = _arrow_base_leg((100.0, 100.0), (1.0, 0.0))
        att = q._dim_arrow_attach([tuple(v) for v in arrow.vertices],
                                  "triangle", (0.0, 1.0))
        assert att is None

    def test_cluster_keeps_sign_blind_alignment(self):
        # Fill clusters have no vertex apex: an isotropic blob keeps the
        # neutral 0.7 score regardless of direction.
        blob = [(100.0, 100.0), (101.0, 101.5), (99.0, 101.0),
                (100.5, 98.7), (98.8, 99.2)]
        att = q._dim_arrow_attach(blob, "fill_cluster", (-1.0, 0.0))
        assert att is not None
        assert att[0] == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# The arbitration steal (sheet 10.31A anatomy)
# ---------------------------------------------------------------------------

def _steal_entities():
    """Two true leaders + the long witness line touching both their tips.

    Anatomy of 10.31A's false 0.833 dimension: shaft = the near-vertical
    105 pt line e2133 running between the two multileader tips; its ends
    each grabbed the OTHER construct's closed triangle with the old
    fold-blind alignment.
    """
    d1 = q._unit_vec(-6.25, -1.6)     # measured arrow direction, tip 3
    d2 = q._unit_vec(-5.2, 4.2)       # measured arrow direction, tip 4
    ents = [
        # leader 1: triangle apex (220.9, 388.8), shaft trailing outward
        _arrow_base_leg((220.9, 388.8), d1, leg=6.5, base=2.1),
        Line(start=(227.2, 390.4),
             end=(227.2 + 35.0 * -d1[0], 390.4 + 35.0 * -d1[1])),
        # leader 2: triangle apex (219.6, 488.2), shaft trailing outward
        # from the arrow's base center
        _arrow_base_leg((219.6, 488.2), d2, leg=6.6, base=2.2),
        Line(start=(219.6 + 6.51 * -d2[0], 488.2 + 6.51 * -d2[1]),
             end=(219.6 + 60.0 * -d2[0], 488.2 + 60.0 * -d2[1])),
        # the long near-vertical line whose ends touch both triangles
        Polyline(vertices=[(219.8, 495.2), (218.2, 460.0), (221.3, 389.8)],
                 closed=False),
    ]
    return ents


class TestArbitrationSteal:
    def test_no_dimension_claims_the_leader_arrows(self):
        ir = _ir(_steal_entities())
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS,
                                 min_confidence=0.5)
        claimed = {aid for p in dims for aid in p["arrowhead_ids"]}
        assert "e0" not in claimed
        assert "e2" not in claimed

    def test_leaders_survive_exclude_dimensions(self):
        ir = _ir(_steal_entities())
        leads = q.find_leaders(ir, max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        tips = [tuple(p["tip_xy"]) for p in leads]
        assert any(math.hypot(t[0] - 220.9, t[1] - 388.8) < 8 for t in tips)
        assert any(math.hypot(t[0] - 219.6, t[1] - 488.2) < 8 for t in tips)


# ---------------------------------------------------------------------------
# Short both-arrowed continuous dimension (sheet 3001 'T=' anatomy)
# ---------------------------------------------------------------------------

def _t_dim_entities():
    y = 158.6
    return [
        Line(start=(520.0, 157.4), end=(520.0, 165.8)),    # left witness
        Polyline(vertices=[(527.2, 159.8), (527.2, 157.4),
                           (520.0, y)], closed=False),      # left arrow
        Line(start=(527.2, y), end=(536.9, y)),             # 9.7 pt shaft
        Polyline(vertices=[(536.9, 159.8), (536.9, 157.4),
                           (544.1, y)], closed=False),      # right arrow
        Line(start=(544.1, 157.4), end=(544.1, 165.8)),    # right witness
    ]


class TestShortContinuousDimension:
    def test_detected_with_apex_ends(self):
        ir = _ir(_t_dim_entities())
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS,
                                 min_confidence=0.5)
        cont = [p for p in dims if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        p = cont[0]
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        # Ends are the arrow APEXES (the CAD defpoints on the witnesses),
        # not the short shaft's endpoints.
        assert xs[0] == pytest.approx(520.0, abs=0.1)
        assert xs[1] == pytest.approx(544.1, abs=0.1)
        assert p["evidence"]["n_extension_ends"] == 2
        assert p["confidence"] >= 0.5

    def test_short_shaft_requires_two_drawn_arrows(self):
        # The same short shaft with only ONE arrow must not produce a
        # continuous proposal (the min-length floor still guards it).
        ents = _t_dim_entities()[:3]
        ir = _ir(ents)
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        assert [p for p in dims
                if p["evidence"]["path"] == "continuous"] == []


# ---------------------------------------------------------------------------
# Leader letterform caps (sheet 2000a anatomy)
# ---------------------------------------------------------------------------

class TestLeaderLetterformCaps:
    def test_genuine_leader_uncapped(self):
        ents = [
            _arrow_chevron((100.0, 100.0), (-1.0, 0.0)),
            Line(start=(100.0, 100.0), end=(140.0, 100.0)),
        ]
        ir = _ir(ents)
        leads = q.find_leaders(ir, max_arrowhead_size=MAS)
        assert leads and leads[0]["confidence"] > 0.85
        ev = leads[0]["evidence"]
        assert "arrow_direction_violation" not in ev
        assert "arrow_detached" not in ev
        assert ev["signed_axis_alignment"] == pytest.approx(1.0, abs=0.02)

    def test_misdirected_arrow_capped(self):
        # A letterform chevron pointing UP paired with a horizontal
        # neighboring stroke: the stroke ends right at the chain (attach
        # passes) but the arrow does not point along it.
        ents = [
            _arrow_chevron((100.0, 100.0), (0.0, 1.0)),
            Line(start=(101.0, 96.0), end=(131.0, 96.0)),
        ]
        ir = _ir(ents)
        leads = q.find_leaders(ir, max_arrowhead_size=MAS)
        assert leads
        p = leads[0]
        assert p["confidence"] <= q._UNCORROBORATED_CAP
        assert p["evidence"]["arrow_direction_violation"] is True

    def test_detached_arrow_capped(self):
        # Axis-aligned by luck, but the stroke ends 9.6 pt from the chain
        # (the measured p50 of the sign-passing 2000a junk): a shaft ENDS
        # at its arrowhead; this one merely passes near it.
        ents = [
            _arrow_chevron((100.0, 100.0), (-1.0, 0.0)),
            Line(start=(113.5, 100.0), end=(145.0, 100.0)),
        ]
        ir = _ir(ents)
        leads = q.find_leaders(ir, max_arrowhead_size=MAS)
        assert leads
        p = leads[0]
        assert p["confidence"] <= q._UNCORROBORATED_CAP
        assert p["evidence"]["arrow_detached"] is True

    def test_capped_proposals_stay_visible_below_default(self):
        ents = [
            _arrow_chevron((100.0, 100.0), (0.0, 1.0)),
            Line(start=(101.0, 96.0), end=(131.0, 96.0)),
        ]
        ir = _ir(ents)
        assert q.find_leaders(ir, max_arrowhead_size=MAS,
                              min_confidence=0.5) == []
        assert q.find_leaders(ir, max_arrowhead_size=MAS,
                              min_confidence=0.3)  # still proposed
