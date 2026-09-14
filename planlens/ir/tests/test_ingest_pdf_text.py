"""from_pdf_vector text: reading direction, annotation exclusion, hidden CAD text.

Uses the synthetic review document, whose drawing sheet is stored portrait with
/Rotate 90 and carries a reviewer's comment, a reply and AutoCAD-style hidden
SHX text. Displayed-frame truth is hand-derived in the fixture.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.ir import from_pdf_vector  # noqa: E402
from planlens.ir.render import ir_to_page_point  # noqa: E402
from planlens.pdf import discover_pdf_content  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def _texts(ir):
    return [e for e in ir.entities if e.KIND == "text"]


def _one(ir, content):
    items = [e for e in _texts(ir) if e.content == content]
    assert len(items) == 1, [e.content for e in _texts(ir)]
    return items[0]


def test_text_direction_is_recorded_in_the_ir_frame(gt):
    ir = from_pdf_vector(content=gt.pdf, page=gt.sheet_page)
    # Upright as displayed on a /Rotate 90 sheet = drawn at 90 on the stored
    # page, which is the frame the IR keeps; the other string is drawn at 0.
    assert _one(ir, gt.sheet_text_upright).rotation == 90.0
    assert _one(ir, gt.sheet_text_vertical).rotation == 0.0


def test_text_position_maps_back_to_its_displayed_origin(gt):
    ir = from_pdf_vector(content=gt.pdf, page=gt.sheet_page)
    item = _one(ir, gt.sheet_text_upright)
    src = fitz.open("pdf", gt.pdf)
    x, y = ir_to_page_point(item.position[0], item.position[1],
                            src[gt.sheet_page])
    src.close()
    ox, oy = gt.sheet_text_upright_origin
    assert abs(x - ox) < 1.0 and abs(y - oy) < 1.0


def test_reviewer_comments_are_not_drawing_text(gt):
    ir = from_pdf_vector(content=gt.pdf, page=gt.sheet_page)
    joined = " ".join(e.content for e in _texts(ir))
    assert "CONFIRM THE PILE" not in joined
    assert "Embedment revised" not in joined
    blocks = discover_pdf_content(content=gt.pdf, page=gt.sheet_page)["text_blocks"]
    assert blocks and all("rotation" in b for b in blocks)
    assert not any("Embedment" in b["text"] for b in blocks)


def test_stamp_wording_is_not_drawing_text(gt):
    ir = from_pdf_vector(content=gt.pdf, page=gt.narrative_page)
    assert gt.stamp_wording not in " ".join(e.content for e in _texts(ir))


def test_cad_hidden_text_is_opt_in(gt):
    plain = from_pdf_vector(content=gt.pdf, page=gt.sheet_page)
    assert gt.hidden_cad_text not in [e.content for e in _texts(plain)]
    assert "n_cad_hidden_text" not in plain.metadata

    ir = from_pdf_vector(content=gt.pdf, page=gt.sheet_page,
                         include_cad_hidden_text=True)
    item = _one(ir, gt.hidden_cad_text)
    assert item.source == "pdf_annotation"
    assert "rotation_estimated_from_box" in item.style
    # A wide box as displayed is a tall box on the stored /Rotate 90 page.
    assert item.rotation == 90.0
    # The copy of a text-layer string at the same spot is skipped.
    assert ir.metadata["n_cad_hidden_text"] == 1
    _one(ir, gt.sheet_text_upright)


def test_unrotated_page_origins_are_unchanged():
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((100, 200), "Sand", fontsize=12)
    page.insert_text((300, 400), "Clay", fontsize=12, rotate=90)
    data = doc.tobytes()
    doc.close()
    blocks = {b["text"]: b for b in discover_pdf_content(content=data)["text_blocks"]}
    assert (blocks["Sand"]["x"], blocks["Sand"]["y"]) == (100.0, 200.0)
    assert blocks["Sand"]["rotation"] == 0.0
    assert (blocks["Clay"]["x"], blocks["Clay"]["y"]) == (300.0, 400.0)
    assert blocks["Clay"]["rotation"] == 90.0
