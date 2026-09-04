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
(auto-detects sideways-plotted sheets and PDF page rotation).

`planlens.ir.align.fit_plot_transform` fits the model-space-to-plot
transform (axis rotation + scale + offset) from anchor geometry, so
native CAD entities can be located on the plotted page.

## Capability status (kept honest)

- **Proven on real agency sheets**: bubble callouts (40/40 count match
  on a dense municipal standard detail), region rendering, endpoint /
  text-anchored queries, multi-page drawing-set search, OCR text
  recovery on no-text-layer plots (88-92% truth-text coverage, median
  coordinate error 1.4-7 pt on the validation sheets), plot-transform
  fitting (0.02-0.03 pt rms on rotated real plots).
- **Partially proven on real sheets**: leader detection reaches 11/25
  native-truth tips on the validation set (up from 0/25). Independent
  verification attributes that gain to the plot-transform fit plus
  fold-blind triangle alignment — the newer stroke-cluster arrowhead
  model contributes no additional matched tips yet and is groundwork
  for the sparse-dot regime, not the source of the number. Residuals
  are tips with no plotted arrow fragments at all, or dots inside
  stipple below any principled density gate.
- **Not yet usable on stroked/no-text real plots**: dimension
  detection. On such sheets the confidence renormalization admits
  arrow/hatch misreads at high confidence (measured ~0 precision
  against native truth on the worst validation sheet at default
  thresholds); real plots also split the dimension line around
  centered text, which the current model does not pair. Both are
  documented next steps — treat real-sheet dimension output as noise
  until then. (Synthetic/fixture dimension detection is proven.)
- **Best-effort tier**: revision clouds (drafting-practice dependent).

## Install

```
pip install planlens            # DXF + vector-PDF ingest
pip install "planlens[raster]"  # + raster/scanned-sheet tracing
pip install "planlens[ocr]"     # + optical text for stroked/scanned sheets
```

The `[ocr]` extra installs RapidOCR + onnxruntime with PP-OCR models
inside the wheel (no runtime downloads; all-permissive licenses:
Apache-2.0/MIT/BSD). Clean-environment weight is roughly 170 MB —
rapidocr requires full `opencv-python` (~112 MB), which coexists
uneasily with the `[raster]` extra's `opencv-python-headless` (two
distributions own the `cv2` namespace; installing both works but
uninstalling either can break the other). Resolving the opencv-variant
story is a known open item.
