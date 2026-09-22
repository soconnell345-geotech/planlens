# Drawing IR — design notes

## North star

> "LLM vision often doesn't handle precise geometry well."

So the division of labor is deliberate: the **deterministic extractor owns
coordinates**; the **LLM owns semantics**. A drawing is digitized once into a
single, exact intermediate representation (the `DrawingIR`), and the model then
asks *structured questions* of it — "what text sits near entity `e12`?", "which
polylines run left-to-right longer than 20 m?" — instead of eyeballing pixels
and guessing numbers. Every coordinate the LLM eventually uses came from the
extractor, not from a vision read.

This module is the schema + the ingest legs + the query surface that make that
possible. It does **not** interpret the drawing (no "this is a retaining wall").
It hands an LLM/agent a clean, queryable geometry model and lets *that* layer do
the interpretation, with the raw coordinates always one `get_entities` call away.

## What it is

```
planlens/ir/
  results.py   # DrawingIR + Entity types (Line/Polyline/Arc/Circle/TextItem/
               # Region/Leader/Dimension)
  ingest.py    # from_dxf / from_pdf_vector / from_raster
  raster.py    # the OpenCV tracing leg (isolated so cv2 stays optional)
  queries.py   # the LLM-facing slice queries + the construct finders
  render.py    # render_region — the region-snip "zoom in" vision primitive
  align.py     # fit_plot_transform — model space -> plotted page
  measure.py   # Quantity: derived numbers that carry unit + confidence
  spatial.py   # point-pattern measures (spacing conventions, hulls)
  tests/       # programmatic DXF/PDF/raster fixtures + query correctness
planlens/pdf/      # vector-path + text extraction, scale parsing (the ingest leg)
planlens/dxf/      # unit detection, native-annotation truth extraction
planlens/ocr.py    # optional optical text (RapidOCR) merged into the IR
planlens/document/ # the whole-document layer (page map, text, tables,
                   # markups, structure) — see planlens/document/DESIGN.md
planlens/tools/    # the framework-neutral LLM tool layer
planlens/testing/  # shipped synthetic fixtures (public API)
```

(Historical note: this package began life as `drawing_ir/` inside the
geotech app and was split out as `planlens` on 2026-09-04; older prose and
commit messages use the old module names `drawing_ir` / `pdf_import` /
`dxf_import` for what are now `planlens.ir` / `planlens.pdf` /
`planlens.dxf`.)

## The IR schema (`results.py`)

A `DrawingIR` is **one drawing page**: page metadata + a flat list of entities.
It round-trips to/from JSON losslessly (`to_dict` / `from_dict`,
`entity_from_dict`).

Page metadata: `width`, `height`, `units`, `coordinate_space`, `scale`,
`scale_provenance`, `origin`, `source`, `warnings`, `metadata`.

Every entity carries a common envelope:

| field        | meaning |
|--------------|---------|
| `id`         | stable within the page (`e0`, `e1`, …) |
| `layer`      | CAD layer / logical group: a DXF layer, or a vector PDF's optional-content group; `None` when the source gives none (raster always) |
| `color`      | hex `#rrggbb`, or `ACI<n>` when only a DXF color index is known |
| `filled`     | the shape is PAINTED, not just outlined — `False` unless the source says so |
| `fill_color` | that fill's colour, hex `#rrggbb` like `color`, or `None` when unpainted |
| `style`      | linetype / a note like `approx_from_spline`, `hough`, `contour` |
| `source`     | `dxf` \| `pdf_vector` \| `raster_trace` (provenance) |
| `confidence` | `1.0` for deterministic sources; `< 1.0` for raster detections |
| `bbox`       | `(x_min, y_min, x_max, y_max)`, auto-computed from geometry |

Concrete types and their geometry:

- **Line** — `start`, `end` (+ derived `length`, `angle_deg` folded to [0,180)).
- **Polyline** — `vertices`, `closed` (+ `length`).
- **Arc** — `center`, `radius`, `start_angle`, `end_angle` (CCW degrees). The
  bbox is sampled (it accounts for the axis crossings the arc actually sweeps).
