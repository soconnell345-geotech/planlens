# planlens.document — design notes

## Why this exists

planlens began as a drawing-sheet reader: lines, arrows, leaders, dimensions.
The goal it serves is broader — an LLM harness helping an engineer **review any
architecture / engineering / construction document**. A real design submittal
runs from a transmittal letter through drawing sheets, calculations, boring logs
and program printouts, and carries the review itself as PDF markups. An LLM
cannot review that from a single sheet's geometry, and it cannot reliably read
geometry from pixels. This subpackage turns the whole document into located,
attributed data; `planlens.ir` stays the geometry layer for drawing sheets.

## What it is

```
document/
  frame.py        the one coordinate contract + conversions to the IR frame
  model.py        TextLine / TextBlock / Table / Markup / PageSummary / PageContent
  pdf_text.py     the PDF text layer (default text source)
  annotations.py  review markups + hidden CAD text
  tables.py       find_tables wrapper
  scale.py        the measurement calibration the PDF itself stores
  quantities.py   the numbers the text STATES, with their units
  classify.py     coarse page kinds with evidence
  imagehash.py    a page's picture in 64 bits, for duplicates on scans
  structure.py    headers/footers, printed page numbers, dividers, duplicates,
                  segments (the constituent documents)
  advice.py       when text is not enough: "! look:" statements
  document.py     Document: page_map, segments, page, search (exact or
                  fuzzy), quantities, markups, text, render,
                  render_thumbnails
  azure_di.py     Azure Document Intelligence result -> text source (optional)
```

## The frame contract

Every coordinate is **PDF points in the page's displayed orientation**: origin
top-left, y down, `/Rotate` applied — what a viewer shows, what
`page.get_pixmap()` renders, and what `planlens.ir.render.render_region(frame="page")`
expects. A box from this package can be rendered and shown to a vision model
with no conversion.

PyMuPDF's ordinary `get_text`, `annot.rect` and `annot.vertices` are in the
UNROTATED page space (verified: identical numbers on /Rotate 0/90/270 while
`page.rect` swaps). Annotation geometry is converted with `page.rotation_matrix`
(`frame.to_display_*`). Text is read through a display list instead, which MuPDF
runs in the displayed orientation — verified on /Rotate 0/90/180/270 and an
offset crop box: boxes identical to rotating ordinary output, rendered ink inside
every box. **Never apply the rotation matrix to display-list text**; doing so
double-rotates (a regression caught by the tests on the rotated fixture sheet).

`DrawingIR` uses a different frame (unrotated geometry y-flipped with the
rotated height). `frame.to_ir_point` / `from_ir_point` convert, and are pinned
against `ir_to_page_point` on a rotated page.

## Text

- **Lines** carry text, box, reading direction (degrees CCW as displayed),
  font, size, bold/italic, colour and block. Directions within 0.5 deg of a
  cardinal snap to it (CAD plots write near-horizontal text at 359.9).
- **Annotations are excluded from the text layer.** Ordinary `get_text` reads
  the text drawn by annotation appearances, so a reviewer's FreeText comment
  comes back as drawing text — verified on a real submittal, where deleting a
  callout removed its words from `get_text()`. Markup text reaches the reader
  only as attributed `Markup` objects.
- Characters from fonts with no Unicode map (U+FFFD) are counted and warned
  about; the string should not be trusted on those pages.
- Order is content-stream order: reading order for word-processor output,
  drafting order for CAD plots. No reading-order reconstruction yet.

## Markups

A `Markup` is what a person placed: kind (FreeText, Polygon, Line, Stamp…),
Bluebeam/Acrobat intent and subject, author, ISO dates, comment text, box,
vertices, colour. Relationships come only from explicit PDF data:

