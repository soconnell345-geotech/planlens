"""planlens.document — read a whole AEC document the way a reviewer needs to.

A design or construction document is rarely one sheet. A submittal can run from
a transmittal letter through drawing sheets, calculations, boring logs and
program printouts to a reviewer's marked-up comments. This subpackage turns any
PDF into data an LLM can navigate and cite:

- a **page map** — what kind of page each one is, with the evidence
  (:meth:`Document.page_map`);
- **text with exact location** — lines with true reading direction, font and
  size, grouped into blocks (:meth:`Document.page`);
- **tables** as rows and columns (``PageContent.tables``);
- **review markups** — comments, callouts, clouds, arrows, stamps — with
  author, date and the exact spot each one points at (:meth:`Document.markups`);
- **hidden CAD text** — the real characters AutoCAD stores behind stroked SHX
  lettering;
- **search** across all of it (:meth:`Document.search`);
- an optional **Azure Document Intelligence** text source for scanned pages,
  paragraph roles and header-marked tables — the caller runs the paid
  analysis, planlens only reads the result (:class:`AzureLayout`).

Every coordinate is in the displayed-page frame described in
:mod:`planlens.document.frame`. Drawing geometry (lines, arrows, dimensions)
lives in :mod:`planlens.ir`; the two are joined with
:func:`~planlens.document.frame.to_ir_point` / ``from_ir_point``.

Quick start::

    from planlens.document import open_document

    with open_document("submittal.pdf") as doc:
        for row in doc.page_map():
            print(row.page, row.kind, row.label, row.heading)
        hits = doc.search("raker load")
        page = doc.page(hits["hits"][0]["page"])
        comments = doc.markups(author="Reviewer A")
"""

from planlens.document.azure_di import (
    AzureLayout,
    pages_needing_ocr,
    pages_to_azure_range,
)
from planlens.document.document import Document, open_document, parse_pages
from planlens.document.model import (
    PAGE_KINDS,
    SOURCE_AZURE_DI,
    SOURCE_CAD_HIDDEN,
    SOURCE_OCR,
    SOURCE_PDF_TEXT,
    Markup,
    PageContent,
    PageSummary,
    Table,
    TextBlock,
    TextLine,
)

__all__ = [
    "AzureLayout",
    "pages_needing_ocr",
    "pages_to_azure_range",
    "Document",
    "open_document",
    "parse_pages",
    "PageContent",
    "PageSummary",
    "TextLine",
    "TextBlock",
    "Table",
    "Markup",
    "PAGE_KINDS",
    "SOURCE_PDF_TEXT",
    "SOURCE_CAD_HIDDEN",
    "SOURCE_OCR",
    "SOURCE_AZURE_DI",
]
