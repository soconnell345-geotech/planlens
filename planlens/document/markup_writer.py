"""Write review markups onto a COPY of a PDF — the other half of annotations.py.

:mod:`planlens.document.annotations` READS what a reviewer placed in Bluebeam
or Acrobat. This module writes: a caller — an LLM agent reviewing a submittal —
hands over a list of small specs and gets back a NEW PDF carrying ordinary PDF
annotations, which the reviewer opens in their own viewer and sees as comments,
highlights, boxes and callouts in the comments list like anybody else's.

**A markup is anchored, never placed by eye.** Each spec names ONE anchor:

``quote``
    text to find on that page. Exactly first, then through the same fuzzy
    fallback :meth:`~planlens.document.Document.search` offers, so a comment on
    a scan or on stroke-plotted lettering still lands on the words it is about.
    A quote nobody can find is REFUSED with a reason rather than dropped on an
    arbitrary spot — a comment in the wrong place is worse than a comment the
    caller is told did not go on.
``bbox`` / ``point``
    a box or a spot in the DISPLAYED frame (:mod:`planlens.document.frame`) —
    the frame everything in this package reports and ``render_region`` renders,
    so a box straight off ``read_document`` or a markup goes back on unchanged.
``reply_to``
    an existing markup's :attr:`~planlens.document.model.Markup.id`, which
    becomes a PDF reply link (``/IRT``) — the same link
    :attr:`Markup.in_reply_to` reads back.

Pages are 0-based, as :attr:`Markup.page` and every tool in this package are.

**The source is never modified.** It is opened from its bytes and the result is
written to ``output``; ``output == source`` is refused. With ``append=True``
(the default) an ``output`` that already exists becomes the base, so successive
calls accumulate onto one marked-up copy instead of each starting again.

**What the read-back box is.** Every written markup reports the box the READER
will see, not the one that was asked for, because MuPDF sets ``/Rect`` itself
for two of these kinds. A Square is placed so the two agree exactly (its rect
comes back 1 pt larger each way, which is compensated for). A Highlight's rect
carries an appearance margin around the quads — measured 1/16 of the line
height above and below and about 0.24 of it left and right — and that margin is
NOT compensated for: the quads are the ink, so shrinking them to make the box
match would stop the highlight covering the words. A callout's rect encloses
its leader as well as its text box, which is what the PDF specification asks
for.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

from planlens.document.annotations import extract_annotations
from planlens.document.document import DEFAULT_FUZZY_MIN_SCORE, Document
from planlens.document.frame import (
    bbox_union, from_display_bbox, from_display_corners, from_display_point,
    to_display_bbox,
)
from planlens.document.model import BBox, Markup, Point

#: What each kind becomes in the PDF — and therefore what ``Markup.kind`` reads
#: back as. A reply is a sticky note whose only difference is its ``/IRT`` link,
#: which is how Acrobat and Bluebeam both write one.
ANNOT_SUBTYPE = {
    "note": "Text",
    "highlight": "Highlight",
    "box": "Square",
    "callout": "FreeText",
    "reply": "Text",
}

#: The kinds :func:`write_markups` accepts.
KINDS = tuple(ANNOT_SUBTYPE)

#: Colour per kind, as a reviewer's own palette would run: yellow for something
#: to read, red for something to change, blue for an answer to someone else.
KIND_COLOR = {
    "note": (1.0, 0.82, 0.25),
    "highlight": (1.0, 0.94, 0.30),
    "box": (0.85, 0.15, 0.15),
    "callout": (0.85, 0.15, 0.15),
    "reply": (0.15, 0.35, 0.80),
}

#: ``/Subj`` per kind — what a viewer's comments list prints in its type column.
KIND_SUBJECT = {
    "note": "Note",
    "highlight": "Highlight",
    "box": "Box",
    "callout": "Callout",
    "reply": "Reply",
}

#: Author written on a markup whose caller named none.
DEFAULT_AUTHOR = "planlens"

#: Point size of a callout's text, and the pale fill its box gets so the
#: lettering reads against it.
CALLOUT_FONTSIZE = 10.0
CALLOUT_FILL = (1.0, 1.0, 0.88)

#: Width of a callout box this module places itself, and the characters that
#: fit on one of its lines at :data:`CALLOUT_FONTSIZE`.
CALLOUT_BOX_WIDTH = 220.0
CALLOUT_CHARS_PER_LINE = 46

#: Where a callout box this module places itself sits relative to the spot it
#: points at (displayed frame: x right, y down), before it is pushed back onto
#: the page.
CALLOUT_OFFSET = (48.0, -72.0)

#: How much larger than the rectangle it is given MuPDF makes a Square
#: annotation's ``/Rect``. Measured on PyMuPDF 1.27.2 at /Rotate 0, 90, 180 and
#: 270 and at border widths 0, 1 and 2 — always exactly 1 pt on each side — so
#: a Square is placed this much smaller and the box the reader sees is the box
#: the caller asked for. The tests pin it: if MuPDF ever changes, they say so
#: rather than the boxes quietly drifting.
SQUARE_RECT_PAD = 1.0

#: Border width drawn on a Square or a callout.
BORDER_WIDTH = 1.5

_ID = re.compile(r"^p(\d+)\.m\d+$")


def _pdf_date(when: Optional[datetime] = None) -> str:
    """``D:20260921160934-04'00'`` — a PDF date string for the local clock."""
    now = (when or datetime.now()).astimezone()
    off = now.utcoffset()
    if off is None:  # pragma: no cover - astimezone always sets one
        return now.strftime("D:%Y%m%d%H%M%S")
    total = int(off.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    return (now.strftime("D:%Y%m%d%H%M%S")
            + f"{sign}{total // 3600:02d}'{(total % 3600) // 60:02d}'")


def _as_point(value: Any, what: str) -> Point:
    try:
        x, y = value
        return (float(x), float(y))
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be [x, y] in PDF points "
                         f"(displayed page, top-left origin)")


def _as_bbox(value: Any, what: str) -> BBox:
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        raise ValueError(f"{what} must be [x0, y0, x1, y1] in PDF points "
                         f"(displayed page, top-left origin)")
    return (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


@dataclass
class MarkupSpec:
    """One markup a caller asks for: what it says, and what it is anchored to.

    ``kind`` is one of :data:`KINDS`. ``page`` is 0-based. Exactly one anchor
    is given — ``quote``, ``bbox``, ``point`` or ``reply_to``. A ``callout`` is
    the one kind that takes two things: ``points_at`` is where its leader
    lands, and is an anchor on its own; a ``bbox`` beside it says where its
    text box goes rather than what is being commented on. ``min_score`` is the
    lowest fuzzy score a quote may match at when the exact search finds
    nothing; it means what it means in :meth:`Document.search`.
    """
    kind: str
    page: int
    comment: str = ""
    quote: Optional[str] = None
    bbox: Optional[BBox] = None
    point: Optional[Point] = None
    points_at: Optional[Point] = None
    reply_to: Optional[str] = None
    author: Optional[str] = None
    min_score: int = DEFAULT_FUZZY_MIN_SCORE

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "MarkupSpec":
        """Build one from the JSON a tool layer receives, or say what is wrong."""
        if not isinstance(raw, dict):
            raise ValueError("each markup must be an object with kind, page "
                             "and comment")
        unknown = set(raw) - {f for f in cls.__dataclass_fields__}
        if unknown:
            raise ValueError(
                f"unknown markup field(s) {sorted(unknown)}; the fields are "
                f"{sorted(cls.__dataclass_fields__)}")
        kind = str(raw.get("kind") or "").strip().lower()
        if kind not in ANNOT_SUBTYPE:
            raise ValueError(f"unknown markup kind '{raw.get('kind')}'; "
                             f"kinds are {list(KINDS)}")
        if "page" not in raw:
            raise ValueError(f"the {kind} markup has no page (0-based)")
        try:
            page = int(raw["page"])
        except (TypeError, ValueError):
            raise ValueError(f"page must be a 0-based integer, not "
                             f"{raw['page']!r}")
        return cls(
            kind=kind,
            page=page,
            comment=str(raw.get("comment") or ""),
            quote=(str(raw["quote"]) if raw.get("quote") else None),
            bbox=(_as_bbox(raw["bbox"], "bbox")
                  if raw.get("bbox") is not None else None),
            point=(_as_point(raw["point"], "point")
                   if raw.get("point") is not None else None),
            points_at=(_as_point(raw["points_at"], "points_at")
                       if raw.get("points_at") is not None else None),
            reply_to=(str(raw["reply_to"]) if raw.get("reply_to") else None),
            author=(str(raw["author"]) if raw.get("author") else None),
            min_score=int(raw.get("min_score") or DEFAULT_FUZZY_MIN_SCORE),
        )


@dataclass
class WrittenMarkup:
    """One markup that went on, and where it actually landed.

    ``bbox`` is what :meth:`Document.markups` will report for it — the box the
    READER sees, which for a highlight or a callout is not the box that was
    asked for (see the module docstring). ``anchored_by`` says which anchor
    placed it, and carries ``score`` when the quote was matched approximately.
    """
    page: int
    kind: str
    anchored_by: str
    bbox: BBox
    comment: str = ""
    author: str = DEFAULT_AUTHOR
    points_at: Optional[Point] = None
    in_reply_to: Optional[str] = None
    score: Optional[float] = None
    xref: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "page": self.page,
            "kind": self.kind,
            "anchored_by": self.anchored_by,
            "bbox": [round(v, 1) for v in self.bbox],
        }
        if self.points_at is not None:
            out["points_at"] = [round(v, 1) for v in self.points_at]
        if self.in_reply_to:
            out["in_reply_to"] = self.in_reply_to
        if self.score is not None:
            out["score"] = round(float(self.score), 1)
        return out


@dataclass
class WriteReport:
    """What :func:`write_markups` wrote, what it refused, and where it wrote it.

    ``skipped`` is the half a caller must read: a markup whose quote is not on
    the page, whose anchor cannot carry its kind, or whose reply names a markup
    that is not there, is reported with its reason rather than placed somewhere
    plausible.
    """
    output: str
    author: str = DEFAULT_AUTHOR
    appended: bool = False
    written: List[WrittenMarkup] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def n_written(self) -> int:
        return len(self.written)

    @property
    def n_skipped(self) -> int:
        return len(self.skipped)

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "output_path": self.output,
            "author": self.author,
            "appended_to_existing": self.appended,
            "n_written": self.n_written,
            "n_skipped": self.n_skipped,
            "written": [w.to_dict() for w in self.written],
        }
        if self.skipped:
            out["skipped"] = list(self.skipped)
        return out


@dataclass
class _Anchor:
    """Where a spec's anchor put us, in the displayed frame."""
    how: str
    boxes: List[BBox] = field(default_factory=list)
    point: Optional[Point] = None
    score: Optional[float] = None
    parent: Optional[Markup] = None


