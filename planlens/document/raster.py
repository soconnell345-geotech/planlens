"""Page pixels as geometry: skew, ruled lines, ink blobs, markers, curve crossings.

WHY THIS MODULE EXISTS. A scanned page has no text and no vector paths, only
pixels — and the pixels still say where things are. Measured on ten scanned
boring-log sheets (2026-10-07): the page's skew to a hundredth of a degree,
the 12 column rules and the depth frame, all ten depth labels as ink blobs,
and every stratum line located to under a pixel along its length; on twelve
scanned grading sheets the gridlines of a linear and a LOG axis. Everything
here runs on numpy and PyMuPDF alone — no OpenCV, which aborts the process on
the FIPS hosts this package runs on (see :mod:`planlens.opencv`), no scipy and
no PIL; PyMuPDF renders straight to numpy.

Frames. A :class:`Raster` is a grey render of a page (or of a clip of it) in
the DISPLAYED frame, and :meth:`Raster.to_page` / :meth:`Raster.to_pixel`
move between its pixels and page points. Pixel ``i`` covers ``[i, i+1)``, so
its centre is ``i + 0.5`` — half a pixel is 0.18 pt at 200 dpi, which is the
size of the effects this module is asked to see.

Lines. :func:`find_lines` measures the page's skew from the projection of its
ink (:func:`measure_skew`), shears the ink so that rules drawn horizontally
run along rows, collects row runs that are SOLID along one or two rows (text,
at any single row, covers half its width at most), groups them across rows,
joins collinear pieces, keeps solid runs and regular dashed ones (a dashed
contact is information, and comes back labelled ``dashed``), and then fits
each line's centre in the UNsheared image from ink-weighted column centroids.
Verticals are the same done on the transpose. Positions are page points.

Blobs. :func:`components` labels connected ink with a run-length union-find;
:func:`text_blobs` merges the characters of one label first;
:func:`solid_blobs` keeps only ink that survives a square erosion (a filled
plot marker survives, a line or a letter does not).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

__all__ = [
    "Raster", "RasterLine", "Blob", "DEFAULT_DPI", "MAX_MEGAPIXELS",
    "ink", "render", "crop", "otsu", "ink_mask", "measure_skew", "find_lines",
    "close_gaps", "components", "text_blobs", "solid_blobs", "erase_lines",
    "column_runs", "image_coverage", "is_raster_page",
]

#: The default analysis resolution: a 1 pt rule is ~3 px, a 7 pt label ~19.
DEFAULT_DPI = 200.0

#: Ceiling on one render, in megapixels (findlike's cap): a D-size sheet at
#: 200 dpi is about 30 MP; analyse a region instead when the box is known.
MAX_MEGAPIXELS = 30.0

#: A row run counts toward a line when, over one or two adjacent rows, ink
#: covers at least this much of it. Text covers about half at most.
RUN_SOLID = 0.55
#: A joined line is SOLID at this coverage of its extent, and may be DASHED
#: down to :data:`DASHED_MIN` coverage when its pieces are regular.
LINE_SOLID = 0.85
DASHED_MIN = 0.25
#: Dashes: at least this many pieces, gaps and dash lengths this regular
#: (coefficient of variation).
DASHED_MIN_PIECES = 4
#: At most this fraction of a dashed line's dash columns may carry ink just
#: above or below the dash (0.6-2.5 pt away): more is a row of text.
DASHED_MAX_CROWDED = 0.35
DASHED_MAX_CV = 0.5


def _np():
    import numpy as np
    return np


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def ink(page, dpi: float, clip=None):
    """The page (or ``clip``) as an ink image: 0 = paper, 255 = black.

    ``clip`` is in the displayed frame (PDF points, top-left origin), which is
    the frame ``page.get_pixmap(clip=...)`` uses. Moved here from
    :mod:`planlens.document.findlike`, which still imports it as ``_ink``.
    """
    import fitz
    np = _np()
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY,
                          clip=fitz.Rect(clip) if clip is not None else None,
                          alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width]
    return 255 - img


def _capped_dpi(width_pt: float, height_pt: float, dpi: float) -> float:
    px = (width_pt * dpi / 72.0) * (height_pt * dpi / 72.0)
    cap = MAX_MEGAPIXELS * 1e6
    return dpi if px <= cap else dpi * math.sqrt(cap / px)


@dataclass
class Raster:
    """A grey render of a page region in the displayed frame."""
    grey: Any                    # uint8, 255 = paper
    z: float                     # pixels per point
    x0: float = 0.0              # page point at the image's left edge
    y0: float = 0.0              # page point at the image's top edge
    page: Optional[int] = None
    _mask: Any = field(default=None, repr=False)
    _line_mask: Any = field(default=None, repr=False)
    _inkw: Any = field(default=None, repr=False)
    threshold: Optional[int] = None

    @property
    def shape(self) -> Tuple[int, int]:
        return self.grey.shape

    @property
    def dpi(self) -> float:
        return self.z * 72.0

    @property
    def pixel_pt(self) -> float:
        """One pixel, in points."""
        return 1.0 / self.z

    @property
    def mask(self):
        if self._mask is None:
            self._mask = ink_mask(self)
        return self._mask

    @property
    def line_mask(self):
        """A more permissive ink mask for FINDING lines.

        Gridlines are often printed grey and a scan lightens a hairline
        further, so at the page's own threshold a gridline breaks into
        fragments. Halfway between that threshold and the paper keeps it
        whole; the line finder's solidity tests keep the paper's grain out.
        """
        if self._line_mask is None:
            np = _np()
            t = self.threshold if self.threshold is not None else None
            if t is None:
                _ = self.mask
                t = self.threshold
            paper = float(np.median(self.grey))
            if paper <= t:
                paper = 245.0
            self._line_mask = self.grey < (t + paper) / 2.0
        return self._line_mask

    @property
    def inkw(self):
        """Ink weight per pixel (0 paper .. 255 black), float32."""
        if self._inkw is None:
            np = _np()
            self._inkw = (255.0 - self.grey.astype(np.float32))
        return self._inkw

    def to_page(self, col: float, row: float) -> Point:
        """A continuous pixel coordinate (pixel ``i`` spans ``[i, i+1)``) ->
        page points."""
        return (self.x0 + col / self.z, self.y0 + row / self.z)

    def to_pixel(self, x: float, y: float) -> Point:
        """Page points -> continuous pixel coordinates."""
        return ((x - self.x0) * self.z, (y - self.y0) * self.z)

    def box_to_page(self, c0: float, r0: float, c1: float, r1: float) -> BBox:
        a = self.to_page(c0, r0)
        b = self.to_page(c1, r1)
        return (a[0], a[1], b[0], b[1])

    def box_to_pixel(self, box: Sequence[float]) -> Tuple[int, int, int, int]:
        """A page box -> integer pixel slice bounds, clipped to the image."""
        h, w = self.grey.shape
        c0, r0 = self.to_pixel(box[0], box[1])
        c1, r1 = self.to_pixel(box[2], box[3])
        return (max(0, int(math.floor(min(c0, c1)))),
                max(0, int(math.floor(min(r0, r1)))),
                min(w, int(math.ceil(max(c0, c1)))),
                min(h, int(math.ceil(max(r0, r1)))))


def render(page, dpi: float = DEFAULT_DPI, clip: Optional[Sequence[float]] = None
           ) -> Raster:
    """A grey :class:`Raster` of ``page`` (or of ``clip``, displayed frame).

    The resolution is capped at :data:`MAX_MEGAPIXELS`. The image's origin is
    read off the pixmap itself (``pix.x``, ``pix.y``), because a clip's pixel
    rectangle is rounded outward and its corner is not the clip's corner.
    """
    import fitz
    np = _np()
    if clip is not None:
        w = abs(clip[2] - clip[0])
        h = abs(clip[3] - clip[1])
    else:
        w, h = page.rect.width, page.rect.height
    dpi = _capped_dpi(w, h, dpi)
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY,
                          clip=fitz.Rect(clip) if clip is not None else None,
                          alpha=False)
    grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width].copy()
    return Raster(grey=grey, z=z, x0=pix.x / z, y0=pix.y / z,
                  page=getattr(page, "number", None))


def crop(raster: Raster, box: Sequence[float]) -> Optional[Raster]:
    """The part of ``raster`` under a page box, as a Raster of its own
    (sharing the page threshold, so ink means the same in both)."""
    c0, r0, c1, r1 = raster.box_to_pixel(box)
    if c1 - c0 < 3 or r1 - r0 < 3:
        return None
    _ = raster.mask
    sub = Raster(grey=raster.grey[r0:r1, c0:c1], z=raster.z,
                 x0=raster.x0 + c0 / raster.z, y0=raster.y0 + r0 / raster.z,
                 page=raster.page)
    sub._mask = raster.mask[r0:r1, c0:c1]
    sub._line_mask = raster.line_mask[r0:r1, c0:c1]
    sub.threshold = raster.threshold
    return sub


def otsu(grey) -> int:
    """Otsu's threshold of a grey image (between paper and ink)."""
    np = _np()
    hist = np.bincount(grey.ravel(), minlength=256).astype(np.float64)
    total = hist.sum()
    if total <= 0:
        return 128
    p = hist / total
    w = np.cumsum(p)
    mu = np.cumsum(p * np.arange(256))
    mt = mu[-1]
    with np.errstate(divide="ignore", invalid="ignore"):
        sb = (mt * w - mu) ** 2 / (w * (1.0 - w))
    sb = np.nan_to_num(sb, nan=0.0, posinf=0.0)
    return int(np.argmax(sb))


