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

## Capability status (kept honest)

- **Proven on real agency sheets**: bubble callouts (40/40 count match
  on a dense municipal standard detail), region rendering, endpoint /
  text-anchored queries, multi-page drawing-set search.
- **Proven on synthetic fixtures, in progress on real sheets**: leader
  and dimension detection score 100% recall / 100% precision on fixture
  families, but many production plot workflows draw arrowheads as
  micro-dot fill clusters and letter text as stroked outlines (no text
  layer at all). Fill-cluster arrowhead detection and an OCR leg for
  stroked-text sheets are active development.
- **Best-effort tier**: revision clouds (drafting-practice dependent).

## Install

```
pip install planlens            # DXF + vector-PDF ingest
pip install "planlens[raster]"  # + raster/scanned-sheet tracing
```
