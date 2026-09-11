"""Tests for find_dimensions v2: split-shaft pairing + no-text discipline.

Geometry in these fixtures is copied (rounded) from the Mecklenburg
ground-truth plots — the verified real plotted anatomy, not invented
shapes: 3-vertex OPEN arrow chains (base+leg flavor from the native
dimensions, chevron flavor from the native leaders), split half-shafts
around a centered text gap (sheet 3001), and the outside-arrows narrow
style (sheet 10.31A). Sizes are in page points at the real plot scale, so
``max_arrowhead_size`` is passed explicitly (the statistic-derived default
needs a whole sheet's linework to be meaningful).
"""

from __future__ import annotations

import pytest

from planlens.ir import queries as q
from planlens.ir.results import DrawingIR, Line, Polyline, TextItem

MAS = 9.31  # the validation sheets' arrowhead scale (points)


def _ir(entities, with_text=None):
    ir = DrawingIR(units="pt", coordinate_space="page", origin="bottom_left",
                   source="pdf_vector", width=612.0, height=792.0)
    for i, e in enumerate(entities):
        e.id = f"e{i}"
        ir.add(e)
    if with_text:
        for j, (content, pos) in enumerate(with_text):
            ir.add(TextItem(id=f"t{j}", content=content, position=pos,
                            height=8.0, source="pdf_vector", confidence=1.0))
    return ir


def _arrow_base_leg(apex, direction, leg=7.3, base=2.4):
    """3-vertex OPEN chain, base+leg flavor: [base-corner, base-corner,
    apex] — the native-dimension plot style (one leg implied)."""
    dx, dy = direction
    px, py = -dy, dx
    import math
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[(bx + px * base / 2, by + py * base / 2),
                              (bx - px * base / 2, by - py * base / 2),
                              apex], closed=False)


def _arrow_chevron(apex, direction, leg=7.3, base=2.4):
    """3-vertex OPEN chain, chevron flavor: [barb, apex, barb] — the
    native-leader plot style (base implied)."""
    dx, dy = direction
    px, py = -dy, dx
    import math
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return Polyline(vertices=[(bx + px * base / 2, by + py * base / 2),
                              apex,
                              (bx - px * base / 2, by - py * base / 2)],
                    closed=False)


# ---------------------------------------------------------------------------
# Arrowhead representation
# ---------------------------------------------------------------------------

class TestOpenTriangleArrowheads:
    def test_base_leg_flavor_accepted(self):
        ir = _ir([_arrow_base_leg((100.0, 100.0), (1.0, 0.0))])
        cands = list(q._arrowhead_candidates(ir, MAS))
        assert len(cands) == 1

    def test_chevron_flavor_accepted(self):
        ir = _ir([_arrow_chevron((100.0, 100.0), (1.0, 0.0))])
        cands = list(q._arrowhead_candidates(ir, MAS))
        assert len(cands) == 1

    def test_sub_scale_junk_rejected(self):
        # A stipple-scale V: right shape, but far below arrowhead scale
        # (the measured junk population on the stippled sheets).
        ir = _ir([_arrow_chevron((100.0, 100.0), (1.0, 0.0),
                                 leg=1.0, base=0.33)])
        assert list(q._arrowhead_candidates(ir, MAS)) == []

    def test_blunt_open_v_rejected(self):
        # Near-equilateral open V (a glyph stroke, not an arrow: every
        # measured real arrow has base/leg = 0.33; the gate allows 0.55).
        ir = _ir([_arrow_chevron((100.0, 100.0), (1.0, 0.0),
                                 leg=7.3, base=7.0)])
        assert list(q._arrowhead_candidates(ir, MAS)) == []

    def test_lopsided_legs_rejected(self):
        ir = _ir([Polyline(vertices=[(92.0, 102.0), (92.0, 99.6),
                                     (100.0, 100.0)], closed=False)])
        # legs 8.24 vs 8.01 pass; make one leg much shorter:
        ir2 = _ir([Polyline(vertices=[(96.0, 103.0), (95.0, 99.6),
                                      (100.0, 100.0)], closed=False)])
        assert len(list(q._arrowhead_candidates(ir2, MAS))) == 0

    def test_oversize_allowance_for_shape_verified(self):
        # 1.2x the estimated scale still accepted (the 21.01 finding: the
        # sheet-statistic estimate ran under the real plotted arrows).
        ir = _ir([_arrow_base_leg((100.0, 100.0), (1.0, 0.0),
                                  leg=11.0, base=3.6)])
        assert len(list(q._arrowhead_candidates(ir, MAS))) == 1


