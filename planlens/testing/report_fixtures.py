"""A synthetic geotechnical report with known page roles and work items.

Built for :mod:`planlens.document.roles`: every kind of page the role rules
have to tell apart, in the order a real report puts them, with the answer
stated by hand. Nothing here is taken from a real document — the wording is
the wording of the trade, not of anybody's report.

Layout::

    0        cover               title page, "Prepared for"
    1        toc                 contents, list of figures, list of appendices
    2-4      narrative           prose, running header, "Page 1..3 of 3"
    5        figure              a drawn detail with "Figure 1 - ..." beneath
    6        divider             APPENDIX A - BORING LOGS AND TEST PIT LOGS
    7-8      boring_log          ruled log form, "BORING NO. B-1", 2 sheets
    9        test_pit_log        ruled log form, "TEST PIT LOG TP-1"
    10       photos              a picture with "Photograph 1 - view looking"
    11       divider             APPENDIX B - LABORATORY TEST DATA
    12       lab_test            ATTERBERG LIMITS sheet
    13       lab_test            SIEVE ANALYSIS sheet
    14       divider             APPENDIX C - GEOTECHNICAL REPORT BY OTHERS
    15       appended_report     the nested report's own cover
    16       appended_report     its narrative, its own running header
    17       appended_report     its own "APPENDIX A - BORING LOGS" tab
    18       appended_report     a boring log inside it
    19       divider             APPENDIX D - CALCULATIONS
    20-21    calculation         a program printout, "LPILE" banner, 2 pages

The appended report is the case worth the trouble: pages 14-18 look like a
cover, prose, a tab and a boring log, and every one of them is a page of one
appended document.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

LETTER = (612.0, 792.0)

PROSE = (
    "The subsurface exploration consisted of four borings advanced with "
    "hollow-stem augers and three test pits excavated with a rubber-tyred "
    "backhoe. Standard penetration tests were performed at 1.5 m intervals "
    "and samples were classified in the field by a geotechnical engineer. "
    "Groundwater was encountered in two of the borings at the time of "
    "drilling. Laboratory testing included moisture content determinations, "
    "Atterberg limits and grain size distribution. The recommendations that "
    "follow are based on the conditions encountered at the exploration "
    "locations and on the loads provided by the structural engineer. ")

NESTED_PROSE = (
    "This study was carried out for the previous owner of the property and "
    "is reproduced here in full. Four borings were advanced to refusal on "
    "weathered rock and the results are presented in the appendix to this "
    "study. The allowable bearing pressure recommended for spread footings "
    "founded in the dense residual soil is stated in the summary below. ")


@dataclass
class ReportGT:
    """The synthetic report and what every page of it is."""

    pdf: bytes
    n_pages: int = 22
    #: page -> expected role.
    roles: Dict[int, str] = field(default_factory=lambda: {
        0: "cover", 1: "toc",
        2: "narrative", 3: "narrative", 4: "narrative",
        5: "figure",
        6: "divider",
        7: "boring_log", 8: "boring_log", 9: "test_pit_log", 10: "photos",
        11: "divider", 12: "lab_test", 13: "lab_test",
        14: "divider",
        15: "appended_report", 16: "appended_report", 17: "appended_report",
        18: "appended_report", 19: "divider",
        20: "calculation", 21: "calculation",
    })
    #: (kind, first page, last page) of the work items that must come out.
    items: List[Tuple[str, int, int]] = field(default_factory=lambda: [
        ("narrative", 2, 4),
        ("boring_log", 7, 8),
        ("test_pit_log", 9, 9),
        ("photos", 10, 10),
        ("lab_test", 12, 12),
        ("lab_test", 13, 13),
        ("appended_report", 15, 18),
        ("calculation", 20, 21),
    ])
    #: What the outline must read off the contents page, in order:
    #: (kind, number, title, printed page as written, the page it resolves
    #: to or None). Figure 2 and the two sections whose headings the report
    #: does not print must stay unplaced.
    outline_entries: List[Tuple[str, Optional[str], str, Optional[str],
                                Optional[int]]] = field(
        default_factory=lambda: [
            ("contents", None, "1.0 Introduction", "1", 2),
            ("contents", None, "2.0 Site Conditions", "1", 3),
            ("contents", None, "3.0 Subsurface Exploration", "2", 4),
            ("contents", None, "4.0 Laboratory Testing", "3", None),
            ("contents", None, "5.0 Recommendations", "3", None),
            ("figure", "1", "Footing Undercut Detail", "4", 5),
            ("figure", "2", "Lateral Earth Pressure Diagram", "5", None),
            ("appendix", "A", "Boring Logs and Test Pit Logs", None, 6),
            ("appendix", "B", "Laboratory Test Data", None, 11),
            ("appendix", "C", "Geotechnical Report by Others", None, 14),
            ("appendix", "D", "Calculations", None, 19),
        ])
    #: The page that carries the figure caption.
    caption_page: int = 5
    header: str = "Rosewood Terrace Development | Geotechnical Investigation"
    nested_header: str = "Former Owner Site Study | Preliminary Geotechnics"


def _cover(doc) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((90, 250), "GEOTECHNICAL ENGINEERING REPORT", fontsize=20,
                  fontname="hebo")
    p.insert_text((90, 285), "Rosewood Terrace Development", fontsize=18)
    p.insert_textbox(fitz.Rect(90, 420, 520, 620),
                     "Prepared for:\nRosewood Terrace Partners\n\n"
                     "Prepared by:\nSoil & Rock Consulting Engineers\n"
                     "14 March 2026", fontsize=11)


#: The contents page, its list of figures and its list of appendices, with
#: page numbers written the way a report writes them. Figure 2 is listed and
#: does NOT exist in the document, so the outline has to leave it unplaced
#: rather than guess at a page for it.
TOC_LINES = (
    "TABLE OF CONTENTS",
    "1.0 Introduction ........................................... 1",
    "2.0 Site Conditions ........................................ 1",
    "3.0 Subsurface Exploration ................................. 2",
    "4.0 Laboratory Testing ..................................... 3",
    "5.0 Recommendations ........................................ 3",
    "",
    "LIST OF FIGURES",
    "Figure 1  Footing Undercut Detail .......................... 4",
    "Figure 2  Lateral Earth Pressure Diagram ................... 5",
    "",
    "APPENDICES",
    "Appendix A  Boring Logs and Test Pit Logs",
    "Appendix B  Laboratory Test Data",
    "Appendix C  Geotechnical Report by Others",
    "Appendix D  Calculations",
)

#: The narrative's section headings, in order. They match the contents list,
#: so an entry can be placed on the page that actually carries it.
SECTION_HEADINGS = ("1.0 Introduction", "2.0 Site Conditions",
                    "3.0 Subsurface Exploration")

#: The caption the one figure page prints beneath its drawing.
FIGURE_CAPTION = "Figure 1 - Footing Undercut Detail"


def _toc(doc) -> None:
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((90, 90), TOC_LINES[0], fontsize=16, fontname="hebo")
    y = 130
    for line in TOC_LINES[1:]:
        if line:
            bold = line in ("LIST OF FIGURES", "APPENDICES")
            p.insert_text((90, y), line, fontsize=11,
                          fontname="hebo" if bold else "helv")
        y += 20


def _narrative(doc, gt: ReportGT, n: int) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 40), gt.header, fontsize=8)
    p.insert_text((72, 100), SECTION_HEADINGS[n - 1], fontsize=14,
                  fontname="hebo")
    p.insert_textbox(fitz.Rect(72, 120, 540, 720), PROSE * 3, fontsize=10)
    p.insert_text((72, 770), f"Project 26-118 | Page {n} of 3", fontsize=8)


def _figure_page(doc) -> None:
    """A drawn figure with the caption a figure page prints beneath it."""
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    shape = p.new_shape()
    for i in range(360):
        x = 90 + (i % 24) * 18
        y = 120 + (i // 24) * 22
        shape.draw_line((x, y), (x + 14, y + 10))
        shape.finish(color=(0, 0, 0), width=0.6)
    shape.commit()
    p.insert_text((90, 96), "FOOTING UNDERCUT DETAIL", fontsize=13,
                  fontname="hebo")
    p.insert_text((90, 480), FIGURE_CAPTION, fontsize=11)


def _divider(doc, title: str, contents: str = "") -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((90, 300), title, fontsize=20, fontname="hebo")
    if contents:
        p.insert_textbox(fitz.Rect(90, 340, 520, 520), contents, fontsize=11)


def _log_page(doc, title: str, number: str, sheet: str, fields) -> None:
    """A ruled log form with a title block and column headings."""
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 46), title, fontsize=13, fontname="hebo")
    p.insert_text((430, 46), number, fontsize=13, fontname="hebo")
    p.insert_text((72, 64), "Project: Rosewood Terrace   Sheet " + sheet,
                  fontsize=8)
    xs = [72 + 44 * i for i in range(11)]
    ys = [90 + 26 * i for i in range(24)]
    for x in xs:
        p.draw_line((x, ys[0]), (x, ys[-1]), width=0.6)
    for y in ys:
        p.draw_line((xs[0], y), (xs[-1], y), width=0.6)
    for i, head in enumerate(fields):
        p.insert_text((xs[i] + 2, ys[0] + 16), head, fontsize=6)
    for r in range(1, 22, 3):
        p.insert_text((xs[0] + 2, ys[r] + 16), f"{1.5 * r:.1f}", fontsize=7)
        p.insert_text((xs[1] + 2, ys[r] + 16), f"{100 - r:.1f}", fontsize=7)
        p.insert_text((xs[2] + 2, ys[r] + 16), "SS", fontsize=7)
        p.insert_text((xs[3] + 2, ys[r] + 16), f"{3 + r}-{5 + r}-{7 + r}",
                      fontsize=7)
        p.insert_text((xs[5] + 2, ys[r] + 16), "CL", fontsize=7)
        p.insert_textbox(fitz.Rect(xs[6] + 2, ys[r] + 6, xs[10], ys[r] + 24),
                         "Brown sandy lean CLAY, stiff, moist", fontsize=6)
    p.insert_text((72, 770), "Drilled by Regional Drilling   Logged by AB",
                  fontsize=7)


LOG_FIELDS = ("DEPTH (m)", "ELEV (m)", "SAMPLE", "BLOWS N", "RECOVERY",
              "USCS", "MATERIAL DESCRIPTION", "", "", "", "")


def _photo_page(doc) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 60), "Photograph 1 - Test Pit TP-1, view looking north",
                  fontsize=12, fontname="hebo")
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 320, 240), False)
    pix.set_rect(pix.irect, (150, 140, 120))
    p.insert_image(fitz.Rect(90, 90, 520, 410), pixmap=pix)
    p.insert_text((72, 430), "Photograph 2 - stockpiled spoil, view looking "
                             "east", fontsize=12, fontname="hebo")
    pix2 = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 320, 240), False)
    pix2.set_rect(pix2.irect, (110, 150, 130))
    p.insert_image(fitz.Rect(90, 450, 520, 700), pixmap=pix2)


def _lab_page(doc, title: str, standard: str, rows) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 60), title, fontsize=15, fontname="hebo")
    p.insert_text((72, 82), standard, fontsize=9)
    p.insert_text((72, 110), "Boring No. B-1     Sample S-3     Depth 4.5 m",
                  fontsize=9)
    y = 150
    for label, value in rows:
        p.insert_text((90, y), label, fontsize=10)
        p.insert_text((330, y), value, fontsize=10)
        y += 22
    p.insert_text((72, 770), "Tested by Regional Materials Laboratory",
                  fontsize=8)


def _nested_cover(doc) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((90, 250), "GEOTECHNICAL STUDY", fontsize=20, fontname="hebo")
    p.insert_text((90, 285), "Former Owner Site Study", fontsize=18)
    p.insert_textbox(fitz.Rect(90, 420, 520, 600),
                     "Prepared for:\nThe Former Owner\n\n"
                     "Prepared by:\nOther Geotechnical Consultants\n"
                     "2 June 2019", fontsize=11)


def _nested_narrative(doc, gt: ReportGT) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 40), gt.nested_header, fontsize=8)
    p.insert_text((72, 100), "2.0 Findings", fontsize=14, fontname="hebo")
    p.insert_textbox(fitz.Rect(72, 120, 540, 700), NESTED_PROSE * 4,
                     fontsize=10)
    p.insert_text((72, 770), "Job 19-004 | Page 1 of 1", fontsize=8)


def _calc_page(doc, n: int) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 50), "LPILE 2022 - Analysis of Piles and Drilled "
                            "Shafts", fontsize=11, fontname="hebo")
    p.insert_text((72, 68), "Input echo and summary of results", fontsize=9)
    body = "\n".join(
        f"  {d:6.2f}   {d * 12.3:10.2f}   {d * 4.1:10.3f}   {d * 0.07:8.4f}"
        for d in [0.5 * i for i in range(1, 34)])
    p.insert_textbox(fitz.Rect(72, 96, 540, 740),
                     "   DEPTH      MOMENT     SHEAR      DEFLECTION\n" + body,
                     fontsize=8, fontname="cour")
    p.insert_text((72, 770), f"Page {n} of 2", fontsize=8)


def build_synthetic_report() -> ReportGT:
    """The report described in this module's docstring, with its answers."""
    import fitz
    gt = ReportGT(pdf=b"")
    doc = fitz.open()
    _cover(doc)
    _toc(doc)
    for n in (1, 2, 3):
        _narrative(doc, gt, n)
    _figure_page(doc)
    _divider(doc, "APPENDIX A - BORING LOGS AND TEST PIT LOGS",
             "Boring Logs B-1 (2 sheets)\nTest Pit Logs TP-1 (1 sheet)\n"
             "Test Pit Photographs (1 sheet)")
    _log_page(doc, "LOG OF BORING", "BORING NO. B-1", "1 of 2", LOG_FIELDS)
    _log_page(doc, "LOG OF BORING", "BORING NO. B-1", "2 of 2", LOG_FIELDS)
    _log_page(doc, "TEST PIT LOG", "TEST PIT NO. TP-1", "1 of 1", LOG_FIELDS)
    _photo_page(doc)
    _divider(doc, "APPENDIX B - LABORATORY TEST DATA",
             "Atterberg Limits (1 sheet)\nSieve Analysis (1 sheet)")
    _lab_page(doc, "ATTERBERG LIMITS", "ASTM D4318",
              [("Liquid Limit, LL", "38"), ("Plastic Limit, PL", "19"),
               ("Plasticity Index, PI", "19"),
               ("Natural moisture content", "24.1 %")])
    _lab_page(doc, "SIEVE ANALYSIS (GRADATION)", "ASTM D6913",
              [("Passing 4.75 mm", "98 %"), ("Passing 0.425 mm", "81 %"),
               ("Passing 0.075 mm", "54 %"), ("Coefficient of uniformity",
                                              "12.4")])
    _divider(doc, "APPENDIX C - GEOTECHNICAL REPORT BY OTHERS",
             "Former Owner Site Study, 2019, reproduced in full")
    _nested_cover(doc)
    _nested_narrative(doc, gt)
    _divider(doc, "APPENDIX A - BORING LOGS", "Boring Logs BH-1 (1 sheet)")
    _log_page(doc, "LOG OF BORING", "BORING NO. BH-1", "1 of 1", LOG_FIELDS)
    _divider(doc, "APPENDIX D - CALCULATIONS",
             "Lateral Pile Analysis (2 sheets)")
    for n in (1, 2):
        _calc_page(doc, n)
    gt.pdf = doc.tobytes()
    doc.close()
    return gt