| field | source |
|---|---|
| `points_at` | FreeText callout: the start of `/CL` (PDF spec: the end at the target). Line/PolyLine: the end with a line ending, when exactly one end has one. Verified by rendering real Bluebeam markups: callout starts sit on the commented text, the arrowhead end on its target. Never inferred from proximity. |
| `points_to_markup` | the smallest other markup whose box CONTAINS `points_at`, skipping the markup's own /IRT companions (Bluebeam ties the cloud a responder draws around the original comment to the response by /IRT; the tip lands inside that cloud too). On the real submittal this links contractor replies to reviewer comments. |
| `in_reply_to` / `replies` | the PDF `/IRT` link and its inverse. |
| `appearance_text` | text the annotation's OWN appearance draws (`annot.get_text`), kept when it differs from the comment field: review-stamp wording, what a snapshot stamp copied. Per-annotation, so overlapping markups cannot swap text (an earlier box-containment version did). Capped at 400 chars. |

## Hidden CAD text

AutoCAD plots SHX lettering as strokes (no text layer) and adds an invisible
Square annotation titled `AutoCAD SHX Text` holding the real string. These
become `TextLine`s with `source="cad_hidden_text"` and `rotation=None` (the
annotation records a box, never a direction; the payload says `"unknown"`).
Strings also present in the text layer at an overlapping box are dropped.
Measured: 320 on a real submittal's drawing sheets; 66 on one of the ten
Mecklenburg validation sheets, none on the other nine — so it complements OCR,
it does not replace it.

## Forgiving search (2026-09-16)

An exact search that finds nothing says one of two things, and the tools
cannot tell them apart: the term is not in the document, or the term is there
with a letter wrong. The second is common on exactly the pages a reviewer
cares about — a scan read optically, SHX lettering recovered from strokes, a
callout retyped from a photograph. "Not found" is the one answer a review tool
must never give wrongly, so `Document.search(fuzzy=True)` offers the second
reading.

**What it is.** The SAME candidates the exact search scores — a block's lines
joined, each unblocked line, each hidden CAD string, each markup's comment,
author and subject — compared with `rapidfuzz`'s partial ratio instead of a
regular expression. Every hit carries its `score` (0-100) and its `source`
(text layer, hidden CAD text, OCR, Azure, markup), and hits come back best
score first, then by page. `rapidfuzz` is the optional `text` extra; without it
the call raises with the install command in the message, and
`fuzzy_search_available()` lets a caller ask before offering the advice. Exact
mode is untouched: no `score`, no reordering, and it still stops at the first
`max_hits`. Fuzzy mode cannot — ordering by score means there is no such thing
as the first `max_hits` — so it costs a full pass over the requested pages.

**Measured before the default was chosen**, on a real submittal's drawing
sheets (260 pages, 7,261 search candidates): 15 real callout strings off those
sheets, each corrupted three ways — one substituted letter, one dropped
letter, one transposition — and 10 words confirmed absent from the document,
every candidate scored against every query.

| threshold | of 45 corrupted callouts, source line found | unrelated hits per query, median / max | of 10 absent words, any hit |
|---|---|---|---|
| 70 | 45 | 10 / 68 | 0 |
| 75 | 45 | 7 / 44 | 0 |
| **80** | **45** | **5 / 40** | **0** |
| 85 | 45 | 3 / 28 | 0 |
| 88 | 45 | 2 / 28 | 0 |
| 90 | 43 | 1 / 28 | 0 |
| 95 | 21 | 0 / 15 | 0 |

Three findings changed the design.

- **A candidate shorter than the query must be scored whole.** `partial_ratio`
  slides the SHORTER of the two strings over the longer one, whichever that
  is, so a one-character line — a grid bubble, a dimension tick — scores 100
  against any query containing that character. In the first run that alone put
  a median of 120 hits under each of the ten absent words, at EVERY threshold
  up to 95: no threshold could have fixed it, because the mismatch was in the
  part of the query nobody compared. A candidate shorter than the query is now
  scored with the full ratio instead, and the absent words return nothing from
  70 up.
- **The score falls with the query's LENGTH, not its wrongness.** One wrong
  letter in a 20-character callout scores about 95; the same one letter in a
  5-letter word scores 80, and a dropped letter in a 5-letter word scores 75 —
  its ceiling, whatever the threshold. Measured on single words off the same
  sheets: 45/45 recovered at 75, 41/45 at 80, 22/45 at 85. So the default
  serves the phrase and the tool says the rule for the word: `min_score` near
  75 for a query under about 8 letters, which the `search_document` schema and
  its no-hit advice both state.
