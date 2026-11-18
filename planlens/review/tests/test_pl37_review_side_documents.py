"""PL-37 review: a Word, Excel or DXF file is read as text, with no pages
and no sheet index. For a host that asked for the sheet tools, ``sheets``
on such a file is refused with words that say so; it was dropped without a
word and the whole file read from the top."""

import json

from planlens import review
from planlens.review import conversation
from planlens.review.host import ReviewHost, using

#: A made-up drawing: one line of text.
DXF = (b"0\nSECTION\n2\nENTITIES\n0\nTEXT\n8\nNOTES\n10\n0.0\n20\n0.0\n40\n"
       b"2.5\n1\nSEE DETAIL 3 ON S-201\n0\nENDSEC\n0\nEOF\n")
FILES = {"plan.dxf": DXF}


def _call(name, args, host):
    return json.loads(review.call(name, args, FILES, host=host))


def test_sheets_on_a_file_read_as_text_is_refused_for_a_host_that_asked():
    asked = ReviewHost(toolkit_options={"sheet_tools": True})
    plain = _call("read_document", {"handle": "plan.dxf"}, asked)
    assert "S-201" in plain["text"]
    for name, args in (("read_document", {}),
                       ("search_document", {"pattern": "DETAIL"})):
        out = _call(name, {"handle": "plan.dxf", "sheets": "S-201", **args},
                    asked)
        assert out["error"].startswith(
            "sheets 'S-201': 'plan.dxf' is read as text, with no pages and "
            "no sheet index")
        assert "text" not in out and "hits" not in out
        assert "leave sheets out" in out["hint"]
    # an empty sheets is no sheets
    assert _call("read_document", {"handle": "plan.dxf", "sheets": ""},
                 asked) == plain


def test_a_host_that_did_not_ask_is_answered_as_before():
    host = ReviewHost()
    refused = _call("read_document", {"handle": "plan.dxf",
                                      "sheets": "S-201"}, host)
    assert refused["error"] == "read_document takes no argument(s) ['sheets']"
    # the dispatcher the app's own tool functions call: as at the base
    with using(host):
        raw = conversation.dispatch_document_tool(
            "read_document", {"handle": "plan.dxf", "sheets": "S-201"},
            FILES)
        same = conversation.dispatch_document_tool(
            "read_document", {"handle": "plan.dxf"}, FILES)
    assert raw == same and "S-201" in json.loads(raw)["text"]
