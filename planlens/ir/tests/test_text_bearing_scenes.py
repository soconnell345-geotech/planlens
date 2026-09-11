"""TEXT-BEARING regression scenes for the composition caps.

Why this file exists. All ten Mecklenburg ground-truth sheets are
SHX-stroked with NO TEXT LAYER, so ``text_score`` is identically 0 on
them and every behavior gated on a text layer, on ``ext_ends`` or on
witness corroboration is invisible to the corpus BY CONSTRUCTION. A
change that silently dropped a real leader on any ordinary text-bearing
drawing therefore measured "zero change" on the corpus. These scenes
carry a text layer on purpose, and they pin the caps the corpus cannot
see:

- :func:`leader_beside_a_blunt_detail_line` — a real leader with tail
  text, a 60 pt detail line leaving the same neighborhood and ending in
  an ordinary rectangle, two witness ticks and a "30'" note. Every
  ingredient the blunt-terminator escape hatch needed;
- :func:`dimension_with_a_letterform_chevron` — a genuine two-arrow
  dimension with one letterform chevron parked nearer to an end than the
  true arrow is.

Geometry conventions follow :mod:`planlens.ir.tests.test_arrow_direction`
(same arrow builders, same 9.31 pt validation-sheet arrowhead scale).
"""

from __future__ import annotations

import math

import pytest

from planlens.ir import queries as q
from planlens.ir.results import DrawingIR, Line, Polyline, TextItem

from planlens.ir.tests.test_arrow_direction import (
    MAS, _arrow_base_leg, _arrow_chevron, _ir)


# ---------------------------------------------------------------------------
# The scenes
# ---------------------------------------------------------------------------

def _rect(cx, cy, w=3.0, h=3.0):
    """An ordinary 3x3 plan rectangle — a valve box, a pull box, a tile."""
    return Polyline(vertices=[(cx - w / 2, cy - h / 2),
                              (cx + w / 2, cy - h / 2),
                              (cx + w / 2, cy + h / 2),
                              (cx - w / 2, cy + h / 2)], closed=True)


def leader_beside_a_blunt_detail_line():
    """Real leader + a blunt-terminated detail line sharing its neighborhood.

    e0 filled arrowhead at apex (0, 0); e1 the leader shaft
    (60, 8) -> (7.1, 0); e2 its tail text; e3 a 60 pt detail line leaving
    the same point; e4 the rectangle that ends it; e5/e6 witness ticks;
    e7 the "30'" note at the mid-construct point.
    """
    d = q._unit_vec(7.1 - 60.0, 0.0 - 8.0)
    return [
        _arrow_base_leg((0.0, 0.0), d),
        Line(start=(7.1, 0.0), end=(60.0, 8.0)),
        TextItem(content='EXIST. 8" DIP', position=(66.0, 8.0), height=3.0),
        Line(start=(7.1, 0.0), end=(67.1, 0.0)),
        _rect(67.1, 0.0),
        Line(start=(0.0, -6.0), end=(0.0, 6.0)),
        Line(start=(67.1, -6.0), end=(67.1, 6.0)),
        TextItem(content="30'", position=(33.5, 3.0), height=3.0),
    ]


def dimension_with_a_letterform_chevron(with_junk: bool):
    """A 60 pt witnessed, texted dimension; optionally one glyph chevron
    parked 1.7 pt from end B against the true arrow's 4.8 pt."""
    ents = [
        Line(start=(0.0, 0.0), end=(60.0, 0.0)),
        _arrow_base_leg((0.0, 0.0), (-1.0, 0.0)),
        _arrow_base_leg((60.0, 0.0), (1.0, 0.0)),
        Line(start=(0.0, -8.0), end=(0.0, 8.0)),
        Line(start=(60.0, -8.0), end=(60.0, 8.0)),
        TextItem(content="60", position=(30.0, 4.0), height=3.0),
    ]
    if with_junk:
        ents.append(_arrow_chevron((61.0, 5.0), (0.0, 1.0), leg=5.5,
                                   base=1.8))
    return ents


def _cont(ir, min_confidence):
    return [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                         min_confidence=min_confidence)
            if p["evidence"]["path"] == "continuous"]


# ---------------------------------------------------------------------------
# BLOCKER 1 — a blunt terminator must never win an arrowhead arbitration
# ---------------------------------------------------------------------------

