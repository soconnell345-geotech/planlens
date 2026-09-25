# planlens

Review-ready data from architecture / engineering / construction documents,
for a language model to read and cite. An engineer reviewing a submittal, a
report or a drawing set should not have to tell the model whether the answer
is in the prose, a table, a drawing or a reviewer's markup, and the model
should not have to read geometry off pixels. planlens turns the PDF into
located, attributed data:

- **`planlens.document`** — the whole document: a page map (text pages,
  drawing sheets, forms, figures, scans, each with the evidence for the call,
  its word density, the page number printed on it, its sheet reference and
  scale notes) and the document's structure — the transmittal, drawing set,
  calc package, nested reports and appendices, found from the running
  headers, footers and printed numbering the pages themselves carry; repeated
  pages, found from their words or — on scans, which have none — from a hash
  of the page's own picture; a flag on every page whose text layer is there
  but UNRELIABLE (a font with no Unicode map, so the extracted string is not
  what the page says, which no reader could otherwise tell from prose);
  contact
  sheets of every page like a viewer's page panel; text
  lines with exact boxes and true reading direction; tables; the review
  record (comments, callouts, clouds, arrows, stamps — author, date, and the
  exact spot each points at); the hidden text AutoCAD stores behind stroked
  SHX lettering; search across all of it. Search also matches
  approximately (`search(..., fuzzy=True)`), so a
  term still turns up in text that was read optically or recovered from
  stroked lettering and has a letter wrong. And it pulls out the numbers the
  document STATES — "approximately 40-foot centers", "2,500 psf", "EL. 1684",
  "2H:1V", "20 to 35 ft" — each with its raw wording, its qualifier and its
  place on the page, so what the text claims can be compared with what the
  drawing measures. An optional Azure Document
  Intelligence result can supply text for scanned pages — planlens reads the
  result, it never calls or requires the paid service.

  And it says what each page of a report IS — narrative, a boring log, a
  laboratory sheet, a calculation printout, a report bound inside another
  report — with the evidence for the call, and groups the pages into the work
  items a reader takes in one at a time: one per boring or test pit with its
  continuation sheets folded in, one per laboratory sheet, one per printout.
  Rules over what the pages themselves print: what an appendix tab states it
  holds, what a page's own largest type calls it, and the page's shape. And
  it hands over the report's own account of itself — the table of contents,
  the lists of figures, tables and appendices with the page numbers as
  printed, every divider, each figure's caption, the section headings — plus
  one compact line per page, so a model can take a 700-page report in before
  it opens anything.

  It also reads the measurement calibration a PDF already stores, so a scale
  need not be guessed off a title block. When someone has calibrated a sheet
  in Bluebeam or measured with Acrobat's tools, the file carries that as
  structured data: a page's `/VP` viewports, and the `/Measure` dictionary on
  each dimension markup. `planlens.document.scale` reads both — the ratio
  (`1 in = 20 ft`), the real-world units per PDF point, and the region each
  governs, in the same displayed frame as everything else — and turns them
  into a resolved scale for `planlens.ir.measure`, so a page-point length
  becomes feet or metres on the drafter's own statement rather than an
  inference. A dimension markup reports both what its comment STATES and what
  its vertex path DERIVES, so the two can be seen to agree. A viewport that
  exists but is the untouched 1:1 default is reported as exactly that, and a
  drawing sheet storing no scale says so.
- **`planlens.tools`** — the above as LLM tools, framework-neutral: JSON-Schema
  specs in Anthropic or OpenAI style and a dispatcher whose every result is
  valid JSON inside the size limit the host sets, paging losslessly through
  anything longer. Text first, eyes second — and the tools say when: a scan,
  a figure, a drawing sheet or a ruled form read as a sparse grid comes back
  with a `! look:` line and the host's instruction for viewing it, and
  `render_page` / `render_region` produce the image (displayed frame,
  pixel-capped, numbered marks on request). Given the vision model's image
  budget (`image_budget="openai-original"`, `"claude"`, ...), every image is
  the largest that model reads without shrinking it, a zoom is re-drawn from
  the PDF to fill it, and `render_region` also takes a box read off an
  earlier image (in its pixels, or on a 0-999 grid) so the model can zoom on
  what it saw. `find_like` takes a box round ONE copy of a tag, code or
  symbol and finds every other copy on every page — even where the
  lettering is drawn as lines and there is no text to search — labelling
  each one callout (with where its leader points), legend or unanchored,
  with numbered contact sheets for a vision model to confirm the reading.
  `budget_from_probe` reads the budget a deployment really
  applies off three blank test images, and each render reports how tall
  the page's lettering is in it (`text_px`), warning when it is too small
  to read. Image files open as one-page
  documents. `search_document` takes `fuzzy` for text whose letters were read
  wrong, and `find_quantities` returns every value-with-unit the document
  states, filterable by kind and unit, so a model can compare the narrative's
  claims with the drawing's geometry. `log_grid` hands back one
  boring or test-pit log as columns, a depth ruler, placed cells, layers and
  header fields. And `annotate_document` sends the review BACK: notes,
  highlights over the words quoted, boxes, callouts and replies written onto a
  NEW copy of the PDF as ordinary annotations, anchored to text that is
  searched for rather than to coordinates guessed at, with anything it could
  not anchor reported instead of placed. The same tools serve over the Model
  Context Protocol (`planlens[mcp]`), generated from the same specs — see
  "Use from an MCP host".
