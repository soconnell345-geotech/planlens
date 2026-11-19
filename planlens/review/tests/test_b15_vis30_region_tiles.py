"""Tiles cut along a sheet's own regions (app BACKLOG VIS-30, first slice):
``frames.region_tiles`` beside ``frames.tile_boxes``, on two made-up E-size
sheets laid out differently, a 24 x 36 in fixture, a scan and a letter page.

``python -m planlens.review.tests.test_b15_vis30_region_tiles`` prints the
comparison table (uniform grid against region tiles) as Markdown.
"""

import numpy as np
import pytest

from planlens.document import Document
from planlens.review import frames
from planlens.testing.document_fixtures import build_synthetic_review_document
from planlens.testing.esize_fixtures import build_esize_sheet
from planlens.testing.sheet_set_fixtures import build_sheet_set

#: The image budget the app's core renders at when nothing is set.
BUDGET = "openai-high"
LAYOUTS = ("right", "bottom")
#: A ceiling on tiles high enough that it does not stop the cutting.
ROOMY = 40


@pytest.fixture(scope="module")
def sheets():
    out = {}
    for layout in LAYOUTS:
        gt = build_esize_sheet(layout)
        doc = Document(content=gt.pdf)
        out[layout] = (gt, doc, frames.page_marks(doc, 0))
    yield out
    for _gt, doc, _marks in out.values():
        doc.close()


def uniform_grid(doc, page):
    """The tiles the app's core cuts today (``_tile_count`` with ``auto``
    under the robust policy): ``tile_grid`` on the whole-page render."""
    _data, info = doc.render(page, budget=BUDGET)
    return frames.tile_boxes(info["clip"], frames.tile_grid(info))


def _scale(box):
    from planlens.document.budget import fit_size, resolve_budget
    w, h = box[2] - box[0], box[3] - box[1]
    return fit_size(w, h, resolve_budget(BUDGET))[0] / w


def _inside(inner, box, tol=0.5):
    return (inner[0] >= box[0] - tol and inner[1] >= box[1] - tol
            and inner[2] <= box[2] + tol and inner[3] <= box[3] + tol)


def _layer(marks):
    return frames.TextLayer(
        [frames.LayerLine("line %d" % k, tuple(row[:4]), 0.0)
         for k, row in enumerate(marks.text.tolist())],
        marks.width, marks.height)


def cut_text_lines(boxes, marks):
    """How many text lines some tile's edge passes through."""
    layer = _layer(marks)
    return len({ln.text for box in boxes
                for ln, _edges in frames.cut_lines(box, layer)})


def measure(boxes, marks, gt=None):
    """One row of the comparison. Each text line is taken in the tile that
    holds it whole and shows it largest: ``px_min`` is the smallest
    lettering on the sheet read that way, ``px_tiles`` the smallest in each
    tile's own lines (least and most over the tiles), ``under`` the lines
    under the line in every tile, ``nowhere`` the lines whole in no tile,
    ``cut`` the lines some tile edge passes through, ``dims_lost`` the
    dimension strings whole in no tile (``None``: none known)."""
    best = np.zeros(len(marks.text))
    home = np.full(len(marks.text), -1)
    for k, box in enumerate(boxes):
        whole = np.array([_inside(row[:4], box) for row in marks.text],
                         dtype=bool)
        px = marks.text[:, 4] * _scale(box)
        better = whole & (px > best)
        best[better], home[better] = px[better], k
    held = best > 0
    tiles = [float(best[home == k].min()) for k in set(home[held].tolist())]
    return {
        "tiles": len(boxes),
        "px_min": round(float(best[held].min()), 1) if held.any() else None,
        "px_tiles": ("%.1f to %.1f" % (min(tiles), max(tiles))
                     if tiles else None),
        "under": int((held & (best < frames.LEGIBLE_TEXT_PX)).sum()),
        "nowhere": int((~held).sum()),
        "cut": cut_text_lines(boxes, marks),
        "dims_lost": None if gt is None else sum(
            not any(_inside(d, box) for box in boxes) for d in gt.dims),
    }


