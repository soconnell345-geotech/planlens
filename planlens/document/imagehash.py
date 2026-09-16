"""What a page LOOKS like, in 256 bits — so scans can be compared.

The duplicate rule in :mod:`planlens.document.structure` reads a page's
characters and its path count, which is exactly the evidence a scanned page
does not have: two scans of the same sheet both hash as "no text, no paths"
and neither is a duplicate of anything. A reviewer meanwhile has the
document open in a viewer and can see at a glance that page 41 is page 12
again.

This module gives the same evidence in numbers. A page renders to a small
grayscale grid — in the DISPLAYED orientation, like everything else in this
package — and each pixel is compared with its right-hand neighbour: a
**difference hash**. It records where the picture gets lighter and darker
across the page rather than the absolute greys, and two genuinely different
sheets disagree in many of the 256 bits.

**The grid is 17x16 because a coarser one cannot read a FORM.** An appendix
of laboratory sheets — one printed template, a light diagonal watermark, and
the numbers that differ from sheet to sheet occupying a few percent of the
ink — is the hard case, and at 8x8 it defeats the hash outright: over a
73-page appendix of sheets that are all different, 1,260 of the 2,628 pairs
came within 2 bits of each other and the closest sat at 0. The same pages at
16x16 are never closer than 5. Normalising the ink first (thresholding the
render so the watermark and the paper tone drop out) was tried at 8x8 and is
WORSE — 1,763 pairs within 2 — because the template's own rules and boxes
survive the threshold and the data does not. Resolution, not contrast, is
what tells two filled-in forms apart. The measurements are in DESIGN.md,
"Duplicates on scans".

**What it does NOT do, measured rather than assumed**: recognise a page that
was printed and scanned AGAIN. A copy re-encoded through another resolution
and JPEG quality drifts as far from its original as two different pages of
one template sit from each other, at 64 bits and at 256. So the claim this
rule makes is the narrow one — the same page PLACED twice, which is how a
duplicate reaches an assembled submittal: an appendix bound in twice, one
scan inserted under two tabs, a sheet repeated in a set. A page placed twice
renders identically and is 0 bits away at any grid size.

The whole thing is numpy and PyMuPDF. There is no ``imagehash``, ``Pillow``
or ``scipy`` dependency and there deliberately will not be one: the
computation is a render, a subtraction and a bit-pack.

**The resize is MuPDF's**, done by rendering through a matrix straight to the
17x16 target, never by rendering a big page and scaling it down here. A
full-size render of a D-size sheet is the expensive part of this package's
whole page map; a 272-pixel one is not.

Annotations are left OUT of the render (``annots=False``). A reviewer's cloud
on one copy of a sheet must not hide that it is the same sheet — the markups
are reported separately, and it is the page underneath that is or is not a
repeat.
"""

from __future__ import annotations

from typing import Optional

#: Rows (and bit-columns) of the grid the hash is read off. The render is
#: ``HASH_SIDE + 1`` pixels wide: comparing each pixel with its right-hand
#: neighbour turns a row of 17 into 16 bits, so the hash is 16x16 = 256 bits
#: — 64 hex characters. MEASURED, see the module docstring: 8x8 cannot tell
#: two filled-in copies of one printed form apart and 16x16 can.
HASH_SIDE = 16

#: Page kinds whose text cannot settle whether two pages are the same: a scan
#: has no text layer at all, a figure and a drawing sheet carry labels rather
#: than prose.
IMAGE_HASH_KINDS = ("scanned", "figure", "drawing_sheet")

#: Below this many text-layer characters a page's text is not evidence of
#: anything — two different mostly-graphic pages can both hash to a footer.
IMAGE_HASH_MAX_CHARS = 50

#: Grey levels (of 255) two neighbouring cells must differ by before the bit
#: between them is read as a picture rather than as noise. A cover sheet or an
#: appendix divider is white paper carrying a rule and two lines of type: the
#: cells either side of almost every bit hold the same white, and which way
#: the bit falls is decided by rounding.
INK_CONTRAST = 16

#: Least number of such confident bits (of 256) before the hash is evidence at
#: all. A near-blank page hashes to almost-nothing — and so does the NEXT
#: near-blank page, which made "Appendix D" and "Appendix E" the same picture
#: at a distance of 0.
#:
#: MEASURED over 198 pages of five real documents and ten public sheets: the
#: near-blank pages (dividers, cover sheets, a slip-sheet) score 0, 1, 2, 2,
#: 2, 2, 2, 2, 3, 4, 4 and 9; every page carrying a figure, a form or a scan
#: of one scores 10 or more. The floor sits in the gap.
#:
#: This replaces the older whole-grid grey RANGE floor, which a single printed
#: border defeats: a blank page inside a rule spans the full range and says
#: nothing.
MIN_CONFIDENT_BITS = 8

