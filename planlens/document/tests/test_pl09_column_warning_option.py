"""PL-09: the warning on a page of columns drawn row by row is an option of
``Document`` (``column_warning``), off by default. Unset, the check is not
run and the page is what it was before the check existed. Made-up pages
only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document import document as document_module  # noqa: E402
from planlens.document.tests.test_pl09_reading_order import (  # noqa: E402
    ACROSS, _columns,
)


@pytest.mark.parametrize("n_cols", [2, 3])
def test_columns_drawn_row_by_row_carry_no_warning_unless_asked(n_cols):
    data = _columns(n_cols)
    with Document(content=data) as doc:
        assert doc.column_warning is False
        plain = doc.page(0)
    assert plain.warnings == []
    with Document(content=data, column_warning=True) as doc:
        asked = doc.page(0)
    notes = [w for w in asked.warnings if w.startswith(ACROSS)]
    assert len(notes) == 1
    # The warning is the whole difference between the two pages.
    want = asked.to_dict()
    assert want.pop("warnings") == notes
    assert plain.to_dict() == want


def test_the_page_a_tool_renders_has_no_note_unless_asked():
    from planlens.tools.formatting import render_page
    with Document(content=_columns(2)) as doc:
        rows = render_page(doc.page(0), doc.summary(0), include_tables=False,
                           include_markups=False, with_locations=False)
    assert not any(ACROSS in r for r in rows)


def test_the_check_is_not_run_unless_asked(monkeypatch):
    calls = []

    def spy(lines):
        calls.append(len(lines))
        return None

    monkeypatch.setattr(document_module, "order_runs_across_columns", spy)
    with Document(content=_columns(2)) as doc:
        doc.page(0)
        doc.page(0, words=True, tables=False)
    assert calls == []
    with Document(content=_columns(2), column_warning=True) as doc:
        doc.page(0)
    assert len(calls) == 1
