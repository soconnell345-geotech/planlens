"""PL-37, the sheet's printed scale in the sheet index's rows.

Made-up sheets only. Each set here is three or more 34 x 22 in sheets with
a title block down the right edge; the forms differ in how the block prints
its scale: a ratio on the label's line, an imperial scale in a cell apart
from its label, "AS NOTED", "NTS", and no scale at all. A view title out on
the sheet prints a scale of its own, which is never the sheet's.
"""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document                  # noqa: E402
from planlens.document import sheets                         # noqa: E402
from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402

W, H = 34.0 * 72, 22.0 * 72
X0 = W - 300.0                         # the title block's left edge


def _sheet(doc, number, title, scale_lines, view_scale='1/2" = 1\'-0"'):
    """One sheet: a view title with its own scale out on the sheet, and a
    title block whose scale is drawn by ``scale_lines``: (x, y, text)."""
    page = doc.new_page(width=W, height=H)
    page.insert_text((300, 400), "FRAMING PLAN", fontsize=12)
    page.insert_text((300, 414), f"SCALE {view_scale}", fontsize=7)
    # a view low on the sheet, inside the bands a title block is sought in
    page.insert_text((900, H - 120), "TYPICAL SECTION", fontsize=12)
    page.insert_text((900, H - 106), "SCALE: 1:5", fontsize=7)
    page.insert_text((X0, 200), "ALDER POINT ENGINEERS", fontsize=10)
    page.insert_text((X0, 240), "PROJECT", fontsize=6)
    page.insert_text((X0, 262), "MILLBROOK DEPOT", fontsize=14)
    page.insert_text((X0, H - 330), "SHEET TITLE", fontsize=6)
    page.insert_text((X0, H - 306), title, fontsize=13)
    for x, y, text in scale_lines:
        page.insert_text((x, y), text, fontsize=8)
    page.insert_text((X0, H - 130), "SHEET NO.", fontsize=6)
    page.insert_text((X0, H - 90), number, fontsize=30)


def _set(forms):
    """A set with one sheet per form; a form is the title block's scale
    lines as (dx, y, text) from the block's left edge."""
    doc = pymupdf.open()
    titles = ("GENERAL NOTES", "FOUNDATION PLAN", "ROOF FRAMING PLAN",
              "WALL SECTIONS", "PILE SCHEDULE", "SITE PLAN")
    for i, form in enumerate(forms):
        _sheet(doc, f"S-{101 + i}", titles[i],
               [(X0 + dx, y, text) for dx, y, text in form])
    data = doc.tobytes()
    doc.close()
    return data


Y = H - 230.0
RATIO = [(0, Y, "SCALE: 1:100")]
IMPERIAL_APART = [(0, Y, "SCALE"), (0, Y + 16, '1/4" = 1\'-0"')]
IMPERIAL_WORDS = [(0, Y, "SCALE"), (70, Y, "1/4 in = 1 ft")]
AS_NOTED = [(0, Y, "SCALE"), (0, Y + 16, "AS NOTED")]
NTS = [(0, Y, "SCALE: NTS")]
NO_SCALE = []
LABEL_ONLY = [(0, Y, "SCALE")]


def _rows(pdf):
    with open_document(pdf) as doc:
        return doc.sheet_index().rows


@pytest.fixture(scope="module")
def index():
    with open_document(_set([RATIO, IMPERIAL_APART, AS_NOTED, NO_SCALE,
                             NTS, IMPERIAL_WORDS])) as doc:
        yield doc.sheet_index()


def _row(index, number):
    return next(r for r in index.rows if r.number == number)


def test_a_ratio_on_the_labels_line_is_given_as_printed(index):
    row = _row(index, "S-101")
    assert row.scale == "1:100"
    assert row.scale_how == sheets.HOW_SCALE_LINE
    assert row.to_dict()["scale"] == "1:100"
    assert row.to_dict()["scale_how"] == sheets.HOW_SCALE_LINE


def test_an_imperial_scale_in_a_cell_apart_from_its_label(index):
    under = _row(index, "S-102")
    assert under.scale == '1/4" = 1\'-0"'
    assert under.scale_how == sheets.HOW_SCALE_CELL
    beside = _row(index, "S-106")
    assert beside.scale == "1/4 in = 1 ft"
    assert beside.scale_how == sheets.HOW_SCALE_CELL


def test_as_noted_and_nts_are_given_as_the_block_says(index):
    assert _row(index, "S-103").scale == "AS NOTED"
    assert _row(index, "S-103").scale_how == sheets.HOW_SCALE_CELL
    assert _row(index, "S-105").scale == "NTS"


def test_no_scale_in_the_title_block_gives_none_not_a_views_scale(index):
    # every sheet has two view titles with a scale; this one's title block
    # prints none
    row = _row(index, "S-104")
    assert row.scale is None and row.scale_how is None
    assert "scale" not in row.to_dict()
    assert "scale_how" not in row.to_dict()
    # and no row took a view's scale
    assert {r.scale for r in index.rows} == {
        "1:100", '1/4" = 1\'-0"', "AS NOTED", None, "NTS", "1/4 in = 1 ft"}


def test_the_rest_of_the_row_is_read_as_it_was(index):
    assert [r.number for r in index.rows] == [
        f"S-{n}" for n in range(101, 107)]
    assert _row(index, "S-102").title == "FOUNDATION PLAN"
    assert {r.how for r in index.rows} == {sheets.HOW_SLOT}