- **`planlens.ir`** — drawing geometry: lines, arcs, text, and the annotation
  constructs built from them (below).

Every coordinate `planlens.document` emits is in PDF points in the displayed
page frame (top-left origin, y down, page rotation applied) — the frame of a
rendered page image. Design notes: `planlens/document/DESIGN.md`,
`planlens/ir/DESIGN.md`.

```python
from planlens.document import open_document

with open_document("submittal.pdf") as doc:
    for row in doc.page_map():
        print(row.page, row.kind, row.label, row.heading)
    hits = doc.search("raker load")
    comments = doc.markups(author="Reviewer A")
```

## Drawing geometry (`planlens.ir`)

Deterministic geometry extraction plus confidence-scored annotation
constructs from PDF and DXF construction drawings.

## Architecture

Two layers, one principle — **geometry says WHERE, vision says WHAT**.
LLM/VLM vision is unreliable on precise geometry, so a deterministic
extractor owns every coordinate and an LLM (if you attach one) owns only
semantics.

1. **Primitive layer** (`planlens.ir`): a unified intermediate
   representation — Line / Polyline / Arc / Circle / Text with
   coordinates, layer, fill, provenance, and confidence — ingested from
   DXF (`ezdxf`, confidence 1.0), vector PDF (`planlens.pdf`, confidence
   1.0), or raster images (OpenCV, confidence < 1.0, `raster` extra).
   A plotted PDF keeps the drafter's own layer names (its
   optional-content groups) and says which shapes are painted rather
   than outlined, so "existing vs proposed" and "this symbol is solid"
   survive the trip from CAD to paper. Slice queries (bbox / angle /
   text / layer / nearest / endpoint) let a caller request exactly the
   geometry it needs.
2. **Composition layer** (`planlens.ir.queries`): named annotation
   constructs assembled from primitives as confidence-scored
   **proposals**, never asserted facts — leaders, dimensions, title
   blocks, bubble callouts (keynotes, grid bubbles, detail marks), and
   best-effort revision clouds. Every proposal carries the evidence it
   was built from.

`planlens.ir.render.render_region` snips any region of a sheet to a
high-DPI PNG (optionally with numbered set-of-marks overlays) so a
vision model can answer "what is this pointing at" about a location the
geometry layer pinned down.

`planlens.ocr` (optional `[ocr]` extra) reads lettering optically off
rendered sheets — many production plots letter with stroked outlines
(no text layer at all) — and merges the results into the IR as
confidence-scored text entities in the same coordinate frame
(auto-detects sideways-plotted sheets and PDF page rotation, and
corrects the engine's silent corner-order rotation on flipped or
vertical lines — verified within ~2 pt of the vector-ingest frame on
/Rotate=0/90/180/270 and both vertical reading directions).

`planlens.ir.align.fit_plot_transform` fits the model-space-to-plot
transform (axis rotation + scale + offset) from anchor geometry, so
native CAD entities can be located on the plotted page.

### Measurement: numbers that know what they rest on

The confidence ladder above disciplines **detection** — whether a construct
is really on the sheet. `planlens.ir.measure` extends the same discipline to
**derived** numbers, which is where the dangerous errors live: a detection
miss is visible (nothing is reported), while a scale error is invisible (a
plausible number is reported, in the wrong units, with nothing to flag it).

Every length and area travels as a `Quantity` carrying its unit, its basis
and its uncertainty, under three rules:

- **`units` is mandatory and inseparable from `value`** — there is no
  `value_ft` key anywhere that could be read bare.
- **Confidence composes by `min`, not by product.** A derived value is
  exactly as trustworthy as the weakest thing beneath it; a product over
  several sound inputs decays toward zero and stops meaning anything.
