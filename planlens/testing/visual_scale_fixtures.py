"""Synthetic pages with known scales: logs, plots, plans, profiles, chart families.

Built for :mod:`planlens.document.scales`, :mod:`planlens.document.raster`,
:mod:`planlens.document.scalefinder` and :mod:`planlens.document.measuring`,
and for the no-model measurement harness beside the app
(``module_work/scales_harness/``). Every page is drawn with PyMuPDF from
numbers stated here, and every answer is computed from those numbers — never
through the code under test (the ``scale_fixtures`` convention).

WHY SO MANY VARIANTS. The scanned logs the visual-scales work was measured on
are ONE template, and a finder tuned to one template fails on the next: the
first experiment assumed the depth column was the leftmost band and broke on
a test-pit form in the same report. So the fixtures vary what a template
fixes — where the depth labels sit relative to their depth (centred, on their
baseline, hanging from their top), whether ticks are drawn, whether the frame
lines fall on round depths, where the ruler column is, the skew, dashed
contacts, a hatched legend column, a continuation sheet, the scan's
resolution, noise and JPEG quality, and the ``/Rotate 270`` storage the real
scans use.

HOW A SCAN IS MADE. The form is drawn upright as vectors, rendered to grey at
the scan's dpi, turned by ``skew_deg`` about the page centre with bilinear
resampling (numpy), darkened to a scan's ink and paper greys, blurred, given
noise, JPEG-encoded and embedded as the only content of an image-only page
(stored ``/Rotate 270`` when asked, as the real scans are). A point ``p`` of
the upright design lands at ``C + R(skew) (p - C)`` on the displayed page,
which is how every answer is moved into the frame the code measures in.

Nothing here is copied from a real document.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

Point = Tuple[float, float]
BBox = Tuple[float, float, float, float]

A4 = (595.0, 842.0)
LETTER = (612.0, 792.0)
TABLOID_LANDSCAPE = (1224.0, 792.0)

#: Helvetica's digit height as a fraction of the font size (ink top to
#: baseline); the ink of a number is centred this/2 above its baseline.
DIGIT_HEIGHT = 0.703


# ---------------------------------------------------------------------------
# What a fixture states
# ---------------------------------------------------------------------------

@dataclass
class TruthLabel:
    """A printed number: its text, value and ink box on the DISPLAYED page."""
    text: str
    value: float
    box: BBox
    axis: str = ""                   # which scale it belongs to (a tag)


@dataclass
class TruthReading:
    """One thing to measure and its true value.

    ``kind`` is a :func:`planlens.document.measuring.measure` kind. ``at_pt``
    is where the thing is on the DISPLAYED page and ``box_pt`` its tight box
    there; ``value`` the true value of ``quantity`` (``values`` for a point
    read on two axes). ``at`` is the axis value of a curve read-off;
    ``to_pt`` the second point of a distance. ``region`` is the band the
    thing lives in (a log's description column, a plot's frame).
    """
    kind: str
    quantity: str
    value: Optional[float]
    at_pt: Point
    box_pt: BBox
    values: Dict[str, float] = field(default_factory=dict)
    at: Optional[Dict[str, float]] = None
    to_pt: Optional[Point] = None
    to_box_pt: Optional[BBox] = None
    region: Optional[BBox] = None
    tag: str = ""


@dataclass
class ScaleFixture:
    """One synthetic page and everything true about it."""
    name: str
    kind: str                        # log | plot | plan | profile | chart
    pdf: bytes
    page: int = 0
    raster: bool = False
    readings: List[TruthReading] = field(default_factory=list)
    labels: List[TruthLabel] = field(default_factory=list)
    #: Scale facts: quantity -> {"per_point", "unit", ...} as drawn.
    scales: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    skew_deg: float = 0.0
    #: A text source (OCR / Azure lines) to open the document with, or None.
    text_source: Any = None
    #: The page is not drawn to scale: every finder must refuse it.
    not_to_scale: bool = False
    notes: List[str] = field(default_factory=list)

    def open(self):
        """The fixture as a :class:`planlens.document.Document`."""
        from planlens.document import Document
        return Document(content=self.pdf, text_source=self.text_source,
                        name=self.name)

    def values_for(self, boxes: Sequence[Sequence[float]]
                   ) -> List[Optional[float]]:
        """What a perfect reader of numbered label crops would say.

        For each requested box, the printed label whose ink overlaps it most
        (``None`` where none does) — the stand-in for the app's one vision
        call over a contact sheet of the crops.
        """
        out: List[Optional[float]] = []
        for b in boxes:
            best, best_ov = None, 0.0
            for lab in self.labels:
                ov = _overlap(b, lab.box)
                if ov > best_ov:
                    best, best_ov = lab, ov
            out.append(best.value if best is not None and best_ov > 0.2
                       else None)
        return out


def _overlap(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over the smaller box's area."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return inter / small if small > 0 else 0.0


# ---------------------------------------------------------------------------
# Turning, rasterising, embedding
# ---------------------------------------------------------------------------

@dataclass
class _Turn:
    """The skew a scan applies: about the page centre, ``deg`` clockwise
    as displayed (y down), so a horizontal rule's slope is ``tan(deg)``."""
    deg: float
    cx: float
    cy: float

    def __call__(self, x: float, y: float) -> Point:
        t = math.radians(self.deg)
        c, s = math.cos(t), math.sin(t)
        dx, dy = x - self.cx, y - self.cy
        return (self.cx + c * dx - s * dy, self.cy + s * dx + c * dy)

    def box(self, b: Sequence[float]) -> BBox:
        pts = [self(b[0], b[1]), self(b[2], b[1]), self(b[0], b[3]),
               self(b[2], b[3])]
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        return (min(xs), min(ys), max(xs), max(ys))


def _render_grey(pdf: bytes, dpi: float):
    import fitz
    import numpy as np
    doc = fitz.open(stream=pdf, filetype="pdf")
    try:
        z = dpi / 72.0
        pix = doc[0].get_pixmap(matrix=fitz.Matrix(z, z),
                                colorspace=fitz.csGRAY, alpha=False)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(
            pix.height, pix.stride)[:, :pix.width].astype(np.float32)
    finally:
        doc.close()
    return img


def _rotate_image(img, deg: float):
    """Turn a grey image by ``deg`` about its centre (bilinear, paper fill).

    Inverse mapping: each output pixel centre samples the source at
    ``C + R(-deg) (p - C)``, which is exactly :class:`_Turn`'s inverse.
    """
    import numpy as np
    if abs(deg) < 1e-9:
        return img
    h, w = img.shape
    cy, cx = h / 2.0, w / 2.0
    t = math.radians(deg)
    c, s = math.cos(t), math.sin(t)
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    px, py = xx + 0.5 - cx, yy + 0.5 - cy
    sx = cx + c * px + s * py - 0.5
    sy = cy - s * px + c * py - 0.5
    x0 = np.floor(sx).astype(np.int64)
    y0 = np.floor(sy).astype(np.int64)
    fx, fy = sx - x0, sy - y0
    pad = np.pad(img, 2, mode="constant", constant_values=255.0)

    def at(yi, xi):
        return pad[np.clip(yi + 2, 0, h + 3), np.clip(xi + 2, 0, w + 3)]

    out = (at(y0, x0) * (1 - fx) * (1 - fy) + at(y0, x0 + 1) * fx * (1 - fy)
           + at(y0 + 1, x0) * (1 - fx) * fy + at(y0 + 1, x0 + 1) * fx * fy)
    return out


