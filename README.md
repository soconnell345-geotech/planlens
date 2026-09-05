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
  recovery on no-text-layer plots (88-92% truth-text coverage, median
  coordinate error 1.4-7 pt on the validation sheets), plot-transform
  fitting (0.02-0.03 pt rms on rotated real plots; guarded against the
  degenerate-scale and chance-match regimes — random anchors on a dense
  10k-entity sheet now return None in 60/60 trials while every true fit
  still passes), and native DXF annotation ingest (LEADER/MULTILEADER/
  DIMENSION/ATTRIB as first-class entities at confidence 1.0, surfaced
  by find_leaders/find_dimensions as evidence "native_dxf"; the
  ground-truth extractor lives at planlens.dxf.truth and reproduces the
  committed validation corpus exactly).
- **Partially proven on real sheets**: leader detection reaches 21/25
  native-truth tips on the validation set (11/25 before the 2026-09-05
  arrowhead-representation work; 0/25 before the plot-transform fit).
  The 2026-09-05 gain came from accepting 3-vertex OPEN arrow chains —
  real plotters draw an arrow outline minus one whole edge, in both
  base+leg and chevron flavors — behind a shape gate (near-equal legs,
  slender base, arrowhead-scale size) that keeps SHX glyph strokes
  out. Dimension detection on the same sheets reaches 13/16 native
  defpoints (from 1/16) via a split-shaft pairing leg: two collinear
  opposed-arrow half-shafts around a centered text gap (the dominant
  real plot style), plus the outside-arrows narrow style, both with
  witness-line corroboration; proposal ends are the arrow apexes (the
  CAD defpoints). Witness lines now require arrowhead-scale length
  (stipple fragments no longer corroborate), and on no-text sheets an
  un-corroborated or cluster-only proposal is capped at confidence
  0.45 — the worst sheet's default-threshold dimension output went
  from 44 proposals at ~0 precision to 11 with 8 touching native
  truth (the survivors flagged as false-vs-native include what appear
  to be manually-drafted dimensions the native truth cannot see).
  Residual misses are tips with no plotted arrow fragments at all,
  dots inside stipple below any principled density gate, and a few
  dimension layouts (witness-crossing verticals) not yet modeled.
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
