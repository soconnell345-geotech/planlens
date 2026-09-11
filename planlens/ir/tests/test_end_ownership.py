"""Who owns a dimension's end — the round-5 repair of findings 2, 3 and 4.

Round-4 verification, three findings with one root:

- **finding 2** — a fill-cluster arrowhead 0.04 pt from the tip lost its
  end to a foreign chevron 3.24 pt away, purely on tier (clusters carry
  no direction and ranked below any directional candidate at ANY
  distance). The foreign arrowhead then landed in ``arrowhead_ids`` and
  ``find_leaders(exclude_dimensions=True)`` deleted the leader that owned
  it — the arbitration steal, reached by a new route;
- **finding 3** — an on-spine chevron at apex (108, 0) beside a 100 pt
  shaft whose real arrow sits at (100, 0) took the end and published
  108.0 at 0.942;
- **finding 4** — an off-spine chevron at (103, 3) took the same end;
  the tip fallback saved the coordinate but the off-spine cap dropped a
  true dimension to 0.45.

Findings 3 and 4 are one defect: ends were ordered by CENTROID distance,
which reads the true arrows-inside arrow (apex ON the end, centroid 2h/3
behind it) as farther than junk beside or just beyond the end. The
repair orders by SEAT (:func:`planlens.ir.queries._seat_distance`) —
the shaft end's distance from the candidate's own apex or base centre
(0.0 for a real terminator in either drafted style) or, for a fill
cluster, from its NEAREST member (0.0 when the end is in the splash) —
and awards an end across the two sound tiers by
:func:`planlens.ir.queries._award_end`: the better seated wins, a tie
to the directional one, and a contradicted candidate never wins on
distance. (Round 5 first tried "directional wins unless the sign-blind
one is seated ten times nearer", with the cluster seated at its
centroid; the independent verification measured that window at
0.05-0.4 pt — a real stipple arrowhead, centroid 0.5-2 pt off its end,
still lost to a foreign chevron seated 0.94 pt beyond it at every
realistic offset. Both directions of the trade are pinned below,
including the case round 4 measured when it decided the other way and
the verifier's table at every offset.)

Geometry conventions follow :mod:`planlens.ir.tests.test_arrow_direction`.
"""

from __future__ import annotations

import math

import pytest

from planlens.ir import queries as q
from planlens.ir.results import Line, TextItem

from planlens.ir.tests.test_arrow_direction import (
    MAS, _arrow_base_leg, _arrow_chevron, _ir)
from planlens.ir.tests.test_tipless_terminators import core, diamond

_H = math.sqrt(7.3 ** 2 - 1.2 ** 2)   # apex-to-base of the standard arrow


def fill_cluster(tip, d, n=7, span=3.0):
    """Micro-dot stipple arrowhead straddling ``tip`` (the Mecklenburg
    anatomy, as the round-4 verification built it)."""
    out = []
    px, py = -d[1], d[0]
    for i in range(n):
        t = (i / (n - 1.0)) * span - 0.5 * span
        for k in (-0.6, 0.0, 0.6):
            x = tip[0] + d[0] * t + px * k
            y = tip[1] + d[1] * t + py * k
            out.append(Line(start=(x, y), end=(x + 0.06, y + 0.06)))
    return out


def _cont(ents, min_confidence, shaft_id="e0"):
    return [p for p in q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                         min_confidence=min_confidence)
            if p["evidence"]["path"] == "continuous"
            and p["shaft_id"] == shaft_id]


def _one(ents, min_confidence=0.0):
    got = _cont(ents, min_confidence)
    assert len(got) == 1, got
    return got[0]


A = _arrow_chevron((0.0, 0.0), (-1.0, 0.0))     # the left arrow, always true
B = _arrow_chevron((100.0, 0.0), (1.0, 0.0))    # the true right arrow


# ---------------------------------------------------------------------------
# The seat metric itself
# ---------------------------------------------------------------------------

