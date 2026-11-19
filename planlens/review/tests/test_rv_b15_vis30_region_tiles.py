"""Review of the region tiles (app BACKLOG VIS-30): where cutting along the
regions ends with tiles under the legibility line and the uniform grid reads
better, ``region_tiles`` returns the uniform grid and says so. Made-up
sheets only, none of them the builder's."""

import random

import numpy as np
import pytest

from planlens.document import Document
from planlens.document.budget import fit_size, resolve_budget
from planlens.review import frames
from planlens.testing.esize_fixtures import build_esize_sheet

BUDGET = "openai-high"
_WORDS = "PROVIDE VERIFY FIELD ANCHOR SPACING GROUT LEVEL TYPICAL".split()


def _sheet(width, height, lines, seed, size=4.3, words=7, pitch=None):
    """A sheet of ``lines`` text lines at made-up positions (or, with
    ``pitch``, one under the other from the top), and what cuts it."""
    import pymupdf
    rng = random.Random(seed)
    pdf = pymupdf.open()
    page = pdf.new_page(width=width, height=height)
    page.draw_rect((30, 30, width - 30, height - 30), width=2)
    for k in range(lines):
        at = ((40, 40 + k * pitch) if pitch else
              (40 + rng.random() * (width - 80 - words * 6 * size),
               50 + rng.random() * (height - 100)))
        page.insert_text(at, " ".join(rng.choice(_WORDS)
                                      for _ in range(words)),
                         fontsize=size, fontname="helv")
    doc = Document(content=pdf.tobytes())
    pdf.close()
    try:
        return frames.page_marks(doc, 0)
    finally:
        doc.close()


def _read(boxes, marks):
    """``(smallest lettering in px, the lines' heights in px summed, each
    at no more than the line)``, each line in the tile that holds it whole
    and shows it largest (0 where none does)."""
    bud, text = resolve_budget(BUDGET), marks.text
    best = np.zeros(len(text))
    for b in boxes:
        whole = ((text[:, 0] >= b[0]) & (text[:, 1] >= b[1])
                 & (text[:, 2] <= b[2]) & (text[:, 3] <= b[3]))
        px = text[:, 4] * fit_size(b[2] - b[0], b[3] - b[1], bud)[0] / (
            b[2] - b[0])
        best = np.where(whole, np.maximum(best, px), best)
    return (float(best[best > 0].min()) if (best > 0).any() else None,
            float(np.minimum(best, frames.LEGIBLE_TEXT_PX).sum()))


#: One D-size plan with notes all through it and no gutter; an E-size sheet
#: so full of notes that no position is free of text.
CROWDED = {"plan": (2448.0, 1584.0, 260, 11),
           "full": (3456.0, 2592.0, 1500, 12)}


@pytest.mark.parametrize("name", sorted(CROWDED))
def test_a_sheet_the_regions_read_worse_than_the_grid_gets_the_grid(name):
    marks = _sheet(*CROWDED[name])
    grid = frames.tile_boxes((0.0, 0.0, marks.width, marks.height), 4)
    tiles = frames.region_tiles(marks, BUDGET)
    assert tiles.method == "grid" and tiles.boxes == grid
    assert tiles.level == [0] * 16
    # Each tile says what it shows, and which are still under the line.
    assert _read(tiles.boxes, marks)[0] == pytest.approx(
        min(px for px in tiles.lettering_px if px), abs=0.06)
    assert tiles.under == [k for k, px in enumerate(tiles.lettering_px)
                           if px is not None and px < frames.LEGIBLE_TEXT_PX]


def test_the_grid_is_returned_only_where_it_reads_better():
    """The builder's two sheets keep their region tiles at 16: there the
    regions show the smallest lettering larger than the grid does."""
    for layout in ("right", "bottom"):
        doc = Document(content=build_esize_sheet(layout).pdf)
        try:
            marks = frames.page_marks(doc, 0)
        finally:
            doc.close()
        tiles = frames.region_tiles(marks, BUDGET)
        assert tiles.method == "regions" and tiles.capped
        grid = frames.tile_boxes((0.0, 0.0, marks.width, marks.height), 4)
        assert _read(tiles.boxes, marks)[0] > _read(grid, marks)[0]


def test_lines_the_grid_would_cut_everywhere_keep_their_region_tiles():
    """A letter page of fine print right across it: every grid tile cuts
    every line, so the grid is not the better answer though its lettering
    is larger."""
    marks = _sheet(612.0, 792.0, 170, 13, size=3.0, words=38, pitch=4.2)
    tiles = frames.region_tiles(marks, BUDGET)
    assert tiles.method != "grid" and tiles.under
    assert _read(tiles.boxes, marks)[0] is not None
    grid = frames.tile_boxes((0.0, 0.0, 612.0, 792.0), 4)
    assert _read(grid, marks)[1] < _read(tiles.boxes, marks)[1]
