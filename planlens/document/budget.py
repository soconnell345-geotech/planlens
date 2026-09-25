"""Image budgets: the size of picture a vision model actually looks at.

A vision model does not look at the image it is sent. It first shrinks it to
fit its own limits: a longest side, and a count of the square patches (or, on
older models, tiles) it cuts the picture into. Detail smaller than a patch is
gone before the model starts. Two things follow, and both are this module:

* **Render to the budget, not past it.** A page rendered bigger than the budget
  is shrunk by the server, so the pixels the model talks about are not the
  pixels we hold. Rendered to exactly the largest size the budget allows, the
  image is looked at as sent: a box the model reads off it maps back onto the
  page with no guesswork (:func:`image_box_to_page`).
* **Fill the budget when zooming.** A region is re-rendered from the PDF — not
  cropped from a bitmap — at whatever dpi makes it fill the budget, so a small
  detail gets every patch the model will spend on one image.

The published limits (checked 2026-09-23 against OpenAI's "Images and vision"
guide and Anthropic's zoom-tool cookbook):

``openai-high``      GPT-5.4 / 5.5 / 5.6 at ``detail="high"`` (GPT-5.4's
                     ``auto`` too): 2048 px longest side, 2,500 patches of
                     32 px.
``openai-original``  GPT-5.4 / 5.5 at ``detail="original"`` (GPT-5.5's
                     ``auto`` too): 6000 px, 10,000 patches of 32 px. GPT-5.6
                     keeps larger images at ``original``; this budget is the
                     sensible ceiling on what one image should cost.
``gpt-5.2-high``     GPT-5.2 / GPT-4.1-mini (any detail): 2048 px, 6,144
                     patches of 32 px.
``gpt-4.1-high``     GPT-4.1 / GPT-4o / GPT-5.1 at ``high``: fit 2048 px, then
                     the SHORTEST side to 768 px (512 px tiles). GPT-5.1
                     ACCEPTS ``detail="original"`` and ignores it (measured
                     2026-09-24: the same tokens as ``high``).
``claude``           Claude, standard tier: 1568 px, 1,568 patches of 28 px.
``claude-hires``     Claude, high-resolution tier: 2576 px, 4,784 patches.

Which budget a deployment needs is a fact about the MODEL behind it, and a
deployment name is an alias that can be re-pointed at another model without
notice. :func:`budget_for_model` reads the model a response says answered;
:func:`budget_from_probe` goes further and reads the budget off the token
counts of three blank test images, so it needs no table and works on a model
it has never heard of.

Each budget also names the box convention its family is advised to use:
OpenAI recommends a 0-999 grid with a top-left origin (``norm1000``);
Anthropic recommends pixels of the image as seen (``px``). Both map back
through :func:`image_box_to_page`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple, Union

#: Box conventions a model may read a location off an image in.
BOX_UNITS = ("px", "norm1000")


@dataclass(frozen=True)
class ImageBudget:
    """The largest picture one model looks at without shrinking it.

    ``max_edge`` caps the longest side; ``max_units`` caps the count of
    ``unit``-pixel square patches (``None`` for a tile model, whose cap is
    ``short_side`` on the shortest side instead). ``detail`` is the value a
    host sends in the image block's ``detail`` field for this budget to hold
    (``None`` when the provider has no such field). ``box_units`` is the box
    convention the family is advised to answer locations in.
    """

    name: str
    max_edge: int
    unit: int = 32
    max_units: Optional[int] = None
    short_side: Optional[int] = None
    detail: Optional[str] = None
    box_units: str = "px"

    def units(self, width: int, height: int) -> int:
        """Patches an image of this size costs (0 for a tile model)."""
        if self.max_units is None:
            return 0
        return math.ceil(width / self.unit) * math.ceil(height / self.unit)

    def fits(self, width: int, height: int) -> bool:
        """True when the model looks at a ``width`` x ``height`` image as sent."""
        if max(width, height) > self.max_edge:
            return False
        if self.short_side is not None and min(width, height) > self.short_side:
            return False
        if self.max_units is not None and self.units(width, height) > self.max_units:
            return False
        return True

    @property
    def max_pixels(self) -> int:
        """An upper bound on the area of any image that fits."""
        if self.max_units is not None:
            return self.max_units * self.unit * self.unit
        side = self.short_side or self.max_edge
        return self.max_edge * side


BUDGETS: Dict[str, ImageBudget] = {b.name: b for b in (
    ImageBudget("openai-high", max_edge=2048, unit=32, max_units=2500,
                detail="high", box_units="norm1000"),
    ImageBudget("openai-original", max_edge=6000, unit=32, max_units=10000,
                detail="original", box_units="norm1000"),
    ImageBudget("gpt-5.2-high", max_edge=2048, unit=32, max_units=6144,
                detail="high", box_units="norm1000"),
    ImageBudget("gpt-4.1-high", max_edge=2048, short_side=768,
                detail="high", box_units="px"),
    ImageBudget("claude", max_edge=1568, unit=28, max_units=1568,
                box_units="px"),
    ImageBudget("claude-hires", max_edge=2576, unit=28, max_units=4784,
                box_units="px"),
)}


def resolve_budget(budget: Union[None, str, ImageBudget]) -> Optional[ImageBudget]:
    """An :class:`ImageBudget` from a name in :data:`BUDGETS`, or ``None``."""
    if budget is None or isinstance(budget, ImageBudget):
        return budget
    try:
        return BUDGETS[str(budget)]
    except KeyError:
        raise ValueError(f"unknown image budget {budget!r}; "
                         f"known: {', '.join(sorted(BUDGETS))}") from None


#: Model-name prefixes, most specific first, and the budgets they take:
#: (for any image, for a detail-bound one). From OpenAI's "Images and vision"
#: guide and Anthropic's vision docs, 2026-09-23/24. A first guess only —
#: :func:`budget_from_probe` measures instead of trusting a name.
MODEL_BUDGETS: Tuple[Tuple[str, str, str], ...] = (
    ("gpt-6", "openai-high", "openai-original"),
    ("gpt-5.6", "openai-high", "openai-original"),
    ("gpt-5.5", "openai-high", "openai-original"),
    ("gpt-5.4", "openai-high", "openai-original"),
    ("gpt-5.2", "gpt-5.2-high", "gpt-5.2-high"),
    ("gpt-4.1-mini", "gpt-5.2-high", "gpt-5.2-high"),
    ("gpt-5.1", "gpt-4.1-high", "gpt-4.1-high"),
    ("gpt-5-", "gpt-4.1-high", "gpt-4.1-high"),
    ("gpt-4.1", "gpt-4.1-high", "gpt-4.1-high"),
    ("gpt-4o", "gpt-4.1-high", "gpt-4.1-high"),
    ("claude-fable", "claude-hires", "claude-hires"),
    ("claude-opus-5", "claude-hires", "claude-hires"),
    ("claude-sonnet-5", "claude-hires", "claude-hires"),
    ("claude", "claude", "claude"),
)


def budget_for_model(model: Optional[str]) -> Optional[Tuple[ImageBudget, ImageBudget]]:
    """``(any image, detail-bound image)`` budgets for a model name, or ``None``.

    ``model`` is what a response says answered (``gpt-5.1-2025-11-13``), not
    a deployment alias — an alias says nothing about the model behind it.
    """
    name = (model or "").strip().lower()
    for prefix, general, detailed in MODEL_BUDGETS:
        if name.startswith(prefix):
            return BUDGETS[general], BUDGETS[detailed]
    return None


#: The two square test images :func:`budget_from_probe` reasons about.
PROBE_SMALL_PX = 1024
PROBE_LARGE_PX = 2048


def budget_from_probe(small_high: float, large_high: float,
                      large_original: Optional[float] = None
                      ) -> Tuple[ImageBudget, ImageBudget, Dict[str, float]]:
    """Read a deployment's image budget off what three blank images cost.

    Send a :data:`PROBE_SMALL_PX` and a :data:`PROBE_LARGE_PX` square image at
    ``detail="high"`` and the large one again at ``"original"``, and pass the
    IMAGE tokens each cost (the call's input tokens less a text-only call's).
    Ratios cancel whatever per-token multiplier a model applies:

    * a TILE model shrinks both squares to 768 px — ratio about 1;
    * a 2,500-patch model caps the large one (1,024 → 2,500 patches) — 2.4;
    * a 6,144-patch model takes it whole (1,024 → 4,096) — 4.0;
    * ``original`` honoured costs clearly more than ``high`` on the large
      image — on a patch model AND on a deployment whose ``high`` is capped
      like a tile model (Funhouse GPT-5.4); ignored (GPT-5.1) or unsupported
      costs the same.

    Returns ``(any image, detail-bound image, the ratios)``.
    """
    if not small_high or small_high <= 0 or large_high is None:
        raise ValueError("need positive image-token counts for both squares")
    r_high = float(large_high) / float(small_high)
    ratios = {"high": round(r_high, 3)}
    if r_high < 1.3:
        general = BUDGETS["gpt-4.1-high"]
    elif r_high < 3.2:
        general = BUDGETS["openai-high"]
    else:
        general = BUDGETS["gpt-5.2-high"]
    detailed = general
    if large_original is not None and large_original > 0:
        r_orig = float(large_original) / float(small_high)
        ratios["original"] = round(r_orig, 3)
        # ``original`` honoured = the large square costs clearly more than at
        # ``high`` — WHATEVER ``high`` does. Funhouse's GPT-5.4 (measured
        # 2026-09-25: 714 / 714 / 4,234) caps ``high`` like a tile model yet
        # keeps the whole 2048 px at ``original``; 0.9.0 only looked for
        # ``original`` on a patch-model ``high`` and so sent that deployment
        # 768 px images when 2,560 were there for the asking.
        if r_orig >= 1.3 * r_high:
            detailed = BUDGETS["openai-original"]
    return general, detailed, ratios


def legible_window(text_pt: float, budget: ImageBudget,
                   min_px: float = 14.0) -> float:
    """The side, in points, of the largest square window in which lettering
    ``text_pt`` tall still arrives ``min_px`` tall under ``budget``."""
    if text_pt <= 0:
        raise ValueError("text_pt must be positive")
    side_px = fit_size(1, 1, budget)[0]
    return side_px * float(text_pt) / float(min_px)


def fit_size(width: float, height: float, budget: ImageBudget) -> Tuple[int, int]:
    """The largest whole-pixel size with this aspect ratio that ``budget`` fits.

    Larger OR smaller than ``width`` x ``height``: a render from a PDF can be
    made at any size, so the answer is the budget's, not the source's.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    aspect = width / height
    long_is_w = width >= height
    scale = budget.max_edge / max(width, height)
    if budget.short_side is not None:
        scale = min(scale, budget.short_side / min(width, height))
    if budget.max_units is not None:
        scale = min(scale, math.sqrt(budget.max_units * budget.unit ** 2
                                     / (width * height)))
    # Start a pixel ABOVE the estimate: floating point can put the exact
    # answer (e.g. a 768 px short side) a hair over it.
    long_side = int(math.ceil(max(width, height) * scale)) + 1

    def size(long_px: int) -> Tuple[int, int]:
        short_px = max(1, int(math.floor(long_px / aspect if long_is_w
                                         else long_px * aspect)))
        return (long_px, short_px) if long_is_w else (short_px, long_px)

    # Whole patches round UP, so the area estimate can overshoot by a row or a
    # column; step down a pixel at a time until the patch count fits.
    while long_side > 1 and not budget.fits(*size(long_side)):
        long_side -= 1
    return size(long_side)