def test_a_label_with_nothing_readable_by_it_gives_none():
    # a bare label, a label over words that are no scale, and two labels
    # that disagree: nothing is chosen
    rows = _rows(_set([
        LABEL_ONLY, [(0, Y, "SCALE"), (0, Y + 16, "SEE PLAN")],
        [(0, Y, "SCALE: 1:100"), (0, Y + 40, "SCALE: 1:50")], RATIO]))
    assert [r.scale for r in rows] == [None, None, None, "1:100"]


def test_the_fixture_sets_view_scales_are_never_a_sheets_scale():
    # the shared fixture sets print a scale under each view title and none
    # in the title block
    for style in ("strip", "bottom", "inline", "turned"):
        rows = _rows(build_sheet_set(style=style).pdf)
        assert rows and all(r.scale is None for r in rows), style


def test_one_sheet_alone_says_its_title_block_was_not_located():
    row = _rows(_set([RATIO]))[0]
    assert row.number == "S-101"
    assert row.scale == "1:100"
    assert row.scale_how == sheets.HOW_SCALE_CORNER
    # and without a scale by the corner's label, none: the view titles'
    # scales stand elsewhere on the sheet
    assert _rows(_set([NO_SCALE]))[0].scale is None


def test_the_scale_value_is_read_by_the_measuring_tools_reader():
    from planlens.document.scale import parse_ratio
    for printed in ("1:100", '1/4" = 1\'-0"', "1 in = 20 ft", "1/4 in = 1 ft"):
        assert parse_ratio(printed)
        assert sheets._scale_value(f"  {printed} ") == printed
    for words in ("AS NOTED", "As Shown", "NTS", "N.T.S.", "NONE",
                  "NOT TO SCALE"):
        assert sheets._scale_value(words) == words
    for other in ("", "SEE PLAN", "BAR", "S-201", "12", "FULL"):
        assert sheets._scale_value(other) is None


def test_the_tool_shows_the_scale_and_how_only_for_a_host_that_asked():
    from planlens.tools import SHEET_TOOL_SPECS, TOOL_SPECS, ReviewToolkit
    pdf = _set([RATIO, IMPERIAL_APART, AS_NOTED, NO_SCALE])

    def kit(**kw):
        return ReviewToolkit(resolve_source=lambda key: pdf, **kw)

    def call(k, name, **args):
        return json.loads(k.call_json(name, args))

    asked = kit(sheet_tools=True)
    handle = call(asked, "open_document", source="set.pdf")["handle"]
    by = {r["sheet"]: r
          for r in call(asked, "sheet_index", handle=handle)["sheets"]}
    assert by["S-101"]["scale"] == "1:100"
    assert by["S-101"]["scale_from"] == sheets.HOW_SCALE_LINE
    assert by["S-102"]["scale"] == '1/4" = 1\'-0"'
    assert by["S-102"]["scale_from"] == sheets.HOW_SCALE_CELL
    assert by["S-103"]["scale"] == "AS NOTED"
    # nothing but the two keys is new in a row, and a sheet whose title
    # block prints no scale has neither
    assert set(by["S-101"]) - set(by["S-104"]) == {"scale", "scale_from"}
    assert set(by["S-104"]) <= set(by["S-101"])
    # the specification says it in one word, and only the sheet tool's
    spec = next(s for s in SHEET_TOOL_SPECS if s["name"] == "sheet_index")
    assert "its title, scale and latest revision" in spec["description"]
    assert all(s["name"] != "sheet_index" for s in TOOL_SPECS)
    # a toolkit that did not ask has no such tool, and its open_document
    # brings no index
    plain = kit()
    opened = call(plain, "open_document", source="set.pdf")
    assert "sheet_index" not in opened
    assert "error" in call(plain, "sheet_index", handle=opened["handle"])


def test_a_title_block_across_the_bottom_with_the_value_beside_its_label():
    # the block runs along the bottom edge; a key plan the set repeats up
    # the right edge prints "SCALE: NTS", which is not the sheet's
    doc = pymupdf.open()
    scales = ('1" = 20\'', "1:200", "AS SHOWN", None)
    for i, scale in enumerate(scales):
        page = doc.new_page(width=W, height=H)
        page.insert_text((W - 260, 300), "KEY PLAN", fontsize=10)
        page.insert_text((W - 260, 314), "SCALE: NTS", fontsize=7)
        y = H - 70.0
        page.insert_text((60, y), "ALDER POINT ENGINEERS", fontsize=10)
        page.insert_text((480, y - 20), "PROJECT", fontsize=6)
        page.insert_text((480, y), "MILLBROOK DEPOT", fontsize=14)
        page.insert_text((1080, y - 20), "DRAWING TITLE:", fontsize=6)
        page.insert_text((1080, y), ("GRADING PLAN", "DRAINAGE PLAN",
                                     "SITE DETAILS", "EROSION CONTROL")[i],
                         fontsize=13)
        page.insert_text((1640, y), "SCALE:", fontsize=7)
        if scale:
            page.insert_text((1690, y), scale, fontsize=8)
        page.insert_text((2130, y - 20), "DWG NO.", fontsize=6)
        page.insert_text((2130, y + 10), f"C-{201 + i}", fontsize=26)
    pdf = doc.tobytes()
    doc.close()
    rows = _rows(pdf)
    assert [r.number for r in rows] == ["C-201", "C-202", "C-203", "C-204"]
    assert [r.scale for r in rows] == ['1" = 20\'', "1:200", "AS SHOWN", None]
    assert {r.scale_how for r in rows[:3]} == {sheets.HOW_SCALE_CELL}