class TestBluntTerminatorCannotStealALeadersArrowhead:
    """The corpus could not see this: with ``text_score`` pinned at 0 the
    "witnesses AND text" escape hatch never opened there, so a change that
    opened it on every ordinary drawing measured as no change at all."""

    def test_the_detail_line_is_not_called_a_dimension(self):
        ents = leader_beside_a_blunt_detail_line()
        assert _cont(_ir(ents), 0.5) == []

    def test_but_it_is_still_proposed_below_the_call_threshold(self):
        deep = _cont(_ir(leader_beside_a_blunt_detail_line()), 0.0)
        assert len(deep) == 1
        assert deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        # Corroborated in every observable channel — and still capped,
        # because none of those channels speaks to the terminator.
        assert deep[0]["evidence"]["n_extension_ends"] == 2
        assert deep[0]["evidence"]["text_proximity_score"] > 0

    def test_the_real_leader_survives_exclude_dimensions(self):
        leads = q.find_leaders(_ir(leader_beside_a_blunt_detail_line()),
                               max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        assert [p["arrowhead_id"] for p in leads] == ["e0"]
        assert leads[0]["confidence"] > 0.9

    def test_the_blunt_end_is_withheld_from_the_arbitration_channel(self):
        deep = _cont(_ir(leader_beside_a_blunt_detail_line()), 0.0)
        assert "e4" not in deep[0]["arrowhead_ids"]
        assert deep[0]["evidence"]["blunt_terminator_ids"] == ["e4"]

    def test_two_plain_rectangles_are_not_a_called_dimension(self):
        ents = [
            Line(start=(0.0, 30.0), end=(60.0, 30.0)),
            _rect(0.0, 30.0), _rect(60.0, 30.0),
            Line(start=(0.0, 24.0), end=(0.0, 36.0)),
            Line(start=(60.0, 24.0), end=(60.0, 36.0)),
            TextItem(content="30'", position=(30.0, 33.0), height=3.0),
        ]
        assert _cont(_ir(ents), 0.5) == []
        deep = _cont(_ir(ents), 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        assert deep[0]["arrowhead_ids"] == []

    def test_one_real_arrow_plus_one_rectangle_is_not_called_either(self):
        ents = [
            Line(start=(0.0, 30.0), end=(60.0, 30.0)),
            _arrow_chevron((0.0, 30.0), (-1.0, 0.0)), _rect(60.0, 30.0),
            Line(start=(0.0, 24.0), end=(0.0, 36.0)),
            Line(start=(60.0, 24.0), end=(60.0, 36.0)),
            TextItem(content="30'", position=(30.0, 33.0), height=3.0),
        ]
        assert _cont(_ir(ents), 0.5) == []
        deep = _cont(_ir(ents), 0.0)
        assert deep and deep[0]["confidence"] <= q._UNCORROBORATED_CAP
        # Only the DIRECTIONAL terminator may be named for arbitration.
        assert deep[0]["arrowhead_ids"] == ["e1"]


# ---------------------------------------------------------------------------
# BLOCKER 2 — a contradicted candidate must not shadow a sound one
# ---------------------------------------------------------------------------

class TestNearerJunkDoesNotShadowTheTrueArrow:
    """Ends are won on (soundness, then distance) — never distance alone.
    A single letterform chevron 1.7 pt from an end, against the true
    arrow's 4.8 pt, otherwise took the end and collapsed the whole
    construct."""

    def _p(self, with_junk):
        # The construct founded on the DIMENSION SHAFT (e0). Adding the
        # chevron also lets a witness line found its own capped 0.25
        # reading, which is cap-not-delete working as intended.
        deep = [p for p in
                _cont(_ir(dimension_with_a_letterform_chevron(with_junk)), 0.0)
                if p["shaft_id"] == "e0"]
        assert len(deep) == 1
        return deep[0]

    def test_the_junk_is_genuinely_nearer(self):
        ents = dimension_with_a_letterform_chevron(True)
        end_b = (60.0, 0.0)
        d_true = math.hypot(*[a - b for a, b in zip(
            q._centroid([tuple(v) for v in ents[2].vertices]), end_b)])
        d_junk = math.hypot(*[a - b for a, b in zip(
            q._centroid([tuple(v) for v in ents[6].vertices]), end_b)])
        assert d_junk < d_true

    def test_the_construct_is_unchanged_by_the_junk(self):
        clean, dirty = self._p(False), self._p(True)
        assert dirty["confidence"] == pytest.approx(clean["confidence"])
        assert dirty["end_b_xy"] == pytest.approx(clean["end_b_xy"])
        assert dirty["length"] == pytest.approx(clean["length"])
        assert dirty["angle_deg"] == pytest.approx(clean["angle_deg"])
        assert "arrow_direction_violation" not in dirty["evidence"]

    def test_the_dimension_is_still_called_at_the_default(self):
        assert len(_cont(_ir(dimension_with_a_letterform_chevron(True)),
                         0.5)) == 1


# ---------------------------------------------------------------------------
# MAJOR 7b — the detach bound, measured on a text-bearing scene
# ---------------------------------------------------------------------------

def _leader_at_centroid_distance(leg, mas, dist):
    """A leader whose shaft end sits ``dist`` from the arrowhead CENTROID
    — the quantity the detach bound is stated in. An apex-anchored shaft
    genuinely sits 2h/3 away, so ``dist=0`` is the true attachment."""
    base = round(0.33 * leg, 2)
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    cx = 100.0 + 2.0 * h / 3.0
    return [
        _arrow_chevron((100.0, 100.0), (-1.0, 0.0), leg=leg, base=base),
        Line(start=(cx + dist, 100.0), end=(cx + dist + 60.0, 100.0)),
        TextItem(content="6 IN. PIPE", position=(cx + dist + 66.0, 100.0),
                 height=3.0),
    ]


class TestDetachBoundOnATextBearingScene:
    """The own-length term (:data:`q._ATTACH_SCALE`) widens the bound only
    for a shape-verified candidate LARGER than the sheet estimate. The
    measured letterform-junk p50 of 9.6 pt must still be capped for an
    in-scale chevron, which is the case that actually occurs in lettering."""

    def _p(self, leg, mas, dist):
        leads = q.find_leaders(_ir(_leader_at_centroid_distance(leg, mas,
                                                                dist)),
                               max_arrowhead_size=mas, min_confidence=0.0)
        assert leads
        return leads[0]

    @pytest.mark.parametrize("leg,mas", [(7.3, 9.31), (12.3, 10.0)])
    def test_a_genuine_leader_is_attached_at_either_scale(self, leg, mas):
        p = self._p(leg, mas, 0.0)
        assert "arrow_detached" not in p["evidence"]
        assert p["confidence"] > 0.9

    def test_an_in_scale_chevron_at_the_junk_p50_is_capped(self):
        p = self._p(7.3, 9.31, 9.6)
        assert p["evidence"]["arrow_detached"] is True
        assert p["confidence"] <= q._UNCORROBORATED_CAP

    def test_the_bound_is_geometry_not_slack(self):
        # 0.75 = 2/3 (a triangle's vertex centroid sits 1/3 of the way
        # from base to apex, so an apex-anchored shaft end is 2/3 of the
        # candidate's own length away) plus 12.5%. Zero slack would be a
        # floating-point coin flip on every genuine apex-anchored arrow.
        assert q._ATTACH_SCALE == pytest.approx(2.0 / 3.0 * 1.125)


# ---------------------------------------------------------------------------
# MAJOR 7a — a fill cluster faces an EXACT bound, not just the prune
# ---------------------------------------------------------------------------

def _stipple_splash(cx, cy):
    """Six micro-fragments in a ring — the fill-cluster arrowhead shape."""
    out = []
    for k in range(6):
        a = 2 * math.pi * k / 6
        px, py = cx + 1.2 * math.cos(a), cy + 1.2 * math.sin(a)
        out.append(Line(start=(px, py), end=(px + 0.4, py + 0.3)))
    return out


def _splash_dim(offset):
    """A 200 pt witnessed, texted line whose ends carry stipple splashes
    ``offset`` pt away — each anchored to its OWN long stroke, which is
    what builds it as a cluster candidate in the first place."""
    y = 400.0
    ents = [Line(start=(100.0, y), end=(300.0, y)),
            Line(start=(100.0, y - 12), end=(100.0, y + 12)),
            Line(start=(300.0, y - 12), end=(300.0, y + 12)),
            TextItem(content="200", position=(200.0, y + 4), height=3.0)]
    for sx, sgn in ((100.0, -1.0), (300.0, 1.0)):
        cx = sx + sgn * offset
        ents += _stipple_splash(cx, y)
        ents.append(Line(start=(cx, y + 40.0), end=(cx, y + 0.6)))
    return ents


class TestClusterAttachmentIsAnExactBound:
    """Fill clusters are exempt from the direction and spine tests, so
    without an exact attachment bound the caller's coarse prune WOULD be
    the whole test for them — and a stipple splash 8 pt off a shaft end
    scored 0.85."""

    def test_a_splash_on_the_end_is_attached(self):
        p = _cont(_ir(_splash_dim(0.0)), 0.5)
        assert len(p) == 1
        assert "arrow_detached" not in p[0]["evidence"]

    def test_a_splash_off_the_end_is_capped_not_called(self):
        assert _cont(_ir(_splash_dim(8.0)), 0.5) == []
        deep = _cont(_ir(_splash_dim(8.0)), 0.0)
        assert deep
        assert deep[0]["evidence"]["arrow_detached"] is True
        assert deep[0]["confidence"] <= q._UNCORROBORATED_CAP


# ---------------------------------------------------------------------------
# MAJOR 8 — one home for the loose size multiplier
# ---------------------------------------------------------------------------

class TestLooseSizeScaleHasOneHome:
    def test_the_candidate_gate_and_the_prune_share_it(self):
        assert q._LOOSE_SIZE_SCALE == 1.5

    def test_a_maximally_oversized_arrow_still_attaches(self):
        # A candidate at the very top of what the gate admits must be
        # reachable by the dimension leg's prune, or the prune silently
        # becomes the attachment test for the largest real arrowheads.
        mas = 9.0
        leg = 1.35 * mas          # bbox diagonal 12.6 vs the 13.5 cap
        base = round(0.33 * leg, 2)
        h = math.sqrt(leg * leg - (base / 2) ** 2)
        y = 500.0
        ents = [
            Line(start=(200.0 + h, y), end=(320.0 - h, y)),
            _arrow_chevron((200.0, y), (-1.0, 0.0), leg=leg, base=base),
            _arrow_chevron((320.0, y), (1.0, 0.0), leg=leg, base=base),
            Line(start=(200.0, y - 14), end=(200.0, y + 14)),
            Line(start=(320.0, y - 14), end=(320.0, y + 14)),
            TextItem(content="120", position=(260.0, y + 5), height=3.0),
        ]
        cont = [p for p in q.find_dimensions(_ir(ents),
                                             max_arrowhead_size=mas,
                                             min_confidence=0.5)
                if p["evidence"]["path"] == "continuous"]
        assert len(cont) == 1
        assert "arrow_detached" not in cont[0]["evidence"]


# ---------------------------------------------------------------------------
# ROUND-4 BLOCKER — a SKEWED dimension must not steal a neighbour's arrowhead
# ---------------------------------------------------------------------------

_H = math.sqrt(7.3 ** 2 - 1.2 ** 2)   # apex-to-base of the standard arrow


def skewed_dimension_beside_a_real_leader(off_deg, leader_apex_x=124.0):
    """A witnessed, texted 120 pt dimension whose right arrow is drafted
    ``off_deg`` off axis, plus an UNRELATED real leader whose arrowhead
    sits farther out on the same terminal ray.

    The arrow swings about its APEX, because a crooked arrow's tip still
    marks the measurement point — that is what makes the shaft miss the
    arrow's spine past asin((base/2) / h) = 9.59 deg.
    """
    a = math.radians(off_deg)
    ents = [
        Line(id="d_shaft", start=(7.2, 0.0), end=(112.8, 0.0)),
        _arrow_base_leg((0.0, 0.0), (-1.0, 0.0)),
        _arrow_base_leg((120.0, 0.0), (math.cos(a), math.sin(a))),
        Line(id="w_a", start=(0.0, -8.0), end=(0.0, 8.0)),
        Line(id="w_b", start=(120.0, -8.0), end=(120.0, 8.0)),
        TextItem(id="d_txt", content="120", position=(60.0, 4.0), height=3.0),
        _arrow_base_leg((leader_apex_x, 0.0), (1.0, 0.0)),
        Line(id="l_shaft", start=(leader_apex_x - _H, 0.0), end=(20.0, -20.0)),
        TextItem(id="l_txt", content="CB #4", position=(14.0, -22.0),
                 height=3.0),
    ]
    for i, e in enumerate(ents):
        if not getattr(e, "id", None):
            e.id = "e%d" % i
    return ents


class TestSkewedDimensionDoesNotStealANeighbouringLeader:
    """Ranking ends by soundness re-opened the steal from the other side.

    While ``on_spine`` fed the soundness tier, a dimension's OWN correctly
    attached arrow was demoted the moment it was drafted past 9.59 deg —
    and soundness-first ranking then handed the end to whatever FARTHER
    candidate was straight, including this scene's real leader. The
    dimension published the leader's arrowhead in ``arrowhead_ids`` at
    0.964, and ``exclude_dimensions`` deleted the leader at EVERY
    ``min_confidence``. Identity is direction-and-attachment only; the
    spine is a statement about the COORDINATE.
    """

    SKEWS = [0, 5, 9, 10, 12, 15, 20, 25]

    def _dim_and_leaders(self, off_deg, junk_x, min_confidence):
        ir = _ir(skewed_dimension_beside_a_real_leader(off_deg, junk_x))
        dims = [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                             min_confidence=min_confidence)
                if p["evidence"]["path"] == "continuous"]
        leads = q.find_leaders(ir, max_arrowhead_size=MAS,
                               min_confidence=min_confidence,
                               exclude_dimensions=True)
        return dims, leads

    @staticmethod
    def _real_dim(dims):
        """The construct founded on e1 — the left arrow no other line can
        plausibly claim, so it identifies the genuine dimension."""
        got = [p for p in dims if "e1" in p["arrowhead_ids"]]
        assert len(got) == 1
        return got[0]

    @pytest.mark.parametrize("off_deg", SKEWS)
    @pytest.mark.parametrize("junk_x", [124.0, 126.4])
    def test_the_dimension_never_claims_the_leaders_arrowhead(self, off_deg,
                                                              junk_x):
        dims, _ = self._dim_and_leaders(off_deg, junk_x, 0.0)
        # e1/e2 are the dimension's own arrows; e6 is the leader's.
        assert self._real_dim(dims)["arrowhead_ids"] == ["e1", "e2"]

    @pytest.mark.parametrize("off_deg", SKEWS)
    @pytest.mark.parametrize("junk_x", [124.0, 126.4])
    def test_nothing_CALLED_ever_claims_the_leaders_arrowhead(self, off_deg,
                                                              junk_x):
        # Past 20 deg the witness line at x=120 does get read as a shaft
        # terminated by the skewed arrow and the leader's — but that
        # reading is contradicted, detached AND off spine, so it lands on
        # the bottom rung and is never asserted. Visible below the call
        # threshold is the cap ladder working; CALLED would be the steal.
        dims, _ = self._dim_and_leaders(off_deg, junk_x, 0.0)
        for p in dims:
            if "e6" in p["arrowhead_ids"]:
                assert p["confidence"] <= q._CONTRADICTED_CAP
        called, _ = self._dim_and_leaders(off_deg, junk_x, 0.5)
        assert all("e6" not in p["arrowhead_ids"] for p in called)

    @pytest.mark.parametrize("off_deg", SKEWS)
    @pytest.mark.parametrize("junk_x", [124.0, 126.4])
    def test_the_real_leader_survives_at_every_skew(self, off_deg, junk_x):
        _, leads = self._dim_and_leaders(off_deg, junk_x, 0.0)
        assert any(p.get("text") == "CB #4" for p in leads)

    @pytest.mark.parametrize("off_deg", [0, 5, 9])
    def test_within_the_spine_the_defpoint_is_exact_and_called(self, off_deg):
        dims, _ = self._dim_and_leaders(off_deg, 124.0, 0.5)
        assert self._real_dim(dims)["end_b_xy"] == pytest.approx(
            [120.0, 0.0], abs=0.01)

    @pytest.mark.parametrize("off_deg", [10, 15, 25])
    def test_past_the_spine_it_falls_back_to_drawn_fact_and_is_capped(
            self, off_deg):
        # THE DOCUMENTED RESIDUAL. Past 9.59 deg this leg publishes the
        # shaft's own end — drawn fact — rather than an apex the shaft
        # does not run into, and caps the construct. In the arrows-OUTSIDE
        # style used here that under-reports the span by one arrow length
        # (112.8 for a defpoint at 120.0), and the witness line standing
        # at 120.0 is the evidence that would settle it the other way;
        # using it means resolving the ends BEFORE the confidence hoist
        # that keeps sheet 3001 at 0.4 s instead of 8.5 s. Nothing is
        # lost — the construct is still proposed at min_confidence=0.0.
        dims, _ = self._dim_and_leaders(off_deg, 124.0, 0.0)
        real = self._real_dim(dims)
        assert real["end_b_xy"] == pytest.approx([112.8, 0.0], abs=0.01)
        assert real["confidence"] <= q._UNCORROBORATED_CAP
        assert real["evidence"]["arrow_off_spine"] is True
        # ...and nothing here is called at the conventional threshold.
        called, _ = self._dim_and_leaders(off_deg, 124.0, 0.5)
        assert called == []
