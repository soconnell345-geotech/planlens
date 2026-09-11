"""Tipless terminators: the coordinate clamp and the oriented/blunt policy.

Round-4 verification finding 1: a 6 pt diamond (or flat-tipped) terminator
at each end of a 100 pt witnessed, texted dimension published
``end_b=[97, 0]``, length 94.0, confidence 0.45 — the blunt branch
projected the shape's CENTROID on the shaft ray (half a terminator behind
the line's own end) and the unconditional blunt cap held the construct
below the call threshold. v0.1.0 and the committed tip both gave
``[100, 0]`` / 100.0 / 0.952.

Two halves, pinned separately:

- the COORDINATE: the projection never falls behind the shaft tip
  (:func:`planlens.ir.queries._arrow_attach`). A centred box block is
  unaffected (its centroid IS the defpoint); a diamond drawn inside the
  line now publishes the line's end;
- the POLICY: a tipless shape whose long axis lies along the line is
  ``"oriented"`` and takes a fill cluster's standing (admitted, scored
  sign-blind, never ranked above a directional arrowhead); an isotropic
  shape stays ``"blunt"`` and capped. Both directions of that trade are
  here — the rectangle scene the cap exists for, and the oblong scene
  the oriented rank newly admits.

Geometry conventions follow :mod:`planlens.ir.tests.test_arrow_direction`
(9.31 pt validation-sheet arrowhead scale).
"""

from __future__ import annotations

import math

import pytest

from planlens.ir import queries as q
from planlens.ir.results import Line, Polyline, TextItem

from planlens.ir.tests.test_arrow_direction import (
    MAS, _arrow_base_leg, _box_terminator, _ir)
from planlens.ir.tests.test_text_bearing_scenes import (
    _rect, leader_beside_a_blunt_detail_line)


# ---------------------------------------------------------------------------
# builders (the round-4 verification's own shapes)
# ---------------------------------------------------------------------------

def diamond(apex, d, leg=6.0, w=2.4, rot=0):
    """Rhombus terminator: far corner at ``apex``, long axis along ``d``.
    ``rot`` rotates the VERTEX ORDER (a plotter starts anywhere)."""
    dx, dy = d
    px, py = -dy, dx
    v = [apex,
         (apex[0] - dx * leg / 2 + px * w / 2,
          apex[1] - dy * leg / 2 + py * w / 2),
         (apex[0] - dx * leg, apex[1] - dy * leg),
         (apex[0] - dx * leg / 2 - px * w / 2,
          apex[1] - dy * leg / 2 - py * w / 2)]
    return Polyline(vertices=v[rot:] + v[:rot], closed=True)


def trapezoid(apex, d, leg=6.0, tip_w=0.8, base=3.0):
    """Truncated closed arrow ('flat tip'): flat end at ``apex``."""
    dx, dy = d
    px, py = -dy, dx
    bx, by = apex[0] - dx * leg, apex[1] - dy * leg
    return Polyline(vertices=[
        (apex[0] + px * tip_w / 2, apex[1] + py * tip_w / 2),
        (apex[0] - px * tip_w / 2, apex[1] - py * tip_w / 2),
        (bx - px * base / 2, by - py * base / 2),
        (bx + px * base / 2, by + py * base / 2)], closed=True)


def core():
    """A 100 pt dimension: shaft, two witness lines, value text.
    TRUTH: end_a=[0,0], end_b=[100,0], length 100."""
    return [Line(start=(0.0, 0.0), end=(100.0, 0.0)),
            Line(start=(0.0, -5.0), end=(0.0, 25.0)),
            Line(start=(100.0, -5.0), end=(100.0, 25.0)),
            TextItem(content="100'", position=(50.0, 6.0), height=6.0)]


def _cont(ents, min_confidence):
    return [p for p in q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                         min_confidence=min_confidence)
            if p["evidence"]["path"] == "continuous"]


def _attach(shape, sdir=(1.0, 0.0), tip=(100.0, 0.0)):
    return q._arrow_attach([tuple(v) for v in shape.vertices], "triangle",
                           sdir, tip, MAS)


# ---------------------------------------------------------------------------
# The coordinate half
# ---------------------------------------------------------------------------

