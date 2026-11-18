"""PL-09 review: a page read row by row that is NOT prose in columns (a table
with no rules, a form, an index, a list in two columns) carries no "reading
order not reconstructed" warning; wrapped text drawn row by row still does.
Made-up pages and wording only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402

ACROSS = "reading order not reconstructed"

SENTENCES = (
    "The bearing stratum lies below the made ground at a depth that varies "
    "across the site. It was proved in every trial hole, and groundwater "
    "was met during boring in three of them. Samples were sealed on the day "
    "they were taken and tested within a week. No obstruction was met. The "
    "made ground is loose and holds brick and ash throughout. ")

CELLS = ("Made ground of brick and ash", "Firm brown silty clay with gravel",
         "Sealed on the day it was taken", "Water met at the foot of the hole",
         "Dense grey sand and fine gravel over rock",
         "No sample was recovered here", "Hole left open for one day only",
         "Stiff clay", "Casing driven to the top of the weathered rock")


def _warned(draw) -> bool:
    doc = pymupdf.open()
    draw(doc.new_page(width=612, height=792))
    data = doc.tobytes()
    doc.close()
    with Document(content=data) as d:
        return any(w.startswith(ACROSS) for w in d.page(0).warnings)


def _wrapped(start: int, n: int, width: float, size: float, upper=False):
    words = (SENTENCES.upper() if upper else SENTENCES).split() * 20
    i, out = start, []
    while len(out) < n:
        line = words[i]
        i += 1
        while pymupdf.get_text_length(f"{line} {words[i]}",
                                      fontsize=size) <= width:
            line = f"{line} {words[i]}"
            i += 1
        out.append(line)
    return out


def test_a_table_of_two_text_columns_with_no_rules_says_nothing():
    def draw(page):
        for r in range(16):
            page.insert_text((72, 90 + 16 * r), CELLS[r % 9], fontsize=9)
            page.insert_text((320, 90 + 16 * r), CELLS[(r * 5 + 2) % 9],
                             fontsize=9)
    assert not _warned(draw)


def test_a_form_with_labels_left_and_values_right_says_nothing():
    labels = ("Name of the project and site", "Client and the client's agent",
              "Date the work was started on", "Method used to bore the hole",
              "Depth to the water when met", "Weather on the day of work",
              "Person who logged the samples", "Level of the ground at hole")

    def draw(page):
        for r, label in enumerate(labels):
            page.insert_text((72, 90 + 22 * r), label + ":", fontsize=9)
            page.insert_text((300, 90 + 22 * r), CELLS[r], fontsize=9)
    assert not _warned(draw)


def test_an_index_with_dot_leaders_says_nothing():
    def draw(page):
        for r in range(20):
            y = 100 + 15 * r
            page.insert_text((72, y), f"{r + 1}.0 {CELLS[r % 9]}", fontsize=9)
            page.insert_text((300, y), ". " * 38, fontsize=9)
            page.insert_text((520, y), str(r + 3), fontsize=9)
    assert not _warned(draw)


def test_a_list_set_in_two_columns_says_nothing():
    def draw(page):
        for r in range(14):
            y = 95 + 14 * r
            page.insert_text((72, y), f"{r + 1}. {CELLS[r % 9]}", fontsize=9)
            page.insert_text((320, y), f"{r + 15}. {CELLS[(r + 4) % 9]}",
                             fontsize=9)
    assert not _warned(draw)


@pytest.mark.parametrize("upper", [False, True])
def test_wrapped_text_drawn_row_by_row_still_says_so(upper):
    """The repair holds for text that wraps at the column's width, in small
    letters or in capitals throughout (notes on a sheet)."""
    left = _wrapped(0, 24, 225, 8, upper)
    right = _wrapped(31, 24, 225, 8, upper)

    def draw(page):
        for r in range(24):
            page.insert_text((72, 100 + 13 * r), left[r], fontsize=8)
            page.insert_text((320, 100 + 13 * r), right[r], fontsize=8)
    assert _warned(draw)