def _read_bytes(source: Union[str, bytes, bytearray]) -> bytes:
    if isinstance(source, (bytes, bytearray)):
        return bytes(source)
    with open(source, "rb") as fh:
        return fh.read()


def _same_file(a: Any, b: Any) -> bool:
    if isinstance(a, (bytes, bytearray)) or isinstance(b, (bytes, bytearray)):
        return False
    return os.path.abspath(str(a)) == os.path.abspath(str(b))


def _tokens(text: str) -> List[str]:
    """Words of a string, lower-cased and stripped of edge punctuation."""
    return [t for t in (w.strip(".,;:()[]{}\"'").lower()
                        for w in (text or "").split()) if t]


def _quote_box_on_line(line, wanted: List[str]) -> Optional[BBox]:
    """The box of the words of ``line`` the quote actually covers, or None.

    A search hit names the LINES a match touches, and a line of a paragraph is
    the full width of the column — so highlighting the line to mark three words
    of it marks the wrong thing. The line's own word boxes narrow it: the
    longest run of consecutive words shared with the quote is the run that was
    matched. A line with no word boxes, or one the run does not reach (a fuzzy
    hit, where the letters differ), keeps the whole line.
    """
    if not line.words or not wanted:
        return None
    toks = [t.strip(".,;:()[]{}\"'").lower() for t, _b in line.words]
    best_len, best_at = 0, None
    for i in range(len(toks)):
        for k in range(len(wanted)):
            n = 0
            while (i + n < len(toks) and k + n < len(wanted)
                   and toks[i + n] == wanted[k + n]):
                n += 1
            if n > best_len:
                best_len, best_at = n, i
    if not best_len:
        return None
    return bbox_union([b for _t, b in
                       line.words[best_at:best_at + best_len]])