def _scanify(img, seed: int, noise: float, blur: bool,
             paper: float = 246.0, ink_level: float = 40.0):
    """Paper and ink greys, a little blur, noise."""
    import numpy as np
    rng = np.random.default_rng(seed)
    g = paper - (paper - ink_level) * (1.0 - img / 255.0)
    if blur:
        k = np.array([0.25, 0.5, 0.25], dtype=np.float32)
        g = (np.pad(g, ((0, 0), (1, 1)), mode="edge")[:, :-2] * k[0]
             + g * k[1] + np.pad(g, ((0, 0), (1, 1)), mode="edge")[:, 2:] * k[2])
        g = (np.pad(g, ((1, 1), (0, 0)), mode="edge")[:-2] * k[0]
             + g * k[1] + np.pad(g, ((1, 1), (0, 0)), mode="edge")[2:] * k[2])
    if noise > 0:
        g = g + rng.normal(0.0, noise, size=g.shape)
        # a few specks of dust
        n = int(g.size * 2e-5)
        ys = rng.integers(0, g.shape[0], n)
        xs = rng.integers(0, g.shape[1], n)
        g[ys, xs] = ink_level + 30.0
    return np.clip(g, 0, 255).astype(np.uint8)


def _image_pdf(grey, page_size: Tuple[float, float], jpeg: int,
               rotate270: bool, dpi: float) -> bytes:
    """An image-only page showing ``grey`` upright over the displayed page.

    The image is placed at EXACTLY its pixel size at ``dpi`` (a render's
    pixel count is the page size rounded up, so stretching it over the page
    would move content by up to a pixel across the sheet — a fixture error,
    not a finder's).
    """
    import fitz
    import numpy as np
    h, w = grey.shape
    pix = fitz.Pixmap(fitz.csGRAY, w, h, np.ascontiguousarray(grey).tobytes(),
                      False)
    data = pix.tobytes("jpeg", jpg_quality=int(jpeg))
    doc = fitz.open()
    W, H = page_size
    z = dpi / 72.0
    disp = fitz.Rect(0, 0, w / z, h / z)
    if rotate270:
        # Stored landscape, displayed portrait, the way the real scans are;
        # the image goes in turned so that the DISPLAYED page is upright
        # (verified by rendering it back in the fixture tests).
        page = doc.new_page(width=H, height=W)
        page.set_rotation(270)
        page.insert_image(disp * page.derotation_matrix, stream=data,
                          rotate=270)
    else:
        page = doc.new_page(width=W, height=H)
        page.insert_image(disp, stream=data)
    out = doc.tobytes()
    doc.close()
    return out


def scanned(pdf: bytes, page_size: Tuple[float, float], *, dpi: float = 200,
            skew_deg: float = 0.0, jpeg: int = 75, noise: float = 6.0,
            blur: bool = True, rotate270: bool = False, seed: int = 0
            ) -> bytes:
    """``pdf``'s first page as a scan: rendered, turned, noised, JPEG'd."""
    img = _render_grey(pdf, dpi)
    img = _rotate_image(img, skew_deg)
    grey = _scanify(img, seed, noise, blur)
    return _image_pdf(grey, page_size, jpeg, rotate270, dpi)


class OcrLines:
    """A text source carrying given lines, the way OCR or Azure DI would.

    It stands in for :class:`planlens.document.AzureLayout` on a synthetic
    scan: the same protocol (``covers`` / ``extract``), the same displayed
    frame, boxes that hug the ink as an optical reader's do.
    """

    def __init__(self, lines: Sequence[Tuple[str, BBox]], page: int = 0,
                 name: str = "ocr"):
        self._lines = list(lines)
        self._page = int(page)
        self.name = name

    def covers(self, index: int) -> bool:
        return index == self._page

    def extract(self, page, index: int, words: bool = False):
        from planlens.document.model import TextLine
        out = [TextLine(id=f"p{index}.o{i}", page=index, text=t,
                        bbox=tuple(b), rotation=0.0, source=self.name,
                        confidence=0.99)
               for i, (t, b) in enumerate(self._lines)]
        return out, [], [], {}, []


def _text_ink_box(x: float, baseline: float, text: str, size: float,
                  font: str = "helv") -> BBox:
    """The ink box of a run of digits drawn at ``(x, baseline)``."""
    import fitz
    w = fitz.get_text_length(text, fontname=font, fontsize=size)
    return (x, baseline - DIGIT_HEIGHT * size, x + w, baseline)


def _text_width(text: str, size: float, font: str = "helv") -> float:
    import fitz
    return fitz.get_text_length(text, fontname=font, fontsize=size)


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LogVariant:
    """One way a boring-log form can be drawn and scanned."""
    name: str
    raster: bool = True
    label_anchor: str = "baseline"     # centre | baseline | top
    ticks: bool = False
    frame_labelled: bool = True
    ruler: str = "left"                # left | inside | right
    skew_deg: float = 0.0
    dashed: bool = False
    hatched: bool = True
    start: float = 0.0
    step: float = 1.0
    n_steps: int = 10
    unit: str = "m"
    pt_per_step: float = 46.0
    dpi: float = 200.0
    jpeg: int = 75
    noise: float = 6.0
    rotate270: bool = False
    contacts: Tuple[float, ...] = (2.26, 3.86, 5.31, 7.26)
    samples_regular: bool = False
    text_source: Optional[str] = None  # None | "ocr" | "azure_di"
    label_size: float = 9.0
    seed: int = 1


#: Column order and widths by where the ruler stands. Widths in points.
_LOG_COLUMNS = {
    "left": (("depth", 44), ("description", 200), ("legend", 46),
             ("spt", 104), ("type", 28), ("no", 28), ("remarks", 65)),
    "inside": (("legend", 46), ("description", 200), ("depth", 44),
               ("spt", 104), ("type", 28), ("no", 28), ("remarks", 65)),
    "right": (("description", 200), ("legend", 46), ("spt", 104),
              ("type", 28), ("no", 28), ("remarks", 65), ("depth", 44)),
}

_LOG_HEADERS = {"depth": "Depth", "description": "Soil Description",
                "legend": "Legend", "spt": "SPT Blows", "type": "Type",
                "no": "No", "remarks": "Remarks"}

_LOG_DESCRIPTIONS = (
    "Brown, loose, silty fine SAND.",
    "Grey, medium dense, clean SAND with fine gravel.",
    "Brown, medium dense, silty sandy GRAVEL.",
    "Light grey, dense, silty SAND with trace gravel.",
    "Light brown, very dense, gravelly SAND.",
    "Grey, stiff, sandy lean CLAY.",
)


def _hatch(shape, x0: float, y0: float, x1: float, y1: float, kind: int):
    """A legend pattern inside a box: diagonals, or dots, or clay dashes."""
    if kind % 3 == 0:
        step = 7.0
        t = -(y1 - y0)
        while t < (x1 - x0):
            a = (x0 + max(0.0, t), y0 + max(0.0, -t))
            ex = min(x1 - x0, t + (y1 - y0))
            b = (x0 + ex, y0 + (ex - t))
            if b[0] > a[0]:
                shape.draw_line(a, b)
            t += step
        shape.finish(color=(0, 0, 0), width=0.5)
    elif kind % 3 == 1:
        y = y0 + 4.0
        row = 0
        while y < y1 - 2:
            x = x0 + 3.0 + (3.0 if row % 2 else 0.0)
            while x < x1 - 2:
                shape.draw_circle((x, y), 0.6)
                x += 7.0
            y += 5.0
            row += 1
        shape.finish(color=(0, 0, 0), fill=(0, 0, 0), width=0.3)
    else:
        # clay: short horizontal dashes in rows - HORIZONTAL ink that must
        # never be read as a stratum line in the description column
        y = y0 + 4.0
        row = 0
        while y < y1 - 2:
            x = x0 + 2.0 + (4.0 if row % 2 else 0.0)
            while x + 6 < x1 - 1:
                shape.draw_line((x, y), (x + 6.0, y))
                x += 10.0
            y += 5.0
            row += 1
        shape.finish(color=(0, 0, 0), width=0.6)


