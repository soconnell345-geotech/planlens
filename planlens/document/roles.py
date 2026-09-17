"""What each page of a report IS, and which work item it belongs to.

:mod:`planlens.document.structure` finds a stapled document's constituent
parts from what the pages print about themselves. This module goes one step
further and says what each page is FOR — narrative, a boring log, a laboratory
sheet, a calculation printout, a divider, a report bound inside another report
— and groups the pages into the **work items** a reader consumes one at a
time: one item per boring or test pit, one per laboratory sheet, one per calc
printout, one for the narrative, one per appended report.

It is rules over evidence this package already has, not a trained model. Three
kinds of evidence decide a page, in this order of authority:

1. **The document's own structure.** An appendix divider states what its
   appendix holds ("APPENDIX B — LABORATORY TEST DATA", "PART 2 — FIELD
   BOREHOLE LOGS", "APPENDIX D — CALCULATIONS"), and every page up to the next
   divider inherits that unless it says otherwise. A report bound inside an
   appendix ("APPENDIX C — GEOPHYSICAL SURVEY REPORT") takes every one of its
   pages, whatever those pages look like, because it is one appended document.
2. **What the page's own title says.** A log page names its boring or pit in
   its title block; a laboratory sheet names its test; a calculation printout
   carries a program banner. Titles are read from the page's largest type and
   its top band, never from a keyword anywhere on the page — a lab sheet
   quotes the boring it sampled, and a narrative discusses test pits.
3. **The page's shape**, as :class:`~planlens.document.model.PageSummary`
   measures it: ruled form, figure, scan, word density, images.

Nothing here is specific to a firm, a project or a template: the keyword
tables are the words the profession prints on these documents, in the
languages the documents are written in.

What is measured and what is not is stated in ``DESIGN.md`` ("Page roles and
work items"). The short version: the roles were scored against 4,147
hand-labelled pages of 14 real geotechnical reports, and the five roles a
downstream reader depends on — ``boring_log``, ``test_pit_log``, ``lab_test``,
``narrative`` and ``calculation`` — are held to precision and recall of 0.90.
The other thirteen are reported, not gated.

Usage::

    from planlens.document import open_document
    from planlens.document.roles import page_roles, document_items

    with open_document("report.pdf") as doc:
        for r in page_roles(doc):
            print(r.page, r.role, r.confidence, r.evidence)
        for item in document_items(doc):
            print(item.kind, item.pages, item.title)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.document.model import (
    SOURCE_AZURE_DI, PageSummary, _compact,
)

#: The role vocabulary. Fixed: a reader that cannot name a page's role must
#: say ``other`` rather than invent a word.
ROLES: Tuple[str, ...] = (
    "narrative",
    "figure",
    "plan",
    "profile",
    "boring_log",
    "test_pit_log",
    "cpt_log",
    "dcp_log",
    "lab_test",
    "field_test",
    "calculation",
    "appended_report",
    "photos",
    "divider",
    "cover",
    "letter",
    "toc",
    "other",
)

#: Roles that are subsurface exploration logs, in the order a mixed
#: exploration appendix is searched.
LOG_ROLES: Tuple[str, ...] = ("boring_log", "test_pit_log", "cpt_log",
                              "dcp_log")

#: Item kinds :func:`document_items` produces.
ITEM_KINDS: Tuple[str, ...] = (
    "narrative", "boring_log", "test_pit_log", "cpt_log", "dcp_log",
    "lab_test", "field_test", "calculation", "appended_report", "photos",
    "figures", "front_matter", "other",
)

#: Pages with fewer words than this can be a divider or a cover. A tab
#: page often lists its appendix's contents sheet by sheet, so the
#: ceiling sits well above a bare tab; a narrative page runs to 379
#: words in the median of the corpus this was measured on.
MAX_DIVIDER_WORDS = 140
MAX_COVER_WORDS = 220

#: Fraction of the page height that counts as the top / bottom band.
BAND_FRACTION = 0.18

#: How far after an exploration's name the word "log" may sit and still
#: be part of that name ("TEST PIT 4 LOG", "BORING B-12 LOG SHEET").
LOG_WORD_CHARS = 14

#: Confidence a page gets when its ROLE came from its appendix tab rather
#: than from anything the page itself says. Deliberately well below a page
#: that names itself: a tab is right about most of its appendix and wrong
#: about the summary table, the legend and the stray calculation bound into
#: it, and a reviewer reading these rows needs to see which labels are the
#: tab talking and which are the page talking.
INHERITED_CONFIDENCE = 0.6

#: Above this many words a page is carrying argument, not results.
WORKING_PAGE_WORDS = 120

#: How far into what a page calls itself its own figure number may sit.
#: A page LEADS with its caption; a calculation that refers to figure 3
#: somewhere in its working does not.
CAPTION_LEAD_CHARS = 40

#: How many pieces of evidence a cue found only in a page's running bands
#: needs before it overrules the page's appendix tab. Measured either way on
#: the corpus: a laboratory sheet naming the pit it sampled carries one or
#: two, a log form carries eight and up.
DECISIVE_WEIGHT = 8

#: Fewest words a page that prints its own page number needs to be prose:
#: a numbered page in the narrative's own series is already half the case.
NARRATIVE_NUMBERED_WORDS = 25

#: Words per square inch below which a text page is a heading sheet or a
#: table rather than prose.
NARRATIVE_DENSITY = 1.5

#: Fewest words a page of the report's prose carries. Below it the page
#: is a heading sheet, a table or a picture with a caption.
NARRATIVE_MIN_WORDS = 60

#: Above this many words a page is prose whatever it names. A plan
#: sheet with a block of notes beside it stays below it.
NARRATIVE_PROSE_WORDS = 200

#: A top-band line printed on this many pages or more is the document's
#: running header, not anything a particular page is saying.
RUNNING_HEADER_PAGES = 3

#: How far into a tab page's text its "FIGURES" heading may sit and still
#: be the tab's own name rather than an item in a list beneath it.
FIGURES_TAB_CHARS = 40

#: How far into a tab page's text a LIST word may sit. A tab that holds
#: the figures LEADS with FIGURES; a sentence that says "provided in
#: tables" is a sentence, and reading it as a tab hands the rest of the
#: report to the figures.
LIST_TAB_CHARS = 2

#: How far into a tab page's own text its tab word may sit. A divider says
#: what it is at the top of what it prints, not in a note at the bottom.
DIVIDER_TITLE_CHARS = 160

#: And how far in a bare "APPENDIX A" / "PART 2" may sit. Tighter,
#: because a laboratory sheet cites a test standard the same way —
#: "BS 1377 PART 4" halfway down a compaction result is not a tab.
DIVIDER_TOKEN_CHARS = 60


# ---------------------------------------------------------------------------
# Results
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PageRole:
    """One page's role, with the evidence that decided it.

    ``confidence`` is how much the rule that fired is worth, not a
    probability: 0.9 for a page that names itself, 0.75 for one that inherits
    its appendix's declaration, 0.4 for a guess from the page's shape alone.
    ``item_id`` names the work item the page belongs to (see :class:`Item`);
    it is ``None`` for a page that is not part of one — a divider, a cover.
    """

    page: int
    role: str
    confidence: float
    evidence: Dict[str, Any] = field(default_factory=dict)
    item_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {"page": self.page, "role": self.role,
                             "confidence": round(float(self.confidence), 2)}
        if self.item_id:
            d["item_id"] = self.item_id
        if self.evidence:
            d["evidence"] = dict(self.evidence)
        return d


@dataclass(frozen=True)
class Item:
    """One work item: the pages a reader takes in at once.

    A boring log printed "Page 1 of 3" is ONE item of three pages, not three
    logs; a multi-page consolidation test is one test; an appended report is
    one document however many pages it runs to.
    """

    id: str
    kind: str
    pages: List[int]
    title: Optional[str] = None
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def n_pages(self) -> int:
        return len(self.pages)

    def to_dict(self) -> Dict[str, Any]:
        d: Dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "pages": _compact_pages(self.pages),
            "n_pages": len(self.pages),
        }
        if self.title:
            d["title"] = self.title
        if self.evidence:
            d["evidence"] = dict(self.evidence)
        return d


def _compact_pages(pages: Sequence[int]) -> str:
    """``[4,5,6,9]`` as ``"4-6,9"``."""
    if not pages:
        return ""
    out: List[str] = []
    start = prev = pages[0]
    for p in list(pages)[1:]:
        if p == prev + 1:
            prev = p
            continue
        out.append(str(start) if start == prev else f"{start}-{prev}")
        start = prev = p
    out.append(str(start) if start == prev else f"{start}-{prev}")
    return ",".join(out)


# ---------------------------------------------------------------------------
# Text normalisation
# ---------------------------------------------------------------------------

def _norm(text: str) -> str:
    """Accent-, case- and punctuation-insensitive form, padded with spaces.

    Accents go because the same lab sheet is printed "GRANULOMÉTRIQUE" and
    "GRANULOMETRIQUE"; punctuation goes because "BORING NO." , "BORING NO:"
    and "BORING No" are the same field label.
    """
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^A-Za-z0-9%#]+", " ", text.lower())
    return " " + " ".join(text.split()) + " "


def _mask_digits(text: str) -> str:
    """Numbers masked, so a running header that counts survives as one line.

    "LB-1 Core Runs - 3 to 4.5 m" and "LB-4 Core Runs - 6 to 11.8 m" are the
    same running caption; without masking, every page of such an appendix
    looks like a page saying something of its own.
    """
    return re.sub(r"\d+", "#", text)


def _phrases(*groups: Sequence[str]) -> "re.Pattern[str]":
    """One alternation over every phrase, longest first, on word boundaries.

    A trailing "s" is allowed on every phrase, because a tab page says
    "BORING LOGS" and "PHOTOGRAPHS" where a page title says "BORING LOG" and
    "PHOTOGRAPH", and the tables are written in the singular.
    """
    terms: List[str] = []
    for group in groups:
        terms.extend(group)
    terms.sort(key=len, reverse=True)
    body = "|".join(re.escape(t) for t in terms)
    return re.compile(r"(?<![a-z0-9])(?:" + body + r")s?(?![a-z0-9])")


# ---------------------------------------------------------------------------
# Keyword tables — the words the profession prints on these documents
# ---------------------------------------------------------------------------

#: Titles of a boring / borehole log. English first, then the languages the
#: corpus's local drillers report in.
BORING_TITLES = (
    "test boring log", "boring log", "log of boring", "log of test boring",
    "borehole log", "log of borehole", "field borehole log", "bore log",
    "drill hole log", "log of drill hole", "drilling log",
    "geologic drilling log", "boring record", "record of boring",
    "boring number", "boring no", "boring nr", "borehole number",
    "borehole no", "bore hole no", "bore hole number", "boring profile",
    "test boring", "soil boring", "rock core log", "core log",
    # French
    "sondage", "sondage carotte", "log de sondage", "forage",
    "journal de forage",
    # Spanish
    "sondeo", "perforacion", "registro de perforacion", "log de sondeo",
    # Portuguese
    "sondagem", "furo de sondagem", "boletim de sondagem",
)

#: Titles of a test-pit / trial-pit log.
TEST_PIT_TITLES = (
    "test pit log", "log of test pit", "test pit record", "trial pit log",
    "log of trial pit", "trial pit", "test pit number", "test pit no",
    "pit number", "pit no", "testpit", "test pit",
    # French
    "fosse", "puits d essai", "log de fosse",
    # Spanish
    "calicata", "pozo a cielo abierto", "cata",
    # Portuguese
    "poco n", "poco no", "poco numero",
)

#: Titles of a cone-penetration log.
CPT_TITLES = (
    "cone penetration test", "cone penetration", "cpt log", "cpt no",
    "cpt number", "cptu", "piezocone", "static cone", "dutch cone",
    "penetrometre statique", "penetracion con cono", "ensayo cpt",
)

#: Titles of a dynamic-cone / DCP log.
DCP_TITLES = (
    "dynamic cone penetrometer", "dynamic cone penetration", "dcp log",
    "dcp test", "dcpt", "dynamic cone", "dynamic probing",
    "penetrometre dynamique", "penetration dynamique", "penetrometrique",
    "penetrometro dinamico", "sondagem dinamica",
    # The standard abbreviations for dynamic probing: EN ISO 22476-2 grades
    # (light, medium, heavy, superheavy) and the forms the trade prints on
    # the sheet itself.
    "dpl", "dpm", "dph", "dpsh", "dpt", "lcpt", "pdl", "pdm",
)

#: Field labels a log FORM prints, in any of its columns. Counted, not
#: matched one by one: three or more of them is a log form, whatever the
#: template.
LOG_FORM_FIELDS = (
    "depth", "elevation", "elev", "sample", "samples", "sample type",
    "blows", "blow count", "blows per", "n value", "spt", "penetration",
    "penetration resistance", "recovery", "rec", "rqd", "uscs",
    "soil description", "material description", "description of material",
    "stratum", "strata", "graphic log", "water level", "groundwater",
    "ground water", "casing", "drilling method", "drilling fluid",
    "drill rig", "hammer", "hammer type", "drop", "sampler", "split spoon",
    "auger", "rotary", "wash", "logged by", "driller", "drilled by",
    "completion depth", "total depth", "surface elevation", "datum",
    "date started", "date completed", "boring method", "core",
    # French
    "profondeur", "echantillon", "nappe", "coups", "niveau d eau",
    "nature du terrain", "carottier",
    # Spanish / Portuguese
    "profundidad", "muestra", "nivel freatico", "golpes", "profundidade",
    "amostra", "descricao", "cota",
)

#: Laboratory-test titles. A lab sheet names its test; this is that list.
LAB_TITLES = (
    "atterberg", "atterberg limits", "liquid limit", "plastic limit",
    "plasticity index", "plasticity chart", "sieve analysis", "gradation",
    "grain size", "grain size distribution", "particle size",
    "particle size distribution", "hydrometer", "hydrometer analysis",
    "wash sieve", "sieve", "consolidation test", "consolidation",
    "one dimensional consolidation", "oedometer", "swell test", "swell",
    "swell consolidation", "collapse potential", "triaxial",
    "triaxial compression", "unconsolidated undrained",
    "consolidated undrained", "consolidated drained", "direct shear",
    "shear test", "unconfined compression", "unconfined compressive",
    "compaction test", "proctor", "modified proctor", "standard proctor",
    "california bearing ratio", "cbr test", "moisture content",
    "water content", "natural moisture", "moisture density",
    "dry density", "bulk density", "specific gravity", "corrosivity",
    "corrosion test", "resistivity", "soil resistivity", "sulfate",
    "sulphate", "chloride", "ph value", "soil ph", "organic content",
    "chemical test", "chemical analysis", "water analysis",
    "aqueous extract", "water extract", "soluble salt", "carbonate content",
    "gypsum content", "total dissolved solids", "grading analysis",
    "grading test", "grading curve",
    "organic matter", "loss on ignition", "permeability",
    "hydraulic conductivity", "laboratory test", "laboratory testing",
    "laboratory results", "laboratory data", "lab test results",
    "summary of laboratory", "test results summary", "soil classification",
    "classification test", "certificate of analysis", "test certificate",
    "analytical results", "foundation indicator", "point load",
    "los angeles abrasion", "sand equivalent", "aggregate impact",
    "soil mechanical test", "index properties", "index test",
    # French
    "limites d atterberg", "limites de atterberg", "limite de liquidite",
    "limite de plasticite", "analyse granulometrique", "granulometrie",
    "teneur en eau", "essai oedometrique", "oedometrique",
    "essai de cisaillement", "cisaillement", "compression simple",
    "poids volumique", "essai proctor", "essai cbr", "equivalent de sable",
    "masse volumique", "essai au bleu",
    # Spanish
    "limites de consistencia", "granulometria", "humedad natural",
    "contenido de humedad", "corte directo", "consolidacion",
    "peso especifico", "compactacion", "limite liquido",
    "limite plastico",
    # Portuguese
    "analise granulometrica", "teor de humidade", "teor em agua",
    "ensaio de compactacao", "peso volumico",
)

#: Field (in-situ) tests that are NOT logs and NOT laboratory work.
FIELD_TEST_TITLES = (
    "infiltration test", "borehole infiltration", "infiltration rate",
    "percolation test", "perc test", "field permeability",
    "in situ permeability", "pump test", "pumping test", "packer test",
    "falling head test", "constant head test", "double ring infiltrometer",
    "plate load test", "essai d infiltration", "essai lefranc",
    "essai lugeon", "ensayo de infiltracion", "prueba de percolacion",
)

#: Legends, keys and notes sheets. They sit at the front of an appendix and
#: name every exploration and test it holds, which makes them read as all of
#: them at once; they are none.
LEGEND_TITLES = (
    "exploration legend", "key to symbols", "symbols used on",
    "symbols and terms", "terms and symbols", "general notes",
    "log general notes", "soil classification chart",
    "unified soil classification", "classification of soils",
    "descriptive terms", "conversion factors", "identification of soil",
    "exploration procedures", "investigation procedures",
    "drilling procedures", "sampling procedures", "legende des symboles",
)

#: In-situ tests the role vocabulary has no word for. Named so they are
#: answered "other" rather than swept into the log they were run in.
OTHER_TEST_TITLES = (
    "pressuremeter", "pressuremeter test", "menard", "borehole expansion",
    "vane shear test", "field vane", "dilatometer", "flat dilatometer",
    "borehole video", "downhole seismic", "crosshole seismic",
    "essai pressiometrique", "pressiometre", "scissometre",
)

#: Program banners a calculation printout carries. Naming the program is the
#: single most reliable calc signal on a page that is otherwise numbers.
CALC_PROGRAMS = (
    "lpile", "lpile plus", "group v", "apile", "shaft v", "settle3",
    "settle3d", "slide2", "slideinterpret", "slide interpret", "slope w",
    "geostudio", "sigma w", "seep w", "quake w", "plaxis", "liquefypro",
    "cliq", "novoliq", "novospt", "deepex", "spw911", "gstrudl", "staad",
    "rocscience", "rspile", "rswall", "rocplane", "swedge", "dips",
    "phase2", "rs2", "rs3", "flac", "allpile", "ensoft", "wallap",
    "prokon", "spectra", "shake2000", "proshake", "deepsoil", "settle 3",
    "usgs design maps", "seismic design maps", "unified hazard tool",
    "atkinson boore", "boussinesq", "gtstrudl", "risa", "sap2000",
    "mathcad", "civil 3d",
)

#: What a calculation package prints about itself.
CALC_TITLES = (
    "calculation", "calculations", "calculation title", "calculated by",
    "computed by", "checked by", "calc by", "calc no", "calculation sheet",
    "design calculation", "analysis results", "input echo", "input data",
    "summary of results", "output summary", "load case", "load combination",
    "factor of safety", "bearing capacity analysis", "settlement analysis",
    "liquefaction analysis", "slope stability analysis",
    "global stability", "seismic site class", "site classification",
    "pavement design", "design parameters", "note de calcul",
    "memoire de calcul", "calculo", "dimensionnement",
)

#: What a photograph page prints.
PHOTO_TITLES = (
    "photo", "photos", "photograph", "photographs", "photographic log",
    "photo log", "core box", "core run", "core photograph", "view looking", "looking north", "looking south",
    "looking east", "looking west", "upon completion", "stockpile",
    "plate no", "photo no", "photographie", "fotografia",
)

#: What a table of contents prints.
TOC_TITLES = (
    "table of contents", "list of figures", "list of tables",
    "list of appendices", "list of appendixes", "table des matieres",
    "tabla de contenido", "indice general", "conteudo",
)

#: What a cover letter prints.
LETTER_TITLES = (
    "dear", "sincerely", "yours truly", "very truly yours",
    "respectfully submitted", "letter of transmittal", "transmittal",
    "we are pleased to submit", "we are pleased to present", "enclosure",
    "cordialement", "atentamente",
)

#: What the LARGEST type on a report's title page says.
COVER_TITLES = (
    "geotechnical report", "geotechnical engineering report",
    "geotechnical investigation", "geotechnical study", "engineering study",
    "engineering report", "final report", "draft report", "volume i",
    "volume ii", "volume iii", "volume 1", "volume 2", "volume 3",
    "rapport", "informe", "relatorio", "memoria",
)

#: What a title page says about who it is for. A cover names its client.
COVER_FOR = (
    "prepared for", "prepared by", "submitted to", "submitted by",
    "presented to", "on behalf of", "prepared at the request",
    "preparado para", "prepare pour", "pour le compte de",
)

#: Words that make a near-blank page a divider / tab on their own.
#:
#: "Section" is deliberately absent, and "part" only counts with a letter or
#: number after it (see the appendix-token pattern): a slope-stability
#: printout headed "SECTION A-A'" is a calculation, and reading it as a tab
#: starts a new appendix in the middle of the calc package.
DIVIDER_WORDS = (
    "appendix", "appendices", "annex", "annexe", "annexes", "attachment",
    "exhibit", "enclosure", "apendice", "anexo", "apendix",
)

#: Words that name a tab only when the page LEADS with them. A narrative
#: sentence says "provided in tables" and a specification page is headed
#: "Earthwork and Grading Guide Specifications"; neither divides anything,
#: and a tab that holds the figures says FIGURES at the top and nowhere else.
DIVIDER_LIST_WORDS = (
    "figures", "tables", "plates", "photographs", "calculations",
    "drawings", "specifications", "exhibits",
)

#: A plan page.
PLAN_TITLES = (
    "boring location plan", "test location plan", "exploration plan",
    "exploration location plan", "investigation location plan",
    "site plan", "plot plan", "location plan", "site location map",
    "vicinity map", "site and vicinity", "boring location map",
    "exploration location", "plan de situation", "plano de localizacion",
    # A map page is a plan of the site by another name, and the base image
    # under it is not a photograph of anything.
    "geologic map", "geological map", "topographic map", "historic map",
    "aerial photo", "aerial photograph", "aerial image", "map of",
    "carte geologique", "mapa geologico",
)

#: A subsurface-profile page.
PROFILE_TITLES = (
    "subsurface profile", "soil profile", "generalized profile",
    "generalised profile", "cross section", "cross sections",
    "geologic cross section", "geological cross section", "profile a a",
    "subsurface cross section", "interpreted profile", "probable soil",
    "shear wave profile", "coupe geologique", "perfil del suelo",
)

_RE_BORING = _phrases(BORING_TITLES)
_RE_TEST_PIT = _phrases(TEST_PIT_TITLES)
_RE_CPT = _phrases(CPT_TITLES)
_RE_DCP = _phrases(DCP_TITLES)
_RE_LOG_FIELD = _phrases(LOG_FORM_FIELDS)
#: The one column every exploration log has and no laboratory sheet lays out
#: down its page: depth, in the languages the logs are written in.
_RE_DEPTH_FIELD = _phrases(("depth", "profondeur", "profundidad",
                            "profundidade", "tiefe", "prof"))
_RE_LAB = _phrases(LAB_TITLES)
_RE_FIELD_TEST = _phrases(FIELD_TEST_TITLES)
_RE_OTHER_TEST = _phrases(OTHER_TEST_TITLES)
_RE_LEGEND = _phrases(LEGEND_TITLES)
#: "FIGURE 3", "PLATE A-2" at the head of what a page calls itself: the page
#: is that figure, whatever the tab above it says the appendix holds.
_RE_CAPTION = re.compile(
    r"(?<![a-z0-9])(?:figure|fig|plate|exhibit|planche|figura)\.?\s*"
    r"(?:no\.?\s*)?[a-z]{0,2}-?\d{1,3}[a-z]?(?![a-z0-9])", re.I)
_RE_LOG_WORD = _phrases(("log", "logs", "record", "records"))
_RE_CALC_PROGRAM = _phrases(CALC_PROGRAMS)
_RE_CALC = _phrases(CALC_TITLES)
_RE_PHOTO = _phrases(PHOTO_TITLES)
#: An aerial photograph is the base image a plan is drawn on, not a page of
#: photographs; the words are struck out before the caption is read.
_RE_AERIAL = re.compile(
    r"\b(?:aerial|satellite|orthophoto\w*|photogrammetr\w*)\s*"
    r"(?:photo\w*|image\w*|map)?", re.I)
_RE_TOC = _phrases(TOC_TITLES)
_RE_LETTER = _phrases(LETTER_TITLES)
_RE_COVER_TITLE = _phrases(COVER_TITLES)
_RE_COVER_FOR = _phrases(COVER_FOR)
_RE_DIVIDER = _phrases(DIVIDER_WORDS)
_RE_DIVIDER_LIST = _phrases(DIVIDER_LIST_WORDS)
_RE_FIGURES_TAB = _phrases(("figures", "tables", "plates", "exhibits"))
_RE_PLAN = _phrases(PLAN_TITLES)
_RE_PROFILE = _phrases(PROFILE_TITLES)

#: A divider that hands its whole appendix to somebody else's document.
#: Whole phrases only. A bare "report" or "survey" appears in half the tab
#: pages in the corpus — in a firm's own strapline, in a footer, in
#: "Laboratory Test Report" — and an appendix is only appended when the tab
#: names a DOCUMENT.
_RE_APPENDED = _phrases((
    "geotechnical report", "engineering report", "geotechnical study",
    "survey report", "factual report", "final report", "draft report",
    "investigation report", "report by", "report from", "report prepared",
    "previous report", "prior report", "report of subsurface",
    "geophysical survey", "geophysical investigation", "seismic refraction",
    "refraction survey", "masw", "by others", "by the local consultant",
))

#: A divider that hands its appendix to the exploration programme as a whole,
#: without saying which kind of log is inside.
_RE_EXPLORATION = _phrases((
    "subsurface exploration", "subsurface investigation", "exploration",
    "explorations", "field exploration", "field tests", "field test data",
    "field work", "exploration data", "subsurface data", "field data",
    "boring logs and", "logs", "reconnaissance", "investigation",
))

#: "Page 2 of 3" — the printed continuation a multi-page log carries.
_RE_PAGE_OF = re.compile(r"\bpage\s+(\d{1,3})\s+(?:of|de|sur|of)\s+(\d{1,3})\b")

#: An exploration id as it is printed in a log's title block.
_RE_EXPLORATION_ID = re.compile(
    r"(?<![A-Za-z0-9])([A-Z]{1,5}[\-– ]?\d{1,3}[A-Z]?)(?![A-Za-z0-9])")

#: A lettered or numbered appendix, for following a document's own sequence.
_RE_APPENDIX_TOKEN = re.compile(
    r"\b(?:appendix|appendice|apendice|annex|annexe|anexo|attachment|part|"
    r"partie|parte|exhibit|tab)\s+(?:no\s+)?"
    r"(?:#\s*)?([a-z]{1,2}\d{0,2}|\d{1,2}(?:\.\d{1,2})?)(?![a-z0-9])")


# ---------------------------------------------------------------------------
# Per-page facts
# ---------------------------------------------------------------------------

@dataclass
class PageFacts:
    """Everything the rules read about one page.

    Built from a :class:`~planlens.document.model.PageSummary` and the page's
    text lines. It is a plain data object on purpose: a test, or a scoring
    harness working from a cached extraction, can build one without a PDF.
    """

    page: int
    kind: str = "mixed"
    n_words: int = 0
    n_text_chars: int = 0
    text_density: float = 0.0
    n_images: int = 0
    ruling_h: int = 0
    ruling_v: int = 0
    heading: Optional[str] = None
    header: Optional[str] = None
    footer: Optional[str] = None
    printed_page: Optional[int] = None
    printed_of: Optional[int] = None
    divider_title: Optional[str] = None
    segment: Optional[int] = None
    text_reliable: bool = True
    width: float = 612.0
    height: float = 792.0
    #: Normalised text: the whole page, the largest type, the top band, the
    #: bottom band, and everything between the bands — where a tab page
    #: prints what its appendix holds, clear of the previous appendix's
    #: running footer.
    flat: str = " "
    title: str = " "
    top: str = " "
    bottom: str = " "
    middle: str = " "
    #: The largest type size on the page, in points. A report's title page
    #: sets its title big; a lab sheet that happens to say "report" does not.
    max_size: float = 0.0

    @property
    def is_ruled(self) -> bool:
        return self.ruling_h >= 12 and self.ruling_v >= 12

    @property
    def near_blank(self) -> bool:
        return self.n_words <= MAX_DIVIDER_WORDS

    @property
    def title_and_bands(self) -> str:
        """The page's title and its top and bottom bands.

        A log's title block sits at the top of the page on one template and
        at the foot of it on the next, so both bands count.
        """
        return self.title + self.top + self.bottom

    @property
    def own_title(self) -> str:
        """The same, less the document's running header and footer.

        An appendix headed "Test Pit Logs and Photographs" prints those words
        on every page in it. Left in, they make every log page a photograph;
        taken out, what remains is what the page itself is called. Used where
        the running band would otherwise decide the answer on its own.
        """
        own = [ln for ln in self.top_lines + self.bottom_lines
               if _mask_digits(ln) not in self.running]
        return self.title + " " + " ".join(own) + " "

    #: The top band as individual normalised lines, and the ones of them that
    #: repeat across the document (its running header). ``running`` is filled
    #: by :func:`assign`, the first place that can know what repeats.
    top_lines: List[str] = field(default_factory=list)
    bottom_lines: List[str] = field(default_factory=list)
    running: frozenset = frozenset()

    @property
    def declaration(self) -> str:
        """What a tab page declares: its own words, not its neighbours'.

        The top band and everything below it, but NOT the bottom band. A tab
        prints its name near the top ("APPENDIX A — SUBSURFACE EXPLORATION
        DATA") and lists its contents beneath; what sits at the foot of the
        page is the PREVIOUS appendix's running footer, and reading a tab's
        declaration from that hands the new appendix to the old one.
        """
        if not self.near_blank:
            return self.title
        own = [ln for ln in self.top_lines
               if _mask_digits(ln) not in self.running]
        text = (" ".join(own) + " " + self.middle).strip()
        return " " + " ".join(text.split()) + " " if text else self.title


def _size_groups(lines, n_sizes: int, max_lines: int) -> str:
    """Normalised text of the lines set in the ``n_sizes`` largest types.

    Type size is how a page says which of its words is the title, and it
    survives every template. It is read as a RANK, not a ratio: a DRAFT
    watermark at 98 pt would swallow any "within 75 % of the largest" rule,
    but it is only ever one size band of its own.
    """
    sized = [(round(ln.size or 0.0, 1), ln) for ln in lines
             if (ln.text or "").strip()]
    if not sized:
        return " "
    sizes = sorted({s for s, _ in sized if s > 0}, reverse=True)[:n_sizes]
    if not sizes:
        keep = [ln for _, ln in sized][:max_lines]
    else:
        floor = min(sizes)
        keep = [ln for s, ln in sized if s >= floor][:max_lines]
    keep = sorted(keep, key=lambda ln: (ln.bbox[1], ln.bbox[0])
                  if ln.bbox else (0.0, 0.0))
    return _norm(" ".join(ln.text for ln in keep))


def _band_lines(lines, height: float, where: str) -> List[str]:
    """Normalised text of each line in the top, bottom or middle band."""
    keep = []
    for ln in lines:
        if not ln.bbox or not (ln.text or "").strip():
            continue
        in_top = ln.bbox[3] <= BAND_FRACTION * height
        in_bottom = ln.bbox[1] >= (1.0 - BAND_FRACTION) * height
        if where == "top" and in_top:
            keep.append(ln)
        elif where == "bottom" and in_bottom:
            keep.append(ln)
        elif where == "middle" and not in_top and not in_bottom:
            keep.append(ln)
    keep.sort(key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    out = [_norm(ln.text).strip() for ln in keep]
    return [s for s in out if s]


def _band(lines, height: float, where: str) -> str:
    """Normalised text of the top, bottom or middle band of the page."""
    return _norm(" ".join(_band_lines(lines, height, where)))


def facts_for(summary: PageSummary, lines) -> PageFacts:
    """Build the facts for one page from its summary and its text lines."""
    height = summary.height or 792.0
    return PageFacts(
        page=summary.page,
        kind=summary.kind,
        n_words=summary.n_words,
        n_text_chars=summary.n_text_chars,
        text_density=summary.text_density,
        n_images=summary.n_images,
        ruling_h=summary.ruling_h,
        ruling_v=summary.ruling_v,
        heading=summary.heading,
        header=summary.header,
        footer=summary.footer,
        printed_page=summary.printed_page,
        printed_of=summary.printed_of,
        divider_title=summary.divider_title,
        segment=summary.segment,
        text_reliable=summary.text_reliable,
        width=summary.width,
        height=height,
        flat=_norm(" ".join(ln.text for ln in lines)),
        title=_size_groups(lines, 2, 14),
        top=_band(lines, height, "top"),
        bottom=_band(lines, height, "bottom"),
        middle=_band(lines, height, "middle"),
        top_lines=_band_lines(lines, height, "top"),
        bottom_lines=_band_lines(lines, height, "bottom"),
        max_size=max([ln.size or 0.0 for ln in lines], default=0.0),
    )


def facts_from_document(doc) -> List[PageFacts]:
    """Facts for every page of an open :class:`~planlens.document.Document`.

    The page map is read first so every page carries its segment; the text is
    the extraction the page map already cached, so this costs one pass.
    """
    summaries = doc.page_map()
    out: List[PageFacts] = []
    for s in summaries:
        content = doc.page(s.page, tables=False)
        out.append(facts_for(s, content.lines))
    return out


# ---------------------------------------------------------------------------
# What one page says about itself
# ---------------------------------------------------------------------------

@dataclass
class _Cue:
    role: str
    strength: str          # "strong" | "named" | "weak"
    why: str
    #: Whether the phrase was found in the page's own largest type, rather
    #: than somewhere in its top or bottom band. A tab's declaration is
    #: overruled by a page that TITLES itself something else, or by one
    #: whose evidence is too heavy to be a mention (see ``weight``).
    in_title: bool = False
    #: How many separate pieces of evidence the cue rests on -- log form
    #: field labels, laboratory test names, photograph captions. A lab sheet
    #: that prints the trial pit its sample came from carries one or two; a
    #: log form carries a dozen, whatever its title block was read as.
    weight: int = 1

    @property
    def decisive(self) -> bool:
        """Enough to overrule a tab that names one thing."""
        return self.in_title or (self.strength == "strong"
                                 and self.weight >= DECISIVE_WEIGHT)

    @property
    def confidence(self) -> float:
        return {"strong": 0.9, "named": 0.8, "weak": 0.6}[self.strength]


def _log_family(text: str) -> Optional[Tuple[str, str]]:
    """Which log a title names, and the phrase that said so.

    Checked most specific first: a DCP sheet in French says "sondage au
    penetrometre dynamique", which names a boring and a dynamic cone in one
    line, and the dynamic cone is the one that is true.
    """
    for role, rx in (("dcp_log", _RE_DCP), ("cpt_log", _RE_CPT),
                     ("test_pit_log", _RE_TEST_PIT),
                     ("boring_log", _RE_BORING)):
        m = rx.search(text)
        if m:
            return role, m.group(0)
    return None


def _log_first(f: PageFacts) -> bool:
    """Does the page title a log BEFORE it mentions photographs?

    "TEST PIT 4 LOG NOT IN REPORT — test pit photo(s) shown below" is a page
    about the pit, illustrated; "TP-01 — Upon Completion" is a photograph of
    one. Which word leads settles it, the same way it settles what a tab is
    a tab of.
    """
    own_log = _log_family(f.own_title)
    own_photo = _RE_PHOTO.search(f.own_title)
    if own_log is None or own_photo is None:
        return False
    at = f.own_title.find(own_log[1])
    if not 0 <= at < own_photo.start():
        return False
    # And it must say LOG, not merely name the pit: "Test Pit 1 Photographs"
    # is a page of photographs of test pit 1.
    tail = f.own_title[at:at + len(own_log[1]) + LOG_WORD_CHARS]
    m = _RE_LOG_WORD.search(tail)
    return m is not None and at + m.start() < own_photo.start()


def _count(rx: "re.Pattern[str]", text: str) -> int:
    return len({m.group(0) for m in rx.finditer(text)})


def _page_cue(f: PageFacts) -> Optional[_Cue]:
    """What the page's own title and shape say it is, or None."""
    title_area = f.title_and_bands
    # A page carrying argument rather than results or drawing: prose or a
    # printout, unruled, with more words than a result sheet or a plotted
    # sheet ever has. Such a page is not named by a phrase in its bands.
    working = (f.n_words >= WORKING_PAGE_WORDS and not f.is_ruled
               and f.kind in ("text", "mixed"))
    n_fields = _count(_RE_LOG_FIELD, f.flat)
    photo_hits = _count(_RE_PHOTO, _RE_AERIAL.sub(" ", f.own_title))
    image_heavy = f.n_images >= 1 and f.n_words < 220

    # Photographs first: a test-pit photo page carries the pit's name in its
    # caption, and would otherwise read as that pit's log.
    if photo_hits >= 1 and (image_heavy or photo_hits >= 2) and not _log_first(f):
        plate = image_heavy and f.n_words < 60      # a picture with a caption
        return _Cue("photos", "strong" if (photo_hits >= 2 or plate)
                    else "named",
                    f"photograph caption ({photo_hits})",
                    bool(_RE_PHOTO.search(f.title)), photo_hits)

    # A caption names its page whatever kind the page was MEASURED as -- a
    # plan comes back as a form, a figure, a scan or a mixed page depending
    # on how it was plotted -- but a page of prose is not named by a phrase
    # inside its paragraphs.
    graphic = 8 <= f.n_words < 400 and (
        f.kind in ("figure", "drawing_sheet", "form", "scanned", "mixed")
        or f.n_images >= 1 or f.n_words < 150)
    if graphic:
        # Before the logs: a location plan and a cross-section both name the
        # explorations they plot, and a legend beside them names every kind.
        # A page with no words of its own names nothing: what would be read
        # is the running header of the appendix it sits in.
        drawn = f.kind in ("drawing_sheet", "figure")
        m = _RE_PROFILE.search(f.own_title)
        if m:
            return _Cue("profile", "strong" if drawn else "named",
                        f"profile title {m.group(0)!r}",
                        bool(_RE_PROFILE.search(f.title)) or not working)
        m = _RE_PLAN.search(f.own_title)
        if m:
            return _Cue("plan", "strong" if drawn else "named",
                        f"plan title {m.group(0)!r}",
                        bool(_RE_PLAN.search(f.title)) or not working)
        cap = _RE_CAPTION.search(f.own_title)
        if cap and cap.start() < CAPTION_LEAD_CHARS:
            return _Cue("figure", "named",
                        f"figure caption {cap.group(0).strip()!r}",
                        bool(_RE_CAPTION.search(f.title)) or not working)

    m = _RE_LEGEND.search(f.title)
    if m and f.n_words < 400:
        # A legend, a notes sheet or a classification chart names every kind
        # of exploration on it. It is none of them.
        return _Cue("other", "strong",
                    f"legend or notes sheet {m.group(0)!r}", True)

    m = _RE_OTHER_TEST.search(title_area)
    if m:
        # A pressuremeter, vane or dilatometer sounding is a real thing with
        # no role of its own here, and its form is headed "BOREHOLE No." like
        # any other. Saying "other" is the honest answer; calling it a boring
        # is not.
        return _Cue("other", "strong", f"in-situ test title {m.group(0)!r} "
                                       f"the role vocabulary has no word for",
                    bool(_RE_OTHER_TEST.search(f.title)))

    fam = _log_family(title_area)
    lab_hits = _count(_RE_LAB, title_area)
    if fam is not None:
        role, phrase = fam
        if _log_first(f):
            return _Cue(role, "strong",
                        f"log title {phrase!r} before the photographs "
                        f"the page shows of it", True)
        # A laboratory sheet prints the boring and depth its sample came
        # from, in the same title block as the test's name. The test wins
        # unless the page is also built like a log: a ruled form with the
        # log's own field labels down its columns.
        # The page must be built like a log as well as name one: a plan
        # whose legend lists the test pits it plots is drawn on a ruled
        # sheet too, and has none of a log's column headings.
        log_form = n_fields >= 4 or ((f.is_ruled or f.kind == "form")
                                     and n_fields >= 2)
        if lab_hits >= 1 and not (log_form and n_fields >= 6):
            m = _RE_LAB.search(title_area)
            return _Cue("lab_test", "named",
                        f"laboratory test title {m.group(0)!r} beside a "
                        f"reference to the exploration it sampled",
                        bool(_RE_LAB.search(f.title)), lab_hits)
        in_title = bool(_log_family(f.title))
        if log_form:
            return _Cue(role, "strong", f"log title {phrase!r}, "
                                        f"{n_fields} log form fields",
                        in_title, n_fields)
        if n_fields >= 2:
            return _Cue(role, "named", f"log title {phrase!r}, "
                                       f"{n_fields} log form fields",
                        in_title, n_fields)

    if lab_hits >= 1:
        m = _RE_LAB.search(title_area)
        if working:
            # A page of WORKING that names a test is naming the method it
            # applies -- a one-dimensional consolidation settlement, a
            # triaxial strength assumption. A laboratory result sheet is a
            # form or a short page of values, not a page of argument.
            return _Cue("lab_test", "weak",
                        f"laboratory test name {m.group(0)!r} on a page of "
                        f"working" if m else "laboratory test name",
                        False, lab_hits)
        return _Cue("lab_test", "strong" if lab_hits >= 2 else "named",
                    f"laboratory test title {m.group(0)!r}"
                    if m else "laboratory test title",
                    bool(_RE_LAB.search(f.title)), lab_hits)

    m = _RE_FIELD_TEST.search(title_area)
    if m:
        return _Cue("field_test", "strong",
                    f"field test title {m.group(0)!r}",
                    bool(_RE_FIELD_TEST.search(f.title)))

    m = _RE_CALC_PROGRAM.search(f.flat)
    if m:
        return _Cue("calculation", "strong",
                    f"analysis program banner {m.group(0)!r}", True)

    calc_hits = _count(_RE_CALC, title_area)
    if calc_hits >= 1:
        m = _RE_CALC.search(title_area)
        return _Cue("calculation", "named" if calc_hits >= 2 else "weak",
                    f"calculation heading {m.group(0)!r}"
                    if m else "calculation heading")

    # A page with no title of its own but the shape of a log form: a
    # continuation sheet. Said weakly; the run rule is what places it.
    if n_fields >= 6 and (f.is_ruled or f.kind == "form"):
        return _Cue("boring_log", "weak",
                    f"log form shape, {n_fields} log form fields")

    lab_body = _count(_RE_LAB, f.flat)
    if lab_body >= 2:
        return _Cue("lab_test", "weak",
                    f"{lab_body} laboratory test terms on the page")
    return None


def _is_divider(f: PageFacts) -> Optional[str]:
    """The divider's title, or None. Wider than the page map's own rule.

    ``PageSummary.divider_title`` reads the heading and the first three lines;
    a tab page that prints its firm's name above "APPENDIX 4" passes neither
    test, so the divider words are looked for in the page's largest type as
    well.

    A table of contents is NOT a divider, however many appendices it names.
    It is the one page in a report that lists every tab, and reading it as a
    tab of its own puts a divider in front of the narrative and hands the
    whole report to whatever the contents list happened to mention first.
    """
    if _is_toc(f) or _count(_RE_APPENDIX_TOKEN, f.flat) >= 3:
        return None
    if f.page == 0 or not f.near_blank or _is_report_cover(f):
        # A report's title page may well say which attachment of the
        # submittal it is; it is still the cover of a document, not a tab
        # inside one.
        return None
    if len(_appendix_tokens(f)) >= 2:
        # Two different appendix letters on one page is a contents list --
        # the front matter, or the index at the back -- not a tab.
        return None
    text = f.declaration
    for rx, limit in ((_RE_DIVIDER, DIVIDER_TITLE_CHARS),
                      (_RE_DIVIDER_LIST, LIST_TAB_CHARS),
                      (_RE_APPENDIX_TOKEN, DIVIDER_TOKEN_CHARS)):
        m = rx.search(text)
        if m is not None and m.start() < limit:
            return text.strip()[:100] or None
    # The page map's own divider reading is a fallback, not the first word:
    # it accepts a page whose running header says "Appendix A: Test Pit
    # Photographs" and then returns that page's heading, which is a photo
    # caption. It is taken only when it names a tab itself.
    if f.divider_title and _RE_DIVIDER.search(_norm(f.divider_title)):
        return f.divider_title
    return None


def _declared_roles(f: PageFacts) -> List[str]:
    """What a divider says its appendix holds, most prominent first.

    Read from the divider's own type, not the whole page: a tab page carries
    the PREVIOUS appendix's footer, and a declaration read from the footer
    would hand the new appendix to the old one.
    """
    text = f.declaration if f.declaration.strip() else f.title
    found: List[Tuple[int, str]] = []

    def add(rx: "re.Pattern[str]", role: str) -> None:
        m = rx.search(text)
        if m:
            found.append((m.start(), role))

    add(_RE_APPENDED, "appended_report")
    m = _RE_FIGURES_TAB.search(text)
    if m is not None and m.start() < FIGURES_TAB_CHARS:
        found.append((m.start(), "figure"))
    add(_RE_PHOTO, "photos")
    add(_RE_BORING, "boring_log")
    add(_RE_TEST_PIT, "test_pit_log")
    add(_RE_CPT, "cpt_log")
    add(_RE_DCP, "dcp_log")
    add(_RE_FIELD_TEST, "field_test")
    add(_RE_LAB, "lab_test")
    add(_RE_CALC, "calculation")
    add(_RE_PROFILE, "profile")
    add(_RE_PLAN, "plan")
    # "TEST PIT PHOTOGRAPHS" is a tab of photographs, not of test pit logs:
    # where a photograph word follows the exploration word immediately, the
    # photographs are the head of the phrase. "TEST PIT LOGS AND
    # PHOTOGRAPHS" says both, in that order, and keeps it.
    found.sort()
    if len(found) >= 2 and not _RE_LOG_WORD.search(text):
        photo_at = next((i for i, (_p, r) in enumerate(found)
                         if r == "photos"), None)
        log_at = next((i for i, (_p, r) in enumerate(found)
                       if r in LOG_ROLES), None)
        if (photo_at is not None and log_at is not None and log_at < photo_at
                and found[photo_at][0] - found[log_at][0] <= 20):
            found[log_at], found[photo_at] = found[photo_at], found[log_at]
            found = sorted(found, key=lambda pr: (pr[0], pr[1]))
            found = [found[photo_at]] + [x for i, x in enumerate(found)
                                         if i != photo_at]
    roles = []
    for _pos, role in found:
        if role not in roles:
            roles.append(role)
    if not roles and _RE_EXPLORATION.search(text):
        roles = ["exploration"]
    return roles


def _appendix_tokens(f: PageFacts) -> set:
    """Every distinct appendix letter or number the page prints."""
    return {m.group(1).lower()
            for m in _RE_APPENDIX_TOKEN.finditer(f.declaration)}


def _appendix_token(f: PageFacts) -> Optional[str]:
    """The appendix letter or number a divider prints ("a", "b", "3")."""
    m = _RE_APPENDIX_TOKEN.search(f.declaration or f.title)
    if m is None:
        return None
    tok = m.group(1)
    if tok.isdigit():
        return tok
    return tok if len(tok) == 1 else None


def _token_rank(token: Optional[str]) -> Optional[int]:
    """Where a tab sits in its document's sequence, or None.

    Only a plain letter or a plain number takes a place in the sequence. A
    sub-tab ("PART B2", "APPENDIX 3.1") still divides its appendix into
    sections, but it is not the document's own counting and must not be read
    as one: B2 following B is not the sequence going backwards.
    """
    if token is None:
        return None
    if token.isdigit():
        return int(token)
    if len(token) == 1 and token.isalpha():
        return ord(token) - ord("a") + 1
    return None


def _is_report_cover(f: PageFacts) -> bool:
    """Does this page open a report — a title page, not a tab or a sheet?

    Three things at once, because any one of them alone is common: the page
    is short, its LARGEST type names a document, and it says who it was
    prepared for. A laboratory sheet headed "Test Report" fails the second;
    a tab page fails the third; a narrative page fails the first.
    """
    if not (4 <= f.n_words <= MAX_COVER_WORDS):
        return False
    if f.max_size and f.max_size < 15.0:
        return False
    if not _RE_COVER_TITLE.search(f.title):
        return False
    return bool(_RE_COVER_FOR.search(f.flat))


def _is_toc(f: PageFacts) -> bool:
    """A contents page. "Contents" alone counts only as the page's opening
    word: a narrative page discussing moisture contents is not one."""
    if _RE_TOC.search(f.title) or _RE_TOC.search(f.top):
        return True
    return f.title.lstrip().startswith(("contents ", "sommaire ", "indice "))


def _is_letter(f: PageFacts) -> bool:
    if f.n_words > 500:
        return False
    return _count(_RE_LETTER, f.flat) >= 2


# ---------------------------------------------------------------------------
# The document's own structure
# ---------------------------------------------------------------------------

@dataclass
class _Section:
    """A divider and the pages that follow it, up to the next divider."""
    divider: Optional[int]
    first: int
    last: int
    roles: List[str] = field(default_factory=list)
    token: Optional[str] = None
    title: Optional[str] = None


@dataclass
class _Doc:
    """One constituent document: the report, a later volume, an appended one."""
    first: int
    last: int
    appended: bool = False
    last_rank: Optional[int] = None
    first_divider: Optional[int] = None


def _build_sections(facts: Sequence[PageFacts],
                    dividers: Dict[int, str]) -> List[_Section]:
    n = len(facts)
    starts = sorted(dividers)
    out: List[_Section] = []
    if not starts or starts[0] > 0:
        out.append(_Section(divider=None, first=0,
                            last=(starts[0] - 1 if starts else n - 1)))
    for i, d in enumerate(starts):
        last = (starts[i + 1] - 1) if i + 1 < len(starts) else n - 1
        f = facts[d]
        out.append(_Section(divider=d, first=d + 1, last=last,
                            roles=_declared_roles(f),
                            token=_appendix_token(f),
                            title=dividers[d]))
    return [s for s in out if s.first <= s.last or s.divider is not None]


def _build_documents(facts: Sequence[PageFacts],
                     sections: Sequence[_Section]) -> List[_Doc]:
    """Split the PDF into its constituent documents, appended ones marked.

    Two things open a document. A divider that hands its appendix to somebody
    else's report ("APPENDIX C — GEOPHYSICAL SURVEY REPORT") opens an APPENDED
    one, and every page of it carries that role. A report title page appearing
    part-way through opens a plain one — a second volume of the same report,
    whose narrative is narrative and whose calculations are calculations.

    A document ENDS where its enclosing document's appendix lettering resumes.
    A report bound inside appendix C brings its own A, B, C, D, E; the outer
    report's next tab is D, and D following the inner E is the outer document
    speaking again, not the inner one going backwards.
    """
    n = len(facts)
    stack: List[_Doc] = [_Doc(first=0, last=n - 1)]
    done: List[_Doc] = []
    by_divider = {s.divider: s for s in sections if s.divider is not None}

    for i, f in enumerate(facts):
        sec = by_divider.get(i)
        if sec is not None:
            rank = _token_rank(sec.token)
            if rank is not None:
                # Does this tab continue an enclosing document's sequence?
                for depth in range(len(stack) - 1, -1, -1):
                    doc = stack[depth]
                    last = doc.last_rank
                    continues = (rank == 1 if last is None
                                 else rank == last + 1)
                    if continues:
                        while len(stack) - 1 > depth:
                            closed = stack.pop()
                            closed.last = i - 1
                            done.append(closed)
                        stack[-1].last_rank = rank
                        break
                else:
                    # Nobody's sequence: a restart, so a nested document that
                    # the divider itself belongs to.
                    pass
            if stack[-1].first_divider is None and (sec.token or sec.roles):
                # The narrative ends at the first tab that says what its
                # appendix holds. A near-blank page that merely repeats the
                # report's title is not that tab.
                stack[-1].first_divider = i
            opens_appended = (
                "appended_report" in sec.roles
                and i + 1 < n
                and sec.token is not None
                and stack[-1].first_divider is not None
                and stack[-1].first_divider < i)
            if opens_appended:
                stack.append(_Doc(first=i + 1, last=n - 1, appended=True))
            continue

        if (i >= 5 and not stack[-1].appended and _is_report_cover(f)
                and i > stack[-1].first + 2
                and any(_is_toc(g) for g in facts[i + 1:i + 4])):
            # A title page followed by its own table of contents, arriving
            # after this document has already run its appendix tabs: the
            # second volume of the same report, not a page of the appendix
            # it happens to sit behind. A laboratory certificate headed
            # "Certificate of Analysis … submitted to" has no contents page
            # after it and does not qualify.
            closed = stack[-1]
            if closed.first_divider is not None and i > closed.first_divider:
                # A title page arriving after this document has already
                # run its own appendix tabs: a new volume of the report.
                while len(stack) > 1:
                    old = stack.pop()
                    old.last = i - 1
                    done.append(old)
                stack[0].last = i - 1
                done.append(stack[0])
                stack = [_Doc(first=i, last=n - 1)]

    while stack:
        done.append(stack.pop())
    done.sort(key=lambda d: (d.first, -d.last))
    return done


#: Page kinds that are a picture whatever else is on them. Only the plotted
#: sheet: a report read optically comes back with prose pages measured as
#: figures, and excluding that kind would take the narrative with it. A
#: figure page inside the narrative is caught by its CAPTION instead.
NOT_PROSE_KINDS = ("drawing_sheet",)


def _narrative_evidence(f: PageFacts, running: Sequence[str]) -> Optional[str]:
    """Why this page is the report's prose, or None.

    Being in front of the first tab is a position, not evidence. The page
    must also carry something of the narrative: the running header or footer
    the narrative segment prints on every page, its printed page numbering,
    or the density of actual prose.
    """
    if f.header and _mask_digits(_norm(f.header).strip()) in running:
        return "prose carrying the narrative's running header"
    if f.footer and _mask_digits(_norm(f.footer).strip()) in running:
        return "prose carrying the narrative's running footer"
    if f.printed_page is not None:
        return "prose in the narrative's printed page series"
    if f.kind == "text" and f.text_density >= NARRATIVE_DENSITY:
        return "text-kind page at prose density"
    return None


def _narrative_pages(facts: Sequence[PageFacts], docs: Sequence[_Doc],
                     sections: Sequence[_Section],
                     dividers: Dict[int, str],
                     cues: Dict[int, "_Cue"]) -> Dict[int, str]:
    """The narrative run of each non-appended document.

    A report's narrative is the prose between its front matter and its first
    tab, and it has to LOOK like the narrative as well as sit there. A text
    page inside an appendix is not narrative however much prose it holds; a
    figure page inside the narrative is a figure; and where the document has
    no tabs at all the narrative does not run to the end of it -- it stops
    with the segment that carries the report's own running header.
    """
    from collections import Counter
    out: Dict[int, str] = {}
    counts: Counter = Counter()
    for f in facts:
        for band in (f.header, f.footer):
            if band:
                counts[_mask_digits(_norm(band).strip())] += 1
    running = [k for k, v in counts.items()
               if v >= RUNNING_HEADER_PAGES and len(k) >= 4]

    for doc in docs:
        if doc.appended:
            continue
        no_tabs = doc.first_divider is None
        end = doc.last if no_tabs else doc.first_divider - 1
        for i in range(doc.first, min(end, doc.last) + 1):
            f = facts[i]
            if i in dividers or i == doc.first:
                continue                     # a document opens with its cover
            if f.kind in NOT_PROSE_KINDS:
                continue
            if _is_toc(f) or _is_letter(f) or _is_report_cover(f):
                continue
            cue = cues.get(i)
            if cue is not None and cue.in_title and cue.role in (
                    "figure", "plan", "profile", "photos"):
                continue                     # a captioned page names itself
            floor = (NARRATIVE_MIN_WORDS if f.printed_page is None
                     else NARRATIVE_NUMBERED_WORDS)
            if f.n_words < floor:
                continue
            if f.n_words < NARRATIVE_PROSE_WORDS and (
                    _RE_PLAN.search(f.title_and_bands)
                    or _RE_PROFILE.search(f.title_and_bands)):
                # A plan or a section drawn in the body of the report, with
                # a paragraph of notes beside it. A page of actual prose
                # names the exploration locations too, and stays prose.
                continue
            why = _narrative_evidence(f, running)
            if why is None:
                continue
            if no_tabs:
                # Nothing divides this document, so there is no "before the
                # first tab" to stand in for evidence and the position says
                # nothing at all. Only a page that is PROSE and carries the
                # report's own running band or its printed numbering counts;
                # the rest keep their kind-and-cue labels, and the reader is
                # told the document prints no tabs.
                if f.kind not in ("text", "mixed", "scanned"):
                    continue
                if why.endswith("prose density"):
                    continue
                out[i] = why + " (this document prints no appendix tabs)"
            else:
                out[i] = why
    return out


# ---------------------------------------------------------------------------
# Assignment
# ---------------------------------------------------------------------------

def _mark_boilerplate(facts: Sequence[PageFacts]) -> None:
    """Tell each page which of its top band is the document's running header.

    A tab page inherits the report's running header, and a running header
    that names the report ("… Geotechnical Report … Part B") would otherwise
    read as a declaration on every tab in the document. What repeats is
    boilerplate; what does not is the tab speaking.
    """
    from collections import Counter
    counts: "Counter[str]" = Counter()
    for f in facts:
        for line in {_mask_digits(ln) for ln in f.top_lines + f.bottom_lines}:
            counts[line] += 1
    running = frozenset(k for k, v in counts.items()
                        if v >= RUNNING_HEADER_PAGES and len(k) >= 4)
    for f in facts:
        f.running = running


def _choose_declared(f: PageFacts, declared: Sequence[str]
                     ) -> Tuple[Optional[str], str]:
    """Which of a tab's several roles this page is, from its shape alone.

    A ruled grid with a depth column is one of the logs; a plot or a table of
    results is laboratory work; a page that is mostly picture is
    photographs. Where the shape says nothing, so does this -- the caller
    emits ``other`` with the candidates rather than a confident wrong role.
    """
    log_roles = [r for r in declared if r in LOG_ROLES]
    if log_roles and _has_depth_column(f):
        if len(log_roles) == 1:
            return log_roles[0], "ruled grid with a depth column"
        return None, ""
    if "photos" in declared and f.n_images >= 1 and f.n_words < 80:
        if not log_roles or _RE_PHOTO.search(f.own_title):
            return "photos", "mostly picture, almost no words"
    if "lab_test" in declared and (f.is_ruled or f.kind == "form"
                                   or f.n_images >= 1):
        return "lab_test", "a results form or a plotted result"
    return None, ""


def _has_depth_column(f: PageFacts) -> bool:
    """Does the page carry a log's depth column?"""
    if not (f.is_ruled or f.kind == "form"):
        return False
    return bool(_RE_DEPTH_FIELD.search(f.flat)) and _count(
        _RE_LOG_FIELD, f.flat) >= 3


def _by_kind(f: PageFacts) -> Tuple[str, float, str]:
    if f.kind == "blank":
        return "other", 0.5, "blank page"
    if f.kind in ("figure", "drawing_sheet"):
        return "figure", 0.4, f"{f.kind} page with no title of its own"
    if f.kind == "form" and f.n_images >= 1 and f.n_words < 250:
        # A ruled sheet carrying images and almost no words is a drawing that
        # happened to be plotted with a border and a title block.
        return "figure", 0.4, "ruled sheet that is mostly picture"
    return "other", 0.35, f"{f.kind} page with no title of its own"


def assign(facts: Sequence[PageFacts]) -> List[PageRole]:
    """The role of every page, from the facts alone.

    Kept separate from :func:`page_roles` so it can be exercised on recorded
    facts — a scoring harness reads thousands of pages once and re-runs the
    rules, and a test states the facts by hand.
    """
    n = len(facts)
    _mark_boilerplate(facts)
    dividers: Dict[int, str] = {}
    for f in facts:
        title = _is_divider(f)
        if title:
            dividers[f.page] = title

    sections = _build_sections(facts, dividers)
    docs = _build_documents(facts, sections)
    cues = {f.page: _page_cue(f) for f in facts}
    cues = {k: v for k, v in cues.items() if v is not None}
    narrative = _narrative_pages(facts, docs, sections, dividers, cues)
    no_dividers = not dividers

    doc_of: Dict[int, _Doc] = {}
    for d in docs:
        for i in range(d.first, min(d.last, n - 1) + 1):
            doc_of.setdefault(i, d)
        if d.appended:
            for i in range(d.first, min(d.last, n - 1) + 1):
                doc_of[i] = d

    # A tab speaks only for its own document. When a second volume of the
    # report starts part-way through an appendix, its narrative is narrative
    # — the laboratory tab it happens to sit behind is not talking about it.
    sec_of: Dict[int, _Section] = {}
    for s in sections:
        owner = doc_of.get(s.divider) if s.divider is not None else None
        for i in range(s.first, s.last + 1):
            if 0 <= i < n and (owner is None or doc_of.get(i) is owner):
                sec_of[i] = s

    out: List[PageRole] = []
    for f in facts:
        i = f.page
        doc = doc_of.get(i)
        sec = sec_of.get(i)
        cue = cues.get(i)

        if doc is not None and doc.appended:
            evidence = {
                "tag": "nested-report",
                "rule": "inside a report bound into this one",
                "document_pages": f"{doc.first}-{doc.last}"}
            if cue is not None and cue.role not in ("other",):
                # The page is a page of THAT report, and the role is the
                # binding, not the page. What the page itself is goes in the
                # evidence, so the prior investigation's logs and laboratory
                # sheets can still be made into items later.
                evidence["inner_role"] = cue.role
                evidence["inner_why"] = cue.why
            out.append(PageRole(i, "appended_report", 0.85, evidence))
            continue

        if f.kind == "blank":
            out.append(PageRole(i, "other", 0.6,
                                {"tag": "blank", "rule": "blank page"}))
            continue

        if i in dividers:
            out.append(PageRole(i, "divider", 0.85, {
                "tag": "tab",
                "rule": "tab or cover page naming what follows",
                "title": dividers[i][:80]}))
            continue

        front = doc is not None and (
            doc.first_divider is None or i < doc.first_divider)
        if front:
            if _is_toc(f):
                out.append(PageRole(i, "toc", 0.85,
                                    {"tag": "toc", "rule": "table of contents heading"}))
                continue
            if _is_report_cover(f):
                out.append(PageRole(i, "cover", 0.8,
                                    {"tag": "cover", "rule": "report title page"}))
                continue
            if _is_letter(f):
                out.append(PageRole(i, "letter", 0.8,
                                    {"tag": "letter", "rule": "cover letter wording"}))
                continue

        if i in narrative:
            # Before the cues. Principle A is about a page overruling its
            # TAB; the narrative block is not a tab, it is positive evidence
            # that this page is the report's prose, and a narrative page
            # DISCUSSES the test pits, the laboratory testing and the
            # infiltration rates. Letting a cue win here was measured: it
            # bought two pages out of sample and cost eighteen in it.
            out.append(PageRole(i, "narrative", 0.85,
                                {"tag": "narrative-block",
                                 "rule": narrative[i]}))
            continue

        declared = list(sec.roles) if sec else []
        exploration = declared == ["exploration"] or len(declared) >= 3
        if len(declared) >= 3:
            # A tab that lists three or four different kinds of page is a
            # contents list for its appendix, not a statement that every
            # page in it is the first thing on the list.
            pass
        if declared == ["exploration"]:
            # "APPENDIX A — SUBSURFACE EXPLORATION DATA" says the appendix
            # holds the field work; it does not say which page is a boring,
            # which a test pit and which the general notes in front of them.
            # Only the pages' own titles can say that.
            declared = list(LOG_ROLES) + ["photos"]

        # A tab that names one thing is strong evidence for every page under
        # it. A page overrules it only by TITLING itself something else: a
        # laboratory sheet naming the trial pit its sample came from, in its
        # header, is still a laboratory sheet.
        single_tab = len(declared) == 1 and not exploration
        if cue is not None and (cue.decisive or not single_tab
                                or cue.role in declared):
            out.append(PageRole(i, cue.role, cue.confidence,
                                {"tag": "page-title",
                                 "rule": "the page names itself",
                                 "why": cue.why}))
            continue

        if cue is not None and declared and cue.role in declared:
            out.append(PageRole(i, cue.role, cue.confidence, {
                "tag": "page-title+tab",
                "rule": "the page names itself, and its appendix expects it",
                "why": cue.why,
                "appendix": (sec.title or "")[:60] if sec else None}))
            continue

        if (declared and not exploration and f.kind == "drawing_sheet"
                and cue is None):
            # A D-size sheet bound into an appendix of logs is a drawing —
            # a cross-section or a plan — not a log page.
            out.append(PageRole(i, "figure", 0.5, {
                "tag": "sheet-in-tab",
                "rule": "large-format sheet inside an appendix of "
                        f"{declared[0]} pages"}))
            continue

        if (declared and declared[0] in LOG_ROLES and cue is None
                and f.kind == "text" and not f.is_ruled
                and f.n_words >= NARRATIVE_PROSE_WORDS):
            # A tab of logs does not make a page of prose a log. Some
            # reports write their data volume as numbered prose sections
            # about the explorations rather than as forms.
            out.append(PageRole(i, "other", 0.5, {
                "tag": "prose-in-tab",
                "rule": f"prose page inside an appendix of "
                        f"{declared[0]} pages"}))
            continue

        if declared and not exploration:
            single = len(declared) == 1
            if single:
                out.append(PageRole(i, declared[0], INHERITED_CONFIDENCE, {
                    "tag": "tab-declares",
                    "rule": "INHERITED from its appendix tab; the page says "
                            "nothing about itself",
                    "appendix": (sec.title or "")[:60] if sec else None,
                    "declared": declared}))
                continue
            if cue is None:
                chosen, why = _choose_declared(f, declared)
                if chosen is None:
                    out.append(PageRole(i, "other", INHERITED_CONFIDENCE - 0.2, {
                        "tag": "tab-ambiguous",
                        "rule": "its appendix tab names several things and "
                                "the page names none of them",
                        "appendix": (sec.title or "")[:60] if sec else None,
                        "candidates": declared}))
                else:
                    out.append(PageRole(i, chosen, INHERITED_CONFIDENCE - 0.1, {
                        "tag": "tab-declares",
                        "rule": "INHERITED from a tab that names several "
                                "things; chosen on the page's own shape",
                        "why": why,
                        "appendix": (sec.title or "")[:60] if sec else None,
                        "declared": declared}))
                continue

        if cue is not None:
            out.append(PageRole(i, cue.role, cue.confidence,
                                {"rule": "the page names itself",
                                 "why": cue.why}))
            continue

        role, conf, why = _by_kind(f)
        out.append(PageRole(i, role, conf, {"tag": "page-shape", "rule": why}))

    return _smooth_runs(facts, out, dividers)


def _fill_ambiguous_runs(facts: Sequence[PageFacts],
                         out: List[PageRole]) -> None:
    """Give a run of unplaceable pages the log its neighbours belong to.

    A tab that names several things leaves a page with no role of its own as
    other. But a run of such pages closed on both sides by pages of ONE
    log is part of that log: the document's own ordering is evidence, where
    the pages have none of their own. Said at low confidence, and the
    candidates the tab offered are kept.
    """
    n = len(out)
    i = 0
    while i < n:
        if out[i].evidence.get("tag") != "tab-ambiguous":
            i += 1
            continue
        j = i
        while j + 1 < n and out[j + 1].evidence.get("tag") == "tab-ambiguous":
            j += 1
        before = out[i - 1].role if i > 0 else None
        after = out[j + 1].role if j + 1 < n else None
        if before == after and before in LOG_ROLES:
            for k in range(i, j + 1):
                out[k] = PageRole(facts[k].page, before, 0.55, {
                    "tag": "between-pages-of-one-log",
                    "rule": "its tab named several things and the page names "
                            "none; the run sits inside one log",
                    "candidates": out[k].evidence.get("candidates")})
        i = j + 1


def _smooth_runs(facts: Sequence[PageFacts], roles: List[PageRole],
                 dividers: Dict[int, str]) -> List[PageRole]:
    """Let a log run carry its continuation sheets.

    A log printed "Page 2 of 3" repeats none of its title block; on its own it
    reads as a nameless ruled form. A page that sits between two pages of one
    log, and has the shape of a log form, belongs to that log.
    """
    out = list(roles)
    _fill_ambiguous_runs(facts, out)
    for i, f in enumerate(facts):
        r = out[i]
        if r.role not in ("other", "figure") or i in dividers:
            continue
        prev = out[i - 1] if i > 0 else None
        if prev is None or prev.role not in LOG_ROLES:
            continue
        n_fields = _count(_RE_LOG_FIELD, f.flat)
        cont = bool(_RE_PAGE_OF.search(f.top + f.bottom))
        if n_fields >= 5 or (cont and n_fields >= 3):
            out[i] = PageRole(f.page, prev.role, 0.7, {
                "tag": "run-continuation",
                "rule": "continuation sheet of the log on the page before",
                "why": f"{n_fields} log form fields"
                       + (", printed 'page N of M'" if cont else "")})
    return out


# ---------------------------------------------------------------------------
# Work items
# ---------------------------------------------------------------------------

_ITEM_ROLE_KIND = {
    "boring_log": "boring_log",
    "test_pit_log": "test_pit_log",
    "cpt_log": "cpt_log",
    "dcp_log": "dcp_log",
    "lab_test": "lab_test",
    "field_test": "field_test",
    "calculation": "calculation",
    "appended_report": "appended_report",
    "photos": "photos",
    "narrative": "narrative",
    "figure": "figures",
    "plan": "figures",
    "profile": "figures",
    "cover": "front_matter",
    "letter": "front_matter",
    "toc": "front_matter",
    "divider": None,
    "other": "other",
}


def _exploration_id(f: PageFacts) -> Optional[str]:
    """The boring or pit id printed in the page's title block."""
    for text in (f.title, f.top):
        for m in _RE_EXPLORATION_ID.finditer(text.upper()):
            tok = m.group(1).replace(" ", "-").replace("–", "-")
            if any(ch.isdigit() for ch in tok):
                return tok
    return None


def _item_key(f: PageFacts, role: str) -> Optional[str]:
    """What makes this page the START of a new item, or None to continue."""
    if role in LOG_ROLES:
        m = _RE_PAGE_OF.search(f.top + f.bottom)
        if m and int(m.group(1)) > 1:
            return None                      # a continuation sheet
        return _exploration_id(f) or "log"
    if role in ("lab_test", "field_test"):
        m = _RE_LAB.search(f.title_and_bands) or _RE_FIELD_TEST.search(
            f.title_and_bands)
        return m.group(0) if m else "sheet"
    if role == "calculation":
        m = _RE_PAGE_OF.search(f.top + f.bottom)
        if m and int(m.group(1)) > 1:
            return None                      # a continuation sheet
        m = _RE_CALC_PROGRAM.search(f.flat)
        if m:
            return m.group(0)
        m = _RE_CALC.search(f.title)
        if m:
            return m.group(0) + " " + (f.heading or "")[:40]
        # A printout's later pages carry neither a banner nor a heading of
        # their own. Keeping the item open is right: a run of pages IS the
        # printout, and splitting on whatever line happened to be set
        # largest would make one item per page.
        return None
    return None


def build_items(facts: Sequence[PageFacts],
                roles: Sequence[PageRole]) -> List[Item]:
    """Group pages into the work items a reader consumes one at a time."""
    items: List[Item] = []
    current: Optional[Item] = None
    cur_key: Optional[str] = None
    by_page = {r.page: r for r in roles}

    def close() -> None:
        nonlocal current, cur_key
        current = None
        cur_key = None

    for f in facts:
        r = by_page.get(f.page)
        if r is None:
            continue
        kind = _ITEM_ROLE_KIND.get(r.role)
        if kind is None:
            close()
            continue
        key = _item_key(f, r.role)
        start = (current is None or current.kind != kind
                 or (key is not None and key != cur_key))
        if start:
            title = None
            if r.role in LOG_ROLES:
                title = _exploration_id(f)
            elif r.role in ("lab_test", "field_test", "calculation"):
                title = (f.heading or None)
            elif r.role == "narrative":
                title = "Narrative"
            elif r.role == "appended_report":
                title = (f.heading or "Appended report")
            current = Item(id=f"item_{len(items) + 1}", kind=kind,
                           pages=[f.page],
                           title=(title[:80] if title else None),
                           evidence={"first_page": f.page})
            items.append(current)
            cur_key = key if key is not None else cur_key
        else:
            current.pages.append(f.page)
            if key is not None:
                cur_key = key

    out: List[Item] = []
    id_of: Dict[int, str] = {}
    for n, item in enumerate(items, start=1):
        fixed = Item(id=f"item_{n}", kind=item.kind, pages=list(item.pages),
                     title=item.title, evidence=dict(item.evidence))
        out.append(fixed)
        for p in fixed.pages:
            id_of[p] = fixed.id
    for n, r in enumerate(roles):
        if r.item_id is None and r.page in id_of:
            roles[n] = PageRole(r.page, r.role, r.confidence, r.evidence,
                                id_of[r.page])
    return out


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------

def page_roles(doc) -> List[PageRole]:
    """The role of every page of an open document, with its evidence."""
    facts = facts_from_document(doc)
    roles = assign(facts)
    build_items(facts, roles)
    return roles


def document_items(doc) -> List[Item]:
    """The work items of an open document: logs, sheets, printouts, narrative."""
    facts = facts_from_document(doc)
    roles = assign(facts)
    return build_items(facts, roles)


def roles_and_items(doc) -> Tuple[List[PageRole], List[Item]]:
    """Both, for a caller that wants them without reading the document twice."""
    facts = facts_from_document(doc)
    roles = assign(facts)
    items = build_items(facts, roles)
    return roles, items


# ---------------------------------------------------------------------------
# What the report says about itself
# ---------------------------------------------------------------------------
#
# A model reviewing a report needs the report's own account of its contents
# before it reads any of them: the table of contents, the list of figures,
# the appendix tabs, the figure captions, the section headings. All of it is
# printed ON the pages; none of it is inferred. An entry this cannot place on
# a page carries ``page = None``, never a guess.

#: What a contents list calls itself, in the languages the corpus is in.
CONTENTS_HEADINGS = (
    "table of contents", "contents", "table des matieres", "sommaire",
    "indice", "indice general", "tabla de contenido", "conteudo",
    "inhaltsverzeichnis",
)
FIGURE_LIST_HEADINGS = (
    "list of figures", "figures", "table of figures", "liste des figures",
    "lista de figuras", "indice de figuras", "plates", "list of plates",
)
TABLE_LIST_HEADINGS = (
    "list of tables", "tables", "liste des tableaux", "lista de tablas",
    "indice de tablas",
)
APPENDIX_LIST_HEADINGS = (
    "list of appendices", "list of appendixes", "appendices", "appendixes",
    "annexes", "anexos", "apendices", "attachments", "list of attachments",
    "liste des annexes",
)

_RE_CONTENTS_HEADING = _phrases(CONTENTS_HEADINGS)
_RE_FIGURE_LIST = _phrases(FIGURE_LIST_HEADINGS)
_RE_TABLE_LIST = _phrases(TABLE_LIST_HEADINGS)
_RE_APPENDIX_LIST = _phrases(APPENDIX_LIST_HEADINGS)

#: "Figure 3", "Table A-2", "Appendix B", "Annexe 4", "Plate 7" — the label a
#: listed item and its own page both print.
_RE_ITEM_LABEL = re.compile(
    r"^\s*(?P<word>figure|fig|plate|table|tableau|tabla|appendix|appendice|"
    r"apendice|annex|annexe|anexo|attachment|exhibit|part)\.?\s*"
    r"(?P<number>[A-Z]{0,2}-?\d{1,3}[A-Za-z]?|[A-Z])\b[\s:.–—-]*"
    r"(?P<rest>.*)$", re.I)

#: A dotted leader, or three or more spaces, then what must be a page number.
_RE_LEADER = re.compile(
    r"^(?P<title>.*?)[\s.·•…_-]{3,}"
    r"(?P<page>[ivxlcdm]{1,7}|[A-Z]{0,2}-?\d{1,4})\s*$", re.I)

#: A title and a page number separated by nothing but space.
_RE_TRAILING_PAGE = re.compile(
    r"^(?P<title>\S.*?\S)\s{2,}(?P<page>[ivxlcdm]{1,7}|[A-Z]{0,2}-?\d{1,4})\s*$",
    re.I)

#: A numbered section heading: "3.2 Subsurface Conditions", "4.0 FINDINGS".
_RE_SECTION_NUMBER = re.compile(r"^\s*(\d{1,2}(?:\.\d{1,2}){0,3})\s+(\S.*)$")

#: Shortest and longest a listed title may be, so a stray number or a
#: paragraph of prose is not read as an entry.
OUTLINE_MIN_TITLE = 3
OUTLINE_MAX_TITLE = 120


@dataclass(frozen=True)
class OutlineEntry:
    """One line of a contents list, as the list printed it.

    ``printed_page`` is the page number AS WRITTEN ("12", "A-3", "iv"),
    because that is what a reader cites; ``page`` is the 0-based PDF page the
    entry was matched to, and is ``None`` when it could not be matched to one
    without guessing.
    """

    kind: str                       # contents | figure | table | appendix
    title: str
    number: Optional[str] = None
    printed_page: Optional[str] = None
    page: Optional[int] = None
    listed_on: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return _compact({
            "kind": self.kind, "number": self.number, "title": self.title,
            "printed_page": self.printed_page, "page": self.page,
            "listed_on": self.listed_on,
        })


@dataclass(frozen=True)
class OutlineMark:
    """Something a page prints about itself: a tab, a caption, a heading."""

    kind: str                       # divider | caption | heading
    page: int
    text: str
    number: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        d = _compact({"kind": self.kind, "number": self.number,
                      "text": self.text})
        d["page"] = self.page
        return d


@dataclass(frozen=True)
class Outline:
    """The report's own account of its contents."""

    entries: List[OutlineEntry] = field(default_factory=list)
    dividers: List[OutlineMark] = field(default_factory=list)
    captions: List[OutlineMark] = field(default_factory=list)
    headings: List[OutlineMark] = field(default_factory=list)
    #: True when the document prints no appendix tab anywhere. Said out loud
    #: because everything downstream leans on the tabs: with none, no page
    #: inherits a role, the narrative is only what carries the report's own
    #: running band, and the rest of the labels are the pages' own shapes at
    #: low confidence. A reviewer should look at such a document first.
    no_dividers: bool = False

    def of_kind(self, kind: str) -> List[OutlineEntry]:
        return [e for e in self.entries if e.kind == kind]

    @property
    def contents(self) -> List[OutlineEntry]:
        return self.of_kind("contents")

    @property
    def figures(self) -> List[OutlineEntry]:
        return self.of_kind("figure")

    @property
    def tables(self) -> List[OutlineEntry]:
        return self.of_kind("table")

    @property
    def appendices(self) -> List[OutlineEntry]:
        return self.of_kind("appendix")

    def to_dict(self) -> Dict[str, Any]:
        placed = sum(1 for e in self.entries if e.page is not None)
        return {
            "no_dividers": self.no_dividers,
            "n_entries": len(self.entries),
            "n_entries_placed": placed,
            "entries": [e.to_dict() for e in self.entries],
            "dividers": [m.to_dict() for m in self.dividers],
            "captions": [m.to_dict() for m in self.captions],
            "headings": [m.to_dict() for m in self.headings],
        }


def _looks_like_page_number(text: str) -> bool:
    t = text.strip().strip(".")
    if not t:
        return False
    if re.fullmatch(r"[A-Za-z]{0,2}-?\d{1,4}", t):
        return True
    return bool(re.fullmatch(r"[ivxlcdmIVXLCDM]{1,7}", t))


def _list_kind(heading: str) -> Optional[str]:
    """Which list a heading opens, or None."""
    text = _norm(heading)
    if _RE_FIGURE_LIST.search(text):
        return "figure"
    if _RE_TABLE_LIST.search(text):
        return "table"
    if _RE_APPENDIX_LIST.search(text):
        return "appendix"
    if _RE_CONTENTS_HEADING.search(text):
        return "contents"
    return None


def _entry_from_line(line: str, default_kind: str):
    """``(kind, number, title, printed_page)`` for one listed line, or None."""
    raw = " ".join(line.split())
    if not raw:
        return None
    printed = None
    body = raw
    for rx in (_RE_LEADER, _RE_TRAILING_PAGE):
        m = rx.match(raw)
        if m and _looks_like_page_number(m.group("page")):
            body = m.group("title").strip(" .·…_-")
            printed = m.group("page").strip()
            break
    kind, number = default_kind, None
    m = _RE_ITEM_LABEL.match(body)
    if m:
        word = m.group("word").lower()
        number = m.group("number")
        rest = m.group("rest").strip(" :.–—-")
        if word in ("figure", "fig", "plate"):
            kind = "figure"
        elif word in ("table", "tableau", "tabla"):
            kind = "table"
        else:
            kind = "appendix"
        body = rest or (m.group("word").title() + " " + str(number))
    if not (OUTLINE_MIN_TITLE <= len(body) <= OUTLINE_MAX_TITLE):
        return None
    if not any(ch.isalpha() for ch in body):
        return None
    return kind, number, body, printed


def _read_list_pages(facts, lines_of) -> List[OutlineEntry]:
    """Every entry printed on the document's contents and list pages.

    A line whose text ends in a page number, on a page that calls itself a
    contents list. The current list heading decides an entry's kind when the
    entry does not name itself ("Figure 3" says what it is; "2.0 Site
    Conditions" takes it from the heading above it).
    """
    out: List[OutlineEntry] = []
    for f in facts:
        heading_kind = _list_kind(f.title) or _list_kind(f.top)
        if heading_kind is None and not _is_toc(f):
            continue
        current = heading_kind or "contents"
        pending = None
        for raw in lines_of(f.page):
            flat = " ".join(raw.split())
            found = _list_kind(flat)
            if found is not None and len(flat) <= 40:
                current = found
                pending = None
                continue
            entry = _entry_from_line(raw, current)
            if entry is None:
                # "Figure 1:" on its own line, its title on the next — the
                # layout every tab page in the corpus uses.
                m = _RE_ITEM_LABEL.match(flat)
                if m and not m.group("rest").strip():
                    word = m.group("word").lower()
                    kind = ("figure" if word in ("figure", "fig", "plate")
                            else "table" if word in ("table", "tableau",
                                                     "tabla")
                            else "appendix")
                    pending = (kind, m.group("number"))
                continue
            kind, number, title, printed = entry
            if pending is not None and number is None:
                kind, number = pending
                pending = None
            out.append(OutlineEntry(kind=kind, number=number, title=title,
                                    printed_page=printed, listed_on=f.page))
    return out


def _caption(f: PageFacts, lines_of) -> Optional[OutlineMark]:
    """The "Figure 3 - Site Plan" line a figure page prints, or None."""
    for raw in lines_of(f.page):
        text = " ".join(raw.split())
        if not (OUTLINE_MIN_TITLE <= len(text) <= OUTLINE_MAX_TITLE):
            continue
        m = _RE_ITEM_LABEL.match(text)
        if m and m.group("word").lower() in ("figure", "fig", "plate",
                                             "table"):
            return OutlineMark(kind="caption", page=f.page, text=text,
                               number=m.group("number"))
    return None


def _section_heading(f: PageFacts) -> Optional[OutlineMark]:
    """A narrative page's section heading, when it prints one."""
    text = " ".join((f.heading or "").split())
    if not (OUTLINE_MIN_TITLE <= len(text) <= OUTLINE_MAX_TITLE):
        return None
    m = _RE_SECTION_NUMBER.match(text)
    if m:
        return OutlineMark(kind="heading", page=f.page,
                           text=" ".join(m.group(2).split()),
                           number=m.group(1))
    # An unnumbered heading counts only when it is set apart: short, and not
    # the first sentence of a paragraph.
    if len(text) <= 60 and not text.endswith((".", ",", ";")):
        words = text.split()
        if len(words) <= 9 and (text.isupper() or sum(
                w[:1].isupper() for w in words) >= max(1, len(words) - 2)):
            return OutlineMark(kind="heading", page=f.page, text=text)
    return None


def _match_entries(entries, dividers, captions, headings):
    """Place each entry on the page that carries it, or leave it unplaced.

    A match must be unique and must agree on the item's number when both
    sides print one. Nothing is placed by position or by proximity.
    """
    def key(text: str) -> str:
        return _norm(text).strip()

    pools = {"appendix": dividers, "figure": captions + dividers,
             "table": captions, "contents": headings}
    out: List[OutlineEntry] = []
    for e in entries:
        pool = pools.get(e.kind, [])
        want = key(e.title)
        hits = []
        for mark in pool:
            got = key(mark.text)
            if e.number and mark.number and (
                    e.number.lower() != mark.number.lower()):
                continue
            if not want or len(want) < 6:
                if e.number and mark.number and (
                        e.number.lower() == mark.number.lower()):
                    hits.append(mark)
                continue
            if want in got or got in want:
                hits.append(mark)
        pages = {m.page for m in hits}
        out.append(OutlineEntry(
            kind=e.kind, title=e.title, number=e.number,
            printed_page=e.printed_page,
            page=(hits[0].page if len(pages) == 1 else None),
            listed_on=e.listed_on))
    return out


def outline_from(facts, roles, lines_of) -> Outline:
    """The outline, from facts, their roles and a page -> text lines lookup."""
    role_of = {r.page: r for r in roles}
    dividers: List[OutlineMark] = []
    captions: List[OutlineMark] = []
    headings: List[OutlineMark] = []
    for f in facts:
        r = role_of.get(f.page)
        if r is None:
            continue
        if r.role == "divider":
            text = " ".join((r.evidence.get("title")
                             or f.declaration).split())[:200]
            m = _RE_APPENDIX_TOKEN.search(f.declaration)
            dividers.append(OutlineMark(
                kind="divider", page=f.page, text=text,
                number=(m.group(1).upper() if m else None)))
        elif r.role in ("figure", "plan", "profile"):
            cap = _caption(f, lines_of)
            if cap is not None:
                captions.append(cap)
        elif r.role == "narrative":
            head = _section_heading(f)
            if head is not None:
                headings.append(head)
    entries = _read_list_pages(facts, lines_of)
    return Outline(entries=_match_entries(entries, dividers, captions,
                                          headings),
                   dividers=dividers, captions=captions, headings=headings,
                   no_dividers=not dividers)


def document_outline(doc) -> Outline:
    """What the report says about itself: contents, lists, tabs, captions.

    Deterministic and literal. Every entry is a line somebody printed; an
    entry this could not place on a page carries ``page = None``.
    """
    facts = facts_from_document(doc)
    roles = assign(facts)

    def lines_of(index: int) -> List[str]:
        return [ln.text for ln in doc.page(index, tables=False).lines
                if (ln.text or "").strip()]

    return outline_from(facts, roles, lines_of)


# ---------------------------------------------------------------------------
# One line per page, for a model to read
# ---------------------------------------------------------------------------

def _clip(text: Optional[str], n: int) -> str:
    out = " ".join((text or "").split())
    return out[:n]


def ledger_from(facts, roles, di_pages=()) -> List[str]:
    """One compact line per page. See :func:`page_ledger`."""
    di = set(di_pages)
    role_of = {r.page: r for r in roles}
    out: List[str] = []
    for f in facts:
        r = role_of.get(f.page)
        role = r.role if r else "other"
        conf = r.confidence if r else 0.0
        tag = (r.evidence.get("tag") if r else None) or "-"
        parts = [
            "p%03d" % f.page,
            "%-13s" % f.kind,
            "%-15s" % role,
            "%.2f" % conf,
            "[%s]" % tag,
            '"%s"' % _clip(f.heading, 60),
        ]
        if f.header:
            parts.append('hdr="%s"' % _clip(f.header, 40))
        if f.printed_page is not None:
            parts.append("pp=%s" % f.printed_page
                         + ("/%s" % f.printed_of if f.printed_of else ""))
        if f.segment is not None:
            parts.append("seg=%s" % f.segment)
        parts.append("chars=%d" % f.n_text_chars)
        parts.append("text_ok=" + ("Y" if f.text_reliable else "N"))
        parts.append("di=" + ("Y" if f.page in di else "N"))
        if r is not None and r.item_id:
            parts.append(r.item_id)
        out.append(" ".join(parts))
    return out


def page_ledger(doc, roles=None) -> List[str]:
    """One line per page, for a model that will review the whole document.

    ``p035 form boring_log 0.90 [page-title] "BORING LOG NO. B-1" hdr="..."
    pp=1 seg=12 chars=1306 text_ok=Y di=N item_7``. Everything a reader needs
    to decide which pages to open, in the order the pages come, at about a
    line each. Pass the roles when they have already been computed.
    """
    facts = facts_from_document(doc)
    if roles is None:
        roles = assign(facts)
        build_items(facts, roles)
    di = [s.page for s in doc.page_map()
          if s.evidence.get("text_source") == SOURCE_AZURE_DI]
    return ledger_from(facts, roles, di)
