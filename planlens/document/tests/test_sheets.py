"""The sheet index, on a made-up drawing set.

Truth is the fixture's own statement of what it drew
(:mod:`planlens.testing.sheet_set_fixtures`): five sheets, three title
blocks, five ways of numbering, the text written in a shuffled order.
"""

import functools
import re

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.sheets import (  # noqa: E402
    HOW_FURTHER_SLOT, HOW_SIZE, HOW_SLOT, NUMBER_LABEL,
    SHEET_COUNT, SHEET_NUMBER, discipline, normalize_number, number_shape,
    sheet_index,
)
from planlens.testing import build_synthetic_submittal  # noqa: E402
from planlens.testing.sheet_set_fixtures import (  # noqa: E402
    SCHEMES, SHEET, build_sheet_set, sketch_pdf,
)

SET_STYLES = ("strip", "bottom", "inline", "turned")


def _labelled(pdf, labels):
    """``pdf`` with a PDF page label on each page."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    doc.set_page_labels([{"startpage": i, "prefix": label, "style": "",
                          "firstpagenum": 1}
                         for i, label in enumerate(labels)])
    data = doc.tobytes()
    doc.close()
    return data


@functools.lru_cache(maxsize=None)
def _built(style="strip", numbers="dash", scanned=(), only=None, seed=7):
    return build_sheet_set(style, numbers, scanned=scanned, only=only,
                           seed=seed)


def _index(gt):
    with open_document(gt.pdf) as doc:
        return doc.sheet_index()


@pytest.mark.parametrize("numbers", sorted(SCHEMES))
@pytest.mark.parametrize("style", SET_STYLES)
def test_every_sheet_has_its_number_title_revision_and_count(style, numbers):
    gt = _built(style, numbers)
    index = _index(gt)
    assert [r.page for r in index.rows] == [0, 1, 2, 3, 4]
    assert [r.number for r in index.rows] == gt.numbers
    assert [r.title for r in index.rows] == gt.titles
    assert [r.revision for r in index.rows] == gt.revisions
    assert [r.count for r in index.rows] == gt.counts
    assert index.not_indexed == [] and index.notes == []
    assert index.n_pages == 5


@pytest.mark.parametrize("style", SET_STYLES)
def test_the_number_is_read_from_the_place_the_sheets_share(style):
    index = _index(_built(style))
    for row in index.rows:
        assert row.how == "title-block slot"
        assert row.confidence > 0.9
        x0, y0, x1, y1 = row.bbox
        # in the title block: the right strip, or the band across the bottom
        assert x0 > SHEET[0] - 330 if style != "bottom" \
            else y0 > SHEET[1] - 150
    # one line of the title block: the same height on every sheet, or for
    # lettering that reads up the sheet the same distance from its edge
    across = (0, 2) if style == "turned" else (1, 3)
    places = {tuple(round(r.bbox[i]) for i in across) for r in index.rows}
    assert len(places) == 1


@pytest.mark.parametrize("numbers", sorted(SCHEMES))
@pytest.mark.parametrize("style", SET_STYLES + ("count_only",))
def test_a_sheet_count_is_never_a_sheet_number(style, numbers):
    index = _index(_built(style, numbers))
    for row in index.rows:
        assert row.count and re.fullmatch(r"\d of 5", row.count)
        if row.number is not None:
            assert not SHEET_COUNT.search(row.number)
            assert not re.fullmatch(r"\d+", row.number)


def test_sheets_that_print_only_a_count_are_listed_without_a_number():
    """No title block prints a number. The plans and details name
    themselves in their view titles, and that does not number them."""
    index = _index(_built("count_only"))
    assert [r.number for r in index.rows] == [None] * 5
    assert [r.count for r in index.rows] == [f"{n} of 5" for n in range(1, 6)]
    for row in index.rows:
        assert row.reason == ("no sheet number found in the title block's "
                              "text (it prints a sheet count)")
        assert row.title is None and row.how is None
    assert index.notes == ["5 of 5 sheets could not be indexed from their "
                           "text"]
    # the same on the sheets of planlens' made-up submittal: "SHEET 1 OF 2"
    with open_document(build_synthetic_submittal().pdf) as doc:
        rows = doc.sheet_index().rows
    assert [(r.page, r.number, r.count) for r in rows] == [
        (8, None, "1 of 2"), (9, None, "2 of 2")]


@pytest.mark.parametrize("style, numbers, role, how", [
    ("strip", "dash", 1, "label"),          # the value in the cell under
    ("bottom", "dot", 2, "label"),          # "DWG NO." and the cell beside
    ("bottom", "civil", 3, "label"),
    ("inline", "suffix", 2, "label"),       # "SHEET NO. S-201A" on one line
    ("inline", "decimal", 2, "label"),      # "SHEET NO. 10.31A"
    ("turned", "suffix", 2, "label"),       # read up the sheet
    ("turned", "dot", 3, "label"),
    ("bare", "civil", 1, "largest lettering in the title-block corner"),
    ("bare", "decimal", 2, "largest lettering in the title-block corner"),
])
def test_one_sheet_on_its_own_is_read_by_its_label_or_its_size(
        style, numbers, role, how):
    gt = _built(style, numbers, only=role)
    index = _index(gt)
    assert len(index.rows) == 1
    row = index.rows[0]
    assert row.number == gt.numbers[0] == SCHEMES[numbers][0][role]
    assert row.how == how
    assert row.confidence < 0.9          # one sheet is weaker than a set
    assert row.revision == gt.revisions[0]
    # a title only where a label names it: one sheet cannot tell its title
    # from the project's name
    assert row.title == (gt.titles[0]
                         if style in ("strip", "bottom", "turned") else None)


def test_one_sheet_that_prints_only_a_count_is_not_indexed():
    index = _index(_built("count_only", only=4))        # the schedule
    row = index.rows[0]
    assert row.number is None and row.count == "5 of 5"
    assert row.reason.endswith("(it prints a sheet count)")
    # a plan names itself in its view title, which does not number it
    plan = _index(_built("count_only", only=1)).rows[0]
    assert (plan.number, plan.how) == (None, None)


def test_the_order_of_the_text_does_not_matter():
    first = [r.to_dict() for r in _index(_built(seed=1)).rows]
    second = [r.to_dict() for r in _index(_built(seed=99)).rows]
    assert first == second
    with open_document(_built(seed=1).pdf) as a, \
            open_document(_built(seed=99).pdf) as b:
        assert [ln.text for ln in a._page_text(1)[0]] != \
            [ln.text for ln in b._page_text(1)[0]]


def test_a_sheet_with_no_text_is_listed_and_not_guessed():
    gt = _built(scanned=(3,))
    index = _index(gt)
    assert [r.number for r in index.rows] == [
        "G-001", "A-101", "A-102", None, "A-601"]
    blind = index.rows[3]
    assert "no text layer" in blind.reason and "looking" in blind.reason
    assert blind.title is None and blind.revision is None
    assert blind.count is None and blind.bbox is None
    assert blind.to_dict() == {"page": 3, "reason": blind.reason}
    assert index.notes == ["1 of 5 sheets could not be indexed from their "
                           "text"]
    assert index.page_of("A-501") is None


def test_a_scanned_set_has_every_sheet_listed_and_none_numbered():
    index = _index(_built(scanned=(0, 1, 2, 3, 4)))
    assert len(index.rows) == 5 and index.indexed == []
    assert all("no text layer" in r.reason for r in index.rows)
    assert index.numbers() == []


def test_lookups_by_number_and_by_page():
    index = _index(_built("inline", "suffix"))
    assert index.numbers() == ["S-001", "S-201", "S-201A", "S-501", "S-601"]
    assert index.page_of("S-201A") == 2 and index.page_of("s-201a") == 2
    assert index.page_of("S-201") == 1
    assert index.page_of("S-999") is None
    assert index.number_of(3) == "S-501" and index.number_of(40) is None
    assert normalize_number(" a-501 ") == "A-501"
    assert number_shape("S-201A") == "A-999A"
    assert number_shape("10.31") == "99.99"


def test_a_number_printed_on_two_pages_is_noted():
    gt = _built()
    twice = pymupdf.open(stream=gt.pdf, filetype="pdf")
    twice.insert_pdf(pymupdf.open(stream=gt.pdf, filetype="pdf"),
                     from_page=1, to_page=1)
    with open_document(twice.tobytes()) as doc:
        index = doc.sheet_index()
    twice.close()
    assert [r.number for r in index.rows][-1] == "A-101"
    assert index.notes == ["sheet number A-101 is read on pages 1, 5"]
    assert index.duplicates() == {"A-101": [1, 5]}
    assert index.page_of("A-101") == 1


def test_a_document_keeps_its_index_once_read():
    with open_document(_built().pdf) as doc:
        assert doc.sheet_index() is doc.sheet_index()
        assert sheet_index(doc).numbers() == doc.sheet_index().numbers()


def test_a_report_on_letter_pages_has_no_sheets():
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Section {n + 1}. Ground conditions",
                         fontsize=11)
        page.insert_text((500, 760), f"Page {n + 1} of 3", fontsize=9)
    with open_document(doc.tobytes()) as opened:
        index = opened.sheet_index()
    doc.close()
    assert index.rows == [] and index.n_pages == 3
    assert index.notes == ["no drawing sheets in this document"]


@pytest.mark.parametrize("text, found", [
    ("S-201A", ["S-201A"]), ("A5.01", ["A5.01"]), ("C1.1", ["C1.1"]),
    ("10.31A", ["10.31A"]), ("G001", ["G001"]), ("FP-101", ["FP-101"]),
    ("SHEET NO. S-201A", ["S-201A"]), ("DWG NO: A-101.", ["A-101"]),
    ("3", []), ("2031-01-12", []), ("W12X26", []), ("SCALE 1:100", []),
    ("PROJECT NO. 2417", []), ("SEE 4/A-501", []),
    ("A1-01", ["A1-01"]), ("SHEET # A2-03", ["A2-03"]),
])
def test_what_is_shaped_like_a_sheet_number(text, found):
    assert SHEET_NUMBER.findall(text) == found


# -- the PDF's own page labels ------------------------------------------------

LABELS = ["G-001 COVER SHEET", "A-101 - FIRST FLOOR PLAN", "A-102",
          "A-501 DOOR AND WINDOW DETAILS", "5"]


def test_a_scanned_sheet_takes_its_number_from_the_page_label_and_says_so():
    scan = _built(scanned=(0, 1, 2, 3, 4))
    with open_document(_labelled(scan.pdf, LABELS)) as doc:
        index = doc.sheet_index()
    assert [r.number for r in index.rows] == [
        "G-001", "A-101", "A-102", "A-501", None]
    assert [r.title for r in index.rows] == [
        "COVER SHEET", "FIRST FLOOR PLAN", None, "DOOR AND WINDOW DETAILS",
        None]
    for row in index.rows[:4]:
        assert row.how == "PDF page label (no text layer on the sheet)"
        assert row.confidence == 0.6 and row.reason is None
        assert row.bbox is None and row.revision is None
    last = index.rows[4]                    # a label that only counts pages
    assert last.number is None and "no text layer" in last.reason
    assert [r.readable for r in index.rows] == [False] * 5
    assert len(index.unread) == 5 and len(index.not_indexed) == 1
    assert index.notes == [
        "1 of 5 sheets could not be indexed from their text",
        "4 of 5 sheets are numbered from the PDF's page labels, not read "
        "from their title blocks"]


def test_a_page_label_names_a_sheet_whose_title_block_prints_no_number():
    gt = _built("count_only")
    with open_document(_labelled(gt.pdf, LABELS)) as doc:
        index = doc.sheet_index()
    assert [r.number for r in index.rows] == [
        "G-001", "A-101", "A-102", "A-501", None]
    assert index.rows[1].how == "PDF page label"
    assert index.rows[1].count == "2 of 5" and index.rows[1].readable
    assert index.rows[4].reason.endswith("(it prints a sheet count)")
    assert index.rows[4].count == "5 of 5"


def test_a_page_label_never_replaces_what_the_title_block_prints():
    wrong = ["X-901 OLD NAME", "X-902", "X-903", "X-904", "X-905"]
    gt = _built()
    with open_document(_labelled(gt.pdf, wrong)) as doc:
        index = doc.sheet_index()
    assert [r.number for r in index.rows] == gt.numbers
    assert [r.title for r in index.rows] == gt.titles
    assert {r.how for r in index.rows} == {"title-block slot"}
    assert index.notes == []


# -- what a sheet's number is not, and sheets read some other way ------------

def _strip_set(numbers, extra=None, of=None):
    """Sheets in the strip title block, numbered ``numbers`` (``None``: the
    number cell left blank); ``extra(sheet, number)`` draws the rest."""
    of = of or len(numbers)

    def paint(i):
        def draw(s, _i):
            s.strip_block(numbers[i], f"SHEET TITLE {i}", i + 1, of)
            if extra is not None:
                extra(s, numbers[i])
        return draw
    return [paint(i) for i in range(len(numbers))]


def _consultant(number, title, size=14.0, note=None):
    def draw(s, _i):
        s.bottom_block(number, title, number_size=size)
        s.view_title(330, 1300, "1", number, title)
        if note:
            s.text(1800, 300, "GENERAL NOTES", 9, bold=True)
            s.text(1800, 320, note, 7)
    return draw


def _sketched(draw):
    with open_document(sketch_pdf(draw)) as doc:
        return doc.sheet_index()


@pytest.mark.parametrize("text, is_label", [
    ("SHEET NO.", True), ("DWG NO. A-101", True), ("SHEET #", True),
    ("DWG #: A-101", True), ("SHEET NUMBER: S-201A REV 2", True),
    ("1. REFER TO DWG NO. S-201 FOR TYPICAL DETAILS", False),
    ("SEE SHEET NO. A-501 FOR WALL TYPES", False),
])
def test_a_label_is_a_short_line_or_the_head_of_one(text, is_label):
    from planlens.document.sheets import _label_in
    assert (_label_in(text) is not None) is is_label
    assert NUMBER_LABEL.search(text)                # each holds the words


@pytest.mark.parametrize("size, read", [(14.0, False), (28.0, True)])
def test_a_sheet_number_named_in_a_note_is_not_the_sheet_number(size, read):
    """Two consultant sheets beside four in the strip block; one has a
    general note naming another drawing ("REFER TO DWG NO. S-201"). Its
    own number is read from its large lettering, or not at all; never the
    note's."""
    draw = _strip_set(["G-001", "A-101", "A-102", "A-501"], of=6) + [
        _consultant("S-201", "FOUNDATION PLAN", size),
        _consultant("S-202", "FRAMING PLAN", size,
                    note="1. REFER TO DWG NO. S-201 FOR TYPICAL DETAILS")]
    index = _sketched(draw)
    assert [r.number for r in index.rows] == [
        "G-001", "A-101", "A-102", "A-501"] + (
        ["S-201", "S-202"] if read else [None, None])
    if read:
        assert [r.how for r in index.rows][4:] == [HOW_SIZE, HOW_SIZE]
        assert [r.title for r in index.rows][4:] == ["FOUNDATION PLAN",
                                                     "FRAMING PLAN"]
    assert index.duplicates() == {}
    assert not any("read on pages" in note for note in index.notes)


