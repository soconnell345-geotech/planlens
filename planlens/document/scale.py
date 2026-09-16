"""The measurement calibration a PDF already stores — so the scale need not be guessed.

WHY THIS MODULE EXISTS. :mod:`planlens.ir.measure` refuses to turn page points
into feet without a resolved scale, and it is right to: a wrong scale reports a
plausible number in the wrong units with nothing to flag it. Reading the scale
off a title block or a graphic bar is inference. But when someone has calibrated
a sheet in Bluebeam ("Store Scale in Page") or used Acrobat's measuring tools,
the calibration is WRITTEN INTO THE FILE as structured data (ISO 32000-1
§12.9 / PDF 1.7 §8.7.5). That is not inference — it is the drafter's own
statement, and it is the strongest scale evidence a document can carry.

Two places hold it:

**Page level — the page dictionary's ``/VP`` array.** Each entry is a Viewport
dictionary: ``/Type /Viewport``, a ``/BBox`` marking the region it governs, an
optional ``/Name``, and a ``/Measure`` dictionary. ``/Measure /Subtype /RL``
(rectilinear) carries ``/R``, a human ratio string such as ``1 in = 20 ft``, and
``/X`` / ``/Y``, arrays of number-format dictionaries whose ``/C`` is the
conversion factor and ``/U`` the unit label, plus ``/D`` and ``/A`` number
formats for distances and areas. ``/Subtype /GEO`` is a georeferenced measure
(latitude/longitude through ``/GPTS`` and a coordinate system); this module
detects it and reports ``kind="geo"`` without parsing the geodesy.

**Annotation level — measurement markups.** A dimension a reviewer drew carries
its own ``/Measure`` and an ``/IT`` of ``/LineDimension``,
``/PolyLineDimension`` or ``/PolygonDimension``, and its ``/Contents`` usually
states the value the tool displayed (``12'-6"``, ``24.5 ft``). Stated and
derived are both reported so a reader can see them agree.

WHAT THE REAL DOCUMENTS SAID (measured 2026-09-16 before this module was
designed; 162 PDFs across a real marked-up submittal, a ten-sheet public
drawing corpus and every other PDF in the working tree):

- **``/VP`` is usually absent, and often present but EMPTY.** Seven consecutive
  drawing sheets of the real submittal carry ``/VP []`` — the array exists and
  holds nothing. An empty array is normal, not malformed, and produces no
  warning.
- **A populated ``/VP`` does not mean the page is calibrated.** The one real
  rectilinear viewport found is the identity: ``/X [<</C .01389/U( )>>]`` with
  ``/R ( )``. ``0.01389 x 72 = 1.00008`` — one point maps to 1/72 of a unit,
  which is the untouched 1:1 default a producer writes when nobody set a scale,
  and its unit label and ratio are both a single blank space.
  :attr:`Viewport.is_identity` detects exactly that, and
  :attr:`Viewport.is_calibrated` is False for it, so the reader is told to go
  read the title block instead of trusting a scale that says nothing.
- **``/C`` MULTIPLIES, and its basis is one PDF user-space unit (one point).**
  This is what the identity case settles empirically: a point is by definition
  1/72 inch of paper, and the stored factor is 1/72 with the unit label left
  blank. Had ``/C`` meant "user-space units per ``/U`` unit" the file would have
  said 72. So ``x_per_point = C`` directly, in units of ``/U``. No real
  CALIBRATED (non-identity) rectilinear page has been run through this module
  yet — see DESIGN.md — so :func:`page_viewports` cross-checks ``/R`` against
  ``/X`` on every file it reads and warns when they disagree, which is how a
  wrong reading of ``/C`` would announce itself on the first real calibrated
  sheet.
- **``/BBox`` is in UNROTATED user space, confirmed on a real ``/Rotate 270``
  page.** Its box fits the unrotated mediabox and overflows the displayed rect.
  It is converted with :func:`planlens.document.frame.to_display_bbox` like
  every other coordinate this package emits.
- **``/Measure`` is indirect in one real file and inline in the other**, so both
  are followed.
- **No measurement markup exists anywhere in the corpus** — 360 annotations on
  the real submittal, 66 on the public sheets, none with a ``/Measure`` or a
  ``*Dimension`` ``/IT``. That path is built to the specification and to the
  synthetic fixture, and is marked as such.

HOW THE RAW SYNTAX IS READ. PyMuPDF's ``xref_get_key`` cannot index into an
array, so ``/VP`` comes back as a raw PDF string either way. Rather than chain
key lookups, this module lexes and parses the small subset of PDF object syntax
these structures use (dictionaries, arrays, names, numbers, strings, indirect
references) with :func:`parse_pdf_object`, and follows references through
``doc.xref_object(xref, compressed=False)``. It is deliberately tolerant:
anything it cannot make sense of becomes a warning and a missing field, never an
exception, because a malformed ``/Measure`` must not stop a page from being read.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.frame import to_display_bbox
from planlens.ir.measure import (
    LENGTH_IN_METRES, UNIT_ALIASES, Quantity, convert_length, normalize_unit,
    unknown_scale,
)

__all__ = [
    "Viewport", "MarkupMeasure", "Ref",
    "SOURCE_VIEWPORT", "SOURCE_MEASUREMENT_MARKUP",
    "MEASURE_INTENTS",
    "parse_pdf_object", "parse_ratio", "ratio_magnification",
    "parse_stated_value", "page_viewports", "viewport_at",
    "read_markup_measure", "scale_factor", "to_quantity",
]

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

#: Values of ``Viewport.source``.
SOURCE_VIEWPORT = "viewport"                    # the page's /VP array
SOURCE_MEASUREMENT_MARKUP = "measurement_markup"  # a dimension a person drew

#: ``/IT`` values the PDF specification gives measurement markups. Bluebeam is
#: free to write others, so the reader keys on the presence of ``/Measure``
#: FIRST and treats this set as a second way in; whatever ``/IT`` a file
#: actually carries is recorded on the markup unchanged.
MEASURE_INTENTS = ("LineDimension", "PolyLineDimension", "PolygonDimension")

#: One PDF point is 1/72 inch of PAPER, by definition. The empirical anchor for
#: what ``/C`` means (see the module docstring).
_METRES_PER_POINT_OF_PAPER = 0.0254 / 72.0

#: How far a stored factor may sit from the 1:1 default and still be called the
#: identity. The real file measured 1.00008 (``/C`` written to four decimals),
#: so the tolerance is set well above that rounding and well below any real
#: drawing scale (the loosest, 1 in = 1 ft, magnifies by 12).
_IDENTITY_TOLERANCE = 0.01

#: A viewport covering at least this much of the page is taken to govern the
#: whole sheet when a point falls outside every box. Measured: the one real
#: whole-page viewport found covers 0.845 of its page — producers inset the
#: /BBox by the plot margin — while a detail viewport nested on a sheet is a
#: small fraction of it, so the two do not overlap around this value.
WHOLE_PAGE_COVERAGE = 0.7

#: ``/R`` and ``/X`` are two statements of one scale. Beyond this relative
#: disagreement the viewport is reported with a warning rather than silently
#: preferring one — a producer writing them inconsistently is exactly the case
#: where a derived number would be wrong and invisible.
RATIO_DISAGREEMENT = 0.02


# ---------------------------------------------------------------------------
# A tolerant reader for the subset of PDF object syntax these structures use
# ---------------------------------------------------------------------------

class Ref:
    """An indirect reference ``N G R``, kept unresolved until it is needed."""

    __slots__ = ("num",)

    def __init__(self, num: int) -> None:
        self.num = int(num)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Ref({self.num})"

    def __eq__(self, other: Any) -> bool:
        return isinstance(other, Ref) and other.num == self.num

    def __hash__(self) -> int:
        return hash(("Ref", self.num))


_TOKEN = re.compile(rb"""
    (?P<dict_open><<) | (?P<dict_close>>>) |
    (?P<arr_open>\[)  | (?P<arr_close>\]) |
    (?P<name>/[^\s/\[\]<>(){}%]*) |
    (?P<num>[+-]?(?:\d+\.\d*|\.\d+|\d+)) |
    (?P<hex><[0-9A-Fa-f\s]*>) |
    (?P<kw>true|false|null|R|obj|endobj|stream) |
    (?P<junk>\S)