- **Circle** — `center`, `radius`.
- **TextItem** — `content`, `position` (insertion point), `rotation`, `height`.
  Its bbox is an **approximation** (width estimated from the character count) —
  true text metrics are font-dependent and not recovered.
- **Region** — `boundary` ring + optional `pattern` (hatch/filled area).

### Coordinate space & the "flag"

All entities on a page share ONE coordinate space, flagged on the page (not
repeated per entity):

- `coordinate_space="model"` → calibrated engineering units (`units`, SI meters
  by house convention); a scale has been applied. `scale` (model units per page
  unit) and `scale_provenance` say how.
- `coordinate_space="page"` → raw page/pixel units (`units` = `pt` for PDF
  points, `px` for raster pixels, or the DXF drawing units); no calibrated scale.

`origin` records the y convention (`bottom_left` = engineering up-positive, the
default for PDF/raster; DXF model space is already up-positive).

## Provenance & confidence semantics

Confidence is **the honesty knob**. Deterministic sources read exact path
coordinates and get `1.0`. The raster leg only sees pixels, so every entity is a
*detection* with a fixed sub-1.0 tier (see below). An agent should treat a
`confidence < 1.0` entity as a proposal to confirm, and always prefer
`get_entities` coordinates over its own pixel reading.

| source          | confidence | notes |
|-----------------|-----------|-------|
| `dxf`           | 1.0 | exact CAD geometry, with layers/colors |
| `pdf_vector`    | 1.0 | exact PDF path coordinates |
| `raster_trace`  | 0.4–0.6 | Hough lines 0.6, circles 0.5, contours 0.5, OCR 0.4 |

## Ingest legs (`ingest.py`)

### `from_dxf` (ezdxf) — confidence 1.0
Direct model-space pass: LINE, LWPOLYLINE/POLYLINE, ARC, CIRCLE,
ELLIPSE/SPLINE (flattened to polylines, tagged `approx_from_*`), TEXT/MTEXT, and
best-effort HATCH → Region. Coordinates are converted to **SI meters** using the
drawing's `$INSUNITS` header (or a supplied `units`); `scale`/`scale_provenance`
record any unit conversion. Colors are preserved verbatim.

Layers are **resolved, not copied verbatim**. Geometry drawn on layer `"0"`
inside a block definition means "inherit the layer of the INSERT" in DXF, so
when `explode_blocks` expands a reference the exploded entities take the
INSERT's layer rather than the literal `"0"` in the block record — what a CAD
user sees. Consequence for callers: entities move between layers and
`n_layers` can shrink. Measured directly off the ten-sheet validation corpus
(ezdxf `virtual_entities`, 2026-09-07): 557 entities re-homed on sheet 10.17a,
158 on 11.01, 1696 on 5003, with the distinct layers CARRYING GEOMETRY going
3 -> 2 on 10.17a and 5003 (11.01 stays 4); the other seven sheets have no
layer-`"0"` block geometry and are unchanged. The `n_layers` METADATA field is
a different tally — every layer name seen during ingest, INSERTs and
unsupported entity types included — and reads 4, 8 and 3 on those three
sheets rather than following the inheritance. Entities drawn on an explicit layer inside a block keep that layer.

