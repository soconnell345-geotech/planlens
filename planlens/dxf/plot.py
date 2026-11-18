"""A DXF's model space as a one-page PDF plot (BACKLOG DR-19).

A DXF has no pages, so the tools that look at a page (render, zoom, find a
symbol like this one, mark it up) cannot open one. This makes it a page the
way a plotter would: ezdxf's drawing add-on (``ezdxf`` is already a
dependency of planlens) draws the model space onto one PDF page, black on
white, scaled to fit, with a margin.

What the plot is and is not:

* the MODEL space only: paper-space layouts (title sheets, viewports) are
  not plotted;
* its lettering is drawn as filled shapes, not PDF text, so the plot has no
  text layer: the DXF's own text is read from the DXF (the host reads it as
  text), not from the plot;
* its scale is the fit to the page, not a drawing scale: a length measured
  on the plot is in PDF points of the plot, and the DXF's coordinates are
  the exact ones.
"""

from __future__ import annotations

import io
import os
import time
from typing import Optional

#: The plot's margin, in millimetres.
MARGIN_MM = 10.0

#: The largest side of the plot's page, in millimetres (A0's long side): the
#: drawing is scaled to fit inside it.
MAX_SIDE_MM = 1189.0

#: The bounds of a plot (review of DR-44). A plot is made while its
#: conversation's toolkit is locked, so it must end: measured 2026-11-18
#: with ezdxf 1.4.5 and PyMuPDF 2.1, 100,000 lines took 64 s and 390 MB,
#: a lettered note about 20-35 ms each in the PDF writer, and a 21 KB file
#: of blocks nested ten to a block draws ten times more per level.
#:
#: A DXF larger than this is not read (reading took about 0.8 s per MB).
MAX_FILE_MB = 16.0
#: At most this many entities are drawn, blocks expanded (an INSERT counts,
#: and so does each entity it brings).
MAX_ENTITIES = 50_000
#: Drawing and writing the PDF stop after this long. It is checked between
#: entities and between drawn shapes, so one very heavy entity overruns by
#: its own length; reading the file is bounded by :data:`MAX_FILE_MB` alone.
MAX_SECONDS = 30.0

#: What to do with a DXF that is not plotted.
NOT_PLOTTED = ("ask the user for a PDF plot of it, or read its text with "
               "read_document")

#: What a plot is, in the words a result gives it.
PLOT_NOTE = ("a PDF plot of the DXF's model space made here (one page, page "
             "0; black on white, scaled to fit, paper-space layouts left "
             "out): its lettering is drawn as shapes, not text, so read the "
             "DXF's text from the DXF, and its scale is the fit to the page, "
             "not the drawing's")


class PlotUnavailable(ValueError):
    """The plot cannot be made in this install (a package the drawing
    add-on needs is missing): nothing is wrong with the DXF."""


class PlotTooLarge(ValueError):
    """The DXF is past a bound of the plot (:data:`MAX_FILE_MB`,
    :data:`MAX_ENTITIES`, :data:`MAX_SECONDS`): it is not plotted here."""