def ink_mask(raster: Raster):
    """Ink pixels: darker than Otsu's threshold, clamped to a sane band.

    The clamp keeps a page that is nearly all paper (a sparse plot) from
    putting the threshold in the paper's own noise, and a dark scan from
    putting it inside the ink.
    """
    t = otsu(raster.grey)
    t = int(min(max(t, 90), 215))
    raster.threshold = t
    return raster.grey < t


def image_coverage(page) -> float:
    """How much of the displayed page its images cover (0..1)."""
    try:
        infos = page.get_image_info()
    except Exception:                        # pragma: no cover - defensive
        return 0.0
    area = float(page.rect.width * page.rect.height) or 1.0
    covered = 0.0
    for info in infos:
        b = info.get("bbox")
        if not b:
            continue
        w = max(0.0, min(b[2], page.rect.x1) - max(b[0], page.rect.x0))
        h = max(0.0, min(b[3], page.rect.y1) - max(b[1], page.rect.y0))
        covered += w * h
    return min(1.0, covered / area)


def is_raster_page(page, min_coverage: float = 0.5,
                   max_drawings: int = 40) -> bool:
    """A page whose content is a picture: mostly image, few vector paths."""
    if image_coverage(page) < min_coverage:
        return False
    try:
        n = len(page.get_cdrawings())
    except Exception:                        # pragma: no cover
        n = 0
    return n <= max_drawings