### `from_pdf_vector` (PyMuPDF) — confidence 1.0
Reuses `planlens.pdf.extract_colored_paths` (per-path point lists + color) and
`planlens.pdf.discover_pdf_content` (page size + text). Each path becomes a Line
(2 points) or Polyline; each text span a TextItem carrying its reading
direction in the IR frame (until 2026-09-13 every PDF TextItem said rotation 0,
wrong for 215 of 245 lines on a real /Rotate 270 sheet). Text drawn by
annotations — a reviewer's comment, a stamp — is excluded (ordinary
`get_text` includes it; see `planlens/document/DESIGN.md`).
`include_cad_hidden_text=True` adds AutoCAD's hidden SHX-text annotations as
`source="pdf_annotation"` TextItems with a box-estimated rotation; it is off
by default because the corpus figures were measured without that channel. With an explicit `scale`
(m per point) or a two-point `calibration` (`{p1, p2, distance_m}` via
`planlens.pdf.calibrate_scale`), coordinates are promoted to model meters;
otherwise the IR stays in page points and **scale candidates** parsed from the
page text (`planlens.pdf.propose_scale`) are attached to `metadata` as *proposals,
never applied*. Paths carry their LAYER and their FILL (see the next
section). Bezier curves are SAMPLED (8 subdivisions
per cubic, `planlens.pdf.extractor._sample_cubic_bezier`, Phase 2) — a drawn
circle arrives as a ~32-vertex circle-like ring and a cloud scallop keeps its
bump; before Phase 2 curves collapsed to their chord, which made curve-aware
construct detection impossible.

### Layers and fill from PDF