class TestSeatDistance:
    def _seat(self, shape, tip=(100.0, 0.0), sdir=(1.0, 0.0), kind="triangle"):
        verts = [tuple(v) for v in shape.vertices]
        g = None if kind == "fill_cluster" else q._arrow_geometry(verts)
        return q._seat_distance(verts, kind, sdir, tip, g)

    def test_an_apex_anchored_arrow_is_seated_at_zero(self):
        assert self._seat(B) == pytest.approx(0.0, abs=1e-9)

    def test_a_base_anchored_arrow_is_seated_at_zero_too(self):
        # Arrows outside: the shaft stops at the arrow base, the apex is
        # one arrow length beyond (the ground-truth split-half anatomy).
        outside = _arrow_base_leg((100.0 + _H, 0.0), (1.0, 0.0))
        assert self._seat(outside) == pytest.approx(0.0, abs=1e-9)

    def test_the_centroid_reads_the_true_arrow_as_far_away(self):
        # What the old ordering measured: 2h/3 ~ 4.8 pt for the arrow
        # whose apex IS the end.
        c = q._centroid([tuple(v) for v in B.vertices])
        assert math.hypot(c[0] - 100.0, c[1]) == pytest.approx(2 * _H / 3,
                                                              abs=0.01)

    def test_the_finding_2_chevron_is_seated_at_its_base_gap(self):
        junk = _arrow_chevron((108.0, 0.5), (1.0, 0.0))
        assert self._seat(junk) == pytest.approx(
            math.hypot(108.0 - _H - 100.0, 0.5), abs=0.01)

    @staticmethod
    def _cluster_verts(shift):
        return [q._centroid([tuple(e.start), tuple(e.end)])
                for e in fill_cluster((100.0 + shift, 0.0), (1.0, 0.0))]

    def test_a_cluster_seats_at_its_nearest_member(self):
        # Centred on the end: ~0. Shifted 2 pt (the splash spans +-1.5):
        # the nearest dot is 0.5 pt beyond the end. Its CENTROID would
        # read 0.04 and 2.04 — and the 2.04 lost the end to a foreign
        # chevron seated 0.94 (round-5 verification, D1).
        s = lambda shift: q._seat_distance(self._cluster_verts(shift),
                                           "fill_cluster", (1.0, 0.0),
                                           (100.0, 0.0), None)
        assert s(0.0) == pytest.approx(0.042, abs=0.01)
        assert s(1.0) == pytest.approx(0.042, abs=0.01)
        assert s(2.0) == pytest.approx(0.5, abs=0.05)
        assert s(5.0) == pytest.approx(3.5, abs=0.05)

    def test_a_diamond_inside_the_line_seats_at_its_far_corner(self):
        assert self._seat(diamond((100.0, 0.0), (1.0, 0.0))) == pytest.approx(
            0.0, abs=1e-9)


class TestAwardEnd:
    @staticmethod
    def _f(tier, seat):
        return {"tier": tier, "seat": seat, "tag": (tier, seat)}

    def test_sounder_wins_at_equal_seat(self):
        got = q._award_end({0: self._f(0, 0.0), 1: self._f(1, 0.0)})
        assert got["tier"] == 0

    def test_a_seated_directional_arrow_is_unbeatable(self):
        got = q._award_end({0: self._f(0, 0.0), 1: self._f(1, 1e-9)})
        assert got["tier"] == 0

    def test_between_the_sound_tiers_the_better_seated_wins(self):
        # The verifier's D1 table, in the award's own currency: a cluster
        # seated 0.04-0.5 beats a chevron seated 0.94-3.3; a cluster
        # seated 3.5 (round 4's splash 5 pt off the end) loses to a
        # crooked arrow seated 1.5-2.5.
        for s1 in (0.04, 0.1, 0.3, 0.5):
            for s0 in (0.94, 2.7, 3.0, 3.3):
                assert q._award_end({0: self._f(0, s0), 1: self._f(1, s1)})["tier"] == 1
        for s0 in (1.5, 2.5):
            assert q._award_end({0: self._f(0, s0), 1: self._f(1, 3.5)})["tier"] == 0
        # No ratio: 0.9 beats 1.0.
        assert q._award_end({0: self._f(0, 1.0), 1: self._f(1, 0.9)})["tier"] == 1
        assert not hasattr(q, "_SEAT_DOMINANCE")

    def test_contradicted_is_never_dominated_and_never_dominates(self):
        assert q._award_end({1: self._f(1, 5.0), 2: self._f(2, 0.0)})["tier"] == 1
        assert q._award_end({0: self._f(0, 5.0), 2: self._f(2, 0.0)})["tier"] == 0
        assert q._award_end({2: self._f(2, 3.0)})["tier"] == 2

    def test_empty(self):
        assert q._award_end({}) is None


