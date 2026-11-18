"""PL-37 review: where ``pdf_pages`` / ``pdf_page`` (1-based) and a sheet
number name different pages, the refusal names the argument the caller gave
with the number the caller gave, not the 0-based page the host layer turned
it into."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review.host import ReviewHost  # noqa: E402
from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402


@pytest.fixture(scope="module")
def gt():
    return build_sheet_set()


def _call(name, args, gt, host):
    return json.loads(review.call(name, args, {"set.pdf": gt.pdf},
                                  host=host))


def test_pdf_pages_beside_sheets_is_worded_as_the_caller_gave_it(gt):
    asked = ReviewHost(toolkit_options={"sheet_tools": True,
                                        "plan_tools": True})
    handle = _call("open_document", {"source": "set.pdf"}, gt,
                   asked)["handle"]
    page, number = 1, gt.numbers[1]
    out = _call("read_document", {"handle": handle, "sheets": number,
                                  "pdf_pages": "3-4"}, gt, asked)
    assert out["error"] == (
        f"pdf_pages '3-4' and sheets {number} name different pages (the "
        f"sheet index reads {number} on page {page} (pdf_page {page + 1}))")
    assert out["hint"] == "give one of them, pdf_pages or sheets"
    one = _call("plan_symbols", {"handle": handle, "sheet": number,
                                 "pdf_page": 3}, gt, asked)
    assert one["error"].startswith(f"pdf_page 3 and sheet {number} name "
                                   f"different pages")
    assert one["hint"] == "give one of them, pdf_page or sheet"
    # pages given 0-based is worded 0-based, as it was
    zero = _call("read_document", {"handle": handle, "sheets": number,
                                   "pages": "2"}, gt, asked)
    assert zero["error"].startswith(f"pages '2' and sheets {number} name")
    assert zero["hint"] == "give one of them, pages or sheets"
    # and the next call, with pages, is not worded by the one before
    again = _call("read_document", {"handle": handle, "sheets": number,
                                    "pages": "0"}, gt, asked)
    assert again["error"].startswith(f"pages '0' and sheets {number} name")
