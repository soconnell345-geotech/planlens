"""Two made-up E-size (36 x 48 in) drawing sheets with known answers, for the
region tiles of ``planlens.review.frames`` (app BACKLOG VIS-30).

Each sheet has a title block, a column of general notes in small lettering,
two ruled schedules, a legend and several views with dimension strings,
keynotes and one revision cloud. ``layout="right"`` puts the title block down
the right edge with the notes beside it; ``layout="bottom"`` puts the title
block along the bottom and the notes on the left, so that nothing reading
these sheets can be fitted to one arrangement. Every word is invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Box = Tuple[float, float, float, float]

#: 48 x 36 in, displayed landscape, in points.
E_SIZE = (3456.0, 2592.0)
#: Lettering heights, in points: the notes are the smallest thing to read.
NOTE_PT, SCHEDULE_PT, DIM_PT = 4.3, 5.0, 9.0
_FONT = "helv"
_WORDS = ("PROVIDE", "VERIFY", "FIELD", "ANCHOR", "SPACING", "CONCRETE",
          "REINFORCING", "LAP", "SPLICE", "TYPICAL", "UNLESS", "NOTED",
          "COORDINATE", "OPENINGS", "BEFORE", "PLACING", "GROUT", "LEVEL")


@dataclass
class ESizeGT:
    """Ground truth for :func:`build_esize_sheet` (displayed points)."""
    pdf: bytes
    layout: str
    size: Tuple[float, float] = E_SIZE
    regions: Dict[str, Box] = field(default_factory=dict)
    #: Each dimension string whole: its line, ticks, witness lines and text.
    dims: List[Box] = field(default_factory=list)
    cloud: Box = (0.0, 0.0, 0.0, 0.0)


def _text(page, x, y, words, size, rotate=0):
    """Write ``words`` from baseline point (x, y); return the box it fills."""
    import pymupdf
    page.insert_text((x, y), words, fontsize=size, fontname=_FONT,
                     rotate=rotate)
    run = pymupdf.get_text_length(words, _FONT, size)
    if rotate == 90:                                 # reads bottom to top
        return (x - size, y - run, x + 0.3 * size, y)
    return (x, y - size, x + run, y + 0.3 * size)


def _union(boxes) -> Box:
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def _sentence(seed: int, chars: int) -> str:
    out, k = "", seed
    while len(out) < chars:
        out += _WORDS[k % len(_WORDS)] + " "
        k += 5
    return out[:chars].strip() + "."


def _title_block(page, box: Box):
    x0, y0, x1, y1 = box
    page.draw_rect(box, width=1.5)
    down = (y1 - y0) > (x1 - x0)
    cells = ["NORTHFIELD DESIGN GROUP", "RIVERBEND PUMP STATION",
             "FOUNDATION PLAN AND DETAILS", "DRAWN  J. DOE", "S-201"]
    for k, words in enumerate(cells):
        if down:
            cy = y0 + (y1 - y0) * k / len(cells)
            page.draw_line((x0, cy), (x1, cy), width=0.7)
            at = (x0 + 14, cy + 60)
        else:
            cx = x0 + (x1 - x0) * k / len(cells)
            page.draw_line((cx, y0), (cx, y1), width=0.7)
            at = (cx + 14, y0 + 60)
        _text(page, at[0], at[1] - 34, "PROJECT FIELD %d" % (k + 1),
              SCHEDULE_PT)
        _text(page, at[0], at[1], words, 30 if k == len(cells) - 1 else 13)


def _notes(page, box: Box):
    x0, y0, x1, y1 = box
    _text(page, x0, y0 + 12, "GENERAL NOTES", 10)
    chars = int((x1 - x0 - 16) / (0.6 * NOTE_PT))
    y, n = y0 + 36, 0
    while y + 6 * NOTE_PT * 1.4 < y1:
        n += 1
        _text(page, x0, y, "%d." % n, NOTE_PT)
        for row in range(3 + n % 3):
            _text(page, x0 + 16, y, _sentence(7 * n + row, chars), NOTE_PT)
            y += NOTE_PT * 1.4
        y += 3 * NOTE_PT                              # between notes


def _schedule(page, box: Box, title: str, rows: int, cols: int):
    x0, y0, x1, y1 = box
    _text(page, x0, y0 + 10, title, 9)
    top = y0 + 20
    page.draw_rect((x0, top, x1, y1), width=1.0)
    rh, cw = (y1 - top) / rows, (x1 - x0) / cols
    for r in range(1, rows):
        page.draw_line((x0, top + r * rh), (x1, top + r * rh), width=0.4)
    for c in range(1, cols):
        page.draw_line((x0 + c * cw, top), (x0 + c * cw, y1), width=0.4)
    for r in range(rows):
        for c in range(cols):
            words = ("MARK SIZE BARS TIES REMARKS TOP BOT".split()[c % 7]
                     if r == 0 else "%s%d" % ("FPCWB"[c % 5], 10 * r + c))
            _text(page, x0 + c * cw + 4, top + r * rh + rh / 2 + 2, words,
                  SCHEDULE_PT)


def _legend(page, box: Box):
    x0, y0, x1, y1 = box
    _text(page, x0, y0 + 10, "LEGEND", 9)
    names = ("EXISTING WALL", "NEW WALL", "GRID LINE", "KEYNOTE",
             "REVISION", "SECTION CUT", "DRAIN", "TEST PIT")
    for k, name in enumerate(names):
        y = y0 + 40 + k * (y1 - y0 - 50) / len(names)
        page.draw_line((x0, y), (x0 + 60, y), width=0.5 + 0.3 * (k % 3),
                       dashes="[4 3] 0" if k % 2 else None)
        _text(page, x0 + 80, y + 2, name, SCHEDULE_PT)


def _dim(page, a, b, gt: ESizeGT, words: str, reach: float):
    """A dimension string from ``a`` to ``b`` (a horizontal or a vertical
    run): the line, two ticks, two witness lines ``reach`` long towards the
    thing measured, and the words on it."""
    (ax, ay), (bx, by) = a, b
    flat = ay == by
    page.draw_line(a, b, width=0.4)
    boxes = [(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))]
    for px, py in (a, b):
        page.draw_line((px - 4, py + 4), (px + 4, py - 4), width=0.8)
        end = (px, py + reach) if flat else (px + reach, py)
        page.draw_line((px, py), end, width=0.3)
        boxes += [(px - 4, py - 4, px + 4, py + 4),
                  (px, py, end[0], end[1])]
    if flat:
        boxes.append(_text(page, (ax + bx) / 2 - 20, ay - 4, words, DIM_PT))
    else:
        boxes.append(_text(page, ax - 4, (ay + by) / 2 + 20, words, DIM_PT,
                           rotate=90))
    gt.dims.append(_union(boxes))


def _cloud(page, box: Box, gt: ESizeGT):
    """A revision cloud: one path of arcs round ``box``."""
    x0, y0, x1, y1 = box
    n = 6
    xs = [x0 + (x1 - x0) * k / n for k in range(n + 1)]
    ys = [y0 + (y1 - y0) * k / n for k in range(n + 1)]
    ring = ([(x, y0) for x in xs] + [(x1, y) for y in ys[1:]]
            + [(x, y1) for x in reversed(xs[:-1])]
            + [(x0, y) for y in reversed(ys[:-1])])
    cx, cy, puff = (x0 + x1) / 2, (y0 + y1) / 2, 0.35
    shape = page.new_shape()
    for p, q in zip(ring, ring[1:]):
        mx, my = (p[0] + q[0]) / 2, (p[1] + q[1]) / 2
        shape.draw_curve(p, (mx + puff * (mx - cx), my + puff * (my - cy)), q)
    shape.finish(width=0.8, color=(0.8, 0, 0), closePath=False)
    shape.commit()
    gx, gy = 0.1 * (x1 - x0), 0.1 * (y1 - y0)       # how far an arc bulges
    gt.cloud = (x0 - gx, y0 - gy, x1 + gx, y1 + gy)


def _view(page, box: Box, name: str, gt: ESizeGT, cloud: bool = False):
    x0, y0, x1, y1 = box
    px0, py0, px1, py1 = plan = (x0 + 110, y0 + 110, x1 - 150, y1 - 70)
    page.draw_rect(plan, width=1.2)
    for k in range(1, 6):                              # walls and grid
        gx = px0 + (px1 - px0) * k / 6
        gy = py0 + (py1 - py0) * k / 6
        page.draw_line((gx, py0), (gx, py1), width=0.3, dashes="[6 4] 0")
        page.draw_line((px0, gy), (px1, gy), width=0.6)
    split = px0 + 0.4 * (px1 - px0)
    _dim(page, (px0, py0 - 40), (split, py0 - 40), gt, "12'-6\"", 36)
    _dim(page, (split, py0 - 40), (px1, py0 - 40), gt, "18'-9\"", 36)
    _dim(page, (px0, py0 - 80), (px1, py0 - 80), gt, "31'-3\" OUT TO OUT",
         76)
    _dim(page, (px0 - 60, py0), (px0 - 60, py1), gt, "24'-0\" VERIFY",
         56)
    for k in range(3):                                 # keynotes
        ky = py0 + (py1 - py0) * (k + 1) / 4
        page.draw_line((px1 - 80, ky + 30), (px1 + 30, ky), width=0.3)
        page.draw_circle((px1 + 40, ky), 10, width=0.5)
        _text(page, px1 + 37, ky + 2, str(k + 1), DIM_PT)
        _text(page, px1 + 56, ky + 2, "SEE NOTE %d" % (k + 4), DIM_PT)
    _text(page, px0, py1 + 30, name, 11)
    _text(page, px0, py1 + 44, "SCALE 1/4 IN = 1 FT", DIM_PT)
    if cloud:
        _cloud(page, (px0 + 0.42 * (px1 - px0), py0 + 0.42 * (py1 - py0),
                      px0 + 0.62 * (px1 - px0), py0 + 0.6 * (py1 - py0)), gt)


def build_esize_sheet(layout: str = "right") -> ESizeGT:
    """One made-up E-size sheet; ``layout`` is ``"right"`` or ``"bottom"``
    (where the title block sits)."""
    import pymupdf
    w, h = E_SIZE
    doc = pymupdf.open()
    page = doc.new_page(width=w, height=h)
    page.draw_rect((36, 36, w - 36, h - 36), width=2.0)
    if layout == "right":
        r = {"title": (3060, 36, w - 36, h - 36),
             "notes": (2610, 70, 3020, 2520),
             "schedule 1": (1960, 70, 2550, 640),
             "schedule 2": (1960, 730, 2550, 1300),
             "legend": (1960, 1400, 2550, 2100),
             "view 1": (60, 60, 960, 1260), "view 2": (990, 60, 1900, 1260),
             "view 3": (60, 1300, 960, 2540),
             "view 4": (990, 1300, 1900, 2540)}
    elif layout == "bottom":
        r = {"title": (36, 2300, w - 36, h - 36),
             "notes": (70, 70, 500, 2260),
             "schedule 1": (2700, 70, 3390, 560),
             "schedule 2": (2700, 650, 3390, 1140),
             "legend": (2700, 1240, 3390, 2240),
             "view 1": (560, 60, 2640, 1180),
             "view 2": (560, 1210, 1560, 2270),
             "view 3": (1600, 1210, 2640, 2270)}
    else:
        raise ValueError("layout is 'right' or 'bottom'")
    gt = ESizeGT(pdf=b"", layout=layout, regions=r)
    _title_block(page, r["title"])
    _notes(page, r["notes"])
    _schedule(page, r["schedule 1"], "FOOTING SCHEDULE", rows=14, cols=6)
    _schedule(page, r["schedule 2"], "PIER SCHEDULE", rows=10, cols=5)
    _legend(page, r["legend"])
    views = [k for k in r if k.startswith("view")]
    for k, key in enumerate(views):
        _view(page, r[key], "%s PLAN" % "ABCD"[k], gt, cloud=(k == 1))
    gt.pdf = doc.tobytes()
    doc.close()
    return gt
