"""PL-18 review: the grid of a MINSERT, checked by hand in both readers.

``planlens.dxf.truth`` and ``planlens.ir.from_dxf`` expand a MINSERT with the
same module, so their agreeing no longer shows the cells are in the right
place. These cases pin what no earlier test did: the grid is NOT scaled or
mirrored by the reference's own scale, columns step along the reference's
x and rows along its y (a 2 x 3 grid, so swapping them shows), and under an
extrusion of (0, 0, -1) the grid steps in the OCS. Every expected point is
plain trigonometry written here.
"""

import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.dxf.truth import extract_native_annotations  # noqa: E402
from planlens.ir import from_dxf  # noqa: E402
from planlens.ir.results import Line  # noqa: E402

_Q = ((0.0, 0.0), (1.0, 0.5))      # the LINE and the LEADER in CELL
_AT = (2.0, 3.0)
_ROWS, _COLS, _ROW_SP, _COL_SP = 2, 3, 4.0, 6.0


def _turn(p, deg):
    a = math.radians(deg)
    return (p[0] * math.cos(a) - p[1] * math.sin(a),
            p[0] * math.sin(a) + p[1] * math.cos(a))


def _want(sx, sy, rot, neg):
    out = []
    for row in range(_ROWS):
        for col in range(_COLS):
            step = _turn((_COL_SP * col, _ROW_SP * row), rot)   # unscaled
            ends = []
            for q in _Q:
                x, y = _turn((sx * q[0], sy * q[1]), rot)
                x, y = _AT[0] + step[0] + x, _AT[1] + step[1] + y
                if neg:
                    x = -x          # OCS of (0, 0, -1) -> WCS
                ends.append((round(x, 4) + 0.0, round(y, 4) + 0.0))
            out.append(tuple(sorted(ends)))
    return sorted(out)


def _dxf(path, sx, sy, rot, neg):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    cell = doc.blocks.new("CELL")
    cell.add_line(*_Q)
    cell.add_leader(vertices=list(_Q))
    attribs = {"xscale": sx, "yscale": sy, "rotation": rot}
    if neg:
        attribs["extrusion"] = (0, 0, -1)
    m = doc.modelspace().add_blockref("CELL", _AT, dxfattribs=attribs)
    m.dxf.row_count, m.dxf.column_count = _ROWS, _COLS
    m.dxf.row_spacing, m.dxf.column_spacing = _ROW_SP, _COL_SP
    doc.saveas(str(path))
    return str(path)


_CASES = [
    (1.0, 1.0, 0.0, False),     # plain
    (3.0, 0.5, 0.0, False),     # scaled unevenly: the grid keeps its spacing
    (3.0, 0.5, 30.0, False),
    (-1.0, 1.0, 20.0, False),   # mirrored in x: the grid is not mirrored
    (1.0, -1.0, 20.0, False),   # mirrored in y
    (-2.0, -2.0, 20.0, False),  # both
    (1.0, 1.0, 0.0, True),      # extrusion (0, 0, -1)
    (2.0, 2.0, 30.0, True),
]


@pytest.mark.parametrize("sx, sy, rot, neg", _CASES)
def test_truth_records_every_cell_where_it_is_drawn(tmp_path, sx, sy, rot,
                                                    neg):
    t = extract_native_annotations(_dxf(tmp_path / "g.dxf", sx, sy, rot,
                                        neg))
    got = sorted(tuple(sorted(tuple(v) for v in ld["vertices"]))
                 for ld in t["spaces"]["model"]["leaders"])
    assert got == _want(sx, sy, rot, neg)
    assert "warnings" not in t


@pytest.mark.parametrize("sx, sy, rot, neg", _CASES)
def test_ingest_places_every_cell_where_it_is_drawn(tmp_path, sx, sy, rot,
                                                    neg):
    ir = from_dxf(_dxf(tmp_path / "g.dxf", sx, sy, rot, neg))
    got = sorted(
        tuple(sorted(((round(ln.start[0], 4) + 0.0,
                       round(ln.start[1], 4) + 0.0),
                      (round(ln.end[0], 4) + 0.0,
                       round(ln.end[1], 4) + 0.0))))
        for ln in ir.entities if isinstance(ln, Line))
    assert got == _want(sx, sy, rot, neg)
    assert ir.warnings == []
