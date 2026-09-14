"""The displayed-page frame — the single coordinate contract of planlens.document.

Every coordinate this package emits is in **PDF points in the page's DISPLAYED
orientation**: the page as a PDF viewer shows it and as ``page.get_pixmap()``
renders it, with ``/Rotate`` already applied. Origin top-left, x to the right,
y downward. It is the frame of :func:`planlens.ir.render.render_region` with
``frame="page"``, so a bbox from this package can be rendered without any
conversion, and it is the frame a vision model sees in a rendered page image.

PyMuPDF reports text spans, words and annotation rects/vertices in the
UNROTATED page space. Verified 2026-09-13 by building one page at /Rotate 0, 90
and 270: the extracted numbers were identical on all three while ``page.rect``
swapped width and height. ``page.rotation_matrix`` maps unrotated -> displayed,
and the helpers here apply it — to points, to boxes, and to reading directions.

A :class:`planlens.ir.results.DrawingIR` uses a different frame (unrotated
geometry y-flipped with the rotated page height). :func:`to_ir_point` and
:func:`from_ir_point` convert between the two so text found here can be joined
to geometry found there.
"""

from __future__ import annotations

import math
from typing import Optional, Sequence, Tuple

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]


def _apply(m, x: float, y: float) -> Point:
    return (m.a * x + m.c * y + m.e, m.b * x + m.d * y + m.f)


def to_display_point(page, x: float, y: float) -> Point:
    """Unrotated PyMuPDF page coordinates -> displayed frame."""
    return _apply(page.rotation_matrix, float(x), float(y))


def to_display_bbox(page, bbox: Sequence[float]) -> BBox:
    """Axis-aligned unrotated box -> axis-aligned displayed box.

    /Rotate is always a multiple of 90 degrees, so the transformed box is still
    axis-aligned and no area is gained by taking the min/max of its corners.
    """
    x0, y0, x1, y1 = (float(v) for v in bbox)
    m = page.rotation_matrix
    pts = [_apply(m, x0, y0), _apply(m, x1, y0), _apply(m, x0, y1),
           _apply(m, x1, y1)]
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return (min(xs), min(ys), max(xs), max(ys))


def direction_to_rotation(page, direction: Sequence[float]) -> float:
    """A text reading direction (unrotated, y-down) -> degrees CCW as displayed.

    0 is ordinary left-to-right text, 90 reads bottom-to-top, 270 reads
    top-to-bottom. PyMuPDF's line ``dir`` is a unit vector in y-down unrotated
    space; only the linear part of the rotation matrix applies to a direction.
    """
    dx, dy = float(direction[0]), float(direction[1])
    m = page.rotation_matrix
    ddx = m.a * dx + m.c * dy
    ddy = m.b * dx + m.d * dy
    # y grows downward, so a CCW angle as seen uses -dy.
    ang = math.degrees(math.atan2(-ddy, ddx)) % 360.0
    ang = round(ang, 1)
    return 0.0 if ang >= 359.95 else ang


def to_ir_point(page, x: float, y: float) -> Point:
    """Displayed frame -> a bottom-left :class:`DrawingIR` of the same page.

    The exact inverse of :func:`planlens.ir.render.ir_to_page_point`: derotate
    to unrotated page space, then y-flip with the ROTATED page height (the
    convention ``from_pdf_vector`` uses).
    """
    xu, yu = _apply(page.derotation_matrix, float(x), float(y))
    return (xu, page.rect.height - yu)


def from_ir_point(page, x_ir: float, y_ir: float) -> Point:
    """A bottom-left :class:`DrawingIR` point -> displayed frame."""
    return _apply(page.rotation_matrix, float(x_ir),
                  page.rect.height - float(y_ir))


def bbox_union(boxes: Sequence[Optional[Sequence[float]]]) -> Optional[BBox]:
    boxes = [b for b in boxes if b is not None]
    if not boxes:
        return None
    return (min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes))


def bbox_iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    if inter <= 0.0:
        return 0.0
    area_a = max(0.0, a[2] - a[0]) * max(0.0, a[3] - a[1])
    area_b = max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0
