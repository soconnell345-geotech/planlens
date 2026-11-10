"""sheet_index and check_cross_references: offered only by a toolkit built
with ``sheet_tools=True``, and what they return on a made-up drawing set
(:mod:`planlens.testing.sheet_set_fixtures`)."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing.sheet_set_fixtures import (  # noqa: E402
    build_sheet_set, sketch_pdf,
)
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
        "status": "unresolved",
        "why": "view marks recognised on A-501: 1 to 4; none for 5",
        "target": {"sheet": "A-501", "page": 3},
        "bbox": rows[0]["bbox"]}
    assert all(isinstance(v, int) for v in rows[0]["bbox"])
    assert rows[1]["callout"] == "1/A-301" and "target" not in rows[1]
    assert rows[1]["why"] == "there is no sheet A-301 in this document"
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


# -- a result that always fits, and says how each number was found -----------

def _strip(number, nth, of, label="SHEET NO."):
    def draw(s, _i):
        s.strip_block(number, f"PLAN {nth}", nth, of, label=label)
    return draw


def _pages_of(group):
    out = []
    for part in group.split(","):
        a, _, b = part.partition("-")
        out.extend(range(int(a), int(b or a) + 1))
    return out


@pytest.mark.parametrize("max_chars", [1000, 2500, 12000])
def test_sheet_index_always_fits_and_says_what_it_left_out(max_chars):
    """Sixty sheets numbered and sixty between them that print no number:
    the sheets not indexed are one reason with its pages, cut where they
    are scattered, and the rows page on by next_offset whatever the
    limit."""
    of = 120
    draw = [_strip(None if i % 2 == 0 else f"A-{101 + i}", i + 1, of)
            for i in range(of)]
    kit = _kit(sketch_pdf(draw))
    handle = _open(kit)
    out = call(kit, "sheet_index", max_chars=max_chars, handle=handle)
    assert "error" not in out and out["sheets"]
    assert (out["n_sheets"], out["n_indexed"]) == (120, 60)
    if "not_indexed" in out:
        (group,) = out["not_indexed"]
        assert group["reason"] == ("no sheet number found in the title "
                                   "block's text (it prints a sheet count)")
        shown = _pages_of(group["pages"])
        assert shown == list(range(0, 2 * len(shown), 2))
        assert len(group["pages"]) <= 120
        assert group["more_sheets"] == 60 - len(shown)
    else:                               # a small limit: a count in its place
        assert out["not_indexed_left_out"] == {"reasons": 1, "sheets": 60}
    assert ("notes" in out) != ("notes_left_out" in out)
    got, offset = [], 0
    while offset is not None:
        part = call(kit, "sheet_index", max_chars=max_chars, handle=handle,
                    offset=offset)
        got.extend(part["sheets"])
        offset = part.get("next_offset")
    assert [r["sheet"] for r in got] == [f"A-{101 + i}"
                                         for i in range(1, of, 2)]
    kit.close()


def test_sheet_index_of_sheets_that_print_only_a_count_is_one_line():
    draw = [_strip(None, i + 1, 80, label=None) for i in range(80)]
    kit = _kit(sketch_pdf(draw))
    out = call(kit, "sheet_index", max_chars=1000, handle=_open(kit))
    assert out["n_indexed"] == 0 and out["sheets"] == []
    assert out["not_indexed"] == [{
        "pages": "0-79", "reason": "no sheet number found in the title "
                                   "block's text (it prints a sheet count)"}]
    kit.close()


def test_a_set_bound_in_twice_is_said_plainly_and_fits(gt):
    kit = _kit(_joined(gt.pdf, gt.pdf))
    handle = _open(kit)
    out = call(kit, "sheet_index", max_chars=1500, handle=handle)
    assert "error" not in out
    assert out["notes"] == [
        "every sheet number in this document is read on two pages (5 "
        "numbers: G-001 (pages 0, 5), A-101 (pages 1, 6), A-102 (pages 2, "
        "7), and 2 more); the document may hold the set twice over"]
    assert all(row["duplicate"] is True for row in out["sheets"])
    assert "duplicate: the number is read on another page too" in out["note"]
    kit.close()


def test_each_row_says_how_its_number_was_found(gt):
    """Four sheets read from the place their title blocks share carry no
    mark; one read from its large corner lettering, one from its own view
    titles and one from its label say so; a number on two pages is marked
    on both; the note explains the marks that occur."""
    def big(s, _i):
        s.bottom_block("S-201", "FOUNDATION PLAN", number_size=28.0)

    def titled(s, _i):
        s.bottom_block("S-202", "FRAMING PLAN", number_size=14.0)
        s.view_title(330, 1300, "1", "S-202", "FRAMING PLAN")

    def labelled(s, _i):
        s.page.draw_rect((36, 36, 2412, 1548), width=1.5)
        s.text(2100, 120, "DWG NO.", 7)
        s.text(2160, 124, "M-101", 14, bold=True)

    draw = [_strip(n, i + 1, 8) for i, n in
            enumerate(["G-001", "A-101", "A-102", "A-501"])]
    draw += [big, titled, labelled, _strip("A-102", 8, 8)]
    kit = _kit(sketch_pdf(draw))
    out = call(kit, "sheet_index", handle=_open(kit))
    marks = [(r["sheet"], r.get("from"), r.get("duplicate"))
             for r in out["sheets"]]
    assert marks == [
        ("G-001", None, None), ("A-101", None, None), ("A-102", None, True),
        ("A-501", None, None), ("S-201", "large lettering", None),
        ("S-202", "view titles", None), ("M-101", "label", None),
        ("A-102", None, True)]
    note = out["note"]
    for words in ("large lettering: the largest number in the title "
                  "block's corner", "view titles: named by the sheet's own "
                  "view titles", "label: beside a SHEET NO. label",
                  "duplicate: the number is read on another page too"):
        assert words in note
    assert "page label" not in note
    kit.close()
    # a set read wholly from its slot: no marks, and the note does not
    # explain any
    kit = _kit(gt.pdf)
    out = call(kit, "sheet_index", handle=_open(kit))
    assert not any("from" in r or "duplicate" in r for r in out["sheets"])
    assert out["note"] == (
        "page is the 0-based PDF page these tools take; sheet, title and "
        "rev are as the sheet's own title block prints them; views are the "
        "view marks recognised on the sheet")
    kit.close()


def test_the_tool_text_says_what_the_code_reads():
    specs = {s["name"]: s["description"] for s in SHEET_TOOL_SPECS}
    for text in specs.values():
        assert "in the set" not in text and "document" in text
    check = specs["check_cross_references"]
    assert not check.startswith("Every")
    for words in ("upright text", "turned callout", "elevation marker",
                  "SIM", "no mark for that view was recognised",
                  "could not be indexed", "discipline", "two pages",
                  "not read as one", "no text layer"):
        assert words in check, words
    index = specs["sheet_index"]
    assert "view marks recognised" in index and "page label" in index


def test_sheet_index_fits_whatever_the_index_holds(gt):
    """An index with thirty reasons, long notes and long titles: under any
    limit the result fits, holds rows, and counts what it left out."""
    from planlens.document.crossrefs import CrossReferences
    from planlens.document.sheets import SheetIndex, SheetRow
    rows = [SheetRow(page=i, reason=f"reason {i}: " + "x" * 250)
            for i in range(0, 60, 2)]
    rows += [SheetRow(page=i, number=f"A-{100 + i}", title="T" * 150,
                      how="label", confidence=0.75) for i in range(1, 60, 2)]
    rows.sort(key=lambda r: r.page)
    index = SheetIndex(rows=rows, n_pages=60,
                       notes=[f"note {k}: " + "y" * 300 for k in range(8)])
    kit = _kit(gt.pdf)
    handle = _open(kit)
    doc = kit._entries[handle].doc
    doc._sheet_index, doc._cross_references = index, CrossReferences()
    seen_cut = set()
    for max_chars in (1000, 1500, 4000, 12000):
        out = call(kit, "sheet_index", max_chars=max_chars, handle=handle)
        assert "error" not in out and out["sheets"], max_chars
        shown = len(out.get("not_indexed", []))
        left = out.get("not_indexed_left_out", {"reasons": 0, "sheets": 0})
        assert shown + left["reasons"] == 30
        assert shown + left["sheets"] == 30
        assert len(out.get("notes", [])) + out.get("notes_left_out", 0) == 8
        if "notes_left_out" in out:
            seen_cut.add("notes")
        if "not_indexed" not in out:
            seen_cut.add("not_indexed")
    assert seen_cut == {"notes", "not_indexed"}
    kit.close()
