"""Plan locations: exploration symbols off the vector drawing, and a
right-handed page-to-local grid (BACKLOG PL-25).

A site plan locates its borings, test pits and soundings twice: as a SYMBOL
(a circle, a circle with a cross, a square ...) whose centre is the location,
and as a LABEL ("B-1", "TP-1", "CPT-3") set beside it. A vector PDF holds the
symbol's centre exactly. Reading the location off a picture instead lands on
the label: measured on a field session (the app's 2026-10-14 trace review),
points read from vision prose were a median 8.8 pt (4.7 m at 1:1500) off the
symbol centres, most of them on the label text beside the symbol.

:func:`find_plan_symbols` reads the page's own paths, groups the small closed
shapes into symbols by drawn type and size (one program's symbols share a type
and a size), and pairs each with the nearest label, one label per symbol.
Every centre is in page points, in the displayed frame every other planlens
tool uses (origin top left, y DOWN); the label pairing carries a confidence.

The second error of that session was the grid: a rotation written by hand
from the north arrow, in a page frame whose y runs down, which turned the map
into its mirror image ("east" pointed west and every x had the wrong sign).
:func:`fit_plan_grid` fits page points to local easting / northing from two
or more control points (grid intersections, coordinate labels, surveyed
points); :func:`grid_from_north` builds the grid from a north arrow, a scale
and an origin. Both are RIGHT-HANDED by construction: east is 90 degrees
clockwise from north on the page as viewed. With three or more control
points that are not in a line, the fit also tries the mirror image; when the
mirror fits the controls and the right-handed grid does not, the control set
itself is mirrored (easting and northing swapped, or a y read downward) and
the fit is refused, or warned about on request. Residuals are reported per
control point either way.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from planlens.document.frame import to_display_bbox, to_display_point

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]

#: Default label pattern: a site-exploration ID — letters, an optional
#: hyphen, a number, an optional letter suffix (B-1, BH-12, TP-1, CPT-3A,
#: MW-2, EB-1, HA-4). A deployment whose plans use another scheme passes
#: ``label_pattern``.
DEFAULT_LABEL_PATTERN = (
    r"(?:B|BH|TB|SB|DH|EB|TP|TH|TT|TR|CPT|CPTU|SCPT|SCPTU|DMT|SPT|MW|OW|PZ|"
    r"P|HA|AH|AB|DCP|PB|RC|R|S|W|WB|GB|BR|FB)-?\d{1,4}[A-Z]?")

#: Smallest and largest symbol, as its width in page points. Exploration
#: symbols on the plans this was built from were 9.0 to 12.6 pt across.
DEFAULT_MIN_SIZE = 3.0
DEFAULT_MAX_SIZE = 36.0

#: A label may sit up to this many symbol widths from the symbol's edge ...
LABEL_GAP_WIDTHS = 3.0
#: ... and never less than this many points.
LABEL_GAP_FLOOR = 20.0

#: Two widths whose ratio is within this are one size (a program's symbols
#: are drawn from one block; the PDF's numbers jitter by a few hundredths).
_SIZE_TOL = 0.06

#: Default for :func:`fit_plan_grid`'s ``on_mirror``.
DEFAULT_ON_MIRROR = "refuse"


class PlanGridError(ValueError):
    """A grid that cannot be fitted as asked: too few or coincident control
    points, or a mirrored control set with ``on_mirror="refuse"``.
    ``details`` holds both fits' residuals so a caller can show them."""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None):
        super().__init__(message)
        self.details = details or {}


# ---------------------------------------------------------------------------
# Symbols
# ---------------------------------------------------------------------------

@dataclass
class _Shape:
    kind: str                 # circle | square | diamond | triangle
    center: Point
    size: float               # width across, page points
    filled: bool
    pts: List[Point]


@dataclass
class PlanSymbol:
    """One exploration symbol: where the drawing puts it, and its label."""
    page: int
    center: Point
    shape: str                # circle, circle-cross, circle-dot, square ...
    size: float               # width across, page points
    filled: bool = False
    label: Optional[str] = None
    label_bbox: Optional[BBox] = None
    #: Gap from the symbol's edge to the label's box, page points.
    label_gap: Optional[float] = None
    #: How sure the LABEL pairing is, 0-1. The centre itself is exact
    #: (read off the PDF's own path coordinates).
    confidence: float = 0.0
    #: A nearer-than-comfortable competitor: the other label this symbol could
    #: take, or the other symbol this label could belong to.
    alternative: Optional[str] = None
    notes: List[str] = field(default_factory=list)

    @property
    def style(self) -> str:
        """What one program's symbols share: type, fill and size."""
        return (("filled " if self.filled else "") + self.shape
                + f" {self.size:.1f} pt")

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "center": [round(self.center[0], 2), round(self.center[1], 2)],
            "shape": self.shape, "size": round(self.size, 2),
            "style": self.style,
        }
        if self.filled:
            out["filled"] = True
        if self.label is not None:
            out["label"] = self.label
            out["label_bbox"] = [round(v, 1) for v in self.label_bbox]
            out["label_gap"] = round(self.label_gap, 1)
            out["confidence"] = round(self.confidence, 2)
        if self.alternative:
            out["alternative"] = self.alternative
        if self.notes:
            out["notes"] = list(self.notes)
        return out


