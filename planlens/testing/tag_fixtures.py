"""A drawing set whose lettering is DRAWN, not typed — for ``find_like``.

AutoCAD plots SHX lettering as single strokes: the sheet carries line
segments where the words are, and no text layer at all. That is what defeated
a text search on a real 85-sheet security set (2026-09-25): 0.06 in penetration
tags ("GCE") drawn as strokes, each sheet repeating a legend table that names
every tag type, and the question was where the CALLOUTS are — the tags with a
leader drawn from them — not where the legend repeats.

:func:`build_synthetic_tag_set` draws that, on 11x17 sheets, with a tiny
single-stroke font (:data:`STROKE_FONT`) so every tag is vector geometry:

* a legend table in the same place on every sheet, one ruled row per tag type;
* callouts: a tag with a leader (polyline + filled arrowhead) to a point;
* look-alike tags (GCG, GPE, QCE — the misreading that started this);
* tags with no leader, and tags turned 90 degrees.

It returns the PDF bytes and the ground truth, in the displayed frame
(top-left origin, points), so a test can score recall, precision and where
each leader points.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Point = Tuple[float, float]

#: Single-stroke glyphs: polylines in a cell 0..0.7 wide, 0..1 tall, y DOWN
#: from the cap line (0) to the baseline (1) — the SHX "romans" idea.
STROKE_FONT: Dict[str, List[List[Point]]] = {
    "G": [[(0.62, 0.18), (0.5, 0.02), (0.2, 0.02), (0.05, 0.2), (0.05, 0.8),
           (0.2, 0.98), (0.5, 0.98), (0.65, 0.8), (0.65, 0.55), (0.38, 0.55)]],
    "C": [[(0.63, 0.18), (0.5, 0.02), (0.2, 0.02), (0.05, 0.2), (0.05, 0.8),
           (0.2, 0.98), (0.5, 0.98), (0.63, 0.82)]],
    "E": [[(0.62, 0.0), (0.05, 0.0), (0.05, 1.0), (0.62, 1.0)],
          [(0.05, 0.5), (0.45, 0.5)]],
    "F": [[(0.62, 0.0), (0.05, 0.0), (0.05, 1.0)], [(0.05, 0.5), (0.45, 0.5)]],
    "P": [[(0.05, 1.0), (0.05, 0.0), (0.5, 0.0), (0.63, 0.12), (0.63, 0.4),
           (0.5, 0.52), (0.05, 0.52)]],
    "B": [[(0.05, 1.0), (0.05, 0.0), (0.48, 0.0), (0.6, 0.1), (0.6, 0.38),
           (0.48, 0.48), (0.05, 0.48)],
          [(0.48, 0.48), (0.63, 0.6), (0.63, 0.88), (0.5, 1.0), (0.05, 1.0)]],
    "X": [[(0.05, 0.0), (0.63, 1.0)], [(0.63, 0.0), (0.05, 1.0)]],
    "Q": [[(0.2, 0.02), (0.05, 0.2), (0.05, 0.8), (0.2, 0.98), (0.5, 0.98),
           (0.65, 0.8), (0.65, 0.2), (0.5, 0.02), (0.2, 0.02)],
          [(0.4, 0.7), (0.7, 1.05)]],
    "T": [[(0.0, 0.0), (0.68, 0.0)], [(0.34, 0.0), (0.34, 1.0)]],
    "Y": [[(0.0, 0.0), (0.34, 0.5), (0.68, 0.0)], [(0.34, 0.5), (0.34, 1.0)]],
    "-": [[(0.1, 0.55), (0.55, 0.55)]],
}
ADVANCE = 0.9          # glyph advance, in cap heights

#: The tag types the legend lists, with the kind of description row drawn.
LEGEND_TYPES = ("FBG", "FPG", "GCE", "GCG", "GPE", "GPG", "XPC")


@dataclass
class TagTruth:
    """One tag drawn on a sheet."""
    page: int
    text: str
    bbox: Tuple[float, float, float, float]    # displayed frame, points
    rotation: int                              # 0 or 90 (reads upward)
    kind: str                                  # "callout" | "legend" | "bare"
    points_to: Point = None                    # leader tip, for a callout


@dataclass
class TagSetGT:
    pdf: bytes
    cap_pt: float
    n_pages: int
    tags: List[TagTruth] = field(default_factory=list)
    legend_bbox: Tuple[float, float, float, float] = None   # the legend table
    example_bbox: Tuple[float, float, float, float] = None  # legend "GCE", p0

    def of(self, text: str, kind: str = None) -> List[TagTruth]:
        return [t for t in self.tags if t.text == text
                and (kind is None or t.kind == kind)]


def draw_text(shape, text: str, x: float, y: float, cap: float,
              rotation: int = 0, width: float = 0.35):
    """Stroke ``text`` with its cap line at ``y`` (rotation 0) and return its
    box. ``rotation=90`` reads bottom-to-top with the baseline on x=``x``."""
    pts_all = []
    for i, ch in enumerate(text):
        for poly in STROKE_FONT.get(ch, []):
            pts = []
            for gx, gy in poly:
                u = (i * ADVANCE + gx) * cap          # along the reading line
                v = gy * cap                          # down from the cap line
                if rotation == 0:
                    pts.append((x + u, y + v))
                else:                                 # reads upward
                    pts.append((x + v, y - u))
            shape.draw_polyline(pts)
            pts_all += pts
    shape.finish(color=(0, 0, 0), width=width, closePath=False)
    xs = [p[0] for p in pts_all]
    ys = [p[1] for p in pts_all]
    return (min(xs), min(ys), max(xs), max(ys))


def _leader(shape, start: Point, tip: Point, cap: float):
    """A leader: a dog-leg polyline and a filled arrowhead at ``tip``."""
    import math
    elbow = (start[0] - 1.5 * cap, start[1])
    shape.draw_polyline([start, elbow, tip])
    shape.finish(color=(0, 0, 0), width=0.35, closePath=False)
    dx, dy = tip[0] - elbow[0], tip[1] - elbow[1]
    n = math.hypot(dx, dy) or 1.0
    ux, uy = dx / n, dy / n
    a, b = 1.4 * cap, 0.45 * cap
    base = (tip[0] - a * ux, tip[1] - a * uy)
    left = (base[0] - b * uy, base[1] + b * ux)
    right = (base[0] + b * uy, base[1] - b * ux)
    shape.draw_polyline([tip, left, right, tip])
    shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.2, closePath=True)


def build_synthetic_tag_set(n_pages: int = 3, cap_in: float = 0.06,
                            seed: int = 11,
                            legend_cap_in: float = None) -> TagSetGT:
    """The tag set described in the module docstring (11x17, landscape).
    ``legend_cap_in`` draws the legend's lettering at another height (a
    legend is often lettered larger than the plan's tags)."""
    import fitz

    rnd = random.Random(seed)
    cap = cap_in * 72.0
    lcap = (legend_cap_in or cap_in) * 72.0
    W, H = 1224.0, 792.0
    doc = fitz.open()
    gt = TagSetGT(pdf=b"", cap_pt=cap, n_pages=n_pages)
    lx0, ly0 = W - 300.0, 60.0                    # legend table, top-right
    row = 2.6 * lcap
    col = 7 * lcap
    gt.legend_bbox = (lx0, ly0, lx0 + 230.0, ly0 + row * (len(LEGEND_TYPES) + 1))
    for p in range(n_pages):
        page = doc.new_page(width=W, height=H)
        sh = page.new_shape()
        # walls: a grid of rooms
        for i in range(10):
            sh.draw_line((40 + i * 90, 40), (40 + i * 90, H - 60))
        for j in range(8):
            sh.draw_line((40, 40 + j * 90), (850, 40 + j * 90))
        sh.finish(color=(0, 0, 0), width=0.8)
        # the legend: ruled rows, TYPE column + a stroked "description"
        x0, y0, x1 = gt.legend_bbox[0], gt.legend_bbox[1], gt.legend_bbox[2]
        for r in range(len(LEGEND_TYPES) + 2):
            sh.draw_line((x0, y0 + r * row), (x1, y0 + r * row))
        for x in (x0, x0 + col, x1):
            sh.draw_line((x, y0), (x, y0 + row * (len(LEGEND_TYPES) + 1)))
        sh.finish(color=(0, 0, 0), width=0.5)
        draw_text(sh, "TYPE", x0 + 0.6 * lcap, y0 + 0.8 * lcap, lcap)
        for r, t in enumerate(LEGEND_TYPES, start=1):
            yy = y0 + r * row + 0.8 * lcap
            box = draw_text(sh, t, x0 + 1.2 * lcap, yy, lcap)
            gt.tags.append(TagTruth(p, t, box, 0, "legend"))
            if p == 0 and t == "GCE":
                gt.example_bbox = box
            # the description: a run of strokes like lettering
            draw_text(sh, "FFEE-CCPP-EFFE", x0 + col + lcap, yy, lcap)
        # callouts and bare tags on the plan
        taken: List[Tuple[float, float]] = []

        def spot():
            for _ in range(400):
                x = rnd.uniform(90, 800)
                y = rnd.uniform(70, H - 110)
                if all(abs(x - a) > 60 or abs(y - b) > 30 for a, b in taken):
                    taken.append((x, y))
                    return x, y
            raise RuntimeError("sheet too crowded")

        plan = (["GCE"] * (8 + 3 * p) + ["GCG"] * 6 + ["GPE"] * 3 +
                ["QCE"] * 1 + ["FBG"] * 3)
        rnd.shuffle(plan)
        for i, t in enumerate(plan):
            x, y = spot()
            rot = 90 if i % 7 == 3 else 0
            bare = (i % 9 == 5)
            box = draw_text(sh, t, x, y, cap, rotation=rot)
            if bare:
                gt.tags.append(TagTruth(p, t, box, rot, "bare"))
                continue
            if rot == 0:
                start = (box[0] - 0.6 * cap, (box[1] + box[3]) / 2)
            else:
                start = ((box[0] + box[2]) / 2, box[3] + 0.6 * cap)
            tip = (start[0] - rnd.uniform(18, 40), start[1] + rnd.uniform(-25, 25))
            _leader(sh, start, tip, cap)
            gt.tags.append(TagTruth(p, t, box, rot, "callout", tip))
        # a title block with stroked lettering
        sh.draw_rect(fitz.Rect(W - 300, H - 90, W - 40, H - 40))
        sh.finish(color=(0, 0, 0), width=0.8)
        draw_text(sh, "TYPE-GPE-XPC", W - 290, H - 75, 1.5 * cap)
        sh.commit()
    gt.pdf = doc.tobytes()
    doc.close()
    return gt


__all__ = ["STROKE_FONT", "LEGEND_TYPES", "TagTruth", "TagSetGT", "draw_text",
           "build_synthetic_tag_set"]
