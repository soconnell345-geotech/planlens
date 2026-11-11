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
    CANNOT_TELL, NOT_IN_DOCUMENT, RESOLVED, UNRESOLVED, VIEW_ID,
    cross_references, text_callouts,
)
from planlens.testing.sheet_set_fixtures import (  # noqa: E402
    SCHEMES, build_sheet_set, sketch_pdf,
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
    assert refs.counts() == {"callouts": 9, UNRESOLVED: 1,
                             NOT_IN_DOCUMENT: 1, CANNOT_TELL: 1, RESOLVED: 6}
    for status in (UNRESOLVED, NOT_IN_DOCUMENT, CANNOT_TELL):
        assert sorted(c.label for c in refs.with_status(status)) == \
            gt.with_status(status)
    assert refs.views == gt.views
    assert refs.view_titles == gt.view_titles
    assert refs.notes == []


def test_an_unresolved_callout_says_what_is_missing_and_where_it_is():
    gt = _built()
    by_label = {c.label: c for c in _refs(gt).callouts
                if c.status != RESOLVED}
    view = by_label["5/A-501"]
    assert view.why == "view marks recognised on A-501: 1 to 4; none for 5"
    assert (view.page, view.sheet, view.target_page) == (1, "A-101", 3)
    x0, y0, x1, y1 = view.bbox                   # the bubble drawn at 1540,760
    assert x0 < 1540 < x1 and y0 < 760 < y1
    assert x1 - x0 < 60 and y1 - y0 < 60
    sheet = by_label["1/A-301"]
    assert sheet.status == NOT_IN_DOCUMENT
    assert sheet.why == ("no sheet A-301 was read in this document; the "
                         "document holds 5 sheets")
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
    assert all(c.why == "no sheet A-501 was read in this document, but "
               "page 3 has no usable text: view its title block"
               for c in to_details)
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
    # every sheet has a name now, so a sheet no page carries is not here
    missing = [c for c in refs.callouts if c.target_sheet == "A-301"]
    assert [c.status for c in missing] == [NOT_IN_DOCUMENT]
    assert missing[0].why == ("no sheet A-301 was read in this document; "
                              "the document holds 5 sheets")
    assert refs.notes == ["1 sheet(s) have no usable text: their callouts "
                          "are not in this list"]


# -- unresolved only where the text shows it -----------------------------------

def _set(numbers, extra, of=None, block=None):
    """Sheets in the strip title block numbered ``numbers`` (``None``: the
    cell left blank); ``extra(sheet, number)`` draws each sheet's rest;
    ``block(sheet, number)`` draws a title block in place of the strip."""
    of = of or len(numbers)

    def paint(i):
        def draw(s, _i):
            if block is None or not block(s, numbers[i]):
                s.strip_block(numbers[i], f"SHEET TITLE {i}", i + 1, of)
            extra(s, numbers[i])
        return draw
    return [paint(i) for i in range(len(numbers))]


def _checked(draw):
    with open_document(sketch_pdf(draw)) as doc:
        return doc.cross_references()


def _verdicts(refs):
    return sorted((c.page, c.label, c.status) for c in refs.callouts)


ARCH = ["G-001", "A-101", "A-102", "A-501"]


def test_a_broken_callout_drawn_on_the_sheet_it_names_is_unresolved():
    """A bubble naming its own sheet is a view only with a title beside
    it: 9/A-501 drawn on A-501 with no title is a callout like any other,
    and A-501's views stay 1 and 2."""
    def extra(s, n):
        if n == "A-101":
            s.bubble(900, 300, "9", "A-501")
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")
            s.view_title(820, 620, "2", n, "SILL DETAIL")
            s.bubble(1500, 400, "9", "A-501")

    refs = _checked(_set(ARCH, extra))
    assert refs.views == {"A-501": ["1", "2"]}
    assert _verdicts(refs) == [(1, "9/A-501", UNRESOLVED),
                               (3, "9/A-501", UNRESOLVED)]
    assert {c.why for c in refs.callouts} == {
        "view marks recognised on A-501: 1, 2; none for 9"}


def test_views_titled_by_their_number_alone_are_not_called_missing():
    """A-501 titles its details with a bubble holding the view number
    only; a same-sheet callout 4/A-501 is not one of its views. Nothing
    about A-501's views can be said from the text."""
    def extra(s, n):
        if n == "A-101":
            for k, v in enumerate("123"):
                s.bubble(900 + 240 * k, 300, v, "A-501")
        if n == "A-501":
            for k, (x, y) in enumerate(((220, 620), (820, 620),
                                        (1420, 620), (220, 1300))):
                s.view_title(x, y, str(k + 1), n, f"DETAIL {k + 1}",
                             solo=True)
            s.bubble(1500, 400, "4", "A-501")

    refs = _checked(_set(ARCH, extra))
    assert "A-501" not in refs.views
    assert refs.unread_marks == {"A-501": ["1", "2", "3", "4"]}
    assert _verdicts(refs) == [(1, "1/A-501", CANNOT_TELL),
                               (1, "2/A-501", CANNOT_TELL),
                               (1, "3/A-501", CANNOT_TELL),
                               (3, "4/A-501", CANNOT_TELL)]
    assert refs.with_status(UNRESOLVED) == []


def test_a_lone_view_number_keeps_only_its_own_view_unsaid():
    """A-501 holds views 1 and 2 as full title bubbles and view 3 titled
    by its number alone: a callout to 3 cannot be told, and the reason
    says what is there; a callout to 7, which nothing on A-501 reads as,
    is unresolved."""
    def extra(s, n):
        if n == "A-101":
            for k, v in enumerate("137"):
                s.bubble(900 + 240 * k, 300, v, "A-501")
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")
            s.view_title(820, 620, "2", n, "SILL DETAIL")
            s.view_title(1420, 620, "3", n, "JAMB DETAIL", solo=True)

    refs = _checked(_set(ARCH, extra))
    assert refs.views == {"A-501": ["1", "2"]}
    assert refs.unread_marks == {"A-501": ["3"]}
    assert _verdicts(refs) == [(1, "1/A-501", RESOLVED),
                               (1, "3/A-501", CANNOT_TELL),
                               (1, "7/A-501", UNRESOLVED)]
    why = {c.label: c.why for c in refs.callouts}
    assert why["3/A-501"] == ("view marks recognised on A-501: 1, 2; none "
                              "for 3, but a 3 with words beside it is on "
                              "A-501 and was not read as a view title: view "
                              "it")
    assert why["7/A-501"] == "view marks recognised on A-501: 1, 2; none for 7"


def test_a_keynote_list_hides_no_view_but_its_own_numbers():
    """A detail sheet with details 1 to 4 and a keynote legend numbered 1
    to 6, each with its note beside it: 9/A-501 is a finding; 5/A-501 has
    a 5 with words beside it, so it cannot be told, and says so."""
    def extra(s, n):
        if n == "A-101":
            for k, v in enumerate("59"):
                s.bubble(900 + 240 * k, 300, v, "A-501")
        if n == "A-501":
            for k, (x, y) in enumerate(((220, 620), (820, 620),
                                        (1420, 620), (220, 1300))):
                s.view_title(x, y, str(k + 1), n, f"DETAIL {k + 1}")
            s.text(1820, 200, "KEYNOTES", 9, bold=True)
            for k in range(6):
                s.text(1820, 230 + 24 * k, str(k + 1), 7)
                s.text(1840, 230 + 24 * k, f"PROVIDE ITEM {k + 1} AS NOTED",
                       7)

    refs = _checked(_set(ARCH, extra))
    assert refs.views == {"A-501": ["1", "2", "3", "4"]}
    assert _verdicts(refs) == [(1, "5/A-501", CANNOT_TELL),
                               (1, "9/A-501", UNRESOLVED)]
    why = {c.label: c.why for c in refs.callouts}
    assert why["9/A-501"] == ("view marks recognised on A-501: 1 to 4; none "
                              "for 9")
    assert "a 5 with words beside it is on A-501" in why["5/A-501"]


@pytest.mark.parametrize("titled", [True, False])
def test_a_view_drawn_turned_is_read_in_its_own_direction(titled):
    """Detail 2 on A-501 is drawn on its side, its title mark reading up
    the sheet. With its title it is one of A-501's views; without one it
    is a view mark with no text, which the code does not see."""
    def extra(s, n):
        if n == "A-101":
            for k, v in enumerate("125"):
                s.bubble(900 + 240 * k, 300, v, "A-501")
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")
            s.turned_view_title(1420, 900, "2", n,
                                "JAMB DETAIL" if titled else None)

    refs = _checked(_set(ARCH, extra))
    if titled:
        assert refs.views == {"A-501": ["1", "2"]}
        assert refs.view_titles[("A-501", "2")] == "JAMB DETAIL"
        assert _verdicts(refs) == [(1, "1/A-501", RESOLVED),
                                   (1, "2/A-501", RESOLVED),
                                   (1, "5/A-501", UNRESOLVED)]
    else:
        assert refs.views == {"A-501": ["1"]}
        assert refs.unread_marks == {}
        assert _verdicts(refs) == [(1, "1/A-501", RESOLVED),
                                   (1, "2/A-501", UNRESOLVED),
                                   (1, "5/A-501", UNRESOLVED)]


def test_a_sheet_not_in_the_document_is_said_as_that_fact():
    """Whether a file is the whole set, the code cannot know: a callout to
    a sheet no page of it carries is not_in_document, never unresolved,
    with what is known beside it (how many sheets, and that a discipline
    is absent)."""
    def extra(s, n):
        if n == "A-101":
            s.bubble(900, 300, "3", "S-501")
            s.bubble(1140, 300, "1", "A-302")
            s.text(1560, 360, "SEE 2/S-201 FOR BEAM", 7)
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")

    refs = _checked(_set(ARCH, extra))
    assert _verdicts(refs) == [(1, "1/A-302", NOT_IN_DOCUMENT),
                               (1, "2/S-201", NOT_IN_DOCUMENT),
                               (1, "3/S-501", NOT_IN_DOCUMENT)]
    why = {c.label: c.why for c in refs.callouts}
    assert why["3/S-501"] == ("no sheet S-501 was read in this document; the "
                              "document holds 4 sheets and no S sheets")
    assert why["1/A-302"] == ("no sheet A-302 was read in this document; the "
                              "document holds 4 sheets")
    assert refs.counts()[UNRESOLVED] == 0
    # numbers with no letter: the series before the dot
    gt = build_sheet_set("strip", "decimal")
    doc = pymupdf.open(stream=gt.pdf, filetype="pdf")
    page = doc[1]
    page.draw_circle((1700, 500), 15.0, width=0.7)
    page.insert_text((1695, 497), "2", fontsize=9, fontname="hebo")
    page.insert_text((1687, 509), "40.01", fontsize=6)
    data = doc.tobytes()
    doc.close()
    with open_document(data) as opened:
        refs = opened.cross_references()
    other = [c for c in refs.callouts if c.target_sheet == "40.01"]
    assert [c.status for c in other] == [NOT_IN_DOCUMENT]
    assert other[0].why.endswith("and no sheets numbered 40.x")


def test_a_number_read_on_two_pages_keeps_a_missing_sheet_unsaid():
    """Two pages read as one number: one of them may be misread, so a
    callout to a number not in the index, or to the doubled number,
    cannot be told."""
    gt = _built()
    twice = pymupdf.open(stream=gt.pdf, filetype="pdf")
    twice.insert_pdf(pymupdf.open(stream=gt.pdf, filetype="pdf"),
                     from_page=3, to_page=3)
    with open_document(twice.tobytes()) as doc:
        refs = doc.cross_references()
    twice.close()
    by = {}
    for c in refs.callouts:
        by.setdefault(c.label, set()).add(c.status)
    assert by["1/A-301"] == {CANNOT_TELL}
    assert by["5/A-501"] == {CANNOT_TELL} and by["1/A-501"] == {CANNOT_TELL}
    assert refs.with_status(UNRESOLVED) == []
    why = {c.label: c.why for c in refs.callouts}
    assert why["1/A-301"] == ("no sheet A-301 among the sheets indexed, but "
                              "A-501 is read on pages 3, 5: one of those "
                              "may be A-301")
    assert why["1/A-501"] == ("A-501 is read on pages 3, 5, so which sheet "
                              "the callout names cannot be told")


def _with_cover(extra_on_cover):
    """A-101 calls 2/A-302 and 7/A-501; the cover has no number and
    ``extra_on_cover(sheet)`` draws whatever else it prints."""
    def extra(s, n):
        if n == "A-101":
            s.view_title(330, 1300, "1", n, "LEVEL 1 PLAN")
            s.bubble(900, 300, "2", "A-302")
            s.bubble(1140, 300, "7", "A-501")
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")
        if n is None:
            extra_on_cover(s)
    return _checked(_set([None] + ARCH[1:], extra))


def test_an_unnumbered_sheet_could_be_a_sheet_only_if_it_prints_its_number():
    """A cover with no number that prints A-302 nowhere is not A-302: the
    missing sheet is not_in_document, and the reason says the sheet left
    unindexed does not print it."""
    refs = _with_cover(lambda s: s.text(800, 800, "GENERAL NOTES", 12))
    assert _verdicts(refs) == [(1, "2/A-302", NOT_IN_DOCUMENT),
                               (1, "7/A-501", UNRESOLVED)]
    why = {c.label: c.why for c in refs.callouts}
    assert why["2/A-302"] == ("no sheet A-302 was read in this document; the "
                              "document holds 4 sheets; the 1 sheet(s) that "
                              "could not be indexed do not print A-302")


@pytest.mark.parametrize("printing", [
    lambda s: s.text(2200, 500, "A-302", 8),            # in its band
    lambda s: s.text(600, 900, "A-302 ENTRY DETAILS", 9),  # in an index
    lambda s: [s.text(2150 + 22 * k, 1450, ch, 20, bold=True)  # tracked
               for k, ch in enumerate("A-302")],
    lambda s: (s.text(2150, 1450, "A-", 20), s.text(2185, 1450, "302", 20)),
])
def test_an_unnumbered_sheet_that_prints_the_number_could_be_it(printing):
    """The whole page is searched, spaces aside, so a number lettered wide
    or in two pieces is found: the verdict is cannot_tell and the reason
    names the page to view."""
    refs = _with_cover(printing)
    missing = [c for c in refs.callouts if c.label == "2/A-302"]
    assert [c.status for c in missing] == [CANNOT_TELL]
    assert missing[0].why == ("no sheet A-302 was read in this document, but "
                              "page 0 could not be indexed and prints A-302: "
                              "view its title block")
    assert [c.label for c in refs.with_status(UNRESOLVED)] == ["7/A-501"]


def test_a_set_whose_title_blocks_print_no_number_has_no_callouts():
    refs = _refs(build_sheet_set("count_only"))
    assert refs.callouts == []
    assert refs.notes == ["no callouts were recognised in the text"]


def test_the_sheets_of_a_second_title_block_are_checked_like_the_rest():
    def struct(s, n):
        if n.startswith("S"):
            s.bottom_block(n, f"FRAMING {n[2:]}")
            return True
        return False

    def extra(s, n):
        s.view_title(330, 1300, "1", n, f"PLAN OF {n}")
        if n == "A-101":
            s.bubble(900, 300, "1", "A-302")
            s.bubble(1140, 300, "1", "S-101")

    numbers = ["G-001", "A-101", "A-102", "A-201", "A-301", "A-501",
               "S-001", "S-101", "S-201", "S-501"]
    refs = _checked(_set(numbers, extra, block=struct))
    assert _verdicts(refs) == [(1, "1/A-302", NOT_IN_DOCUMENT),
                               (1, "1/S-101", RESOLVED)]
    assert refs.views["S-101"] == ["1"]


def test_a_tag_over_larger_lettering_is_not_a_bubble():
    """A bubble's view number is lettered at least as large as its sheet
    number; a small number over a larger tag shaped like a sheet number
    is something else."""
    def extra(s, n):
        if n == "A-101":
            s.bubble(900, 300, "1", "A-501")
            s.bubble(1140, 300, "2", "A-501", view_size=5.0, sheet_size=9.0)
        if n == "A-501":
            s.view_title(220, 620, "1", n, "HEAD DETAIL")

    refs = _checked(_set(ARCH, extra))
    assert _verdicts(refs) == [(1, "1/A-501", RESOLVED)]
