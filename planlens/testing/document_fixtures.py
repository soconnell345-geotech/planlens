"""A synthetic review document with known answers, for planlens.document.

One PDF, five pages, each exercising one thing a real submittal throws at a
reviewer:

- page 0 — a letter-size report page: a heading, a wrapped paragraph, and a
  review stamp whose wording lives only in its appearance (comment field
  empty, as Bluebeam writes them);
- page 1 — a 36x24 in drawing sheet stored portrait with ``/Rotate 90`` (so its
  numbers only come out right if the displayed frame is honoured), carrying
  text in two reading directions, linework, a reviewer's callout comment, a
  contractor's reply callout aimed into that comment, the cloud Bluebeam ties
  to the reply, an arrow, and AutoCAD-style hidden SHX text (one string also in
  the text layer, to be de-duplicated);
- page 2 — a ruled table;
- page 3 — blank;
- page 4 — a "scanned" page: one full-page image, no text layer.

Truth is stated in the DISPLAYED frame and derived by hand, not with PyMuPDF's
rotation matrix, so tests that use it are independent of the code under test.
For ``/Rotate 90`` on an unrotated page of height ``H``: a displayed point
``(xd, yd)`` is the unrotated point ``(yd, H - xd)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Tuple

LETTER = (612.0, 792.0)
#: Unrotated mediabox of the drawing sheet (portrait); displayed it is 2592x1728.
SHEET_UNROT = (1728.0, 2592.0)
SHEET_ROTATION = 90

Point = Tuple[float, float]


def _u(xd: float, yd: float) -> Point:
    """Displayed point on the /Rotate 90 sheet -> unrotated page point."""
    return (yd, SHEET_UNROT[1] - xd)


def _u_rect(x0: float, y0: float, x1: float, y1: float):
    import fitz
    (ax, ay), (bx, by) = _u(x0, y0), _u(x1, y1)
    return fitz.Rect(min(ax, bx), min(ay, by), max(ax, bx), max(ay, by))


@dataclass
class DocumentGT:
    """Ground truth for :func:`build_synthetic_review_document`."""
    pdf: bytes
    narrative_page: int = 0
    sheet_page: int = 1
    table_page: int = 2
    blank_page: int = 3
    scanned_page: int = 4
    heading: str = "GEOTECHNICAL ENGINEERING REPORT"
    stamp_author: str = "Reviewer A"
    stamp_wording: str = "APPROVED"
    phrase_across_lines: Tuple[str, str] = ("approximately 40-foot", "centers")
    # Sheet text: (text, displayed baseline origin, displayed rotation).
    sheet_text_upright: str = "GENERAL NOTE A"
    sheet_text_upright_origin: Point = (300.0, 400.0)
    sheet_text_vertical: str = "EL 1704 m"
    sheet_text_vertical_rotation: float = 270.0
    reviewer: str = "Reviewer A"
    reviewer_comment: str = "CONFIRM THE PILE EMBEDMENT SHOWN HERE."
    reviewer_target: Point = (1500.0, 900.0)
    reviewer_box: Tuple[float, float, float, float] = (1800.0, 1200.0, 2200.0, 1320.0)
    contractor: str = "Contractor B"
    reply_comment: str = "Embedment revised to 6 m per updated calcs."
    reply_box: Tuple[float, float, float, float] = (2250.0, 1350.0, 2550.0, 1450.0)
    reply_tip: Point = (1850.0, 1260.0)
    arrow_tail: Point = (2300.0, 1550.0)
    arrow_tip: Point = (2000.0, 600.0)
    hidden_cad_text: str = "SHX HIDDEN NOTE"
    hidden_cad_box: Tuple[float, float, float, float] = (100.0, 1500.0, 400.0, 1540.0)
    n_sheet_lines_drawn: int = 250
    table_rows: List[List[str]] = field(default_factory=lambda: [
        ["Boring", "Depth (ft)", "N-value"],
        ["B-1", "25", "12"],
        ["B-2", "30", "18"],
        ["B-3", "20", "9"],
        ["B-4", "35", "22"],
    ])
    toc: List[Tuple[int, str, int]] = field(default_factory=lambda: [
        (1, "Report", 0), (1, "Drawings", 1), (2, "Sheet S-1", 1),
        (1, "Boring summary", 2)])
    sheet_label: str = "S-1"
    extra: Dict[str, object] = field(default_factory=dict)


def build_synthetic_review_document() -> DocumentGT:
    import fitz

    gt = DocumentGT(pdf=b"")
    doc = fitz.open()

    # -- page 0: report text --------------------------------------------------
    p = doc.new_page(width=LETTER[0], height=LETTER[1])
    p.insert_text((72, 90), gt.heading, fontsize=18, fontname="hebo")
    body = (
        "Subsurface conditions were explored with twelve borings. Borings "
        "were drilled at " + gt.phrase_across_lines[0] + "\n"
        + gt.phrase_across_lines[1] + " across the building footprint and "
        "extended to depths of 20 to 35 feet below existing grade. "
        "Groundwater was encountered in four of the borings. "
        "Standard penetration tests were performed at 5-foot intervals. ") * 3
    p.insert_textbox(fitz.Rect(72, 120, 540, 700), body, fontsize=10,
                     fontname="helv")
    stamp = p.add_stamp_annot(fitz.Rect(400, 700, 560, 760),
                              stamp=fitz.STAMP_Approved)
    stamp.set_info(title=gt.stamp_author)
    stamp.update()
    # PyMuPDF fills /Contents with the stamp name and ignores content="";
    # Bluebeam leaves it absent, so remove it to match real review stamps.
    doc.xref_set_key(stamp.xref, "Contents", "null")

    # -- page 1: rotated drawing sheet ---------------------------------------
    s = doc.new_page(width=SHEET_UNROT[0], height=SHEET_UNROT[1])
    shape = s.new_shape()
    for i in range(gt.n_sheet_lines_drawn):
        xd = 200.0 + (i % 50) * 40.0
        yd = 700.0 + (i // 50) * 60.0
        shape.draw_line(_u(xd, yd), _u(xd + 30.0, yd))
        shape.finish(color=(0, 0, 0), width=0.5)
    # A small circle at the reviewer's target, so the spot has something on it.
    shape.draw_circle(_u(*gt.reviewer_target), 6)
    shape.finish(color=(0, 0, 0), width=1)
    shape.commit()
    # Upright as displayed: on a /Rotate 90 page that is unrotated angle 90.
    s.insert_text(_u(*gt.sheet_text_upright_origin), gt.sheet_text_upright,
                  fontsize=14, rotate=90)
    # Unrotated angle 0 displays reading top-to-bottom (270).
    s.insert_text(_u(900.0, 300.0), gt.sheet_text_vertical, fontsize=12,
                  rotate=0)

    rev = s.add_freetext_annot(
        _u_rect(*gt.reviewer_box), gt.reviewer_comment, fontsize=12,
        callout=(_u(*gt.reviewer_target), _u(1700.0, 1000.0),
                 _u(gt.reviewer_box[0], 1260.0)),
        line_end=fitz.PDF_ANNOT_LE_OPEN_ARROW)
    rev.set_info(title=gt.reviewer, subject="Callout",
                 creationDate="D:20260825160934-04'00'")
    rev.update()

    reply = s.add_freetext_annot(
        _u_rect(*gt.reply_box), gt.reply_comment, fontsize=12,
        callout=(_u(*gt.reply_tip), _u(2240.0, 1300.0),
                 _u(gt.reply_box[0], 1400.0)),
        line_end=fitz.PDF_ANNOT_LE_OPEN_ARROW)
    reply.set_info(title=gt.contractor, subject="Cloud+")
    reply.update()

    x0, y0, x1, y1 = gt.reviewer_box
    cloud = s.add_polygon_annot([_u(x0 - 10, y0 - 10), _u(x1 + 10, y0 - 10),
                                 _u(x1 + 10, y1 + 10), _u(x0 - 10, y1 + 10)])
    cloud.set_info(title=gt.contractor, subject="Cloud+")
    cloud.update()
    cloud.set_irt_xref(reply.xref)

    arrow = s.add_line_annot(_u(*gt.arrow_tail), _u(*gt.arrow_tip))
    arrow.set_line_ends(fitz.PDF_ANNOT_LE_NONE, fitz.PDF_ANNOT_LE_OPEN_ARROW)
    arrow.set_info(title=gt.contractor, subject="Arrow")
    arrow.update()

    hidden = s.add_rect_annot(_u_rect(*gt.hidden_cad_box))
    hidden.set_info(title="AutoCAD SHX Text", content=gt.hidden_cad_text)
    hidden.set_border(width=0)
    hidden.update(opacity=0)
    # The same string as a text-layer line, at the same spot: must be dropped.
    tb = fitz.Rect(*gt.sheet_text_upright_origin, 0, 0)
    dup = s.add_rect_annot(_u_rect(tb.x0 - 2, tb.y0 - 14, tb.x0 + 120,
                                   tb.y0 + 4))
    dup.set_info(title="AutoCAD SHX Text", content=gt.sheet_text_upright)
    dup.set_border(width=0)
    dup.update(opacity=0)
    s.set_rotation(SHEET_ROTATION)

    # -- page 2: ruled table ---------------------------------------------------
    t = doc.new_page(width=LETTER[0], height=LETTER[1])
    col_x = [72.0, 192.0, 312.0, 432.0]
    row_y = [100.0 + 24.0 * i for i in range(len(gt.table_rows) + 1)]
    for x in col_x:
        t.draw_line((x, row_y[0]), (x, row_y[-1]), width=0.8)
    for y in row_y:
        t.draw_line((col_x[0], y), (col_x[-1], y), width=0.8)
    for r, row in enumerate(gt.table_rows):
        for c, val in enumerate(row):
            t.insert_text((col_x[c] + 6, row_y[r] + 16), val, fontsize=10)

    # -- page 3: blank; page 4: scanned image ---------------------------------
    doc.new_page(width=LETTER[0], height=LETTER[1])
    sc = doc.new_page(width=LETTER[0], height=LETTER[1])
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 85, 110), False)
    pix.set_rect(pix.irect, (235, 235, 230))
    sc.insert_image(sc.rect, pixmap=pix)

    doc.set_toc([[lvl, title, page + 1] for lvl, title, page in gt.toc])
    doc.set_page_labels([
        {"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1},
        {"startpage": 1, "prefix": "S-", "style": "D", "firstpagenum": 1},
        {"startpage": 2, "prefix": "", "style": "D", "firstpagenum": 3},
    ])
    gt.pdf = doc.tobytes()
    doc.close()
    return gt