# ---------------------------------------------------------------------------
# Skew
# ---------------------------------------------------------------------------

def measure_skew(mask, max_deg: float = 3.0) -> float:
    """The slope dy/dx of the page's horizontal rules, from its ink alone.

    The ink is projected onto rows after a shear at each candidate angle; the
    angle at which the projection is sharpest (largest sum of squares) is the
    one that lays the rules and the text rows flat. Coarse then fine; the
    result is refined by :func:`find_lines` from the long rules themselves.
    """
    np = _np()
    ys, xs = np.nonzero(mask)
    if len(ys) < 50:
        return 0.0
    if len(ys) > 400000:
        sel = np.random.default_rng(0).choice(len(ys), 400000, replace=False)
        ys, xs = ys[sel], xs[sel]
    ys = ys.astype(np.float64)
    xs = xs.astype(np.float64) - xs.mean()

    def sharpness(slope: float) -> float:
        idx = np.round(ys - xs * slope).astype(np.int64)
        idx -= idx.min()
        h = np.bincount(idx)
        return float((h.astype(np.float64) ** 2).sum())

    def search(lo: float, hi: float, step: float) -> float:
        best, best_v = 0.0, -1.0
        a = lo
        while a <= hi + 1e-12:
            v = sharpness(math.tan(math.radians(a)))
            if v > best_v:
                best, best_v = a, v
            a += step
        return best

    coarse = search(-max_deg, max_deg, 0.1)
    fine = search(coarse - 0.12, coarse + 0.12, 0.01)
    return math.tan(math.radians(fine))


# ---------------------------------------------------------------------------
# Lines
# ---------------------------------------------------------------------------

@dataclass
class RasterLine:
    """One ruled line found in pixels, as fitted, in page points.

    ``orientation`` is ``"h"`` or ``"v"``. For a horizontal line ``y`` at any
    ``x`` is ``a + b * x`` (page points); for a vertical one ``x = a + b * y``.
    ``lo`` / ``hi`` is its extent along its run. ``fit_pt`` is the rms scatter
    of its column centroids about the fit, ``thickness_pt`` its ink thickness.
    """
    orientation: str
    a: float
    b: float
    lo: float
    hi: float
    thickness_pt: float
    coverage: float
    dashed: bool = False
    pieces: int = 1
    fit_pt: float = 0.0
    n_cols: int = 0
    #: ``pixels`` for a line found in a render, ``vector`` for a drawn path.
    source: str = "pixels"

    @property
    def length(self) -> float:
        return self.hi - self.lo

    @property
    def mid(self) -> float:
        return (self.lo + self.hi) / 2.0

    def at(self, t: float) -> float:
        """The line's cross coordinate at ``t`` along it."""
        return self.a + self.b * t

    @property
    def position(self) -> float:
        """The cross coordinate at the line's midpoint."""
        return self.at(self.mid)

    def endpoints(self) -> Tuple[Point, Point]:
        if self.orientation == "h":
            return (self.lo, self.at(self.lo)), (self.hi, self.at(self.hi))
        return (self.at(self.lo), self.lo), (self.at(self.hi), self.hi)

    def point_at(self, t: float) -> Point:
        return (t, self.at(t)) if self.orientation == "h" else (self.at(t), t)

    @property
    def bbox(self) -> BBox:
        (x0, y0), (x1, y1) = self.endpoints()
        h = self.thickness_pt / 2.0
        if self.orientation == "h":
            return (min(x0, x1), min(y0, y1) - h, max(x0, x1), max(y0, y1) + h)
        return (min(x0, x1) - h, min(y0, y1), max(x0, x1) + h, max(y0, y1))

    def plus_minus_pt(self, pixel_pt: float) -> float:
        """The 95 % uncertainty of the line's centre.

        A drawn path is exact. A line found in pixels is as good as the
        scatter of its column centroids allows (twice the standard error,
        with the scatter taken as at least a fifth of a pixel: neighbouring
        columns of a scan are not independent), floored at half a pixel.
        """
        if self.source == "vector":
            return 0.0
        sd = max(self.fit_pt, 0.2 * pixel_pt)
        n_eff = max(1.0, self.n_cols / 6.0)
        se = 2.0 * sd / math.sqrt(n_eff)
        return math.hypot(se, 0.5 * pixel_pt)

    def to_dict(self) -> Dict[str, Any]:
        (x0, y0), (x1, y1) = self.endpoints()
        return {"orientation": self.orientation,
                "from": [round(x0, 2), round(y0, 2)],
                "to": [round(x1, 2), round(y1, 2)],
                "thickness_pt": round(self.thickness_pt, 2),
                "dashed": self.dashed}


def close_gaps(mask, gap: int):
    """Fill runs of up to ``gap`` paper pixels between ink along each row."""
    np = _np()
    if gap <= 0:
        return mask.copy()
    h, w = mask.shape
    idx = np.arange(w)
    big = 1 << 30
    prev = np.where(mask, idx, -big)
    prev = np.maximum.accumulate(prev, axis=1)
    nxt = np.where(mask, idx, big)
    nxt = np.minimum.accumulate(nxt[:, ::-1], axis=1)[:, ::-1]
    return mask | ((nxt - prev) <= gap + 1)