def test_view_titles_never_give_or_change_a_sheet_number():
    """A view title mark names a sheet, and so does a callout with a word
    beside it ("TYP.", "SIM."): neither is the sheet's number. A one-sheet
    file read as A-101 stays A-101 though its marks all name A-501; a
    consultant's sheet read as S-205 stays S-205 though its view titles
    name S-202; a sheet with no number read gets none from them."""
    def one(s, _i):
        s.page.draw_rect((36, 36, 2412, 1548), width=1.5)
        s.text(2150, 1450, "A-101", 40, bold=True)
        s.text(400, 1450, "PROJECT P-100", 8)
        s.view_title(600, 600, "3", "A-501", "TYP.")
        s.view_title(1200, 600, "4", "A-501", "SIM.")

    row = _sketched([one]).rows[0]
    assert (row.number, row.how) == ("A-101", HOW_SIZE)

    def odd(s, _i):
        s.bottom_block("S-205", "FRAMING PLAN", number_size=28.0)
        s.view_title(330, 1300, "1", "S-202", "FRAMING PLAN")
        s.view_title(1030, 1300, "2", "S-202", "BRACING ELEVATION")

    def unread(s, _i):
        s.bottom_block("S-206", "ROOF PLAN", number_size=14.0)
        s.view_title(330, 1300, "1", "S-203", "ROOF PLAN")

    index = _sketched(_strip_set(["G-001", "A-101", "A-102", "A-501"],
                                 of=6) + [odd, unread])
    assert [(r.number, r.how) for r in index.rows][4:] == [
        ("S-205", HOW_SIZE), (None, None)]
    assert not any("view title" in note for note in index.notes)