#: Longest Hamming distance (of 256 bits) at which two pages of the SAME kind
#: and the SAME displayed size are called the same picture.
#:
#: MEASURED, not chosen — see "Duplicates on scans" in DESIGN.md. A page
#: placed twice renders identically and is 0 bits away; the closest pair of
#: pages that are genuinely DIFFERENT, over five real documents and ten public
#: sheets, is 5 bits (two laboratory sheets off one template). 2 leaves two
#: bits of margin below and three above. It is deliberately tight: a
#: wrongly-claimed duplicate tells a reviewer to skip a page they have not
#: read, while a missed one only leaves the map as blind as it was before this
#: existed.
DUP_HASH_DISTANCE = 2


def wants_image_hash(kind: str, n_text_chars: int,
                     needs_ocr: bool = False) -> bool:
    """Whether this page needs a picture hash — i.e. its text cannot decide.

    ``blank`` pages never get one: every blank page in a document looks like
    every other, and calling page 40 a duplicate of page 2 because both are
    empty is noise in the one field that tells a reviewer to skip a page.
    """
    if kind == "blank":
        return False
    return (bool(needs_ocr) or kind in IMAGE_HASH_KINDS
            or n_text_chars < IMAGE_HASH_MAX_CHARS)


def _gray_grid(page, width: int, height: int):
    """``height x width`` grayscale pixels of the page as DISPLAYED, or None.

    MuPDF does the scaling: the matrix maps the page rect onto the target
    grid, so the renderer never builds a large pixmap. Rounding can still hand
    back a grid one pixel out (a page whose rect does not start at the origin),
    so the result is sampled to the exact shape when that happens.
    """
    import fitz
    import numpy as np
    rect = page.rect
    if rect.is_empty or rect.width <= 0 or rect.height <= 0:
        return None
    matrix = fitz.Matrix(width / rect.width, height / rect.height)
    try:
        pix = page.get_pixmap(matrix=matrix, colorspace=fitz.csGRAY,
                              alpha=False, annots=False)
    except Exception:       # pragma: no cover - a page MuPDF cannot render
        return None
    if pix.n != 1 or pix.width < 2 or pix.height < 1:
        return None         # pragma: no cover - defensive
    buf = np.frombuffer(pix.samples, dtype=np.uint8)
    if buf.size == pix.height * pix.stride:
        grid = buf.reshape(pix.height, pix.stride)[:, :pix.width]
    elif buf.size == pix.height * pix.width:
        grid = buf.reshape(pix.height, pix.width)
    else:                   # pragma: no cover - defensive
        return None
    if grid.shape != (height, width):
        rows = np.arange(height) * grid.shape[0] // height
        cols = np.arange(width) * grid.shape[1] // width
        grid = grid[np.ix_(rows, cols)]
    return grid


def confident_bits(grid) -> int:
    """How many of the grid's bits were set by real contrast, not by noise.

    One per neighbouring pair whose grey levels differ by more than
    :data:`INK_CONTRAST`. This is what separates a page with a picture on it
    from a page of paper — and unlike the grey RANGE of the whole grid, a
    single printed border cannot fake it.
    """
    import numpy as np
    g = grid.astype(np.int16)
    return int((np.abs(g[:, 1:] - g[:, :-1]) > INK_CONTRAST).sum())


def page_dhash(page, side: int = HASH_SIDE,
               guard: bool = True) -> Optional[str]:
    """The page's difference hash as ``side * side`` bits in hex, or None.

    ``page`` is a PyMuPDF page; the render honours its ``/Rotate``, so the
    hash describes what a viewer shows. ``None`` means the page could not be
    rendered, or carries too little picture to hash (see
    :data:`MIN_CONFIDENT_BITS`) — a hash is evidence, and no evidence must not
    read as a match. ``guard=False`` returns the hash of a blank page anyway,
    for measurement.

    :data:`MIN_CONFIDENT_BITS` was measured at :data:`HASH_SIDE`; a caller
    passing a different ``side`` for measurement should pass ``guard=False``
    and apply its own floor.
    """
    import numpy as np
    side = int(side)
    if side < 2:
        raise ValueError("side must be at least 2")
    grid = _gray_grid(page, side + 1, side)
    if grid is None:
        return None
    if guard and confident_bits(grid) < MIN_CONFIDENT_BITS:
        return None
    bits = grid[:, 1:] > grid[:, :-1]
    return np.packbits(bits.reshape(-1)).tobytes().hex()


def hamming(a: str, b: str) -> int:
    """Bits that differ between two hashes (0 = the same picture)."""
    if len(a) != len(b):
        raise ValueError(
            f"hashes of different lengths cannot be compared: {len(a)} vs "
            f"{len(b)} hex characters")
    return bin(int(a, 16) ^ int(b, 16)).count("1")
