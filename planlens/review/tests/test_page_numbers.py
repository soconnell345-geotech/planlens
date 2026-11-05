"""0-based ``page`` / 1-based ``pdf_page``, and a page out of range said
plainly (PL-29 (a)).

Moved from the app's test_wave2c_offline.py: the helpers, and the document
tools taking ``pdf_pages`` / a markup's ``pdf_page`` through the host layer.
The app's tests of its own vision tools' pages stay in the app.
"""

from __future__ import annotations

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.review import page_numbers  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402


def _pdf(n=5) -> bytes:
    d = pymupdf.open()
    for i in range(n):
        page = d.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Sheet {i + 1}", fontsize=14)
    data = d.tobytes()
    d.close()
    return data


def _call(tool, **kw):
    return json.loads(tool.invoke(kw))


def test_page_number_helpers():
    assert page_numbers.pdf_pages_to_pages("1-3,6") == ("0-2,5", None)
    assert page_numbers.pdf_pages_to_pages(5) == (4, None)
    assert page_numbers.pdf_pages_to_pages([1, 2]) == ([0, 1], None)
    assert page_numbers.pdf_pages_to_pages("0-2")[1] is not None
    assert page_numbers.resolve_page(None, None) == (0, None)
    assert page_numbers.resolve_page("3", None) == (3, None)
    assert page_numbers.resolve_page(None, 0)[1] is not None
    assert page_numbers.range_hint(
        "page(s) [5] out of range: this document has 5 pages, numbered "
        "0-4") == "pages are 0-based here; PDF page 5 is page 4"
    assert page_numbers.range_hint(
        "page 5 is outside this document, which has pages 0-4") == \
        "pages are 0-based here; PDF page 5 is page 4"
    assert page_numbers.range_hint("some other error") is None


def test_resolve_page_never_guesses():
    assert page_numbers.resolve_page(None, 5) == (4, None)
    assert page_numbers.resolve_page(4, 5) == (4, None)
    page, problem = page_numbers.resolve_page(0, 5)
    assert page is None and "different pages" in problem["error"]
    assert page_numbers.resolve_page("x", None)[1] is not None
    assert page_numbers.page_error(3, 5) is None
    err = page_numbers.page_error(5, 5)
    assert err["hint"] == "pages are 0-based here; PDF page 5 is page 4"
    assert page_numbers.page_error(0, 0) == {
        "error": "this document has no pages"}


def test_document_tools_say_pages_are_0_based_and_take_pdf_pages(tmp_path):
    tools = make_tools(None, tmp_path, {"set.pdf": _pdf(5)})
    handle = _call(tools["open_document"], source="set.pdf")["handle"]
    bad = _call(tools["read_document"], handle=handle, pages="5")
    assert "out of range" in bad["error"]
    assert bad["hint"].startswith("pages are 0-based here; PDF page 5 is "
                                  "page 4")
    good = _call(tools["read_document"], handle=handle, pdf_pages="5")
    assert "error" not in good, good
    assert good["pages_returned"] == "4" and "Sheet 5" in json.dumps(good)
    past = _call(tools["read_document"], handle=handle, pdf_pages="6")
    assert "pdf_page 1-5" in past["hint"]
    both = _call(tools["search_document"], handle=handle, pattern="Sheet",
                 pages="0", pdf_pages="1")
    assert "not both" in both["error"]
    hits = _call(tools["search_document"], handle=handle, pattern="Sheet",
                 pdf_pages="2-3")
    assert hits["n_hits"] == 2


def test_a_markup_may_give_pdf_page(tmp_path):
    tools = make_tools(None, tmp_path, {"set.pdf": _pdf(5)})
    handle = _call(tools["open_document"], source="set.pdf")["handle"]
    out = _call(tools["annotate_document"], handle=handle,
                output_path="set_marked.pdf", check=False,
                markups=[{"kind": "note", "pdf_page": 5,
                          "comment": "last sheet", "point": [80, 80]}])
    assert "error" not in out, out
    assert out["written"][0]["page"] == 4
    clash = _call(tools["annotate_document"], handle=handle,
                  output_path="set_marked.pdf", check=False,
                  markups=[{"kind": "note", "page": 0, "pdf_page": 5,
                            "comment": "x", "point": [80, 80]}])
    assert "different pages" in clash["error"]
    past = _call(tools["annotate_document"], handle=handle,
                 output_path="set_marked2.pdf", check=False,
                 markups=[{"kind": "note", "page": 5, "comment": "x",
                           "point": [80, 80]}])
    skipped = past.get("skipped") or []
    assert skipped and skipped[0]["hint"] == \
        "pages are 0-based here; PDF page 5 is page 4"