# ---------------------------------------------------------------------------
# Split-shaft pairing (geometry from sheet 3001, dimension "5' MAX FILL")
# ---------------------------------------------------------------------------

def _outward_split_entities():
    """Left/right halves + outward arrows + witnesses, horizontal at y=471."""
    y = 471.24
    return [
        # left: witness, arrow (apex at 234.66 pointing -x), half-shaft
        Line(start=(234.66, y + 7.2), end=(234.66, y - 7.2)),
        _arrow_base_leg((234.66, y), (-1.0, 0.0)),
        Line(start=(241.86, y), end=(281.94, y)),
        # right: half-shaft, arrow (apex at 317.16 pointing +x), witness
        Line(start=(296.04, y), end=(309.96, y)),
        _arrow_base_leg((317.16, y), (1.0, 0.0)),
        Line(start=(317.16, y + 7.2), end=(317.16, y - 7.2)),
    ]


class TestSplitShaftOutward:
    def test_detected_no_text(self):
        ir = _ir(_outward_split_entities())
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        splits = [p for p in dims
                  if p["evidence"]["path"] == "split_shaft"]
        assert len(splits) == 1
        p = splits[0]
        assert p["evidence"]["arrangement"] == "outward"
        # Ends are the arrow APEX points (the CAD defpoints).
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        assert xs[0] == pytest.approx(234.66, abs=0.1)
        assert xs[1] == pytest.approx(317.16, abs=0.1)
        assert p["confidence"] >= 0.5  # witnessed both ends
        assert p["evidence"]["n_extension_ends"] == 2

    def test_text_in_gap_scores(self):
        ir = _ir(_outward_split_entities(),
                 with_text=[("5' MAX FILL", (289.0, 471.3))])
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        p = next(p for p in dims
                 if p["evidence"]["path"] == "split_shaft")
        assert p["text"] == "5' MAX FILL"
        assert p["confidence"] >= 0.5

    def test_lone_half_no_proposal(self):
        # One half only: no partner -> no split proposal.
        ents = _outward_split_entities()[:3]
        ir = _ir(ents)
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        assert [p for p in dims
                if p["evidence"]["path"] == "split_shaft"] == []


# ---------------------------------------------------------------------------
# Inward (outside-arrows) style — geometry from sheet 10.31A "WIDTH VARIES"
# ---------------------------------------------------------------------------

def _inward_entities(with_witnesses=True):
    y = 287.04
    ents = [
        # left: trailing half-shaft, arrow apex at 481.32 pointing +x
        Line(start=(464.04, y), end=(472.68, y)),
        _arrow_base_leg((481.32, y), (1.0, 0.0), leg=8.75, base=2.88),
        # right: arrow apex at 536.76 pointing -x, trailing half-shaft
        _arrow_base_leg((536.76, y), (-1.0, 0.0), leg=8.75, base=2.88),
        Line(start=(545.4, y), end=(554.04, y)),
    ]
    if with_witnesses:
        ents += [Line(start=(481.32, y - 16.8), end=(481.32, y + 6.48)),
                 Line(start=(536.76, y - 16.8), end=(536.76, y + 6.48))]
    return ents


class TestSplitShaftInward:
    def test_detected_with_witnesses(self):
        ir = _ir(_inward_entities())
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        splits = [p for p in dims
                  if p["evidence"]["path"] == "split_shaft"]
        assert len(splits) == 1
        p = splits[0]
        assert p["evidence"]["arrangement"] == "inward"
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        assert xs[0] == pytest.approx(481.32, abs=0.1)
        assert xs[1] == pytest.approx(536.76, abs=0.1)

    def test_dropped_without_witnesses_on_no_text_sheet(self):
        # The inward style's wide span is licensed by its witness lines;
        # without them (and without text) the pair must NOT surface.
        ir = _ir(_inward_entities(with_witnesses=False))
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        assert [p for p in dims
                if p["evidence"]["path"] == "split_shaft"] == []