- **Unrelated hits cost tokens, not the answer.** Whenever the source line
  cleared the threshold it ranked FIRST for 44 of the 45 corrupted callouts
  and in the top five for all 45 (37 of 41 first for single words). That
  asymmetry is what sets the default low rather than high: a threshold too high
  loses the answer outright, while one too low ranks it first and pads the tail.

`DEFAULT_FUZZY_MIN_SCORE` is therefore **80** — every corrupted callout
recovered with 9 points of margin on the worst (88.9), every absent word still
silent with 10 points of margin (nothing scored above 70), and a median of 5
unrelated hits ranked below the answer.

## Quantities (2026-09-16)

A review is a comparison. The report says "borings at approximately 40-foot
centers"; the plan draws borings `planlens.ir` can measure. The calculation
says "171 kN pre-load"; the schedule on the sheet says something else. This
package could already measure the drawing while the narrative stayed an
undifferentiated wall of text, so `Document.quantities()` makes the text's own
numbers addressable — value, unit, kind, the raw wording, any hedge on it, and
the page, line ids and box that locate it.

The rules are `planlens.ir.measure`'s rules. **A bare number is not a
mention**: without a unit there is nothing to compare, and a value that might
be feet or metres is worse than silence. **Nothing is converted**: `6300 mm`
stays 6300 mm, and `7'-6"` becomes 7.5 ft only because the two halves are one
value in one unit. **A range is one mention**, carrying `value_to`, because
"40 to 60 ft" is a single statement and splitting it loses that. The ONE
exception to the first rule is an elevation: "EL. 1684" is a measurement whose
unit the page leaves to its datum, so it is reported with `units=""` and
`units_known=False` rather than given a unit nobody wrote. `kind` adds `slope`
to the obvious list, because "2H:1V" is among the most-compared statements on
a geotechnical sheet and does not belong in `other`.

### The bake-off: native regex vs quantulum3

Before choosing, both were run over 40 invented geotechnical sentences
covering every claimed form, and 40 real sentences off a submittal's narrative
and calculation pages (2,677 candidates on 236 pages; 20 sampled from prose,
20 from program printout, because the calc package is 245 pages of printout
and an even sample never reaches the narrative). Scored by the rule above: a
united mention whose value is stated is a hit, a united mention of a value
nobody stated is a false positive, and so is a bare number.

| corpus | extractor | TP | FN | FP | precision | recall | crashes |
|---|---|---|---|---|---|---|---|
| A — 40 invented | regex | 37 | 1 | 1 | 0.97 | 0.97 | 0 |
| A — 40 invented | quantulum3 | 23 | 15 | 14 | 0.62 | 0.61 | 9 |
| B — 40 real | regex | 16 | 15 | 0 | 1.00 | 0.52 | 0 |
| B — 40 real | quantulum3 | 11 | 20 | 114 | 0.09 | 0.35 | 2 |

**quantulum3 is not adopted, and the decisive number is not in that table.**
The condition was that it recover mentions the regex misses. Across both
corpora it produced 10 united mentions the regex did not, and **every one of
them was wrong**: 1 volt and 1 volt (the V in `2H:1V` and `1V:3H`), 1 byte
("Task 1B"), 8 atomic mass units ("8 da"), and six range MIDPOINTS — 27.5 ft
for "20 to 35 feet", and five more on real text — which are values that appear
nowhere in the document and would be compared against a drawing as if someone
had written them. It also raises `ImportError` mid-parse on surfaces its
disambiguator cannot resolve without the heavier `[classifier]` extra (9 of
the 40 invented sentences, including a plain `7'-6"`), and it knows none of
the units this domain runs on: psf, pcf, tsf, ksf, cy, sf, kN/m³. It was
installed into the venv for the measurement and uninstalled after.

Two findings from the real corpus changed the native extractor.

