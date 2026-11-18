"""Sheet numbers as addresses (BACKLOG PL-37) in the toolkit: with the sheet
tools served, a tool that takes ``pages`` takes ``sheets`` and one that takes
``page`` takes ``sheet``; a number the sheet index holds twice, or not at
all, is refused with words that say so. A toolkit without the sheet tools is
specified, and answers, as before. On made-up sets only
(:mod:`planlens.testing.sheet_set_fixtures`)."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402
from planlens.tools import (  # noqa: E402
    SHEET_TOOL_SPECS, TOOL_SPECS, ReviewToolkit,
)
from planlens.tools.sheet_addresses import (  # noqa: E402
    SHEET_SCHEMA, SHEETS_SCHEMA, with_sheet_args,
)
from planlens.tools.toolkit import sheets_at_open  # noqa: E402

ADDED = ("sheets", "sheet")


@pytest.fixture(scope="module")
def gt():
    return build_sheet_set()


@pytest.fixture(scope="module")
def twice(gt):
    """The set bound in twice: every sheet number on two pages."""
    out = pymupdf.open()
    for _ in range(2):
        src = pymupdf.open(stream=gt.pdf, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes()
    out.close()
    return data


def _kit(pdf, **kw):
    return ReviewToolkit(resolve_source=lambda key: pdf, **kw)


def call(kit, name, **args):
    return json.loads(kit.call_json(name, args))


def _open(kit):
    return call(kit, "open_document", source="set.pdf")["handle"]


def _numbered(gt):
    return [(page, number) for page, number in enumerate(gt.numbers)
            if number]


# -- what is offered -----------------------------------------------------------

def test_a_toolkit_without_the_sheet_tools_is_specified_as_before(gt):
    plain = _kit(gt.pdf)
    assert plain.specs("plain") == [dict(s) for s in TOOL_SPECS]
    assert plain.specs("anthropic") == [
        {"name": s["name"], "description": s["description"],
         "input_schema": s["parameters"]} for s in TOOL_SPECS]
    assert plain.specs("openai") == [{"type": "function",
                                      "function": dict(s)}
                                     for s in TOOL_SPECS]
    for spec in plain.specs("plain"):
        assert not set(ADDED) & set(spec["parameters"]["properties"])


def test_with_the_sheet_tools_every_page_argument_has_a_sheet_twin(gt):
    asked = _kit(gt.pdf, sheet_tools=True)
    before = {s["name"]: s for s in list(TOOL_SPECS) + list(SHEET_TOOL_SPECS)}
    shown = {s["name"]: s for s in asked.specs("plain")}
    assert list(shown) == list(before)
    for name, spec in shown.items():
        old = before[name]["parameters"]
        new = spec["parameters"]
        added = {"sheets"} if "pages" in old["properties"] else set()
        if "page" in old["properties"]:
            added.add("sheet")
        assert set(new["properties"]) == set(old["properties"]) | added
        # nothing else moves: the old arguments, description and title
        assert {k: v for k, v in new["properties"].items()
                if k not in ADDED} == old["properties"]
        assert spec["description"] == before[name]["description"]
        if "sheet" in added and "required" in old:
            assert new["required"] == [r for r in old["required"]
                                       if r != "page"]
        elif "required" in old:
            assert new["required"] == old["required"]
        if "sheets" in added:
            assert new["properties"]["sheets"] == SHEETS_SCHEMA
        if "sheet" in added:
            assert new["properties"]["sheet"] == SHEET_SCHEMA
    assert "sheets" in shown["read_document"]["parameters"]["properties"]
    assert "sheets" in shown["check_cross_references"]["parameters"][
        "properties"]
    assert shown["render_page"]["parameters"]["required"] == ["handle"]
    assert not set(ADDED) & set(shown["sheet_index"]["parameters"][
        "properties"])
    # the other styles carry the same schema
    assert [s["input_schema"] for s in asked.specs("anthropic")] == \
        [s["parameters"] for s in asked.specs("plain")]
    # a spec with no page argument is the very object, untouched
    assert with_sheet_args(before["open_document"]) is \
        before["open_document"]


def test_a_call_without_the_sheet_tools_is_answered_as_before(gt):
    plain = _kit(gt.pdf)
    h = _open(plain)
    for name, args in (("read_document", {"pages": "1"}),
                       ("document_page_map", {}),
                       ("search_document", {"pattern": "PLAN"})):
        assert plain.call(name, {"handle": h, **args}) == \
            getattr(plain, f"_tool_{name}")(handle=h, **args)
    number = _numbered(gt)[1][1]
    refused = plain.call("read_document", {"handle": h, "sheets": number})
    assert "error" in refused and "sheets" in refused["error"]
    assert "sheet_pages" not in refused
    # a toolkit that serves them, called for a host that did not ask
    # (the host layer's shared toolkit), answers as a plain one
    asked = _kit(gt.pdf, sheet_tools=True)
    h2 = _open(asked)
    with sheets_at_open(False):
        again = asked.call("read_document", {"handle": h2, "sheets": number})
    assert again == refused


# -- sheet numbers taken -----------------------------------------------------

def test_a_sheet_number_reads_the_page_its_title_block_prints_it_on(gt):
    kit = _kit(gt.pdf, sheet_tools=True)
    h = _open(kit)
    for page, number in _numbered(gt):
        by_page = call(kit, "read_document", handle=h, pages=str(page))
        by_sheet = call(kit, "read_document", handle=h, sheets=number)
        assert by_sheet.pop("sheet_pages") == [{"sheet": number,
                                                "page": page}]
        assert by_sheet == by_page
        # the index's own rule: case and spaces aside
        loose = call(kit, "read_document", handle=h,
                     sheets=f"  {number.lower()} ")
        assert loose["sheet_pages"] == [{"sheet": number, "page": page}]
    (p1, n1), (p2, n2) = _numbered(gt)[3], _numbered(gt)[1]
    both = call(kit, "check_cross_references", handle=h,
                sheets=f"{n1}, {n2}")
    rows = both.pop("sheet_pages")
    assert rows == [{"sheet": n1, "page": p1}, {"sheet": n2, "page": p2}]
    assert both == call(kit, "check_cross_references", handle=h,
                        pages=f"{min(p1, p2)},{max(p1, p2)}")
    listed = call(kit, "document_page_map", handle=h, sheets=[n1, n2])
    assert [r["page"] for r in listed["sheet_pages"]] == [p1, p2]


def test_one_sheet_where_a_tool_takes_one_page(gt):
    kit = _kit(gt.pdf, sheet_tools=True)
    h = _open(kit)
    page, number = _numbered(gt)[1]
    by_page = call(kit, "plan_symbols", handle=h, page=page)
    by_sheet = call(kit, "plan_symbols", handle=h, sheet=number)
    assert by_sheet.pop("sheet_pages") == [{"sheet": number, "page": page}]
    assert by_sheet == by_page
    # sheets with one number is that sheet; sheet on a pages tool too
    assert call(kit, "plan_symbols", handle=h, sheets=number)[
        "sheet_pages"] == [{"sheet": number, "page": page}]
    assert call(kit, "read_document", handle=h, sheet=number)[
        "sheet_pages"] == [{"sheet": number, "page": page}]
    two = call(kit, "plan_symbols", handle=h,
               sheet=f"{number}, {_numbered(gt)[2][1]}")
    assert two["error"].startswith("sheet: it names one sheet, not 2")
    # a page given beside it must be the same page
    same = call(kit, "plan_symbols", handle=h, page=page, sheet=number)
    assert same["sheet_pages"] == [{"sheet": number, "page": page}]
    other = call(kit, "plan_symbols", handle=h, page=page + 1, sheet=number)
    assert "name different pages" in other["error"]
    assert f"page {page} (pdf_page {page + 1})" in other["error"]


# -- refused, never guessed ----------------------------------------------------

def test_a_number_the_index_does_not_hold_is_refused_and_named(gt):
    kit = _kit(gt.pdf, sheet_tools=True)
    h = _open(kit)
    out = call(kit, "read_document", handle=h, sheets="Z-999")
    assert out["error"].startswith(
        "sheet Z-999 is not in this document's sheet index, which reads "
        "5 sheet numbers: ")
    for _page, number in _numbered(gt):
        assert number in out["error"]
    assert "sheet_index lists every sheet" in out["hint"]
    # the same letters and digits printed otherwise: named, not taken
    page, number = _numbered(gt)[1]
    bare = number.replace("-", "")
    near = call(kit, "read_document", handle=h, sheets=bare)
    assert near["error"].startswith(f"sheet {bare} is not in")
    assert f"{bare} -> {number}" in near["hint"]
    # one known and one unknown: refused whole
    mixed = call(kit, "read_document", handle=h, sheets=f"{number}, Z-999")
    assert "error" in mixed and "sheet_pages" not in mixed


def test_a_number_the_index_holds_twice_is_refused_with_its_pages(gt, twice):
    kit = _kit(twice, sheet_tools=True)
    h = _open(kit)
    page, number = _numbered(gt)[2]
    out = call(kit, "read_document", handle=h, sheets=number)
    assert out["error"] == (
        f"sheet {number} is read on pages {page} and {page + 5} (pdf_pages "
        f"{page + 1} and {page + 6}): one sheet number on more than one page "
        f"is not taken for either")
    assert "pages" in out["hint"]


def test_a_document_with_no_sheet_numbers_says_so():
    scans = build_sheet_set(scanned=range(5))
    kit = _kit(scans.pdf, sheet_tools=True)
    h = _open(kit)
    out = call(kit, "read_document", handle=h, sheets="A-101")
    assert out["error"].startswith(
        "sheets A-101: this document's sheet index holds no sheet numbers")
    assert "pdf_pages" in out["hint"]


def test_bad_sheet_arguments_are_said(gt):
    kit = _kit(gt.pdf, sheet_tools=True)
    h = _open(kit)
    assert call(kit, "read_document", handle=h, sheets=[3.5])["error"] == \
        "sheets: sheet numbers are text such as 'S-201', not 3.5"
    assert call(kit, "read_document", handle=h, sheets=" , ")["error"] == \
        "sheets: no sheet number was given"
    assert "error" in call(kit, "read_document", handle="nope",
                           sheets="A-101")
    # an empty sheets is no sheets: answered as without it
    assert call(kit, "read_document", handle=h, pages="0", sheets="") == \
        call(kit, "read_document", handle=h, pages="0")
