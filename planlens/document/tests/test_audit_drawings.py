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


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_note_at_the_sheet_edge_stays_on_the_sheet(tmp_path, rotation):
    from planlens.document.markup_writer import write_markups
    src = tmp_path / "sheet.pdf"
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    page.insert_text((100, 100), "MADE-UP SHEET A-101", fontsize=20)
    page.set_rotation(rotation)
    w, h = page.rect.width, page.rect.height
    doc.save(str(src))
    out = tmp_path / "marked.pdf"
    write_markups(str(src), str(out), [
        {"kind": "note", "page": 0, "point": [w - 4, h - 4],
         "comment": "edge note"},
        {"kind": "note", "page": 0, "point": [300, 300],
         "comment": "inside note"}])
    marked = pymupdf.open(str(out))
    pg = marked[0]
    shown = {a.info["content"]: a.rect * pg.rotation_matrix
             for a in pg.annots() if a.type[1] == "Text"}
    edge = shown["edge note"]
    assert edge.x1 <= w + 0.5 and edge.y1 <= h + 0.5
    assert edge.x0 >= w - 40 and edge.y0 >= h - 40
    inside = shown["inside note"]
    assert (round(inside.x0), round(inside.y0)) == (300, 300)


def test_a_line_on_a_plan_with_a_scale_is_not_told_there_is_none(tmp_path):
    path = tmp_path / "plan.pdf"
    _detail_sheet(path, 'SCALE: 1/4" = 1\'-0"')
    with open_document(str(path)) as doc:
        listed = measure(doc, 0)
        got = measure(doc, 0, [198, 398, 274, 402], kind="line", pad=1)
    assert listed["scales"]["frames"][0]["scales"]
    assert got["value"] is None
    assert got["scale_known"] is True
    assert "no scale on this page" not in got.get("note", "")
    assert "kind='distance'" in got["note"]
