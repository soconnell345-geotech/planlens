"""Tests for planlens.ocr — the B7 raster/OCR leg.

Engine-gated: skipped wholesale when the [ocr] extra (RapidOCR) is not
installed. The fixtures draw REAL fonts (so what OCR reads is known),
but everything asserted about coordinates goes through the same frame
contract the vector ingest uses — that agreement is the point of B7.
"""

import math

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("rapidocr_onnxruntime")

from planlens.ir import from_pdf_vector
from planlens.ocr import augment_ir_with_ocr, ocr_text_items

W, H = 612.0, 792.0
LABEL = "FOUNDATION PLAN"
LABEL_PDF_XY = (150.0, 300.0)  # insert_text point, PDF top-left frame


def _build(tmp_path, rotate_text=0):
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text(fitz.Point(*LABEL_PDF_XY), LABEL, fontsize=14,
                     rotate=rotate_text)
    page.insert_text(fitz.Point(120.0, 500.0), "SCALE 1:100", fontsize=12,
                     rotate=rotate_text)
    # a bit of linework so the page is not text-only
    s = page.new_shape()
    s.draw_line(fitz.Point(100, 600), fitz.Point(400, 600))
    s.finish(color=(0, 0, 0), width=1.0)
    s.commit()
    p = str(tmp_path / f"ocr_fixture_{rotate_text}.pdf")
    doc.save(p)
    doc.close()
    return p


def _find(items, needle):
    return [i for i in items if needle in i.content.upper()]


class TestOcrTextItems:
    def test_reads_horizontal_text_at_true_position(self, tmp_path):
        path = _build(tmp_path)
        items = ocr_text_items(filepath=path, rotate=0)
        hits = _find(items, "FOUNDATION")
        assert hits, "label not recognized"
        t = hits[0]
        assert t.source == "ocr" and 0.0 < t.confidence < 1.0
        # IR frame: y flips; the box's bottom-left sits just under the
        # baseline, so allow a text-height of slack.
        exp = (LABEL_PDF_XY[0], H - LABEL_PDF_XY[1])
        assert math.hypot(t.position[0] - exp[0],
                          t.position[1] - exp[1]) < 15.0
        assert 8.0 < t.height < 25.0

    def test_auto_rotation_reads_sideways_sheet(self, tmp_path):
        # Text drawn rotated 90 deg (the landscape-plot-on-portrait-page
        # reality): auto mode must pick the rotation AND map the box back
        # to where the text actually sits on the page.
        path = _build(tmp_path, rotate_text=90)
        items = ocr_text_items(filepath=path, rotate="auto")
        hits = _find(items, "FOUNDATION")
        assert hits, "sideways label not recognized in auto mode"
        t = hits[0]
        exp = (LABEL_PDF_XY[0], H - LABEL_PDF_XY[1])
        assert math.hypot(t.position[0] - exp[0],
                          t.position[1] - exp[1]) < 25.0

    def test_requires_exactly_one_source(self, tmp_path):
        path = _build(tmp_path)
        with pytest.raises(ValueError):
            ocr_text_items()
        with pytest.raises(ValueError):
            ocr_text_items(filepath=path, content=b"x")


class TestAugmentIr:
    def test_augment_adds_ocr_entities_and_metadata(self, tmp_path):
        path = _build(tmp_path)
        ir = from_pdf_vector(filepath=path)
        n_before = len(ir.entities)
        out = augment_ir_with_ocr(ir, filepath=path, rotate=0)
        assert out["n_added"] >= 1
        assert len(ir.entities) == n_before + out["n_added"]
        ocr_items = [e for e in ir.entities
                     if e.KIND == "text" and e.source == "ocr"]
        assert len(ocr_items) == out["n_added"]
        assert ir.metadata["ocr"]["engine"] == "rapidocr-onnxruntime"

    def test_scaled_ir_gets_scaled_ocr_coordinates(self, tmp_path):
        path = _build(tmp_path)
        scale = 0.05
        ir = from_pdf_vector(filepath=path, scale=scale)
        augment_ir_with_ocr(ir, filepath=path, rotate=0)
        hits = [e for e in ir.entities if e.source == "ocr"
                and "FOUNDATION" in e.content.upper()]
        assert hits
        exp = (LABEL_PDF_XY[0] * scale, (H - LABEL_PDF_XY[1]) * scale)
        t = hits[0]
        assert math.hypot(t.position[0] - exp[0],
                          t.position[1] - exp[1]) < 15.0 * scale


