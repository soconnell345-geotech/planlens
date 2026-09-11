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

The later classes pin the Phase-3.2 review fixes, each of which grows the
representation rather than loosening a gate — a blunt terminator is not a
failed arrow, an apex-closed ring is not a backward one, a big arrow is
not a detached one — and each of which CAPS rather than deletes.

Read the caps as a LADDER, and note that nothing on it deletes: an arrow
whose axis contradicts its line ranks below one that is merely
uncorroborated, which ranks below the 0.5 call threshold, which is where
a blunt terminator, an off-spine or detached end, and an extent under the
floor all hold a construct — unconditionally, corroboration or not, since
none of the observable channels speaks to those defects. The scenes that
can only be exercised with a TEXT LAYER (which no corpus sheet has) live
in :mod:`planlens.ir.tests.test_text_bearing_scenes`.
"""

from __future__ import annotations

import math

import pytest

from planlens.ir import queries as q
from planlens.ir.results import (
    Circle, DrawingIR, Line, Polyline, TextItem)

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


def _closed_triangle(apex, direction, leg=4.0, base=1.35):
    """A FILLED arrowhead as a closed 3-vertex polyline (the PDF-vector
    ingest shape). Small enough to leave a real shaft between two of
    them at a narrow span."""
    dx, dy = direction
    px, py = -dy, dx
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[apex,
                              (bx + px * base / 2, by + py * base / 2),
                              (bx - px * base / 2, by - py * base / 2)],
                    closed=True)


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
        att = q._arrow_attach([tuple(v) for v in arrow.vertices],
                              "triangle", (1.0, 0.0), (100.0, 100.0), MAS)
        assert att.state == "ok"
        assert att.attached and att.on_spine
        assert att.score == pytest.approx(1.0, abs=0.01)
        assert att.apex == pytest.approx((100.0, 100.0), abs=1e-9)

    @pytest.mark.parametrize("sdir", [(-1.0, 0.0), (0.0, 1.0)])
    def test_misdirected_arrow_contradicted_not_deleted(self, sdir):
        # An arrow pointing BACK along the claiming line, or across it (a
        # leader arrowhead a stroke merely grazes), is not that line's
        # arrow — but the construct is CAPPED, never dropped: the attach
        # predicate still returns it, carrying zero alignment evidence.
        arrow = _arrow_base_leg((100.0, 100.0), (1.0, 0.0))
        att = q._arrow_attach([tuple(v) for v in arrow.vertices],
                              "triangle", sdir, (100.0, 100.0), MAS)
        assert att.state == "contradicted"
        assert att.score == 0.0
        assert att.signed < q._ARROW_AXIS_MIN

    def test_cluster_keeps_sign_blind_alignment(self):
        # Fill clusters have no vertex apex: an isotropic blob keeps the
        # neutral 0.7 score regardless of direction.
        blob = [(100.0, 100.0), (101.0, 101.5), (99.0, 101.0),
                (100.5, 98.7), (98.8, 99.2)]
        att = q._arrow_attach(blob, "fill_cluster", (-1.0, 0.0),
                              (100.0, 100.0), MAS)
        assert att.state == "ok"
        assert att.score == pytest.approx(0.7)

    def test_cluster_apex_lands_on_the_shaft_ray(self):
        # A stipple splash carries no vertex-level tip: its defpoint is
        # its farthest REACH along the line it terminates, ON that line —
        # the members' lateral scatter is stipple, not information.
        # (Taking the farthest member center published dots up to 4.5 pt
        # off the dimension line as CAD defpoints.)
        blob = [(100.0, 100.0), (102.4, 101.9), (101.1, 98.0),
                (103.0, 100.4), (98.9, 99.6)]
        att = q._arrow_attach(blob, "fill_cluster", (1.0, 0.0),
                              (100.0, 100.0), MAS)
        assert att.apex == pytest.approx((103.0, 100.0), abs=1e-9)

    def test_cluster_behind_the_tip_falls_back_to_the_tip(self):
        blob = [(97.0, 100.2), (96.0, 99.4), (95.2, 100.6)]
        att = q._arrow_attach(blob, "fill_cluster", (1.0, 0.0),
                              (100.0, 100.0), MAS)
        assert att.apex == pytest.approx((100.0, 100.0), abs=1e-9)


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


# ---------------------------------------------------------------------------
# Vertex dedup: an apex-closed ring is not a backward arrow
# ---------------------------------------------------------------------------

def _apex_closed_ring(apex, direction, leg=7.3, base=2.4, closed=False,
                      gap=0.0):
    """``[apex, b1, b2, apex]`` — an outline traced FROM its apex and
    closed back onto it. Ingests verbatim from a DXF LWPOLYLINE that
    repeats its closing vertex (two such candidates on ground-truth
    sheet 21.01); ``gap`` makes the repeat near-coincident instead."""
    dx, dy = direction
    px, py = -dy, dx
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[apex,
                              (bx + px * base / 2, by + py * base / 2),
                              (bx - px * base / 2, by - py * base / 2),
                              (apex[0] + dx * gap, apex[1] + dy * gap)],
                    closed=closed)


class TestCoincidentVertexDedup:
    """A repeated corner made "the centroid of the OTHER vertices" false:
    the duplicate dragged that centroid onto the apex and the vote elected
    a BASE corner, inverting the axis (measured -0.949 against a true
    +1.0). Real ingest shapes, all three flavors."""

    @pytest.mark.parametrize("closed,gap", [(False, 0.0), (True, 0.0),
                                            (False, 0.01)])
    def test_apex_recovered_from_a_closed_ring(self, closed, gap):
        ring = _apex_closed_ring((100.0, 100.0), (-1.0, 0.0), closed=closed,
                                 gap=gap)
        apex, ax = q._intrinsic_apex_axis([tuple(v) for v in ring.vertices])
        assert apex == pytest.approx((100.0, 100.0), abs=0.02)
        assert ax[0] == pytest.approx(-1.0, abs=0.01)

    def test_ring_leader_is_not_flagged_misdirected(self):
        ents = [
            _apex_closed_ring((100.0, 100.0), (-1.0, 0.0)),
            Line(start=(100.0, 100.0), end=(160.0, 100.0)),
            TextItem(content="TOP OF WALL", position=(164.0, 100.0),
                     height=3.0),
        ]
        leads = q.find_leaders(_ir(ents), max_arrowhead_size=MAS)
        assert leads
        assert "arrow_direction_violation" not in leads[0]["evidence"]
        assert leads[0]["evidence"]["signed_axis_alignment"] == \
            pytest.approx(1.0, abs=0.01)
        assert leads[0]["confidence"] > 0.85

    def test_ring_dimension_is_detected_with_apex_ends(self):
        y = 158.6
        ents = [
            Line(start=(500.0, y - 12), end=(500.0, y + 12)),
            Line(start=(560.0, y - 12), end=(560.0, y + 12)),
            Line(start=(500.0, y), end=(560.0, y)),
            _apex_closed_ring((500.0, y), (-1.0, 0.0)),
            _apex_closed_ring((560.0, y), (1.0, 0.0)),
            TextItem(content="24 FT", position=(530.0, y + 4), height=3.0),
        ]
        cont = [p for p in q.find_dimensions(_ir(ents),
                                             max_arrowhead_size=MAS,
                                             min_confidence=0.5)
                if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        xs = sorted([cont[0]["end_a_xy"][0], cont[0]["end_b_xy"][0]])
        assert xs == pytest.approx([500.0, 560.0], abs=0.1)


# ---------------------------------------------------------------------------
# Scale-aware attachment: a big arrow is not a detached one
# ---------------------------------------------------------------------------

class TestScaleAwareAttachment:
    """The detach bound is the arrow's OWN axial length when that exceeds
    the sheet-statistic estimate. The candidate gate deliberately admits
    open-3 shapes up to 1.5x the estimate (it ran ~20% under the real
    plotted arrows on one validation sheet); bounding those by the
    estimate alone made them unattachable to anything."""

    def _leader(self, leg, mas):
        ents = [
            _arrow_chevron((100.0, 100.0), (-1.0, 0.0), leg=leg,
                           base=round(0.33 * leg, 2)),
            Line(start=(100.0, 100.0), end=(160.0, 100.0)),
            TextItem(content="6 IN. PIPE", position=(164.0, 100.0),
                     height=3.0),
        ]
        leads = q.find_leaders(_ir(ents), max_arrowhead_size=mas)
        assert leads
        return leads[0]

    def test_arrow_larger_than_the_sheet_estimate_still_attaches(self):
        # 12.3 pt arrow at a 10.0 pt estimate: identical anatomy to the
        # in-scale case, perfect alignment, text present.
        p = self._leader(12.3, 10.0)
        assert "arrow_detached" not in p["evidence"]
        assert p["confidence"] > 0.9

    def test_in_scale_arrow_unchanged(self):
        p = self._leader(7.3, 9.31)
        assert "arrow_detached" not in p["evidence"]
        assert p["confidence"] > 0.9

    def test_the_two_scales_score_alike(self):
        assert self._leader(12.3, 10.0)["confidence"] == \
            pytest.approx(self._leader(7.3, 9.31)["confidence"], abs=0.01)

    def test_the_bound_is_a_floor_not_a_replacement(self):
        # The measured letterform case must STILL be capped: a 7.3 pt
        # chevron (h = 7.2 < 9.31) does not widen its own bound, and the
        # stroke ends 13.5 pt away.
        ents = [
            _arrow_chevron((100.0, 100.0), (-1.0, 0.0)),
            Line(start=(113.5, 100.0), end=(145.0, 100.0)),
        ]
        p = q.find_leaders(_ir(ents), max_arrowhead_size=MAS)[0]
        assert p["evidence"]["arrow_detached"] is True
        assert p["confidence"] <= q._UNCORROBORATED_CAP


# ---------------------------------------------------------------------------
# Blunt terminators: box / dot / oblique are not failed arrows
# ---------------------------------------------------------------------------

def _box_terminator(cx, cy, s=1.3):
    return Polyline(vertices=[(cx - s, cy - s), (cx + s, cy - s),
                              (cx + s, cy + s), (cx - s, cy + s)],
                    closed=True)


def _box_dim_entities(y=158.6):
    return [
        Line(start=(500.0, y - 12), end=(500.0, y + 12)),   # witness
        Line(start=(560.0, y - 12), end=(560.0, y + 12)),   # witness
        Line(start=(500.0, y), end=(560.0, y)),             # shaft
        _box_terminator(500.0, y), _box_terminator(560.0, y),
        TextItem(content="24 FT", position=(530.0, y + 4), height=3.0),
    ]


class TestBluntTerminators:
    def test_a_box_has_no_pointing_direction(self):
        box = [tuple(v) for v in _box_terminator(500.0, 158.6).vertices]
        assert q._arrow_geometry(box).pointed is False
        assert q._arrow_geometry(
            [tuple(v) for v in
             _arrow_chevron((100.0, 100.0), (-1.0, 0.0)).vertices]).pointed

    def test_box_terminated_dimension_is_proposed_but_never_called(self):
        # Asking whether a box "points along the line" is a category
        # error, and answering no deleted the construct at every
        # min_confidence. It is PROPOSED — with the drawn ends — and held
        # in the observational band, corroboration or not: witnesses and
        # a value text say something is measured here, never that a
        # non-directional shape terminates THIS line.
        ents = _box_dim_entities()
        dims = q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                 min_confidence=0.0)
        cont = [p for p in dims if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        p = cont[0]
        assert p["evidence"]["blunt_terminators"] is True
        # The alignment channel is UNOBSERVABLE, not zero: confidence
        # renormalizes over text + witnesses — and is then capped.
        assert p["evidence"]["extension_line_score"] == 1.0
        assert p["evidence"]["text_proximity_score"] > 0
        assert p["confidence"] <= q._UNCORROBORATED_CAP
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        assert xs == pytest.approx([500.0, 560.0], abs=0.1)
        assert [pp for pp in q.find_dimensions(_ir(_box_dim_entities()),
                                               max_arrowhead_size=MAS,
                                               min_confidence=0.5)
                if pp["evidence"]["path"] == "continuous"] == []

    def test_a_blunt_terminator_never_enters_the_arbitration_channel(self):
        # arrowhead_ids is what exclude_dimensions arbitrates on. A
        # non-directional shape carries no evidence that it belongs to
        # THIS line rather than one crossing it, so it may not be named
        # there — but it stays discoverable in the evidence.
        cont = [p for p in q.find_dimensions(_ir(_box_dim_entities()),
                                             max_arrowhead_size=MAS,
                                             min_confidence=0.0)
                if p["evidence"]["path"] == "continuous"]
        assert cont[0]["arrowhead_ids"] == []
        assert sorted(cont[0]["evidence"]["blunt_terminator_ids"]) == \
            ["e3", "e4"]

    def test_uncorroborated_blunt_stays_below_the_call_threshold(self):
        # Same scene minus the value text (an SHX-stroked plot has no text
        # layer at all): witnesses alone do not corroborate a terminator
        # that carries no direction, so it stays visible in the
        # observational band and is never called.
        ir = _ir([e for e in _box_dim_entities()
                  if not isinstance(e, TextItem)])
        assert [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                             min_confidence=0.5)
                if p["evidence"]["path"] == "continuous"] == []
        deep = [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                             min_confidence=0.3)
                if p["evidence"]["path"] == "continuous"]
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP

    def test_one_blunt_end_cannot_lift_a_pair_over_the_threshold(self):
        # Measured on ground-truth sheet 3001: a true leader arrowhead at
        # one end and a blunt shape at the other scored 0.825 and stole
        # the leader's triangle through exclude_dimensions.
        y = 158.6
        ents = [
            Line(start=(500.0, y - 12), end=(500.0, y + 12)),
            Line(start=(560.0, y - 12), end=(560.0, y + 12)),
            Line(start=(500.0, y), end=(560.0, y)),
            _arrow_chevron((500.0, y), (-1.0, 0.0)),
            _box_terminator(560.0, y),
        ]
        cont = [p for p in q.find_dimensions(_ir(ents),
                                             max_arrowhead_size=MAS,
                                             min_confidence=0.3)
                if p["evidence"]["path"] == "continuous"]
        assert cont and cont[0]["confidence"] <= q._UNCORROBORATED_CAP


# ---------------------------------------------------------------------------
# Cap, don't delete: a contradicted dimension arrow
# ---------------------------------------------------------------------------

def _crooked_dim_entities(off_deg=40.0, y=158.6):
    """A witnessed, texted dimension whose two arrows are drafted well off
    the dimension line — the shape the signed test rejects."""
    a1 = math.radians(180.0 + off_deg)
    a2 = math.radians(off_deg)
    return [
        Line(start=(500.0, y - 12), end=(500.0, y + 12)),
        Line(start=(560.0, y - 12), end=(560.0, y + 12)),
        Line(start=(500.0, y), end=(560.0, y)),
        _arrow_chevron((500.0, y), (math.cos(a1), math.sin(a1))),
        _arrow_chevron((560.0, y), (math.cos(a2), math.sin(a2))),
        TextItem(content="24 FT", position=(530.0, y + 4), height=3.0),
    ]


class TestContradictedArrowIsCappedNotDeleted:
    def _cont(self, min_confidence):
        return [p for p in q.find_dimensions(_ir(_crooked_dim_entities()),
                                             max_arrowhead_size=MAS,
                                             min_confidence=min_confidence)
                if p["evidence"]["path"] == "continuous"]

    def test_absent_at_the_observational_band(self):
        assert self._cont(0.5) == []
        assert self._cont(0.3) == []

    def test_present_and_flagged_below_the_contradicted_cap(self):
        deep = self._cont(0.0)
        assert len(deep) == 1
        assert deep[0]["confidence"] <= q._CONTRADICTED_CAP
        assert deep[0]["evidence"]["arrow_direction_violation"] is True

    def test_contradicted_ranks_below_merely_uncorroborated(self):
        assert q._CONTRADICTED_CAP < q._UNCORROBORATED_CAP < 0.5


# ---------------------------------------------------------------------------
# Spine: the shaft runs INTO its arrowhead, along its middle
# ---------------------------------------------------------------------------

class TestArrowSpine:
    def _entities(self):
        """A 100 pt shaft whose far end is claimed by a glyph chevron
        sitting 5 pt off to the side — axis-aligned, centroid inside the
        attach radius, but the shaft does not run into it."""
        return [
            _arrow_chevron((0.0, 0.0), (-1.0, 0.0)),
            Line(start=(0.0, 0.0), end=(100.0, 0.0)),
            _arrow_chevron((108.0, 5.0), (1.0, 0.0)),
        ]

    def test_off_spine_end_publishes_drawn_geometry(self):
        cont = [p for p in q.find_dimensions(_ir(self._entities()),
                                             max_arrowhead_size=MAS,
                                             min_confidence=0.3)
                if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        p = cont[0]
        # The published end returns to the shaft's own end, so length and
        # angle describe the drawn line (the apex gave 108.1 pt / 2.65 deg).
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)
        assert p["length"] == pytest.approx(100.0, abs=0.01)
        assert p["angle_deg"] == pytest.approx(0.0, abs=0.01)
        assert p["evidence"]["arrow_off_spine"] is True
        assert p["confidence"] <= q._UNCORROBORATED_CAP

    def test_an_angled_arrow_on_the_spine_still_passes(self):
        # The spine test is independent of the direction test: an arrow
        # drafted at an angle to its line passes as long as the line runs
        # into it. 20 deg is inside the 30 deg direction cone.
        d = (math.cos(math.radians(20.0)), math.sin(math.radians(20.0)))
        att = q._arrow_attach(
            [tuple(v) for v in _arrow_chevron((100.0, 0.0), d).vertices],
            "triangle", (1.0, 0.0), (100.0, 0.0), MAS)
        assert att.on_spine is True
        assert att.state == "ok"


# ---------------------------------------------------------------------------
# Extent, not shaft length: what the length floor is a floor ON
# ---------------------------------------------------------------------------

class TestExtentFloor:
    def test_letter_stroke_between_two_glyph_chevrons_is_not_a_dimension(self):
        # A 5.2 pt stroke between two 5.0 pt chevrons: two shape-verified
        # triangles, so the retired both-ends-are-triangles bypass called
        # it a confidence-1.0 dimension. Its EXTENT is 5.2 pt against an
        # 18.62 pt floor.
        ents = [
            _arrow_chevron((100.0, 300.0), (-1.0, 0.0), leg=5.0, base=1.7),
            Line(start=(100.0, 300.0), end=(105.2, 300.0)),
            _arrow_chevron((105.2, 300.0), (1.0, 0.0), leg=5.0, base=1.7),
            Line(start=(99.0, 297.0), end=(99.0, 303.0)),
            Line(start=(106.2, 297.0), end=(106.2, 303.0)),
        ]
        assert [p for p in q.find_dimensions(_ir(ents),
                                             max_arrowhead_size=MAS,
                                             min_confidence=0.5)
                if p["evidence"]["path"] == "continuous"] == []
        # CAP, DON'T DELETE: it stays visible in the observational band,
        # flagged, and is never called.
        deep = [p for p in q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                             min_confidence=0.0)
                if p["evidence"]["path"] == "continuous"]
        assert len(deep) == 1
        assert deep[0]["evidence"]["below_extent_floor"] is True
        assert deep[0]["confidence"] <= q._UNCORROBORATED_CAP

    def test_a_narrow_real_dimension_is_capped_not_dropped(self):
        # An 18.0 pt both-triangle dimension: witnessed, texted, drawn
        # exactly like the 'T=' construct but 0.6 pt under the 18.62 pt
        # floor. Dropping it at every min_confidence is the trade the cap
        # ladder exists to avoid.
        y = 158.6
        h = math.sqrt(4.0 ** 2 - (1.35 / 2) ** 2)
        ents = [
            Line(start=(500.0, y - 12), end=(500.0, y + 12)),
            Line(start=(518.0, y - 12), end=(518.0, y + 12)),
            Line(start=(500.0 + h, y), end=(518.0 - h, y)),
            _closed_triangle((500.0, y), (-1.0, 0.0)),
            _closed_triangle((518.0, y), (1.0, 0.0)),
            TextItem(content="18", position=(509.0, y + 4), height=3.0),
        ]
        deep = [p for p in q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                             min_confidence=0.0)
                if p["evidence"]["path"] == "continuous"]
        assert len(deep) == 1
        assert deep[0]["evidence"]["below_extent_floor"] is True
        assert deep[0]["confidence"] == pytest.approx(q._UNCORROBORATED_CAP)
        xs = sorted([deep[0]["end_a_xy"][0], deep[0]["end_b_xy"][0]])
        assert xs == pytest.approx([500.0, 518.0], abs=0.1)

    def test_the_floor_measures_the_published_ends(self):
        # The 'T=' construct survives because its EXTENT (24.1 pt between
        # defpoints) clears the floor its 9.7 pt shaft does not — the
        # regression this floor's rewrite had to preserve.
        ir = _ir(_t_dim_entities())
        cont = [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                             min_confidence=0.5)
                if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        assert cont[0]["length"] == pytest.approx(24.1, abs=0.1)


# ---------------------------------------------------------------------------
# The pointed/blunt criterion: invariances and scope
# ---------------------------------------------------------------------------

def _regular_polygon(n, cx, cy, r=2.0, phase=0.0):
    return Polyline(vertices=[(cx + r * math.cos(phase + 2 * math.pi * k / n),
                               cy + r * math.sin(phase + 2 * math.pi * k / n))
                              for k in range(n)], closed=True)


def _kite_arrowhead(apex, direction, h=7.2, base=2.4, notch=0.65):
    """The 4-vertex filled arrow: apex, two barbs, a rear notch."""
    dx, dy = direction
    px, py = -dy, dx
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[apex,
                              (bx + px * base / 2, by + py * base / 2),
                              (apex[0] - dx * h * notch,
                               apex[1] - dy * h * notch),
                              (bx - px * base / 2, by - py * base / 2)],
                    closed=True)


def _pointed(entity_or_verts):
    verts = getattr(entity_or_verts, "vertices", entity_or_verts)
    return q._arrow_geometry([tuple(v) for v in verts]).pointed


class TestPointedCriterion:
    """The criterion is the apex vote's own MARGIN, and the watershed is
    derived from the candidate gate's slenderness rule rather than typed
    (:data:`q._APEX_VOTE_MARGIN`). The three axes the earlier
    corner-angle-vs-regular-form test failed on — vertex order,
    translation, and sitting exactly ON the watershed — are pinned here."""

    def test_the_watershed_is_derived_not_typed(self):
        assert q._APEX_VOTE_MARGIN == pytest.approx(
            q._limiting_apex_margin(q._OPEN3_BASE_RATIO))
        assert 0.3 < q._APEX_VOTE_MARGIN < 0.4

    def test_a_box_is_blunt_and_a_real_arrowhead_is_not(self):
        assert _pointed(_box_terminator(500.0, 158.6)) is False
        assert _pointed(_arrow_chevron((100.0, 100.0), (-1.0, 0.0))) is True
        assert _pointed(_kite_arrowhead((100.0, 100.0), (-1.0, 0.0))) is True

    def test_order_invariant(self):
        # A diamond flipped between 'contradicted' and 'ok' under vertex
        # reversal, because the corner-angle test read a TIED apex vote.
        diamond = [(0.0, 0.0), (3.0, 1.4), (6.0, 0.0), (3.0, -1.4)]
        assert _pointed(diamond) is False
        assert _pointed(list(reversed(diamond))) is False
        for k in range(4):
            assert _pointed(diamond[k:] + diamond[:k]) is False
        arrow = [tuple(v) for v in
                 _arrow_chevron((100.0, 100.0), (-1.0, 0.0)).vertices]
        assert _pointed(list(reversed(arrow))) is True

    def test_translation_invariant(self):
        # A pentagon dot flipped classification when moved into sheet
        # coordinates (floating point broke the exact tie differently).
        for cx, cy in [(0.0, 0.0), (500.0, 700.0), (1e4, 1e4)]:
            assert _pointed(_regular_polygon(5, cx, cy)) is False

    @pytest.mark.parametrize("k", range(25))
    def test_rotation_invariant(self, k):
        phase = 2 * math.pi * k / 25
        assert _pointed(_regular_polygon(5, 500.0, 700.0,
                                         phase=phase)) is False
        assert _pointed(_regular_polygon(4, 500.0, 700.0,
                                         phase=phase)) is False

    def test_plot_jitter_does_not_flip_a_box(self):
        # 2% jitter took a box-terminated dimension from 0.907 to 0.250.
        base = [tuple(v) for v in _box_terminator(500.0, 158.6).vertices]
        for k, (x, y) in enumerate(base):
            jitter = [(px + 0.02 * 1.3 * math.cos(3.0 * (i + k)),
                       py + 0.02 * 1.3 * math.sin(5.0 * (i + k)))
                      for i, (px, py) in enumerate(base)]
            assert _pointed(jitter) is False

    def test_a_filled_equilateral_triangle_keeps_its_drawn_tip(self):
        # A "Datum triangle filled" is a REAL drafted arrowhead. Landing
        # exactly on the old watershed called it blunt, which moved its
        # published defpoint 4.04 pt off its own drawn corner. Three
        # vertices are the whole shape: the tip stays a drawn vertex.
        tri = _regular_polygon(3, 300.0, 300.0, r=4.0)
        g = q._arrow_geometry([tuple(v) for v in tri.vertices])
        assert g.pointed is True
        assert any(math.hypot(g.apex[0] - v[0], g.apex[1] - v[1]) < 1e-9
                   for v in tri.vertices)

    def test_the_branch_only_ever_sees_quad_like_terminators(self):
        # The docstring used to claim dot and oblique-tick coverage. Both
        # are structurally unreachable: a dot ingests as a Circle or a
        # fill cluster, an oblique tick as a 2-vertex Line, and neither
        # is an _arrowhead_candidates candidate at all.
        ents = [Circle(center=(100.0, 100.0), radius=1.4),
                Line(start=(200.0, 100.0), end=(202.0, 103.0))]
        assert list(q._arrowhead_candidates(_ir(ents), MAS)) == []


class TestBluntCapsAreDeterministic:
    def test_a_blunt_leader_candidate_is_capped_without_a_coin_flip(self):
        # The signed value of a tipless shape is whichever corner the
        # tied vote elected, so it is withheld and the cap is outright.
        ents = [_box_terminator(100.0, 100.0),
                Line(start=(101.3, 100.0), end=(160.0, 100.0)),
                TextItem(content="TOP OF WALL", position=(164.0, 100.0),
                         height=3.0)]
        leads = q.find_leaders(_ir(ents), max_arrowhead_size=MAS,
                               min_confidence=0.0)
        assert leads
        p = leads[0]
        assert p["evidence"]["blunt_terminator"] is True
        assert "signed_axis_alignment" not in p["evidence"]
        assert p["confidence"] <= q._UNCORROBORATED_CAP
