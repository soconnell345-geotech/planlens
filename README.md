# planlens

Drawing & submittal intelligence: deterministic geometry extraction plus
confidence-scored annotation constructs from PDF and DXF construction
drawings.

## Architecture

Two layers, one principle — **geometry says WHERE, vision says WHAT**.
LLM/VLM vision is unreliable on precise geometry, so a deterministic
extractor owns every coordinate and an LLM (if you attach one) owns only
semantics.

1. **Primitive layer** (`planlens.ir`): a unified intermediate
   representation — Line / Polyline / Arc / Circle / Text with
   coordinates, layer, provenance, and confidence — ingested from DXF
   (`ezdxf`, confidence 1.0), vector PDF (`planlens.pdf`, confidence
   1.0), or raster images (OpenCV, confidence < 1.0, `raster` extra).
   Slice queries (bbox / angle / text / layer / nearest / endpoint) let
   a caller request exactly the geometry it needs.
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
pip install planlens            # DXF + vector-PDF ingest
pip install "planlens[raster]"  # + raster/scanned-sheet tracing
pip install "planlens[ocr]"     # + optical text for stroked/scanned sheets
```

The `[ocr]` extra installs RapidOCR + onnxruntime with PP-OCR models
inside the wheel (no runtime downloads; all-permissive licenses:
Apache-2.0/MIT/BSD).

**OpenCV variants — pick per environment.** Every published rapidocr
distribution (`rapidocr-onnxruntime` 1.x and the unified `rapidocr`
2/3.x alike) hard-requires the full GUI `opencv-python` (~112 MB),
while the `[raster]` extra uses `opencv-python-headless`; pip cannot
express "either variant", and installing both leaves two distributions
owning the `cv2` namespace (works, but uninstalling either can break
the other). Decision:

- **Desktop / notebook**: `pip install "planlens[raster,ocr]"` as
  above — the GUI build wins the namespace and everything works.
- **Server / headless deploy** (Databricks, TinyApps — no GUI libs):
  skip the `[ocr]` extra and install the engine without its metadata
  deps; the OCR leg needs only the cv2 APIs headless provides
  (verified end-to-end in a clean headless-only venv, 2026-09-05):

  ```
  pip install "planlens[raster]"
  pip install --no-deps "rapidocr-onnxruntime==1.2.3"
  pip install "onnxruntime>=1.7" pyclipper shapely pillow pyyaml six
  ```

  Pin the rapidocr version you validated — `--no-deps` means ITS
  dependency list is being supplied by hand, so an unpinned upgrade
  could silently need something new. (1.2.3 is the newest wheel that
  installs on Python 3.14 today; newer versions keep the same runtime
  set — re-verify when bumping.)

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