- **A page-point value refuses to become feet by unit lookup.** It raises.
  Points become feet only through a scale the caller resolved and applied.

`planlens.ir.spatial` measures a set of located things — how far apart are
these borings, columns, piles, trees, light poles. numpy only; no scipy.

Its central decision: **there is no key named `average_spacing`, at any
depth.** The phrase is genuinely ambiguous, and the readings diverge — on a
*perfectly regular* 3x3 grid the three conventions differ by **2.45x**
(nearest-neighbour 10.0, density-equivalent 6.67). So the caller gets all
three, each carrying its own `definition`, plus `convention_spread` saying
how much the choice costs on this particular point set. Degenerate cases are
answered as such rather than as zero: coincident points are merged and
counted (a duplicate would otherwise report a spacing of zero), and
collinear points return `None` for area-based spacing because "undefined"
and "zero" are different claims. A concave or nearly linear footprint —
borings along a street frontage — is flagged as overstating its own area
rather than silently deflating the answer.

## Capability status (kept honest)

- **Page roles, measured twice — read the second number (2026-09-16)**: the
  rules were written against 4,147 hand-labelled pages of 14 real
  geotechnical reports, 38 to 729 pages each, in four languages, text-layer
  and scanned. Page accuracy on the nine reports they were developed on is
  **0.92**, and **0.88** on the other five — but those five were looked at
  too, so neither figure is held out and neither is a forecast. The
  out-of-sample number is: **0.79**, or **0.86** where a hand-labeller
  recorded a second equally defensible label, on **70 pages of fourteen
  further reports that were never used in development**. 0.79 is what these
  rules do on the next report. What they get wrong is written down with them
  in `planlens/document/DESIGN.md`, and it is one thing above all: a page
  that says nothing about itself takes its appendix tab's word for what it
  is, which is why an inherited role carries a deliberately lower confidence
  and names the rule that fired. On the development reports, `dcp_log` recall
  is 0.54 because dynamic-cone sheets are usually scans, `other` is a
  residual rather than a class (precision 0.51), `figure` and `plan` are weak
  (precision 0.26 and 0.29) because a figure page carries the least text on
  it of any page, and a page with no text at all cannot be placed by its
  title. Both corpora are private and neither is in this repository.
- **A boring log read as the grid it is (2026-09-17)**: `log_grid(doc,
  pages)` gives one log's columns with their x bands and canonical names, the
  depth ruler fitted to the printed scale with the residual of the fit, every
  line of text placed in a column at a depth, the layers the description band
  is cut into, and the fields printed outside the body. No templates: the
  columns come from the form's own ruling lines and headers, in English,
  French or Spanish. Values come back AS PRINTED and are never parsed into a
  meaning — a blow record stays `"5-9-12"`. Measured against fifteen
  hand-transcribed logs from fifteen reports of a PRIVATE corpus that is not
  in this repository: six were open during development and the rules were
  TUNED ON them, and nine were scored BLIND (tolerances 0.15 m on a sample,
  0.30 m on a layer top). On the open six, ruler and unit 6/6, blow records
  and N values 57/57, layer tops 34/34, index values 35/36, header fields
  63/68; on the blind nine, ruler 9/9, unit 6/8, blow records and N values
  50/50, layer tops 22/26, index values 8/17, fields 33/54 — and one of those
  nine is the scanned page the optical path was built on, so it is eight and
  a half blind logs. **Read the blind column; the open one forecasts
  nothing.** No sheet of the fifteen is read at a wrong scale, which is the
  failure that matters: a withheld depth costs a reader a page, a wrong one
  costs them the boring. What it is still short of is the index properties,
  and two sheets that state their depth unit nowhere at all. A page whose
  ruler cannot be found returns its cells with NO depths and says so in
  `warnings`; that is the point of it, and one of the fifteen measured sheets
  is a tabular list of borings carrying no depth scale at all, where refusing
  IS the right answer. See `planlens/document/DESIGN.md`, "Log grid".
- **A line a form draws twice is returned once (2026-09-17)**: some forms and
  printer drivers overprint a string at the same place to fake a bold weight.
  `planlens.document` now drops the duplicate in TEXT EXTRACTION, so a page's
  words are not doubled, one occurrence does not return two search hits, and
  a model is not handed "9 9 10 10" where the page reads 9, 10; the page map
  reports how many went as `n_overprinted_lines`. Measured over a PRIVATE
  7,829-page corpus of 38 geotechnical reports, not in this repository: 4,751
  such lines on 523 pages of at least twelve of them, up to 2.5 per cent of a
  report's lines. Nothing published moved — re-scoring the page-role rules
  over the same 4,147 hand-labelled pages returned every rate identical.
