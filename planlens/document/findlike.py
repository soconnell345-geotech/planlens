"""Find every copy of one mark — a tag, a code, a symbol — across a document.

Why this exists. A reviewer asked a real 85-sheet security set (2026-09-25)
where the "GCE" penetrations are. The sheets are AutoCAD plots: the lettering
is drawn as strokes, so there is no text layer to search, and at 0.06 in it
arrives 4 px tall in a whole-sheet image — the model read "GCE" as "QCE" and
found none of 34. Lettering drawn by CAD is drawn the SAME way every time,
though, so once one copy is located, every other copy is an image match away:

1. **The example.** A box around ONE copy, on any page (a legend row is fine).
   Its ink is cut out at the search dpi and trimmed to the mark.
2. **The search.** Every page is rendered in grey and the example is matched
   (zero-mean normalised cross-correlation, OpenCV's ``TM_CCOEFF_NORMED``) at
   a ladder of scales and at 0 / 90 / 180 / 270 degrees — a legend's
   lettering is often larger than the plan's, and tags turn with the walls
   they sit on. Overlapping matches are merged.
3. **What each hit IS.** From the drawing's own geometry, never guessed:
   *legend* — the hit sits in a ruled table row, or at the same place on most
   same-size pages (a legend, a title block, general notes); *callout* — a
   drawn path has a vertex at the tag and reaches away from it (a leader), and
   ``points_to`` is that path's far vertex, the place the tag is about;
   *unanchored* — on the plan, with no leader found.
4. **Look-alikes.** Correlation cannot tell GCE from GCG on its own (their
   first two letters are the same strokes), so hits are CANDIDATES.
   :func:`like_sheets` cuts each one out, turns it upright, enlarges it and
   numbers it on contact sheets, for a vision model to read at a size it
   cannot misread — the host confirms or rejects each number.

**Two matchers, one answer.** Where OpenCV loads, the correlation is its
``matchTemplate``. Where it does not, a numpy matcher computes the same
quantity — and that is most of the places this runs: on a host whose OpenSSL
enforces FIPS mode, loading OpenCV's wheel aborts the interpreter (Palantir
Foundry and Funhouse/Databricks, 2026-10; see :mod:`planlens.opencv`). The
numpy matcher cuts the page into square tiles that overlap by a template and
a peak window, correlates each tile with the ZERO-MEAN example by FFT (which
is the numerator of the normalised score outright — the window's mean drops
out because the template sums to zero), takes every window's sum and sum of
squares from the tile's integral images (exact: the ink is integer), scores a
window with no contrast 0 as OpenCV does, and keeps a candidate only where it
is the top of its hill — OpenCV's peak rule, applied inside the tile because
the overlap holds the whole window. The example's scales come from a port of
OpenCV's area / bilinear resize, and the contact sheets are drawn with numpy
and PyMuPDF. Measured on the tag fixtures (2026-10-07; 3 and 24 sheets of
11x17, and a 34x22 in sheet): the same hits, scores within 4e-5 (only hits
whose scores tie exactly may come back in another order), at 1.0-1.35 times
OpenCV's time a page one page at a time and about 1.6 times with four pages
searched in parallel. ``PLANLENS_FINDLIKE_BACKEND`` = ``auto`` | ``numpy`` |
``opencv``, or ``backend=``, chooses; :func:`available` says whether a search
can run here at all.

Nothing here calls a model or downloads anything; it needs only numpy and
PyMuPDF, and uses OpenCV where it loads.
"""

from __future__ import annotations

import math
import os
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]
Point = Tuple[float, float]

#: Default search resolution. At 150 dpi a 0.06 in tag is ~9 px tall — enough
#: to correlate, and an 11x17 sheet is 4 MP. A smaller example raises it.
DEFAULT_DPI = 150.0

#: The example's ink is rendered at least this many pixels tall.
MIN_EXAMPLE_PX = 9.0

#: Ceiling on one page's search raster, in megapixels.
MAX_PAGE_MEGAPIXELS = 30.0

#: Scales of the example searched for: 0.5x to ~2x in 12 % steps.
DEFAULT_SCALES: Tuple[float, ...] = tuple(round(0.5 * 1.12 ** i, 3)
                                          for i in range(13))

#: Normalised correlation a candidate must reach. Measured on stroked CAD
#: tags: true copies score 0.7-1.0, look-alikes sharing two of three letters
#: up to ~0.85, unrelated lettering under 0.55.
DEFAULT_THRESHOLD = 0.55

#: Rotations searched, degrees counter-clockwise.
DEFAULT_ROTATIONS: Tuple[int, ...] = (0, 90, 180, 270)

CONTEXTS = ("callout", "legend", "unanchored")

#: Chooses the matcher: ``auto`` (the default: OpenCV where it loads, numpy
#: otherwise), ``numpy`` or ``opencv``. A ``backend=`` argument wins over it.
BACKEND_ENV = "PLANLENS_FINDLIKE_BACKEND"

#: The matchers, the faster first.
BACKENDS: Tuple[str, ...] = ("opencv", "numpy")

_DBL_EPSILON = 2.220446049250313e-16

#: FFT tile edges for the numpy matcher: 2^a x {1, 3, 5, 9, 15}, sizes
#: pocketfft transforms fastest.
_FFT_SIZES: Tuple[int, ...] = tuple(sorted({2 ** a * m for a in range(4, 14)
                                            for m in (1, 3, 5, 9, 15)}))