def build_log(v: LogVariant) -> ScaleFixture:
    """One sheet of a boring log drawn per ``v``, with every answer stated."""
    import fitz
    W, H = A4
    left, right = 40.0, 555.0
    head_top, body_top = 175.0, 220.0
    per = v.pt_per_step / v.step                     # points per unit depth
    if v.frame_labelled:
        top_value = v.start
        bottom_value = v.start + v.n_steps * v.step
        label_values = [v.start + k * v.step for k in range(1, v.n_steps + 1)]
    else:
        # The body runs past the label run at both ends by a part of a step,
        # so neither frame line falls on a round depth.
        top_value = v.start - 0.40 * v.step
        bottom_value = v.start + v.n_steps * v.step + 0.45 * v.step
        label_values = [v.start + k * v.step for k in range(0, v.n_steps + 1)]
    body_bottom = body_top + (bottom_value - top_value) * per

    def y_of(depth: float) -> float:
        return body_top + (depth - top_value) * per

    cols = _LOG_COLUMNS[v.ruler]
    scale_x = (right - left) / sum(wd for _, wd in cols)
    edges = [left]
    for _name, wd in cols:
        edges.append(edges[-1] + wd * scale_x)
    band = {name: (edges[i], edges[i + 1]) for i, (name, _) in enumerate(cols)}

    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    turn = _Turn(v.skew_deg if v.raster else 0.0, W / 2.0, H / 2.0)
    ocr: List[Tuple[str, BBox]] = []

    # -- title and fields ---------------------------------------------------
    page.insert_text((230, 110), "RECORD OF BORING", fontsize=12,
                     fontname="hebo")
    page.insert_text((left, 140), "CLIENT  :  Example Holdings", fontsize=8)
    page.insert_text((left, 152), "PROJECT :  Synthetic Site", fontsize=8)
    page.insert_text((360, 140), f"BOREHOLE NO :  {1 + int(v.start // 10)}",
                     fontsize=8)
    # -- the form -----------------------------------------------------------
    shape = page.new_shape()
    for x in edges:
        shape.draw_line((x, head_top), (x, body_bottom))
    for y in (head_top, body_top, body_bottom):
        shape.draw_line((left, y), (right, y))
    shape.finish(color=(0, 0, 0), width=0.9)
    shape.commit()
    for name, (a, b) in band.items():
        text = _LOG_HEADERS[name]
        size = 7.5
        tw = _text_width(text, size)
        if tw > b - a - 3:
            size = max(5.0, size * (b - a - 3) / tw)
            tw = _text_width(text, size)
        page.insert_text(((a + b - tw) / 2.0, (head_top + body_top) / 2.0 + 3),
                         text, fontsize=size)
        ocr.append((text, turn.box((((a + b - tw) / 2.0),
                                    (head_top + body_top) / 2.0 + 3 - 0.72 * size,
                                    (a + b + tw) / 2.0,
                                    (head_top + body_top) / 2.0 + 3))))

    # -- the depth ruler -----------------------------------------------------
    da, db = band["depth"]
    labels: List[TruthLabel] = []
    fs = v.label_size
    for val in label_values:
        text = f"{val:.1f}"
        tw = _text_width(text, fs)
        x = (da + db - tw) / 2.0
        y = y_of(val)
        if v.label_anchor == "centre":
            base = y + DIGIT_HEIGHT * fs / 2.0
        elif v.label_anchor == "top":
            base = y + 1.5 + DIGIT_HEIGHT * fs
        else:                                    # baseline: label sits above
            base = y - 1.5
        if base > body_bottom - 1.0 or base - DIGIT_HEIGHT * fs < body_top + 1.0:
            # A label that would cross the frame is not printed (a form
            # leaves the one at the foot line off rather than misplace it).
            continue
        page.insert_text((x, base), text, fontsize=fs)
        ink_box = _text_ink_box(x, base, text, fs)
        labels.append(TruthLabel(text, val, turn.box(ink_box), "depth"))
        ocr.append((text, turn.box((ink_box[0] - 0.5, ink_box[1] - 0.8,
                                    ink_box[2] + 0.5, ink_box[3] + 0.8))))
        if v.ticks:
            page.draw_line((db - 7.0, y), (db, y), width=0.7)

    # -- layers ---------------------------------------------------------------
    dsa, dsb = band["description"]
    la, lb = band["legend"]
    contacts = [c for c in v.contacts]
    bounds = [top_value] + [v.start + c for c in contacts] + [bottom_value]
    for i, (t, b) in enumerate(zip(bounds, bounds[1:])):
        if v.hatched:
            sh = page.new_shape()
            _hatch(sh, la + 1.0, y_of(t) + 1.0, lb - 1.0, y_of(b) - 1.0, i)
            sh.commit()
        words = _LOG_DESCRIPTIONS[i % len(_LOG_DESCRIPTIONS)].split()
        lines_txt: List[str] = []
        cur = ""
        for w in words:
            if _text_width((cur + " " + w).strip(), 7.5) > (dsb - dsa - 12):
                lines_txt.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        lines_txt.append(cur)
        ty = y_of(t) + 14.0
        for ln in lines_txt:
            if ty > y_of(b) - 3:
                break
            page.insert_text((dsa + 6, ty), ln, fontsize=7.5)
            ocr.append((ln, turn.box((dsa + 6, ty - 6.0,
                                      dsa + 6 + _text_width(ln, 7.5), ty + 1.5))))
            ty += 9.5
    readings: List[TruthReading] = []
    # A contact is drawn across the description and the legend beside it.
    span = (min(dsa, la), max(dsb, lb))
    for i, c in enumerate(contacts):
        y = y_of(v.start + c)
        dashed = v.dashed and i % 2 == 1
        sh = page.new_shape()
        if dashed:
            x = span[0]
            while x < span[1]:
                sh.draw_line((x, y), (min(x + 5.0, span[1]), y))
                x += 8.0
        else:
            sh.draw_line((span[0], y), (span[1], y))
        sh.finish(color=(0, 0, 0), width=0.8)
        sh.commit()
        xm = (dsa + dsb) / 2.0
        region = turn.box((dsa, y_of(top_value), dsb, y_of(bottom_value)))
        readings.append(TruthReading(
            kind="line", quantity="depth", value=v.start + c,
            at_pt=turn(xm, y), box_pt=turn.box((dsa + 30, y - 1.0,
                                                dsb - 30, y + 1.0)),
            region=region, tag="dashed" if dashed else "contact"))

    # -- samples: values at sample depths in their own columns ---------------
    sa, sb = band["spt"]
    ta, tb = band["type"]
    na, nb = band["no"]
    if v.samples_regular:
        sample_depths = [v.start + 0.75 + 1.5 * k for k in range(7)
                         if 0.75 + 1.5 * k < v.n_steps * v.step - 0.3]
    else:
        sample_depths = [v.start + d for d in (0.55, 1.05, 2.4, 3.15, 3.7,
                                               5.05, 6.45, 6.9, 8.3, 9.55)
                         if d < v.n_steps * v.step - 0.3]
    for k, d in enumerate(sample_depths):
        y = y_of(d) + 3.0
        vals = f"{5 + k % 4}   {9 + k % 5}   {11 + k % 3}   {25 + k}"
        page.insert_text((sa + 4, y), vals, fontsize=7.5)
        page.insert_text((ta + 8, y), "D", fontsize=7.5)
        page.insert_text((na + 8, y), f"{k + 1 + int(v.start)}", fontsize=7.5)
        ocr.append((vals, turn.box((sa + 4, y - 6, sa + 4 + _text_width(vals, 7.5),
                                    y + 1))))
    ra_, rb_ = band["remarks"]
    page.insert_text((ra_ + 4, y_of(v.start + 4.5)), "Casing to 4.5", fontsize=6.5)

    page.insert_text((left, body_bottom + 22), "ABBREVIATIONS:  D - DISTURBED",
                     fontsize=7)
    vector_pdf = doc.tobytes()
    doc.close()

    if v.raster:
        pdf = scanned(vector_pdf, A4, dpi=v.dpi, skew_deg=v.skew_deg,
                      jpeg=v.jpeg, noise=v.noise, rotate270=v.rotate270,
                      seed=v.seed)
    else:
        pdf = vector_pdf
    text_source = None
    if v.raster and v.text_source:
        text_source = OcrLines(ocr, page=0, name=v.text_source)
    fx = ScaleFixture(
        name=f"log/{v.name}", kind="log", pdf=pdf, raster=v.raster,
        readings=readings, labels=labels, skew_deg=turn.deg,
        text_source=text_source,
        scales={"depth": {"per_point": 1.0 / per, "unit": v.unit,
                          "top": top_value, "bottom": bottom_value,
                          "body_top_y": body_top, "body_bottom_y": body_bottom,
                          "column": turn.box((da, body_top, db, body_bottom)),
                          "description": turn.box((dsa, body_top, dsb,
                                                   body_bottom))}})
    return fx


