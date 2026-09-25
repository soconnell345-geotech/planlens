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
   (OpenCV normalised cross-correlation) at a ladder of scales and at 0 / 90 /
   180 / 270 degrees — a legend's lettering is often larger than the plan's,
   and tags turn with the walls they sit on. Overlapping matches are merged.
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

Nothing here calls a model, downloads anything or needs more than the numpy,
PyMuPDF and OpenCV planlens already depends on.
"""

from __future__ import annotations

import math
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
    return np.ascontiguousarray(ink[y0:y1, x0:x1]), tb


def _page_dpi(page, dpi: float) -> float:
    w, h = page.rect.width, page.rect.height
    px = (w * dpi / 72.0) * (h * dpi / 72.0)
    cap = MAX_PAGE_MEGAPIXELS * 1e6
    return dpi if px <= cap else dpi * math.sqrt(cap / px)


# -- matching -------------------------------------------------------------------

def _match(ink, tpl, dpi: float, scales, rotations, threshold: float,
           max_hits: int) -> List[Tuple[float, BBox, int, float]]:
    import cv2
    import numpy as np
    z = dpi / 72.0
    found = []
    for sc in scales:
        t = tpl if sc == 1.0 else cv2.resize(
            tpl, None, fx=sc, fy=sc,
            interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
        if t.shape[0] < 5 or t.shape[1] < 5:
            continue
        for rot in rotations:
            tr = np.ascontiguousarray(np.rot90(t, k=(rot // 90) % 4))
            if tr.shape[0] >= ink.shape[0] or tr.shape[1] >= ink.shape[1]:
                continue
            res = cv2.matchTemplate(ink, tr, cv2.TM_CCOEFF_NORMED)
            res[~np.isfinite(res)] = 0
            # Peaks only: a mark gives a hill of above-threshold scores, and a
            # dense sheet at a low threshold gives thousands of such pixels.
            # Keeping each hill's top (the maximum over a window half the
            # template's size) leaves one candidate per mark per pass.
            k = (max(3, (tr.shape[1] // 2) | 1), max(3, (tr.shape[0] // 2) | 1))
            peaks = (res >= threshold) & (res >= cv2.dilate(
                res, cv2.getStructuringElement(cv2.MORPH_RECT, k)))
            ys, xs = np.where(peaks)
            if len(ys) > 4 * max_hits:           # a plain example matching
                keep = np.argsort(res[ys, xs])[-4 * max_hits:]   # everything
                ys, xs = ys[keep], xs[keep]
            th, tw = tr.shape
            for y, x in zip(ys.tolist(), xs.tolist()):
                found.append((float(res[y, x]),
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
              classify: bool = True, workers: int = 4) -> Dict[str, Any]:
    """Every copy of the mark boxed at ``bbox`` on ``page``, on ``pages``.

    ``doc`` is a :class:`~planlens.document.Document` (or a ``fitz.Document``);
    ``bbox`` is in the displayed frame (PDF points, top-left origin) and
    should hug the mark — a leader or a table rule inside it makes the
    example worse. ``pages`` is a page spec (default: every page). Returns
    ``{"example", "hits", "pages", "counts", "dpi", "warnings"}``; ``hits``
    are :class:`LikeHit`, best first within each page, pages in order.
    """
    fz = getattr(doc, "_doc", doc)
    from planlens.document.document import parse_pages
    idx = parse_pages(pages, fz.page_count)
    (page,) = parse_pages(page, fz.page_count)
    bx = tuple(float(v) for v in bbox)
    if len(bx) != 4 or bx[2] <= bx[0] or bx[3] <= bx[1]:
        raise ValueError("bbox must be [x0, y0, x1, y1] with x1 > x0, y1 > y0")
    height_pt = min(bx[2] - bx[0], bx[3] - bx[1])
    want = dpi if dpi else DEFAULT_DPI
    want = max(want, 72.0 * MIN_EXAMPLE_PX / max(height_pt, 0.5))
    tpl, tb = _example(fz[page], bx, want)
    scales = tuple(scales) if scales else DEFAULT_SCALES
    warnings: List[str] = []

    def one(p: int) -> List[LikeHit]:
        pg = fz[p]
        d = _page_dpi(pg, want)
        t = tpl
        if d != want:
            import cv2
            f = d / want
            t = cv2.resize(tpl, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
        ink = _ink(pg, d)
        return [LikeHit(p, b, s, r, sc)
                for s, b, r, sc in _match(ink, t, d, scales, rotations,
                                          threshold, max_hits_per_page)]

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
            "dpi": round(want, 1), "warnings": warnings}


# -- contact sheets for the host's eyes ----------------------------------------

def like_sheets(doc, hits: Sequence[LikeHit], per_sheet: int = 20,
                cols: int = 4, text_px: float = 34.0,
                start: int = 1) -> List[Tuple[bytes, List[int]]]:
    """Numbered contact sheets of ``hits``: each cut out with its
    surroundings (so a leader shows), turned upright and enlarged so its
    lettering is ``text_px`` tall — for a vision model to read each number
    at a size it cannot misread. Returns ``[(png_bytes, [hit indices])]``;
    labels count from ``start`` in ``hits`` order."""
    import cv2
    import numpy as np
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
                img = cv2.resize(img, None, fx=f, fy=f, interpolation=cv2.INTER_AREA)
            r0 = (n // cols) * (cell_h + band)
            c0 = (n % cols) * cell_w
            oy = r0 + band + (cell_h - img.shape[0]) // 2
            ox = c0 + (cell_w - img.shape[1]) // 2
            sheet[oy:oy + img.shape[0], ox:ox + img.shape[1]] = img
            cv2.rectangle(sheet, (c0 + 2, r0 + 2),
                          (c0 + cell_w - 3, r0 + band + cell_h - 3), 128, 2)
            cv2.putText(sheet, f"#{start + i}", (c0 + 10, r0 + band - 7),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.9, 0, 2, cv2.LINE_AA)
        ok, png = cv2.imencode(".png", sheet)
        if not ok:                                   # pragma: no cover
            raise RuntimeError("could not encode a contact sheet")
        out.append((png.tobytes(), [start + i for i in chunk]))
    return out


__all__ = ["LikeHit", "CONTEXTS", "DEFAULT_THRESHOLD", "DEFAULT_SCALES",
           "DEFAULT_ROTATIONS", "DEFAULT_DPI", "find_like", "like_sheets"]