def test_a_second_title_block_layout_is_read_by_its_own_slot():
    """Six sheets in the strip block and four in a consultant's block
    across the bottom, all of one size: the four are left by the first
    slot and form one of their own."""
    arch = ["G-001", "A-101", "A-102", "A-201", "A-301", "A-501"]
    draw = _strip_set(arch, of=10) + [
        _consultant(n, f"FRAMING {n[2:]}")
        for n in ("S-001", "S-101", "S-201", "S-501")]
    index = _sketched(draw)
    assert index.numbers() == arch + ["S-001", "S-101", "S-201", "S-501"]
    assert [r.how for r in index.rows] == [HOW_SLOT] * 6 + [
        HOW_FURTHER_SLOT] * 4
    first = {r.confidence for r in index.rows[:6]}
    assert max(r.confidence for r in index.rows[6:]) < min(first)
    assert [r.title for r in index.rows][6:] == [
        "FRAMING 001", "FRAMING 101", "FRAMING 201", "FRAMING 501"]
    assert index.notes == []


def _key_plan_sheet(number, k):
    """A consultant's sheet whose number is lettered wide, one letter at a
    time (not read), and whose key plan prints the area it shows, "C1",
    alone at the same place on each."""
    def draw(s, _i):
        s.page.draw_rect((36, 36, 2412, 1548), width=1.5)
        s.text(900, 1470, "DRAWING TITLE", 6)
        s.text(900, 1490, f"FRAMING PLAN AREA C{k}", 11, bold=True)
        s.text(1700, 1460, "KEY PLAN", 6)
        s.text(1740, 1505, f"C{k}", 8)
        for j, ch in enumerate(number):
            s.text(2200 + 34 * j, 1500, ch, 28, bold=True)
    return draw


