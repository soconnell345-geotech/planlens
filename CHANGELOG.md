# Changelog

## Unreleased

- **Forgiving search.** `Document.search(fuzzy=True, min_score=...)` matches
  approximately over the same candidates the exact search uses — lines, hidden
  CAD text, markup comments — so a term is still found in text read optically
  or recovered from stroked lettering with a letter wrong. Every hit carries a
  `score` (0-100) and the `source` that read it, best score first; exact search
  is unchanged. `search_document` gains `fuzzy` / `min_score`, and an exact
  search that finds nothing now says to try `fuzzy: true`. Needs the new
  optional extra `planlens[text]` (`rapidfuzz`); without it the call names the
  extra to install, and `fuzzy_search_available()` answers before it is asked.
- **The numbers the document SAYS.** New `planlens.document.quantities` and
  `Document.quantities(pages, kinds, units)`: every value-with-unit in the
  text, hidden CAD strings and reviewers' comments — `7'-6"`, `40-foot`,
  `6300 mm`, `21 degrees`, `2,500 psf`, `120 pcf`, `18 kN/m³`, `150 kPa`,
  `50 kN`, `EL. 1684`, `STA 10+50`, `2H:1V`, `1%`, `20 to 35 ft` — each with
  its kind, its raw wording, any qualifier (`approximately`, `minimum`,
  `typ.`, `±`), its page, line ids and box in the displayed frame. So what the
  report CLAIMS can be set beside what `planlens.ir` MEASURES. A bare number
  is never a mention, a range is one mention with `value_to`, and nothing is
  converted; `units_known` says whether `planlens.ir.measure` can convert it.
  New tool `find_quantities`.
- That extractor stayed native after a measured bake-off against quantulum3 on
  40 invented sentences and 40 real ones (precision/recall 0.97/0.97 and
  1.00/0.52 against 0.62/0.61 and 0.09/0.35). The decisive number: of the ten
  mentions quantulum3 found that the regex did not, all ten were wrong —
  including the V of `2H:1V` read as volts and six ranges replaced by their
  midpoint, a value stated nowhere in the document. It is not a dependency.
- The default `min_score` of 80 is measured, not chosen: real drawing callouts
  from a submittal's sheets, each corrupted by a substituted letter, a dropped
  letter and a transposition, against words confirmed absent from the same
  document. The measurement also found that `rapidfuzz`'s partial ratio slides
  whichever string is shorter, so a one-character line scored 100 against any
  query holding that character — a candidate shorter than the query is now
  scored whole. See `planlens/document/DESIGN.md`, "Forgiving search".

- **`planlens.document.scale`** — the measurement calibration a PDF already
  stores is now read, so a scale need not be inferred from a title block. A
  page's `/VP` viewports (ISO 32000-1 §12.9) and the `/Measure` dictionary on
  each dimension markup give the ratio (`1 in = 20 ft`), the real-world units
  per PDF point, the distance and area units, and the region each governs —
  in the displayed frame like every other coordinate. Georeferenced (`/GEO`)
  measures are detected and reported, not parsed.
- `PageSummary.viewports` and a one-line `scale` on every page-map row
  (`1 in = 20 ft [viewport]`), kept separate from `scales`, which is what the
  page's text says. `Markup.measure` reports a dimension's scale, the value its
  comment STATES and the length its vertex path DERIVES, so the two can be seen
  to agree; the path is measured unrotated, so a page rotation cannot change a
  length.
- `scale.to_quantity` bridges to `planlens.ir.measure`: a calibrated viewport
  yields a `Quantity` in feet or metres at confidence 1.0 whose basis names
  `pdf_viewport` or `measurement_markup`; without one the value stays in points
  with `scale_known=False`.
- A viewport being present is not a scale. The 1:1 default a real sheet was
  found to store reads as `1:1 (uncalibrated default)` and is not calibrated,
  and a drawing sheet with no stored scale now says so in its `! look:` advice.
- New fixtures `build_synthetic_scaled_sheet_pdf` /
  `build_synthetic_uncalibrated_sheet_pdf` in `planlens.testing`.
- **Layers and fill survive the trip from CAD to PDF.** Every entity
  `from_pdf_vector` builds from a vector path now carries `layer` — the
  optional-content group AutoCAD writes per CAD layer — so a PDF-sourced IR
  slices by layer exactly as a DXF one does (`entities_on_layer`,
  `counts_by_layer`). Metadata publishes `n_layers` with the DXF leg's
  meaning plus an `ocgs` summary (name → default ON). A path in no group
  reports `None`, never `""` and never `"0"`; a group genuinely named `"0"`
  is kept verbatim, since PDF has no inheritance sentinel.
- Content on a layer the document HIDES is not ingested — MuPDF hides it as a
  viewer does — so the IR warns, naming the group, and
  `from_pdf_vector(include_hidden_layers=True)` reads it.
- Entities gain `filled` and `fill_color` (hex `#rrggbb`, the same spelling
  `color` uses; `None` when the entity is not painted): a painted shape (a
  boring symbol, a solid arrowhead, a hatch) can now say so. Measured on the
  ten-sheet corpus,
  the `closePath` flag is False on all 6,669 filled paths, so `filled` is the
  dependable "this is an area, not a line" signal. What an entity IS is
  unchanged — a filled triangle is still a closed 3-vertex `Polyline` — and
  the construct finders carry fill as EVIDENCE only (`arrowhead_filled`,
  `filled_terminator_ids`, `filled`); no threshold or score moved, and every
  published corpus figure is identical.
- `PageSummary.layers` puts the same layer names on every page-map row, capped
  at `LAYER_NAMES_ON_ROW` with the total alongside when cut. The page map now
  reads a page's paths once instead of twice, so the 260-page map got faster.
- `planlens.pdf`: `extract_colored_paths` returns `layer` / `filled` /
  `fill_color` per path, `discover_pdf_content` returns `ocgs`, and
  `layer_state` / `enable_all_layers` are public.

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
