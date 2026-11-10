"""The cross-reference check, on a made-up drawing set.

Truth is the fixture's own statement of the callouts it drew
(:mod:`planlens.testing.sheet_set_fixtures`): nine callouts, one to a view
the details sheet does not have, one to a sheet the set does not have, one
to a sheet with no view marks.
"""

import functools

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.crossrefs import (  # noqa: E402
    CANNOT_TELL, RESOLVED, UNRESOLVED, VIEW_ID, cross_references,
    text_callouts,
)
from planlens.testing.sheet_set_fixtures import (  # noqa: E402
    SCHEMES, build_sheet_set,
)

SET_STYLES = ("strip", "bottom", "inline", "turned")


@functools.lru_cache(maxsize=None)
def _built(style="strip", numbers="dash", scanned=()):
    return build_sheet_set(style, numbers, scanned=scanned)


def _refs(gt):
    with open_document(gt.pdf) as doc:
        return doc.cross_references()


def _key(page, view, target, form, status):
    return (page, view, target, form, status)


@pytest.mark.parametrize("numbers", sorted(SCHEMES))
@pytest.mark.parametrize("style", SET_STYLES)
def test_every_callout_is_found_and_judged(style, numbers):
    gt = _built(style, numbers)
    refs = _refs(gt)
    got = sorted(_key(c.page, c.view, c.target_sheet, c.form, c.status)
                 for c in refs.callouts)
    want = sorted(_key(c["page"], c["view"], c["target"], c["form"],
                       c["status"]) for c in gt.callouts)
    assert got == want
    assert refs.counts() == {"callouts": 9, RESOLVED: 6, UNRESOLVED: 2,
                             CANNOT_TELL: 1}
    assert sorted(c.label for c in refs.with_status(UNRESOLVED)) == \
        gt.with_status("unresolved")
    assert refs.views == gt.views
    assert refs.view_titles == gt.view_titles
    assert refs.notes == []


def test_an_unresolved_callout_says_what_is_missing_and_where_it_is():
    gt = _built()
    by_label = {c.label: c for c in _refs(gt).callouts
                if c.status != RESOLVED}
    view = by_label["5/A-501"]
    assert view.why == "A-501 holds views 1 to 4, not 5"
    assert (view.page, view.sheet, view.target_page) == (1, "A-101", 3)
    x0, y0, x1, y1 = view.bbox                   # the bubble drawn at 1540,760
    assert x0 < 1540 < x1 and y0 < 760 < y1
    assert x1 - x0 < 60 and y1 - y0 < 60
    sheet = by_label["1/A-301"]
    assert sheet.why == "there is no sheet A-301 in this set"
    assert sheet.target_page is None and sheet.page == 2
    assert "target" not in sheet.to_dict()
    unknown = by_label["1/A-601"]
    assert unknown.status == CANNOT_TELL
    assert "no view marks were recognised on A-601" in unknown.why
    assert view.to_dict() == {
        "page": 1, "sheet": "A-101", "callout": "5/A-501",
        "status": "unresolved", "why": view.why,
        "target": {"sheet": "A-501", "page": 3},
        "bbox": [int(round(v)) for v in view.bbox]}


@pytest.mark.parametrize("numbers", sorted(SCHEMES))
def test_what_is_not_a_callout_is_left_alone(numbers):
    """Grid bubbles, room tags, door marks, the schedule's marks and the
    cover's list of sheet numbers under a discipline letter."""
    gt = _built("strip", numbers)
    refs = _refs(gt)
    assert [c for c in refs.callouts if c.page == 0] == []
    assert gt.numbers[0] not in refs.views           # the cover holds none
    assert gt.numbers[4] not in refs.views           # nor the schedule
    assert len(refs.callouts) == len(gt.callouts)


def test_a_callout_written_on_one_line_is_placed_on_its_line():
    refs = _refs(_built())
    written = [c for c in refs.callouts if c.form == "text"]
    assert sorted((c.page, c.label) for c in written) == [
        (2, "4/A-501"), (4, "2/A-501")]
    for c in written:
        assert c.status == RESOLVED and c.to_dict()["form"] == "text"


