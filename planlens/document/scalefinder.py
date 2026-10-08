"""``find_scales`` — every scale a page carries, found from its text, paths or pixels.

A page can carry many scales: a boring log's depth ruler, a plot's two axes
(linear or log), a profile's stations and elevations, a plan's scale bar, its
printed ratio and the scale the PDF stores. This module finds them all, the
same way on a vector page and on a scan with no text, and returns them as
:class:`~planlens.document.scales.Frame` s of
:class:`~planlens.document.scales.Scale` s with their evidence.

THE ORDER OF WORK on one page:

1. **Facts.** Text lines and the lone numbers among them (a station
   ``12+50`` and a coordinate ``N 2,100`` are numbers too); every drawn
   segment, rule, filled box and curve path in the displayed frame; and on a
   picture page a render at 200 dpi with its skew and every ruled line found
   in pixels (:mod:`planlens.document.raster`).
2. **Gridded frames** (plots, profiles, chart families). Rules that share
   their extents are one grid; its positions step evenly (a linear axis) or in
   the pattern of a log decade (:func:`~planlens.document.scales.log_decades`);
   the printed labels aligned with its lines give the values — from text, or,
   on a scan with no text, as numbered label boxes whose values the caller
   reads (``needs_values``). Values go to gridlines by fitted spacing, never by
   count: a gridline broken by a title is a gap, not a shift. A plot drawn
   with ticks only is found from its frame and the ticks along it.
3. **Label runs** (depth and elevation rulers, scales printed down a margin).
   Lone numbers standing in one band that rise or fall evenly down the page;
   on a scan without text, ink blobs of label size standing in one ruled
   column at even spacing (the depth column is found from the labels
   themselves, never from where it usually sits). The anchor rule then ties
   the labels to the drawing: ticks beside the labels; else frame lines the
   run predicts as round values, which fix a constant offset; else the label
   centres, with half a label height added to the uncertainty and a warning.
4. **Plan scales.** The ``/VP`` scale the PDF stores; a printed note ("1 in =
   20 ft", "1:100") on any page; a graphic scale bar (labels over ticks or
   block edges, vector or pixels); a coordinate grid's labels. Several sources
   on one sheet are COMPARED, never chosen silently: agreement within 2 %
   raises the stated note's confidence; on disagreement a stored scale or a
   bar beats a note (a re-plot changes the paper, and the bar shrinks with
   it), and the result names both and the size of the gap.

Nothing here calls a model. Where a scan has no text, a scale comes back with
``needs_values`` and its label boxes in run order; pass ``values`` back (one
value per box, ``None`` for one that cannot be read) and the fit runs. A
misread value breaks the even run: one is dropped and named, two are refused.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.scales import (
    unrotate_point, Anchor, Frame, PageScales, Scale, confidence_for, fit_labels,
    index_by_spacing, log_decades, printed_resolution, rms_dof, rotate_point,
    scale_from_stated, scale_from_viewport, MAX_RESIDUAL_FRAC,
)

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

__all__ = ["find_scales", "page_facts", "PageFacts", "Label",
           "RECONCILE_AGREE", "parse_label_value"]

#: Two scales of one sheet agree within this relative difference.
RECONCILE_AGREE = 0.02

#: A label is aligned with a gridline when its centre is this close (points,
#: or this fraction of a grid step, whichever is larger).
ALIGN_PT = 3.0
ALIGN_STEP = 0.2

#: Label blobs on a scan: sizes a printed number can have.
LABEL_MIN_H = 2.5
LABEL_MAX_H = 14.0
LABEL_MAX_W = 70.0

#: A tick beside a label: within this of the label's centre across the axis
#: (plus half the label height), and this far outside the label band.
TICK_REACH = 12.0


# ---------------------------------------------------------------------------
# Numbers as printed
# ---------------------------------------------------------------------------

_NUM = r"[-+−–]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.?\d+)"
_RE_PLAIN = re.compile(rf"^\s*({_NUM})\s*[-–—]?\s*$")
_RE_STATION = re.compile(r"^\s*(?:STA\.?\s*)?(\d+)\s*\+\s*(\d{2}(?:\.\d+)?)\s*$",
                         re.I)
_RE_COORD = re.compile(rf"^\s*([NE])\s*[:=]?\s*({_NUM})\s*$", re.I)


def parse_label_value(text: str) -> Optional[Tuple[float, str]]:
    """A printed label -> ``(value, kind)`` or ``None``.

    ``kind`` is ``number``, ``station`` ("12+50" is 1250), ``northing`` or
    ``easting`` ("N 2,100", "E 1,000").
    """
    t = str(text or "").strip()
    if not t:
        return None
    m = _RE_STATION.match(t)
    if m:
        return float(m.group(1)) * 100.0 + float(m.group(2)), "station"
    m = _RE_COORD.match(t)
    if m:
        v = _to_float(m.group(2))
        if v is None:
            return None
        return v, ("northing" if m.group(1).upper() == "N" else "easting")
    m = _RE_PLAIN.match(t)
    if m:
        v = _to_float(m.group(1))
        if v is None:
            return None
        return v, "number"
    return None


def _to_float(tok: str) -> Optional[float]:
    tok = tok.replace(",", "").replace("−", "-").replace("–", "-")
    try:
        return float(tok)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Page facts
# ---------------------------------------------------------------------------

@dataclass
class Label:
    """A printed number on the page, wherever it came from."""
    value: Optional[float]
    text: str
    box: BBox                       # displayed frame, tight round the ink
    kind: str = "number"
    source: str = "text"            # text | ocr | azure_di | pixels
    positioned_by: str = "text_box"  # text_box | pixels
    used: bool = False

    @property
    def centre(self) -> Point:
        return ((self.box[0] + self.box[2]) / 2.0,
                (self.box[1] + self.box[3]) / 2.0)

    @property
    def height(self) -> float:
        return self.box[3] - self.box[1]

    @property
    def width(self) -> float:
        return self.box[2] - self.box[0]


@dataclass
class PageFacts:
    """Everything a finder or a snap needs to know about one page."""
    index: int
    width: float
    height: float
    angle_deg: float = 0.0
    raster: Any = None               # planlens.document.raster.Raster
    is_raster: bool = False
    lines: List[Any] = field(default_factory=list)       # RasterLine (h/v)
    segments: List[Tuple[float, float, float, float, float]] = field(
        default_factory=list)        # short vector segments: x0,y0,x1,y1,w
    rects: List[Tuple[BBox, bool]] = field(default_factory=list)
    curves: List[List[Point]] = field(default_factory=list)
    fills: List[BBox] = field(default_factory=list)      # small filled marks
    labels: List[Label] = field(default_factory=list)
    texts: List[Any] = field(default_factory=list)       # TextLine
    text_source: str = "pdf_text"
    warnings: List[str] = field(default_factory=list)
    _nolines: Any = None

    @property
    def pixel_pt(self) -> float:
        return self.raster.pixel_pt if self.raster is not None else 0.0

    def deskew(self, x: float, y: float) -> Point:
        return rotate_point(x, y, self.angle_deg)

    def mask_without_lines(self):
        """The ink mask with every found ruled line erased (for blobs)."""
        if self.raster is None:
            return None
        if self._nolines is None:
            from planlens.document.raster import erase_lines
            self._nolines = erase_lines(
                self.raster, [L for L in self.lines if L.source == "pixels"])
        return self._nolines

    def hlines(self) -> List[Any]:
        return [L for L in self.lines if L.orientation == "h"]

    def vlines(self) -> List[Any]:
        return [L for L in self.lines if L.orientation == "v"]

    def line_span(self, L) -> Tuple[float, float, float]:
        """A line in the deskewed frame: ``(position, lo, hi)``."""
        (x0, y0), (x1, y1) = L.endpoints()
        a = self.deskew(x0, y0)
        b = self.deskew(x1, y1)
        if L.orientation == "h":
            return ((a[1] + b[1]) / 2.0, min(a[0], b[0]), max(a[0], b[0]))
        return ((a[0] + b[0]) / 2.0, min(a[1], b[1]), max(a[1], b[1]))


def _vector_geometry(page, facts: PageFacts) -> None:
    """Every segment, box and curve the page draws, in the displayed frame."""
    from planlens.document.frame import to_display_bbox, to_display_point
    from planlens.document.loggrid import _Rule, join_segments
    from planlens.document.raster import RasterLine
    try:
        drawings = page.get_cdrawings()
    except Exception:                                   # pragma: no cover
        return
    h_rules: List[Any] = []
    v_rules: List[Any] = []
    widths_h: Dict[int, float] = {}
    for d in drawings:
        width = float(d.get("width") or 0.0) or 0.5
        filled = d.get("fill") is not None
        stroked = "s" in str(d.get("type") or "s")
        items = d.get("items", ())
        pts_chain: List[Point] = []
        n_diag = 0
        for it in items:
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                ax, ay = to_display_point(page, x0, y0)
                bx, by = to_display_point(page, x1, y1)
                ln = math.hypot(bx - ax, by - ay)
                if ln < 0.3:
                    continue
                if not stroked and filled:
                    continue
                if abs(by - ay) <= 0.35 and ln >= 0.8:
                    facts.segments.append((min(ax, bx), ay, max(ax, bx), by,
                                           width))
                    if ln >= 12.0:
                        h_rules.append(_Rule((ay + by) / 2.0, min(ax, bx),
                                             max(ax, bx)))
                        widths_h[len(h_rules) - 1] = width
                elif abs(bx - ax) <= 0.35 and ln >= 0.8:
                    facts.segments.append((ax, min(ay, by), bx, max(ay, by),
                                           width))
                    if ln >= 12.0:
                        v_rules.append(_Rule((ax + bx) / 2.0, min(ay, by),
                                             max(ay, by)))
                else:
                    n_diag += 1
                if not pts_chain or math.dist(pts_chain[-1], (ax, ay)) > 0.05:
                    if pts_chain:
                        pts_chain.append((float("nan"), float("nan")))
                    pts_chain.append((ax, ay))
                pts_chain.append((bx, by))
            elif it[0] == "re":
                # get_cdrawings gives a rectangle as (x0, y0, x1, y1).
                r = it[1]
                bb = to_display_bbox(page, (min(r[0], r[2]), min(r[1], r[3]),
                                            max(r[0], r[2]), max(r[1], r[3])))
                w, h = bb[2] - bb[0], bb[3] - bb[1]
                facts.rects.append((bb, filled))
                if filled and w <= 14 and h <= 14 and w > 0.5 and h > 0.5:
                    facts.fills.append(bb)
                if stroked or (filled and min(w, h) <= 2.0):
                    for (x0, y0, x1, y1) in ((bb[0], bb[1], bb[2], bb[1]),
                                             (bb[0], bb[3], bb[2], bb[3])):
                        if w >= 0.8:
                            facts.segments.append((x0, y0, x1, y1, width))
                            if w >= 12.0 and (stroked or h <= 2.0):
                                yy = y0 if stroked else (bb[1] + bb[3]) / 2
                                h_rules.append(_Rule(yy, x0, x1))
                    for (x0, y0, x1, y1) in ((bb[0], bb[1], bb[0], bb[3]),
                                             (bb[2], bb[1], bb[2], bb[3])):
                        if h >= 0.8:
                            facts.segments.append((x0, y0, x1, y1, width))
                            if h >= 12.0 and (stroked or w <= 2.0):
                                xx = x0 if stroked else (bb[0] + bb[2]) / 2
                                v_rules.append(_Rule(xx, y0, y1))
            elif it[0] in ("c", "qu"):
                n_diag += 1
                if it[0] == "qu":
                    q = it[1]
                    pts = [to_display_point(page, float(p[0]), float(p[1]))
                           for p in q]
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    bb = (min(xs), min(ys), max(xs), max(ys))
                    if filled and bb[2] - bb[0] <= 14 and bb[3] - bb[1] <= 14:
                        facts.fills.append(bb)
        if filled and items and all(it[0] == "c" for it in items):
            # a drawn circle or ellipse: a filled symbol or marker
            xs, ys = [], []
            for it in items:
                for p in it[1:]:
                    dx, dy = to_display_point(page, p[0], p[1])
                    xs.append(dx)
                    ys.append(dy)
            bb = (min(xs), min(ys), max(xs), max(ys))
            if bb[2] - bb[0] <= 14 and bb[3] - bb[1] <= 14:
                facts.fills.append(bb)
        if n_diag >= 2 and len(pts_chain) >= 4:
            facts.curves.append(pts_chain)
    for r in join_segments(h_rules, max_gap=1.0):
        facts.lines.append(RasterLine("h", a=r.a, b=0.0, lo=r.lo, hi=r.hi,
                                      thickness_pt=0.5, coverage=1.0,
                                      source="vector"))
    for r in join_segments(v_rules, max_gap=1.0):
        facts.lines.append(RasterLine("v", a=r.a, b=0.0, lo=r.lo, hi=r.hi,
                                      thickness_pt=0.5, coverage=1.0,
                                      source="vector"))
    # Dotted and dashed rules drawn as many short strokes: join them with a
    # larger gap, keep the long regular runs.
    for orient, segs in (("h", [s for s in facts.segments
                                if abs(s[3] - s[1]) <= 0.35]),
                         ("v", [s for s in facts.segments
                                if abs(s[2] - s[0]) <= 0.35])):
        short = [(_Rule((s[1] + s[3]) / 2.0, s[0], s[2]) if orient == "h"
                  else _Rule((s[0] + s[2]) / 2.0, s[1], s[3]))
                 for s in segs if (s[2] - s[0] if orient == "h"
                                   else s[3] - s[1]) < 12.0]
        if len(short) < 4:
            continue
        joined = join_segments(short, max_gap=4.0)
        for r in joined:
            if r.length < 30.0:
                continue
            pieces = [s for s in short if abs(s.a - r.a) <= 2.0
                      and r.lo - 0.5 <= s.lo and s.hi <= r.hi + 0.5]
            cover = sum(s.length for s in pieces) / max(1e-9, r.length)
            if len(pieces) >= 4 and cover >= 0.2:
                facts.lines.append(RasterLine(orient, a=r.a, b=0.0, lo=r.lo,
                                              hi=r.hi, thickness_pt=0.5,
                                              coverage=min(1.0, cover),
                                              dashed=True,
                                              pieces=len(pieces),
                                              source="vector"))


def _text_labels(doc, index: int, facts: PageFacts) -> None:
    from planlens.document.model import SOURCE_CAD_HIDDEN
    try:
        content = doc.page(index, words=True, tables=False)
    except Exception:                                   # pragma: no cover
        return
    facts.text_source = (content.text_sources[0] if content.text_sources
                         else "pdf_text")
    for ln in content.lines:
        if ln.source == SOURCE_CAD_HIDDEN or not (ln.text or "").strip():
            continue
        rot = ln.rotation or 0.0
        if min(abs((rot % 360) - a) for a in (0, 360)) > 15:
            facts.texts.append(ln)
            continue
        facts.texts.append(ln)
        src = ln.source if ln.source in ("ocr", "azure_di") else "text"
        got = parse_label_value(ln.text)
        if got is not None:
            box = _ink_box(ln)
            facts.labels.append(Label(got[0], ln.text.strip(), box, got[1],
                                      source=src))
            continue
        toks = ln.text.split()
        if len(toks) >= 2 and ln.words and len(ln.words) == len(toks):
            parsed = [parse_label_value(w[0]) for w in ln.words]
            if all(p is not None for p in parsed):
                for (wt, wb), p in zip(ln.words, parsed):
                    facts.labels.append(Label(p[0], wt.strip(),
                                              _ink_box_of(wb, ln), p[1],
                                              source=src))


def _ink_box(ln) -> BBox:
    """A text line's box narrowed to the ink of its digits.

    A text layer's line box runs from the font's ascender to its descender
    (about 1.4 em for Helvetica) while digits stand on the baseline and reach
    0.7 em up; the centre of the BOX is not the centre of the ink. An
    optical reader's box already hugs the ink, so it is kept as given.
    """
    if ln.source in ("ocr", "azure_di"):
        return tuple(ln.bbox)
    return _ink_box_of(ln.bbox, ln)


def _ink_box_of(b: Sequence[float], ln) -> BBox:
    if ln.source in ("ocr", "azure_di"):
        return tuple(b)
    size = ln.size or ((b[3] - b[1]) / 1.374 if b[3] > b[1] else None)
    if not size:
        return tuple(b)
    # PyMuPDF's line box: ascender 1.075 em above the baseline, descender
    # 0.299 em below it (Helvetica / Arial metrics as PyMuPDF reports them).
    h = b[3] - b[1]
    asc = 1.075 / 1.374 * h
    base = b[1] + asc
    return (b[0], base - 0.703 * size, b[2], base)


def _snap_labels_to_ink(facts: PageFacts) -> None:
    """On a scan read optically, take each label's box from its own ink."""
    if facts.raster is None:
        return
    from planlens.document.raster import text_blobs
    mask = facts.mask_without_lines()
    for lab in facts.labels:
        if lab.source not in ("ocr", "azure_di"):
            continue
        b = lab.box
        pad = 1.5
        blobs = text_blobs(facts.raster, mask=mask,
                           region=(b[0] - pad, b[1] - pad, b[2] + pad,
                                   b[3] + pad), merge_pt=2.0, min_px=4)
        blobs = [bl for bl in blobs if bl.height >= 0.4 * lab.height]
        if not blobs:
            continue
        x0 = min(bl.bbox[0] for bl in blobs)
        y0 = min(bl.bbox[1] for bl in blobs)
        x1 = max(bl.bbox[2] for bl in blobs)
        y1 = max(bl.bbox[3] for bl in blobs)
        lab.box = (x0, y0, x1, y1)
        lab.positioned_by = "pixels"


