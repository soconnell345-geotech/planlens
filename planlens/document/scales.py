"""Scales: fitted maps from a position on the page to a quantity.

WHY THIS MODULE EXISTS. A position read off an image by eye is a guess. On the
2026-10-06 field session an agent read every printed number on ten scanned
log sheets right and placed every layer boundary by eye against the depth
scale: a median 0.20 m off, the worst 1.1 m. The same boundaries measured in
code from the same pixels carry about +/-0.02 m. *Geometry says where, the
model says what*: code finds the drawn thing and converts its position through
a fitted scale; a model only ever reads printed text (the label VALUES) and
names the thing to measure.

A :class:`Scale` is that fitted map, valid over a stated region (its
``extent``). It is linear or log10 along one axis of the displayed page, the
axis turned by the page's measured skew on a scan, so "down the column" means
down the column as drawn. It carries its evidence (:class:`Anchor`), the fit
residual in points and in value units, the rule by which label positions were
tied to the drawing (``anchor_rule``), the uncertainty of anything read
through it, and a confidence from the evidence ladder below.

**One scale shape, many sources.** :func:`scale_from_ruler` adapts
:class:`planlens.document.loggrid.Ruler`; :func:`scale_from_viewport` the
PDF's stored ``/VP`` scale (:mod:`planlens.document.scale`);
:func:`scale_from_stated` a parsed note ("1 in = 20 ft", "1:100");
:func:`scale_from_two_points` a two-point calibration. The finders in
:mod:`planlens.document.scalefinder` build the rest from text, vector paths
and pixels.

**The anchor rule** (how a printed label's box becomes a position), in order:
a drawn tick or gridline beside the label beats the label's box; frame lines
the label run predicts as round values fix a constant offset (a scanned form's
labels sit ~4.6 pt ABOVE their depth, a vector form's on it, and a fit's
residual cannot see a constant offset); otherwise the label centre, with half
a label height added to the uncertainty and a warning.

**Uncertainty.** ``plus_minus`` is a 95 % half-width (about two standard
deviations of the fit and snap scatter, plus the anchor rule's whole
allowance), in points along the axis and converted to value units through the
slope (multiplicatively on a log axis). The design asked for one standard
deviation; a 95 % width is what lets a reader take the truth to lie inside
the stated range, and the measurement harness gates on exactly that.

Nothing here reads a page: this module is data and arithmetic only.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from planlens.ir.measure import Quantity, unknown_scale

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

__all__ = [
    "Anchor", "Scale", "Frame", "Reading", "PageScales", "FitResult",
    "TRANSFORMS", "AXES", "ANCHOR_KINDS",
    "CONF_STORED", "CONF_VECTOR", "CONF_PIXELS_TEXT", "CONF_PIXELS_CALLER",
    "CONF_UNRESOLVED_PENALTY", "CONF_STATED", "CONF_STATED_CONFIRMED",
    "CONF_MODEL_BOXES", "MIN_ANCHORS", "MAX_RESIDUAL_FRAC", "MIN_RUN_FRAC",
    "fit_points", "fit_labels", "estimate_step", "index_by_spacing",
    "log_decades", "LOG_PATTERN", "printed_resolution", "display_value",
    "scale_from_ruler", "scale_from_viewport", "scale_from_stated",
    "scale_from_two_points", "rotate_point", "unrotate_point",
    "confidence_for",
]

#: Per-axis transforms. Room is left for others (probability paper) without
#: building them.
TRANSFORMS = ("linear", "log10")

#: ``x`` / ``y``: a coordinate along that axis of the displayed page, turned by
#: the scale's ``angle_deg``. ``distance``: an isotropic length scale (a plan),
#: with no positional origin.
AXES = ("x", "y", "distance")

ANCHOR_KINDS = ("tick_label", "tick_mark", "gridline", "frame_line",
                "bar_tick", "stated_ratio", "stored", "two_point",
                "log_pattern")

# -- the confidence ladder (design section 4.4) ---------------------------------
#: Stored calibrated viewport: the drafter's own statement.
CONF_STORED = 0.98
#: Vector ticks / rules and text-layer labels, tick-, frame- or grid-anchored.
CONF_VECTOR = 0.95
#: Pixel positions and text (DI, OCR, text layer) label values, anchored.
CONF_PIXELS_TEXT = 0.90
#: Pixel positions and label values supplied by the caller (a vision read of
#: numbered crops), in a regular run, anchored.
CONF_PIXELS_CALLER = 0.85
#: Taken off any of the above when the anchor rule could not be resolved.
CONF_UNRESOLVED_PENALTY = 0.10
#: A stated ratio alone (true plot size assumed), and when a bar agrees.
CONF_STATED = 0.70
CONF_STATED_CONFIRMED = 0.90
#: Labels located by a model's boxes, snapped to ink blobs.
CONF_MODEL_BOXES = 0.60

# -- the gates (loggrid's, generalised) ------------------------------------------
#: A scale needs at least this many positional anchors.
MIN_ANCHORS = 3
#: The largest anchor residual, as a fraction of the step between anchors.
MAX_RESIDUAL_FRAC = 0.20
#: The monotone run must account for this much of the anchors offered.
MIN_RUN_FRAC = 0.60
#: Value steps are "even" when each is an integer multiple of the base step to
#: within this fraction (a missed label leaves a double step, which is fine).
STEP_TOLERANCE = 0.03

#: Spacing of the minor lines of one log decade, as fractions of the decade:
#: log10(k+1) - log10(k), k = 1..9.
LOG_PATTERN = tuple(math.log10(k + 1) - math.log10(k) for k in range(1, 10))


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _r(v: Optional[float], n: int = 3) -> Optional[float]:
    if v is None:
        return None
    out = round(float(v), n)
    return 0.0 if out == 0 else out


def _rb(b: Optional[Sequence[float]], n: int = 1) -> Optional[List[float]]:
    return None if b is None else [_r(v, n) for v in b]


def _compact(d: Dict[str, Any]) -> Dict[str, Any]:
    return {k: v for k, v in d.items()
            if v is not None and v != [] and v != {} and v != ""}


def _plain(v: Any) -> Any:
    """A JSON-ready copy: numpy numbers to floats, private keys dropped."""
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()
                if not str(k).startswith("_")}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    if isinstance(v, (str, bool)) or v is None:
        return v
    if isinstance(v, int):
        return v
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return round(f, 6)


def rotate_point(x: float, y: float, angle_deg: float) -> Point:
    """Displayed point -> the deskewed frame (rotated by -angle about 0,0).

    A rule drawn horizontally on a sheet scanned at ``angle_deg`` (its slope
    dy/dx is ``tan(angle)``) comes out horizontal in this frame: its ``y`` is
    constant along it. The inverse is :func:`unrotate_point`.
    """
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    return (x * c + y * s, -x * s + y * c)


def unrotate_point(u: float, v: float, angle_deg: float) -> Point:
    """The deskewed frame -> displayed (the inverse of :func:`rotate_point`)."""
    t = math.radians(angle_deg)
    c, s = math.cos(t), math.sin(t)
    return (u * c - v * s, u * s + v * c)


def printed_resolution(text: Optional[str]) -> Optional[float]:
    """The resolution a printed number states: ``"1.0"`` -> 0.1, ``"20"`` -> 1."""
    if not text:
        return None
    t = str(text).strip().replace(",", "")
    if "+" in t:                         # a station "12+50" prints to 1 unit
        return 1.0
    digits = "".join(ch for ch in t if ch.isdigit() or ch == ".")
    if not digits or not any(ch.isdigit() for ch in digits):
        return None
    if "." in digits:
        decimals = len(digits.split(".", 1)[1])
        return 10.0 ** (-decimals) if decimals else 1.0
    return 1.0


def display_value(value: Optional[float], plus_minus: Optional[float],
                  resolution: Optional[float] = None) -> Optional[str]:
    """``value +/- plus_minus``, never finer than the printed resolution.

    The owner's rule (2026-10-08, design section 10 item 5): a value is given
    with its uncertainty and is never written to more decimals than the
    source prints. The uncertainty itself is written to one significant
    figure (two when it starts with a 1).
    """
    if value is None:
        return None
    if plus_minus is None or plus_minus <= 0:
        dec_pm = 3
        pm_txt = None
    else:
        exp = math.floor(math.log10(plus_minus))
        lead = plus_minus / 10.0 ** exp
        sig = 2 if lead < 2.0 else 1
        dec_pm = max(0, -(exp - sig + 1))
        # an uncertainty is rounded UP: written smaller it would claim more
        unit = 10.0 ** (-dec_pm)
        pm_up = math.ceil(plus_minus / unit - 1e-9) * unit
        pm_txt = f"{pm_up:.{dec_pm}f}"
    dec = dec_pm
    if resolution and resolution > 0:
        dec_res = max(0, -int(math.floor(math.log10(resolution) + 1e-9)))
        dec = min(dec, dec_res)
    txt = f"{round(value, dec):.{dec}f}"
    return txt if pm_txt is None else f"{txt} +/- {pm_txt}"


# ---------------------------------------------------------------------------
# The data
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Anchor:
    """One piece of a scale's evidence: a position and the value it carries.

    ``position`` is along the scale's axis in the deskewed frame, points.
    ``value`` is ``None`` for a position whose label has not been read yet.
    ``kind`` is one of :data:`ANCHOR_KINDS`; ``source`` says where the value
    came from (``text``, ``ocr``, ``azure_di``, ``caller``, ``pdf``,
    ``pattern``) and ``positioned_by`` where the position came from
    (``vector``, ``pixels``, ``text_box``).
    """
    position: float
    value: Optional[float]
    kind: str = "tick_label"
    source: str = "text"
    positioned_by: str = "text_box"
    box: Optional[BBox] = None
    weight: float = 1.0
    label: Optional[str] = None
    note: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({"position": _r(self.position, 2),
                         "value": _r(self.value, 6), "kind": self.kind,
                         "source": self.source,
                         "positioned_by": self.positioned_by,
                         "box": _rb(self.box), "label": self.label,
                         "note": self.note})


@dataclass
class FitResult:
    """A weighted least-squares fit ``t = a + b*s`` (``t`` = value or log10).

    Residuals are kept in POINTS along the axis, which is what the gate and
    the uncertainty are about; ``kept`` / ``dropped`` index the anchors given.
    """
    a: float
    b: float
    n: int
    s_mean: float
    sss: float
    max_res_pt: float
    rms_pt: float
    step_pt: float
    step_value: float
    kept: List[int] = field(default_factory=list)
    dropped: List[int] = field(default_factory=list)
    reason: str = ""


@dataclass
class Reading:
    """One measured value: what was read, how far to trust it, and from what."""
    value: Optional[float]
    plus_minus: Optional[float]
    unit: Optional[str]
    quantity: str
    confidence: float
    scale_id: Optional[str]
    position_pt: Optional[Point] = None
    along_pt: Optional[float] = None
    plus_minus_pt: Optional[float] = None
    scale_known: bool = True
    resolution: Optional[float] = None
    warnings: List[str] = field(default_factory=list)

    @property
    def display(self) -> Optional[str]:
        return display_value(self.value, self.plus_minus, self.resolution)

    def contains(self, truth: float) -> bool:
        """Is ``truth`` inside ``value +/- plus_minus``?"""
        if self.value is None or self.plus_minus is None:
            return False
        return abs(float(truth) - self.value) <= self.plus_minus + 1e-12

    def to_dict(self) -> Dict[str, Any]:
        d = _compact({
            self.quantity: _r(self.value, 6),
            "plus_minus": _r(self.plus_minus, 6),
            "unit": self.unit,
            "display": self.display,
            "confidence": round(float(self.confidence), 2),
            "scale": self.scale_id,
            "at_pt": _rb(self.position_pt, 2) if self.position_pt else None,
            "plus_minus_pt": _r(self.plus_minus_pt, 2),
            "warnings": list(self.warnings),
        })
        if not self.scale_known:
            d["scale_known"] = False
        return d

    def to_quantity(self) -> Quantity:
        """The reading as a :class:`~planlens.ir.measure.Quantity`.

        With no scale behind it the value stays in page points with
        ``scale_known=False`` — the rule :mod:`planlens.ir.measure` exists
        to keep.
        """
        if not self.scale_known or self.value is None:
            q = unknown_scale(self.along_pt or 0.0,
                              basis="no scale on this page")
            return q
        return Quantity(value=self.value, units=self.unit or "unitless",
                        confidence=self.confidence,
                        basis=f"scale:{self.scale_id}",
                        plus_minus=self.plus_minus)


@dataclass
class Scale:
    """A fitted map from a position on the page to a quantity.

    ``value_at(s)`` is ``a + b*s`` (linear) or ``10 ** (a + b*s)`` (log10),
    where ``s`` is the position along ``axis`` in the deskewed frame
    (:meth:`along`). An ``axis="distance"`` scale has no origin: ``b`` is
    value per point of length and ``a`` is 0.
    """
    id: str
    page: int
    extent: BBox
    quantity: str
    unit: Optional[str]
    axis: str
    transform: str = "linear"
    a: float = 0.0
    b: float = 0.0
    angle_deg: float = 0.0
    anchors: List[Anchor] = field(default_factory=list)
    residual_pt: float = 0.0
    rms_pt: float = 0.0
    residual_value: float = 0.0
    anchor_rule: str = ""
    anchor_rule_kind: str = "none"
    anchor_pt: float = 0.0
    pixel_pt: float = 0.0
    confidence: float = 0.5
    provenance: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)
    needs_values: bool = False
    #: For ``needs_values``: the boxes of the labels to read, in run order,
    #: and the positions they stand at (deskewed, points).
    label_boxes: List[BBox] = field(default_factory=list)
    label_positions: List[float] = field(default_factory=list)
    resolution: Optional[float] = None
    rel_uncertainty: float = 0.0
    #: Fit statistics for the prediction error at a position.
    n_fit: int = 0
    s_mean: float = 0.0
    sss: float = 0.0
    rms_dof_pt: float = 0.0

    # -- geometry -----------------------------------------------------------
    def along(self, x: float, y: float) -> float:
        """The position of a displayed point along this scale's axis."""
        u, v = rotate_point(x, y, self.angle_deg)
        return v if self.axis == "y" else u

    def across(self, x: float, y: float) -> float:
        """The position of a displayed point across this scale's axis."""
        u, v = rotate_point(x, y, self.angle_deg)
        return u if self.axis == "y" else v

    def point(self, along: float, across: float) -> Point:
        """The displayed point at ``along`` / ``across`` this axis."""
        if self.axis == "y":
            return unrotate_point(across, along, self.angle_deg)
        return unrotate_point(along, across, self.angle_deg)

    def contains(self, x: float, y: float, slack: float = 2.0) -> bool:
        x0, y0, x1, y1 = self.extent
        return (x0 - slack <= x <= x1 + slack
                and y0 - slack <= y <= y1 + slack)

    # -- values -------------------------------------------------------------
    @property
    def usable(self) -> bool:
        return not self.needs_values and self.b != 0.0

    def value_at(self, s: float) -> Optional[float]:
        if not self.usable:
            return None
        t = self.a + self.b * float(s)
        if self.transform == "log10":
            return 10.0 ** t
        return t

    def position_of(self, value: float) -> Optional[float]:
        """Where along the axis ``value`` falls (``None`` off a log axis' domain)."""
        if not self.usable:
            return None
        if self.transform == "log10":
            if value <= 0:
                return None
            t = math.log10(value)
        else:
            t = float(value)
        return (t - self.a) / self.b

    def plus_minus_pt_at(self, s: Optional[float] = None) -> float:
        """The scale's own 95 % uncertainty at ``s``, in points.

        The fitted line's prediction error (doubled), the anchor rule's
        allowance and the render's pixel term, in quadrature. It grows away
        from the anchors — extrapolating a scale is less sure than
        interpolating it.
        """
        fit = 0.0
        if self.n_fit >= 2 and self.rms_dof_pt > 0:
            k = 1.0 / self.n_fit
            if s is not None and self.sss > 0:
                k += (float(s) - self.s_mean) ** 2 / self.sss
            fit = 2.0 * self.rms_dof_pt * math.sqrt(k)
        return math.sqrt(fit * fit + self.anchor_pt ** 2 + self.pixel_pt ** 2)

    @property
    def plus_minus_pt(self) -> float:
        return self.plus_minus_pt_at(None)

    def value_plus_minus(self, s: float, pm_pt: float) -> Optional[float]:
        """A position uncertainty (points) in value units at ``s``."""
        if not self.usable:
            return None
        if self.transform == "log10":
            v = self.value_at(s)
            return abs(v * math.log(10.0) * self.b * pm_pt)
        out = abs(self.b) * pm_pt
        if self.axis == "distance" and self.rel_uncertainty:
            out = math.hypot(out, abs(self.b * s) * self.rel_uncertainty)
        return out

    @property
    def per_point(self) -> float:
        return self.b

    def reading(self, s: float, *, snap_pt: float = 0.0,
                snap_confidence: float = 1.0,
                point: Optional[Point] = None,
                warnings: Iterable[str] = ()) -> Reading:
        """Read position ``s`` through this scale.

        ``snap_pt`` is the snapped thing's own 95 % uncertainty in points (or
        the box's location error when nothing was snapped); it is added in
        quadrature to the scale's own.
        """
        warn = list(warnings)
        if not self.usable:
            return Reading(value=None, plus_minus=None, unit=self.unit,
                           quantity=self.quantity, confidence=0.0,
                           scale_id=self.id, position_pt=point, along_pt=s,
                           scale_known=False,
                           warnings=warn + ["this scale is waiting for its "
                                            "label values"])
        pm_pt = math.hypot(self.plus_minus_pt_at(s), snap_pt)
        value = self.value_at(s)
        return Reading(value=value, plus_minus=self.value_plus_minus(s, pm_pt),
                       unit=self.unit, quantity=self.quantity,
                       confidence=min(self.confidence, snap_confidence),
                       scale_id=self.id, position_pt=point, along_pt=s,
                       plus_minus_pt=pm_pt, resolution=self.resolution,
                       warnings=warn)

    def read_point(self, x: float, y: float, **kw) -> Reading:
        return self.reading(self.along(x, y), point=(x, y), **kw)

    # -- output -------------------------------------------------------------
    def to_dict(self, anchors: bool = False) -> Dict[str, Any]:
        d = _compact({
            "id": self.id, "page": self.page, "quantity": self.quantity,
            "unit": self.unit, "axis": self.axis,
            "transform": self.transform if self.transform != "linear" else None,
            "extent": _rb(self.extent),
            "per_point": (_r(self.b, 6) if self.usable
                          and self.transform == "linear" else None),
            "decade_pt": (_r(1.0 / self.b, 2) if self.usable
                          and self.transform == "log10" else None),
            "skew_deg": (_r(self.angle_deg, 2)
                         if abs(self.angle_deg) >= 0.005 else None),
            "anchors": len(self.anchors) or None,
            "residual_pt": (_r(self.residual_pt, 2)
                            if self.anchors and self.usable else None),
            "plus_minus_pt": _r(self.plus_minus_pt, 2) if self.usable else None,
            "anchor_rule": self.anchor_rule,
            "confidence": round(float(self.confidence), 2),
            "needs_values": True if self.needs_values else None,
            "labels_to_read": ([_rb(b) for b in self.label_boxes]
                               if self.needs_values else None),
            "found_by": _plain(self.provenance) or None,
            "warnings": list(self.warnings),
        })
        if anchors and self.anchors:
            d["anchor_list"] = [a.to_dict() for a in self.anchors]
        return d