# ---------------------------------------------------------------------------
# FINDING 2 — a seated cluster keeps its end; the foreign arrowhead is
# not named for arbitration; the leader that owns it survives
# ---------------------------------------------------------------------------

def cluster_dimension(with_foreign):
    ents = core() + [A] + fill_cluster((100.0, 0.0), (1.0, 0.0))
    if with_foreign:
        ents.append(_arrow_chevron((108.0, 0.5), (1.0, 0.0)))
    return ents


def cluster_dimension_beside_a_real_leader():
    """The foreign chevron now BELONGS to someone: a real leader whose
    arrowhead sits on the dimension's terminal ray, 0.8 pt beyond the
    shaft end — the anatomy through which finding 2 deleted a leader."""
    ents = cluster_dimension(with_foreign=False)
    ents += [
        _arrow_chevron((108.0, 0.5), (1.0, 0.0)),
        # The leader shaft leaves the arrow base within the 30 deg cone
        # the leader flow's own direction cap allows (~14 deg here).
        Line(start=(108.0 - _H, 0.5), end=(60.0, -10.0)),
        TextItem(content="CB #4", position=(54.0, -12.0), height=3.0),
    ]
    return ents


class TestASeatedClusterKeepsItsEnd:
    def test_the_foreign_chevron_changes_nothing(self):
        clean, dirty = _one(cluster_dimension(False)), _one(cluster_dimension(True))
        assert dirty["end_b_xy"] == pytest.approx(clean["end_b_xy"])
        assert dirty["confidence"] == pytest.approx(clean["confidence"])
        assert dirty["arrowhead_ids"] == clean["arrowhead_ids"]

    def test_the_cluster_wins_and_the_foreign_id_is_not_named(self):
        p = _one(cluster_dimension(True), 0.5)
        assert p["arrowhead_ids"] == ["e4", "cluster:e10"]
        assert p["end_b_xy"][0] == pytest.approx(101.53, abs=0.05)
        assert abs(p["end_b_xy"][1]) < 0.05

    def test_the_leader_that_owns_the_chevron_survives(self):
        ents = cluster_dimension_beside_a_real_leader()
        ir = _ir(ents)
        leads = q.find_leaders(ir, max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        assert any(p.get("text") == "CB #4" for p in leads)
        # Whatever id the scene assigned to the leader's chevron, no
        # dimension names it — at any threshold.
        chevron_id = [e.id for e in ir.entities
                      if getattr(e, "vertices", None)
                      and len(e.vertices) == 3
                      and abs(e.vertices[1][0] - 108.0) < 1e-6][0]
        dims = q.find_dimensions(ir, max_arrowhead_size=MAS,
                                 min_confidence=0.0)
        assert all(chevron_id not in p["arrowhead_ids"] for p in dims)

    def test_the_seat_is_what_decides_it(self):
        # Cluster seat ~0.04 (nearest member) vs the chevron's ~0.94
        # base gap: the end is IN the splash and not at the chevron.
        c_verts = [q._centroid([tuple(e.start), tuple(e.end)])
                   for e in fill_cluster((100.0, 0.0), (1.0, 0.0))]
        s_cluster = q._seat_distance(c_verts, "fill_cluster", (1.0, 0.0),
                                     (100.0, 0.0), None)
        j = [tuple(v) for v in _arrow_chevron((108.0, 0.5), (1.0, 0.0)).vertices]
        s_junk = q._seat_distance(j, "triangle", (1.0, 0.0), (100.0, 0.0),
                                  q._arrow_geometry(j))
        assert s_cluster < 0.1 < 0.9 < s_junk


def cluster_dimension_at_offset(shift, seat, chevron_first=False,
                                with_leader=False):
    """The verifier's D1 anatomy: a witnessed, texted 100 pt dimension,
    true chevron at 0, a fill cluster straddling ``(100 + shift, 0)`` and
    a foreign outward chevron whose base centre sits ``seat`` pt beyond
    the end on axis (apex at ``100 + seat + H``). Optionally the leader
    that owns the foreign chevron; optionally the chevron enumerated
    BEFORE the cluster (the order that exposed a prune bug)."""
    chev = [_arrow_chevron((100.0 + seat + _H, 0.0), (1.0, 0.0))]
    splash = fill_cluster((100.0 + shift, 0.0), (1.0, 0.0))
    ents = core() + [A] + (chev + splash if chevron_first else splash + chev)
    if with_leader:
        ents += [Line(start=(100.0 + seat, 0.0), end=(60.0, -10.0)),
                 TextItem(content="CB #4", position=(54.0, -12.0),
                          height=3.0)]
    return ents


class TestARealClusterOwnsItsEndAtEveryOffset:
    """The round-5 verification's D1 table (round 5's 10x rule closed
    finding 2 only for a cluster centred within 0.05-0.4 pt of the end;
    the tip got every row right). A real stipple arrowhead's centroid
    sits 0.5-2 pt from the end it terminates; its nearest member does
    not, and that is now its seat."""

    SHIFTS = [0.04, 0.1, 0.3, 0.5, 1.0, 2.0]
    SEATS = [0.94, 2.7, 3.0, 3.3]

    @pytest.mark.parametrize("shift", SHIFTS)
    @pytest.mark.parametrize("seat", SEATS)
    @pytest.mark.parametrize("chevron_first", [False, True])
    def test_the_cluster_wins_and_the_foreign_id_is_absent(self, shift, seat,
                                                            chevron_first):
        p = _one(cluster_dimension_at_offset(shift, seat, chevron_first), 0.5)
        assert any(i.startswith("cluster:") for i in p["arrowhead_ids"])
        assert len(p["arrowhead_ids"]) == 2
        assert all(not i.startswith("e") or i == "e4"
                   for i in p["arrowhead_ids"])
        # Published inside the splash (its reach on the ray), never at
        # the foreign apex.
        assert 100.0 <= p["end_b_xy"][0] <= 100.0 + shift + 1.6
        assert abs(p["end_b_xy"][1]) < 0.05

    @pytest.mark.parametrize("shift", [0.1, 0.5, 1.0, 2.0])
    def test_appendix_c_the_leader_that_owns_the_chevron_survives(self, shift):
        # The verifier's drop-in fixture, verbatim in substance.
        ents = core() + [A] + fill_cluster((100.0 + shift, 0.0), (1.0, 0.0)) + [
            _arrow_chevron((108.0, 0.5), (1.0, 0.0)),
            Line(start=(108.0 - _H, 0.5), end=(60.0, -10.0)),
            TextItem(content="CB #4", position=(54.0, -12.0), height=3.0)]
        ir = _ir(ents)
        p = [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS,
                                          min_confidence=0.5)
             if p["shaft_id"] == "e0"][0]
        assert any(i.startswith("cluster:") for i in p["arrowhead_ids"]), \
            p["arrowhead_ids"]
        assert abs(p["end_b_xy"][0] - 100.0) < 4.0
        leads = q.find_leaders(ir, max_arrowhead_size=MAS,
                               exclude_dimensions=True, min_confidence=0.5)
        real = [l for l in leads if l.get("text") == "CB #4"
                and not str(l.get("arrowhead_id")).startswith("cluster:")]
        assert real, "the leader that owns the chevron was arbitrated away"


