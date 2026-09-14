# planlens.document — design notes

## Why this exists

planlens began as a drawing-sheet reader: lines, arrows, leaders, dimensions.
The goal it serves is broader — an LLM harness helping an engineer **review any
architecture / engineering / construction document**. A real design submittal
runs from a transmittal letter through drawing sheets, calculations, boring logs
and program printouts, and carries the review itself as PDF markups. An LLM
cannot review that from a single sheet's geometry, and it cannot reliably read
geometry from pixels. This subpackage turns the whole document into located,
attributed data; `planlens.ir` stays the geometry layer for drawing sheets.

## What it is

```
document/
  frame.py        the one coordinate contract + conversions to the IR frame
  model.py        TextLine / TextBlock / Table / Markup / PageSummary / PageContent
  pdf_text.py     the PDF text layer (default text source)
  annotations.py  review markups + hidden CAD text
  tables.py       find_tables wrapper
  classify.py     coarse page kinds with evidence
  document.py     Document: page_map, page, search, markups, text
  azure_di.py     Azure Document Intelligence result -> text source (optional)
```

## The frame contract

Every coordinate is **PDF points in the page's displayed orientation**: origin
top-left, y down, `/Rotate` applied — what a viewer shows, what
`page.get_pixmap()` renders, and what `planlens.ir.render.render_region(frame="page")`
expects. A box from this package can be rendered and shown to a vision model
with no conversion.

PyMuPDF's ordinary `get_text`, `annot.rect` and `annot.vertices` are in the
UNROTATED page space (verified: identical numbers on /Rotate 0/90/270 while
`page.rect` swaps). Annotation geometry is converted with `page.rotation_matrix`
(`frame.to_display_*`). Text is read through a display list instead, which MuPDF
runs in the displayed orientation — verified on /Rotate 0/90/180/270 and an
offset crop box: boxes identical to rotating ordinary output, rendered ink inside
every box. **Never apply the rotation matrix to display-list text**; doing so
double-rotates (a regression caught by the tests on the rotated fixture sheet).

`DrawingIR` uses a different frame (unrotated geometry y-flipped with the
rotated height). `frame.to_ir_point` / `from_ir_point` convert, and are pinned
against `ir_to_page_point` on a rotated page.

## Text

- **Lines** carry text, box, reading direction (degrees CCW as displayed),
  font, size, bold/italic, colour and block. Directions within 0.5 deg of a
  cardinal snap to it (CAD plots write near-horizontal text at 359.9).
- **Annotations are excluded from the text layer.** Ordinary `get_text` reads
  the text drawn by annotation appearances, so a reviewer's FreeText comment
  comes back as drawing text — verified on a real submittal, where deleting a
  callout removed its words from `get_text()`. Markup text reaches the reader
  only as attributed `Markup` objects.
- Characters from fonts with no Unicode map (U+FFFD) are counted and warned
  about; the string should not be trusted on those pages.
- Order is content-stream order: reading order for word-processor output,
  drafting order for CAD plots. No reading-order reconstruction yet.

## Markups

A `Markup` is what a person placed: kind (FreeText, Polygon, Line, Stamp…),
Bluebeam/Acrobat intent and subject, author, ISO dates, comment text, box,
vertices, colour. Relationships come only from explicit PDF data:

| field | source |
|---|---|
| `points_at` | FreeText callout: the start of `/CL` (PDF spec: the end at the target). Line/PolyLine: the end with a line ending, when exactly one end has one. Verified by rendering real Bluebeam markups: callout starts sit on the commented text, the arrowhead end on its target. Never inferred from proximity. |
| `points_to_markup` | the smallest other markup whose box CONTAINS `points_at`, skipping the markup's own /IRT companions (Bluebeam ties the cloud a responder draws around the original comment to the response by /IRT; the tip lands inside that cloud too). On the real submittal this links contractor replies to reviewer comments. |
| `in_reply_to` / `replies` | the PDF `/IRT` link and its inverse. |
| `appearance_text` | text the annotation's OWN appearance draws (`annot.get_text`), kept when it differs from the comment field: review-stamp wording, what a snapshot stamp copied. Per-annotation, so overlapping markups cannot swap text (an earlier box-containment version did). Capped at 400 chars. |

## Hidden CAD text

AutoCAD plots SHX lettering as strokes (no text layer) and adds an invisible
Square annotation titled `AutoCAD SHX Text` holding the real string. These
become `TextLine`s with `source="cad_hidden_text"` and `rotation=None` (the
annotation records a box, never a direction; the payload says `"unknown"`).
Strings also present in the text layer at an overlapping box are dropped.
Measured: 320 on a real submittal's drawing sheets; 66 on one of the ten
Mecklenburg validation sheets, none on the other nine — so it complements OCR,
it does not replace it.

