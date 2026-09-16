"""Forgiving (fuzzy) search — against a sheet whose lettering came out wrong.

The fixture here is the failure this feature exists for: a plotted sheet where
the same callout appears once spelled correctly and once with a letter read
wrong, the way an optical pass or a stroked-SHX recovery returns it. Every word
on it is invented, so nothing in these tests depends on a real drawing.

The threshold under test is the package default, which was MEASURED on a real
submittal's drawing callouts (see DESIGN.md, "Forgiving search"); what is
pinned here is the BEHAVIOUR at a threshold — that a one-letter error is
found, that a different word is not, that exact search is untouched.
"""

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("rapidfuzz")

from planlens.document import (  # noqa: E402
    DEFAULT_FUZZY_MIN_SCORE, SOURCE_CAD_HIDDEN, SOURCE_PDF_TEXT,
    fuzzy_search_available, open_document,
)

#: Invented callouts. TREMBLIN / GAVOTTE / QUILLMARK are words no drawing
#: carries, so a hit on one of them can only have come from this fixture.
GOOD = "TREMBLIN STRUT AT PLATFORM LEVEL"
#: The same callout with one letter read wrong (N -> B), as a scan would.
CORRUPT = "TREMBLIB STRUT AT PLATFORM LEVEL"
#: A callout that shares no word with either, to sit in the results as a
#: candidate that must NOT match.
OTHER = "GAVOTTE FOOTING SCHEDULE SEE DETAIL"
#: Hidden AutoCAD text, also corrupted, so the CAD leg is covered too.
CAD_GOOD = "QUILLMARK TIEBACK ANCHOR"
CAD_CORRUPT = "QUILLMARC TIEBACK ANCHOR"


@pytest.fixture(scope="module")
def sheet_pdf():
    doc = fitz.open()
    page = doc.new_page(width=1224, height=792)
    page.insert_text((72, 100), GOOD, fontsize=12)
    page.insert_text((72, 140), CORRUPT, fontsize=12)
    page.insert_text((72, 180), OTHER, fontsize=12)
    # Single characters, the kind a sheet is full of: a dimension tick label,
    # a grid bubble. They must not score against a long query.
    for n, ch in enumerate("ABCDE"):
        page.insert_text((72 + 40 * n, 220), ch, fontsize=12)
    for text, y in ((CAD_GOOD, 300), (CAD_CORRUPT, 340)):
        annot = page.add_rect_annot(fitz.Rect(72, y - 12, 400, y + 4))
        annot.set_info(title="AutoCAD SHX Text", content=text)
        annot.set_border(width=0)
        annot.update(opacity=0)
    note = page.add_freetext_annot(fitz.Rect(600, 400, 900, 460),
                                   "Check the TREMBLIB strut connection.",
                                   fontsize=11)
    note.set_info(title="Reviewer Q", subject="Callout")
    note.update()
    out = doc.tobytes()
    doc.close()
    return out


@pytest.fixture
def doc(sheet_pdf):
    d = open_document(sheet_pdf, name="fuzzy-sheet")
    yield d
    d.close()


def _texts(res):
    return [h.get("match", "") for h in res["hits"]]


def _snippets(res):
    return [h.get("snippet", "") for h in res["hits"]]


# -- exact search is untouched -------------------------------------------------

def test_exact_search_still_finds_only_the_exact_spelling(doc):
    res = doc.search("TREMBLIN")
    assert res["n_hits"] == 1
    assert "TREMBLIB" not in _snippets(res)[0]
    assert "score" not in res["hits"][0]
    assert "fuzzy" not in res


def test_exact_search_of_the_wrong_spelling_finds_nothing(doc):
    assert doc.search("TREMBLIM")["n_hits"] == 0


# -- fuzzy finds the letter error ----------------------------------------------

def test_fuzzy_finds_a_line_with_one_letter_wrong(doc):
    res = doc.search("TREMBLIN STRUT AT PLATFORM LEVEL", fuzzy=True)
    assert res["fuzzy"] is True
    assert res["min_score"] == DEFAULT_FUZZY_MIN_SCORE
    joined = " ".join(_snippets(res))
    assert "TREMBLIB STRUT" in joined      # the corrupted line was found
    assert "TREMBLIN STRUT" in joined      # and the clean one too


