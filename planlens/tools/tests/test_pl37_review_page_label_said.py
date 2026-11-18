"""PL-37 review: a sheet number the index took from the PDF's page label
(a scan: the title block was not read) is not a reading of the sheet, and
the file's labels can name another page than the one that prints the
number. ``sheet_pages`` says where such a number came from, in the words the
sheet index's own rows use (``from``)."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing.sheet_set_fixtures import build_sheet_set  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402


def _labelled(gt, labels):
    doc = pymupdf.open(stream=gt.pdf, filetype="pdf")
    doc.set_page_labels([{"startpage": i, "prefix": label, "style": "",
                          "firstpagenum": 1}
                         for i, label in enumerate(labels)])
    data = doc.tobytes()
    doc.close()
    return data


def test_a_number_from_the_pdf_page_label_is_said_to_be_one():
    # pages 1 and 2 are scans; the file labels them the other way round
    gt = build_sheet_set(scanned=[1, 2])
    n = gt.numbers
    pdf = _labelled(gt, [n[0], n[2], n[1], n[3], n[4]])
    kit = ReviewToolkit(resolve_source=lambda key: pdf, sheet_tools=True)

    def call(name, **args):
        return json.loads(kit.call_json(name, args))

    h = call("open_document", source="set.pdf")["handle"]
    by_label = call("read_document", handle=h, sheets=n[1])
    words = [w for start, _short, w in ReviewToolkit.SHEET_FROM
             if start == "PDF page label"][0]
    assert by_label["sheet_pages"] == [{"sheet": n[1], "page": 2,
                                        "from": words}]
    # a number read from the title block's slot carries no such word
    read = call("read_document", handle=h, sheets=n[3])
    assert read["sheet_pages"] == [{"sheet": n[3], "page": 3}]
    both = call("document_page_map", handle=h, sheets=[n[3], n[2]])
    assert both["sheet_pages"] == [{"sheet": n[3], "page": 3},
                                   {"sheet": n[2], "page": 1,
                                    "from": words}]