def log_variants() -> List[LogVariant]:
    """The log variants the harness runs: a covering set, not a full grid."""
    V = LogVariant
    return [
        V("scan_baseline_frames"),
        V("scan_centre_frames", label_anchor="centre", skew_deg=0.35),
        V("scan_top_frames", label_anchor="top", skew_deg=-0.45, dpi=300,
          jpeg=80, seed=2),
        V("scan_ticks_unframed", ticks=True, frame_labelled=False,
          skew_deg=0.6, seed=3),
        V("scan_inside_ruler", ruler="inside", skew_deg=-0.8, seed=4),
        V("scan_right_ruler", ruler="right", label_anchor="centre",
          skew_deg=1.0, dpi=150, jpeg=60, noise=9.0, seed=5),
        V("scan_dashed_contacts", dashed=True, skew_deg=-1.0, seed=6),
        V("scan_no_hatch", hatched=False, skew_deg=0.15, seed=7),
        V("scan_continuation", start=10.0, contacts=(2.86, 4.28, 7.7),
          skew_deg=-0.25, seed=8),
        V("scan_rotate270", rotate270=True, skew_deg=0.4, seed=9),
        V("scan_regular_samples", samples_regular=True, skew_deg=0.2,
          seed=10),
        V("scan_feet", unit="ft", step=5.0, n_steps=8, pt_per_step=56.0,
          contacts=(6.2, 17.5, 29.0), label_anchor="centre", skew_deg=-0.6,
          seed=11),
        V("scan_unframed_centre", frame_labelled=False,
          label_anchor="centre", skew_deg=0.7, seed=12),
        V("scan_unframed_baseline", frame_labelled=False, skew_deg=-0.3,
          seed=13),
        V("scan_low_dpi_noisy", dpi=150, jpeg=60, noise=10.0, skew_deg=0.9,
          seed=14),
        V("scan_ocr_text", text_source="ocr", skew_deg=0.5, seed=15),
        V("scan_di_text_rot270", text_source="azure_di", rotate270=True,
          ruler="inside", skew_deg=-0.55, seed=16),
        V("vector_centre", raster=False, label_anchor="centre"),
        V("vector_baseline_ticks", raster=False, ticks=True,
          ruler="inside"),
        V("vector_top_frames", raster=False, label_anchor="top",
          ruler="right"),
    ]


def build_pit_sketch(raster: bool = True, skew_deg: float = 0.3
                     ) -> ScaleFixture:
    """A test-pit record NOT drawn to scale: printed contact depths only.

    The depths "0.00", "2.00", "2.20" are printed at the contacts of a
    sketch, so they do not step evenly down the page. A finder must refuse
    to make a scale of them (and say to use the printed depths).
    """
    import fitz
    W, H = A4
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    page.insert_text((230, 110), "RECORD OF TRIAL PIT", fontsize=12,
                     fontname="hebo")
    edges = (60.0, 110.0, 160.0, 230.0, 300.0, 370.0, 540.0)
    top, head, bot = 180.0, 230.0, 470.0
    sh = page.new_shape()
    for x in edges:
        sh.draw_line((x, top), (x, bot))
    for y in (top, head, bot):
        sh.draw_line((edges[0], y), (edges[-1], y))
    sh.draw_line((edges[4], 440.0), (edges[-1], 440.0))
    sh.finish(color=(0, 0, 0), width=0.9)
    sh.commit()
    for text, y in (("0.00", head + 12), ("2.00", 432.0), ("2.20", 458.0)):
        page.insert_text((edges[2] + 18, y), text, fontsize=9)
    page.insert_text((edges[5] + 6, 300), "0.00 - 2.00 m: brown silty SAND",
                     fontsize=7.5)
    page.insert_text((edges[5] + 6, 452), "2.00 - 2.20 m: gravelly SAND",
                     fontsize=7.5)
    pdf = doc.tobytes()
    doc.close()
    if raster:
        pdf = scanned(pdf, A4, skew_deg=skew_deg, seed=31)
    return ScaleFixture(name=f"log/pit_sketch{'_scan' if raster else ''}",
                        kind="log", pdf=pdf, raster=raster,
                        not_to_scale=True, skew_deg=skew_deg if raster else 0)


# ---------------------------------------------------------------------------
# Axes, plots, profiles, chart families
# ---------------------------------------------------------------------------

@dataclass
class Axis:
    """One drawn axis: values ``vmin``..``vmax`` at page positions ``p0``..``p1``."""
    transform: str
    vmin: float
    vmax: float
    p0: float
    p1: float
    major: Sequence[float] = ()
    minor: Sequence[float] = ()
    fmt: Callable[[float], str] = lambda v: f"{v:g}"     # noqa: E731
    name: str = ""
    unit: Optional[str] = None

    def _t(self, v: float) -> float:
        return math.log10(v) if self.transform == "log10" else v

    def pos(self, v: float) -> float:
        a, b = self._t(self.vmin), self._t(self.vmax)
        return self.p0 + (self._t(v) - a) / (b - a) * (self.p1 - self.p0)

    def value(self, p: float) -> float:
        a, b = self._t(self.vmin), self._t(self.vmax)
        t = a + (p - self.p0) / (self.p1 - self.p0) * (b - a)
        return 10.0 ** t if self.transform == "log10" else t

    @property
    def per_point(self) -> float:
        a, b = self._t(self.vmin), self._t(self.vmax)
        return (b - a) / (self.p1 - self.p0)


def _log_lines(vmin: float, vmax: float) -> Tuple[List[float], List[float]]:
    majors, minors = [], []
    e = int(math.floor(math.log10(vmin)))
    while 10.0 ** e <= vmax * 1.0000001:
        for k in range(1, 10):
            v = k * 10.0 ** e
            if vmin * 0.9999999 <= v <= vmax * 1.0000001:
                (majors if k == 1 else minors).append(v)
        e += 1
    return majors, minors


@dataclass
class _PlotSpec:
    frame: BBox
    x: Axis
    y: Axis
    grid: str = "full"                  # full | major | ticks | dotted
    markers: Sequence[Tuple[float, float]] = ()
    marker: str = "square"              # square | circle | hollow
    curves: Sequence[Callable[[float], float]] = ()
    curve_labels: Sequence[str] = ()
    curve_domain: Optional[Tuple[float, float]] = None
    curve_along: str = "x"              # the curve is y(x), or x(y)
    title: str = ""
    broken_title: bool = False
    label_size: float = 8.0
    tag: str = "plot"