class TestRotationAndClsFlip:
    """The /Rotate + cls-flip contract (2026-09-05).

    Two verified failure mechanisms, one shared symptom (position lands
    one string-length away, silently):

    - a page /Rotate that presents the text 180-degrees to the engine
      (measured ~200 pt off on /Rotate=90/270 fixtures before the fix);
    - a single 180-authored line on an otherwise upright page.

    In both, RapidOCR's angle classifier flips the line crop before
    recognition but returns the DETECTOR's corner order, so the quad's
    corner ROLES are 180-rotated. ocr_text_items now re-runs the
    engine's own classifier per line and rotates the role assignment.
    The assertion baseline is the vector ingest's own text position —
    frame agreement is the whole contract.
    """

    @pytest.mark.parametrize("page_rotate", [0, 90, 180, 270])
    def test_all_page_rotations_land_on_vector_position(self, tmp_path,
                                                        page_rotate):
        doc = fitz.open()
        page = doc.new_page(width=W, height=H)
        author = (360 - page_rotate) % 360
        page.insert_text(fitz.Point(200.0, 300.0), "CALIBRATION TARGET",
                         fontsize=18, rotate=author)
        page.set_rotation(page_rotate)
        p = str(tmp_path / f"pgrot_{page_rotate}.pdf")
        doc.save(p)
        doc.close()

        ir = from_pdf_vector(filepath=p)
        vec = [e for e in ir.entities if e.KIND == "text"][0]
        items = ocr_text_items(filepath=p, dpi=200)
        hits = _find(items, "CALIBRATION")
        assert hits, f"nothing read on /Rotate={page_rotate}"
        err = math.hypot(hits[0].position[0] - vec.position[0],
                         hits[0].position[1] - vec.position[1])
        assert err < 8.0, (
            f"/Rotate={page_rotate}: OCR landed {err:.1f} pt from the "
            f"vector-ingest position (cls-flip / frame regression)")

    def test_single_flipped_line_on_upright_page(self, tmp_path):
        doc = fitz.open()
        page = doc.new_page(width=W, height=H)
        page.insert_text(fitz.Point(100, 100), "NORMAL HEADING",
                         fontsize=16)
        page.insert_text(fitz.Point(150, 400), "UPRIGHT NOTE", fontsize=14)
        page.insert_text(fitz.Point(420, 600), "FLIPPED LABEL",
                         fontsize=14, rotate=180)
        p = str(tmp_path / "mixed_flip.pdf")
        doc.save(p)
        doc.close()

        items = ocr_text_items(filepath=p, dpi=200)
        flip = _find(items, "FLIPPED")
        assert flip, "flipped line not read"
        t = flip[0]
        # truth: inserted at (420, 600) top-left frame -> IR (420, 192).
        err = math.hypot(t.position[0] - 420.0, t.position[1] - 192.0)
        assert err < 8.0, f"flipped line landed {err:.1f} pt off"
        assert abs(abs(t.rotation) - 180.0) < 5.0
        # ... and the upright lines are untouched by the correction.
        up = _find(items, "UPRIGHT")
        assert up and math.hypot(up[0].position[0] - 150.0,
                                 up[0].position[1] - (H - 400.0)) < 8.0

    @pytest.mark.parametrize("author,exp_rot", [(90, 90.0), (270, -90.0)])
    def test_vertical_text_lands_on_insertion(self, tmp_path, author,
                                              exp_rot):
        # Vertical lines ride the engine's internal tall-crop path,
        # whose corner roles shift by +/-1 (reading up vs down) — the
        # reading-DOWN case was silently ~100 pt off before the
        # role-shift table.
        doc = fitz.open()
        page = doc.new_page(width=W, height=H)
        page.insert_text(fitz.Point(300.0, 400.0), "MARKER TEXT",
                         fontsize=14, rotate=author)
        p = str(tmp_path / f"vert_{author}.pdf")
        doc.save(p)
        doc.close()
        items = ocr_text_items(filepath=p, dpi=200, rotate=0)
        hits = _find(items, "MARKER")
        assert hits, f"vertical author={author} not read"
        t = hits[0]
        err = math.hypot(t.position[0] - 300.0, t.position[1] - (H - 400.0))
        assert err < 16.0, f"author={author}: landed {err:.1f} pt off"
        assert abs(t.rotation - exp_rot) < 6.0
