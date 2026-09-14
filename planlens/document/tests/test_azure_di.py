"""AzureLayout: an Azure Document Intelligence result as a planlens text source.

No Azure call is made. The results below are hand-built in the documented
``analyzeResult`` shape, in both key styles the SDKs produce, and placed on the
synthetic review document so their coordinates can be checked in the displayed
frame against values derived by hand.
"""

import copy

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import SOURCE_AZURE_DI, open_document  # noqa: E402
from planlens.document.azure_di import (  # noqa: E402
    AzureLayout, _snake, pages_needing_ocr, pages_to_azure_range,
)
from planlens.testing.document_fixtures import (  # noqa: E402
    SHEET_UNROT, build_synthetic_review_document,
)


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def _quad(x0, y0, x1, y1):
    return [x0, y0, x1, y0, x1, y1, x0, y1]


def _scanned_result(page_number=5):
    """What prebuilt-layout would return for the fixture's scanned page."""
    content = "Boring Log B-7\nDepth N\n5 12\n"
    return {
        "apiVersion": "2024-11-30",
        "modelId": "prebuilt-layout",
        "content": content,
        "pages": [{
            "pageNumber": page_number, "angle": 0.0,
            "width": 8.5, "height": 11.0, "unit": "inch",
            "words": [
                {"content": "Boring", "polygon": _quad(1.0, 1.0, 1.8, 1.2),
                 "confidence": 0.99, "span": {"offset": 0, "length": 6}},
                {"content": "Log", "polygon": _quad(1.9, 1.0, 2.3, 1.2),
                 "confidence": 0.97, "span": {"offset": 7, "length": 3}},
                {"content": "B-7", "polygon": _quad(2.4, 1.0, 2.9, 1.2),
                 "confidence": 0.62, "span": {"offset": 11, "length": 3}},
            ],
            "lines": [
                {"content": "Boring Log B-7",
                 "polygon": _quad(1.0, 1.0, 2.9, 1.2),
                 "spans": [{"offset": 0, "length": 14}]},
            ],
            "selectionMarks": [
                {"state": "selected", "polygon": _quad(6.0, 1.0, 6.2, 1.2),
                 "confidence": 0.91},
            ],
        }],
        "paragraphs": [
            {"role": "title", "content": "Boring Log B-7",
             "boundingRegions": [{"pageNumber": page_number,
                                  "polygon": _quad(1.0, 1.0, 2.9, 1.2)}],
             "spans": [{"offset": 0, "length": 14}]},
        ],
        "tables": [{
            "rowCount": 2, "columnCount": 2,
            "boundingRegions": [{"pageNumber": page_number,
                                 "polygon": _quad(1.0, 2.0, 4.0, 3.0)}],
            "cells": [
                {"kind": "columnHeader", "rowIndex": 0, "columnIndex": 0,
                 "content": "Depth"},
                {"kind": "columnHeader", "rowIndex": 0, "columnIndex": 1,
                 "content": "N"},
                {"rowIndex": 1, "columnIndex": 0, "content": "5"},
                {"rowIndex": 1, "columnIndex": 1, "content": "12"},
            ],
        }],
    }


def _to_snake(obj):
    """The same result as snake_case keys with {x, y} polygon points."""
    if isinstance(obj, list):
        return [_to_snake(v) for v in obj]
    if not isinstance(obj, dict):
        return obj
    out = {}
    for k, v in obj.items():
        if k == "polygon":
            v = [{"x": v[i], "y": v[i + 1]} for i in range(0, len(v), 2)]
        out[_snake(k)] = _to_snake(v)
    return out


def _check_scanned_page(doc, gt):
    page = doc.page(gt.scanned_page)
    assert page.text_sources == [SOURCE_AZURE_DI]
    title = [ln for ln in page.lines if ln.text == "Boring Log B-7"][0]
    # Inches x 72, displayed frame, unrotated letter page.
    assert title.bbox == pytest.approx((72.0, 72.0, 208.8, 86.4))
    assert title.rotation == 0.0
    assert title.confidence == pytest.approx(0.62)    # its weakest word
    assert [ln.text for ln in page.lines if ln.text == "[x]"] == ["[x]"]
    assert [b.role for b in page.blocks] == ["title"]
    assert page.blocks[0].line_ids == [title.id]
    (table,) = page.tables
    assert table.header == ["Depth", "N"]
    assert table.rows == [["5", "12"]]
    assert page.stats["n_low_confidence_words"] == 1
    assert not any("OCR" in w for w in page.warnings)


def test_camel_case_result_fills_the_scanned_page(gt):
    layout = AzureLayout(_scanned_result())
    assert layout.pages == [gt.scanned_page]
    with open_document(gt.pdf, text_source=layout) as doc:
        _check_scanned_page(doc, gt)
        summary = doc.summary(gt.scanned_page)
        assert summary.heading == "Boring Log B-7"
        assert summary.evidence["text_source"] == SOURCE_AZURE_DI
        assert not summary.evidence.get("needs_ocr")
        hits = doc.search("B-7")["hits"]
        assert [(h["page"], h.get("source")) for h in hits] == [
            (gt.scanned_page, SOURCE_AZURE_DI)]