@dataclass
class PlanSymbols:
    """What :func:`find_plan_symbols` found on one page."""
    page: int
    symbols: List[PlanSymbol]
    #: Labels matching the pattern that no symbol took: a label with no
    #: drawn symbol, or one beyond the gap limit.
    unmatched_labels: List[Dict[str, Any]]
    warnings: List[str] = field(default_factory=list)
    choices: List[Dict[str, Any]] = field(default_factory=list)
    defaults_used: Dict[str, Any] = field(default_factory=dict)

    def styles(self) -> Dict[str, int]:
        out: Dict[str, int] = {}
        for s in self.symbols:
            out[s.style] = out.get(s.style, 0) + 1
        return dict(sorted(out.items(), key=lambda kv: -kv[1]))

    def by_label(self) -> Dict[str, PlanSymbol]:
        return {s.label: s for s in self.symbols if s.label}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "page": self.page, "pdf_page": self.page + 1,
            "frame": "page points, displayed frame (origin top left, y down)",
            "styles": self.styles(),
            "symbols": [s.to_dict() for s in self.symbols],
        }
        if self.unmatched_labels:
            out["unmatched_labels"] = self.unmatched_labels
        if self.warnings:
            out["warnings"] = self.warnings
        out["choices"] = self.choices
        if self.defaults_used:
            out["defaults_used"] = self.defaults_used
        return out


def _bezier(p0, p1, p2, p3, t: float) -> Point:
    u = 1.0 - t
    return (u ** 3 * p0[0] + 3 * u * u * t * p1[0] + 3 * u * t * t * p2[0]
            + t ** 3 * p3[0],
            u ** 3 * p0[1] + 3 * u * u * t * p1[1] + 3 * u * t * t * p2[1]
            + t ** 3 * p3[1])


def _close(a: Point, b: Point, tol: float = 0.05) -> bool:
    return abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol


def _subpaths(items) -> Tuple[List[Tuple[List[Point], int, bool]],
                              List[Tuple[Point, Point]]]:
    """A path's items as closed point rings ``(points, n_curves, closed)``
    and its single straight segments. Items are ``get_cdrawings()``'s (plain
    tuples) or ``get_drawings()``'s (Point / Rect / Quad): both index alike."""
    rings: List[Tuple[List[Point], int, bool]] = []
    segments: List[Tuple[Point, Point]] = []
    cur: List[Point] = []
    curves = 0
    lines = 0

    def flush():
        nonlocal cur, curves, lines
        if cur:
            closed = len(cur) > 2 and _close(cur[0], cur[-1], 0.2)
            if closed and curves == 0 and len(cur) <= 3:
                # out and back along one line (how some writers stroke a
                # single segment): a segment, not a ring
                segments.append((cur[0], cur[1]))
            elif closed:
                rings.append((cur[:-1], curves, True))
            elif lines == 1 and curves == 0 and len(cur) == 2:
                segments.append((cur[0], cur[1]))
            elif curves == 0:
                for a, b in zip(cur, cur[1:]):
                    segments.append((a, b))
            else:
                rings.append((cur, curves, False))
        cur, curves, lines = [], 0, 0

    for it in items:
        op = it[0]
        if op == "re":
            flush()
            r = it[1]
            rings.append(([(r[0], r[1]), (r[2], r[1]), (r[2], r[3]),
                           (r[0], r[3])], 0, True))
            continue
        if op == "qu":
            flush()
            q = it[1]               # ul, ur, ll, lr
            rings.append(([(q[0][0], q[0][1]), (q[1][0], q[1][1]),
                           (q[3][0], q[3][1]), (q[2][0], q[2][1])], 0, True))
            continue
        a = (it[1][0], it[1][1])
        if cur and not _close(cur[-1], a):
            flush()
        if not cur:
            cur.append(a)
        if op == "l":
            cur.append((it[2][0], it[2][1]))
            lines += 1
        elif op == "c":
            p0, p1, p2, p3 = ((p[0], p[1]) for p in it[1:5])
            for t in (0.25, 0.5, 0.75):
                cur.append(_bezier(p0, p1, p2, p3, t))
            cur.append(p3)
            curves += 1
    flush()
    return rings, segments


