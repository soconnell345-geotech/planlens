"""Sheet numbers as addresses (BACKLOG PL-37) through the host layer: a host
that asked for the sheet tools (``ReviewHost(toolkit_options={"sheet_tools":
True})``) is offered ``sheets`` beside ``pages`` (and, with the plan tools,
``sheet`` beside ``page``) and served them, through :func:`planlens.review.
call` and through the dispatcher the app's own tool functions call. Every
other host is offered, and answered, what it was."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import conversation, specs  # noqa: E402
from planlens.review.host import ReviewHost, using  # noqa: E402
from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402

ASK = {"sheet_tools": True}
ADDED = ("sheets", "sheet")
#: The host tools that take pages, and so take sheets for a host that asked
#: (find_like reads an attachment's bytes, with no handle to index).
TAKE_SHEETS = {"render_page_thumbnails", "document_page_map",
               "read_document", "search_document", "find_quantities",
               "document_markups", "check_cross_references",
               "check_schedule_marks"}


@pytest.fixture(scope="module")
def gt():
    return build_sheet_set()


def _call(name, args, gt, host):
    return json.loads(review.call(name, args, {"set.pdf": gt.pdf},
                                  host=host))


def _without_sheet_args(spec):
    out = json.loads(json.dumps(spec))
    schema = out.get("input_schema") or out["function"]["parameters"]
    for arg in ADDED:
        schema["properties"].pop(arg, None)
    return out


def test_a_host_that_did_not_ask_is_specified_and_answered_as_before(gt):
    for style in ("plain", "openai"):
        for spec in specs.host_specs(style):
            schema = spec.get("input_schema") or \
                spec["function"]["parameters"]
            assert not set(ADDED) & set(schema["properties"])
    for name in specs.HOST_TOOLS:
        assert specs.sheet_args(name) == []
        assert specs.host_arg_names(name) == \
            [arg for arg, _f, _d in specs._ARGS[name]]
    plain = ReviewHost()
    handle = _call("open_document", {"source": "set.pdf"}, gt,
                   plain)["handle"]
    number = gt.numbers[1]
    refused = _call("read_document", {"handle": handle, "sheets": number},
                    gt, plain)
    assert "sheets" in refused["error"] and "sheet_pages" not in refused
    # the dispatcher the app's own tool functions call: the shared toolkit
    # serves the sheet tools, and still refuses a host that did not ask
    with using(plain):
        raw = conversation.dispatch_document_tool(
            "read_document", {"handle": handle, "sheets": number},
            {"set.pdf": gt.pdf})
    out = json.loads(raw)
    assert "sheets" in out["error"] and "sheet_pages" not in out


def test_a_host_that_asks_is_offered_sheets_beside_pages(gt):
    plain = {s["name"]: s for s in specs.host_specs(look=True)}
    shown = {s["name"]: s for s in specs.host_specs(look=True,
                                                    sheet_tools=True,
                                                    plan_tools=True)}
    for name, spec in shown.items():
        props = spec["input_schema"]["properties"]
        assert ("sheets" in props) is (name in TAKE_SHEETS)
        assert ("sheet" in props) is (name in specs.PLAN_TOOLS)
        if name in plain:
            # the tools every host has: as before but for the sheet args
            assert _without_sheet_args(spec) == plain[name]
    read = shown["read_document"]["input_schema"]["properties"]
    assert list(read)[-1] == "sheets"
    assert read["sheets"]["default"] is None
    assert read["sheets"]["title"] == "Sheets"
    assert "never guessed" in read["sheets"]["description"]
    grid = shown["plan_symbols"]["input_schema"]
    assert grid["properties"]["sheet"]["anyOf"][-1] == {"type": "null"}
    assert "sheet" not in grid.get("required", [])
    openai = specs.host_specs("openai", sheet_tools=True)
    assert all("sheets" in s["function"]["parameters"]["properties"]
               for s in openai if s["function"]["name"] in TAKE_SHEETS)


def test_a_host_that_asks_is_served_sheet_numbers(gt):
    asked = ReviewHost(toolkit_options=ASK)
    handle = _call("open_document", {"source": "set.pdf"}, gt,
                   asked)["handle"]
    page, number = 1, gt.numbers[1]
    by_page = _call("read_document", {"handle": handle, "pages": str(page)},
                    gt, asked)
    by_sheet = _call("read_document", {"handle": handle, "sheets": number},
                     gt, asked)
    # the viewer's number is added beside the page, as everywhere
    assert by_sheet.pop("sheet_pages") == [{"sheet": number, "page": page,
                                            "pdf_page": page + 1}]
    assert by_sheet == by_page
    check = _call("check_cross_references",
                  {"handle": handle, "sheets": number}, gt, asked)
    assert check["sheet_pages"][0]["sheet"] == number
    # the dispatcher the app's own tool functions call
    with using(asked):
        raw = conversation.dispatch_document_tool(
            "document_page_map", {"handle": handle, "sheets": number},
            {"set.pdf": gt.pdf})
    out = json.loads(raw)
    assert out["sheet_pages"] == [{"sheet": number, "page": page,
                                   "pdf_page": page + 1}]
    # refused with words, never guessed
    missing = _call("read_document", {"handle": handle, "sheets": "Z-999"},
                    gt, asked)
    assert missing["error"].startswith("sheet Z-999 is not in this "
                                       "document's sheet index")
    # pdf_pages beside sheets must name the same pages
    same = _call("read_document", {"handle": handle, "sheets": number,
                                   "pdf_pages": str(page + 1)}, gt, asked)
    assert same["sheet_pages"][0]["page"] == page
    other = _call("read_document", {"handle": handle, "sheets": number,
                                    "pdf_pages": str(page + 2)}, gt, asked)
    assert "name different pages" in other["error"]


def test_the_plan_tools_take_one_sheet_for_a_host_that_asked_for_both(gt):
    asked = ReviewHost(toolkit_options={"sheet_tools": True,
                                        "plan_tools": True})
    plan_only = ReviewHost(toolkit_options={"plan_tools": True})
    handle = _call("open_document", {"source": "set.pdf"}, gt,
                   asked)["handle"]
    page, number = 1, gt.numbers[1]
    by_page = _call("plan_symbols", {"handle": handle, "page": page}, gt,
                    asked)
    by_sheet = _call("plan_symbols", {"handle": handle, "sheet": number},
                     gt, asked)
    assert by_sheet.pop("sheet_pages")[0]["page"] == page
    assert by_sheet == by_page
    refused = _call("plan_symbols", {"handle": handle, "sheet": number}, gt,
                    plan_only)
    assert "sheet" in refused["error"] and "sheet_pages" not in refused