# ---------------------------------------------------------------------------
# THE OTHER DIRECTION — round 4's measured trade must not regress
# ---------------------------------------------------------------------------

def skewed_dimension_with_a_splash(off_deg, splash_offset):
    """A witnessed, texted 100 pt dimension in the ARROWS-OUTSIDE style
    (the shaft runs between the arrow bases, apexes at the defpoints 0
    and 100 — the ground-truth anatomy), whose right arrow is drafted
    ``off_deg`` off axis about its apex, plus a stipple splash
    ``splash_offset`` pt beyond the shaft end. Round 4 measured this at
    5 pt: the splash took the end from the arrow (centroid 2.4 pt from
    the end, i.e. base-anchored) and lifted the construct 0.45 -> 0.908.

    Note why the arrows-outside style is the one that matters here: an
    arrows-INSIDE arrow swung about its apex keeps its apex ON the end,
    so it stays on spine and seated at 0.0 whatever the skew — nothing
    can take that end from it and no cap is in play.
    """
    a = math.radians(off_deg)
    ents = [Line(start=(_H, 0.0), end=(100.0 - _H, 0.0)),
            Line(start=(0.0, -5.0), end=(0.0, 25.0)),
            Line(start=(100.0, -5.0), end=(100.0, 25.0)),
            TextItem(content="100'", position=(50.0, 6.0), height=6.0),
            _arrow_base_leg((0.0, 0.0), (-1.0, 0.0)),
            _arrow_base_leg((100.0, 0.0), (math.cos(a), math.sin(a)))]
    ents += fill_cluster((100.0 - _H + splash_offset, 0.0), (1.0, 0.0))
    return ents


