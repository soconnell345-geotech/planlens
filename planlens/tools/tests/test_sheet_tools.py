"""sheet_index and check_cross_references: offered only by a toolkit built
with ``sheet_tools=True``, and what they return on a made-up drawing set
(:mod:`planlens.testing.sheet_set_fixtures`)."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402
from planlens.tools import (  # noqa: E402
    SHEET_TOOL_SPECS, TOOL_SPECS, ReviewToolkit,
)

SHEET_TOOLS = ["sheet_index", "check_cross_references"]
VIEW_HINT = "look_at(source, page, bbox)"


@pytest.fixture(scope="module")
def gt():
    return build_sheet_set()


def _joined(*pdfs):
    out = pymupdf.open()
    for pdf in pdfs:
        src = pymupdf.open(stream=pdf, filetype="pdf")
        out.insert_pdf(src)
        src.close()
    data = out.tobytes()
    out.close()
    return data


@pytest.fixture(scope="module")
def long_set():
    """Twenty sheets: the set four times over, numbered four ways."""
    return _joined(*(build_sheet_set("strip", numbers).pdf
                     for numbers in ("dash", "suffix", "civil", "dot")))


def _kit(pdf, **kw):
    kw.setdefault("sheet_tools", True)
    return ReviewToolkit(resolve_source=lambda key: pdf,
                         vision_hint=VIEW_HINT, **kw)


def call(kit, name, max_chars=None, **args):
    text = kit.call_json(name, args, max_chars=max_chars)
    assert len(text) <= (max_chars or kit.max_chars)
    return json.loads(text)


def _open(kit):
    return call(kit, "open_document", source="set.pdf")["handle"]


# -- opt-in ------------------------------------------------------------------

def test_the_sheet_tools_are_offered_only_when_asked_for(gt):
    plain = ReviewToolkit(resolve_source=lambda key: gt.pdf)
    asked = _kit(gt.pdf)
    published = [s["name"] for s in TOOL_SPECS]
    assert [s["name"] for s in SHEET_TOOL_SPECS] == SHEET_TOOLS
    assert not set(SHEET_TOOLS) & set(published)
    assert plain.sheet_tools is False
    assert plain.tool_names == published
    assert asked.tool_names == published + SHEET_TOOLS
    for style in ("anthropic", "openai", "plain"):
        assert asked.specs(style)[:len(published)] == plain.specs(style)
    assert [s["name"] for s in asked.specs()[-2:]] == SHEET_TOOLS
    for spec in asked.specs()[-2:]:
        assert spec["input_schema"]["required"] == ["handle"]
        assert "handle" in spec["input_schema"]["properties"]
    assert asked.specs("openai")[-1]["function"]["name"] == \
        "check_cross_references"


def test_a_toolkit_that_did_not_ask_refuses_them_and_answers_as_before(gt):
    plain = ReviewToolkit(resolve_source=lambda key: gt.pdf,
                          vision_hint=VIEW_HINT)
    asked = _kit(gt.pdf)
    opened_plain = call(plain, "open_document", source="set.pdf")
    opened_asked = call(asked, "open_document", source="set.pdf")
    assert opened_plain == opened_asked            # open_document unchanged
    handle = opened_plain["handle"]
    for name in SHEET_TOOLS:
        refused = call(plain, name, handle=handle)
        assert refused["error"] == f"unknown tool '{name}'"
        assert name not in refused["hint"]
    for name, args in (("document_page_map", {}), ("document_structure", {}),
                       ("read_document", {"pages": "1"}),
                       ("search_document", {"pattern": "A-501"})):
        assert call(plain, name, handle=handle, **args) == \
            call(asked, name, handle=handle, **args)
    plain.close()
    asked.close()


# -- sheet_index -------------------------------------------------------------

def test_sheet_index_lists_every_sheet(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)
    out = call(kit, "sheet_index", handle=handle)
    assert (out["n_pages"], out["n_sheets"], out["n_indexed"]) == (5, 5, 5)
    assert out["sheets"] == [
        {"page": 0, "sheet": "G-001", "title": "COVER SHEET", "rev": "0"},
        {"page": 1, "sheet": "A-101", "title": "FIRST FLOOR PLAN",
         "rev": "0", "views": "1"},
        {"page": 2, "sheet": "A-102", "title": "SECOND FLOOR PLAN",
         "rev": "1", "views": "1"},
        {"page": 3, "sheet": "A-501", "title": "DOOR AND WINDOW DETAILS",
         "rev": "0", "views": "1 to 4"},
        {"page": 4, "sheet": "A-601", "title": "DOOR SCHEDULE", "rev": "2"},
    ]
    assert "0-based" in out["note"]
    assert "not_indexed" not in out and "next_offset" not in out
    assert "look" not in out and "notes" not in out
    kit.close()


def test_sheet_index_continues_where_it_was_cut(long_set):
    kit = _kit(long_set)
    handle = _open(kit)
    whole = call(kit, "sheet_index", handle=handle)
    assert whole["n_indexed"] == 20 and len(whole["sheets"]) == 20
    assert "next_offset" not in whole
    numbers = [row["sheet"] for row in whole["sheets"]]
    assert len(set(numbers)) == 20
    assert numbers[5:8] == ["S-001", "S-201", "S-201A"]
    got, offset, calls = [], 0, 0
    while offset is not None:
        part = call(kit, "sheet_index", max_chars=1000, handle=handle,
                    offset=offset)
        assert part["sheets"] and part["n_indexed"] == 20
        got.extend(part["sheets"])
        offset = part.get("next_offset")
        calls += 1
    assert calls > 1 and got == whole["sheets"]
    bad = call(kit, "sheet_index", handle=handle, offset=20)
    assert bad["error"] == "offset 20 is outside 0-19"
    kit.close()


def test_sheet_index_lists_scanned_sheets_without_a_number():
    kit = _kit(build_sheet_set(scanned=(3,)).pdf)
    handle = _open(kit)
    out = call(kit, "sheet_index", handle=handle)
    assert (out["n_sheets"], out["n_indexed"]) == (5, 4)
    assert [row["sheet"] for row in out["sheets"]] == [
        "G-001", "A-101", "A-102", "A-601"]
    assert out["not_indexed"] == [{
        "pages": "3", "reason": "no text layer on this sheet: read its "
                                "title block by looking"}]
    assert out["look"].endswith(VIEW_HINT)
    assert out["notes"] == ["1 of 5 sheets could not be indexed from their "
                            "text"]
    kit.close()
    kit = _kit(build_sheet_set(scanned=(0, 1, 2, 3, 4)).pdf)
    out = call(kit, "sheet_index", handle=_open(kit))
    assert out["sheets"] == [] and out["n_indexed"] == 0
    assert out["not_indexed"][0]["pages"] == "0-4"
    kit.close()


# -- check_cross_references --------------------------------------------------

def test_check_cross_references_puts_the_unresolved_first(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)
    out = call(kit, "check_cross_references", handle=handle)
    assert out["counts"] == {"callouts": 9, "unresolved": 2,
                             "cannot_tell": 1, "resolved": 6}
    rows = out["callouts"]
    assert [r["status"] for r in rows] == (
        ["unresolved"] * 2 + ["cannot_tell"] + ["resolved"] * 6)
    assert rows[0] == {
        "page": 1, "sheet": "A-101", "callout": "5/A-501",
        "status": "unresolved", "why": "A-501 holds views 1 to 4, not 5",
        "target": {"sheet": "A-501", "page": 3},
        "bbox": rows[0]["bbox"]}
    assert all(isinstance(v, int) for v in rows[0]["bbox"])
    assert rows[1]["callout"] == "1/A-301" and "target" not in rows[1]
    assert rows[1]["why"] == "there is no sheet A-301 in this set"
    assert rows[2]["callout"] == "1/A-601"
    assert [r["callout"] for r in rows if r.get("form") == "text"] == [
        "4/A-501", "2/A-501"]
    assert "before you comment" in out["look"]
    assert out["look"].endswith(VIEW_HINT)
    assert "n_sheets_without_text" not in out and "next_offset" not in out
    kit.close()


def test_check_cross_references_filters_by_status_and_pages(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)
    only = call(kit, "check_cross_references", handle=handle,
                status="unresolved")
    assert [r["callout"] for r in only["callouts"]] == ["5/A-501", "1/A-301"]
    assert only["counts"]["callouts"] == 9        # the counts stay whole
    page = call(kit, "check_cross_references", handle=handle, pages="2")
    assert page["counts"] == {"callouts": 4, "unresolved": 1,
                              "cannot_tell": 0, "resolved": 3}
    assert {r["page"] for r in page["callouts"]} == {2}
    both = call(kit, "check_cross_references", handle=handle, pages=[1, 2],
                status="resolved")
    assert len(both["callouts"]) == 5
    none = call(kit, "check_cross_references", handle=handle, pages="0")
    assert none["callouts"] == [] and none["counts"]["callouts"] == 0
    assert "look" not in none
    kit.close()


def test_check_cross_references_refuses_what_it_cannot_take(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)
    bad = call(kit, "check_cross_references", handle=handle, status="wrong")
    assert bad["error"].startswith("status must be one of")
    bad = call(kit, "check_cross_references", handle=handle, offset=9)
    assert bad["error"] == "offset 9 is outside 0-8"
    bad = call(kit, "check_cross_references", handle=handle, pages="7")
    assert "error" in bad and "7" in bad["error"]
    bad = call(kit, "check_cross_references", handle="doc_nothing")
    assert "error" in bad
    kit.close()


def test_check_cross_references_continues_where_it_was_cut(long_set):
    kit = _kit(long_set)
    handle = _open(kit)
    whole = call(kit, "check_cross_references", handle=handle)
    assert whole["counts"] == {"callouts": 36, "unresolved": 8,
                               "cannot_tell": 4, "resolved": 24}
    got, offset, calls = [], 0, 0
    while offset is not None:
        part = call(kit, "check_cross_references", max_chars=1500,
                    handle=handle, offset=offset)
        assert part["callouts"] and part["counts"] == whole["counts"]
        got.extend(part["callouts"])
        offset = part.get("next_offset")
        calls += 1
    assert calls > 3 and len(got) == 36
    assert [r["status"] for r in got] == (
        ["unresolved"] * 8 + ["cannot_tell"] * 4 + ["resolved"] * 24)
    kit.close()


def test_check_cross_references_says_how_many_sheets_it_could_not_read():
    kit = _kit(build_sheet_set(scanned=(3,)).pdf)
    out = call(kit, "check_cross_references", handle=_open(kit))
    assert out["n_sheets_without_text"] == 1
    assert out["counts"] == {"callouts": 8, "unresolved": 0,
                             "cannot_tell": 7, "resolved": 1}
    assert out["notes"] == ["1 sheet(s) have no usable text: their callouts "
                            "are not in this list"]
    kit.close()


def test_a_sheet_named_by_its_page_label_is_marked_so():
    scan = build_sheet_set(scanned=(3,))
    doc = pymupdf.open(stream=scan.pdf, filetype="pdf")
    doc.set_page_labels([{"startpage": i, "prefix": name, "style": "",
                          "firstpagenum": 1}
                         for i, name in enumerate(scan.numbers)])
    data = doc.tobytes()
    doc.close()
    kit = _kit(data)
    handle = _open(kit)
    out = call(kit, "sheet_index", handle=handle)
    assert (out["n_sheets"], out["n_indexed"]) == (5, 5)
    assert out["sheets"][3] == {"page": 3, "sheet": "A-501",
                                "from": "page label"}
    assert [row.get("from") for row in out["sheets"]] == [
        None, None, None, "page label", None]
    assert "not_indexed" not in out
    assert "page labels" in out["notes"][0]
    refs = call(kit, "check_cross_references", handle=handle)
    assert refs["n_sheets_without_text"] == 1
    assert refs["counts"] == {"callouts": 8, "unresolved": 1,
                              "cannot_tell": 6, "resolved": 1}
    kit.close()