class TestTheProjectionNeverFallsBehindTheTip:
    def test_a_diamond_inside_the_line_publishes_the_lines_end(self):
        att = _attach(diamond((100.0, 0.0), (1.0, 0.0)))
        assert att.apex == pytest.approx((100.0, 0.0))

    def test_a_flat_tipped_arrow_inside_the_line_does_too(self):
        att = _attach(trapezoid((100.0, 0.0), (1.0, 0.0)))
        assert att.apex == pytest.approx((100.0, 0.0))

    def test_a_centred_box_is_unaffected(self):
        # The box block is inserted centred AT the defpoint, so its
        # centroid is the defpoint and the clamp is inert.
        att = _attach(_box_terminator(100.0, 0.0))
        assert att.apex == pytest.approx((100.0, 0.0))
        assert att.state == "blunt"

    @pytest.mark.parametrize("rot", range(4))
    def test_the_coordinate_is_vertex_order_invariant(self, rot):
        # The elected apex of a tied vote follows vertex order; the
        # clamped centroid projection does not.
        att = _attach(diamond((100.0, 0.0), (1.0, 0.0), rot=rot))
        assert att.apex == pytest.approx((100.0, 0.0))
        assert att.state == "oriented"

    def test_a_shape_wholly_beyond_the_tip_publishes_its_projected_centroid(
            self):
        # DOCUMENTED, not endorsed: in an arrows-outside placement a
        # centred block reads exactly and a tip-at-defpoint block reads
        # half its own length short. Nothing in the geometry says which.
        att = _attach(diamond((106.0, 0.0), (1.0, 0.0)))
        assert att.apex == pytest.approx((103.0, 0.0))


# ---------------------------------------------------------------------------
# The policy half — oriented vs blunt
# ---------------------------------------------------------------------------

class TestOrientedVersusBlunt:
    def test_a_diamond_along_the_line_is_oriented(self):
        att = _attach(diamond((100.0, 0.0), (1.0, 0.0)))
        assert att.state == "oriented"
        assert att.score == pytest.approx(1.0)
        assert att.on_spine is True

    def test_the_same_diamond_across_the_line_is_blunt(self):
        # Its long axis crosses the line at 90 deg: whatever it
        # terminates, it is not this line.
        att = _attach(diamond((100.0, 3.0), (0.0, 1.0)))
        assert att.state == "blunt"

    def test_a_box_is_blunt(self):
        assert _attach(_box_terminator(100.0, 0.0)).state == "blunt"
        assert _attach(_rect(100.0, 0.0)).state == "blunt"

    def test_the_cone_is_the_signed_tests_cone(self):
        # Oriented within 30 deg of the line, blunt beyond — the same
        # cos-30 statement the direction test makes, not a new constant.
        for deg, expect in ((20.0, "oriented"), (40.0, "blunt")):
            a = math.radians(deg)
            att = _attach(diamond((100.0, 0.0), (math.cos(a), math.sin(a))))
            assert att.state == expect, deg

    # -- round-5 verification D3: commensurate with the arrowhead scale,
    #    and tapered toward the end it marks -------------------------------

    @pytest.mark.parametrize("w,h", [(2.0, 0.8), (1.98, 0.96), (3.0, 1.0)])
    def test_a_sub_scale_fragment_is_blunt_however_well_it_lies(self, w, h):
        # Every shape the rank admitted on the corpus without the floor
        # was a 0.6-2 pt SHX glyph fragment (6 of 6). The floor is the
        # open-3 chevron gate's own (diagonal >= 0.5x the arrowhead
        # scale = 4.66 pt at MAS); a diamond at arrow scale clears it.
        frag = Polyline(vertices=[(100.0 - w, -h / 2), (100.0, -h / 2),
                                  (100.0, h / 2), (100.0 - w, h / 2)],
                        closed=True)
        assert math.hypot(w, h) < q._MIN_ARROW_SIZE_SCALE * MAS
        assert _attach(frag).state == "blunt"
        small_diamond = diamond((100.0, 0.0), (1.0, 0.0), leg=3.0, w=1.2)
        assert _attach(small_diamond).state == "blunt"
        assert _attach(diamond((100.0, 0.0), (1.0, 0.0))).state == "oriented"

    @pytest.mark.parametrize("deg", [0.0, 10.0, 25.0, 29.0])
    def test_a_rectangle_never_tapers_so_it_is_never_oriented(self, deg):
        # A 6x3 block lying along the line (or turned up to the cone's
        # edge) is a scale-bar block or a tile, not a terminator: it does
        # not narrow toward the end it would mark. Measured in the
        # shape's own frame, so turning it does not fake a taper.
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        loc = [(-3.0, -1.5), (3.0, -1.5), (3.0, 1.5), (-3.0, 1.5)]
        rect = Polyline(vertices=[(97.0 + x * c - y * s, x * s + y * c)
                                  for x, y in loc], closed=True)
        assert _attach(rect).state == "blunt"

    def test_a_flat_tip_must_be_slender_to_count(self):
        # The marking end may be at most _OPEN3_BASE_RATIO (0.55) of the
        # body's half-width — the chevron gate's own base-to-leg
        # slenderness. tip 0.8 of base 3.0 is a truncated arrow; tip 2.0
        # of base 3.0 is a trapezoidal tile.
        assert _attach(trapezoid((100.0, 0.0), (1.0, 0.0),
                                 tip_w=0.8, base=3.0)).state == "oriented"
        assert _attach(trapezoid((100.0, 0.0), (1.0, 0.0),
                                 tip_w=2.0, base=3.0)).state == "blunt"

    def test_a_crooked_diamond_still_tapers(self):
        # Taper is judged along the shape's OWN axis: a diamond drafted
        # 20 deg off the line narrows to its apex exactly as a straight
        # one does. (Judged in the shaft's frame it read as untapered.)
        a = math.radians(20.0)
        att = _attach(diamond((100.0, 0.0), (math.cos(a), math.sin(a))))
        assert att.state == "oriented"


