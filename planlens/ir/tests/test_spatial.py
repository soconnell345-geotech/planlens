"""Contracts for :mod:`planlens.ir.spatial`.

The headline test is :class:`TestTheAmbiguousNameDoesNotExist` — the module's
central design decision is that a caller cannot accidentally read one spacing
convention as another, and the way that is enforced is by refusing to publish
the ambiguous name at all.
"""

from __future__ import annotations

import math

import pytest

from planlens.ir.spatial import (
    convex_hull, min_area_rect, nearest_neighbour, point_pattern_stats,
    polygon_area, spacing_summary, stats_by_class,
)

SQUARE = [(0.0, 0.0), (10.0, 0.0), (10.0, 10.0), (0.0, 10.0)]
GRID33 = [(i * 10.0, j * 10.0) for i in range(3) for j in range(3)]


def _walk_keys(obj):
    """Every key anywhere in a nested structure."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _walk_keys(v)


class TestTheAmbiguousNameDoesNotExist:
    """"Average spacing" is not one number, so it is not one key."""

    @pytest.mark.parametrize("banned", ["average_spacing", "mean_spacing",
                                        "spacing", "avg_spacing"])
    def test_no_such_key_at_any_depth(self, banned):
        keys = set(_walk_keys(point_pattern_stats(GRID33)))
        assert banned not in keys

    def test_every_convention_carries_its_own_definition(self):
        s = point_pattern_stats(GRID33)
        for conv in ("nearest_neighbour", "density_equivalent", "pairwise"):
            assert s[conv]["definition"]

    def test_the_conventions_differ_even_on_a_perfectly_regular_grid(self):
        # If they differ by 2.45x on the most uniform arrangement possible,
        # silently picking one is never defensible.
        s = point_pattern_stats(GRID33)
        assert s["convention_spread"]["ratio"] > 2.0

    def test_summary_flags_when_the_choice_matters(self):
        s = spacing_summary(point_pattern_stats(GRID33))
        assert s["choice_matters"] is True
        assert {c["convention"] for c in s["answer_candidates"]} == {
            "nearest_neighbour_mean", "density_equivalent", "pairwise_mean"}
        assert all(c["when_to_use"] for c in s["answer_candidates"])


class TestExactGeometry:
    def test_hull_and_area_of_a_square(self):
        assert polygon_area(convex_hull(SQUARE)) == pytest.approx(100.0)

    def test_interior_points_do_not_change_the_hull(self):
        assert polygon_area(convex_hull(SQUARE + [(5.0, 5.0)])) == \
            pytest.approx(100.0)

    def test_min_area_rect_of_a_square(self):
        r = min_area_rect(SQUARE)
        assert r["area"] == pytest.approx(100.0)
        assert r["aspect"] == pytest.approx(1.0)

    def test_min_area_rect_follows_a_rotated_set(self):
        # A 20x10 rectangle rotated 30 deg still has area 200.
        a = math.radians(30.0)
        pts = [(x * math.cos(a) - y * math.sin(a),
                x * math.sin(a) + y * math.cos(a))
               for x, y in [(0, 0), (20, 0), (20, 10), (0, 10)]]
        assert min_area_rect(pts)["area"] == pytest.approx(200.0, rel=1e-9)

    def test_grid_nearest_neighbour_is_the_grid_spacing(self):
        d, _ = nearest_neighbour(GRID33)
        assert all(v == pytest.approx(10.0) for v in d)

    def test_density_equivalent_is_sqrt_area_over_n(self):
        s = point_pattern_stats(GRID33)
        assert s["density_equivalent"]["value"]["value"] == \
            pytest.approx(math.sqrt(400.0 / 9.0))


class TestDegenerateCasesAreNotZero:
    def test_collinear_points_have_no_area_spacing(self):
        s = point_pattern_stats([(0, 0), (5, 0), (10, 0)])
        # None, not 0.0 - "undefined" and "zero" are different claims.
        assert s["density_equivalent"] is None
        assert any("degenerate" in n for n in s["notes"])

    def test_fewer_than_two_points_defines_no_spacing(self):
        s = point_pattern_stats([(1.0, 1.0)])
        assert s["nearest_neighbour"] is None
        assert s["convention_spread"] is None

    def test_coincident_points_are_merged_not_reported_as_zero_spacing(self):
        s = point_pattern_stats([(0, 0), (0, 0), (10, 0), (20, 0), (10, 10)])
        assert s["n_duplicates_merged"] == 1
        assert s["n"] == 4
        assert s["nearest_neighbour"]["min"]["value"] > 0.0


class TestLinearAndConcaveFootprints:
    def test_a_frontage_line_is_flagged_as_overstating_area(self):
        pts = [(i * 30.0, (i % 2) * 2.0) for i in range(8)]
        de = point_pattern_stats(pts)["density_equivalent"]
        assert de["hull_may_overstate_area"] is True
        assert de["enclosing_rect_aspect"] > 3.0

    def test_a_filled_square_is_not_flagged(self):
        pts = [(i * 10.0, j * 10.0) for i in range(4) for j in range(4)]
        de = point_pattern_stats(pts)["density_equivalent"]
        assert de["hull_may_overstate_area"] is False


class TestScalePropagation:
    def test_without_a_scale_lengths_stay_in_page_units(self):
        s = point_pattern_stats(GRID33, unit="pt")
        assert s["scale_known"] is False
        assert s["units"] == "pt"
        assert s["nearest_neighbour"]["mean"]["scale_known"] is False

    def test_with_a_scale_lengths_convert_and_inherit_its_confidence(self):
        # 1 in = 40 ft at 72 pt/in  ->  40/72 ft per pt.
        s = point_pattern_stats(
            GRID33, unit="pt", units_per_input=40.0 / 72.0, unit_name="ft",
            unit_confidence=0.55, unit_rel_uncertainty=0.02,
            unit_basis="scale:graphic_bar")
        assert s["units"] == "ft"
        nn = s["nearest_neighbour"]["mean"]
        assert nn["units"] == "ft"
        assert nn["value"] == pytest.approx(10.0 * 40.0 / 72.0)
        # A spacing can never be more trustworthy than the scale under it.
        assert nn["confidence"] == pytest.approx(0.55)
        assert nn["basis"] == "scale:graphic_bar"

    def test_area_takes_the_scale_squared(self):
        s = point_pattern_stats(GRID33, unit="pt", units_per_input=2.0,
                                unit_name="ft")
        assert s["density_equivalent"]["hull_area"]["value"] == \
            pytest.approx(400.0 * 4.0)


class TestByClass:
    def test_all_and_each_class_are_both_reported(self):
        pts = GRID33 + [(500.0, 500.0), (530.0, 500.0)]
        classes = ["a"] * 9 + ["b", "b"]
        out = stats_by_class(pts, classes)
        assert out["all"]["n"] == 11
        assert set(out["by_class"]) == {"a", "b"}
        assert out["by_class"]["b"]["n"] == 2
        assert out["by_class"]["a"]["nearest_neighbour"]["mean"]["value"] == \
            pytest.approx(10.0)