- **Proven on real agency sheets**: bubble callouts (40/40 count match
  on a dense municipal standard detail), region rendering, endpoint /
  text-anchored queries, multi-page drawing-set search, OCR text
  recovery on no-text-layer plots (88-100% truth-text coverage, median
  coordinate error 2.2-15.5 pt per the committed ocr_coverage_check
  convention on the validation sheets), plot-transform
  fitting (0.02-0.03 pt rms on rotated real plots; guarded against the
  degenerate-scale and chance-match regimes — random anchors on a dense
  10k-entity sheet now return None in 60/60 trials while every true fit
  still passes), and native DXF annotation ingest (LEADER/MULTILEADER/
  DIMENSION/ATTRIB as first-class entities at confidence 1.0, surfaced
  by find_leaders/find_dimensions as evidence "native_dxf"; the
  ground-truth extractor lives at planlens.dxf.truth, extracts model
  AND paper-space layouts, and regenerates the committed corpus
  byte-for-byte — verified against all 10 files 2026-09-05). DXF
  INSERT block references now EXPLODE into IR primitives (exact
  insert transform via ezdxf virtual_entities, nested references
  included, style="block:<name>" provenance, depth/entity caps) —
  previously most standard-detail linework hid inside INSERTs
  (measured +561 and +1700 entities on two corpus sheets).
- **DXF layers are RESOLVED, not copied verbatim** (behavior change,
  2026-09-05). Geometry drawn on layer `"0"` inside a block definition
  is, by DXF semantics, drawn on *whatever layer the INSERT sits on* —
  layer `"0"` inside a block means "inherit from the reference". The
  DXF leg now applies that inheritance when it explodes a reference, so
  an exploded entity reports the layer a CAD user sees, not the literal
  `"0"` in the block record. This MOVES entities between layers and can
  change `n_layers`: measured directly off the corpus DXFs (ezdxf
  `virtual_entities`, 2026-09-07) it re-homes **557** entities on sheet
  10.17a, **158** on 11.01 and **1696** on 5003, and the distinct layers CARRYING GEOMETRY fall from 3 to 2 on both
  10.17a and 5003 (11.01 stays at 4; the other seven corpus sheets have
  no layer-`"0"` block geometry and are untouched). Note the
  `n_layers` METADATA field is a different tally — it counts every
  layer name seen during ingest, including those on INSERTs and on
  entity types that produce no IR entity — so it reads 4, 8 and 3 on
  those same three sheets and does NOT move with the inheritance. A
  caller that hard-codes a layer name, or that counts layers, will see
  different numbers than it did before — this is the correct reading
  of the file, not a loss.
- **Proven recall on the native-truth corpus (Phase 3.2,
  2026-09-05)**: leader detection reaches 25/25 native-truth tips and
  dimension detection 16/16 native defpoints at the scorer's 0.3
  observational threshold (21/25 and 13/16 after Phase 3.1; 11/25 and
  1/16 after Phase 3; 0/25 before the plot-transform fit). What moved
  each number: Phase 3.1's misses were NOT missing arrowhead
  representations — every missed tip's arrow was already a candidate;
  the leaders were being consumed by FALSE dimension proposals that
  claimed their arrowheads (exclude_dimensions arbitration), and the
  dimension misses were narrow constructs whose short shafts the
  min-length floor rejected. The Phase-3.2 fixes: (a) dimension-arrow
  attachment is now SIGNED — an arrow attaches only when its
  intrinsic apex axis points outward along the line within 30 deg
  (measured +1.000 on every true dimension arrow vs -0.998..+0.208
  for the impostors); (b) short both-triangle continuous shafts are
  accepted (the narrow 'T=' style: a 9.7 pt shaft between outward
  arrows); (c) continuous proposal ends are the arrow APEXES = the
  CAD defpoints (match distance ~0.0 pt on the recovered dims). At
  the DEFAULT 0.5 confidence, 23/25 tips remain (two sparse-dot tips
  surface only at 0.45, below the call threshold, honestly capped).
