"""A small made-up drawing set with known sheets, views and callouts.

Five 34 x 22 in sheets, drawn the way a set is plotted: a cover with a
printed sheet index, two floor plans with reference bubbles, a details
sheet and a schedule. Every sheet has a title block with its number, its
title, a sheet count ("SHEET 3 OF 5") and a revision block. The text is
written into each page in a shuffled order, as CAD writes it: nothing can be
read off the order of the text.

Four title blocks (``style``), because firms differ:

* ``strip`` — down the right edge; "SHEET TITLE" and "SHEET NO." are labels
  in their own cells with the value in the cell under them; the revisions
  are a table (NO. / DATE / DESCRIPTION), each cell its own text;
* ``bottom`` — across the bottom; "DRAWING TITLE:" and "DWG NO." are labels
  with the value in the cell to their right; each revision is one line;
* ``inline`` — down the right edge; the number is written on the label's
  line ("SHEET NO. S-201A"), the title has no label, and the count is bare
  ("3 of 5");
* ``turned`` — down the right edge with the title, the number and the count
  lettered to read up the sheet, each value on the line after its label.

Two more, for one sheet on its own: ``bare`` (the number alone in the
corner, large, no label) and ``count_only`` (no sheet number at all, only
"SHEET 2 OF 5").

Five ways of numbering (``numbers``): ``dash`` (A-101), ``dot`` (A1.01),
``civil`` (C1.1), ``decimal`` (10.31, 10.31A) and ``suffix`` (S-201, S-201A).

The callouts, the same in every set (the truth is in :class:`SheetSetGT`):

* plan one calls details 1 and 3 (there) and detail 5 (the details sheet
  stops at 4);
* plan two calls detail 2 (there), view 1 of plan one (there), and view 1 of
  a sheet the set does not have;
* plan two's notes say "SEE 4/<details> FOR HEAD DETAIL" and the schedule's
  remarks "SEE 2/<details>": callouts written on one line, both there;
* the details sheet calls view 1 of the schedule, which has no view marks.

Also drawn, to be left alone: grid bubbles (a letter or a number alone in a
circle), room tags (a name over a room number), door marks, and on the cover
a list of the set's sheet numbers under a discipline letter.

Every name, number and date here is made up.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

SHEET = (34.0 * 72, 22.0 * 72)
MARGIN = 36.0
STRIP = 288.0
BOTTOM = 110.0

FIRM = "NORTHGATE DESIGN GROUP"
PROJECT = "HARBOR STREET CLINIC"

#: Sheet numbers by role (cover, plan one, plan two, details, schedule) and a
#: number of the same series that the set does not have.
SCHEMES: Dict[str, Tuple[Tuple[str, ...], str]] = {
    "dash": (("G-001", "A-101", "A-102", "A-501", "A-601"), "A-301"),
    "dot": (("G0.01", "A1.01", "A1.02", "A5.01", "A6.01"), "A3.01"),
    "civil": (("C0.1", "C1.1", "C1.2", "C5.1", "C6.1"), "C3.1"),
    "decimal": (("00.01", "10.31", "10.31A", "50.01", "60.01"), "30.01"),
    "suffix": (("S-001", "S-201", "S-201A", "S-501", "S-601"), "S-301"),
}
STYLES = ("strip", "bottom", "inline", "turned", "bare", "count_only")

TITLES = ("COVER SHEET", "FIRST FLOOR PLAN", "SECOND FLOOR PLAN",
          "DOOR AND WINDOW DETAILS", "DOOR SCHEDULE")
#: The title as the strip's narrow cell wraps it.
WRAPPED = {"DOOR AND WINDOW DETAILS": ("DOOR AND WINDOW", "DETAILS")}
#: The views drawn on each sheet, by role, with the title beside each mark
#: (not the sheet's title: a plan's view is titled by its level).
VIEWS: Tuple[Tuple[Tuple[str, str], ...], ...] = (
    (),
    (("1", "LEVEL 1 PLAN"),),
    (("1", "LEVEL 2 PLAN"),),
    (("1", "SILL DETAIL"), ("2", "HEAD DETAIL"), ("3", "JAMB DETAIL"),
     ("4", "THRESHOLD DETAIL")),
    (),
)
#: Revisions by role: (mark, date, description), oldest first.
_REVISIONS = {
    "numbers": (
        (("0", "2031-01-12", "ISSUED FOR PERMIT"),),
        (("0", "2031-01-12", "ISSUED FOR PERMIT"),),
        (("0", "2031-01-12", "ISSUED FOR PERMIT"),
         ("1", "2031-03-02", "REVISED CORRIDOR")),
        (("0", "2031-01-12", "ISSUED FOR PERMIT"),),
        (("0", "2031-01-12", "ISSUED FOR PERMIT"),
         ("1", "2031-03-02", "REVISED HARDWARE"),
         ("2", "2031-04-20", "REVISED FRAME TYPES")),
    ),
    "letters": (
        (("A", "2031-01-12", "ISSUED FOR REVIEW"),),
        (("A", "2031-01-12", "ISSUED FOR REVIEW"),
         ("B", "2031-02-03", "ISSUED FOR BID")),
        (("A", "2031-01-12", "ISSUED FOR REVIEW"),),
        (("A", "2031-01-12", "ISSUED FOR REVIEW"),
         ("B", "2031-02-03", "ISSUED FOR BID"),
         ("C", "2031-03-17", "ADDENDUM 2")),
        (("A", "2031-01-12", "ISSUED FOR REVIEW"),),
    ),
}


@dataclass
class SheetSetGT:
    """The set and what is true of it. Lists are in page order."""
    pdf: bytes
    style: str
    #: The sheet number each page prints (``None``: the page prints none).
    numbers: List[Optional[str]]
    titles: List[str]
    #: The latest revision each page's revision block shows.
    revisions: List[str]
    #: The sheet count each page prints, as "3 of 5".
    counts: List[str]
    #: Sheet number -> the view numbers drawn on it.
    views: Dict[str, List[str]] = field(default_factory=dict)
    #: (sheet number, view) -> the title beside the view's mark.
    view_titles: Dict[Tuple[str, str], str] = field(default_factory=dict)
    #: Every callout: ``{"page", "view", "target", "form", "status"}``,
    #: status as :mod:`planlens.document.crossrefs` words it. Stated for
    #: the whole set with every page's text in place.
    callouts: List[Dict[str, Any]] = field(default_factory=list)
    #: The sheet number a callout names that the set does not have.
    missing_sheet: str = ""
    #: Pages that are one image with no text layer.
    scanned: Tuple[int, ...] = ()

    def with_status(self, status: str) -> List[str]:
        """The callouts of one status, as "view/sheet", sorted."""
        return sorted(f"{c['view']}/{c['target']}" for c in self.callouts
                      if c["status"] == status)


class _Page:
    """One sheet being drawn: text is held back and written shuffled."""

    def __init__(self, page):
        self.page = page
        self.texts: List[Tuple[float, float, str, float, bool, int]] = []

    def text(self, x: float, y: float, text: str, size: float = 8.0,
             bold: bool = False, rotate: int = 0) -> None:
        """Text starting at ``(x, y)``; ``rotate=90`` reads up the sheet."""
        self.texts.append((x, y, text, size, bold, rotate))

    def centred(self, cx: float, y: float, text: str, size: float,
                bold: bool = False) -> None:
        import pymupdf
        width = pymupdf.get_text_length(
            text, fontname="hebo" if bold else "helv", fontsize=size)
        self.text(cx - width / 2.0, y, text, size, bold)

    def line(self, a, b, width: float = 0.7) -> None:
        self.page.draw_line(a, b, width=width)

    def rect(self, box, width: float = 0.7) -> None:
        import pymupdf
        self.page.draw_rect(pymupdf.Rect(*box), width=width)

    def circle(self, centre, radius: float) -> None:
        self.page.draw_circle(centre, radius, width=0.7)

    def bubble(self, cx: float, cy: float, view: str, sheet: str) -> None:
        """A reference bubble: the view number over the sheet number."""
        self.circle((cx, cy), 15.0)
        self.line((cx - 15.0, cy), (cx + 15.0, cy))
        self.centred(cx, cy - 3.0, view, 9.0, bold=True)
        self.centred(cx, cy + 9.0, sheet, 6.0)

    def view_title(self, cx: float, cy: float, view: str, sheet: str,
                   title: str, scale: str) -> None:
        self.bubble(cx, cy, view, sheet)
        self.line((cx + 15.0, cy), (cx + 260.0, cy))
        self.text(cx + 22.0, cy - 4.0, title, 12.0, bold=True)
        self.text(cx + 22.0, cy + 10.0, f"SCALE {scale}", 7.0)

    def flush(self, seed: int) -> None:
        order = list(self.texts)
        random.Random(seed).shuffle(order)
        for x, y, text, size, bold, rotate in order:
            self.page.insert_text((x, y), text, fontsize=size,
                                  fontname="hebo" if bold else "helv",
                                  rotate=rotate)


def _revision_rows(style: str, role: int):
    return _REVISIONS["letters" if style == "bottom" else "numbers"][role]


def _title_block(p: _Page, style: str, number: str, title: str, role: int,
                 nth: int, of: int) -> None:
    w, h = SHEET
    p.rect((MARGIN, MARGIN, w - MARGIN, h - MARGIN), width=1.5)
    rows = _revision_rows(style, role)
    if style == "bottom":
        y0 = h - MARGIN - BOTTOM
        p.line((MARGIN, y0), (w - MARGIN, y0), width=1.2)
        for x in (460.0, 1060.0, 1620.0, 2110.0):
            p.line((x, y0), (x, h - MARGIN))
        p.text(60, y0 + 34, FIRM, 10, bold=True)
        p.text(60, y0 + 50, "14 QUARRY LANE, EASTMERE", 7)
        p.text(480, y0 + 18, "PROJECT", 6)
        p.text(480, y0 + 44, PROJECT, 14)
        p.text(480, y0 + 62, "PROJECT NO. 2417", 7)
        # label, and the value in the cell to its right
        p.text(1080, y0 + 40, "DRAWING TITLE:", 7)
        for i, part in enumerate(WRAPPED.get(title, (title,))):
            p.text(1160, y0 + 44 + 14 * i, part, 11, bold=True)
        p.text(1640, y0 + 14, "REVISIONS", 7)
        for i, (mark, date, what) in enumerate(rows):
            p.text(1640, y0 + 28 + 11 * i, f"{mark} {date} {what}", 7)
        p.text(2130, y0 + 54, "DWG NO.", 7)
        p.text(2190, y0 + 66, number, 26, bold=True)
        p.text(2190, y0 + 92, f"SHEET {nth} OF {of}", 7)
        return
    x0 = w - MARGIN - STRIP
    p.line((x0, MARGIN), (x0, h - MARGIN), width=1.2)
    for y in (200.0, 680.0) + (() if style == "turned"
                               else (1160.0, 1280.0)):
        p.line((x0, y), (w - MARGIN, y))
    p.text(x0 + 12, 80, FIRM, 12, bold=True)
    p.text(x0 + 12, 96, "14 QUARRY LANE, EASTMERE", 7)
    p.text(x0 + 12, 150, PROJECT, 16)
    p.text(x0 + 12, 170, "PROJECT NO. 2417", 8)
    if style in ("strip", "turned"):
        p.text(x0 + 12, 700, "REVISIONS", 7)
        for x, head in ((12, "NO."), (44, "DATE"), (110, "DESCRIPTION")):
            p.text(x0 + x, 714, head, 6)
        for i, (mark, date, what) in enumerate(rows):
            y = 728 + 12 * i
            p.text(x0 + 14, y, mark, 7)
            p.text(x0 + 44, y, date, 7)
            p.text(x0 + 110, y, what, 7)
    if style == "turned":
        # read up the sheet: each label, and its value on the next line
        for x in (130.0, 185.0):
            p.line((x0 + x, 900), (x0 + x, h - MARGIN))
        p.text(x0 + 150, 1500, "SHEET TITLE", 6, rotate=90)
        p.text(x0 + 168, 1500, title, 12, bold=True, rotate=90)
        p.text(x0 + 205, 1500, "SHEET NO.", 6, rotate=90)
        p.text(x0 + 250, 1500, number, 40, bold=True, rotate=90)
        p.text(x0 + 275, 1500, f"SHEET {nth} OF {of}", 7, rotate=90)
        return
    if style == "strip":
        p.text(x0 + 12, 1176, "SHEET TITLE", 6)
        for i, part in enumerate(WRAPPED.get(title, (title,))):
            p.text(x0 + 12, 1198 + 15 * i, part, 12, bold=True)
        p.text(x0 + 12, 1296, "SHEET NO.", 6)
        p.text(x0 + 12, 1350, number, 40, bold=True)
        p.text(x0 + 12, 1420, f"SHEET {nth} OF {of}", 7)
        return
    # inline, bare, count_only: no labels for the title
    p.text(x0 + 12, 700, "REV", 7)
    for i, (mark, date, what) in enumerate(rows):
        y = 714 + 12 * i
        p.text(x0 + 14, y, mark, 7)
        p.text(x0 + 44, y, f"{date} {what}", 7)
    for i, part in enumerate(WRAPPED.get(title, (title,))):
        p.text(x0 + 12, 1198 + 14 * i, part, 11, bold=True)
    if style == "inline":
        p.text(x0 + 12, 1340, f"SHEET NO. {number}", 10)
        p.text(x0 + 12, 1420, f"{nth} of {of}", 7)
    elif style == "bare":
        p.text(x0 + 12, 1350, number, 36, bold=True)
        p.text(x0 + 12, 1420, f"SHEET {nth} OF {of}", 7)
    else:                                              # count_only
        p.text(x0 + 12, 1350, f"SHEET {nth} OF {of}", 14, bold=True)


def _cover(p: _Page, numbers: Sequence[str]) -> None:
    p.text(300, 300, PROJECT, 30, bold=True)
    p.text(300, 340, "CONSTRUCTION DOCUMENTS", 14)
    p.text(300, 470, "SHEET INDEX", 11, bold=True)
    y = 500.0
    last = None
    for number, title in zip(numbers, TITLES):
        letter = number[0] if number[0].isalpha() else None
        if letter and letter != last:
            p.text(300, y, letter, 9)      # the discipline, over its sheets
            y += 14.0
            last = letter
        p.text(300, y, number, 9)
        p.text(420, y, title, 9)
        y += 14.0


def _plan(p: _Page, role: int, numbers: Sequence[str], missing: str) -> None:
    own, details = numbers[role], numbers[3]
    p.rect((300, 200, 1500, 1100), width=1.4)
    for x in (700.0, 1100.0):
        p.line((x, 200), (x, 1100))
    p.line((300, 650), (1500, 650))
    # grid bubbles: one letter or one number alone in a circle
    for x, letter in ((300.0, "A"), (700.0, "B"), (1100.0, "C"),
                      (1500.0, "D")):
        p.circle((x, 150), 14.0)
        p.centred(x, 153.5, letter, 10)
    for y, digit in ((200.0, "1"), (650.0, "2"), (1100.0, "3")):
        p.circle((250, y), 14.0)
        p.centred(250, y + 3.5, digit, 10)
    # room tags: a name over a room number; door marks
    base = 100 * role
    for (x, y), name, n in (((450.0, 400.0), "OFFICE", 1),
                            ((850.0, 400.0), "EXAM", 2),
                            ((1250.0, 850.0), "STAFF", 3)):
        p.centred(x, y, name, 8)
        p.centred(x, y + 11.0, str(base + n), 8)
        p.text(x + 60, y + 60, f"{base + n}A", 6)
    p.text(1560, 300, "ALL DIMENSIONS IN MILLIMETRES", 7)
    p.text(1560, 314, "VERIFY ON SITE BEFORE FABRICATION", 7)
    view, title = VIEWS[role][0]
    p.view_title(330, 1300, view, own, title, "1:100")
    if role == 1:
        p.bubble(900, 300, "1", details)
        p.bubble(1300, 520, "3", details)
        p.bubble(1540, 760, "5", details)          # the sheet stops at 4
    else:
        p.bubble(900, 300, "2", details)
        p.bubble(1300, 520, "1", missing)          # no such sheet
        p.bubble(1800, 900, "1", numbers[1])
        p.text(1560, 360, f"SEE 4/{details} FOR HEAD DETAIL", 7)


def _details(p: _Page, numbers: Sequence[str]) -> None:
    own = numbers[3]
    spots = ((220.0, 620.0), (820.0, 620.0), (1420.0, 620.0),
             (220.0, 1300.0))
    for (x, y), (view, title) in zip(spots, VIEWS[3]):
        p.rect((x - 20, y - 380, x + 440, y - 40))
        p.line((x + 40, y - 300), (x + 380, y - 120))
        p.text(x + 60, y - 100, "SEALANT AND BACKER ROD", 6)
        p.view_title(x, y, view, own, title, "1:5")
    p.bubble(1040, 1000, "1", numbers[4])       # the schedule: no view marks


def _schedule(p: _Page, numbers: Sequence[str]) -> None:
    p.text(150, 150, "DOOR SCHEDULE", 14, bold=True)
    columns = ((150.0, "MARK"), (300.0, "WIDTH"), (450.0, "HEIGHT"),
               (600.0, "TYPE"), (750.0, "REMARKS"))
    for x, head in columns:
        p.text(x, 200, head, 8, bold=True)
    remarks = ("", f"SEE 2/{numbers[3]}", "FIRE RATED 45 MIN", "",
               "HOLD OPEN", "")
    for i, remark in enumerate(remarks):
        y = 220.0 + 16 * i
        p.line((140, y - 11), (1100, y - 11), width=0.4)
        for (x, _), value in zip(columns, (f"{101 + i}A", "900", "2100",
                                           f"F-{1 + i % 2}", remark)):
            if value:
                p.text(x, y, value, 8)
    p.rect((140, 188, 1100, 220 + 16 * len(remarks)))


def _as_image(pdf: bytes, pages: Sequence[int]) -> bytes:
    """``pdf`` with ``pages`` each turned into one grey image, no text."""
    import pymupdf
    src = pymupdf.open(stream=pdf, filetype="pdf")
    out = pymupdf.open()
    try:
        for i, page in enumerate(src):
            if i not in pages:
                out.insert_pdf(src, from_page=i, to_page=i)
                continue
            pix = page.get_pixmap(dpi=50, colorspace=pymupdf.csGRAY)
            new = out.new_page(width=page.rect.width,
                               height=page.rect.height)
            new.insert_image(new.rect,
                             stream=pix.tobytes("jpeg", jpg_quality=60))
        return out.tobytes(garbage=3, deflate=True, no_new_id=True)
    finally:
        out.close()
        src.close()


def build_sheet_set(style: str = "strip", numbers: str = "dash",
                    scanned: Sequence[int] = (), only: Optional[int] = None,
                    seed: int = 7) -> SheetSetGT:
    """The set as a PDF with its truth.

    ``style`` is the title block (:data:`STYLES`), ``numbers`` the way the
    sheets are numbered (:data:`SCHEMES`). ``scanned`` pages are turned
    into images with no text layer. ``only`` builds one sheet on its own
    (its role: 0 cover, 1 and 2 the plans, 3 details, 4 schedule), with no
    callout truth. ``seed`` orders the text within each page."""
    import pymupdf
    if style not in STYLES:
        raise ValueError(f"style must be one of {STYLES}")
    names, missing = SCHEMES[numbers]
    roles = list(range(5)) if only is None else [int(only)]
    doc = pymupdf.open()
    try:
        for nth, role in enumerate(roles, start=1):
            p = _Page(doc.new_page(width=SHEET[0], height=SHEET[1]))
            of = 5
            _title_block(p, style, names[role], TITLES[role], role,
                         nth if only is None else role + 1, of)
            if role == 0:
                _cover(p, names)
            elif role in (1, 2):
                _plan(p, role, names, missing)
            elif role == 3:
                _details(p, names)
            else:
                _schedule(p, names)
            p.flush(seed * 101 + role)
        doc.set_metadata({"title": f"{PROJECT} drawings (made up)"})
        pdf = doc.tobytes(garbage=3, deflate=True, no_new_id=True)
    finally:
        doc.close()
    scanned = tuple(sorted(int(i) for i in scanned))
    if scanned:
        pdf = _as_image(pdf, scanned)
    revs = [_revision_rows(style, role)[-1][0] for role in roles]
    gt = SheetSetGT(
        pdf=pdf, style=style,
        numbers=[None if style == "count_only" else names[r] for r in roles],
        titles=[TITLES[r] for r in roles], revisions=revs,
        counts=[f"{(i + 1) if only is None else r + 1} of 5"
                for i, r in enumerate(roles)],
        missing_sheet=missing, scanned=scanned)
    for role in roles:
        if VIEWS[role]:
            gt.views[names[role]] = [v for v, _ in VIEWS[role]]
            for view, title in VIEWS[role]:
                gt.view_titles[(names[role], view)] = title
    if only is None:
        d, s = names[3], names[4]
        gt.callouts = [
            {"page": 1, "view": "1", "target": d, "form": "bubble",
             "status": "resolved"},
            {"page": 1, "view": "3", "target": d, "form": "bubble",
             "status": "resolved"},
            {"page": 1, "view": "5", "target": d, "form": "bubble",
             "status": "unresolved"},
            {"page": 2, "view": "2", "target": d, "form": "bubble",
             "status": "resolved"},
            {"page": 2, "view": "1", "target": missing, "form": "bubble",
             "status": "unresolved"},
            {"page": 2, "view": "1", "target": names[1], "form": "bubble",
             "status": "resolved"},
            {"page": 2, "view": "4", "target": d, "form": "text",
             "status": "resolved"},
            {"page": 3, "view": "1", "target": s, "form": "bubble",
             "status": "cannot_tell"},
            {"page": 4, "view": "2", "target": d, "form": "text",
             "status": "resolved"},
        ]
    return gt


__all__ = ["SheetSetGT", "build_sheet_set", "SCHEMES", "STYLES", "TITLES",
           "VIEWS", "FIRM", "PROJECT"]
