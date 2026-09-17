"""``log_grid`` — a boring or test-pit log read as the coordinate system it is.

A geotechnical log is a form, and a form is geometry. Down one narrow band of
the page runs a depth ruler: a few numbers, evenly spaced, that fix a linear
map from y to depth. Across the top sits a row of column headers, often turned
on their side to fit. Everything else on the page is a value that means
whatever its column says it means, at whatever depth its y says it is.

This module reads that geometry and hands back the grid — columns with their
x bands, the ruler with its fit, every remaining line of text placed in a
column at a depth, the layers the description band is cut into, and the
key-value pairs printed outside the body. It does **no** interpretation: a
blow-count cell comes back as the string ``"5-9-12"``, not as three numbers
with a meaning, and an N-value cell comes back as ``"N=6"``. Naming the column
and fixing the depth is the whole job; what a value MEANS is the reader's.

Nothing here is template-specific. There is no list of firms and no list of
log formats — every fact comes off the page: the ruling lines the form is
drawn with, the text lines with their boxes and reading direction, and a small
multilingual vocabulary that maps a printed header onto a canonical column
name. A template this module has never seen reads the same way, and a page
whose ruler it cannot find says so in ``LogGrid.warnings`` and returns no
depths rather than guessing them.

It works the same on optical text. Azure Document Intelligence and OCR lines
arrive in the same displayed frame with the same boxes, so the only difference
is that a scanned page carries no vector ruling lines and the columns have to
come from the header labels alone — which is said in the warnings, because the
column edges are then softer.

Output is data with boxes, never a claim.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.frame import to_display_bbox, to_display_point
from planlens.document.model import (
    SOURCE_AZURE_DI, SOURCE_CAD_HIDDEN, SOURCE_OCR, BBox, TextLine, _compact,
    _r, _rb,
)

# ---------------------------------------------------------------------------
# Thresholds
#
# Every number below is a threshold the rules lean on. They are gathered here
# so a reader can see the whole of the module's discretion in one screen, and
# so a measurement run can move one and see what it costs.
# ---------------------------------------------------------------------------

#: Degrees a text line may sit away from an axis and still be read as part of
#: the grid. Optical text is never exactly straight — Azure Document
#: Intelligence reports a scanned log's horizontal lines at 358.5 to 359.4
#: degrees and its rotated headers at 84 to 90 — while a diagonal DRAFT
#: watermark sits at 45 and must not be read as a column header at all.
SKEW_TOLERANCE_DEG = 15.0

#: A drawn line counts as a rule when it wanders less than this across its
#: length, and runs at least this far.
RULE_STRAIGHTNESS_PT = 0.8
MIN_RULE_LENGTH_PT = 12.0

#: A vertical rule is a COLUMN EDGE when it runs at least this fraction of the
#: page height. Measured on the corpus: real column edges run 0.39 (a
#: half-page test-pit form) to 0.73 of the page; the longest rule that is not
#: a column edge — a box around a groundwater table or a signature block — ran
#: 0.14. The floor sits in the gap, nearer the decoys than the edges so that a
#: short form is not thrown away.
MIN_COLUMN_EDGE_FRAC = 0.30

#: Column edges closer together than this are the same edge drawn twice (a
#: form drawn with a hairline and a shadow, or the same vector emitted once
#: per table cell). Measured: duplicate emissions land within 0.1 pt; the
#: narrowest genuine column on the corpus is 10.8 pt wide (a sample-symbol
#: strip).
SAME_EDGE_PT = 2.0
MIN_COLUMN_WIDTH_PT = 5.0

#: A horizontal rule spanning this fraction of the ruled width is a full-width
#: rule: the line between the header band and the body, or the line that
#: closes the body. Stratum lines cross only the description column, which on
#: every corpus template is under half the ruled width.
FULL_WIDTH_FRAC = 0.60

#: A ruler needs at least this many ticks. Three points make a line; a fourth
#: is what makes the line an observation rather than a construction.
MIN_RULER_TICKS = 3

#: How far a tick may sit from the fitted line before the fit is not a ruler,
#: as a fraction of the tick spacing. At 0.20 a 5 ft ruler tolerates a tick
#: printed 1 ft out of place, which no real ruler is.
MAX_RULER_RESIDUAL_FRAC = 0.20

#: Two depth rulers whose scales differ by more than this are a disagreement
#: worth a warning rather than a silent choice.
RULER_SCALE_TOLERANCE = 0.02

#: A run of ticks is "regular" when its value steps are all within this
#: fraction of their mean — the difference between a printed ruler (0, 5, 10,
#: 15) and a column of depths that happen to be sorted (0.3, 2.7, 4.0).
REGULAR_STEP_TOLERANCE = 0.02

#: How far above the first tick the header band may reach when no ruling line
#: marks its foot (the scanned path).
HEADER_BAND_PT = 70.0

#: The deepest into the ruled area the header band may reach, as a fraction
#: of it. Measured on the corpus: the divider under the header sits 8, 12 and
#: 12 per cent of the way down three different firms' forms.
HEADER_BAND_MAX_FRAC = 0.50

#: A horizontal rule is an UNDERLINE, not a stratum line, when its y sits
#: within this window of a text line's own foot (as a fraction of the text
#: height, then a flat allowance) and it runs under most of that text. gINT
#: and its imitators underline the bold layer name at the head of each layer,
#: and those underlines are as wide as a stratum line.
UNDERLINE_ABOVE_FRAC = 0.45
UNDERLINE_BELOW_PT = 2.5
UNDERLINE_COVER_FRAC = 0.70

#: How much of the narrower of two header labels must lie inside the other
#: before they are read as one label rather than two. Stripes of a single
#: turned label overlap almost completely; two neighbouring labels on an
#: optically read page overlapped by half, and merging them cost a column.
LABEL_MERGE_FRAC = 0.60

#: A stratum line must cross this fraction of the description column.
STRATUM_COVER_FRAC = 0.55

#: How far left of the description's own prose a lone number must sit before
#: it is the layer-contact depth printed in that column's margin rather than
#: a value inside the description. Measured on the corpus: the margin ticks
#: start 16.6 pt left of the prose; nothing else in a description column
#: starts more than 1 pt left of it.
TICK_MARGIN_PT = 4.0

#: Layer boundaries whose y values sit this close are the same boundary seen
#: twice — a stratum rule and the depth tick that labels it. It is measured in
#: POINTS on the page rather than in depth, because a fraction of a log's
#: depth range would swallow a 0.2 ft pavement layer at the top of a 60 ft
#: boring, and a rule and its own label land within 0.2 pt of each other.
SAME_BOUNDARY_PT = 2.5

#: A description line whose top sits this far above a boundary still belongs
#: below it: a form sets the first line of a layer tight against its stratum
#: line, and a line's box carries a point or two of leading above the ink.
BOUNDARY_SLACK_PT = 0.5


#: A value-shape refinement fires once this many cells of a column match.
MIN_SHAPE_CELLS = 2

#: The refinement only speaks where the header did not. A column whose header
#: already names a value is left alone: "ATTERBERG LIMITS LL-PL-PI" holds
#: "42-21-21", which has the shape of a blow record and is not one.
_GENERIC_NAMES = frozenset(("tests", "sample_id", "remarks", "other"))

#: The sentence a page with no depth scale is reported with. One wording, so
#: a caller can look for it, and so the two ways of failing to find a ruler —
#: no columns to search, and no column that holds one — read the same.
NO_RULER = "no depth ruler was found"

#: What a header saying "depth" is worth in the ruler contest, against 1.5
#: for even steps and 0.1 a tick. It is deliberately more than both together:
#: a column the form does not call depth has to be the only candidate before
#: it is read as the scale.
HEADER_NAMES_IT = 10.0

#: Confidences, by how the fact was come by.
CONF_HEADER_CANONICAL = 0.90
CONF_HEADER_VALUES = 0.60
CONF_COLUMN_UNNAMED = 0.40
CONF_RULER_FLOOR = 0.30


# ---------------------------------------------------------------------------
# The column vocabulary
# ---------------------------------------------------------------------------

#: The canonical column names. ``other`` is a residual, not a class: a column
#: whose header matched nothing keeps its printed header text.
COLUMNS = (
    "depth", "elevation", "sample_id", "sample_type", "blows", "n_value",
    "recovery", "rqd", "description", "uscs", "graphic", "water_content",
    "dry_unit_weight", "liquid_limit", "plastic_limit", "plasticity_index",
    "fines", "qu", "pocket_pen", "torvane", "remarks", "tests", "other",
)

# Phrases that name each column, in English, French and Spanish. They are
# matched against an accent-stripped, punctuation-stripped, space-padded form
# of the header, so "PROFONDEUR" and "Profondeur," are the same string and a
# phrase only matches on word boundaries.
_VOCAB: Dict[str, Tuple[str, ...]] = {
    "description": (
        "description", "material description", "soil description",
        "strata description", "description of material", "description of soil",
        "soil and rock description", "classification of material",
        "material classification", "subsurface profile", "soil profile",
        "lithology", "strata", "stratum description", "visual description",
        "sample description", "description of sample", "soil and rock",
        "field description", "description of strata",
        "description des sols", "description du sol", "description de sol",
        "nature des terrains", "descripcion", "descripcion del suelo",
        "descripcion de suelos", "clasificacion",
        "soil identification", "description and classification",
    ),
    "graphic": (
        "graphic log", "graphic", "soil graphic", "log graphic",
        "material symbol", "graphic symbol", "soil profile symbol",
        "graphique", "grafico", "symbole graphique", "profile graphic",
    ),
    "uscs": (
        "uscs", "uscs symbol", "soil symbol", "symbol", "group symbol",
        "usc", "sym", "simbolo", "symbole", "classification uscs",
        "soil class", "unified", "astm d2487",
    ),
    "depth": (
        "depth", "depth ft", "depth m", "depth feet", "depth meters",
        "depth metres", "depth in feet", "depth below grade",
        "depth below ground surface", "sample depth", "depth bgs",
        # gINT's default template, which a great many firms print unchanged
        "depth scale", "depth scale m", "depth scale ft", "depth scale feet",
        "depth scale meters", "depth scale metres", "scale depth",
        "profondeur", "prof", "profundidad", "tiefe",
    ),
    "elevation": (
        "elevation", "elev", "elevations", "altitude", "cote",
        "elevacion", "nivel", "cota", "elevation ft", "elevation m",
        "elev ft", "elev m", "elevation scale", "elev scale",
        "ground elevation scale",
    ),
    "sample_type": (
        "sample type", "type of sample", "sampler type", "sampler",
        "type", "type d echantillon", "type de prelevement",
        "tipo de muestra", "tipo muestra", "sample type and number",
    ),
    "sample_id": (
        "sample", "sample no", "sample number", "sample id", "samples",
        "sample and blows", "sampling", "sampling data", "spl no", "samp no",
        "echantillon", "echantillons", "no echantillon",
        "prelevement", "muestra", "no muestra", "numero de muestra",
        "sample interval", "sample information",
        "number", "sample data", "sample and rqd data", "samp",
    ),
    "blows": (
        "blows", "blow counts", "blow count", "blows per 6", "blows per 6 in",
        "blows per foot", "blows ft", "blows per 0 15 m", "blows 6",
        "penetration resistance", "standard penetration test",
        "spt blows", "spt", "driving record", "hammer blows", "n blows ft",
        "coups", "nombre de coups", "coups par", "battage",
        "golpes", "numero de golpes", "golpes por", "resistencia penetracion",
        "schlagzahl", "blows 150mm", "blows per 150 mm",
        # gINT prints the penetration record abbreviated and unit-suffixed
        "penetr resist", "penetr resist bl 6in", "penetr resist bl 15cm",
        "pen resist", "bl 6in", "bl 15cm", "bl 30cm", "blows 6in",
        "blows 15cm", "blows 30cm", "blow count 6in",
    ),
    "n_value": (
        "n value", "n values", "spt n", "spt n value", "n60",
        "standard penetration resistance n", "valeur n", "valor n",
        "n spt", "n blows",
        "n value blows ft", "n value blows 30cm", "n value blows 300mm",
    ),
    "recovery": (
        "recovery", "rec", "recovery percent", "core recovery", "rec percent",
        "percent recovery", "recuperation", "recuperacion", "recobro",
        "recov", "recov in", "recov cm", "recov mm", "recovery in",
        "recovery cm",
    ),
    "rqd": ("rqd", "rock quality designation", "rqd percent", "r q d"),
    "water_content": (
        "water content", "moisture content", "natural moisture content",
        "moisture", "water content percent", "natural water content",
        "wc", "mc", "wn", "moist",
        "teneur en eau", "teneur en eau naturelle", "humidite",
        "contenido de agua", "contenido de humedad", "humedad",
    ),
    "dry_unit_weight": (
        "dry unit weight", "dry unit wt", "dry density", "unit dry weight",
        "dry wt", "gamma d", "dry unit weigh", "dry unit",
        "densite seche", "poids volumique sec", "masse volumique seche",
        "peso unitario seco", "densidad seca", "peso especifico seco",
    ),
    "liquid_limit": (
        "liquid limit", "ll", "limite de liquidite", "limite liquide",
        "limite liquido", "limite de liquidez",
    ),
    "plastic_limit": (
        "plastic limit", "pl", "limite de plasticite", "limite plastique",
        "limite plastico", "limite de plasticidad",
    ),
    "plasticity_index": (
        "plasticity index", "pi", "ip", "plasticity", "indice de plasticite",
        "indice de plasticidad", "atterberg", "atterberg limits",
        "limites d atterberg", "limites de atterberg",
    ),
    "fines": (
        "fines", "percent fines", "fines content", "percent passing no 200",
        "passing no 200", "passing 200", "percent passing 200", "minus 200",
        "p200", "200 wash", "percent silt and clay", "percent finer no 200",
        "percent passing", "passant 80 um", "pasante 200", "pasa 200",
        "finos", "fins",
    ),
    "qu": (
        "unconfined compressive strength", "compressive strength",
        "unconfined compression", "qu", "qu tsf", "qu psf", "uc strength",
        "unconfined", "strength test", "shear strength",
        "resistance a la compression simple", "compression simple",
        "resistencia a la compresion", "compresion simple",
        "undrained shear strength",
    ),
    "pocket_pen": (
        "pocket pen", "pocket penetrometer", "pocket pen tsf", "pp",
        "penetrometre de poche", "penetrometro de bolsillo",
    ),
    "torvane": ("torvane", "torvane tsf", "tv", "vane shear", "vane",
                "scissometre", "veleta"),
    "remarks": (
        "remarks", "notes", "comments", "observations", "remark",
        "field notes", "commentaires", "remarques", "observaciones", "notas",
    ),
    "tests": (
        "tests", "test results", "test data", "laboratory tests",
        "laboratory data", "lab data", "lab results", "lab tests",
        "field test results", "field tests", "other tests", "test type",
        "laboratory test results", "additional tests",
        "essais", "essais de laboratoire", "resultats des essais",
        "ensayos", "ensayos de laboratorio", "resultados",
    ),
}

#: The order headers are tested in. Longer, more specific names are tried
#: before the short ones they contain: "SAMPLE TYPE" must not be read as
#: "SAMPLE", and "N VALUE" must beat the bare "N" inside "BLOWS PER 6 IN (N)".
_COLUMN_ORDER = (
    "description", "graphic", "elevation", "depth", "sample_type",
    "n_value", "blows", "recovery", "rqd", "water_content",
    "dry_unit_weight", "plasticity_index", "liquid_limit", "plastic_limit",
    "fines", "pocket_pen", "torvane", "qu", "uscs", "sample_id",
    "remarks", "tests",
)

#: Headers that mean "this column carries the water-table symbols", which is
#: not a value column at all. Listed so that WATER LEVEL OBSERVATIONS is never
#: read as WATER CONTENT, and so that the column keeps its printed header.
_WATER_LEVEL = ("water level", "water table", "groundwater", "ground water",
                "water level observations", "niveau d eau", "nappe",
                "nivel freatico", "nivel de agua", "wl")

_UNIT_WORDS = {
    "ft": "ft", "feet": "ft", "foot": "ft", "pi": "ft", "pied": "ft",
    "pieds": "ft", "pies": "ft",
    "m": "m", "meter": "m", "meters": "m", "metre": "m", "metres": "m",
    "metros": "m", "mts": "m", "mt": "m",
}

#: Header keys, normalised, that name a field the record wants under a fixed
#: name. Anything else keeps the key the page printed.
_FIELD_KEYS: Dict[str, Tuple[str, ...]] = {
    "boring_id": ("boring number", "boring no", "boring", "borehole number",
                  "borehole no", "bore hole no", "hole number", "hole no",
                  "log of boring", "boring id", "sondage", "sondeo"),
    "test_pit_id": ("test pit number", "test pit no", "test pit", "pit no",
                    "tp no", "puits d essai", "calicata"),
    "project": ("project", "project name", "projet", "proyecto"),
    "project_number": ("project no", "project number", "job number", "job no",
                       "contract number", "contract no", "file number",
                       "no de projet", "numero de proyecto"),
    "client": ("client", "owner", "cliente"),
    "location": ("location", "site", "emplacement", "ubicacion", "station"),
    "ground_surface_elevation": (
        "ground surface elevation", "surface elevation", "ground elevation",
        "elevation", "existing grade elevation", "gse",
        "cote du terrain naturel", "elevacion del terreno"),
    "date_started": ("date started", "started", "boring started",
                     "start date", "dates started", "date de debut",
                     "fecha de inicio", "date drilled"),
    "date_finished": ("date finished", "finished", "boring completed",
                      "completed", "completion date", "end date",
                      "date de fin", "fecha de termino"),
    "total_depth": ("total depth", "depth of boring", "boring depth",
                    "bottom of boring", "profondeur totale",
                    "profundidad total"),
    "hammer_type": ("hammer type", "hammer", "type de marteau",
                    "tipo de martillo"),
    "hammer_energy": ("hammer energy", "energy ratio", "hammer efficiency",
                      "rod energy ratio"),
    "drilling_method": ("drilling method", "advancement method", "method",
                        "drilling", "methode de forage",
                        "metodo de perforacion"),
    "drilling_equipment": ("drilling equipment", "drill rig", "rig",
                           "equipment", "equipement", "equipo"),
    "driller": ("driller", "drilled by", "sondeur", "perforista"),
    "contractor": ("contractor", "drilling contractor", "boring contractor",
                   "entreprise", "contratista"),
    "foreman": ("foreman", "boring foreman", "contractor foreman",
                "chef de chantier", "capataz"),
    "logged_by": ("logged by", "log by", "inspector", "representative",
                  "engineer", "field engineer", "geologist", "checked by",
                  "reviewed by"),
    "sheet": ("sheet", "sheet no", "feuille", "hoja"),
    "groundwater": ("groundwater", "ground water", "water level",
                    "water level observations", "groundwater observations",
                    "water table", "water depth", "nappe", "niveau d eau",
                    "nivel freatico"),
    "abandonment": ("abandonment method", "backfill", "borehole backfill",
                    "backfilled with"),
    "coordinates": ("latitude", "longitude", "northing", "easting",
                    "coordinates", "coordenadas"),
    "diameter": ("hole diameter", "boring diameter", "casing size",
                 "diametre", "diametro"),
}


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Column:
    """One column of the log: an x band, and what its header calls it.

    ``names`` is every canonical column the header matched, in the order the
    vocabulary found them, because one printed header often names several:
    "ATTERBERG LIMITS LL-PL-PI" is one x band carrying three values, and
    "SAMPLING DATA" carries a sample id, a blow record and a recovery. ``name``
    is the first of them, or ``"other"`` when the header matched nothing — in
    which case ``header`` still holds what the page printed.
    """

    id: str
    page: int
    x0: float
    x1: float
    name: str = "other"
    names: Tuple[str, ...] = ()
    header: str = ""
    unit: Optional[str] = None
    confidence: float = CONF_COLUMN_UNNAMED
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "id": self.id, "page": self.page, "name": self.name,
            "names": list(self.names) if len(self.names) > 1 else None,
            "header": self.header, "unit": self.unit,
            "x": [_r(self.x0), _r(self.x1)],
            "confidence": round(float(self.confidence), 2),
            "evidence": dict(self.evidence) or None,
        })


@dataclass(frozen=True)
class Ruler:
    """The linear map from y to depth (or to elevation) fitted on one page.

    ``slope`` is units per displayed point and ``intercept`` the value at
    ``y = 0``, so ``value_at(y) = intercept + slope * y``. ``residual`` is the
    largest distance any tick sits from that line, IN THE RULER'S OWN UNITS —
    the number that says whether the fit is a ruler or a coincidence.
    """

    page: int
    kind: str                 # "depth" or "elevation"
    column_id: Optional[str]
    slope: float
    intercept: float
    residual: float
    step: float               # mean value step between ticks
    ticks: Tuple[Tuple[float, float], ...] = ()   # (y centre, value)
    unit: Optional[str] = None
    unit_source: Optional[str] = None
    confidence: float = 0.5
    evidence: Dict[str, Any] = field(default_factory=dict)

    def value_at(self, y: float) -> float:
        return self.intercept + self.slope * float(y)

    def y_at(self, value: float) -> Optional[float]:
        if not self.slope:
            return None
        return (float(value) - self.intercept) / self.slope

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "page": self.page, "kind": self.kind, "column": self.column_id,
            "unit": self.unit, "unit_source": self.unit_source,
            "per_point": _r(self.slope, 4),
            "at_y0": _r(self.intercept, 3),
            "residual": _r(self.residual, 3),
            "step": _r(self.step, 3),
            "n_ticks": len(self.ticks),
            "confidence": round(float(self.confidence), 2),
            "evidence": dict(self.evidence) or None,
        })


@dataclass(frozen=True)
class Cell:
    """One line of text, placed: which column it is in and at what depth.

    ``depth`` is the depth at the line's y centre; ``depth_top`` and
    ``depth_bottom`` are the depths at its top and bottom edges, so a caller
    can see the interval a tall cell covers. All three are ``None`` on a page
    with no ruler. ``numbers`` are the numbers the text contains, extracted and
    nothing more: ``"5-9-12"`` gives ``(5.0, 9.0, 12.0)`` and what they mean is
    not this module's business.
    """

    page: int
    column: str
    column_id: str
    text: str
    bbox: BBox
    depth: Optional[float] = None
    depth_top: Optional[float] = None
    depth_bottom: Optional[float] = None
    numbers: Tuple[float, ...] = ()
    elevation: Optional[float] = None
    confidence: float = 0.5
    source: str = "pdf_text"

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "page": self.page, "column": self.column,
            "column_id": self.column_id,
            "depth": _r(self.depth, 2),
            "depth_range": ([_r(self.depth_top, 2), _r(self.depth_bottom, 2)]
                            if self.depth_top is not None else None),
            "elevation": _r(self.elevation, 2),
            "text": self.text, "bbox": _rb(self.bbox),
            "numbers": [_r(n, 3) for n in self.numbers] or None,
            "confidence": round(float(self.confidence), 2),
            "source": self.source if self.source != "pdf_text" else None,
        })


@dataclass(frozen=True)
class Layer:
    """One band of the description column, between two boundaries.

    ``bottom`` is ``None`` for the last layer of the log, whose foot the page
    does not state. ``source`` says what opened the layer: a stratum rule
    drawn across the description column, a depth tick printed beside it, or
    the top of the body on the log's first page.
    """

    top: float
    bottom: Optional[float]
    description: str
    pages: Tuple[int, ...] = ()
    source: str = "stratum_rule"
    bbox: Optional[BBox] = None
    confidence: float = 0.5

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "top": _r(self.top, 2), "bottom": _r(self.bottom, 2),
            "description": self.description,
            "pages": list(self.pages),
            "source": self.source,
            "bbox": _rb(self.bbox),
            "confidence": round(float(self.confidence), 2),
        })


@dataclass
class LogGrid:
    """One log — its pages, continuation sheets included — read as a grid."""

    pages: List[int] = field(default_factory=list)
    unit: Optional[str] = None
    columns: List[Column] = field(default_factory=list)
    rulers: Dict[int, Ruler] = field(default_factory=dict)
    elevation_rulers: Dict[int, Ruler] = field(default_factory=dict)
    rows: List[Cell] = field(default_factory=list)
    layers: List[Layer] = field(default_factory=list)
    fields: Dict[str, str] = field(default_factory=dict)
    field_boxes: Dict[str, Tuple[int, BBox]] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    # -- convenience ---------------------------------------------------------
    def columns_on(self, page: int) -> List[Column]:
        return [c for c in self.columns if c.page == page]

    def column(self, column_id: str) -> Optional[Column]:
        for c in self.columns:
            if c.id == column_id:
                return c
        return None

    def cells(self, name: Optional[str] = None,
              page: Optional[int] = None) -> List[Cell]:
        """Cells, optionally only those in a column carrying ``name``."""
        out: List[Cell] = []
        for cell in self.rows:
            if page is not None and cell.page != page:
                continue
            if name is not None:
                col = self.column(cell.column_id)
                if col is None or name not in col.names:
                    continue
            out.append(cell)
        return out

    @property
    def has_ruler(self) -> bool:
        return bool(self.rulers)

    def to_dict(self, rows: bool = True) -> Dict[str, Any]:
        seen = set()
        cols: List[Dict[str, Any]] = []
        for c in self.columns:
            key = (c.name, c.header, round(c.x0, 1), round(c.x1, 1))
            if key in seen:
                continue
            seen.add(key)
            cols.append(c.to_dict())
        out: Dict[str, Any] = {
            "pages": list(self.pages),
            "depth_unit": self.unit,
            "n_columns": len(cols),
            "columns": cols,
            "rulers": [r.to_dict() for r in self.rulers.values()],
            "n_rows": len(self.rows),
            "layers": [ly.to_dict() for ly in self.layers],
            "fields": dict(self.fields),
        }
        if self.elevation_rulers:
            out["elevation_rulers"] = [r.to_dict()
                                       for r in self.elevation_rulers.values()]
        if rows:
            out["rows"] = [c.to_dict() for c in self.rows]
        if self.warnings:
            out["warnings"] = list(self.warnings)
        return out


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------

def _strip_accents(text: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFKD", text)
                   if not unicodedata.combining(ch))


_PUNCT = re.compile(r"[^0-9a-z%#]+")


def _norm(text: str) -> str:
    """Accent-, case- and punctuation-insensitive form, padded with spaces."""
    flat = _PUNCT.sub(" ", _strip_accents(str(text or "")).lower())
    return " " + " ".join(flat.split()) + " "


#: A number, with the sign only where a sign can be. The hyphen in a blow
#: record ("5-9-12") is a separator, and reading it as a minus turned two of
#: the three blow counts negative.
_NUMBER = re.compile(
    r"(?<![\d.,])[-+]?(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d*\.?\d+)")


def numbers_in(text: str) -> Tuple[float, ...]:
    """Every number in a string, in order. ``"5-9-12"`` gives 5, 9, 12."""
    out: List[float] = []
    for m in _NUMBER.finditer(str(text or "")):
        token = m.group(0).replace(",", "")
        if token in ("", "-", "+", ".", "-.", "+."):
            continue
        try:
            out.append(float(token))
        except ValueError:      # pragma: no cover - the regex excludes these
            continue
    return tuple(out)


_SINGLE_NUMBER = re.compile(r"^[-+]?\d*\.?\d+$")

#: Dashes a printed minus sign can arrive as. A true minus sign, an en dash
#: and an em dash all mean minus when a digit follows; a hyphen already does.
_MINUS = "−–—"


def _single_number(text: str) -> Optional[float]:
    """The value of a line that is ONE number and nothing else, else None.

    A trailing tick mark is tolerated because optical text reads the dash of a
    printed ruler tick as part of the number beside it ("13-"). A LEADING dash
    is not tolerated but READ: it is a minus sign, and a column of elevations
    below datum reads "-2.5, -4.0, -5.5". Stripping it turned that column into
    a rising series, which let it be chosen as the depth ruler over the scale
    the page actually prints.
    """
    token = str(text or "").strip().rstrip("–—-").strip()
    if token[:1] in _MINUS and token[1:2] and token[1:2] in "0123456789.":
        token = "-" + token[1:]
    if not _SINGLE_NUMBER.match(token):
        return None
    try:
        return float(token)
    except ValueError:          # pragma: no cover
        return None


def _snap_rotation(rot: Optional[float]) -> Optional[int]:
    """The axis a line reads along, or None when it reads along neither.

    Optical text is never exactly straight and a watermark is deliberately
    not: 359.2 is horizontal, 84.3 is vertical, 45 is neither.
    """
    if rot is None:
        return 0
    r = float(rot) % 360.0
    for axis in (0, 90, 180, 270, 360):
        if abs(r - axis) <= SKEW_TOLERANCE_DEG:
            return axis % 360
    return None


def _unit_from_header(header: str) -> Optional[str]:
    """``"DEPTH (Ft.)"`` -> ``"ft"``; ``"DEPTH, feet"`` -> ``"ft"``."""
    for token in re.findall(r"\(([^)]{1,14})\)", str(header or "")):
        unit = _UNIT_WORDS.get(_norm(token).strip())
        if unit:
            return unit
    words = _norm(header).split()
    for word in reversed(words):
        unit = _UNIT_WORDS.get(word)
        if unit:
            return unit
    return None


def classify_header(header: str) -> Tuple[Tuple[str, ...], Optional[str]]:
    """The canonical names a printed column header carries, and its unit.

    Returns every name that matched, most specific first, so a header naming
    three things ("ATTERBERG LIMITS LL-PL-PI") comes back naming three.
    """
    flat = _norm(header)
    if not flat.strip():
        return (), None
    unit = _unit_from_header(header)
    water = min((flat.find(f" {phrase} ") for phrase in _WATER_LEVEL
                 if f" {phrase} " in flat), default=-1)
    hits: List[Tuple[int, int, int, str]] = []
    for order, name in enumerate(_COLUMN_ORDER):
        best: Optional[Tuple[int, int, int, str]] = None
        for phrase in _VOCAB[name]:
            at = flat.find(f" {phrase} ")
            if at < 0:
                continue
            candidate = (at, -len(phrase), order, name)
            if best is None or candidate < best:
                best = candidate
        if best is not None:
            hits.append(best)
    # A column header NAMES itself first and qualifies afterwards, so the
    # earliest match wins, and of two starting together the longer wins.
    # "Remarks (Drilling Fluid, Depth of Casing)" is a remarks column that
    # mentions depth, not a depth column; "Sample Description" is a
    # description, not a sample; "N-Value (Blows/ft)" is an N value that
    # mentions blows. The vocabulary order is only the last tie-break.
    hits.sort()
    if water >= 0 and (not hits or water <= hits[0][0]):
        # The column carries the water-table symbols and is not a value
        # column at all; it keeps its printed header and no canonical name.
        # The test is POSITIONAL, because "Remarks (Drilling Fluid, Depth of
        # Casing, Water Level)" is a remarks column that mentions the water
        # level, and refusing to name it at all lost a whole column.
        return (), unit
    return tuple(name for *_rest, name in hits), unit


# ---------------------------------------------------------------------------
# Page geometry
# ---------------------------------------------------------------------------

@dataclass
class _Rule:
    """One drawn rule, in the displayed frame."""
    a: float     # constant coordinate (x for vertical, y for horizontal)
    lo: float    # start along the run
    hi: float    # end along the run

    @property
    def length(self) -> float:
        return self.hi - self.lo


def page_rules(page) -> Tuple[List[_Rule], List[_Rule]]:
    """``(horizontal, vertical)`` drawn rules of a page, displayed frame.

    PyMuPDF reports drawings in the UNROTATED page space, so every segment is
    carried through ``page.rotation_matrix`` before it is classified: on a
    /Rotate 90 page what the content stream draws horizontally is displayed
    vertically, and a column edge has to be vertical AS DISPLAYED because that
    is the frame every text box is in.
    """
    horizontal: List[_Rule] = []
    vertical: List[_Rule] = []
    try:
        drawings = page.get_cdrawings()
    except Exception:           # pragma: no cover - defensive
        return horizontal, vertical
    rect = page.rect
    pw, ph = float(rect.width), float(rect.height)

    def on_page(a: float, lo: float, hi: float, vertical_rule: bool) -> bool:
        limit_a = pw if vertical_rule else ph
        limit_r = ph if vertical_rule else pw
        return (-2.0 <= a <= limit_a + 2.0
                and hi >= -2.0 and lo <= limit_r + 2.0)

    def add(x0: float, y0: float, x1: float, y1: float) -> None:
        (dx0, dy0) = to_display_point(page, x0, y0)
        (dx1, dy1) = to_display_point(page, x1, y1)
        if abs(dy1 - dy0) <= RULE_STRAIGHTNESS_PT:
            a, lo, hi = (dy0 + dy1) / 2.0, min(dx0, dx1), max(dx0, dx1)
            if hi - lo >= MIN_RULE_LENGTH_PT and on_page(a, lo, hi, False):
                horizontal.append(_Rule(a, lo, hi))
        elif abs(dx1 - dx0) <= RULE_STRAIGHTNESS_PT:
            a, lo, hi = (dx0 + dx1) / 2.0, min(dy0, dy1), max(dy0, dy1)
            if hi - lo >= MIN_RULE_LENGTH_PT and on_page(a, lo, hi, True):
                vertical.append(_Rule(a, lo, hi))

    for d in drawings:
        stroked = "s" in str(d.get("type") or "s")
        for it in d.get("items", ()):
            if it[0] == "l":
                (x0, y0), (x1, y1) = it[1], it[2]
                add(x0, y0, x1, y1)
            elif it[0] == "re":
                r = it[1]
                x0, y0 = float(r[0]), float(r[1])
                x1, y1 = x0 + float(r[2]), y0 + float(r[3])
                bx = to_display_bbox(page, (min(x0, x1), min(y0, y1),
                                            max(x0, x1), max(y0, y1)))
                w, h = bx[2] - bx[0], bx[3] - bx[1]
                if w >= MIN_RULE_LENGTH_PT and h <= 2.0:
                    if on_page((bx[1] + bx[3]) / 2.0, bx[0], bx[2], False):
                        horizontal.append(
                            _Rule((bx[1] + bx[3]) / 2.0, bx[0], bx[2]))
                elif h >= MIN_RULE_LENGTH_PT and w <= 2.0:
                    if on_page((bx[0] + bx[2]) / 2.0, bx[1], bx[3], True):
                        vertical.append(
                            _Rule((bx[0] + bx[2]) / 2.0, bx[1], bx[3]))
                elif stroked and w >= MIN_RULE_LENGTH_PT and h >= MIN_RULE_LENGTH_PT:
                    # A drawn box is four rules, and a form is often drawn as
                    # boxes. Only a STROKED box: a filled one is a logo, a
                    # swatch or a shaded band, and its edges are not rules.
                    if on_page(bx[1], bx[0], bx[2], False):
                        horizontal.append(_Rule(bx[1], bx[0], bx[2]))
                        horizontal.append(_Rule(bx[3], bx[0], bx[2]))
                    if on_page(bx[0], bx[1], bx[3], True):
                        vertical.append(_Rule(bx[0], bx[1], bx[3]))
                        vertical.append(_Rule(bx[2], bx[1], bx[3]))
    return horizontal, vertical


def _merge_edges(rules: Sequence[_Rule]) -> List[Tuple[float, float, float]]:
    """Collapse rules at the same coordinate into ``(a, lo, hi)`` runs."""
    out: List[Tuple[float, float, float]] = []
    for r in sorted(rules, key=lambda r: r.a):
        if out and abs(r.a - out[-1][0]) <= SAME_EDGE_PT:
            a, lo, hi = out[-1]
            out[-1] = ((a + r.a) / 2.0, min(lo, r.lo), max(hi, r.hi))
        else:
            out.append((r.a, r.lo, r.hi))
    return out


# ---------------------------------------------------------------------------
# One page
# ---------------------------------------------------------------------------

@dataclass
class _PageGrid:
    page: int
    width: float
    height: float
    columns: List[Column] = field(default_factory=list)
    ruler: Optional[Ruler] = None
    elevation_ruler: Optional[Ruler] = None
    cells: List[Cell] = field(default_factory=list)
    outside: List[TextLine] = field(default_factory=list)
    header_lines: List[TextLine] = field(default_factory=list)
    margin_ticks: set = field(default_factory=set)
    body_top: Optional[float] = None
    body_bottom: Optional[float] = None
    header_band: Optional[Tuple[float, float]] = None
    horizontal: List[_Rule] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    text_source: str = "pdf_text"
    #: How far above a boundary a description line may start and still belong
    #: below it. A point of leading on an embedded text layer; a quarter of a
    #: line on an optically read page, whose boxes wander by that much.
    slack: float = BOUNDARY_SLACK_PT


def _grid_columns(v_runs: Sequence[Tuple[float, float, float]],
                  h_rules: Sequence[_Rule], height: float
                  ) -> Tuple[List[Tuple[float, float]],
                             Optional[float], Optional[float]]:
    """Column x bands from the vertical rules, plus the ruled y extent.

    The outermost columns of a form are bounded on one side by its BORDER,
    not by a column edge, and on many templates the border is drawn as a
    horizontal rule that runs the whole width rather than as two vertical
    ones. The widest rule crossing the ruled area is taken as that width, so
    the first and last columns are not lost — which is where the depth ruler
    itself sits on more than one corpus template.
    """
    edges = [(a, lo, hi) for (a, lo, hi) in v_runs
             if (hi - lo) >= MIN_COLUMN_EDGE_FRAC * height]
    if len(edges) < 3:
        return [], None, None
    xs = [e[0] for e in edges]
    top = min(e[1] for e in edges)
    bottom = max(e[2] for e in edges)
    inside = [r for r in h_rules if top - 2.0 <= r.a <= bottom + 2.0]
    if inside:
        widest = max(inside, key=lambda r: r.length)
        if widest.length > xs[-1] - xs[0]:
            if xs[0] - widest.lo >= MIN_COLUMN_WIDTH_PT:
                xs.insert(0, widest.lo)
            if widest.hi - xs[-1] >= MIN_COLUMN_WIDTH_PT:
                xs.append(widest.hi)
    bands: List[Tuple[float, float]] = []
    for a, b in zip(xs, xs[1:]):
        if b - a >= MIN_COLUMN_WIDTH_PT:
            bands.append((a, b))
    return bands, top, bottom


def _header_text(lines: Sequence[TextLine], x0: float, x1: float) -> str:
    """The header of one column: its lines in the order a reader takes them.

    A label turned on its side is printed one line per stripe, and the stripes
    run left to right across the column whichever way the text reads, so x
    order is reading order for both. Ties go to the upper line.
    """
    inside = []
    for ln in lines:
        b = ln.bbox
        centre = (b[0] + b[2]) / 2.0
        if x0 - 0.5 <= centre < x1 + 0.5:
            inside.append(ln)
    if not inside:
        return ""
    rot = _snap_rotation(inside[0].rotation)
    if rot in (90, 270):
        inside.sort(key=lambda ln: (round(ln.bbox[0], 1), ln.bbox[1]))
    else:
        inside.sort(key=lambda ln: (round(ln.bbox[1], 1), ln.bbox[0]))
    return " ".join(ln.text.strip() for ln in inside if ln.text.strip())


def _bands_from_headers(lines: Sequence[TextLine]
                        ) -> List[Tuple[float, float]]:
    """Column bands when the page is not ruled: split between header labels.

    Labels are clustered by x overlap, then a boundary is put in the gap
    between neighbours — or, when optical boxes overlap, halfway between their
    centres. It is softer than a drawn edge and the caller says so.
    """
    boxes = [(ln.bbox[0], ln.bbox[2]) for ln in lines if ln.text.strip()]
    if len(boxes) < 2:
        return []
    boxes.sort()
    groups: List[List[Tuple[float, float]]] = [[boxes[0]]]
    for b in boxes[1:]:
        prev = (min(g[0] for g in groups[-1]), max(g[1] for g in groups[-1]))
        overlap = min(prev[1], b[1]) - max(prev[0], b[0])
        narrower = min(prev[1] - prev[0], b[1] - b[0])
        # Stripes of one turned label, or one label wrapped onto two lines,
        # sit inside each other; two DIFFERENT labels on an optically read
        # page merely graze each other, and grazing is a boundary.
        if overlap >= LABEL_MERGE_FRAC * max(1.0, narrower):
            groups[-1].append(b)
        else:
            groups.append([b])
    spans = [(min(g[0] for g in grp), max(g[1] for g in grp)) for grp in groups]
    if len(spans) < 2:
        return []
    edges = [spans[0][0] - 2.0]
    for left, right in zip(spans, spans[1:]):
        if right[0] > left[1]:
            edges.append((left[1] + right[0]) / 2.0)
        else:
            edges.append(((left[0] + left[1]) / 2.0
                          + (right[0] + right[1]) / 2.0) / 2.0)
    edges.append(spans[-1][1] + 2.0)
    return [(a, b) for a, b in zip(edges, edges[1:])
            if b - a >= MIN_COLUMN_WIDTH_PT]


def _longest_monotone(ticks: Sequence[Tuple[float, float]], rising: bool
                      ) -> List[Tuple[float, float]]:
    """The longest run of ticks whose values move one way down the page.

    A band of numbers is rarely a ruler and nothing else: an optical read
    puts a stray digit in the band, a form prints a sample number beside the
    scale, a tick is read as "13-" and another as "l3". Demanding that EVERY
    number in the band rise throws a nineteen-tick ruler away for one stray.
    The longest rising run is kept and the rest reported as dropped.
    """
    ordered = sorted(ticks)
    n = len(ordered)
    if n == 0:
        return []
    best = [1] * n
    prev = [-1] * n
    for i in range(n):
        for j in range(i):
            better = (ordered[j][1] < ordered[i][1] if rising
                      else ordered[j][1] > ordered[i][1])
            if better and best[j] + 1 > best[i]:
                best[i] = best[j] + 1
                prev[i] = j
    end = max(range(n), key=lambda i: best[i])
    out: List[Tuple[float, float]] = []
    while end >= 0:
        out.append(ordered[end])
        end = prev[end]
    return list(reversed(out))


#: How much of a band of numbers the monotone run must account for before the
#: band is read as a ruler. Below it the band is a column of values that
#: happens to contain a rising stretch, not a scale.
MIN_MONOTONE_FRAC = 0.60


def _fit_ruler(ticks: Sequence[Tuple[float, float]], rising: bool):
    """``(ticks, slope, intercept, residual, step, regular, dropped)``or None."""
    kept = _longest_monotone(ticks, rising)
    if len(kept) < MIN_RULER_TICKS:
        return None
    if len(kept) < MIN_MONOTONE_FRAC * len(ticks):
        return None
    slope, intercept, resid = _fit(kept)
    if not slope or (slope > 0) != rising:
        return None
    values = [v for _, v in kept]
    steps = [abs(b - a) for a, b in zip(values, values[1:])]
    step = sum(steps) / len(steps)
    if step <= 0 or resid > MAX_RULER_RESIDUAL_FRAC * step:
        return None
    regular = all(abs(x - step) <= REGULAR_STEP_TOLERANCE * step for x in steps)
    return kept, slope, intercept, resid, step, regular, len(ticks) - len(kept)


def _tick_clusters(lines: Sequence[TextLine]
                   ) -> List[Tuple[List[Tuple[float, float]], float, float]]:
    """Lone numbers grouped into the vertical bands they stand in."""
    marks = []
    for ln in lines:
        value = _single_number(ln.text)
        if value is None:
            continue
        b = ln.bbox
        marks.append((b[0], b[2], (b[1] + b[3]) / 2.0, b[1], value))
    marks.sort()
    groups: List[List[Tuple[float, float, float, float, float]]] = []
    for m in marks:
        if groups and m[0] <= max(g[1] for g in groups[-1]) + 1.0:
            groups[-1].append(m)
        else:
            groups.append([m])
    return [([(m[2], m[4]) for m in g],
             min(m[0] for m in g), max(m[1] for m in g))
            for g in groups if len(g) >= MIN_RULER_TICKS]


def _best_rising_run(lines: Sequence[TextLine]
                     ) -> Optional[Tuple[List[Tuple[float, float]], float, float]]:
    """The band of numbers that most looks like a printed depth ruler.

    Used only where the page is not ruled and there is no column to hang the
    search on: every band of lone numbers is fitted, the ones that do not
    make a straight rising line are dropped, and what is left is judged on
    how many ticks it has and whether its steps are even.
    """
    best = None
    for ticks, bx0, bx1 in _tick_clusters(lines):
        fitted = _fit_ruler(ticks, rising=True)
        if fitted is None:
            continue
        kept, _slope, _intercept, _resid, _step, regular, _dropped = fitted
        score = (1.5 if regular else 0.0) + min(len(kept), 20) / 10.0
        if best is None or score > best[0]:
            best = (score, kept, bx0, bx1)
    return (best[1], best[2], best[3]) if best else None


def _fit(points: Sequence[Tuple[float, float]]) -> Tuple[float, float, float]:
    """Least-squares ``value = intercept + slope * y``; max abs residual."""
    n = len(points)
    sy = sum(p[0] for p in points)
    sv = sum(p[1] for p in points)
    syy = sum(p[0] * p[0] for p in points)
    syv = sum(p[0] * p[1] for p in points)
    den = n * syy - sy * sy
    if abs(den) < 1e-9:
        return 0.0, 0.0, float("inf")
    slope = (n * syv - sy * sv) / den
    intercept = (sv - slope * sy) / n
    resid = max(abs(v - (intercept + slope * y)) for y, v in points)
    return slope, intercept, resid


def _ruler_candidates(cells_by_column: Dict[str, List[Tuple[float, float]]],
                      columns: Dict[str, Column], page_index: int,
                      ) -> Tuple[List[Ruler], List[Ruler]]:
    """Every column whose numbers make a straight line, scored.

    ``increasing`` candidates are depth rulers (depth grows down the page),
    ``decreasing`` ones are elevation rulers. A column of layer-contact depths
    fits just as straight a line as a printed ruler — it is the same map —
    so the score prefers the one with EVEN value steps, which is what tells a
    ruler from a sorted list of contacts.
    """
    up: List[Tuple[float, Ruler]] = []
    down: List[Tuple[float, Ruler]] = []
    for col_id, ticks in cells_by_column.items():
        if len(ticks) < MIN_RULER_TICKS:
            continue
        up_fit = _fit_ruler(ticks, rising=True)
        down_fit = _fit_ruler(ticks, rising=False)
        fitted = up_fit or down_fit
        if up_fit and down_fit:
            fitted = up_fit if len(up_fit[0]) >= len(down_fit[0]) else down_fit
        if fitted is None:
            continue
        kept, slope, intercept, resid, step, regular, dropped = fitted
        rising = slope > 0
        ticks = kept
        col = columns.get(col_id)
        names = col.names if col else ()
        kind = "depth" if rising else "elevation"
        named = ((kind == "depth" and "depth" in names)
                 or (kind == "elevation" and "elevation" in names))
        if kind == "elevation" and not named:
            # Any column of numbers that happens to fall can be fitted —
            # dry unit weight down a stiffening profile fits beautifully. An
            # elevation scale is only claimed where a header says elevation.
            continue
        # What a ruler is, in order of weight: a column the form CALLS
        # depth, then one whose numbers step evenly, then one with many
        # ticks. The header DOMINATES, because the columns that fit a
        # straight line without being the scale are numerous -- layer-contact
        # depths, elevations, sample numbers, a plot axis -- and several of
        # them carry more ticks than the ruler does. The two later terms are
        # there to choose BETWEEN columns the form calls depth: a contact
        # column is called depth too and fits the same line, but it steps
        # unevenly and is short, and reading it as the ruler costs a fifth of
        # a metre because its labels are set against the contacts rather than
        # centred on their own ticks.
        score = (HEADER_NAMES_IT if named else 0.0)
        score += (1.5 if regular else 0.0) + min(len(ticks), 20) / 10.0
        conf = max(CONF_RULER_FLOOR,
                   min(0.98, 1.0 - resid / (MAX_RULER_RESIDUAL_FRAC * step)))
        ruler = Ruler(
            page=page_index, kind=kind, column_id=col_id, slope=slope,
            intercept=intercept, residual=resid, step=step,
            ticks=tuple(ticks), unit=(col.unit if col else None),
            unit_source=("header" if col and col.unit else None),
            confidence=conf,
            evidence=_compact({"regular_steps": regular,
                               "named_column": named,
                               "dropped_ticks": dropped or None,
                               "header": col.header if col else None}))
        (up if rising else down).append((score, ruler))
    up.sort(key=lambda sr: -sr[0])
    down.sort(key=lambda sr: -sr[0])
    return [r for _, r in up], [r for _, r in down]


_SHAPES = (
    ("blows", re.compile(r"^\s*\d{1,3}\s*[-+/]\s*\d{1,3}\s*[-+/]\s*\d{1,3}")),
    ("blows", re.compile(r"^\s*\d{1,3}\s*/\s*\d{1,2}\s*(in|\"|mm|cm)?\s*$",
                         re.I)),
    ("n_value", re.compile(r"\bN\s*[=:]\s*\d", re.I)),
    ("recovery", re.compile(r"\bREC\s*[=:]", re.I)),
    ("rqd", re.compile(r"\bRQD\s*[=:]", re.I)),
    ("water_content", re.compile(r"\b(MC|WC|W)\s*[=:]\s*\d", re.I)),
    ("liquid_limit", re.compile(r"\bLL\s*[=:]\s*\d", re.I)),
    ("plastic_limit", re.compile(r"\bPL\s*[=:]\s*\d", re.I)),
    ("plasticity_index", re.compile(r"\bPI\s*[=:]\s*\d", re.I)),
)


#: A cell counts as prose at this many words, and a column as a prose column
#: at this many such cells. A description band is the only column on a log
#: that holds sentences; a value column holds tokens.
PROSE_WORDS = 3
MIN_PROSE_CELLS = 2


def _is_prose(text: str) -> bool:
    words = str(text or "").split()
    if len(words) < PROSE_WORDS:
        return False
    letters = sum(ch.isalpha() for ch in text)
    return letters >= 0.6 * max(1, len(text.replace(" ", "")))


def _refine_by_values(columns: List[Column],
                      texts: Dict[str, List[str]]) -> List[Column]:
    """Name a column from the SHAPE of what it holds, when its header did not.

    A column headed "FIELD TEST RESULTS" or "SAMPLING DATA" is generic on the
    page and specific in fact: it holds "5-9-12" and "N=6". The refinement
    mostly ADDS names, and what it adds is recorded as ``names_from_values``
    so a reader can see which names came off the header and which off the ink.

    The one name it takes AWAY is a numeric one from a column full of
    sentences. A description band is often unheaded — the only label above it
    belongs to the narrow strip of layer-contact depths printed inside its
    left margin — and a column of prose is not a depth column whatever the
    label above it says.
    """
    out: List[Column] = []
    for col in columns:
        cells = list(texts.get(col.id, ()))
        found: List[str] = []
        counts: Dict[str, int] = {}
        if set(col.names) <= _GENERIC_NAMES:
            for text in cells:
                for name, rx in _SHAPES:
                    if name in col.names:
                        continue
                    if rx.search(text):
                        if name not in found:
                            found.append(name)
                        counts[name] = counts.get(name, 0) + 1
        added = [n for n in found if counts.get(n, 0) >= MIN_SHAPE_CELLS]
        prose = sum(1 for text in cells if _is_prose(text))
        names = list(col.names)
        evidence = dict(col.evidence)
        if prose >= MIN_PROSE_CELLS and "description" not in names:
            dropped = [n for n in names
                       if n in ("depth", "elevation", "n_value", "blows",
                                "recovery", "rqd", "fines", "water_content",
                                "dry_unit_weight")]
            names = ["description"] + [n for n in names if n not in dropped]
            evidence["description_from_prose"] = prose
            if dropped:
                evidence["dropped_by_prose"] = dropped
            added = []
        if added:
            names = names + added
            evidence["names_from_values"] = list(added)
        if tuple(names) == tuple(col.names):
            out.append(col)
            continue
        out.append(Column(
            id=col.id, page=col.page, x0=col.x0, x1=col.x1,
            name=names[0] if names else "other", names=tuple(names),
            header=col.header, unit=col.unit,
            confidence=max(col.confidence, CONF_HEADER_VALUES),
            evidence=evidence))
    return out


def _ruler_from_run(page_index: int,
                    run: Tuple[List[Tuple[float, float]], float, float],
                    columns: Sequence[Column]) -> Ruler:
    """A ruler built from the band of evenly stepping numbers itself."""
    ticks, bx0, bx1 = run
    ticks = sorted(ticks)
    slope, intercept, resid = _fit(ticks)
    values = [v for _, v in ticks]
    steps = [b - a for a, b in zip(values, values[1:])]
    step = sum(steps) / len(steps) if steps else 1.0
    centre = (bx0 + bx1) / 2.0
    owner = None
    for col in columns:
        if col.x0 - 0.5 <= centre < col.x1 + 0.5:
            owner = col
            break
    unit = owner.unit if owner else None
    if unit is None:
        for col in columns:
            if "depth" in col.names and col.unit:
                unit = col.unit
                break
    conf = max(CONF_RULER_FLOOR,
               min(0.98, 1.0 - resid / max(1e-9,
                                           MAX_RULER_RESIDUAL_FRAC * step)))
    return Ruler(
        page=page_index, kind="depth",
        column_id=owner.id if owner else None, slope=slope,
        intercept=intercept, residual=resid, step=step, ticks=tuple(ticks),
        unit=unit, unit_source=("header" if unit else None), confidence=conf,
        evidence={"found_before_columns": True,
                  "x": [round(bx0, 1), round(bx1, 1)]})


def _read_page(doc, index: int) -> _PageGrid:
    """Everything one page of a log says about itself, geometry first."""
    content = doc.page(index, tables=False)
    pg = _PageGrid(page=index, width=content.width, height=content.height)
    if content.text_sources:
        pg.text_source = content.text_sources[0]

    lines: List[TextLine] = []
    skew = 0
    for ln in content.lines:
        if not (ln.text or "").strip():
            continue
        if ln.source == SOURCE_CAD_HIDDEN:
            continue
        if _snap_rotation(ln.rotation) is None:
            skew += 1
            continue
        lines.append(ln)
    if skew:
        pg.warnings.append(
            f"page {index}: {skew} text line(s) run diagonally across the page "
            f"(a watermark or a stamp) and were left out of the grid")
    if not lines:
        pg.warnings.append(
            f"page {index}: no text to place — read it with an OCR or Azure "
            f"Document Intelligence text source, or look at the page")
        return pg
    if pg.text_source in (SOURCE_AZURE_DI, SOURCE_OCR):
        pg.warnings.append(
            f"page {index}: the text was read optically ({pg.text_source}); "
            f"boxes and column edges are softer than on an embedded text layer")
    try:
        fitz_page = doc._doc[index]
    except Exception:           # pragma: no cover - defensive
        fitz_page = None
    if fitz_page is not None and int(getattr(fitz_page, "rotation", 0)):
        pg.warnings.append(
            f"page {index}: the page is stored rotated "
            f"{int(fitz_page.rotation)} degrees; every box here is in the "
            f"displayed frame, the page as a viewer shows it")

    h_rules: List[_Rule] = []
    v_runs: List[Tuple[float, float, float]] = []
    if fitz_page is not None:
        h_rules, v_rules = page_rules(fitz_page)
        v_runs = _merge_edges(v_rules)
    pg.horizontal = h_rules

    bands, ruled_top, ruled_bottom = _grid_columns(v_runs, h_rules, pg.height)
    ruled = bool(bands)
    provisional = None
    if not ruled:
        pg.warnings.append(
            f"page {index}: no ruled column edges were found; the columns "
            f"below come from the header labels alone and their x bands are "
            f"approximate")

    # -- the header band and the body ---------------------------------------
    full_width: List[float] = []
    if ruled:
        span = bands[-1][1] - bands[0][0]
        full_width = sorted(
            r.a for r in h_rules
            if r.length >= FULL_WIDTH_FRAC * span
            and ruled_top - 2.0 <= r.a <= ruled_bottom + 2.0)

    # Numbers are needed to find the body top, and the body top is needed to
    # know which numbers are cells: the ticks are found first over the whole
    # ruled area, and the band above the topmost tick is then the header.
    def in_bands(x_centre: float) -> Optional[int]:
        for i, (a, b) in enumerate(bands):
            if a - 0.5 <= x_centre < b + 0.5:
                return i
        return None

    numeric_ys: List[float] = []
    if ruled:
        for ln in lines:
            b = ln.bbox
            if _single_number(ln.text) is None:
                continue
            if in_bands((b[0] + b[2]) / 2.0) is None:
                continue
            if ruled_top <= b[1] and b[3] <= ruled_bottom + 2.0:
                numeric_ys.append(b[1])
    first_tick = min(numeric_ys) if numeric_ys else None

    body_top: Optional[float] = None
    if ruled:
        # The rules that cross the whole form are its top border, the line
        # under the header band, and its foot. The header band is a row or
        # two of labels, never a quarter of the form, so the divider is the
        # LOWEST full-width rule inside the top quarter of the ruled area.
        # It is found from the drawn form alone: looking for the first number
        # instead would be led astray by the axis labels of a plotted column,
        # which are printed in the header band and are numbers.
        limit = ruled_top + HEADER_BAND_MAX_FRAC * (ruled_bottom - ruled_top)
        candidates = [y for y in full_width if ruled_top + 2.0 < y <= limit]
        best = (-1.0, ruled_top, ruled_top)
        for y in candidates:
            higher = [h for h in full_width if h < y - 2.0]
            top = max(higher) if higher else ruled_top
            band = [ln for ln in lines
                    if top - 1.0 <= (ln.bbox[1] + ln.bbox[3]) / 2.0 <= y + 1.0]
            named = sum(1 for (a, b) in bands
                        if classify_header(_header_text(band, a, b))[0])
            # The band that NAMES the most columns is the header band. A form
            # can be bordered above its header by any number of rules — the
            # page frame, the title block, a groundwater table — and only one
            # of the bands between them holds the column labels.
            if named > best[0]:
                best = (named, y, top)
        _, body_top, head_top = best
        pg.body_bottom = ruled_bottom
        pg.header_band = (min(head_top, body_top), body_top)
    pg.body_top = body_top

    # -- columns -------------------------------------------------------------
    if ruled and pg.header_band is not None:
        top, bottom = pg.header_band
        header_lines = [ln for ln in lines
                        if top - 1.0 <= (ln.bbox[1] + ln.bbox[3]) / 2.0
                        <= bottom + 1.0]
    else:
        provisional = _best_rising_run(lines)
        run = provisional[0] if provisional else None
        header_lines = []
        if run is not None:
            # With no rule to mark it, the body begins one tick above the
            # first tick: the ruler itself says where its own scale starts,
            # and a band measured from the first LABEL instead would reach
            # down into the first layer's description.
            ticks = sorted(run)
            slope, intercept, _resid = _fit(ticks)
            step = (ticks[-1][1] - ticks[0][1]) / max(1, len(ticks) - 1)
            body_top = ticks[0][0] - (step / slope if slope else 0.0)
            header_lines = [ln for ln in lines
                            if body_top - HEADER_BAND_PT
                            <= (ln.bbox[1] + ln.bbox[3]) / 2.0 < body_top]
    if not ruled:
        bands = _bands_from_headers(header_lines)
        if bands and header_lines:
            pg.body_top = body_top
            pg.header_band = (min(ln.bbox[1] for ln in header_lines), body_top)
            pg.body_bottom = max(ln.bbox[3] for ln in lines) + 1.0

    if not bands:
        pg.warnings.append(
            f"page {index}: no columns could be laid out — nothing on this "
            f"page is placed")
        return pg

    pg.header_lines = list(header_lines)
    columns: List[Column] = []
    for i, (x0, x1) in enumerate(bands):
        header = _header_text(header_lines, x0, x1)
        names, unit = classify_header(header)
        conf = CONF_HEADER_CANONICAL if names else CONF_COLUMN_UNNAMED
        columns.append(Column(
            id=f"p{index}c{i}", page=index, x0=x0, x1=x1,
            name=names[0] if names else "other", names=names,
            header=header, unit=unit, confidence=conf,
            evidence=_compact({"edges": "ruling" if ruled else "header_text"})))

    # -- cells ---------------------------------------------------------------
    body_bottom = pg.body_bottom if pg.body_bottom is not None else pg.height
    body_top_v = pg.body_top if pg.body_top is not None else 0.0
    by_column: Dict[str, List[TextLine]] = {c.id: [] for c in columns}
    for ln in lines:
        b = ln.bbox
        centre_y = (b[1] + b[3]) / 2.0
        if centre_y < body_top_v or centre_y > body_bottom + 1.0:
            pg.outside.append(ln)
            continue
        idx = in_bands((b[0] + b[2]) / 2.0)
        if idx is None:
            pg.outside.append(ln)
            continue
        by_column[columns[idx].id].append(ln)

    columns = _refine_by_values(
        columns, {cid: [ln.text for ln in lns] for cid, lns in by_column.items()})
    pg.columns = columns
    col_by_id = {c.id: c for c in columns}

    # -- the ruler -----------------------------------------------------------
    ticks_by_column: Dict[str, List[Tuple[float, float]]] = {}
    for cid, lns in by_column.items():
        picked: List[Tuple[float, float]] = []
        for ln in lns:
            value = _single_number(ln.text)
            if value is None:
                continue
            picked.append(((ln.bbox[1] + ln.bbox[3]) / 2.0, value))
        if len(picked) >= MIN_RULER_TICKS:
            ticks_by_column[cid] = picked
    rising, falling = _ruler_candidates(ticks_by_column, col_by_id, index)
    if provisional is not None:
        # On an unruled page the ruler was found before the columns were, by
        # looking for the one band of numbers that steps evenly down the
        # page. That search did not depend on column edges the page never
        # drew, so it is the one to trust; the columns only say which band
        # it stands in.
        rising = [_ruler_from_run(index, provisional, columns)] + [
            r for r in rising if r.column_id != rising[0].column_id]             if rising else [_ruler_from_run(index, provisional, columns)]
    if rising:
        pg.ruler = rising[0]
        if len(rising) > 1:
            other = rising[1]
            if abs(other.slope - pg.ruler.slope) > (
                    RULER_SCALE_TOLERANCE * abs(pg.ruler.slope)):
                pg.warnings.append(
                    f"page {index}: two columns fit a depth scale and they "
                    f"disagree ({pg.ruler.slope:.3f} vs {other.slope:.3f} per "
                    f"point); the one headed {pg.ruler.evidence.get('header')!r} "
                    f"was used")
    else:
        pg.warnings.append(
            f"page {index}: {NO_RULER} — no column holds three or more "
            f"numbers that fall on a straight line, so nothing on this page "
            f"carries a depth")
    if falling:
        pg.elevation_ruler = falling[0]

    # -- the unit ------------------------------------------------------------
    if pg.ruler is not None and pg.ruler.unit is None:
        for col in columns:
            if "depth" in col.names and col.unit:
                pg.ruler = Ruler(**{**pg.ruler.__dict__, "unit": col.unit,
                                    "unit_source": "depth column header"})
                break

    # -- place every remaining line -----------------------------------------
    ruler = pg.ruler
    elev = pg.elevation_ruler
    for cid, lns in by_column.items():
        col = col_by_id[cid]
        for ln in lns:
            b = ln.bbox
            depth = depth_top = depth_bottom = None
            elevation = None
            if ruler is not None:
                depth = ruler.value_at((b[1] + b[3]) / 2.0)
                depth_top = ruler.value_at(b[1])
                depth_bottom = ruler.value_at(b[3])
            if elev is not None:
                elevation = elev.value_at((b[1] + b[3]) / 2.0)
            conf = min(col.confidence,
                       ruler.confidence if ruler is not None else 0.3,
                       float(ln.confidence))
            pg.cells.append(Cell(
                page=index, column=col.name, column_id=cid,
                text=ln.text.strip(), bbox=tuple(b), depth=depth,
                depth_top=depth_top, depth_bottom=depth_bottom,
                numbers=numbers_in(ln.text), elevation=elevation,
                confidence=conf, source=ln.source))
    pg.cells.sort(key=lambda c: (c.bbox[1], c.bbox[0]))
    # A lone number set into the left margin of the description column is the
    # layer-contact depth the form prints there. It is a depth cell that
    # happens to share a ruled column with the prose, and it is named one.
    if pg.text_source in (SOURCE_AZURE_DI, SOURCE_OCR) and pg.cells:
        heights = sorted(c.bbox[3] - c.bbox[1] for c in pg.cells)
        pg.slack = max(BOUNDARY_SLACK_PT,
                       0.40 * heights[len(heights) // 2])
    pg.margin_ticks = _margin_ticks(pg)
    if pg.margin_ticks:
        pg.cells = [c if tuple(c.bbox) not in pg.margin_ticks
                    else Cell(**{**c.__dict__, "column": "depth"})
                    for c in pg.cells]
    return pg


def _margin_ticks(pg: _PageGrid) -> set:
    """Boxes of the layer-contact depths printed in the description's margin."""
    desc = _description_column(pg)
    if desc is None:
        return set()
    cells = [c for c in pg.cells if c.column_id == desc.id]
    prose_x0 = [c.bbox[0] for c in cells if _is_prose(c.text)]
    if not prose_x0:
        return set()
    margin = min(prose_x0) - TICK_MARGIN_PT
    return {tuple(c.bbox) for c in cells
            if c.bbox[0] <= margin and _single_number(c.text) is not None}


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

