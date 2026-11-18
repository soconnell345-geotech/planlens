"""PL-09: a table printed over two pages, linked for a host that asks
(``Document(..., continued_tables=True)``, off by default). Made-up pages
and values only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.tools.formatting import render_page  # noqa: E402

COLS = (72, 172, 272, 372, 540)
TITLES = ("Hole", "Depth (m)", "Moisture (%)", "Remarks")


def _table(page, top, rows, titles=True):
    """A ruled four-column table; returns its bottom."""
    grid = ([TITLES] if titles else []) + rows
    y = top
    for r, row in enumerate(grid + [None]):
        page.draw_line((COLS[0], y), (COLS[-1], y))
        if row is None:
            break
        for c, text in enumerate(row):
            page.insert_text((COLS[c] + 4, y + 13), text, fontsize=9)
        y += 18
    for x in COLS:
        page.draw_line((x, top), (x, y))
    return y


def _rows(start, n):
    return [(f"TH-{k}", f"{k}.5", f"{20 + k}", "none")
            for k in range(start, start + n)]


def _band(page, text):
    page.insert_text((72, 30), text, fontsize=8)


def _doc(second="repeated", header=("Made-up report", "Made-up report"),
         note_below=False, para_above=False):
    doc = pymupdf.open()
    p1 = doc.new_page(width=612, height=792)
    _band(p1, header[0] + " page 3")
    p1.insert_text((72, 90), "Table 4: Moisture contents of the samples",
                   fontsize=10)
    bottom = _table(p1, 110, _rows(1, 30))
    if note_below:
        p1.insert_text((72, bottom + 16),
                       "Note: the samples were sealed on the day they were "
                       "taken.", fontsize=9)
    p2 = doc.new_page(width=612, height=792)
    _band(p2, header[1] + " page 4")
    top = 90
    if second == "continued":
        p2.insert_text((72, 90), "Table 4 (continued)", fontsize=10)
        top = 110
    if para_above:
        p2.insert_text((72, 90), "The next table lists the readings in the "
                       "wells.", fontsize=10)
        top = 110
    _table(p2, top, _rows(31, 8), titles=(second == "repeated"))
    data = doc.tobytes()
    doc.close()
    return data


def _notes(data, switch=True):
    with Document(content=data, continued_tables=switch) as doc:
        return ([n for t in doc.page(0).tables for n in t.notes],
                [n for t in doc.page(1).tables for n in t.notes],
                [t.id for t in doc.page(1).tables])


def test_off_by_default_nothing_is_noted():
    data = _doc()
    with Document(content=data) as doc:
        plain = [[t.to_dict() for t in doc.page(i).tables] for i in (0, 1)]
    with Document(content=data, continued_tables=True) as doc:
        linked = [[t.to_dict() for t in doc.page(i).tables] for i in (0, 1)]
    assert not any("continues" in n for p in plain for t in p
                   for n in t.get("notes", []))
    # With the switch only the notes differ: rows, ids and boxes are kept.
    for p, q in zip(plain, linked):
        for t, u in zip(p, q):
            u = dict(u)
            u.pop("notes", None)
            t = dict(t)
            t.pop("notes", None)
            assert t == u


def test_a_table_repeating_its_column_titles_is_linked():
    first, second, ids = _notes(_doc("repeated"))
    assert first == [f"continues on page 1 as table {ids[0]}"]
    assert second == ["continues table p0.tbl0 from page 0: its first row "
                      "repeats that table's column titles"]


def test_a_table_said_to_be_continued_is_linked():
    first, second, ids = _notes(_doc("continued"))
    assert first == [f"continues on page 1 as table {ids[0]}"]
    assert second == ["continues table p0.tbl0 from page 0: the column "
                      "titles are on that page, not repeated here"]


def test_the_note_reaches_the_page_a_tool_renders():
    with Document(content=_doc("repeated"), continued_tables=True) as doc:
        rows = render_page(doc.page(1), doc.summary(1), include_tables=True,
                           include_markups=False, with_locations=False)
    assert any(r.startswith("note: continues table p0.tbl0") for r in rows)


def test_a_line_with_no_box_on_either_page_means_cannot_tell():
    from dataclasses import replace

    from planlens.document.continued_tables import table_continuation
    from planlens.document.model import TextLine
    with Document(content=_doc("repeated")) as doc:
        first, second = doc.page(0), doc.page(1)
    assert table_continuation(first, second) is not None
    boxless = TextLine(id="p1.x0", page=1, bbox=None,
                       text="a made-up line from a text source with no box")
    second = replace(second, lines=list(second.lines) + [boxless])
    assert table_continuation(first, second) is None


@pytest.mark.parametrize("case", [
    dict(second="bare"),                   # nothing says it continues
    dict(note_below=True),                 # a note under the first ends it
    dict(para_above=True),                 # a paragraph over the second
    dict(header=("Made-up report", "Another made-up report")),
])
def test_tables_that_do_not_run_on_are_not_linked(case):
    first, second, _ = _notes(_doc(**case))
    assert not any("continues" in n for n in first + second)
