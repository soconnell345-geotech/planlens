"""Contracts for :mod:`planlens.ir.measure`.

These pin the three rules the module exists to enforce, because each one is
a rule about what CANNOT happen: a unitless number, a page-point value
silently read as feet, and a derived value more confident than the scale
beneath it.
"""

from __future__ import annotations

import pytest

from planlens.ir.measure import (
    Quantity, combine_confidence, combine_rel_uncertainty, convert_length,
    normalize_unit, unknown_scale,
)


class TestUnitsAreMandatory:
    def test_empty_units_is_refused(self):
        with pytest.raises(ValueError, match="units are mandatory"):
            Quantity(1.0, "")

    def test_aliases_normalise(self):
        assert normalize_unit("FEET") == "ft"
        assert normalize_unit("Meters") == "m"
        assert normalize_unit("inches") == "in"

    def test_an_unknown_unit_passes_through_rather_than_raising(self):
        # A caller may carry a unit this table never heard of; refusing it
        # would be worse than declining to convert it.
        assert normalize_unit("stations") == "stations"


class TestPageSpaceIsNotAPhysicalLength:
    def test_points_are_not_physical(self):
        assert not Quantity(100.0, "pt").is_physical
        assert Quantity(100.0, "ft").is_physical

    def test_points_cannot_be_converted_to_feet_by_unit_table(self):
        # THE central refusal: page points become feet only through a
        # resolved scale, never a lookup.
        with pytest.raises(ValueError, match="not a physical length"):
            Quantity(1200.0, "pt").to("ft")

    def test_physical_conversions_are_exact(self):
        assert Quantity(100.0, "ft").to("m").value == pytest.approx(30.48)
        assert convert_length(1.0, "mi", "ft") == pytest.approx(5280.0)

    def test_unknown_scale_is_confident_geometry_with_no_meaning(self):
        q = unknown_scale(87.4)
        assert q.units == "pt"
        assert q.scale_known is False
        # The measurement really is exact; only its meaning is missing.
        assert q.confidence == 1.0


class TestConfidenceComposesByWeakestLink:
    def test_min_not_product(self):
        # Five sound inputs must not decay to 0.59.
        assert combine_confidence(0.9, 0.9, 0.9, 0.9, 0.9) == pytest.approx(0.9)
        assert combine_confidence(1.0, 0.55, 0.9) == pytest.approx(0.55)

    def test_none_parts_are_skipped_as_not_applicable(self):
        assert combine_confidence(0.8, None, 0.9) == pytest.approx(0.8)

    def test_no_evidence_earns_no_confidence(self):
        assert combine_confidence() == 0.0

    def test_a_scaled_value_can_never_beat_its_scale(self):
        exact = Quantity(1200.0, "pt", confidence=1.0)
        ft = exact.scaled(40.0 / 72.0, "ft", factor_confidence=0.55)
        assert ft.confidence == pytest.approx(0.55)
        assert ft.units == "ft"
        assert ft.scale_known is True


class TestUncertainty:
    def test_independent_terms_combine_in_quadrature(self):
        assert combine_rel_uncertainty(0.03, 0.04) == pytest.approx(0.05)

    def test_range_is_reported_in_the_units_of_the_decision(self):
        q = Quantity(87.4, "ft", rel_uncertainty=0.02)
        lo, hi = q.range()
        assert lo == pytest.approx(85.652)
        assert hi == pytest.approx(89.148)
        assert "range" in q.to_dict()

    def test_an_exact_value_reports_no_range(self):
        assert "range" not in Quantity(10.0, "ft").to_dict()


class TestSerialisation:
    def test_units_are_structurally_inseparable_from_the_value(self):
        d = Quantity(42.0, "ft", confidence=0.8, basis="scale:bar").to_dict()
        assert d["units"] == "ft"
        assert d["scale_known"] is True
        assert d["basis"] == "scale:bar"
        # There must be no bare number readable without its unit.
        assert not any(k.endswith("_ft") or k.endswith("_m") for k in d)
