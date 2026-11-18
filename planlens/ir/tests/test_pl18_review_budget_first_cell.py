"""PL-18 review: a MINSERT met after the budget is spent is still read as
the single reference it was before (its first cell), and the warning says
how many of its copies arrived.

Before the expansion existed, such an array's first cell was read like any
INSERT: its ATTRIBs arrived whatever the budget, and its geometry while the
budget lasted. With the budget spent the expansion took no cell at all, so
the array's ATTRIB was lost where a plain INSERT's beside it was kept.
"""

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.dxf.truth import extract_native_annotations  # noqa: E402
from planlens.ir import from_dxf  # noqa: E402
from planlens.ir.results import Leader, Line, TextItem  # noqa: E402


def _array(space, name, at, rows, cols):
    ins = space.add_blockref(name, at)
    ins.dxf.row_count, ins.dxf.column_count = rows, cols
    ins.dxf.row_spacing = ins.dxf.column_spacing = 5.0
    return ins


def test_the_first_cells_attrib_survives_a_spent_entity_budget(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    big = doc.blocks.new("BIG")
    for i in range(30):
        big.add_line((i, 0), (i, 1))
    doc.blocks.new("TAG").add_line((0, 0), (1, 0))
    msp = doc.modelspace()
    msp.add_blockref("BIG", (0, 0))
    _array(msp, "TAG", (50, 0), 1, 3).add_attrib("ID", "IN-ARRAY", (50, 1))
    msp.add_blockref("TAG", (80, 0)).add_attrib("ID", "PLAIN", (80, 1))
    path = str(tmp_path / "spent.dxf")
    doc.saveas(path)

    ir = from_dxf(path, max_block_entities=20)
    texts = [(t.content, tuple(t.position)) for t in ir.entities
             if isinstance(t, TextItem)]
    assert texts == [("IN-ARRAY", (50.0, 1.0)), ("PLAIN", (80.0, 1.0))]
    assert sum(isinstance(e, Line) for e in ir.entities) == 20
    assert any("MINSERT 'TAG' places 3 copies" in w
               and "after 0 of them" in w for w in ir.warnings)


def test_the_warning_counts_the_copies_that_arrived(tmp_path):
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.blocks.new("TAG").add_line((0, 0), (1, 0))
    _array(doc.modelspace(), "TAG", (0, 0), 10, 10)
    path = str(tmp_path / "mid.dxf")
    doc.saveas(path)
    ir = from_dxf(path, max_block_entities=30)
    assert sum(isinstance(e, Line) for e in ir.entities) == 30
    assert any("stopped at the 30-entity budget after 30 of them" in w
               for w in ir.warnings)


def test_truth_and_ingest_agree_once_the_cell_budget_is_spent(tmp_path):
    # An array of an empty block spends the cell budget and walks no block
    # entity; the array of NOTE after it is then read as its first cell by
    # both readers.
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.blocks.new("EMPTY")
    doc.blocks.new("NOTE").add_leader(vertices=[(1, 1), (3, 3)])
    msp = doc.modelspace()
    _array(msp, "EMPTY", (0, 0), 10, 10)
    _array(msp, "NOTE", (100, 100), 1, 2)
    path = str(tmp_path / "cells.dxf")
    doc.saveas(path)
    ir = from_dxf(path, max_block_entities=20)
    t = extract_native_annotations(path, max_block_entities=20)
    ir_tips = [(round(ld.vertices[0][0], 4), round(ld.vertices[0][1], 4))
               for ld in ir.entities if isinstance(ld, Leader)]
    truth_tips = [tuple(ld["vertices"][0])
                  for ld in t["spaces"]["model"]["leaders"]]
    assert ir_tips == truth_tips == [(101.0, 101.0)]
    for warnings in (ir.warnings, t["warnings"]):
        assert any("MINSERT 'NOTE' places 2 copies" in w
                   and "after 0 of them" in w for w in warnings)