def _description_column(pg: _PageGrid) -> Optional[Column]:
    named = [c for c in pg.columns if "description" in c.names]
    if named:
        return max(named, key=lambda c: c.width)
    # No header said so: the widest column that holds prose is the one.
    prose = []
    for c in pg.columns:
        words = sum(len(cell.text.split()) for cell in pg.cells
                    if cell.column_id == c.id)
        if words >= 12:
            prose.append((words, c))
    if not prose:
        return None
    return max(prose, key=lambda wc: (wc[0], wc[1].width))[1]


def _is_underline(rule: _Rule, texts: Sequence[Cell]) -> bool:
    """Is this rule the underline of a text line rather than a stratum line?"""
    for cell in texts:
        b = cell.bbox
        height = max(1.0, b[3] - b[1])
        if not (b[3] - UNDERLINE_ABOVE_FRAC * height
                <= rule.a <= b[3] + UNDERLINE_BELOW_PT):
            continue
        cover = min(rule.hi, b[2]) - max(rule.lo, b[0])
        if cover >= UNDERLINE_COVER_FRAC * max(1.0, b[2] - b[0]):
            return True
    return False


def _page_boundaries(pg: _PageGrid, desc: Column
                     ) -> Tuple[List[Tuple[float, float, str]], set]:
    """``(y, depth, source)`` for every layer boundary this page states.

    A boundary's y is where the boundary IS and its depth is what the page
    SAYS it is: for a stratum rule the y is the rule and the depth is the
    ruler's reading of it, and for a printed tick it is the other way round —
    the depth is the number the form prints, and the y is where the ruler puts
    that number, not where the label happened to fit.

    Also returns the boxes of the ticks found inside the description column,
    so that a layer-contact depth is not joined into the prose beside it.
    """
    out: List[Tuple[float, float, str]] = []
    ticks: set = set()
    ruler = pg.ruler
    if ruler is None:
        return out, ticks
    desc_cells = [c for c in pg.cells if c.column_id == desc.id]
    width = max(1.0, desc.width)
    for rule in pg.horizontal:
        if rule.a < (pg.body_top or 0.0) - 1.0:
            continue
        if pg.body_bottom is not None and rule.a > pg.body_bottom - 2.0:
            continue
        cover = min(rule.hi, desc.x1) - max(rule.lo, desc.x0)
        if cover < STRATUM_COVER_FRAC * width:
            continue
        if _is_underline(rule, desc_cells):
            continue
        out.append((rule.a, ruler.value_at(rule.a), "stratum_rule"))

    # A depth tick printed beside the description: a number standing alone in
    # a depth column that is NOT the ruler. Its printed value is exact, so it
    # is used instead of the ruler's reading of its y.
    for col in pg.columns:
        if col.id == (ruler.column_id or ""):
            continue
        if "depth" not in col.names:
            continue
        for cell in pg.cells:
            if cell.column_id != col.id:
                continue
            value = _single_number(cell.text)
            if value is None:
                continue
            y = ruler.y_at(value)
            out.append((y if y is not None else cell.bbox[3], value,
                        "depth_tick"))

    # The same tick, printed INSIDE the description column's left margin —
    # which is where a form puts it when the layer-contact depths share the
    # description's ruled column.
    for cell in desc_cells:
        if tuple(cell.bbox) not in pg.margin_ticks:
            continue
        value = _single_number(cell.text)
        if value is None:
            continue
        ticks.add(tuple(cell.bbox))
        y = ruler.y_at(value)
        out.append((y if y is not None else cell.bbox[3], value, "depth_tick"))
    return out, ticks


