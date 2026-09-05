"""Programmatic fixtures for drawing_ir tests.

Every fixture builds its source in-memory (ezdxf / PyMuPDF / OpenCV+numpy) — no
external files. Individual test modules ``pytest.importorskip`` the backend they
need, so a missing optional dependency skips only that module's tests.
"""

import importlib.util

import pytest


def _have(mod):
    return importlib.util.find_spec(mod) is not None


# ---------------------------------------------------------------------------
# DXF fixtures (ezdxf)
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_dxf(tmp_path):
    """A metric section: surface polyline, GWT line, a circle, an arc, text.

    $INSUNITS = 6 (meters). Known coordinates used by the ingest tests:
      SURFACE poly:  (0,10) (10,10) (20,5) (30,5)
      GWT line:      (5,8) -> (25,3)
      CIRCLE:        center (15,7) r 2
      ARC:           center (10,4) r 3, 0..90 deg
      TEXT "Clay":   insert (12,6), height 1
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 10), (10, 10), (20, 5), (30, 5)],
                       dxfattribs={"layer": "SURFACE"})
    msp.add_line((5, 8), (25, 3), dxfattribs={"layer": "GWT"})
    msp.add_circle((15, 7), 2.0, dxfattribs={"layer": "DETAIL"})
    msp.add_arc((10, 4), 3.0, 0, 90, dxfattribs={"layer": "DETAIL"})
    msp.add_text("Clay", dxfattribs={"layer": "NOTES", "insert": (12, 6),
                                     "height": 1.0})
    path = tmp_path / "simple.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def imperial_dxf(tmp_path):
    """A section drawn in feet ($INSUNITS = 2). Surface (0,30)->(100,15)."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 2  # feet
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 30), (30, 30), (60, 15), (100, 15)],
                       dxfattribs={"layer": "GROUND"})
    path = tmp_path / "imperial.dxf"
    doc.saveas(str(path))
    return str(path)


# ---------------------------------------------------------------------------
# PDF fixtures (PyMuPDF)
# ---------------------------------------------------------------------------

@pytest.fixture
def cross_section_pdf(tmp_path):
    """A 400x300 pt page: a 3-segment black surface polyline + a scale note.

    Surface drawn top-left origin: (50,100)->(150,100)->(300,200).
    Text "SCALE 1:100" so the scale module proposes a candidate.
    """
    import fitz
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    sh = page.new_shape()
    pts = [(50, 100), (150, 100), (300, 200), (350, 200)]
    for a, b in zip(pts, pts[1:]):
        sh.draw_line(fitz.Point(*a), fitz.Point(*b))
    sh.finish(color=(0, 0, 0), width=2)
    sh.commit()
    page.insert_text(fitz.Point(160, 250), "SCALE 1:100", fontsize=10)
    path = tmp_path / "section.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


# ---------------------------------------------------------------------------
# Raster fixtures (OpenCV + numpy)
# ---------------------------------------------------------------------------

@pytest.fixture
def synthetic_raster_array():
    """A 300x400 white image with two thick black lines forming an L + a circle.

    Horizontal line y=250 from x=50..350; vertical line x=50 from y=50..250;
    circle center (200,150) r 40. Returns the BGR numpy array.
    """
    import numpy as np
    import cv2
    img = np.full((300, 400, 3), 255, np.uint8)
    cv2.line(img, (50, 250), (350, 250), (0, 0, 0), 3)
    cv2.line(img, (50, 250), (50, 50), (0, 0, 0), 3)
    cv2.circle(img, (200, 150), 40, (0, 0, 0), 3)
    return img


@pytest.fixture
def synthetic_raster_png(tmp_path, synthetic_raster_array):
    """The synthetic raster written to a PNG; returns the path."""
    import cv2
    path = tmp_path / "scan.png"
    cv2.imwrite(str(path), synthetic_raster_array)
    return str(path)


@pytest.fixture
def blocks_dxf(tmp_path):
    """Nested block references ($INSUNITS = 6, meters).

    Block PART: line (0,0)->(1,0) + circle center (0.5,0.5) r 0.25.
    Block ASM:  INSERT of PART at (2,0) (no scale/rotation).
    Model space:
      INSERT PART at (10,5), rotation 90 deg, uniform scale 2
      INSERT ASM  at (100,100)
      INSERT TBLOCK at (50,50) with ATTRIB SHEET_NO = "S-1"
      LINE (0,0)->(5,0) drawn directly (non-block control)
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    part = doc.blocks.new(name="PART")
    part.add_line((0, 0), (1, 0), dxfattribs={"layer": "P"})
    part.add_circle((0.5, 0.5), 0.25, dxfattribs={"layer": "P"})
    asm = doc.blocks.new(name="ASM")
    asm.add_blockref("PART", (2, 0))
    tblock = doc.blocks.new(name="TBLOCK")
    tblock.add_line((0, 0), (3, 0), dxfattribs={"layer": "TB"})
    tblock.add_attdef("SHEET_NO", (0.5, 0.5), dxfattribs={"height": 0.2})
    msp = doc.modelspace()
    msp.add_blockref("PART", (10, 5), dxfattribs={
        "rotation": 90.0, "xscale": 2.0, "yscale": 2.0})
    msp.add_blockref("ASM", (100, 100))
    ref = msp.add_blockref("TBLOCK", (50, 50))
    ref.add_attrib("SHEET_NO", "S-1", (50.5, 50.5))
    msp.add_line((0, 0), (5, 0), dxfattribs={"layer": "DIRECT"})
    path = tmp_path / "blocks.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def deep_blocks_dxf(tmp_path):
    """A 12-deep chain of nested block references (depth-cap fixture)."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    b0 = doc.blocks.new(name="B0")
    b0.add_line((0, 0), (1, 0))
    for i in range(1, 12):
        b = doc.blocks.new(name=f"B{i}")
        b.add_blockref(f"B{i-1}", (0, 0))
    doc.modelspace().add_blockref("B11", (0, 0))
    path = tmp_path / "deep_blocks.dxf"
    doc.saveas(str(path))
    return str(path)