def _classify_ring(pts: List[Point], n_curves: int, closed: bool,
                   filled: bool) -> Optional[_Shape]:
    if len(pts) < 3:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    w, h = max(xs) - min(xs), max(ys) - min(ys)
    if w <= 0 or h <= 0:
        return None
    cx, cy = (max(xs) + min(xs)) / 2.0, (max(ys) + min(ys)) / 2.0
    aspect = w / h
    radii = [math.hypot(x - cx, y - cy) for x, y in pts]
    mean_r = sum(radii) / len(radii)
    round_ = (max(radii) - min(radii)) / mean_r if mean_r else 1.0
    if closed and 0.9 <= aspect <= 1.1 and round_ < 0.08 and (
            n_curves >= 4 or len(pts) >= 8):
        return _Shape("circle", (cx, cy), (w + h) / 2.0, filled, pts)
    if n_curves:
        return None
    if not closed:
        return None
    # A straight-sided polygon: drop collinear in-between vertices.
    corners = []
    n = len(pts)
    for i in range(n):
        a, b, c = pts[i - 1], pts[i], pts[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if abs(cross) > 1e-3 * (w * h):
            corners.append(b)
    if len(corners) == 3:
        # the centroid, not the box centre: a triangle's box centre is off it
        tx = sum(p[0] for p in corners) / 3.0
        ty = sum(p[1] for p in corners) / 3.0
        return _Shape("triangle", (tx, ty), (w + h) / 2.0, filled, corners)
    if len(corners) == 4 and 0.9 <= aspect <= 1.1:
        axis = all(abs(p[0] - min(xs)) < 0.05 * w or abs(p[0] - max(xs))
                   < 0.05 * w for p in corners)
        return _Shape("square" if axis else "diamond", (cx, cy),
                      (w + h) / 2.0, filled, corners)
    return None


def _shapes_and_segments(page, min_size: float = DEFAULT_MIN_SIZE,
                         max_size: float = DEFAULT_MAX_SIZE
                         ) -> Tuple[List[_Shape], List[Tuple[Point, Point]]]:
    """The page's small closed shapes, and the straight segments that could
    cross one (:func:`_through_centre`), in the displayed frame.

    A plan sheet holds hundreds of thousands of paths (the field session's
    site plan: 257,000 paths, 331,000 segments), so what can never be part
    of a symbol is dropped BEFORE it is converted or classified: a ring wider
    than ``max_size`` across, and a segment shorter than ``0.6 * min_size``
    or longer than ``1.7 * max_size`` (a crossing line is 1.2 to 3.4 radii
    long). Both tests are on lengths, which ``/Rotate`` (a multiple of 90
    degrees) does not change.
    """
    shapes: List[_Shape] = []
    segments: List[Tuple[Point, Point]] = []
    seg_lo, seg_hi = 0.6 * min_size, 1.7 * max_size
    for path in page.get_cdrawings():
        filled = path.get("fill") is not None and path.get("type") in (
            "f", "fs")
        rings, segs = _subpaths(path.get("items") or [])
        for pts, n_curves, closed in rings:
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            if (max(xs) - min(xs) + max(ys) - min(ys)) / 2.0 > max_size:
                continue
            disp = [to_display_point(page, x, y) for x, y in pts]
            sh = _classify_ring(disp, n_curves, closed, filled)
            if sh is not None:
                shapes.append(sh)
        for a, b in segs:
            if not seg_lo <= math.hypot(b[0] - a[0], b[1] - a[1]) <= seg_hi:
                continue
            segments.append((to_display_point(page, *a),
                             to_display_point(page, *b)))
    return shapes, segments


def _cells(points: Sequence[Point], cell: float) -> Dict[Tuple[int, int],
                                                          List[int]]:
    """Indices of ``points`` bucketed on a square grid of side ``cell``."""
    grid: Dict[Tuple[int, int], List[int]] = {}
    for k, (x, y) in enumerate(points):
        grid.setdefault((int(math.floor(x / cell)),
                         int(math.floor(y / cell))), []).append(k)
    return grid


def _near(grid: Dict[Tuple[int, int], List[int]], c: Point,
          cell: float) -> List[int]:
    """Indices in the 3 x 3 cells round ``c``: everything within ``cell``."""
    i, j = int(math.floor(c[0] / cell)), int(math.floor(c[1] / cell))
    out: List[int] = []
    for di in (-1, 0, 1):
        for dj in (-1, 0, 1):
            out.extend(grid.get((i + di, j + dj), ()))
    return out


def _through_centre(seg, c: Point, r: float) -> bool:
    (x0, y0), (x1, y1) = seg
    length = math.hypot(x1 - x0, y1 - y0)
    if length < 1.2 * r or length > 3.4 * r:
        return False
    for x, y in (seg[0], seg[1]):
        if math.hypot(x - c[0], y - c[1]) > 1.7 * r:
            return False
    # distance from the centre to the segment's line
    d = abs((x1 - x0) * (y0 - c[1]) - (x0 - c[0]) * (y1 - y0)) / length
    return d <= 0.15 * r


def _compose(shapes: List[_Shape], segments, min_size: float,
             max_size: float) -> List[Tuple[str, Point, float, bool]]:
    """Group the shapes drawn at one centre into symbols:
    ``(shape name, centre, size, filled)``."""
    cand = [s for s in shapes if s.size <= max_size]
    cand.sort(key=lambda s: -s.size)
    used = [False] * len(cand)
    out = []
    # Spatial buckets, so a sheet with thousands of small shapes and
    # hundreds of thousands of segments is not compared all against all:
    # a concentric shape is within 0.2 r <= 0.1 max_size of the centre, a
    # crossing segment's midpoint within 1.7 r <= 0.85 max_size.
    c_cell = max(0.1 * max_size, 1e-3)
    s_cell = max(0.85 * max_size, 1e-3)
    by_centre = _cells([s.center for s in cand], c_cell)
    by_mid = _cells([((a[0] + b[0]) / 2.0, (a[1] + b[1]) / 2.0)
                     for a, b in segments], s_cell)
    for i, s in enumerate(cand):
        if used[i] or s.size < min_size:
            continue
        used[i] = True
        r = s.size / 2.0
        filled = s.filled
        inner = []
        for j in sorted(_near(by_centre, s.center, c_cell)):
            if j <= i or used[j]:
                continue
            o = cand[j]
            if math.hypot(o.center[0] - s.center[0],
                          o.center[1] - s.center[1]) > 0.2 * r:
                continue
            used[j] = True
            if o.kind == s.kind and abs(o.size - s.size) <= _SIZE_TOL * s.size:
                filled = filled or o.filled      # the same outline twice
            else:
                inner.append(o)
        name = s.kind
        crossing = [segments[k] for k in _near(by_mid, s.center, s_cell)
                    if _through_centre(segments[k], s.center, r)]
        if len(crossing) >= 2:
            angles = sorted(math.degrees(math.atan2(b[1] - a[1], b[0] - a[0]))
                            % 180.0 for a, b in crossing)
            if any(abs(((x - y) + 90.0) % 180.0 - 90.0) > 60.0
                   for x in angles for y in angles):
                name += "-cross"
        dots = [o for o in inner if o.kind == "circle" and o.filled
                and o.size <= 0.6 * s.size]
        if dots:
            name += "-dot"
        elif any(o.kind != s.kind or o.filled for o in inner):
            name += "-inner-" + "-".join(sorted({o.kind for o in inner}))
        out.append((name, s.center, s.size, filled))
    return out


def _labels(page, pattern: "re.Pattern", cad_lines=()) -> List[Dict[str, Any]]:
    """Words matching the label pattern, with displayed-frame boxes; hidden
    CAD (SHX) strings included, since stroked lettering has no text layer."""
    out = []
    for w in page.get_text("words"):
        text = w[4].strip(" ,;:()[]")
        if pattern.fullmatch(text):
            out.append({"text": text, "bbox": to_display_bbox(page, w[:4]),
                        "source": "text"})
    for ln in cad_lines:
        full = ln.text
        x0, y0, x1, y1 = ln.bbox
        for m in re.finditer(r"\S+", full):
            tok = m.group(0).strip(" ,;:()[]")
            if not pattern.fullmatch(tok):
                continue
            # the token's share of the string's box (characters are not all
            # one width; the centre is close enough to pair a label)
            n = max(len(full), 1)
            if (x1 - x0) >= (y1 - y0):
                box = (x0 + (x1 - x0) * m.start() / n, y0,
                       x0 + (x1 - x0) * m.end() / n, y1)
            else:
                box = (x0, y0 + (y1 - y0) * m.start() / n, x1,
                       y0 + (y1 - y0) * m.end() / n)
            out.append({"text": tok, "bbox": box, "source": "cad_hidden_text"})
    return out


def _gap(c: Point, r: float, box: BBox) -> float:
    dx = max(box[0] - c[0], 0.0, c[0] - box[2])
    dy = max(box[1] - c[1], 0.0, c[1] - box[3])
    return max(math.hypot(dx, dy) - r, 0.0)


def _in_region(c: Point, region: Optional[Sequence[float]]) -> bool:
    return region is None or (region[0] <= c[0] <= region[2]
                              and region[1] <= c[1] <= region[3])


def find_plan_symbols(document, page: int, *,
                      label_pattern: Optional[str] = None,
                      shapes: Optional[Iterable[str]] = None,
                      min_size: Optional[float] = None,
                      max_size: Optional[float] = None,
                      max_label_gap: Optional[float] = None,
                      region: Optional[Sequence[float]] = None) -> PlanSymbols:
    """Exploration symbols on one plan page, by drawn type, with their labels.

    ``document`` is a :class:`planlens.document.Document`; ``page`` counts
    from 0. ``shapes`` keeps only symbols whose shape name starts with one of
    these ("circle" keeps circle, circle-cross, circle-dot ...).
    ``label_pattern`` is a regular expression a whole label word must match
    (default :data:`DEFAULT_LABEL_PATTERN`). ``max_label_gap`` caps the gap
    from a symbol's edge to its label, in points (default: three symbol
    widths, at least 20 pt). ``region`` is a displayed-frame box to look in
    (default: the whole page).
    """
    defaults: Dict[str, Any] = {}
    if label_pattern is None:
        label_pattern = DEFAULT_LABEL_PATTERN
        defaults["label_pattern"] = "site-exploration IDs (B-1, TP-1, CPT-3 ...)"
    if min_size is None:
        min_size = DEFAULT_MIN_SIZE
        defaults["min_size"] = DEFAULT_MIN_SIZE
    if max_size is None:
        max_size = DEFAULT_MAX_SIZE
        defaults["max_size"] = DEFAULT_MAX_SIZE
    if max_label_gap is None:
        defaults["max_label_gap"] = (f"{LABEL_GAP_WIDTHS:g} symbol widths, "
                                     f"at least {LABEL_GAP_FLOOR:g} pt")
    pat = re.compile(label_pattern, re.I)
    fpage = document._doc[page]
    _markups, cad = document._page_annots(page)
    raw_shapes, segments = _shapes_and_segments(fpage, float(min_size),
                                                float(max_size))
    composed = [c for c in _compose(raw_shapes, segments, float(min_size),
                                    float(max_size))
                if _in_region(c[1], region)]
    if shapes:
        keep = tuple(str(s).lower() for s in shapes)
        composed = [c for c in composed if c[0].startswith(keep)]
    labels = [lb for lb in _labels(fpage, pat, cad)
              if _in_region(((lb["bbox"][0] + lb["bbox"][2]) / 2,
                             (lb["bbox"][1] + lb["bbox"][3]) / 2), region)]
    symbols = [PlanSymbol(page=page, center=c, shape=name, size=size,
                          filled=filled)
               for name, c, size, filled in composed]
    limits = [float(max_label_gap) if max_label_gap is not None
              else max(LABEL_GAP_WIDTHS * s.size, LABEL_GAP_FLOOR)
              for s in symbols]
    gaps = [[_gap(s.center, s.size / 2.0, lb["bbox"]) for lb in labels]
            for s in symbols]
    pairs = sorted((gaps[i][j], i, j) for i in range(len(symbols))
                   for j in range(len(labels)) if gaps[i][j] <= limits[i])
    sym_taken: Dict[int, int] = {}
    lab_taken: Dict[int, int] = {}
    for g, i, j in pairs:
        if i in sym_taken or j in lab_taken:
            continue
        sym_taken[i] = j
        lab_taken[j] = i
    for i, j in sym_taken.items():
        s, lb, g = symbols[i], labels[j], gaps[i][j]
        s.label, s.label_bbox, s.label_gap = lb["text"], lb["bbox"], g
        # the nearest competitor: another label for this symbol, or another
        # symbol for this label
        alt_gap, alt = math.inf, None
        for jj in range(len(labels)):
            if jj != j and gaps[i][jj] < alt_gap:
                alt_gap, alt = gaps[i][jj], f"label {labels[jj]['text']}"
        for ii in range(len(symbols)):
            if ii != i and gaps[ii][j] < alt_gap:
                alt_gap = gaps[ii][j]
                c = symbols[ii].center
                alt = (f"symbol at [{c[0]:.1f}, {c[1]:.1f}]"
                       + (f" ({symbols[ii].label})" if ii in sym_taken
                          and symbols[ii].label else ""))
        near = 1.0 - 0.5 * g / limits[i]
        room = max(s.size, g)
        sep = 1.0 if alt_gap - g >= room else max(alt_gap - g, 0.0) / room
        s.confidence = near * (0.5 + 0.5 * sep)
        if sep < 0.5:
            s.alternative = alt
            s.notes.append("ambiguous: a competitor is nearly as close; "
                           "confirm the pairing on a render")
        if lb["source"] != "text":
            s.notes.append(f"label from {lb['source']}")
    unmatched = [{"text": lb["text"],
                  "bbox": [round(v, 1) for v in lb["bbox"]]}
                 for j, lb in enumerate(labels) if j not in lab_taken]
    warnings: List[str] = []
    seen: Dict[str, List[PlanSymbol]] = {}
    for s in symbols:
        if s.label:
            seen.setdefault(s.label.upper(), []).append(s)
    for lab, group in seen.items():
        if len(group) > 1:
            warnings.append(f"label {lab} is on {len(group)} symbols")
    if not symbols:
        warnings.append("no symbols found: the page has no small closed "
                        "shapes in the size range (a scan, or symbols drawn "
                        "as a picture) — read them by looking instead")
    unlabelled = sum(1 for s in symbols if s.label is None)
    if unlabelled:
        warnings.append(f"{unlabelled} symbol(s) have no label within the gap "
                        "limit (other drawn circles, or a label drawn as "
                        "strokes)")
    symbols.sort(key=lambda s: (s.label is None, s.label or "",
                                s.center[1], s.center[0]))
    choices = [
        {"choice": "centre",
         "value": "the symbol's own path geometry (box centre; a triangle's "
                  "centroid)",
         "basis": "the PDF stores the symbol exactly; a label is set beside "
                  "it, not on it",
         "alternatives": ["the label's position (what a picture read gives)"]},
        {"choice": "label pairing",
         "value": "nearest label to the symbol's edge, one label per symbol, "
                  "closest pairs first",
         "basis": "a plan sets each label beside its own symbol",
         "alternatives": ["a leader line drawn from label to symbol "
                          "(not read here: confirm on a render)"]},
    ]
    return PlanSymbols(page=page, symbols=symbols, unmatched_labels=unmatched,
                       warnings=warnings, choices=choices,
                       defaults_used=defaults)


# ---------------------------------------------------------------------------
# Page -> local grid
# ---------------------------------------------------------------------------

def _z(x: float, y: float, right: bool) -> complex:
    # The displayed frame's y runs DOWN: a right-handed map (east 90 degrees
    # clockwise from north as viewed) is a similarity of (x, -y).
    return complex(x, -y) if right else complex(x, y)


def _fit(page_pts: List[Point], local: List[Point], right: bool
         ) -> Tuple[complex, complex]:
    zs = [_z(x, y, right) for x, y in page_pts]
    ws = [complex(e, n) for e, n in local]
    zm = sum(zs) / len(zs)
    wm = sum(ws) / len(ws)
    den = sum(abs(z - zm) ** 2 for z in zs)
    a = sum((w - wm) * (z - zm).conjugate() for z, w in zip(zs, ws)) / den
    return a, wm - a * zm


@dataclass
class PlanGrid:
    """A similarity map from page points to local easting / northing.

    ``local = a * z + b`` in complex numbers, ``z = x - i*y`` for a
    right-handed grid (``x + i*y`` for the mirror image, only ever returned
    with ``on_mirror="warn"``).
    """
    a: complex
    b: complex
    right_handed: bool = True
    units: Optional[str] = None
    residuals: List[Dict[str, Any]] = field(default_factory=list)
    rms: Optional[float] = None
    max_residual: Optional[float] = None
    mirror_rms: Optional[float] = None
    handedness_tested: bool = False
    warnings: List[str] = field(default_factory=list)
    choices: List[Dict[str, Any]] = field(default_factory=list)
    defaults_used: Dict[str, Any] = field(default_factory=dict)

    @property
    def scale(self) -> float:
        """Local units per page point."""
        return abs(self.a)

    def to_local(self, x: float, y: float) -> Point:
        w = self.a * _z(x, y, self.right_handed) + self.b
        return (w.real, w.imag)

    def to_page(self, easting: float, northing: float) -> Point:
        z = (complex(easting, northing) - self.b) / self.a
        return (z.real, -z.imag if self.right_handed else z.imag)

    def _azimuth(self, w: complex) -> float:
        """Page direction of a local direction ``w``, in degrees clockwise
        from straight up the page as viewed."""
        z = w / self.a
        dx, dy = z.real, (-z.imag if self.right_handed else z.imag)
        return math.degrees(math.atan2(dx, -dy)) % 360.0

    @property
    def north_on_page(self) -> float:
        return self._azimuth(1j)

    @property
    def east_on_page(self) -> float:
        return self._azimuth(1.0)

    def check(self) -> Dict[str, Any]:
        """East must be 90 degrees clockwise from north as the page is viewed."""
        turn = (self.east_on_page - self.north_on_page) % 360.0
        ok = abs(turn - 90.0) < 1e-6
        return {"north_on_page_deg": round(self.north_on_page, 3),
                "east_on_page_deg": round(self.east_on_page, 3),
                "east_minus_north_deg": round(turn, 3),
                "right_handed": ok,
                "rule": "east is 90 deg clockwise from north on the page as "
                        "viewed"}

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "handedness": "right" if self.right_handed else "LEFT (mirrored)",
            "scale": round(self.scale, 8),
            "scale_unit": f"{self.units or 'local units'} per page point",
            "check": self.check(),
            "frame": "page points, displayed frame (origin top left, y down)",
            "transform": {
                "a": [round(self.a.real, 10), round(self.a.imag, 10)],
                "b": [round(self.b.real, 6), round(self.b.imag, 6)],
                "form": ("E + iN = (a0 + i a1) * (x - i y) + (b0 + i b1)"
                         if self.right_handed else
                         "E + iN = (a0 + i a1) * (x + i y) + (b0 + i b1)")},
        }
        if self.residuals:
            out["residuals"] = self.residuals
            out["rms"] = round(self.rms, 4)
            out["max_residual"] = round(self.max_residual, 4)
        if self.mirror_rms is not None:
            out["mirror_rms"] = round(self.mirror_rms, 4)
        out["handedness_tested"] = self.handedness_tested
        if self.warnings:
            out["warnings"] = self.warnings
        out["choices"] = self.choices
        if self.defaults_used:
            out["defaults_used"] = self.defaults_used
        return out


