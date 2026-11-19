"""Frames, tiles, zoom windows and turned lettering: the geometry a look
needs, apart from the model.

Moved from the GeotechStaffEngineer app's ``vision_view`` module (BACKLOG
PL-29 (a)). What moved is the geometry the document tools convert boxes
with; what stays in the app is the image budget per model, the detail
level and the text given to the model (planlens already owns
``Document.render(budget=)``).

* **Where things are -- pixels in, a 0-999 grid out.** A look is told the
  image's size and asked for locations as boxes in PIXELS of that image
  (``px=[x0, y0, x1, y1]``); :func:`boxes_to_grid` converts them, with the
  size SENT, into boxes on a 0-999 grid over the image, and
  :func:`image_box_to_page` turns a view and a 0-999 box back into a box on
  the page. :func:`px_box_to_page` is the exact conversion with no grid.
  A box with a page unit written after it (``[..] pt``) is the model's own
  estimate in the page's units: it is left as written and not read as a
  place.
* **How far to trust a location.** A box is only as good as the view it was
  read off: off by up to about a tenth of a whole sheet, a few points from a
  zoom (:func:`location_error`, :func:`precision_note`); a zoom on a box is
  padded by that error (:func:`zoom_pad`, :func:`zoom_window`).
* **Tiles.** :func:`tile_grid` says how many tiles per side bring a page's
  small lettering up to a legible size; :func:`tile_boxes` cuts them.
  :func:`region_tiles` cuts along the sheet's own regions instead (app
  BACKLOG VIS-30); nothing calls it yet.
* **Turned lettering is sent upright.** Where a page's text layer shows the
  lettering in a view running up, down or upside down, the image can be
  sent turned so it reads upright (:func:`reading_turn`,
  :func:`turn_image`); every pixel box is turned back before it becomes a
  page box, so ``view`` + ``image_box`` mean the same thing either way.
* **Lettering cut by the window's edge.** :func:`widen_to_lines` widens a
  zoom, within a limit, to take in whole the text-layer lines its edges
  cut; :func:`cut_note` names the lines its edges still cut.

Every box is in planlens' displayed frame (PDF points as the page is shown,
top-left origin).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, NamedTuple, Optional, Sequence, Tuple


#: Lettering shorter than this in an image is not read reliably (5 pt CAD
#: lettering at ~5 px smeared bar sizes and spacings for GPT-5.1, 2026-09-24).
LEGIBLE_TEXT_PX = 12.0

#: The lettering height a suggested zoom window aims for.
TARGET_TEXT_PX = 16.0


#: A box read off a view can be off by up to about this fraction of the view
#: (per axis). Measured 2026-10-07 on an 11 x 17 sheet: 0-999 boxes off
#: whole-sheet images were 14-90 pt off (to 0.11 of the sheet's height), zooms
#: of 80-350 pt 0.2-5 pt. Pixel boxes did far better on the whole sheet (1-6
#: pt, one call), but a model can still answer on the grid, so locations are
#: trusted as if it had.
LOCATION_ERROR_FRAC = 0.10

#: ... and never less than this, in points (a zoom's own boxes were up to
#: 12 pt off in y twice in seven, GPT-5.4 on 40-pt crops).
MIN_LOCATION_ERROR_PT = 12.0

#: A view no wider than this (its longer side, pt) is one a mark may be
#: placed from — the same limit planlens' writer enforces
#: (``markup_writer.VIEW_ANCHOR_MAX_PT``). A page view's rule: a view of an
#: IMAGE is in its pixels and is judged by the scale it was sent at
#: (``marked_copy.judge_image_views``, app BACKLOG VIS-26).
MARK_VIEW_PT = 300.0



#: Lettering assumed on a CAD sheet whose lettering cannot be measured (drawn
#: as lines): 0.06 in, the small end of plotted half-size sheets — the
#: drawing set of the 2026-09-25 test run.
ASSUMED_CAD_LETTERING_PT = 4.3

#: Most tiles one sheet is split into for a read (4 x 4).
MAX_TILES_PER_SIDE = 4


def tile_grid(info: Dict[str, Any]) -> int:
    """Tiles per side needed for the page's small lettering to arrive at
    :data:`TARGET_TEXT_PX` — 1 when the whole page already reads.

    Uses the measured ``text_px`` where the text layer gives one; on a sheet
    whose lettering is drawn as lines (no measurable text), assumes
    :data:`ASSUMED_CAD_LETTERING_PT`."""
    import math
    x0, y0, x1, y1 = info["clip"]
    # The image's width ALONG the view's x: its height when sent turned.
    across, _down = unturned_size((info["width_px"], info["height_px"]),
                                  info.get("turn") or 0)
    px_per_pt = across / max(x1 - x0, 1e-6)
    text_px = info.get("text_px")
    if text_px is None:
        if info.get("text_chars", 1 << 30) >= 40:
            return 1                         # prose page with no size: fine
        text_px = ASSUMED_CAD_LETTERING_PT * px_per_pt
    if text_px >= LEGIBLE_TEXT_PX:
        return 1
    n = math.ceil(TARGET_TEXT_PX / max(text_px, 0.1))
    return max(1, min(MAX_TILES_PER_SIDE, n))


def tile_boxes(clip: Sequence[float], n: int, overlap: float = 0.08):
    """``n`` x ``n`` boxes over ``clip`` (points), each widened by
    ``overlap`` of its size so nothing on a seam is cut in two. Row-major,
    top-left first."""
    x0, y0, x1, y1 = (float(v) for v in clip)
    w, h = (x1 - x0) / n, (y1 - y0) / n
    ox, oy = overlap * w, overlap * h
    out = []
    for r in range(n):
        for c in range(n):
            out.append((max(x0, x0 + c * w - ox), max(y0, y0 + r * h - oy),
                        min(x1, x0 + (c + 1) * w + ox),
                        min(y1, y0 + (r + 1) * h + oy)))
    return out



# ---------------------------------------------------------------------------
# Locations: pixel boxes from the vision call, 0-999 boxes for the agent
# ---------------------------------------------------------------------------

_NUM = r"-?\d+(?:\.\d+)?"
#: A box as a vision answer writes one: ``px=[x0, y0, x1, y1]`` (also ``px:``,
#: ``px [..]``, ``px=(..)``), ``[..] px`` / ``[..] pixels``, or a bare
#: ``[a, b, c, d]``.
_BOX = re.compile(
    rf"(?P<pre>\bpx\s*[=:]?\s*)?(?P<open>[\[(])\s*(?P<a>{_NUM})\s*,\s*"
    rf"(?P<b>{_NUM})\s*,\s*(?P<c>{_NUM})\s*,\s*(?P<d>{_NUM})\s*[\])]"
    rf"(?P<post>\s*(?:px|pixels?)\b)?", re.IGNORECASE)

#: A page unit written right after a box (app BACKLOG VIS-24): ``[..] pt``,
#: ``[..] points``, ``[..] in PDF points``, ``[..] PDF-view pt``, ``[..]
#: (page coordinates)``. Such a box is the vision model's own estimate in
#: the page's units, neither a pixel box nor a 0-999 box: it is left as
#: written (:func:`boxes_to_grid`) and not read as a place
#: (:func:`answer_boxes`). A bare "points" is the unit only before
#: punctuation or the end of a line: "points right to the boring" is the
#: verb. Not a size ("pt lettering").
_PAGE_UNIT_AFTER = re.compile(
    r"\s*[)\]}]?\s*\(?\s*"
    r"(?:(?:in\s+)?(?:(?:pdf|page)(?:[\s-]+[a-z]+)?[\s-]+)?(?:pt|pts)\b"
    r"|(?:in\s+)?(?:pdf|page)(?:[\s-]+[a-z]+)?[\s-]+points\b"
    r"|in\s+points\b"
    r"|points(?=[ \t]*(?:[^\w\s]|\r?\n|$))"
    r"|(?:in\s+)?(?:pdf|page)[\s-]+(?:units|co-?ordinates|coords|space)\b)"
    r"(?!\s*(?:font|type|text|letter|lettering|tall|high|size|thick|"
    r"line)\b)", re.IGNORECASE)

#: A box value over this cannot be on the 0-999 grid.
_GRID_MAX = 999.5


def _box_values(m) -> List[float]:
    return [float(m.group(k)) for k in "abcd"]


def _is_box(m) -> bool:
    """A tagged box, or a bare one in square brackets — ``(1, 2, 3, 4)``
    with no tag is prose, not a box."""
    return bool(m.group("pre") or m.group("post") or m.group("open") == "[")


def px_to_grid(px_box: Sequence[float], size: Sequence[int],
               turn: int = 0) -> List[int]:
    """A pixel box on an image of ``size`` (w, h) as a whole-number box on
    the 0-999 grid over the same image (corners ordered, clamped). For an
    image sent turned ``turn`` degrees clockwise (``size`` as sent), the
    grid is over the view as the page shows it: the box is turned back
    first (:func:`unturn_px_box`)."""
    if int(turn or 0) % 360:
        px_box = unturn_px_box(px_box, size, turn)
        size = unturned_size(size, turn)
    w, h = (max(float(v), 1.0) for v in size)
    x0, y0, x1, y1 = (float(v) for v in px_box)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)

    def g(v, s):
        return int(max(0, min(999, round(v / s * 999.0))))
    return [g(x0, w), g(y0, h), g(x1, w), g(y1, h)]


def px_box_to_page(view: Sequence[float], px_box: Sequence[float],
                   size: Sequence[int], turn: int = 0
                   ) -> Tuple[float, float, float, float]:
    """A pixel box on the image of ``view`` (``size`` = the image's w, h as
    SENT) as PDF points on the page — the exact conversion, with no grid in
    between. ``turn`` is how far (degrees clockwise) the image was turned
    before it was sent; the box is turned back first."""
    if len(view) != 4 or len(px_box) != 4:
        raise ValueError("view and the box must each be [x0, y0, x1, y1]")
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    if vx1 <= vx0 or vy1 <= vy0:
        raise ValueError("view must be a non-empty [x0, y0, x1, y1] rect")
    if int(turn or 0) % 360:
        px_box = unturn_px_box(px_box, size, turn)
        size = unturned_size(size, turn)
    w, h = (max(float(v), 1.0) for v in size)
    x0, y0, x1, y1 = (float(v) for v in px_box)
    fx0, fx1 = sorted((min(1.0, max(0.0, x0 / w)), min(1.0, max(0.0, x1 / w))))
    fy0, fy1 = sorted((min(1.0, max(0.0, y0 / h)), min(1.0, max(0.0, y1 / h))))
    vw, vh = vx1 - vx0, vy1 - vy0
    return (vx0 + fx0 * vw, vy0 + fy0 * vh, vx0 + fx1 * vw, vy0 + fy1 * vh)


def boxes_to_grid(text: str, size: Optional[Sequence[int]],
                  tagged_only: bool = False,
                  turn: int = 0) -> Tuple[str, Dict[str, int]]:
    """A vision answer with every pixel box rewritten as a 0-999 box on the
    same image, so the agent reads ONE convention — the one
    ``render_region(view=, image_box=)`` and ``annotate_document`` take.

    A box tagged ``px=`` (or followed by ``px``) is in pixels. An untagged
    ``[a, b, c, d]`` is in pixels too when the answer shows it is answering
    in pixels — a tagged box anywhere in it, or a value past 999, which no
    grid box has (a model that forgot the tag on some boxes) — provided it
    is shaped like a pixel box on this image (whole numbers, or one past
    999; corners in order; inside the image), so a list of four readings off
    a chart is never rewritten. Without that evidence every box in the
    answer is taken as an old-style 0-999 box and left as written.
    ``tagged_only`` converts tagged boxes alone — for a chart read-off, whose
    answer is about values and may hold a bracketed list of four readings.
    ``turn``: the image was sent turned that many degrees clockwise
    (``size`` is its size as sent); each pixel box is turned back, so the
    0-999 box is on the view as the page shows it.
    A box with a page unit written right after it (``[..] pt``, ``[..] in
    PDF points``; :data:`_PAGE_UNIT_AFTER`) is left exactly as written,
    tagged or not: it is the model's own estimate in the page's units, and
    no conversion from pixels applies to it (app BACKLOG VIS-24).
    Returns the text and ``{"converted": n, "grid": m}`` — the pixel boxes
    rewritten and the untagged boxes left as grid boxes — with
    ``"page_units"``: k added when k > 0 such boxes were left."""
    counts = {"converted": 0, "grid": 0}
    if not text or not size:
        return text, counts
    w, h = (int(v) for v in size)
    found = [m for m in _BOX.finditer(text) if _is_box(m)]
    in_pixels = any(m.group("pre") or m.group("post") for m in found) or any(
        v > _GRID_MAX for m in found for v in _box_values(m))

    def pixel_shaped(vals: List[float]) -> bool:
        x0, y0, x1, y1 = vals
        if not (x1 > x0 and y1 > y0):
            return False
        if not (-0.02 * w <= x0 and x1 <= 1.02 * w
                and -0.02 * h <= y0 and y1 <= 1.02 * h):
            return False
        return (any(v > _GRID_MAX for v in vals)
                or all(float(v).is_integer() for v in vals))

    def rewrite(m) -> str:
        if not _is_box(m):
            return m.group(0)
        if _PAGE_UNIT_AFTER.match(m.string, m.end()):
            counts["page_units"] = counts.get("page_units", 0) + 1
            return m.group(0)
        vals = _box_values(m)
        tagged = bool(m.group("pre") or m.group("post"))
        if tagged or (not tagged_only and in_pixels and pixel_shaped(vals)):
            counts["converted"] += 1
            return "[{}, {}, {}, {}]".format(*px_to_grid(vals, (w, h), turn))
        counts["grid"] += 1
        return m.group(0)

    return _BOX.sub(rewrite, text), counts


def boxes_note(counts: Dict[str, int], size: Sequence[int],
               turn: int = 0, what: str = "page") -> Optional[str]:
    """What the agent is told about the boxes in an answer, or ``None`` when
    it gave none. ``what`` names the thing looked at (``page``, or
    ``image`` for an image file). Boxes left as written for the page unit
    after them (``counts["page_units"]``) are named first."""
    w, h = (int(v) for v in size)
    quarter = int(turn or 0) % 360
    units = int(counts.get("page_units") or 0)
    own = (f"{units} box(es) with a page unit written after them (such as "
           f"pt) were left as the vision call wrote them: its own estimate "
           f"in page units, not converted from pixels and not on the 0-999 "
           f"grid" if units else "")
    lead = f"{own}; " if units else ""
    other = "other " if units else ""
    if counts.get("converted"):
        left = counts.get("grid") or 0
        turned = (f" (sent turned {quarter} degrees clockwise; each box was "
                  f"turned back, so it is on this view as the {what} shows "
                  f"it)" if quarter else "")
        return (f"{lead}the {other}boxes in this analysis are on the 0-999 "
                f"grid over this view's image, converted in code from the "
                f"vision call's pixel boxes on the {w} x {h} px image it was "
                f"sent{turned}: pass one as image_box with this view"
                + (f" ({left} bracketed list(s) that were not pixel boxes "
                   f"were left as written)" if left else ""))
    if counts.get("grid"):
        return (f"{lead}the vision call gave its {other}boxes without units; "
                "they are taken as 0-999 grid boxes over this view's image "
                "— zoom before relying on one")
    return own or None


def location_error(view: Sequence[float]) -> Tuple[float, float]:
    """How far (x, y, in points) a box read off ``view`` may be from the
    thing: :data:`LOCATION_ERROR_FRAC` of the view on each axis, at least
    :data:`MIN_LOCATION_ERROR_PT`."""
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    return (max(MIN_LOCATION_ERROR_PT, LOCATION_ERROR_FRAC * (vx1 - vx0)),
            max(MIN_LOCATION_ERROR_PT, LOCATION_ERROR_FRAC * (vy1 - vy0)))


#: Said on every look's precision line, in general words that name no tool
#: (an owner decision of 2026-10-08, in the visual-scales design):
#: a box says where a thing is to look at, not where it is to the point.
NOT_A_MEASUREMENT = ("A box read off an image locates a thing; it is not a "
                     "measurement.")


def precision_note(view: Sequence[float]) -> str:
    """The line every vision result carries on how far its boxes can be
    trusted, and what that means for zooming and for placing a mark — and
    that a box is not a measurement (:data:`NOT_A_MEASUREMENT`)."""
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    vw, vh = vx1 - vx0, vy1 - vy0
    ex, ey = location_error(view)
    if max(vw, vh) <= MARK_VIEW_PT:
        return (f"a box read off this {vw:.0f} x {vh:.0f} pt view is good to "
                f"a few points: a mark may be anchored on this view + the "
                f"thing's image_box. {NOT_A_MEASUREMENT}")
    return (f"a box read off this {vw:.0f} x {vh:.0f} pt view can be up to "
            f"~{ex:.0f} x {ey:.0f} pt from the thing: good for finding where "
            f"to zoom (render_region with this view + image_box pads its "
            f"window by that much), NOT for placing a mark — zoom until the "
            f"thing is legible in a view of {MARK_VIEW_PT:.0f} pt or less and "
            f"anchor the mark on that zoom's view + image_box. "
            f"{NOT_A_MEASUREMENT}")


def zoom_window(view: Sequence[float], box: Sequence[float]
                ) -> Optional[List[float]]:
    """``box`` (PDF points, read off ``view``) padded by the view's location
    error — a ``render_region(bbox=...)`` that will hold the thing — or
    ``None`` when the view is narrow enough that the box itself can be used
    (:data:`MARK_VIEW_PT`)."""
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    if max(vx1 - vx0, vy1 - vy0) <= MARK_VIEW_PT:
        return None
    px, py = zoom_pad(view, box)
    bx0, by0, bx1, by1 = (float(v) for v in box)
    return [round(bx0 - px, 1), round(by0 - py, 1),
            round(bx1 + px, 1), round(by1 + py, 1)]


def zoom_pad(view: Sequence[float], box: Sequence[float],
             pad_frac: float = 0.15) -> Tuple[float, float]:
    """How much to pad, each side (x, y, points), a zoom on ``box`` read off
    ``view``: the location error of the SOURCE view (:func:`location_error`),
    or ``pad_frac`` of the box (at least 20 pt across, planlens' own rule)
    when that is more. A 15 % pad of a tag-sized box is a 30 x 14 pt window
    — far smaller than the error of a whole-sheet box, and the first zooms
    of 2026-10-07 came back as blank paper 11 times in 11."""
    ex, ey = location_error(view)
    bx0, by0, bx1, by1 = (float(v) for v in box)
    own = float(pad_frac) * max(bx1 - bx0, by1 - by0, 20.0)
    return max(ex, own), max(ey, own)


#: How far, as a fraction of the SOURCE view's longer side, a zoom's answer
#: may sit from the box it was aimed at before the result says the answer
#: may be about another thing. Pixel boxes off a whole 11 x 17 sheet landed
#: 1-6 pt from their tags (15-20 pt where a model boxed tag and leader
#: together; Foundry brief 4, part A), so 2.5 % — 31 pt on that sheet — is
#: past any of them. The zoom window itself is padded far wider
#: (:data:`LOCATION_ERROR_FRAC`), and in brief 4 a 259 pt window aimed at a
#: look-alike also held a tag 98 pt away, and the look answered about the
#: tag (Sol r3, the tag ringed twice).
AIM_TOLERANCE_FRAC = 0.025


def aim_tolerance(view: Sequence[float]) -> float:
    """Points a zoom's answer may sit from its aim, for a zoom on a box read
    off ``view`` (:data:`AIM_TOLERANCE_FRAC`, at least
    :data:`MIN_LOCATION_ERROR_PT`)."""
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    return max(MIN_LOCATION_ERROR_PT,
               AIM_TOLERANCE_FRAC * max(vx1 - vx0, vy1 - vy0))


#: A box as an answer reads AFTER :func:`boxes_to_grid`: four numbers in
#: square brackets, each on the 0-999 grid.
_GRID_BOX_RE = re.compile(
    rf"\[\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*,\s*({_NUM})\s*\]")

#: A box this big on the 0-999 grid (either side) is a region, not a thing.
REGION_GRID = 300


def _label_text(text: str) -> str:
    """The words before a box, cleaned for a label: markdown, list numbers,
    table bars and a trailing "at"/"px=" dropped."""
    t = re.sub(r"[*`|#>]", " ", text)
    t = re.sub(r"^\s*(?:[-•]|\d+[.)])\s+", "", t)
    t = re.sub(r"\b(?:px|box|at|in|bbox)\s*[=:]?\s*$", "", t.strip(),
               flags=re.IGNORECASE)
    t = re.sub(r"\s+", " ", t).strip(" ,;:-–—()")
    return t[:60]


def answer_boxes(text: str, view: Sequence[float]
                 ) -> List[Tuple[str, Tuple[float, float, float, float]]]:
    """``(label, page box)`` for every thing-sized 0-999 box in an answer
    on ``view`` — the label is the words just before the box on its line
    (or the line's words, when none) — in PDF points. Region-sized boxes
    (:data:`REGION_GRID`), boxes with a page unit written after them (the
    model's own page numbers, :data:`_PAGE_UNIT_AFTER`) and anything that
    is not a box are left out."""
    out = []
    for line in str(text or "").splitlines():
        prev = 0
        for m in _GRID_BOX_RE.finditer(line):
            vals = [float(v) for v in m.groups()]
            before, prev = line[prev:m.start()], m.end()
            if _PAGE_UNIT_AFTER.match(line, m.end()):
                continue
            x0, y0, x1, y1 = vals
            if not (all(0 <= v <= 999 for v in vals) and x1 > x0 and y1 > y0):
                continue
            if x1 - x0 > REGION_GRID or y1 - y0 > REGION_GRID:
                continue
            label = _label_text(before) or _label_text(
                _GRID_BOX_RE.sub(" ", line))
            try:
                out.append((label, image_box_to_page(view, vals)))
            except ValueError:
                continue
    return out



# ---------------------------------------------------------------------------
# Turned lettering (VIS-01, VIS-05) and lettering cut by a window (VIS-04)
# ---------------------------------------------------------------------------
#
# A measured review run (brief 5, 2026-10-08): one model read turned lettering markedly worse
# than upright (6 of 15 turned look-alike tags misread, 0 of 11 upright;
# sheet numbers and titles misread on sideways sheets); one whole-page
# answer gave its boxes in the frame of the sheet turned to read; and a zoom
# whose edge cut a note's lines came back with the cut words made up. Where
# the page's text layer says which way its lines run, the view is sent so
# they read upright and every box is turned back in code; a zoom is widened
# to take in whole the lines its edge cuts, and the lines it still cuts are
# named to the vision call. Where there is no text layer the image goes as
# before, and the host's general prompt sentences are all the help it gets.
# Every sheet of the brief-5 cases above is such a sheet: the public drawing
# set and the synthetic tag sets carry their lettering as strokes, with no
# text layer.

#: The turns a view's image can be sent at: degrees CLOCKWISE from the page
#: as displayed (0 = as the page shows it).
TURNS = (0, 90, 180, 270)

#: A view is turned when at least this share of its lettering (characters,
#: each line weighted by how much of it lies in the view) runs one turned
#: way ...
TURN_SHARE = 0.6
#: ... and that is at least this many characters ...
TURN_MIN_CHARS = 3
#: ... and at least this many per square inch of the view: a whole sheet is
#: not turned for a vertical stamp when the rest of its lettering is drawn
#: as strokes the text layer cannot see.
TURN_MIN_CHARS_PER_SQ_IN = 0.5
#: A line within this many degrees of a quarter turn runs that way; lettering
#: at other angles (a name along a curved road) counts against every turn.
TURN_SNAP_DEG = 10.0

#: The quality a turned JPEG is written at (planlens' own render quality).
TURN_JPEG_QUALITY = 92

#: Which way lettering runs on the page, by the turn that sets it upright.
_RUNS = {90: "from bottom to top", 180: "upside down",
         270: "from top to bottom"}

#: The image's edges in clockwise order: a quarter turn clockwise moves each
#: edge one place on.
EDGES = ("top", "right", "bottom", "left")

#: A text-layer line is cut by a view's edge when it runs at least this far
#: past it, and lies at least this far inside it (glyph boxes carry slack).
CUT_TOL_PT = 1.0
#: A zoom window is widened to take in whole the lines its edges cut, by at
#: most this fraction of its own size on each side ...
WIDEN_MAX_FRAC = 0.25
#: ... or this many points when that is more (a small window).
WIDEN_MIN_PT = 12.0
#: Paper left round a line taken in.
WIDEN_MARGIN_PT = 2.0
#: Most cut lines named to one vision call.
CUT_LINES_LISTED = 8

#: planlens' ``model.SOURCE_CAD_HIDDEN``: AutoCAD's hidden "SHX Text" — one
#: box for a whole block of rows and no direction, so it neither turns a view
#: nor counts as a cut line.
_CAD_HIDDEN = "cad_hidden_text"


class LayerLine(NamedTuple):
    """One line of the page's text layer: its text, its box (displayed
    points, top-left origin) and its reading direction (degrees
    counter-clockwise as displayed; ``None`` when not known)."""
    text: str
    bbox: Tuple[float, float, float, float]
    rotation: Optional[float]


class TextLayer(NamedTuple):
    """A page's text-layer lines and the page's displayed size in points."""
    lines: List[LayerLine]
    width: float
    height: float


def _text_layer_of(doc, page: int) -> Optional[TextLayer]:
    """:func:`page_text_layer` on an open planlens ``Document``."""
    try:
        pc = doc.page(int(page), tables=False)
        lines = []
        for ln in pc.lines:
            if getattr(ln, "source", None) == _CAD_HIDDEN:
                continue
            text = " ".join(str(ln.text or "").split())
            if not text:
                continue
            rot = getattr(ln, "rotation", None)
            lines.append(LayerLine(text, tuple(float(v) for v in ln.bbox),
                                   None if rot is None else float(rot)))
        return TextLayer(lines, float(pc.width), float(pc.height))
    except Exception:  # noqa: BLE001 - no layer is never a failed render
        return None


def page_text_layer(source, page: int) -> Optional[TextLayer]:
    """The page's text-layer lines with their boxes and reading directions,
    and its size — ``None`` when the page cannot be read. Never raises."""
    try:
        from planlens.document import Document
        doc = (Document(content=source) if isinstance(source, (bytes, bytearray))
               else Document(filepath=str(source)))
    except Exception:  # noqa: BLE001
        return None
    try:
        return _text_layer_of(doc, page)
    finally:
        try:
            doc.close()
        except Exception:  # noqa: BLE001
            pass


def _inside(bbox, clip) -> Tuple[float, float]:
    """How far (x, y, points) a box and a clip overlap; <= 0 when apart."""
    return (min(bbox[2], clip[2]) - max(bbox[0], clip[0]),
            min(bbox[3], clip[3]) - max(bbox[1], clip[1]))


def reading_turn(layer: Optional[TextLayer], clip: Sequence[float],
                 default: Optional[int] = 0) -> Optional[int]:
    """The turn (degrees clockwise, :data:`TURNS`) that sets the lettering
    in ``clip`` upright, from the text layer's own reading directions: 90
    for lines that run from bottom to top, 270 from top to bottom, 180
    upside down. 0 when the lettering already reads upright, or when no one
    way holds :data:`TURN_SHARE` of it. ``default`` when the view has too
    little lettering with a known direction to say
    (:data:`TURN_MIN_CHARS`, :data:`TURN_MIN_CHARS_PER_SQ_IN`) — ``None``
    asks to be told so."""
    if not layer or not layer.lines:
        return default
    x0, y0, x1, y1 = (float(v) for v in clip)
    weight = {t: 0.0 for t in TURNS}
    total = 0.0
    for ln in layer.lines:
        if ln.rotation is None:
            continue
        ix, iy = _inside(ln.bbox, (x0, y0, x1, y1))
        if ix <= 0 or iy <= 0:
            continue
        area = (max(ln.bbox[2] - ln.bbox[0], 1e-6)
                * max(ln.bbox[3] - ln.bbox[1], 1e-6))
        n = len(ln.text.replace(" ", "")) * min(1.0, ix * iy / area)
        total += n
        r = float(ln.rotation) % 360.0
        quarter = (int(round(r / 90.0)) % 4) * 90
        if abs((r - quarter + 180.0) % 360.0 - 180.0) <= TURN_SNAP_DEG:
            weight[quarter] += n
    best = max((90, 180, 270), key=lambda t: weight[t])
    sq_in = (x1 - x0) * (y1 - y0) / (72.0 * 72.0)
    floor = max(float(TURN_MIN_CHARS), TURN_MIN_CHARS_PER_SQ_IN * sq_in)
    if total < floor:
        return default
    if weight[best] >= floor and weight[best] >= TURN_SHARE * total:
        return best
    return 0


def turn_image(data: bytes, info: Dict[str, Any], turn: int
               ) -> Tuple[bytes, Dict[str, Any]]:
    """The image turned ``turn`` degrees clockwise (a quarter turn moves
    whole pixels: nothing is resampled), in its own format, with ``info``
    giving its size AS SENT and ``turn``. ``clip`` — the page rect the image
    shows — is unchanged. Left as it was for no turn, or an image that
    cannot be decoded."""
    quarter = int(turn or 0) % 360
    if quarter not in TURNS or not quarter:
        return data, info
    try:
        import pymupdf
        import numpy as np
        pix = pymupdf.Pixmap(data)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.width, pix.n)
        r = np.ascontiguousarray(np.rot90(a, k=-(quarter // 90)))
        out = pymupdf.Pixmap(pix.colorspace, r.shape[1], r.shape[0], r.tobytes(),
                          pix.alpha)
        fmt = str(info.get("format") or "png").lower()
        body = (out.tobytes("jpeg", jpg_quality=TURN_JPEG_QUALITY)
                if fmt == "jpeg" else out.tobytes("png"))
    except Exception:  # noqa: BLE001 - an image that will not turn goes as is
        return data, info
    turned = dict(info)
    turned["width_px"], turned["height_px"] = out.width, out.height
    turned["turn"] = quarter
    return body, turned


def turned_note(turn: int, source: Optional[str] = "text_layer",
                what: str = "page") -> str:
    """What a result says about a view sent turned ``turn`` degrees, by
    where the turn came from (``render_view``'s ``info['turn_from']``, or
    ``retry`` for a view read again after a first look found it turned).
    ``what`` names the thing looked at (``page``, or ``image`` for an image
    file)."""
    quarter = int(turn or 0) % 360
    runs = _RUNS.get(quarter, "turned")
    why = {
        "asked": "as asked",
        "remembered": (f"because earlier looks at this {what} found its "
                       f"lettering running {runs}: the vision call read it "
                       f"upright"),
        "retry": (f"because a first look, as the {what} shows it, found its "
                  f"lettering running {runs}: this is a second reading, "
                  f"turned so the lettering read upright"),
    }.get(str(source), (f"because the page's text layer shows its lettering "
                        f"running {runs}: the vision call read it upright"))
    return (f"this view's image was sent turned {quarter} degrees clockwise, "
            f"{why}. Its boxes were turned back in code, so every image_box "
            f"is on this view as the {what} shows it; words such as top or "
            f"left in the analysis describe the turned image.")


#: Asked of a look whose image goes as the page shows it, when the host
#: retries turned views (:func:`read_turn_tag` reads the line).
TURN_TAG_INSTRUCTION = (
    "If most of the lettering in this image does not read from left to "
    "right, end your answer with one line saying which way it runs: "
    "TURNED: bottom-to-top, TURNED: top-to-bottom or TURNED: upside-down.")

#: The tagged line, however a model dresses it (bold, a list mark, a full
#: stop).
_TURN_TAG = re.compile(r"^[ \t*_>`\-]*TURNED[ \t*]*:[ \t]*(.*?)[ \t*_`.]*$",
                       re.IGNORECASE | re.MULTILINE)


def _turn_for(phrase: str) -> Optional[int]:
    """The turn (degrees clockwise) that sets lettering running ``phrase``
    upright, or ``None`` for a phrase that names no turned direction."""
    p = " " + re.sub(r"[-_]", " ", str(phrase or "").lower()) + " "
    if "upside" in p:
        return 180
    b, t = p.find("bottom"), p.find(" top")
    if b >= 0 and t >= 0:
        return 90 if b < t else 270
    if " up " in p or "upward" in p:
        return 90
    if " down " in p or "downward" in p:
        return 270
    return None


def read_turn_tag(text: Any) -> Tuple[Any, Optional[int]]:
    """``(answer without its TURNED line, the turn that sets its lettering
    upright)`` for an answer to :data:`TURN_TAG_INSTRUCTION`; the turn is
    ``None`` when there is no such line or it names no turned direction (the
    line is dropped either way). The last such line counts."""
    if not isinstance(text, str) or "turned" not in text.lower():
        return text, None
    found = list(_TURN_TAG.finditer(text))
    if not found:
        return text, None
    m = found[-1]
    rest = (text[:m.start()].rstrip() + "\n" + text[m.end():].lstrip()).strip()
    return rest, _turn_for(m.group(1))


def unturned_size(size: Sequence[int], turn: int = 0) -> Tuple[int, int]:
    """(w, h) of the image as the page shows it, for one of ``size`` (w, h)
    sent turned ``turn`` degrees — and the other way round: a quarter turn
    swaps the sides."""
    w, h = (int(v) for v in size)
    return (h, w) if int(turn or 0) % 180 == 90 else (w, h)


def _ordered(box: Sequence[float]) -> Tuple[float, float, float, float]:
    x0, y0, x1, y1 = (float(v) for v in box)
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def unturn_px_box(px_box: Sequence[float], size: Sequence[int],
                  turn: int = 0) -> List[float]:
    """A pixel box on an image sent turned ``turn`` degrees clockwise
    (``size`` = its w, h AS SENT) as the same box on the image before the
    turn — the view as the page shows it. Corners ordered."""
    x0, y0, x1, y1 = _ordered(px_box)
    w, h = (float(v) for v in size)
    quarter = int(turn or 0) % 360
    if quarter == 90:
        return [y0, w - x1, y1, w - x0]
    if quarter == 180:
        return [w - x1, h - y1, w - x0, h - y0]
    if quarter == 270:
        return [h - y1, x0, h - y0, x1]
    return [x0, y0, x1, y1]


def turn_px_box(px_box: Sequence[float], size: Sequence[int],
                turn: int = 0) -> List[float]:
    """The reverse of :func:`unturn_px_box`: a pixel box on the image as the
    page shows it (``size`` = ITS w, h) as the box on that image turned
    ``turn`` degrees clockwise."""
    u0, v0, u1, v1 = _ordered(px_box)
    w, h = (float(v) for v in size)
    quarter = int(turn or 0) % 360
    if quarter == 90:
        return [h - v1, u0, h - v0, u1]
    if quarter == 180:
        return [w - u1, h - v1, w - u0, h - v0]
    if quarter == 270:
        return [v0, w - u1, v1, w - u0]
    return [u0, v0, u1, v1]


def turn_edge(edge: str, turn: int = 0) -> str:
    """Which edge of an image turned ``turn`` degrees clockwise the given
    edge of the unturned image becomes (the top goes to the right)."""
    return EDGES[(EDGES.index(edge) + int(turn or 0) // 90) % 4]


def cut_lines(clip: Sequence[float], layer: Optional[TextLayer]
              ) -> List[Tuple[LayerLine, List[str]]]:
    """Every text-layer line that ``clip``'s edges cut, with the edges
    (``top``/``right``/``bottom``/``left`` of the view as the page shows it)
    it runs past — a line partly in the view, not one wholly inside or
    wholly outside it."""
    if not layer or not layer.lines:
        return []
    x0, y0, x1, y1 = (float(v) for v in clip)
    out = []
    for ln in layer.lines:
        ix, iy = _inside(ln.bbox, (x0, y0, x1, y1))
        if ix <= CUT_TOL_PT or iy <= CUT_TOL_PT:
            continue
        bx0, by0, bx1, by1 = ln.bbox
        edges = [e for e, past in (("top", y0 - by0), ("right", bx1 - x1),
                                   ("bottom", by1 - y1), ("left", x0 - bx0))
                 if past > CUT_TOL_PT]
        if edges:
            out.append((ln, edges))
    return out


def widen_to_lines(clip: Sequence[float], layer: Optional[TextLayer]
                   ) -> Tuple[Tuple[float, float, float, float], int]:
    """``(clip, n)``: ``clip`` widened to take in whole the ``n`` text-layer
    lines its edges cut — each only when all of it lies within
    :data:`WIDEN_MAX_FRAC` of the clip's size (at least
    :data:`WIDEN_MIN_PT`) of the edge, so a zoom is never spread so wide its
    lettering shrinks away; held to the page. A window narrow enough to
    place a mark from (:data:`MARK_VIEW_PT`) stays that narrow: a line that
    would take it past is left out. One pass, nearest lines first: a line
    the wider window then cuts is named to the vision call
    (:func:`cut_note`), not chased."""
    x0, y0, x1, y1 = (float(v) for v in clip)
    lim_x = max(WIDEN_MIN_PT, WIDEN_MAX_FRAC * (x1 - x0))
    lim_y = max(WIDEN_MIN_PT, WIDEN_MAX_FRAC * (y1 - y0))
    cap = MARK_VIEW_PT if max(x1 - x0, y1 - y0) <= MARK_VIEW_PT else None
    page_w = layer.width if layer is not None and layer.width > 0 else None
    page_h = layer.height if layer is not None and layer.height > 0 else None

    def grown(box, bx0, by0, bx1, by1):
        g = (min(box[0], bx0 - WIDEN_MARGIN_PT),
             min(box[1], by0 - WIDEN_MARGIN_PT),
             max(box[2], bx1 + WIDEN_MARGIN_PT),
             max(box[3], by1 + WIDEN_MARGIN_PT))
        if page_w is not None and page_h is not None:
            g = (max(0.0, g[0]), max(0.0, g[1]), min(page_w, g[2]),
                 min(page_h, g[3]))
        return g

    def need(ln):
        bx0, by0, bx1, by1 = ln.bbox
        return max(0.0, x0 - bx0, bx1 - x1, y0 - by0, by1 - y1)

    box = (x0, y0, x1, y1)
    taken = 0
    for ln, _edges in sorted(cut_lines(box, layer), key=lambda c: need(c[0])):
        bx0, by0, bx1, by1 = ln.bbox
        if (x0 - bx0 > lim_x or bx1 - x1 > lim_x
                or y0 - by0 > lim_y or by1 - y1 > lim_y):
            continue
        wider = grown(box, bx0, by0, bx1, by1)
        if cap is not None and max(wider[2] - wider[0],
                                   wider[3] - wider[1]) > cap:
            continue
        box = wider
        taken += 1
    return box, taken


def cut_note(cuts: Sequence[Tuple[LayerLine, List[str]]],
             clip: Sequence[float], size: Optional[Sequence[int]],
             turn: int = 0) -> Optional[str]:
    """What a vision call is told about the lines its image's edges cut
    (:func:`cut_lines`): where each one's visible part is — in pixels of the
    image as sent (``size``, turned ``turn`` degrees) — and which edge cuts
    it; ``None`` when none is cut. The text itself is not given: the call
    reads what it can see and says where it stops."""
    if not cuts or not size:
        return None
    x0, y0, x1, y1 = (float(v) for v in clip)
    vw, vh = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    uw, uh = unturned_size(size, turn)
    sw, sh = (int(v) for v in size)
    rows = []
    for ln, edges in list(cuts)[:CUT_LINES_LISTED]:
        bx0, by0 = max(ln.bbox[0], x0), max(ln.bbox[1], y0)
        bx1, by1 = min(ln.bbox[2], x1), min(ln.bbox[3], y1)
        box = turn_px_box([(bx0 - x0) / vw * uw, (by0 - y0) / vh * uh,
                           (bx1 - x0) / vw * uw, (by1 - y0) / vh * uh],
                          (uw, uh), turn)
        box = [max(0, min(sw, round(box[0]))), max(0, min(sh, round(box[1]))),
               max(0, min(sw, round(box[2]))), max(0, min(sh, round(box[3])))]
        sides = " and ".join(turn_edge(e, turn) for e in edges)
        rows.append(f"px={box} (cut by the {sides} edge)")
    more = len(cuts) - len(rows)
    return (f"The page's text layer shows {len(cuts)} line(s) of lettering "
            f"running past the edge of this image: " + "; ".join(rows)
            + (f"; and {more} more" if more > 0 else "")
            + ". Quote only what you can see of each and write (cut) where it "
              "stops.")


def image_box_to_page(view: Sequence[float], image_box: Sequence[float]
                      ) -> Tuple[float, float, float, float]:
    """A 0-999 box on the image of ``view`` as PDF points on the page."""
    if len(view) != 4 or len(image_box) != 4:
        raise ValueError("view and image_box must each be [x0, y0, x1, y1]")
    vx0, vy0, vx1, vy1 = (float(v) for v in view)
    if vx1 <= vx0 or vy1 <= vy0:
        raise ValueError("view must be a non-empty [x0, y0, x1, y1] rect")
    x0, y0, x1, y1 = (min(999.0, max(0.0, float(v))) for v in image_box)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    w, h = vx1 - vx0, vy1 - vy0
    return (vx0 + x0 / 999.0 * w, vy0 + y0 / 999.0 * h,
            vx0 + x1 / 999.0 * w, vy0 + y1 / 999.0 * h)



# ---------------------------------------------------------------------------
# Tiles cut along the sheet's own regions (app BACKLOG VIS-30, first slice).
# Not wired: no tool calls region_tiles yet; tile_boxes is what callers get.
# ---------------------------------------------------------------------------

#: Under this many characters of text a page has no lettering to cut by (the
#: count :func:`tile_grid` takes a stroked sheet by): it gets the uniform grid.
REGION_MIN_CHARS = 40
#: A band free of text and linework is a gutter when it is at least this
#: many times as wide as the region's median lettering is tall.
GUTTER_LETTERINGS = 2.0
#: Drawn pieces on one straight line are a border (of a title block, a ruled
#: table, the sheet's frame) when together they run this share of the region.
RULE_SPAN = 0.9
#: A drawn piece no thicker than this (pt) across is a straight line.
RULE_THIN_PT = 1.0
#: A second-level cut (through linework, where no border or gutter is left)
#: stays this share of the region away from its ends.
SECOND_CUT_BAND = 0.25


class PageMarks(NamedTuple):
    """What a page is cut by, in displayed points: ``text`` one row per
    text-layer line (x0, y0, x1, y1, lettering height in pt), ``strokes``
    one box per drawn line or curve piece, ``shapes`` one box per drawn
    path, the page's size and its count of text characters."""
    text: Any
    strokes: Any
    shapes: Any
    width: float
    height: float
    chars: int


class RegionTiles(NamedTuple):
    """:func:`region_tiles`' answer. ``boxes`` are the tiles (top first);
    ``lettering_px`` the smallest lettering each tile holds at the size it
    is sent (``None``: no text of its own); ``level`` 1 for a tile cut only
    along borders and gutters, 2 where a cut through linework made it, 0
    for an uncut page or a grid tile; ``under`` the tiles (indices) still
    under :data:`LEGIBLE_TEXT_PX`; ``method`` ``"regions"``, ``"whole"``
    (one tile) or ``"grid"`` (:func:`tile_boxes`: no text to cut by, and
    ``lettering_px`` is then :data:`ASSUMED_CAD_LETTERING_PT`; or the
    regions left tiles under the line and the grid reads better: its
    smallest lettering is larger and its lines are on the whole no
    smaller, though its edges do cut text lines); ``capped`` when the ceiling on
    tiles stopped the cutting."""
    boxes: List[Tuple[float, float, float, float]]
    lettering_px: List[Optional[float]]
    level: List[int]
    under: List[int]
    method: str
    capped: bool


def page_marks(doc, page: int) -> PageMarks:
    """The text lines (hidden CAD text left out) and drawn lines of one page
    of an open planlens ``Document``, for :func:`region_tiles`."""
    import numpy as np
    from planlens.document.frame import to_display_bbox
    text, chars = [], 0
    for ln in doc.page(int(page), tables=False).lines:
        words = str(ln.text or "").strip()
        if not words or getattr(ln, "source", None) == _CAD_HIDDEN:
            continue
        x0, y0, x1, y1 = (float(v) for v in ln.bbox)
        text.append((x0, y0, x1, y1,
                     float(ln.size or min(x1 - x0, y1 - y0))))
        chars += len(words)
    raw = doc._doc[int(page)]
    strokes, shapes = [], []
    for path in raw.get_cdrawings():
        shapes.append(to_display_bbox(raw, path["rect"]))
        for item in path.get("items", ()):
            if item[0] == "re":
                x0, y0, x1, y1 = item[1]
                sides = [(x0, y0, x1, y0), (x0, y1, x1, y1),
                         (x0, y0, x0, y1), (x1, y0, x1, y1)]
            else:
                pts = item[1] if item[0] == "qu" else item[1:]
                xs, ys = [p[0] for p in pts], [p[1] for p in pts]
                sides = [(min(xs), min(ys), max(xs), max(ys))]
            strokes.extend(to_display_bbox(raw, s) for s in sides)
    pc = doc.summary(int(page))
    return PageMarks(np.array(text, float).reshape(-1, 5),
                     np.array(strokes, float).reshape(-1, 4),
                     np.array(shapes, float).reshape(-1, 4),
                     float(pc.width), float(pc.height), chars)


def _gaps(lo_of, hi_of, lo: float, hi: float) -> List[Tuple[float, float]]:
    """The stretches of ``lo``..``hi`` that no interval covers."""
    import numpy as np
    order = np.argsort(lo_of)
    starts = np.concatenate(([lo], np.clip(
        np.maximum.accumulate(hi_of[order]), lo, hi)))
    ends = np.concatenate((np.clip(lo_of[order], lo, hi), [hi]))
    keep = ends > starts
    return list(zip(starts[keep].tolist(), ends[keep].tolist()))


def _in(rows, box, tol: float = 0.0):
    """Which rows (boxes) overlap ``box`` by more than ``tol`` both ways."""
    return ((rows[:, 2] > box[0] + tol) & (rows[:, 0] < box[2] - tol)
            & (rows[:, 3] > box[1] + tol) & (rows[:, 1] < box[3] - tol))


def _through(pieces, a: int, lo: float, hi: float):
    """Of straight pieces lying ACROSS axis ``a`` (thin on the other axis),
    which sit on a line whose pieces together run :data:`RULE_SPAN` of
    ``lo``..``hi``; with each such line's position on the other axis."""
    import numpy as np
    b = 1 - a
    keys = np.round((pieces[:, b] + pieces[:, b + 2]) / 2.0)
    found = np.zeros(len(pieces), bool)
    at = []
    names, which = np.unique(keys, return_inverse=True)
    run = np.bincount(which, pieces[:, a + 2] - pieces[:, a],
                      minlength=len(names))
    for key in names[run >= RULE_SPAN * (hi - lo)]:
        on = keys == key
        free = sum(g1 - g0 for g0, g1 in
                   _gaps(pieces[on, a], pieces[on, a + 2], lo, hi))
        if (hi - lo) - free >= RULE_SPAN * (hi - lo):
            found |= on
            at.append(float(key))
    return found, at


def _cuts(box, text, strokes, shapes, a: int,
          second: bool) -> List[Tuple[float, float]]:
    """Where ``box`` may be cut across axis ``a`` (0: at an x), as
    ``(position, weight)``: along a border (weight 2), down the middle of a
    gutter (1) or, with ``second``, at the text-free position nearest the
    middle (off every small drawn path, where it can be) or where a :data:`SECOND_CUT_BAND` of paper with no text on it
    ends (0). No position lies on a text line; a border, a gutter and a
    middle cut each leave text on both sides."""
    import numpy as np
    b, lo, hi = 1 - a, box[a], box[a + 2]
    mine = text[_in(text, box)]
    ink = strokes[_in(strokes, box, -RULE_THIN_PT)]
    if not len(mine):
        return []
    clear = _gaps(mine[:, a], mine[:, a + 2], lo, hi)
    mids = (mine[:, a] + mine[:, a + 2]) / 2.0

    def good(c):
        return (any(g0 <= c <= g1 for g0, g1 in clear)
                and (mids < c).any() and (mids > c).any())

    if second:
        mid, edge = (lo + hi) / 2.0, SECOND_CUT_BAND * (hi - lo)
        # Clear of every drawn path that a cut in the band could miss (a
        # cloud, a short dimension line) where there is such a position.
        small = shapes[_in(shapes, box, -RULE_THIN_PT)]
        small = small[small[:, a + 2] - small[:, a] <= (hi - lo) - 2 * edge]
        for rows in (np.concatenate((mine[:, :4], small)), mine):
            spots = [min(max(mid, g0 + CUT_TOL_PT), g1 - CUT_TOL_PT)
                     for g0, g1 in _gaps(rows[:, a], rows[:, a + 2], lo, hi)
                     if g1 - g0 > 2 * CUT_TOL_PT]
            spots = [c for c in spots
                     if lo + edge <= c <= hi - edge and good(c)]
            if spots:
                break
        out = [(min(spots, key=lambda c: abs(c - mid)), 0.0)] if spots else []
        # Paper with no text on it at an end of the region is cut off too.
        if clear and clear[0][0] <= lo and clear[0][1] - lo >= edge:
            out.append((clear[0][1] - WIDEN_MARGIN_PT, 0.0))
        if clear and clear[-1][1] >= hi and hi - clear[-1][0] >= edge:
            out.append((clear[-1][0] + WIDEN_MARGIN_PT, 0.0))
        return out
    out = []
    along = (ink[:, a + 2] - ink[:, a]) <= RULE_THIN_PT     # runs along b
    across = (ink[:, b + 2] - ink[:, b]) <= RULE_THIN_PT    # runs along a
    _on, borders = _through(ink[along & ~across], b, box[b], box[b + 2])
    out += [(c, 2.0) for c in borders if lo < c < hi and good(c)]
    # A line right across the region (the frame, a table rule) is no bar to
    # a gutter; any other linework is.
    crossing = np.zeros(len(ink), bool)
    crossing[np.flatnonzero(across)] = _through(ink[across], a, lo, hi)[0]
    solid = np.concatenate((mine[:, :4], ink[~crossing]))
    wide = GUTTER_LETTERINGS * float(np.median(mine[:, 4]))
    out += [((g0 + g1) / 2.0, 1.0)
            for g0, g1 in _gaps(solid[:, a], solid[:, a + 2], lo, hi)
            if g1 - g0 >= wide and g0 > lo and g1 < hi
            and good((g0 + g1) / 2.0)]
    return out


def _with_overlap(core, clip, level: int, text, shapes, overlap: float):
    """``core`` widened into its neighbours: by ``overlap`` of its size
    where a second-level cut made it, and to take in whole each text line
    and drawn path lying on one of its cuts (within :data:`WIDEN_MAX_FRAC`
    of its size); then drawn back, never inside ``core``, so that its edges
    pass through no text line."""
    import numpy as np
    size = (core[2] - core[0], core[3] - core[1])
    inner = [abs(core[k] - clip[k]) > CUT_TOL_PT for k in range(4)]
    reach = [max(WIDEN_MIN_PT, WIDEN_MAX_FRAC * size[k % 2]) * inner[k]
             for k in range(4)]
    far = (core[0] - reach[0], core[1] - reach[1],
           core[2] + reach[2], core[3] + reach[3])
    grow = overlap * (level == 2)
    box = [max(far[0], core[0] - grow * size[0] * inner[0]),
           max(far[1], core[1] - grow * size[1] * inner[1]),
           min(far[2], core[2] + grow * size[0] * inner[2]),
           min(far[3], core[3] + grow * size[1] * inner[3])]
    things = np.concatenate((text[:, :4], shapes))
    things = things[(things[:, 0] >= far[0]) & (things[:, 1] >= far[1])
                    & (things[:, 2] <= far[2]) & (things[:, 3] <= far[3])]
    for _ in range(4):
        on = things[_in(things, box, -WIDEN_MARGIN_PT)]
        if not len(on):
            break
        wider = [max(far[0], min(box[0], on[:, 0].min() - WIDEN_MARGIN_PT)),
                 max(far[1], min(box[1], on[:, 1].min() - WIDEN_MARGIN_PT)),
                 min(far[2], max(box[2], on[:, 2].max() + WIDEN_MARGIN_PT)),
                 min(far[3], max(box[3], on[:, 3].max() + WIDEN_MARGIN_PT))]
        wider = [wider[k] if inner[k] else core[k] for k in range(4)]
        if wider == box:
            break
        box = wider
    apart = text[~_in(text, core, CUT_TOL_PT)]       # no line of the core's
    while True:
        cut = apart[_in(apart, box, CUT_TOL_PT)
                    & ((apart[:, 0] < box[0] - CUT_TOL_PT)
                       | (apart[:, 1] < box[1] - CUT_TOL_PT)
                       | (apart[:, 2] > box[2] + CUT_TOL_PT)
                       | (apart[:, 3] > box[3] + CUT_TOL_PT))]
        if not len(cut):
            return tuple(box)
        x0, y0, x1, y1 = cut[0, :4]
        if x0 >= core[2] - CUT_TOL_PT:
            box[2] = max(core[2], x0)
        elif x1 <= core[0] + CUT_TOL_PT:
            box[0] = min(core[0], x1)
        elif y0 >= core[3] - CUT_TOL_PT:
            box[3] = max(core[3], y0)
        else:
            box[1] = min(core[1], y1)


def region_tiles(marks: Optional[PageMarks], budget, clip=None,
                 max_tiles: int = MAX_TILES_PER_SIDE ** 2,
                 overlap: float = 0.08) -> RegionTiles:
    """Tiles over ``clip`` (default the whole page) cut along the sheet's
    own regions, each sent at the largest size ``budget`` (a planlens
    ``ImageBudget`` or its name) lets it be.

    The region whose lettering is smallest in its image is cut while that
    is under :data:`LEGIBLE_TEXT_PX`: first along a border (drawn pieces on
    one line right across the region) or down the middle of a gutter (a
    band with no text and no linework), whichever leaves the smaller
    lettering larger; and, where neither is left, a second level, at the
    text-free position nearest the region's middle. Cutting stops at
    ``max_tiles``. No cut and no tile edge passes through a text line; the
    tiles cover ``clip``; each overlaps its neighbours as
    :func:`_with_overlap` says. A page with under :data:`REGION_MIN_CHARS`
    characters of text (a scan, a stroked sheet; ``marks`` may be ``None``
    with a ``clip``) gets :func:`tile_grid`'s uniform :func:`tile_boxes`."""
    import numpy as np
    from planlens.document.budget import fit_size, resolve_budget
    bud = resolve_budget(budget)
    if bud is None:
        raise ValueError("region_tiles needs an image budget")
    clip = tuple(float(v) for v in
                 (clip or (0.0, 0.0, marks.width, marks.height)))

    def scale(box):                        # pixels per point, as sent
        return fit_size(box[2] - box[0], box[3] - box[1], bud)[0] / max(
            box[2] - box[0], 1e-6)

    if marks is None or marks.chars < REGION_MIN_CHARS:
        px = fit_size(clip[2] - clip[0], clip[3] - clip[1], bud)
        n = tile_grid({"clip": clip, "width_px": px[0], "height_px": px[1],
                       "text_chars": marks.chars if marks else 0})
        boxes = tile_boxes(clip, n)
        small = [round(ASSUMED_CAD_LETTERING_PT * scale(b), 1) for b in boxes]
        return RegionTiles(boxes, small, [0] * len(boxes), [
            k for k, v in enumerate(small) if v < LEGIBLE_TEXT_PX],
            "grid", False)
    text, strokes, shapes = marks.text, marks.strokes, marks.shapes

    def smallest(core, box):
        """The smallest lettering ``core`` holds, in pixels of ``box``."""
        mids = (text[:, :2] + text[:, 2:4]) / 2.0
        own = text[(mids[:, 0] >= core[0]) & (mids[:, 0] <= core[2])
                   & (mids[:, 1] >= core[1]) & (mids[:, 1] <= core[3])]
        return float(own[:, 4].min()) * scale(box) if len(own) else None

    def leaf(core, level):
        box = _with_overlap(core, clip, level, text, shapes, overlap)
        return {"core": core, "box": box, "level": level,
                "px": smallest(core, box), "stuck": False}

    def is_under(t):
        return t["px"] is not None and t["px"] < LEGIBLE_TEXT_PX

    def tiles_needed(core):
        """About how many tiles ``core`` still takes to read (1: it reads)."""
        px = smallest(core, core)
        return max(1.0, (LEGIBLE_TEXT_PX / px) ** 2) if px else 1.0

    def halves(core, cut):
        a, c, _w = cut
        one, two = list(core), list(core)
        one[a + 2] = two[a] = c
        return tuple(one), tuple(two)

    leaves = [leaf(clip, 1)]
    while True:
        todo = [t for t in leaves if is_under(t) and not t["stuck"]]
        if not todo or len(leaves) >= max(1, int(max_tiles)):
            break
        worst = min(todo, key=lambda t: t["px"])
        core = worst["core"]

        def cost(cut):
            return sum(tiles_needed(h) for h in halves(core, cut))

        # A cut along a border or gutter; a second-level cut only where
        # the region has none left.
        best = None
        for second in (False, True):
            found = [(a, c, w) for a in (0, 1)
                     for c, w in _cuts(core, text, strokes, shapes, a,
                                      second)]
            best = min(found, key=lambda cut: (cost(cut), -cut[2]),
                       default=None)
            if best:
                break
        if best is None:
            worst["stuck"] = True
            continue
        level = max(worst["level"], 1 if best[2] else 2)
        leaves.remove(worst)
        leaves += [leaf(h, level) for h in halves(core, best)]
    leaves.sort(key=lambda t: (t["core"][1], t["core"][0]))
    capped = any(is_under(t) and not t["stuck"] for t in leaves)
    # Tiles still under the line (the ceiling, or a region with no place to
    # cut): where the uniform grid of no more tiles than the ceiling reads
    # better, it is the answer: its smallest lettering is larger and its
    # lines are on the whole no smaller (each line in the tile that holds
    # it whole and shows it largest, counted at no more than the line and
    # at nothing where no tile holds it whole).
    n = min(MAX_TILES_PER_SIDE, int(max(1, int(max_tiles)) ** 0.5))
    mids = (text[:, :2] + text[:, 2:4]) / 2.0
    mine = np.zeros(len(text))
    for t in leaves:
        c = t["core"]
        own = ((mids[:, 0] >= c[0]) & (mids[:, 0] <= c[2])
               & (mids[:, 1] >= c[1]) & (mids[:, 1] <= c[3]))
        mine[own] = text[own, 4] * scale(t["box"])
    if n > 1 and any(is_under(t) for t in leaves) and (mine > 0).any():
        boxes = tile_boxes(clip, n)
        best, small = np.zeros(len(text)), []
        for b in boxes:
            px = np.where((text[:, 0] >= b[0]) & (text[:, 1] >= b[1])
                          & (text[:, 2] <= b[2]) & (text[:, 3] <= b[3]),
                          text[:, 4] * scale(b), 0.0)
            small.append(round(float(px[px > 0].min()), 1)
                         if (px > 0).any() else None)
            best = np.maximum(best, px)
        if ((best > 0).any() and best[best > 0].min() > mine[mine > 0].min()
                and np.minimum(best, LEGIBLE_TEXT_PX).sum()
                >= np.minimum(mine, LEGIBLE_TEXT_PX).sum()):
            return RegionTiles(boxes, small, [0] * len(boxes), [
                k for k, v in enumerate(small)
                if v is not None and v < LEGIBLE_TEXT_PX], "grid", capped)
    whole = len(leaves) == 1
    return RegionTiles(
        [t["box"] for t in leaves],
        [None if t["px"] is None else round(t["px"], 1) for t in leaves],
        [0 if whole else t["level"] for t in leaves],
        [k for k, t in enumerate(leaves) if is_under(t)],
        "whole" if whole else "regions", capped)


__all__ = ["LEGIBLE_TEXT_PX", "TARGET_TEXT_PX", "LOCATION_ERROR_FRAC",
           "MIN_LOCATION_ERROR_PT", "MARK_VIEW_PT",
           "ASSUMED_CAD_LETTERING_PT", "MAX_TILES_PER_SIDE", "tile_grid",
           "tile_boxes", "px_to_grid", "px_box_to_page", "boxes_to_grid",
           "boxes_note", "location_error", "NOT_A_MEASUREMENT",
           "precision_note", "zoom_window", "zoom_pad",
           "AIM_TOLERANCE_FRAC", "aim_tolerance", "REGION_GRID",
           "answer_boxes", "TURNS", "TURN_SHARE", "TURN_MIN_CHARS",
           "TURN_MIN_CHARS_PER_SQ_IN", "TURN_SNAP_DEG", "TURN_JPEG_QUALITY",
           "EDGES", "CUT_TOL_PT", "WIDEN_MAX_FRAC", "WIDEN_MIN_PT",
           "WIDEN_MARGIN_PT", "CUT_LINES_LISTED", "LayerLine", "TextLayer",
           "page_text_layer", "reading_turn", "turn_image", "turned_note",
           "TURN_TAG_INSTRUCTION", "read_turn_tag", "unturned_size",
           "unturn_px_box", "turn_px_box", "turn_edge", "cut_lines",
           "widen_to_lines", "cut_note", "image_box_to_page",
           "REGION_MIN_CHARS", "GUTTER_LETTERINGS", "RULE_SPAN", "RULE_THIN_PT",
           "SECOND_CUT_BAND", "PageMarks", "RegionTiles", "page_marks",
           "region_tiles"]