def test_a_callout_to_a_sheet_that_could_not_be_read_is_not_called_wrong():
    gt = _built(scanned=(3,))                    # the details sheet, scanned
    refs = _refs(gt)
    to_details = [c for c in refs.callouts if c.target_sheet == "A-501"]
    assert len(to_details) == 6
    assert {c.status for c in to_details} == {CANNOT_TELL}
    assert all("could not be indexed" in c.why for c in to_details)
    # a sheet the set may or may not have: it could be the unread one
    assert [c.status for c in refs.callouts
            if c.target_sheet == "A-301"] == [CANNOT_TELL]
    assert refs.with_status(UNRESOLVED) == []
    # the scanned sheet's own callout is not in the list, and that is said
    assert [c for c in refs.callouts if c.page == 3] == []
    assert refs.notes == ["1 sheet(s) have no usable text: their callouts "
                          "are not in this list"]
    assert [c.label for c in refs.with_status(RESOLVED)] == ["1/A-101"]


def test_a_scanned_set_gives_no_callouts_and_says_why():
    refs = _refs(_built(scanned=(0, 1, 2, 3, 4)))
    assert refs.callouts == [] and refs.views == {}
    assert refs.notes == [
        "5 sheet(s) have no usable text: their callouts are not in this "
        "list", "no callouts were recognised in the text"]


def test_a_document_keeps_its_cross_references_once_read():
    with open_document(_built().pdf) as doc:
        refs = doc.cross_references()
        assert doc.cross_references() is refs
        again = cross_references(doc)
        assert again.counts() == refs.counts()
        assert [c.label for c in again.callouts] == \
            [c.label for c in refs.callouts]


def test_callouts_are_in_reading_order_within_a_page():
    refs = _refs(_built())
    keys = [(c.page, c.bbox[1], c.bbox[0]) for c in refs.callouts]
    assert keys == sorted(keys)


@pytest.mark.parametrize("text, found", [
    ("SEE 3/A-501", [("3", "A-501")]),
    ("SEE 4/A5.01 AND 12/A5.02 FOR HEAD", [("4", "A5.01"), ("12", "A5.02")]),
    ("SECTION A / S-201", [("A", "S-201")]),
    ("DETAIL AA/C1.1", [("AA", "C1.1")]),
    ("SEE 4/50.01", [("4", "50.01")]),
    ("1/2 IN. PLATE", []), ("COORDINATE W/ S-201", []),
    ("A-501", []), ("3/4", []), ("NO/A-501", []), ("AND/OR A-501", []),
])
def test_what_is_read_as_a_callout_on_one_line(text, found):
    assert text_callouts(text) == found


@pytest.mark.parametrize("text, is_view", [
    ("7", True), ("12", True), ("A", True), ("AA", True), ("A1", True),
    ("3B", True), ("NO", False), ("OF", False), ("123", False),
    ("A-501", False), ("", False), ("TYP", False),
])
def test_what_is_read_as_a_view_number(text, is_view):
    assert bool(VIEW_ID.match(text)) is is_view


def test_a_scanned_sheet_named_by_its_page_label_is_there_but_unchecked():
    gt = _built(scanned=(3,))
    doc = pymupdf.open(stream=gt.pdf, filetype="pdf")
    doc.set_page_labels([{"startpage": i, "prefix": name, "style": "",
                          "firstpagenum": 1}
                         for i, name in enumerate(gt.numbers)])
    data = doc.tobytes()
    doc.close()
    with open_document(data) as opened:
        refs = opened.cross_references()
    to_details = [c for c in refs.callouts if c.target_sheet == "A-501"]
    assert len(to_details) == 6
    for c in to_details:                 # the sheet is there; its views are
        assert c.status == CANNOT_TELL and c.target_page == 3   # not read
        assert c.why == ("A-501 has no text layer, so its views cannot be "
                         "checked from the text")
    # every sheet has a name now, so a sheet no page carries is missing
    missing = [c for c in refs.callouts if c.target_sheet == "A-301"]
    assert [c.status for c in missing] == [UNRESOLVED]
    assert missing[0].why == "there is no sheet A-301 in this set"
    assert refs.notes == ["1 sheet(s) have no usable text: their callouts "
                          "are not in this list"]