def _control(c) -> Tuple[Optional[str], Point, Point]:
    if isinstance(c, dict):
        p, w = c.get("page"), c.get("local")
        if p is None or w is None:
            raise PlanGridError("a control point needs 'page' [x, y] and "
                                "'local' [easting, northing]")
        return c.get("name"), (float(p[0]), float(p[1])), (float(w[0]),
                                                           float(w[1]))
    p, w = c
    return None, (float(p[0]), float(p[1])), (float(w[0]), float(w[1]))


def _rms(page_pts, local, a, b, right) -> Tuple[float, List[float]]:
    errs = [abs(a * _z(x, y, right) + b - complex(e, n))
            for (x, y), (e, n) in zip(page_pts, local)]
    return math.sqrt(sum(e * e for e in errs) / len(errs)), errs


def fit_plan_grid(controls: Sequence[Any], *, on_mirror: Optional[str] = None,
                  scale: Optional[float] = None,
                  units: Optional[str] = None) -> PlanGrid:
    """A right-handed page-to-local grid fitted to two or more control points.

    Each control is ``{"page": [x, y], "local": [easting, northing],
    "name": ...}`` (or a ``(page, local)`` pair), the page point in the
    displayed frame. The fit is a similarity — one scale, one rotation, a
    shift — so it needs two points; three or more not in a line also TEST
    the handedness: the mirror image is fitted too, and when it fits and the
    right-handed grid does not, the controls are mirrored. ``on_mirror``:
    ``"refuse"`` (default) raises :class:`PlanGridError` with both fits'
    residuals; ``"warn"`` returns the mirrored fit flagged
    ``right_handed=False`` with a warning. ``scale`` (local units per page
    point, from a scale bar or a stated ratio) is checked against the fitted
    one.
    """
    defaults: Dict[str, Any] = {}
    if on_mirror is None:
        on_mirror = DEFAULT_ON_MIRROR
        defaults["on_mirror"] = DEFAULT_ON_MIRROR
    if on_mirror not in ("refuse", "warn"):
        raise ValueError("on_mirror must be 'refuse' or 'warn'")
    parsed = [_control(c) for c in controls]
    if len(parsed) < 2:
        raise PlanGridError("need at least two control points")
    names = [n or f"c{i}" for i, (n, _p, _w) in enumerate(parsed)]
    page_pts = [p for _n, p, _w in parsed]
    local = [w for _n, _p, w in parsed]
    spread_p = max(math.hypot(p[0] - q[0], p[1] - q[1])
                   for p in page_pts for q in page_pts)
    spread_w = max(math.hypot(p[0] - q[0], p[1] - q[1])
                   for p in local for q in local)
    if spread_p < 1e-6 or spread_w < 1e-9:
        raise PlanGridError("the control points coincide (on the page or in "
                            "local coordinates)")
    a_r, b_r = _fit(page_pts, local, True)
    a_m, b_m = _fit(page_pts, local, False)
    rms_r, err_r = _rms(page_pts, local, a_r, b_r, True)
    rms_m, err_m = _rms(page_pts, local, a_m, b_m, False)
    # Handedness is testable only with a third point off the line of the
    # other two: a similarity through two points (or a line) fits either way.
    xm = sum(p[0] for p in page_pts) / len(page_pts)
    ym = sum(p[1] for p in page_pts) / len(page_pts)
    sxx = sum((p[0] - xm) ** 2 for p in page_pts)
    syy = sum((p[1] - ym) ** 2 for p in page_pts)
    sxy = sum((p[0] - xm) * (p[1] - ym) for p in page_pts)
    tr, det = sxx + syy, sxx * syy - sxy * sxy
    lam_min = tr / 2.0 - math.sqrt(max(tr * tr / 4.0 - det, 0.0))
    lam_max = tr - lam_min
    tested = len(parsed) >= 3 and lam_min > (0.05 ** 2) * lam_max
    tol = 0.002 * spread_w
    mirrored = tested and rms_r > max(3.0 * rms_m, tol)
    warnings: List[str] = []
    details = {
        "right_handed_rms": rms_r, "mirror_rms": rms_m,
        "right_handed_residuals": dict(zip(names, err_r)),
        "mirror_residuals": dict(zip(names, err_m)),
    }
    if mirrored and on_mirror == "refuse":
        raise PlanGridError(
            f"the control points describe a MIRRORED grid: the right-handed "
            f"fit misses them by {rms_r:.4g} rms, the mirror image by "
            f"{rms_m:.4g}. Usually easting and northing are swapped, or a "
            f"local y was read growing down the page. Fix the controls, or "
            f"pass on_mirror='warn' to accept a left-handed grid.", details)
    right = not mirrored
    a, b = (a_r, b_r) if right else (a_m, b_m)
    errs = err_r if right else err_m
    residuals = []
    for name, (x, y), (e, n) in zip(names, page_pts, local):
        w = a * _z(x, y, right) + b
        residuals.append({"name": name, "dE": round(w.real - e, 4),
                          "dN": round(w.imag - n, 4),
                          "d": round(abs(w - complex(e, n)), 4)})
    if mirrored:
        warnings.append(
            f"MIRRORED grid returned (on_mirror='warn'): east is 90 deg "
            f"COUNTER-clockwise from north on the page; the right-handed fit "
            f"misses the controls by {rms_r:.4g} rms")
    if not tested:
        warnings.append(
            "handedness not tested: two control points (or points in a line) "
            "fit a grid and its mirror image equally well; the grid is "
            "right-handed by construction, so check that the controls are "
            "(east, north), not (north, east)")
    elif not mirrored and rms_m <= 3.0 * rms_r and rms_r > tol:
        warnings.append("neither the grid nor its mirror image fits the "
                        "controls well: check them for a wrong value")
    grid = PlanGrid(a=a, b=b, right_handed=right, units=units,
                    residuals=residuals,
                    rms=math.sqrt(sum(e * e for e in errs) / len(errs)),
                    max_residual=max(errs),
                    mirror_rms=(rms_m if right else rms_r) if tested else None,
                    handedness_tested=tested, warnings=warnings,
                    defaults_used=defaults)
    if scale is not None:
        rel = grid.scale / float(scale) - 1.0
        if abs(rel) > 0.02:
            warnings.append(f"the fitted scale {grid.scale:.6g} differs from "
                            f"the stated {float(scale):.6g} by {rel:+.1%}: "
                            "a control value or the stated scale is wrong, or "
                            "the sheet was re-plotted")
    grid.choices = [
        {"choice": "transform",
         "value": "similarity (one scale, one rotation, a shift), least "
                  "squares over the controls",
         "basis": "a plan drawn to one scale in both directions",
         "alternatives": ["affine (separate x and y scales) for a distorted "
                          "scan: not offered"]},
        {"choice": "handedness",
         "value": "right" if right else "left (mirrored; on_mirror='warn')",
         "basis": ("tested: the right-handed fit and its mirror image both "
                   "fitted, the better one kept only if right-handed or "
                   "warned" if tested else
                   "assumed: plans are drawn right-handed; two points cannot "
                   "tell"),
         "alternatives": ["on_mirror='refuse' (default)", "on_mirror='warn'"]},
    ]
    return grid


