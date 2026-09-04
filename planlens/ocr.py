"""OCR leg (B7): read lettering off rendered sheets into IR TextItems.

Many real plotted PDFs carry NO text layer at all — AutoCAD SHX fonts
letter the sheet as stroked vector geometry (verified on all 10
Mecklenburg ground-truth plots), and scanned sheets are pure raster.
``find_text``-style queries on such sheets need optics, not vectors:
render the page, OCR it, and map every recognized word box back into
the IR coordinate frame as a ``TextItem`` with ``source="ocr"`` and the
engine's confidence (< 1.0 — OCR output is evidence, never asserted).

Engine: RapidOCR (rapidocr-onnxruntime) — Apache-2.0, pure pip install
(~60 MB with onnxruntime; PP-OCR models ship inside the wheel, no
runtime downloads, no system binaries, no GPL). Installed via the
optional extra::

    pip install planlens[ocr]

Coordinate contract: rendering happens at ``dpi`` from the PDF page
(top-left origin, points); recognized boxes are mapped back to the IR's
bottom-left frame (y_ir = page_height - y_pdf) and multiplied by the
IR's model scale when augmenting a scaled IR. Rotated text is preserved
(RapidOCR reads vertical/rotated runs; the box's reading edge sets the
TextItem rotation).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence, Tuple

from planlens.ir.results import DrawingIR, TextItem

Point = Tuple[float, float]

#: Engine confidence floor below which results are dropped by default.
DEFAULT_MIN_CONFIDENCE = 0.5

_ENGINE = None  # lazy singleton — model load costs seconds


def _require_engine():
    global _ENGINE
    if _ENGINE is None:
        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:  # pragma: no cover - environment
            raise ImportError(
                "OCR support needs the optional extra: "
                "pip install planlens[ocr]  (RapidOCR, Apache-2.0, "
                "~60 MB, models bundled — no runtime downloads)") from exc
        _ENGINE = RapidOCR()
    return _ENGINE


def _render_page_png(filepath: Optional[str], content: Optional[bytes],
                     page: int, dpi: float
                     ) -> Tuple[bytes, float, float, Tuple[float, ...]]:
    """Render one page; also return its derotation matrix as a 6-tuple.

    The vector ingest (``planlens.pdf``) reads ``get_drawings()``
    coordinates, which live in the UNROTATED page space, while renders
    apply any /Rotate. To land OCR boxes in the SAME frame the IR uses,
    rendered pixel points must be pushed through the page's derotation
    matrix before the usual y-flip (which ingest does with the
    rotation-aware ``rect.height`` — replicated here verbatim, quirks
    and all, so OCR and vector geometry always agree).
    """
    import fitz

    doc = (fitz.open(stream=content, filetype="pdf") if content is not None
           else fitz.open(filepath))
    try:
        pg = doc[page]
        rect = pg.rect
        zoom = dpi / 72.0
        pix = pg.get_pixmap(matrix=fitz.Matrix(zoom, zoom))
        m = pg.derotation_matrix
        return (pix.tobytes("png"), rect.width, rect.height,
                (m.a, m.b, m.c, m.d, m.e, m.f))
    finally:
        doc.close()


def _unrotate_px(x: float, y: float, rotate: int,
                 w: int, h: int) -> Tuple[float, float]:
    """Map a pixel from the rotated image back into the ORIGINAL image.

    ``rotate`` is the CCW rotation that was applied to the original
    (w x h) image before OCR; (x, y) is a point in the rotated image.
    """
    if rotate == 0:
        return x, y
    if rotate == 90:    # forward (CCW): (x,y) -> (y, w-1-x); inverted here
        return w - 1 - y, x
    if rotate == 180:
        return w - 1 - x, h - 1 - y
    return y, h - 1 - x  # 270 CCW: forward (x,y) -> (h-1-y, x)


def _conf(raw: Any) -> float:
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def ocr_text_items(filepath: Optional[str] = None,
                   content: Optional[bytes] = None,
                   page: int = 0, dpi: float = 300.0,
                   min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                   rotate: Any = "auto",
                   id_prefix: str = "ocr") -> List[TextItem]:
    """OCR one PDF page into ``TextItem`` entities in the IR page frame.

    Returns items with ``source="ocr"``, engine confidence (< 1.0),
    position at the recognized box's bottom-left (insertion-point
    convention), rotation from the box's reading edge, and height from
    the box's short edge. Page points, bottom-left origin — multiply by
    a model scale yourself if you are not going through
    :func:`augment_ir_with_ocr`.

    ``rotate``: CCW degrees (0/90/180/270) to rotate the rendered image
    before OCR — landscape drawings plotted onto portrait pages carry
    their lettering sideways (the Mecklenburg ground-truth plots all do),
    and reading them upright is dramatically more accurate. ``"auto"``
    (default) probes 0 vs 90 at low dpi and keeps the direction that
    recognizes more characters. Recognized boxes are always mapped back
    to the UNROTATED page frame.
    """
    if (filepath is None) == (content is None):
        raise ValueError("pass exactly one of filepath / content")
    import cv2  # dependency of rapidocr-onnxruntime, present with [ocr]
    import numpy as np

    engine = _require_engine()

    def _run(img, rot):
        if rot == 90:
            work = cv2.rotate(img, cv2.ROTATE_90_COUNTERCLOCKWISE)
        elif rot == 180:
            work = cv2.rotate(img, cv2.ROTATE_180)
        elif rot == 270:
            work = cv2.rotate(img, cv2.ROTATE_90_CLOCKWISE)
        else:
            work = img
        result, _elapse = engine(work)
        return result or []

    if rotate == "auto":
        png_lo, _, _, _ = _render_page_png(filepath, content, page, 100.0)
        lo = cv2.imdecode(np.frombuffer(png_lo, np.uint8), cv2.IMREAD_COLOR)
        best_rot, best_chars = 0, -1
        # All four rotations (verifier finding, 2026-09-05: probing only
        # 0/90 left 180-presented text to RapidOCR's angle classifier,
        # which silently flips the line and returns a 180-reversed corner
        # order — positions land one string-width away with no error).
        # KNOWN RESIDUAL: when char counts tie across a 180 pair the
        # earlier rotation wins and flipped lines can still slip through;
        # a box-orientation cls-flip detector is the documented next step.
        for rot in (0, 90, 180, 270):
            chars = sum(len(str(t)) for _b, t, c in _run(lo, rot)
                        if _conf(c) >= min_confidence)
            if chars > best_chars:
                best_rot, best_chars = rot, chars
        rotate = best_rot
    rotate = int(rotate) % 360
    if rotate not in (0, 90, 180, 270):
        raise ValueError("rotate must be 0/90/180/270 or 'auto'")

    png, _w_pt, h_pt, derot = _render_page_png(filepath, content, page, dpi)
    img = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
    h_px, w_px = img.shape[:2]
    result = _run(img, rotate)
    items: List[TextItem] = []
    if not result:
        return items
    k = 72.0 / dpi
    da, db, dc, dd, de, df = derot

    def to_ir(px: Sequence[float]) -> Point:
        # image-rotation undo (our cv2 rotate) ...
        x, y = _unrotate_px(px[0], px[1], rotate, w_px, h_px)
        # ... pixel -> rendered page points ...
        xp, yp = x * k, y * k
        # ... page /Rotate undo (into ingest's unrotated frame) ...
        xu = xp * da + yp * dc + de
        yu = xp * db + yp * dd + df
        # ... and ingest's y-flip, rotation-aware height and all.
        return (xu, h_pt - yu)

    n = 0
    for box, text, conf in result:
        conf_f = _conf(conf)
        if conf_f < min_confidence or not str(text).strip():
            continue
        # RapidOCR box order: TL, TR, BR, BL in image coords.
        tl, tr, _br, bl = (to_ir(box[0]), to_ir(box[1]),
                           to_ir(box[2]), to_ir(box[3]))
        read_dir = (tr[0] - tl[0], tr[1] - tl[1])
        rotation = math.degrees(math.atan2(read_dir[1], read_dir[0]))
        height = math.hypot(tl[0] - bl[0], tl[1] - bl[1])
        n += 1
        items.append(TextItem(
            id=f"{id_prefix}{n}",
            source="ocr",
            confidence=min(conf_f, 0.999),  # never 1.0: OCR is evidence
            content=str(text),
            position=bl,
            rotation=round(rotation, 2),
            height=round(height, 3),
        ))
    return items


def augment_ir_with_ocr(ir: DrawingIR,
                        filepath: Optional[str] = None,
                        content: Optional[bytes] = None,
                        page: int = 0, dpi: float = 300.0,
                        min_confidence: float = DEFAULT_MIN_CONFIDENCE,
                        rotate: Any = "auto") -> Dict[str, Any]:
    """OCR the page and merge the results into ``ir`` in place.

    Positions/heights are multiplied by the IR's model scale when one was
    applied at ingest, so OCR text lands in the same coordinate space as
    the vector geometry. Existing entity ids are respected (items get an
    ``ocrN`` id series). Returns a summary dict:
    ``{n_added, n_existing_text, engine, dpi}``.
    """
    items = ocr_text_items(filepath=filepath, content=content, page=page,
                           dpi=dpi, min_confidence=min_confidence,
                           rotate=rotate)
    s = ir.scale or 1.0
    if s != 1.0:
        for t in items:
            t.position = (t.position[0] * s, t.position[1] * s)
            t.height = round(t.height * s, 6)
            t.bbox = t.compute_bbox()
    n_existing = sum(1 for e in ir.entities if e.KIND == "text")
    ir.entities.extend(items)
    ir.metadata = dict(ir.metadata or {})
    ir.metadata["ocr"] = {"n_items": len(items), "dpi": dpi,
                          "min_confidence": min_confidence,
                          "engine": "rapidocr-onnxruntime"}
    return {"n_added": len(items), "n_existing_text": n_existing,
            "engine": "rapidocr-onnxruntime", "dpi": dpi}