def test_fuzzy_reaches_hidden_cad_text(doc):
    res = doc.search(CAD_GOOD, fuzzy=True)
    sources = {h["source"] for h in res["hits"]}
    assert SOURCE_CAD_HIDDEN in sources
    found = " ".join(_snippets(res))
    assert CAD_CORRUPT in found


def test_fuzzy_reaches_markup_comments(doc):
    res = doc.search("Check the TREMBLIN strut connection.", fuzzy=True)
    markup_hits = [h for h in res["hits"] if h["source"] == "pdf_annotation"]
    assert markup_hits and markup_hits[0]["author"] == "Reviewer Q"


def test_every_fuzzy_hit_says_its_score_and_source(doc):
    res = doc.search(GOOD, fuzzy=True)
    assert res["hits"]
    for hit in res["hits"]:
        assert 0 <= hit["score"] <= 100
        assert hit["source"] in (SOURCE_PDF_TEXT, SOURCE_CAD_HIDDEN,
                                 "pdf_annotation")


# -- ordering and cut ----------------------------------------------------------

def test_hits_come_back_best_score_first(doc):
    res = doc.search(GOOD, fuzzy=True)
    scores = [h["score"] for h in res["hits"]]
    assert scores == sorted(scores, reverse=True)
    # The exact line must outrank the one with a letter wrong.
    assert GOOD in _snippets(res)[0]


def test_min_score_cuts_the_weaker_matches(doc):
    loose = doc.search(GOOD, fuzzy=True, min_score=60)
    tight = doc.search(GOOD, fuzzy=True, min_score=99)
    assert loose["n_hits"] > tight["n_hits"] >= 1
    assert all(h["score"] >= 99 for h in tight["hits"])


def test_a_different_word_is_not_matched(doc):
    res = doc.search("GAVOTTE FOOTING SCHEDULE SEE DETAIL", fuzzy=True)
    assert res["n_hits"] >= 1
    assert "GAVOTTE" in _snippets(res)[0]
    assert not any("TREMBLI" in s for s in _snippets(res))


def test_single_character_lines_do_not_score_against_a_long_query(doc):
    # rapidfuzz slides the SHORTER string over the longer one, so without the
    # length guard a one-character line scores 100 against any query holding
    # that character. Measured on a real submittal, that alone invented ~120
    # hits per query for words the document does not contain.
    res = doc.search(GOOD, fuzzy=True, min_score=60)
    assert not any(len(h["snippet"].strip()) <= 2 for h in res["hits"])


def test_max_hits_keeps_the_best_and_says_it_cut(doc):
    res = doc.search(GOOD, fuzzy=True, min_score=50, max_hits=2)
    assert res["n_hits"] == 2
    assert res["truncated"] is True
    assert res["hits"][0]["score"] >= res["hits"][1]["score"]


def test_pages_and_markups_can_be_limited(doc):
    res = doc.search(GOOD, fuzzy=True, include_markups=False)
    assert all(h["source"] != "pdf_annotation" for h in res["hits"])


def test_a_fuzzy_hit_carries_the_same_location_fields_as_an_exact_one(doc):
    fuzzy = doc.search(GOOD, fuzzy=True)["hits"][0]
    exact = doc.search(GOOD)["hits"][0]
    assert set(exact) - {"source"} <= set(fuzzy)
    assert fuzzy["bbox"] == exact["bbox"]
    assert fuzzy["line_ids"] == exact["line_ids"]


# -- the optional dependency ---------------------------------------------------

def test_availability_is_reported(doc):
    assert fuzzy_search_available() is True


def test_without_rapidfuzz_the_error_names_the_extra(doc, monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidfuzz", None)
    with pytest.raises(ImportError) as exc:
        doc.search(GOOD, fuzzy=True)
    assert 'pip install "planlens[text]"' in str(exc.value)
    assert "rapidfuzz" in str(exc.value)
    # Exact search must still work with the package missing.
    assert doc.search(GOOD)["n_hits"] == 1


def test_availability_is_false_without_rapidfuzz(monkeypatch):
    monkeypatch.setitem(__import__("sys").modules, "rapidfuzz", None)
    assert fuzzy_search_available() is False


def test_an_empty_pattern_finds_nothing_rather_than_everything(doc):
    assert doc.search("", fuzzy=True)["n_hits"] == 0
