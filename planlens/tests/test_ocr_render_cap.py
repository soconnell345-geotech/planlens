"""The OCR render cap — a D-size sheet must not become a 70 MP OCR job.

Live 2026-09-10: `digitize_drawing(ocr_text=True)` on a 33.1 x 23.4 in sheet
rendered 9,933 x 7,017 px (69.7 MP, a 0.21 GB pixmap) at the 300 dpi default
and took the notebook driver's websocket down with it. These tests pin the
cap and, more importantly, the coordinate contract that goes with it: when the
render steps down in dpi, pixel->point conversion must follow, or every
recognized box lands in the wrong place.
"""

import math

import pytest

from planlens.ocr import DEFAULT_MAX_MEGAPIXELS, _effective_dpi

# 33.1 x 23.4 in at 72 pt/in — the sheet from the live failure
D_SHEET = (2384.0, 1684.0)
A4 = (595.0, 842.0)


class TestEffectiveDpi:
    def test_small_page_is_untouched(self):
        assert _effective_dpi(*A4, 300.0, DEFAULT_MAX_MEGAPIXELS) == 300.0

    def test_d_size_sheet_is_capped(self):
        dpi = _effective_dpi(*D_SHEET, 300.0, DEFAULT_MAX_MEGAPIXELS)
        assert dpi < 300.0

    def test_capped_render_lands_on_the_cap(self):
        """The step-down must hit the ceiling, not overshoot or undershoot."""
        dpi = _effective_dpi(*D_SHEET, 300.0, DEFAULT_MAX_MEGAPIXELS)
        px = (D_SHEET[0] * dpi / 72.0) * (D_SHEET[1] * dpi / 72.0)
        assert px == pytest.approx(DEFAULT_MAX_MEGAPIXELS * 1e6, rel=1e-9)

    def test_the_live_failure_case_is_bounded(self):
        """69.7 MP uncapped -> at or under the cap."""
        uncapped = (D_SHEET[0] * 300 / 72.0) * (D_SHEET[1] * 300 / 72.0)
        assert uncapped / 1e6 > 65        # the sheet that broke it
        dpi = _effective_dpi(*D_SHEET, 300.0, DEFAULT_MAX_MEGAPIXELS)
        capped = (D_SHEET[0] * dpi / 72.0) * (D_SHEET[1] * dpi / 72.0)
        assert capped / 1e6 <= DEFAULT_MAX_MEGAPIXELS + 1e-6

    @pytest.mark.parametrize("cap", [None, 0, -1])
    def test_cap_can_be_lifted(self, cap):
        assert _effective_dpi(*D_SHEET, 300.0, cap) == 300.0

    def test_scaling_is_by_area_not_by_edge(self):
        """Halving the pixel budget scales dpi by sqrt(2), not by 2."""
        full = _effective_dpi(*D_SHEET, 300.0, 40.0)
        half = _effective_dpi(*D_SHEET, 300.0, 20.0)
        assert full / half == pytest.approx(math.sqrt(2.0), rel=1e-9)

    def test_requesting_a_low_dpi_is_never_raised(self):
        """The cap only ever steps down."""
        assert _effective_dpi(*D_SHEET, 50.0, DEFAULT_MAX_MEGAPIXELS) == 50.0


class TestRenderContract:
    def test_render_returns_the_effective_dpi(self):
        """The 5th element is the dpi used — the caller scales by it."""
        fitz = pytest.importorskip("fitz")
        from planlens.ocr import _render_page_png
        doc = fitz.open()
        doc.new_page(width=D_SHEET[0], height=D_SHEET[1])
        blob = doc.tobytes()
        doc.close()

        png, w_pt, h_pt, derot, dpi_eff = _render_page_png(
            None, blob, 0, 300.0, DEFAULT_MAX_MEGAPIXELS)
        assert (w_pt, h_pt) == pytest.approx(D_SHEET)
        assert dpi_eff < 300.0
        assert len(derot) == 6

    def test_rendered_pixels_match_the_effective_dpi(self):
        """The image really is smaller — not just the reported number."""
        fitz = pytest.importorskip("fitz")
        cv2 = pytest.importorskip("cv2")
        import numpy as np
        from planlens.ocr import _render_page_png
        doc = fitz.open()
        doc.new_page(width=D_SHEET[0], height=D_SHEET[1])
        blob = doc.tobytes()
        doc.close()

        png, w_pt, h_pt, _derot, dpi_eff = _render_page_png(
            None, blob, 0, 300.0, DEFAULT_MAX_MEGAPIXELS)
        img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        h_px, w_px = img.shape[:2]
        assert w_px == pytest.approx(w_pt * dpi_eff / 72.0, abs=2)
        assert h_px == pytest.approx(h_pt * dpi_eff / 72.0, abs=2)
        assert (w_px * h_px) / 1e6 <= DEFAULT_MAX_MEGAPIXELS + 0.1
