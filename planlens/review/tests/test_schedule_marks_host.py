"""check_schedule_marks and what open_document brings, through the host
layer: only a host that asked for the sheet tools gets either; every other
host is answered byte for byte as before."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing.schedule_set_fixtures import (  # noqa: E402
    TRUTH, build_schedule_set,
)

ASK = {"sheet_tools": True}
AT_OPEN = ("sheet_index", "schedule_marks")


@pytest.fixture(scope="module")
def gt():
    return build_schedule_set()


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def _rows(items):
    return sorted((i["mark"], i["status"], i["side"], i["sheet"])
                  for i in items)


def test_the_tool_through_the_host_gives_both_page_numbers(gt):
    tools = make_tools(None, None, {"set.pdf": gt.pdf}, toolkit_options=ASK)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    out = _invoke(tools["check_schedule_marks"], handle=handle)
    assert _rows(out["items"]) == sorted(TRUTH)
    w4 = next(i for i in out["items"] if i["mark"] == "W-4")
    assert (w4["sheet"], w4["page"], w4["pdf_page"]) == ("A-101", 1, 2)
    assert w4["schedule"] == "WINDOW SCHEDULE (A-601)"
    # the viewer's page number in place of the 0-based one
    fifth = _invoke(tools["check_schedule_marks"], handle=handle,
                    pdf_pages="5")
    assert {i["page"] for i in fifth["items"]} == {4}
    bad = _invoke(tools["check_schedule_marks"], handle=handle,
                  status="wrong")
    assert bad["error"].startswith("status must be one of")


def test_open_document_brings_both_only_to_a_host_that_asked(gt):
    from planlens.review import conversation
    from planlens.review.host import ReviewHost
    att = {"set.pdf": gt.pdf}
    plain, asked = ReviewHost(), ReviewHost(toolkit_options=ASK)
    conversation._SPACES.clear()
    before = review.call("open_document", {"source": "set.pdf"}, att,
                         host=plain)
    assert not set(AT_OPEN) & set(json.loads(before))
    # one toolkit, shared and built with the sheet tools: the calling host
    # decides, each call
    for host in (asked, plain, asked):
        raw = review.call("open_document", {"source": "set.pdf"}, att,
                          host=host)
        opened = json.loads(raw)
        if host is plain:
            assert raw == before
        else:
            assert set(AT_OPEN) <= set(opened)
            rows = opened["sheet_index"]["sheets"]
            assert all(r["pdf_page"] == r["page"] + 1 for r in rows)
            items = opened["schedule_marks"]["items"]
            assert _rows(items) == sorted(TRUTH)
            assert all(i["pdf_page"] == i["page"] + 1 for i in items)
    assert len(conversation._SPACES) == 1
