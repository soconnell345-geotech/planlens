"""Exact search compares what a reader sees, and returns what the page holds.

The page built here stores its text the way real PDFs do: ligatures as single
characters, runs of spaces, no-break spaces and words hyphenated at a line
end. Each is searched for as a person would type it. The folding itself
(:mod:`planlens.document.search_text`) is checked first, without a PDF.
"""

import re

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.search_text import (  # noqa: E402
    find_spans, fold, readings,
)

FI, FL, NBSP = "ﬁ", "ﬂ", " "

#: The lines of the synthetic page, as its text layer holds them.
LINES = [
    f"The {FI}ll behind the wall is speci{FI}ed as granular.",
    f"A {FL}exible sleeve is {FI}tted at each joint.",
    "Bearing  capacity   is checked in Section 4.",
    f"Allowable{NBSP}pressure is 3,000{NBSP}psf at the base.",
    "The mat founda-",
    "tion bears on dense sand, and the load-",
    "bearing stratum is 12 ft down. Samples were taken at 5-",
    "10 ft and the pre-",
    "and post-test readings agree.",
]


# -- the folding ---------------------------------------------------------------

def test_text_with_nothing_to_fold_is_left_as_it_is():
    plain = "Bearing capacity is checked - see Section 4 (N = 30)."
    folded = fold(plain)
    assert folded.text is plain and folded.at is None
    assert folded.span(8, 16) == (8, 16)


def test_a_ligature_folds_to_its_letters_and_maps_back_to_itself():
    text = f"speci{FI}ed {FL}ow"
    folded = fold(text)
    assert folded.text == "specified flow"
    start = folded.text.index("fied")
    assert text[slice(*folded.span(start, start + 4))] == f"{FI}ed"
    # One letter of the ligature still names the whole character.
    assert text[slice(*folded.span(start + 1, start + 2))] == FI


def test_white_space_folds_to_one_space():
    text = f"a  b\t c{NBSP}d {NBSP} e"
    folded = fold(text)
    assert folded.text == "a b c d e"
    start = folded.text.index("b c")
    assert text[slice(*folded.span(start, start + 3))] == "b\t c"
    assert folded.span(len(folded.text), len(folded.text)) == (len(text),) * 2


def test_a_line_end_hyphen_is_read_each_way():
    text = "the mat founda- tion and the load- bearing soil"
    ends = [text.index("- tion") + 1, text.index("- bearing") + 1]
    as_joined, closed, unhyphenated = (f.text for f in readings(text, ends))
    assert as_joined == text
    assert closed == "the mat founda-tion and the load-bearing soil"
    assert unhyphenated == "the mat foundation and the loadbearing soil"
    spans = list(find_spans(re.compile("foundation"), readings(text, ends)))
    assert [text[a:b] for a, b in spans] == ["founda- tion"]
    # A match every reading finds is reported once.
    assert len(list(find_spans(re.compile("mat"), readings(text, ends)))) == 1


def test_a_hyphen_between_numbers_or_after_a_space_is_not_dropped():
    text = "taken at 5- 10 ft"
    forms = readings(text, [text.index("- ") + 1])
    assert [f.text for f in forms] == [text, "taken at 5-10 ft"]
    dash = "settlement - not shown"
    assert [f.text for f in readings(dash, [dash.index("- ") + 1])] == [dash]


# -- a page that stores its text the way real PDFs do --------------------------

@pytest.fixture(scope="module")
def doc():
    pdf = pymupdf.open()
    page = pdf.new_page(width=612, height=792)
    writer = pymupdf.TextWriter(page.rect)
    font = pymupdf.Font("helv")
    for i, line in enumerate(LINES):
        writer.append((72, 100 + 14 * i), line, font=font, fontsize=11)
    writer.write_text(page)
    page.add_text_annot((400, 300), f"Check  the{NBSP}toe of the {FI}ll.")
    data = pdf.tobytes()
    pdf.close()
    d = open_document(data, name="stored-text")
    yield d
    d.close()


def test_the_page_holds_its_text_as_the_pdf_stores_it(doc):
    assert [ln.text for ln in doc.page(0).lines] == LINES


@pytest.mark.parametrize("typed, on_the_page", [
    ("fill behind", f"{FI}ll behind"),
    ("specified", f"speci{FI}ed"),
    ("flexible sleeve is fitted", f"{FL}exible sleeve is {FI}tted"),
    ("Bearing capacity is checked", "Bearing  capacity   is checked"),
    ("Allowable pressure is 3,000 psf", f"Allowable{NBSP}pressure is "
                                        f"3,000{NBSP}psf"),
    ("mat foundation bears", "mat founda- tion bears"),
    ("load-bearing stratum", "load- bearing stratum"),
    ("5-10 ft", "5- 10 ft"),
    ("pre- and post-test", "pre- and post-test"),
])
def test_what_a_reader_types_finds_what_the_page_prints(doc, typed,
                                                        on_the_page):
    res = doc.search(typed, include_markups=False)
    assert res["n_hits"] == 1, res
    hit = res["hits"][0]
    assert hit["match"] == on_the_page          # the page's own text
    assert on_the_page in hit["snippet"]
    lines = {ln.id: ln for ln in doc.page(0).lines}
    assert " ".join(lines[i].text for i in hit["line_ids"]).count(
        on_the_page) == 1


def test_a_hit_across_a_hyphenated_break_names_both_lines(doc):
    hit = doc.search("foundation")["hits"][0]
    texts = [ln.text for ln in doc.page(0).lines if ln.id in hit["line_ids"]]
    assert texts == LINES[4:6]


def test_folding_joins_nothing_but_a_word_broken_at_a_line_end(doc):
    assert doc.search("510 ft")["n_hits"] == 0      # the hyphen of 5-10 stays
    assert doc.search("wallis")["n_hits"] == 0      # a space is never dropped
    assert doc.search("down.Samples")["n_hits"] == 0


def test_case_and_regex_still_work_on_folded_text(doc):
    assert doc.search("SPECIFIED")["n_hits"] == 1
    assert doc.search("SPECIFIED", case_sensitive=True)["n_hits"] == 0
    res = doc.search(r"speci\w+ as", regex=True)
    assert [h["match"] for h in res["hits"]] == [f"speci{FI}ed as"]
    res = doc.search(r"capacity is \w+", regex=True)
    assert [h["match"] for h in res["hits"]] == ["capacity   is checked"]


def test_a_markup_comment_is_compared_the_same_way(doc):
    res = doc.search("check the toe of the fill")
    assert res["n_hits"] == 1
    hit = res["hits"][0]
    assert hit["markup_id"] and hit["match"] == (
        f"Check  the{NBSP}toe of the {FI}ll")