def _join_descriptions(first: str, second: str) -> str:
    """Join a layer's text across a sheet break without saying it twice.

    A continuation sheet reprints the head of the layer it carries on from,
    so the second page's first words are often the first page's last words.
    A repeated run of four words or more is dropped; a shorter coincidence
    ("brown, moist" twice) is left alone, because it may well be the log
    saying the same thing about two different depths.
    """
    a, b = (first or "").split(), (second or "").split()
    if not a or not b:
        return " ".join(a + b).strip()
    for n in range(min(len(a), len(b)), 3, -1):
        if [w.lower() for w in a[-n:]] == [w.lower() for w in b[:n]]:
            b = b[n:]
            break
    return " ".join(a + b).strip()


#: A classification symbol is "at the top of its layer" when it sits within
#: this many of its own line heights below a boundary already known from a
#: drawn rule or a printed tick. Measured on the corpus: where the symbol is
#: top-aligned it sits 0.6 line heights below the contact; where it is
#: centred on its layer it sits 6 to 12 line heights below.
USCS_TOP_ALIGNED_LINES = 2.0


def _uscs_boundaries(pg: _PageGrid, known: Sequence[Tuple[float, float, str]]
                     ) -> List[Tuple[float, float, str]]:
    """Boundaries from a change of classification symbol, where that is safe.

    A form with its own symbol column states a material change every time the
    symbol changes, and on some templates that is the ONLY statement of a
    contact the page makes. But only some templates print the symbol against
    the top of its layer; others centre it in the band, where reading it as a
    contact would put every boundary in the middle of a layer. So the column
    is CALIBRATED first against the contacts already known from drawn rules
    and printed ticks, and used only if it passes.
    """
    ruler = pg.ruler
    if ruler is None:
        return []
    column = None
    for col in pg.columns:
        if "uscs" in col.names and "description" not in col.names:
            column = col
            break
    if column is None:
        return []
    cells = sorted((c for c in pg.cells if c.column_id == column.id),
                   key=lambda c: c.bbox[1])
    if len(cells) < 2:
        return []
    heights = sorted(c.bbox[3] - c.bbox[1] for c in cells)
    line = max(1.0, heights[len(heights) // 2])
    anchors = [y for y, _d, source in known if source != "page_top"]
    if len(anchors) < 1:
        return []
    offsets = []
    for y in anchors:
        below = [c.bbox[1] - y for c in cells if c.bbox[1] >= y - line]
        if below:
            offsets.append(min(below))
    if not offsets:
        return []
    offsets.sort()
    if offsets[len(offsets) // 2] > USCS_TOP_ALIGNED_LINES * line:
        return []
    out: List[Tuple[float, float, str]] = []
    previous = None
    for cell in cells:
        text = cell.text.strip()
        if previous is not None and text != previous:
            out.append((cell.bbox[1], ruler.value_at(cell.bbox[1]),
                        "uscs_change"))
        previous = text
    return out


def _build_layers(grids: Sequence[_PageGrid], unit: Optional[str]
                  ) -> Tuple[List[Layer], List[str]]:
    warnings: List[str] = []
    marks: List[Tuple[int, float, float, str]] = []    # page, y, depth, source
    desc_by_page: Dict[int, Column] = {}
    tick_boxes: set = set()
    for pg in grids:
        desc = _description_column(pg)
        if desc is None:
            continue
        desc_by_page[pg.page] = desc
        if pg.ruler is None:
            continue
        top_y = pg.body_top or 0.0
        top_depth = pg.ruler.value_at(top_y)
        if abs(top_depth) <= 0.10 * pg.ruler.step:
            top_depth = 0.0
        marks.append((pg.page, top_y, top_depth, "page_top"))
        bounds, ticks = _page_boundaries(pg, desc)
        tick_boxes |= ticks
        bounds = bounds + _uscs_boundaries(pg, bounds)
        for y, depth, source in bounds:
            marks.append((pg.page, y, depth, source))
    if not desc_by_page:
        warnings.append("no description column was identified, so no layers "
                        "were read")
        return [], warnings
    if not marks:
        return [], warnings

    marks.sort(key=lambda m: (m[0], m[1]))
    steps = [pg.ruler.step for pg in grids if pg.ruler is not None]
    same_depth = 0.05 * (min(steps) if steps else 1.0)
    kept: List[Tuple[int, float, float, str]] = []
    for mark in marks:
        if (kept and kept[-1][0] == mark[0]
                and abs(mark[1] - kept[-1][1]) <= SAME_BOUNDARY_PT):
            # The stratum rule and the tick that labels it: keep the tick,
            # whose value the page PRINTS, over the ruler's reading of a y.
            if kept[-1][3] != "depth_tick" and mark[3] == "depth_tick":
                kept[-1] = (kept[-1][0], mark[1], mark[2], "depth_tick")
            continue
        kept.append(mark)

    # Every description line goes to the last boundary at or above it, which
    # is where the layer it describes begins.
    buckets: Dict[int, List[Tuple[int, float, float, str, BBox]]] = {
        i: [] for i in range(len(kept))}
    for pg in grids:
        desc = desc_by_page.get(pg.page)
        if desc is None or pg.ruler is None:
            continue
        page_marks = [(i, m) for i, m in enumerate(kept) if m[0] == pg.page]
        if not page_marks:
            continue
        for cell in pg.cells:
            if cell.column_id != desc.id:
                continue
            if tuple(cell.bbox) in tick_boxes:
                continue
            top = cell.bbox[1] + pg.slack
            chosen = None
            for i, m in page_marks:
                if m[1] <= top:
                    chosen = i
                else:
                    break
            if chosen is None:
                chosen = page_marks[0][0]
            buckets[chosen].append((pg.page, cell.bbox[1], cell.bbox[0],
                                    cell.text, cell.bbox))

    # A new sheet opens a boundary whether or not a layer starts there. One
    # that catches no description is the sheet break itself and not a layer,
    # and it must go before the bottoms are read off, or the layer above it
    # would be closed at a depth no layer begins.
    surviving = [i for i, m in enumerate(kept)
                 if buckets[i] or m[3] != "page_top" or i == 0]
    layers: List[Layer] = []
    for n, i in enumerate(surviving):
        page, y, depth, source = kept[i]
        nxt = surviving[n + 1] if n + 1 < len(surviving) else None
        bottom = kept[nxt][2] if nxt is not None else None
        texts = sorted(buckets[i])
        if not texts and source == "page_top" and layers:
            continue
        description = " ".join(t[3] for t in texts).strip()
        box = None
        if texts:
            box = (min(t[4][0] for t in texts), min(t[4][1] for t in texts),
                   max(t[4][2] for t in texts), max(t[4][3] for t in texts))
        pages = tuple(sorted({t[0] for t in texts})) or (page,)
        conf = {"stratum_rule": 0.85, "depth_tick": 0.85,
                "uscs_change": 0.7}.get(source, 0.5)
        layers.append(Layer(top=depth, bottom=bottom, description=description,
                            pages=pages, source=source, bbox=box,
                            confidence=conf))
    # A boundary at the foot of the last sheet opens nothing: it closes the
    # layer above it, which already carries it as its bottom.
    while layers and not layers[-1].description:
        layers.pop()
    # A layer opened only because a new sheet started, carrying text that
    # continues the layer above, is that same layer.
    merged: List[Layer] = []
    for ly in layers:
        if (merged and ly.source == "page_top"
                and merged[-1].bottom is not None
                and abs(merged[-1].bottom - ly.top) <= same_depth):
            prev = merged[-1]
            merged[-1] = Layer(
                top=prev.top, bottom=ly.bottom,
                description=_join_descriptions(prev.description,
                                               ly.description),
                pages=tuple(sorted(set(prev.pages) | set(ly.pages))),
                source=prev.source, bbox=prev.bbox,
                confidence=min(prev.confidence, ly.confidence))
            continue
        merged.append(ly)
    return merged, warnings


# ---------------------------------------------------------------------------
# Header fields
# ---------------------------------------------------------------------------

_KEY_VALUE = re.compile(r"([^:]{2,60}?)\s*:\s*([^:]*?)(?=\s{2,}[^:\s][^:]{1,58}:|$)")


#: Every field phrase, flattened, for the colon-less case.
_FIELD_PHRASES = {phrase: name
                  for name, phrases in _FIELD_KEYS.items()
                  for phrase in phrases}


def _exact_field(key: str) -> Optional[str]:
    """The field a line names when the line is NOTHING but a field name.

    Some forms label their boxes without punctuation — a line reading
    "DRILLING METHOD" and the method set beside it. Pairing any two
    neighbouring lines would turn a log into nonsense, so only a line that IS
    one of the known field names, exactly, is read this way.
    """
    return _FIELD_PHRASES.get(_norm(key).strip())


def _canonical_field(key: str) -> Optional[str]:
    flat = _norm(key).strip()
    if not flat:
        return None
    for name, phrases in _FIELD_KEYS.items():
        if flat in phrases:
            return name
    for name, phrases in _FIELD_KEYS.items():
        for phrase in phrases:
            if flat.endswith(" " + phrase) or flat.startswith(phrase + " "):
                return name
    return None


def _field_key(raw: str) -> str:
    return " ".join(str(raw or "").split()).strip(" .-")


#: A log names itself in its own title as often as in a labelled field:
#: "BORING LOG NO. B-1", "TEST PIT TP-02", "LOG OF BORING B-4".
_TITLE_PATTERNS = (
    ("test_pit_id", re.compile(
        r"(?:TEST\s*PIT|TRIAL\s*PIT|PUITS|CALICATA)\s*(?:LOG\s*)?"
        r"(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9_.\-/]*)\s*$", re.I)),
    ("boring_id", re.compile(
        r"(?:TEST\s*BORING|BORING|BOREHOLE|SONDAGE|SONDEO)\s*(?:LOG\s*)?"
        r"(?:NO\.?|NUMBER|#)?\s*[:\-]?\s*([A-Z0-9][A-Z0-9_.\-/]*)\s*$", re.I)),
    # "Page 1 of 3" is how a log says which sheet of itself this is, and it
    # is printed as a sentence rather than as a labelled field.
    ("sheet", re.compile(
        r"^(?:PAGE|SHEET|FEUILLE|HOJA)\s+(\d+\s*(?:OF|/|DE|SUR)\s*\d+)\s*$",
        re.I)),
)


#: How far a label may stand from the value it labels. Measured on the
#: corpus: a value set beside its label sits 7 to 60 pt from it (the boring
#: number is set in large type at the far end of its own box) and a value
#: set under it a fraction of a point below, while the nearest unrelated
#: text on a crowded form footer was 134 pt away and had been read as the
#: value before this cap.
VALUE_RIGHT_GAP_PT = 70.0
VALUE_BELOW_GAP_PT = 6.0
VALUE_BELOW_INDENT_PT = 12.0
#: Two stacked lines of the same form overlap by a fraction of a point,
#: because a text box carries leading its ink does not fill.
VALUE_BELOW_OVERLAP_PT = 3.0


def _value_beside(label: TextLine, lines: Sequence[TextLine]) -> str:
    """The value a label ending in a colon points at: beside it, or under it.

    A form lays a value against its label in one of two ways, and which one
    is a matter of how much room the box had. Beside is tried first because
    it is the commoner and the less ambiguous; under it is accepted only
    when the line starts at the label's own left margin.
    """
    b = label.bbox
    height = max(1.0, b[3] - b[1])
    best_right: Optional[Tuple[float, str]] = None
    best_below: Optional[Tuple[float, str]] = None
    for other in lines:
        if other is label or ":" in (other.text or ""):
            continue
        text = (other.text or "").strip()
        if not text:
            continue
        ob = other.bbox
        overlap = min(ob[3], b[3]) - max(ob[1], b[1])
        if overlap >= 0.5 * height and ob[0] >= b[2] - 1.0:
            gap = ob[0] - b[2]
            if gap <= VALUE_RIGHT_GAP_PT and (best_right is None
                                              or gap < best_right[0]):
                best_right = (gap, text)
        elif (abs(ob[0] - b[0]) <= VALUE_BELOW_INDENT_PT
                and -VALUE_BELOW_OVERLAP_PT <= ob[1] - b[3]
                <= VALUE_BELOW_GAP_PT):
            gap = ob[1] - b[3]
            if best_below is None or gap < best_below[0]:
                best_below = (gap, text)
    if best_right is not None:
        return best_right[1]
    return best_below[1] if best_below is not None else ""


#: A groundwater note is rarely a labelled field; it is a sentence with a
#: depth in it, printed wherever the form had room.
_GROUNDWATER_LINE = re.compile(
    r"(groundwater|ground water|water level|water table|water encountered"
    r"|nappe|niveau d.eau|nivel freatico)", re.I)

#: "Groundwater not encountered" is an observation too, and carries no
#: number to recognise it by.
_NO_WATER = re.compile(
    r"\b(not|no|none|never|dry|absent|aucune|ninguna)\b", re.I)


def _collect_fields(grids: Sequence[_PageGrid]
                    ) -> Tuple[Dict[str, str], Dict[str, Tuple[int, BBox]]]:
    """The key-value pairs printed outside the body of every page.

    Two shapes are read, and only two. A line that carries its own colon is
    split on it, more than once when it carries more than one pair ("Dates
    Started: 9 Nov 2022    Finished: 14 Nov 2022"). A line that ENDS in a
    colon takes as its value the nearest line to its right sharing its
    baseline, which is how a form lays a label against the box it labels.
    """
    fields: Dict[str, str] = {}
    boxes: Dict[str, Tuple[int, BBox]] = {}

    def put(key: str, value: str, page: int, bbox: BBox) -> None:
        key = _field_key(key)
        value = " ".join(str(value or "").split()).strip()
        if not key or not value or len(key) > 60:
            return
        if not any(ch.isalpha() for ch in key):
            # "11:35 AM" is a time, not a field called eleven.
            return
        name = _canonical_field(key) or key
        if name in fields and fields[name]:
            return
        fields[name] = value
        boxes[name] = (page, tuple(bbox))

    for pg in grids:
        # The header band is column headers, but a form often tucks a printed
        # fact in there too (a location, a latitude). A line carrying a colon
        # is that fact; one without is the column's name.
        pool = list(pg.outside) + [ln for ln in pg.header_lines
                                   if ":" in (ln.text or "")]
        lines = sorted(pool, key=lambda ln: (round(ln.bbox[1], 1), ln.bbox[0]))
        for ln in lines:
            text = ln.text.strip()
            for name, rx in _TITLE_PATTERNS:
                m = rx.search(text)
                if m and m.group(1):
                    put(name, m.group(1), pg.page, ln.bbox)
            if (_GROUNDWATER_LINE.search(text) and len(text) <= 120
                    and (numbers_in(text) or _NO_WATER.search(text))
                    and "groundwater" not in fields):
                put("groundwater", text, pg.page, ln.bbox)
            if ":" not in text:
                name = _exact_field(text)
                if name is not None:
                    put(text, _value_beside(ln, lines), pg.page, ln.bbox)
                continue
            if text.endswith(":"):
                put(text[:-1], _value_beside(ln, lines), pg.page, ln.bbox)
                continue
            for m in _KEY_VALUE.finditer(text):
                put(m.group(1), m.group(2), pg.page, ln.bbox)
    return fields, boxes


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

_UNIT_IN_TEXT = re.compile(
    r"\d(?:[.,]\d+)?\s*(ft\b|feet\b|foot\b|'|m\b|meters?\b|metres?\b)", re.I)


def _unit_from_text(grids: Sequence[_PageGrid]) -> Optional[str]:
    counts: Dict[str, int] = {}
    for pg in grids:
        for cell in pg.cells:
            for m in _UNIT_IN_TEXT.finditer(cell.text):
                token = m.group(1).lower().rstrip(".")
                unit = "ft" if token in ("ft", "feet", "foot", "'") else "m"
                counts[unit] = counts.get(unit, 0) + 1
    if not counts:
        return None
    best = max(counts.items(), key=lambda kv: kv[1])
    return best[0] if best[1] >= 2 else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def log_grid(doc, pages=None) -> LogGrid:
    """Read one boring or test-pit log — its pages together — as a grid.

    ``pages`` takes anything :func:`planlens.document.document.parse_pages`
    takes: a page number, a list, a ``"4-6,9"`` range string, or ``None`` for
    the whole document. Pass the pages of ONE log, continuation sheets
    included; the depths and the layers are joined across them in page order.
    """
    from planlens.document.document import parse_pages

    indexes = parse_pages(pages, doc.n_pages)
    grid = LogGrid(pages=list(indexes))
    grids = [_read_page(doc, i) for i in indexes]
    for pg in grids:
        if pg.ruler is None and not any(NO_RULER in w for w in pg.warnings):
            # Said on every page that has none, however early the reading
            # stopped. "No columns could be laid out" is a truer account of
            # what went wrong, but it is not the sentence a caller looking
            # for depths knows to look for.
            pg.warnings.append(
                f"page {pg.page}: {NO_RULER} — nothing on this page carries "
                f"a depth")
        grid.warnings.extend(pg.warnings)
        grid.columns.extend(pg.columns)
        grid.rows.extend(pg.cells)
        if pg.ruler is not None:
            grid.rulers[pg.page] = pg.ruler
        if pg.elevation_ruler is not None:
            grid.elevation_rulers[pg.page] = pg.elevation_ruler

    units = {r.unit for r in grid.rulers.values() if r.unit}
    if len(units) > 1:
        grid.warnings.append(
            f"the pages of this log state different depth units ({sorted(units)}); "
            f"depths are left in the unit each page prints")
    grid.unit = next(iter(units)) if len(units) == 1 else None
    if grid.unit is None and grid.rulers:
        guessed = _unit_from_text(grids)
        if guessed:
            grid.unit = guessed
            grid.warnings.append(
                f"no column header states the depth unit; {guessed} was read "
                f"off depths written into the log's own text")
        else:
            grid.warnings.append(
                "the depth unit is not stated on these pages and could not be "
                "read from their text; depths are in whatever the ruler prints")

    scales = [abs(r.slope) for r in grid.rulers.values()]
    if len(scales) > 1:
        lo, hi = min(scales), max(scales)
        if hi - lo > RULER_SCALE_TOLERANCE * hi:
            grid.warnings.append(
                f"the sheets of this log are drawn at different depth scales "
                f"({lo:.3f} to {hi:.3f} per point); each page was read with "
                f"its own ruler")

    layers, layer_warnings = _build_layers(grids, grid.unit)
    grid.layers = layers
    grid.warnings.extend(layer_warnings)
    grid.fields, grid.field_boxes = _collect_fields(grids)
    return grid