@dataclass
class LikeHit:
    """One place a copy of the example was found."""
    page: int
    bbox: BBox                  # displayed frame, points, top-left origin
    score: float
    rotation: int               # degrees CCW relative to the example
    scale: float                # size relative to the example
    context: str = "unanchored"
    points_to: Optional[Point] = None
    leader: Optional[List[Point]] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def center(self) -> Point:
        x0, y0, x1, y1 = self.bbox
        return ((x0 + x1) / 2.0, (y0 + y1) / 2.0)

    def to_dict(self) -> Dict[str, Any]:
        r = lambda v: round(float(v), 1)          # noqa: E731
        d = {"page": self.page, "bbox": [r(v) for v in self.bbox],
             "score": round(self.score, 3), "rotation": self.rotation,
             "scale": round(self.scale, 2), "context": self.context}
        if self.points_to is not None:
            d["points_to"] = [r(v) for v in self.points_to]
        if self.evidence:
            d["evidence"] = dict(self.evidence)
        return d


# -- rendering -------------------------------------------------------------------

def _ink(page, dpi: float, clip=None):
    """The page (or ``clip``) as an ink image: 0 = paper, 255 = black."""
    import fitz
    import numpy as np
    z = dpi / 72.0
    pix = page.get_pixmap(matrix=fitz.Matrix(z, z), colorspace=fitz.csGRAY,
                          clip=fitz.Rect(clip) if clip is not None else None,
                          alpha=False)
    img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width]
    return 255 - img


def _example(page, bbox: BBox, dpi: float):
    """The example's ink, trimmed to the mark, and its trimmed box in points."""
    import numpy as np
    ink = _ink(page, dpi, clip=bbox)
    mask = ink > 64
    if not mask.any():
        raise ValueError("the example box holds no ink — box the mark itself")
    ys, xs = np.where(mask)
    pad = 1
    y0, y1 = max(0, ys.min() - pad), min(ink.shape[0], ys.max() + pad + 1)
    x0, x1 = max(0, xs.min() - pad), min(ink.shape[1], xs.max() + pad + 1)
    z = dpi / 72.0
    tb = (bbox[0] + x0 / z, bbox[1] + y0 / z, bbox[0] + x1 / z, bbox[1] + y1 / z)
    tpl = np.ascontiguousarray(ink[y0:y1, x0:x1])
    if tpl.min() == tpl.max():
        # A box inside solid ink: a template with no contrast correlates as 1
        # with every window on every page.
        raise ValueError("the example box holds solid ink and no paper — box "
                         "the mark itself, with a little paper round it")
    return tpl, tb


def _page_dpi(page, dpi: float) -> float:
    w, h = page.rect.width, page.rect.height
    px = (w * dpi / 72.0) * (h * dpi / 72.0)
    cap = MAX_PAGE_MEGAPIXELS * 1e6
    return dpi if px <= cap else dpi * math.sqrt(cap / px)


# -- the matcher: OpenCV where it loads, numpy anywhere ---------------------------

def _wanted(backend: Optional[str]) -> str:
    want = backend if backend is not None else os.environ.get(BACKEND_ENV, "")
    want = (want or "auto").strip().lower()
    if want not in ("auto",) + BACKENDS:
        raise ValueError(f"the find_like backend must be auto, numpy or opencv, "
                         f"not {want!r} (backend= or {BACKEND_ENV})")
    return want


def resolve_backend(backend: Optional[str] = None) -> str:
    """The matcher a search will use, ``"opencv"`` or ``"numpy"``:
    ``backend`` if given, else :data:`BACKEND_ENV`, else ``auto`` — OpenCV
    where it loads (test-loaded in a child process first, see
    :mod:`planlens.opencv`), numpy otherwise. Asking for ``opencv`` where it
    cannot load raises ``ImportError`` with the reason."""
    want = _wanted(backend)
    if want == "numpy":
        return "numpy"
    from planlens import opencv
    ok, why = opencv.available()
    if ok:
        return "opencv"
    if want == "opencv":
        raise ImportError(f"{why} — the numpy matcher needs no OpenCV "
                          f"({BACKEND_ENV}=numpy or auto)")
    return "numpy"


def available() -> Tuple[bool, str]:
    """``(True, "")`` when :func:`find_like` can run here — with OpenCV where
    it loads, with the numpy matcher anywhere else — or ``(False, reason)``:
    only when :data:`BACKEND_ENV` asks for OpenCV by name and it cannot load,
    or names no matcher. Cheap: unless OpenCV is asked for by name it is not
    test-loaded here (the first search does that)."""
    try:
        want = _wanted(None)
    except ValueError as exc:
        return False, str(exc)
    if want == "opencv":
        from planlens import opencv
        ok, why = opencv.available()
        return (True, "") if ok else (False, f"{BACKEND_ENV}=opencv, but {why}")
    try:
        import fitz  # noqa: F401
        import numpy.fft  # noqa: F401
    except ImportError as exc:                       # pragma: no cover
        return False, f"find_like needs numpy and PyMuPDF ({exc})"
    return True, ""


# -- scaling the example: cv2.resize, or a numpy port of it -----------------------

def _resize(img, f: float, backend: str):
    """``img`` scaled by ``f`` as ``cv2.resize(fx=f, fy=f)`` scales it: area
    averaging to shrink, bilinear to enlarge."""
    if backend == "opencv":
        from planlens.opencv import load
        cv2 = load()
        return cv2.resize(img, None, fx=f, fy=f, interpolation=(
            cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR))
    return _np_resize(img, f)


