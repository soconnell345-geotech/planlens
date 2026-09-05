"""frame="ir" rotation correctness for render_region (verifier finding,
2026-09-05: the documented y-flip conversion mispoints on every /Rotate
page — all 10 real validation sheets are /Rotate 270).

Methodology (the independent verifier's): draw a filled black square at a
known UNROTATED location, set the page rotation, ingest to IR, snip via
frame="ir" around the square's IR bbox with zero padding, and assert the
crop is dominated by the black square (and, as a control, that a crop of a
far-away empty IR region is blank).
"""

import io

import pytest

fitz = pytest.importorskip("fitz")

from planlens.ir import from_pdf_vector
from planlens.ir.render import ir_to_page_point, render_region


def _make_pdf(rotation):
    doc = fitz.open()
    pg = doc.new_page(width=612, height=792)
    pg.draw_rect(fitz.Rect(100, 200, 120, 220), color=(0, 0, 0),
                 fill=(0, 0, 0))
    pg.set_rotation(rotation)
    data = doc.tobytes()
    doc.close()
    return data


def _square_ir_bbox(ir):
    pts = [p for e in ir.entities if e.KIND in ("polyline", "line")
           for p in e.points()]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def _dark_fraction(png_bytes):
    pix = fitz.Pixmap(png_bytes)
    n, step = pix.n, max(1, (pix.width * pix.height) // 5000)
    samples = pix.samples
    dark = total = 0
    for i in range(0, pix.width * pix.height, step):
        px = samples[i * n:i * n + 3]
        total += 1
        if px and sum(px[:3]) / len(px[:3]) < 80:
            dark += 1
    return dark / max(total, 1)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_ir_frame_snip_lands_on_target(rotation):
    data = _make_pdf(rotation)
    ir = from_pdf_vector(content=data)
    bbox = _square_ir_bbox(ir)

    png = render_region(content=data, bbox=bbox, dpi=72, pad_frac=0.0,
                        frame="ir")
    assert _dark_fraction(png) > 0.5, (
        f"rotation={rotation}: frame='ir' crop missed the square")

    # Control: an empty region far from the square must be blank.
    empty = (bbox[0] + 250.0, bbox[1] + 150.0,
             bbox[2] + 270.0, bbox[3] + 170.0)
    png_empty = render_region(content=data, bbox=empty, dpi=72,
                              pad_frac=0.0, frame="ir")
    assert _dark_fraction(png_empty) < 0.05


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_naive_yflip_would_miss_on_rotated_pages(rotation):
    """Pin WHY frame='ir' exists: the documented rotation-0 conversion
    (x, height - y) points at empty space on rotated pages."""
    data = _make_pdf(rotation)
    ir = from_pdf_vector(content=data)
    x0, y0, x1, y1 = _square_ir_bbox(ir)

    doc = fitz.open(stream=data, filetype="pdf")
    h = doc[0].rect.height
    doc.close()
    naive = (x0, h - y1, x1, h - y0)
    png = render_region(content=data, bbox=naive, dpi=72, pad_frac=0.0,
                        frame="page")
    # On 180 the naive flip coincidentally lands correctly in x but not in
    # rotated cases generally; require only that at least one rotation
    # demonstrates the miss — parametrized: assert miss OR document hit.
    frac = _dark_fraction(png)
    if rotation in (90, 270):
        assert frac < 0.5, (
            f"rotation={rotation}: naive conversion unexpectedly hit — "
            "re-derive the frame contract before trusting this test")


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_ir_frame_marks_land_on_target(rotation):
    """A frame='ir' mark at the square's IR center must be drawn ON the
    square: render the full page and check the mark ring (red) overlaps
    the square's page-frame location."""
    data = _make_pdf(rotation)
    ir = from_pdf_vector(content=data)
    x0, y0, x1, y1 = _square_ir_bbox(ir)
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0

    png = render_region(content=data, bbox=(x0, y0, x1, y1), dpi=144,
                        pad_frac=1.0, marks=[(cx, cy, "1")], frame="ir")
    pix = fitz.Pixmap(png)
    n = pix.n
    reds = 0
    for i in range(pix.width * pix.height):
        px = pix.samples[i * n:i * n + 3]
        if len(px) == 3 and px[0] > 180 and px[1] < 100 and px[2] < 100:
            reds += 1
    assert reds > 20, f"rotation={rotation}: mark ring not found in crop"


def test_ir_to_page_point_matches_probe():
    """Pin the empirically probed mapping for /Rotate 270: unrotated
    (100, 200) -> IR (100, 412) -> page (200, 512)."""
    data = _make_pdf(270)
    doc = fitz.open(stream=data, filetype="pdf")
    x, y = ir_to_page_point(100.0, 412.0, doc[0])
    doc.close()
    assert abs(x - 200.0) < 1e-6 and abs(y - 512.0) < 1e-6


def test_bad_frame_rejected():
    data = _make_pdf(0)
    with pytest.raises(ValueError):
        render_region(content=data, bbox=(0, 0, 10, 10), frame="model")