def covered(boxes, clip):
    """Whether the boxes together cover ``clip`` (on a 1 pt raster)."""
    x0, y0, x1, y1 = (int(round(v)) for v in clip)
    seen = np.zeros((y1 - y0, x1 - x0), bool)
    for bx0, by0, bx1, by1 in boxes:
        seen[max(0, int(np.floor(by0)) - y0):int(np.ceil(by1)) - y0,
             max(0, int(np.floor(bx0)) - x0):int(np.ceil(bx1)) - x0] = True
    return bool(seen.all())


# -- what is guaranteed ------------------------------------------------------

@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("ceiling", (1, 4, 16, 40))
def test_the_tiles_cover_the_sheet_and_cut_no_text_line(sheets, layout,
                                                        ceiling):
    gt, _doc, marks = sheets[layout]
    tiles = frames.region_tiles(marks, BUDGET, max_tiles=ceiling)
    assert 1 <= len(tiles.boxes) <= ceiling
    assert covered(tiles.boxes, (0, 0) + gt.size)
    assert cut_text_lines(tiles.boxes, marks) == 0
    for box in tiles.boxes:                      # every tile is on the page
        assert _inside(box, (0, 0) + gt.size, tol=0.0)


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("ceiling", (16, 40))
def test_every_dimension_string_and_the_cloud_is_whole_in_a_tile(
        sheets, layout, ceiling):
    gt, _doc, marks = sheets[layout]
    tiles = frames.region_tiles(marks, BUDGET, max_tiles=ceiling)
    assert len(gt.dims) >= 12
    lost = [d for d in gt.dims
            if not any(_inside(d, box) for box in tiles.boxes)]
    # Promised only within a tile's reach past its cut (WIDEN_MAX_FRAC of
    # its size): a lost string is longer than that for every tile on it.
    for d in lost:
        flat = (d[2] - d[0]) > (d[3] - d[1])
        on = [b for b in tiles.boxes if frames._inside(d, b)[0] > 0
              and frames._inside(d, b)[1] > 0]
        reach = frames.WIDEN_MAX_FRAC * min(
            (b[2] - b[0]) if flat else (b[3] - b[1]) for b in on)
        assert ((d[2] - d[0]) if flat else (d[3] - d[1])) > reach
    if ceiling == 16:
        assert len(lost) <= (1 if layout == "bottom" else 0)
    assert any(_inside(gt.cloud, box) for box in tiles.boxes)


@pytest.mark.parametrize("layout", LAYOUTS)
def test_the_cuts_follow_the_sheets_own_regions(sheets, layout):
    """Wherever the title block or a schedule sits, no tile edge runs
    through the middle of one of its lettered cells: the edges inside a
    ruled region lie on its rules, and the notes are cut between notes."""
    gt, _doc, marks = sheets[layout]
    tiles = frames.region_tiles(marks, BUDGET)
    assert tiles.method == "regions"
    assert 1 in tiles.level
    lettering = marks.text[:, 4]
    small = marks.text[lettering <= lettering.min() + 0.01]
    # The notes' small lettering gained on the whole page by more than a
    # uniform grid of as many tiles gives.
    note = small[0, :4]
    held = [b for b in tiles.boxes if _inside(note, b)]
    grid = frames.tile_boxes((0, 0) + gt.size, 4)
    assert max(_scale(b) for b in held) > max(_scale(b) for b in grid)


# -- the second level and the ceiling ----------------------------------------

def test_a_region_still_under_the_line_is_cut_again(sheets):
    """The widest view of the second sheet has no gutter or border to cut
    along, and its lettering is under the line in one tile: it is cut a
    second time, at a position with no text on it."""
    gt, _doc, marks = sheets["bottom"]
    tiles = frames.region_tiles(marks, BUDGET, max_tiles=40)
    assert 2 in tiles.level and 1 in tiles.level
    view = gt.regions["view 1"]
    over = [k for k, b in enumerate(tiles.boxes)
            if min(b[2], view[2]) - max(b[0], view[0]) > 200
            and min(b[3], view[3]) - max(b[1], view[1]) > 200]
    assert len(over) >= 2 and any(tiles.level[k] == 2 for k in over)
    assert cut_text_lines(tiles.boxes, marks) == 0