@dataclass
class Frame:
    """Scales over one region: a log's one axis, a plot's two, a plan's one."""
    id: str
    page: int
    kind: str                         # log | plot | plan | profile
    extent: BBox
    scales: Dict[str, Scale] = field(default_factory=dict)
    provenance: Dict[str, Any] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id, "kind": self.kind, "extent": _rb(self.extent),
            "scales": [s.to_dict() for s in self.scales.values()],
            "found_by": _plain(self.provenance) or None,
            "warnings": list(self.warnings),
        })


@dataclass
class PageScales:
    """Every scale found on one page (:func:`find_scales`)."""
    page: int
    frames: List[Frame] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    skew_deg: Optional[float] = None
    raster: bool = False
    extra: Dict[str, Any] = field(default_factory=dict)

    def scales(self) -> List[Scale]:
        return [s for f in self.frames for s in f.scales.values()]

    def get(self, scale_id: str) -> Optional[Scale]:
        for s in self.scales():
            if s.id == scale_id:
                return s
        return None

    def frame_of(self, scale_id: str) -> Optional[Frame]:
        for f in self.frames:
            for s in f.scales.values():
                if s.id == scale_id:
                    return f
        return None

    def needing_values(self) -> List[Scale]:
        return [s for s in self.scales() if s.needs_values]

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "page": self.page,
            "frames": [f.to_dict() for f in self.frames],
            "skew_deg": (_r(self.skew_deg, 2)
                         if self.skew_deg is not None else None),
            "raster": True if self.raster else None,
            "warnings": list(self.warnings),
        })


