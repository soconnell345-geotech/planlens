"""BACKLOG DR-19, review of DR-44: a small drawing is scaled UP to fit its
plot, as a large one is scaled down. ezdxf plots one drawing unit to the
millimetre, so a detail drawn in metres (two units wide) was a 2 mm speck
in the middle of a page of margin, and the plot's "scaled to fit" was
untrue.

Every DXF here is made up and drawn by the test with ezdxf.
"""

import pytest

ezdxf = pytest.importorskip("ezdxf")
pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("PIL", reason="plotting a DXF needs Pillow")

from planlens.dxf.plot import (  # noqa: E402
    MARGIN_MM, MIN_SIDE_MM, dxf_to_pdf)

MM = 72.0 / 25.4


def _plot(tmp_path, width, height):
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)],
                       close=True)
    msp.add_line((0, 0), (width, height))
    path = tmp_path / "detail.dxf"
    doc.saveas(str(path))
    page = pymupdf.open(stream=dxf_to_pdf(filepath=str(path)),
                        filetype="pdf")[0]
    full = page.rect
    rects = [d["rect"] for d in page.get_drawings()
             if not (d["rect"].width >= full.width - 1
                     and d["rect"].height >= full.height - 1)]
    drawn = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        drawn |= r
    return full, drawn


@pytest.mark.parametrize("width,height", [(2.0, 1.0), (0.5, 0.2),
                                          (1.0, 3.0)])
def test_a_small_drawing_fills_its_page(tmp_path, width, height):
    full, drawn = _plot(tmp_path, width, height)
    longer = max(full.width, full.height)
    assert abs(longer - MIN_SIDE_MM * MM) <= 2.0
    # the drawing, not the margin, is most of the page
    assert max(drawn.width, drawn.height) >= 0.9 * (
        longer - 2 * MARGIN_MM * MM)
    # and it keeps its shape
    assert abs(drawn.width / drawn.height - width / height) <= 0.1 * (
        width / height)


def test_a_drawing_already_that_large_is_plotted_as_before(tmp_path):
    full, drawn = _plot(tmp_path, 400.0, 300.0)
    assert abs(full.width - (400.0 + 2 * MARGIN_MM) * MM) <= 2.0
    assert abs(drawn.width - 400.0 * MM) <= 2.0