def _np_resize(img, f: float):
    """numpy port of ``cv2.resize(img, None, fx=f, fy=f)`` for a uint8 grey
    image, INTER_AREA when ``f < 1`` and INTER_LINEAR otherwise, so the numpy
    matcher scales the example as the OpenCV one does: the same output size
    (rounded half to even), a copy when that size is the input's, the area
    average pixel for pixel (float32 accumulation in OpenCV's order), the
    bilinear within one grey level on under 1 % of pixels (OpenCV rounds in
    its vector code in a way this does not chase)."""
    import numpy as np
    h, w = img.shape
    dh, dw = max(1, int(round(h * f))), max(1, int(round(w * f)))
    if (dh, dw) == (h, w):
        return img.copy()
    if f >= 1:
        return _np_linear(img, f, dh, dw)
    k = int(round(1.0 / f))
    if k >= 2 and abs(1.0 / f - k) < _DBL_EPSILON:
        return _np_area_whole(img, k, dh, dw)
    src = img.astype(np.float32)
    out = _apply_taps(src, _area_taps(w, dw, f), axis=1)
    out = _apply_taps(out, _area_taps(h, dh, f), axis=0)
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


def _area_taps(n_src: int, n_dst: int, f: float):
    """OpenCV's area weights (``computeResizeAreaTab``): destination pixel
    ``d`` covers source ``[d/f, (d+1)/f)``, each source pixel weighted by its
    overlap, over the part of the cell inside the image."""
    scale = 1.0 / f
    taps = []
    for d in range(n_dst):
        a = d * scale
        b = a + scale
        cell = min(scale, n_src - a)
        s1, s2 = math.ceil(a), math.floor(b)
        s2 = min(s2, n_src - 1)
        s1 = min(s1, s2)
        row = []
        if s1 - a > 1e-3:
            row.append((s1 - 1, (s1 - a) / cell))
        row.extend((s, 1.0 / cell) for s in range(s1, s2))
        if b - s2 > 1e-3:
            row.append((s2, min(b - s2, 1.0, cell) / cell))
        taps.append(row)
    return taps


def _apply_taps(src, taps, axis: int):
    """Weighted sums of source pixels along ``axis``, in float32 and in tap
    order (OpenCV's accumulation, so the rounding comes out the same)."""
    import numpy as np
    n_dst, kmax = len(taps), max(len(t) for t in taps)
    idx = np.zeros((n_dst, kmax), np.intp)
    wt = np.zeros((n_dst, kmax), np.float32)
    for d, row in enumerate(taps):
        for j, (s, a) in enumerate(row):
            idx[d, j], wt[d, j] = s, a
    a = src if axis == 1 else src.T
    out = np.zeros((a.shape[0], n_dst), np.float32)
    for j in range(kmax):
        out += wt[:, j] * a[:, idx[:, j]]
    return out if axis == 1 else np.ascontiguousarray(out.T)


def _np_area_whole(img, k: int, dh: int, dw: int):
    """OpenCV's area shrink by a whole factor ``k`` (``resizeAreaFast``):
    the mean of each k x k block, over the pixels present where a block runs
    off the image."""
    import numpy as np
    h, w = img.shape
    hh, ww = min(h, dh * k), min(w, dw * k)
    blk = np.zeros((dh * k, dw * k), np.int64)
    cnt = np.zeros_like(blk)
    blk[:hh, :ww] = img[:hh, :ww]
    cnt[:hh, :ww] = 1
    s = blk.reshape(dh, k, dw, k).sum(axis=(1, 3))
    n = cnt.reshape(dh, k, dw, k).sum(axis=(1, 3))
    out = np.zeros((dh, dw))
    full = n == k * k
    if k == 2:
        out[full] = (s[full] + 2) >> 2               # OpenCV's vector code
    else:
        out[full] = np.rint(s[full] * np.float32(1.0 / (k * k)))
    part = ~full & (n > 0)
    out[part] = np.rint(s[part].astype(np.float32) / n[part])
    return out.astype(np.uint8)


def _np_linear(img, f: float, dh: int, dw: int):
    """OpenCV's 8-bit bilinear enlargement: 11-bit fixed-point weights at
    half-pixel centres, edges clamped, a horizontal pass then its vertical
    one (``((a>>4)*b0 >> 16) + ((c>>4)*b1 >> 16) + 2 >> 2``)."""
    import numpy as np
    h, w = img.shape

    def taps(n_src, n_dst):
        scale = 1.0 / f
        s = np.empty(n_dst, np.intp)
        w0 = np.empty(n_dst, np.int64)
        w1 = np.empty(n_dst, np.int64)
        for d in range(n_dst):
            fx = np.float32((d + 0.5) * scale - 0.5)
            sx = math.floor(fx)
            fx = np.float32(fx - np.float32(sx))
            if sx < 0:
                sx, fx = 0, np.float32(0.0)
            if sx >= n_src - 1:
                sx, fx = n_src - 1, np.float32(0.0)
            s[d] = sx
            w0[d] = int(np.rint((np.float32(1.0) - fx) * np.float32(2048.0)))
            w1[d] = int(np.rint(fx * np.float32(2048.0)))
        return s, np.minimum(s + 1, n_src - 1), w0, w1

    x0, x1, a0, a1 = taps(w, dw)
    y0, y1, b0, b1 = taps(h, dh)
    src = img.astype(np.int64)
    row = src[:, x0] * a0 + src[:, x1] * a1          # scale 2048
    top, bot = row[y0] >> 4, row[y1] >> 4
    out = ((top * b0[:, None]) >> 16) + ((bot * b1[:, None]) >> 16)
    return np.clip((out + 2) >> 2, 0, 255).astype(np.uint8)


# -- matching -------------------------------------------------------------------

