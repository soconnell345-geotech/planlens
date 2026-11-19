"""Audit of how planlens reads a report (2026-11-18): the printed page
numbers in ``document_structure``.

A segment's ``printed_pages`` are the numbers printed on its pages. The host
layer gives every page range a viewer's 1-based twin (``pdf_...``), and it
gave one to ``printed_pages`` too: ``pdf_printed_pages`` one higher than the
printed numbers, a page number no page prints and no viewer shows.
"""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.review.conversation import _viewer_pages  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402


def _report() -> bytes:
    pdf = pymupdf.open()
    font = pymupdf.Font("helv")
    for n in range(1, 4):
        page = pdf.new_page(width=612, height=792)
        writer = pymupdf.TextWriter(page.rect)
        writer.append((72, 40), "Zorvex Ridge Report", font=font, fontsize=8)
        for i in range(20):
            writer.append((72, 100 + 14 * i),
                          f"Line {i} of the narrative about blorite fill.",
                          font=font, fontsize=10)
        writer.append((300, 762), f"Page {n} of 3", font=font, fontsize=9)
        writer.write_text(page)
    data = pdf.tobytes()
    pdf.close()
    return data


def test_printed_pages_get_no_viewer_twin():
    out = _viewer_pages({"pages": "0-2", "printed_pages": "1-3"})
    assert out == {"pages": "0-2", "pdf_pages": "1-3",
                   "printed_pages": "1-3"}


def test_document_structure_cites_the_printed_numbers_only(tmp_path):
    tools = make_tools(None, str(tmp_path), {"r.pdf": _report()})
    handle = json.loads(tools["open_document"].invoke(
        {"source": "r.pdf"}))["handle"]
    out = json.loads(tools["document_structure"].invoke({"handle": handle}))
    seg = out["segments"][0]
    assert seg["printed_pages"] == "1-3"
    assert seg["pdf_pages"] == "1-3"
    assert "pdf_printed_pages" not in seg