@pytest.mark.parametrize("layout", LAYOUTS)
@pytest.mark.parametrize("ceiling", (4, 16, 40))
def test_under_names_the_tiles_still_under_the_line(sheets, layout, ceiling):
    _gt, _doc, marks = sheets[layout]
    tiles = frames.region_tiles(marks, BUDGET, max_tiles=ceiling)
    assert tiles.under == [
        k for k, px in enumerate(tiles.lettering_px)
        if px is not None and px < frames.LEGIBLE_TEXT_PX]
    for k, box in enumerate(tiles.boxes):        # the px are the tile's own
        whole = [row[4] for row in marks.text if _inside(row[:4], box)]
        if tiles.lettering_px[k] is not None:
            assert tiles.lettering_px[k] >= round(
                min(whole) * _scale(box), 1) - 0.05


def test_the_ceiling_stops_the_cutting_and_says_so(sheets):
    _gt, _doc, marks = sheets["right"]
    few = frames.region_tiles(marks, BUDGET, max_tiles=4)
    assert len(few.boxes) == 4 and few.capped and few.under
    enough = frames.region_tiles(marks, BUDGET, max_tiles=40)
    assert not enough.capped and enough.under == []
    assert len(enough.boxes) < 40
    assert min(px for px in enough.lettering_px if px) >= frames.LEGIBLE_TEXT_PX


# -- the comparison with today's uniform grid --------------------------------

def comparison():
    """``(sheet, grid row, region row at the default ceiling of 16 tiles,
    region row with room to finish)`` for the two made-up E-size sheets,
    a 34 x 22 in sheet of the sheet-set fixture and the 24 x 36 in sheet of
    the review-document fixture (which has almost no text)."""
    rows = []
    cases = [("E-size, title block right", build_esize_sheet("right"), 0),
             ("E-size, title block bottom", build_esize_sheet("bottom"), 0),
             ("34 x 22 in sheet-set fixture", build_sheet_set(), 0)]
    review = build_synthetic_review_document()
    cases.append(("24 x 36 in review fixture", review, review.sheet_page))
    for name, gt, page in cases:
        doc = Document(content=gt.pdf)
        try:
            marks = frames.page_marks(doc, page)
            dims = gt if getattr(gt, "dims", None) else None
            found = []
            for ceiling in (16, ROOMY):
                tiles = frames.region_tiles(marks, BUDGET, max_tiles=ceiling)
                found.append(dict(
                    measure(tiles.boxes, marks, dims), method=tiles.method,
                    under_tiles=len(tiles.under), capped=tiles.capped))
            rows.append((name, measure(uniform_grid(doc, page), marks, dims),
                         found[0], found[1]))
        finally:
            doc.close()
    return rows


def test_region_tiles_against_the_uniform_grid():
    rows = {row[0]: row[1:] for row in comparison()}
    for name in ("E-size, title block right", "E-size, title block bottom"):
        grid, region, roomy = rows[name]
        assert grid["tiles"] == 16 and region["tiles"] <= 16
        assert roomy["under"] == 0 and roomy["cut"] == 0
        assert roomy["tiles"] < 2 * grid["tiles"]
        assert grid["px_min"] < frames.LEGIBLE_TEXT_PX      # VIS-30's point
        assert region["px_min"] > grid["px_min"]
        assert region["nowhere"] == 0
        assert grid["cut"] > 0 and region["cut"] == 0
        assert region["dims_lost"] < grid["dims_lost"]
    grid, region, _roomy = rows["34 x 22 in sheet-set fixture"]
    assert region["method"] == "regions" and region["cut"] == 0
    assert region["under"] == 0 and region["nowhere"] == 0
    # Almost no text to cut by: the answer is today's grid, box for box.
    grid, region, _roomy = rows["24 x 36 in review fixture"]
    assert region["method"] == "grid" and region["tiles"] == grid["tiles"]


# -- pages with nothing to cut by --------------------------------------------

