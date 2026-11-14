"""A drawing set's sheet index, read from its title blocks.

A reviewer names a sheet by its number ("the callouts on S-201"); a PDF knows
only page indexes. Most sets carry no page labels, so the number has to come
off the sheet: the title block prints it, with the sheet's title and its
revisions. This module reads those for every sheet of a document and returns
one row per sheet: the page, the sheet number, the title, the latest revision,
and how the number was found.

HOW THE NUMBER IS FOUND. Title blocks differ from firm to firm, so nothing
here knows a layout. In this order:

* **The slot.** Across a set, the sheet number sits at the same place on
  every sheet and is different on each. So: collect every piece of text
  shaped like a sheet number in the right and bottom bands of each sheet
  (a whole line, or straight after a number label; not a number inside a
  note's sentence), group the pieces that sit at the same place from sheet
  to sheet, and take the group that covers most sheets with a different
  value on each. What the set repeats (the project, the firm, a label) is
  the same on every sheet and never wins; a callout's sheet number is at a
  different place on each sheet and never forms a group. Sheets the slot
  leaves (a consultant's title block bound into the set) are searched the
  same way for a slot of their own, whose numbers must have the shape of
  the first slot's; its rows say so.
* **The label.** For a sheet no slot covers, and for a document of one or
  two sheets: a number after a label ("SHEET NO.", "DWG NO.", "SHEET #",
  "DRAWING NUMBER"). A label counts only as a title block prints one: a
  short line of its own or the start of one, with the number straight
  after it, or alone in the cell beside or under it. "REFER TO DWG NO.
  S-201" in a note is not a label.
* **The size.** Failing a label, a number standing alone in the title
  block's corner in lettering at least twice the band's usual lettering.

Nothing else gives a sheet its number: not its view titles, not the
callouts drawn on it.

A number after PAGE, FIG., TABLE, SECTION or APPENDIX is never a sheet
number. Where the title block gives no number (a scan, a number drawn as
lines) and the PDF itself labels the page with one ("A-101 FIRST FLOOR
PLAN"), the sheet takes that, and the row says it came from the page label.

A sheet count ("SHEET 3 OF 24", "3 of 24") is never a sheet number: it is
kept apart as ``count``. A sheet with no text layer is listed with the reason
and no number: it is read by looking, and nothing here guesses. A number read
on two pages is one note; :meth:`SheetIndex.duplicates` lists them.

THE TITLE is the text of the title block that changes from sheet to sheet
(the project name does not), under a title label where there is one, else
the largest such lettering; wrapped lines are joined. THE REVISION is the
highest entry under a "REVISIONS" heading.

Positions are the displayed frame (top-left origin, y down, PDF points), as
everywhere in :mod:`planlens.document`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Any, Dict, List, Optional, Sequence, Tuple

BBox = Tuple[float, float, float, float]

#: A sheet number with a discipline letter (A-501, S-201A, A5.01, A1-01,
#: C1.1, FP-101, G001) or without one but with a decimal part (10.31A,
#: 20.01).
_LETTERED = r"[A-Z]{1,3}[-.]?\d{1,4}(?:[-.]\d{1,3})?[A-Z]?"
_DECIMAL = r"\d{1,3}\.\d{1,3}[A-Z]?"
SHEET_NUMBER = re.compile(
    rf"(?<![\w./-])(?:{_LETTERED}|{_DECIMAL})(?![\w/-]|\.\d)")
#: The same, as the whole of a piece of text.
WHOLE_SHEET_NUMBER = re.compile(rf"^(?:{_LETTERED}|{_DECIMAL})$")

#: "3 of 24", "SHEET 3 OF 24": the sheet's place in the set, not its number.
SHEET_COUNT = re.compile(r"\b(\d{1,3})\s+OF\s+(\d{1,3})\b", re.I)

#: A label a sheet number is printed beside or under.
NUMBER_LABEL = re.compile(
    r"\b(?:SHEET|SHT|DWG|DRAWING)\.?\s*(?:(?:NO|NUM|NUMBER)\b|#)\.?:?|"
    r"^\s*(?:SHEET|SHT|DWG|DRAWING)\.?\s*:?\s*$", re.I)
#: A word that makes the number after it something else: "Page 1.1",
#: "Figure A-1", "Table 3.2", "Section 4.1", "Appendix B-2".
NOT_A_SHEET_BEFORE = re.compile(
    r"\b(?:PAGE|PG|FIG|FIGURE|TABLE|SECTION|APPENDIX)\b\.?\s*"
    r"(?:(?:NO\b\.?|#)\s*)?$", re.I)
#: A label a sheet's title is printed beside or under.
TITLE_LABEL = re.compile(
    r"^\s*(?:(?:SHEET|SHT|DWG|DRAWING)\.?\s+)?TITLE\s*:?\s*$", re.I)
#: The heading of a revision block.
REVISION_HEADING = re.compile(r"^\s*REV(?:ISION)?S?\b\.?", re.I)
#: A revision's mark in its own cell or at the head of its row.
_REV_MARK = re.compile(r"^\(?([A-Z]|\d{1,2})\)?$")
_REV_ROW = re.compile(r"^\(?([A-Z]|\d{1,2})\)?\s+(?=\d{1,4}[-/.]\d{1,2})")
_COLUMN_HEADS = {"NO", "NO.", "REV", "REV.", "DATE", "DESCRIPTION", "BY",
                 "MARK", "#", "CHK", "APPD", "APP'D"}

#: The right band and the bottom band a title block sits in, as fractions of
#: the sheet (standard drafting: along the right edge or across the bottom).
BAND_RIGHT = 0.68
BAND_BOTTOM = 0.76
#: A page at least this large (11 x 17 in) is a sheet whatever its kind.
SHEET_MIN_AREA = 792.0 * 1224.0 * 0.95
#: Two pieces are at the same place when their centres are this close (as
#: fractions of the sheet): along the line they are written on, and across.
SLOT_ALONG = 0.03
SLOT_ACROSS = 0.012
#: A slot must cover this share of the sheets with this share of its values
#: distinct, on at least this many sheets.
SLOT_MIN_COVER = 0.5
SLOT_MIN_DISTINCT = 0.8
SLOT_MIN_SHEETS = 3
#: Text the set prints on at least this share of its sheets is the set's
#: (the project, the firm, a label), not a sheet's.
STATIC_SHARE = 0.6
#: With no slot and no label, a number is taken only from the title block's
#: corner (right of / below these fractions of the sheet) and only when its
#: lettering is this many times the title-block bands' usual lettering.
CORNER_X = 0.68
CORNER_Y = 0.60
STANDS_OUT = 2.0

#: How a row's number was found (:attr:`SheetRow.how`).
HOW_SLOT = "title-block slot"
HOW_LABEL = "label"
HOW_SIZE = "largest lettering in the title-block corner"
HOW_PAGE_LABEL = "PDF page label"
#: The slot of a second title-block layout in the set (a consultant's).
HOW_FURTHER_SLOT = "slot of a second title-block layout"
#: The reason a readable sheet has no number.
NO_NUMBER = "no sheet number found in the title block's text"


@dataclass
class SheetRow:
    """One sheet of the index. ``number`` is ``None`` when the sheet could
    not be indexed; ``reason`` then says why."""
    page: int
    number: Optional[str] = None
    title: Optional[str] = None
    revision: Optional[str] = None
    revision_note: Optional[str] = None
    count: Optional[str] = None
    how: Optional[str] = None
    confidence: float = 0.0
    bbox: Optional[BBox] = None
    reason: Optional[str] = None
    #: ``False`` for a sheet with no usable text layer: nothing on it was
    #: read, whatever its page label says.
    readable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {"page": self.page}
        for key in ("number", "title", "revision", "revision_note", "count",
                    "how", "reason"):
            value = getattr(self, key)
            if value:
                out[key] = value
        if self.number:
            out["confidence"] = round(self.confidence, 2)
        if self.bbox is not None:
            out["bbox"] = [round(v, 1) for v in self.bbox]
        return out


@dataclass
class SheetIndex:
    """A document's sheets. ``rows`` has one row per page that is a sheet,
    in page order; ``notes`` says what a reader should know (a number on two
    pages, sheets not indexed)."""
    rows: List[SheetRow] = field(default_factory=list)
    n_pages: int = 0
    notes: List[str] = field(default_factory=list)

    @property
    def indexed(self) -> List[SheetRow]:
        return [r for r in self.rows if r.number]

    @property
    def not_indexed(self) -> List[SheetRow]:
        return [r for r in self.rows if not r.number]

    @property
    def unread(self) -> List[SheetRow]:
        """The sheets with no usable text layer."""
        return [r for r in self.rows if not r.readable]

    def page_of(self, number: str) -> Optional[int]:
        """The 0-based page of sheet ``number`` (the first, if two pages
        print it), or ``None``."""
        want = normalize_number(number)
        for r in self.rows:
            if r.number and normalize_number(r.number) == want:
                return r.page
        return None

    def number_of(self, page: int) -> Optional[str]:
        for r in self.rows:
            if r.page == page:
                return r.number
        return None

    def numbers(self) -> List[str]:
        return [r.number for r in self.rows if r.number]

    def duplicates(self) -> Dict[str, List[int]]:
        """Each number read on more than one page (normalized), with its
        pages in order."""
        held: Dict[str, List[int]] = {}
        for r in self.indexed:
            held.setdefault(normalize_number(r.number), []).append(r.page)
        return {n: pages for n, pages in held.items() if len(pages) > 1}


def normalize_number(number: str) -> str:
    """A sheet number for comparison: upper case, no spaces ("a-501" and
    "A-501" are one sheet; "A501" is not assumed to be "A-501")."""
    return re.sub(r"\s+", "", str(number or "")).upper()


def discipline(number: str) -> str:
    """The discipline part of a sheet number: the letters before its first
    digit ("S-201A" -> "S", "FP-101" -> "FP", "A5.01" -> "A"), or for a
    number with no letter the part before the dot ("10.31" -> "10")."""
    n = normalize_number(number)
    m = re.match(r"[A-Z]+", n)
    return m.group(0) if m else n.split(".")[0]


def number_shape(number: str) -> str:
    """The shape of a sheet number: letters as ``A``, digits as ``9``
    ("S-201A" -> "A-999A"). Two numbers of one set usually share a shape."""
    return re.sub(r"[A-Z]", "A", re.sub(r"\d", "9", normalize_number(number)))


# ---------------------------------------------------------------------------
# One page's pieces
# ---------------------------------------------------------------------------

@dataclass
class _Piece:
    """A piece of text shaped like a sheet number, where it is, and what
    line it came from."""
    page: int
    token: str
    bbox: BBox
    size: float
    turned: bool
    whole_line: bool
    line_text: str
    labelled: bool = False


@dataclass
class _Sheet:
    page: int
    width: float
    height: float
    lines: list
    has_text: bool
    reason: Optional[str] = None
    pieces: List[_Piece] = field(default_factory=list)


def _size(line) -> float:
    if getattr(line, "size", None):
        return float(line.size)
    x0, y0, x1, y1 = line.bbox
    return max(1.0, min(x1 - x0, y1 - y0) * 0.8)


def _turned(line) -> bool:
    rot = getattr(line, "rotation", 0.0)
    return rot is not None and abs((float(rot) % 180.0) - 90.0) < 20.0


def _in_band(bbox: BBox, width: float, height: float) -> bool:
    cx, cy = (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0
    return cx >= BAND_RIGHT * width or cy >= BAND_BOTTOM * height


def _token_box(line, start: int, end: int) -> BBox:
    """The box of ``line.text[start:end]``: the words that hold it where the
    line carries its words, else the line's box cut in proportion."""
    words = getattr(line, "words", None)
    token = line.text[start:end]
    if words:
        # a token has no space in it, so one word holds it: the word at the
        # token's place in the line where the words are the line's own,
        # else the first word that holds it
        nth = len(line.text[:start].split())
        if start and not line.text[start - 1].isspace():
            nth -= 1
        if (len(words) == len(line.text.split()) and 0 <= nth < len(words)
                and token in words[nth][0]):
            return tuple(float(v) for v in words[nth][1])
        for word, box in words:
            if token in word:
                return tuple(float(v) for v in box)
    x0, y0, x1, y1 = line.bbox
    n = max(1, len(line.text))
    if _turned(line):
        return (x0, y0 + (y1 - y0) * start / n, x1, y0 + (y1 - y0) * end / n)
    return (x0 + (x1 - x0) * start / n, y0, x0 + (x1 - x0) * end / n, y1)