def _shear_rows(arr, slope: float, fill=0):
    """``out[r, x] = arr[r + round(slope * (x - cx)), x]``: lays rules of
    slope ``slope`` flat. Returns ``(out, shifts)``."""
    np = _np()
    h, w = arr.shape
    cx = (w - 1) / 2.0
    shifts = np.round(slope * (np.arange(w) - cx)).astype(np.int64)
    rows = np.arange(h)[:, None] + shifts[None, :]
    valid = (rows >= 0) & (rows < h)
    rows_c = np.clip(rows, 0, h - 1)
    out = np.take_along_axis(arr, rows_c, axis=0)
    if out.dtype == bool:
        out = out & valid
    else:
        out = np.where(valid, out, fill)
    return out, shifts


def _row_runs(mask, min_len: int):
    """Every row run of True of at least ``min_len``: (row, start, end)."""
    np = _np()
    h, w = mask.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    sr, sc = np.nonzero(d == 1)
    er, ec = np.nonzero(d == -1)
    keep = (ec - sc) >= min_len
    return sr[keep], sc[keep], ec[keep]


def _lines_along_rows(mask, inkw, slope: float, z: float, *,
                      min_len_px: int, max_thick_px: int, gap_px: int,
                      join_gap_px: int, piece_px: int) -> List[Dict[str, Any]]:
    """Horizontal lines of an array, in ARRAY pixel coordinates.

    Returns dicts with ``a, b`` (row = a + b*col, continuous coordinates),
    ``lo, hi`` (columns), thickness, coverage, dashed, pieces, fit rms.
    """
    np = _np()
    h, w = mask.shape
    sheared, shifts = _shear_rows(mask, slope)
    closed = close_gaps(sheared, gap_px)
    rr, ra, rb = _row_runs(closed, piece_px)
    if len(rr) == 0:
        return []
    # Coverage over one or two adjacent rows of the RAW sheared ink: a rule
    # is solid along it, a row of text is not.
    pair = sheared.copy()
    pair[:-1] |= sheared[1:]
    cs1 = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(sheared, axis=1, out=cs1[:, 1:])
    cs2 = np.zeros((h, w + 1), dtype=np.int32)
    np.cumsum(pair, axis=1, out=cs2[:, 1:])
    length = (rb - ra).astype(np.float64)
    cov = (cs2[rr, rb] - cs2[rr, ra]) / length
    keep = cov >= RUN_SOLID
    rr, ra, rb = rr[keep], ra[keep], rb[keep]
    if len(rr) == 0:
        return []

    # Group runs across adjacent rows into pieces (one stroke's thickness).
    order = np.lexsort((ra, rr))
    rr, ra, rb = rr[order], ra[order], rb[order]
    groups: List[List[int]] = []     # [r0, r1, a, b]
    active: List[int] = []           # indexes into groups, touched last row
    last_row = -10
    row_start = 0
    n = len(rr)
    i = 0
    while i < n:
        r = int(rr[i])
        j = i
        while j < n and rr[j] == r:
            j += 1
        if r - last_row > 1:
            active = []
        new_active: List[int] = []
        for k in range(i, j):
            a, b = int(ra[k]), int(rb[k])
            placed = None
            for g in active:
                G = groups[g]
                ov = min(b, G[3]) - max(a, G[2])
                # The rows of one stroke run about as far as each other; a
                # long rule must not be swallowed into a letter or a marker
                # touching it from the row above.
                if ov >= 0.5 * max(b - a, G[3] - G[2]):
                    placed = g
                    break
            if placed is None:
                groups.append([r, r, a, b])
                placed = len(groups) - 1
            else:
                G = groups[placed]
                G[1] = r
                G[2] = min(G[2], a)
                G[3] = max(G[3], b)
            if placed not in new_active:
                new_active.append(placed)
        active = new_active
        last_row = r
        i = j

    # Pieces: thin, solid along their best row pair.
    pieces = []
    for r0, r1, a, b in groups:
        if r1 - r0 + 1 > max_thick_px:
            continue
        best = 0.0
        for r in range(r0, r1 + 1):
            c = (cs2[r, b] - cs2[r, a]) / max(1, b - a)
            best = max(best, c)
        if best < RUN_SOLID:
            continue
        pieces.append([(r0 + r1) / 2.0, r0, r1, a, b])
    if not pieces:
        return []

    # Join collinear pieces into lines, in two passes. A SOLID rule is joined
    # across small breaks only, then its long solid stretches across larger
    # ones: a few strokes of a label standing just past a gridline's end
    # must not lengthen the gridline (it would then be erased through the
    # label). What is left over may make a DASHED line, if its pieces are
    # regular.
    def coverage_of(L) -> float:
        r0 = max(0, int(math.floor(L["r0"])) - 1)
        r1 = min(h - 1, int(math.ceil(L["r1"])) + 1)
        band = sheared[r0:r1 + 1, L["a"]:L["b"]].any(axis=0)
        return float(band.mean()) if band.size else 0.0

    small_gap = max(2, int(round(0.8 * join_gap_px / 3.0)))
    long_px = max(min_len_px // 2, int(round(3.5 * piece_px)))
    stage_a = _join_pieces(pieces, small_gap)
    solid_segs = []
    rest = []
    for L in stage_a:
        cov = coverage_of(L)
        if cov >= LINE_SOLID and L["b"] - L["a"] >= long_px:
            L["coverage"] = cov
            solid_segs.append(L)
        else:
            rest.extend((L["rc"], L["r0"], L["r1"], a, b)
                        for a, b in L["pieces"])
    out = []
    for L in _join_segments(solid_segs, join_gap_px):
        if L["b"] - L["a"] < min_len_px:
            continue
        out.append({**L, "coverage": coverage_of(L), "dashed": False,
                    "pieces": len(L["pieces"])})
    for L in _join_pieces(rest, join_gap_px):
        a, b = L["a"], L["b"]
        if b - a < min_len_px:
            continue
        coverage = coverage_of(L)
        segs = L["pieces"]
        if coverage >= LINE_SOLID:
            out.append({**L, "coverage": coverage, "dashed": False,
                        "pieces": len(segs)})
            continue
        if coverage < DASHED_MIN or len(segs) < DASHED_MIN_PIECES:
            continue
        dl = np.array([s_[1] - s_[0] for s_ in segs], dtype=np.float64)
        gl = np.array([segs[k + 1][0] - segs[k][1]
                       for k in range(len(segs) - 1)], dtype=np.float64)
        # Regular, robustly: most gaps and most dashes near their medians. A
        # line crossing the dashes, a dot lost in the scan or the first and
        # last dash cut short must not cost the whole line its dashes.
        def regular(v, need):
            if not len(v):
                return False
            m = float(np.median(v))
            if m <= 0:
                return False
            ok = (v >= 0.5 * m) & (v <= 1.7 * m)
            return float(ok.mean()) >= need
        if not (regular(gl, 0.75) and regular(dl, 0.6)):
            continue
        # A dashed rule's dashes stand alone, with paper above and below
        # each. The strokes along a row of text (the feet of its letters, a
        # line of figures) have the rest of each letter right over them.
        near = max(1, int(round(0.6 * z)))
        far = max(near + 1, int(round(2.5 * z)))
        r0_ = int(math.floor(L["r0"]))
        r1_ = int(math.ceil(L["r1"]))
        above = sheared[max(0, r0_ - far):max(0, r0_ - near + 1), :]
        below = sheared[min(h, r1_ + near):min(h, r1_ + far + 1), :]
        crowded = total = 0
        for a_, b_ in segs:
            hit = np.zeros(max(0, b_ - a_), dtype=bool)
            if above.size:
                hit |= above[:, a_:b_].any(axis=0)
            if below.size:
                hit |= below[:, a_:b_].any(axis=0)
            crowded += int(hit.sum())
            total += max(0, b_ - a_)
        if total and crowded > DASHED_MAX_CROWDED * total:
            continue
        out.append({**L, "coverage": coverage, "dashed": True,
                    "pieces": len(segs)})

    # Precise centre: ink-weighted column centroids in the UNSHEARED image.
    results = []
    for L in out:
        a, b = L["a"], L["b"]
        cols = np.arange(a, b)
        half = (L["r1"] - L["r0"]) / 2.0 + 2.0
        centre_rows = L["rc"] + shifts[cols]      # sheared row -> image row
        lo_r = np.floor(centre_rows - half).astype(np.int64)
        span = int(math.ceil(2 * half)) + 2
        rows = lo_r[:, None] + np.arange(span)[None, :]
        valid = (rows >= 0) & (rows < h)
        rows_c = np.clip(rows, 0, h - 1)
        wts = inkw[rows_c, cols[:, None]] * valid
        # Weigh only the line's own ink: the paper's grain and a JPEG's
        # ringing are subtracted as a floor.
        floor = np.percentile(wts, 20, axis=1)[:, None]
        wts = np.clip(wts - floor, 0.0, None)
        tot = wts.sum(axis=1)
        pos = tot[tot > 0]
        if not len(pos):
            continue
        # Only columns carrying the line's ink (a dashed line's gaps carry
        # none, and a faint speck of noise is no evidence of where it is).
        good = tot > 0.25 * float(np.median(pos))
        if good.sum() < 3:
            continue
        cy = (wts * (rows + 0.5)).sum(axis=1)[good] / tot[good]
        cx = cols[good] + 0.5
        # A crossing rule or a letter touching the line drags the centroid;
        # a robust refit drops those columns.
        A = np.vstack([np.ones_like(cx), cx]).T
        sol, *_ = np.linalg.lstsq(A, cy, rcond=None)
        res = cy - A @ sol
        mad = float(np.median(np.abs(res))) or 0.05
        ok = np.abs(res) <= max(3.0 * 1.4826 * mad, 0.35)
        if ok.sum() >= 3:
            sol, *_ = np.linalg.lstsq(A[ok], cy[ok], rcond=None)
            res = cy[ok] - A[ok] @ sol
        rms = float(np.sqrt(np.mean(res ** 2))) if len(res) else 0.0
        results.append({"a": float(sol[0]), "b": float(sol[1]),
                        "lo": float(a), "hi": float(b),
                        "thickness": float(L["r1"] - L["r0"] + 1),
                        "coverage": L["coverage"], "dashed": L["dashed"],
                        "pieces": L["pieces"], "fit": rms,
                        "n": int(ok.sum())})
    return results


def _join_pieces(pieces, gap: int) -> List[Dict[str, Any]]:
    """Collinear pieces ``(rc, r0, r1, a, b)`` joined across gaps <= ``gap``."""
    pieces = sorted(pieces, key=lambda p: (p[3], p[0]))
    lines: List[Dict[str, Any]] = []
    open_lines: List[Dict[str, Any]] = []
    for rc, r0, r1, a, b in pieces:
        host = None
        for L in open_lines:
            if abs(L["rc"] - rc) <= 1.5 and a - L["b"] <= gap                     and a >= L["a"] - 1:
                host = L
                break
        if host is None:
            L = {"rc": rc, "r0": r0, "r1": r1, "a": a, "b": b,
                 "pieces": [(a, b)]}
            open_lines.append(L)
            lines.append(L)
        else:
            n = len(host["pieces"])
            host["rc"] = (host["rc"] * n + rc) / (n + 1)
            host["r0"] = min(host["r0"], r0)
            host["r1"] = max(host["r1"], r1)
            if a <= host["b"]:
                host["pieces"][-1] = (host["pieces"][-1][0], max(b, host["b"]))
            else:
                host["pieces"].append((a, b))
            host["b"] = max(host["b"], b)
        # Lines whose end is far behind the sweep cannot grow any more.
        open_lines = [L for L in open_lines if L["b"] >= a - gap]
    return lines


def _join_segments(segs: List[Dict[str, Any]], gap: int
                   ) -> List[Dict[str, Any]]:
    """Long solid stretches of one rule joined across breaks <= ``gap``."""
    segs = sorted(segs, key=lambda L: (L["a"], L["rc"]))
    out: List[Dict[str, Any]] = []
    for L in segs:
        host = None
        for M in out:
            if abs(M["rc"] - L["rc"]) <= 1.5 and L["a"] - M["b"] <= gap                     and L["a"] >= M["a"] - 1:
                host = M
                break
        if host is None:
            out.append({k: (list(v) if k == "pieces" else v)
                        for k, v in L.items()})
        else:
            w0 = host["b"] - host["a"]
            w1 = L["b"] - L["a"]
            host["rc"] = (host["rc"] * w0 + L["rc"] * w1) / max(1, w0 + w1)
            host["r0"] = min(host["r0"], L["r0"])
            host["r1"] = max(host["r1"], L["r1"])
            host["pieces"].extend(L["pieces"])
            host["b"] = max(host["b"], L["b"])
    return out


def find_lines(raster: Raster, *, orientation: str = "both",
               min_len_pt: float = 18.0, max_thick_pt: float = 3.0,
               gap_pt: float = 1.2, join_gap_pt: float = 6.0,
               piece_pt: float = 1.2, skew: Optional[float] = None,
               mask=None) -> Tuple[List[RasterLine], float]:
    """Ruled lines in ``raster``: ``(lines, skew)``.

    ``skew`` is the slope dy/dx of the page's horizontal rules (measured when
    not given, then refined from the long rules found). Lines shorter than
    ``min_len_pt`` or thicker than ``max_thick_pt`` are left out; collinear
    pieces up to ``join_gap_pt`` apart are one line, solid or dashed.
    """
    np = _np()
    m = raster.line_mask if mask is None else mask
    z = raster.z
    if skew is None:
        skew = measure_skew(raster.mask if mask is None else mask)
    kw = dict(min_len_px=max(3, int(round(min_len_pt * z))),
              max_thick_px=max(2, int(round(max_thick_pt * z))),
              gap_px=max(1, int(round(gap_pt * z))),
              join_gap_px=max(2, int(round(join_gap_pt * z))),
              piece_px=max(2, int(round(piece_pt * z))))
    inkw = raster.inkw
    out: List[RasterLine] = []
    if orientation in ("both", "h"):
        for L in _lines_along_rows(m, inkw, skew, z, **kw):
            # row = a + b*col in continuous pixels -> y = A + B*x in points
            B = L["b"]
            A = raster.y0 + L["a"] / z - B * raster.x0
            out.append(RasterLine("h", a=A, b=B,
                                  lo=raster.x0 + L["lo"] / z,
                                  hi=raster.x0 + L["hi"] / z,
                                  thickness_pt=L["thickness"] / z,
                                  coverage=L["coverage"], dashed=L["dashed"],
                                  pieces=L["pieces"], fit_pt=L["fit"] / z,
                                  n_cols=L["n"]))
    if orientation in ("both", "v"):
        mt = np.ascontiguousarray(m.T)
        it = np.ascontiguousarray(inkw.T)
        for L in _lines_along_rows(mt, it, -skew, z, **kw):
            # transposed: col_img = a + b*row_img -> x = A + B*y
            B = L["b"]
            A = raster.x0 + L["a"] / z - B * raster.y0
            out.append(RasterLine("v", a=A, b=B,
                                  lo=raster.y0 + L["lo"] / z,
                                  hi=raster.y0 + L["hi"] / z,
                                  thickness_pt=L["thickness"] / z,
                                  coverage=L["coverage"], dashed=L["dashed"],
                                  pieces=L["pieces"], fit_pt=L["fit"] / z,
                                  n_cols=L["n"]))
    out = _merge_doubles(out)
    # Refine the skew from the long solid rules themselves.
    width_pt = raster.shape[1] / z
    long_h = [(L.length, L.b) for L in out if L.orientation == "h"
              and not L.dashed and L.length >= 0.2 * width_pt]
    if long_h:
        long_h.sort(key=lambda t: t[1])
        total = sum(t[0] for t in long_h)
        acc = 0.0
        for ln, b in long_h:
            acc += ln
            if acc >= total / 2.0:
                skew = b
                break
    return out, float(skew)


def _merge_doubles(lines: List[RasterLine]) -> List[RasterLine]:
    """One rule found twice (a thick or noisy stroke split into parallel
    strands under a point apart) is one rule: the longer strand stays."""
    out: List[RasterLine] = []
    for L in sorted(lines, key=lambda L: -L.length):
        dup = False
        for M in out:
            if M.orientation != L.orientation or M.dashed != L.dashed:
                continue
            ov = min(L.hi, M.hi) - max(L.lo, M.lo)
            if ov < 0.5 * L.length:
                continue
            t = min(max(L.lo, M.lo) + ov / 2.0, L.hi)
            if abs(L.at(t) - M.at(t)) <= max(1.0, 0.6 * M.thickness_pt):
                dup = True
                break
        if not dup:
            out.append(L)
    return out


def erase_lines(raster: Raster, lines: Sequence[RasterLine], mask=None,
                pad_px: float = 1.5):
    """A copy of the ink mask with the given lines' pixels removed."""
    np = _np()
    m = (raster.mask if mask is None else mask).copy()
    h, w = m.shape
    z = raster.z
    for L in lines:
        half = L.thickness_pt * z / 2.0 + pad_px
        if L.orientation == "h":
            c0 = max(0, int(math.floor((L.lo - raster.x0) * z)) - 1)
            c1 = min(w, int(math.ceil((L.hi - raster.x0) * z)) + 1)
            if c1 <= c0:
                continue
            cols = np.arange(c0, c1)
            xs = raster.x0 + (cols + 0.5) / z
            rc = (L.a + L.b * xs - raster.y0) * z
            r0 = np.clip(np.floor(rc - half).astype(np.int64), 0, h)
            r1 = np.clip(np.ceil(rc + half).astype(np.int64), 0, h)
            for c, a, b in zip(cols, r0, r1):
                m[a:b, c] = False
        else:
            r0_ = max(0, int(math.floor((L.lo - raster.y0) * z)) - 1)
            r1_ = min(h, int(math.ceil((L.hi - raster.y0) * z)) + 1)
            if r1_ <= r0_:
                continue
            rows = np.arange(r0_, r1_)
            ys = raster.y0 + (rows + 0.5) / z
            cc = (L.a + L.b * ys - raster.x0) * z
            c0 = np.clip(np.floor(cc - half).astype(np.int64), 0, w)
            c1 = np.clip(np.ceil(cc + half).astype(np.int64), 0, w)
            for r, a, b in zip(rows, c0, c1):
                m[r, a:b] = False
    return m


# ---------------------------------------------------------------------------
# Blobs
# ---------------------------------------------------------------------------

@dataclass
class Blob:
    """A connected patch of ink, in page points."""
    bbox: BBox
    n_px: int
    centroid: Point

    @property
    def width(self) -> float:
        return self.bbox[2] - self.bbox[0]

    @property
    def height(self) -> float:
        return self.bbox[3] - self.bbox[1]

    @property
    def centre(self) -> Point:
        return ((self.bbox[0] + self.bbox[2]) / 2.0,
                (self.bbox[1] + self.bbox[3]) / 2.0)

    def to_dict(self) -> Dict[str, Any]:
        return {"bbox": [round(v, 2) for v in self.bbox],
                "centroid": [round(v, 2) for v in self.centroid]}


def _component_slices(mask, max_components: int = 20000):
    """Run-length union-find labelling (8-connected).

    Returns a list of ``(r0, c0, r1, c1, n, sum_r, sum_c)`` per component, in
    pixel indexes (``r1`` / ``c1`` exclusive).
    """
    np = _np()
    h, w = mask.shape
    padded = np.zeros((h, w + 2), dtype=np.int8)
    padded[:, 1:-1] = mask
    d = np.diff(padded, axis=1)
    sr, sc = np.nonzero(d == 1)
    _er, ec = np.nonzero(d == -1)
    n = len(sr)
    if n == 0:
        return []
    parent = list(range(n))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    # Index the runs by row.
    row_start = np.searchsorted(sr, np.arange(h + 1))
    srl = sr.tolist()
    scl = sc.tolist()
    ecl = ec.tolist()
    for r in range(1, h):
        i0, i1 = int(row_start[r]), int(row_start[r + 1])
        if i0 == i1:
            continue
        j0, j1 = int(row_start[r - 1]), int(row_start[r])
        if j0 == j1:
            continue
        j = j0
        for i in range(i0, i1):
            a, b = scl[i], ecl[i]
            # previous-row runs that touch [a-1, b+1) (8-connectivity)
            while j < j1 and ecl[j] < a:
                j += 1
            k = j
            while k < j1 and scl[k] <= b:
                ri, rk = find(i), find(k)
                if ri != rk:
                    parent[ri] = rk
                k += 1
    roots: Dict[int, List[float]] = {}
    for i in range(n):
        rt = find(i)
        r = srl[i]
        a, b = scl[i], ecl[i]
        ln = b - a
        st = roots.get(rt)
        if st is None:
            roots[rt] = [r, a, r + 1, b, ln, (r + 0.5) * ln,
                         (a + b) / 2.0 * ln]
        else:
            st[0] = min(st[0], r)
            st[1] = min(st[1], a)
            st[2] = max(st[2], r + 1)
            st[3] = max(st[3], b)
            st[4] += ln
            st[5] += (r + 0.5) * ln
            st[6] += (a + b) / 2.0 * ln
        if len(roots) > max_components:
            break
    return list(roots.values())


def components(raster: Raster, mask=None, region: Optional[BBox] = None,
               min_px: int = 4) -> List[Blob]:
    """Connected ink components (in ``region``, page points), as blobs."""
    m = raster.mask if mask is None else mask
    c0, r0, c1, r1 = (0, 0, m.shape[1], m.shape[0]) if region is None \
        else raster.box_to_pixel(region)
    sub = m[r0:r1, c0:c1]
    out = []
    for (a, b, c, d, n, sr_, sc_) in _component_slices(sub):
        if n < min_px:
            continue
        box = raster.box_to_page(c0 + b, r0 + a, c0 + d, r0 + c)
        cen = raster.to_page(c0 + sc_ / n, r0 + sr_ / n)
        out.append(Blob(bbox=box, n_px=int(n), centroid=cen))
    return out


def text_blobs(raster: Raster, mask=None, region: Optional[BBox] = None,
               merge_pt: float = 2.5, min_px: int = 6) -> List[Blob]:
    """Blobs with the characters of one printed label merged into one.

    The ink is closed along rows by ``merge_pt`` (the space between the
    characters of a number, not between two numbers), labelled, and each
    merged blob's box and ink centroid are taken from the ORIGINAL pixels.
    """
    np = _np()
    m = raster.mask if mask is None else mask
    c0, r0, c1, r1 = (0, 0, m.shape[1], m.shape[0]) if region is None \
        else raster.box_to_pixel(region)
    sub = m[r0:r1, c0:c1]
    if sub.size == 0 or not sub.any():
        return []
    gap = max(1, int(round(merge_pt * raster.z)))
    closed = close_gaps(sub, gap)
    out = []
    for (a, b, c, d, n, _sr, _sc) in _component_slices(closed):
        patch = sub[a:c, b:d]
        k = int(patch.sum())
        if k < min_px:
            continue
        ys, xs = np.nonzero(patch)
        box = raster.box_to_page(c0 + b + xs.min(), r0 + a + ys.min(),
                                 c0 + b + xs.max() + 1, r0 + a + ys.max() + 1)
        cen = raster.to_page(c0 + b + xs.mean() + 0.5, r0 + a + ys.mean() + 0.5)
        out.append(Blob(bbox=box, n_px=k, centroid=cen))
    return out


def solid_blobs(raster: Raster, mask=None, region: Optional[BBox] = None,
                core_pt: float = 1.6, min_core_px: int = 1,
                max_aspect: Optional[float] = None) -> List[Blob]:
    """Filled marks: ink that survives a square erosion of ``core_pt``.

    Lines (a few pixels thick) and lettering vanish; a filled square or
    circle marker remains. The blob's box is grown back by the erosion.
    ``max_aspect`` drops a core longer than that against its width: what
    survives along a thick stroke, or two markers joined by one, is not a
    marker.
    """
    np = _np()
    m = raster.mask if mask is None else mask
    c0, r0, c1, r1 = (0, 0, m.shape[1], m.shape[0]) if region is None \
        else raster.box_to_pixel(region)
    sub = m[r0:r1, c0:c1].astype(np.int32)
    k = max(3, int(round(core_pt * raster.z)))
    if sub.shape[0] < k or sub.shape[1] < k:
        return []
    ii = np.zeros((sub.shape[0] + 1, sub.shape[1] + 1), dtype=np.int32)
    ii[1:, 1:] = sub.cumsum(0).cumsum(1)
    box = ii[k:, k:] - ii[:-k, k:] - ii[k:, :-k] + ii[:-k, :-k]
    core = box == k * k
    out = []
    half = k / 2.0
    for (a, b, c, d, n, sr_, sc_) in _component_slices(core):
        if n < min_core_px:
            continue
        if max_aspect is not None:
            hh, ww = c - a, d - b
            if max(hh, ww) > max_aspect * max(1, min(hh, ww)) + 1:
                continue
        # core pixel (i, j) is the k x k window starting at (i, j)
        bx = raster.box_to_page(c0 + b, r0 + a, c0 + d + k - 1, r0 + c + k - 1)
        # The core's mean, taken round its median: a stroke leaving the mark
        # at one side adds a thin tail to the core that would drag a plain
        # mean, and a plain median is quantised to the pixel.
        ys, xs = np.nonzero(core[a:c, b:d])
        if len(ys):
            my, mx = np.median(ys), np.median(xs)
            r = max(2.0, math.ceil(min(c - a, d - b) / 2.0))
            sel = (np.abs(ys - my) <= r) & (np.abs(xs - mx) <= r)
            mr = float(ys[sel].mean()) + a + 0.5
            mc = float(xs[sel].mean()) + b + 0.5
        else:
            mr, mc = sr_ / n, sc_ / n
        cen = raster.to_page(c0 + mc + half - 0.5, r0 + mr + half - 0.5)
        out.append(Blob(bbox=bx, n_px=int(n), centroid=cen))
    return out


def column_runs(raster: Raster, x: float, y0: float, y1: float, mask=None,
                width_px: int = 1) -> List[Tuple[float, float]]:
    """Ink runs down the column at page ``x`` between ``y0`` and ``y1``.

    Returns ``(top, bottom)`` page y per run (``width_px`` columns OR-ed).
    """
    np = _np()
    m = raster.mask if mask is None else mask
    h, w = m.shape
    c, _ = raster.to_pixel(x, y0)
    c = int(math.floor(c))
    ca, cb = max(0, c - width_px // 2), min(w, c - width_px // 2 + width_px)
    if cb <= ca:
        return []
    _, ra = raster.to_pixel(x, min(y0, y1))
    _, rb = raster.to_pixel(x, max(y0, y1))
    ra, rb = max(0, int(math.floor(ra))), min(h, int(math.ceil(rb)))
    if rb <= ra:
        return []
    col = m[ra:rb, ca:cb].any(axis=1).astype(np.int8)
    d = np.diff(np.concatenate(([0], col, [0])))
    s = np.nonzero(d == 1)[0]
    e = np.nonzero(d == -1)[0]
    return [(raster.y0 + (ra + a) / raster.z, raster.y0 + (ra + b) / raster.z)
            for a, b in zip(s, e)]
