"""What a page LOOKS like, in 64 bits — so scans can be compared.

The duplicate rule in :mod:`planlens.document.structure` reads a page's
characters and its path count, which is exactly the evidence a scanned page
does not have: two scans of the same sheet both hash as "no text, no paths"
and neither is a duplicate of anything. A reviewer meanwhile has the
document open in a viewer and can see at a glance that page 41 is page 12
again.

This module gives the same evidence in numbers. A page renders to a tiny
grayscale grid — in the DISPLAYED orientation, like everything else in this
package — and each pixel is compared with its right-hand neighbour: a
**difference hash**. It records where the picture gets lighter and darker
across the page rather than the absolute greys, and two genuinely different
sheets disagree in most of the 64 bits.

**What it does NOT do, measured rather than assumed**: recognise a page that
was printed and scanned AGAIN. A copy re-encoded through another resolution
and JPEG quality drifts as far from its original as two different pages of
one template sit from each other, at 64 bits and at 256 (the measurements are
in DESIGN.md, "Duplicates on scans"). So the claim this rule makes is the
narrow one — the same page PLACED twice, which is how a duplicate reaches an
assembled submittal: an appendix bound in twice, one scan inserted under two
tabs, a sheet repeated in a set.

The whole thing is numpy and PyMuPDF. There is no ``imagehash``, ``Pillow``
or ``scipy`` dependency and there deliberately will not be one: the
computation is a render, a subtraction and a bit-pack.

**The resize is MuPDF's**, done by rendering through a matrix straight to the
9x8 target, never by rendering a big page and scaling it down here. A
full-size render of a D-size sheet is the expensive part of this package's
whole page map; a 72-pixel one is not.

Annotations are left OUT of the render (``annots=False``). A reviewer's cloud
on one copy of a sheet must not hide that it is the same sheet — the markups
are reported separately, and it is the page underneath that is or is not a
repeat.
"""

from __future__ import annotations

from typing import Optional

#: Rows (and bit-columns) of the grid the hash is read off. The render is
#: ``HASH_SIDE + 1`` pixels wide: comparing each pixel with its right-hand
#: neighbour turns a row of 9 into 8 bits, so the hash is 8x8 = 64 bits — 16
#: hex characters.
HASH_SIDE = 8

#: Page kinds whose text cannot settle whether two pages are the same: a scan
#: has no text layer at all, a figure and a drawing sheet carry labels rather
#: than prose.
IMAGE_HASH_KINDS = ("scanned", "figure", "drawing_sheet")

#: Below this many text-layer characters a page's text is not evidence of
#: anything — two different mostly-graphic pages can both hash to a footer.
IMAGE_HASH_MAX_CHARS = 50

#: Least grey range (of 255) the grid must span before its bits mean
#: anything. A page of one flat tone compares every pixel with an equal
#: neighbour and hashes to all zeros — and so does the NEXT flat page, which
#: would make the two "the same picture" at any threshold. MEASURED: on a real
#: submittal every page the gate lets through spans at least 67, every
#: near-uniform page at most 19, and three different near-blank pages built by
#: hand all hashed to zero. The floor sits between those, at an eighth of the
#: range.
MIN_GRID_SPREAD = 32

#: Longest Hamming distance (of 64 bits) at which two pages of the SAME kind
#: and the SAME displayed size are called the same picture.
#:
#: MEASURED, not chosen — see "Duplicates on scans" in DESIGN.md. A page
#: placed twice renders identically and is 0 bits away; the closest pair of
#: pages that are genuinely DIFFERENT, on a real 260-page submittal, is 4 bits
#: (two figures drawn off one template). 2 is the midpoint, two bits of margin
#: each way. It is deliberately tight: a wrongly-claimed duplicate tells a
#: reviewer to skip a page they have not read, while a missed one only leaves
#: the map as blind as it was before this existed.
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


def page_dhash(page, side: int = HASH_SIDE,
               guard: bool = True) -> Optional[str]:
    """The page's difference hash as ``side * side`` bits in hex, or None.

    ``page`` is a PyMuPDF page; the render honours its ``/Rotate``, so the
    hash describes what a viewer shows. ``None`` means the page could not be
    rendered, or carries no picture to hash (see :data:`MIN_GRID_SPREAD`) — a
    hash is evidence, and no evidence must not read as a match. ``guard=False``
    returns the hash of a flat page anyway, for measurement.
    """
    import numpy as np
    side = int(side)
    if side < 2:
        raise ValueError("side must be at least 2")
    grid = _gray_grid(page, side + 1, side)
    if grid is None:
        return None
    if guard and int(grid.max()) - int(grid.min()) < MIN_GRID_SPREAD:
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
