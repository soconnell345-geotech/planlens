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
  arrowhead (endpoint within 0.75x arrowhead scale of the candidate;
  measured 2.2-5.4 pt on every genuine leader vs p50 9.6 pt for
  sign-passing letter junk). Across the seven sheets with no native
  annotations: 295->2, 273->10, 91->6, 77->19, 57->23, 151->20,
  137->38 at default confidence (some survivors on the detail sheets
  may be REAL manually drafted leaders that native truth cannot
  record — render-verify before treating counts as pure FP).
  Dimension proposals on the pure-notes sheets are now ZERO at 0.3+.
  Caveats that remain true: the scorer's greedy 18-pt match can ride
  a nearby capped proposal, confidence 1.0 does not preclude glyph
  junk on dense SHX sheets (verify visually), and manually drafted
  dimensions are invisible to native truth (independent 2026-09-05
  render-adjudication found ~14/15 semantic precision on the
  curb-ramp sheet's "false" dims — most were real).
- **Best-effort tier**: revision clouds (drafting-practice dependent).

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