def image_box_to_page(box: Sequence[float], clip: Sequence[float],
                      width_px: int, height_px: int,
                      units: str = "px") -> Tuple[float, float, float, float]:
    """Map a box read off a rendered image back to PDF points on its page.

    ``clip`` is the displayed-frame rect the image shows (``info["clip"]`` of
    :meth:`~planlens.document.Document.render`) and ``width_px`` /
    ``height_px`` its size as sent. ``units`` is ``"px"`` (pixels of that
    image, top-left origin) or ``"norm1000"`` (a 0-999 grid over it, top-left
    origin — 999 is the far edge). The result is in the displayed frame, the
    frame every other planlens box uses.
    """
    if units not in BOX_UNITS:
        raise ValueError(f"units must be one of {BOX_UNITS}, got {units!r}")
    if len(box) != 4:
        raise ValueError("box must be [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (float(v) for v in box)
    x0, x1 = min(x0, x1), max(x0, x1)
    y0, y1 = min(y0, y1), max(y0, y1)
    if units == "norm1000":
        fx0, fx1, fy0, fy1 = x0 / 999.0, x1 / 999.0, y0 / 999.0, y1 / 999.0
    else:
        fx0, fx1 = x0 / float(width_px), x1 / float(width_px)
        fy0, fy1 = y0 / float(height_px), y1 / float(height_px)
    fx0, fx1, fy0, fy1 = (min(1.0, max(0.0, f)) for f in (fx0, fx1, fy0, fy1))
    cx0, cy0, cx1, cy1 = (float(v) for v in clip)
    cw, ch = cx1 - cx0, cy1 - cy0
    return (cx0 + fx0 * cw, cy0 + fy0 * ch, cx0 + fx1 * cw, cy0 + fy1 * ch)


__all__ = ["ImageBudget", "BUDGETS", "BOX_UNITS", "MODEL_BUDGETS",
           "PROBE_SMALL_PX", "PROBE_LARGE_PX", "resolve_budget",
           "budget_for_model", "budget_from_probe", "legible_window",
           "fit_size", "image_box_to_page"]