def page_facts(doc, index: int, *, dpi: float = 200.0) -> PageFacts:
    """Text, paths and (on a picture page) pixels of one page, gathered once."""
    from planlens.document import raster as R
    fz = getattr(doc, "_doc", doc)
    page = fz[index]
    facts = PageFacts(index=index, width=float(page.rect.width),
                      height=float(page.rect.height))
    _vector_geometry(page, facts)
    if hasattr(doc, "page"):
        _text_labels(doc, index, facts)
    facts.is_raster = R.is_raster_page(page)
    if facts.is_raster:
        ras = R.render(page, dpi=dpi)
        lines, slope = R.find_lines(ras)
        facts.raster = ras
        facts.angle_deg = math.degrees(math.atan(slope))
        facts.lines = [L for L in facts.lines if L.source == "vector"] + lines
        _snap_labels_to_ink(facts)
    return facts


# ---------------------------------------------------------------------------
# Building one axis scale from labels and an anchor rule
# ---------------------------------------------------------------------------

@dataclass
class _Run:
    """Labels standing in one band, in order along the axis."""
    axis: str                         # "y" (a vertical band) or "x" (a row)
    positions: List[float]            # deskewed label centres, along the axis
    boxes: List[BBox]
    values: List[Optional[float]]
    texts: List[str]
    kinds: List[str]
    heights: List[float]
    across: float                     # band centre, across the axis
    band: Tuple[float, float]         # band extent across the axis
    source: str = "text"
    positioned_by: str = "text_box"
    labels: List[Label] = field(default_factory=list)

    @property
    def label_height(self) -> float:
        hs = sorted(self.heights)
        return hs[len(hs) // 2] if hs else 0.0


def _perp_lines(facts: PageFacts, axis: str) -> List[Any]:
    """Lines perpendicular to an axis (horizontal lines for a y axis)."""
    return facts.hlines() if axis == "y" else facts.vlines()


def _frames_for(facts: PageFacts, run: _Run, origin: float, step: float,
                n: int) -> Tuple[Optional[float], List[Tuple[float, int, float]],
                                 str]:
    """The offset the frame lines give a label run: ``(delta, frames, note)``.

    A frame line is a rule crossing the whole label band. Where the run's
    grid (label positions ``origin + k * step``) predicts it within a quarter
    step of a whole index, it marks a round value, and its distance from that
    index is the labels' offset from what they mark. Frames more than a step
    outside the run (a header's top line) are not the run's.
    """
    cands: List[Tuple[float, int, float]] = []       # (pos, k, delta)
    lo_b, hi_b = run.band
    width = max(1.0, hi_b - lo_b)
    for L in _perp_lines(facts, run.axis):
        pos, lo, hi = facts.line_span(L)
        if min(hi, hi_b) - max(lo, lo_b) < 0.8 * width:
            continue
        if L.dashed:
            continue
        kf = (pos - origin) / step
        k = int(round(kf))
        if k < -1 or k > n:
            continue
        delta = pos - (origin + k * step)
        if abs(delta) > 0.25 * step:
            continue
        cands.append((pos, k, delta))
    if not cands:
        return None, [], ""
    # One frame line found twice is one frame.
    cands.sort()
    merged: List[Tuple[float, int, float]] = []
    for c in cands:
        if merged and abs(c[0] - merged[-1][0]) <= 1.5 and c[1] == merged[-1][1]:
            continue
        merged.append(c)
    cands = merged
    tol = max(1.0, 0.03 * step)
    best: List[Tuple[float, int, float]] = []
    for c in cands:
        group = [d for d in cands if abs(d[2] - c[2]) <= tol]
        if len(group) > len(best) or (
                len(group) == len(best)
                and abs(sum(g[2] for g in group) / len(group))
                < abs(sum(g[2] for g in best) / len(best))):
            best = group
    if len(best) == 1 and len(cands) > 1:
        return None, cands, ("the frame lines disagree about where the "
                             "labels sit")
    delta = sum(g[2] for g in best) / len(best)
    return delta, best, ""


def _ticks_for(facts: PageFacts, run: _Run) -> List[Optional[float]]:
    """For each label, the position of a drawn tick beside it, or ``None``.

    A tick is a short rule perpendicular to the axis, standing just outside
    the label (never through it), within half a label height of its centre.
    """
    out: List[Optional[float]] = []
    lo_b, hi_b = run.band
    for pos, box, h in zip(run.positions, run.boxes, run.heights):
        # A label set on its baseline above its tick, or hanging below it,
        # stands most of its own height from the tick.
        reach = max(2.0, 0.8 * h + 1.0)
        best = None
        if run.axis == "y":
            cands = _tick_candidates_h(facts, box, lo_b - TICK_REACH,
                                       hi_b + TICK_REACH, pos, reach)
        else:
            cands = _tick_candidates_v(facts, box, lo_b - TICK_REACH,
                                       hi_b + TICK_REACH, pos, reach)
        for c in cands:
            if best is None or abs(c - pos) < abs(best - pos):
                best = c
        out.append(best)
    return out


def _tick_candidates_h(facts: PageFacts, box: BBox, x_lo: float, x_hi: float,
                       pos: float, reach: float) -> List[float]:
    cands = []
    for (x0, y0, x1, y1, _w) in facts.segments:
        if abs(y1 - y0) > 0.35:
            continue
        ln = x1 - x0
        if ln < 1.5 or ln > 40.0:
            continue
        if x1 < x_lo or x0 > x_hi:
            continue
        # not through the label itself
        if x0 < box[2] - 0.5 and x1 > box[0] + 0.5 and box[1] - 1 <= y0 <= box[3] + 1:
            continue
        s = facts.deskew((x0 + x1) / 2.0, y0)[1]
        if abs(s - pos) <= reach:
            cands.append(s)
    if facts.raster is not None:
        cands += _raster_ticks(facts, box, x_lo, x_hi, pos, reach, "h")
    return cands


def _tick_candidates_v(facts: PageFacts, box: BBox, y_lo: float, y_hi: float,
                       pos: float, reach: float) -> List[float]:
    cands = []
    for (x0, y0, x1, y1, _w) in facts.segments:
        if abs(x1 - x0) > 0.35:
            continue
        ln = y1 - y0
        if ln < 1.5 or ln > 40.0:
            continue
        if y1 < y_lo or y0 > y_hi:
            continue
        if y0 < box[3] - 0.5 and y1 > box[1] + 0.5 and box[0] - 1 <= x0 <= box[2] + 1:
            continue
        s = facts.deskew(x0, (y0 + y1) / 2.0)[0]
        if abs(s - pos) <= reach:
            cands.append(s)
    if facts.raster is not None:
        cands += _raster_ticks(facts, box, y_lo, y_hi, pos, reach, "v")
    return cands


def _raster_ticks(facts: PageFacts, box: BBox, lo: float, hi: float,
                  pos: float, reach: float, orient: str) -> List[float]:
    """Short thin ink marks beside a label on a scan (a tick, not a letter)."""
    from planlens.document.raster import components
    ras = facts.raster
    mask = facts.mask_without_lines()
    if orient == "h":
        cy = (box[1] + box[3]) / 2.0
        region = (lo, cy - reach - 3.0, hi, cy + reach + 3.0)
    else:
        cx = (box[0] + box[2]) / 2.0
        region = (cx - reach - 3.0, lo, cx + reach + 3.0, hi)
    out = []
    for bl in components(ras, mask=mask, region=region, min_px=3):
        w, h = bl.width, bl.height
        if orient == "h":
            if not (h <= 2.2 and 2.5 <= w <= 40.0 and w >= 2.5 * h):
                continue
            if bl.bbox[0] < box[2] - 0.3 and bl.bbox[2] > box[0] + 0.3:
                continue
            s = facts.deskew(bl.centroid[0], _line_centre(facts, bl, "h"))[1]
        else:
            if not (w <= 2.2 and 2.5 <= h <= 40.0 and h >= 2.5 * w):
                continue
            if bl.bbox[1] < box[3] - 0.3 and bl.bbox[3] > box[1] + 0.3:
                continue
            s = facts.deskew(_line_centre(facts, bl, "v"), bl.centroid[1])[0]
        if abs(s - pos) <= reach:
            out.append(s)
    return out


def _line_centre(facts: PageFacts, bl, orient: str) -> float:
    """The ink-weighted centre of a thin mark, across its length."""
    import numpy as np
    ras = facts.raster
    c0, r0, c1, r1 = ras.box_to_pixel(bl.bbox)
    sub = ras.inkw[max(0, r0 - 1):r1 + 1, max(0, c0 - 1):c1 + 1]
    if sub.size == 0:
        return bl.centroid[1] if orient == "h" else bl.centroid[0]
    sub = np.clip(sub - np.percentile(sub, 30), 0, None)
    if orient == "h":
        prof = sub.sum(axis=1)
        idx = np.arange(len(prof)) + max(0, r0 - 1) + 0.5
        return ras.y0 + float((prof * idx).sum() / max(prof.sum(), 1e-9)) / ras.z
    prof = sub.sum(axis=0)
    idx = np.arange(len(prof)) + max(0, c0 - 1) + 0.5
    return ras.x0 + float((prof * idx).sum() / max(prof.sum(), 1e-9)) / ras.z


def _anchor_rule(facts: PageFacts, run: _Run, centres: List[float],
                 values: List[Optional[float]], transform: str
                 ) -> Dict[str, Any]:
    """Tie each label to the drawing: ticks, else frames, else centres.

    Returns ``{"positions", "kind", "text", "anchor_pt", "frames"}`` where
    ``positions`` are the corrected anchor positions, one per label.
    """
    n = len(centres)
    ticks = _ticks_for(facts, run) if n else []
    have = [t for t in ticks if t is not None]
    if n and len(have) >= max(2, 0.6 * n):
        offs = sorted(c - t for c, t in zip(centres, ticks) if t is not None)
        med = offs[len(offs) // 2]
        mad = sorted(abs(o - med) for o in offs)[len(offs) // 2]
        # A tick that sits apart from where the other labels' ticks sit is
        # some other short rule (a frame end, the next tick): that label is
        # placed by the common offset instead.
        keep = max(0.6, 3.0 * mad)
        ticks = [t if t is not None and abs((c - t) - med) <= keep else None
                 for c, t in zip(centres, ticks)]
        have = [t for t in ticks if t is not None]
        if mad <= max(0.6, 0.25 * run.label_height) and                 len(have) >= max(2, 0.6 * n):
            pos = [t if t is not None else c - med
                   for c, t in zip(centres, ticks)]
            missing = sum(1 for t in ticks if t is None)
            return {"positions": pos, "kind": "ticks",
                    "text": (f"labels snapped to the drawn ticks beside them "
                             f"({len(have)} of {n}; label centres sit "
                             f"{med:+.1f} pt from their ticks)"),
                    "anchor_pt": (2.0 * mad + 0.2) if missing else 0.0,
                    "offset": med, "frames": []}
    # Frames: the label run's even grid, by positions alone.
    idx = index_by_spacing(centres) if n >= 2 else None
    if idx and idx["step"] > 0:
        origin, step = idx["origin"], idx["step"]
        on = [k for k in idx["index"] if k is not None]
        n_grid = (max(on) + 1) if on else n
        delta, frames, note = _frames_for(facts, run, origin, step, n_grid)
        if delta is not None:
            spread = (max(abs(f[2] - delta) for f in frames)
                      if len(frames) > 1 else 0.0)
            ends = ", ".join(f"{f[1] - (min(on) if on else 0)}" for f in frames)
            where = "above" if delta > 0 else "below"
            if run.axis == "x":
                where = "left of" if delta > 0 else "right of"
            pm_frames = math.sqrt(spread ** 2 + (0.3 + facts.pixel_pt) ** 2)
            return {"positions": [c + delta for c in centres],
                    "kind": "frames",
                    "text": (f"label centres sit {abs(delta):.1f} pt {where} "
                             f"what they mark (frame lines on the run's grid "
                             f"agree to {spread:.1f} pt)" if abs(delta) >= 0.5
                             else f"labels sit on what they mark (frame lines "
                                  f"agree to {max(spread, abs(delta)):.1f} pt)"),
                    "anchor_pt": pm_frames, "offset": delta,
                    "frames": frames}
        warn_note = note
    else:
        warn_note = ""
    # Unresolved: the label could be centred on what it marks, set on its
    # baseline just above it, or hanging just below it. The allowance covers
    # all three (half the label's height plus the gap a form leaves).
    return {"positions": list(centres), "kind": "centred_assumed",
            "text": ("label centres taken as the value they mark; nothing on "
                     "the page confirms it"
                     + (f" ({warn_note})" if warn_note else "")),
            "anchor_pt": 0.75 * run.label_height + 0.5, "offset": 0.0,
            "frames": []}


def _build_scale(facts: PageFacts, sid: str, run: _Run, transform: str,
                 quantity: str, unit: Optional[str], extent: BBox,
                 values: Optional[List[Optional[float]]] = None,
                 values_from: Optional[str] = None,
                 anchor: Optional[Dict[str, Any]] = None,
                 grid_positions: Optional[List[float]] = None,
                 note: str = "", require_even: Optional[bool] = None
                 ) -> Optional[Scale]:
    """Fit a run of labels (values known) into a :class:`Scale`.

    ``anchor`` is a precomputed anchor rule (gridlines); otherwise
    :func:`_anchor_rule` decides. ``None`` when the gates refuse the fit.
    """
    vals = list(values if values is not None else run.values)
    if anchor is None:
        anchor = _anchor_rule(facts, run, run.positions, vals, transform)
    if require_even is None:
        # Labels tied to the gridlines of an even grid are placed by the grid
        # itself: a label the reader missed leaves any gap, and the fit's
        # residual still catches a wrong value.
        require_even = anchor["kind"] not in ("gridlines",)
    positions = list(anchor["positions"])
    weights = None
    pts_pos = list(positions)
    pts_val = list(vals)
    # Frame lines at round values are anchors too (the head of a log at its
    # first depth, the foot at its last), once the labels' values are known.
    frame_anchors: List[Anchor] = []
    if anchor.get("frames") and transform == "linear":
        pre = fit_labels(positions, vals, transform)
        if pre is not None:
            for pos, _k, _d in anchor["frames"]:
                v = pre.a + pre.b * pos
                step_v = pre.step_value
                vr = round(v / step_v) * step_v if step_v else v
                # a frame that IS a label's place (the foot line under its
                # last label) adds nothing and would count against the run
                if any(abs(pp - pos) <= 1.0 and vv is not None
                       and abs(vv - vr) <= 1e-9
                       for pp, vv in zip(positions, vals)):
                    continue
                if abs(vr - v) <= 0.25 * step_v:
                    pts_pos.append(pos)
                    pts_val.append(vr)
                    frame_anchors.append(Anchor(
                        position=pos, value=vr, kind="frame_line",
                        source="predicted", positioned_by=(
                            "pixels" if facts.raster is not None else "vector"),
                        note="the value the label run predicts here"))
    fit = fit_labels(pts_pos, pts_val, transform, weights=weights,
                     require_even=require_even,
                     max_excluded=1 if values_from == "caller" else None)
    if fit is None:
        return None
    n_lab = len(positions)
    dropped = [i for i in fit.dropped if i < n_lab and vals[i] is not None]
    anchors: List[Anchor] = []
    for i in range(n_lab):
        if vals[i] is None or i in dropped:
            continue
        anchors.append(Anchor(
            position=positions[i], value=vals[i],
            kind="gridline" if anchor["kind"] == "gridlines" else "tick_label",
            source=values_from or run.source,
            positioned_by=run.positioned_by, box=run.boxes[i],
            label=run.texts[i] if i < len(run.texts) else None))
    anchors.extend(frame_anchors)
    pixel_pt = 0.5 * facts.pixel_pt if facts.raster is not None else 0.0
    floor = max(0.05, 0.25 * facts.pixel_pt) if facts.raster is not None \
        else 0.03
    rd = rms_dof(fit, floor=floor)
    vfrom = values_from or run.source
    positioned = ("vector" if run.positioned_by == "text_box"
                  and facts.raster is None else "pixels")
    resolved = anchor["kind"] != "centred_assumed"
    conf = confidence_for(positioned, vfrom, resolved, fit.max_res_pt,
                          fit.step_pt)
    warnings = []
    if not resolved:
        warnings.append("the labels' alignment could not be checked against "
                        "a tick or a frame line; most of a label's height is "
                        "in the uncertainty")
    if dropped:
        bad = ", ".join(run.texts[i] if i < len(run.texts) and run.texts[i]
                        else f"label {i + 1}" for i in dropped)
        vtxt = ", ".join(f"{vals[i]:g}" for i in dropped)
        warnings.append(f"one label broke the even run and was left out "
                        f"({bad}: {vtxt})")
    res_vals = [d for d in (run.texts or []) if d]
    reso = None
    for t in res_vals:
        r = printed_resolution(t)
        if r is not None:
            reso = r if reso is None else min(reso, r)
    prov = {"positions_from": ("pixels (scan" + (
        f", skew {facts.angle_deg:+.2f} deg corrected)"
        if abs(facts.angle_deg) >= 0.01 else ")")
        if positioned == "pixels" else "vector and text boxes"),
            "values_from": {"text": "the text layer", "ocr": "OCR text",
                            "azure_di": "Azure Document Intelligence text",
                            "caller": "values supplied for the label boxes",
                            "pattern": "the gridline pattern"}.get(vfrom,
                                                                   vfrom)}
    if note:
        prov["note"] = note
    if dropped:
        prov["dropped"] = [run.texts[i] if i < len(run.texts) else i
                           for i in dropped]
    return Scale(
        id=sid, page=facts.index, extent=extent, quantity=quantity,
        unit=unit, axis=run.axis, transform=transform, a=fit.a, b=fit.b,
        angle_deg=facts.angle_deg, anchors=anchors,
        residual_pt=fit.max_res_pt, rms_pt=fit.rms_pt,
        residual_value=fit.max_res_pt * abs(fit.b),
        anchor_rule=anchor["text"], anchor_rule_kind=anchor["kind"],
        anchor_pt=anchor.get("anchor_pt", 0.0), pixel_pt=pixel_pt,
        confidence=conf, provenance=prov, warnings=warnings,
        label_boxes=list(run.boxes), label_positions=list(run.positions),
        resolution=reso, n_fit=fit.n, s_mean=fit.s_mean, sss=fit.sss,
        rms_dof_pt=rd)


def _pending_scale(facts: PageFacts, sid: str, run: _Run, quantity: str,
                   unit: Optional[str], extent: BBox, transform: str,
                   note: str, anchor: Optional[Dict[str, Any]] = None
                   ) -> Scale:
    """A scale whose positions are known and whose label values are not."""
    sc = Scale(
        id=sid, page=facts.index, extent=extent, quantity=quantity,
        unit=unit, axis=run.axis, transform=transform,
        angle_deg=facts.angle_deg, confidence=0.0, needs_values=True,
        label_boxes=list(run.boxes), label_positions=list(run.positions),
        anchor_rule=(anchor or {}).get("text", ""),
        anchor_rule_kind=(anchor or {}).get("kind", "none"),
        provenance={"positions_from": "pixels (scan"
                    + (f", skew {facts.angle_deg:+.2f} deg corrected)"
                       if abs(facts.angle_deg) >= 0.01 else ")"),
                    "values_from": "not yet read",
                    "note": note},
        warnings=[f"{len(run.boxes)} label boxes need their values read "
                  f"(pass values=[...] in this order, None for one that "
                  f"cannot be read)"])
    sc.provenance["_run"] = run          # kept for the refit; not serialised
    if anchor is not None:
        sc.provenance["_anchor"] = anchor
    return sc


# ---------------------------------------------------------------------------
# Gridded frames: plots, profiles, chart families
# ---------------------------------------------------------------------------

@dataclass
class _Family:
    orient: str                       # h | v
    lines: List[Any]
    positions: List[float]            # deskewed, across the lines
    lo: float
    hi: float


def _families(facts: PageFacts, orient: str, min_len: float = 40.0,
              tol: float = 6.0) -> List[_Family]:
    """Parallel lines sharing their extents: the gridlines of one grid.

    On a scan a gridline arrives in pieces (a marker or a label touching it,
    a light stretch) and its ends wander by a few points, so collinear pieces
    are joined first and extents are compared with a tolerance that grows
    with the length.
    """
    raw = []
    for L in (facts.hlines() if orient == "h" else facts.vlines()):
        pos, lo, hi = facts.line_span(L)
        raw.append([pos, lo, hi, L])
    raw.sort(key=lambda t: t[0])
    # Pieces at one position (within 1.3 pt, single linkage) ...
    rows: List[List[List[Any]]] = []
    for it in raw:
        if rows and it[0] - rows[-1][-1][0] <= 1.3:
            rows[-1].append(it)
        else:
            rows.append([it])
    # ... joined along their run where they leave gaps of up to 15 pt. The
    # joined run sits where its LONGEST piece does: a marker's edge or a
    # letter's stroke touching a gridline must not move it.
    joined: List[List[Any]] = []
    for row in rows:
        row.sort(key=lambda t: t[1])
        cur: Optional[List[Any]] = None
        for it in row:
            if cur is not None and it[1] <= cur[2] + 15.0:
                if (it[2] - it[1]) > cur[4]:
                    cur[0], cur[3], cur[4] = it[0], it[3], it[2] - it[1]
                cur[2] = max(cur[2], it[2])
                continue
            if cur is not None:
                joined.append(cur)
            cur = list(it) + [it[2] - it[1]]
        if cur is not None:
            joined.append(cur)
    items = [tuple(j[:4]) for j in joined if j[2] - j[1] >= min_len]
    fams: List[List[Tuple[float, float, float, Any]]] = []
    for it in sorted(items, key=lambda t: -(t[2] - t[1])):
        for f in fams:
            ref = f[0]
            tl = max(tol, 0.03 * (ref[2] - ref[1]))
            same = abs(it[1] - ref[1]) <= tl and abs(it[2] - ref[2]) <= tl
            # A gridline seen only in part (a dotted line whose dots the scan
            # lost at one end, a line broken by a title) still stands on the
            # grid: it lies inside the grid's extent and runs most of it.
            part = (it[1] >= ref[1] - tl and it[2] <= ref[2] + tl
                    and (it[2] - it[1]) >= 0.6 * (ref[2] - ref[1]))
            if same or part:
                f.append(it)
                break
        else:
            fams.append([it])
    # A gridline that runs on past the grid (a major line drawn long, or one
    # run into its label) still belongs to the grid it crosses: a smaller
    # cluster whose lines CONTAIN a larger cluster's typical extent, by no
    # more than a label's width either side, joins it.
    fams.sort(key=len, reverse=True)
    merged = True
    while merged:
        merged = False
        for i, big in enumerate(fams):
            los = sorted(t[1] for t in big)
            his = sorted(t[2] for t in big)
            lo_t, hi_t = los[len(los) // 2], his[len(his) // 2]
            tl = max(tol, 0.03 * (hi_t - lo_t))
            for j in range(len(fams) - 1, i, -1):
                small = fams[j]
                if len(small) > len(big):
                    continue
                if all(t[1] <= lo_t + tl and t[2] >= hi_t - tl
                       and lo_t - t[1] <= 30.0 and t[2] - hi_t <= 30.0
                       for t in small):
                    big.extend(small)
                    del fams[j]
                    merged = True
            if merged:
                break
    out = []
    for f in fams:
        f.sort(key=lambda t: t[0])
        # one line seen twice (a thick rule) is one gridline
        dedup = []
        for it in f:
            if dedup and abs(it[0] - dedup[-1][0]) < 1.2:
                continue
            dedup.append(it)
        if len(dedup) < 2:
            continue
        # The family's extent is its members' TYPICAL extent: one gridline
        # run into a label at its end must not stretch the whole grid.
        los = sorted(d[1] for d in dedup)
        his = sorted(d[2] for d in dedup)
        out.append(_Family(orient, [d[3] for d in dedup],
                           [d[0] for d in dedup],
                           los[len(los) // 2], his[len(his) // 2]))
    return out


def _axis_model(positions: List[float]) -> Optional[Dict[str, Any]]:
    """Linear (even steps) or log10 (decade pattern) gridline positions.

    Even steps are tried first: a log grid's gaps differ six-fold inside a
    decade and never pass for even, while a loose log search can always find
    some decade that an even grid half fits.
    """
    if len(positions) >= 3:
        idx = index_by_spacing(positions)
        if idx is not None:
            on = [k for k in idx["index"] if k is not None]
            span = (max(on) - min(on) + 1) if on else 0
            if (len(on) >= 3 and len(on) >= 0.6 * span
                    and len(on) >= 0.75 * len(positions)
                    and idx["residual"] <= 0.08 * idx["step"] + 0.6):
                return {"transform": "linear", **idx}
    if len(positions) >= 10:
        dec = log_decades(positions)
        if dec is not None and dec["residual"] <= 0.012 * dec["decade"] + 0.6:
            mants = {round(q - math.floor(q + 1e-9), 3)
                     for q in dec["places"] if q is not None}
            if len(mants) >= 5:
                return {"transform": "log10", **dec}
    return None


def _sub_family(fam: _Family, lo: float, hi: float) -> Optional[_Family]:
    keep = [(p, L) for p, L in zip(fam.positions, fam.lines)
            if lo - 3.0 <= p <= hi + 3.0]
    if len(keep) < 3:
        return None
    return _Family(fam.orient, [k[1] for k in keep], [k[0] for k in keep],
                   fam.lo, fam.hi)


def _grid_frames(facts: PageFacts) -> List[Tuple[BBox, _Family, _Family]]:
    """Pairs of families that make one grid, with the grid's extent.

    The horizontals of a grid run between its outer verticals and the
    verticals between its outer horizontals. Two charts stacked on a page
    share their x extent, so a family of horizontals is first cut to the
    span of the verticals it is paired with.
    """
    hf = [f for f in _families(facts, "h") if len(f.positions) >= 3]
    vf = [f for f in _families(facts, "v") if len(f.positions) >= 3]
    out = []
    used = set()
    for v in vf:
        best = None
        for i, h0 in enumerate(hf):
            tx = max(6.0, 0.03 * (h0.hi - h0.lo))
            if abs(h0.lo - v.positions[0]) > tx                     or abs(h0.hi - v.positions[-1]) > tx:
                continue
            h = _sub_family(h0, v.lo, v.hi)
            if h is None:
                continue
            ty = max(6.0, 0.03 * (v.hi - v.lo))
            if abs(v.lo - h.positions[0]) <= ty                     and abs(v.hi - h.positions[-1]) <= ty:
                key = (i, round(h.positions[0], 1))
                if key in used:
                    continue
                best = (key, h)
                break
        if best is None:
            continue
        used.add(best[0])
        h = best[1]
        # extent in the displayed frame (corners turned back)
        from planlens.document.scales import unrotate_point
        corners = [unrotate_point(x, y, facts.angle_deg)
                   for x in (v.positions[0], v.positions[-1])
                   for y in (h.positions[0], h.positions[-1])]
        xs = [c[0] for c in corners]
        ys = [c[1] for c in corners]
        out.append(((min(xs), min(ys), max(xs), max(ys)), h, v))
    return out


def _labels_along(facts: PageFacts, axis: str, grid_pos: List[float],
                  frame_lo: float, frame_hi: float, other_lo: float,
                  other_hi: float, step: float) -> List[Tuple[Label, float]]:
    """Printed labels standing beside an axis, each with the gridline it marks.

    For an x axis: below (or above) the frame, centred on a vertical
    gridline; for a y axis: left (or right) of the frame, centred on a
    horizontal one.
    """
    out = []
    tol = max(ALIGN_PT, ALIGN_STEP * step)
    for lab in facts.labels:
        if lab.used or lab.value is None:
            continue
        cx, cy = facts.deskew(*lab.centre)
        a = facts.deskew(lab.box[0], lab.box[1])
        b = facts.deskew(lab.box[2], lab.box[3])
        lo_x, hi_x = min(a[0], b[0]), max(a[0], b[0])
        lo_y, hi_y = min(a[1], b[1]), max(a[1], b[1])
        # A label marks an axis from OUTSIDE the frame: wholly below or above
        # it for x, wholly left or right of it for y. One standing level
        # with a corner belongs to the other axis.
        if axis == "x":
            along = cx
            first = lo_y >= other_hi - 1.5 and lo_y <= other_hi + 30.0
            second = hi_y <= other_lo + 1.5 and hi_y >= other_lo - 30.0
        else:
            along = cy
            first = hi_x <= other_lo + 1.5 and hi_x >= other_lo - 60.0
            second = lo_x >= other_hi - 1.5 and lo_x <= other_hi + 60.0
        if not (first or second):
            continue
        g = min(grid_pos, key=lambda p: abs(p - along))
        if abs(g - along) <= tol:
            out.append((lab, g, 0 if first else 1))
    # An axis is labelled along ONE side (below an x axis, left of a y
    # axis, or the other side); a stray label by the far side (the other
    # axis's end label beside a corner) is not part of the run.
    sides = [sum(1 for t in out if t[2] == k) for k in (0, 1)]
    keep = 0 if sides[0] >= sides[1] else 1
    return [(lab, g) for lab, g, k in out if k == keep]


def _blob_labels_along(facts: PageFacts, axis: str, grid_pos: List[float],
                       majors: List[float], other_lo: float, other_hi: float,
                       step: float) -> List[Tuple[Any, float, BBox]]:
    """On a scan, ink blobs of label size beside an axis, centred on lines."""
    from planlens.document.raster import text_blobs
    from planlens.document.scales import unrotate_point
    if facts.raster is None:
        return []
    mask = facts.mask_without_lines()
    lo_g, hi_g = min(grid_pos), max(grid_pos)
    pad = max(8.0, 0.6 * step)
    out = []
    tol = max(ALIGN_PT, ALIGN_STEP * step)
    for side in ((other_hi + 1.0, other_hi + 26.0),
                 (other_lo - 26.0, other_lo - 1.0)) if axis == "x" else (
            (other_lo - 60.0, other_lo - 1.0),
            (other_hi + 1.0, other_hi + 60.0)):
        if axis == "x":
            pts = [unrotate_point(u, v, facts.angle_deg)
                   for u in (lo_g - pad, hi_g + pad) for v in side]
        else:
            pts = [unrotate_point(u, v, facts.angle_deg)
                   for u in side for v in (lo_g - pad, hi_g + pad)]
        region = (min(p[0] for p in pts), min(p[1] for p in pts),
                  max(p[0] for p in pts), max(p[1] for p in pts))
        blobs = text_blobs(facts.raster, mask=mask, region=region,
                           merge_pt=2.6, min_px=6)
        found = []
        best_d: Dict[float, float] = {}
        for box in _assemble_labels(blobs, axis):
            h = box[3] - box[1]
            if not (LABEL_MIN_H <= h <= LABEL_MAX_H)                     or box[2] - box[0] > LABEL_MAX_W:
                continue
            c = facts.deskew((box[0] + box[2]) / 2.0, (box[1] + box[3]) / 2.0)
            along = c[0] if axis == "x" else c[1]
            pool = majors if majors else grid_pos
            g = min(pool, key=lambda p: abs(p - along))
            d = abs(g - along)
            if d > tol:
                continue
            if g in best_d and best_d[g] <= d:
                continue
            best_d[g] = d
            found = [f for f in found if f[1] != g]

            class _B:
                pass
            pseudo = _B()
            pseudo.bbox = box
            pseudo.height = h
            pseudo.width = box[2] - box[0]
            found.append((pseudo, g, box))
        if len(found) >= 2:
            out.extend(found)
            break
    return out


def _assemble_labels(blobs: Sequence[Any], axis: str) -> List[BBox]:
    """Ink pieces put back together into printed labels.

    A label can arrive as several pieces ("0", ".", "01"); a gridline's tail
    past the frame arrives as a sliver. Slivers are dropped, and pieces that
    share a row and stand a character's gap apart are one label.
    """
    parts = []
    for bl in blobs:
        w, h = bl.width, bl.height
        if w < 0.9 and h < 0.9:
            continue
        if axis == "x" and w < 0.9 and h > 2.0:
            continue                        # a vertical gridline's tail
        if axis == "y" and h < 0.9 and w > 2.0:
            continue                        # a horizontal gridline's tail
        parts.append(list(bl.bbox))
    parts.sort(key=lambda b: ((b[1] + b[3]) / 2.0, b[0]))
    labels: List[List[float]] = []
    for b in sorted(parts, key=lambda b: b[0]):
        hb = b[3] - b[1]
        for L in labels:
            hl = L[3] - L[1]
            ov = min(b[3], L[3]) - max(b[1], L[1])
            gap = b[0] - L[2]
            if ov >= 0.5 * min(hb, hl) and -1.0 <= gap <= max(2.5, 0.45 * max(hb, hl)):
                L[0] = min(L[0], b[0])
                L[1] = min(L[1], b[1])
                L[2] = max(L[2], b[2])
                L[3] = max(L[3], b[3])
                break
        else:
            labels.append(list(b))
    return [tuple(L) for L in labels]


def _axis_quantity(facts: PageFacts, axis: str, extent: BBox,
                   kinds: Sequence[str]) -> Tuple[str, Optional[str]]:
    """An axis' quantity and printed unit, from its title where there is one."""
    if "station" in kinds:
        return "station", None
    if "easting" in kinds:
        return "easting", None
    if "northing" in kinds:
        return "northing", None
    x0, y0, x1, y1 = extent
    best = None
    for ln in facts.texts:
        t = (ln.text or "").strip()
        if not t or parse_label_value(t) is not None or len(t) > 60:
            continue
        b = ln.bbox
        cx, cy = (b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0
        if axis == "x":
            if not (x0 <= cx <= x1 and y1 < cy <= y1 + 45.0):
                continue
            d = cy - y1
        else:
            rot = (ln.rotation or 0.0) % 360
            if not (y0 <= cy <= y1 and x0 - 70.0 <= cx < x0):
                continue
            if not (60 <= rot <= 120 or 240 <= rot <= 300):
                continue
            d = x0 - cx
        if best is None or d < best[0]:
            best = (d, t)
    if best is None:
        return axis, None
    title = best[1]
    unit = None
    m = re.search(r"\(([^)]{1,12})\)", title)
    if m:
        unit = m.group(1).strip()
        title = title[:m.start()].strip()
    name = " ".join(title.lower().split())
    return (name or axis), unit


def _grid_axis(facts: PageFacts, values: Dict[str, Any], fid: str,
               axis: str, positions: List[float], model: Dict[str, Any],
               o_lo: float, o_hi: float, extent: BBox, note: str,
               kinds_seen: List[str], what: str = "gridlines"
               ) -> Optional[Scale]:
    """One axis of a grid: its labels found and fitted (or left pending)."""
    transform = model["transform"]
    if transform == "log10":
        grid_pos = [p for p, q in zip(model["positions"], model["places"])
                    if q is not None]
        majors = model["majors"]
        step = model["decade"] / 9.0
    else:
        grid_pos = [p for p, k in zip(positions, model["index"])
                    if k is not None]
        majors = grid_pos
        step = model["step"]
    texts = _labels_along(facts, axis, grid_pos, 0, 0, o_lo, o_hi, step)
    run: Optional[_Run] = None
    if len(texts) >= 2:
        texts.sort(key=lambda t: t[1])
        labs = [t[0] for t in texts]
        kinds_seen += [lb.kind for lb in labs]
        run = _Run(axis=axis, positions=[t[1] for t in texts],
                   boxes=[lb.box for lb in labs],
                   values=[lb.value for lb in labs],
                   texts=[lb.text for lb in labs],
                   kinds=[lb.kind for lb in labs],
                   heights=[lb.height for lb in labs],
                   across=(o_lo + o_hi) / 2.0, band=(o_lo, o_hi),
                   source=labs[0].source,
                   positioned_by=labs[0].positioned_by, labels=labs)
    elif facts.raster is not None:
        blobs = _blob_labels_along(facts, axis, grid_pos, majors, o_lo, o_hi,
                                   step)
        if len(blobs) >= 2:
            blobs.sort(key=lambda t: t[1])
            run = _Run(axis=axis, positions=[t[1] for t in blobs],
                       boxes=[t[2] for t in blobs],
                       values=[None] * len(blobs), texts=[],
                       kinds=["number"] * len(blobs),
                       heights=[t[0].height for t in blobs],
                       across=(o_lo + o_hi) / 2.0, band=(o_lo, o_hi),
                       source="pixels", positioned_by="pixels")
    if run is None:
        return None
    run = _labels_in_line(facts, run, axis)
    if run is None:
        return None
    sid = f"{fid}.{axis}"
    anchor = {"positions": list(run.positions), "kind": "gridlines",
              "text": (f"labels aligned with the {what} they mark"),
              "anchor_pt": 0.0, "frames": []}
    quantity, unit = _axis_quantity(facts, axis, extent, run.kinds)
    supplied = _values_for(values, sid, run)
    if run.values and all(v is not None for v in run.values) \
            and run.source != "pixels":
        sc = _build_scale(facts, sid, run, transform, quantity, unit, extent,
                          anchor=anchor, note=note)
        if sc is None and transform == "linear":
            # A log axis drawn with its decades only is an even grid; its
            # labels say it is a log axis.
            sc = _build_scale(facts, sid, run, "log10", quantity, unit,
                              extent, anchor=anchor, note="decade gridlines")
    elif supplied is not None:
        sc = _build_scale(facts, sid, run, transform, quantity, unit, extent,
                          values=supplied, values_from="caller",
                          anchor=anchor, note=note)
        if sc is None and transform == "linear" and all(
                v is None or v > 0 for v in supplied):
            sc = _build_scale(facts, sid, run, "log10", quantity, unit,
                              extent, values=supplied, values_from="caller",
                              anchor=anchor, note="decade gridlines")
        if sc is None:
            sc = _pending_scale(facts, sid, run, quantity, unit, extent,
                                transform, "the values given do not make an "
                                           "even run on these gridlines",
                                anchor)
            sc.warnings.append("the values supplied were refused: they do "
                               "not rise or fall evenly along the gridlines")
    else:
        sc = _pending_scale(facts, sid, run, quantity, unit, extent,
                            transform, note, anchor)
    if sc is not None:
        for lb in run.labels:
            lb.used = True
    return sc


def _on_model(positions: List[float], model: Dict[str, Any]) -> List[float]:
    """The positions an axis model placed (lines off the pattern dropped)."""
    keys = model.get("places") if model.get("transform") == "log10"         else model.get("index")
    if not keys:
        return sorted(positions)
    on = sorted(p for p, k in zip(positions, keys) if k is not None)
    return on or sorted(positions)


def _labels_in_line(facts: PageFacts, run: _Run, axis: str
                    ) -> Optional[_Run]:
    """An axis's labels, standing in one row (x) or one column (y), in one
    size; or ``None`` when they do not.

    Labels read off pixels must be at least three: two blobs beside two
    lines are a coincidence on any ruled form. Words scattered round a
    ruled box (a legend, a logo) neither line up nor match in size. One
    blob in five may stand out of line (a sliver of an axis title beside a
    label): it is left out of the run, never the run for it.
    """
    n = len(run.boxes)
    if run.source == "pixels" and n < 3:
        return None
    boxes = []
    for b in run.boxes:
        x0, y0 = facts.deskew(b[0], b[1])
        x1, y1 = facts.deskew(b[2], b[3])
        boxes.append((min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)))
    hs = sorted(b[3] - b[1] for b in boxes)
    h_med = hs[len(hs) // 2]
    tol = max(3.0, 0.5 * h_med)
    if axis == "x":
        keys = [lambda b: (b[1] + b[3]) / 2.0]
    else:
        keys = [lambda b: b[0], lambda b: (b[0] + b[2]) / 2.0,
                lambda b: b[2]]
    best: List[int] = []
    for f in keys:
        v = [f(b) for b in boxes]
        med = sorted(v)[len(v) // 2]
        members = [i for i, x in enumerate(v) if abs(x - med) <= tol / 2.0
                   + 1e-9]
        # widen to the whole band round the members' own middle
        if members:
            mid = sorted(v[i] for i in members)[len(members) // 2]
            members = [i for i, x in enumerate(v) if abs(x - mid) <= tol]
        if len(members) > len(best):
            best = members
    need = max(3 if run.source == "pixels" else 2, math.ceil(0.8 * n))
    if len(best) < need:
        return None
    if run.source == "pixels":
        # one size of print: most labels within a band round the median (a
        # label run into an axis title is allowed for, a mixture is not)
        hk = sorted(boxes[i][3] - boxes[i][1] for i in best)
        hm = hk[len(hk) // 2]
        same = sum(0.7 * hm <= h <= 1.45 * hm for h in hk)
        if same < 0.8 * len(hk):
            return None
    if len(best) == n:
        return run
    keep = sorted(best)
    return _Run(axis=run.axis,
                positions=[run.positions[i] for i in keep],
                boxes=[run.boxes[i] for i in keep],
                values=[run.values[i] for i in keep],
                texts=[run.texts[i] for i in keep] if run.texts else [],
                kinds=[run.kinds[i] for i in keep] if run.kinds else [],
                heights=[run.heights[i] for i in keep],
                across=run.across, band=run.band, source=run.source,
                positioned_by=run.positioned_by,
                labels=[run.labels[i] for i in keep] if run.labels else [])


def _next_plot_id(facts: PageFacts, frames: List[Frame]) -> str:
    n = len([f for f in frames if f.kind in ("plot", "profile")]) + 1
    return f"p{facts.index}.plot{n}"


def _gridded(facts: PageFacts, values: Dict[str, Any], frames: List[Frame],
             counter: Dict[str, int]) -> None:
    for extent, hfam, vfam in _grid_frames(facts):
        scales: Dict[str, Scale] = {}
        kinds_seen: List[str] = []
        fid = _next_plot_id(facts, frames)
        # A chart's grid is regular BOTH ways; a ruled form (a log sheet, a
        # table) is regular one way at most, and three rules evenly spaced
        # are a coincidence.
        models = {id(fam): (_axis_model(fam.positions)
                            if len(fam.positions) >= 4 else None)
                  for fam in (hfam, vfam)}
        if any(m is None for m in models.values()):
            continue
        # The grid's own lines bound it: a frame drawn round the chart (and
        # taken into the family) is not where the axis labels stand.
        on = {id(fam): _on_model(fam.positions, models[id(fam)])
              for fam in (hfam, vfam)}
        # ... and the frame is the grid's: a box drawn round the chart is
        # not the chart's extent either
        from planlens.document.scales import unrotate_point as _unrot
        ys_on, xs_on = on[id(hfam)], on[id(vfam)]
        corners = [_unrot(x, y, facts.angle_deg)
                   for x in (xs_on[0], xs_on[-1])
                   for y in (ys_on[0], ys_on[-1])]
        extent = (min(c[0] for c in corners), min(c[1] for c in corners),
                  max(c[0] for c in corners), max(c[1] for c in corners))
        for axis, fam, other in (("x", vfam, hfam), ("y", hfam, vfam)):
            model = models[id(fam)]
            if model is None:
                continue
            o_pos = on[id(other)]
            sc = _grid_axis(facts, values, fid, axis, fam.positions, model,
                            o_pos[0], o_pos[-1], extent,
                            _grid_note(model, fam), kinds_seen)
            if sc is not None:
                scales[axis] = sc
        if scales:
            kind = "profile" if "station" in kinds_seen else "plot"
            frames.append(Frame(id=fid, page=facts.index, kind=kind,
                                extent=extent, scales=scales,
                                provenance={"grid": f"{len(hfam.positions)} x "
                                                    f"{len(vfam.positions)} "
                                                    f"lines"}))


def _collinear(spans: List[Tuple[float, float, float]], gap: float = 8.0,
               tol: float = 1.2) -> List[Tuple[float, float, float]]:
    """Pieces of one rule put back together.

    A scanned rule comes in pieces where a marker, a label or a faint patch
    breaks it, and the pieces may overlap; on one position (within ``tol``)
    pieces closer than ``gap`` are one rule.
    """
    out: List[Tuple[float, float, float]] = []
    groups: List[List[Tuple[float, float, float]]] = []
    for sp in sorted(spans):
        if groups and abs(sp[0] - groups[-1][-1][0]) <= tol:
            groups[-1].append(sp)
        else:
            groups.append([sp])
    for g in groups:
        g.sort(key=lambda t: t[1])
        cur = list(g[0])
        wsum = (cur[2] - cur[1]) * cur[0]
        wlen = cur[2] - cur[1]
        for p, lo, hi in g[1:]:
            if lo <= cur[2] + gap:
                cur[2] = max(cur[2], hi)
                wsum += (hi - lo) * p
                wlen += hi - lo
            else:
                out.append((wsum / max(wlen, 1e-9), cur[1], cur[2]))
                cur = [p, lo, hi]
                wsum, wlen = (hi - lo) * p, hi - lo
        out.append((wsum / max(wlen, 1e-9), cur[1], cur[2]))
    return out


def _side_cover(spans: List[Tuple[float, float, float]], pos: float,
                a: float, b: float, tol: float = 2.0) -> float:
    """How much of ``[a, b]`` the pieces on ``pos`` cover, as a fraction."""
    iv = sorted((max(lo, a), min(hi, b)) for p, lo, hi in spans
                if abs(p - pos) <= tol and hi > a and lo < b)
    total = 0.0
    cur: Optional[List[float]] = None
    for lo, hi in iv:
        if cur is None or lo > cur[1]:
            if cur is not None:
                total += cur[1] - cur[0]
            cur = [lo, hi]
        else:
            cur[1] = max(cur[1], hi)
    if cur is not None:
        total += cur[1] - cur[0]
    return total / max(1e-9, b - a)


def _rectangles(facts: PageFacts, min_side: float = 60.0
                ) -> List[Tuple[float, float, float, float]]:
    """Closed frames drawn with four solid rules: ``(left, top, right,
    bottom)`` in the deskewed frame, largest first.

    Each side may be drawn in pieces (a scan breaks rules where markers or
    labels touch them): a side is there when its pieces cover most of it.
    """
    hs = _collinear([facts.line_span(L) for L in facts.hlines()
                     if not L.dashed])
    vs = [facts.line_span(L) for L in facts.vlines() if not L.dashed]
    vpos = sorted({round(p, 1) for p, lo, hi in vs if hi - lo >= 8.0})
    out = []
    for (hp, hlo, hhi) in hs:
        if hhi - hlo < min_side:
            continue
        for (hp2, hlo2, hhi2) in hs:
            if hp2 <= hp + min_side or abs(hlo2 - hlo) > 4                     or abs(hhi2 - hhi) > 4:
                continue
            sides = []
            for end in (hlo, hhi):
                cands = [(round(_side_cover(vs, x, hp, hp2), 3), -abs(x - end),
                          x) for x in vpos if abs(x - end) <= 4]
                cands = [c for c in cands if c[0] >= 0.75]
                if not cands:
                    break
                sides.append(max(cands)[2])
            if len(sides) < 2:
                continue
            # The frame's sides are where its vertical rules ARE, not where
            # the horizontal rules happen to stop.
            rect = (sides[0], hp, sides[1], hp2)
            if not any(abs(r[0] - rect[0]) < 3 and abs(r[1] - rect[1]) < 3
                       and abs(r[2] - rect[2]) < 3
                       and abs(r[3] - rect[3]) < 3 for r in out):
                out.append(rect)
    out.sort(key=lambda r: -(r[2] - r[0]) * (r[3] - r[1]))
    return out


def _projected_lines(facts: PageFacts, rect, axis: str) -> List[float]:
    """Gridline positions inside a frame from the ink projected across it.

    A faint or dotted gridline that the line finder sees only in pieces still
    adds up over the whole width of the plot: the rows (or columns) where a
    fifth or more of the interior is ink are gridlines.
    """
    import numpy as np
    from planlens.document.raster import _shear_rows
    ras = facts.raster
    left, top, right, bottom = rect
    slope = math.tan(math.radians(facts.angle_deg))
    pts = [unrotate_point(u, v, facts.angle_deg)
           for u in (left, right) for v in (top, bottom)]
    box = (min(p[0] for p in pts), min(p[1] for p in pts),
           max(p[0] for p in pts), max(p[1] for p in pts))
    c0, r0, c1, r1 = ras.box_to_pixel(box)
    sub = ras.line_mask[r0:r1, c0:c1]
    if sub.size == 0:
        return []
    if axis == "y":
        sh, _ = _shear_rows(sub, slope)
        inset = int(3 * ras.z)
        prof = sh[:, inset:-inset or None].mean(axis=1)
        base_pt = ras.y0 + r0 / ras.z
        u_mid = (left + right) / 2.0
    else:
        sh, _ = _shear_rows(np.ascontiguousarray(sub.T), -slope)
        inset = int(3 * ras.z)
        prof = sh[:, inset:-inset or None].mean(axis=1)
        base_pt = ras.x0 + c0 / ras.z
    peaks = []
    n = len(prof)
    i = 0
    while i < n:
        if prof[i] >= 0.2:
            j = i
            while j < n and prof[j] >= 0.2:
                j += 1
            seg = prof[i:j]
            c = float((seg * (np.arange(i, j) + 0.5)).sum() / seg.sum())
            if j - i <= 2.0 * ras.z:          # a gridline is thin; text is not
                peaks.append(c)
            i = j
        else:
            i += 1
    out = []
    for c in peaks:
        # sheared index -> page coordinate at the frame's middle -> deskewed
        if axis == "y":
            mid_x = (box[0] + box[2]) / 2.0
            y = base_pt + c / ras.z
            out.append(facts.deskew(mid_x, y)[1])
        else:
            mid_y = (box[1] + box[3]) / 2.0
            x = base_pt + c / ras.z
            out.append(facts.deskew(x, mid_y)[0])
    return sorted(out)


def _projected_grids(facts: PageFacts, values: Dict[str, Any],
                     frames: List[Frame]) -> None:
    """Plots whose gridlines the line finder saw only in part (dotted, faint)."""
    if facts.raster is None:
        return
    taken = [f.extent for f in frames]
    for rect in _rectangles(facts):
        pts = [unrotate_point(u, v, facts.angle_deg)
               for u in (rect[0], rect[2]) for v in (rect[1], rect[3])]
        ext = (min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))
        if any(_overlap_frac(t, ext) > 0.5 for t in taken):
            continue
        ys = _projected_lines(facts, rect, "y")
        xs = _projected_lines(facts, rect, "x")
        # the frame's own edges are gridlines of the grid too
        ys = _with_edges(ys, rect[1], rect[3])
        xs = _with_edges(xs, rect[0], rect[2])
        # A plot's grid runs both ways: a ruled form's boxes (a log's
        # columns between two stratum lines) do not make a plot.
        my = _axis_model(ys) if len(ys) >= 5 else None
        mx = _axis_model(xs) if len(xs) >= 5 else None
        if my is None or mx is None:
            continue
        fid = _next_plot_id(facts, frames)
        scales: Dict[str, Scale] = {}
        kinds_seen: List[str] = []
        for axis, pos, model, lo, hi in (("x", xs, mx, rect[1], rect[3]),
                                         ("y", ys, my, rect[0], rect[2])):
            if model is None:
                continue
            note = (f"{len(pos)} gridlines found by projecting the ink across "
                    f"the frame")
            sc = _grid_axis(facts, values, fid, axis, pos, model, lo, hi, ext,
                            note, kinds_seen)
            if sc is not None:
                scales[axis] = sc
        if scales:
            frames.append(Frame(id=fid, page=facts.index, kind="plot",
                                extent=ext, scales=scales,
                                provenance={"grid": "projected"}))
            taken.append(ext)


def _with_edges(pos: List[float], lo: float, hi: float) -> List[float]:
    out = [p for p in pos if lo + 1.0 < p < hi - 1.0]
    return [lo] + out + [hi]


def _overlap_frac(a: BBox, b: BBox) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    ab = max(1e-9, (b[2] - b[0]) * (b[3] - b[1]))
    return ix * iy / ab


def _grid_note(model: Dict[str, Any], fam: _Family) -> str:
    if model["transform"] == "log10":
        return (f"log axis: {len(fam.positions)} gridlines in the pattern of "
                f"a log decade (pattern error {model['error']:.3f} of a "
                f"decade, decade {model['decade']:.1f} pt)")
    off = len(model.get("off_grid", []))
    return (f"{len(fam.positions)} gridlines {model['step']:.1f} pt apart"
            + (f" ({off} off the grid, left out)" if off else ""))


def _values_for(values: Dict[str, Any], sid: str, run: _Run
                ) -> Optional[List[Optional[float]]]:
    v = values.get(sid)
    if v is None:
        return None
    vals = [None if x is None else float(x) for x in v]
    if len(vals) != len(run.boxes):
        return None
    return vals


# ---------------------------------------------------------------------------
# Ticks-only plots: a frame and the ticks along its edges
# ---------------------------------------------------------------------------

def _tick_plots(facts: PageFacts, values: Dict[str, Any],
                frames: List[Frame]) -> None:
    """A plot drawn with ticks only: a closed frame, labelled ticks on it."""
    taken = [f.extent for f in frames]
    for rect in _rectangles(facts, min_side=60.0):
        pts = [unrotate_point(u, v, facts.angle_deg)
               for u in (rect[0], rect[2]) for v in (rect[1], rect[3])]
        ext = (min(p[0] for p in pts), min(p[1] for p in pts),
               max(p[0] for p in pts), max(p[1] for p in pts))
        if any(_overlap_frac(t, ext) > 0.5 for t in taken):
            continue
        fid = _next_plot_id(facts, frames)
        scales: Dict[str, Scale] = {}
        kinds_seen: List[str] = []
        # A plot drawn with ticks is ticked along BOTH axes; a ruled box
        # with ticks down one side (a log's depth column) is not a plot.
        tick_sets = {axis: _frame_ticks(facts, axis, rect)
                     for axis in ("x", "y")}
        models = {axis: (_axis_model(t) if len(t) >= 4 else None)
                  for axis, t in tick_sets.items()}
        if any(m is None or m["transform"] != "linear"
               for m in models.values()):
            continue
        for axis in ("x", "y"):
            ticks = tick_sets[axis]
            model = models[axis]
            lo, hi = (rect[1], rect[3]) if axis == "x" else (rect[0], rect[2])
            sc = _grid_axis(facts, values, fid, axis, ticks, model, lo, hi,
                            ext, f"{len(ticks)} ticks on the frame",
                            kinds_seen, what="ticks")
            if sc is not None:
                scales[axis] = sc
        if scales:
            frames.append(Frame(id=fid, page=facts.index, kind="plot",
                                extent=ext, scales=scales,
                                provenance={"grid": "ticks on the frame"}))
            taken.append(ext)


def _contains(a: BBox, b: BBox, tol: float = 4.0) -> bool:
    return (abs(a[0] - b[0]) <= tol and abs(a[1] - b[1]) <= tol
            and abs(a[2] - b[2]) <= tol and abs(a[3] - b[3]) <= tol)


def _frame_ticks(facts: PageFacts, axis: str, frame: BBox) -> List[float]:
    """Positions of the short ticks standing on a frame's bottom / left edge."""
    hlo, hp, hhi, hp2 = frame
    out: List[float] = []
    if axis == "x":
        for (x0, y0, x1, y1, _w) in facts.segments:
            if abs(x1 - x0) > 0.35 or not (1.5 <= y1 - y0 <= 12.0):
                continue
            s = facts.deskew(x0, (y0 + y1) / 2.0)
            if hlo + 0.5 < s[0] < hhi - 0.5 and (abs(s[1] - hp2) <= 8.0
                                                  or abs(s[1] - hp) <= 8.0):
                out.append(s[0])
    else:
        for (x0, y0, x1, y1, _w) in facts.segments:
            if abs(y1 - y0) > 0.35 or not (1.5 <= x1 - x0 <= 12.0):
                continue
            s = facts.deskew((x0 + x1) / 2.0, y0)
            if hp + 0.5 < s[1] < hp2 - 0.5 and (abs(s[0] - hlo) <= 8.0
                                                 or abs(s[0] - hhi) <= 8.0):
                out.append(s[1])
    if facts.raster is not None:
        out += _raster_frame_ticks(facts, axis, frame)
    out = sorted(out)
    dedup: List[float] = []
    for p in out:
        if dedup and abs(p - dedup[-1]) < 1.0:
            continue
        dedup.append(p)
    # the frame's own corners are ticks too
    ends = [hlo, hhi] if axis == "x" else [hp, hp2]
    return sorted(set(round(p, 3) for p in dedup + ends))


def _raster_frame_ticks(facts: PageFacts, axis: str, frame: BBox
                        ) -> List[float]:
    from planlens.document.raster import components
    from planlens.document.scales import unrotate_point
    hlo, hp, hhi, hp2 = frame
    out = []
    mask = facts.mask_without_lines()
    if axis == "x":
        bands = [((hlo, hp2 - 8.0), (hhi, hp2 + 1.0)),
                 ((hlo, hp - 1.0), (hhi, hp + 8.0))]
    else:
        bands = [((hlo - 1.0, hp), (hlo + 8.0, hp2)),
                 ((hhi - 8.0, hp), (hhi + 1.0, hp2))]
    for (u0, v0), (u1, v1) in bands:
        pts = [unrotate_point(u, v, facts.angle_deg)
               for u in (u0, u1) for v in (v0, v1)]
        region = (min(p[0] for p in pts), min(p[1] for p in pts),
                  max(p[0] for p in pts), max(p[1] for p in pts))
        for bl in components(facts.raster, mask=mask, region=region,
                             min_px=3):
            if axis == "x" and bl.width <= 2.2 and bl.height >= 2.0:
                out.append(facts.deskew(_line_centre(facts, bl, "v"),
                                        bl.centroid[1])[0])
            elif axis == "y" and bl.height <= 2.2 and bl.width >= 2.0:
                out.append(facts.deskew(bl.centroid[0],
                                        _line_centre(facts, bl, "h"))[1])
    return out


# ---------------------------------------------------------------------------
# Label runs: rulers printed down (or across) a band
# ---------------------------------------------------------------------------

def _bands_of_labels(labels: List[Label], facts: PageFacts, axis: str
                     ) -> List[List[Label]]:
    """Labels grouped into the bands they stand in (columns, or rows)."""
    items = []
    for lab in labels:
        if lab.used or lab.value is None:
            continue
        (x0, y0) = facts.deskew(lab.box[0], lab.box[1])
        (x1, y1) = facts.deskew(lab.box[2], lab.box[3])
        if axis == "y":
            items.append((min(x0, x1), max(x0, x1), lab))
        else:
            items.append((min(y0, y1), max(y0, y1), lab))
    items.sort(key=lambda t: t[0])
    groups: List[List[Tuple[float, float, Label]]] = []
    for it in items:
        if groups and it[0] <= max(g[1] for g in groups[-1]) + 1.0:
            groups[-1].append(it)
        else:
            groups.append([it])
    return [[g[2] for g in grp] for grp in groups if len(grp) >= 3]


def _label_runs(facts: PageFacts, values: Dict[str, Any],
                frames: List[Frame]) -> None:
    """Depth and elevation rulers (and any scale printed along a margin)."""
    counts: Dict[str, int] = {}
    for axis in ("y", "x"):
        for band in _bands_of_labels(facts.labels, facts, axis):
            band.sort(key=lambda lb: facts.deskew(*lb.centre)[1 if axis == "y"
                                                               else 0])
            run = _run_of_labels(facts, band, axis)
            if run is None:
                continue
            best = None
            for transform in ("linear", "log10"):
                sid_tmp = "tmp"
                sc = _build_scale(facts, sid_tmp, run, transform, "value",
                                  None, (0, 0, 0, 0))
                if sc is None:
                    continue
                if best is None or sc.residual_pt < best.residual_pt:
                    best = sc
            if best is None:
                continue
            kinds = set(run.kinds)
            rising = best.b > 0
            if axis == "y":
                if "northing" in kinds:
                    quantity, fkind = "northing", "plan"
                else:
                    quantity, fkind = (("depth", "log") if rising
                                       else ("elevation", "log"))
            else:
                if "easting" in kinds:
                    quantity, fkind = "easting", "plan"
                elif "station" in kinds:
                    quantity, fkind = "station", "profile"
                else:
                    quantity, fkind = "value", "plot"
            header_q, unit = _header_of_band(facts, run)
            if header_q:
                quantity = header_q
            counts[quantity] = counts.get(quantity, 0) + 1
            suffix = "" if counts[quantity] == 1 else str(counts[quantity])
            sid = f"p{facts.index}.{quantity}{suffix}"
            extent = _run_extent(facts, run, best)
            best.id = sid
            best.quantity = quantity
            best.unit = unit
            best.extent = extent
            for lb in band:
                lb.used = True
            frames.append(Frame(id=sid, page=facts.index, kind=fkind,
                                extent=extent, scales={quantity: best},
                                provenance={"labels": len(run.positions)}))


def _run_of_labels(facts: PageFacts, band: List[Label], axis: str
                   ) -> Optional[_Run]:
    pos = []
    for lb in band:
        c = facts.deskew(*lb.centre)
        pos.append(c[1] if axis == "y" else c[0])
    across = []
    for lb in band:
        c = facts.deskew(*lb.centre)
        across.append(c[0] if axis == "y" else c[1])
    if axis == "y":
        lo = min(facts.deskew(lb.box[0], lb.box[1])[0] for lb in band)
        hi = max(facts.deskew(lb.box[2], lb.box[1])[0] for lb in band)
    else:
        lo = min(facts.deskew(lb.box[0], lb.box[1])[1] for lb in band)
        hi = max(facts.deskew(lb.box[0], lb.box[3])[1] for lb in band)
    return _Run(axis=axis, positions=pos, boxes=[lb.box for lb in band],
                values=[lb.value for lb in band],
                texts=[lb.text for lb in band], kinds=[lb.kind for lb in band],
                heights=[(lb.height if axis == "y" else lb.width)
                         for lb in band],
                across=sum(across) / len(across), band=(lo, hi),
                source=band[0].source, positioned_by=band[0].positioned_by,
                labels=list(band))


def _header_of_band(facts: PageFacts, run: _Run
                    ) -> Tuple[Optional[str], Optional[str]]:
    """What the text over a ruler's band calls it, and the unit it states."""
    if run.axis != "y":
        return None, None
    from planlens.document.loggrid import _unit_from_header, classify_header
    first = min(run.positions)
    lo, hi = run.band
    words = []
    for ln in facts.texts:
        b = ln.bbox
        c = facts.deskew((b[0] + b[2]) / 2.0, (b[1] + b[3]) / 2.0)
        if lo - 25.0 <= c[0] <= hi + 25.0 and first - 120.0 <= c[1] < first - 4:
            words.append((c[1], ln.text))
    if not words:
        return None, None
    words.sort()
    header = " ".join(t for _, t in words[-4:])
    names, unit = classify_header(header)
    unit = unit or _unit_from_header(header)
    q = None
    if "depth" in names:
        q = "depth"
    elif "elevation" in names:
        q = "elevation"
    return q, unit


def _run_extent(facts: PageFacts, run: _Run, sc: Scale) -> BBox:
    """The region a ruler governs: between its frames, across the form."""
    step = abs(1.0 / sc.b) * (sc.anchors[1].value - sc.anchors[0].value
                              if len(sc.anchors) > 1 else 1.0) \
        if sc.b else 20.0
    step = abs(step) or 20.0
    lo_s = min(run.positions) - 1.05 * step
    hi_s = max(run.positions) + 1.05 * step
    frames = [a.position for a in sc.anchors if a.kind == "frame_line"]
    if frames:
        lo_s = min(lo_s, min(frames) - 0.5)
        hi_s = max(hi_s, max(frames) + 0.5)
    # across: the widest rule crossing the band within the run
    across_lo, across_hi = run.band
    for L in _perp_lines(facts, run.axis):
        pos, lo, hi = facts.line_span(L)
        if lo_s - 2 <= pos <= hi_s + 2 and lo <= run.band[0] + 2 \
                and hi >= run.band[1] - 2:
            across_lo = min(across_lo, lo)
            across_hi = max(across_hi, hi)
    if across_hi - across_lo < 3 * (run.band[1] - run.band[0]):
        across_lo, across_hi = 0.0, (facts.width if run.axis == "y"
                                     else facts.height)
    from planlens.document.scales import unrotate_point
    if run.axis == "y":
        corners = [unrotate_point(u, v, facts.angle_deg)
                   for u in (across_lo, across_hi) for v in (lo_s, hi_s)]
    else:
        corners = [unrotate_point(u, v, facts.angle_deg)
                   for u in (lo_s, hi_s) for v in (across_lo, across_hi)]
    xs = [c[0] for c in corners]
    ys = [c[1] for c in corners]
    return (max(0.0, min(xs)), max(0.0, min(ys)), min(facts.width, max(xs)),
            min(facts.height, max(ys)))


# ---------------------------------------------------------------------------
# Scans with no text: label blobs standing in a ruled column
# ---------------------------------------------------------------------------

def _columns(facts: PageFacts) -> Tuple[List[Tuple[float, float]],
                                         Optional[Tuple[float, float]]]:
    """Column bands between long vertical rules, and the ruled y extent."""
    vs = []
    for L in facts.vlines():
        pos, lo, hi = facts.line_span(L)
        if hi - lo >= 0.25 * facts.height and not L.dashed:
            vs.append((pos, lo, hi))
    if len(vs) < 2:
        return [], None
    vs.sort()
    xs: List[float] = []
    for p, _lo, _hi in vs:
        if xs and p - xs[-1] < 3.0:
            continue
        xs.append(p)
    top = min(v[1] for v in vs)
    bot = max(v[2] for v in vs)
    bands = [(a, b) for a, b in zip(xs, xs[1:]) if b - a >= 8.0]
    return bands, (top, bot)


def _regular_blob_run(items: List[Tuple[float, float, Any]]
                      ) -> Optional[Tuple[List[int], float, float]]:
    """The longest evenly spaced run among blobs ``(position, height, blob)``.

    Spacing must be at least 2.2 label heights (labels down a ruler stand
    apart; the lines of a paragraph do not), the run at least three long and
    filling at least 60 % of the slots between its ends.
    """
    n = len(items)
    if n < 3:
        return None
    hs = sorted(t[1] for t in items)
    hmed = hs[len(hs) // 2]
    best = None
    pos = [t[0] for t in items]
    for i in range(n):
        for j in range(i + 1, n):
            step = pos[j] - pos[i]
            if step < max(2.2 * hmed, 8.0):
                continue
            tol = max(0.12 * step, 1.5)
            members = {}
            for k in range(n):
                q = (pos[k] - pos[i]) / step
                kk = round(q)
                if abs(pos[k] - (pos[i] + kk * step)) <= tol:
                    if kk not in members or abs(pos[k] - pos[i] - kk * step) \
                            < abs(pos[members[kk]] - pos[i] - kk * step):
                        members[kk] = k
            if len(members) < 3:
                continue
            # A member of another size (a header's remnant, a stroke of
            # hatching) is not a label of this run: it is left out, not
            # allowed to sink the run.
            hm_all = sorted(items[k][1] for k in members.values())
            hm0 = hm_all[len(hm_all) // 2]
            members = {kk: k for kk, k in members.items()
                       if abs(items[k][1] - hm0) <= 0.45 * hm0}
            if len(members) < 3:
                continue
            ks = sorted(members)
            # take the longest stretch without a gap of more than one slot
            segs: List[List[int]] = [[ks[0]]]
            for a, b in zip(ks, ks[1:]):
                if b - a <= 2:
                    segs[-1].append(b)
                else:
                    segs.append([b])
            seg = max(segs, key=len)
            if len(seg) < 3:
                continue
            span = seg[-1] - seg[0] + 1
            if len(seg) < 0.6 * span:
                continue
            idx = [members[k] for k in seg]
            ps = [pos[k] for k in idx]
            ks_ = seg
            a_, b_ = _lsq(ks_, ps)
            res = max(abs(p - (a_ + b_ * k)) for p, k in zip(ps, ks_))
            hts = [items[k][1] for k in idx]
            hm = sorted(hts)[len(hts) // 2]
            if any(abs(h - hm) > 0.45 * hm for h in hts):
                continue
            score = (len(idx), -res)
            if best is None or score > best[0]:
                best = (score, idx, b_, res)
    if best is None:
        return None
    return best[1], best[2], best[3]


def _lsq(xs: Sequence[float], ys: Sequence[float]) -> Tuple[float, float]:
    n = len(xs)
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return my, 0.0
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    return my - b * mx, b


def _blob_rulers(facts: PageFacts, values: Dict[str, Any],
                 frames: List[Frame]) -> None:
    """Depth rulers on a scan with no text, from label blobs in a column."""
    if facts.raster is None:
        return
    from planlens.document.raster import text_blobs
    from planlens.document.scales import unrotate_point
    bands, ruled = _columns(facts)
    if not bands or ruled is None:
        return
    mask = facts.mask_without_lines()
    # A plot's labels are its own; a plan's scales govern the whole sheet and
    # must not hide a ruler printed on it.
    taken = [f.extent for f in frames if f.kind in ("plot", "profile")]
    # The rules that cross the whole form cut it into a header, a body and a
    # foot: a ruler's labels all stand in ONE of those (a header's words,
    # evenly spaced by chance with the labels below, are not labels).
    width = bands[-1][1] - bands[0][0]
    full = sorted(pos for pos, lo, hi in
                  (facts.line_span(L) for L in facts.hlines()
                   if not L.dashed)
                  if hi - lo >= 0.6 * width and lo <= bands[0][0] + 0.2 * width
                  and ruled[0] - 2 <= pos <= ruled[1] + 2)
    cuts = [ruled[0]] + [p for p in full if ruled[0] + 2 < p < ruled[1] - 2]         + [ruled[1]]
    segments = [(c0, c1) for c0, c1 in zip(cuts, cuts[1:]) if c1 - c0 >= 40.0]
    cands = []
    for a, b in bands:
        pts = [unrotate_point(u, v, facts.angle_deg)
               for u in (a + 1.5, b - 1.5) for v in (ruled[0], ruled[1])]
        region = (min(p[0] for p in pts), min(p[1] for p in pts),
                  max(p[0] for p in pts), max(p[1] for p in pts))
        blobs = text_blobs(facts.raster, mask=mask, region=region,
                           merge_pt=2.6, min_px=6)
        items_all = []
        for bl in blobs:
            if not (LABEL_MIN_H <= bl.height <= LABEL_MAX_H):
                continue
            if bl.width < 1.0 or bl.width > min(LABEL_MAX_W, 0.85 * (b - a)):
                continue                  # a sliver of rule, or prose
            cx, cy = facts.deskew(*bl.centre)
            if not (a + 1.0 <= cx <= b - 1.0):
                continue
            if any(t[0] <= bl.centre[0] <= t[2] and t[1] <= bl.centre[1]
                   <= t[3] for t in taken):
                continue
            # the box's centre, deskewed: the label's position
            top = facts.deskew(bl.centre[0], bl.bbox[1])[1]
            bot = facts.deskew(bl.centre[0], bl.bbox[3])[1]
            items_all.append(((top + bot) / 2.0, bot - top, bl))
        items_all.sort(key=lambda t: t[0])
        for s0, s1 in segments:
            items = [it for it in items_all if s0 + 1.0 < it[0] < s1 - 1.0]
            got = _regular_blob_run(items)
            if got is None:
                continue
            idx, step, res = got
            members = [items[i] for i in idx]
            if len(members) < 4:
                continue                  # three blobs in a row is chance
            first, last = members[0][0], members[-1][0]
            # A ruler's column holds its labels and little else; a column of
            # prose or of table cells holds a run among many other blobs.
            others = [it for it in items if first - 1 <= it[0] <= last + 1
                      and it not in members]
            if len(others) > 0.5 * len(members):
                continue
            # ... and a ruler runs most of the body it stands in.
            if last - first < 0.45 * (s1 - s0):
                continue
            run = _Run(axis="y", positions=[m[0] for m in members],
                       boxes=[m[2].bbox for m in members],
                       values=[None] * len(members), texts=[],
                       kinds=["number"] * len(members),
                       heights=[m[1] for m in members],
                       across=(a + b) / 2.0, band=(a, b), source="pixels",
                       positioned_by="pixels")
            anchor = _anchor_rule(facts, run, run.positions, run.values,
                                  "linear")
            score = (len(members)
                     + (2.0 if anchor["kind"] in ("frames", "ticks") else 0.0)
                     + (0.5 if len(anchor.get("frames", [])) >= 2 else 0.0))
            cands.append((score, run, anchor, step, res))
    if not cands:
        return
    cands.sort(key=lambda c: -c[0])
    score, run, anchor, step, res = cands[0]
    others = [c for c in cands[1:] if c[0] >= 0.8 * score]
    sid = f"p{facts.index}.depth"
    extent = _blob_extent(facts, run, ruled, step)
    note = (f"{len(run.boxes)} label blobs {step:.1f} pt apart in the column "
            f"at x {run.band[0]:.0f}-{run.band[1]:.0f} pt")
    supplied = _values_for(values, sid, run)
    if supplied is not None:
        sc = _build_scale(facts, sid, run, "linear", "depth", None, extent,
                          values=supplied, values_from="caller",
                          anchor=anchor, note=note)
        if sc is None:
            sc = _pending_scale(facts, sid, run, "depth", None, extent,
                                "linear", note, anchor)
            sc.warnings.append("the values supplied were refused: they do "
                               "not rise or fall evenly down the column "
                               "(two or more disagree with the run)")
        elif sc.b < 0:
            sc.quantity = "elevation"
    else:
        sc = _pending_scale(facts, sid, run, "depth", None, extent, "linear",
                            note, anchor)
    if others:
        sc.provenance["other_columns"] = [
            f"x {c[1].band[0]:.0f}-{c[1].band[1]:.0f} pt "
            f"({len(c[1].boxes)} blobs)" for c in others]
        sc.warnings.append(f"{len(others)} other column(s) hold an even run "
                           f"of label-sized blobs too; the one chosen has the "
                           f"most labels and agrees with the frame lines")
    frames.append(Frame(id=sid, page=facts.index, kind="log", extent=extent,
                        scales={"depth": sc},
                        provenance={"column": [round(run.band[0], 1),
                                               round(run.band[1], 1)]}))


def _blob_extent(facts: PageFacts, run: _Run, ruled: Tuple[float, float],
                 step: float) -> BBox:
    from planlens.document.scales import unrotate_point
    lo_s = max(ruled[0], min(run.positions) - 1.1 * step)
    hi_s = min(ruled[1], max(run.positions) + 1.1 * step)
    across = [facts.line_span(L) for L in facts.hlines()
              if not L.dashed]
    wide = [(lo, hi) for pos, lo, hi in across
            if lo <= run.band[0] + 2 and hi >= run.band[1] - 2
            and hi - lo >= 3 * (run.band[1] - run.band[0])]
    if wide:
        a = min(w[0] for w in wide)
        b = max(w[1] for w in wide)
    else:
        a, b = 0.0, facts.width
    corners = [unrotate_point(u, v, facts.angle_deg)
               for u in (a, b) for v in (lo_s, hi_s)]
    return (min(c[0] for c in corners), min(c[1] for c in corners),
            max(c[0] for c in corners), max(c[1] for c in corners))


def _refit_pending(facts: PageFacts, sc: Scale,
                   values: List[Optional[float]]) -> Optional[Scale]:
    """Fit a pending scale once its label values are known."""
    run = sc.provenance.get("_run")
    if run is None or len(values) != len(run.boxes):
        return None
    anchor = sc.provenance.get("_anchor")
    note = sc.provenance.get("note", "")
    new = _build_scale(facts, sc.id, run, sc.transform, sc.quantity, sc.unit,
                       sc.extent, values=values, values_from="caller",
                       anchor=anchor, note=note)
    if new is None and sc.transform == "linear" and all(
            v is None or v > 0 for v in values):
        new = _build_scale(facts, sc.id, run, "log10", sc.quantity, sc.unit,
                           sc.extent, values=values, values_from="caller",
                           anchor=anchor, note=note)
    return new


# ---------------------------------------------------------------------------
# Plans: stored, stated, bars
# ---------------------------------------------------------------------------

_UNIT_WORDS = {"feet": "ft", "foot": "ft", "ft": "ft", "meters": "m",
               "metres": "m", "meter": "m", "metre": "m", "m": "m",
               "miles": "mi", "kilometers": "km", "km": "km", "yards": "yd",
               "inches": "in"}


def _bar_unit(facts: PageFacts, run: _Run) -> Optional[str]:
    """A unit word printed beside or under a bar's labels."""
    y = sum(b[1] + b[3] for b in run.boxes) / (2 * len(run.boxes))
    x_lo = min(b[0] for b in run.boxes) - 30
    x_hi = max(b[2] for b in run.boxes) + 60
    for ln in facts.texts:
        t = (ln.text or "").strip().lower().strip(".")
        b = ln.bbox
        cy = (b[1] + b[3]) / 2.0
        if abs(cy - y) > 22 or b[2] < x_lo or b[0] > x_hi:
            continue
        for w in re.findall(r"[a-z]+", t):
            if w in _UNIT_WORDS:
                return _UNIT_WORDS[w]
    return None


def _bar_marks(facts: PageFacts, run: _Run) -> List[Optional[float]]:
    """For each bar label, the x of the tick or block edge under (or over) it."""
    out: List[Optional[float]] = []
    for box in run.boxes:
        cx = (box[0] + box[2]) / 2.0
        reach = max(3.0, 0.5 * (box[2] - box[0]) + 2.0)
        best = None
        for (x0, y0, x1, y1, _w) in facts.segments:
            if abs(x1 - x0) > 0.35:
                continue
            if not (box[3] - 1.0 <= y0 <= box[3] + 14.0
                    or box[1] - 14.0 <= y1 <= box[1] + 1.0):
                continue
            if abs(x0 - cx) <= reach and (best is None
                                          or abs(x0 - cx) < abs(best - cx)):
                best = x0
        for bb, filled in facts.rects:
            if not (box[3] - 1.0 <= bb[1] <= box[3] + 14.0):
                continue
            for ex in (bb[0], bb[2]):
                if abs(ex - cx) <= reach and (best is None
                                              or abs(ex - cx) < abs(best - cx)):
                    best = ex
        if best is None and facts.raster is not None:
            got = _raster_bar_mark(facts, box, cx, reach)
            if got is not None:
                # A filled block's ink runs past the block's true edge by
                # half its outline's width; that is in the bar's anchor
                # allowance rather than guessed here.
                best, _side = got
        out.append(None if best is None else facts.deskew(best, box[3])[0])
    return out


def _raster_bar_mark(facts: PageFacts, box: BBox, cx: float, reach: float
                     ) -> Optional[Tuple[float, str]]:
    """A tick or a block edge under a label, from the pixels."""
    import numpy as np
    ras = facts.raster
    region = (cx - reach - 2.0, box[3] + 0.8, cx + reach + 2.0, box[3] + 14.0)
    c0, r0, c1, r1 = ras.box_to_pixel(region)
    if c1 - c0 < 3 or r1 - r0 < 3:
        return None
    sub = ras.mask[r0:r1, c0:c1]
    if not sub.any():
        return None
    # longest vertical run of ink per column
    runs = np.zeros(sub.shape[1], dtype=np.int32)
    cur = np.zeros(sub.shape[1], dtype=np.int32)
    for row in sub:
        cur = np.where(row, cur + 1, 0)
        runs = np.maximum(runs, cur)
    tall = runs >= max(3, int(round(2.5 * ras.z)))
    if not tall.any():
        return None
    cols = np.nonzero(tall)[0]
    groups = []
    start = prev = cols[0]
    for c in cols[1:]:
        if c != prev + 1:
            groups.append((start, prev))
            start = c
        prev = c
    groups.append((start, prev))
    marks: List[Tuple[float, str]] = []
    w2 = int(round(2.0 * ras.z))
    inkw = ras.inkw[r0:r1, c0:c1]
    for a, b in groups:
        if b - a + 1 <= w2:
            prof = inkw[:, a:b + 1].sum(axis=0)
            xs = np.arange(a, b + 1) + 0.5
            marks.append((float((prof * xs).sum() / max(prof.sum(), 1e-9)),
                          "thin"))
        else:
            # A filled block's two edges, to a fraction of a pixel: the edge
            # column holds the block's ink in proportion to how much of it
            # the edge covers.
            rows = sub[:, min(b, a + 2):max(a + 3, b - 1)].all(axis=1)
            if rows.sum() >= 2 and b - a >= 4:
                full = float(inkw[rows, a + 2:b - 1].mean())
                fa = float(inkw[rows, a].mean()) / max(full, 1e-9)
                fb = float(inkw[rows, b].mean()) / max(full, 1e-9)
                fa, fb = min(max(fa, 0.0), 1.0), min(max(fb, 0.0), 1.0)
                marks += [(a + 1.0 - fa, "left"), (b + fb, "right")]
            else:
                marks += [(a + 0.0, "left"), (b + 1.0, "right")]
    if not marks:
        return None
    pcx = (cx - ras.x0) * ras.z - c0
    m, side = min(marks, key=lambda t: abs(t[0] - pcx))
    return ras.x0 + (c0 + m) / ras.z, side


def _plan_scales(doc, facts: PageFacts, values: Dict[str, Any],
                 frames: List[Frame]) -> None:
    """The scales of a plan sheet, compared with each other."""
    from planlens.document.scale import page_viewports, parse_ratio
    fz = getattr(doc, "_doc", doc)
    page = fz[facts.index]
    found: Dict[str, Scale] = {}
    page_box = (0.0, 0.0, facts.width, facts.height)
    try:
        vps, _w = page_viewports(fz, page, facts.index)
    except Exception:                                   # pragma: no cover
        vps = []
    for n, vp in enumerate(vps):
        sc = scale_from_viewport(vp, scale_id=f"p{facts.index}.plan.stored"
                                 + (str(n + 1) if n else ""))
        if sc is not None:
            found[sc.id] = sc
    for ln in facts.texts:
        t = (ln.text or "").strip()
        if not t or len(t) > 60:
            continue
        low = t.lower()
        if "scale" not in low and "=" not in t and ":" not in t:
            continue
        parsed = parse_ratio(t)
        if not parsed:
            continue
        if parsed[1] is None and parsed[3] is None and parsed[2] / parsed[0] < 2:
            continue
        if parsed[1] is None and parsed[3] is None and "scale" not in low:
            continue
        sc = scale_from_stated(t, facts.index, page_box,
                               scale_id=f"p{facts.index}.plan.stated"
                               + ("" if f"p{facts.index}.plan.stated"
                                  not in found else "2"),
                               box=tuple(ln.bbox))
        if sc is not None:
            found[sc.id] = sc
    # scale bars: a row of labels over ticks or block edges
    for band in _bands_of_labels(facts.labels, facts, "x"):
        band.sort(key=lambda lb: facts.deskew(*lb.centre)[0])
        run = _run_of_labels(facts, band, "x")
        if run is None or min(v for v in run.values if v is not None) != 0.0:
            continue
        marks = _bar_marks(facts, run)
        if sum(m is not None for m in marks) < max(3, 0.6 * len(marks)):
            continue
        if _drawn_marks_run_on(facts, run, marks) or (
                facts.raster is not None
                and _marks_run_on(facts, run, marks)):
            continue                # a chart's axis or a table, not a bar
        unit = _bar_unit(facts, run)
        sc = _bar_scale(facts, run, marks, unit, run.values, run.source)
        if sc is not None:
            for lb in band:
                lb.used = True
            found[sc.id] = sc
    if facts.raster is not None and not any(".bar" in k for k in found):
        sc = _raster_bar(facts, values)
        if sc is not None:
            found[sc.id] = sc
    # coordinate grids found as label runs become plan distance evidence too
    if not found:
        return
    primary, notes = _reconcile(found)
    frames.append(Frame(id=f"p{facts.index}.plan", page=facts.index,
                        kind="plan", extent=page_box, scales=dict(found),
                        provenance={"primary": primary},
                        warnings=notes))


def _bar_scale(facts: PageFacts, run: _Run, marks: List[Optional[float]],
               unit: Optional[str], values: List[Optional[float]],
               values_from: str) -> Optional[Scale]:
    pairs = [(m, v) for m, v in zip(marks, values)
             if m is not None and v is not None]
    if len(pairs) < 3:
        return None
    # Block edges found in pixels sit where the ink stops, which is half an
    # outline's width past the true edge (about 0.3 pt on a drafted bar).
    edge_pt = 0.35 if facts.raster is not None else 0.0
    anchor = {"positions": [m if m is not None else p
                            for m, p in zip(marks, run.positions)],
              "kind": "ticks", "text": "bar labels snapped to the ticks or "
                                       "block edges under them",
              "anchor_pt": edge_pt, "frames": []}
    sc = _build_scale(facts, f"p{facts.index}.plan.bar", run, "linear",
                      "distance", unit, (0, 0, facts.width, facts.height),
                      values=values, values_from=values_from, anchor=anchor,
                      note="graphic scale bar")
    if sc is None:
        return None
    # A bar is a length scale: value per point, no origin. Its relative
    # uncertainty is both ends' position error over its length.
    sc.axis = "distance"
    sc.rel_uncertainty = (math.sqrt(2.0) * sc.plus_minus_pt_at(None) / max(
        1e-9, max(m for m, _ in pairs) - min(m for m, _ in pairs)))
    sc.provenance["bar_length_pt"] = round(max(m for m, _ in pairs)
                                           - min(m for m, _ in pairs), 2)
    if unit is None:
        sc.warnings.append("the bar's unit word was not found; its values "
                           "are in whatever unit the bar prints")
    return sc


def _raster_bar(facts: PageFacts, values: Dict[str, Any]) -> Optional[Scale]:
    """A scale bar on a scan: a row of label blobs over ticks or blocks."""
    from planlens.document.raster import text_blobs
    mask = facts.mask_without_lines()
    blobs = [bl for bl in text_blobs(facts.raster, mask=mask, merge_pt=2.2,
                                     min_px=6)
             if LABEL_MIN_H <= bl.height <= LABEL_MAX_H and bl.width <= 30]
    # rows of blobs at one height
    rows: List[List[Any]] = []
    for bl in sorted(blobs, key=lambda b: facts.deskew(*b.centre)[1]):
        cy = facts.deskew(*bl.centre)[1]
        if rows and abs(facts.deskew(*rows[-1][0].centre)[1] - cy) <= 1.5:
            rows[-1].append(bl)
        else:
            rows.append([bl])
    best = None
    for row in rows:
        if len(row) < 3:
            continue
        row.sort(key=lambda b: b.centre[0])
        # bars are compact: labels within a few inches of each other
        for i in range(len(row)):
            seq = [row[i]]
            for bl in row[i + 1:]:
                if bl.centre[0] - seq[-1].centre[0] > 160:
                    break
                seq.append(bl)
            if len(seq) < 3:
                continue
            run = _Run(axis="x", positions=[facts.deskew(*b.centre)[0]
                                            for b in seq],
                       boxes=[b.bbox for b in seq], values=[None] * len(seq),
                       texts=[], kinds=["number"] * len(seq),
                       heights=[b.width for b in seq],
                       across=facts.deskew(*seq[0].centre)[1],
                       band=(min(b.bbox[1] for b in seq),
                             max(b.bbox[3] for b in seq)),
                       source="pixels", positioned_by="pixels")
            hs = sorted(b.height for b in seq)
            if hs[0] < 3.0 or hs[-1] > 10.0 or hs[-1] / hs[0] > 1.4:
                continue                  # a bar's numbers are one size
            # A bar's labels are spread out along it (numbers with room
            # between them); the letters of an underlined word are not.
            ws = sorted(b.width for b in seq)
            gaps = sorted(b2 - b1 for b1, b2 in zip(run.positions,
                                                    run.positions[1:]))
            if gaps[len(gaps) // 2] < max(8.0, 2.0 * ws[len(ws) // 2]):
                continue
            # A bar's divisions are even, or step up 1-2-5 (a subdivided
            # first segment, a doubled last one); a row of table cells or
            # of words is spaced however the text falls.
            if gaps[-1] > 4.5 * max(gaps[0], 1e-9):
                continue
            marks = _bar_marks(facts, run)
            # ... and each label stands over its tick or block edge: a mark
            # half a cell away is the rule BETWEEN two table cells.
            marks = [m if m is not None and abs(m - p) <= max(2.0, 0.3 * b.width)
                     else None for m, p, b in zip(marks, run.positions, seq)]
            n_m = sum(m is not None for m in marks)
            if n_m < 3 or n_m < 0.75 * len(seq):
                continue
            if _marks_run_on(facts, run, marks):
                continue
            if not _bar_base(facts, run, marks):
                continue
            if best is None or len(seq) > len(best[0].boxes):
                best = (run, marks)
    if best is None:
        return None
    run, marks = best
    sid = f"p{facts.index}.plan.bar"
    supplied = _values_for(values, sid, run)
    if supplied is None:
        anchor = {"positions": [m if m is not None else p
                                for m, p in zip(marks, run.positions)],
                  "kind": "ticks", "text": "bar labels over ticks or block "
                                           "edges", "anchor_pt": 0.0,
                  "frames": []}
        run2 = _Run(**{**run.__dict__})
        sc = _pending_scale(facts, sid, run2, "distance", None,
                            (0, 0, facts.width, facts.height), "linear",
                            "a graphic scale bar (pixels)", anchor)
        sc.axis = "distance"
        sc.provenance["_marks"] = marks
        return sc
    sc = _bar_scale(facts, run, marks, _bar_unit(facts, run), supplied,
                    "caller")
    return sc


def _drawn_marks_run_on(facts: PageFacts, run: _Run,
                        marks: List[Optional[float]]) -> bool:
    """Are the drawn marks under a row of labels long rules?

    A bar's ticks and block edges are a few points long; a chart's
    gridlines and a table's column rules, standing on the same row of
    numbers, run on for the height of the chart or the table.
    """
    n = hits = 0
    for m, box in zip(marks, run.boxes):
        if m is None:
            continue
        n += 1
        lo_y, hi_y = box[1] - 14.0, box[3] + 14.0
        long_rule = False
        for (x0, y0, x1, y1, _w) in facts.segments:
            if abs(x1 - x0) > 0.35 or abs(y1 - y0) <= 20.0:
                continue
            sx = facts.deskew(x0, (y0 + y1) / 2.0)[0]
            if abs(sx - m) <= 0.6 and min(y0, y1) <= hi_y                     and max(y0, y1) >= lo_y:
                long_rule = True
                break
        if not long_rule:
            for bb, _f in facts.rects:
                if bb[3] - bb[1] <= 20.0:
                    continue
                if (abs(bb[0] - m) <= 0.6 or abs(bb[2] - m) <= 0.6)                         and bb[1] <= hi_y and bb[3] >= lo_y:
                    long_rule = True
                    break
        hits += long_rule
    return n > 0 and hits >= 0.5 * n


def _marks_run_on(facts: PageFacts, run: _Run,
                  marks: List[Optional[float]]) -> bool:
    """Are the marks under a row of labels long rules (a table's columns)?

    A bar's ticks and block edges are short: nothing carries on above its
    labels or far below its body at the same x. A table's column rules do.
    """
    import numpy as np
    ras = facts.raster
    if ras is None:
        return False
    top = min(facts.deskew(b[0], b[1])[1] for b in run.boxes)
    bottom = max(facts.deskew(b[0], b[3])[1] for b in run.boxes)
    long_run = max(3, int(round(5.0 * ras.z)))
    n = hits = 0
    for m in marks:
        if m is None:
            continue
        n += 1
        for v0, v1 in ((top - 14.0, top - 1.0), (bottom + 18.0, bottom + 34.0)):
            pts = [unrotate_point(u, v, facts.angle_deg)
                   for u in (m - 0.8, m + 0.8) for v in (v0, v1)]
            c0, r0, c1, r1 = ras.box_to_pixel(
                (min(p[0] for p in pts), min(p[1] for p in pts),
                 max(p[0] for p in pts), max(p[1] for p in pts)))
            sub = ras.line_mask[r0:r1, c0:c1]
            if not sub.size:
                continue
            col = sub.any(axis=1)
            best = cur = 0
            for v in col:
                cur = cur + 1 if v else 0
                best = max(best, cur)
            if best >= long_run:
                hits += 1
                break
    return n > 0 and hits >= 0.5 * n


def _bar_base(facts: PageFacts, run: _Run, marks: List[Optional[float]]
              ) -> bool:
    """Does a drawn rule run under the labels from the first mark to the last?

    A scale bar is a horizontal structure — a base line, or the outline of
    its blocks — under all of its labels. A row of numbers in a table with
    column rules beneath it is not.
    """
    xs = [m for m in marks if m is not None]
    if len(xs) < 3:
        return False
    lo, hi = min(xs), max(xs)
    if hi - lo < 20.0:
        return False
    bottom = max(facts.deskew(b[0], b[3])[1] for b in run.boxes)
    for L in facts.hlines():
        if L.dashed or L.coverage < 0.9:
            continue                     # a bar's base is drawn solid
        pos, a, b = facts.line_span(L)
        if not (bottom - 1.0 <= pos <= bottom + 14.0):
            continue
        # the base runs from the first mark to the last and STOPS there (a
        # table's rule runs on to the table's edges)
        slack = 6.0 + 0.1 * (hi - lo)
        if a <= lo + 2.0 and b >= hi - 2.0 and a >= lo - slack                 and b <= hi + slack:
            return True
    if facts.raster is not None:
        # A bar drawn as filled and empty blocks has no single rule along
        # it: its body is the evidence — under the labels, every column from
        # the first mark to the last carries ink, which the rows of a table
        # (empty between its column rules) do not.
        import numpy as np
        ras = facts.raster
        top_y = max(b[3] for b in run.boxes) + 0.5
        pts = [unrotate_point(u, v, facts.angle_deg)
               for u in (lo + 1.0, hi - 1.0)
               for v in (bottom + 0.5, bottom + 12.0)]
        c0, r0, c1, r1 = ras.box_to_pixel(
            (min(p[0] for p in pts), min(p[1] for p in pts),
             max(p[0] for p in pts), max(p[1] for p in pts)))
        sub = ras.line_mask[r0:r1, c0:c1]
        del top_y
        if not (sub.size and sub.shape[1] >= 10):
            return False
        if float(np.mean(sub.any(axis=0))) < 0.92:
            return False
        # ... and a bar is a band that STOPS: little ink just past its ends
        # in the same rows, and little under it. A hatched legend column or
        # a table full of figures carries on in every direction.
        span = c1 - c0
        pad = max(3, int(round(6.0 * ras.z)))
        gap = max(1, int(round(1.5 * ras.z)))
        h, w = ras.line_mask.shape
        left = ras.line_mask[r0:r1, max(0, c0 - gap - pad):max(0, c0 - gap)]
        right = ras.line_mask[r0:r1, min(w, c1 + gap):min(w, c1 + gap + pad)]
        below = ras.line_mask[min(h, r1 + gap):min(h, r1 + gap + pad), c0:c1]
        for part in (left, right, below):
            if part.size and float(np.mean(part.any(axis=0 if part is below
                                                     else 1))) > 0.5:
                return False
        # ... nor does a rule run on past either end (a table's or a
        # chart's edge, with a row of figures standing on it)
        for part in (left, right):
            if part.size and float(np.mean(part.any(axis=0))) > 0.9:
                return False
        del span
        return True
    return False


def _reconcile(found: Dict[str, Scale]) -> Tuple[Optional[str], List[str]]:
    """Compare a sheet's distance scales; name the one to use and the gaps."""
    usable = {k: s for k, s in found.items() if s.usable}
    rank = {"stored": 0, "bar": 1, "stated": 2}

    def kind(k: str) -> str:
        for name in rank:
            if f".plan.{name}" in k:
                return name
        return "other"

    def per_unit(s: Scale) -> Optional[float]:
        from planlens.ir.measure import LENGTH_IN_METRES
        if s.unit in LENGTH_IN_METRES:
            return s.b * LENGTH_IN_METRES[s.unit]
        return None

    notes: List[str] = []
    if not usable:
        return None, notes
    order = sorted(usable, key=lambda k: rank.get(kind(k), 9))
    primary = order[0]
    p = per_unit(usable[primary])
    for k in order[1:]:
        q = per_unit(usable[k])
        if p is None or q is None:
            continue
        gap = abs(q - p) / p
        if gap <= RECONCILE_AGREE:
            notes.append(f"{kind(k)} and {kind(primary)} agree to "
                         f"{100 * gap:.2f} %")
            if kind(k) == "stated":
                from planlens.document.scales import CONF_STATED_CONFIRMED
                usable[k].confidence = max(usable[k].confidence,
                                           CONF_STATED_CONFIRMED)
        else:
            notes.append(f"the {kind(k)} scale disagrees with the "
                         f"{kind(primary)} by {100 * gap:.1f} % (the sheet "
                         f"may have been re-plotted at another size); the "
                         f"{kind(primary)} is used")
            usable[k].warnings.append(f"disagrees with the {kind(primary)} "
                                      f"scale by {100 * gap:.1f} %")
    return primary, notes


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def _normalise_values(values: Any) -> Tuple[Dict[str, Any], Optional[list]]:
    if values is None:
        return {}, None
    if isinstance(values, dict):
        return {str(k): v for k, v in values.items()}, None
    return {}, list(values)


def find_scales(doc, page: int, values: Any = None, *,
                dpi: float = 200.0, use_cache: bool = True) -> PageScales:
    """Every scale on one page (0-based), found and fitted.

    ``values`` supplies label values for scales found without them (a scan
    with no text): a dict ``{scale_id: [v, ...]}`` or, for the one scale
    waiting, a plain list — one value per label box, in the order the
    pending scale lists them, ``None`` where a label cannot be read.
    """
    from planlens.document.document import parse_pages
    fz = getattr(doc, "_doc", doc)
    (page,) = parse_pages(page, fz.page_count)
    vdict, vlist = _normalise_values(values)
    key = (page, round(float(dpi), 1),
           repr(sorted(vdict.items())), repr(vlist))
    cache = getattr(doc, "_visual_scales", None)
    if use_cache and cache is not None and key in cache:
        return cache[key]
    facts_cache = getattr(doc, "_visual_facts", None)
    if facts_cache is not None and (page, dpi) in facts_cache:
        facts = facts_cache[(page, dpi)]
        for lb in facts.labels:
            lb.used = False
    else:
        facts = page_facts(doc, page, dpi=dpi)
        try:
            if facts_cache is None:
                facts_cache = {}
                setattr(doc, "_visual_facts", facts_cache)
            facts_cache[(page, dpi)] = facts
        except Exception:                               # pragma: no cover
            pass
    frames: List[Frame] = []
    counter: Dict[str, int] = {}
    _gridded(facts, vdict, frames, counter)
    _projected_grids(facts, vdict, frames)
    _tick_plots(facts, vdict, frames)
    _plan_scales(doc, facts, vdict, frames)
    _label_runs(facts, vdict, frames)
    if not any(f.kind == "log" for f in frames):
        _blob_rulers(facts, vdict, frames)
    out = PageScales(page=page, frames=frames,
                     skew_deg=facts.angle_deg if facts.raster is not None
                     else None, raster=facts.raster is not None,
                     warnings=list(facts.warnings))
    # A plain list of values goes to the first scale waiting for them.
    if vlist is not None:
        pend = out.needing_values()
        if pend:
            vdict = {pend[0].id: vlist}
    if vdict:
        for f in out.frames:
            for k, sc in list(f.scales.items()):
                if sc.needs_values and sc.id in vdict:
                    vals = [None if v is None else float(v)
                            for v in vdict[sc.id]]
                    new = None
                    if sc.axis == "distance" and "_marks" in sc.provenance:
                        run = sc.provenance["_run"]
                        if len(vals) == len(run.boxes):
                            new = _bar_scale(facts, run,
                                             sc.provenance["_marks"],
                                             _bar_unit(facts, run), vals,
                                             "caller")
                    elif len(vals) == len(sc.label_boxes):
                        new = _refit_pending(facts, sc, vals)
                    if new is not None:
                        if new.b < 0 and new.quantity == "depth":
                            new.quantity = "elevation"
                        f.scales[k] = new
                    else:
                        sc.warnings.append(
                            "the values supplied were refused: they do not "
                            "make an even run (or their count does not "
                            "match the label boxes)")
        if any(f.kind == "plan" for f in out.frames):
            for f in out.frames:
                if f.kind == "plan":
                    prim, notes = _reconcile(f.scales and {s.id: s for s in
                                                           f.scales.values()})
                    f.provenance["primary"] = prim
                    f.warnings = notes
    if not out.frames:
        out.warnings.append("no scale was found on this page: positions "
                            "stay in page points")
    if facts.is_raster and not facts.labels:
        out.warnings.append("the page is a picture with no text; label "
                            "values come from the caller (needs_values)")
    out.extra["facts"] = facts
    if use_cache:
        try:
            if cache is None:
                cache = {}
                setattr(doc, "_visual_scales", cache)
            cache[key] = out
        except Exception:                               # pragma: no cover
            pass
    return out