def dxf_to_pdf(filepath: Optional[str] = None,
               content: Optional[bytes] = None,
               margin_mm: float = MARGIN_MM,
               max_side_mm: float = MAX_SIDE_MM,
               max_file_mb: Optional[float] = None,
               max_entities: Optional[int] = None,
               max_seconds: Optional[float] = None) -> bytes:
    """The PDF bytes of a one-page plot of a DXF's model space.

    Give ``filepath`` or ``content`` (the file's bytes, ASCII or binary).
    ezdxf's recover mode reads it, so a file with small structural faults
    still plots. Raises ValueError when the model space has nothing to draw,
    and :class:`PlotUnavailable` (a ValueError) when this install cannot
    plot at all (ezdxf's drawing add-on needs Pillow).

    The plot is bounded: :class:`PlotTooLarge` (a ValueError) for a file
    over ``max_file_mb``, a drawing of more than ``max_entities`` entities
    (blocks expanded), or one not drawn and written within ``max_seconds``
    (defaults :data:`MAX_FILE_MB`, :data:`MAX_ENTITIES`,
    :data:`MAX_SECONDS`, read at the call).
    """
    from ezdxf import recover
    try:
        from ezdxf.addons.drawing import Frontend, RenderContext, layout
        from ezdxf.addons.drawing.config import (BackgroundPolicy,
                                                 Configuration)
        from ezdxf.addons.drawing.pymupdf import (PyMuPdfBackend,
                                                  PyMuPdfRenderBackend)
    except ImportError as exc:
        # ezdxf's drawing add-on imports Pillow, which planlens does not
        # require: say so, rather than call the file unreadable
        raise PlotUnavailable(
            "plotting a DXF needs the Pillow package (ezdxf's drawing "
            f"add-on imports it), which is not installed here ({exc})")

    if (filepath is None) == (content is None):
        raise ValueError("give filepath or content")
    file_mb = float(MAX_FILE_MB if max_file_mb is None else max_file_mb)
    entities = int(MAX_ENTITIES if max_entities is None else max_entities)
    seconds = float(MAX_SECONDS if max_seconds is None else max_seconds)
    size = len(content) if content is not None else os.path.getsize(filepath)
    if size > file_mb * 1e6:
        raise PlotTooLarge(
            f"the DXF is {size / 1e6:.1f} MB, over the {file_mb:g} MB a "
            f"plot is made from here: {NOT_PLOTTED}")
    deadline = time.monotonic() + seconds

    def in_time() -> None:
        if time.monotonic() > deadline:
            raise PlotTooLarge(
                f"the DXF's plot took longer than the {seconds:g} s a plot "
                f"may take here: {NOT_PLOTTED}")

    class BoundedFrontend(Frontend):
        """Counts every entity drawn, blocks expanded, and stops past the
        bound or the deadline."""
        drawn = 0

        def draw_entity(self, entity, properties):
            self.drawn += 1
            if self.drawn > entities:
                raise PlotTooLarge(
                    f"the DXF's model space has more than {entities} "
                    "entities to draw (blocks expanded), over what a plot "
                    f"is made from here: {NOT_PLOTTED}")
            in_time()
            super().draw_entity(entity, properties)

    def timed(method):
        def run(self, *args, **kwargs):
            in_time()
            return method(self, *args, **kwargs)
        return run

    # the PDF writer is the slow part (lettering is drawn as filled
    # shapes): each shape it writes checks the deadline too
    render = type("BoundedRender", (PyMuPdfRenderBackend,), {
        name: timed(getattr(PyMuPdfRenderBackend, name))
        for name in ("draw_point", "draw_line", "draw_solid_lines",
                     "draw_path", "draw_filled_paths",
                     "draw_filled_polygon", "draw_image")})

    class BoundedBackend(PyMuPdfBackend):
        @staticmethod
        def make_backend(page, settings):
            return render(page, settings)

    if content is not None:
        doc, _auditor = recover.read(io.BytesIO(bytes(content)))
    else:
        doc, _auditor = recover.readfile(filepath)
    msp = doc.modelspace()
    if len(msp) == 0:
        raise ValueError("the DXF's model space is empty: there is nothing "
                         "to plot")
    in_time()
    backend = BoundedBackend()
    config = Configuration(background_policy=BackgroundPolicy.WHITE)
    BoundedFrontend(RenderContext(doc), backend, config=config).draw_layout(
        msp, finalize=True)
    page = layout.Page(0, 0, layout.Units.mm,
                       margins=layout.Margins.all(float(margin_mm)),
                       max_width=float(max_side_mm),
                       max_height=float(max_side_mm))
    return backend.get_pdf_bytes(page)


def is_dxf(name: object) -> bool:
    """Whether ``name`` (a file name or path) names a DXF, by its
    extension."""
    return str(name or "").strip().lower().endswith(".dxf")


__all__ = ["MARGIN_MM", "MAX_SIDE_MM", "MAX_FILE_MB", "MAX_ENTITIES",
           "MAX_SECONDS", "NOT_PLOTTED", "PLOT_NOTE", "PlotUnavailable",
           "PlotTooLarge", "dxf_to_pdf", "is_dxf"]