- **Leader precision on lettering-heavy sheets (measured, Phase
  3.2)**: two structural confidence CAPS (to 0.45, never deletions)
  cut the worst zero-annotation notes sheet from 295 to 2 leader
  proposals at default confidence — two orders of magnitude — with
  corpus recall unchanged: a real arrow POINTS along its shaft
  (signed intrinsic-axis alignment >= cos 30 deg; letterform chevrons
  vs neighboring strokes are near-random) and a shaft ENDS at its
  arrowhead. The attachment bound is 0.75x the arrowhead scale — but
  0.75x the CANDIDATE'S OWN axial length whenever that is larger,
  because a drawn arrow's centroid sits ~2/3 of its own length behind
  its apex, so a candidate bigger than the sheet-statistic estimate
  (which the candidate gate deliberately admits, up to 1.5x) could
  otherwise never attach to anything. Measured 2.2-5.4 pt on every
  genuine leader vs p50 9.6 pt for sign-passing letter junk. Across
  the seven sheets with no native annotations: 295->2, 273->10,
  91->6, 77->19, 57->23, 151->20, 137->38 at default confidence (some
  survivors on the detail sheets may be REAL manually drafted leaders
  that native truth cannot record — render-verify before treating
  counts as pure FP).
- **The under-reported half of that work: dimension PRECISION also
  improved, at unchanged recall** (measured 2026-09-07, committed tip
  `1f6551c` vs the Phase-3.2 close-out). Dimension proposals at the
  0.5 default fell on every sheet that carries native dims — 21.01
  19->5, 3001 15->10, 11.01 18->12, 10.31A 15->10 — and the ones that
  went away were the FALSE ones: 21.01 and 3001 now return exactly
  their native dimensions and nothing else (5/5 and 10/10, zero
  unmatched), where the tip returned 14 and 5 unmatched alongside
  them. Defpoint recall is 16/16 at the 0.5 default, not merely at
  the 0.3 observational threshold. The signed-attach rule is what did
  it: an arrowhead that does not point outward along the line
  claiming it no longer founds a dimension.
- **What "few false dimensions" does and does not mean** (measured
  2026-09-07 on the close-out tree). Only three corpus sheets (2000a,
  2000b, 3000) are genuinely pure notes. At the 0.5 default those
  three return **zero** dimension proposals — and so do 5003 and
  10.25a. The remaining two zero-native-dimension sheets, 10.17a (17
  proposals) and 11.01 (12), are DETAIL sheets whose annotation was
  hand-drafted rather than placed as native DIMENSION entities, so
  their proposals are not false by construction — an independent
  render-adjudication put semantic precision at ~14/15 on the
  curb-ramp sheet's "false" dims.
  Below the 0.5 default the counts rise on every sheet BY DESIGN:
  capping (rather than deleting) an uncorroborated construct is what
  puts it in the 0.25-0.45 band, so a low `min_confidence` is a
  request to see the capped tier, not a precision regression.
  Caveats that remain true: the scorer's greedy 18-pt match can ride
  a nearby capped proposal, and confidence 1.0 does not preclude glyph
  junk on dense SHX sheets (verify visually).
- **Best-effort tier**: revision clouds (drafting-practice dependent).

**Every count above carries a code state, on purpose.** They were
measured on the ten committed Mecklenburg County NC sheets — the
Phase-3.2 figures at planlens `1f6551c` (2026-09-05), the ones dated
2026-09-07 on the close-out tree that followed it. A proposal count is
meaningless without both the `min_confidence` it was taken at and the
revision it was taken on, and the two dates differ precisely because
the close-out round converted deletions into caps. The ledger of
record is `module_work/drawing_ground_truth/score_compositions.py` in
the GeotechStaffEngineer repo: re-run it rather than trusting a count
copied into prose.

## Confidence ladder

A construct finder never deletes a construct it can see; it CAPS the
confidence and lets the caller decide. So `min_confidence` is the only
control that matters, and it has three meaningful stops:

| threshold | what it admits |
|-----------|----------------|
| **0.25** | *contradicted* — the geometry actively argues against the reading (an arrowhead pointing the wrong way along the line claiming it). Published so a caller can see WHY something was rejected. |
| **0.45** | *uncorroborated* — a real construct with one channel of evidence missing or weak (no witness lines, no text in range, an arrowhead the shaft does not run into along its spine). |
| **0.5** | the **default** call threshold: everything above is corroborated on more than one channel. |

The practical consequence, and the reason the ladder is written down:
lowering `min_confidence` to 0.45 to catch uncorroborated constructs
still silently drops the contradicted tier. Pass 0.25 (or 0.0) to see
everything the finder considered. Conversely, counting proposals at a
low threshold and calling the extras "false positives" mis-reads the
design — the capped band is where a cap-don't-delete policy puts its
doubts.

## What the validation corpus cannot see