# ---------------------------------------------------------------------------
# No-text discipline for the continuous leg
# ---------------------------------------------------------------------------

def _continuous_entities(with_witnesses):
    y = 398.22
    ents = [
        _arrow_base_leg((517.98, y), (1.0, 0.0)),
        Line(start=(425.34, y), end=(510.78, y)),
        _arrow_base_leg((418.14, y), (-1.0, 0.0)),
    ]
    if with_witnesses:
        ents += [Line(start=(510.78, y - 1.2), end=(510.78, y + 7.2)),
                 Line(start=(425.34, y - 1.2), end=(425.34, y + 7.2))]
    return ents


class TestNoTextContinuousCap:
    def test_witnessed_continuous_scores_high(self):
        ir = _ir(_continuous_entities(with_witnesses=True))
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        cont = [p for p in dims if p["evidence"]["path"] == "continuous"]
        assert cont and cont[0]["confidence"] > 0.5
        assert cont[0]["evidence"]["n_extension_ends"] == 2

    def test_unwitnessed_continuous_capped(self):
        ir = _ir(_continuous_entities(with_witnesses=False))
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        cont = [p for p in dims if p["evidence"]["path"] == "continuous"]
        assert cont  # still proposed...
        assert cont[0]["confidence"] <= q._UNCORROBORATED_CAP  # ...capped

    def test_micro_fragments_are_not_witnesses(self):
        # Stipple-scale fragments perpendicular at the tips must not count
        # as witness lines (the measured 3001 failure: 392 sub-3-pt
        # "witnesses" corroborating junk).
        y = 398.22
        ents = _continuous_entities(with_witnesses=False)
        ents += [Line(start=(510.78, y - 0.5), end=(510.78, y + 0.5)),
                 Line(start=(425.34, y - 0.5), end=(425.34, y + 0.5))]
        ir = _ir(ents)
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        cont = [p for p in dims if p["evidence"]["path"] == "continuous"]
        assert cont
        assert cont[0]["evidence"]["n_extension_ends"] == 0
        assert cont[0]["confidence"] <= q._UNCORROBORATED_CAP


class TestArrowAttachment:
    def test_far_arrow_does_not_hijack_an_end(self):
        # An arrow 1.4 arrow-lengths from a shaft end must not attach to
        # it (the measured 3001 failure: glyph junk 13.3 pt from a half's
        # inner end flipped it to "two-arrowed", killing the pair).
        ents = _outward_split_entities()
        # Junk arrow floating 13 pt past the right half's inner end.
        ents.append(_arrow_base_leg((283.0, 484.0), (0.2, 0.98)))
        ir = _ir(ents)
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS)
        splits = [p for p in dims
                  if p["evidence"]["path"] == "split_shaft"]
        assert len(splits) == 1


# ---------------------------------------------------------------------------
# Off-spine halves: found on drawn geometry, CAPPED — never refused
# ---------------------------------------------------------------------------

def _split_with_rotated_left_arrow(off_deg):
    """The 3001 outward split with its LEFT arrow rotated about its apex.

    Rotating about the apex is what pushes the shaft tip off the
    arrowhead's own spine while leaving the defpoint where it is; the
    spine tolerance is the arrow's half-width, so the refusal that used
    to follow broke at ``asin(base / 2 leg)`` — 4.8 deg for the
    slenderest heads, 16 deg for the bluntest. The slenderer and more
    arrow-like the head, the tighter the refusal: an inverted incentive.
    """
    import math
    y = 471.24
    a = math.radians(180.0 + off_deg)
    return [
        Line(start=(234.66, y + 7.2), end=(234.66, y - 7.2)),
        _arrow_base_leg((234.66, y), (math.cos(a), math.sin(a))),
        Line(start=(241.86, y), end=(281.94, y)),
        Line(start=(296.04, y), end=(309.96, y)),
        _arrow_base_leg((317.16, y), (1.0, 0.0)),
        Line(start=(317.16, y + 7.2), end=(317.16, y - 7.2)),
    ]


