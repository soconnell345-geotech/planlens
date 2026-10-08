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
    caller is told did not go on. Where the matched text is ONE object that
    spans several printed lines with no word boxes (a CAD notes column stored
    as one hidden string), the mark goes on the printed line the quote is on,
    found from the rows of ink inside the object's box
    (:func:`_quoted_rows`), not on the object's left edge.
``bbox`` / ``point``
    a box or a spot in the DISPLAYED frame (:mod:`planlens.document.frame`) —
    the frame everything in this package reports and ``render_region`` renders,
    so a box straight off ``read_document`` or a markup goes back on unchanged.
    ``box`` and ``page_bbox`` are accepted as names for it.
``view`` + ``image_box``
    a box read off a rendered IMAGE: ``view`` is the displayed-frame rect the
    image shows (what a render reports as its view or clip) and ``image_box``
    the thing's box on a 0-999 grid over that image. It is converted here
    (:func:`planlens.document.budget.image_box_to_page`), so a caller who found
    something by LOOKING never does the arithmetic — the arithmetic is where
    markups landed in empty paper (field report, 2026-10-01). A box is only as
    good as the view it was read off, so one read off a view much wider than
    the mark, or one that is the whole view, is REFUSED with a reason
    (:func:`_view_anchor_problem`): zoom on the thing and anchor on the zoom.
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
    "circle": "Circle",
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
    "circle": (0.85, 0.15, 0.15),
    "callout": (0.85, 0.15, 0.15),
    "reply": (0.15, 0.35, 0.80),
}

#: ``/Subj`` per kind — what a viewer's comments list prints in its type column.
KIND_SUBJECT = {
    "note": "Note",
    "highlight": "Highlight",
    "box": "Box",
    "circle": "Circle",
    "callout": "Callout",
    "reply": "Reply",
}

#: Kinds that mark a REGION and so can carry a visible ``label`` beside it.
LABELLED_KINDS = ("box", "circle", "highlight")

#: A circle is the ellipse through the corners of the box it is given, grown
#: by this much, so everything in the box is inside the ring with a little air
#: (an ellipse inscribed in the box would cut its corners off).
CIRCLE_MARGIN = 2.0

#: A circle round something smaller than this (points) is drawn at least this
#: wide, so a ring round a three-letter tag is still a ring a reader sees.
CIRCLE_MIN_SIZE = 14.0

#: A visible label's point size and the gap between it and its mark.
LABEL_FONTSIZE = 9.0
LABEL_GAP = 2.0

#: Names accepted for ``bbox``: ``box`` is what a caller reaches for first (an
#: agent sent it twice in one field session, losing a call each time), and
#: ``page_bbox`` is what a located vision item carries.
BBOX_ALIASES = ("box", "page_bbox")

#: The obvious guesses at a kind's name, and the kind each one is written
#: as. A PDF's own name for a sticky note is a Text annotation, and a viewer's
#: comments list calls every markup a comment, so ``text`` and ``comment`` are
#: what a caller reaches for when it means a note (Foundry brief 4,
#: 2026-10-07: both cost an agent a round trip, refused).
KIND_ALIASES = {
    "comment": "note", "text": "note", "sticky": "note",
    "sticky_note": "note", "sticky note": "note",
    "rectangle": "box", "rect": "box", "square": "box",
    "ellipse": "circle", "oval": "circle", "ring": "circle",
}

#: Fields a caller adds to style a mark that this module does not take: the
#: colour of every mark is fixed by its kind (:data:`KIND_COLOR`). They are
#: ignored, with a note, rather than refusing the whole call — 8 of 14 marking
#: runs on Foundry (2026-10-07) lost a round trip to ``color``.
IGNORED_FIELDS = ("color", "colour")

#: What an ``anchor`` OBJECT may hold: a caller that nests the anchor
#: (``"anchor": {"quote": ...}``) has it read as the markup's own field.
ANCHOR_FIELDS = ("quote", "bbox", "point", "points_at", "reply_to", "view",
                 "image_box") + BBOX_ALIASES

