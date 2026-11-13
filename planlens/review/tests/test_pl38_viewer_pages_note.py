"""PL-38: a result too near its size limit to carry the viewer's page
numbers says so, instead of every row losing its pdf_page with no word.
A result under the limit is what it was. All values are made up."""
import json

import pytest

from planlens.review import conversation
from planlens.review.conversation import (VIEWER_PAGES_KEY,
                                          VIEWER_PAGES_LEFT_OUT,
                                          with_viewer_pages)


def _rows(n=40):
    return json.dumps({"handle": "doc_0123456789",
                       "rows": [{"page": i, "kind": "text", "words": 120}
                                for i in range(n)]},
                      separators=(",", ":"))


def test_page_numbers_left_out_near_the_limit_are_said_to_be():
    raw = _rows()
    full = with_viewer_pages(raw)
    assert len(full) > len(raw) + 400          # 40 rows gained pdf_page
    limit = len(raw) + 300                     # the note fits, they do not
    out = json.loads(with_viewer_pages(raw, limit))
    assert out[VIEWER_PAGES_KEY] == VIEWER_PAGES_LEFT_OUT
    assert not any("pdf_page" in row for row in out["rows"])
    assert {k: v for k, v in out.items() if k != VIEWER_PAGES_KEY} == \
        json.loads(raw)
    assert len(json.dumps(out, ensure_ascii=False,
                          separators=(",", ":"))) <= limit


def test_a_result_under_the_limit_is_what_it_was():
    raw = _rows()
    full = with_viewer_pages(raw)
    assert with_viewer_pages(raw, len(full)) == full
    assert VIEWER_PAGES_KEY not in json.loads(full)
    assert json.loads(full)["rows"][3]["pdf_page"] == 4


def test_no_note_where_no_page_number_was_left_out():
    # over the limit with nothing to add: out as it came, no note
    raw = json.dumps({"text": "x" * 500})
    assert with_viewer_pages(raw, 100) == raw
    # the note itself does not fit: out as it came
    rows = _rows()
    assert with_viewer_pages(rows, len(rows) + 10) == rows


def _without_viewer_pages(obj):
    if isinstance(obj, dict):
        return {k: _without_viewer_pages(v) for k, v in obj.items()
                if not k.startswith("pdf_")}
    if isinstance(obj, list):
        return [_without_viewer_pages(v) for v in obj]
    return obj


def test_a_document_tool_near_the_host_cap_says_so(tmp_path):
    pymupdf = pytest.importorskip("pymupdf")
    from planlens import review
    doc = pymupdf.open()
    for i in range(40):
        doc.new_page(width=612, height=792).insert_text(
            (72, 100), f"Made-up report page {i + 1}", fontsize=11)
    att = {"report.pdf": doc.tobytes()}
    doc.close()
    review.configure(review.ReviewHost(working_folder=lambda: str(tmp_path)))
    handle = json.loads(review.call("open_document", {"source": "report.pdf"},
                                    attachments=att))["handle"]
    args = {"handle": handle}
    full = conversation.dispatch_document_tool(
        "document_page_map", dict(args), att, max_chars=20000)
    assert json.loads(full)["rows"][0]["pdf_page"] == 1
    bare = json.dumps(_without_viewer_pages(json.loads(full)),
                      ensure_ascii=False, separators=(",", ":"))
    # the host's cap leaves room for the note, not for 40 pdf_page keys
    near = json.loads(conversation.dispatch_document_tool(
        "document_page_map", dict(args), att, max_chars=20000,
        cap=len(bare) + 320))
    assert near[VIEWER_PAGES_KEY] == VIEWER_PAGES_LEFT_OUT
    assert "pdf_page" not in near["rows"][0]
    assert len(near["rows"]) == 40
    # with room for them, the same call is what it was
    assert conversation.dispatch_document_tool(
        "document_page_map", dict(args), att, max_chars=20000,
        cap=len(full) + 20) == full
