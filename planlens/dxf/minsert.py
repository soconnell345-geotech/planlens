"""MINSERT expansion shared by :func:`planlens.ir.from_dxf` and
:mod:`planlens.dxf.truth`.

A MINSERT is one INSERT that places its block on a row/column grid. ezdxf's
``virtual_entities()`` resolves only the first grid cell, so both walkers
expand the grid here, one plain INSERT per cell, and then treat each cell as
an ordinary reference.
"""

from __future__ import annotations

from typing import Iterator, Optional


def minsert_cells(insert, parent=None) -> Optional[Iterator]:
    """One plain INSERT per grid cell of the MINSERT ``insert``, lazily.

    The grid steps in the reference's own frame (its OCS), turned by the
    reference's rotation and not scaled by it; each cell carries its own
    copy of the ATTRIBs, moved with it. These are the placements ezdxf's
    ``Insert.multi_insert()`` makes, but a direction whose spacing is 0 is
    walked once rather than looped over (``multi_insert`` loops rows x
    columns and skips the repeats, so a 30000 x 30000 array with one zero
    spacing loops 9e8 times for 30000 cells).

    ``parent`` is ``None`` for a MINSERT in a layout. For one ezdxf yielded
    while exploding the INSERT ``parent``, the grid is built from the block
    definition's own MINSERT (``source_of_copy``), in the parent block's
    frame, and each cell is then moved by the parent's whole transform
    (``parent.matrix44()``). Expanding the virtual copy instead would be
    wrong: ezdxf's ``Insert.transform`` scales and mirrors a reference but
    not its grid spacing, so under a scaled or mirrored parent the grid
    would stay in the unscaled, unmirrored frame.

    Returns ``None`` when a nested MINSERT carries no source to build from;
    the caller then falls back to the first cell.
    """
    if parent is None:
        source, m = insert, None
    else:
        source = getattr(insert, "source_of_copy", None)
        if source is None or source.dxftype() != "INSERT":
            return None
        m = parent.matrix44()
    return _cells(source, m)


def _cells(source, m) -> Iterator:
    from ezdxf.math import Vec3

    dxf = source.dxf
    row_sp, col_sp = dxf.row_spacing, dxf.column_spacing
    rows = int(dxf.row_count) if row_sp else 1
    cols = int(dxf.column_count) if col_sp else 1
    rotation = dxf.rotation
    for row in range(rows):
        for col in range(cols):
            offset = Vec3(col * col_sp, row * row_sp)
            if rotation:
                offset = offset.rotate_deg(rotation)
            cell = source.copy()
            cdxf = cell.dxf
            cdxf.insert = Vec3(cdxf.insert) + offset
            for name in ("row_count", "column_count", "row_spacing",
                         "column_spacing"):
                cdxf.discard(name)
            for attrib in cell.attribs:
                attrib.dxf.insert = Vec3(attrib.dxf.insert) + offset
                if attrib.dxf.hasattr("align_point"):
                    attrib.dxf.align_point = (Vec3(attrib.dxf.align_point)
                                              + offset)
            if m is not None:
                cell.transform(m)
            yield cell
