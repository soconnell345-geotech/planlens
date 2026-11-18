"""BACKLOG DR-19: a DXF's model space as a one-page PDF plot.

Every DXF here is made up and drawn by the test with ezdxf.
"""

import importlib.util

import pytest

ezdxf = pytest.importorskip("ezdxf")
pymupdf = pytest.importorskip("pymupdf")

#: ezdxf's drawing add-on imports Pillow, which planlens does not require:
#: the tests that make a plot are skipped where it is not installed
#: (``test_dr19_plot_bounds.py`` covers what is said then).
needs_pillow = pytest.mark.skipif(
    importlib.util.find_spec("PIL") is None,
    reason="plotting a DXF needs Pillow (ezdxf's drawing add-on)")

from planlens.dxf.plot import (  # noqa: E402
    MARGIN_MM, MAX_SIDE_MM, dxf_to_pdf, is_dxf)

MM = 72.0 / 25.4


def _dxf(path, width=40.0, height=20.0, text=True):
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (width, 0), (width, height), (0, height)],
                       close=True)
    msp.add_line((0, height / 2), (width, height / 2))
    msp.add_circle((width / 4, height / 4), height / 10)
    if text:
        msp.add_text("WALL A", height=height / 12).set_placement(
            (width / 20, height * 0.7))
    doc.saveas(str(path))
    return str(path)


def _union(page):
    """The box of what is drawn, without the white background (a filled
    rectangle the size of the page)."""
    full = page.rect
    rects = [d["rect"] for d in page.get_drawings()
             if not (d["rect"].width >= full.width - 1
                     and d["rect"].height >= full.height - 1)]
    out = pymupdf.Rect(rects[0])
    for r in rects[1:]:
        out |= r
    return out


@needs_pillow
def test_the_plot_is_one_page_fitted_inside_its_margin(tmp_path):
    pdf = dxf_to_pdf(filepath=_dxf(tmp_path / "plan.dxf"))
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        assert doc.page_count == 1
        page = doc[0]
        assert page.rect.width > page.rect.height     # the drawing's shape
        drawn = _union(page)
        margin = MARGIN_MM * MM
        # the drawing fills the page inside the margin
        assert abs(drawn.x0 - margin) < 2 and abs(drawn.y0 - margin) < 2
        assert abs(page.rect.width - drawn.x1 - margin) < 2
        assert abs(page.rect.height - drawn.y1 - margin) < 2
        # lettering is drawn as shapes: the plot has no text layer
        assert page.get_text().strip() == ""
        # black on white
        pix = page.get_pixmap(dpi=150)
        assert pix.pixel(1, 1)[:3] == (255, 255, 255)
        assert min(pix.samples) < 128


@needs_pillow
def test_bytes_plot_as_the_file_does(tmp_path):
    path = _dxf(tmp_path / "plan.dxf")
    with open(path, "rb") as fh:
        data = fh.read()
    a = pymupdf.open(stream=dxf_to_pdf(filepath=path), filetype="pdf")
    b = pymupdf.open(stream=dxf_to_pdf(content=data), filetype="pdf")
    assert a[0].rect == b[0].rect
    assert len(a[0].get_drawings()) == len(b[0].get_drawings())


@needs_pillow
def test_a_large_drawing_is_scaled_to_fit_the_largest_page(tmp_path):
    pdf = dxf_to_pdf(filepath=_dxf(tmp_path / "site.dxf", width=200000.0,
                                   height=80000.0, text=False))
    with pymupdf.open(stream=pdf, filetype="pdf") as doc:
        page = doc[0]
        assert page.rect.width <= MAX_SIDE_MM * MM + 1
        assert page.rect.height <= MAX_SIDE_MM * MM + 1
        assert page.get_drawings()


@needs_pillow
def test_nothing_to_plot_and_not_a_dxf(tmp_path):
    empty = ezdxf.new()
    empty.saveas(str(tmp_path / "empty.dxf"))
    with pytest.raises(ValueError, match="model space is empty"):
        dxf_to_pdf(filepath=str(tmp_path / "empty.dxf"))
    with pytest.raises(Exception):
        dxf_to_pdf(content=b"%PDF-1.7 this is no drawing")
    with pytest.raises(ValueError):
        dxf_to_pdf()
    assert is_dxf("Plan.DXF") and is_dxf("a/b/site.dxf ")
    assert not is_dxf("plan.pdf") and not is_dxf(None)
