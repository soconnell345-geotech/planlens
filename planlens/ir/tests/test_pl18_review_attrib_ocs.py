"""PL-18 review: a MINSERT's ATTRIB whose extrusion differs from the
reference's follows each cell.

The grid step is in the reference's OCS; an ATTRIB's points are in the
ATTRIB's own. The expected positions are worked out here by hand (an
extrusion of (0, 0, -1) maps an OCS point (x, y) to the WCS point (-x, y)).
"""

import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.ir import from_dxf  # noqa: E402
from planlens.ir.results import TextItem  # noqa: E402


def _dxf(path, *, ref_neg, attrib_neg, rotation=0.0):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.blocks.new("CELL").add_line((0, 0), (1, 0))
    attribs = {"rotation": rotation}
    if ref_neg:
        attribs["extrusion"] = (0, 0, -1)
    m = doc.modelspace().add_blockref("CELL", (2, 3), dxfattribs=attribs)
    m.add_attrib("ID", "X-1", (2.5, 3.5), dxfattribs=(
        {"extrusion": (0, 0, -1)} if attrib_neg else {}))
    m.dxf.row_count, m.dxf.column_count = 2, 3
    m.dxf.row_spacing, m.dxf.column_spacing = 4.0, 6.0
    doc.saveas(str(path))
    return str(path)


def _want(ref_neg, attrib_neg, rotation):
    a = math.radians(rotation)
    out = []
    for row in range(2):
        for col in range(3):
            # the step in the reference's OCS, then in the WCS
            sx = 6.0 * col * math.cos(a) - 4.0 * row * math.sin(a)
            sy = 6.0 * col * math.sin(a) + 4.0 * row * math.cos(a)
            if ref_neg:
                sx = -sx
            ax = -2.5 if attrib_neg else 2.5
            out.append((round(ax + sx, 4) + 0.0, round(3.5 + sy, 4) + 0.0))
    return sorted(out)


@pytest.mark.parametrize("ref_neg, attrib_neg, rotation", [
    (True, False, 0.0),
    (True, False, 30.0),
    (False, True, 30.0),
    (True, True, 30.0),      # same extrusion: as before
    (False, False, 30.0),    # same extrusion: as before
])
def test_attrib_follows_each_cell(tmp_path, ref_neg, attrib_neg, rotation):
    ir = from_dxf(_dxf(tmp_path / "a.dxf", ref_neg=ref_neg,
                       attrib_neg=attrib_neg, rotation=rotation))
    got = sorted((round(t.position[0], 4) + 0.0,
                  round(t.position[1], 4) + 0.0)
                 for t in ir.entities
                 if isinstance(t, TextItem) and t.content == "X-1")
    assert got == _want(ref_neg, attrib_neg, rotation)
    assert ir.warnings == []
