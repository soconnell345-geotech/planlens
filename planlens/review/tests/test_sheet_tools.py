"""The sheet tools through the host layer: offered only to a host that asks
(``ReviewHost(toolkit_options={"sheet_tools": True})``); every other host is
offered, and answered, what it was."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import specs  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402

SHEET_TOOLS = ("sheet_index", "check_cross_references")
BEFORE = specs.DOCUMENT_TOOLS + specs.OPTIONAL_DOCUMENT_TOOLS
ASK = {"sheet_tools": True}


@pytest.fixture(scope="module")
def gt():
    return build_sheet_set()


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def test_a_host_that_did_not_ask_is_offered_what_it_was(gt):
    assert specs.SHEET_TOOLS == SHEET_TOOLS
    assert specs.HOST_TOOLS == BEFORE + specs.LOOK_TOOLS
    assert not set(SHEET_TOOLS) & set(specs.HOST_TOOLS)
    assert specs.host_tool_names(look=False) == BEFORE
    assert specs.host_tool_names() == BEFORE + specs.LOOK_TOOLS
    assert [s["name"] for s in specs.host_specs()] == \
        list(BEFORE + specs.LOOK_TOOLS)
    tools = make_tools(None, None, {"set.pdf": gt.pdf})
    assert tuple(tools) == BEFORE == tuple(review.tool_names())
    for name in SHEET_TOOLS:
        refused = json.loads(review.call(name, {"handle": "set.pdf"},
                                         {"set.pdf": gt.pdf}))
        assert refused["error"] == f"unknown tool '{name}'"
        assert name not in refused["hint"]


def test_a_host_that_asks_is_offered_them_after_the_others(gt):
    plain = {s["name"]: s for s in specs.host_specs(look=False)}
    make_tools(None, None, {"set.pdf": gt.pdf}, toolkit_options=ASK)
    assert tuple(review.tool_names()) == BEFORE + SHEET_TOOLS
    assert specs.host_tool_names(look=True, sheet_tools=True) == \
        BEFORE + specs.LOOK_TOOLS + SHEET_TOOLS
    shown = {s["name"]: s for s in review.tool_specs()}
    assert list(shown) == list(BEFORE + SHEET_TOOLS)
    # the tools every host has are specified word for word as before
    assert {n: s for n, s in shown.items() if n not in SHEET_TOOLS} == plain
    index = shown["sheet_index"]["input_schema"]
    assert index["required"] == ["handle"]
    assert list(index["properties"]) == ["handle", "offset"]
    check = shown["check_cross_references"]["input_schema"]
    assert check["required"] == ["handle"]
    assert list(check["properties"]) == ["handle", "pages", "status",
                                         "offset", "pdf_pages"]
    assert "title blocks" in shown["sheet_index"]["description"]
    assert "unresolved" in shown["check_cross_references"]["description"]
    openai = review.tool_specs("openai")
    assert openai[-1]["function"]["name"] == "check_cross_references"


def test_the_sheet_tools_through_the_host_give_both_page_numbers(gt):
    tools = make_tools(None, None, {"set.pdf": gt.pdf}, toolkit_options=ASK)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    index = _invoke(tools["sheet_index"], handle=handle)
    assert [(r["page"], r["pdf_page"], r["sheet"])
            for r in index["sheets"]] == [
        (0, 1, "G-001"), (1, 2, "A-101"), (2, 3, "A-102"), (3, 4, "A-501"),
        (4, 5, "A-601")]
    refs = _invoke(tools["check_cross_references"], handle=handle)
    first = refs["callouts"][0]
    assert first["callout"] == "5/A-501"
    assert (first["page"], first["pdf_page"]) == (1, 2)
    assert first["target"] == {"sheet": "A-501", "page": 3, "pdf_page": 4}
    assert refs["counts"] == {"callouts": 9, "unresolved": 2,
                              "cannot_tell": 1, "resolved": 6}


def test_the_sheet_tools_take_what_the_other_document_tools_take(gt):
    tools = make_tools(None, None, {"set.pdf": gt.pdf}, toolkit_options=ASK)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    # the viewer's page number in place of the 0-based one
    third = _invoke(tools["check_cross_references"], handle=handle,
                    pdf_pages="3")
    assert {r["page"] for r in third["callouts"]} == {2}
    assert third["counts"]["callouts"] == 4
    only = _invoke(tools["check_cross_references"], handle=handle,
                   status="unresolved")
    assert [r["callout"] for r in only["callouts"]] == ["5/A-501", "1/A-301"]
    # a file name where the handle goes opens the file
    again = _invoke(tools["sheet_index"], handle="set.pdf")
    assert again["n_indexed"] == 5
    # an argument a tool does not take is refused, never dropped
    bad = _invoke(tools["sheet_index"], handle=handle, pages="1")
    assert "takes no argument" in bad["error"]
    bad = _invoke(tools["check_cross_references"], handle=handle,
                  status="wrong")
    assert bad["error"].startswith("status must be one of")
    bad = _invoke(tools["check_cross_references"], handle=handle,
                  pdf_pages="9")
    assert "error" in bad


def test_scanned_sheets_come_back_listed_with_both_page_numbers():
    scan = build_sheet_set(scanned=(3,))
    tools = make_tools(None, None, {"scan.pdf": scan.pdf},
                       toolkit_options=ASK)
    index = _invoke(tools["sheet_index"], handle="scan.pdf")
    assert index["n_indexed"] == 4
    assert index["not_indexed"] == [{
        "pages": "3", "pdf_pages": "4",
        "reason": "no text layer on this sheet: read its title block by "
                  "looking"}]
    refs = _invoke(tools["check_cross_references"], handle="scan.pdf")
    assert refs["n_sheets_without_text"] == 1
    assert refs["counts"]["unresolved"] == 0
    assert refs["counts"]["cannot_tell"] == 7


def test_a_small_limit_cuts_the_list_and_says_where_to_go_on(gt):
    tools = make_tools(None, None, {"set.pdf": gt.pdf}, max_chars=1500,
                       toolkit_options=ASK)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    raw = tools["check_cross_references"].invoke({"handle": handle})
    assert len(raw) <= 1500
    part = json.loads(raw)
    assert part["counts"]["callouts"] == 9 and len(part["callouts"]) < 9
    rest = _invoke(tools["check_cross_references"], handle=handle,
                   offset=part["next_offset"])
    assert rest["callouts"][0] != part["callouts"][0]