def test_snake_case_result_with_point_dicts_is_equivalent(gt):
    layout = AzureLayout(_to_snake(_scanned_result()))
    with open_document(gt.pdf, text_source=layout) as doc:
        _check_scanned_page(doc, gt)


def test_rest_envelope_is_accepted(gt):
    layout = AzureLayout({"status": "succeeded",
                          "analyzeResult": _scanned_result()})
    assert layout.pages == [gt.scanned_page]


def test_uncovered_pages_keep_the_pdf_text_layer(gt):
    layout = AzureLayout(_scanned_result())
    with open_document(gt.pdf, text_source=layout) as doc:
        assert doc.page(gt.narrative_page).text_sources == ["pdf_text"]


def _sheet_result(width_in, height_in, polygon):
    return {"pages": [{
        "pageNumber": 2, "width": width_in, "height": height_in,
        "unit": "inch", "words": [],
        "lines": [{"content": "PILE CAP", "polygon": polygon,
                   "spans": [{"offset": 0, "length": 8}]}],
    }]}


def test_rotated_sheet_in_displayed_frame(gt):
    # 36x24 in = the sheet as displayed: coordinates pass straight through.
    layout = AzureLayout(_sheet_result(36.0, 24.0, _quad(10.0, 5.0, 12.0, 5.5)))
    with open_document(gt.pdf, text_source=layout) as doc:
        page = doc.page(gt.sheet_page, tables=False)
        (ln,) = [l for l in page.lines if l.source == SOURCE_AZURE_DI]
        assert ln.bbox == pytest.approx((720.0, 360.0, 864.0, 396.0))
        assert ln.rotation == 0.0
        assert page.stats["azure_frame"] == "displayed"


def test_rotated_sheet_in_unrotated_frame(gt):
    # 24x36 in = the sheet as stored. A horizontal line there, from unrotated
    # (x, y) = (5, 10) to (5.5, 10) in inches, displays at (H - y, x) with
    # H = 2592 pt and reads top-to-bottom.
    layout = AzureLayout(_sheet_result(24.0, 36.0, _quad(5.0, 10.0, 7.0, 10.5)))
    with open_document(gt.pdf, text_source=layout) as doc:
        page = doc.page(gt.sheet_page, tables=False)
        (ln,) = [l for l in page.lines if l.source == SOURCE_AZURE_DI]
        h = SHEET_UNROT[1]
        xs = [h - 10.0 * 72, h - 10.5 * 72]
        ys = [5.0 * 72, 7.0 * 72]
        assert ln.bbox == pytest.approx((min(xs), min(ys), max(xs), max(ys)))
        assert ln.rotation == 270.0
        assert page.stats["azure_frame"] == "unrotated"


def test_pixel_units_scale_by_aspect(gt):
    result = _scanned_result()
    page = result["pages"][0]
    page.update(width=1700, height=2200, unit="pixel")
    s = 1700 / 8.5          # pixels per inch in this synthetic image
    for w in page["words"] + page["lines"] + page["selectionMarks"]:
        w["polygon"] = [v * s for v in w["polygon"]]
    for item in result["paragraphs"] + result["tables"]:
        for reg in item["boundingRegions"]:
            reg["polygon"] = [v * s for v in reg["polygon"]]
    with open_document(gt.pdf, text_source=AzureLayout(result)) as doc:
        _check_scanned_page(doc, gt)


def test_mismatched_page_size_warns(gt):
    result = _scanned_result()
    result["pages"][0].update(width=5.0, height=5.0)
    with open_document(gt.pdf, text_source=AzureLayout(result)) as doc:
        page = doc.page(gt.scanned_page)
        assert any("matches neither" in w for w in page.warnings)


def test_page_numbering_is_reconciled(gt):
    # Azure kept the document's numbering.
    assert AzureLayout(_scanned_result(5), analyzed_pages=[4]).pages == [4]
    # Azure renumbered the analysed subset from 1.
    assert AzureLayout(_scanned_result(1), analyzed_pages=[4]).pages == [4]
    # Neither: refuse rather than misplace text.
    with pytest.raises(ValueError, match="cannot place"):
        AzureLayout(_scanned_result(3), analyzed_pages=[4, 6])


def test_pages_to_azure_range():
    assert pages_to_azure_range([0, 1, 2, 4, 7, 8]) == "1-3,5,8-9"
    assert pages_to_azure_range([3]) == "4"
    with pytest.raises(ValueError):
        pages_to_azure_range([])


def test_analyze_passes_the_page_range_and_refuses_nothing(gt):
    seen = {}

    def fake(content, page_range=None, **kw):
        seen["page_range"] = page_range
        return copy.deepcopy(_scanned_result(1))

    layout = AzureLayout.analyze(fake, gt.pdf, pages=[4])
    assert seen["page_range"] == "5"
    assert layout.pages == [4]
    with pytest.raises(RuntimeError, match="budget"):
        AzureLayout.analyze(lambda c, page_range=None: None, gt.pdf)


def test_pages_needing_ocr(gt):
    with open_document(gt.pdf) as doc:
        assert pages_needing_ocr(doc) == [gt.scanned_page]
