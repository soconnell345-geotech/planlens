# Changelog

## 0.13.0 — 2026-10-09

- **A callout's aim ignores markups drawn after it (2026-10-09, the app's
  live smoke wave 3).** `extract_annotations` judged what an original
  callout is "aimed at" against every markup on the page, including boxes a
  reviewer (or the app) added later, so a callout read as pointing at the
  new box. The aim now considers only markups drawn earlier (by date, else
  by order in the PDF) and the content under the leader.

- **Markups on rotated pages, and labels that read with the page
  (2026-10-09, the app's live smoke wave 2a).**
  - **Rotated pages.** `markup_writer` rotated the page box a second time,
    so on 90 and 270 degree pages labels, circles and callout boxes landed
    away from their marks (labels on a sheet's title text). The writer now
    uses `page.rect` as it is. Tests write every mark kind on 0/90/180/270
    pages and read each back with `markups()`.
  - **Label reading direction and placement.** A label reads the way the
    page's nearby text reads. A new optional `label_reads` (up/down) covers
    pages with no text layer. A label goes to the side of its mark that
    covers the least drawing.
  - **`measure` on a point of a plan with a scale** says `scale_known: true`,
    names the scale, and points to `kind='distance'` with `to=`.

- **Markups and `find_like` after the Foundry brief 4 review (2026-10-07).**
  Each changes behaviour an agent sees, and is to be measured live before it
  ships.
  - **`find_like`: linework through the example is not the mark.** Ink that
    runs straight through the example box and on past its edge (a grid line
    under the lettering, a wall, a rule, a leader's shoulder) is left out of
    the template (`findlike._crossing_lines`), and the result's `example`
    says how much (`linework_left_out`). On the tag fixture the callout whose
    lettering sits on a heavy grid line gave 367 and 400 candidates as its
    own example; now 54-67, every callout found. A clean example's template
    is unchanged; a box holding only a line is refused.
  - **`write_markups` reads the obvious guesses.** A `color` / `colour`
    (each kind has its own colour), kinds `comment` / `text` (a sticky note)
    and the other names in `KIND_ALIASES`, and an `anchor` object holding the
    anchor are read as what they mean, with a note per markup in
    `WriteReport.adjusted`, instead of refusing the whole call.
  - **`target`** on a markup: what the mark is on, in a few words or as
    printed — for a placement check to compare with when the comment is a
    request rather than the thing's name. Not drawn.
  - **One thing marked twice is flagged**: `WriteReport.duplicates`
    (`duplicate_marks`) lists marks of one kind saying the same thing whose
    boxes overlap by more than half, and the `annotate_document` result says
    what to do about them.
  - The `render_page` / `render_region` tool descriptions say a `dpi` only
    makes the image smaller.

- **Visual scales: geometry says where, the caller says what.** A page's own
  scales are found from its drawing, so a position read off a scan, a chart
  or a plan comes back as a value with its +/-, its provenance and what it
  snapped to. numpy and PyMuPDF only (no OpenCV, no scipy); planlens still
  never calls a model.
  - **`planlens.document.scales`** — the `Scale` primitive: a fitted map from
    a position along a (deskewed) axis to a quantity, linear or log10, with
    its anchors, residual, anchor rule, confidence and a 95 % +/- (the fit's
    prediction error at the point, the anchor rule's allowance and the
    position's own error). `Reading` carries value, +/-, unit, confidence and
    a display rounded no finer than the print. Adapters from a ruler, a PDF
    viewport, a stated scale and two points; `fit_points` / `fit_labels`
    refuse uneven runs, drop ONE misread label and name it, and refuse two.
  - **`planlens.document.raster`** — page pixels as geometry: render with a
    megapixel cap, Otsu and a permissive line mask, skew from the long rules,
    solid and dashed lines to a fraction of a pixel (dashes must be regular
    and stand alone: a row of text is not a dashed rule), components, text
    blobs, solid markers by eroded cores, column runs. `findlike`'s `_ink`
    moved here.
  - **`planlens.document.scalefinder.find_scales(doc, page, values=None)`** —
    every scale on a page as a `PageScales` of frames: depth rulers on logs
    (from text, OCR / Azure DI text, or label BLOBS on a scan with no text);
    gridded, dotted (by projection) and tick-only plots, log axes found from
    the decade pattern of their gridlines alone; profiles (stations and
    elevations as separate scales); plans (stored /VP viewports, graphic
    bars, stated notes, N/E grids) compared with each other, stored > bar >
    stated, agreeing within 2 % or flagged. Labels are tied to what they mark
    by an anchor rule — ticks beside the labels, else frame lines on round
    values (the labels' constant offset measured and taken out), else the
    label centre with an allowance — and the rule is recorded. A scan with no
    text comes back with `needs_values` and the label boxes; the caller reads
    them and passes `values`. A sketch not drawn to scale is refused.
    The finders do not assume where a column sits: the depth column is the
    one whose blobs step evenly down the body of the form.
  - **`planlens.document.measuring.measure(doc, page, where, kind, ...)`** —
    snap a rough box to the drawn thing near it (`line`, `lines`, `point`,
    `edge`, `curve` at an axis value, `distance` to a second box, `text`) and
    read it through the page's scale. One candidate is taken; two or more are
    listed and none chosen; a window of 25 pt or more (or a small mark asked
    of a wide view) lists and never snaps; nothing to snap to reads the box
    itself with its location error; no scale means page points, said plainly.
  - **`log_grid` reads scanned logs.** Its raster leg finds the columns from
    the rules, the ruler from the label blobs (`needs_values` /
    `values={page: [...]}`), the anchor rule and every stratum line, solid or
    dashed, each layer top with its +/- and evidence; OCR or DI text is
    placed through the fitted scale. On vector logs a ruler whose labels have
    ticks beside them is refitted through the ticks, and one whose labels sit
    consistently off the frame lines by 0.75 pt or more has that offset taken
    out; otherwise it is left exactly as it was. `LogGrid.needs_values`,
    `.scales`, `Ruler.plus_minus`, `Layer.plus_minus` / `.evidence`.
  - **`Quantity.plus_minus`** (absolute, 95 %), carried through `scaled`,
    `to`, `range`, `to_dict` and `__str__`.
  - **A `measure` tool** in `ReviewToolkit` and the MCP server: no box lists
    the page's scales and what they wait for; a `bbox` or a `view` +
    `image_box` (pad defaulting to the view's location error) measures;
    mistakes come back as instructions; every result fits the size limit.
  - **`planlens.testing.visual_scale_fixtures`** — synthetic pages with every
    answer stated: 20 log variants (vector, scan, OCR, Azure DI, rotated 270,
    feet, continuation, unframed, dashed, ticks, regular sample numbers), a
    pit sketch not to scale, 12 plot variants (grading, ticks only, dotted,
    reversed, log y, two charts, a broken gridline; vector and scan), a
    profile, semilog / log-log chart families and 7 plan variants (note and
    bar, ticks bar, stored viewport, N/E grid, re-plotted at half size,
    scans).
  - **Measured.** The no-model harness (in the app repository,
    `module_work/scales_harness/`) over 48 fixtures and four rough-box
    regimes: 4,404 readings, 99.93 % inside their +/-, 0 wrong snaps without
    alternatives, 2 of 2 sketches refused, a single misread label dropped 6
    of 6 and two refused 6 of 6. On a private scanned report (counts and
    error sizes only): ten log sheets, 10 of 10 labels found on each, 32 of
    32 stratum lines found with no extra, error median 0.007 m and largest
    0.018 m against +/- about 0.018 m; four vector sheets tied to their ticks
    (labels 0.12 pt off them, layer tops moved 0.002 m); two pit sketches
    refused; twelve grading sheets read, every plotted point within 0.4 % of
    its printed table value and 0.01 of a decade in size.
  - **For the app's side (steps 6-8, 2026-10-08):**
    - `log_grid` the TOOL takes `values={page: [...]}` and says
      `needs_values` (with a note) first, so a scanned log's labels can be
      read by the caller and the call repeated; its spec says so.
    - Label values may be given **as printed** (`"1.0"`, `"12+50"`): they
      are parsed (`scalefinder.label_values`) and the print's resolution
      kept, so a reading read through them is never written finer than the
      print. A plain number still works.
    - A `values` list of the wrong length is refused with the counts
      ("8 value(s) for 10 label box(es)"), on the scale and in `log_grid`'s
      warnings, instead of looking as if none had been given.
    - **Speed** (worst private scanned page 13.7 s -> 6.9 s for the whole
      page; a second box on a page 0.3-0.6 s): the decade-pattern fallback
      (`scales._log_decades_by_pairs`) is vectorised with numpy and gives
      the same answer to the bit (pinned against the old loop); the page's
      closed frames, the ink projected across each, the ticks on each and
      each run's axis model are remembered on the page; and `measure`
      looks for plot frames only among those holding its box
      (`find_scales(near=...)`; a whole-page result already made serves
      any box).

## 0.12.0 — 2026-10-08

Released from branch `release/0.12.0` (cut from 08a1d53). Measured before
release on Palantir Foundry (the app's brief 4, GPT-5.4 and GPT-5.6 Sol,
0.12.0rc1): marks placed from zooms landed 0.1-3.8 pt from their tags,
quote anchors on a CAD notes column landed on their line, and `find_like`
on the numpy matcher gave the same hits as OpenCV. The visual-scales work
above came after it and is not in 0.12.0.

- **Marks land where the thing is (live check on Funhouse, 2026-10-07).**
  - **A `view` + `image_box` anchor is judged by the view it came from.** A
    box read off a rendered image is only as good as the view: boxes read
    off whole-sheet images were measured 14-90 pt from 10 pt tags (GPT-5.4
    and GPT-5.6), boxes read off views of 80-350 pt 0.2-5 pt. `write_markups`
    now SKIPS, with the reason and what to do, a mark whose box was read off
    a view wider than `VIEW_ANCHOR_MAX_PT` (300 pt) when the mark is under
    `VIEW_ANCHOR_MIN_FRACTION` (a quarter) of the view's longer side, and a
    mark whose `image_box` is the whole view (`WHOLE_VIEW_FRACTION`, 95 % both
    ways — an agent passed `[0, 0, 999, 999]` of a zoom and got a ring round
    the window). Per mark; the rest still go on. A callout's box beside its
    `points_at` only places its text and is not judged. `MarkupSpec` keeps
    `view` and `image_box` beside the converted `bbox`.
  - **A quote on a multi-line CAD block lands on its own line.** A notes
    column stored as ONE hidden SHX string has one box and no word boxes, so
    a quote from note 4 was anchored at the column's left edge, half-way
    down: 50-80 pt above the quoted line on sheet 10.31A. The rows of ink
    inside the object's box (rendered grey, annotations off) now say where
    the printed lines are, and the quote's place in the string, weighed by
    how much lettering each row holds, says which it is on; AutoCAD's habit
    of storing a hanging-indent paragraph's first line twice is undone first.
    On 10.31A all eight test quotes land on their own row, and the 8.33 %
    callout now points at (60, 227) on the line at y 224-230. A one-row
    object, or lettering that does not run in rows across the box, keeps the
    old anchor.
  - **A sticky note hangs from its spot on a rotated page.** On `/Rotate`
    90, 180 and 270 MuPDF hung the icon one icon size (16 pt) right, down or
    both of the spot; the displayed box is now read back and corrected.
  - The `annotate_document` description says to anchor a found thing by the
    view and image_box of a ZOOMED look in which it is legible.

- **`find_like` runs where OpenCV cannot load.** On every government host
  this runs on (Palantir Foundry, Funhouse/Databricks), the OpenCV wheel's
  bundled OpenSSL fails the FIPS self-test and aborts the interpreter, so
  since 0.11.0 `find_like` raised `ImportError` there and hosts hid it: it had
  never run where its users are (a live check on 2026-10-07 had GPT-5.4
  placing circles 40-80 pt off tags it could have had exact boxes for). It
  now has a second matcher in numpy alone that computes the same score as
  OpenCV's `matchTemplate(TM_CCOEFF_NORMED)`: the page in overlapping FFT
  tiles correlated with the zero-mean example (the numerator outright),
  window sums and sums of squares from integral images (exact integers),
  windows with no contrast scored 0, OpenCV's hill-top rule inside each tile,
  and a half turn as a convolution with the quarter turn's spectrum. The
  example's scales come from a numpy port of `cv2.resize` (area shrink
  pixel-exact, bilinear enlargement within one grey level on under 1 % of
  pixels), and the contact sheets are drawn with numpy and PyMuPDF.
  **Measured against OpenCV** (2026-10-07, every search at the defaults: 13
  scales x 4 turns, 150 dpi): the same hits and the same callout / legend /
  unanchored counts on the 3-sheet tag fixture (219 hits), the app suite's
  24-sheet set (1,705) and a 34x22 in sheet of four tiled fixtures (294);
  scores within 3.9e-5 (mean 6e-7). Only hits whose scores TIE exactly (the
  legend repeats the same strokes) can come back in another order: float32
  and float64 break a tie differently. Seconds a page, best of rounds on one
  laptop:

  | set | OpenCV, 1 worker | numpy, 1 worker | OpenCV, 4 workers | numpy, 4 workers |
  |---|---|---|---|---|
  | 3 sheets 11x17 (4.2 MP) | 6.2 | 8.2 | 4.4 | 7.0 |
  | 24 sheets 11x17 | 7.6 | 7.9 | 3.4 | 5.4 |
  | 1 sheet 34x22 in (16.8 MP) | 24.6 | 30.3 | — | — |

  Beyond the page raster itself, the numpy matcher's working memory is one
  FFT tile and the template spectra of one scale per page worker, whatever
  the page size.
  - **Choosing:** `PLANLENS_FINDLIKE_BACKEND` = `auto` (default: OpenCV where
    `planlens.opencv.available()` says it loads, numpy otherwise) | `numpy` |
    `opencv`; `find_like(..., backend=)` and `like_sheets(..., backend=)`
    override it, and the result carries `"backend"`. OpenCV asked for by name
    where it cannot load raises `ImportError` with the reason.
  - **Asking:** `planlens.document.findlike.available() -> (ok, reason)` says
    whether a search can run here at all: true wherever numpy and PyMuPDF
    are; false only when the switch asks for OpenCV by name and it cannot
    load, or names no matcher. It does not test-load OpenCV unless asked for
    it by name. `resolve_backend()` names the matcher a search would use.
  - A box inside solid ink (no paper in the example) is refused with a
    `ValueError`; OpenCV scored such a template 1 against every window.
  - The OpenCV path returns exactly what 0.11.0 returned (checked hit for
    hit). Only `find_like` changed: the raster drawing-IR leg and OCR still
    need OpenCV and still raise `ImportError` on those hosts.

## 0.11.0 — 2026-10-04

- **OpenCV is test-loaded before it is loaded** (`planlens.opencv`). On a
  host whose OpenSSL enforces FIPS mode, loading OpenCV's native library
  aborts the interpreter (Palantir Foundry, 2026-10-03: exit -6 from
  `find_like`), with no exception to catch. Before the first import in a
  process, `planlens.opencv.available()` tries it in a child interpreter; if
  the child dies, `find_like`, the raster IR leg and OCR raise `ImportError`
  with the reason instead, and a host can leave those tools out.
  `PLANLENS_CV2_PROBE=0` imports directly.
- **A markup goes where the tool said the thing is, not where a model
  guessed.** A field session (2026-10-01) asked for red circles round tags an
  agent had found by looking; the circles went on at coordinates the agent
  wrote from nothing, nowhere near the tags, and an earlier box was ~50 pt off
  from the agent's own conversion of a vision result's 0-999 box. So:
  - a markup can be anchored by **`view` + `image_box`** — the rect a rendered
    image shows and the thing's box on the 0-999 grid over it, exactly as a
    look reports them — and `write_markups` converts it
    (`image_box_to_page`); no arithmetic is left to the caller;
  - **`box`** and **`page_bbox`** are accepted as names for `bbox` (an agent
    sent `box` twice and lost a call each time); a box given two ways, or a
    `view` without its `image_box`, is refused with the reason;
  - the tool description says never to place a mark at a location that did
    not come from a tool result for that page.
- **`circle` markups and visible labels.** `kind: "circle"` draws a red ring
  (a PDF Circle annotation) through the corners of the box it is given, so
  the whole box is inside it, at least 14 pt across. A box, circle or
  highlight can carry a **`label`**: a few words drawn ON the page beside the
  mark, as a borderless FreeText tied to it by `/IRT` + `/RT /Group` (the
  PDF's "one markup" link; planlens reads it back as reply-linked to the
  mark). PyMuPDF writes a default leader (`/CL`) on every FreeText it
  creates; a label carries none, so it is not read back as a callout aimed at
  the page corner. Written rows now report a circle's `target` (the box it
  was drawn round) and a label's `label_bbox`.
- **The drawing-sheet advice names no tool it cannot vouch for.** Every
  `! look:` line on a drawing sheet ended "and use drawing-geometry tools for
  measurements" — tools the toolkit does not offer and a host may not have
  (the app's Document Review page has none), so the line sent a model after
  a tool that was not there. It now says only what is true of the page: the
  text lines are labels in drafting order; view the sheet or a region of it.

## 0.10.1 — 2026-09-26

- **`find_like` is one optional tool, not a workflow.** 0.10.0's render note
  for a page whose lettering is not in its text layer told the model to
  "box ONE copy and call find_like" on every such sheet — a special-purpose
  search pushed from every drawing render. The note now says only what is
  true of the page (text search cannot see it; zoom to read small lettering;
  never conclude absence from a whole-sheet view), and the tool's spec says
  when it applies: when every occurrence of one repeated mark across many
  sheets is wanted.

## 0.10.0 — 2026-09-25

- **Find every copy of one mark — `find_like`.** A reviewer asked a real
  85-sheet security set where the "GCE" penetrations are. Its 0.06 in
  lettering is drawn as strokes (AutoCAD SHX): no text layer to search, and 4
  px tall in a whole-sheet image, where a vision model read GCE as QCE and
  found none of 34. CAD draws its lettering the same way every time, so
  `Document.find_like(page, bbox, pages)` (and the tool `find_like`, over
  MCP with the rest) takes a box round ONE copy and image-matches it on every
  page at 13 scales (0.5x-2x — a legend is often lettered larger) and four
  rotations, peak-only and with fragment-aware merging, in about 2 s a sheet.
  Each hit is labelled from the drawing's own geometry: **legend** (a ruled
  row, or the same place on most same-size sheets), **callout** (a drawn path
  with a vertex at the tag reaching away from it — `points_to` is its far
  vertex, the place the tag is about) or **unanchored**. Matching cannot tell
  GCE from GCG, so hits are candidates: `Document.like_sheets(hits)` makes
  numbered, upright, enlarged contact sheets for a vision model to read. On
  the new stroke-lettered fixture (`planlens.testing.tag_fixtures`): every
  on-plan GCE found once, every leader tip within 0.07 pt, the legend set
  apart, and a legend example drawn 40 % larger still finds the plan's tags.
- **`original` whenever it is honoured.** `budget_from_probe` only offered
  `original` for a patch-model `high`; Funhouse's GPT-5.4 caps `high` like a
  tile model yet keeps the whole image at `original` (714 / 714 / 4,234
  tokens, measured 2026-09-25), so it was sent 768 px images when 2,560 were
  there for the asking.
- **Lettering drawn as lines is said out loud.** `text_size` ignores text
  under 2 pt and needs 40 characters of lettering (a stroked sheet's stray
  microscopic text had reported "0.6 pt"); renders report `text_chars`; and a
  render of a page whose lettering is not in its text layer says so and
  points at `find_like` and zooming, instead of at a text layer it does not
  have.

## 0.9.0 — 2026-09-24

- **Which budget is a fact about the model, and a deployment name is an
  alias.** Found on the first Tiny Apps run: `tinyapp-gpt-medium` is
  GPT-5.1, a TILE model that cuts every image to 768 px on its short side and
  ACCEPTS `detail="original"` only to ignore it. New in
  `planlens.document.budget`: `budget_for_model(name)` looks a model name up
  (the name a response says answered, never an alias), and
  `budget_from_probe(small_high, large_high, large_original)` reads the
  budget off the image tokens of three blank squares — tile, 2,500-patch or
  6,144-patch, and whether `original` is really honoured — by RATIOS, so a
  model's token multiplier cancels and a model no table knows still works.
  New budget `gpt-5.2-high` (GPT-5.2 / GPT-4.1-mini: 2048 px, 6,144 patches).
- **Too small to read, said out loud.** `Document.text_size(page)` is how
  tall the page's small lettering is (the character-weighted 25th percentile
  of its line heights); every render's info carries `text_px`, that lettering
  in the image's pixels; and the toolkit's render notes warn below 12 px —
  read the words from the text layer, zoom on a window about N pt across
  (N from the lettering and the budget: `legible_window`), and do not call
  the page unreadable before both. On the bridge sheet that prompted it, 5 pt
  lettering arrived at 4.8 px on GPT-5.1; a 238 pt zoom brings it to 16 px.

## 0.8.0 — 2026-09-23

- **Renders sized to the model that looks at them.** New
  `planlens.document.budget`: named image budgets (`openai-high`,
  `openai-original`, `gpt-4.1-high`, `claude`, `claude-hires`) with the
  published limits, `fit_size` and `image_box_to_page`.
  `Document.render(budget=...)` makes the largest image the model reads without
  shrinking it, and re-renders a region from the PDF to fill it.
  `ReviewToolkit(image_budget=..., image_format=...)` applies it to
  `render_page` / `render_region` / `render()`. The default is unchanged.
- **Zoom on what was seen.** `render_region` also takes `image` (an earlier
  render's `image_path`) plus `image_box` in `px` or `norm1000` (a 0-999
  grid). Every render's note states the image's pixel size, the top-left
  origin and the box convention for the model's family.
- **`fmt="auto"`** keeps the smaller of PNG and JPEG: PNG for vector drawings,
  JPEG for scans. The MCP server sends a JPEG as `image/jpeg`, and gains
  `--image-budget` / `--image-format` so a host sizes the pictures to the
  model it runs.
- **Fixed:** a render now IS the size its info reports. A whole-number dpi
  made the "2000 px" page 2016 px, and the outward-rounded pixel rect could
  pass `max_pixels`.

## 0.7.0 — 2026-09-22

- **The review goes back onto the PDF.** planlens has read a reviewer's
  markups since 0.3.0; it can now write them. New
  `planlens.document.markup_writer.write_markups(source, output, markups)` and
  the tool `annotate_document` beside it (over MCP with the rest): a caller
  hands over a list of small specs — a sticky NOTE, a HIGHLIGHT over the words
  quoted, a BOX round a region, a CALLOUT with a leader pointing at a spot, or
  a REPLY threaded onto an existing comment — and gets a NEW PDF whose
  annotations Bluebeam and Acrobat list beside a person's. **Every markup is
  anchored, never placed by eye.** A comment about text names the words: they
  are found exactly first and then through the same fuzzy fallback
  `search_document` offers, and the line's own word boxes narrow the highlight
  to the words quoted rather than painting the whole column. A comment about a
  drawing takes a box or a point in the displayed frame — the frame
  `read_document(with_locations=true)`, a markup and `render_region` all
  already speak, so a box goes back on with no conversion, on a `/Rotate 90`
  sheet as on a portrait page. **A quote nobody can find is REFUSED with a
  reason and named in the report**, because a comment on the wrong words is
  worse than a comment the caller is told did not go on. The source file is
  never opened for writing and `output == source` is an error; a second call to
  the same `output` adds to the first call's file.
- **What the writer reports is what the READER will see.** Every written markup
  carries the box `Document.markups()` gives back for it, not the one that was
  asked for: a Square is placed 1 pt smaller each way so that MuPDF's own
  padding lands it exactly where it was asked for, while a Highlight's rect
  keeps the appearance margin around its quads (shrinking them would stop the
  highlight covering the words) and a callout's rect encloses its leader, as
  the PDF specification asks. A callout's text is written with
  `rotate=page.rotation`: a FreeText lays its text out in the UNROTATED box, so
  on a `/Rotate 90` sheet the comment came out sideways and clipped to its
  first few words — with the rotation set, the rendered callout is
  pixel-identical to the same callout on an unrotated page, measured at
  /Rotate 0, 90, 180 and 270.
- **`--root` now confines writing as well as reading.** The MCP server's one
  new tool writes a file, so `ReviewToolkit` gained `output_root`: with one
  set, a relative `output_path` resolves inside that tree and anything
  escaping it is refused. Without one a relative path goes beside the rendered
  images and an absolute path is honoured, because a host that names one has
  already decided where its files belong. `ReviewToolkit(author=...)` is who
  a comment is signed by when the call names nobody.

## 0.6.0 — 2026-09-17

The boring-log release: a ruled log form is read as the coordinate system it
is, and a line a form draws twice is returned once.

- **A rule drawn in pieces is one rule.** A form's lines are very often not
  single strokes: the line under a header row is drawn once per stretch
  between the columns it has to skip, so on one corpus template it arrives as
  four collinear pieces with a 14 pt gap where a narrow column's tick marks
  live. Measured stroke by stroke no piece crossed the form, the page looked
  as though it had no line under its header at all, and every column on three
  such sheets came back unnamed. Collinear pieces sharing a coordinate and
  leaving a gap no wider than one narrow column are now joined before any
  length is measured — for column edges, for the header band and for stratum
  lines alike.
- **A dash after a letter names a sample and is not a minus.** "S-7" is
  sample seven, not minus seven. A dash between digits was already a
  separator ("5-9-12"); a dash after a letter is one too, and only a dash
  that starts a number, or follows a space, is a sign.

- **A line drawn twice is one line.** Some forms and printer drivers draw a
  string a second time at the same place to fake a bold weight.
  `planlens.document` now returns it once, dropped in TEXT EXTRACTION rather
  than in any one reader, because counting it twice doubles a page's words,
  returns two search hits for one occurrence and hands a model "9 9 10 10"
  where the page reads 9, 10. The page map reports how many were dropped as
  `n_overprinted_lines`. Measured over a PRIVATE 7,829-page corpus of 38
  geotechnical reports, which is not in this repository: 4,751 such lines on
  523 pages of at least twelve reports, up to 2.5 per cent of a report's
  lines. Nothing published moved as a result — re-scoring the page-role rules
  over the same 4,147 hand-labelled pages afterwards returned every rate
  identical, held-out accuracy 0.880 included. For `log_grid` it was fatal
  rather than untidy — "5, 5, 10, 10, 15, 15" holds no strictly rising run of
  three, so the depth scale was refused and the sheet came back with no
  depths at all.
- **A title block never names a column.** A header candidate must lie inside
  the column it would name: a line that crosses column boundaries is the form
  talking about the SHEET, not about that column, and reading one as a
  column's name renamed a depth scale after an elevation. Such a line is not
  discarded — `fields` still reads it.

- **A boring log read as the grid it is.** New
  `planlens.document.loggrid.log_grid(doc, pages)`, and the tool `log_grid`
  beside it (over MCP with the rest). Give it the pages of ONE log — its
  continuation sheets included, as `document_roles` already groups them — and
  it returns the COLUMNS with their x bands and what the page's own header
  calls each of them over 22 canonical names in English, French and Spanish;
  the depth RULER fitted to the printed scale, with its unit and the residual
  of the fit; every remaining line of text as a ROW carrying its column, its
  depth, the depth range its box covers, its numbers and its box; the LAYERS
  the description column is cut into; and the FIELDS printed outside the body
  (boring number, ground surface elevation, dates, hammer type, driller,
  total depth, groundwater). No templates and no trained model: the columns
  come from the ruling lines the form is drawn with, the ruler from the one
  band of numbers that steps evenly down the page, the layers from stratum
  rules, printed depth ticks and a classification symbol that has been proved
  top-aligned first. Values come back AS PRINTED and are never parsed into a
  meaning — a blow record stays `"5-9-12"` and an N value `"N=21"` — because
  naming the column and fixing the depth is the whole job and what a value
  means needs the page image anyway. **A page whose ruler cannot be found
  returns its cells with no depths and says so in `warnings`**, along with a
  scan's softer column edges, a page stored rotated, a diagonal watermark
  left out, a unit the page never states and sheets of one log drawn at
  different scales. Measured against fifteen logs from fifteen real reports
  of a private corpus that is not in this repository, hand-transcribed into a
  private ledger: six were OPEN during development and the rules were tuned
  on them, and nine were scored BLIND. On the open six, ruler and unit 6/6,
  blow records and N values 57/57, layer tops 34/34, index values 35/36,
  header fields 63/68; on
  the blind nine, 9/9, 6/8, 50/50, 22/26, 8/17 and 33/54 — and one of those
  nine is the scanned page the optical path was built on, so read it as eight
  and a half. **The blind column is the one that forecasts anything.** One of
  the fifteen is a tabular list of borings with no depth scale on it, where
  finding no ruler and saying so IS the answer, and it is the only sheet
  without one. **No sheet of the fifteen is read at a wrong scale.** What the
  blind column is short of is the index properties, and two sheets that state
  their depth unit nowhere at all.
- **Signed numbers, and a header that outweighs a tick count.** A leading
  dash is a minus sign, not something to strip: a column of elevations below
  datum reads "-2.5, -4.0, -5.5", and throwing the signs away turned it into
  a RISING series that could be, and on one sheet was, chosen as the depth
  scale in place of the ruler the page prints. A trailing dash is still a
  tick mark ("13-"). A monotone falling series is an elevation scale and is
  only claimed under a header that says elevation. And what the header says
  now outweighs even steps and tick count together, because the columns that
  fit a straight line without being the scale are many — contact depths,
  elevations, sample numbers, a plot axis — and several carry more ticks than
  the ruler does.
- **The headers gINT's default template prints**, which many firms ship
  unchanged, are in the vocabulary: "Depth Scale (m)", "Elev. (ft)",
  "Number", "Type", "Recov. (in)", "Penetr. resist. BL/6in", "N-Value
  (Blows/ft)", "Sample Description", "MATERIAL SYMBOL", "Remarks (Drilling
  Fluid, Depth of Casing, Water Level)" and their variants. With them, a
  header now NAMES itself before it qualifies itself: the earliest phrase
  wins and the longer of two starting together, so a remarks column that
  mentions depth and a water level is a remarks column. New
  `planlens.testing.build_imperial_log` / `build_metric_log` /
  `build_log_without_ruler` build the same synthetic form on a 5 ft ruler, on
  a 1 m ruler and with no ruler at all, with the answers.
  See `planlens/document/DESIGN.md`, "Log grid".

## 0.5.0 — 2026-09-17

The what-is-this-page release: a report now says what each of its pages IS and
which of them are read together, a text layer that is there but WRONG is called
out instead of quoted as prose, and a filled-in form is no longer reported as a
copy of the sheet beside it.

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
  hand-labelled pages of 14 real geotechnical reports: page accuracy 0.92 on
  the nine the rules were developed on and 0.88 on the five set aside, and on
  those development reports, precision/recall 0.95 / 0.92 boring_log, 0.95 /
  0.89 test_pit_log, 0.96 / 0.99 lab_test, 0.98 / 0.95 narrative, 1.00 / 0.99
  calculation. Neither figure is held out — the rules had been tuned against
  all fourteen — so read the out-of-sample one instead: **0.79**, or 0.86
  where a second label is equally defensible, on 70 pages of fourteen further
  reports never used in development. What it gets wrong is written down
  beside that: `dcp_log` recall is 0.54 on scanned forms, `other` is a
  residual rather than a class, and a page with no text at all cannot be
  placed by its title. New `planlens.testing.build_synthetic_report` builds a
  22-page report carrying eleven of those roles, with the answers.
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
- **When the page and its appendix tab disagree, the page wins -- and when
  neither knows, the answer says so.** These four principles were written
  against fifty hand-labelled pages of ten reports whose misses a reviewer
  handed over (0.74 to 0.76 strict, 0.80 to 0.84 accepting alternates), and
  then scored on seventy pages of fourteen reports that stayed shut
  throughout: **0.71 to 0.79 strict, 0.77 to 0.86 accepting alternates.** A
  cue in the page's own largest
  type always beats its tab, and a cue in its running bands beats it once the
  evidence is more than a mention; the cue tables run on every page that has
  words, because a plan is measured as a form, a figure, a scan or a mixed
  page depending on how it was plotted. Prose now needs narrative evidence --
  the running band, the printed numbering or the density of prose -- rather
  than merely sitting in front of the first tab, and a document that prints
  no tab at all is marked `no_dividers` and inherits nothing. A tab naming
  several things chooses on the page's shape or answers `other` with the
  candidates listed. A page inside a bound-in report keeps
  `appended_report` and records what it is as `inner_role`. In sample this
  costs what it should: `test_pit_log` recall 0.925 to 0.885, because a tab
  no longer falls back on the first thing it names, and `narrative` recall
  0.959 to 0.948 in exchange for precision 0.965 to 0.976.

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
  0.0064 and 23 pages with a broken encoding at 0.248 and above, and the floor
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
