"""Audit of how planlens reads a report (2026-11-18): exact search and the
punctuation a word processor sets.

A report written in a word processor prints "contractor’s" (a curly
apostrophe), “quoted” words and "−1.2" (a minus sign); a reader
types "contractor's", "quoted" and "-1.2". The search said "not found" for
words that are on the page.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.search_text import fold  # noqa: E402

LINES = [
    "The contractor’s schedule shows the ‘final’ lift.",
    "The “drainage blanket” is placed first.",
    "Elevation change is −1.2 ft at boring QX-2; range 2–4 ft.",
]


@pytest.fixture(scope="module")
def doc():
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    writer = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("helv")
    for i, line in enumerate(LINES):
        writer.append((72, 100 + 14 * i), line, font=font, fontsize=11)
    writer.write_text(page)
    data = pdf.tobytes()
    pdf.close()
    d = open_document(data, name="made-up-report")
    yield d
    d.close()


def test_the_page_holds_the_typeset_punctuation(doc):
    assert [ln.text for ln in doc.page(0).lines] == LINES


@pytest.mark.parametrize("typed, on_the_page", [
    ("contractor's schedule", "contractor’s schedule"),
    ("'final'", "‘final’"),
    ('"drainage blanket"', "“drainage blanket”"),
    ("-1.2 ft", "−1.2 ft"),
    ("2-4 ft", "2–4 ft"),
])
def test_a_plainly_typed_search_finds_the_typeset_text(doc, typed,
                                                       on_the_page):
    out = doc.search(typed)
    assert out["n_hits"] == 1, out
    assert out["hits"][0]["match"] == on_the_page


def test_the_typeset_form_still_finds_itself(doc):
    assert doc.search("contractor’s")["n_hits"] == 1


def test_folding_maps_back_to_the_page_characters():
    text = "the contractor’s"
    folded = fold(text)
    assert folded.text == "the contractor's"
    i = folded.text.index("'")
    assert text[slice(*folded.span(i, i + 1))] == "’"


# -- a page number printed alone at the foot of the page -----------------------

def _numbered_report(numbers):
    pdf = pymupdf.open()
    font = pymupdf.Font("helv")
    for n in numbers:
        page = pdf.new_page(width=612, height=792)
        writer = pymupdf.TextWriter(page.rect)
        writer.append((72, 40), "Zorvex Ridge Report - Project 4471",
                      font=font, fontsize=8)
        for i in range(20):
            writer.append((72, 100 + 14 * i),
                          f"Line {i} of the narrative about blorite fill.",
                          font=font, fontsize=10)
        writer.append((72, 762), "Quillfen Associates", font=font,
                      fontsize=8)
        writer.append((300, 762), n, font=font, fontsize=9)
        writer.write_text(page)
    data = pdf.tobytes()
    pdf.close()
    return open_document(data, name="made-up-numbered-report")


def test_a_bare_number_in_the_footer_is_the_printed_page():
    d = _numbered_report(["1", "2", "- 3 -"])
    try:
        assert [d.summary(i).printed_page for i in range(3)] == [1, 2, 3]
        assert d.segments()[0].get("printed_pages") == "1-3"
    finally:
        d.close()


def test_a_year_or_a_letter_number_is_not_read_as_one():
    d = _numbered_report(["2026", "A-1", "ii"])
    try:
        assert [d.summary(i).printed_page for i in range(3)] == [None] * 3
    finally:
        d.close()
