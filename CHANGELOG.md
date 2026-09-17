# Changelog

## Unreleased

- **What each page of a report IS, and the work items its pages make.** New
  `planlens.document.roles`: `page_roles(doc)` gives every page one of
  eighteen roles — narrative, figure, plan, profile, boring_log,
  test_pit_log, cpt_log, dcp_log, lab_test, field_test, calculation,
  appended_report, photos, divider, cover, letter, toc, other — with the
  evidence that decided it, and `document_items(doc)` groups those pages into
  what a reader takes in at once: one item per boring or test pit with its
  "Page 2 of 3" continuation sheets folded in, one per laboratory sheet or
  multi-page test, one per calculation printout, one for the narrative, one
  per report bound inside the report. Rules, not a trained model, over
  evidence the page map already produces: what an appendix tab STATES it
  holds, what a page's own largest type calls it, and the page's shape. A
  report bound into an appendix takes every one of its pages whatever they
  look like, and its extent is found from the appendix lettering it restarts
  and the outer document resumes. Served to a model as the tool
  `document_roles` (and over MCP with the rest). Measured against 4,147
  hand-labelled pages of 14 real geotechnical reports: overall page accuracy
  0.90, and on the five roles a reader depends on — precision/recall 0.97 /
  0.94 boring_log, 0.96 / 0.92 test_pit_log, 0.94 / 0.95 lab_test, 0.94 /
  0.93 narrative, 1.00 / 0.97 calculation. What it gets wrong is written down
  beside that: `dcp_log` recall is 0.24 on scanned forms, `other` is a
  residual rather than a class, and a page with no text at all cannot be
  placed by its title. New `planlens.testing.build_synthetic_report` builds a
  21-page report carrying every one of those page kinds, with the answers.
  See `planlens/document/DESIGN.md`, "Page roles and work items".
- **What the report says about itself**, for a model that is going to review
  it. `document_outline(doc)` reads the table of contents, the lists of
  figures, tables and appendices (each entry with its number, its title and
  the page number AS PRINTED), every divider page with its text, each figure
  page's caption and the narrative's section headings in order, and matches
  each entry to the page that carries it — or leaves it at `page = None`,
  because a page index that is probably wrong is worse than an honest gap.
  `page_ledger(doc, roles)` gives one line per page — page, kind, role,
  confidence, the rule that fired, heading, running header, printed page,
  segment, text characters, text reliability, whether Azure supplied the text
  and the work item — so a 729-page report can be taken in at about 100 kB
  before anything is opened. Both ride on the `document_roles` tool as
  `outline=true` and `ledger=true`. A role a page did NOT name itself, and
  took from its appendix tab, now carries a lower confidence
  (`INHERITED_CONFIDENCE`) and says so in its evidence, because that is
  exactly the row a reviewer should check.

- **A page whose text layer is there and WRONG now says so.** An
  analysis-program printout bound into a report often carries a font with no
  usable Unicode map: the extraction succeeds, returns thousands of
  characters, and a large share of them are U+FFFD. The count was already in
  the page's evidence and nothing read it — the page was not `needs_ocr`, it
  was not a scan, and its transcript went to a model as prose. Measured on a
  455-page report, 22 pages are like this, 14 of them 98% undecodable. New
  `PageSummary.text_reliable` and `unmapped_fraction`: when the layer is
  unreliable the page becomes `needs_ocr` (its words must be read off the
  picture, exactly as on a scan) and so joins `pages_needing_ocr()`,
  `read_document` advice says the layer is unreliable and not to quote the
  text tools on it, the page-map row carries `text_unreliable`, and
  `open_document` reports the pages under `pages_with_unreliable_text` —
  beside `pages_without_text_layer`, not inside it, because a page with
  nothing to read and a page with the wrong thing to read need different
  answers. The threshold `MAX_UNMAPPED_FRACTION` is 0.10, measured over five
  real documents: 30 pages carrying a stray undecodable glyph sit at 0.0007 to
  0.0064 and 22 pages with a broken encoding at 0.248 and above, and the floor
  sits in the empty span between them. New
  `planlens.testing.build_unmapped_text_pdf` builds such a page for tests. See
  `planlens/document/DESIGN.md`, "An unreliable text layer".
- **Fixed: a scanned appendix could be claimed as 69 duplicates of its first
  page.** The picture hash behind `duplicate_rule == "image"` read a 9x8 grid,
  and a laboratory appendix — one printed template, a diagonal DRAFT
  watermark, and the numbers that make each sheet itself a few percent of the
  ink — is below that grid's resolution: measured on a real 202-page report,
  1,260 of the 2,628 pairs its 73 different sheets make came within the
  threshold and the closest sat at 0 bits. An ingest that skipped duplicates
  would have dropped the whole appendix. The grid is now 17x16 (256 bits, 64
  hex characters), where no two of those sheets come within 5 bits and the
  appendix draws no claim at all. Normalising the ink first, so the watermark
  and the paper tone drop out, was tried and is measurably worse; resolution,
  not contrast, is what separates two filled-in copies of one form.
  `DUP_HASH_DISTANCE` stays 2, re-derived at the new width. The emptiness
  floor changed with it: the grey RANGE of the whole grid (`MIN_GRID_SPREAD`)
  is defeated by a single printed border, which at the finer grid made two
  appendix dividers the same picture, so a page must now carry
  `MIN_CONFIDENT_BITS` (8 of 256) bits set by real contrast — measured, the
  near-blank pages of five real documents score 9 or less and every page with
  a figure, a form or a scan of one scores 10 or more. Across those five
  documents (202, 455, 97, 94 and 260 pages) the rule now claims no image
  duplicate at all, and the same page placed twice is still caught at 0 bits.
  The finer grid costs nothing: hashing 260 pages measures 1.26 s either way,
  because the cost is the render and not the grid. See
  `planlens/document/DESIGN.md`, "Duplicates on scans".

