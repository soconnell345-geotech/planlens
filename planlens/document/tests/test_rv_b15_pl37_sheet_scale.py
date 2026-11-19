"""Review of PL-37 (the sheet's printed scale in the sheet index's rows):
the cases where a row was handed a scale its title block does not print.

Made-up sheets only: 36 x 24 in sheets of a made-up firm, with the title
block down the right edge or across the bottom.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document                  # noqa: E402

W, H = 36 * 72.0, 24 * 72.0
BX = W - 280.0                         # the right-edge block's left edge
Y = H - 260.0                          # its scale cell
TITLES = ("COVER NOTES", "PILE LAYOUT", "GRADE BEAM PLAN", "PIER DETAILS",
          "RETAINING WALL", "DRAIN SECTIONS")


def _right_block(page, i, cells):
    def t(at, text, size):
        page.insert_text(at, text, fontsize=size)
    t((BX, 150), "HOLLOW CREEK CONSULTING", 11)
    t((BX, 190), "JOB NAME", 6)
    t((BX, 210), "TANNERY ROW WAREHOUSE", 13)
    t((BX, H - 360), "DRAWING TITLE", 6)
    t((BX, H - 338), TITLES[i], 13)
    t((BX, H - 120), "DRAWING NO.", 6)
    t((BX, H - 80), f"G-{301 + i}", 28)
    for dx, y, text in cells:
        t((BX + dx, y), text, 8)


def _bottom_block(page, i, cells):
    def t(at, text, size):
        page.insert_text(at, text, fontsize=size)
    y = H - 60
    t((50, y), "HOLLOW CREEK CONSULTING", 11)
    t((500, y - 22), "JOB NAME", 6)
    t((500, y), "TANNERY ROW WAREHOUSE", 13)
    t((1100, y - 22), "DRAWING TITLE", 6)
    t((1100, y), TITLES[i], 13)
    t((W - 300, y - 22), "DRAWING NO.", 6)
    t((W - 300, y + 8), f"G-{301 + i}", 26)
    for x, y2, text in cells:
        t((x, y2), text, 8)


def _scales(sheets, block=_right_block):
    """The rows' scales for a set drawn from ``sheets``: per sheet, the
    title block's extra cells and the lines out on the sheet."""
    doc = pymupdf.open()
    for i, (cells, body) in enumerate(sheets):
        page = doc.new_page(width=W, height=H)
        for x, y, text, size in body:
            page.insert_text((x, y), text, fontsize=size)
        block(page, i, cells)
    pdf = doc.tobytes()
    doc.close()
    with open_document(pdf) as opened:
        return [r.scale for r in opened.sheet_index().rows]


PRINTED = ["1:100", "1:200", "AS NOTED", None]


@pytest.mark.parametrize("view", [
    [(20, 420, "KEY PLAN"), (20, 520, "SCALE: NTS")],
    [(20, 420, "KEY PLAN"), (20, 520, "SCALE"), (20, 534, "N.T.S.")],
    [(20, 420, "LOCATION MAP"), (20, 520, "SCALE: 1:5000")],
])
def test_a_key_plans_scale_in_the_blocks_band_is_no_sheets_scale(view):
    # the set repeats a key plan, with its own scale line, inside the
    # title block's column; the block's own scale cell differs by sheet
    # and the last sheet's block prints none
    got = _scales([(view + ([(0, Y, f"SCALE: {s}")] if s else []), [])
                   for s in PRINTED])
    assert got == PRINTED


def test_a_key_plan_in_a_bottom_blocks_strip():
    got = _scales(
        [([(1500, H - 110, "KEY PLAN"), (1500, H - 40, "SCALE: NTS")]
          + ([(1800, H - 82, "SCALE"), (1800, H - 60, s)] if s else []), [])
         for s in PRINTED], _bottom_block)
    assert got == PRINTED


def test_a_scale_most_sheets_share_is_still_theirs():
    # no repeated view here: four sheets print the same scale at the
    # block's scale cell and two print their own in that cell
    printed = ["1:100", "1:100", "1:50", "1:100", "AS NOTED", "1:100"]
    assert _scales([([(0, Y, f"SCALE: {s}")], []) for s in printed]) == printed


def _revisions(words):
    """A revision block in the title block whose last row tells of a scale
    change; ``words`` is that row's description, one line or two."""
    y = H - 560
    rows = [(0, y, "REVISIONS"), (0, y + 15, "NO"), (30, y + 15, "DATE"),
            (100, y + 15, "DESCRIPTION"), (0, y + 30, "A"),
            (30, y + 30, "2026-02-03"), (100, y + 30, "ISSUED FOR REVIEW"),
            (0, y + 45, "B"), (30, y + 45, "2026-04-09"),
            (100, y + 45, words[0])]
    return rows + [(100, y + 57, more) for more in words[1:]]


CHANGES = [("SCALE CHANGED TO 1:50",), ("SCALE 1:50",), ("SCALE: 1:50",)]


def test_a_revision_row_that_tells_of_a_scale_is_not_the_sheets_scale():
    # the title block prints no scale; its revision block mentions one
    assert _scales([(_revisions(c), []) for c in CHANGES]) == [None] * 3


def test_a_revision_row_does_not_take_the_blocks_own_scale_away():
    got = _scales([(_revisions(c) + [(0, Y, "SCALE: 1:100")], [])
                   for c in CHANGES])
    assert got == ["1:100"] * 3
