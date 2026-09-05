"""Tests for planlens.dxf.truth — native-annotation ground-truth extraction.

Phase-3.2 addition: paper-space layouts are extracted too (the committed
Mecklenburg corpus records them, so regeneration reproduces the committed
files byte-for-byte — verified against all 10 corpus files 2026-09-05).
"""

from __future__ import annotations

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.dxf.truth import extract_native_annotations


def _doc_with_layout_text():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("MODEL NOTE", dxfattribs={"insert": (1, 2), "height": 0.2})
    layout = doc.layout("Layout1")
    layout.add_text("SHEET TITLE", dxfattribs={"insert": (5, 1),
                                               "height": 0.25})
    return doc


class TestPaperSpaceExtraction:
    def test_layouts_extracted_under_their_names(self):
        truth = extract_native_annotations(doc=_doc_with_layout_text())
        assert set(truth["spaces"]) == {"model", "Layout1"}
        assert truth["spaces"]["model"]["text"][0]["text"] == "MODEL NOTE"
        assert truth["spaces"]["Layout1"]["text"][0]["text"] == "SHEET TITLE"

    def test_model_space_first_in_order(self):
        # Corpus files serialize "model" first; dict order is the contract.
        truth = extract_native_annotations(doc=_doc_with_layout_text())
        assert list(truth["spaces"]) == ["model", "Layout1"]

    def test_empty_layout_still_present(self):
        doc = ezdxf.new("R2010")
        doc.modelspace().add_line((0, 0), (1, 0))
        truth = extract_native_annotations(doc=doc)
        assert "Layout1" in truth["spaces"]
        assert truth["spaces"]["Layout1"]["text"] == []

    def test_exactly_one_source_required(self):
        with pytest.raises(ValueError):
            extract_native_annotations()
        with pytest.raises(ValueError):
            extract_native_annotations(filepath="x.dxf",
                                       doc=_doc_with_layout_text())