# ---------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------

def _wls(ss: Sequence[float], ts: Sequence[float],
         ws: Optional[Sequence[float]] = None) -> Tuple[float, float]:
    n = len(ss)
    ws = list(ws) if ws is not None else [1.0] * n
    sw = sum(ws)
    sm = sum(w * s for w, s in zip(ws, ss)) / sw
    tm = sum(w * t for w, t in zip(ws, ts)) / sw
    sxx = sum(w * (s - sm) ** 2 for w, s in zip(ws, ss))
    if sxx <= 1e-12:
        return tm, 0.0
    b = sum(w * (s - sm) * (t - tm) for w, s, t in zip(ws, ss, ts)) / sxx
    return tm - b * sm, b


def _monotone_run(points: Sequence[Tuple[float, float]], rising: bool
                  ) -> List[int]:
    """Indexes (in ``points`` order, sorted by position) of the longest run
    whose values move one way along the axis."""
    n = len(points)
    if not n:
        return []
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            ok = (points[j][1] < points[i][1] if rising
                  else points[j][1] > points[i][1])
            if ok and best[j] + 1 > best[i]:
                best[i] = best[j] + 1
                prev[i] = j
    end = max(range(n), key=lambda i: best[i])
    out = []
    while end >= 0:
        out.append(end)
        end = prev[end]
    return list(reversed(out))