## 0.4.0 — 2026-09-16

The take-the-document-at-its-word release: the scale, the CAD layers, the fill
and the numbers a file already carries are now read rather than inferred or
lost, search forgives a letter read wrong, a page bound in twice is caught even
on a scan, and the same tools serve any MCP host.

- **An MCP server.** New `planlens.mcp_server` (`python -m
  planlens.mcp_server`, or the `planlens-mcp` console script) serves the
  review tools over the Model Context Protocol, so any host — Claude Code,
  Claude Desktop, an editor, a LangChain or deepagents program through
  `langchain-mcp-adapters` — can discover and call them with no
  planlens-specific integration code. The tool list is GENERATED from
  `ReviewToolkit.specs("plain")`, one MCP tool per spec with the same name,
  description and input schema, so it cannot drift from the toolkit. Over the
  wire the pictures travel: any result naming an image file (`render_page`,
  `render_region`, `render_page_thumbnails`) carries the PNG as image content
  beside the JSON. A toolkit error becomes an MCP tool error with its message
  and hint intact, never a dropped connection. Flags: `--max-chars` to match
  the host's result-size limit, `--vision-hint` (default names this server's
  own render tools), `--root DIR` to confine reading to one directory tree
  (`../`, absolute paths and symlinks out are refused), `--http HOST:PORT` for
  streamable HTTP instead of stdio. The server authenticates nobody — the host
  decides who may run it, and a shared deployment needs authentication from
  the platform in front of it. Optional extra `planlens[mcp]` (the official
  SDK, pinned to `mcp>=2,<3`: v1 and v2 are different APIs). See the README,
  "Use from an MCP host", and `planlens/document/DESIGN.md`, "MCP".
- **Fixed: `open_document` could exceed the host's size limit.** The result
  reserved a flat 200 characters for the parts still to come, but the segment,
  bookmark and sheet-label rows were fitted with whatever room arithmetic said
  was left, and `fit_items` always returns one row even when that row is
  bigger than the budget. One long row therefore pushed the result past the
  limit, and `call_json` replaced the whole payload with "produced N
  characters, over the limit" — leaving the model no handle and nothing to
  continue from. Observed between two limits that both looked fine: 1,000 and
  1,500 characters passed, 1,200 did not. The fixed parts are now measured
  rather than guessed at (as `find_quantities` already did), every candidate
  row block is measured as the whole result and shortened until it fits, and a
  limit too small even for the map returns the handle with a `truncated` note
  naming `document_page_map` and `document_structure`.
- **Duplicate pages on scans.** `duplicate_of` used to read a page's text and
  path count, which is exactly what a scanned page has none of, so a sheet
  bound in twice went unnoticed. New `planlens.document.imagehash` hashes the
  page's picture instead — a 9x8 grayscale render in the displayed
  orientation, adjacent columns compared, 64 bits, numpy and PyMuPDF only —
  for the pages whose text cannot decide (`needs_ocr`, `scanned`, `figure`,
  `drawing_sheet`, or under 50 characters; never a blank page). Page-map rows
  now carry `duplicate_rule`, `"text"` or `"image"`. The threshold is measured
  on a real 260-page submittal and the ten public sheets, and the rule is
  deliberately narrow: it finds a page PLACED twice, not a page scanned twice,
  it never overrules a page's own words, and it withholds the hash from a page
  too flat to carry one. See `planlens/document/DESIGN.md`, "Duplicates on
  scans".
- **Forgiving search.** `Document.search(fuzzy=True, min_score=...)` matches
  approximately over the same candidates the exact search uses — lines, hidden
  CAD text, markup comments — so a term is still found in text read optically
  or recovered from stroked lettering with a letter wrong. Every hit carries a
  `score` (0-100) and the `source` that read it, best score first; exact search
  is unchanged. `search_document` gains `fuzzy` / `min_score`, and an exact
  search that finds nothing now says to try `fuzzy: true`. Runs on `rapidfuzz`,
  a core dependency (see the packaging note below), imported only when fuzzy
  matching is asked for; `fuzzy_search_available()` answers before it is asked,
  so a caller never advises a retry that would raise.
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
- **Packaging: `raster` and `text` are folded into core.** `pip install
  planlens` now brings `opencv-python-headless` and `rapidfuzz` with it, so
  raster/scanned-sheet tracing and forgiving search work out of the box —
  an install without them read a scanned sheet as an empty drawing and refused
  a search the tools advertise, which is a trap, not a saving. Both extras are
  KEPT as empty aliases, so `planlens[raster]` and `planlens[text]` still
  resolve and install the same thing as plain `planlens`. Nothing about the
  import cost changed: `import planlens` loads neither, and both are imported
  at the moment they are used. `ocr` stays optional (every rapidocr
  distribution hard-requires the GUI OpenCV build, which owns the same `cv2`
  namespace) and so does `mcp`.
- `ReviewToolkit` now serves TEN tools: `find_quantities` joined the nine of
  0.3.0, on the framework-neutral specs and over MCP alike.

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