- **Scientific notation.** A calculation printout writes `2.540E-07 M`, and
  the first version read that as SEVEN METRES — it found the `07` and the `M`.
  A plausible number seven orders of magnitude wrong, in the right units, is
  precisely the invisible failure `planlens.ir.measure` exists to prevent, so
  the number grammar now carries an exponent.
- **A hedge belongs to the value it touches.** "MAXIMUM" opens the next label
  in `2.540E-07 M MAXIMUM NUMBER OF ITERATIONS 300`, and an earlier window-
  based reading attached it to the tolerance. A qualifier must now be
  adjacent — immediately before the value, or closing the clause after it —
  and a following capitalized word rejects it. A note set entirely in capitals
  loses its trailing hedge that way, which is the intended trade.

**What is not read, measured rather than assumed:** the regex's 15 misses on
the real corpus are all ONE form, `Label (unit): value` — "Axial Capacity Pc
(kip): 437.0" — the unit-first shape of structural-calculation printouts,
concentrated in 3 of the 40 sentences. quantulum3 misses it too (it crashed on
two of those three), so adopting it would not have closed the gap. Reading it
needs the label grammar of those programs and is deliberately left for when
that is measured rather than guessed.

Prose false positives were also measured rather than assumed. The largest
single source is the preposition "in": "borings were advanced 12 in the
northern block" reads as a 12-inch depth unless the following word is checked
against a stop list, which is why one exists.

## Scale (2026-09-16)

`planlens.ir.measure` refuses to turn page points into feet without a resolved
scale, and reading a scale off a title block or a graphic bar is inference. But
when someone calibrates a sheet in Bluebeam ("Store Scale in Page") or measures
with Acrobat's tools, the calibration is **written into the file** as structured
data (ISO 32000-1 §12.9): the page dictionary's `/VP` array of Viewport
dictionaries, and a `/Measure` dictionary on each measurement markup. That is
the drafter's own statement, and `scale.py` reads it.

**What is read.** From `/VP`: each viewport's `/BBox`, `/Name`, and `/Measure`.
`/Subtype /RL` (rectilinear) gives `/R`, the ratio string, and `/X` / `/Y`,
number formats whose `/C` is the conversion factor and `/U` the unit, plus `/D`
and `/A` for distance and area units. `/Subtype /GEO` is detected and reported
as `kind="geo"` without parsing the geodesy — it is survey control, not a
drawing scale. From a markup: its `/Measure`, its `/IT` (`/LineDimension`,
`/PolyLineDimension`, `/PolygonDimension`, and whatever else a producer writes,
recorded unchanged), and the value its `/Contents` states.

**The frame.** `/BBox` is in unrotated user space and is converted with
`frame.to_display_bbox` like everything else, so a viewport box renders as-is.
Verified on a real `/Rotate 270` sheet: the stored box fits the unrotated
mediabox (612x792) and overflows the displayed rect (792x612). A markup's
derived length is computed on the **unrotated** vertices, because `/X` and `/Y`
are per-axis and a rotation swaps the axes; a length must not change with how
the page is displayed, and the tests assert it across /Rotate 0/90/180/270.

**What `/C` means, and how that was established.** `/C` multiplies, and its
basis is one PDF user-space unit (one point): `x_per_point = C`, in units of
`/U`. The real anchor is the one rectilinear viewport the corpus contains,
which stores `/X [<</C .01389/U( )>>]`. A point is by definition 1/72 inch of
paper, `0.01389 x 72 = 1.00008`, and the unit label is blank — so the file is
stating the untouched 1:1 identity, in inches, per point. Had `/C` meant
"user-space units per `/U` unit" the number would have been 72. No real
CALIBRATED (non-identity) rectilinear page has been run through this code, so
`page_viewports` cross-checks `/R` against `/X` on every file it reads and warns
above a 2% disagreement; that is how a misreading would announce itself on the
first real calibrated sheet, rather than producing a plausible wrong number.

**Measured, before any of this was designed** — a real Bluebeam-marked-up
submittal (260 pages), a ten-sheet public drawing corpus, and every other PDF in
the working tree (162 files):

