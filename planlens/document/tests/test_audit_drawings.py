"""Drawing-set audit of 2026-11-18: cases an ordinary set meets, on made-up
sheets (made-up words and numbers only)."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.measuring import measure  # noqa: E402
from planlens.document.scale import (  # noqa: E402
    parse_ratio, ratio_magnification)


@pytest.mark.parametrize("text, mag", [
    ('1-1/2" = 1\'-0"', 8.0),
    ('SCALE: 1-1/2"=1\'-0"', 8.0),
    ('1 1/2" = 1\'-0"', 8.0),
    ('3/4" = 1\'-0"', 16.0),
])
def test_hyphenated_mixed_inch_scale_is_read(text, mag):
    assert ratio_magnification(parse_ratio(text)) == pytest.approx(mag)


def test_a_hyphen_elsewhere_is_not_read_as_a_mixed_number():
    assert parse_ratio("A-1/2 = 1") is None


def _detail_sheet(path, note):
    doc = pymupdf.open()
    page = doc.new_page(width=2448, height=1584)
    page.insert_text((150, 300), "DETAIL 1", fontsize=14)
    page.insert_text((150, 320), note, fontsize=10)
    page.draw_line((200, 400), (272, 400))
    doc.save(str(path))


def test_a_detail_at_one_and_a_half_inch_scale_measures(tmp_path):
    path = tmp_path / "detail.pdf"
    _detail_sheet(path, 'SCALE: 1-1/2" = 1\'-0"')
    with open_document(str(path)) as doc:
        got = measure(doc, 0, [198, 398, 202, 402], kind="distance",
                      to=[270, 398, 274, 402])
    # 72 pt is one inch of paper: at 1-1/2" = 1'-0" that is 8 in.
    assert got["value"]["unit"] == "ft"
    assert got["value"]["distance"] == pytest.approx(8.0 / 12.0, abs=0.02)