class TestASplashOffTheEndCannotLiftACappedConstruct:
    """Round 4's own measured case, pinned: the crooked arrow's base
    centre seats it 2h.sin(a/2) from the end (1.5 pt at 12 deg, 2.5 at
    20); a splash 5 pt off the end seats ~3.5 pt by its nearest member,
    so the arrow keeps the end — and the construct keeps the arrow's
    off-spine cap, exactly as round 4 decided."""

    @pytest.mark.parametrize("off_deg", [12.0, 20.0])
    def test_the_arrow_keeps_the_end_and_the_cap_holds(self, off_deg):
        p = _one(skewed_dimension_with_a_splash(off_deg, 5.0))
        assert p["arrowhead_ids"] == ["e4", "e5"]
        assert p["evidence"]["arrow_off_spine"] is True
        assert p["confidence"] <= q._UNCORROBORATED_CAP
        assert _cont(skewed_dimension_with_a_splash(off_deg, 5.0), 0.5) == []

    def test_within_the_spine_the_splash_is_simply_irrelevant(self):
        p = _one(skewed_dimension_with_a_splash(5.0, 5.0), 0.5)
        assert p["arrowhead_ids"] == ["e4", "e5"]
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)


class TestTheDocumentedResidualOfTheTrade:
    """What the nearer-wins rule DOES let through, pinned so it cannot
    move silently: a splash seated ON the shaft end (nearest member
    0.04 pt), with the dimension's own (arrows-outside) arrow drafted
    crooked about its apex — the arrow then seats at 2h.sin(a/2), 1.5 pt
    at 12 deg, and loses. The end is founded on the splash, its reach is
    published (inside the splash's own footprint, ~5.7 pt short of the
    defpoint at 100 — the tip fallback the arrow would have carried was
    7.2 pt short), and the off-spine cap is gone, so the construct is
    CALLED. The alternative — tier always wins — is finding 2 verbatim
    (a foreign arrowhead named for arbitration, the leader that owns it
    deleted). Deleting is the worse failure; this one publishes a
    coordinate a caller can render-verify."""

    def test_a_seated_splash_takes_the_end_from_a_crooked_arrow(self):
        p = _one(skewed_dimension_with_a_splash(12.0, 0.0), 0.5)
        assert p["arrowhead_ids"][1].startswith("cluster:")
        assert "arrow_off_spine" not in p["evidence"]
        # Published within the splash (span +-1.5 pt about the end).
        assert abs(p["end_b_xy"][0] - (100.0 - _H)) <= 1.6
        assert abs(p["end_b_xy"][1]) < 0.05