#: The colours, in the words a note to the caller uses.
COLOR_WORDS = ("box, circle and callout red; highlight and note yellow; "
               "reply blue")

#: Two marks of one kind that say the same thing (the same label, or with
#: none the same comment) and overlap by more than this — intersection over
#: the smaller box — are one thing marked twice (Foundry brief 4: a tag ringed
#: twice from two zooms, each ring confirmed, and the answer counted eight
#: tags on a sheet of seven).
DUPLICATE_OVERLAP = 0.5

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

#: A box read off a rendered image is only as good as the VIEW it was read
#: off, because the error grows with the view, not with the thing. Measured
#: 2026-10-07 on an 11 x 17 sheet of 10 x 4 pt tags (GPT-5.4 on one host,
#: GPT-5.6 on another): boxes read off whole-sheet images (1224 pt across)
#: were 14-90 pt from their tags, about a tenth of the view, while boxes read
#: off views of 80-350 pt were 0.2-5 pt off. So a ``view`` + ``image_box``
#: anchor from a view wider than this on its longer side is refused for a
#: mark that is small next to the view (:data:`VIEW_ANCHOR_MIN_FRACTION`).
VIEW_ANCHOR_MAX_PT = 300.0

#: ... unless the mark is at least this fraction of the view's longer side:
#: an error of about a tenth of the view is then under half the mark's own
#: size, and the mark still lands on the thing.
VIEW_ANCHOR_MIN_FRACTION = 0.25

