"""The PyMuPDF settings planlens reads text and tables with, on 1.x and 2.x.

PyMuPDF 2.0 (2026-10-19) made its layout analyser and ``pymupdf4llm`` hard
dependencies and imports both inside ``import pymupdf``. That changes two
things no caller asked for (the app's BACKLOG INF-17):

* **Character boxes.** ``pymupdf4llm`` switches PyMuPDF's ascender/descender
  ("quad") corrections OFF, process-wide, as it is imported
  (``TOOLS.unset_quad_corrections(True)``). A character's box then hugs its
  glyph's ink instead of spanning the line: a hyphen or a full stop floats
  mid-line on its own and a space has no width. A ruled table cell printed
  "A-1" read "A1\\n-", "4.75" read "475\\n." and "3 in" read "3in". 1.x leaves
  the corrections on.
* **Which tables are found.** The layout analyser (an ONNX model) is active,
  so ``find_tables()``'s default ``use_layout=True`` keeps only the ruled
  tables the model also boxed and builds tables of its own from the model's
  boxes: two ruled tables printed side by side came back as one.

:func:`line_boxes` puts the corrections back for the duration of a read and
restores what it found, so a ``pymupdf4llm`` user in the same process keeps
its setting. :func:`find_tables` reads the ruling lines alone, passing
``use_layout=False`` where the installed PyMuPDF knows the option (a PyMuPDF
without it has no layout gate either). On 1.x both change nothing, so 1.x and
2.x read the same cells and the same boxes.

PyMuPDF keeps these switches as process globals and does not support threads,
while the app reads documents on parallel tool threads. So :func:`line_boxes`
also lets ONE planlens read run at a time in the process (a nested read on the
same thread shares it). Before it did, two table reads on two threads left
the small-glyph-heights switch on for the process (``find_tables`` flips it
and puts back what it found, and the two save-and-restores interleaved: every
later line box came back a font size high) and left ``sys.stdout`` on
``extract_tables``' dead buffer.
"""

from __future__ import annotations

import contextlib
import inspect
import threading
from typing import Any, Iterator, Optional

__all__ = ["line_boxes", "find_tables"]

_lock = threading.RLock()
_depth = 0
_saved: Optional[bool] = None
_use_layout_kw: Optional[bool] = None


@contextlib.contextmanager
def line_boxes() -> Iterator[None]:
    """Read with the ascender/descender corrections ON, as PyMuPDF 1.x does,
    and with no other planlens read in progress on another thread.

    Wrap every call that turns a page's text into boxes: ``get_text("dict")``
    and ``"words"``, and ``find_tables`` with the ``extract()`` of what it
    found.
    """
    import fitz
    global _depth, _saved
    tools = fitz.TOOLS
    with _lock:
        if _depth == 0:
            _saved = bool(tools.unset_quad_corrections())
            if _saved:
                tools.unset_quad_corrections(False)
        _depth += 1
        try:
            yield
        finally:
            _depth -= 1
            if _depth == 0 and _saved:
                tools.unset_quad_corrections(True)


def _knows_use_layout(page: Any) -> bool:
    global _use_layout_kw
    if _use_layout_kw is None:
        import fitz
        # ``Page.find_tables`` is a ``**kwargs`` wrapper; the options are on
        # the module function it calls.
        fn = getattr(getattr(fitz, "table", None), "find_tables", None)
        try:
            params = inspect.signature(fn or page.find_tables).parameters
            _use_layout_kw = "use_layout" in params
        except (TypeError, ValueError):
            _use_layout_kw = False
    return _use_layout_kw


def find_tables(page: Any) -> Any:
    """``page.find_tables()`` on the ruling lines and text alone.

    Call it inside :func:`line_boxes`, with the ``extract()`` of each table,
    so the cells are read with the boxes the tables were found with.
    """
    if _knows_use_layout(page):
        return page.find_tables(use_layout=False)
    return page.find_tables()
