"""A site plan with exploration symbols at known local coordinates — for the
plan-location tools (:mod:`planlens.document.planlocation`, BACKLOG PL-25).

:func:`build_plan_location_sheet` draws what a field session misread: four
exploration programs, each with its own symbol (an open circle 12.6 pt, a
circle with a cross 10.2 pt, a filled circle 9.5 pt, a square 9.0 pt) plus a
test pit drawn as a triangle; each label set BESIDE its symbol, on different
sides; a north arrow pointing to the lower left of the sheet (so a rotation
written in the page frame, whose y runs down, comes out mirrored); a scale
of 1:1500 (0.529 m per point); decoys — a manhole circle with no ID, a tank
too large to be a symbol, a label with no symbol, and two symbols whose labels
sit close together.

Every truth is in the DISPLAYED frame (origin top left, y down, points), the
frame every planlens tool reports, including on a page carrying ``/Rotate``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

Point = Tuple[float, float]

#: Metres per page point at 1:1500 (1 pt = 1/72 in = 0.35278 mm).
M_PER_PT_1500 = 1500 * 0.0254 / 72.0

#: The programs: shape, width across (pt), filled.
PROGRAMS = {
    "2026": ("circle", 12.6, False),
    "2024": ("circle-cross", 10.2, False),
    "2019": ("circle", 9.5, True),
    "1987": ("square", 9.0, False),
    "pits": ("triangle", 11.0, False),
}

#: (label, program, easting m, northing m, label side)
EXPLORATIONS = (
    ("B-1", "2026", 40.0, 60.0, "ne"),
    ("B-2", "2026", 95.0, 30.0, "w"),
    ("B-3", "2026", 150.0, 85.0, "s"),
    ("BH-1", "2024", 60.0, 140.0, "ne"),
    ("BH-2", "2024", 120.0, 150.0, "e"),
    ("EB-1", "2019", 20.0, 110.0, "nw"),
    ("EB-2", "2019", 175.0, 20.0, "ne"),
    ("B-87", "1987", 130.0, 110.0, "sw"),
    ("TP-1", "pits", 75.0, 85.0, "ne"),
    # two symbols 7 m apart whose labels sit between them
    ("CPT-1", "2026", 190.0, 120.0, "e"),
    ("CPT-2", "2026", 190.0, 133.0, "e"),
)


@dataclass
class PlanSymbolTruth:
    label: str
    program: str
    shape: str
    size: float
    filled: bool
    easting: float
    northing: float
    center: Point = (0.0, 0.0)          # displayed page points


@dataclass
class PlanLocationGT:
    pdf: bytes
    rotation: int
    m_per_pt: float
    #: north azimuth on the page as viewed, degrees clockwise from up
    north_deg: float
    origin_page: Point
    origin_local: Point
    north_from: Point
    north_to: Point
    symbols: List[PlanSymbolTruth] = field(default_factory=list)
    #: grid intersections: {"name", "page": [x, y], "local": [E, N]}
    controls: List[Dict] = field(default_factory=list)
    #: labels drawn with no symbol
    orphan_labels: List[str] = field(default_factory=list)

    def to_page(self, e: float, n: float) -> Point:
        phi = math.radians(self.north_deg)
        north = (math.sin(phi), -math.cos(phi))
        east = (math.cos(phi), math.sin(phi))
        de = (e - self.origin_local[0]) / self.m_per_pt
        dn = (n - self.origin_local[1]) / self.m_per_pt
        return (self.origin_page[0] + de * east[0] + dn * north[0],
                self.origin_page[1] + de * east[1] + dn * north[1])


def build_plan_location_sheet(rotation: int = 0,
                              north_deg: float = 207.0) -> PlanLocationGT:
    """The sheet, on a page displayed with ``/Rotate rotation``."""
    import pymupdf
    from planlens.document.frame import from_display_point

    W, H = 1224.0, 792.0                 # tabloid landscape, as viewed
    doc = pymupdf.open()
    if rotation in (90, 270):
        page = doc.new_page(width=H, height=W)
    else:
        page = doc.new_page(width=W, height=H)
    page.set_rotation(rotation)
    gt = PlanLocationGT(pdf=b"", rotation=rotation, m_per_pt=M_PER_PT_1500,
                        north_deg=north_deg, origin_page=(612.0, 396.0),
                        origin_local=(100.0, 85.0), north_from=(0, 0),
                        north_to=(0, 0))

    def U(p: Point) -> Point:
        return from_display_point(page, p[0], p[1])

    def text(p: Point, s: str, size: float = 7.0) -> None:
        page.insert_text(U(p), s, fontsize=size, rotate=rotation)

    sh = page.new_shape()
    for label, prog, e, n, side in EXPLORATIONS:
        shape, size, filled = PROGRAMS[prog]
        c = gt.to_page(e, n)
        r = size / 2.0
        if shape.startswith("circle"):
            sh.draw_circle(U(c), r)
            sh.finish(color=(0, 0, 0), fill=(0, 0, 0) if filled else None,
                      width=0.6)
            if shape == "circle-cross":
                sh.draw_line(U((c[0] - r, c[1])), U((c[0] + r, c[1])))
                sh.draw_line(U((c[0], c[1] - r)), U((c[0], c[1] + r)))
                sh.finish(color=(0, 0, 0), width=0.5)
        elif shape == "square":
            sh.draw_polyline([U((c[0] - r, c[1] - r)), U((c[0] + r, c[1] - r)),
                              U((c[0] + r, c[1] + r)), U((c[0] - r, c[1] + r)),
                              U((c[0] - r, c[1] - r))])
            sh.finish(color=(0, 0, 0), width=0.6, closePath=True)
        elif shape == "triangle":
            # centroid at c
            pts = [(c[0], c[1] - r), (c[0] + r * math.sqrt(3) / 2, c[1] + r / 2),
                   (c[0] - r * math.sqrt(3) / 2, c[1] + r / 2)]
            sh.draw_polyline([U(p) for p in pts + pts[:1]])
            sh.finish(color=(0, 0, 0), width=0.6, closePath=True)
        tw = 4.2 * len(label)
        off = {"ne": (r + 2, -r - 1), "e": (r + 3, 2.5), "w": (-r - 3 - tw, 2.5),
               "s": (-tw / 2, r + 8), "nw": (-r - 2 - tw, -r - 1),
               "sw": (-r - 2 - tw, r + 7)}[side]
        text((c[0] + off[0], c[1] + off[1]), label)
        gt.symbols.append(PlanSymbolTruth(label, prog, shape, size, filled,
                                          e, n, c))
    # decoys: a manhole (open circle, no ID), a tank (too big), an orphan label
    mh = gt.to_page(110.0, 60.0)
    sh.draw_circle(U(mh), 6.0)
    sh.finish(color=(0, 0, 0), width=0.6)
    text((mh[0] + 8, mh[1] + 2), "MH")
    tank = gt.to_page(30.0, 20.0)
    sh.draw_circle(U(tank), 40.0)
    sh.finish(color=(0, 0, 0), width=0.8)
    text((tank[0] - 10, tank[1]), "TANK")
    text((1000.0, 700.0), "B-99")
    gt.orphan_labels.append("B-99")
    # north arrow: shaft and a filled head, near the top right
    tail = (1120.0, 120.0)
    phi = math.radians(north_deg)
    head = (tail[0] + 40 * math.sin(phi), tail[1] - 40 * math.cos(phi))
    sh.draw_line(U(tail), U(head))
    sh.finish(color=(0, 0, 0), width=1.0)
    gt.north_from, gt.north_to = tail, head
    text((head[0] + 4, head[1]), "N", 10)
    # grid intersections as plus marks with coordinate labels
    for e in (0.0, 100.0, 200.0):
        for n in (0.0, 100.0):
            p = gt.to_page(e, n)
            sh.draw_line(U((p[0] - 3, p[1])), U((p[0] + 3, p[1])))
            sh.draw_line(U((p[0], p[1] - 3)), U((p[0], p[1] + 3)))
            sh.finish(color=(0.4, 0.4, 0.4), width=0.3)
            gt.controls.append({"name": f"E{e:g} N{n:g}",
                                "page": [p[0], p[1]], "local": [e, n]})
    text((60.0, 60.0), "EXPLORATION LOCATION PLAN  SCALE 1:1500", 12)
    sh.commit()
    gt.pdf = doc.tobytes()
    doc.close()
    return gt