def _reading(box: BBox, rotation) -> BBox:
    """``box`` in the frame its lettering is read in: x along the line, y
    from line to line. Lettering turned to read up the sheet (90), down it
    (270) or upside down (180) is turned back; anything else is left as it
    is."""
    x0, y0, x1, y1 = box
    turn = float(rotation or 0.0) % 360.0
    if abs(turn - 90.0) < 20.0:
        return (-y1, x0, -y0, x1)
    if abs(turn - 270.0) < 20.0:
        return (y0, -x1, y1, -x0)
    if abs(turn - 180.0) < 20.0:
        return (-x1, -y1, -x0, -y0)
    return box


def _beside_label(piece_box: BBox, label_box: BBox, rotation=0.0) -> bool:
    """Whether a value sits in the cell after its label: to its right on the
    same row, or under it, in the direction the value's lettering reads."""
    px0, py0, px1, py1 = _reading(piece_box, rotation)
    lx0, ly0, lx1, ly1 = _reading(label_box, rotation)
    ph, lh = py1 - py0, ly1 - ly0
    row = abs((py0 + py1) / 2 - (ly0 + ly1) / 2) <= 0.6 * max(ph, lh)
    right = row and -2.0 <= px0 - lx1 <= max(160.0, 14.0 * lh)
    column = px0 <= lx1 + 4.0 and px1 >= lx0 - 4.0
    under = column and -2.0 <= py0 - ly1 <= max(3.0 * lh, 0.8 * ph) + 6.0
    return right or under