def grid_from_north(north_from: Point, north_to: Point, scale: float,
                    origin_page: Point, origin_local: Point = (0.0, 0.0), *,
                    units: Optional[str] = None) -> PlanGrid:
    """A right-handed grid from a north arrow, a scale and an origin.

    ``north_from`` -> ``north_to`` is the arrow (tail to head) in displayed
    page points; ``scale`` is local units per page point (a scale bar's
    length over its drawn length); ``origin_page`` is where
    ``origin_local`` (easting, northing) lies on the page. East is taken 90
    degrees clockwise from north as viewed — never computed by rotating the
    page frame, whose y runs down and would mirror the map.
    """
    dx, dy = north_to[0] - north_from[0], north_to[1] - north_from[1]
    length = math.hypot(dx, dy)
    if length <= 0:
        raise PlanGridError("the north arrow has no length")
    if scale <= 0:
        raise PlanGridError("scale must be positive (local units per point)")
    z_north = _z(dx / length, dy / length, True)
    a = 1j * float(scale) / z_north
    b = complex(*origin_local) - a * _z(origin_page[0], origin_page[1], True)
    grid = PlanGrid(a=a, b=b, right_handed=True, units=units,
                    warnings=["no control points: the grid is only as good as "
                              "the arrow and the scale; check it on a known "
                              "point"])
    grid.choices = [
        {"choice": "east", "value": "90 deg clockwise from north as viewed",
         "basis": "a right-handed grid (easting, northing)",
         "alternatives": []}]
    return grid


