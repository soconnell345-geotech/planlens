"""A synthetic stapled submittal with known structure, for the page map.

Nine letter pages and two D-size sheets, built the way real submittals arrive:

- page 0 — a cover with the project name (a divider);
- pages 1-3 — a report with a running header, a running footer carrying
  "Page N" (N = 1..3), dense prose;
- page 4 — a divider "APPENDIX A - BORING LOGS";
- pages 5-6 — ruled boring-log forms, sparse cells, footer "Page N of 2";
- page 7 — an exact repeat of page 2 (the duplicate);
- pages 8-9 — two 34x22 in drawing sheets, footer "SHEET n OF 2", a scale
  note "SCALE: 1:100";
- page 10 — a divider "ATTACHMENT B - PHOTOGRAPHS".

Truth: the expected segments, printed page numbers, sheet references and the
duplicate, all stated by hand.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

LETTER = (612.0, 792.0)
SHEET = (34.0 * 72, 22.0 * 72)

PROSE = ("The subsurface exploration program consisted of borings advanced "
         "with hollow-stem augers to depths of 20 to 35 feet below grade. "
         "Standard penetration tests were performed at 5-foot intervals and "
         "samples were classified in the field. Groundwater was observed in "
         "three borings at the time of drilling. Laboratory testing included "
         "moisture content, Atterberg limits and grain-size distribution. ")


@dataclass
class SubmittalGT:
    pdf: bytes
    n_pages: int = 11
    cover_page: int = 0
    report_pages: Tuple[int, ...] = (1, 2, 3)
    report_header: str = "Acme Geotechnical | Site Investigation Report"
    appendix_divider_page: int = 4
    appendix_title: str = "APPENDIX A - BORING LOGS"
    form_pages: Tuple[int, ...] = (5, 6)
    duplicate_page: int = 7
    duplicate_of: int = 2
    sheet_pages: Tuple[int, ...] = (8, 9)
    sheet_scale: str = "1:100"
    attachment_divider_page: int = 10
    attachment_title: str = "ATTACHMENT B - PHOTOGRAPHS"
    #: (first page, last page, title fragment) per expected segment.
    expected_segments: List[Tuple[int, int, str]] = field(default_factory=lambda: [
        (0, 0, "Project Alpha"),
        (1, 3, "Acme Geotechnical"),
        (4, 4, "APPENDIX A"),
        (5, 6, "Log of Boring"),
        (7, 7, "Acme Geotechnical"),
        (8, 9, "Drawing Set"),
        (10, 10, "ATTACHMENT B"),
    ])
    extra: Dict[str, object] = field(default_factory=dict)


def _report_page(doc, gt: SubmittalGT, n: int) -> None:
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 40), gt.report_header, fontsize=8)
    p.insert_text((72, 90), f"{n}. Subsurface Conditions", fontsize=14,
                  fontname="hebo")
    import fitz
    p.insert_textbox(fitz.Rect(72, 110, 540, 720), PROSE * 4, fontsize=10)
    p.insert_text((72, 770), f"Project 1234 | Page {n} | June 2026",
                  fontsize=8)


def _form_page(doc, gt: SubmittalGT, n: int) -> None:
    import fitz
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 60), f"Log of Boring B-{n}", fontsize=12,
                  fontname="hebo")
    xs = [72 + 33 * i for i in range(15)]
    ys = [90 + 28 * i for i in range(22)]
    for x in xs:
        p.draw_line((x, ys[0]), (x, ys[-1]), width=0.6)
    for y in ys:
        p.draw_line((xs[0], y), (xs[-1], y), width=0.6)
    for r, txt in enumerate(["Depth", "SPT", "Description"]):
        p.insert_text((xs[r] + 3, ys[0] + 18), txt, fontsize=7)
    for r in range(1, 21, 4):
        p.insert_text((xs[0] + 3, ys[r] + 18), str(5 * r), fontsize=7)
        p.insert_text((xs[1] + 3, ys[r] + 18), str(10 + r), fontsize=7)
    p.insert_text((72, 770), f"Page {n} of 2 | Checked: AB 2026-06-30",
                  fontsize=8)


def _divider(doc, title: str, subtitle: Optional[str] = None) -> None:
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 300), title, fontsize=22, fontname="hebo")
    if subtitle:
        p.insert_text((72, 340), subtitle, fontsize=12)


def _sheet(doc, gt: SubmittalGT, n: int) -> None:
    import fitz
    p = doc.new_page(width=SHEET[0], height=SHEET[1])
    shape = p.new_shape()
    for i in range(300):
        x = 100 + (i % 30) * 70
        y = 100 + (i // 30) * 120
        shape.draw_line((x, y), (x + 40, y + 20))
        shape.finish(color=(0, 0, 0), width=0.5)
    shape.draw_rect(fitz.Rect(SHEET[0] - 700, SHEET[1] - 200,
                              SHEET[0] - 50, SHEET[1] - 50))
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    p.insert_text((SHEET[0] - 680, SHEET[1] - 170), "Drawing Set - Project Alpha",
                  fontsize=14)
    p.insert_text((SHEET[0] - 680, SHEET[1] - 130), f"SCALE: {gt.sheet_scale}",
                  fontsize=10)
    p.insert_text((SHEET[0] - 680, SHEET[1] - 70), f"SHEET {n} OF 2",
                  fontsize=12)


def build_synthetic_submittal() -> SubmittalGT:
    import fitz
    gt = SubmittalGT(pdf=b"")
    doc = fitz.open()
    _divider(doc, "Project Alpha", "Cover - Geotechnical Submittal")
    for n in (1, 2, 3):
        _report_page(doc, gt, n)
    _divider(doc, gt.appendix_title)
    for n in (1, 2):
        _form_page(doc, gt, n)
    _report_page(doc, gt, 2)                    # the duplicate of page 2
    for n in (1, 2):
        _sheet(doc, gt, n)
    _divider(doc, gt.attachment_title)
    gt.pdf = doc.tobytes()
    doc.close()
    return gt