def _numbers_in(line):
    """The sheet-number-shaped pieces of one line, as ``(masked text,
    match)``: the line with any sheet count blanked out, and each match."""
    masked = SHEET_COUNT.sub(lambda m: " " * len(m.group(0)), line.text)
    return [(masked, m) for m in SHEET_NUMBER.finditer(masked)]


#: A word that makes the label after it a reference to another sheet:
#: "REFER TO DWG NO. S-201", "SEE SHEET NO. A-5", "AS SHOWN ON DWG NO. C-2".
_REFERRAL_BEFORE = re.compile(
    r"\b(?:see|refer|referred|to|on|per|in|from|with|for|also|and|or|by|"
    r"of|verify|match|matches|shown)\W*$", re.I)


def _label_in(text: str):
    """The number label of a line that is a label, else ``None``: a short
    line holding one (three words or fewer: "SHEET NO.", "DWG NO. A-101"),
    a line that begins with one, or one later in a line that is not a
    reference to another sheet ("ACME ENGINEERS | DWG NO. C-101 | REV 2",
    a footer printed as one line). A label after a word that refers
    ("1. REFER TO DWG NO. S-201 FOR DETAILS") is not one: it names another
    sheet."""
    m = NUMBER_LABEL.search(text)
    if m is None:
        return None
    before = text[:m.start()]
    if len(text.split()) <= 3 or not before.strip():
        return m
    if not _REFERRAL_BEFORE.search(before):
        return m
    return None


