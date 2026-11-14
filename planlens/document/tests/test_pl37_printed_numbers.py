"""PL-37: the sheet number a page prints (``printed_numbers``), read in the
title-block bands by the sheet index's rules for one sheet; a sheet count
is never the number. Every sheet here is made up inside the test."""
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.structure import printed_numbers  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

W, H = 36 * 72.0, 24 * 72.0          # a 36 x 24 in sheet


def _sheet(texts, size=(W, H)):
    """One sheet: a border, some line-work, and ``texts`` as
    ``(x_fraction, y_fraction, text, fontsize)`` (y is the baseline)."""
    doc = pymupdf.open()
    w, h = size
    page = doc.new_page(width=w, height=h)
    shape = page.new_shape()
    shape.draw_rect(pymupdf.Rect(20, 20, w - 20, h - 20))
    for i in range(12):
        shape.draw_line((60 + 40 * i, 80), (60 + 40 * i, h * 0.5))
    shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    for fx, fy, text, size_pt in texts:
        page.insert_text((fx * w, fy * h), text, fontsize=size_pt)
    pdf = doc.tobytes()
    doc.close()
    return pdf


def _summary(pdf):
    with Document(content=pdf, name="set.pdf") as d:
        return d.summary(0)


def test_a_label_and_number_on_one_line_in_a_right_title_block():
    # the right-hand title block, well above the bottom 8 % band that was
    # the only place read before
    s = _summary(_sheet([(0.88, 0.85, "SHEET NO. S-201A", 10),
                         (0.88, 0.80, "FOUNDATION PLAN", 12)]))
    assert s.sheet == "S-201A"


def test_the_dwg_no_forms():
    s = _summary(_sheet([(0.80, 0.95, "DWG. NO.: A-101", 10)]))
    assert s.sheet == "A-101"
    assert printed_numbers(["DWG. NO.: A-101"])["sheet"] == "A-101"
    assert printed_numbers(["DRAWING NO. C-3.1"])["sheet"] == "C-3.1"
    assert printed_numbers(["SHEET NO. S-201A"])["sheet"] == "S-201A"


def test_a_label_and_its_value_in_separate_cells():
    s = _summary(_sheet([(0.88, 0.80, "DRAWING NUMBER", 9),
                         (0.88, 0.80 + 26 / H, "C-102", 18)]))
    assert s.sheet == "C-102"
    # without positions, a label alone and the text read after it
    assert printed_numbers(["SHEET NO.", "S-201A"])["sheet"] == "S-201A"


def test_a_sheet_count_is_never_the_sheet_number():
    s = _summary(_sheet([(0.80, 0.95, "SHEET 3 OF 24", 10)]))
    assert s.sheet is None
    assert s.evidence["sheet_count"] == "3 of 24"
    # with a number printed too, each goes where it belongs
    s = _summary(_sheet([(0.80, 0.95, "SHEET 3 OF 24", 10),
                         (0.88, 0.85, "SHEET NO. S-203", 10)]))
    assert (s.sheet, s.evidence["sheet_count"]) == ("S-203", "3 of 24")
    assert printed_numbers(["SHEET", "3", "OF 24"]) == {
        "sheet_count": "3 of 24"}
    # a page's own "Page 3 of 24" numbers the page, not the sheet
    assert printed_numbers(["Project 1234 sheet log | Page 3 of 24"]) == {
        "printed_page": 3, "printed_of": 24}


def test_a_note_naming_another_sheet_is_not_the_number():
    s = _summary(_sheet([
        (0.05, 0.95, "1. REFER TO DWG NO. S-501 FOR DETAILS.", 9),
        (0.88, 0.85, "SHEET NO. A-101", 10)]))
    assert s.sheet == "A-101"
    assert "sheet" not in printed_numbers(
        ["1. REFER TO DWG NO. S-501 FOR DETAILS."])
    for note in ("SEE SHEET NO. A-5 FOR THE LAYOUT",
                 "AS SHOWN ON DWG NO. C-2, TYPICAL"):
        assert "sheet" not in printed_numbers([note]), note
    # a footer printed as one line keeps its label's value
    assert printed_numbers(["ACME ENGINEERS | DWG NO. C-101 | REV 2"]
                           )["sheet"] == "C-101"
    # a report page is read in its header and footer bands only: prose low
    # on the page that opens with a label is not its sheet number
    report = _sheet([(0.12, 0.85, "SHEET NO. S-2 SHOWS THE FOOTING LAYOUT", 10),
                     (0.12, 0.30, "Made-up report text " * 6, 10)],
                    size=(612.0, 792.0))
    assert _summary(report).sheet is None


def test_a_large_number_alone_in_the_corner_of_a_sheet():
    texts = [(0.90, 0.96, "A-501", 40), (0.80, 0.90, "PROJECT ALPHA", 8),
             (0.80, 0.92, "DRAWN BY: J.D.", 8), (0.80, 0.94, "SCALE 1:50", 8)]
    assert _summary(_sheet(texts)).sheet == "A-501"
    # the size rule is a sheet's: a letter page's big corner text is not read
    letter = _sheet(texts, size=(612.0, 792.0))
    assert _summary(letter).sheet is None


def test_the_page_map_row_keeps_number_and_count_apart(tmp_path):
    pdf = _sheet([(0.80, 0.95, "SHEET 3 OF 24", 10),
                  (0.88, 0.85, "SHEET NO. S-203", 10)])
    kit = ReviewToolkit(resolve_source=lambda key: pdf,
                        output_dir=str(tmp_path))
    try:
        handle = json.loads(kit.call_json(
            "open_document", {"source": "set.pdf"}))["handle"]
        (row,) = json.loads(kit.call_json(
            "document_page_map", {"handle": handle}))["rows"]
        assert (row["sheet"], row["sheet_count"]) == ("S-203", "3 of 24")
        (row,) = json.loads(kit.call_json(
            "document_page_map", {"handle": handle,
                                  "with_evidence": True}))["rows"]
        assert row["sheet_count"] == "3 of 24"
        assert "sheet_count" not in row["evidence"]
        out = json.loads(kit.call_json("document_structure",
                                       {"handle": handle}))
        assert out["segments"][0]["sheets"] == ["S-203"]
    finally:
        kit.close()