class TestTheAcceptedResidualAtItsRealThreshold:
    """THE ACCEPTED RESIDUAL, pinned at its measured boundary (round-6
    verification, R1; the README's "Who owns a dimension end" sharp
    edge is the statement of record).

    The award is seat-only with the tie to the directional side, so a
    splash centred on the shaft end (nearest member ~0.04 pt) takes the
    end from the dimension's own base-anchored arrow at ANY non-zero
    crookedness — an arrow rotated 0.5 deg about its apex already
    seats 0.06 pt at its base centre — not "past ~9.6 deg", which is
    the off-spine cap's angle and was the README's error. At 0.5 deg
    the construct is founded on the splash, CALLED at ~0.947, and its
    end published at ~94.3 for a defpoint at 100. At exactly 0 deg the
    arrow keeps the end. Identical to the published tip at every angle;
    a regression only against round 4 in the 0.3-9.6 deg band;
    corpus-inert (corpus arrows seat within 0.036 pt). This test
    asserts the CURRENT behaviour in both directions so the residual
    cannot narrow or widen silently: a fix that keeps the arrow below
    some tolerance must change these expectations on purpose.
    """

    @staticmethod
    def _scene(off_deg):
        a = math.radians(off_deg)
        return ([Line(start=(_H, 0.0), end=(100.0 - _H, 0.0)),
                 Line(start=(0.0, -5.0), end=(0.0, 25.0)),
                 Line(start=(100.0, -5.0), end=(100.0, 25.0)),
                 TextItem(content="100'", position=(50.0, 6.0), height=6.0),
                 _arrow_base_leg((0.0, 0.0), (-1.0, 0.0)),
                 _arrow_base_leg((100.0, 0.0), (math.cos(a), math.sin(a)))]
                + fill_cluster((100.0 - _H, 0.0), (1.0, 0.0)))

    @pytest.mark.parametrize("off_deg", [0.5, 1.0, 2.4, 5.0])
    def test_a_splash_on_the_end_takes_it_from_a_barely_crooked_arrow(
            self, off_deg):
        p = _one(self._scene(off_deg), 0.5)
        # (The cluster id is "cluster:<lexicographically smallest member
        # id>", which is e10 here, not e6.)
        assert p["arrowhead_ids"][0] == "e4", p["arrowhead_ids"]
        assert p["arrowhead_ids"][1].startswith("cluster:"), p["arrowhead_ids"]
        assert p["end_b_xy"][0] == pytest.approx(100.0 - _H + 1.53, abs=0.05)
        assert abs(p["end_b_xy"][1]) < 0.05
        assert p["confidence"] == pytest.approx(0.947, abs=0.002)
        assert "arrow_off_spine" not in p["evidence"]

    def test_at_exactly_zero_degrees_the_arrow_keeps_its_end(self):
        # Seat 0.0 vs the splash's 0.04: the tie-to-directional rule
        # never comes into it; the arrow is simply nearer.
        p = _one(self._scene(0.0), 0.5)
        assert p["arrowhead_ids"] == ["e4", "e5"]
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)

    def test_the_boundary_is_the_seat_not_an_angle(self):
        # 0.5 deg about the apex moves the base centre 2H.sin(0.25 deg)
        # = 0.063 pt — already past the splash's 0.042 pt.
        a = math.radians(0.5)
        arrow = [tuple(v) for v in
                 _arrow_base_leg((100.0, 0.0), (math.cos(a), math.sin(a))).vertices]
        s_arrow = q._seat_distance(arrow, "triangle", (1.0, 0.0),
                                   (100.0 - _H, 0.0), q._arrow_geometry(arrow))
        splash = [q._centroid([tuple(e.start), tuple(e.end)])
                  for e in fill_cluster((100.0 - _H, 0.0), (1.0, 0.0))]
        s_splash = q._seat_distance(splash, "fill_cluster", (1.0, 0.0),
                                    (100.0 - _H, 0.0), None)
        assert s_arrow == pytest.approx(2 * _H * math.sin(a / 2), abs=1e-6)
        assert s_splash < s_arrow < 0.1


# ---------------------------------------------------------------------------
# FINDINGS 3 and 4 — the true arrow beats junk beside and beyond the end
# ---------------------------------------------------------------------------