def test_a_further_slot_must_have_the_shape_of_the_sets_numbers():
    """Key-plan labels "C1", "C2", "C3" at one place on three sheets differ
    on each, as a sheet number would; beside a set numbered "A-101" they
    are not one, and the three sheets are left unindexed."""
    arch = ["G-001", "A-101", "A-102", "A-201", "A-301", "A-501"]
    draw = _strip_set(arch, of=9) + [
        _key_plan_sheet(n, k + 1)
        for k, n in enumerate(("S-101", "S-102", "S-103"))]
    index = _sketched(draw)
    assert [r.number for r in index.rows] == arch + [None] * 3
    assert index.notes == ["3 of 9 sheets could not be indexed from their "
                           "text"]


def _report(footer, pages=6, size=(1224.0, 792.0)):
    doc = pymupdf.open()
    for p in range(pages):
        page = doc.new_page(width=size[0], height=size[1])
        y = 80.0
        for _k in range(20):
            page.insert_text((72, y), "The bearing values follow Table 3.2 "
                             "and Figure A-1; see sheet C1.1.", fontsize=10)
            y += 26.0
        page.insert_text((1040, 760), footer(p // 3 + 1, p % 3 + 1),
                         fontsize=9)
    data = doc.tobytes()
    doc.close()
    return data


@pytest.mark.parametrize("footer", [
    lambda ch, pp: f"Page {ch}.{pp}",
    lambda ch, pp: f"Fig. {ch}.{pp}",
    lambda ch, pp: f"Report part {ch}.{pp} draft",
])
def test_a_report_on_large_pages_is_given_no_sheet_numbers(footer):
    """An 11 x 17 report counts as sheets by its size; its numbered
    footers are not sheet numbers: one after PAGE, FIG and the like is
    something else, and one inside a longer line is no title block's."""
    with open_document(_report(footer)) as doc:
        index = doc.sheet_index()
    assert len(index.rows) == 6 and index.indexed == []
    assert {r.reason for r in index.rows} == {
        "no sheet number found in the title block's text"}


def test_one_sparse_sheet_is_read_by_its_large_number():
    """A sheet holding little but its number: the number's own line is not
    counted in the lettering it must stand out from."""
    def sparse(s, _i):
        s.page.draw_rect((36, 36, 2412, 1548), width=1.5)
        s.text(2150, 1450, "A-101", 40, bold=True)
        s.text(400, 1450, "PROJECT P-100", 8)

    row = _sketched([sparse]).rows[0]
    assert (row.number, row.how) == ("A-101", HOW_SIZE)


def test_numbers_read_on_two_pages_are_noted_once():
    gt = _built()
    doubled = pymupdf.open(stream=gt.pdf, filetype="pdf")
    doubled.insert_pdf(pymupdf.open(stream=gt.pdf, filetype="pdf"))
    with open_document(doubled.tobytes()) as doc:
        index = doc.sheet_index()
    doubled.close()
    assert index.numbers() == gt.numbers * 2
    assert index.notes == [
        "every sheet number in this document is read on two pages (5 "
        "numbers: G-001 (pages 0, 5), A-101 (pages 1, 6), A-102 (pages 2, "
        "7), and 2 more); the document may hold the set twice over"]
    # two numbers of several read twice: one note, the count and the first
    some = pymupdf.open(stream=gt.pdf, filetype="pdf")
    some.insert_pdf(pymupdf.open(stream=gt.pdf, filetype="pdf"),
                    from_page=1, to_page=2)
    with open_document(some.tobytes()) as doc:
        notes = doc.sheet_index().notes
    some.close()
    assert notes == ["2 sheet numbers are each read on more than one page: "
                     "A-101 (pages 1, 5), A-102 (pages 2, 6)"]


@pytest.mark.parametrize("number, part", [
    ("S-201A", "S"), ("FP-101", "FP"), ("A5.01", "A"), ("G001", "G"),
    ("10.31", "10"), ("10.31A", "10"), ("a-501", "A")])
def test_the_discipline_of_a_sheet_number(number, part):
    assert discipline(number) == part