All ten Mecklenburg sheets are plotted with SHX-stroked lettering and
carry **no text layer at all** (verified: `has_text` is false on 10/10).
That makes the corpus structurally blind to every behavior gated on
text: the text-corroboration channel of every construct score is
identically zero there, so a change that alters how text evidence is
used can measure as "no change on the corpus" while materially altering
results on an ordinary text-bearing drawing. Text-dependent behavior is
therefore validated on synthetic text-bearing scenes
(`planlens.testing`), never on the corpus alone.

Two more honest limits of the same kind:

- The **tipless-terminator** branch (box-like quads, which carry no
  pointing direction and so take the sign-blind path) is validated by
  synthetic fixtures. Since 2026-09-10 it splits two ways. An
  **oriented** shape is admitted with a fill cluster's standing and can
  be called: its long axis lies along the line, it is at ARROW SCALE
  (vertex-set diagonal at least 0.5x the sheet's arrowhead scale — the
  chevron gate's own floor) and it TAPERS toward the end it marks (a
  diamond, a flat-tipped closed arrow). Everything else tipless is
  **blunt** and capped unconditionally: isotropic boxes and tiles,
  sub-scale glyph fragments, and untapered oblongs — a rectangle at
  each end of a line with periodic ticks is a graphic scale bar, not a
  dimension (the 2026-09-10 gate, which lacked the scale floor and the
  taper, admitted 0.6-2 pt SHX fragments and read a 2:1-block scale
  bar as a 0.944 dimension; both are pinned as NOT called since
  2026-09-11). On real sheets the blunt family fires only into the
  capped band. Across all ten sheets **19** proposals carry
  `blunt_terminators` evidence at `min_confidence=0.0`, **17** at 0.3
  and **none at the 0.5 default** —
  21.01 contributes 17 / 15 / 0 and 11.01 contributes 2 / 2 / 0, and
  the other eight sheets none at any threshold (re-measured 2026-09-11;
  the figure is DEFINED as proposals whose every terminator is blunt
  under the split above, and it coincides with the 2026-09-07 figure
  because the oriented class has no member on this corpus:
  oriented-terminator proposals: 0 / 0 / 0 at 0.0 / 0.3 / 0.5, every
  shape the looser 2026-09-10 gate had admitted being a sub-scale
  fragment). So no default-confidence result on this corpus rests on
  either family, and their behavior above the call threshold is
  unmeasured on real drafting.

  Every corpus figure in this section is regenerated by ONE committed
  command — `doc_claims_check.py`, beside the recall scorer in the
  consuming repo's `module_work/drawing_ground_truth/`. It exists
  because these numbers drifted twice during the Phase-3.2 remediation:
  prose was edited without a run, and nothing failed. If a figure here
  disagrees with that script, the DOCUMENT is wrong.

- Native DXF truth records only annotation placed as LEADER /
  MULTILEADER / DIMENSION entities. Hand-drafted annotation — lines
  and triangles a drafter drew by hand — is invisible to it, so a
  proposal counted "false" against native truth may be a real
  annotation. Render-verify before reporting a precision number.

Sharp edges the corpus cannot falsify (2026-09-10, from the round-4
independent verification of the Phase-3.2 remediation; each is pinned
by a fixture so it cannot move silently):

- A **concave (swallowtail / barbed) arrowhead** never becomes a
  candidate: the non-degeneracy gate (`area / perimeter^2 >= 0.02`)
  rejects it, so a dimension or leader drawn with that style is
  DELETED at every `min_confidence`, not capped. Documented, not fixed
  — the gate is what keeps dash artifacts and near-collinear glyph
  strokes out, and relaxing it needs a corpus that contains the style.
- An **equilateral or wide (>= 60 deg tip) closed triangle** has no
  decisive apex vote: at 60 deg the elected tip follows the vertex
  order the plotter happened to use, above it a base corner is
  genuinely the farthest vertex. Such an arrowhead reads as
  contradicted (0.25) with its published end ~5 pt off, or as sound at
  0.95, depending on vertex order. Open; a fix that applies the
  quad-style margin test to triangles moves every wide junk triangle
  on the corpus from the 0.25 rung into the 0.45 observational band and
  needs its own measured round.