class TestTheTrueArrowOutranksJunkAtTheSameEnd:
    @pytest.mark.parametrize("apex", [(108.0, 0.0), (108.0, 1.0), (108.0, 5.0)])
    def test_on_spine_axial_junk_no_longer_takes_the_end(self, apex):
        p = _one(core() + [A, B, _arrow_chevron(apex, (1.0, 0.0))], 0.5)
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)
        assert p["length"] == pytest.approx(100.0, abs=0.01)
        assert p["arrowhead_ids"] == ["e4", "e5"]
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)

    @pytest.mark.parametrize("oy", [2.0, 3.0, 4.0, 5.0])
    def test_nearer_off_spine_junk_no_longer_caps_the_construct(self, oy):
        p = _one(core() + [A, B, _arrow_chevron((103.0, oy), (1.0, 0.0))], 0.5)
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)
        assert "arrow_off_spine" not in p["evidence"]
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)

    def test_the_junk_really_was_nearer_by_centroid(self):
        junk = _arrow_chevron((103.0, 4.0), (1.0, 0.0))
        cj = q._centroid([tuple(v) for v in junk.vertices])
        cb = q._centroid([tuple(v) for v in B.vertices])
        assert math.hypot(cj[0] - 100.0, cj[1]) < math.hypot(cb[0] - 100.0, cb[1])

    def test_the_award_is_independent_of_entity_order(self):
        ents = core() + [A, B, _arrow_chevron((108.0, 0.0), (1.0, 0.0))]
        swapped = core() + [A, _arrow_chevron((108.0, 0.0), (1.0, 0.0)), B]
        p1, p2 = _one(ents, 0.5), _one(swapped, 0.5)
        assert p1["end_b_xy"] == pytest.approx(p2["end_b_xy"])
        assert p1["confidence"] == pytest.approx(p2["confidence"])

    @pytest.mark.parametrize("skew_first", [False, True])
    def test_an_exact_seat_tie_is_broken_by_alignment_not_order(self,
                                                                skew_first):
        # Round-5 verification D4: two chevrons both base-seated at the
        # end (seat 0.0 each, centroids both h/3 away), one on axis and
        # one 20 deg up, published a 2.5 pt different end depending on
        # entity order. The tie now goes to the one pointing more
        # exactly along the line, whichever is enumerated first.
        a = math.radians(20.0)
        straight = _arrow_chevron((100.0 + _H, 0.0), (1.0, 0.0))
        skew = _arrow_chevron((100.0 + _H * math.cos(a), _H * math.sin(a)),
                              (math.cos(a), math.sin(a)))
        ents = core() + [A] + ([skew, straight] if skew_first
                               else [straight, skew])
        p = _one(ents, 0.0)
        assert p["end_b_xy"] == pytest.approx([100.0 + _H, 0.0], abs=0.01)