def _steps_even(values: Sequence[float]) -> Tuple[bool, float]:
    """Do the value steps make a printed ruler's even run?

    Every step equals one base step, except that a label the reader missed
    leaves a DOUBLE step — allowed for up to a third of the steps, and never
    in a run of three (three numbers with one double step are as likely a
    coincidence as a scale). "0, 2.0, 2.2" is not a ruler: its steps are
    ten times apart.
    """
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    if not steps or any(s <= 0 for s in steps):
        return False, 0.0
    base = min(steps)
    doubles = 0
    for s in steps:
        k = s / base
        if abs(k - 1.0) <= STEP_TOLERANCE:
            continue
        if abs(k - 2.0) <= 2 * STEP_TOLERANCE:
            doubles += 1
            continue
        return False, base
    if doubles and (len(steps) < 3 or doubles > len(steps) / 3.0):
        return False, base
    return True, base


def fit_points(points: Sequence[Tuple[float, float]], transform: str = "linear",
               *, weights: Optional[Sequence[float]] = None,
               require_even: bool = True, allow_drop: int = 1,
               min_anchors: int = MIN_ANCHORS,
               max_excluded: Optional[int] = None) -> Optional[FitResult]:
    """Fit ``(position, value)`` pairs, or ``None`` with the gates unmet.

    The longest monotone run is kept (it must hold :data:`MIN_RUN_FRAC` of
    the points and at least ``min_anchors``); with ``require_even`` its value
    steps must be integer multiples of one base step; the fit's largest
    residual must be under :data:`MAX_RESIDUAL_FRAC` of the step. At most
    ``allow_drop`` points that break the run may be dropped, and are named.

    ``max_excluded`` caps how many points may be left out IN ALL (outside the
    monotone run plus dropped). Text found in a band may hold stray numbers
    that are not labels, so it is uncapped there; label values read for the
    boxes of one run are all labels of that run, and two that disagree with
    it are a refusal, not something to fit around.
    """
    pts = [(float(s), float(v)) for s, v in points]
    if transform == "log10":
        if any(v <= 0 for _, v in pts):
            return None
        pts = [(s, math.log10(v)) for s, v in pts]
    if len(pts) < min_anchors:
        return None
    order = sorted(range(len(pts)), key=lambda i: pts[i][0])
    spts = [pts[i] for i in order]
    w_all = list(weights) if weights is not None else [1.0] * len(pts)
    best: Optional[FitResult] = None
    for rising in (True, False):
        run = _monotone_run(spts, rising)
        if len(run) < max(min_anchors, MIN_RUN_FRAC * len(spts)):
            continue
        fit = _fit_run([spts[i] for i in run],
                       [w_all[order[i]] for i in run],
                       require_even, allow_drop, min_anchors)
        if fit is None:
            continue
        fit.kept = [order[run[i]] for i in fit.kept]
        dropped_in_run = [order[run[i]] for i in fit.dropped]
        fit.dropped = sorted(set(range(len(pts))) - set(fit.kept))
        if dropped_in_run:
            fit.reason = (fit.reason or "") + " (a point broke the run)"
        if max_excluded is not None and len(fit.dropped) > max_excluded:
            continue
        if best is None or len(fit.kept) > len(best.kept):
            best = fit
    return best