class TestOffSpineHalfIsCappedNotRefused:
    def _split(self, off_deg, min_confidence=0.0):
        dims = q.find_dimensions(_ir(_split_with_rotated_left_arrow(off_deg)),
                                 max_arrowhead_size=MAS,
                                 min_confidence=min_confidence)
        return [p for p in dims if p["evidence"]["path"] == "split_shaft"]

    @pytest.mark.parametrize("off_deg", [10, 15, 20, 25, 30])
    def test_a_real_split_dimension_is_still_proposed(self, off_deg):
        # Refusing the half deleted these at EVERY min_confidence.
        sp = self._split(off_deg)
        assert len(sp) == 1
        assert sp[0]["evidence"]["arrow_off_spine"] is True
        assert sp[0]["confidence"] <= q._UNCORROBORATED_CAP

    def test_the_off_spine_half_publishes_the_projected_defpoint(self):
        # This leg's arrows sit OUTSIDE their half-shafts, so the CAD
        # defpoint is genuinely BEYOND the shaft's tip — the witness line
        # marks it at 234.66 while the shaft stops at 241.86. Substituting
        # the tip therefore moved the published point by a whole arrow
        # length (7.2 pt) on a construct whose defpoint was never in
        # doubt. Projecting the apex on the terminal ray keeps the axial
        # measurement and discards only the lateral wobble.
        p = self._split(20)[0]
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        assert xs[0] == pytest.approx(234.66, abs=0.1)   # projected apex
        assert xs[1] == pytest.approx(317.16, abs=0.1)   # the sound apex

    def test_a_crooked_arrow_reports_the_same_defpoint_as_a_straight_one(self):
        # The point of projecting rather than substituting: drafting
        # wobble must not move the measurement.
        crooked = sorted(self._split(20)[0][k][0]
                         for k in ("end_a_xy", "end_b_xy"))
        straight = sorted(self._split(0)[0][k][0]
                          for k in ("end_a_xy", "end_b_xy"))
        assert crooked == pytest.approx(straight, abs=0.1)

    def test_an_on_axis_split_is_unaffected(self):
        p = self._split(0)[0]
        assert "arrow_off_spine" not in p["evidence"]
        assert p["confidence"] >= 0.5
        xs = sorted([p["end_a_xy"][0], p["end_b_xy"][0]])
        assert xs[0] == pytest.approx(234.66, abs=0.1)


# ---------------------------------------------------------------------------
# The contradicted cap in the split leg is REACHABLE, not a docstring promise
# ---------------------------------------------------------------------------

def _split_with_both_arrows_contradicted():
    """Two collinear halves whose only end candidates point ACROSS their
    shafts — letterform chevrons parked where the arrows would be."""
    y = 471.24
    return [
        _arrow_base_leg((238.0, y + 3.0), (0.0, 1.0)),
        Line(start=(241.86, y), end=(281.94, y)),
        Line(start=(296.04, y), end=(309.96, y)),
        _arrow_base_leg((313.8, y + 3.0), (0.0, 1.0)),
    ]


class TestContradictedCapReachesTheSplitPairing:
    def _split(self, min_confidence):
        dims = q.find_dimensions(_ir(_split_with_both_arrows_contradicted()),
                                 max_arrowhead_size=MAS,
                                 min_confidence=min_confidence)
        return [p for p in dims if p["evidence"]["path"] == "split_shaft"]

    def test_absent_from_the_observational_band(self):
        assert self._split(0.5) == []
        assert self._split(0.3) == []

    def test_present_and_flagged_below_the_contradicted_cap(self):
        deep = self._split(0.0)
        assert len(deep) == 1
        assert deep[0]["evidence"]["arrow_direction_violation"] is True
        assert deep[0]["confidence"] <= q._CONTRADICTED_CAP

    def test_the_state_actually_travels_with_the_half(self):
        # The bug this pins: find_dimensions used to build `halves` only
        # from non-contradicted ends, so hi/hj state was never
        # "contradicted" and the cap below was unreachable dead code
        # while the docstring advertised it (measured with a spy over all
        # ten corpus sheets: 396 halves offered, zero contradicted).
        seen = []
        real = q._pair_split_halves

        def spy(halves, *a, **k):
            seen.extend(h["state"] for h in halves)
            return real(halves, *a, **k)

        q._pair_split_halves = spy
        try:
            q.find_dimensions(_ir(_split_with_both_arrows_contradicted()),
                              max_arrowhead_size=MAS, min_confidence=0.0)
        finally:
            q._pair_split_halves = real
        assert "contradicted" in seen