def _match(ink, tpl, dpi: float, scales, rotations, threshold: float,
           max_hits: int, backend: str = "opencv"
           ) -> List[Tuple[float, BBox, int, float]]:
    import numpy as np
    peaks_of = _peaks_opencv if backend == "opencv" else _peaks_numpy
    z = dpi / 72.0
    found = []
    for sc in scales:
        t = tpl if sc == 1.0 else _resize(tpl, sc, backend)
        if t.shape[0] < 5 or t.shape[1] < 5:
            continue
        rots, turns = [], []
        for rot in rotations:
            k = (rot // 90) % 4
            th, tw = t.shape if k % 2 == 0 else t.shape[::-1]
            if th >= ink.shape[0] or tw >= ink.shape[1]:
                continue
            rots.append(rot)
            turns.append(k)
        if not turns:
            continue
        for rot, k, (ys, xs, vs) in zip(rots, turns,
                                        peaks_of(ink, t, turns, threshold)):
            if len(ys) > 4 * max_hits:           # a plain example matching
                keep = np.argsort(vs)[-4 * max_hits:]            # everything
                ys, xs, vs = ys[keep], xs[keep], vs[keep]
            th, tw = t.shape if k % 2 == 0 else t.shape[::-1]
            for y, x, v in zip(ys.tolist(), xs.tolist(), vs.tolist()):
                found.append((float(v),
                              (x / z, y / z, (x + tw) / z, (y + th) / z),
                              rot, float(sc)))
    found.sort(key=lambda f: -f[0])
    kept: List[Tuple[float, BBox, int, float]] = []
    for f in found:
        # One mark, one hit: a weaker match overlapping a kept one by a
        # third of the smaller box is the same mark seen at another scale,
        # rotation or offset (a letter's width to one side); a MUCH weaker one
        # touching it at all is a fragment of it (the example shrunk and
        # turned, matching part of a letter).
        if all(_overlap(f[1], k[1]) < 0.33
               and not (_overlap(f[1], k[1]) > 0 and f[0] < k[0] - 0.2)
               for k in kept):
            kept.append(f)
            if len(kept) >= max_hits:
                break
    return kept


def _overlap(a: BBox, b: BBox) -> float:
    """Intersection over the SMALLER box's area."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (iw * ih) / small if small > 0 else 0.0


def _peak_radii(th: int, tw: int) -> Tuple[int, int]:
    """Half-sizes of the peak window for a th x tw template: an odd window
    about half the template's size, at least 3 x 3."""
    return max(3, (th // 2) | 1) // 2, max(3, (tw // 2) | 1) // 2


def _peaks_opencv(ink, t, turns, threshold: float):
    """Per quarter-turn count in ``turns``: the hill tops of OpenCV's
    ``TM_CCOEFF_NORMED`` scores of ``t`` turned that way, ``(ys, xs,
    scores)`` row by row."""
    import numpy as np
    from planlens.opencv import load
    cv2 = load()
    out = []
    for k in turns:
        tr = np.ascontiguousarray(np.rot90(t, k=k))
        res = cv2.matchTemplate(ink, tr, cv2.TM_CCOEFF_NORMED)
        res[~np.isfinite(res)] = 0
        # Peaks only: a mark gives a hill of above-threshold scores, and a
        # dense sheet at a low threshold gives thousands of such pixels.
        # Keeping each hill's top (the maximum over a window half the
        # template's size) leaves one candidate per mark per pass.
        size = (max(3, (tr.shape[1] // 2) | 1), max(3, (tr.shape[0] // 2) | 1))
        peaks = (res >= threshold) & (res >= cv2.dilate(
            res, cv2.getStructuringElement(cv2.MORPH_RECT, size)))
        ys, xs = np.where(peaks)
        out.append((ys, xs, res[ys, xs]))
    return out


def _peaks_numpy(ink, t, turns, threshold: float):
    """What :func:`_peaks_opencv` returns, computed with numpy alone.

    The score of a window W against the template T is OpenCV's
    ``TM_CCOEFF_NORMED``: sum((T - mean T)(W - mean W)) over
    sqrt(sum (T - mean T)^2 * sum (W - mean W)^2). Correlating with the
    zero-mean template gives the numerator directly (the window mean drops out
    because the template sums to zero); the window sums come from integral
    images. The page goes through in square FFT tiles that overlap by the
    template plus the peak window, so each tile computes the scores of the
    outputs it owns AND of every position their peak windows reach, and the
    hill-top test needs nothing from another tile. A half turn is the
    quarter-turn template's spectrum used for convolution rather than
    correlation, so two spectra serve all four turns."""
    import numpy as np
    H, W = ink.shape
    dims = [t.shape if k % 2 == 0 else t.shape[::-1] for k in turns]
    T = t.astype(np.float64)
    if T.var() < _DBL_EPSILON:
        # No contrast: OpenCV scores every window 1, so every one is a peak.
        out = []
        for th, tw in dims:
            eh, ew = H - th + 1, W - tw + 1
            ys, xs = np.divmod(np.arange(eh * ew), ew)
            out.append((ys, xs, np.ones(eh * ew)))
        return out
    tz = T - T.mean()
    tn = math.sqrt(float((tz * tz).sum()))
    rad = [_peak_radii(th, tw) for th, tw in dims]
    hy, hx = max(r[0] for r in rad), max(r[1] for r in rad)
    need = max(max(d) for d in dims) - 1 + 2 * max(hy, hx)
    B = _fft_size(max(128, min(6 * need, need + 1024)))
    B = min(B, _fft_size(max(H, W) + need))       # a small page: one tile
    S = B - need                                  # outputs a tile owns, per side
    spec = {p: np.fft.rfft2(np.rot90(tz, p), s=(B, B))
            for p in sorted({k % 2 for k in turns})}
    cspec = {p: np.conj(spec[p]) for p in spec if p in turns}
    found: List[List[Any]] = [[[], [], []] for _ in turns]
    tile = np.zeros((B, B))
    c1 = np.zeros((B + 1, B + 1))                 # integral of the tile
    c2 = np.zeros((B + 1, B + 1))                 # ... and of its square
    prod = np.empty((B, B // 2 + 1), complex)
    for oy in range(0, H, S):
        iy = oy - hy                              # the tile's first row
        for ox in range(0, W, S):
            ix = ox - hx
            ya, yb, xa, xb = max(iy, 0), min(iy + B, H), max(ix, 0), min(ix + B, W)
            if not ink[ya:yb, xa:xb].any():
                continue                          # blank paper: all score 0
            tile[:] = 0
            tile[ya - iy:yb - iy, xa - ix:xb - ix] = ink[ya:yb, xa:xb]
            F = np.fft.rfft2(tile)
            np.cumsum(tile, 0, out=c1[1:, 1:])
            np.cumsum(c1[1:, 1:], 1, out=c1[1:, 1:])
            np.multiply(tile, tile, out=tile)
            np.cumsum(tile, 0, out=c2[1:, 1:])
            np.cumsum(c2[1:, 1:], 1, out=c2[1:, 1:])
            norms: Dict[int, Any] = {}
            for j, k in enumerate(turns):
                th, tw = dims[j]
                eh, ew = H - th + 1, W - tw + 1   # where a window fits
                oy1, ox1 = min(oy + S, eh), min(ox + S, ew)
                if oy1 <= oy or ox1 <= ox:
                    continue                      # owns no valid output
                # the region scored: owned outputs plus the peak halo
                gy0, gy1 = max(oy - hy, 0), min(oy + S + hy, eh)
                gx0, gx1 = max(ox - hx, 0), min(ox + S + hx, ew)
                ly, lx, rh, rw = gy0 - iy, gx0 - ix, gy1 - gy0, gx1 - gx0
                if k % 2 not in norms:
                    den = _window_norms(c1, c2, ly, lx, rh, rw, th, tw, tn)
                    norms[k % 2] = (den, threshold * den if threshold > 0
                                    else None)
                den, lim = norms[k % 2]
                if k < 2:
                    np.multiply(F, cspec[k], out=prod)
                    num = np.fft.irfft2(prod, s=(B, B))[ly:ly + rh, lx:lx + rw]
                else:                             # half turn: convolution
                    np.multiply(F, spec[k - 2], out=prod)
                    num = np.fft.irfft2(prod, s=(B, B))[
                        ly + th - 1:ly + th - 1 + rh, lx + tw - 1:lx + tw - 1 + rw]
                own = (slice(oy - gy0, oy1 - gy0), slice(ox - gx0, ox1 - gx0))
                if threshold > 0:
                    cy, cx = np.nonzero(num[own] >= lim[own])
                else:
                    cy, cx = np.nonzero(_clamp(num[own] / den[own]) >= threshold)
                if not len(cy):
                    continue
                cy += oy - gy0
                cx += ox - gx0
                top, v = _hill_tops(num, den, cy, cx, *rad[j], threshold)
                found[j][0].append(cy[top] + gy0)
                found[j][1].append(cx[top] + gx0)
                found[j][2].append(v[top])
    out = []
    for ys, xs, vs in found:
        if not ys:
            out.append((np.zeros(0, np.intp), np.zeros(0, np.intp), np.zeros(0)))
            continue
        ys, xs, vs = np.concatenate(ys), np.concatenate(xs), np.concatenate(vs)
        order = np.lexsort((xs, ys))              # row by row, as np.where
        out.append((ys[order], xs[order], vs[order]))
    return out


def _fft_size(n: int) -> int:
    return next((s for s in _FFT_SIZES if s >= n), n)


def _window_norms(c1, c2, y0: int, x0: int, h: int, w: int, th: int, tw: int,
                  tn: float):
    """The score's denominator for every th x tw window whose corner is in
    rows ``y0..y0+h`` and columns ``x0..x0+w`` of the tile: ``tn`` times the
    root of the window's sum of squared deviations, and ``inf`` where the
    window is flat (OpenCV scores those 0)."""
    import numpy as np

    def box(c):
        s = c[y0 + th:y0 + th + h, x0 + tw:x0 + tw + w] - \
            c[y0:y0 + h, x0 + tw:x0 + tw + w]
        s -= c[y0 + th:y0 + th + h, x0:x0 + w]
        s += c[y0:y0 + h, x0:x0 + w]
        return s

    s1, d2 = box(c1), box(c2)
    s1 *= s1
    s1 *= 1.0 / (th * tw)
    d2 -= s1                                       # sum of (x - mean)^2
    # The ink is integer, so a window that is not flat has d2 >= 1 - 1/n,
    # far above this; OpenCV's rounding guard catches exactly the flat ones.
    flat = d2 < 0.25
    np.sqrt(d2, out=d2, where=~flat)
    d2 *= tn
    d2[flat] = np.inf
    return d2


def _clamp(r):
    """OpenCV's last step: a score a rounding hair beyond +-1 is +-1, one
    further beyond is 0."""
    import numpy as np
    a = np.abs(r)
    if not a.size or a.max() < 1.0:
        return r
    return np.where(a < 1.0, r, np.where(a < 1.125, np.sign(r), 0.0))


def _hill_tops(num, den, cy, cx, ry: int, rx: int, threshold: float):
    """For candidates ``(cy, cx)`` of a scored region: which score at least
    ``threshold`` and are the maximum of the (2ry+1) x (2rx+1) window round
    them (``cv2.dilate``'s peak rule; positions outside the region are
    outside the page's valid outputs, as its border is), and their scores."""
    import numpy as np
    rh, rw = num.shape
    m = len(cy)
    if m * (2 * ry + 1) * (2 * rx + 1) > 4 * rh * rw:
        # a plain example matching most windows: filter the whole region
        r = _clamp(num / den)
        v = r[cy, cx]
        return (v >= threshold) & (v >= _max_filter(r, ry, rx)[cy, cx]), v
    yy = cy[:, None, None] + np.arange(-ry, ry + 1)[None, :, None]
    xx = cx[:, None, None] + np.arange(-rx, rx + 1)[None, None, :]
    off = (yy < 0) | (yy >= rh) | (xx < 0) | (xx >= rw)
    yy, xx = np.clip(yy, 0, rh - 1), np.clip(xx, 0, rw - 1)
    r = _clamp(num[yy, xx] / den[yy, xx])
    r[off] = -np.inf
    v = r[:, ry, rx]
    return (v >= threshold) & (v >= r.reshape(m, -1).max(axis=1)), v


def _max_filter(a, ry: int, rx: int):
    """The maximum over the (2ry+1) x (2rx+1) window round each element,
    the window clipped to the array (``cv2.dilate`` with a rectangle)."""
    import numpy as np

    def rows(a, r):                       # sliding max down axis 0
        if r == 0:
            return a
        n, w = a.shape[0], 2 * r + 1
        m = np.pad(a, ((r, r), (0, 0)), constant_values=-np.inf)
        span = 1
        while 2 * span <= w:                     # m[i] = max(p[i:i+2span])
            m = np.maximum(m[:-span], m[span:])
            span *= 2
        if span < w:
            m = np.maximum(m[:len(m) - (w - span)], m[w - span:])
        return m[:n]

    return rows(rows(a, ry).T, rx).T


# -- what a hit is: the drawing's own geometry ----------------------------------

def _paths(page) -> List[List[Point]]:
    """Every drawn path's vertices, in the displayed frame."""
    import fitz
    m = page.rotation_matrix
    out = []
    for d in page.get_cdrawings():
        pts: List[Point] = []
        for it in d.get("items", ()):
            op = it[0]
            if op == "l":
                pts += [it[1], it[2]]
            elif op == "c":
                pts += [it[1], it[4]]
            elif op == "re":
                r = it[1]
                pts += [(r[0], r[1]), (r[2], r[1]), (r[2], r[3]), (r[0], r[3])]
            elif op == "qu":
                q = it[1]
                pts += [tuple(q[0]), tuple(q[1]), tuple(q[3]), tuple(q[2])]
        if pts:
            out.append([tuple(fitz.Point(p) * m) for p in pts])
    return out


def _box_dist(p: Point, b: BBox) -> float:
    dx = max(b[0] - p[0], 0.0, p[0] - b[2])
    dy = max(b[1] - p[1], 0.0, p[1] - b[3])
    return math.hypot(dx, dy)


def _segments(paths: List[List[Point]]):
    for pts in paths:
        for a, b in zip(pts, pts[1:]):
            yield a, b


def _in_table(hit: LikeHit, segs, h: float) -> bool:
    """A ruled row round the hit: a rule close above AND below it (spanning
    well past it) — a legend or schedule row, not a tag near a wall."""
    x0, y0, x1, y1 = hit.bbox
    vertical_text = hit.rotation in (90, 270)
    near, span = 1.6 * h, 2.5 * (y1 - y0 if vertical_text else x1 - x0)
    above = below = False
    for (ax, ay), (bx, by) in segs:
        if not vertical_text and abs(ay - by) < 0.4:
            lo, hi = min(ax, bx), max(ax, bx)
            if lo <= x0 and hi >= x1 and hi - lo >= span:
                if 0 <= y0 - ay <= near:
                    above = True
                elif 0 <= ay - y1 <= near:
                    below = True
        elif vertical_text and abs(ax - bx) < 0.4:
            lo, hi = min(ay, by), max(ay, by)
            if lo <= y0 and hi >= y1 and hi - lo >= span:
                if 0 <= x0 - ax <= near:
                    above = True
                elif 0 <= ax - x1 <= near:
                    below = True
        if above and below:
            return True
    return False


def _leader(hit: LikeHit, paths: List[List[Point]], h: float):
    """A drawn path with a vertex AT the tag that reaches well away from it:
    the far vertex is where the tag points. Strokes of the lettering itself
    (all inside the tag) and walls merely passing by (no vertex near) are not
    leaders."""
    b = hit.bbox
    inner = (b[0] - 0.2 * h, b[1] - 0.2 * h, b[2] + 0.2 * h, b[3] + 0.2 * h)
    best = None
    for pts in paths:
        near = min(_box_dist(p, b) for p in pts)
        if near > 1.0 * h:
            continue
        if all(_box_dist(p, inner) == 0.0 for p in pts):
            continue                                  # a stroke of the text
        far = max(pts, key=lambda p: _box_dist(p, b))
        reach = _box_dist(far, b)
        if reach < 2.5 * h:
            continue
        if best is None or near < best[0]:
            best = (near, far, pts)
    return best


def _classify(hits: List[LikeHit], doc, pages: Sequence[int]) -> None:
    """Set each hit's context from the geometry (see the module docstring)."""
    by_page: Dict[int, List[LikeHit]] = {}
    for h in hits:
        by_page.setdefault(h.page, []).append(h)
    # Repeated: same place on most same-size pages.
    sizes = {p: (round(doc[p].rect.width), round(doc[p].rect.height))
             for p in pages}
    for h in hits:
        same = [p for p in pages if sizes[p] == sizes[h.page]]
        if len(same) < 3:
            continue
        cx, cy = h.center
        tol = max(3.0, 0.4 * min(h.bbox[2] - h.bbox[0], h.bbox[3] - h.bbox[1]))
        on = sum(1 for p in same if any(
            abs(cx - o.center[0]) <= tol and abs(cy - o.center[1]) <= tol
            for o in by_page.get(p, ())))
        if on >= max(3, 0.6 * len(same)):
            h.evidence["repeated_on_pages"] = on
    for p, ph in by_page.items():
        paths = _paths(doc[p])
        segs = list(_segments(paths))
        for h in ph:
            hgt = (h.bbox[2] - h.bbox[0]) if h.rotation in (90, 270) \
                else (h.bbox[3] - h.bbox[1])
            table = _in_table(h, segs, hgt)
            if table:
                h.evidence["in_ruled_row"] = True
            lead = None if table else _leader(h, paths, hgt)
            if table or "repeated_on_pages" in h.evidence:
                h.context = "legend"
            elif lead is not None:
                h.context = "callout"
                h.points_to = (round(lead[1][0], 1), round(lead[1][1], 1))
                h.leader = [(round(x, 1), round(y, 1)) for x, y in lead[2]]
            else:
                h.context = "unanchored"


# -- the search -------------------------------------------------------------------

def find_like(doc, page: int, bbox: Sequence[float], pages=None, *,
              threshold: float = DEFAULT_THRESHOLD,
              scales: Optional[Sequence[float]] = None,
              rotations: Sequence[int] = DEFAULT_ROTATIONS,
              dpi: Optional[float] = None, max_hits_per_page: int = 400,
              classify: bool = True, workers: int = 4,
              backend: Optional[str] = None) -> Dict[str, Any]:
    """Every copy of the mark boxed at ``bbox`` on ``page``, on ``pages``.

    ``doc`` is a :class:`~planlens.document.Document` (or a ``fitz.Document``);
    ``bbox`` is in the displayed frame (PDF points, top-left origin) and
    should hug the mark — a leader or a table rule inside it makes the
    example worse. ``pages`` is a page spec (default: every page).
    ``backend`` picks the matcher — ``"opencv"``, ``"numpy"`` or ``"auto"``
    (default: :data:`BACKEND_ENV`, else auto; see :func:`resolve_backend`).
    Returns ``{"example", "hits", "pages", "counts", "dpi", "backend",
    "warnings"}``; ``hits`` are :class:`LikeHit`, best first within each
    page, pages in order.
    """
    fz = getattr(doc, "_doc", doc)
    from planlens.document.document import parse_pages
    idx = parse_pages(pages, fz.page_count)
    (page,) = parse_pages(page, fz.page_count)
    bx = tuple(float(v) for v in bbox)
    if len(bx) != 4 or bx[2] <= bx[0] or bx[3] <= bx[1]:
        raise ValueError("bbox must be [x0, y0, x1, y1] with x1 > x0, y1 > y0")
    engine = resolve_backend(backend)
    height_pt = min(bx[2] - bx[0], bx[3] - bx[1])
    want = dpi if dpi else DEFAULT_DPI
    want = max(want, 72.0 * MIN_EXAMPLE_PX / max(height_pt, 0.5))
    tpl, tb = _example(fz[page], bx, want)
    scales = tuple(scales) if scales else DEFAULT_SCALES
    warnings: List[str] = []

    def one(p: int) -> List[LikeHit]:
        pg = fz[p]
        d = _page_dpi(pg, want)
        t = tpl if d == want else _resize(tpl, d / want, engine)
        ink = _ink(pg, d)
        return [LikeHit(p, b, s, r, sc)
                for s, b, r, sc in _match(ink, t, d, scales, rotations,
                                          threshold, max_hits_per_page, engine)]

    hits: List[LikeHit] = []
    with ThreadPoolExecutor(max_workers=max(1, int(workers))) as ex:
        for ph in ex.map(one, idx):
            if len(ph) >= max_hits_per_page:
                warnings.append(
                    f"page {ph[0].page}: {len(ph)} candidates (the cap) — the "
                    f"example may be too plain; box a more distinctive mark or "
                    f"raise the threshold")
            hits.extend(ph)
    if classify and hits:
        _classify(hits, fz, idx)
    counts = {c: sum(1 for h in hits if h.context == c) for c in CONTEXTS}
    return {"example": {"page": page, "bbox": [round(v, 1) for v in tb],
                        "height_pt": round(min(tb[2] - tb[0], tb[3] - tb[1]), 2)},
            "hits": hits, "pages": idx, "counts": counts,
            "dpi": round(want, 1), "backend": engine, "warnings": warnings}


# -- contact sheets for the host's eyes ----------------------------------------

def like_sheets(doc, hits: Sequence[LikeHit], per_sheet: int = 20,
                cols: int = 4, text_px: float = 34.0,
                start: int = 1, *,
                backend: Optional[str] = None) -> List[Tuple[bytes, List[int]]]:
    """Numbered contact sheets of ``hits``: each cut out with its
    surroundings (so a leader shows), turned upright and enlarged so its
    lettering is ``text_px`` tall — for a vision model to read each number
    at a size it cannot misread. Returns ``[(png_bytes, [hit indices])]``;
    labels count from ``start`` in ``hits`` order. ``backend`` as for
    :func:`find_like`: OpenCV draws the frames and numbers where it loads,
    numpy and PyMuPDF elsewhere (the same layout; the numbers' typeface
    differs)."""
    import numpy as np
    cv2 = None
    if resolve_backend(backend) == "opencv":
        from planlens.opencv import load
        cv2 = load()
    fz = getattr(doc, "_doc", doc)
    cell_w, cell_h, band = 460, 190, 30
    out: List[Tuple[bytes, List[int]]] = []
    for s0 in range(0, len(hits), per_sheet):
        chunk = list(range(s0, min(len(hits), s0 + per_sheet)))
        rows = math.ceil(len(chunk) / cols)
        sheet = np.full((rows * (cell_h + band), cols * cell_w), 255, np.uint8)
        for n, i in enumerate(chunk):
            h = hits[i]
            x0, y0, x1, y1 = h.bbox
            vertical = h.rotation in (90, 270)
            hgt = (x1 - x0) if vertical else (y1 - y0)
            mx, my = (2.2 * hgt, 3.2 * hgt) if vertical else (3.2 * hgt, 2.2 * hgt)
            pg = fz[h.page]
            r = pg.rect
            clip = (max(r.x0, x0 - mx), max(r.y0, y0 - my),
                    min(r.x1, x1 + mx), min(r.y1, y1 + my))
            dpi = 72.0 * text_px / max(hgt, 0.5)
            img = 255 - _ink(pg, min(dpi, 1200.0), clip=clip)
            img = np.ascontiguousarray(np.rot90(img, k=-((h.rotation // 90) % 4)))
            f = min((cell_w - 10) / img.shape[1], (cell_h - 10) / img.shape[0], 1.0)
            if f < 1.0:
                img = _resize(img, f, "opencv" if cv2 is not None else "numpy")
            r0 = (n // cols) * (cell_h + band)
            c0 = (n % cols) * cell_w
            oy = r0 + band + (cell_h - img.shape[0]) // 2
            ox = c0 + (cell_w - img.shape[1]) // 2
            sheet[oy:oy + img.shape[0], ox:ox + img.shape[1]] = img
            if cv2 is not None:
                cv2.rectangle(sheet, (c0 + 2, r0 + 2),
                              (c0 + cell_w - 3, r0 + band + cell_h - 3), 128, 2)
                cv2.putText(sheet, f"#{start + i}", (c0 + 10, r0 + band - 7),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2, cv2.LINE_AA)
            else:
                _frame(sheet, c0 + 1, r0 + 1, c0 + cell_w - 2,
                       r0 + band + cell_h - 2, 128)
                _stamp(sheet, f"#{start + i}", c0 + 10, r0 + band - 7, 24.0)
        if cv2 is not None:
            ok, png = cv2.imencode(".png", sheet)
            if not ok:                               # pragma: no cover
                raise RuntimeError("could not encode a contact sheet")
            png = png.tobytes()
        else:
            png = _png(sheet)
        out.append((png, [start + i for i in chunk]))
    return out


def _frame(img, x0: int, y0: int, x1: int, y1: int, grey: int) -> None:
    """A 2-pixel rectangle outline, corners inclusive."""
    img[y0:y0 + 2, x0:x1 + 1] = grey
    img[y1 - 1:y1 + 1, x0:x1 + 1] = grey
    img[y0:y1 + 1, x0:x0 + 2] = grey
    img[y0:y1 + 1, x1 - 1:x1 + 1] = grey


def _stamp(img, text: str, x: int, baseline: int, size: float) -> None:
    """``text`` in bold Helvetica, ``size`` px, its baseline at ``baseline``,
    darkened onto the grey image (PyMuPDF rasterises the glyphs)."""
    import fitz
    import numpy as np
    font = fitz.Font("hebo")
    w = int(math.ceil(font.text_length(text, fontsize=size))) + 4
    h = int(math.ceil(1.3 * size))
    tmp = fitz.open()
    try:
        pg = tmp.new_page(width=w, height=h)
        pg.insert_text((2, size), text, fontsize=size, fontname="hebo")
        pix = pg.get_pixmap(colorspace=fitz.csGRAY, alpha=False)
        glyphs = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.stride)[:, :pix.width]
    finally:
        tmp.close()
    y0 = baseline - int(size)
    ya, xa = max(0, y0), max(0, x)
    yb = min(img.shape[0], y0 + glyphs.shape[0])
    xb = min(img.shape[1], x + glyphs.shape[1])
    if yb > ya and xb > xa:
        np.minimum(img[ya:yb, xa:xb], glyphs[ya - y0:yb - y0, xa - x:xb - x],
                   out=img[ya:yb, xa:xb])


def _png(img) -> bytes:
    """A grey uint8 image as PNG bytes (PyMuPDF's encoder)."""
    import fitz
    import numpy as np
    img = np.ascontiguousarray(img, dtype=np.uint8)
    pix = fitz.Pixmap(fitz.csGRAY, img.shape[1], img.shape[0], img.tobytes(),
                      False)
    return pix.tobytes("png")


__all__ = ["LikeHit", "CONTEXTS", "DEFAULT_THRESHOLD", "DEFAULT_SCALES",
           "DEFAULT_ROTATIONS", "DEFAULT_DPI", "BACKEND_ENV", "BACKENDS",
           "available", "resolve_backend", "find_like", "like_sheets"]
