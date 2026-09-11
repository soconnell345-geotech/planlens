"""Quantities that know what they rest on.

WHY THIS MODULE EXISTS. planlens' confidence ladder — ``_CONTRADICTED_CAP``
0.25 < ``_UNCORROBORATED_CAP`` 0.45 < the 0.50 call threshold — disciplines
DETECTION: whether a leader or a dimension is really on the sheet. It says
nothing about a DERIVED number, such as a spacing in feet computed from a
scale nobody verified.

That gap is more dangerous than any detection error, and the asymmetry is
the point:

- A detection miss is VISIBLE. Nothing is reported, and the caller notices.
- A scale error is INVISIBLE. A plausible number is reported, in the wrong
  units, with nothing to flag it. An engineer reads "average spacing 47 ft"
  and has no way to know the page was re-plotted 11x17 to letter and the
  true answer is 50 ft.

So every length, area and derived number this package reports travels as a
:class:`Quantity`, which carries its unit, what it rests on, and how much
of the answer is actually known.

THE THREE RULES, and why each is what it is:

1. **``units`` is mandatory and inseparable from ``value``.** There is
   deliberately no ``value_ft`` key anywhere in the API that could be read
   without its unit. A bare float is how unit errors happen.

2. **Confidence composes by ``min``, not by product.** A derived value is
   exactly as trustworthy as the weakest thing it stands on. A product over
   five inputs decays toward zero and stops meaning anything (five sound
   0.9 inputs would report 0.59, which is a lie in the pessimistic
   direction); a max lies in the optimistic direction. ``min`` is the only
   composition that answers the question a reviewer is actually asking —
   *what is the weakest link here?*

3. **A quantity computed from an unknown scale is NOT converted.** It comes
   back in page points with ``scale_known=False``. Refusing to convert is
   the honest output; guessing 1:1 is how points get reported as feet.

Relative uncertainty propagates in quadrature for independent sources
(:func:`combine_rel_uncertainty`), which is the standard treatment and is
appropriate here because the dominant terms — scale error and measurement
error — genuinely are independent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Tuple

__all__ = [
    "Quantity",
    "UNIT_ALIASES",
    "LENGTH_IN_METRES",
    "combine_confidence",
    "combine_rel_uncertainty",
    "convert_length",
    "normalize_unit",
]


#: Canonical spellings for the units this package reports. ``pt`` is a PDF
#: point (1/72 inch of PAPER) and is deliberately NOT in
#: :data:`LENGTH_IN_METRES` — a page point is not a physical length until a
#: scale says how much ground it covers, and conflating the two is the
#: error this whole module exists to prevent.
UNIT_ALIASES = {
    "pt": "pt", "pts": "pt", "point": "pt", "points": "pt",
    "px": "px", "pixel": "px", "pixels": "px",
    "in": "in", "inch": "in", "inches": "in", '"': "in",
    "ft": "ft", "foot": "ft", "feet": "ft", "'": "ft",
    "yd": "yd", "yard": "yd", "yards": "yd",
    "mm": "mm", "millimetre": "mm", "millimeter": "mm",
    "cm": "cm", "centimetre": "cm", "centimeter": "cm",
    "m": "m", "metre": "m", "meter": "m", "metres": "m", "meters": "m",
    "km": "km", "kilometre": "km", "kilometer": "km",
    "mi": "mi", "mile": "mi", "miles": "mi",
}

#: Physical lengths only. ``pt`` and ``px`` are absent on purpose (see
#: :data:`UNIT_ALIASES`) — asking to convert page points to feet without a
#: scale raises rather than guessing.
LENGTH_IN_METRES = {
    "in": 0.0254,
    "ft": 0.3048,
    "yd": 0.9144,
    "mm": 0.001,
    "cm": 0.01,
    "m": 1.0,
    "km": 1000.0,
    "mi": 1609.344,
}


def normalize_unit(unit: str) -> str:
    """Canonical spelling of ``unit``.

    Unknown units pass through unchanged rather than raising: a caller may
    legitimately carry a unit this table has never heard of (``"ac"``,
    ``"stations"``), and refusing it would be worse than not converting it.
    """
    if not unit:
        raise ValueError("units are mandatory on a Quantity — pass 'pt' if "
                         "the value is in page points")
    return UNIT_ALIASES.get(str(unit).strip().lower(), str(unit).strip())


def combine_confidence(*parts: Optional[float]) -> float:
    """Confidence of a value derived from several inputs: the WEAKEST one.

    ``min``, not a product — see this module's docstring. ``None`` parts are
    treated as "not applicable" and skipped, so a caller can pass an
    optional channel without special-casing it. With nothing to combine the
    answer is 0.0, because a derived value resting on no stated evidence has
    earned no confidence.
    """
    vals = [float(p) for p in parts if p is not None]
    if not vals:
        return 0.0
    return max(0.0, min(1.0, min(vals)))


def combine_rel_uncertainty(*parts: Optional[float]) -> float:
    """Relative uncertainty of a product/quotient: independent terms in
    quadrature.

    Appropriate here because the dominant contributions — the scale factor
    and the measurement of a plotted length — arise from unrelated causes.
    Correlated inputs would want a linear sum; if that case ever appears,
    add it explicitly rather than changing this default silently.
    """
    vals = [float(p) for p in parts if p is not None and p > 0.0]
    if not vals:
        return 0.0
    return math.sqrt(sum(v * v for v in vals))


@dataclass(frozen=True)
class Quantity:
    """A number that carries its unit, its provenance and its uncertainty.

    ``basis`` is prose for a human reader — the chain the number rests on,
    e.g. ``"scale:graphic_bar+title_block(agree)"``. It is not parsed; it is
    what a reviewer reads when deciding whether to believe the answer.

    ``scale_known`` is separate from ``confidence`` on purpose. A page-point
    measurement can be perfectly confident (``confidence=1.0``) while the
    scale is entirely unknown — the geometry is exact, its meaning in feet
    is not. Collapsing the two would force a false choice between reporting
    an exact measurement and admitting the scale is missing.
    """

    value: float
    units: str
    confidence: float = 1.0
    rel_uncertainty: float = 0.0
    basis: str = ""
    scale_known: bool = True
    notes: Tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "units", normalize_unit(self.units))
        object.__setattr__(self, "confidence",
                           max(0.0, min(1.0, float(self.confidence))))
        object.__setattr__(self, "rel_uncertainty",
                           max(0.0, float(self.rel_uncertainty)))
        object.__setattr__(self, "value", float(self.value))

    # -- reading ---------------------------------------------------------

    @property
    def is_physical(self) -> bool:
        """True when the unit is a real-world length rather than page space."""
        return self.units in LENGTH_IN_METRES

    def range(self) -> Tuple[float, float]:
        """``(low, high)`` at one relative uncertainty either side.

        This is what a reviewer actually reads: ``87.4 ft [85.7, 89.1]``
        says more than ``87.4 ft (confidence 0.9)``, because it is in the
        units of the decision being made.
        """
        d = abs(self.value) * self.rel_uncertainty
        return (self.value - d, self.value + d)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "value": round(self.value, 6),
            "units": self.units,
            "confidence": round(self.confidence, 3),
            "rel_uncertainty": round(self.rel_uncertainty, 5),
            "scale_known": self.scale_known,
        }
        lo, hi = self.range()
        if self.rel_uncertainty > 0.0:
            out["range"] = [round(lo, 6), round(hi, 6)]
        if self.basis:
            out["basis"] = self.basis
        if self.notes:
            out["notes"] = list(self.notes)
        return out

    def __str__(self) -> str:
        if self.rel_uncertainty > 0.0:
            lo, hi = self.range()
            return (f"{self.value:.4g} {self.units} "
                    f"[{lo:.4g}, {hi:.4g}]")
        return f"{self.value:.4g} {self.units}"

    # -- deriving --------------------------------------------------------

    def with_note(self, note: str) -> "Quantity":
        """Copy carrying one more note."""
        return Quantity(self.value, self.units, self.confidence,
                        self.rel_uncertainty, self.basis, self.scale_known,
                        tuple(self.notes) + (note,))

    def scaled(self, factor: float, units: str, *,
               factor_confidence: float = 1.0,
               factor_rel_uncertainty: float = 0.0,
               basis: str = "") -> "Quantity":
        """Apply a scale factor, moving into ``units``.

        THE ONLY WAY a quantity changes unit system. It exists as an
        explicit call rather than an automatic promotion because applying a
        scale is a claim about the drawing, and claims are made deliberately.

        The result's confidence is ``min(self, factor)`` and its relative
        uncertainty is the two in quadrature — so a spacing derived through
        a 0.55-confidence scale bar can never be reported above 0.55, no
        matter how exact the underlying geometry was.
        """
        if factor == 0.0:
            raise ValueError("scale factor must be non-zero")
        return Quantity(
            value=self.value * float(factor),
            units=units,
            confidence=combine_confidence(self.confidence, factor_confidence),
            rel_uncertainty=combine_rel_uncertainty(
                self.rel_uncertainty, factor_rel_uncertainty),
            basis=basis or self.basis,
            scale_known=True,
            notes=self.notes,
        )

    def to(self, units: str) -> "Quantity":
        """Convert between PHYSICAL length units only.

        Raises for a page-space unit (``pt``/``px``). That refusal is the
        module's whole point: page points become feet only by way of a
        scale the caller has resolved and applied through :meth:`scaled`,
        never by a unit-table lookup.
        """
        target = normalize_unit(units)
        if target == self.units:
            return self
        if not self.is_physical or target not in LENGTH_IN_METRES:
            raise ValueError(
                f"cannot convert {self.units!r} to {target!r} by unit table: "
                f"page/pixel space is not a physical length until a scale is "
                f"resolved and applied (use .scaled(...) with a resolved "
                f"scale, or leave the value in {self.units!r})")
        f = LENGTH_IN_METRES[self.units] / LENGTH_IN_METRES[target]
        return Quantity(self.value * f, target, self.confidence,
                        self.rel_uncertainty, self.basis, self.scale_known,
                        self.notes)


def convert_length(value: float, frm: str, to: str) -> float:
    """Bare physical-length conversion, for internal arithmetic.

    Prefer :meth:`Quantity.to` at any boundary a caller can see — this
    returns a naked float and so drops every guarantee the module provides.
    """
    a, b = normalize_unit(frm), normalize_unit(to)
    if a == b:
        return float(value)
    if a not in LENGTH_IN_METRES or b not in LENGTH_IN_METRES:
        raise ValueError(f"not physical length units: {a!r} -> {b!r}")
    return float(value) * LENGTH_IN_METRES[a] / LENGTH_IN_METRES[b]


def unknown_scale(value: float, page_units: str = "pt",
                  basis: str = "no scale resolved") -> Quantity:
    """A page-space measurement with no scale behind it.

    Confidence stays at 1.0 — the geometry really is exact — while
    ``scale_known`` is False and the unit stays ``pt``. Those three facts
    together say precisely what is true: *we measured this exactly, and we
    do not know what it means on the ground.*
    """
    return Quantity(value=value, units=page_units, confidence=1.0,
                    rel_uncertainty=0.0, basis=basis, scale_known=False)
