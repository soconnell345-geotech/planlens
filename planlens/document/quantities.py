"""Numbers with units, pulled out of what the document SAYS.

WHY THIS MODULE EXISTS. A review is mostly a comparison. The report says
"borings at approximately 40-foot centers"; the plan shows borings the geometry
layer can measure. The calculation says "171 kN pre-load"; the schedule on the
sheet says something else. Neither half of that comparison is useful without
the other, and :mod:`planlens.ir` has long been able to measure the drawing
while the narrative stayed an undifferentiated wall of text. This module makes
the narrative's own numbers addressable, so a reviewer's model can put the two
side by side and see where they disagree.

WHAT A MENTION IS, AND WHAT IT IS NOT. A :class:`QuantityMention` is a value
together with the unit the text attached to it, located on a page. Three rules
follow from :mod:`planlens.ir.measure`, and they are the same rules:

1. **A bare number is not a mention.** "twelve borings to 35" yields the depth
   only if the text says what 35 is. Without a unit there is nothing to
   compare, and a mention that might be feet or metres is worse than silence.
   The ONE exception is an elevation, where the label itself is the claim: "EL.
   1684" is a measurement whose unit the page leaves to its datum, so it is
   reported with ``units=""`` and ``units_known=False`` rather than invented.
2. **Nothing is converted.** ``6300 mm`` stays 6300 mm; ``7'-6"`` becomes 7.5
   ft because the two halves are one value written in one unit, not a
   conversion. A caller wanting metres builds a :class:`~planlens.ir.measure.
   Quantity` and calls ``.to``, which is where a unit change is a deliberate
   act rather than a silent one.
3. **``units_known`` says whether the arithmetic is available.** It is True
   only for the physical lengths ``planlens.ir.measure`` can convert. A ``psf``
   or a ``kN/m^3`` is canonically spelled and perfectly readable, but the
   measure module does not know how to convert it, and this flag is how a
   caller finds out without discovering it from an exception.

A range is ONE mention, not two: "40 to 60 ft" is a single statement about a
single thing, and splitting it into 40 ft and 60 ft loses that it is a range —
so the second value rides along as ``value_to``.

HOW IT READS. A native regular-expression pass over the same text groups
:meth:`planlens.document.Document.search` scores — a block's lines joined, each
unblocked line, each hidden CAD string, each markup's comment — so a value
broken across a line break is still one mention, and every mention carries the
line ids and the box that locate it on the page. See "Quantities" in
DESIGN.md for the bake-off against a general-purpose extractor that decided
this stayed native.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from planlens.document.model import SOURCE_PDF_TEXT
from planlens.ir.measure import UNIT_ALIASES, normalize_unit

__all__ = [
    "QuantityMention", "KINDS", "EXTRACTOR_REGEX", "scan_text",
]

BBox = Tuple[float, float, float, float]

# ---------------------------------------------------------------------------
# Kinds
# ---------------------------------------------------------------------------

KIND_LENGTH = "length"
KIND_AREA = "area"
KIND_VOLUME = "volume"
KIND_PRESSURE = "pressure_or_stress"
KIND_FORCE = "force"
KIND_UNIT_WEIGHT = "unit_weight"
KIND_ANGLE = "angle"
KIND_PERCENT = "percent"
KIND_ELEVATION = "elevation"
KIND_STATION = "station"
KIND_SLOPE = "slope"
KIND_COUNT = "count"
KIND_OTHER = "other"

#: What a mention can be. ``slope`` earns its own name rather than falling into
#: ``other``: "2H:1V" is one of the most-compared statements on a geotechnical
#: sheet, and a reader asking for every slope in a report should not have to
#: sift it out of everything unclassified.
KINDS = (KIND_LENGTH, KIND_AREA, KIND_VOLUME, KIND_PRESSURE, KIND_FORCE,
         KIND_UNIT_WEIGHT, KIND_ANGLE, KIND_PERCENT, KIND_ELEVATION,
         KIND_STATION, KIND_SLOPE, KIND_COUNT, KIND_OTHER)

#: Value of ``QuantityMention.extractor`` for this module's own pass.
EXTRACTOR_REGEX = "regex"


# ---------------------------------------------------------------------------
# The mention
# ---------------------------------------------------------------------------

def _r(v: Optional[float], n: int = 4) -> Optional[float]:
    if v is None:
        return None
    out = round(float(v), n)
    return 0.0 if out == 0 else out


@dataclass
class QuantityMention:
    """One value-with-unit the document states, and where it states it.

    ``text`` is the raw span exactly as written, because "approximately
    40-foot" and "40 ft" are the same number said with different confidence
    and a reviewer reads the difference. ``qualifier`` names that hedge when
    the text carries one; it is never inferred from context.

    ``bbox`` is in the displayed-page frame like every other coordinate in
    this package, so a mention can be rendered or zoomed into directly.
    """
    value: float
    units: str
    kind: str = KIND_OTHER
    text: str = ""
    page: int = 0
    units_known: bool = False
    value_to: Optional[float] = None
    qualifier: Optional[str] = None
    line_ids: List[str] = field(default_factory=list)
    bbox: Optional[BBox] = None
    source: str = SOURCE_PDF_TEXT
    markup_id: Optional[str] = None
    extractor: str = EXTRACTOR_REGEX
    #: Character offsets inside the text group this came from (not serialized);
    #: the caller uses them to resolve which lines the span touched.
    span: Tuple[int, int] = (0, 0)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "value": _r(self.value),
            "units": self.units,
            "kind": self.kind,
            "page": self.page,
            "text": self.text,
        }
        if self.value_to is not None:
            d["value_to"] = _r(self.value_to)
        if self.qualifier:
            d["qualifier"] = self.qualifier
        if self.units_known:
            d["units_known"] = True
        if self.line_ids:
            d["line_ids"] = self.line_ids
        if self.markup_id:
            d["markup_id"] = self.markup_id
        if self.bbox is not None:
            d["bbox"] = [round(v, 1) for v in self.bbox]
        if self.source != SOURCE_PDF_TEXT:
            d["source"] = self.source
        if self.extractor != EXTRACTOR_REGEX:
            d["extractor"] = self.extractor
        return d

    def __str__(self) -> str:
        head = f"{self.value:g}"
        if self.value_to is not None:
            head += f"-{self.value_to:g}"
        if self.units:
            head += f" {self.units}"
        if self.qualifier:
            head = f"{self.qualifier} {head}"
        return f"{head} [{self.kind}]"


# ---------------------------------------------------------------------------
# Units this module recognizes
# ---------------------------------------------------------------------------

#: ``(spelling, canonical units, kind)``. Every spelling is matched
#: case-insensitively and the scanner sorts them longest-first, so ``kN/m3``
#: is tried before ``kN`` and ``mm`` before ``m``. The canonical spelling goes
#: through :func:`planlens.ir.measure.normalize_unit`, which returns a physical
#: length's canonical name and passes anything else through unchanged — so the
#: output spelling is stable whatever the page wrote.
_UNIT_TABLE: Sequence[Tuple[str, str, str]] = (
    # -- length ------------------------------------------------------------
    ("millimetres", "mm", KIND_LENGTH), ("millimeters", "mm", KIND_LENGTH),
    ("centimetres", "cm", KIND_LENGTH), ("centimeters", "cm", KIND_LENGTH),
    ("kilometres", "km", KIND_LENGTH), ("kilometers", "km", KIND_LENGTH),
    ("metres", "m", KIND_LENGTH), ("meters", "m", KIND_LENGTH),
    ("metre", "m", KIND_LENGTH), ("meter", "m", KIND_LENGTH),
    ("inches", "in", KIND_LENGTH), ("inch", "in", KIND_LENGTH),
    ("feet", "ft", KIND_LENGTH), ("foot", "ft", KIND_LENGTH),
    ("yards", "yd", KIND_LENGTH), ("yard", "yd", KIND_LENGTH),
    ("miles", "mi", KIND_LENGTH), ("mile", "mi", KIND_LENGTH),
    ("mm", "mm", KIND_LENGTH), ("cm", "cm", KIND_LENGTH),
    ("km", "km", KIND_LENGTH), ("m", "m", KIND_LENGTH),
    ("ft", "ft", KIND_LENGTH), ("in", "in", KIND_LENGTH),
    ("yd", "yd", KIND_LENGTH), ("mi", "mi", KIND_LENGTH),
    ('"', "in", KIND_LENGTH), ("'", "ft", KIND_LENGTH),
    # -- area --------------------------------------------------------------
    ("square feet", "ft^2", KIND_AREA), ("square foot", "ft^2", KIND_AREA),
    ("square metres", "m^2", KIND_AREA), ("square meters", "m^2", KIND_AREA),
    ("square inches", "in^2", KIND_AREA), ("square yards", "yd^2", KIND_AREA),
    ("hectares", "ha", KIND_AREA), ("hectare", "ha", KIND_AREA),
    ("acres", "ac", KIND_AREA), ("acre", "ac", KIND_AREA),
    ("sq ft", "ft^2", KIND_AREA), ("sq. ft.", "ft^2", KIND_AREA),
    ("sq m", "m^2", KIND_AREA), ("sq in", "in^2", KIND_AREA),
    ("sq yd", "yd^2", KIND_AREA),
    ("ft2", "ft^2", KIND_AREA), ("ft^2", "ft^2", KIND_AREA),
    ("ft²", "ft^2", KIND_AREA),
    ("m2", "m^2", KIND_AREA), ("m^2", "m^2", KIND_AREA),
    ("m²", "m^2", KIND_AREA),
    ("in2", "in^2", KIND_AREA), ("in²", "in^2", KIND_AREA),
    ("yd2", "yd^2", KIND_AREA), ("yd²", "yd^2", KIND_AREA),
    ("sf", "ft^2", KIND_AREA), ("sy", "yd^2", KIND_AREA),
    ("ha", "ha", KIND_AREA),
    # -- volume ------------------------------------------------------------
    ("cubic yards", "yd^3", KIND_VOLUME), ("cubic yard", "yd^3", KIND_VOLUME),
    ("cubic metres", "m^3", KIND_VOLUME), ("cubic meters", "m^3", KIND_VOLUME),
    ("cubic feet", "ft^3", KIND_VOLUME), ("cubic foot", "ft^3", KIND_VOLUME),
    ("yd3", "yd^3", KIND_VOLUME), ("yd³", "yd^3", KIND_VOLUME),
    ("m3", "m^3", KIND_VOLUME), ("m^3", "m^3", KIND_VOLUME),
    ("m³", "m^3", KIND_VOLUME),
    ("ft3", "ft^3", KIND_VOLUME), ("ft³", "ft^3", KIND_VOLUME),
    ("cy", "yd^3", KIND_VOLUME), ("cf", "ft^3", KIND_VOLUME),
    # -- pressure / stress -------------------------------------------------
    ("kn/m2", "kN/m^2", KIND_PRESSURE), ("kn/m^2", "kN/m^2", KIND_PRESSURE),
    ("kn/m²", "kN/m^2", KIND_PRESSURE),
    ("t/m2", "t/m^2", KIND_PRESSURE), ("t/m²", "t/m^2", KIND_PRESSURE),
    ("kpa", "kPa", KIND_PRESSURE), ("mpa", "MPa", KIND_PRESSURE),
    ("gpa", "GPa", KIND_PRESSURE), ("pa", "Pa", KIND_PRESSURE),
    ("psf", "psf", KIND_PRESSURE), ("psi", "psi", KIND_PRESSURE),
    ("ksf", "ksf", KIND_PRESSURE), ("ksi", "ksi", KIND_PRESSURE),
    ("tsf", "tsf", KIND_PRESSURE), ("ksc", "ksc", KIND_PRESSURE),
    ("bar", "bar", KIND_PRESSURE),
    # -- unit weight / density ---------------------------------------------
    ("kn/m3", "kN/m^3", KIND_UNIT_WEIGHT),
    ("kn/m^3", "kN/m^3", KIND_UNIT_WEIGHT),
    ("kn/m³", "kN/m^3", KIND_UNIT_WEIGHT),
    ("kg/m3", "kg/m^3", KIND_UNIT_WEIGHT),
    ("kg/m³", "kg/m^3", KIND_UNIT_WEIGHT),
    ("lb/ft3", "lb/ft^3", KIND_UNIT_WEIGHT),
    ("g/cm3", "g/cm^3", KIND_UNIT_WEIGHT),
    ("pcf", "pcf", KIND_UNIT_WEIGHT),
    # -- force -------------------------------------------------------------
    ("tonnes", "tonne", KIND_FORCE), ("tonne", "tonne", KIND_FORCE),
    ("tons", "ton", KIND_FORCE), ("ton", "ton", KIND_FORCE),
    ("kips", "kip", KIND_FORCE), ("kip", "kip", KIND_FORCE),
    ("kn", "kN", KIND_FORCE), ("mn", "MN", KIND_FORCE),
    ("lbs", "lb", KIND_FORCE), ("lb", "lb", KIND_FORCE),
    ("kg", "kg", KIND_FORCE),
    # -- angle / percent ---------------------------------------------------
    ("degrees", "deg", KIND_ANGLE), ("degree", "deg", KIND_ANGLE),
    ("degs", "deg", KIND_ANGLE), ("deg", "deg", KIND_ANGLE),
    ("°", "deg", KIND_ANGLE),
    ("percent", "%", KIND_PERCENT), ("pct", "%", KIND_PERCENT),
    ("%", "%", KIND_PERCENT),
)

#: English words that make a preceding "in" the preposition, not the unit.
#: Without this, "borings were advanced 12 in the northern block" reports a
#: 12-inch depth. Measured on real narrative text, this is by far the largest
#: single source of false positives a unit table produces.
_IN_IS_A_PREPOSITION = (
    "the", "a", "an", "this", "that", "these", "those", "his", "her", "their",
    "its", "our", "your", "accordance", "addition", "areas", "area", "any",
    "all", "both", "conjunction", "contact", "each", "excess", "front",
    "general", "lieu", "order", "part", "place", "situ", "some", "such",
    "terms", "them", "which", "who", "whole",
)

#: Countable things a geotechnical document numbers. A count needs a noun for
#: the same reason every other mention needs a unit: "12" alone is not a
#: measurement of anything.
_COUNTABLE = (
    "test pits", "test pit", "boring logs", "boring log",
    "borings", "boring", "boreholes", "borehole", "piles", "pile", "anchors",
    "anchor", "tiebacks", "tieback", "struts", "strut", "rakers", "raker",
    "braces", "brace", "panels", "panel", "sheets", "sheet", "bays", "bay",
    "levels", "level", "layers", "layer", "lifts", "lift", "samples",
    "sample", "specimens", "specimen", "tests", "test", "blows", "blow",
    "locations", "location", "stages", "stage", "rows", "row",
)

#: Hedges a document puts on a number. Recorded, never resolved: "approximately
#: 40 ft" and "40 ft" are different claims and the reviewer is the one who
#: decides what the difference is worth.
#: ``over`` and ``under`` are deliberately absent. In AEC prose they are
#: usually spatial — "4,500 sf over the footprint", "the fill under the slab" —
#: and reading them as inequalities put a false hedge on a third of the values
#: they touched.
_QUALIFIERS: Sequence[Tuple[str, str]] = (
    (r"approximately|approx\.?|about|roughly|circa|ca\.", "approximately"),
    (r"minimum|min\.?|at\s+least|not\s+less\s+than|no\s+less\s+than",
     "minimum"),
    (r"maximum|max\.?|at\s+most|not\s+more\s+than|no\s+greater\s+than",
     "maximum"),
    (r"typical|typ\.?|nominal|nom\.?", "typical"),
    (r"±|\+/-|plus\s+or\s+minus", "plus_minus"),
    (r"greater\s+than|more\s+than|exceeding|in\s+excess\s+of|>",
     "greater_than"),
    (r"less\s+than|<", "less_than"),
)


# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

#: A number as PROSE and a CALCULATION PRINTOUT write it, which is not quite
#: how a ratio string does: thousands separators are ordinary here ("2,500
#: psf"), a leading sign appears on elevations, and an engineering program
#: writes "2.540E-07 M". Fractions come from the same grammar
#: ``planlens.document.scale`` uses, so ``3/32"`` reads identically in both.
#:
#: The exponent is not a nicety. Without it the scanner read "2.540E-07 M" as
#: SEVEN METRES — it found the "07" and the "M" and saw a length — which was
#: measured on a real submittal's program output, where that form is
#: everywhere. A false quantity off by seven orders of magnitude is exactly
#: the invisible error :mod:`planlens.ir.measure` exists to prevent.
_NUM = (r"(?:(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?"
        r"|\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.\d+|\d+)"
        r"(?:[Ee][-+]?\d+)?)")

_FEET = r"(?:'|ft\b\.?|feet\b|foot\b)"
_INCH = r'(?:"|in\b\.?|inch\b|inches\b)'

_UNIT_ALT = "|".join(
    re.escape(spelling) + (r"\b" if spelling[-1].isalnum() else "")
    for spelling, _c, _k in sorted(_UNIT_TABLE, key=lambda r: -len(r[0])))

_CANON = {spelling.lower(): (canon, kind)
          for spelling, canon, kind in _UNIT_TABLE}

#: Feet and inches written as one value: 7'-6", 5' 6", 12 ft 3 in.
_RE_FEET_INCHES = re.compile(
    r"(?P<ft>" + _NUM + r")\s*" + _FEET +
    r"\s*(?:-\s*)?(?P<inch>" + _NUM + r")\s*" + _INCH, re.I)

#: A station: 10+50, STA 3+25.50. The offset is exactly two digits before any
#: decimal, which is what distinguishes a station from arithmetic.
_RE_STATION = re.compile(
    r"(?:\b(?:sta(?:tion)?\b\.?)\s*)?(?P<whole>\d{1,4})\+(?P<off>\d{2}(?:\.\d+)?)\b",
    re.I)

#: An elevation, with or without a unit: EL. 1684, Elev. 12.5 m, EL +3.20.
_RE_ELEVATION = re.compile(
    r"\b(?:el|elev|elevation)\b\.?\s*[:=]?\s*"
    r"(?P<sign>[-+])?\s*(?P<num>" + _NUM + r")"
    r"(?:\s*(?P<unit>" + _UNIT_ALT + r"))?", re.I)

#: A slope, either way round: 2H:1V, 1V:2H, 3:1 (H:V).
_RE_SLOPE = re.compile(
    r"(?P<a>" + _NUM + r")\s*(?P<al>[HV])\s*:\s*(?P<b>" + _NUM + r")\s*(?P<bl>[HV])\b",
    re.I)

#: The general case, and the range: 40 ft, 40-foot, 2,500 psf, 20 to 35 feet.
_RE_VALUE = re.compile(
    r"(?P<n1>" + _NUM + r")"
    r"(?:\s*(?:to|thru|through|–|—|-)\s*(?P<n2>" + _NUM + r"))?"
    r"\s*-?\s*(?P<unit>" + _UNIT_ALT + r")", re.I)

#: A count: 12 borings, three levels is not counted (words are not numbers).
_RE_COUNT = re.compile(
    r"(?P<n>" + _NUM + r")\s+(?P<noun>" + "|".join(
        sorted(_COUNTABLE, key=len, reverse=True)) + r")\b", re.I)

_QUALIFIER_ALT = "|".join(
    f"(?P<q{i}>{pat})" for i, (pat, _name) in enumerate(_QUALIFIERS))
_QUALIFIER_NAMES = [name for _pat, name in _QUALIFIERS]

#: A hedge BEFORE the value must be the thing immediately before it — at most
#: one small connector in between. A plain window instead of this anchor let
#: "8 ft typ. and the anchor is 45 ft" put "typical" on the 45, which is a
#: hedge lifted from the previous clause and attached to a number it says
#: nothing about.
_RE_QUALIFIER_BEFORE = re.compile(
    r"(?<![A-Za-z])(?:" + _QUALIFIER_ALT + r")"
    r"(?:\s+(?:of|at|to|be|is|are|a|an|the|about))?[\s(]*$", re.I)

#: And AFTER: "40 ft typ.", "6300 mm +/- 50", "3 in. minimum". Only a little
#: punctuation may intervene, so "6300 mm. The minimum cover..." does not hand
#: the 6300 a hedge from the next sentence — and the hedge must END the
#: clause. A calculation printout writes "2.540E-07 M MAXIMUM NUMBER OF
#: ITERATIONS 300", where MAXIMUM opens the NEXT label rather than closing
#: this value; a following capitalized word is that case, and the hedge is
#: dropped. A note set entirely in capitals loses its trailing hedge this way,
#: which is the intended trade: no qualifier beats a wrong one.
_RE_QUALIFIER_AFTER = re.compile(
    r"^[\s,.;:()\-]{0,3}(?:" + _QUALIFIER_ALT + r")(?![A-Za-z])(?!\s+[A-Z])",
    re.I)

#: How much text either side is looked at at all.
_QUALIFIER_LOOKBACK = 30
_QUALIFIER_LOOKAHEAD = 14


def _to_float(text: str) -> Optional[float]:
    """A prose number to a float: thousands separators and fractions."""
    from planlens.document.scale import parse_number
    return parse_number(" ".join(text.replace(",", "").split()))


def _canonical(spelling: str) -> Tuple[str, str]:
    canon, kind = _CANON[spelling.strip().rstrip(".").lower()]
    return normalize_unit(canon), kind


def _known(units: str) -> bool:
    return units.strip().lower() in UNIT_ALIASES


def _qualifier(text: str, start: int, end: int) -> Optional[str]:
    """The hedge the text puts on a value, from just before or just after it.

    Never inferred from further away: a qualifier that is not adjacent belongs
    to some other number, and a wrong hedge is worse than none.
    """
    windows = (
        _RE_QUALIFIER_BEFORE.search(text[max(0, start - _QUALIFIER_LOOKBACK):start]),
        _RE_QUALIFIER_AFTER.search(text[end:end + _QUALIFIER_LOOKAHEAD]),
    )
    for m in windows:
        if m is None:
            continue
        for i, name in enumerate(_QUALIFIER_NAMES):
            if m.group(f"q{i}"):
                return name
    return None


def _in_is_a_preposition(text: str, m: "re.Match") -> bool:
    """True when the ``in`` this match ends on is English, not inches."""
    unit = (m.groupdict().get("unit") or "").strip().lower()
    if unit not in ("in", "in."):
        return False
    if unit == "in.":
        return False                       # a period settles it: inches
    tail = text[m.end():m.end() + 24].lstrip()
    word = re.match(r"[A-Za-z']+", tail)
    return bool(word) and word.group(0).lower() in _IN_IS_A_PREPOSITION


def _mention(value: float, units: str, kind: str, text: str, start: int,
             end: int, whole: str, value_to: Optional[float] = None
             ) -> QuantityMention:
    return QuantityMention(
        value=value, units=units, kind=kind, text=text.strip(),
        units_known=_known(units), value_to=value_to,
        qualifier=_qualifier(whole, start, end), span=(start, end))


def scan_text(text: str) -> List[QuantityMention]:
    """Every value-with-unit in one string, left to right, non-overlapping.

    Pure: no page, no lines, no document. The caller attaches the location,
    which is what lets the same scanner read a paragraph, a hidden CAD string
    and a reviewer's comment.

    Where two patterns claim the same characters the more specific one wins —
    ``7'-6"`` is one length in feet, not a 7 ft followed by a 6 in, and
    ``EL. 1684 ft`` is an elevation rather than a bare length. That is what
    the priority number on each candidate is for.
    """
    if not text:
        return []
    found: List[Tuple[int, int, int, QuantityMention]] = []   # start, prio, -len

    def add(prio: int, start: int, end: int, men: QuantityMention) -> None:
        found.append((start, prio, -(end - start), men))

    for m in _RE_FEET_INCHES.finditer(text):
        feet, inches = _to_float(m.group("ft")), _to_float(m.group("inch"))
        if feet is None or inches is None:
            continue
        add(0, m.start(), m.end(),
            _mention(feet + inches / 12.0, "ft", KIND_LENGTH, m.group(0),
                     m.start(), m.end(), text))

    for m in _RE_ELEVATION.finditer(text):
        num = _to_float(m.group("num"))
        if num is None:
            continue
        if m.group("sign") == "-":
            num = -num
        units, _kind = (_canonical(m.group("unit")) if m.group("unit")
                        else ("", KIND_ELEVATION))
        add(1, m.start(), m.end(),
            _mention(num, units, KIND_ELEVATION, m.group(0), m.start(),
                     m.end(), text))

    for m in _RE_STATION.finditer(text):
        whole, off = _to_float(m.group("whole")), _to_float(m.group("off"))
        if whole is None or off is None:
            continue
        add(2, m.start(), m.end(),
            _mention(whole * 100.0 + off, "sta", KIND_STATION, m.group(0),
                     m.start(), m.end(), text))

    for m in _RE_SLOPE.finditer(text):
        a, b = _to_float(m.group("a")), _to_float(m.group("b"))
        if a is None or b is None:
            continue
        al, bl = m.group("al").upper(), m.group("bl").upper()
        if al == bl:
            continue
        horiz, vert = (a, b) if al == "H" else (b, a)
        if not vert:
            continue
        add(3, m.start(), m.end(),
            _mention(horiz / vert, "H:V", KIND_SLOPE, m.group(0), m.start(),
                     m.end(), text))

    for m in _RE_VALUE.finditer(text):
        if _in_is_a_preposition(text, m):
            continue
        n1 = _to_float(m.group("n1"))
        if n1 is None:
            continue
        n2 = _to_float(m.group("n2")) if m.group("n2") else None
        if n2 is not None and n2 < n1:
            n2 = None                      # "40-60" backwards is not a range
        units, kind = _canonical(m.group("unit"))
        add(4, m.start(), m.end(),
            _mention(n1, units, kind, m.group(0), m.start(), m.end(), text,
                     value_to=n2))

    for m in _RE_COUNT.finditer(text):
        n = _to_float(m.group("n"))
        if n is None:
            continue
        noun = m.group("noun").lower()
        add(5, m.start(), m.end(),
            _mention(n, noun, KIND_COUNT, m.group(0), m.start(), m.end(),
                     text))

    out: List[QuantityMention] = []
    taken_to = -1
    for start, _prio, _neg_len, men in sorted(found, key=lambda r: r[:3]):
        if start < taken_to:
            continue
        out.append(men)
        taken_to = men.span[1]
    return out


def filter_mentions(mentions: Iterable[QuantityMention],
                    kinds: Optional[Sequence[str]] = None,
                    units: Optional[Sequence[str]] = None
                    ) -> List[QuantityMention]:
    """By kind and by unit; unit matching ignores case and a trailing period."""
    want_kinds = {str(k).strip().lower() for k in kinds} if kinds else None
    want_units = ({str(u).strip().rstrip(".").lower() for u in units}
                  if units else None)
    out = []
    for men in mentions:
        if want_kinds is not None and men.kind.lower() not in want_kinds:
            continue
        if want_units is not None and men.units.lower() not in want_units:
            continue
        out.append(men)
    return out