def _fit_run(run: Sequence[Tuple[float, float]], ws: Sequence[float],
             require_even: bool, allow_drop: int, min_anchors: int
             ) -> Optional[FitResult]:
    idx = list(range(len(run)))

    def attempt(keep: List[int]) -> Optional[FitResult]:
        if len(keep) < min_anchors:
            return None
        ss = [run[i][0] for i in keep]
        ts = [run[i][1] for i in keep]
        even, base = _steps_even(ts)
        if require_even and not even:
            return None
        a, b = _wls(ss, ts, [ws[i] for i in keep])
        if b == 0.0:
            return None
        res_pt = [(t - (a + b * s)) / b for s, t in zip(ss, ts)]
        max_res = max(abs(r) for r in res_pt)
        step_pt = abs(base / b) if base else 0.0
        if step_pt <= 0 or max_res > MAX_RESIDUAL_FRAC * step_pt:
            return None
        n = len(keep)
        sm = sum(ss) / n
        sss = sum((s - sm) ** 2 for s in ss)
        rms = math.sqrt(sum(r * r for r in res_pt) / n)
        return FitResult(a=a, b=b, n=n, s_mean=sm, sss=sss,
                         max_res_pt=max_res, rms_pt=rms, step_pt=step_pt,
                         step_value=base, kept=list(keep))

    fit = attempt(idx)
    if fit is not None or allow_drop <= 0:
        return fit
    # Drop the one point whose removal leaves the best fit (a misread label,
    # a stray digit, a mis-snapped blob) -- and say which. Only where four
    # anchors remain: three numbers that fit once one is thrown away are not
    # evidence of a scale.
    if len(idx) - 1 < max(min_anchors, 4):
        return None
    best = None
    for drop in idx:
        keep = [i for i in idx if i != drop]
        f = attempt(keep)
        if f is not None and (best is None or f.max_res_pt < best.max_res_pt):
            best = f
            best.dropped = [drop]
            best.reason = "one anchor dropped"
    return best


def fit_labels(positions: Sequence[float], values: Sequence[Optional[float]],
               transform: str = "linear", **kw) -> Optional[FitResult]:
    """:func:`fit_points` over parallel lists, skipping unread (``None``) values.

    The result's ``kept`` / ``dropped`` index the ORIGINAL lists.
    """
    pairs = [(i, p, v) for i, (p, v) in enumerate(zip(positions, values))
             if v is not None]
    fit = fit_points([(p, v) for _, p, v in pairs], transform, **kw)
    if fit is None:
        return None
    fit.kept = [pairs[i][0] for i in fit.kept]
    fit.dropped = sorted(set(i for i, _, _ in pairs) - set(fit.kept))
    return fit


def rms_dof(fit: FitResult, floor: float = 0.05) -> float:
    """The fit's rms residual with the degrees of freedom restored, floored.

    Three points can fit a line perfectly by chance; the floor (a fraction of
    a pixel, set by the caller) keeps a lucky fit from claiming exactness.
    """
    if fit.n > 2:
        r = fit.rms_pt * math.sqrt(fit.n / (fit.n - 2))
    else:
        r = fit.rms_pt
    return max(r, floor)


# ---------------------------------------------------------------------------
# Spacing: values to lines by fitted spacing, never by count
# ---------------------------------------------------------------------------