class TestTheSeatPruneIsExactAndTheOldOneWasNot:
    """Why the nearest-first prune had to go, and why its replacement may
    stay. The old rule skipped any candidate farther BY CENTROID than a
    tier-0 incumbent; under seat ordering that is wrong the moment junk
    sits nearer by centroid than the true arrow — the true arrows-inside
    arrow is 2h/3 behind its own apex, so junk at (103, 4) (centroid
    4.39 pt) enumerated before it would have had the true arrow (4.80 pt)
    skipped before its seat was ever computed. The replacement skips
    only when ``d - R >= incumbent seat``, which the triangle inequality
    makes exact: every seat point lies within the candidate's radius R
    of its centroid."""

    def _scene(self, junk_first):
        junk = _arrow_chevron((103.0, 4.0), (1.0, 0.0))
        return core() + ([A, junk, B] if junk_first else [A, B, junk])

    @pytest.mark.parametrize("junk_first", [True, False])
    def test_the_true_arrow_wins_whichever_is_enumerated_first(self,
                                                              junk_first):
        p = _one(self._scene(junk_first), 0.5)
        assert p["end_b_xy"] == pytest.approx([100.0, 0.0], abs=0.01)
        assert "arrow_off_spine" not in p["evidence"]
        assert p["confidence"] == pytest.approx(0.952, abs=0.001)

    def test_the_old_prune_would_have_skipped_the_true_arrow(self):
        # The premise, measured: junk nearer by centroid, farther by seat.
        junk = [tuple(v) for v in _arrow_chevron((103.0, 4.0), (1.0, 0.0)).vertices]
        true = [tuple(v) for v in B.vertices]
        tip = (100.0, 0.0)
        d = lambda vs: math.hypot(q._centroid(vs)[0] - tip[0],
                                  q._centroid(vs)[1] - tip[1])
        seat = lambda vs: q._seat_distance(vs, "triangle", (1.0, 0.0), tip,
                                           q._arrow_geometry(vs))
        assert d(junk) < d(true)
        assert seat(true) < seat(junk)

    @pytest.mark.parametrize("shape", [
        B, _arrow_base_leg((100.0 + _H, 0.0), (1.0, 0.0)),
        _arrow_chevron((103.0, 4.0), (1.0, 0.0)),
        _arrow_chevron((108.0, 0.5), (1.0, 0.0)),
        diamond((100.0, 0.0), (1.0, 0.0)),
        diamond((106.0, 0.0), (1.0, 0.0)),
    ])
    @pytest.mark.parametrize("tip", [(100.0, 0.0), (97.0, 2.0), (104.0, -3.0)])
    def test_the_bound_seat_ge_d_minus_R_holds(self, shape, tip):
        vs = [tuple(v) for v in shape.vertices]
        c = q._centroid(vs)
        R = max(math.hypot(v[0] - c[0], v[1] - c[1]) for v in vs)
        d = math.hypot(c[0] - tip[0], c[1] - tip[1])
        s = q._seat_distance(vs, "triangle", (1.0, 0.0), tip,
                             q._arrow_geometry(vs))
        assert s >= d - R - 1e-9

    @pytest.mark.parametrize("shift", [0.0, 1.0, 2.0, 5.0])
    def test_the_bound_holds_for_a_cluster_with_its_real_radius(self, shift):
        # A cluster's seat is its NEAREST member, up to a whole radius
        # nearer than its centroid. With a radius of 0 the prune skipped a
        # real cluster centred 1 pt off its end behind a foreign chevron
        # seated 0.94 (caught by the verifier's D1 table, shift 1.0/2.0).
        vs = [q._centroid([tuple(e.start), tuple(e.end)])
              for e in fill_cluster((100.0 + shift, 0.0), (1.0, 0.0))]
        c = q._centroid(vs)
        R = max(math.hypot(v[0] - c[0], v[1] - c[1]) for v in vs)
        d = math.hypot(c[0] - 100.0, c[1])
        s = q._seat_distance(vs, "fill_cluster", (1.0, 0.0), (100.0, 0.0), None)
        assert s >= d - R - 1e-9
        if shift >= 1.0:
            assert s < d - 0.5     # the centroid over-states it by > 0.5

    def test_the_prune_never_changes_the_answer(self, monkeypatch):
        from planlens.ir.tests.test_text_bearing_scenes import (
            dimension_with_a_letterform_chevron,
            leader_beside_a_blunt_detail_line,
            skewed_dimension_beside_a_real_leader)
        scenes = [self._scene(True), self._scene(False),
                  cluster_dimension(True),
                  cluster_dimension_beside_a_real_leader(),
                  skewed_dimension_with_a_splash(12.0, 5.0),
                  skewed_dimension_with_a_splash(12.0, 0.0),
                  dimension_with_a_letterform_chevron(True),
                  leader_beside_a_blunt_detail_line(),
                  skewed_dimension_beside_a_real_leader(15, 124.0),
                  skewed_dimension_beside_a_real_leader(25, 126.4)]
        # The D1 anatomy with the chevron enumerated BEFORE the cluster,
        # at the offsets where a zero cluster radius pruned the cluster.
        scenes += [cluster_dimension_at_offset(s, 0.94, chevron_first=True,
                                               with_leader=True)
                   for s in (0.5, 1.0, 2.0)]
        for ents in scenes:
            def run():
                return q.find_dimensions(_ir(ents), max_arrowhead_size=MAS,
                                         min_confidence=0.0)
            monkeypatch.setattr(q, "_EXACT_SEAT_PRUNE", True)
            pruned = run()
            monkeypatch.setattr(q, "_EXACT_SEAT_PRUNE", False)
            assert run() == pruned


class TestALoneOutwardChevronIsAnArrowsOutsideEnd:
    """DOCUMENTED, not a defect: with NO better-seated candidate at the
    end, an outward-pointing chevron whose base sits at the shaft end is
    read as an arrows-outside terminator and published at its apex — it
    IS one, by every observable (the ground-truth 'T=' construct is
    exactly this anatomy). The round-4 fixture at (108, 0) expected
    100.0 only because the true arrow was also present; with it absent
    there is nothing in the geometry to prefer."""

    def test_base_at_the_end_publishes_the_apex(self):
        outside = _arrow_base_leg((100.0 + _H, 0.0), (1.0, 0.0))
        p = _one(core() + [A, outside], 0.5)
        assert p["end_b_xy"] == pytest.approx([100.0 + _H, 0.0], abs=0.01)

    def test_a_small_base_gap_reads_the_same_way(self):
        p = _one(core() + [A, _arrow_chevron((108.0, 0.0), (1.0, 0.0))], 0.0)
        assert p["end_b_xy"] == pytest.approx([108.0, 0.0], abs=0.01)