""", re.VERBOSE)

#: A parse is abandoned past this many tokens; a /Measure is tens of tokens and
#: a runaway lex on a mis-identified object should not cost a page.
_MAX_TOKENS = 20000


def _lex(data: bytes) -> List[Tuple[str, bytes]]:
    out: List[Tuple[str, bytes]] = []
    i, n = 0, len(data)
    while i < n and len(out) < _MAX_TOKENS:
        c = data[i:i + 1]
        if c.isspace():
            i += 1
            continue
        if c == b"%":                                  # comment to end of line
            j = data.find(b"\n", i)
            i = n if j < 0 else j + 1
            continue
        if c == b"(":                                  # literal string
            depth, j = 1, i + 1
            buf = bytearray()
            while j < n and depth:
                ch = data[j:j + 1]
                if ch == b"\\":
                    buf += data[j:j + 2]
                    j += 2
                    continue
                if ch == b"(":
                    depth += 1
                elif ch == b")":
                    depth -= 1
                    if not depth:
                        break
                buf += ch
                j += 1
            out.append(("str", bytes(buf)))
            i = j + 1
            continue
        m = _TOKEN.match(data, i)
        if not m:                                      # pragma: no cover
            i += 1
            continue
        out.append((m.lastgroup or "junk", m.group(0)))
        i = m.end()
    return out


def _parse_tokens(tokens: List[Tuple[str, bytes]], i: int) -> Tuple[Any, int]:
    if i >= len(tokens):
        return None, i
    kind, tok = tokens[i]
    if kind == "dict_open":
        d: Dict[str, Any] = {}
        i += 1
        while i < len(tokens) and tokens[i][0] != "dict_close":
            if tokens[i][0] != "name":
                i += 1
                continue
            key = tokens[i][1][1:].decode("latin-1")
            val, i = _parse_tokens(tokens, i + 1)
            d[key] = val
        return d, i + 1
    if kind == "arr_open":
        a: List[Any] = []
        i += 1
        while i < len(tokens) and tokens[i][0] != "arr_close":
            before = i
            val, i = _parse_tokens(tokens, i)
            if i <= before:                            # pragma: no cover
                break
            a.append(val)
        return a, i + 1
    if kind == "name":
        return "/" + tok[1:].decode("latin-1"), i + 1
    if kind == "num":
        # "12 0 R" is one indirect reference, not three tokens.
        if (i + 2 < len(tokens) and tokens[i + 1][0] == "num"
                and tokens[i + 2] == ("kw", b"R")):
            return Ref(int(float(tok))), i + 3
        txt = tok.decode("latin-1")
        return (int(txt) if re.fullmatch(r"[+-]?\d+", txt) else float(txt)), i + 1
    if kind == "str":
        return tok.decode("latin-1"), i + 1
    if kind == "hex":
        raw = re.sub(rb"\s", b"", tok[1:-1])
        try:
            data = bytes.fromhex(raw.decode("ascii"))
            return data.decode("utf-16-be" if raw[:4].lower() == b"feff"
                               else "latin-1"), i + 1
        except Exception:                              # pragma: no cover
            return tok.decode("latin-1"), i + 1
    if kind == "kw":
        return {b"true": True, b"false": False}.get(tok, None), i + 1
    return None, i + 1


def parse_pdf_object(raw: Optional[str]) -> Any:
    """Raw PDF object syntax -> Python values; ``None`` when it cannot be read.

    Dictionaries become ``dict`` keyed by name without the slash, arrays become
    ``list``, names become ``"/Name"`` strings, indirect references become
    :class:`Ref`. A leading ``N G obj`` header is skipped, so the output of
    ``doc.xref_object`` and of ``doc.xref_get_key`` both go in unchanged.
    """
    if not raw:
        return None
    tokens = _lex(raw.encode("latin-1", "replace"))
    for j, tok in enumerate(tokens):
        if tok == ("kw", b"obj"):
            tokens = tokens[j + 1:]
            break
    if not tokens:
        return None
    try:
        val, _ = _parse_tokens(tokens, 0)
    except Exception:                                  # pragma: no cover
        return None
    return val


def _resolve(doc, val: Any, depth: int = 0) -> Any:
    """Follow indirect references (bounded, so a reference loop cannot hang)."""
    while isinstance(val, Ref) and depth < 8:
        try:
            val = parse_pdf_object(doc.xref_object(val.num, compressed=False))
        except Exception:                              # pragma: no cover
            return None
        depth += 1
    return None if isinstance(val, Ref) else val


def _key(doc, xref: int, key: str) -> Any:
    """One key of an object, parsed and dereferenced; ``None`` when absent."""
    try:
        kind, val = doc.xref_get_key(xref, key)
    except Exception:                                  # pragma: no cover
        return None
    if kind in (None, "null") or not val:
        return None
    if kind == "xref":
        try:
            return _resolve(doc, Ref(int(val.split()[0])))
        except (ValueError, IndexError):               # pragma: no cover
            return None
    return parse_pdf_object(val)


# ---------------------------------------------------------------------------
# Ratio strings
# ---------------------------------------------------------------------------

_NUM = r"(?:\d+\s+\d+\s*/\s*\d+|\d+\s*/\s*\d+|\d*\.\d+|\d+)"
_UNIT = "|".join(re.escape(u) for u in
                 sorted(UNIT_ALIASES, key=len, reverse=True))
_FEET = r"(?:'|ft\b|feet\b|foot\b)"
_INCH = r'(?:"|in\b|inch\b|inches\b)'

_RE_FEET_INCHES = re.compile(
    r"(" + _NUM + r")\s*" + _FEET + r"(?:\s*-\s*|\s+)?"
    r"(?:(" + _NUM + r")\s*" + _INCH + r"?)?", re.I)
_RE_VALUE_UNIT = re.compile(r"(" + _NUM + r")\s*(" + _UNIT + r")?", re.I)
_RE_BARE_RATIO = re.compile(r"(" + _NUM + r")\s*:\s*(" + _NUM + r")")
_RE_SCALE_LABEL = re.compile(r"^\s*scale\s*[:=]?\s*", re.I)

#: Area unit spellings met on dimension markups, mapped to a canonical
#: ``<length>^2`` (or left as written when there is no length behind it).
_AREA_UNITS = {
    "sf": "ft^2", "sq ft": "ft^2", "sqft": "ft^2", "ft2": "ft^2",
    "ft^2": "ft^2", "ft²": "ft^2", "square feet": "ft^2", "sq. ft.": "ft^2",
    "sy": "yd^2", "sq yd": "yd^2", "yd2": "yd^2", "yd²": "yd^2",
    "sm": "m^2", "sq m": "m^2", "sqm": "m^2", "m2": "m^2", "m^2": "m^2",
    "m²": "m^2", "square metres": "m^2", "square meters": "m^2",
    "si": "in^2", "sq in": "in^2", "in2": "in^2", "in²": "in^2",
    "ac": "ac", "acre": "ac", "acres": "ac", "ha": "ha", "hectare": "ha",
}


def _number(text: Optional[str]) -> Optional[float]:
    """``"3/32"``, ``"1 1/2"``, ``"20"``, ``".5"`` -> float; else None."""
    if not text:
        return None
    t = " ".join(text.split())
    m = re.fullmatch(r"(\d+)\s+(\d+)\s*/\s*(\d+)", t)
    if m:
        den = float(m.group(3))
        return None if den == 0 else float(m.group(1)) + float(m.group(2)) / den
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", t)
    if m:
        den = float(m.group(2))
        return None if den == 0 else float(m.group(1)) / den
    try:
        return float(t)
    except ValueError:
        return None


def _parse_length(text: str, anchored: bool = True
                  ) -> Optional[Tuple[float, Optional[str]]]:
    """One side of a ratio, or a stated value: ``(value, canonical unit)``.

    Handles feet-and-inches (``1'-0"``, ``5' 6"``) as a single value in feet,
    fractions (``3/32"``), and a plain number with or without a unit. The unit
    is ``None`` when the text states none — a dimensionless side.
    """
    t = " ".join(str(text).split())
    if not t:
        return None
    m = _RE_FEET_INCHES.fullmatch(t) if anchored else _RE_FEET_INCHES.search(t)
    if m:
        feet = _number(m.group(1))
        inches = _number(m.group(2)) if m.group(2) else 0.0
        if feet is not None and inches is not None:
            return (feet + inches / 12.0, "ft")
    m = _RE_VALUE_UNIT.fullmatch(t) if anchored else _RE_VALUE_UNIT.search(t)
    if m:
        value = _number(m.group(1))
        if value is not None:
            unit = normalize_unit(m.group(2)) if m.group(2) else None
            return (value, unit)
    return None


def parse_ratio(text: Optional[str]
                ) -> Optional[Tuple[float, Optional[str], float, Optional[str]]]:
    """A ``/R`` ratio string -> ``(page_len, page_unit, real_len, real_unit)``.

    Understands the forms producers actually write: ``1 in = 20 ft``,
    ``1" = 20'``, ``1:100``, ``1 cm = 1 m``, ``1/8" = 1'-0"``, ``3/32" = 1'``,
    and tolerates a leading ``SCALE:`` label. A dimensionless ratio (``1:100``)
    comes back with both units ``None`` — the drawing states a magnification and
    no unit, and inventing one would be a guess.

    Anything else — including the single blank space a real file was found to
    write — returns ``None`` rather than a fabricated reading.
    """
    if not text:
        return None
    t = _RE_SCALE_LABEL.sub("", " ".join(str(text).split())).strip()
    if not t:
        return None
    if "=" in t:
        left, right = t.split("=", 1)
        a, b = _parse_length(left), _parse_length(right)
        if a is None or b is None or a[0] <= 0 or b[0] <= 0:
            return None
        return (a[0], a[1], b[0], b[1])
    m = _RE_BARE_RATIO.fullmatch(t)
    if m:
        page, real = _number(m.group(1)), _number(m.group(2))
        if page and real and page > 0 and real > 0:
            return (page, None, real, None)
    return None


def ratio_magnification(parsed: Optional[Sequence[Any]]) -> Optional[float]:
    """How many real-world lengths one paper length covers, as a pure number.

    ``1 in = 20 ft`` -> 240. ``1:100`` -> 100. ``1/8" = 1'-0"`` -> 96. Unit-free
    on purpose: it is what lets ``/R`` and ``/X`` — which need not use the same
    units — be compared against each other.
    """
    if not parsed:
        return None
    page_len, page_unit, real_len, real_unit = parsed
    if not page_len:
        return None
    if page_unit is None and real_unit is None:
        return real_len / page_len
    if page_unit is None or real_unit is None:
        return None
    try:
        return convert_length(real_len, real_unit, page_unit) / page_len
    except ValueError:
        return None


def parse_stated_value(text: Optional[str]
                       ) -> Optional[Tuple[float, str]]:
    """A measurement markup's ``/Contents`` -> ``(value, unit)``, or None.

    Reads what a measuring tool writes onto the page: ``24.5 ft``, ``12'-6"``,
    ``3.05 m``, ``1,250 SF``. A label before the value (``Length: 24.5 ft``) is
    dropped. Text that is a comment rather than a value returns None — a markup
    may carry both a measurement and a remark.
    """
    if not text:
        return None
    t = " ".join(str(text).split()).replace(",", "")
    t = re.split(r"[\n;]", t)[0].strip()
    t = re.sub(r"^[A-Za-z .]{0,20}[:=]\s*", "", t).strip()
    if not t:
        return None
    low = t.lower()
    for spelling in sorted(_AREA_UNITS, key=len, reverse=True):
        m = re.search(r"(" + _NUM + r")\s*" + re.escape(spelling)
                      + r"\.?(?![a-z0-9])\s*$", low)
        if m:
            value = _number(m.group(1))
            if value is not None:
                return (value, _AREA_UNITS[spelling])
    got = _parse_length(t, anchored=True) or _parse_length(t, anchored=False)
    if got is None or got[1] is None:
        return None
    return (got[0], got[1])


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

def _r(v: Optional[float], n: int = 6) -> Optional[float]:
    if v is None:
        return None
    out = round(float(v), n)
    return 0.0 if out == 0 else out


def _rb(b: Optional[Sequence[float]]) -> Optional[List[float]]:
    return None if b is None else [_r(v, 1) for v in b]


def _compact(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items()
            if v is not None and v is not False and v != [] and v != {}
            and v != ""}


@dataclass
class Viewport:
    """A calibrated region of a page, as the PDF itself stores it.

    ``bbox`` is in the DISPLAYED frame like every other coordinate in this
    package, converted from the unrotated ``/BBox`` the file holds.

    ``x_per_point`` / ``y_per_point`` are real-world units per PDF point —
    multiply a page-point distance by them to get a length in ``x_unit`` /
    ``y_unit``. That direction is the file's own (``/C``), verified against a
    real document; see this module's docstring.

    A viewport being PRESENT is not a scale. :attr:`is_calibrated` is what a
    caller should test: it is False for a georeferenced measure, for a viewport
    whose factors could not be read, and for the 1:1 identity a producer writes
    when nobody set a scale — a case met on a real sheet.
    """
    page: int
    bbox: BBox
    kind: str = "unknown"            # "rectilinear" | "geo" | "unknown"
    name: Optional[str] = None
    ratio: Optional[str] = None
    x_unit: Optional[str] = None
    x_per_point: Optional[float] = None
    y_unit: Optional[str] = None
    y_per_point: Optional[float] = None
    distance_unit: Optional[str] = None
    area_unit: Optional[str] = None
    source: str = SOURCE_VIEWPORT
    warnings: List[str] = field(default_factory=list)

    # -- reading ---------------------------------------------------------

    @property
    def magnification(self) -> Optional[float]:
        """Real-world length per paper length, as a pure number (1:N -> N).

        ``None`` when the unit label is missing or is not a length this package
        knows — the real file's blank ``( )`` label, for instance.
        """
        if self.x_per_point is None or self.x_unit not in LENGTH_IN_METRES:
            return None
        return (self.x_per_point * LENGTH_IN_METRES[self.x_unit]
                / _METRES_PER_POINT_OF_PAPER)

    @property
    def is_identity(self) -> bool:
        """True for the untouched 1:1 default (one point = 1/72 of a unit)."""
        mag = self.magnification
        if mag is not None:
            return abs(mag - 1.0) <= _IDENTITY_TOLERANCE
        if self.x_per_point is None:
            return False
        return abs(self.x_per_point * 72.0 - 1.0) <= _IDENTITY_TOLERANCE

    @property
    def is_calibrated(self) -> bool:
        """True when this viewport states a usable drawing scale."""
        return (self.kind == "rectilinear" and self.x_per_point is not None
                and self.x_per_point > 0 and not self.is_identity)

    @property
    def label(self) -> str:
        """A short human reading: the ratio if there is one, else the factor."""
        if self.kind == "geo":
            return "georeferenced"
        if self.ratio and parse_ratio(self.ratio):
            return " ".join(self.ratio.split())
        if self.is_identity:
            return "1:1 (uncalibrated default)"
        if self.x_per_point and self.x_unit:
            return f"1 pt = {self.x_per_point:.6g} {self.x_unit}"
        mag = self.magnification
        if mag:
            return f"1:{mag:.6g}"
        return "no usable scale"

    def contains(self, point: Sequence[float]) -> bool:
        x0, y0, x1, y1 = self.bbox
        return x0 <= float(point[0]) <= x1 and y0 <= float(point[1]) <= y1

    @property
    def area(self) -> float:
        x0, y0, x1, y1 = self.bbox
        return max(0.0, x1 - x0) * max(0.0, y1 - y0)

    def to_dict(self) -> Dict[str, Any]:
        d = _compact({
            "kind": self.kind,
            "name": self.name,
            "scale": self.label,
            "ratio": " ".join(self.ratio.split()) if self.ratio else None,
            "bbox": _rb(self.bbox),
            "x_per_point": _r(self.x_per_point),
            "x_unit": self.x_unit,
            "y_per_point": (_r(self.y_per_point)
                            if self.y_per_point != self.x_per_point else None),
            "y_unit": self.y_unit if self.y_unit != self.x_unit else None,
            "distance_unit": (self.distance_unit
                              if self.distance_unit != self.x_unit else None),
            "area_unit": self.area_unit,
            "source": self.source,
            "warnings": list(self.warnings),
        })
        d["calibrated"] = self.is_calibrated
        return d


@dataclass
class MarkupMeasure:
    """The measurement a dimension markup carries: what it says, what it works out to.

    ``stated`` is the value the drafter's tool wrote into ``/Contents``;
    ``derived`` is the markup's own vertex path measured through its ``/Measure``
    factors. Both are reported because agreement is the check that the factors
    were read correctly, and disagreement is a fact the reviewer should see
    rather than a number this package silently picks between.

    The path is measured in UNROTATED page space, before any conversion to the
    displayed frame, because ``/X`` and ``/Y`` are per-axis and a page rotation
    swaps the axes. A length must not depend on how the page is displayed.
    """
    scale: Viewport
    intent: Optional[str] = None
    stated_text: Optional[str] = None
    stated: Optional[Quantity] = None
    derived: Optional[Quantity] = None
    derived_area: Optional[Quantity] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def agreement(self) -> Optional[float]:
        """Relative difference between stated and derived, when comparable."""
        if self.stated is None or self.derived is None:
            return None
        try:
            other = self.derived.to(self.stated.units)
        except ValueError:
            return None
        if self.stated.value == 0:
            return None
        return abs(other.value - self.stated.value) / abs(self.stated.value)

    def to_dict(self) -> Dict[str, Any]:
        agree = self.agreement
        return _compact({
            "scale": self.scale.label,
            "intent": self.intent,
            "ratio": (" ".join(self.scale.ratio.split())
                      if self.scale.ratio else None),
            "x_per_point": _r(self.scale.x_per_point),
            "units": self.scale.x_unit,
            "stated": str(self.stated) if self.stated else self.stated_text,
            "derived": str(self.derived) if self.derived else None,
            "derived_area": str(self.derived_area) if self.derived_area else None,
            "agreement": (round(agree, 4) if agree is not None else None),
            "warnings": list(self.warnings) + list(self.scale.warnings),
        })


# ---------------------------------------------------------------------------
# Reading /Measure
# ---------------------------------------------------------------------------

_SUBTYPE_KINDS = {"/RL": "rectilinear", "/GEO": "geo"}


def _name(val: Any) -> Optional[str]:
    return val[1:] if isinstance(val, str) and val.startswith("/") else None


def _text(val: Any) -> Optional[str]:
    return val if isinstance(val, str) and not val.startswith("/") else None


def _num(val: Any) -> Optional[float]:
    if isinstance(val, bool) or not isinstance(val, (int, float)):
        return None
    return float(val)


def _number_format(doc, val: Any) -> Tuple[Optional[float], Optional[str], int]:
    """First entry of a ``/X`` / ``/Y`` / ``/D`` / ``/A`` array -> (C, U, n).

    The first number format is the one that converts from user space; the rest
    chain down to smaller units (feet then inches) and are counted, not read.
    """
    arr = _resolve(doc, val)
    if isinstance(arr, dict):
        arr = [arr]
    if not isinstance(arr, list) or not arr:
        return None, None, 0
    first = _resolve(doc, arr[0])
    if not isinstance(first, dict):
        return None, None, len(arr)
    unit = _text(first.get("U"))
    unit = unit.strip() if unit else None
    if unit:
        unit = normalize_unit(unit)
    return _num(first.get("C")), (unit or None), len(arr)


def _read_measure(doc, measure: Any, viewport: Viewport) -> None:
    """Fill ``viewport`` from a ``/Measure`` dictionary, warning as it goes."""
    measure = _resolve(doc, measure)
    if not isinstance(measure, dict):
        viewport.warnings.append("/Measure is missing or unreadable")
        return
    subtype = _name(measure.get("Subtype"))
    viewport.kind = _SUBTYPE_KINDS.get(f"/{subtype}" if subtype else "",
                                       "unknown")
    if viewport.kind == "geo":
        viewport.warnings.append(
            "georeferenced measure (/Subtype /GEO): latitude/longitude "
            "control, not a drawing scale — not parsed")
        return
    if viewport.kind == "unknown":
        viewport.warnings.append(
            f"/Measure /Subtype {subtype or 'missing'} is not understood; "
            f"only /RL (rectilinear) is read")
        return

    viewport.ratio = _text(measure.get("R"))
    x_c, x_u, _n = _number_format(doc, measure.get("X"))
    y_c, y_u, _n = _number_format(doc, measure.get("Y"))
    d_c, d_u, _n = _number_format(doc, measure.get("D"))
    a_c, a_u, _n = _number_format(doc, measure.get("A"))
    if x_c is None:
        viewport.warnings.append(
            "/Measure has no readable /X conversion factor: the page states a "
            "measurement system but not how long a point is")
    elif x_c <= 0:
        viewport.warnings.append(f"/Measure /X /C is {x_c:g}, not a positive "
                                 f"factor — ignored")
        x_c = None
    viewport.x_per_point, viewport.x_unit = x_c, x_u
    # The spec's default for /Y is /X; a square scale is also what every
    # drawing sheet actually uses.
    viewport.y_per_point = y_c if y_c and y_c > 0 else x_c
    viewport.y_unit = y_u or x_u
    viewport.distance_unit = d_u or x_u
    viewport.area_unit = a_u
    if d_c is not None and abs(d_c - 1.0) > 1e-9 and d_u and d_u == x_u:
        viewport.warnings.append(
            f"/Measure /D restates the distance unit with /C {d_c:g}; "
            f"lengths are reported through /X")
    if a_c is not None and abs(a_c - 1.0) > 1e-9:
        viewport.warnings.append(
            f"/Measure /A carries /C {a_c:g}; areas are derived from /X and "
            f"/Y and labelled with /A's unit")
    cyx = _num(measure.get("CYX"))
    if cyx is not None and abs(cyx - 1.0) > 1e-9:
        viewport.warnings.append(
            f"/Measure /CYX is {cyx:g} (non-square page units); the y factor "
            f"is taken from /Y or /X, not scaled by it")
    if viewport.x_unit is None and viewport.x_per_point is not None:
        viewport.warnings.append(
            "/Measure /X states a factor with a blank unit label, so the "
            "real-world unit is unknown")

    # /R and /X are two statements of one scale: disagreement means one of them
    # is wrong, and a length derived from the wrong one would look plausible.
    parsed = parse_ratio(viewport.ratio)
    from_r = ratio_magnification(parsed)
    from_x = viewport.magnification
    if from_r is not None and from_x is not None and from_r > 0:
        if abs(from_x - from_r) / from_r > RATIO_DISAGREEMENT:
            viewport.warnings.append(
                f"the ratio {' '.join((viewport.ratio or '').split())!r} "
                f"magnifies by {from_r:.6g} but /X magnifies by {from_x:.6g} — "
                f"they disagree; lengths use /X")
    elif viewport.ratio and parsed is None and viewport.ratio.strip():
        viewport.warnings.append(
            f"/Measure /R {' '.join(viewport.ratio.split())!r} is not a ratio "
            f"this reader understands")


def _page_vp_raw(doc, page) -> Optional[str]:
    try:
        kind, val = doc.xref_get_key(page.xref, "VP")
    except Exception:                                  # pragma: no cover
        return None
    if kind in (None, "null") or not val:
        return None
    if kind == "xref":
        try:
            return doc.xref_object(int(val.split()[0]), compressed=False)
        except Exception:                              # pragma: no cover
            return None
    return val


def page_viewports(doc, page, page_index: int
                   ) -> Tuple[List[Viewport], List[str]]:
    """The calibrated viewports of one page, in the displayed frame.

    Returns ``(viewports, warnings)``. A page with no ``/VP``, or with the empty
    ``/VP []`` real files were found to write, returns ``([], [])`` — that is
    the normal state of almost every page and is not worth a warning. Anything
    malformed is reported in ``warnings`` or on the viewport itself; nothing
    here raises.
    """
    warnings: List[str] = []
    raw = _page_vp_raw(doc, page)
    if raw is None:
        return [], warnings
    entries = parse_pdf_object(raw)
    if entries is None:
        return [], ["/VP could not be parsed as a PDF array"]
    if not isinstance(entries, list):
        warnings.append("/VP is not an array; read as a single viewport")
        entries = [entries]
    page_bbox = (page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1)
    out: List[Viewport] = []
    for n, entry in enumerate(entries):
        vp = _resolve(doc, entry)
        if not isinstance(vp, dict):
            warnings.append(f"/VP entry {n} is not a dictionary")
            continue
        bbox_raw = _resolve(doc, vp.get("BBox"))
        vp_warnings: List[str] = []
        if (isinstance(bbox_raw, list) and len(bbox_raw) == 4
                and all(_num(v) is not None for v in bbox_raw)):
            # /BBox is in UNROTATED user space — verified on a real /Rotate 270
            # page, whose box overflows the displayed rect and fits the
            # mediabox exactly.
            bbox = to_display_bbox(page, [float(v) for v in bbox_raw])
        else:
            bbox = page_bbox
            vp_warnings.append("/VP entry has no readable /BBox; taken to "
                               "govern the whole page")
        viewport = Viewport(page=page_index, bbox=bbox,
                            name=_text(vp.get("Name")) or None,
                            warnings=vp_warnings)
        _read_measure(doc, vp.get("Measure"), viewport)
        out.append(viewport)
    return out, warnings


def viewport_at(viewports: Sequence[Viewport], point: Sequence[float],
                page_size: Optional[Sequence[float]] = None
                ) -> Optional[Viewport]:
    """The viewport governing a displayed point.

    The smallest viewport CONTAINING the point wins — sheets with a detail at a
    different scale nest a small viewport inside a large one, and the inner one
    is the answer. A point outside every box falls back to a viewport covering
    the whole page (with ``page_size``, one covering at least
    :data:`WHOLE_PAGE_COVERAGE` of it; without, a lone viewport), because a
    whole-page calibration governs the
    sheet whether or not its box was drawn tightly. Otherwise ``None``: no
    scale is better than the wrong scale.
    """
    inside = [v for v in viewports if v.contains(point)]
    if inside:
        return min(inside, key=lambda v: v.area)
    if not viewports:
        return None
    if page_size is not None:
        page_area = float(page_size[0]) * float(page_size[1])
        whole = [v for v in viewports
                 if page_area > 0 and v.area >= WHOLE_PAGE_COVERAGE * page_area]
        return max(whole, key=lambda v: v.area) if whole else None
    return viewports[0] if len(viewports) == 1 else None


# ---------------------------------------------------------------------------
# Measurement markups
# ---------------------------------------------------------------------------

def _path_length(points: Sequence[Point], fx: float, fy: float) -> float:
    total = 0.0
    for (ax, ay), (bx, by) in zip(points, points[1:]):
        total += math.hypot((bx - ax) * fx, (by - ay) * fy)
    return total


def _polygon_area(points: Sequence[Point], fx: float, fy: float) -> float:
    if len(points) < 3:
        return 0.0
    ring = list(points)
    if ring[0] != ring[-1]:
        ring.append(ring[0])
    twice = 0.0
    for (ax, ay), (bx, by) in zip(ring, ring[1:]):
        twice += (ax * fx) * (by * fy) - (bx * fx) * (ay * fy)
    return abs(twice) / 2.0


def read_markup_measure(doc, xref: int, bbox: BBox, page_index: int,
                        vertices_unrotated: Optional[Sequence[Point]] = None,
                        contents: Optional[str] = None,
                        intent: Optional[str] = None) -> Optional[MarkupMeasure]:
    """The measurement a dimension markup carries, or ``None`` if it carries none.

    ``vertices_unrotated`` are the annotation's own ``/Vertices`` or ``/L``
    points as PyMuPDF reports them — UNROTATED — because ``/X`` and ``/Y`` are
    per-axis and a page rotation swaps the axes. ``bbox`` is the markup's box in
    the displayed frame, carried onto the viewport so a caller can ask which
    region the measurement came from.
    """
    has_measure = False
    try:
        kind, _val = doc.xref_get_key(xref, "Measure")
        has_measure = kind not in (None, "null")
    except Exception:                                  # pragma: no cover
        has_measure = False
    looks_like = bool(intent and intent.endswith("Dimension"))
    if not has_measure and not looks_like:
        return None

    viewport = Viewport(page=page_index, bbox=bbox,
                        source=SOURCE_MEASUREMENT_MARKUP)
    if has_measure:
        _read_measure(doc, _key(doc, xref, "Measure"), viewport)
    else:
        viewport.warnings.append(
            f"/IT /{intent} marks a measurement but the markup carries no "
            f"/Measure dictionary, so it states no scale")

    out = MarkupMeasure(scale=viewport, intent=intent,
                        stated_text=(" ".join(contents.split())[:120]
                                     if contents else None))
    stated = parse_stated_value(contents)
    if stated is not None:
        out.stated = Quantity(value=stated[0], units=stated[1], confidence=1.0,
                              basis=f"{SOURCE_MEASUREMENT_MARKUP}: /Contents")

    points = [(float(p[0]), float(p[1])) for p in (vertices_unrotated or [])]
    fx, fy = viewport.x_per_point, viewport.y_per_point
    if len(points) >= 2 and fx and fy and viewport.x_unit:
        if viewport.y_unit and viewport.y_unit != viewport.x_unit:
            out.warnings.append(
                f"the x and y axes use different units "
                f"({viewport.x_unit} / {viewport.y_unit}); the derived length "
                f"is reported in {viewport.x_unit}")
        basis = f"{SOURCE_MEASUREMENT_MARKUP}: {viewport.label}"
        length_pt = _path_length(points, 1.0, 1.0)
        out.derived = unknown_scale(length_pt).scaled(
            _path_length(points, fx, fy) / length_pt if length_pt else fx,
            viewport.x_unit, factor_confidence=1.0, basis=basis)
        if intent == "PolygonDimension" and len(points) >= 3:
            out.derived_area = unknown_scale(
                _polygon_area(points, 1.0, 1.0)).scaled(
                    fx * fy, viewport.area_unit or f"{viewport.x_unit}^2",
                    factor_confidence=1.0, basis=basis)
    elif len(points) >= 2:
        out.warnings.append(
            "no usable /Measure factors on this markup: its length stays in "
            "page points")
    agree = out.agreement
    if agree is not None and agree > 0.02:
        out.warnings.append(
            f"the value the markup states ({out.stated}) and the length of its "
            f"own vertex path ({out.derived}) differ by {agree:.1%}")
    return out


# ---------------------------------------------------------------------------
# The bridge to planlens.ir.measure
# ---------------------------------------------------------------------------

def scale_factor(viewport: Optional[Viewport], axis: str = "x"
                 ) -> Optional[Tuple[float, str, str]]:
    """A viewport -> ``(real units per point, unit, basis)`` for :meth:`Quantity.scaled`.

    ``None`` when the viewport states no usable scale — an uncalibrated 1:1
    default, a georeferenced measure, a blank unit label. Refusing here is what
    keeps :mod:`planlens.ir.measure`'s promise: points become feet only through
    a scale that was actually resolved.
    """
    if viewport is None or not viewport.is_calibrated:
        return None
    factor = viewport.y_per_point if axis == "y" else viewport.x_per_point
    unit = viewport.y_unit if axis == "y" else viewport.x_unit
    if not factor or factor <= 0 or not unit:
        return None
    basis = f"{'pdf_viewport' if viewport.source == SOURCE_VIEWPORT else viewport.source}: {viewport.label}"
    return (float(factor), unit, basis)


def to_quantity(viewport: Optional[Viewport], value_pt: float,
                axis: str = "x", units: Optional[str] = None) -> Quantity:
    """A page-point length -> a :class:`~planlens.ir.measure.Quantity`.

    With a calibrated viewport the result is in real-world units at confidence
    1.0, its ``basis`` naming ``pdf_viewport`` or ``measurement_markup`` and the
    scale read from the file — the drafter's own statement, not an inference.
    Without one the value comes back in points with ``scale_known=False``,
    which is the honest answer and the one :mod:`planlens.ir.measure` is built
    to carry.
    """
    resolved = scale_factor(viewport, axis=axis)
    if resolved is None:
        return unknown_scale(
            value_pt, basis="no scale stored in the PDF for this page")
    factor, unit, basis = resolved
    q = unknown_scale(value_pt, basis=basis).scaled(
        factor, unit, factor_confidence=1.0, basis=basis)
    if units and normalize_unit(units) != q.units:
        try:
            q = q.to(units)
        except ValueError:
            q = q.with_note(f"cannot convert {q.units} to {units}")
    return q