def _after_label(between: str) -> bool:
    """Whether the text between a label and a number is only what
    separates them (space, a colon, a dot, a dash): the number is the
    label's value, the rest of its line."""
    return not between.strip(" \t:.#-")


def _read_page(doc, page: int) -> _Sheet:
    summary = doc.summary(page)
    width, height = float(summary.width), float(summary.height)
    try:
        lines = list(doc._page_text(page)[0])
        # a number inside a longer line ("SHEET NO. S-201A") is placed by
        # its word's box, so that page's text is read again with its words
        if any(masked.strip() != m.group(0)
               for ln in lines if _in_band(ln.bbox, width, height)
               for masked, m in _numbers_in(ln)):
            lines = list(doc._page_text(page, words=True)[0])
    except Exception:                                 # noqa: BLE001
        lines = []
    sheet = _Sheet(page, width, height, lines, has_text=bool(lines))
    if not lines:
        sheet.reason = ("no text layer on this sheet: read its title block "
                        "by looking")
        return sheet
    if not getattr(summary, "text_reliable", True):
        sheet.reason = ("the text layer of this sheet does not read as the "
                        "sheet does: read its title block by looking")
        sheet.has_text = False
        return sheet
    sheet.pieces = _pieces(page, lines, sheet.width, sheet.height)
    return sheet


def _pieces(page: int, lines, width: float, height: float,
            where=None) -> List[_Piece]:
    """The sheet-number-shaped pieces of one page's ``lines`` that lie in
    its title-block bands (or wherever ``where(bbox)`` says), each marked
    whether it is a label's value."""
    def inside(bbox) -> bool:
        if bbox is None:
            return False
        return where(bbox) if where is not None else _in_band(
            bbox, width, height)

    # a label whose value is in the next cell: a label line with nothing
    # after its label (one with a value on it labels only that value)
    labels = []
    for ln in lines:
        if not inside(ln.bbox):
            continue
        m = _label_in(ln.text)
        if m is not None and _after_label(ln.text[m.end():]):
            labels.append(ln)
    pieces: List[_Piece] = []
    for ln in lines:
        if not inside(ln.bbox):
            continue
        text = ln.text
        on_line = _label_in(text)
        for masked, m in _numbers_in(ln):
            box = _token_box(ln, m.start(), m.end())
            if not inside(box):
                continue
            before = masked[:m.start()]
            if NOT_A_SHEET_BEFORE.search(before):
                continue                   # "Page 1.1", "Figure A-1"
            labelled = (
                on_line is not None and on_line.end() <= m.start()
                and _after_label(masked[on_line.end():m.start()])) or any(
                lab is not ln and _beside_label(box, lab.bbox, ln.rotation)
                for lab in labels)
            pieces.append(_Piece(
                page, m.group(0), box, _size(ln), _turned(ln),
                whole_line=masked.strip() == m.group(0), line_text=text,
                labelled=labelled))
    return pieces


def page_sheet_number(lines, width: float, height: float,
                      is_sheet: bool = False, where=None) -> Optional[str]:
    """The sheet number one page prints, read on that page alone by the
    index's own rules for a sheet no slot covers: the value of a number
    label (on the label's line, or alone in the cell beside or under it),
    else -- on a sheet (``is_sheet``) -- a number standing alone in the
    title block's corner in lettering well above the rest
    (:func:`_by_label`, :func:`_by_size`). The slot needs the whole set,
    so it is :func:`sheet_index`'s alone. ``lines`` are the page's text
    lines in the displayed frame; ``where(bbox)`` widens or narrows the
    title-block bands. A sheet count is never a number. ``None`` when the
    page's text gives none."""
    lines = [ln for ln in (lines or ()) if getattr(ln, "bbox", None)]
    sheet = _Sheet(0, float(width), float(height), lines,
                   has_text=bool(lines))
    if not sheet.lines or not sheet.width or not sheet.height:
        return None
    sheet.pieces = _pieces(0, sheet.lines, sheet.width, sheet.height, where)
    piece = _by_label(sheet)
    if piece is None and is_sheet:
        piece = _by_size(sheet)
    return piece.token if piece is not None else None


# ---------------------------------------------------------------------------
# The slot
# ---------------------------------------------------------------------------

def _centre(piece: _Piece, sheet: _Sheet) -> Tuple[float, float]:
    return ((piece.bbox[0] + piece.bbox[2]) / 2.0 / sheet.width,
            (piece.bbox[1] + piece.bbox[3]) / 2.0 / sheet.height)


def _same_place(a, b, turned: bool) -> bool:
    along, across = (1, 0) if turned else (0, 1)
    return (abs(a[along] - b[along]) <= SLOT_ALONG
            and abs(a[across] - b[across]) <= SLOT_ACROSS)