def test_a_scan_gets_the_uniform_grid():
    review = build_synthetic_review_document()
    doc = Document(content=review.pdf)
    try:
        marks = frames.page_marks(doc, review.scanned_page)
        assert marks.chars == 0
        tiles = frames.region_tiles(marks, BUDGET)
        assert tiles.method == "grid"
        assert tiles.boxes == uniform_grid(doc, review.scanned_page)
    finally:
        doc.close()


def test_a_sheet_with_no_text_layer_gets_the_grid_and_says_what_is_under():
    """An E-size scan: the 4 x 4 grid, every tile named as under the line
    at the lettering height assumed for a sheet with nothing to measure."""
    clip = (0.0, 0.0, 3456.0, 2592.0)
    tiles = frames.region_tiles(None, BUDGET, clip=clip)
    assert tiles.method == "grid" and not tiles.capped
    assert tiles.boxes == frames.tile_boxes(clip, frames.MAX_TILES_PER_SIDE)
    assert tiles.under == list(range(16)) and set(tiles.level) == {0}
    assert all(px < frames.LEGIBLE_TEXT_PX for px in tiles.lettering_px)
    assert tiles.lettering_px[0] == round(
        frames.ASSUMED_CAD_LETTERING_PT * _scale(tiles.boxes[0]), 1)


def test_a_letter_page_of_text_is_one_tile():
    review = build_synthetic_review_document()
    doc = Document(content=review.pdf)
    try:
        marks = frames.page_marks(doc, review.narrative_page)
        assert marks.chars >= frames.REGION_MIN_CHARS
        tiles = frames.region_tiles(marks, BUDGET)
        assert tiles.method == "whole" and tiles.level == [0]
        assert tiles.boxes == [(0.0, 0.0, 612.0, 792.0)]
        assert tiles.under == [] and not tiles.capped
        assert uniform_grid(doc, review.narrative_page) == tiles.boxes
    finally:
        doc.close()


# -- nothing else moved ------------------------------------------------------

def test_tile_boxes_is_what_it_was():
    assert frames.tile_boxes((0, 0, 100, 200), 2) == [
        (0.0, 0.0, 54.0, 108.0), (46.0, 0.0, 100.0, 108.0),
        (0.0, 92.0, 54.0, 200.0), (46.0, 92.0, 100.0, 200.0)]
    assert frames.tile_boxes((10, 20, 110, 220), 1) == [
        (10.0, 20.0, 110.0, 220.0)]


def test_no_tool_or_host_uses_the_region_tiles():
    import os
    root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))))
    users = []
    for folder, _dirs, files in os.walk(root):
        if os.sep + "tests" in folder:
            continue
        for name in files:
            if name.endswith(".py") and name != "frames.py":
                with open(os.path.join(folder, name), encoding="utf-8") as fh:
                    if "region_tiles" in fh.read():
                        users.append(name)
    assert users == []


def test_the_source_names_no_sheet_size_or_region():
    import inspect
    source = "".join(inspect.getsource(f) for f in (
        frames.region_tiles, frames._cuts, frames._with_overlap,
        frames._through, frames._gaps, frames.page_marks)).lower()
    for word in ("title", "schedule", "legend", "notes", "3456", "2592",
                 "1728", "e-size", "36", "48"):
        assert word not in source, word


if __name__ == "__main__":
    def cell(row, key):
        return "n/a" if row[key] is None else str(row[key])
    print("| Sheet | Tiles cut by | Tiles | Smallest lettering, px | "
          "... in each tile, least to most | Lines under 12 px | Text lines "
          "cut by a tile edge | Text lines whole in no tile | Dimension "
          "strings whole in no tile |")
    print("|---|---|---|---|---|---|---|---|---|")
    for name, grid, region, roomy in comparison():
        shown = [("uniform grid", grid), (region["method"], region)]
        if roomy["tiles"] != region["tiles"]:
            shown.append(("regions, ceiling %d" % ROOMY, roomy))
        for label, row in shown:
            print("| %s | %s | %d | %s | %s | %d | %d | %d | %s |" % (
                name, label, row["tiles"], cell(row, "px_min"),
                cell(row, "px_tiles"), row["under"], row["cut"],
                row["nowhere"], cell(row, "dims_lost")))