| finding | number |
|---|---|
| documents with a populated `/VP` | 2 of 162 |
| pages with `/VP` populated / empty (`[]`) on the real submittal | 1 / 7 |
| its one viewport | `/Subtype /GEO` (survey control, no ratio) |
| public sheets with a viewport | 1 of 10, `/Subtype /RL`, the 1:1 identity |
| measurement markups anywhere in the corpus | 0 of 426 annotations |
| whole-page viewport coverage of its page | 0.845 |
| cost of the `/VP` scan over 260 pages | 0.02 s |

Three of those changed the design. **An empty `/VP []` is normal** — seven
consecutive drawing sheets carry one — so it returns no viewports and no
warning. **A viewport is not a scale**: `is_calibrated` is False for a
georeferenced measure, for unreadable factors, and for the 1:1 default, so the
`! look:` advice still tells the model to read the title block. And the scan is
cheap enough that `viewports` sits on `PageSummary` (the page map) rather than
behind a full page read: 0.02 s for all 260 pages on its own, and adding it to
the page map moved the total by less than the run-to-run spread of the map
itself (10.3 s against a 11.8 s baseline on the same document, the difference
being noise). `advice.py` uses it there.

**No real Bluebeam-calibrated page has been run through this module.** The
calibrated path — a populated `/R`, a non-identity `/X`, a dimension markup with
its own `/Measure` — is built to the specification and exercised by
`planlens.testing.scale_fixtures`, which writes the viewport and the markup with
`xref_set_key` in raw PDF syntax at /Rotate 0 and /Rotate 90. When a real
calibrated submittal appears, re-run the probe against it first.

**Reading the raw syntax.** PyMuPDF's `xref_get_key` cannot index into an array,
so `/VP` arrives as a raw PDF string either way. `scale.py` lexes and parses the
small subset of object syntax these structures use and follows indirect
references through `xref_object(xref, compressed=False)` — `/Measure` is
indirect in one real file and inline in the other. It is deliberately tolerant:
anything unreadable becomes a warning and a missing field, never an exception.

**Out to the reader.** `PageSummary.stored_scale` is one line
(`1 in = 20 ft [viewport]`) and reaches the page map as `scale`, kept separate
from `scales`, which is what the page's own text says. A dimension markup
renders with its scale, the value it states, the length its path derives and any
disagreement between them — both, because agreement is the evidence the factors
were read right. `scale.to_quantity` is the bridge to `planlens.ir.measure`:
with a calibrated viewport it returns a `Quantity` in feet or metres at
confidence 1.0 whose `basis` names `pdf_viewport` or `measurement_markup` and
the ratio; without one it returns points with `scale_known=False`.

## Tables

`page.find_tables()` on the vector content. Rows are kept whole: PyMuPDF's
in-grid header guess is row 0, which on forms such as boring logs is a title
strip, so a header is reported only when found outside the grid (or marked by
Azure). Columns empty in every row are dropped and counted in `notes`. Cost is
the reason tables are page-level only: 30 s of the 36 s a full measurement pass
took over 260 pages.

## Page map

`classify.classify_page` from cheap measurements (text chars, CAD-text chars,
vector path count, image coverage, page area, ruling lines): `drawing_sheet`
(larger than tabloid with content), `scanned`, `form` (a ruled grid — at
least 12 long horizontal AND 12 vertical rules on a letter/tabloid page;
measured: boring logs 23-44 x 32-39, prose 0 x 0, program printouts under
10), `figure`, `text`, `blank`, `mixed`. Coarse on purpose; every row carries
the numbers and the rule. `needs_ocr` is separate from kind and is cleared when
the page's text came from an optical source. Measured: 4.5 s for 260 pages.
Headings: an Azure title/sectionHeading block if present, else the largest-font
text-layer line.