def _quote_anchor(reader: Document, spec: MarkupSpec
                  ) -> Tuple[Optional[_Anchor], Optional[str]]:
    """The boxes of the words ``spec.quote`` matches on its page.

    Exact first, then the fuzzy pass, because those are the two readings of an
    exact miss and a review tool must not treat the first as the only one. One
    box per line the match touches, narrowed to the matched words where the
    page's own word boxes can say which those are.
    """
    res = reader.search(spec.quote, pages=spec.page, include_markups=False,
                        max_hits=1)
    how = "quote"
    if not res["hits"]:
        try:
            res = reader.search(spec.quote, pages=spec.page,
                                include_markups=False, max_hits=1, fuzzy=True,
                                min_score=spec.min_score)
        except ImportError as exc:
            return None, f"the quote was not found exactly and {exc}"
        how = "quote_fuzzy"
    if not res["hits"]:
        return None, (f"'{spec.quote}' is not on page {spec.page}, exactly or "
                      f"at a fuzzy score of {spec.min_score} — check the page, "
                      f"or quote fewer words")
    hit = res["hits"][0]
    content = reader.page(spec.page, words=True, tables=False)
    wanted = _tokens(spec.quote)
    boxes = []
    for line_id in hit.get("line_ids", ()):
        line = content.line(line_id)
        if line is None:
            continue
        boxes.append(_quote_box_on_line(line, wanted) or line.bbox)
    if not boxes:
        boxes = [_as_bbox(hit["bbox"], "hit bbox")]
    first = boxes[0]
    return _Anchor(how=how, boxes=boxes,
                   point=(first[0], (first[1] + first[3]) / 2.0),
                   score=hit.get("score")), None