A plotted PDF is not the layerless thing this module long assumed. AutoCAD's
export writes one OPTIONAL-CONTENT GROUP per CAD layer, and every path says
which one it sits in; it also says whether it is painted or merely outlined.
Both are evidence a reviewer reads directly — "existing" vs "proposed" is
often nothing but the layer name, and a filled circle is how a boring is
drawn — so both now reach the IR: every entity built from a vector path
carries `layer` (the group's name, else `None`), `filled`, and `fill_color`.
This is evidence for a caller, **not** a finder change; no threshold, score
or rule moved.

What the ten-sheet Mecklenburg corpus actually carries (measured 2026-09-16
with the consuming repo's `module_work/drawing_ground_truth/
probe_layers_fill.py`, PyMuPDF 1.27.2, 41,061 paths in total):

| measured | corpus |
|---|---|
| sheets declaring optional-content groups | 1 of 10 (`10.31A`: `0`, `BORDER`, `TEXT`, `REV`, `PROPOSED`, all default ON) |
| paths carrying a layer | 3,208 (7.8% of the corpus; 100% of that one sheet, 0% of the other nine) |
| that sheet's paths per layer | `0` 2,106, `BORDER` 611, `TEXT` 419, `REV` 64, `PROPOSED` 8 |
| path paint: fill-only / stroke-only / both | 7,096 `f` / 33,821 `s` / 144 `fs` |
| filled paths (>= 3 points) | 6,669 — 3 circle-like rings, 6,278 few-vertex polygons at arrowhead scale |
| filled paths whose `closePath` flag is True | **0 of 6,669** |
| `get_drawings()` on the biggest sheet | 0.07 s (10,095 paths) |

Four facts that decided the design:

- **"No layer" is the empty string, not `None`.** PyMuPDF reports `""` for a
  path in no group, so a naive read makes 100% of every sheet "layered". It
  is normalized to `None` at the extractor, because `""` is not a layer name
  and a caller asking for the unlayered geometry must not have to know which
  spelling of nothing the library used. A group genuinely NAMED `"0"` (that
  corpus sheet has one — AutoCAD's default layer) is a real name and is kept
  verbatim; this is the OPPOSITE of the DXF leg, where `"0"` inside a block is
  the inheritance sentinel described above. The PDF leg never writes `"0"` to
  mean "no layer".
- **A filled path is closed whatever the flag says.** The fill operator closes
  every open subpath, and on this corpus the `closePath` flag is False on all
  6,669 filled paths. So `filled` — not `closed` — is the dependable "this is
  an area" signal, which is the same lesson `find_leaders` learned from the
  other direction (a naively drawn shaft comes back `closed=True`).
- **Hidden layers are hidden.** MuPDF honours the document's own default
  state, so nothing on a group that is OFF reaches `get_drawings()` — verified
  against a two-group synthetic. The IR therefore shows what the sheet SHOWS,
  publishes the `ocgs` summary (name → default ON) so a caller can SEE that a
  hidden group exists, and WARNS naming it, because an omission nobody
  mentions is indistinguishable from an empty layer.
  `from_pdf_vector(include_hidden_layers=True)` turns every group on first and
  reads them — the same opt-in shape as `include_cad_hidden_text`.
- **The OCG dictionary is a supplement, not the authority.** A real submittal
  outside this corpus declares **zero** OCGs while its sheets' paths carry
  dozens of distinct layer names each (that document is confidential; the
  shape of the finding is not). A path's name comes from the marked content
  it sits in, which need not be registered in `/OCProperties`, and nested
  optional content arrives as the names joined by `|`. So the `n_layers` tally
  is taken from the PATHS themselves — the same meaning the DXF leg gives it,
  every distinct layer name seen during ingest — and `ocgs` is published only
  when the document declares some.

Only paths carry a layer: a PDF text span records no group membership, so
every `TextItem` on this leg has `layer=None` rather than a guess at the
nearest path's group.

`filled` and `fill_color` sit on the ENVELOPE beside `color`, not on one shape
class, because the same fact reaches the IR as a closed `Polyline` (a plotted
arrowhead or boring dot), a `Circle`, or a `Region` (a DXF hatch). `to_dict`
emits `filled` only when true and `fill_color` only when present — an unfilled
entity says nothing about fill — and `fill_color` is written in `color`'s own
hex `#rrggbb` spelling, by the same `_color_to_hex` that writes a stroke
colour. Two colour fields on one envelope in two notations would be a trap for
every reader of an entity, and the quantizing that a hex costs is the same
quantizing `color` has always accepted. The one difference is the absence: a
stroke with no stated colour is black, while an unpainted path has no fill at
all, so `fill_color` is `None` there rather than `#000000`.
**What an entity IS did not change**: a filled triangle is
the same closed 3-vertex Polyline it always was (pinned by a test that ingests
the same scene painted and unpainted and compares types, counts and vertices).
The construct finders carry the new fact as EVIDENCE only —
`arrowhead_filled`, `filled_terminator_ids`, and `filled` on bubble-callout
and revision-delta proposals — and every corpus figure in this document is
unchanged by it (`doc_claims_check.py`, before and after: identical).

### `from_raster` (OpenCV) — confidence < 1.0
Delegates to `drawing_ir.raster.trace_raster` (keeps `cv2` optional). See below.

### The import ruling

`ingest.py` imports the `planlens.pdf` / `planlens.dxf` ingest legs directly:
they are one I/O layer inside one package. `results.py` and `queries.py` are
kept pure-schema (no ingest imports) so the schema/query core has zero heavy
dependencies. planlens imports **nothing** from the geotech app — the app
depends on planlens, never the reverse; the only geotech-facing bridge
(`to_dxf_parse_result`) lives app-side in `dxf_import/pdf_bridge.py`.

## The raster leg's honest limits (`raster.py`)

The raster leg is the low-confidence bootstrap, not a replacement for vector
data. What it does and does **not** do:

- **Lines** — probabilistic Hough on Canny edges. A *thick* stroke has two
  edges, so one drawn line can trace as two nearly-parallel Hough segments.
  Coordinates are pixel-quantized.
- **Circles** — Hough gradient transform. Sensitive to `param2`/radius bounds.
- **Contours** — external-contour tracing + polygon approximation → closed
  Polylines. Best for filled/outlined *regions*; on pure line-work it may trace
  the *outline* of a stroke, overlapping the Hough line result — so the
  detectors are individually toggleable (`detect_lines/circles/contours`).
- **Arcs are not recovered** — a partial curve is missed or seen as a contour.
- **No layers** — raster has none; `layer` is always `None`. Colors are sampled
  per detection from the source pixels.
- **Text via OCR is opt-in and best-effort** — only if `pytesseract` + a
  Tesseract binary are importable/working. If not, text is **skipped with a
  warning** (positions are never invented). This machine has no Tesseract, so
  the raster text leg degrades to positions-only there.

Because tracing is inexact, the raster tests are tolerance-based (counts and
approximate coordinates, not exact equality).

## Query interface (`queries.py`) — the LLM's surface

Every function takes a `DrawingIR` and returns compact, JSON-able results —
entity **references** (`id` + a small summary), never full coordinate dumps.
The agent narrows with queries, then pulls exact coordinates for a shortlist via
`get_entities`.

- `entities_in_bbox(x_min,y_min,x_max,y_max, mode, entity_type)` — spatial window
- `nearest_entity(x,y, entity_type, k)` — k nearest by true point-to-geometry
  distance (point-to-segment / point-to-ring / insertion point)
- `lines_by_angle(min_deg,max_deg)`, `horizontal_lines(tol)`,
  `vertical_lines(tol)` — over Line **and** Polyline segments
- `polylines_longer_than(min_length)`
- `text_items(pattern)` (regex or literal substring), `text_near(entity_id,
  radius)`
- `entities_on_layer(layer)`, `entities_by_color(color)`
- `get_entities(ids)` — full exact coordinates for a shortlist
- `entities_ending_near(point, radius, entity_types=None)` — entities with an
  ENDPOINT (not just bbox/full-geometry) within radius; each hit carries
  `end` ("start"|"end"), `end_point`, and `other_end` (the far endpoint —
  e.g. where a leader points FROM). Circle/TextItem never match (no
  endpoints). The primitive "what terminates here" query.
- `text_anchored_geometry(pattern, radius=None)` — composes `text_items` with
  `entities_ending_near` around each match's insertion point: "find text X ->
  the geometry terminating there -> the far endpoint it points at"
  (`points_at`). `radius` defaults per-match from that text's own height.
  **PROPOSAL** (`proposal_only`) — adjacency is not proof of a leader
  relationship, confirm against the drawing.
- `candidate_ground_surface()` — **PROPOSAL only**: the widest left-to-right
  path (tie → upper). A heuristic suggestion the caller confirms, never an
  assertion. (Soil properties never come from a drawing.)
- `find_leaders(max_arrowhead_size=None, search_radius=None, text_radius=None,
  min_confidence=0.0)` — **PROPOSAL only**: composes a leader (bent shaft +
  arrowhead + tail text) from primitives — small closed 3-5-vertex
  Polyline/Region "arrowhead" candidates, the nearest Line/open-Polyline
  endpoint as the shaft, alignment of the shaft's terminal direction with the
  arrowhead's own apex-from-base direction, and the nearest TextItem to the
  shaft's far end as tail text. Confidence = weighted alignment + text
  proximity + shaft simplicity (see the docstring for the exact weights and
  the documented false-positive source: a dimension line's arrowheads are
  geometrically identical — a true dimension is a one-arrow leader
  geometrically and scores HIGH (~0.78), so the DOCUMENTED precision
  contract is `exclude_dimensions=True`, which lets `find_dimensions`
  claim those arrowheads first). Validated on synthetic PDF-vector
  fixtures (`planlens/testing/leader_fixtures.py` + `test_find_leaders.py`):
  100% recall, 100% precision at confidence >= 0.5 *under
  exclude_dimensions* — fixture-scoped numbers, not a general benchmark
  claim.
- **Composition family (Phase 2)** — same proposal pattern
  (confidence + `evidence`, `proposal_only: True`, never asserted):
  - `find_dimensions()` — shaft with two DISTINCT arrowheads + roughly
    perpendicular extension lines + midpoint value text; straight shafts
    only (curved/angular dimensions out of scope). THE disambiguator for
    `find_leaders`' dimension false positive. Each shaft end is awarded
    to ONE candidate by SEAT — the end's distance from the candidate's
    own apex or base centre (0 for a real terminator in either drafted
    style), or from a fill cluster's nearest member (0 when the end is
    in the splash) — across the two sound tiers (directional, and
    direction-unobservable: cluster / tipless / detached), a tie going
    to the directional one; a contradicted candidate wins only when
    nothing sound is there (`_award_end`, `_seat_distance`). Tipless
    terminators split into `oriented` (long axis along the line AND at
    arrow scale AND tapering toward the end it marks — a diamond, a
    flat-tipped arrow; a cluster's standing, callable) and `blunt`
    (isotropic, sub-scale, or untapered — boxes, glyph fragments,
    scale-bar blocks; capped at 0.45 unconditionally, withheld from
    `arrowhead_ids`); both publish the centroid projected on the shaft
    ray, never behind the shaft tip. The README's blunt / oriented
    corpus figures are DEFINED by that split and regenerated by the
    consuming repo's `doc_claims_check.py`.
  - `find_title_block(edge_frac)` — edge-adjacent rectangle scored on
    edge adjacency + text density + rectangle nesting; returns the
    region bbox AND its text payload; text-cluster fallback (low
    confidence) when the sheet has no rectangles.
  - `find_bubble_callouts(max_radius, text_max_chars)` — native circles +
    circle-like closed rings (centroid circle-fit, rms/r <= 0.08) with
    short centered text; kinds keynote / grid_bubble (line ends on ring) /
    detail_callout (chord through center).
  - `find_revision_clouds(min_arcs)` — BEST-EFFORT tier (<= ~0.65 by
    design): native DXF Arc chains (endpoint union-find) + scalloped
    closed rings via the turn-angle cusp signature (smooth low-angle runs
    broken by opposite-sign junction spikes — empirically verified against
    PyMuPDF scallops; a rounded rectangle's same-sign corners are
    rejected); plus revision DELTAS (small labelled triangle with NO
    shaft terminating at it).
- **Performance (real-sheet scale)**: the composition loops index entity
  endpoints in a uniform grid (`_EndpointGrid`) and use O(1) id lookup —
  a 10k-entity Mecklenburg sheet runs in seconds (a linear-scan
  implementation profiled at minutes). Arrowhead candidates are gated on
  non-degeneracy (area/perimeter² >= 0.02) and shafts on
  length-vs-arrowhead scale, which keeps SHX glyph strokes from flooding
  the proposals; `_default_max_arrowhead_size` carries a page-diagonal
  floor because glyph-stroked sheets collapse the median segment length.
- `summary_stats()` — counts by type/layer, page metadata, extent, scale;
  reports `has_text` + an explicit note when a sheet has NO extractable
  text (SHX/stroked lettering — verified on all 10 Mecklenburg plots):
  text queries return nothing there and a zero count is inconclusive.

### The region-snip vision primitive (`render.py`)

`render_region(filepath|content, page, bbox=None, dpi=300, pad_frac=0.15,
marks=None)` renders a zoomed-in PNG crop of a PDF page — turns a WHERE
(a bbox from any exact source, typically a query result) into a high-DPI
image a vision model can answer WHAT about, instead of feeding a whole page
(illegible small annotations) or asking the model to guess pixel coordinates.
`marks` draws numbered circles at given points for set-of-marks prompting.

**Coordinate contract**: `bbox`/`marks` are PDF points in PyMuPDF's own page
space — origin top-left, y DOWN (`page.rect`/`fitz.Rect` convention) — NOT
the `origin="bottom_left"` convention `from_pdf_vector` defaults to. Convert
an IR point before calling: `x_pdf = x_ir; y_pdf = page_height_pt - y_ir`.

Implemented in `funhouse_agent/vision_tools.py` as `_dispatch_render_region`
(same conventions as `_dispatch_analyze_pdf_page`) plus a save-to-file
counterpart `render_region_to_file`. **Live on every agent surface since
Phase 2 (B6)**: `EXTENDED_TOOLS` + `VISION_TOOL_DESCRIPTIONS` + the
dispatch route (v1 agent), `make_vision_tools` (deep agent), and
`OPENAI_TOOLS`/`EXTENDED_TOOL_NAMES` (native). The drawing_ir adapter's
`snip_region` method is the save-to-disk twin and converts IR bottom-left
coordinates to this frame for the caller.

### A verified ingest fact `find_leaders` depends on

`from_pdf_vector` **never emits a `Region`** entity (only Line/Polyline/
TextItem — see `ingest.py`'s `from_pdf_vector` loop). A filled-triangle
arrowhead therefore arrives as a **closed Polyline with 3 vertices and a
small bbox** — verified empirically by round-tripping a synthetic PyMuPDF
leader through `from_pdf_vector` (see `planlens/testing/leader_fixtures.py`).
Gotcha along the way: PyMuPDF's `Shape.finish()` defaults `closePath=True`
even for plain multi-segment line-work, so a NAIVELY-drawn shaft (without
`closePath=False`) also comes back `closed=True` — geometrically
indistinguishable from an arrowhead by the `closed` flag alone. Real CAD/plot
PDF exporters normally leave a stroke-only path open, and the fixture
generator draws shafts with `closePath=False` explicitly to match; the
`closed` flag alone is therefore NOT a reliable arrowhead signal on its
own — `find_leaders` also gates on vertex count (3-5) and bbox size.

## Derived quantities (`measure.py`, `spatial.py`)

Added 2026-09-08, when the goal was restated: the package exists so engineers
can REVIEW design and construction documents, not so it can name CAD objects.
Answering a review question means reporting a number, and a number is where
this package's existing discipline stopped.

### `measure.py` — the confidence envelope

The 0.25 / 0.45 / 0.50 ladder above governs DETECTION. It says nothing about a
spacing in feet computed from a scale nobody verified, and that gap is more
dangerous than any detection error because of an asymmetry worth stating
plainly:

- a detection miss is VISIBLE — nothing is reported, and the caller notices;
- a scale error is INVISIBLE — a plausible number is reported, in the wrong
  units, with nothing to flag it.

So every length and area travels as a `Quantity(value, units, confidence,
rel_uncertainty, basis, scale_known)`. Three rules, each about what CANNOT
happen:

1. **`units` is mandatory and inseparable from `value`.** No `value_ft` key
   exists anywhere that could be read as a bare float.
2. **Confidence composes by `min`, never a product** (`combine_confidence`).
   A derived value is exactly as trustworthy as its weakest input. A product
   over five sound 0.9 inputs would report 0.59 — a lie in the pessimistic
   direction; a max lies in the other. `min` answers the question a reviewer
   is actually asking: what is the weakest link?
3. **A page-point value refuses to convert to feet by unit table.** `.to()`
   raises for `pt`/`px`. Points become feet only via `.scaled()` with a scale
   the caller resolved — which is why `pt` is deliberately absent from
   `LENGTH_IN_METRES`. `unknown_scale()` returns `confidence=1.0` with
   `scale_known=False`, saying precisely what is true: *we measured this
   exactly, and we do not know what it means on the ground.*

Relative uncertainty propagates in quadrature for independent sources, which
is right here because the dominant terms — scale error and measurement error
— genuinely are unrelated. `Quantity.range()` is what a reviewer reads:
`87.4 ft [85.7, 89.1]` beats `87.4 ft (confidence 0.9)` because it is in the
units of the decision.

### `spatial.py` — point-pattern measures

Deliberately PURE: it takes bare coordinates, never a `DrawingIR`, so the
maths is testable with no drawing in sight and cannot accidentally depend on
how a symbol was found. numpy only — scipy is not a planlens dependency and is
not needed, since all-pairs distances vectorise trivially at drawing scale and
the convex hull is Andrew's monotone chain in about thirty lines.

**Why here and not in a discipline package:** every trade asks the same
question about a different noun — borings, piles, columns, trees, light poles,
parking stalls. "How far apart are these?" is a drawing-review primitive. What
stays with the discipline is the JUDGEMENT (*is 45 ft adequate for this
structure?*), which needs standards this module deliberately knows nothing
about.

**The design decision:** there is no key named `average_spacing` or
`mean_spacing`, at any depth, and a test walks the entire returned structure
asserting so. The phrase is ambiguous and the readings genuinely diverge — on
a *perfectly regular* 3x3 grid the conventions differ by 2.45x
(nearest-neighbour 10.0 vs density-equivalent `sqrt(400/9)` = 6.67). If the
most uniform arrangement possible spreads that far, silently picking one for a
clustered real scatter is indefensible. The caller gets all three
(`nearest_neighbour`, `density_equivalent`, `pairwise`), each with its own
`definition` string, plus `convention_spread.ratio`.

Degenerate cases are answered as themselves, never as zero:

- coincident points are merged with `n_duplicates_merged` reported — a
  double-plotted symbol would otherwise contribute a nearest-neighbour
  distance of zero and drag the mean down hard;
- fewer than three non-collinear points returns `None` for area-based
  spacing, because "undefined" and "zero" are different claims;
- a concave or nearly linear footprint (borings along a street frontage) is
  flagged `hull_may_overstate_area` by comparing the hull against the
  minimum-area enclosing rectangle — reported as a flag, never used to change
  a number.

Clark-Evans `R` is reported with its edge-effect caveat rather than a
correction: on the 3x3 grid it reads 3.0 where infinite-grid theory says 2.0,
which is exactly the small-`n` upward bias the caveat describes.

## LLM surfaces

Two, as of planlens 0.4.0:

- **`planlens.tools.ReviewToolkit`** (in this package, framework-neutral) —
  the whole-document tools over `planlens.document`: `open_document`,
  `document_structure`, `document_page_map`, `read_document`,
  `search_document`, `find_quantities`, `document_markups`,
  `annotate_document` (the only one that WRITES: review comments onto a copy
  of the PDF), `log_grid`, `document_roles`, `render_page_thumbnails`,
  `render_page`, `render_region`. The same set serves over MCP
  (`planlens.mcp_server`), generated from the same specs. See
  `planlens/document/DESIGN.md`.
- **The geotech app's drawing adapter** — the drawing-geometry tools below
  still live in `GeotechStaffEngineer/funhouse_agent/adapters/drawing_ir_adapter.py`;
  moving them into `planlens.tools` is the next step on the open list.

That adapter exposes five methods and
**caches the IR server-side keyed by a `handle`** (a full IR can be large):

- `digitize_drawing(file_path, source=auto|dxf|pdf_vector|raster, …)` → `handle`
  + summary/stats (+ PDF `scale_candidates`, `has_text`). The full IR is
  never returned.
- `query_drawing(handle, query, params)` → one slice (`allowed_values` on the
  query name; per-query required/allowed params validated). Includes the
  whole composition family + `entities_ending_near`/`text_anchored_geometry`.
- `get_entities(handle, ids)` → exact coordinates for specific ids.
- `snip_region(file_path, output_path, bbox, frame=ir|pdf, marks, …)` →
  zoomed PNG crop on disk (converts IR bottom-left coords to the render
  frame; set-of-marks supported) for a follow-up `analyze_image`, or use
  the one-step `render_region` vision tool.
- `search_drawing_set(file_paths, pattern|construct, pages, min_confidence)`
  → every page of one or more PDF/DXF files: per-file/per-page counts +
  compact match locations ("how many times does X occur in this set");
  pages with no text layer are flagged `no_text_layer` (zero counts there
  are inconclusive — SHX).

`drawing_ir` is registered in `MODULE_REGISTRY`, so it is a directly-callable
analysis-layer tool for the primary agent (it is an I/O tool, not a reference).

## Downstream

`planlens.document` joins to this layer through the frame conversions in
`planlens/document/frame.py` (`to_ir_point` / `from_ir_point`): a text line,
table or markup box found by the document layer can be handed to the
geometry queries, and a geometry result rendered in the document frame.

In the geotech app, the IR is a superset of what `geo_project` /
`slope_stability` / `fem2d` ingestion needs. Wiring `geo_project` ingestion to consume a confirmed IR (with
its provenance quarantine for anything below confidence 1.0) is the natural
follow-up — the schema already carries the provenance + confidence that
quarantine keys on.