## Tables

`page.find_tables()` on the vector content. Rows are kept whole: PyMuPDF's
in-grid header guess is row 0, which on forms such as boring logs is a title
strip, so a header is reported only when found outside the grid (or marked by
Azure). Columns empty in every row are dropped and counted in `notes`. Cost is
the reason tables are page-level only: 30 s of the 36 s a full measurement pass
took over 260 pages.

## Page map

`classify.classify_page` from cheap measurements (text chars, CAD-text chars,
vector path count, image coverage, page area): `drawing_sheet` (larger than
tabloid with content), `scanned`, `figure`, `text`, `blank`, `mixed`. Coarse on
purpose; every row carries the numbers and the rule. `needs_ocr` is separate
from kind and is cleared when the page's text came from an optical source.
Measured: 3.8 s for 260 pages. Headings: an Azure title/sectionHeading block if
present, else the largest-font text-layer line.

## Text sources

`Document(text_source=...)` replaces the PDF text layer on the pages the source
covers. Protocol: `name`, `covers(index)`, `extract(page, index, words)` ->
`(lines, blocks, tables_or_None, stats, warnings)`. Annotations always come
from the PDF.

`AzureLayout` wraps a `prebuilt-layout` result. planlens never calls Azure or
imports an Azure package; the caller runs the analysis (Funhouse SDK, Azure SDK,
REST) and passes the dict, or passes the function to `AzureLayout.analyze`.
`pages_needing_ocr(doc)` picks the pages worth paying for.

- Keys: camelCase or snake_case; polygons flat, pairs or `{x, y}` points (the
  Funhouse SDK's own helper reads camelCase while its integration test asserts
  snake_case — both occur).
- Frame, per page: the Azure page size is matched against the displayed and the
  unrotated PDF page (inches exactly, pixels by aspect); a page matching neither
  is scaled to the displayed page with a warning.
- Page numbers: when a subset was analysed, the result is reconciled against
  the pages sent (document numbering or renumbered from 1); anything else is
  refused rather than placed on the wrong page.
- Line confidence is the minimum of its words' confidences.
- Tables: explicit `columnHeader` cells in row 0 become the header; merged-cell
  positions stay `None`; a table belongs to the page it starts on.

No live Azure result has been run through it yet; the tests use hand-built
results in the documented shape.

## Text first, eyes second — and the tools say when (2026-09-14)

The owner's gut check of the first build: the layer was undervaluing the
model's own vision. Measured on the real submittal, the three pages a reviewer
most needs eyes on told the model nothing of the kind — a scanned plan figure
with four markups came back as two footer lines (the "no text layer" warning
fires only at zero text); a boring log as 113 fragments in drafting order and a
mangled grid; a plan sheet as 162 labels. The extractor KNEW (page kind, image
coverage, sparse grid) and did not say.

Now every result that touches such a page says so. `advice.page_advice` turns
the signals into plain statements — scanned / figure / drawing_sheet kinds,
image-bearing mixed pages, undecodable glyphs, a table whose detected grid is
mostly empty (`Table.n_cells` / `n_empty_cells`, counted BEFORE empty columns
are dropped), low-confidence optical words, markups that point at a spot —
and the tool layer prints them as `! look:` lines with the host's own
instruction for how to look (`ReviewToolkit.vision_hint`). `open_document`
lists `pages_to_view`; `document_page_map` flags `look`; a text-search miss
names `pages_not_searchable_as_text` so a miss there is not read as absence.

`Document.render(page, bbox, marks)` gives the image itself, in the displayed
frame, pixel-capped (`DEFAULT_MAX_PIXELS`: a 300 dpi D-size render is 70 MP
and took a notebook driver down once); with marks it renders from a fresh copy
so the cached document's text is never polluted by the drawn labels. The
toolkit exposes it as `render_page` / `render_region` (PNG files) and
`ReviewToolkit.render` (bytes) for hosts that hand images to the model
directly. An image file opens as a one-page document (`source_kind="image"`,
converted to PDF by MuPDF), so a photographed drawing is reviewed like a scan.

## Not built yet

- RapidOCR (`planlens.ocr`) as a text source (it currently emits IR TextItems).
- The drawing tools (digitize / query / snip / search a drawing set) as
  planlens tools — the app's drawing adapter still owns them.
- Roles/headings from the PDF text layer; multi-column reading order; figure
  and caption detection; tables spanning pages.