#: An ``image_box`` covering at least this fraction of its view on BOTH axes
#: is the view itself (``[0, 0, 999, 999]``), not a thing in it.
WHOLE_VIEW_FRACTION = 0.95


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
    is given — ``quote``, ``bbox``, ``point`` or ``reply_to``; a ``view`` +
    ``image_box`` pair (a box read off a rendered image) is converted to a
    ``bbox`` when the spec is built. A ``callout`` is the one kind that takes
    two things: ``points_at`` is where its leader lands, and is an anchor on
    its own; a ``bbox`` beside it says where its text box goes rather than what
    is being commented on. ``label`` is short text drawn ON the page beside a
    box, circle or highlight (``comment`` is what opens in the comments list).
    ``min_score`` is the lowest fuzzy score a quote may match at when the
    exact search finds nothing; it means what it means in
    :meth:`Document.search`. ``target`` names, in a few words or as printed,
    the thing the mark is ON — the tag, the line of text, the dimension —
    for a check of where the mark landed to compare with: a review comment
    is usually a request ABOUT the thing ("please confirm ..."), not its
    name, so it cannot serve. It is not drawn on the page.

    ``adjustments`` is filled by :meth:`from_dict`, never by a caller: what
    it read differently from what it was given (a kind's other name, a
    nested ``anchor``, a colour it does not take), so the caller is told.
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
    label: Optional[str] = None
    #: The rendered view a ``view`` + ``image_box`` anchor was read off and
    #: the box on it, kept beside the converted ``bbox`` so the writer can
    #: judge how far that box can be trusted (:func:`_view_anchor_problem`).
    view: Optional[BBox] = None
    image_box: Optional[BBox] = None
    target: Optional[str] = None
    adjustments: List[str] = field(default_factory=list, repr=False)

    #: Accepted in the JSON beside the fields (see :data:`BBOX_ALIASES` and
    #: the ``view`` + ``image_box`` anchor).
    INPUT_ONLY = BBOX_ALIASES + ("view", "image_box")

    #: Fields of the dataclass that are not the caller's to give.
    INTERNAL = ("adjustments",)

    @classmethod
    def fields_accepted(cls) -> List[str]:
        """Every field a markup's JSON may carry."""
        return sorted((set(cls.__dataclass_fields__) - set(cls.INTERNAL))
                      | set(cls.INPUT_ONLY))

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "MarkupSpec":
        """Build one from the JSON a tool layer receives, or say what is wrong.

        The obvious guesses are read rather than refused, each with a note in
        :attr:`adjustments`: a kind's other name (:data:`KIND_ALIASES`), an
        ``anchor`` object holding the anchor (:data:`ANCHOR_FIELDS`), and a
        colour (:data:`IGNORED_FIELDS` — every kind has its own)."""
        if not isinstance(raw, dict):
            raise ValueError("each markup must be an object with kind, page "
                             "and comment")
        raw, notes = _forgiven(raw)
        unknown = set(raw) - set(cls.fields_accepted())
        if unknown:
            raise ValueError(
                f"unknown markup field(s) {sorted(unknown)}; the fields are "
                f"{cls.fields_accepted()}")
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
        boxes = {n: raw[n] for n in ("bbox",) + BBOX_ALIASES
                 if raw.get(n) is not None}
        has_image = raw.get("view") is not None or \
            raw.get("image_box") is not None
        view = image_box = None
        if has_image:
            if raw.get("view") is None or raw.get("image_box") is None:
                raise ValueError(
                    "view and image_box go together: view is the rect the "
                    "rendered image shows (PDF points) and image_box the "
                    "thing's box on the 0-999 grid over that image")
            from planlens.document.budget import image_box_to_page
            view = _as_bbox(raw["view"], "view")
            image_box = _as_bbox(raw["image_box"], "image_box")
            boxes["view+image_box"] = image_box_to_page(
                image_box, view, 1000, 1000, units="norm1000")
        if len(boxes) > 1:
            raise ValueError(
                f"the {kind} markup gives its box {len(boxes)} ways "
                f"({', '.join(sorted(boxes))}); give one")
        label = str(raw.get("label") or "").strip() or None
        if label and kind not in LABELLED_KINDS:
            raise ValueError(f"label is drawn beside a box, circle or "
                             f"highlight, not a {kind}; put the words in "
                             f"comment")
        bbox_raw = next(iter(boxes.values()), None)
        target = raw.get("target")
        if target is not None and not isinstance(target, str):
            raise ValueError(
                "target is a few words naming the thing the mark is on (the "
                "tag, the line of text) — text, not a box; give the box as "
                "bbox")
        return cls(
            kind=kind,
            page=page,
            comment=str(raw.get("comment") or ""),
            quote=(str(raw["quote"]) if raw.get("quote") else None),
            bbox=(_as_bbox(bbox_raw, "bbox")
                  if bbox_raw is not None else None),
            label=label,
            point=(_as_point(raw["point"], "point")
                   if raw.get("point") is not None else None),
            points_at=(_as_point(raw["points_at"], "points_at")
                       if raw.get("points_at") is not None else None),
            reply_to=(str(raw["reply_to"]) if raw.get("reply_to") else None),
            author=(str(raw["author"]) if raw.get("author") else None),
            min_score=int(raw.get("min_score") or DEFAULT_FUZZY_MIN_SCORE),
            view=view,
            image_box=image_box,
            target=(target.strip() or None) if isinstance(target, str)
            else None,
            adjustments=notes,
        )


