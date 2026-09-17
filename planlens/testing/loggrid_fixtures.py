"""Synthetic boring-log pages with the grid stated by hand.

Built for :mod:`planlens.document.loggrid`. Two pages of the same log form,
one drawn in feet and one in metres, carrying between them everything the
grid reader has to cope with: column headers turned on their side, a depth
ruler in its own narrow band, layer-contact depths printed in the margin of
the description column, stratum rules across that column, underlines beneath
each layer name that are NOT stratum rules, blow triples and N values stacked
in one column at one depth, index-property cells, key-value fields above and
below the form, and a groundwater note.

A third builder makes the same form with the ruler taken out, for the case
the reader must refuse: a log page with no scale carries no depths, and says
so.

Nothing here is copied from a real log. The wording is the wording of the
trade; the numbers are made up and the answers are in :class:`LogGridGT`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

LETTER = (612.0, 792.0)

#: The form, in displayed points. One geometry, two scales.
X_LEFT = 40.0
X_RIGHT = 570.0
Y_FORM_TOP = 110.0
Y_HEADER_BOTTOM = 170.0      # the rule under the column headers = the body top
Y_FORM_BOTTOM = 650.0

#: Column edges, left to right. The first band is the graphic log, the second
#: the description (which also carries the layer-contact depths in its left
#: margin), then the depth ruler, the sample type, the blow record, the water
#: content, the dry unit weight, and the Atterberg limits.
EDGES = (40.0, 62.0, 300.0, 330.0, 352.0, 420.0, 455.0, 495.0, 570.0)

HEADERS = (
    "GRAPHIC LOG",          # turned
    "DEPTH",                # the margin label of the description column
    "DEPTH ({u})",          # turned — the ruler
    "SAMPLE TYPE",          # turned
    "FIELD TEST RESULTS",   # turned — blows and N, named by its values
    "WATER CONTENT (%)",    # turned
    "DRY UNIT WEIGHT (pcf)",  # turned
    "ATTERBERG LIMITS LL-PL-PI",   # turned
)


@dataclass
class LogGridGT:
    """One synthetic log page and the grid it should read as."""

    pdf: bytes
    unit: str
    #: ``value at the top of the body`` and ``points per unit of depth``.
    depth_at_body_top: float = 0.0
    points_per_unit: float = 0.0
    #: The ruler's printed ticks, in order.
    ticks: Tuple[float, ...] = ()
    #: Canonical column name -> the x band it should be found in.
    columns: Dict[str, Tuple[float, float]] = field(default_factory=dict)
    #: ``(top, bottom, the first words of the description)``.
    layers: List[Tuple[float, Optional[float], str]] = field(
        default_factory=list)
    #: ``(depth, blow record, N value)`` for each sample.
    samples: List[Tuple[float, str, str]] = field(default_factory=list)
    #: ``(depth, water content, dry unit weight)`` where both were run.
    index_tests: List[Tuple[float, str, str]] = field(default_factory=list)
    fields: Dict[str, str] = field(default_factory=dict)


def _turned(page, text: str, x: float, y_bottom: float, size: float) -> None:
    """A header turned to read bottom-to-top, its stripe starting at ``x``."""
    import fitz
    page.insert_text(fitz.Point(x, y_bottom), text, fontsize=size,
                     rotate=90)


def _draw_form(page, unit: str, ruler_step: float, ticks, per_unit: float,
               contacts, layers, samples, index_tests, ruler: bool = True
               ) -> None:
    import fitz

    # -- the fields above the form ----------------------------------------
    page.insert_text((X_LEFT + 2, 40), "BORING LOG NO. B-12", fontsize=13,
                     fontname="hebo")
    page.insert_text((X_LEFT + 2, 62), "PROJECT:  Rosewood Terrace",
                     fontsize=9)
    page.insert_text((X_LEFT + 2, 78), "Ground Surface Elevation:",
                     fontsize=7)
    page.insert_text((X_LEFT + 120, 78), f"104.5 ({unit})", fontsize=7)
    page.insert_text((X_LEFT + 250, 78), "Total Depth:", fontsize=7)
    page.insert_text((X_LEFT + 320, 78), f"{ticks[-1] + ruler_step:g} {unit}",
                     fontsize=7)
    page.insert_text((X_LEFT + 2, 94), "Hammer Type:  Automatic SPT Hammer",
                     fontsize=7)
    page.insert_text((X_LEFT + 250, 94), "Driller:  Regional Drilling",
                     fontsize=7)

    # -- the ruled form ----------------------------------------------------
    for x in EDGES:
        page.draw_line((x, Y_FORM_TOP), (x, Y_FORM_BOTTOM), width=0.6)
    for y in (Y_FORM_TOP, Y_HEADER_BOTTOM, Y_FORM_BOTTOM):
        page.draw_line((X_LEFT, y), (X_RIGHT, y), width=0.6)

    heads = [h.format(u=unit) for h in HEADERS]
    for i, head in enumerate(heads):
        x0, x1 = EDGES[i], EDGES[i + 1]
        if i == 1:
            # The description column's only printed label sits over the
            # narrow strip of layer-contact depths in its left margin.
            page.insert_text((x0 + 3, Y_HEADER_BOTTOM - 4), head, fontsize=6)
        else:
            _turned(page, head, (x0 + x1) / 2.0 - 3, Y_HEADER_BOTTOM - 4,
                    5.5)

    def y_of(depth: float) -> float:
        return Y_HEADER_BOTTOM + depth * per_unit

    # -- the depth ruler ---------------------------------------------------
    if ruler:
        for tick in ticks:
            page.insert_text((EDGES[2] + 6, y_of(tick) + 3), f"{tick:g}",
                             fontsize=8)

    # -- the layers --------------------------------------------------------
    for top, _bottom, text in layers:
        if top > 0:
            page.draw_line((EDGES[1] + 2, y_of(top)), (EDGES[2] - 2, y_of(top)),
                           width=0.6)
        first = text.split(",")[0]
        page.insert_text((EDGES[1] + 40, y_of(top) + 9), first, fontsize=7.5)
        # gINT and its imitators underline the layer name. It is as wide as a
        # stratum line and must not be read as one.
        page.draw_line((EDGES[1] + 40, y_of(top) + 11),
                       (EDGES[2] - 4, y_of(top) + 11), width=0.4)
        rest = text[len(first):].lstrip(", ")
        if rest:
            page.insert_text((EDGES[1] + 40, y_of(top) + 19), rest,
                             fontsize=7.5)
    for depth in contacts:
        # The layer-contact depth, printed in the description column's margin
        # with its baseline on the contact.
        page.insert_text((EDGES[1] + 4, y_of(depth) - 1), f"{depth:g}",
                         fontsize=6.5)

    # -- the samples -------------------------------------------------------
    for depth, blows, n_value in samples:
        page.insert_text((EDGES[3] + 6, y_of(depth) + 4), "SS", fontsize=7)
        page.insert_text((EDGES[4] + 6, y_of(depth) + 4), blows, fontsize=7)
        page.insert_text((EDGES[4] + 12, y_of(depth) + 13), n_value,
                         fontsize=7)
    for depth, wc, duw in index_tests:
        page.insert_text((EDGES[5] + 8, y_of(depth) + 4), wc, fontsize=7)
        page.insert_text((EDGES[6] + 8, y_of(depth) + 4), duw, fontsize=7)
        page.insert_text((EDGES[7] + 8, y_of(depth) + 4), "42-21-21",
                         fontsize=7)

    # -- the fields below the form ----------------------------------------
    page.insert_text((X_LEFT + 2, Y_FORM_BOTTOM + 20),
                     f"Groundwater encountered at {ticks[1]:g} {unit}",
                     fontsize=8)
    page.insert_text((X_LEFT + 2, Y_FORM_BOTTOM + 36), "Advancement Method:",
                     fontsize=7)
    page.insert_text((X_LEFT + 6, Y_FORM_BOTTOM + 45), "Hollow Stem Auger",
                     fontsize=7)
    page.insert_text((X_LEFT + 250, Y_FORM_BOTTOM + 36),
                     "Boring Started: 3/14/2025", fontsize=7)


def _build(unit: str, per_unit: float, ticks, contacts, layers, samples,
           index_tests, ruler: bool = True) -> LogGridGT:
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=LETTER[0], height=LETTER[1])
    step = ticks[1] - ticks[0]
    _draw_form(page, unit, step, ticks, per_unit, contacts, layers, samples,
               index_tests, ruler=ruler)
    pdf = doc.tobytes()
    doc.close()
    return LogGridGT(
        pdf=pdf, unit=unit, depth_at_body_top=0.0, points_per_unit=per_unit,
        ticks=tuple(ticks),
        columns={
            "graphic": (EDGES[0], EDGES[1]),
            "description": (EDGES[1], EDGES[2]),
            "depth": (EDGES[2], EDGES[3]),
            "sample_type": (EDGES[3], EDGES[4]),
            "blows": (EDGES[4], EDGES[5]),
            "water_content": (EDGES[5], EDGES[6]),
            "dry_unit_weight": (EDGES[6], EDGES[7]),
            "plasticity_index": (EDGES[7], EDGES[8]),
        },
        layers=[(t, b, d) for t, b, d in layers],
        samples=list(samples), index_tests=list(index_tests),
        fields={
            "boring_id": "B-12",
            "project": "Rosewood Terrace",
            "hammer_type": "Automatic SPT Hammer",
            "driller": "Regional Drilling",
            "drilling_method": "Hollow Stem Auger",
            "date_started": "3/14/2025",
        })


#: Three layers, four samples, two of them with index tests, in feet.
_FT_LAYERS = [
    (0.0, 4.0, "SANDY LEAN CLAY (CL), dark brown, very stiff"),
    (4.0, 16.0, "POORLY GRADED SAND WITH SILT (SP-SM), tan, medium dense"),
    (16.0, None, "LEAN CLAY (CL), gray, stiff"),
]
_FT_SAMPLES = [(2.0, "5-9-12", "N=21"), (7.0, "4-6-8", "N=14"),
               (12.0, "8-11-15", "N=26"), (18.0, "3-4-6", "N=10")]
_FT_INDEX = [(2.0, "18", "112"), (18.0, "26", "98")]

#: The metric twin: the same form on a 1 m ruler.
_M_LAYERS = [
    (0.0, 1.2, "SANDY LEAN CLAY (CL), dark brown, very stiff"),
    (1.2, 4.9, "POORLY GRADED SAND WITH SILT (SP-SM), tan, medium dense"),
    (4.9, None, "LEAN CLAY (CL), gray, stiff"),
]
_M_SAMPLES = [(0.6, "5-9-12", "N=21"), (2.1, "4-6-8", "N=14"),
              (3.6, "8-11-15", "N=26"), (5.5, "3-4-6", "N=10")]
_M_INDEX = [(0.6, "18", "112"), (5.5, "26", "98")]


def build_imperial_log() -> LogGridGT:
    """A one-page boring log on a 5 ft ruler, with its grid stated."""
    return _build("ft", 22.0, [5.0, 10.0, 15.0, 20.0], [4.0, 16.0],
                  _FT_LAYERS, _FT_SAMPLES, _FT_INDEX)


def build_metric_log() -> LogGridGT:
    """The same form on a 1 m ruler — the twin that proves nothing is fixed."""
    return _build("m", 72.0, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0], [1.2, 4.9],
                  _M_LAYERS, _M_SAMPLES, _M_INDEX)


def build_log_without_ruler() -> LogGridGT:
    """The same form with the depth ruler left off the page.

    What a reader must not do with it is guess. There is no scale, so there
    is no depth, and the grid says so instead of placing the cells anyway.
    """
    return _build("ft", 22.0, [5.0, 10.0, 15.0, 20.0], [], _FT_LAYERS,
                  _FT_SAMPLES, _FT_INDEX, ruler=False)