- **Who owns a dimension end** is decided by SEAT — the shaft end's
  distance from the candidate's own apex or base centre, or from a
  fill cluster's NEAREST member (0 when the end is in the splash) —
  and between the two sound tiers the better-seated candidate wins,
  ties to the directional one; a contradicted candidate never wins on
  distance. (The 2026-09-10 rule — directional wins unless the
  sign-blind candidate is seated ten times nearer, with the cluster
  seated at its centroid — was measured to close the foreign-arrowhead
  steal only for a cluster centred within 0.05-0.4 pt of the end; a
  real stipple arrowhead sits 0.5-2 pt off, and lost. Since 2026-09-11
  the cluster keeps its end at every offset up to 2 pt against a
  foreign chevron seated 0.94-3.3 pt beyond it, and the leader that
  owns that chevron survives `exclude_dimensions` — pinned per offset.)
  Two consequences are drafted fact, not defects: a lone
  outward-pointing chevron whose base sits at a shaft end IS an
  arrows-outside terminator and is published at its apex (there is
  nothing to prefer over it); and a stipple splash centred on a shaft
  end takes that end from the dimension's own base-anchored arrow at
  ANY non-zero crookedness, because the rule is seat-only with the tie
  going to the directional side — a splash on the end seats ~0.04 pt
  (its nearest member) and an arrow rotated 0.5 deg about its apex
  already seats 0.06 pt at its base centre. Measured (2026-09-11, the
  round-6 verification): at 0.5 deg the construct is founded on the
  splash, CALLED at 0.947, and its end published at 94.3 for a
  defpoint at 100 (inside the splash, 5.7 pt short); the same at 1.0,
  2.4, 5.0 and 12 deg; at exactly 0 deg the arrow keeps the end. This
  is corpus-inert (every corpus arrow seats within 0.036 pt and no
  corpus end carries both a drawn arrow and a splash), identical to
  what the published tip did (it gave the splash the end at every
  angle, 0 deg included), and a regression only against the round-4
  tree in the 0.3-9.6 deg band, where round 4 kept the arrow and
  published the exact apex. The scene needs two terminators at one
  end — a hand-rotated arrow block inside a stippled detail — and a
  splash centred on a shaft end is the anatomy of a real stipple
  arrowhead, which is why the rule stands. Pinned at 0 / 0.5 / 1.0 /
  2.4 / 5.0 deg in `test_end_ownership.py` so it cannot move silently
  in either direction. (A splash 5 pt off the end, the case round 4
  measured, seats ~3.5 pt and loses to an arrow crooked up to ~20 deg.)
- **The oriented-terminator size floor is a sheet statistic**, like
  the open-chevron gate it reuses: without an explicit
  `max_arrowhead_size`, the arrowhead scale is 25 % of the median
  open-segment length (floored at 1 % of the page diagonal), so a
  sheet of long linework raises the floor and can cap a 6 pt diamond
  dimension at 0.45 (measured: five 400 pt lines take the scale from
  10 to 100 and the diamond from called at 0.94 to blunt) that a
  closed-triangle dimension on the same sheet survives — closed 3-5-gons
  carry no lower size floor. Pass the sheet's real arrowhead scale
  when you know it.
- **A full mirror-image tie** at one end — two candidates with equal
  seat, equal alignment and equal centroid distance, e.g. chevrons at
  +20 and -20 deg both base-seated on the end — resolves by the
  candidate grid's cell iteration order: deterministic and
  order-independent, but arbitrary, and there is no right answer for a
  genuine mirror pair.

## Install

```
pip install planlens         # everything below except OCR and MCP
pip install "planlens[ocr]"  # + optical text for stroked/scanned sheets
pip install "planlens[mcp]"  # + the Model Context Protocol server
```

The plain install carries the whole reading path: DXF and vector-PDF
ingest, raster/scanned-sheet tracing (`opencv-python-headless`) and
forgiving search, `Document.search(fuzzy=True)` (`rapidfuzz` — a small
C++ extension, no models, no runtime downloads). Those two were the
`[raster]` and `[text]` extras before 0.4.0 and are now core; both
names survive as empty extras, so `pip install "planlens[raster]"`
still resolves and installs the same thing as `pip install planlens`.

The `[ocr]` extra installs RapidOCR + onnxruntime with PP-OCR models
inside the wheel (no runtime downloads; all-permissive licenses:
Apache-2.0/MIT/BSD).

**OpenCV variants — pick per environment.** Every published rapidocr
distribution (`rapidocr-onnxruntime` 1.x and the unified `rapidocr`
2/3.x alike) hard-requires the full GUI `opencv-python` (~112 MB),
while planlens itself depends on `opencv-python-headless`; pip cannot
express "either variant", and installing both leaves two distributions
owning the `cv2` namespace (works, but uninstalling either can break
the other). That is why OCR is still an extra. Decision:

- **Desktop / notebook**: `pip install "planlens[ocr]"` as above — the
  GUI build wins the namespace and everything works.
