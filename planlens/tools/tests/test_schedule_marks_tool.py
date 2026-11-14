"""check_schedule_marks, and the sheet index and schedule check that
open_document brings with it, for a toolkit built with
``sheet_tools=True``, on the made-up set of
:mod:`planlens.testing.schedule_set_fixtures`."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing.schedule_set_fixtures import (  # noqa: E402
    TRUTH, build_schedule_set,
)
from planlens.tools import ReviewToolkit  # noqa: E402
from planlens.tools.toolkit import sheets_at_open  # noqa: E402

AT_OPEN = ("sheet_index", "schedule_marks")


@pytest.fixture(scope="module")
def gt():
    return build_schedule_set()


def _kit(pdf, **kw):
    kw.setdefault("sheet_tools", True)
    return ReviewToolkit(resolve_source=lambda key: pdf, **kw)


def call(kit, name, max_chars=None, **args):
    text = kit.call_json(name, args, max_chars=max_chars)
    assert len(text) <= (max_chars or kit.max_chars)
    return json.loads(text)


def _open(kit, **kw):
    return call(kit, "open_document", source="set.pdf", **kw)


def _rows(items):
    return sorted((i["mark"], i["status"], i["side"], i["sheet"])
                  for i in items)


# -- the tool ----------------------------------------------------------------

def test_the_tool_gives_every_item_with_its_pages_and_schedule(gt):
    kit = _kit(gt.pdf)
    out = call(kit, "check_schedule_marks", handle=_open(kit)["handle"])
    assert _rows(out["items"]) == sorted(TRUTH)
    assert out["counts"] == {"schedules": 3, "rows": 16, "rows_found": 10,
                             "no_row": 2, "no_mark": 2,
                             "may_be_on_unread_sheet": 0, "cannot_tell": 4}
    assert [s["schedule"] for s in out["schedules"]] == [
        "DOOR SCHEDULE (A-601, A-602)", "WINDOW SCHEDULE (A-601)",
        "table headed TAG, SERVES, CFM, ... (M-101)"]
    assert out["schedules"][0]["pages"] == "3-4"
    for item in out["items"]:
        assert item["pdf_page"] == item["page"] + 1
        assert len(item["bbox"]) == 4 and item["why"]
    assert "view an item at its bbox" in out["look"]
    assert "pdf_page (1-based" in out["note"]
    assert "next_offset" not in out and "unread_sheets" not in out
    kit.close()


def test_the_tool_filters_by_status_and_pages(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)["handle"]
    rows = call(kit, "check_schedule_marks", handle=handle, status="no_row")
    assert sorted(i["mark"] for i in rows["items"]) == ["105", "W-4"]
    on = call(kit, "check_schedule_marks", handle=handle, pages="4")
    assert {i["page"] for i in on["items"]} == {4}
    # the counts are of the pages asked for; the schedules' of the whole set
    assert on["counts"]["cannot_tell"] == 3
    assert on["counts"]["no_row"] == 0
    assert on["counts"]["rows"] == 16
    bad = call(kit, "check_schedule_marks", handle=handle, status="missing")
    assert bad["error"].startswith("status must be one of ['no_row'")
    bad = call(kit, "check_schedule_marks", handle=handle, offset=40)
    assert "outside" in bad["error"]
    kit.close()


def test_a_small_limit_pages_the_items_and_loses_none(gt):
    kit = _kit(gt.pdf)
    handle = _open(kit)["handle"]
    seen, offset = [], 0
    while True:
        out = call(kit, "check_schedule_marks", max_chars=1400,
                   handle=handle, offset=offset)
        assert out["items"], out
        seen += out["items"]
        if "next_offset" not in out:
            break
        offset = out["next_offset"]
    assert _rows(seen) == sorted(TRUTH)
    kit.close()


def test_a_scanned_sheet_is_listed_with_its_reason(gt):
    scan = build_schedule_set(scanned=(2,))
    kit = _kit(scan.pdf)
    out = call(kit, "check_schedule_marks", handle=_open(kit)["handle"])
    assert out["unread_sheets"] == [{
        "pages": "2", "reason": "no text layer on this sheet: read its "
                                "title block by looking"}]
    assert out["counts"]["may_be_on_unread_sheet"] == 10
    assert any("could not be read" in n for n in out["notes"])
    kit.close()


# -- what open_document brings ----------------------------------------------

def test_open_document_brings_the_sheet_index_and_the_check(gt):
    kit = _kit(gt.pdf)
    opened = _open(kit)
    index = opened["sheet_index"]
    assert index["n_sheets"] == index["n_indexed"] == 6
    assert [(r["page"], r["pdf_page"], r["sheet"])
            for r in index["sheets"]] == [
        (0, 1, "G-001"), (1, 2, "A-101"), (2, 3, "A-102"), (3, 4, "A-601"),
        (4, 5, "A-602"), (5, 6, "M-101")]
    assert index["sheets"][3]["title"] == "DOOR SCHEDULE"
    assert index["more"].startswith("sheet_index")
    marks = opened["schedule_marks"]
    assert marks["counts"]["schedules"] == 3
    assert marks["schedules"][0] == "DOOR SCHEDULE (A-601, A-602)"
    assert _rows(marks["items"]) == sorted(TRUTH)
    assert not any("bbox" in i for i in marks["items"])
    assert marks["more"].startswith("check_schedule_marks")
    assert "no_row:" in marks["note"]
    # after the map, before the optional facts
    keys = list(opened)
    assert keys.index("schedule_marks") == keys.index("sheet_index") + 1
    assert keys.index("sheet_index") > keys.index("pages_by_kind")
    kit.close()


def test_open_document_caps_and_fits_the_block(gt):
    kit = _kit(gt.pdf)
    opened = _open(kit, max_chars=2600)
    marks = opened.get("schedule_marks")
    index = opened["sheet_index"]
    assert index["n_sheets"] == 6
    if marks is not None:
        shown = len(marks.get("items", []))
        assert shown + marks.get("items_left_out", 0) == len(TRUTH)
    # the map is never given up for the block
    assert opened["handle"] and opened["n_pages"] == 6
    kit.close()


def test_open_document_is_unchanged_when_the_caller_did_not_ask(gt):
    plain = ReviewToolkit(resolve_source=lambda key: gt.pdf)
    asked = _kit(gt.pdf)
    before = plain.call_json("open_document", {"source": "set.pdf"})
    with sheets_at_open(False):
        refused = asked.call_json("open_document", {"source": "set.pdf"})
    assert refused == before                     # byte for byte
    assert not set(AT_OPEN) & set(json.loads(before))
    with sheets_at_open(True):
        brought = json.loads(asked.call_json("open_document",
                                             {"source": "set.pdf"}))
    assert set(AT_OPEN) <= set(brought)
    plain.close()
    asked.close()


def test_a_document_with_no_drawing_sheets_brings_nothing():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 72), "A LETTER ABOUT THE DOOR SCHEDULE", fontsize=11)
    pdf = doc.tobytes()
    doc.close()
    plain = ReviewToolkit(resolve_source=lambda key: pdf)
    asked = _kit(pdf)
    assert asked.call_json("open_document", {"source": "a.pdf"}) == \
        plain.call_json("open_document", {"source": "a.pdf"})
    plain.close()
    asked.close()