def _draw_plot(page, spec: _PlotSpec, turn: _Turn, labels: List[TruthLabel],
               ocr: List[Tuple[str, BBox]], vector_text: bool = True) -> None:
    import fitz
    x0, y0, x1, y1 = spec.frame
    X, Y = spec.x, spec.y
    sh = page.new_shape()
    dots = spec.grid == "dotted"
    grid_w = 0.4

    def gline(a, b):
        if dots:
            # dotted gridlines: 1.2 pt dashes every 3 pt
            (ax, ay), (bx, by) = a, b
            n = int(math.hypot(bx - ax, by - ay) / 3.0)
            for i in range(n):
                t0 = i / n
                t1 = min(1.0, t0 + 1.2 / max(1e-9, math.hypot(bx - ax, by - ay)))
                sh.draw_line((ax + (bx - ax) * t0, ay + (by - ay) * t0),
                             (ax + (bx - ax) * t1, ay + (by - ay) * t1))
        else:
            sh.draw_line(a, b)

    if spec.grid in ("full", "dotted", "major"):
        xs = list(X.major) + (list(X.minor) if spec.grid != "major" else [])
        ys = list(Y.major) + (list(Y.minor) if spec.grid != "major" else [])
        for v in xs:
            p = X.pos(v)
            if x0 + 0.5 < p < x1 - 0.5:
                gline((p, y0), (p, y1))
        for v in ys:
            p = Y.pos(v)
            if y0 + 0.5 < p < y1 - 0.5:
                gline((x0, p), (x1, p))
        sh.finish(color=(0.35, 0.35, 0.35), width=grid_w)
    else:
        for v in X.major:
            p = X.pos(v)
            sh.draw_line((p, y1), (p, y1 - 5.0))
        for v in Y.major:
            p = Y.pos(v)
            sh.draw_line((x0, p), (x0 + 5.0, p))
        sh.finish(color=(0, 0, 0), width=0.6)
    sh.draw_rect(fitz.Rect(x0, y0, x1, y1))
    sh.finish(color=(0, 0, 0), width=0.9)
    sh.commit()
    if spec.broken_title:
        # The E6 trap: an axis title in a white box inside the plot breaks a
        # gridline in two, and a length filter would drop it.
        yb = Y.pos(Y.major[len(Y.major) // 2])
        bx = (x0 + 6, yb - 6, x0 + 0.42 * (x1 - x0), yb + 6)
        page.draw_rect(fitz.Rect(*bx), color=None, fill=(1, 1, 1))
        page.insert_text((bx[0] + 3, yb + 3), "Percentage finer", fontsize=8)
    fs = spec.label_size
    for v in X.major:
        text = X.fmt(v)
        tw = _text_width(text, fs)
        p = X.pos(v)
        base = y1 + 4.0 + DIGIT_HEIGHT * fs
        page.insert_text((p - tw / 2.0, base), text, fontsize=fs)
        box = _text_ink_box(p - tw / 2.0, base, text, fs)
        labels.append(TruthLabel(text, v, turn.box(box), f"{spec.tag}.x"))
        ocr.append((text, turn.box(box)))
    for v in Y.major:
        text = Y.fmt(v)
        tw = _text_width(text, fs)
        p = Y.pos(v)
        base = p + DIGIT_HEIGHT * fs / 2.0
        page.insert_text((x0 - 4.0 - tw, base), text, fontsize=fs)
        box = _text_ink_box(x0 - 4.0 - tw, base, text, fs)
        labels.append(TruthLabel(text, v, turn.box(box), f"{spec.tag}.y"))
        ocr.append((text, turn.box(box)))
    if spec.title:
        page.insert_text(((x0 + x1) / 2.0 - 40, y1 + 30), spec.title,
                         fontsize=8)
    # curves
    for ci, f in enumerate(spec.curves):
        pts = []
        if spec.curve_along == "x":
            lo, hi = spec.curve_domain or (X.vmin, X.vmax)
            for i in range(241):
                t = i / 240.0
                if X.transform == "log10":
                    v = 10 ** (math.log10(lo) + t * (math.log10(hi) -
                                                     math.log10(lo)))
                else:
                    v = lo + t * (hi - lo)
                pts.append((X.pos(v), Y.pos(f(v))))
        else:
            lo, hi = spec.curve_domain or (Y.vmin, Y.vmax)
            for i in range(241):
                t = i / 240.0
                v = lo + t * (hi - lo)
                pts.append((X.pos(f(v)), Y.pos(v)))
        page.draw_polyline(pts, color=(0, 0, 0), width=1.0)
        if ci < len(spec.curve_labels):
            ex, ey = pts[-1]
            page.insert_text((ex + 3, ey + 3), spec.curve_labels[ci],
                             fontsize=7)
    for xv, yv in spec.markers:
        px, py = X.pos(xv), Y.pos(yv)
        if spec.marker == "square":
            page.draw_rect(fitz.Rect(px - 2.2, py - 2.2, px + 2.2, py + 2.2),
                           color=(0, 0, 0), fill=(0, 0, 0))
        elif spec.marker == "circle":
            page.draw_circle((px, py), 2.4, color=(0, 0, 0), fill=(0, 0, 0))
        else:
            page.draw_circle((px, py), 2.6, color=(0, 0, 0), width=0.8)


def _plot_readings(spec: _PlotSpec, turn: _Turn, reads: Sequence[float],
                   read_axis: str = "x") -> List[TruthReading]:
    out: List[TruthReading] = []
    region = turn.box(spec.frame)
    for xv, yv in spec.markers:
        px, py = spec.x.pos(xv), spec.y.pos(yv)
        out.append(TruthReading(
            kind="point", quantity="point", value=None,
            values={f"{spec.tag}.x": xv, f"{spec.tag}.y": yv},
            at_pt=turn(px, py), box_pt=turn.box((px - 3, py - 3, px + 3,
                                                 py + 3)),
            region=region, tag=f"{spec.tag}.marker"))
    for ci, f in enumerate(spec.curves):
        for v in reads:
            if read_axis == "x" and spec.curve_along == "x":
                yv = f(v)
                px, py = spec.x.pos(v), spec.y.pos(yv)
                out.append(TruthReading(
                    kind="curve", quantity=f"{spec.tag}.y", value=yv,
                    at={f"{spec.tag}.x": v}, at_pt=turn(px, py),
                    box_pt=turn.box((px - 4, py - 4, px + 4, py + 4)),
                    region=region, tag=f"{spec.tag}.curve{ci}"))
            elif read_axis == "y" and spec.curve_along == "y":
                xv = f(v)
                px, py = spec.x.pos(xv), spec.y.pos(v)
                out.append(TruthReading(
                    kind="curve", quantity=f"{spec.tag}.x", value=xv,
                    at={f"{spec.tag}.y": v}, at_pt=turn(px, py),
                    box_pt=turn.box((px - 4, py - 4, px + 4, py + 4)),
                    region=region, tag=f"{spec.tag}.curve{ci}"))
    return out


def _grading_spec(frame: BBox, tag: str = "plot", broken: bool = False,
                  marker: str = "square") -> _PlotSpec:
    x0, y0, x1, y1 = frame
    maj, mino = _log_lines(0.001, 100.0)
    X = Axis("log10", 0.001, 100.0, x0, x1, major=maj, minor=mino,
             fmt=lambda v: f"{v:g}", name="particle size", unit="mm")
    Y = Axis("linear", 0.0, 100.0, y1, y0, major=[10.0 * k for k in range(11)],
             name="percent finer", unit="%")
    d50, n = 0.32, 2.3

    def passing(d: float) -> float:
        return 100.0 / (1.0 + (d50 / d) ** n)

    sizes = (0.063, 0.15, 0.212, 0.3, 0.425, 0.6, 1.18, 2.0, 3.35, 5.0, 6.3,
             10.0, 14.0, 20.0)
    return _PlotSpec(frame=frame, x=X, y=Y, grid="full",
                     markers=[(d, passing(d)) for d in sizes], marker=marker,
                     curves=[passing], curve_domain=(0.05, 30.0),
                     title="Particle size (mm)", broken_title=broken, tag=tag)


@dataclass(frozen=True)
class PlotVariant:
    name: str
    kind: str                       # grading | linear_ticks | dotted |
    #                                 reversed_y | log_y | two_charts | broken
    raster: bool = False
    skew_deg: float = 0.0
    dpi: float = 200.0
    jpeg: int = 75
    seed: int = 21
    rotate270: bool = False


def build_plot(v: PlotVariant) -> ScaleFixture:
    """A plot page per ``v`` with its axes, markers and curves stated."""
    import fitz
    W, H = A4
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    turn = _Turn(v.skew_deg if v.raster else 0.0, W / 2.0, H / 2.0)
    labels: List[TruthLabel] = []
    ocr: List[Tuple[str, BBox]] = []
    specs: List[Tuple[_PlotSpec, Sequence[float], str]] = []
    page.insert_text((180, 80), "LABORATORY TEST REPORT", fontsize=12,
                     fontname="hebo")
    if v.kind in ("grading", "broken"):
        sp = _grading_spec((110, 300, 520, 620), broken=(v.kind == "broken"))
        specs.append((sp, (0.1, 0.3, 1.0, 3.0), "x"))
    elif v.kind in ("linear_ticks", "dotted"):
        X = Axis("linear", 20.0, 24.0, 120, 480, major=[20, 21, 22, 23, 24],
                 minor=[20.5, 21.5, 22.5, 23.5], fmt=lambda t: f"{t:.1f}",
                 name="moisture content", unit="%")
        Y = Axis("linear", 10.0, 30.0, 560, 330, major=[10, 15, 20, 25, 30],
                 minor=[12.5, 17.5, 22.5, 27.5], name="penetration",
                 unit="mm")
        pts = [(20.4, 16.4), (21.6, 19.4), (22.3, 22.4), (23.2, 25.4)]
        sp = _PlotSpec(frame=(120, 330, 480, 560), x=X, y=Y,
                       grid="ticks" if v.kind == "linear_ticks" else "dotted",
                       markers=pts, marker="circle",
                       curves=[lambda m: 16.4 + (m - 20.4) * 3.2],
                       curve_domain=(20.2, 23.6), tag="plot")
        specs.append((sp, (21.0, 22.0, 23.0), "x"))
    elif v.kind == "reversed_y":
        X = Axis("linear", 0.0, 20.0, 150, 470, major=[0, 5, 10, 15, 20],
                 minor=[2.5, 7.5, 12.5, 17.5], name="cone resistance",
                 unit="MPa")
        Y = Axis("linear", 0.0, 10.0, 200, 700,
                 major=[float(k) for k in range(11)], name="depth", unit="m")

        def qc(z: float) -> float:
            return 3.0 + 1.1 * z + 2.0 * math.sin(z * 1.3)

        sp = _PlotSpec(frame=(150, 200, 470, 700), x=X, y=Y, grid="major",
                       curves=[qc], curve_along="y", curve_domain=(0.2, 9.8),
                       tag="plot")
        specs.append((sp, (1.5, 3.0, 4.5, 6.0, 7.5, 9.0), "y"))
    elif v.kind == "log_y":
        maj, mino = _log_lines(1.0, 1000.0)
        X = Axis("linear", 0.0, 40.0, 140, 470, major=[0, 10, 20, 30, 40],
                 name="time", unit="min")
        Y = Axis("log10", 1.0, 1000.0, 600, 260, major=maj, minor=mino,
                 name="stress", unit="kPa")
        sp = _PlotSpec(frame=(140, 260, 470, 600), x=X, y=Y, grid="full",
                       curves=[lambda t: 10 ** (0.06 * t + 0.25)],
                       curve_domain=(1.0, 39.0),
                       markers=[(5.0, 10 ** 0.55), (15.0, 10 ** 1.15),
                                (25.0, 10 ** 1.75), (35.0, 10 ** 2.35)],
                       marker="square", tag="plot")
        specs.append((sp, (8.0, 18.0, 28.0), "x"))
    elif v.kind == "two_charts":
        X1 = Axis("linear", 0.0, 100.0, 120, 480,
                  major=[0, 20, 40, 60, 80, 100], name="normal stress",
                  unit="kPa")
        Y1 = Axis("linear", 0.0, 50.0, 330, 130,
                  major=[0, 10, 20, 30, 40, 50], name="shear stress",
                  unit="kPa")
        sp1 = _PlotSpec(frame=(120, 130, 480, 330), x=X1, y=Y1, grid="major",
                        markers=[(25.0, 18.0), (50.0, 29.5), (75.0, 41.0)],
                        marker="circle",
                        curves=[lambda s: 6.5 + 0.46 * s],
                        curve_domain=(0.0, 95.0), tag="top")
        maj, mino = _log_lines(1.0, 1000.0)
        X2 = Axis("log10", 1.0, 1000.0, 120, 480, major=maj, minor=mino,
                  name="pressure", unit="kPa")
        Y2 = Axis("linear", 0.4, 1.0, 720, 450,
                  major=[0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
                  fmt=lambda t: f"{t:.1f}", name="void ratio", unit=None)
        sp2 = _PlotSpec(frame=(120, 450, 480, 720), x=X2, y=Y2, grid="full",
                        curves=[lambda p: 0.95 - 0.16 * math.log10(p)],
                        markers=[(3.0, 0.95 - 0.16 * math.log10(3.0)),
                                 (30.0, 0.95 - 0.16 * math.log10(30.0)),
                                 (300.0, 0.95 - 0.16 * math.log10(300.0))],
                        marker="square", tag="bottom")
        specs.append((sp1, (30.0, 70.0), "x"))
        specs.append((sp2, (10.0, 100.0), "x"))
    else:
        raise ValueError(f"unknown plot kind {v.kind!r}")
    readings: List[TruthReading] = []
    scales: Dict[str, Dict[str, Any]] = {}
    for sp, reads, axis in specs:
        _draw_plot(page, sp, turn, labels, ocr)
        readings.extend(_plot_readings(sp, turn, reads, axis))
        scales[f"{sp.tag}.x"] = {"transform": sp.x.transform,
                                 "per_point": sp.x.per_point,
                                 "unit": sp.x.unit,
                                 "frame": turn.box(sp.frame)}
        scales[f"{sp.tag}.y"] = {"transform": sp.y.transform,
                                 "per_point": sp.y.per_point,
                                 "unit": sp.y.unit,
                                 "frame": turn.box(sp.frame)}
    vector_pdf = doc.tobytes()
    doc.close()
    pdf = (scanned(vector_pdf, A4, dpi=v.dpi, skew_deg=v.skew_deg,
                   jpeg=v.jpeg, seed=v.seed, rotate270=v.rotate270)
           if v.raster else vector_pdf)
    return ScaleFixture(name=f"plot/{v.name}", kind="plot", pdf=pdf,
                        raster=v.raster, readings=readings, labels=labels,
                        scales=scales, skew_deg=turn.deg)


def plot_variants() -> List[PlotVariant]:
    V = PlotVariant
    out = []
    for kind in ("grading", "linear_ticks", "dotted", "reversed_y", "log_y",
                 "two_charts", "broken"):
        out.append(V(f"{kind}_vector", kind))
        out.append(V(f"{kind}_scan", kind, raster=True,
                     skew_deg={"grading": 0.3, "linear_ticks": -0.4,
                               "dotted": 0.2, "reversed_y": -0.6,
                               "log_y": 0.5, "two_charts": -0.25,
                               "broken": 0.45}[kind],
                     rotate270=(kind == "grading"),
                     seed=40 + len(out)))
    return out


# -- profiles ------------------------------------------------------------------

def station_text(v: float) -> str:
    """Feet along an alignment as a station: 1250 -> ``"12+50"``."""
    hundreds = int(v // 100)
    return f"{hundreds}+{int(round(v - 100 * hundreds)):02d}"


def build_profile(raster: bool = False, skew_deg: float = 0.0,
                  seed: int = 61) -> ScaleFixture:
    """A profile at 5:1 vertical exaggeration with a ground line and a boring.

    Stations 0+00 to 5+00 at 1 in = 50 ft across, elevations 90 to 130 ft at
    1 in = 10 ft up — separate x and y scales, which a single isotropic
    factor cannot express.
    """
    import fitz
    W, H = TABLOID_LANDSCAPE
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    turn = _Turn(skew_deg if raster else 0.0, W / 2.0, H / 2.0)
    x0, x1 = 200.0, 200.0 + 500.0 * 72.0 / 50.0          # 720 pt
    y_bot, y_top = 600.0, 600.0 - 40.0 * 72.0 / 10.0     # 288 pt
    X = Axis("linear", 0.0, 500.0, x0, x1,
             major=[0.0, 100.0, 200.0, 300.0, 400.0, 500.0],
             minor=[50.0, 150.0, 250.0, 350.0, 450.0], fmt=station_text,
             name="station", unit="ft")
    Y = Axis("linear", 90.0, 130.0, y_bot, y_top,
             major=[90.0, 100.0, 110.0, 120.0, 130.0], minor=[95.0, 105.0,
                                                              115.0, 125.0],
             fmt=lambda e: f"{e:g}", name="elevation", unit="ft")

    def ground(s: float) -> float:
        return 112.0 + 7.0 * math.sin(s / 75.0) + 0.012 * s

    spec = _PlotSpec(frame=(x0, y_top, x1, y_bot), x=X, y=Y, grid="full",
                     curves=[ground], curve_domain=(0.0, 500.0), tag="profile",
                     label_size=8.5)
    labels: List[TruthLabel] = []
    ocr: List[Tuple[str, BBox]] = []
    page.insert_text((x0, y_top - 40), "PROFILE ALONG CENTERLINE", fontsize=12,
                     fontname="hebo")
    page.insert_text((x0, y_bot + 45),
                     "HORIZ. 1\" = 50'   VERT. 1\" = 10'", fontsize=8)
    _draw_plot(page, spec, turn, labels, ocr)
    # A boring stick at station 2+50 from the ground to elevation 95.
    bs = 250.0
    bx = X.pos(bs)
    gtop = ground(bs)
    page.draw_rect(fitz.Rect(bx - 4, Y.pos(gtop), bx + 4, Y.pos(95.0)),
                   color=(0, 0, 0), width=0.7)
    contacts = (gtop - 7.5, gtop - 13.0)
    readings: List[TruthReading] = []
    region = turn.box(spec.frame)
    for el in contacts:
        page.draw_line((bx - 4, Y.pos(el)), (bx + 4, Y.pos(el)), width=0.9)
        readings.append(TruthReading(
            kind="line", quantity="profile.y", value=el,
            at_pt=turn(bx, Y.pos(el)),
            box_pt=turn.box((bx - 3, Y.pos(el) - 1, bx + 3, Y.pos(el) + 1)),
            region=turn.box((bx - 4.5, Y.pos(gtop), bx + 4.5, Y.pos(95.0))),
            tag="boring_contact"))
    page.insert_text((bx - 8, Y.pos(gtop) - 8), "B-2", fontsize=8)
    for s in (60.0, 140.0, 310.0, 420.0, 470.0):
        g = ground(s)
        px, py = X.pos(s), Y.pos(g)
        readings.append(TruthReading(
            kind="curve", quantity="profile.y", value=g,
            at={"profile.x": s}, at_pt=turn(px, py),
            box_pt=turn.box((px - 4, py - 4, px + 4, py + 4)), region=region,
            tag="ground"))
    vector_pdf = doc.tobytes()
    doc.close()
    pdf = (scanned(vector_pdf, (W, H), skew_deg=skew_deg, seed=seed)
           if raster else vector_pdf)
    return ScaleFixture(
        name=f"profile/{'scan' if raster else 'vector'}", kind="profile",
        pdf=pdf, raster=raster, readings=readings, labels=labels,
        skew_deg=turn.deg,
        scales={"profile.x": {"per_point": X.per_point, "unit": "ft"},
                "profile.y": {"per_point": Y.per_point, "unit": "ft"}},
        notes=["vertical exaggeration 5:1"])


# -- chart families ---------------------------------------------------------------

def build_chart_family(kind: str = "semilog", raster: bool = True,
                       skew_deg: float = 0.25, seed: int = 71
                       ) -> ScaleFixture:
    """A design chart: curves labelled by a parameter on log axes.

    ``semilog``: log x 1-1000, linear y 0-1, curves for 0.2 / 0.4 / 0.6.
    ``loglog``: log x 1-100, log y 0.1-100, power curves for 0.5 / 1 / 2.
    Most reference charts are images (71 % of catalogued figure pages), so
    the default is a scan.
    """
    import fitz
    W, H = LETTER
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    turn = _Turn(skew_deg if raster else 0.0, W / 2.0, H / 2.0)
    frame = (130.0, 200.0, 500.0, 560.0)
    if kind == "semilog":
        maj, mino = _log_lines(1.0, 1000.0)
        X = Axis("log10", 1.0, 1000.0, frame[0], frame[2], major=maj,
                 minor=mino, name="x", unit=None)
        Y = Axis("linear", 0.0, 1.0, frame[3], frame[1],
                 major=[k / 10.0 for k in range(11)],
                 fmt=lambda t: f"{t:.1f}", name="y", unit=None)
        params = (0.2, 0.4, 0.6)
        curves = [(lambda p: (lambda x: 0.08 + p * math.log10(x) / 2.2))(p)
                  for p in params]
        reads = (3.0, 20.0, 150.0)
    elif kind == "loglog":
        mx, nx = _log_lines(1.0, 100.0)
        my, ny = _log_lines(0.1, 100.0)
        X = Axis("log10", 1.0, 100.0, frame[0], frame[2], major=mx, minor=nx,
                 name="x", unit=None)
        Y = Axis("log10", 0.1, 100.0, frame[3], frame[1], major=my, minor=ny,
                 name="y", unit=None)
        params = (0.5, 1.0, 2.0)
        curves = [(lambda p: (lambda x: 0.3 * p * x ** 0.75))(p)
                  for p in params]
        reads = (2.0, 8.0, 40.0)
    else:
        raise ValueError(kind)
    spec = _PlotSpec(frame=frame, x=X, y=Y, grid="full", curves=curves,
                     curve_labels=[f"{p:g}" for p in params], tag="chart")
    labels: List[TruthLabel] = []
    ocr: List[Tuple[str, BBox]] = []
    page.insert_text((150, 150), f"FIGURE 7-1. DESIGN CHART ({kind})",
                     fontsize=10)
    _draw_plot(page, spec, turn, labels, ocr)
    readings = _plot_readings(spec, turn, reads, "x")
    vector_pdf = doc.tobytes()
    doc.close()
    pdf = (scanned(vector_pdf, (W, H), skew_deg=skew_deg, seed=seed)
           if raster else vector_pdf)
    return ScaleFixture(name=f"chart/{kind}_{'scan' if raster else 'vector'}",
                        kind="chart", pdf=pdf, raster=raster,
                        readings=readings, labels=labels, skew_deg=turn.deg,
                        scales={"chart.x": {"transform": X.transform},
                                "chart.y": {"transform": Y.transform}})


# -- plans ----------------------------------------------------------------------

@dataclass(frozen=True)
class PlanVariant:
    name: str
    bar: Optional[str] = "blocks"       # blocks | ticks | None
    note: bool = True
    stored: bool = False
    ne_grid: bool = False
    replot: float = 1.0                 # 0.5 = printed at half size
    raster: bool = False
    skew_deg: float = 0.0
    seed: int = 81


#: The plan's drawn scale: 1 in = 20 ft at full size.
PLAN_FT_PER_INCH = 20.0

#: Borings at known ground coordinates (easting, northing), feet.
PLAN_BORINGS = (("B-1", 1040.0, 2180.0), ("B-2", 1135.0, 2165.0),
                ("B-3", 1092.0, 2090.0), ("B-4", 1210.0, 2110.0),
                ("B-5", 1168.0, 2195.0))


def build_plan(v: PlanVariant) -> ScaleFixture:
    """A site plan with borings at known ground coordinates and its scales.

    ``replot=0.5`` draws everything at half size on the same sheet, as a
    re-plot onto smaller paper does: the printed note still says 1 in = 20 ft
    and is now wrong; the graphic bar shrank with the drawing and is right.
    """
    import fitz
    W, H = TABLOID_LANDSCAPE
    doc = fitz.open()
    page = doc.new_page(width=W, height=H)
    turn = _Turn(v.skew_deg if v.raster else 0.0, W / 2.0, H / 2.0)
    f = float(v.replot)
    ft_per_pt = PLAN_FT_PER_INCH / 72.0 / f       # ground feet per page point
    origin = (150.0, 680.0)                       # page point of (E0, N0)
    E0, N0 = 1000.0, 2050.0

    def P(e: float, n: float) -> Point:
        return (origin[0] + (e - E0) / ft_per_pt,
                origin[1] - (n - N0) / ft_per_pt)

    sh = page.new_shape()
    # a building outline and a road, for some drawing on the sheet
    bld = [P(1060, 2120), P(1180, 2120), P(1180, 2170), P(1060, 2170),
           P(1060, 2120)]
    sh.draw_polyline(bld)
    sh.draw_line(P(1000, 2075), P(1250, 2075))
    sh.draw_line(P(1000, 2068), P(1250, 2068))
    sh.finish(color=(0, 0, 0), width=0.8)
    sh.commit()
    readings: List[TruthReading] = []
    labels: List[TruthLabel] = []
    pts = {}
    for name, e, n in PLAN_BORINGS:
        cx, cy = P(e, n)
        r = 4.0 * f
        page.draw_circle((cx, cy), r, color=(0, 0, 0), fill=(0, 0, 0))
        page.insert_text((cx + 6 * f, cy - 5 * f), name, fontsize=max(4.0, 8 * f))
        pts[name] = (cx, cy, e, n)
    region = turn.box((0, 0, W, H))
    names = [b[0] for b in PLAN_BORINGS]
    for a, b in zip(names, names[1:] + names[:1]):
        ax, ay, ae, an = pts[a]
        bx, by, be, bn = pts[b]
        d = math.hypot(be - ae, bn - an)
        readings.append(TruthReading(
            kind="distance", quantity="distance", value=d, at_pt=turn(ax, ay),
            box_pt=turn.box((ax - 5 * f, ay - 5 * f, ax + 5 * f, ay + 5 * f)),
            to_pt=turn(bx, by),
            to_box_pt=turn.box((bx - 5 * f, by - 5 * f, bx + 5 * f, by + 5 * f)),
            region=region, tag=f"{a}-{b}"))
    if v.ne_grid:
        for name, (cx, cy, e, n) in pts.items():
            readings.append(TruthReading(
                kind="point", quantity="point", value=None,
                values={"easting": e, "northing": n}, at_pt=turn(cx, cy),
                box_pt=turn.box((cx - 5 * f, cy - 5 * f, cx + 5 * f,
                                 cy + 5 * f)), region=region,
                tag=f"{name}.coords"))
        # border ticks with coordinate labels every 50 ft
        fs = 7.0
        top_y = P(0, 2215)[1]
        left_x = P(990, 0)[0]
        for e in range(1000, 1251, 50):
            x, _ = P(e, 0)
            page.draw_line((x, top_y), (x, top_y + 6), width=0.6)
            text = f"E {e:,}"
            tw = _text_width(text, fs)
            page.insert_text((x - tw / 2, top_y - 3), text, fontsize=fs)
            box = _text_ink_box(x - tw / 2, top_y - 3, text, fs)
            labels.append(TruthLabel(text, float(e), turn.box(box), "easting"))
        for n in range(2050, 2201, 50):
            _, y = P(0, n)
            page.draw_line((left_x, y), (left_x + 6, y), width=0.6)
            text = f"N {n:,}"
            tw = _text_width(text, fs)
            base = y + DIGIT_HEIGHT * fs / 2
            page.insert_text((left_x - 4 - tw, base), text, fontsize=fs)
            box = _text_ink_box(left_x - 4 - tw, base, text, fs)
            labels.append(TruthLabel(text, float(n), turn.box(box), "northing"))
    # the graphic bar: 0 10 20 40 FEET, drawn at the drawing's own scale
    bar_x0, bar_y = 900.0, 720.0
    ticks = (0.0, 10.0, 20.0, 40.0)
    if v.bar:
        xs = [bar_x0 + t / ft_per_pt for t in ticks]
        if v.bar == "blocks":
            for i, (a, b) in enumerate(zip(xs, xs[1:])):
                page.draw_rect(fitz.Rect(a, bar_y, b, bar_y + 5.0),
                               color=(0, 0, 0),
                               fill=(0, 0, 0) if i % 2 == 0 else None,
                               width=0.6)
        else:
            page.draw_line((xs[0], bar_y + 5), (xs[-1], bar_y + 5), width=0.8)
            for x in xs:
                page.draw_line((x, bar_y - 1), (x, bar_y + 5), width=0.8)
        fs = 8.0
        for t, x in zip(ticks, xs):
            text = f"{t:g}"
            tw = _text_width(text, fs)
            page.insert_text((x - tw / 2, bar_y - 4), text, fontsize=fs)
            box = _text_ink_box(x - tw / 2, bar_y - 4, text, fs)
            labels.append(TruthLabel(text, t, turn.box(box), "bar"))
        page.insert_text((xs[-1] + 8, bar_y + 5), "FEET", fontsize=8)
    if v.note:
        page.insert_text((900.0, 760.0), "SCALE: 1\" = 20'", fontsize=9)
    page.insert_text((80, 40), "SITE PLAN - BORING LOCATIONS", fontsize=14,
                     fontname="hebo")
    if v.stored:
        x0, y0, x1, y1 = (36.0, 36.0, W - 36.0, H - 36.0)
        c = ft_per_pt
        doc.xref_set_key(page.xref, "VP",
                         f"[ << /Type /Viewport /Name (Plan) /BBox [ {x0:g} "
                         f"{H - y1:g} {x1:g} {H - y0:g} ] /Measure << /Type "
                         f"/Measure /Subtype /RL /R (1 in = "
                         f"{PLAN_FT_PER_INCH / f:g} ft) /X [ << /Type "
                         f"/NumberFormat /U (ft) /C {c:.10g} /D 100 >> ] "
                         f"/D [ << /U (ft) /C 1 /D 100 >> ] >> >> ]")
    vector_pdf = doc.tobytes()
    doc.close()
    pdf = (scanned(vector_pdf, (W, H), skew_deg=v.skew_deg, seed=v.seed)
           if v.raster else vector_pdf)
    return ScaleFixture(
        name=f"plan/{v.name}", kind="plan", pdf=pdf, raster=v.raster,
        readings=readings, labels=labels, skew_deg=turn.deg,
        scales={"distance": {"per_point": ft_per_pt, "unit": "ft",
                             "stated_per_point": PLAN_FT_PER_INCH / 72.0,
                             "winner": ("bar" if v.bar else
                                        "stored" if v.stored else "note")}})


def plan_variants() -> List[PlanVariant]:
    V = PlanVariant
    return [
        V("note_and_blocks"),
        V("ticks_bar_only", bar="ticks", note=False),
        V("stored_vp", bar=None, note=False, stored=True),
        V("ne_grid", ne_grid=True),
        V("replot_half", replot=0.5),
        V("scan_blocks", raster=True, skew_deg=0.3),
        V("scan_replot_half", replot=0.5, raster=True, skew_deg=-0.2,
          seed=82),
    ]


def all_fixtures() -> List[ScaleFixture]:
    """Every fixture variant the harness measures."""
    out: List[ScaleFixture] = [build_log(v) for v in log_variants()]
    out.append(build_pit_sketch(raster=True))
    out.append(build_pit_sketch(raster=False))
    out.extend(build_plot(v) for v in plot_variants())
    out.append(build_profile(raster=False))
    out.append(build_profile(raster=True, skew_deg=0.35))
    out.append(build_chart_family("semilog", raster=True))
    out.append(build_chart_family("loglog", raster=True, skew_deg=-0.3,
                                  seed=72))
    out.append(build_chart_family("semilog", raster=False))
    out.extend(build_plan(v) for v in plan_variants())
    return out


__all__ = [
    "ScaleFixture", "TruthReading", "TruthLabel", "LogVariant",
    "PlotVariant", "PlanVariant", "Axis", "OcrLines", "build_log",
    "build_pit_sketch", "build_plot", "build_profile", "build_chart_family",
    "build_plan", "log_variants", "plot_variants", "plan_variants",
    "all_fixtures", "scanned", "station_text", "PLAN_BORINGS",
    "PLAN_FT_PER_INCH", "A4", "LETTER", "TABLOID_LANDSCAPE",
]