def _forgiven(raw: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """``raw`` with the obvious guesses read as what they mean, and a note
    for each (see :meth:`MarkupSpec.from_dict`). Anything else is left for
    the caller's own checks to refuse."""
    raw = dict(raw)
    notes: List[str] = []
    for name in IGNORED_FIELDS:
        if name in raw:
            raw.pop(name)
            notes.append(f"'{name}' is not a markup field and was ignored: "
                         f"each kind has its own colour ({COLOR_WORDS})")
    anchor = raw.get("anchor")
    if isinstance(anchor, dict):
        raw.pop("anchor")
        moved = []
        for key, value in anchor.items():
            if key not in ANCHOR_FIELDS:
                raise ValueError(
                    f"the anchor object holds '{key}', which is not an "
                    f"anchor; an anchor is one of quote, bbox, point, "
                    f"points_at, view + image_box or reply_to, given as the "
                    f"markup's own field")
            if raw.get(key) is not None and raw.get(key) != value:
                raise ValueError(
                    f"'{key}' is given twice, in the anchor object and as "
                    f"the markup's own field, with different values; give "
                    f"it once")
            raw[key] = value
            moved.append(key)
        notes.append(f"the anchor object was read as the markup's own "
                     f"{' + '.join(moved) or 'nothing'}: give the anchor as "
                     f"its own field (quote, bbox, point, view + image_box "
                     f"or reply_to)")
    kind = str(raw.get("kind") or "").strip().lower()
    if kind in KIND_ALIASES:
        raw["kind"] = KIND_ALIASES[kind]
        notes.append(f"kind '{kind}' was written as a {KIND_ALIASES[kind]}; "
                     f"the kinds are {list(KINDS)}")
    return raw, notes


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
    #: The box the caller anchored on — what the mark is ABOUT. For a circle
    #: it is smaller than ``bbox`` (the ring goes round it); a check of where a
    #: markup landed looks here.
    target: Optional[BBox] = None
    label: Optional[str] = None
    label_bbox: Optional[BBox] = None
    #: The position of the spec this came from in the caller's list (rows
    #: leave the skipped specs out, so a row's place is not its index).
    index: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        out: Dict[str, Any] = {
            "page": self.page,
            "kind": self.kind,
            "anchored_by": self.anchored_by,
            "bbox": [round(v, 1) for v in self.bbox],
        }
        if self.target is not None and self.kind == "circle":
            out["target"] = [round(v, 1) for v in self.target]
        if self.label:
            out["label"] = self.label
            if self.label_bbox is not None:
                out["label_bbox"] = [round(v, 1) for v in self.label_bbox]
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
    plausible. ``adjusted`` says which specs were read differently from how
    they were written (a kind's other name, a nested anchor, a colour), and
    ``duplicates`` which marks went on twice (:func:`duplicate_marks`).
    """
    output: str
    author: str = DEFAULT_AUTHOR
    appended: bool = False
    written: List[WrittenMarkup] = field(default_factory=list)
    skipped: List[Dict[str, Any]] = field(default_factory=list)
    adjusted: List[Dict[str, Any]] = field(default_factory=list)
    duplicates: List[Dict[str, Any]] = field(default_factory=list)

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
        if self.adjusted:
            out["adjusted"] = list(self.adjusted)
        if self.duplicates:
            out["duplicates"] = list(self.duplicates)
        return out


def _overlap_smaller(a: BBox, b: BBox) -> float:
    """Intersection over the SMALLER box's area (0 for a box with none)."""
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (iw * ih) / small if small > 0 else 0.0


def _says(mark: WrittenMarkup) -> str:
    """What a mark says, for telling two of them apart: its label, or with
    none its comment, folded."""
    return " ".join(str(mark.label or mark.comment or "").lower().split())


def duplicate_marks(written: Sequence[WrittenMarkup]
                    ) -> List[Dict[str, Any]]:
    """Marks that repeat an earlier one: the same page and kind, saying the
    same thing (:func:`_says`), over boxes — the thing anchored on where
    there is one, else the mark's own — overlapping by more than
    :data:`DUPLICATE_OVERLAP`. Each is reported once, against the first mark
    it repeats; replies are never duplicates (a thread may repeat itself)."""
    out: List[Dict[str, Any]] = []
    for j, b in enumerate(written):
        if b.kind == "reply":
            continue
        for a in written[:j]:
            if (a.page != b.page or a.kind != b.kind
                    or _says(a) != _says(b)):
                continue
            share = _overlap_smaller(a.target or a.bbox, b.target or b.bbox)
            if share > DUPLICATE_OVERLAP:
                out.append({"index": b.index, "same_as": a.index,
                            "page": b.page, "kind": b.kind,
                            **({"label": b.label} if b.label else {}),
                            "overlap": round(share, 2)})
                break
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


#: Rows of lettering inside a text object are found on a grey render at this
#: many pixels per point (a 4 pt letter is 16 px tall: enough to separate rows
#: one line-gap apart), capped so a large object stays a small render.
ROW_RENDER_PX_PER_PT = 4.0
ROW_RENDER_MAX_PX = 2400

#: A pixel darker than this (0-255 grey) is ink.
INK_LEVEL = 128


def _ink_rows(page, box: BBox) -> List[Tuple[BBox, float]]:
    """The printed rows of lettering inside ``box`` (displayed frame), top
    to bottom, as ``(row box, inked width in points)`` — from the page's own
    ink, not from its text, so it works on lettering drawn as strokes.

    The box is rendered grey WITHOUT annotations (a mark written earlier in
    the same call is not ink of the page), pixel rows holding ink are grouped
    into runs, and runs closer than a third of a typical row (the dot of an
    i, an underline) are joined. The inked width — the pixel columns of the
    row that carry ink — measures how much lettering the row holds, gaps and
    indents left out. Empty when the box cannot be rendered.
    """
    import fitz
    import numpy as np

    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    if w <= 0 or h <= 0:
        return []
    scale = min(ROW_RENDER_PX_PER_PT, ROW_RENDER_MAX_PX / max(w, h))
    try:
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale),
                              clip=fitz.Rect(*box), colorspace=fitz.csGRAY,
                              alpha=False, annots=False)
    except Exception:  # pragma: no cover - a render failure keeps the box
        return []
    if pix.width < 1 or pix.height < 1:
        return []
    grey = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
        pix.height, pix.stride)[:, :pix.width]
    ink = grey < INK_LEVEL
    has_ink = ink.any(axis=1)
    runs: List[List[int]] = []
    start = None
    for i, flag in enumerate(has_ink):
        if flag and start is None:
            start = i
        elif not flag and start is not None:
            runs.append([start, i])
            start = None
    if start is not None:
        runs.append([start, len(has_ink)])
    if not runs:
        return []
    typical = float(np.median([b - a for a, b in runs]))
    joined = [runs[0]]
    for a, b in runs[1:]:
        if a - joined[-1][1] < typical / 3.0:
            joined[-1][1] = b
        else:
            joined.append([a, b])
    rows = []
    for a, b in joined:
        cols = np.nonzero(ink[a:b].any(axis=0))[0]
        rows.append(((float(x0 + int(cols[0]) / scale), float(y0 + a / scale),
                      float(x0 + (int(cols[-1]) + 1) / scale),
                      float(y0 + b / scale)),
                     float(cols.size / scale)))
    return rows