Each `PageSummary` also carries (2026-09-14, after the owner asked what else
the map could say): `n_words` and `text_density` (words per square inch —
prose 1.5-6, dividers and drawings 0.1-0.7), `n_images`, `ruling_h/v`,
`rotated_text_fraction`, the `header` and `footer` band text (top and bottom
8 %), the page number PRINTED on the page (`printed_page`, `printed_of`), a
`sheet` reference ("1 of 7", "S-101"), `scales` on drawing sheets, a
`divider_title` (APPENDIX / ATTACHMENT / ITEM / cover ... on a page under 60
words), `duplicate_of` (same kind, text and path count as an earlier page —
or, where the text cannot decide, the same picture: see "Duplicates on
scans", with `duplicate_rule` naming which) and the `segment` it belongs to.

`layers` (2026-09-16) names the optional-content groups the page's line-work
sits in — a plotted sheet's CAD layer names, the drafter's own words for what
the geometry is. They cost nothing: the map already read every path twice, to
count them and to count rules, and those two passes are now one, which made
the whole 260-page map measurably FASTER than before the names were collected
— four paired runs on one machine, a median of 5.4 s after against 8.7 s
before, on a run-to-run spread of 2.5-4 s. A real
submittal sheet can use dozens, so a row carries the first
`LAYER_NAMES_ON_ROW` names and, when it cuts the list, the total as
`n_layers`. `planlens.ir` reads the same names onto each entity, so a reader
who sees a layer on the map can ask the geometry queries for it by name.

### Structure (`structure.py`)

A stapled submittal is several documents, and nobody wrote a contents page
for the staple — but each constituent prints its own running header/footer and
its own page numbers, and those change exactly at the seams. `segments()` cuts
the page list where the running key changes (digits masked, numeric-only
fragments dropped, containment allowed so a package's page stamp matches the
longer footers inside it), at dividers, at page-size changes, between drawing
sheets and everything else (consecutive sheets never split — their bands are
full of labels), and where printed numbering restarts at 1 (a nested
document). A segment reports its pages, title (divider > running header >
footer with numbering stripped > first heading; a drawing set by its sheets),
kinds, `printed_pages`/`printed_of`, header, footer, sheets, first heading.

Measured on the real 260-page submittal: 29 segments — transmittal, review
letter, tabs, the seven-sheet drawing set as one segment, the calc package
stamped 1-245 with its Appendices A-E as dividers, the boring logs as one
`form` run, and four calc sheets recognised as nested documents by their own
"Page 1 of 4" numbering. `printed_pages` is what lets the agent turn "see
page 24 of the calcs" into a PDF page, and cite the printed number back.

### Duplicates on scans (2026-09-16)

`duplicate_of` was decided from a page's text and its path count, which is
precisely the evidence a SCANNED page does not have: two scans of one sheet
both carry no text and no linework, so neither was ever a duplicate of
anything. A reviewer with the file open in a viewer can see the repeat at a
glance. `imagehash.py` gives the same evidence in numbers — the page rendered
to a 9x8 grayscale grid in the DISPLAYED orientation, each pixel compared with
its right-hand neighbour: 64 bits, 16 hex characters, numpy and PyMuPDF only
(no `imagehash`, `Pillow` or `scipy`). MuPDF does the resize, by rendering
through a matrix straight to the target grid rather than scaling a big image
here. Annotations are left out of the render: a reviewer's cloud on one copy
must not hide that it is the same sheet. `duplicate_rule` on the page-map row
now names which rule fired, `"text"` or `"image"`; the hash itself is never
printed, because 16 hex characters tell a reader nothing.

**Measured before any threshold was chosen** — a real 260-page submittal and
the ten public Mecklenburg sheets:

| | distance (of 64 bits) |
|---|---|
| a page against ITSELF, re-rendered or re-opened | **0** (10 pages, two opens, 0 differ) |
| the closest pair of DIFFERENT pages the rule compares | **4** (two figures drawn off one template) |
| ... the next closest | 11, 15, 15, 17 (drawing sheets of one set) |
| the ten public sheets against each other | min 7, median 27, max 38 |
| consecutive boring-log pages (the text rule covers these) | min 1, median 7 |
| a rescan of ONE page (other dpi, JPEG quality, offset, skew) | 2-8 on a high-contrast sheet; 17-26 on the rest |

Three findings shaped the rule.

- **The claim is "placed twice", not "scanned twice".** A copy re-encoded
  through another resolution and JPEG quality drifts as far from its original
  as two different pages of one template sit from each other — the two classes
  overlap, and a finer grid does not separate them: at 16x16 = 256 bits a true
  copy of one sheet drifts 20-31 bits while two different boring logs sit 21
  apart, the same overlap one scale up. So the threshold is set for pages that
  RENDER the same, which is how a duplicate actually reaches an assembled
  submittal: an appendix bound in twice, one scan filed under two tabs, a
  sheet repeated in a set. `DUP_HASH_DISTANCE` is **2** — the midpoint between
  0 and the closest different pair at 4, two bits of margin each way. It is
  deliberately tight: a wrongly-claimed duplicate tells a reviewer to skip a
  page they have not read, while a missed one leaves the map as blind as it
  was before.
- **The picture never overrules the words.** Sheets off one border and title
  block differ by a sheet number and a few labels — most of what a reviewer
  needs and almost none of the ink. The synthetic submittal's two D-size
  sheets are *within* the threshold of each other, and the first build
  collapsed the drawing set into its first sheet. So when both pages carry
  enough text for the text rule to have hashed them and those hashes differ,
  no image match is allowed. The picture speaks where the text is SILENT.
- **A page with no picture gets no hash.** A page of one flat tone compares
  every pixel with an equal neighbour and hashes to all zeros — and so does
  the next flat page, which made three different near-blank pages "the same
  page" at any threshold. `MIN_GRID_SPREAD` (32 of 255) withholds the hash
  instead. Measured: every page the gate lets through spans at least 67 grey
  levels, and every near-uniform page at most 19.

**The gate is a cost decision, measured.** Hashing all 260 pages costs 2.3-3.3
s; the 13 pages that qualify cost 0.45-0.64 s. Over one second is too much to
spend on pages whose text already decides, so a hash is computed only where it
cannot: `needs_ocr`, kinds `scanned` / `figure` / `drawing_sheet`, or under 50
text-layer characters — and never on a `blank` page, because every blank page
looks like every other. In four paired page-map runs the gated cost sits
inside the run-to-run spread (median 6.1 s with against 5.8 s without, on runs
ranging 3.1-7.3 s). The 260-page document contains no duplicates and the rule
claims none; its 29 segments are unchanged, because segmentation reads
headers, numbering and page size and has never read `duplicate_of` at all — so
an image duplicate behaves in `segments()` exactly as a text duplicate does.

A page a viewer shows sideways or upside down is **not** called a duplicate:
the hash is taken in the displayed orientation like every coordinate here, and
a turned page is one a reviewer still has to look at. /Rotate 90 fails the
page-size guard before the hash is consulted; /Rotate 180 keeps the size and
is rejected on the picture alone (measured 31 bits apart on a public sheet).

### Thumbnails

`Document.render_thumbnails()` composes contact sheets — every page as a small
thumbnail with "<page> <kind> (<label>)" beneath, 6 x 8 per sheet, a red frame
on pages with review markups — the way a viewer's page panel shows a document.
A model can take in 48 pages in one image and pick out the plan, the logs, the
tables. Cost: 3.2 s for all 260 pages; six 910 x 1322 px images.

## Text sources

`Document(text_source=...)` replaces the PDF text layer on the pages the source
covers. Protocol: `name`, `covers(index)`, `extract(page, index, words)` ->
`(lines, blocks, tables_or_None, stats, warnings)`. Annotations always come
from the PDF.

`AzureLayout` wraps a `prebuilt-layout` result. planlens never calls Azure or
imports an Azure package; the caller runs the analysis (Funhouse SDK, Azure SDK,
REST) and passes the dict, or passes the function to `AzureLayout.analyze`.
`pages_needing_ocr(doc)` picks the pages worth paying for.

- Keys: camelCase or snake_case; polygons flat, pairs or `{x, y}` points (the
  Funhouse SDK's own helper reads camelCase while its integration test asserts
  snake_case — both occur).
- Frame, per page: the Azure page size is matched against the displayed and the
  unrotated PDF page (inches exactly, pixels by aspect); a page matching neither
  is scaled to the displayed page with a warning.
- Page numbers: when a subset was analysed, the result is reconciled against
  the pages sent (document numbering or renumbered from 1); anything else is
  refused rather than placed on the wrong page.
- Line confidence is the minimum of its words' confidences.
- Tables: explicit `columnHeader` cells in row 0 become the header; merged-cell
  positions stay `None`; a table belongs to the page it starts on.

No live Azure result has been run through it yet; the tests use hand-built
results in the documented shape.

## Text first, eyes second — and the tools say when (2026-09-14)

The owner's gut check of the first build: the layer was undervaluing the
model's own vision. Measured on the real submittal, the three pages a reviewer
most needs eyes on told the model nothing of the kind — a scanned plan figure
with four markups came back as two footer lines (the "no text layer" warning
fires only at zero text); a boring log as 113 fragments in drafting order and a
mangled grid; a plan sheet as 162 labels. The extractor KNEW (page kind, image
coverage, sparse grid) and did not say.

Now every result that touches such a page says so. `advice.page_advice` turns
the signals into plain statements — scanned / figure / drawing_sheet kinds,
image-bearing mixed pages, undecodable glyphs, a table whose detected grid is
mostly empty (`Table.n_cells` / `n_empty_cells`, counted BEFORE empty columns
are dropped), low-confidence optical words, markups that point at a spot —
and the tool layer prints them as `! look:` lines with the host's own
instruction for how to look (`ReviewToolkit.vision_hint`). `open_document`
lists `pages_to_view`; `document_page_map` flags `look`; a text-search miss
names `pages_not_searchable_as_text` so a miss there is not read as absence.

`Document.render(page, bbox, marks)` gives the image itself, in the displayed
frame, pixel-capped (`DEFAULT_MAX_PIXELS`: a 300 dpi D-size render is 70 MP
and took a notebook driver down once); with marks it renders from a fresh copy
so the cached document's text is never polluted by the drawn labels. The
toolkit exposes it as `render_page` / `render_region` (PNG files) and
`ReviewToolkit.render` (bytes) for hosts that hand images to the model
directly. An image file opens as a one-page document (`source_kind="image"`,
converted to PDF by MuPDF), so a photographed drawing is reviewed like a scan.

## MCP (2026-09-16)

`planlens.mcp_server` puts the same tools on the Model Context Protocol, so a
host discovers them instead of being programmed against them. It is generated
from `ReviewToolkit.specs("plain")` — one MCP tool per spec, same name,
description and input schema, dispatched through `call_json` — because a
hand-written second list is a copy that drifts: a tool added to `specs.py`
appears on the wire with no edit to the server.

Three things the wire changes, and nothing else. The pictures travel: any
result naming an `image_path` gets that PNG attached as image content (one
rule, not a per-tool table), so `image_view_hint` says the image is attached
rather than sending the model to a file it cannot open. The `! look:` hint
defaults to `DEFAULT_VISION_HINT`, which names `render_page` / `render_region`
— correct here, because on this surface those tools ARE the way to look,
unlike an app that hands pages to its own vision tool. And a toolkit error
(`{"error", "hint"}`, already the model's instruction for fixing it) becomes
an MCP tool error with the message intact; nothing reaches the host as a
crashed connection, and the calls run in a worker thread so a D-size render
does not hold the event loop.

The server opens the files the caller names, with the caller's privileges, and
authenticates nobody: the host decides who may run it, `--root` confines it to
one directory tree (`realpath` then prefix test, so `../`, an absolute path and
a symlink out are all refused the same way), and a shared HTTP deployment needs
authentication from the platform in front of it. The SDK (`mcp`) is an optional
extra pinned to its major version; v1 and v2 are different APIs.

## Not built yet

- RapidOCR (`planlens.ocr`) as a text source (it currently emits IR TextItems).
- The drawing tools (digitize / query / snip / search a drawing set) as
  planlens tools — the app's drawing adapter still owns them.
- Roles/headings from the PDF text layer; multi-column reading order; figure
  and caption detection; tables spanning pages.