- **Server / headless deploy** (Databricks, TinyApps — no GUI libs):
  skip the `[ocr]` extra and install the engine without its metadata
  deps; the OCR leg needs only the cv2 APIs headless provides
  (verified end-to-end in a clean headless-only venv, 2026-09-05):

  ```
  pip install planlens
  pip install --no-deps "rapidocr-onnxruntime==1.2.3"
  pip install "onnxruntime>=1.7" pyclipper shapely pillow pyyaml six
  ```

  Pin the rapidocr version you validated — `--no-deps` means ITS
  dependency list is being supplied by hand, so an unpinned upgrade
  could silently need something new. (1.2.3 is the newest wheel that
  installs on Python 3.14 today; newer versions keep the same runtime
  set — re-verify when bumping.)

## Use from an MCP host

`planlens.mcp_server` serves the same tools over the Model Context
Protocol, so a host — Claude Code, Claude Desktop, an editor, a
LangChain or deepagents program — can discover and call them with no
planlens-specific integration code. The tool list is generated from
`ReviewToolkit.specs`, so it cannot drift from the toolkit.

```
pip install "planlens[mcp]"
claude mcp add planlens -- python -m planlens.mcp_server
```

Claude Desktop, in `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "planlens": {
      "command": "planlens-mcp",
      "args": ["--root", "C:/work/documents"]
    }
  }
}
```

A deepagents or LangChain program reaches the same server through
`langchain-mcp-adapters`, which turns an MCP session into LangChain
tools.

Flags: `--max-chars` matches the host's own result-size limit
(default 7,500); `--vision-hint` replaces the text of the `! look:`
lines, whose default names this server's `render_page` /
`render_region` — pass your host's own vision tool instead if it has
one; `--root DIR` confines reading to one directory tree; `--http
HOST:PORT` serves streamable HTTP instead of stdio; `--image-budget`
sizes every page and zoom to the model family the host runs (`claude`,
`claude-hires`, `openai-high`, `openai-original`, `gpt-4.1-high`) and
`--image-format auto` sends a scan as JPEG and a drawing as PNG.

Over MCP the pictures travel: `render_page`, `render_region` and
`render_page_thumbnails` return the image as content alongside the
JSON, so a host whose model can see gets the page itself.

**Two caveats, both about who is allowed to read what.** The server
opens the files the caller names, with the privileges of whoever
launched it, and it authenticates nobody — the host decides who may run
it, and `--root` is what narrows the reach to one directory (relative
`source` paths resolve against it; anything resolving outside it,
including `../` and symlinks, is refused). `--http` is for a local
process or a platform that puts authentication in front of it; this
server provides none.

If your organisation installs through a package firewall, `mcp` has to
be approved there before this extra can be pinned in a deployment.

## Testing your own wiring (`planlens.testing`)

`planlens.testing` is shipped, public API: synthetic-drawing builders
that plant known constructs on a programmatic PyMuPDF sheet and hand
back ground truth in the IR `bottom_left` frame, so a consuming package
can test its integration without shipping real drawings.

```python
from planlens.testing import build_synthetic_leader_pdf   # + ground truth
from planlens.testing import (
    build_synthetic_dimension_pdf, build_synthetic_title_block_pdf,
    build_synthetic_bubble_pdf, build_synthetic_cloud_pdf,
    build_synthetic_drawing_set_pdf,
)
# Whole-document fixtures for the document layer and the tools:
from planlens.testing import (
    build_synthetic_review_document,   # a report page with a review stamp, a
                                       # /Rotate 90 sheet with a reviewer's
                                       # callout, a reply, a cloud, an arrow and
                                       # hidden CAD text, a ruled table, a blank
                                       # page and a scan
    build_synthetic_submittal,         # a stapled submittal: cover, report with
                                       # running header and "Page N", appendix
                                       # divider, ruled logs, a duplicated page,
                                       # two D-size sheets, attachment divider
    build_synthetic_report,            # a 22-page report over eleven of the
                                       # roles: cover, contents, narrative,
                                       # tabs, boring and test pit logs,
                                       # photographs, lab sheets, a whole
                                       # report bound inside an appendix, a
                                       # program printout - with the answers
)
```

Import them from `planlens.testing`, not from `planlens.ir.tests` — the
`*.tests` packages are excluded from the wheel, so the old path only
ever resolved in a source checkout. Thin re-export shims remain at the
old location for the transition. Nothing in `planlens.testing` imports
pytest, so it adds no test-only dependency to the runtime package.

These builders are also how text-dependent behavior gets validated:
the real-sheet corpus has no text layer (see "What the validation
corpus cannot see"), so a text-bearing synthetic scene is the only
place a text-gated change is observable.