def estimate_step(positions: Sequence[float], tol: float = 0.08
                  ) -> Optional[float]:
    """The base step of positions that sit on a regular grid with gaps.

    Gridlines go missing (a title breaks one, a scan loses another) and a
    stray line can sit off the grid, so neither the median gap nor the
    smallest one is the step. Every gap (and its halves, thirds, quarters)
    is tried; a step scores the positions that land on its grid less half
    the slots it leaves empty, so a fine step that "explains" a stray line
    by inventing empty slots loses to the true one.
    """
    ps = sorted(float(p) for p in positions)
    gaps = [b - a for a, b in zip(ps, ps[1:]) if b - a > 1e-6]
    if not gaps:
        return None
    cands = set()
    for g in gaps:
        for k in (1, 2, 3, 4):
            cands.add(g / k)
    best = None
    floor = 0.5 * min(gaps) - 1e-9
    for c in cands:
        if c < floor:
            continue
        for origin in ps[:3]:
            ks = []
            for p in ps:
                q = (p - origin) / c
                if abs(q - round(q)) <= tol:
                    ks.append(round(q))
            if len(ks) < 2:
                continue
            slots = max(ks) - min(ks) + 1
            score = len(ks) - 0.5 * (slots - len(ks))
            key = (score, c)
            if best is None or key > best[0]:
                best = (key, c, origin)
    if best is None:
        return None
    _key, step, origin = best
    on = [(round((p - origin) / step), p) for p in ps
          if abs((p - origin) / step - round((p - origin) / step)) <= tol]
    if len(on) < 2:
        return step
    a, b = _wls([k for k, _ in on], [p for _, p in on])
    return b if b > 0 else step


def index_by_spacing(positions: Sequence[float], step: Optional[float] = None,
                     tol: float = 0.12) -> Optional[Dict[str, Any]]:
    """Whole-step indexes for positions on a regular grid.

    Returns ``{"index": [...], "step", "origin", "residual", "off_grid"}``:
    ``index[i]`` is the grid index of ``positions[i]`` (``None`` for one that
    sits off the grid by more than ``tol`` of a step), counted from the first
    on-grid position. A missing line leaves a gap in the indexes — which is
    exactly what keeps the values right.
    """
    ps = [float(p) for p in positions]
    if len(ps) < 2:
        return None
    step = step or estimate_step(ps)
    if not step:
        return None
    order = sorted(range(len(ps)), key=lambda i: ps[i])
    first = ps[order[0]]
    raw = [(p - first) / step for p in ps]
    idx: List[Optional[int]] = []
    for r in raw:
        k = round(r)
        idx.append(int(k) if abs(r - k) <= tol else None)
    on = [(i, k) for i, k in enumerate(idx) if k is not None]
    if len(on) >= 2:
        # The first pass counts steps from the first line with a step that
        # may be a little off, so a long grid drifts off it; index again
        # from the line fitted through the lines it did place.
        for _ in range(3):
            a, b = _wls([k for _, k in on], [ps[i] for i, _ in on])
            if b <= 0:
                break
            k0 = round((first - a) / b)
            again: List[Optional[int]] = []
            for p in ps:
                r = (p - a) / b
                k = round(r)
                again.append(int(k - k0) if abs(r - k) <= tol else None)
            on2 = [(i, k) for i, k in enumerate(again) if k is not None]
            if len(on2) <= len(on):
                break
            idx, on, step = again, on2, b
    if len(on) >= 2:
        a, b = _wls([k for _, k in on], [ps[i] for i, _ in on])
        if b > 0:
            step = b
            first = a
            resid = max(abs(ps[i] - (a + b * k)) for i, k in on)
        else:
            resid = 0.0
    else:
        resid = 0.0
    return {"index": idx, "step": step, "origin": first, "residual": resid,
            "off_grid": [i for i, k in enumerate(idx) if k is None]}


def log_decades(positions: Sequence[float], tol: float = 0.035
                ) -> Optional[Dict[str, Any]]:
    """Find a log axis's decades from the pattern of its gridlines alone.

    The nine gaps between a decade's lines stand in the proportion
    log10(k+1) - log10(k). Every window of nine consecutive gaps is compared
    with that pattern; the best (error <= ``tol`` of a decade) fixes the
    decade's length and one major line, and every line is then given its
    place ``decade + log10(mantissa)`` by fitted spacing. Returns
    ``{"decade", "major", "places", "error", "residual", "majors"}`` where
    ``places[i]`` is line ``i``'s position in decades from ``major`` (``None``
    off the pattern), or ``None`` when no decade pattern is there.
    """
    ps = sorted(float(p) for p in positions)
    if len(ps) < 10:
        return None
    gaps = [b - a for a, b in zip(ps, ps[1:])]
    best = None
    for st in range(len(gaps) - 8):
        seg = gaps[st:st + 9]
        dec = sum(seg)
        if dec <= 0:
            continue
        err = max(abs(g / dec - p) for g, p in zip(seg, LOG_PATTERN))
        if best is None or err < best[0]:
            best = (err, st, dec)
    # The same pattern read backwards is a log axis whose values fall along
    # the page (a log axis drawn right to left, or downward).
    rev = False
    for st in range(len(gaps) - 8):
        seg = gaps[st:st + 9][::-1]
        dec = sum(seg)
        if dec <= 0:
            continue
        err = max(abs(g / dec - p) for g, p in zip(seg, LOG_PATTERN))
        if best is None or err < best[0]:
            best = (err, st + 9, dec)
            rev = True
    if best is None or best[0] > tol:
        # No decade survives whole (a scan lost a line or two in each).
        # Try every pair of lines as one decade's ends and keep the pair whose
        # pattern explains the most lines - held to a tighter fit, because a
        # loose one can always find a decade that an even grid half fits.
        fb = _log_decades_by_pairs(ps, min(tol, 0.015))
        if fb is None:
            return None
        best = fb
        rev = fb[3]
    err, major_i, dec = best[0], best[1], best[2]
    major = ps[major_i]
    sign = -1.0 if rev else 1.0
    mant = [math.log10(k) for k in range(1, 10)]
    places: List[Optional[float]] = []
    pairs = []
    for p in ps:
        x = sign * (p - major) / dec
        d = math.floor(x)
        frac = x - d
        cands = [(abs(frac - m), m) for m in mant] + [(abs(frac - 1.0), 1.0)]
        dist, m = min(cands)
        if dist * dec <= max(0.6, tol * dec):
            place = d + m
            places.append(place)
            pairs.append((p, place))
        else:
            places.append(None)
    if len(pairs) < 10:
        return None
    a, b = _wls([q for _, q in pairs], [p for p, _ in pairs])
    # The window's decade is a first guess; over several decades a small
    # error in it walks a line onto its neighbour's place (the 90 line taken
    # for the 100). Place every line again from the fitted axis, one line to
    # a place, until the places stop changing.
    for _ in range(3):
        best_at: Dict[float, Tuple[float, int]] = {}
        trial: List[Optional[float]] = [None] * len(ps)
        for i, p in enumerate(ps):
            x = (p - a) / b
            d = math.floor(x)
            frac = x - d
            cands = [(abs(frac - m), m) for m in mant] + [(abs(frac - 1.0),
                                                           1.0)]
            dist, m = min(cands)
            if dist * abs(b) > max(0.6, tol * abs(b)):
                continue
            place = round(d + m, 9)
            if place in best_at and best_at[place][0] <= dist:
                continue
            if place in best_at:
                trial[best_at[place][1]] = None
            best_at[place] = (dist, i)
            trial[i] = place
        new_pairs = [(p, q) for p, q in zip(ps, trial) if q is not None]
        if len(new_pairs) < 10:
            break
        changed = trial != places
        places = trial
        pairs = new_pairs
        a, b = _wls([q for _, q in pairs], [p for p, _ in pairs])
        if not changed:
            break
    resid = max(abs(p - (a + b * q)) for p, q in pairs)
    majors = [p for p, q in pairs if abs(q - round(q)) < 1e-9]
    return {"decade": abs(b), "major": a, "places": places, "error": err,
            "residual": resid, "majors": majors, "falling": rev,
            "positions": ps}