def _slots(sheets: Sequence[_Sheet]) -> List[Dict[str, Any]]:
    """Groups of pieces at the same place from sheet to sheet, best first:
    ``{"pieces": {page: piece}, "cover", "distinct", "score"}``. Only a
    piece that is a line of its own or the value of a label takes part: a
    number inside a sentence ("Page 1.1 of the report") is not a title
    block's."""
    groups: List[Dict[str, Any]] = []
    # groups are looked up by a grid of tolerance-sized cells, so a long
    # set of dense sheets costs one pass, not one comparison per pair
    cells: Dict[Tuple[bool, int, int], List[Dict[str, Any]]] = {}
    for sheet in sheets:
        for piece in sheet.pieces:
            if not (piece.whole_line or piece.labelled):
                continue
            at = _centre(piece, sheet)
            along, across = (1, 0) if piece.turned else (0, 1)
            ia = int(at[along] // SLOT_ALONG)
            ic = int(at[across] // SLOT_ACROSS)
            found = None
            for da in (0, -1, 1):
                for dc in (0, -1, 1):
                    for g in cells.get((piece.turned, ia + da, ic + dc), ()):
                        if _same_place(at, g["at"], piece.turned):
                            found = g
                            break
                    if found is not None:
                        break
                if found is not None:
                    break
            if found is None:
                found = {"at": at, "turned": piece.turned, "pieces": {}}
                groups.append(found)
                cells.setdefault((piece.turned, ia, ic), []).append(found)
            held = found["pieces"].get(sheet.page)
            if held is None or piece.size > held.size:
                found["pieces"][sheet.page] = piece
    n = sum(1 for s in sheets if s.has_text)
    out = []
    for g in groups:
        pieces = list(g["pieces"].values())
        values = [p.token for p in pieces]
        cover = len(pieces) / max(1, n)
        distinct = len(set(values)) / len(values)
        if (len(pieces) < SLOT_MIN_SHEETS or cover < SLOT_MIN_COVER
                or distinct < SLOT_MIN_DISTINCT):
            continue
        labelled = sum(1 for p in pieces if p.labelled) / len(pieces)
        size = median(p.size for p in pieces)
        g.update(cover=cover, distinct=distinct,
                 score=cover * distinct * (1.0 + 0.5 * labelled)
                 * (1.0 + min(size, 48.0) / 48.0))
        out.append(g)
    return sorted(out, key=lambda g: -g["score"])


def _slots_for(group: Sequence[_Sheet]
               ) -> Dict[int, Tuple[_Piece, Dict[str, Any]]]:
    """``{page: (piece, slot)}`` for the sheets of one size. The best slot
    numbers the sheets it covers; the sheets it leaves are searched again
    for a slot of their own, its cover measured against those sheets (a
    set can hold two title-block layouts: a consultant's sheets among the
    architect's). A further slot is taken only where every one of its
    values has the shape of the first slot's numbers (``number_shape``: a
    key plan's "C1", "C2" beside a set numbered "A-101" is not one) and none
    is a number already read; it carries ``"first"``, the first slot."""
    out: Dict[int, Tuple[_Piece, Dict[str, Any]]] = {}
    rest = [s for s in group if s.has_text]
    first: Optional[Dict[str, Any]] = None
    while len(rest) >= SLOT_MIN_SHEETS:
        taken = {normalize_number(p.token) for p, _slot in out.values()}
        shapes = ({number_shape(p.token) for p in first["pieces"].values()}
                  if first is not None else None)
        found = None
        for slot in _slots(rest):
            values = {normalize_number(p.token)
                      for p in slot["pieces"].values()}
            if values & taken:
                continue
            if shapes is not None and not all(number_shape(v) in shapes
                                              for v in values):
                continue
            found = slot
            break
        if found is None:
            break
        if first is None:
            first = found
        else:
            found["first"] = first
        for page, piece in found["pieces"].items():
            out[page] = (piece, found)
        rest = [s for s in rest if s.page not in found["pieces"]]
    return out


def _by_label(sheet: _Sheet) -> Optional[_Piece]:
    """The sheet number of one sheet on its own: the labelled piece (the
    largest, if several)."""
    labelled = [p for p in sheet.pieces if p.labelled]
    if not labelled:
        return None
    return max(labelled, key=lambda p: p.size)


def _by_size(sheet: _Sheet) -> Optional[_Piece]:
    """The sheet number of one sheet with no label to go by: a number
    standing alone in the title block's corner (the lower right) in
    lettering well above the rest of the title block's and above every
    other number there (the usual lettering is the median of the bands'
    other lines). ``None`` unless one piece stands out so."""
    corner = [p for p in sheet.pieces if p.whole_line
              and (p.bbox[0] + p.bbox[2]) / 2.0 >= CORNER_X * sheet.width
              and (p.bbox[1] + p.bbox[3]) / 2.0 >= CORNER_Y * sheet.height]
    if not corner:
        return None
    best = max(corner, key=lambda p: p.size)
    band = [_size(ln) for ln in sheet.lines
            if _in_band(ln.bbox, sheet.width, sheet.height)
            and ln.text != best.line_text]
    if not band or best.size < STANDS_OUT * median(band):
        return None
    if any(p is not best and p.token != best.token
           and p.size >= 0.8 * best.size for p in sheet.pieces):
        return None
    return best


# ---------------------------------------------------------------------------
# Title, revision, count
# ---------------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


def _at(bbox: BBox, sheet: _Sheet) -> Tuple[float, float]:
    return ((bbox[0] + bbox[2]) / 2.0 / sheet.width,
            (bbox[1] + bbox[3]) / 2.0 / sheet.height)


def _near(a: Tuple[float, float], b: Tuple[float, float]) -> bool:
    return abs(a[0] - b[0]) <= SLOT_ALONG and abs(a[1] - b[1]) <= SLOT_ALONG


def _static_texts(sheets: Sequence[_Sheet]
                  ) -> Tuple[set, Dict[str, List[Tuple[float, float]]]]:
    """``(texts, places)``: the text the set prints in the title-block
    bands of most of its sheets, and, for the wording that also sits at the
    same place on each (the title block's own: the project, the firm, its
    labels), that place as fractions of the sheet. A note the sheets share
    but draw wherever it fits is in ``texts`` and has no place."""
    readable = [s for s in sheets if s.has_text]
    if len(readable) < SLOT_MIN_SHEETS:
        return set(), {}
    seen: Dict[str, List[Tuple[int, Tuple[float, float]]]] = {}
    for s in readable:
        for ln in s.lines:
            if _in_band(ln.bbox, s.width, s.height):
                seen.setdefault(_clean(ln.text), []).append(
                    (s.page, _at(ln.bbox, s)))
    need = max(SLOT_MIN_SHEETS, STATIC_SHARE * len(readable))
    texts = {t for t, hits in seen.items()
             if len({page for page, _ in hits}) >= need}
    places: Dict[str, List[Tuple[float, float]]] = {}
    for text in texts:
        if len(re.findall(r"[A-Za-z]", text)) < 3:
            continue          # a border's zone letters are not a title block
        if len(seen[text]) > 4 * len(readable):
            continue          # a note drawn many times a sheet ("TYP.")
        spots: List[Tuple[Tuple[float, float], set]] = []
        for page, at in seen[text]:
            for spot, pages in spots:
                if _near(at, spot):
                    pages.add(page)
                    break
            else:
                spots.append((at, {page}))
        held = [spot for spot, pages in spots if len(pages) >= need]
        if held:
            places[text] = held
    return texts, places


def _block_box(sheet: _Sheet, places: Dict[str, List[Tuple[float, float]]],
               number: Optional[_Piece]) -> Optional[BBox]:
    """The title block of ``sheet`` as far as the text shows it: the box
    round the wording the set prints at the same place on every sheet, and
    the sheet number. ``None`` when the set shows no such wording."""
    boxes = [ln.bbox for ln in sheet.lines
             if any(_near(_at(ln.bbox, sheet), spot)
                    for spot in places.get(_clean(ln.text), ()))]
    if not boxes:
        return None
    if number is not None:
        boxes.append(number.bbox)
    pad = 0.01 * max(sheet.width, sheet.height)
    return (min(b[0] for b in boxes) - pad, min(b[1] for b in boxes) - pad,
            max(b[2] for b in boxes) + pad, max(b[3] for b in boxes) + pad)


#: Not a title: no words, a date, a scale, a project number, an issue line.
_NOT_A_TITLE = re.compile(
    r"^[\W\d]*$|\d{4}[-/.]\d{1,2}[-/.]\d{1,2}"
    r"|\b\d{1,2}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|^\s*SCALE\b|\bPROJECT\s+NO\b|^\s*ISSUE\b|^\s*DATE\b", re.I)


def _title(sheet: _Sheet, number: Optional[_Piece], static: set,
           places: Optional[Dict[str, List[Tuple[float, float]]]] = None
           ) -> Optional[str]:
    """The sheet's title: under or beside a title label where there is one,
    else the largest lettering of the title block that is this sheet's own,
    wrapped lines joined. ``number`` is the sheet number's piece, ``None``
    for a sheet numbered some other way."""
    band = [ln for ln in sheet.lines
            if _in_band(ln.bbox, sheet.width, sheet.height)]
    revision_boxes = _revision_rows(sheet)[1]
    block = _block_box(sheet, places or {}, number)

    def own(ln) -> bool:
        text = _clean(ln.text)
        if text in static or len(re.findall(r"[A-Za-z]", text)) < 3:
            return False
        if _NOT_A_TITLE.search(text) or SHEET_COUNT.search(text):
            return False
        if NUMBER_LABEL.search(text) or TITLE_LABEL.match(text):
            return False
        if SHEET_NUMBER.fullmatch(text) or (
                number is not None and number.line_text == ln.text):
            return False
        return not any(_inside(ln.bbox, box) for box in revision_boxes)

    candidates = [ln for ln in band if own(ln)]
    labels = [ln for ln in band if TITLE_LABEL.match(ln.text)]
    chosen: list = []
    for lab in labels:
        near = [ln for ln in candidates
                if _beside_label(ln.bbox, lab.bbox, ln.rotation)]
        if near:
            first = min(near, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
            chosen = _wrapped(first, candidates)
            break
    if not chosen and static:
        # with no label, the largest lettering of the sheet's own inside
        # the title block (the bands also hold the drawing's lower and
        # right-hand parts, and a view's title there is not the sheet's).
        # A document of one sheet has nothing to tell its title from the
        # project's name, so it gets a title only from a label.
        if block is not None:
            candidates = [ln for ln in candidates
                          if _inside(ln.bbox, block)]
        if candidates:
            biggest = max(_size(ln) for ln in candidates)
            top = [ln for ln in candidates if _size(ln) >= 0.9 * biggest]
            first = min(top, key=lambda ln: (ln.bbox[1], ln.bbox[0]))
            chosen = _wrapped(first, candidates)
    if not chosen:
        return None
    return _clean(" ".join(ln.text for ln in chosen))[:160] or None


def _inside(inner: BBox, outer: BBox) -> bool:
    cx, cy = (inner[0] + inner[2]) / 2.0, (inner[1] + inner[3]) / 2.0
    return outer[0] <= cx <= outer[2] and outer[1] <= cy <= outer[3]


def _wrapped(first, candidates) -> list:
    """``first`` and the lines it wraps onto: the same lettering, the same
    left edge, each straight under the last."""
    out = [first]
    size = _size(first)
    rest = sorted((ln for ln in candidates if ln is not first),
                  key=lambda ln: ln.bbox[1])
    for ln in rest:
        last = out[-1]
        gap = ln.bbox[1] - last.bbox[3]
        if (abs(_size(ln) - size) <= 0.1 * size
                and abs(ln.bbox[0] - first.bbox[0]) <= 0.5 * size
                and -0.3 * size <= gap <= 1.2 * size):
            out.append(ln)
    return out


def _rev_order(mark: str) -> Tuple[int, int]:
    return (1, int(mark)) if mark.isdigit() else (0, ord(mark[0]))


def _revision_rows(sheet: _Sheet):
    """``(rows, boxes)``: the revision block's rows as ``(mark, note)``, and
    the box of the block (heading and rows) so its text is not taken for a
    title. Empty where no revision heading is found."""
    band = [ln for ln in sheet.lines
            if _in_band(ln.bbox, sheet.width, sheet.height)]
    heads = [ln for ln in band if REVISION_HEADING.match(ln.text)
             and len(_clean(ln.text)) <= 24]
    if not heads:
        return [], []
    head = min(heads, key=lambda ln: ln.bbox[1])
    h = max(6.0, head.bbox[3] - head.bbox[1])
    below = sorted((ln for ln in band if ln is not head
                    and ln.bbox[1] >= head.bbox[1] - 1.0
                    and ln.bbox[0] >= head.bbox[0] - 0.5 * h
                    and ln.bbox[1] - head.bbox[3] <= 0.35 * sheet.height),
                   key=lambda ln: (ln.bbox[1], ln.bbox[0]))
    # the rows' marks: a mark in its own cell under the heading's left edge,
    # or at the head of a row written as one line
    marks = []
    for ln in below:
        text = _clean(ln.text)
        m = None
        if ln.bbox[0] - head.bbox[0] <= 4.0 * h:
            m = _REV_MARK.match(text)
        m = m or _REV_ROW.match(text)
        if m:
            marks.append((ln, text, m))
    rows: List[Tuple[str, str]] = []
    block = list(head.bbox)

    def take(box) -> None:
        block[0], block[1] = min(block[0], box[0]), min(block[1], box[1])
        block[2], block[3] = max(block[2], box[2]), max(block[3], box[3])

    last_y = head.bbox[3]
    for ln, text, m in marks:
        if ln.bbox[1] - last_y > 4.0 * h + 10.0:
            break                      # a gap: the block ended above it
        cy = (ln.bbox[1] + ln.bbox[3]) / 2.0
        cells = []
        reach = ln.bbox[2]
        for o in sorted((o for o in below if o is not ln
                         and abs((o.bbox[1] + o.bbox[3]) / 2.0 - cy)
                         <= 0.6 * h and o.bbox[0] > ln.bbox[0]),
                        key=lambda o: o.bbox[0]):
            if o.bbox[0] - reach > max(120.0, 16.0 * h):
                break                  # the next cell along is another block's
            cells.append(o)
            reach = max(reach, o.bbox[2])
        rows.append((m.group(1), _clean(" ".join(
            [text[m.end():]] + [o.text for o in cells]))))
        for o in [ln] + cells:
            take(o.bbox)
        last_y = ln.bbox[3]
    for ln in below:                   # the column heads over the rows
        if (ln.bbox[1] <= block[3]
                and _clean(ln.text).upper().rstrip(":") in _COLUMN_HEADS):
            take(ln.bbox)
    return rows, [tuple(block)]


def _revision(sheet: _Sheet) -> Tuple[Optional[str], Optional[str]]:
    rows, _boxes = _revision_rows(sheet)
    if not rows:
        return None, None
    mark, note = max(rows, key=lambda r: _rev_order(r[0]))
    return mark, (note or None)


def _count(sheet: _Sheet) -> Optional[str]:
    for ln in sheet.lines:
        if not _in_band(ln.bbox, sheet.width, sheet.height):
            continue
        m = SHEET_COUNT.search(ln.text)
        if m and int(m.group(1)) <= int(m.group(2)):
            return f"{int(m.group(1))} of {int(m.group(2))}"
    return None


def _from_label(label: Optional[str]
                ) -> Optional[Tuple[str, Optional[str]]]:
    """``(number, title)`` from a PDF page label that begins with a sheet
    number ("A-101", "A-101 - FIRST FLOOR PLAN"), else ``None``. A label
    that only counts the pages ("12", "iv") is not one."""
    text = _clean(label or "")
    m = SHEET_NUMBER.match(text)
    if m is None:
        return None
    rest = text[m.end():].lstrip(" -:_\u2013\u2014").strip()
    title = rest if len(re.findall(r"[A-Za-z]", rest)) >= 3 else None
    return m.group(0), title


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

def _is_sheet(summary) -> bool:
    return (getattr(summary, "kind", None) == "drawing_sheet"
            or float(summary.width) * float(summary.height) >= SHEET_MIN_AREA)


def sheet_index(doc) -> SheetIndex:
    """The sheet index of ``doc`` (a :class:`planlens.document.Document`):
    one row per page that is a sheet (a drawing sheet by its content, or any
    page 11 x 17 in or larger). Reads every page once."""
    n = doc.n_pages
    index = SheetIndex(n_pages=n)
    sheets = [_read_page(doc, i) for i in range(n)
              if _is_sheet(doc.summary(i))]
    if not sheets:
        index.notes.append("no drawing sheets in this document")
        return index
    # sheets of one size share a title block; a set mixes sizes rarely
    by_size: Dict[Tuple[int, int], List[_Sheet]] = {}
    for s in sheets:
        by_size.setdefault((round(s.width / 4), round(s.height / 4)),
                           []).append(s)
    for group in by_size.values():
        slotted = _slots_for(group)
        static, places = _static_texts(group)
        for s in group:
            row = SheetRow(page=s.page, count=_count(s) if s.has_text
                           else None, readable=s.has_text)
            index.rows.append(row)
            piece, how, confidence = None, None, 0.0
            if not s.has_text:
                row.reason = s.reason
            elif s.page in slotted:
                piece, slot = slotted[s.page]
                how, confidence = HOW_SLOT, _slot_confidence(slot)
                if "first" in slot:
                    # a second layout's slot: said so, and trusted less
                    # than the first
                    how = HOW_FURTHER_SLOT
                    confidence = max(0.5, min(
                        confidence, _slot_confidence(slot["first"])) - 0.1)
            else:
                piece = _by_label(s)
                if piece is not None:
                    how, confidence = HOW_LABEL, 0.75
                else:
                    piece = _by_size(s)
                    if piece is not None:
                        how = HOW_SIZE
                        confidence = 0.5
            if piece is None:
                if s.has_text:
                    row.reason = NO_NUMBER + (
                        " (it prints a sheet count)" if row.count else "")
                labelled = _from_label(doc.label(s.page))
                if labelled is not None:
                    # the file's own name for the page, not a reading of
                    # the sheet: said so, and trusted less
                    row.number, title = labelled
                    row.title = title
                    row.how = HOW_PAGE_LABEL + (
                        "" if s.has_text else " (no text layer on the sheet)")
                    row.confidence, row.reason = 0.6, None
                continue
            row.number = piece.token
            row.how, row.confidence, row.bbox = how, confidence, piece.bbox
            row.title = _title(s, piece, static, places)
            row.revision, row.revision_note = _revision(s)
    index.rows.sort(key=lambda r: r.page)
    note = _duplicate_note(index)
    if note:
        index.notes.append(note)
    missing = index.not_indexed
    if missing:
        index.notes.append(
            f"{len(missing)} of {len(index.rows)} sheets could not be "
            f"indexed from their text")
    by_label = [r for r in index.indexed
                if (r.how or "").startswith(HOW_PAGE_LABEL)]
    if by_label:
        index.notes.append(
            f"{len(by_label)} of {len(index.rows)} sheets are numbered from "
            f"the PDF's page labels, not read from their title blocks")
    return index


def _slot_confidence(slot: Dict[str, Any]) -> float:
    return min(0.98, 0.6 + 0.4 * slot["cover"] * slot["distinct"])


def _pages_text(pages: Sequence[int], most: int = 6) -> str:
    shown = ", ".join(str(p) for p in pages[:most])
    return shown + (f" and {len(pages) - most} more"
                    if len(pages) > most else "")


def _duplicate_note(index: SheetIndex) -> Optional[str]:
    """One note for every number read on more than one page: the number
    and its pages where there is one, else how many and the first few;
    where every number of the document is read the same number of times,
    that is said plainly (a set bound in twice)."""
    dups = sorted(index.duplicates().items(), key=lambda kv: kv[1][0])
    if not dups:
        return None
    if len(dups) == 1:
        number, pages = dups[0]
        return (f"sheet number {number} is read on pages "
                f"{_pages_text(pages)}")
    head = ", ".join(f"{number} (pages {_pages_text(pages)})"
                     for number, pages in dups[:3])
    more = f", and {len(dups) - 3} more" if len(dups) > 3 else ""
    times = {len(pages) for _number, pages in dups}
    distinct = {normalize_number(n) for n in index.numbers()}
    if len(times) == 1 and len(dups) == len(distinct):
        k = times.pop()
        word = "two" if k == 2 else str(k)
        return (f"every sheet number in this document is read on {word} "
                f"pages ({len(dups)} numbers: {head}{more}); the document "
                f"may hold the set {'twice' if k == 2 else f'{k} times'} "
                f"over")
    return (f"{len(dups)} sheet numbers are each read on more than one "
            f"page: {head}{more}")


__all__ = ["SheetRow", "SheetIndex", "sheet_index", "page_sheet_number",
           "normalize_number",
           "number_shape", "discipline", "SHEET_NUMBER", "WHOLE_SHEET_NUMBER",
           "SHEET_COUNT", "HOW_SLOT", "HOW_LABEL", "HOW_SIZE",
           "HOW_PAGE_LABEL", "HOW_FURTHER_SLOT"]