def _resolve_anchor(spec: MarkupSpec, reader_for_quote, markups_on_page
                    ) -> Tuple[Optional[_Anchor], Optional[str]]:
    """The one anchor a spec names, or the reason it could not be used."""
    named = [n for n, v in (("quote", spec.quote), ("bbox", spec.bbox),
                            ("point", spec.point),
                            ("reply_to", spec.reply_to)) if v is not None]
    if spec.kind == "reply":
        if spec.reply_to is None:
            return None, "a reply needs reply_to: the id of the markup it answers"
        parent = next((m for m in markups_on_page() if m.id == spec.reply_to),
                      None)
        if parent is None:
            page_of = _ID.match(spec.reply_to)
            if page_of and int(page_of.group(1)) != spec.page:
                return None, (f"markup '{spec.reply_to}' is on page "
                              f"{page_of.group(1)}, not page {spec.page}")
            return None, (f"no markup with id '{spec.reply_to}' on page "
                          f"{spec.page} — ids come from document_markups")
        return _Anchor(how="reply", boxes=[parent.bbox],
                       point=(parent.bbox[0], parent.bbox[1]),
                       parent=parent), None
    if not named:
        if spec.kind == "callout" and spec.points_at is not None:
            # points_at is an anchor in its own right for a callout: the spot
            # the leader lands on is the thing being commented on, and the box
            # is then placed beside it.
            return _Anchor(how="points_at", point=spec.points_at), None
        return None, (f"a {spec.kind} needs an anchor: quote, bbox or point "
                      f"(displayed-frame PDF points)")
    if len(named) > 1:
        return None, (f"a {spec.kind} names {len(named)} anchors "
                      f"({', '.join(named)}); give exactly one")
    if spec.quote is not None:
        return _quote_anchor(reader_for_quote(), spec)
    if spec.bbox is not None:
        x0, y0, x1, y1 = spec.bbox
        return _Anchor(how="bbox", boxes=[spec.bbox],
                       point=((x0 + x1) / 2.0, (y0 + y1) / 2.0)), None
    return _Anchor(how="point", point=spec.point), None