def _undoubled(text: str) -> str:
    """``text`` with any run of three or more words that is immediately
    repeated written once. AutoCAD stores the first printed line of a
    hanging-indent paragraph twice in its hidden SHX text ("1. ENSURE FLUSH
    CONDITIONS AT CURB ENSURE FLUSH CONDITIONS AT CURB RAMP TO ...") — the
    page prints it once — and real prose never repeats three words in a row,
    so dropping the copy makes the string read the way the page does."""
    toks = text.split()
    low = [t.lower() for t in toks]
    out: List[str] = []
    i = 0
    while i < len(toks):
        for n in range(min(24, (len(toks) - i) // 2), 2, -1):
            if low[i:i + n] == low[i + n:i + 2 * n]:
                out.extend(toks[i:i + n])
                i += 2 * n
                break
        else:
            out.append(toks[i])
            i += 1
    return " ".join(out)


def _quoted_rows(line, matched: str, page) -> List[BBox]:
    """The printed rows of a multi-row text object that ``matched`` sits on.

    A CAD drawing can store a whole notes column as ONE hidden string with
    one box and no word boxes; a quote from note 4 then matched the column,
    and the mark went on the column's left edge, half-way down — beside note
    3, 50-80 pt from the quoted line (live check 2026-10-07). The rows of ink
    inside the box say where the printed lines are; the quote's place in the
    string, measured against how much lettering each row holds, says which
    of them it is on. A row the quote only grazes (an estimate a few letters
    over a row break) is left out. Empty when the object prints as one row
    or the quote cannot be placed in it — the caller then keeps the box.
    """
    if page is None or not matched:
        return []
    rows = _ink_rows(page, line.bbox)
    if len(rows) < 2:
        return []
    text = _undoubled(" ".join(str(line.text or "").split()))
    want = " ".join(matched.split()).lower()
    at = text.lower().find(want)
    if at < 0 or not text:
        return []
    # Rows of lettering hold several letters each: more rows than a third of
    # the characters, or rows whose ink is barely wider than they are tall,
    # are slices of something else (lettering that reads up the page, a
    # hatch) — keep the object's box rather than guess.
    if len(rows) > len(text) / 3.0:
        return []
    heights = sorted(b[3] - b[1] for b, _n in rows)
    widths = sorted(n for _b, n in rows)
    typical_h = heights[len(heights) // 2]
    typical_w = widths[len(widths) // 2]
    if typical_h <= 0 or typical_w < 3.0 * typical_h:
        return []
    start, end = at / len(text), (at + len(want)) / len(text)
    total = float(sum(n for _b, n in rows)) or 1.0
    picked: List[Tuple[float, BBox]] = []
    done = 0.0
    for box, n in rows:
        a, b = done / total, (done + n) / total
        done += n
        overlap = min(end, b) - max(start, a)
        if overlap > 0:
            picked.append((overlap / max(min(end - start, b - a), 1e-9), box))
    if not picked:
        return []
    kept = [box for share, box in picked if share >= 0.35]
    return kept or [max(picked)[1]]


def _quote_anchor(reader: Document, spec: MarkupSpec, page=None
                  ) -> Tuple[Optional[_Anchor], Optional[str]]:
    """The boxes of the words ``spec.quote`` matches on its page.

    Exact first, then the fuzzy pass, because those are the two readings of an
    exact miss and a review tool must not treat the first as the only one. One
    box per line the match touches, narrowed to the matched words where the
    page's own word boxes can say which those are, and to the printed row
    the quote is on where a line with no word boxes spans several
    (:func:`_quoted_rows`; ``page`` is the ``fitz.Page`` whose ink is read).
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
        narrowed = _quote_box_on_line(line, wanted)
        if narrowed is not None:
            boxes.append(narrowed)
        elif not line.words:
            boxes.extend(_quoted_rows(line, str(hit.get("match") or ""), page)
                         or [line.bbox])
        else:
            boxes.append(line.bbox)
    if not boxes:
        boxes = [_as_bbox(hit["bbox"], "hit bbox")]
    first = boxes[0]
    return _Anchor(how=how, boxes=boxes,
                   point=(first[0], (first[1] + first[3]) / 2.0),
                   score=hit.get("score")), None


def _view_anchor_problem(spec: MarkupSpec) -> Optional[str]:
    """Why a ``view`` + ``image_box`` anchor cannot place this mark, or None.

    Two cases, both from the live checks of 2026-10-07: the box is the whole
    view (an agent passed ``[0, 0, 999, 999]`` of a zoom and got a ring round
    the whole window), or the view is much wider than the mark (rings placed
    from whole-sheet looks landed 20-90 pt from 10 pt tags). See
    :data:`VIEW_ANCHOR_MAX_PT`. A callout's box beside its ``points_at`` only
    says where its text goes, so it is never judged.
    """
    if spec.view is None or spec.bbox is None:
        return None
    if spec.kind == "callout" and spec.points_at is not None:
        return None
    vx0, vy0, vx1, vy1 = spec.view
    vw, vh = vx1 - vx0, vy1 - vy0
    if vw <= 0 or vh <= 0:
        return None
    bx0, by0, bx1, by1 = spec.bbox
    bw, bh = bx1 - bx0, by1 - by0
    if bw >= WHOLE_VIEW_FRACTION * vw and bh >= WHOLE_VIEW_FRACTION * vh:
        return (f"image_box is the whole {vw:.0f} x {vh:.0f} pt view, not a "
                f"thing in it: a {spec.kind} round a whole view does not say "
                f"which thing it means. Box the thing itself in that view "
                f"(its own image_box), or zoom on it with render_region and "
                f"anchor on the zoom's view + the thing's image_box; to mark "
                f"the whole region on purpose, pass its rect as bbox")
    side = max(vw, vh)
    if side > VIEW_ANCHOR_MAX_PT and \
            max(bw, bh) < VIEW_ANCHOR_MIN_FRACTION * side:
        return (f"this {bw:.0f} x {bh:.0f} pt box was read off a "
                f"{vw:.0f} x {vh:.0f} pt view, and a box read off a view that "
                f"wide can be off by about a tenth of it (~{0.1 * side:.0f} "
                f"pt) — more than the {spec.kind} itself, so it would land "
                f"beside the thing. Zoom on the thing first (render_region "
                f"with this view + image_box) and anchor on the ZOOM's view + "
                f"the thing's image_box in it: a view of "
                f"{VIEW_ANCHOR_MAX_PT:.0f} pt or less in which the thing is "
                f"legible")
    return None


def _resolve_anchor(spec: MarkupSpec, reader_for_quote, markups_on_page,
                    page=None) -> Tuple[Optional[_Anchor], Optional[str]]:
    """The one anchor a spec names, or the reason it could not be used.
    ``page`` (the ``fitz.Page`` being written) lets a quote on a multi-row
    text object find its printed row."""
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
        return _quote_anchor(reader_for_quote(), spec, page)
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


def _circle_box(box: BBox, page_box: BBox) -> BBox:
    """The rect of the ellipse through ``box``'s corners, grown by
    :data:`CIRCLE_MARGIN`, at least :data:`CIRCLE_MIN_SIZE` across and kept on
    the page. An ellipse with semi-axes ``a, b`` passes through a
    ``w x h`` box's corners at ``a = w/sqrt(2), b = h/sqrt(2)``."""
    x0, y0, x1, y1 = box
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    a = max((x1 - x0) / 2.0 * 2 ** 0.5 + CIRCLE_MARGIN, CIRCLE_MIN_SIZE / 2.0)
    b = max((y1 - y0) / 2.0 * 2 ** 0.5 + CIRCLE_MARGIN, CIRCLE_MIN_SIZE / 2.0)
    px0, py0, px1, py1 = page_box
    return (max(px0, cx - a), max(py0, cy - b),
            min(px1, cx + a), min(py1, cy + b))


def _label_box(text: str, near: BBox, page_box: BBox) -> BBox:
    """Where a visible label goes: just above the mark's top-right corner,
    pushed back onto the page (below the mark when there is no room above)."""
    w = len(text) * LABEL_FONTSIZE * 0.62 + 6.0
    h = LABEL_FONTSIZE * 1.45 + 4.0
    px0, py0, px1, py1 = page_box
    x0 = min(max(near[2] - w * 0.25, px0 + 2.0), px1 - w - 2.0)
    y0 = near[1] - h - LABEL_GAP
    if y0 < py0 + 2.0:
        y0 = min(near[3] + LABEL_GAP, py1 - h - 2.0)
    return (x0, y0, x0 + w, y0 + h)


def _add_label(page, shape, text: str, near: BBox, author: str,
               created: str):
    """Draw ``text`` on the page beside a mark, as a borderless FreeText tied
    to the mark by ``/IRT`` with ``/RT /Group`` — the PDF's way of saying the
    two are one markup, which a viewer moves and deletes together and which
    planlens' reader reports as reply-linked to the mark."""
    import fitz

    page_box = to_display_bbox(page, (page.rect.x0, page.rect.y0,
                                      page.rect.x1, page.rect.y1))
    box = _label_box(text, near, page_box)
    annot = page.add_freetext_annot(
        _rect(page, from_display_bbox(page, box)), text,
        fontsize=LABEL_FONTSIZE, text_color=KIND_COLOR["circle"],
        border_width=0, rotate=int(page.rotation) % 360)
    annot.set_info(title=author, content=text, subject="Label",
                   creationDate=created, modDate=created)
    annot.update()
    # PyMuPDF 1.27 writes a default /CL (a leader from the page corner) on
    # every FreeText, callout or not. Nothing draws it, but a reader takes it
    # for a callout aimed at (0, 0) — so a label carries none.
    page.parent.xref_set_key(annot.xref, "CL", "null")
    annot.set_irt_xref(shape.xref)
    page.parent.xref_set_key(annot.xref, "RT", "/Group")
    return annot, box


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

    if spec.kind == "circle":
        box = bbox_union(anchor.boxes)
        if box is None:
            return None, "a circle needs a bbox or a quote to go round"
        ring = _circle_box(box, to_display_bbox(
            page, (page.rect.x0, page.rect.y0, page.rect.x1, page.rect.y1)))
        un = from_display_bbox(page, ring)
        # MuPDF grows a Circle's /Rect by the same 1 pt a Square's grows by;
        # compensated the same way, so the read-back box is the ring placed.
        pad = SQUARE_RECT_PAD if min(un[2] - un[0], un[3] - un[1]) > \
            4 * SQUARE_RECT_PAD else 0.0
        annot = page.add_circle_annot(_rect(page, (un[0] + pad, un[1] + pad,
                                                   un[2] - pad, un[3] - pad)))
        annot.set_border(width=BORDER_WIDTH)
        annot.set_colors(stroke=KIND_COLOR["circle"])
        _finish(annot, spec, author, created)
        annot.update()
        _add_popup(annot, page, ring)
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
    # MuPDF still hangs a sticky-note icon from a different corner on a
    # /Rotate 90, 180 or 270 page — measured: one icon size (16 pt) right,
    # down, or both of the spot. So the DISPLAYED box is read back and the
    # icon moved by what it missed by, which puts its displayed top-left on
    # the spot at every rotation.
    shown = to_display_bbox(page, probe.rect)
    dx, dy = spot[0] - shown[0], spot[1] - shown[1]
    if abs(dx) > 0.05 or abs(dy) > 0.05:
        probe.set_rect(_rect(page, from_display_bbox(
            page, (spot[0] + dx, spot[1] + dy,
                   spot[0] + dx + w, spot[1] + dy + h))))
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
    report.adjusted = [{"index": i, "notes": list(s.adjustments)}
                       for i, s in enumerate(specs) if s.adjustments]
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

            problem = _view_anchor_problem(spec)
            if problem is not None:
                report.skipped.append({
                    "index": index, "page": spec.page, "kind": spec.kind,
                    "reason": problem})
                continue
            anchor, reason = _resolve_anchor(spec, reader_for_quote,
                                             markups_on_page, page)
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
            shown = to_display_bbox(page, annot.rect)
            label_box = None
            if spec.label:
                _lab, label_box = _add_label(page, annot, spec.label, shown,
                                             who, created)
            report.written.append(WrittenMarkup(
                page=spec.page, kind=spec.kind, anchored_by=anchor.how,
                bbox=shown, comment=spec.comment,
                author=who,
                points_at=(spec.points_at or anchor.point
                           if spec.kind == "callout" else None),
                in_reply_to=(anchor.parent.id if anchor.parent else None),
                score=anchor.score, xref=annot.xref,
                target=(bbox_union(anchor.boxes)
                        if spec.kind in LABELLED_KINDS and anchor.boxes
                        else None),
                label=spec.label, label_bbox=label_box, index=index))
            # A page whose annotations were just added to must be re-read
            # before the next spec, or a reply naming a markup written in this
            # same call would not find it.
            annots_by_page.pop(spec.page, None)
        report.duplicates = duplicate_marks(report.written)
        parent = os.path.dirname(os.path.abspath(output))
        if parent:
            os.makedirs(parent, exist_ok=True)
        doc.save(output)
    finally:
        doc.close()
        if reader[0] is not None:
            reader[0].close()
    return report