def check_plan_points(points: Sequence[Dict[str, Any]],
                      symbols: PlanSymbols,
                      tol: float = 1.0) -> List[Dict[str, Any]]:
    """Flag points written for explorations that do not sit on a symbol.

    Each point is ``{"name", "page": [x, y]}``. Flags: two names at one point
    (within ``tol`` page points), a point on a label's box rather than on a
    symbol, and a point whose nearest symbol carries a different label.
    """
    flags: List[Dict[str, Any]] = []
    pts = [(str(p.get("name", f"p{i}")), (float(p["page"][0]),
                                          float(p["page"][1])))
           for i, p in enumerate(points)]
    for i, (na, a) in enumerate(pts):
        for nb, b in pts[i + 1:]:
            if na != nb and math.hypot(a[0] - b[0], a[1] - b[1]) <= tol:
                flags.append({"names": [na, nb], "flag": "same point",
                              "page": [round(a[0], 2), round(a[1], 2)]})
    for name, (x, y) in pts:
        best, bd = None, math.inf
        for s in symbols.symbols:
            d = math.hypot(x - s.center[0], y - s.center[1])
            if d < bd:
                best, bd = s, d
        on_symbol = best is not None and bd <= best.size / 2.0
        if not on_symbol:
            for s in symbols.symbols:
                bx = s.label_bbox
                if bx and bx[0] <= x <= bx[2] and bx[1] <= y <= bx[3]:
                    flags.append({
                        "names": [name], "flag": "on a label, not its symbol",
                        "label": s.label,
                        "symbol_center": [round(s.center[0], 2),
                                          round(s.center[1], 2)],
                        "off_by_pt": round(math.hypot(x - s.center[0],
                                                      y - s.center[1]), 2)})
                    break
            else:
                if best is not None:
                    flags.append({
                        "names": [name], "flag": "not on a symbol",
                        "nearest": best.label,
                        "symbol_center": [round(best.center[0], 2),
                                          round(best.center[1], 2)],
                        "off_by_pt": round(bd, 2)})
        elif best.label and best.label.upper() != name.upper():
            flags.append({"names": [name], "flag": "on another label's symbol",
                          "label": best.label})
    return flags
