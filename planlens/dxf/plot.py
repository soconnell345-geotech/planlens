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
from typing import Optional

#: The plot's margin, in millimetres.
MARGIN_MM = 10.0

#: The largest side of the plot's page, in millimetres (A0's long side): the
#: drawing is scaled to fit inside it.
MAX_SIDE_MM = 1189.0

#: What a plot is, in the words a result gives it.
PLOT_NOTE = ("a PDF plot of the DXF's model space made here (one page, page "
             "0; black on white, scaled to fit, paper-space layouts left "
             "out): its lettering is drawn as shapes, not text, so read the "
             "DXF's text from the DXF, and its scale is the fit to the page, "
             "not the drawing's")


class PlotUnavailable(ValueError):
    """The plot cannot be made in this install (a package the drawing
    add-on needs is missing): nothing is wrong with the DXF."""


def dxf_to_pdf(filepath: Optional[str] = None,
               content: Optional[bytes] = None,
               margin_mm: float = MARGIN_MM,
               max_side_mm: float = MAX_SIDE_MM) -> bytes:
    """The PDF bytes of a one-page plot of a DXF's model space.

    Give ``filepath`` or ``content`` (the file's bytes, ASCII or binary).
    ezdxf's recover mode reads it, so a file with small structural faults
    still plots. Raises ValueError when the model space has nothing to draw,
    and :class:`PlotUnavailable` (a ValueError) when this install cannot
    plot at all (ezdxf's drawing add-on needs Pillow).
    """
    from ezdxf import recover
    try:
        from ezdxf.addons.drawing import Frontend, RenderContext, layout
        from ezdxf.addons.drawing.config import (BackgroundPolicy,
                                                 Configuration)
        from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    except ImportError as exc:
        # ezdxf's drawing add-on imports Pillow, which planlens does not
        # require: say so, rather than call the file unreadable
        raise PlotUnavailable(
            "plotting a DXF needs the Pillow package (ezdxf's drawing "
            f"add-on imports it), which is not installed here ({exc})")

    if (filepath is None) == (content is None):
        raise ValueError("give filepath or content")
    if content is not None:
        doc, _auditor = recover.read(io.BytesIO(bytes(content)))
    else:
        doc, _auditor = recover.readfile(filepath)
    msp = doc.modelspace()
    if len(msp) == 0:
        raise ValueError("the DXF's model space is empty: there is nothing "
                         "to plot")
    backend = PyMuPdfBackend()
    config = Configuration(background_policy=BackgroundPolicy.WHITE)
    Frontend(RenderContext(doc), backend, config=config).draw_layout(
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


__all__ = ["MARGIN_MM", "MAX_SIDE_MM", "PLOT_NOTE", "PlotUnavailable",
           "dxf_to_pdf", "is_dxf"]
