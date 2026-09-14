# Changelog

## 0.3.0 — 2026-09-14

The whole-document release: planlens now reads any PDF (or image) the way a
reviewer needs it, not just a drawing sheet.

- **`planlens.document`** — `open_document` / `Document`: a page map (kinds
  `text` / `drawing_sheet` / `form` / `figure` / `scanned` / `blank` /
  `mixed`, each with the measurements behind the call); text lines with true
  reading direction and exact boxes; tables; review markups (author, date,
  what a comment says or shows, the exact point a callout or arrow aims at,
  reply links); the hidden text AutoCAD stores behind stroked SHX lettering;
  search across all of it. One coordinate frame: displayed-page points,
  top-left origin, `/Rotate` applied.
- **Structure** — running headers/footers, the page numbers printed on the
  pages, sheet references, divider pages, duplicates, and from them the
  document's segments (transmittal, drawing set, calc package, nested
  reports, appendices). Contact sheets of every page (`render_thumbnails`).
- **Vision loop** — every result that touches a page the text cannot
  represent says so (`! look:`) and says how to look; `Document.render`,
  `render_page`, `render_region`.
- **Azure Document Intelligence** as an optional text source
  (`AzureLayout`): planlens reads a `prebuilt-layout` result, never calls
  the service.
- **`planlens.tools.ReviewToolkit`** — nine framework-neutral LLM tools with
  Anthropic / OpenAI / plain specs; every result valid JSON inside a size
  limit, longer results paged losslessly through cursors.
- **Drawing-IR text fixes** — PDF text items carry their real rotation
  (they all said 0), text drawn by annotations is no longer ingested as
  drawing text, and AutoCAD hidden text can be ingested with
  `include_cad_hidden_text=True`. Published corpus figures unchanged.
- **Fixtures** — `planlens.testing.build_synthetic_review_document` and
  `build_synthetic_submittal`.

## 0.2.0 — 2026-09-11

- Phase 3.2 remediation of the construct finders through six verified
  rounds (signed arrow-direction attach, split-shaft pairing, tipless
  terminator split, end ownership by seat).
- DXF INSERT block explosion with layer-`0` inheritance; paper-space truth
  extraction.
- `planlens.ir.measure` (`Quantity`: unit-bearing derived numbers) and
  `planlens.ir.spatial` (point-pattern spacing conventions).
- `planlens.testing` shipped as public API.

## 0.1.0 — 2026-09-05

- First release: the drawing stack split out of GeotechStaffEngineer —
  `planlens.ir` (DXF / vector-PDF / raster ingest, slice queries, the
  leader / dimension / title-block / bubble / revision-cloud finders,
  `render_region`), `planlens.pdf`, `planlens.dxf`, `planlens.ocr`.