def _callout_box(tip: Point, comment: str, page_box: BBox) -> BBox:
    """A text box for a callout the caller gave no box for, kept on the page."""
    n_lines = max(1, -(-len(comment) // CALLOUT_CHARS_PER_LINE))
    height = max(28.0, n_lines * CALLOUT_FONTSIZE * 1.35 + 8.0)
    x0 = tip[0] + CALLOUT_OFFSET[0]
    y0 = tip[1] + CALLOUT_OFFSET[1]
    px0, py0, px1, py1 = page_box
    x0 = min(max(x0, px0 + 4.0), max(px0 + 4.0, px1 - CALLOUT_BOX_WIDTH - 4.0))
    y0 = min(max(y0, py0 + 4.0), max(py0 + 4.0, py1 - height - 4.0))
    return (x0, y0, x0 + CALLOUT_BOX_WIDTH, y0 + height)


def _finish(annot, spec: MarkupSpec, author: str, created: str) -> None:
    """The fields every viewer reads: who, when, what it says, what type."""
    annot.set_info(title=author, content=spec.comment,
                   subject=KIND_SUBJECT[spec.kind], creationDate=created,
                   modDate=created)


def _add_popup(annot, page, bbox: BBox) -> None:
    """A popup window beside the markup, so the comment opens where it belongs.

    Acrobat and Bluebeam both list a comment without one, but a reader who
    double-clicks the markup on the page expects the note to open there rather
    than at the page origin.
    """
    x0, y0, x1, y1 = bbox
    rect = from_display_bbox(page, (x1 + 6.0, y0, x1 + 186.0, y0 + 90.0))
    try:
        annot.set_popup(_rect(page, rect))
    except Exception:  # pragma: no cover - viewer-optional, never fatal
        pass


def _rect(page, bbox: Sequence[float]):
    import fitz
    return fitz.Rect(*bbox)


def _place(page, spec: MarkupSpec, anchor: _Anchor, author: str, created: str
           ) -> Tuple[Optional[Any], Optional[str]]:
    """Put one markup on one page. Returns ``(annot, reason it was skipped)``."""
    import fitz

    if spec.kind == "highlight":
        if not anchor.boxes:
            return None, ("a highlight needs text to sit over: anchor it with "
                          "a quote, or give a bbox")
        quads = [fitz.Quad(*(fitz.Point(*p)
                             for p in from_display_corners(page, b)))
                 for b in anchor.boxes]
        annot = page.add_highlight_annot(quads=quads)
        annot.set_colors(stroke=KIND_COLOR["highlight"])
        _finish(annot, spec, author, created)
        annot.update()
        _add_popup(annot, page, anchor.boxes[0])
        return annot, None

    if spec.kind == "box":
        box = bbox_union(anchor.boxes)
        if box is None:
            return None, "a box needs a bbox or a quote to enclose"
        un = from_display_bbox(page, box)
        # Compensated: MuPDF grows a Square's /Rect by SQUARE_RECT_PAD on each
        # side, so placing it that much smaller makes the read-back box the one
        # the caller asked for. A box too small to give the padding back is
        # placed as asked and comes back that little larger.
        pad = SQUARE_RECT_PAD if min(un[2] - un[0], un[3] - un[1]) > \
            4 * SQUARE_RECT_PAD else 0.0
        annot = page.add_rect_annot(_rect(page, (un[0] + pad, un[1] + pad,
                                                 un[2] - pad, un[3] - pad)))
        annot.set_border(width=BORDER_WIDTH)
        annot.set_colors(stroke=KIND_COLOR["box"])
        _finish(annot, spec, author, created)
        annot.update()
        _add_popup(annot, page, box)
        return annot, None

    if spec.kind == "callout":
        tip = spec.points_at or anchor.point
        if tip is None:
            return None, ("a callout needs a spot to point at: give points_at, "
                          "a point, or a quote")
        page_box = to_display_bbox(page, (page.rect.x0, page.rect.y0,
                                          page.rect.x1, page.rect.y1))
        box = spec.bbox if (spec.bbox is not None and spec.points_at is not None) \
            else _callout_box(tip, spec.comment, page_box)
        un_box = from_display_bbox(page, box)
        # The leader runs tip -> knee -> the box's near edge; the PDF spec puts
        # the END at the box and the START on the thing being commented on,
        # which is where the arrowhead is drawn and where Markup.points_at
        # reads from. The knee turns the run horizontal before it meets the
        # box, the way a drafter's leader does.
        edge = (box[0], (box[1] + box[3]) / 2.0)
        knee = ((tip[0] + edge[0]) / 2.0, edge[1])
        annot = page.add_freetext_annot(
            _rect(page, un_box), spec.comment or " ",
            fontsize=CALLOUT_FONTSIZE, text_color=KIND_COLOR["callout"],
            fill_color=CALLOUT_FILL, border_width=BORDER_WIDTH,
            # A FreeText lays its text out in the UNROTATED box, where a box
            # that is wide as DISPLAYED is tall and narrow — so on a /Rotate 90
            # sheet the comment came out sideways and clipped to a few words.
            # ``rotate=page.rotation`` is what turns it back: measured at
            # /Rotate 0, 90, 180 and 270, the rendered region is then
            # pixel-identical to the same callout on an unrotated page.
            rotate=int(page.rotation) % 360,
            callout=tuple(fitz.Point(*from_display_point(page, *p))
                          for p in (tip, knee, edge)),
            line_end=fitz.PDF_ANNOT_LE_OPEN_ARROW)
        # A FreeText takes no set_colors and no border_color outside rich text:
        # its ``/DA`` colour draws the lettering, the frame AND the leader, so
        # the kind's colour is passed as the text colour and the box is filled
        # pale enough to read it against.
        _finish(annot, spec, author, created)
        annot.update()
        return annot, None

    # note and reply are both sticky notes; a reply additionally carries /IRT.
    spot = anchor.point
    if spot is None:  # pragma: no cover - every anchor yields a point
        return None, "a note needs a point, a bbox or a quote"
    probe = page.add_text_annot(
        _rect(page, from_display_bbox(page, (spot[0], spot[1],
                                             spot[0] + 1.0, spot[1] + 1.0))).tl,
        spec.comment, icon="Comment")
    # The icon is a fixed size MuPDF chooses; ask it what that is, then set the
    # rect so the icon's DISPLAYED top-left is the spot the caller named. On a
    # rotated page the unrotated top-left is a different corner, so placing it
    # by the point alone lands the icon a whole icon away.
    w, h = probe.rect.width, probe.rect.height
    probe.set_rect(_rect(page, from_display_bbox(
        page, (spot[0], spot[1], spot[0] + w, spot[1] + h))))
    annot = probe
    annot.set_colors(stroke=KIND_COLOR[spec.kind])
    _finish(annot, spec, author, created)
    annot.update()
    if spec.kind == "reply" and anchor.parent is not None:
        annot.set_irt_xref(anchor.parent.xref)
        # /IRT alone says "related to"; /RT /R is what makes a viewer thread it
        # as a REPLY rather than draw it as a group member.
        page.parent.xref_set_key(annot.xref, "RT", "/R")
    _add_popup(annot, page, to_display_bbox(page, annot.rect))
    return annot, None


def write_markups(source: Union[str, bytes],
                  output: str,
                  markups: Sequence[Union[MarkupSpec, Dict[str, Any]]],
                  *,
                  author: str = DEFAULT_AUTHOR,
                  append: bool = True) -> WriteReport:
    """Write review markups onto a copy of ``source``, saved as ``output``.

    ``markups`` are :class:`MarkupSpec` objects or the dicts they are built
    from. Each is placed in order; one that cannot be anchored is recorded in
    :attr:`WriteReport.skipped` with its reason and the rest still go on.

    ``author`` is written on every markup that names none of its own — a review
    a reader must be able to tell from a person's. With ``append=True`` an
    existing ``output`` is the base, so a second call adds to the first call's
    file; with ``append=False`` it is overwritten from ``source``.
    """
    import fitz

    if _same_file(source, output):
        raise ValueError(
            "output must be a different file from source: write_markups never "
            "modifies the document it reads")
    specs = [m if isinstance(m, MarkupSpec) else MarkupSpec.from_dict(m)
             for m in markups]
    appended = bool(append) and os.path.isfile(output)
    base = _read_bytes(output if appended else source)
    report = WriteReport(output=os.path.abspath(output), author=author,
                         appended=appended)
    doc = fitz.open(stream=base, filetype="pdf")
    reader: List[Optional[Document]] = [None]
    annots_by_page: Dict[int, List[Markup]] = {}
    created = _pdf_date()

    def reader_for_quote() -> Document:
        # Opened only if a quote is actually anchored on, and once for the run.
        if reader[0] is None:
            reader[0] = Document(content=base, name=os.path.basename(output))
        return reader[0]

    try:
        for index, spec in enumerate(specs):
            if not 0 <= spec.page < doc.page_count:
                report.skipped.append({
                    "index": index, "page": spec.page, "kind": spec.kind,
                    "reason": (f"page {spec.page} is outside this document, "
                               f"which has pages 0-{doc.page_count - 1}")})
                continue
            page = doc[spec.page]

            def markups_on_page(index=spec.page):
                if index not in annots_by_page:
                    annots_by_page[index] = extract_annotations(doc[index],
                                                                index)[0]
                return annots_by_page[index]

            anchor, reason = _resolve_anchor(spec, reader_for_quote,
                                             markups_on_page)
            if anchor is None:
                report.skipped.append({
                    "index": index, "page": spec.page, "kind": spec.kind,
                    "reason": reason})
                continue
            who = spec.author or author
            annot, reason = _place(page, spec, anchor, who, created)
            if annot is None:
                report.skipped.append({
                    "index": index, "page": spec.page, "kind": spec.kind,
                    "reason": reason})
                continue
            report.written.append(WrittenMarkup(
                page=spec.page, kind=spec.kind, anchored_by=anchor.how,
                bbox=to_display_bbox(page, annot.rect), comment=spec.comment,
                author=who,
                points_at=(spec.points_at or anchor.point
                           if spec.kind == "callout" else None),
                in_reply_to=(anchor.parent.id if anchor.parent else None),
                score=anchor.score, xref=annot.xref))
            # A page whose annotations were just added to must be re-read
            # before the next spec, or a reply naming a markup written in this
            # same call would not find it.
            annots_by_page.pop(spec.page, None)
        parent = os.path.dirname(os.path.abspath(output))
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.save(output)
    finally:
        doc.close()
        if reader[0] is not None:
            reader[0].close()
    return report
