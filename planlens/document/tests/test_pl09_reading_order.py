"""PL-09: a page set in columns whose file draws the lines ROW by row says so
in its warnings (a repair: the lines handed are unchanged), and
``column_order`` reads such a page column by column for a host that asks.
Made-up pages and wording only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.reading_order import (  # noqa: E402
    column_gutters, column_order, order_runs_across_columns,
)

WORDS = ("the bearing stratum lies below the made ground at a depth that "
         "varies across the site and was proved in every trial hole").split()

ACROSS = "reading order not reconstructed"


def _prose(i: int, n: int = 8) -> str:
    return " ".join(WORDS[(i + k) % len(WORDS)] for k in range(n))


def _pdf(draw) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    draw(page)
    data = doc.tobytes()
    doc.close()
    return data


def _columns(n_cols: int = 2, rows: int = 18, across: bool = True,
             heading: bool = True, top: float = 100.0):
    """A page of prose in ``n_cols`` columns, drawn row by row or column by
    column."""
    width = 468.0 / n_cols
    xs = [72 + c * width for c in range(n_cols)]
    words = 8 if n_cols == 2 else 5

    def draw(page):
        if heading:
            page.insert_text((72, 60), "A MADE-UP HEADING SET ACROSS EVERY "
                             "COLUMN OF THE PAGE", fontsize=13)
        cells = [(r, c) for r in range(rows) for c in range(n_cols)]
        if not across:
            cells.sort(key=lambda rc: (rc[1], rc[0]))
        for r, c in cells:
            page.insert_text((xs[c], top + 14 * r),
                             f"C{c}R{r} " + _prose(r + 3 * c, words),
                             fontsize=8)
    return _pdf(draw)


def _texts(lines):
    return [ln.text.split()[0] for ln in lines]


# -- the repair: the page says its order runs across the columns -------------

@pytest.mark.parametrize("n_cols", [2, 3])
def test_a_page_drawn_row_by_row_says_so(n_cols):
    with Document(content=_columns(n_cols)) as doc:
        pc = doc.page(0)
    notes = [w for w in pc.warnings if w.startswith(ACROSS)]
    assert len(notes) == 1
    assert f"set in {n_cols} columns" in notes[0]
    # The lines themselves are as the file draws them, ids and all.
    assert _texts(pc.lines)[1:1 + n_cols] == [f"C{c}R0" for c in
                                               range(n_cols)]
    assert [ln.id for ln in pc.lines] == [f"p0.t{i}" for i in
                                          range(len(pc.lines))]


def test_the_note_reaches_the_page_a_tool_renders():
    from planlens.tools.formatting import render_page
    with Document(content=_columns(2)) as doc:
        rows = render_page(doc.page(0), doc.summary(0), include_tables=False,
                           include_markups=False, with_locations=False)
    assert any(r.startswith("! " + ACROSS) for r in rows)


@pytest.mark.parametrize("n_cols", [2, 3])
def test_a_page_drawn_column_by_column_is_as_it_was(n_cols):
    with Document(content=_columns(n_cols, across=False)) as doc:
        pc = doc.page(0)
        assert column_gutters(pc.lines)          # it IS set in columns
    assert not any(w.startswith(ACROSS) for w in pc.warnings)
    assert _texts(pc.lines)[1:3] == ["C0R0", "C0R1"]


def test_one_column_of_prose_says_nothing():
    def draw(page):
        for r in range(30):
            page.insert_text((72, 80 + 14 * r), _prose(r, 14), fontsize=9)
    with Document(content=_pdf(draw)) as doc:
        pc = doc.page(0)
        assert column_gutters(pc.lines) == []
    assert pc.warnings == []


def test_a_table_drawn_row_by_row_says_nothing():
    """Short cells read row by row ARE in reading order."""
    def draw(page):
        for r in range(20):
            for c, x in enumerate((72, 200, 330, 460)):
                page.insert_text((x, 80 + 14 * r), f"B-{r}{c} 4.{c} m",
                                 fontsize=9)
    with Document(content=_pdf(draw)) as doc:
        pc = doc.page(0)
    assert not any(w.startswith(ACROSS) for w in pc.warnings)


def test_side_headings_beside_their_paragraphs_say_nothing():
    """A heading in the margin, then its paragraph: the right order."""
    def draw(page):
        y = 80.0
        for s in range(4):
            page.insert_text((40, y), f"Section {s} ground water and seepage",
                             fontsize=8)
            for r in range(7):
                page.insert_text((230, y + 12 * r), _prose(s + r, 10),
                                 fontsize=8)
            y += 12 * 8
    with Document(content=_pdf(draw)) as doc:
        pc = doc.page(0)
    assert not any(w.startswith(ACROSS) for w in pc.warnings)


def test_callouts_scattered_over_a_drawing_say_nothing():
    """Long notes placed all over a sheet in drafting order share no edge
    and are not columns, however they jump about."""
    def draw(page):
        for i in range(40):
            x = 30 + (i * 97) % 700
            y = 40 + (i * 53) % 520
            page.insert_text((x, y), f"NOTE {i} " + _prose(i, 4), fontsize=6)
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    draw(page)
    data = doc.tobytes()
    doc.close()
    with Document(content=data) as d:
        pc = d.page(0)
    assert not any(w.startswith(ACROSS) for w in pc.warnings)


def test_no_fixture_of_the_suite_carries_the_note():
    from planlens.testing.document_fixtures import (
        build_synthetic_review_document)
    from planlens.testing.report_fixtures import build_synthetic_report
    from planlens.testing.schedule_set_fixtures import build_schedule_set
    from planlens.testing.sheet_set_fixtures import build_sheet_set
    from planlens.testing.submittal_fixtures import build_synthetic_submittal
    for gt in (build_synthetic_review_document(), build_synthetic_report(),
               build_synthetic_submittal(), build_sheet_set(),
               build_schedule_set()):
        with Document(content=gt.pdf) as doc:
            for i in range(doc.n_pages):
                lines = doc.page(i, tables=False).lines
                assert order_runs_across_columns(lines) is None, (gt, i)


# -- the new reading, for a host that asks ------------------------------------

def test_column_order_reads_each_column_down():
    with Document(content=_columns(2, rows=6)) as doc:
        lines = doc.page(0, tables=False).lines
    ordered = column_order(lines)
    assert sorted(ln.id for ln in ordered) == sorted(ln.id for ln in lines)
    assert _texts(ordered) == (["A"] + [f"C0R{r}" for r in range(6)]
                               + [f"C1R{r}" for r in range(6)])


def test_column_order_breaks_at_a_line_over_the_columns():
    """Columns above a full-width line are read before it, those below
    after it."""
    def draw(page):
        for r in range(6):
            page.insert_text((72, 80 + 14 * r), "TOP0 " + _prose(r), fontsize=8)
            page.insert_text((306, 80 + 14 * r), "TOP1 " + _prose(r + 1),
                             fontsize=8)
        page.insert_text((72, 190), "MIDDLE " + _prose(2, 16), fontsize=8)
        for r in range(6):
            page.insert_text((72, 220 + 14 * r), "LOW0 " + _prose(r),
                             fontsize=8)
            page.insert_text((306, 220 + 14 * r), "LOW1 " + _prose(r + 1),
                             fontsize=8)
    with Document(content=_pdf(draw)) as doc:
        lines = doc.page(0, tables=False).lines
    got = _texts(column_order(lines))
    assert got == (["TOP0"] * 6 + ["TOP1"] * 6 + ["MIDDLE"]
                   + ["LOW0"] * 6 + ["LOW1"] * 6)


def test_column_order_leaves_one_column_alone():
    def draw(page):
        for r in range(12):
            page.insert_text((72, 80 + 14 * r), _prose(r, 14), fontsize=9)
    with Document(content=_pdf(draw)) as doc:
        lines = doc.page(0, tables=False).lines
    assert column_order(lines) == list(lines)
