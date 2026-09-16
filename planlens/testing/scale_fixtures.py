"""A synthetic sheet carrying the measurement calibration a PDF can store.

WHY IT IS SYNTHETIC. The real corpus this feature was measured on holds no
calibrated page: 162 PDFs, one georeferenced viewport, one rectilinear viewport
that is the untouched 1:1 default, and not a single measurement markup. So the
CALIBRATED path is exercised here — built to ISO 32000-1 §12.9 and to the shapes
the real files do use (an indirect ``/Measure``, a ``/BBox`` in unrotated user
space) rather than to a convenient invention.

The sheet carries:

- a ``/VP`` array with one rectilinear Viewport, ``/R (1 in = 20 ft)`` and
  ``/X [<</C 0.2777777778 /U (ft) /D 100>>]`` — 20 ft per inch is 20/72 ft per
  point, so ``/C`` is what one PDF point covers, the meaning verified on a real
  file;
- a PolyLine measurement markup with ``/IT /PolyLineDimension``, its own
  ``/Measure``, three vertices spanning a known distance, and a ``/Contents``
  stating the value the tool displayed.

:func:`build_synthetic_scaled_sheet_pdf` takes a ``rotation`` so a test can
demand the two things a rotation must not break: the viewport box must land in
the DISPLAYED frame, and the derived length must not change at all.

Truth is computed from the inputs by hand, never through the code under test.
For ``/Rotate 90`` on an unrotated page of height ``H``, a displayed point
``(xd, yd)`` is the unrotated point ``(yd, H - xd)`` — the same relation
:mod:`planlens.testing.document_fixtures` uses.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Tuple

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]

#: Unrotated mediabox of the sheet (portrait 24 x 36 in), displayed landscape
#: at /Rotate 90.
SHEET_UNROT = (1728.0, 2592.0)

#: The stored scale: 20 feet of ground per inch of paper.
FEET_PER_INCH = 20.0
#: ``/X /C``: real-world units per PDF point. 20 ft per inch / 72 pt per inch.
FEET_PER_POINT = FEET_PER_INCH / 72.0
RATIO_TEXT = "1 in = 20 ft"


@dataclass
class ScaledSheetGT:
    """Ground truth for :func:`build_synthetic_scaled_sheet_pdf`."""
    pdf: bytes
    rotation: int = 0
    #: Displayed page size at this rotation.
    page_size: Tuple[float, float] = (0.0, 0.0)
    ratio: str = RATIO_TEXT
    x_unit: str = "ft"
    x_per_point: float = FEET_PER_POINT
    distance_unit: str = "ft"
    area_unit: str = "sq ft"
    viewport_name: str = "Plan"
    #: The viewport /BBox as written (UNROTATED user space). Inset by a plot
    #: margin, covering 0.866 of the page — the shape a real whole-page
    #: viewport was measured to have (0.845).
    viewport_bbox_unrotated: BBox = (72.0, 72.0, 1656.0, 2520.0)
    #: The same box in the DISPLAYED frame, derived by hand.
    viewport_bbox_displayed: BBox = (0.0, 0.0, 0.0, 0.0)
    #: A displayed point inside the viewport, and one outside it.
    point_inside: Point = (0.0, 0.0)
    point_outside: Point = (10.0, 10.0)
    #: The dimension markup, in the DISPLAYED frame.
    markup_vertices: Tuple[Point, ...] = ()
    markup_intent: str = "PolyLineDimension"
    markup_author: str = "Surveyor C"
    #: Path length in page points, and the same length through the scale.
    length_pt: float = 0.0
    length_ft: float = 0.0
    #: What the markup's /Contents states (the same value, as a tool writes it).
    stated_text: str = ""
    stated_ft: float = 0.0
    extra: Dict[str, object] = field(default_factory=dict)


def _measure_dict(ratio: str = RATIO_TEXT, c: float = FEET_PER_POINT,
                  unit: str = "ft", area_unit: str = "sq ft") -> str:
    """A rectilinear /Measure in raw PDF syntax, as a producer writes one."""
    return (f"<< /Type /Measure /Subtype /RL /R ({ratio}) "
            f"/X [ << /Type /NumberFormat /U ({unit}) /C {c:.10g} /D 100 >> ] "
            f"/Y [ << /Type /NumberFormat /U ({unit}) /C {c:.10g} /D 100 >> ] "
            f"/D [ << /Type /NumberFormat /U ({unit}) /C 1 /D 100 >> ] "
            f"/A [ << /Type /NumberFormat /U ({area_unit}) /C 1 /D 100 >> ] >>")


def build_synthetic_scaled_sheet_pdf(rotation: int = 0) -> ScaledSheetGT:
    """A one-page drawing sheet with a stored scale and a dimension markup.

    ``rotation`` is the page's ``/Rotate`` (0 or 90 are what the tests use).
    The PDF's own numbers — the viewport ``/BBox`` and the markup vertices —
    are always written in UNROTATED user space, which is where the format puts
    them; only the returned truth moves into the displayed frame.
    """
    import fitz

    rotation = int(rotation) % 360
    gt = ScaledSheetGT(pdf=b"", rotation=rotation)
    w_un, h_un = SHEET_UNROT

    def to_display(x: float, y: float) -> Point:
        """Unrotated page point -> displayed point, by hand (not via PyMuPDF)."""
        if rotation == 90:
            return (h_un - y, x)
        if rotation == 180:
            return (w_un - x, h_un - y)
        if rotation == 270:
            return (y, w_un - x)
        return (x, y)

    def to_display_box(box) -> BBox:
        xs, ys = [], []
        for cx, cy in ((box[0], box[1]), (box[2], box[1]),
                       (box[0], box[3]), (box[2], box[3])):
            dx, dy = to_display(cx, cy)
            xs.append(dx)
            ys.append(dy)
        return (min(xs), min(ys), max(xs), max(ys))

    gt.page_size = ((h_un, w_un) if rotation in (90, 270) else (w_un, h_un))
    gt.viewport_bbox_displayed = to_display_box(gt.viewport_bbox_unrotated)

    doc = fitz.open()
    page = doc.new_page(width=w_un, height=h_un)

    # Enough separate paths that the page classifies as a drawing sheet: the
    # rule wants 200 on a sheet larger than tabloid, and ``finish`` must be
    # called per line or the whole loop commits as one path.
    shape = page.new_shape()
    for i in range(240):
        x = 200.0 + (i % 40) * 30.0
        y = 300.0 + (i // 40) * 80.0
        shape.draw_line((x, y), (x + 24.0, y))
        shape.finish(color=(0, 0, 0), width=0.5)
    shape.commit()
    page.insert_text((200.0, 200.0), "SITE PLAN", fontsize=24)

    # -- the dimension markup ------------------------------------------------
    # A right-angled path in unrotated space: 720 pt across, 540 pt down, so the
    # path is 1260 pt long. At 20 ft per inch that is 1260 / 72 * 20 = 350 ft.
    verts_unrot: List[Point] = [(288.0, 1008.0), (1008.0, 1008.0),
                                (1008.0, 1548.0)]
    gt.length_pt = sum(
        math.dist(a, b) for a, b in zip(verts_unrot, verts_unrot[1:]))
    gt.length_ft = gt.length_pt * FEET_PER_POINT
    gt.stated_ft = gt.length_ft
    gt.stated_text = f"{gt.length_ft:.2f} ft"
    gt.markup_vertices = tuple(to_display(*p) for p in verts_unrot)

    annot = page.add_polyline_annot([fitz.Point(*p) for p in verts_unrot])
    annot.set_info(title=gt.markup_author, content=gt.stated_text,
                   subject="Polylength")
    annot.update()
    doc.xref_set_key(annot.xref, "IT", f"/{gt.markup_intent}")
    doc.xref_set_key(annot.xref, "Measure", _measure_dict())

    # -- the page's stored scale --------------------------------------------
    # /BBox is UNROTATED user space, as real files write it (verified on a real
    # /Rotate 270 sheet, whose box fits the mediabox and overflows the
    # displayed rect).
    x0, y0, x1, y1 = gt.viewport_bbox_unrotated
    doc.xref_set_key(page.xref, "VP",
                     f"[ << /Type /Viewport /Name ({gt.viewport_name}) "
                     f"/BBox [ {x0:g} {y0:g} {x1:g} {y1:g} ] "
                     f"/Measure {_measure_dict()} >> ]")
    if rotation:
        page.set_rotation(rotation)

    bx0, by0, bx1, by1 = gt.viewport_bbox_displayed
    gt.point_inside = ((bx0 + bx1) / 2.0, (by0 + by1) / 2.0)
    gt.point_outside = (bx0 / 2.0, by0 / 2.0)

    gt.pdf = doc.tobytes()
    doc.close()
    return gt


def build_synthetic_uncalibrated_sheet_pdf() -> ScaledSheetGT:
    """The same sheet with the 1:1 default a real file was found to store.

    ``/X [<</C .01389/U( )>>]`` with ``/R ( )`` — a viewport that exists and
    says nothing, which is the case a reader must not mistake for a scale.
    """
    import fitz

    gt = build_synthetic_scaled_sheet_pdf(rotation=0)
    doc = fitz.open(stream=gt.pdf, filetype="pdf")
    page = doc[0]
    identity = ("<< /Type /Measure /Subtype /RL /R ( ) "
                "/X [ << /C .01389 /U ( ) >> ] "
                "/D [ << /C 1 /U ( ) >> ] /A [ << /C 1 /U ( ) >> ] >>")
    x0, y0, x1, y1 = gt.viewport_bbox_unrotated
    doc.xref_set_key(page.xref, "VP",
                     f"[ << /Type /Viewport /BBox "
                     f"[ {x0:g} {y0:g} {x1:g} {y1:g} ] "
                     f"/Measure {identity} >> ]")
    for annot in page.annots() or []:
        page.delete_annot(annot)
    gt.pdf = doc.tobytes()
    doc.close()
    gt.ratio = " "
    gt.x_unit = ""
    gt.x_per_point = 0.01389
    gt.length_ft = 0.0
    gt.stated_text = ""
    gt.markup_vertices = ()
    return gt
