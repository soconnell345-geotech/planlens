"""PL-09 review: two one-page logs of different borings on the same ruled
form are two tables wherever the page prints the boring's number outside the
grid (the page foot, beside the grid), and a table that runs into the page's
foot or starts in its head is still linked. Made-up pages and values only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.tests.test_pl09_continued_tables import (  # noqa: E402
    _band, _rows, _table,
)


def _notes(doc) -> list:
    data = doc.tobytes()
    doc.close()
    with Document(content=data, continued_tables=True) as d:
        return [n for i in range(d.n_pages) for t in d.page(i).tables
                for n in t.notes]


def _two_logs(place, same_boring=False):
    doc = pymupdf.open()
    for k in (1, 2):
        page = doc.new_page(width=612, height=792)
        _band(page, f"Made-up report page {2 + k}")
        page.insert_text(place, f"B-{1 if same_boring else k} LOG",
                         fontsize=8)
        _table(page, 110, _rows(1, 30))
    return doc


@pytest.mark.parametrize("place", [(72, 770), (545, 300)],
                         ids=["in the page foot", "beside the grid"])
def test_two_borings_on_one_form_are_two_tables(place):
    assert _notes(_two_logs(place)) == []


def test_one_boring_over_two_pages_of_the_form_is_linked():
    notes = _notes(_two_logs((72, 770), same_boring=True))
    assert len(notes) == 2 and all("continues" in n for n in notes)


def test_a_table_running_into_the_foot_and_starting_in_the_head_is_linked():
    doc = pymupdf.open()
    first = doc.new_page(width=612, height=792)
    assert _table(first, 110, _rows(1, 36)) > 0.92 * 792   # into the foot
    second = doc.new_page(width=612, height=792)
    _table(second, 36, _rows(40, 5))                        # in the head
    notes = _notes(doc)
    assert len(notes) == 2 and all("continues" in n for n in notes)