def _log_decades_by_pairs(ps: Sequence[float], tol: float):
    """Fallback for :func:`log_decades` when lines are missing.

    Every pair ``(i, j)`` is tried as a decade's two majors (mantissa 1 at
    ``i``, 10 at ``j``, values rising or falling); every line is then placed
    by the pattern and counted when it lands within ``tol`` of a decade (or
    0.6 pt). The pair explaining the most lines wins; it must explain at
    least ten and 80 % of them. Returns ``(error, major_index, decade,
    falling)`` or ``None``.
    """
    n = len(ps)
    mant = [math.log10(k) for k in range(1, 10)] + [1.0]
    # The closest two lines of a decade are log10(10/9) = 0.046 of it apart:
    # a decade shorter than the grid's own closest gap allows is not one.
    min_gap = min((b - a for a, b in zip(ps, ps[1:]) if b - a > 1e-6),
                  default=0.0)
    min_dec = max(20.0, 0.8 * min_gap / 0.0458)
    best = None
    for i in range(n):
        for j in range(i + 1, n):
            dec = ps[j] - ps[i]
            if dec < min_dec:
                continue
            for falling in (False, True):
                major = ps[j] if falling else ps[i]
                sign = -1.0 if falling else 1.0
                hits = 0
                worst = 0.0
                for p in ps:
                    x = sign * (p - major) / dec
                    d = math.floor(x)
                    frac = x - d
                    dist = min(abs(frac - m) for m in mant)
                    if dist * dec <= max(0.6, tol * dec):
                        hits += 1
                        worst = max(worst, dist)
                if best is None or hits > best[0] or (
                        hits == best[0] and worst < best[1]):
                    best = (hits, worst, (j if falling else i), dec, falling)
    if best is None or best[0] < 10 or best[0] < 0.85 * n:
        return None
    hits, worst, mi, dec, falling = best
    return (worst, mi, dec, falling)


# ---------------------------------------------------------------------------
# Confidence
# ---------------------------------------------------------------------------

def confidence_for(positioned_by: str, values_from: str, anchored: bool,
                   residual_pt: float = 0.0, step_pt: float = 0.0) -> float:
    """The evidence ladder of design section 4.4, then the fit's quality.

    ``positioned_by``: ``vector`` | ``pixels`` | ``model_boxes``;
    ``values_from``: ``text`` | ``ocr`` | ``azure_di`` | ``caller`` |
    ``pdf``. A fit whose residual approaches the gate loses confidence in
    proportion, as loggrid's ruler always has.
    """
    if positioned_by == "model_boxes":
        base = CONF_MODEL_BOXES
    elif positioned_by == "vector":
        base = CONF_VECTOR
    elif values_from == "caller":
        base = CONF_PIXELS_CALLER
    else:
        base = CONF_PIXELS_TEXT
    if not anchored:
        base -= CONF_UNRESOLVED_PENALTY
    if step_pt > 0:
        frac = residual_pt / (MAX_RESIDUAL_FRAC * step_pt)
        base = min(base, max(0.3, 1.0 - 0.5 * frac))
    return round(max(0.0, min(1.0, base)), 3)


# ---------------------------------------------------------------------------
# Adapters: the four scale shapes the code already had
# ---------------------------------------------------------------------------

def scale_from_ruler(ruler, extent: BBox, scale_id: Optional[str] = None
                     ) -> Scale:
    """A :class:`~planlens.document.loggrid.Ruler` as a :class:`Scale`.

    The ruler is ``value = intercept + slope * y`` on the displayed page (no
    skew); its ticks are its anchors. Its anchor rule is whatever loggrid
    recorded in ``evidence["anchor_rule"]`` (label centres otherwise).
    """
    ev = dict(getattr(ruler, "evidence", {}) or {})
    rule_kind = ev.get("anchor_rule_kind") or "centred_assumed"
    anchors = [Anchor(position=float(y), value=float(v), kind="tick_label",
                      source=ev.get("values_from", "text"),
                      positioned_by=ev.get("positions_from", "text_box"))
               for y, v in ruler.ticks]
    ys = [a.position for a in anchors]
    n = len(ys)
    sm = sum(ys) / n if n else 0.0
    sss = sum((y - sm) ** 2 for y in ys)
    res_pt = (abs(ruler.residual / ruler.slope) if ruler.slope else 0.0)
    step_pt = abs(ruler.step / ruler.slope) if ruler.slope else 0.0
    half_label = float(ev.get("label_height_pt", 0.0) or 0.0) / 2.0
    anchor_pt = float(ev.get("anchor_pt", half_label if rule_kind ==
                             "centred_assumed" else 0.0) or 0.0)
    resolved = rule_kind != "centred_assumed"
    conf = confidence_for(ev.get("positions_from", "vector"),
                          ev.get("values_from", "text"), resolved,
                          res_pt, step_pt)
    return Scale(
        id=scale_id or f"p{ruler.page}.{ruler.kind}", page=ruler.page,
        extent=tuple(extent), quantity=ruler.kind, unit=ruler.unit,
        axis="y", transform="linear", a=float(ruler.intercept),
        b=float(ruler.slope), angle_deg=float(ev.get("skew_deg", 0.0) or 0.0),
        anchors=anchors, residual_pt=res_pt, rms_pt=res_pt,
        residual_value=float(ruler.residual),
        anchor_rule=ev.get("anchor_rule", "label centres taken as the depth "
                                          "they mark (not checked)"),
        anchor_rule_kind=rule_kind, anchor_pt=anchor_pt,
        confidence=min(conf, float(ruler.confidence) + 0.05),
        provenance=_compact({"from": "log_grid ruler",
                             "column": ruler.column_id,
                             "values_from": ev.get("values_from", "text"),
                             "positions_from": ev.get("positions_from")}),
        n_fit=n, s_mean=sm, sss=sss, rms_dof_pt=max(res_pt / 2.0, 0.05),
        resolution=ev.get("resolution"))