def scale_bar(block_w, block_h=3.0):
    """A graphic scale bar (round-5 verification D3, P6): 100 pt
    baseline, filled end blocks INSIDE the line, ticks every 25, labels
    0 / 50 / 100 — on most civil sheets."""
    ents = [Line(start=(0.0, 0.0), end=(100.0, 0.0))]
    for cx in (block_w / 2, 100.0 - block_w / 2):
        ents.append(Polyline(vertices=[
            (cx - block_w / 2, -block_h / 2), (cx + block_w / 2, -block_h / 2),
            (cx + block_w / 2, block_h / 2), (cx - block_w / 2, block_h / 2)],
            closed=True))
    ents += [Line(start=(x, -4.0), end=(x, 4.0))
             for x in (0.0, 25.0, 50.0, 75.0, 100.0)]
    ents += [TextItem(content=t, position=(x, 7.0), height=3.0)
             for t, x in (("0", 0.0), ("50", 50.0), ("100", 100.0))]
    return ents


class TestAScaleBarIsNotADimension:
    """Blocks at both ends inside the line, periodic ticks, labels: the
    oriented rank read the 2:1-block version as a 0.944 dimension. The
    blocks do not taper, so they are blunt and the construct is capped
    (the tip returned nothing; round 4 capped it at 0.45)."""

    @pytest.mark.parametrize("block_w", [6.0, 3.0])
    def test_not_called_at_the_default(self, block_w):
        ents = scale_bar(block_w)
        assert _cont(ents, 0.5) == []
        deep = _cont(ents, 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert deep[0]["evidence"]["blunt_terminators"] is True
        assert deep[0]["arrowhead_ids"] == []

    def test_oversized_blocks_are_not_candidates_at_all(self):
        assert _cont(scale_bar(20.0), 0.0) == []

    def test_a_diamond_dimension_of_the_same_span_is_still_called(self):
        # The discriminator is the taper, not the anatomy around it.
        p = _cont(core() + [diamond((0.0, 0.0), (-1.0, 0.0)),
                            diamond((100.0, 0.0), (1.0, 0.0))], 0.5)
        assert len(p) == 1 and p[0]["end_b_xy"] == pytest.approx([100.0, 0.0])


class TestAGlyphFragmentCannotFoundACallOnANoTextSheet:
    """The corpus's own class (round-5 verification D3, P5): a true
    chevron at one end, an oblong glyph fragment at the other, witnesses
    at both, no text. Without the size floor this CALLED at 1.0."""

    def _frag(self, w=2.0, h=0.8, cx=100.6):
        return Polyline(vertices=[(cx - w / 2, -h / 2), (cx + w / 2, -h / 2),
                                  (cx + w / 2, h / 2), (cx - w / 2, h / 2)],
                        closed=True)

    def test_continuous_leg(self):
        ents = [e for e in core() if not isinstance(e, TextItem)]
        ents += [_arrow_base_leg((0.0, 0.0), (-1.0, 0.0)), self._frag()]
        assert _cont(ents, 0.5) == []
        deep = _cont(ents, 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert deep[0]["evidence"]["blunt_terminator_ids"] == ["e4"]

    def test_split_leg(self):
        h = math.sqrt(7.3 ** 2 - 1.2 ** 2)
        ents = [Line(start=(0.0, -5.0), end=(0.0, 25.0)),
                Line(start=(100.0, -5.0), end=(100.0, 25.0)),
                Line(start=(h, 0.0), end=(40.0, 0.0)),
                _arrow_base_leg((0.0, 0.0), (-1.0, 0.0)),
                Line(start=(60.0, 0.0), end=(98.0, 0.0)),
                self._frag(cx=99.0)]
        split = [p for p in q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                              min_confidence=0.0)
                 if p["evidence"]["path"] == "split_shaft"]
        assert split and split[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert all(p["confidence"] < 0.5 for p in split)


class TestDiamondAndFlatTipDimensionsAreCalled:
    """The round-4 verification's finding-1 fixtures, at the default."""

    @pytest.mark.parametrize("mk", [diamond, trapezoid])
    def test_called_at_the_default_with_the_exact_defpoints(self, mk):
        ents = core() + [mk((0.0, 0.0), (-1.0, 0.0)),
                         mk((100.0, 0.0), (1.0, 0.0))]
        called = _cont(ents, 0.5)
        assert len(called) == 1
        p = called[0]
        assert p["end_a_xy"] == pytest.approx([0.0, 0.0])
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0])
        assert p["length"] == pytest.approx(100.0)
        # Same confidence as the slender-triangle control (0.952): the
        # sign-blind alignment scores 1.0 along the line.
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)
        assert "blunt_terminators" not in p["evidence"]
        assert "blunt_terminator_ids" not in p["evidence"]
        assert sorted(p["evidence"]["oriented_terminator_ids"]) == ["e4", "e5"]
        assert sorted(p["arrowhead_ids"]) == ["e4", "e5"]

    @pytest.mark.parametrize("rot", range(4))
    def test_vertex_order_does_not_move_the_construct(self, rot):
        ents = core() + [diamond((0.0, 0.0), (-1.0, 0.0), rot=rot),
                         diamond((100.0, 0.0), (1.0, 0.0), rot=rot)]
        p = _cont(ents, 0.5)[0]
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0])
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)

    def test_the_control_triangle_is_unchanged(self):
        ents = core() + [_arrow_base_leg((0.0, 0.0), (-1.0, 0.0), leg=6.0,
                                         base=1.98),
                         _arrow_base_leg((100.0, 0.0), (1.0, 0.0), leg=6.0,
                                         base=1.98)]
        p = _cont(ents, 0.5)[0]
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0])
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)

    def test_a_box_dimension_is_still_never_called(self):
        # The isotropic family keeps the unconditional cap.
        ents = core() + [_box_terminator(0.0, 0.0), _box_terminator(100.0, 0.0)]
        assert _cont(ents, 0.5) == []
        deep = _cont(ents, 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert deep[0]["evidence"]["blunt_terminators"] is True
        assert deep[0]["arrowhead_ids"] == []
        assert deep[0]["end_b_xy"] == pytest.approx([100.0, 0.0])

    def test_one_oriented_end_and_one_box_end_is_capped(self):
        # ANY blunt (isotropic) end holds the construct — the oriented
        # end does not lift it.
        ents = core() + [diamond((0.0, 0.0), (-1.0, 0.0)),
                         _box_terminator(100.0, 0.0)]
        assert _cont(ents, 0.5) == []
        deep = _cont(ents, 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert deep[0]["evidence"]["blunt_terminator_ids"] == ["e5"]
        assert deep[0]["evidence"]["oriented_terminator_ids"] == ["e4"]
        assert deep[0]["arrowhead_ids"] == ["e4"]

    def test_on_a_no_text_sheet_an_oriented_pair_stays_capped(self):
        # "A drawn shape" means a POINTING one: without text, two
        # sign-blind terminators corroborate no more than two clusters.
        ents = [e for e in core() if not isinstance(e, TextItem)]
        ents += [diamond((0.0, 0.0), (-1.0, 0.0)),
                 diamond((100.0, 0.0), (1.0, 0.0))]
        assert _cont(ents, 0.5) == []
        deep = _cont(ents, 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP


# ---------------------------------------------------------------------------
# The trade, both directions
# ---------------------------------------------------------------------------

def _leader_beside_a_detail_line_ending_in(terminator):
    """The round-2 blocker-1 scene with its terminator swapped."""
    ents = leader_beside_a_blunt_detail_line()
    ents[4] = terminator
    return ents


class TestTheCapStillPreventsWhatItWasBuiltFor:
    """A plain rectangle at the far end of a crossing line — the scene
    that founded a 0.915 dimension and stole a real leader's arrowhead."""

    def test_the_rectangle_scene_is_not_called(self):
        assert _cont(leader_beside_a_blunt_detail_line(), 0.5) == []

    def test_and_the_leader_survives(self):
        leads = q.find_leaders(_ir(leader_beside_a_blunt_detail_line()),
                               max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        assert [p["arrowhead_id"] for p in leads] == ["e0"]

    def test_two_rectangles_are_not_a_called_dimension(self):
        ents = [
            Line(start=(0.0, 30.0), end=(60.0, 30.0)),
            _rect(0.0, 30.0), _rect(60.0, 30.0),
            Line(start=(0.0, 24.0), end=(0.0, 36.0)),
            Line(start=(60.0, 24.0), end=(60.0, 36.0)),
            TextItem(content="30'", position=(30.0, 33.0), height=3.0),
        ]
        assert _cont(ents, 0.5) == []


class TestWhatTheOrientedRankNewlyAdmits:
    """The same scene with an OBLONG lying along the crossing line. By
    every observable — a directional arrow at one end pointing along the
    line, an oriented terminator at the other, witnesses at both, a value
    text between — this is a dimension, and it is read as one; the real
    leader whose arrowhead it shares is then dropped by
    ``exclude_dimensions``, exactly as it would be with a chevron at that
    end. This is the cost of the policy, pinned so it cannot move
    silently."""

    def _scene(self):
        return _leader_beside_a_detail_line_ending_in(
            diamond((67.1 + 3.0, 0.0), (1.0, 0.0)))

    def test_the_oblong_scene_is_called(self):
        called = _cont(self._scene(), 0.5)
        assert len(called) == 1
        assert called[0]["evidence"]["oriented_terminator_ids"] == ["e4"]
        assert sorted(called[0]["arrowhead_ids"]) == ["e0", "e4"]

    def test_and_the_leader_is_arbitrated_away_as_with_a_chevron(self):
        oblong = q.find_leaders(_ir(self._scene()), max_arrowhead_size=MAS,
                                exclude_dimensions=True, min_confidence=0.5)
        chevron = q.find_leaders(
            _ir(_leader_beside_a_detail_line_ending_in(
                _arrow_base_leg((67.1 + 7.2, 0.0), (1.0, 0.0)))),
            max_arrowhead_size=MAS, exclude_dimensions=True,
            min_confidence=0.5)
        assert [p["arrowhead_id"] for p in oblong] == []
        assert [p["arrowhead_id"] for p in chevron] == []

    def test_an_oblong_across_the_line_does_not_qualify(self):
        # Rotate the same diamond 90 deg: its axis now crosses the
        # detail line, it is blunt, and the cap holds.
        ents = _leader_beside_a_detail_line_ending_in(
            diamond((67.1, 3.0), (0.0, 1.0)))
        assert _cont(ents, 0.5) == []
        leads = q.find_leaders(_ir(ents), max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        assert [p["arrowhead_id"] for p in leads] == ["e0"]


class TestTheLeaderFlowKeepsCappingTiplessShapes:
    def test_a_diamond_leader_arrowhead_is_capped(self):
        # A dimension terminator marks an END; a leader arrowhead must
        # POINT AT something, which a sign-blind shape cannot.
        ents = [diamond((100.0, 100.0), (-1.0, 0.0)),
                Line(start=(100.0, 100.0), end=(160.0, 100.0)),
                TextItem(content="TOP OF WALL", position=(164.0, 100.0),
                         height=3.0)]
        leads = q.find_leaders(_ir(ents), max_arrowhead_size=MAS,
                               min_confidence=0.0)
        assert leads
        assert leads[0]["evidence"]["blunt_terminator"] is True
        assert "signed_axis_alignment" not in leads[0]["evidence"]
        assert leads[0]["confidence"] <= q._UNCORROBORATED_CAP