def scale_from_viewport(viewport, scale_id: Optional[str] = None
                        ) -> Optional[Scale]:
    """The PDF's stored ``/VP`` scale as a distance :class:`Scale`.

    ``None`` for the uncalibrated 1:1 default, a georeferenced measure or a
    blank unit — :func:`planlens.document.scale.scale_factor` refuses those,
    and so does this.
    """
    from planlens.document.scale import scale_factor
    resolved = scale_factor(viewport, axis="x")
    if resolved is None:
        return None
    factor, unit, basis = resolved
    return Scale(
        id=scale_id or f"p{viewport.page}.stored", page=viewport.page,
        extent=tuple(viewport.bbox), quantity="distance", unit=unit,
        axis="distance", a=0.0, b=float(factor),
        anchors=[Anchor(position=0.0, value=None, kind="stored",
                        source="pdf", positioned_by="vector",
                        box=tuple(viewport.bbox), label=viewport.label)],
        anchor_rule="stored in the PDF (/VP /Measure)", anchor_rule_kind="exact",
        confidence=CONF_STORED,
        provenance={"from": basis, "ratio": viewport.label})


#: One PDF point is 1/72 inch of paper.
_M_PER_PT = 0.0254 / 72.0


def scale_from_stated(text: str, page: int, extent: BBox,
                      scale_id: Optional[str] = None,
                      box: Optional[BBox] = None) -> Optional[Scale]:
    """A printed scale note ("1 in = 20 ft", "1:100") as a distance Scale.

    It assumes the sheet is printed at its true size, which a re-plot breaks
    and nothing on the sheet can confirm, so it carries confidence
    :data:`CONF_STATED` until a bar or a stored scale agrees with it. A bare
    ratio states no unit: lengths are then in metres (a ratio holds in any
    unit; the paper's metres times N are the ground's), and the note says so.
    """
    from planlens.document.scale import parse_ratio, ratio_magnification
    from planlens.ir.measure import LENGTH_IN_METRES
    parsed = parse_ratio(text)
    mag = ratio_magnification(parsed)
    if not parsed or not mag or mag <= 0:
        return None
    page_len, page_unit, real_len, real_unit = parsed
    warnings = ["a stated scale assumes the sheet is printed at its true "
                "size; a re-plotted sheet keeps the note and changes the "
                "scale"]
    if real_unit and real_unit in LENGTH_IN_METRES:
        unit = real_unit
        per_pt = mag * _M_PER_PT / LENGTH_IN_METRES[real_unit]
    else:
        unit = "m"
        per_pt = mag * _M_PER_PT
        warnings.append("the ratio states no unit; lengths are given in "
                        "metres, which a ratio holds in any unit")
    return Scale(
        id=scale_id or f"p{page}.stated", page=page, extent=tuple(extent),
        quantity="distance", unit=unit, axis="distance", a=0.0, b=per_pt,
        anchors=[Anchor(position=0.0, value=mag, kind="stated_ratio",
                        source="text", positioned_by="text_box", box=box,
                        label=" ".join(str(text).split()))],
        anchor_rule="a printed ratio; true plot size assumed",
        anchor_rule_kind="stated", confidence=CONF_STATED,
        provenance={"from": "scale note", "note": " ".join(str(text).split()),
                    "magnification": round(mag, 6)},
        warnings=warnings)


def scale_from_two_points(page: int, p1: Point, p2: Point, distance: float,
                          unit: str, extent: Optional[BBox] = None,
                          scale_id: Optional[str] = None,
                          plus_minus_pt: float = 0.0) -> Scale:
    """A distance scale from two points and the known distance between them.

    ``plus_minus_pt`` is how well each point is placed; it becomes the
    scale's relative uncertainty (the two errors over the length).
    """
    length = math.dist(p1, p2)
    if length <= 0:
        raise ValueError("the two points coincide")
    rel = math.sqrt(2.0) * plus_minus_pt / length if plus_minus_pt else 0.0
    if extent is None:
        extent = (min(p1[0], p2[0]), min(p1[1], p2[1]),
                  max(p1[0], p2[0]), max(p1[1], p2[1]))
    return Scale(
        id=scale_id or f"p{page}.two_point", page=page, extent=tuple(extent),
        quantity="distance", unit=unit, axis="distance", a=0.0,
        b=float(distance) / length,
        anchors=[Anchor(position=0.0, value=0.0, kind="two_point",
                        source="caller", positioned_by="caller"),
                 Anchor(position=length, value=float(distance),
                        kind="two_point", source="caller",
                        positioned_by="caller")],
        anchor_rule="two named points and the distance between them",
        anchor_rule_kind="exact", confidence=CONF_VECTOR,
        rel_uncertainty=rel,
        provenance={"from": "two-point calibration",
                    "length_pt": round(length, 3)})
