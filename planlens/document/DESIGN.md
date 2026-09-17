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
  imagehash.py    a page's picture in 256 bits, for duplicates on scans
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
- Characters from fonts with no Unicode map (U+FFFD) are counted, and when
  there are enough of them the page is marked **`text_reliable = False`** —
  see "An unreliable text layer" below.
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
score first, then by page. `rapidfuzz` is a core dependency (it was the `text`
extra until 0.4.0), imported at the moment fuzzy matching is asked for rather
than at module import; on an install trimmed by hand the call raises with the
install command in the message, and `fuzzy_search_available()` lets a caller
ask before offering the advice. Exact
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
to a 17x16 grayscale grid in the DISPLAYED orientation, each pixel compared
with its right-hand neighbour: 256 bits, 64 hex characters, numpy and PyMuPDF
only (no `imagehash`, `Pillow` or `scipy`). MuPDF does the resize, by
rendering through a matrix straight to the target grid rather than scaling a
big image here. Annotations are left out of the render: a reviewer's cloud on
one copy must not hide that it is the same sheet. `duplicate_rule` on the
page-map row now names which rule fired, `"text"` or `"image"`; the hash
itself is never printed, because 64 hex characters tell a reader nothing.

**Measured before any threshold was chosen**, on the 9x8 grid the first build
used — a real 260-page submittal and the ten public Mecklenburg sheets. The
figures below are all at 64 bits; the grid is now 17x16, and what that changed
is the section after next.

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
  overlap, and a finer grid does not separate them: at 256 bits a true copy of
  one sheet drifts 20-31 bits while two different boring logs sit 21 apart,
  the same overlap one scale up. So the threshold is set for pages that RENDER
  the same, which is how a duplicate actually reaches an assembled submittal:
  an appendix bound in twice, one scan filed under two tabs, a sheet repeated
  in a set. A page placed twice is 0 bits away at any grid size.
- **The picture never overrules the words.** Sheets off one border and title
  block differ by a sheet number and a few labels — most of what a reviewer
  needs and almost none of the ink. The synthetic submittal's two D-size
  sheets are *within* the threshold of each other (0 bits apart at 256 as at
  64), and the first build collapsed the drawing set into its first sheet. So
  when both pages carry enough text for the text rule to have hashed them and
  those hashes differ, no image match is allowed. The picture speaks where the
  text is SILENT.
- **A page with no picture gets no hash.** A near-blank page hashes to
  almost-nothing — and so does the next near-blank page, which made three
  different near-blank pages "the same page" at any threshold.

#### The grid is 17x16 because 9x8 cannot read a FORM (2026-09-16)

The first build hashed to a 9x8 grid, and on a 202-page report that grid
claimed **69 pages of a 73-page laboratory appendix as duplicates of the
first**. Rendered, they are different sheets: sieve and Atterberg results for
different samples, a density table, a moisture table. What they share is one
printed template, one diagonal DRAFT watermark and very little ink — the
numbers that make a sheet itself are a few percent of it. An ingest that
skipped duplicates would have dropped the entire appendix, which is the worst
failure this field can have: it tells a reviewer to skip pages nobody read.

Measured over the 2,628 pairs that appendix makes, and over five real
documents (202, 455, 97, 94 and 260 pages) plus the ten public Mecklenburg
sheets:

| rule | pairs within 2 bits, 73 sheets that are all DIFFERENT | closest pair |
|---|---|---|
| 8x8 = 64 bits | **1,260 of 2,628** | **0** |
| 8x8 on an INK-NORMALISED render | 1,763 of 2,628 | 0 |
| 16x16 = 256 bits | **0 of 2,628** | **5** |
| 24x24 = 576 bits | 0 of 2,628 | 11 |

Thresholding the render first — so the light watermark and the paper tone drop
out and only ink remains — was the obvious fix and is the WRONG one: it is
measurably worse than doing nothing, because the template's own rules and
boxes survive the threshold while the data does not. **Resolution, not
contrast, is what tells two filled-in forms apart.** 16x16 is the first grid
that separates them and 24x24 buys nothing further, so `HASH_SIDE` is 16.

`DUP_HASH_DISTANCE` stays at **2**, re-derived at the new width: a page placed
twice is 0 bits away, and the closest genuinely different pair anywhere in
that measurement set is 5 (two laboratory sheets) — two bits of margin below,
three above. The old figure it replaces was 4, on the 260-page submittal at 64
bits; that pair re-measures at 24 bits of 256.

**The emptiness gate had to change with it.** The old floor was the grey RANGE
of the whole grid (`MIN_GRID_SPREAD`, 32 of 255), and a single printed border
defeats it: an appendix divider is white paper inside a rule, the rule spans
the full range, and the page sails through. At 9x8 that page was withheld for
an unrelated reason — the coarse grid averaged the border away — so the flaw
only surfaced when the grid got finer, and "APPENDIX D" and "APPENDIX E"
became the same picture at a distance of 0. The floor is now
`MIN_CONFIDENT_BITS`: how many of the 256 bits sit between two cells whose
greys differ by more than `INK_CONTRAST` (16 of 255) — bits set by a picture
rather than by rounding. Measured over 198 gated pages of those five documents
and ten sheets, the two populations do not touch:

| | confident bits (of 256) |
|---|---|
| near-blank pages — dividers, cover sheets, a slip-sheet (12 pages) | 0, 1, 2, 2, 2, 2, 2, 2, 3, 4, 4, 9 |
| every page carrying a figure, a form, or a scan of one (186 pages) | **10 and up** (median 25) |

The floor sits in the gap at **8**. Across those five documents the new rule
claims no image duplicate at all: 69 claims become 0 on the report with the
appendix, and the other four claimed none before and none now.

**The gate is a cost decision, measured.** A hash is computed only where the
text cannot decide: `needs_ocr`, kinds `scanned` / `figure` / `drawing_sheet`,
or under 50 text-layer characters — and never on a `blank` page, because every
blank page looks like every other. The finer grid costs nothing: on the
260-page submittal, hashing all 260 pages measures 1.26 s at 9x8 and 1.26 s at
17x16, and the 13 pages the gate admits 0.27-0.29 s either way — the cost is
the render, not the grid. In four paired page-map runs the gated cost sits
inside the run-to-run spread (median 6.1 s with against 5.8 s without, on runs
ranging 3.1-7.3 s). The 260-page document contains no duplicates and the rule
claims none; its 29 segments are unchanged, because segmentation reads
headers, numbering and page size and has never read `duplicate_of` at all — so
an image duplicate behaves in `segments()` exactly as a text duplicate does.

A page a viewer shows sideways or upside down is **not** called a duplicate:
the hash is taken in the displayed orientation like every coordinate here, and
a turned page is one a reviewer still has to look at. /Rotate 90 fails the
page-size guard before the hash is consulted; /Rotate 180 keeps the size and
is rejected on the picture alone (measured 130 bits of 256 apart on a public
sheet).

### An unreliable text layer (2026-09-16)

A page with no text layer announces itself: `needs_ocr`, kind `scanned`, and
every tool says so. The page that does real damage is the one whose text layer
is THERE and wrong. An analysis-program printout bound into a report carries a
font with no usable Unicode map: the extraction succeeds, returns three
thousand characters, and half of them are U+FFFD. Measured on a 455-page
report, 22 pages are like this — on 14 of them 98% of the characters are
undecodable and the transcript is nothing but replacement characters.

Before this, `unmapped_chars` sat in the page's evidence and **nothing read
it**. The page was not `needs_ocr`, it was not a scan, it did not appear in
`pages_needing_ocr()`, and its transcript went to the model as prose. A model
asked what the calculation assumed answered from noise.

`PageSummary.text_reliable` is the page map saying so, with
`unmapped_fraction` as the number behind it. False means: this page has a text
layer, and it is not what the page says. Then `needs_ocr` becomes true (the
words must be read off the picture, exactly as on a scan), so
`pages_needing_ocr()` offers the page to OCR; `read_document` advice tells the
model the layer is unreliable and not to quote the text tools on it; and
`open_document` reports the pages under **`pages_with_unreliable_text`**,
beside rather than inside `pages_without_text_layer` — two failures whose
answers differ, and only the second can mislead a reader who does not look.

**The threshold is measured, not chosen.** Across five real documents (1,108
pages) only 53 carry any unmapped character at all, and they fall into two
populations with nothing between them:

| | unmapped fraction |
|---|---|
| a stray glyph — a bullet, a degree sign, a logo character (30 pages) | 0.0007 to **0.0064** |
| a broken encoding — program printouts, a laboratory checklist (23 pages) | **0.248**, 0.436-0.469 (8 pages), 0.968-0.994 (14 pages) |

`MAX_UNMAPPED_FRACTION` is **0.10**, in the empty 38x span between them:
fifteen times the worst benign page, two and a half times below the mildest
broken one. The mild "N characters could not be decoded" advice still covers
the benign pages and is REPLACED on an unreliable one, because two statements
about one problem read as two problems.

Tested on built fixtures only (`planlens.testing.build_unmapped_text_pdf`,
which draws character codes the font's own map sends to U+FFFD at a
proportion the caller sets). Deleting a `ToUnicode` entry does not work as a
fixture: MuPDF answers a missing one by substituting a font and guessing, so
the page comes back as confident nonsense rather than as U+FFFD.

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

## Page roles and work items (2026-09-16)

`roles.py`. The page map says what a page LOOKS like; this says what it IS —
narrative, a boring log, a laboratory sheet, a calculation printout, a report
bound inside another report — and groups the pages into the work items a
reader consumes one at a time. Rules over the evidence the page map and the
text extraction already produce; no trained model, and nothing keyed to a
firm, a project or a template.

### What decides a page, in order of authority

1. **The document's own structure.** An appendix tab STATES what its appendix
   holds ("APPENDIX B - LABORATORY TEST DATA", "PART 2 - FIELD BOREHOLE
   LOGS", "APPENDIX D - CALCULATIONS", and often a contents list beneath),
   and every page up to the next tab inherits it. A tab that hands its
   appendix to somebody else's document ("APPENDIX C - GEOTECHNICAL REPORT
   BY OTHERS") takes every one of that document's pages, whatever those pages
   look like, because it is one appended document.
2. **What the page's own title says.** Read from the page's largest two type
   sizes and its top and bottom bands, never from a keyword anywhere on the
   page: a laboratory sheet prints the boring and depth its sample came from,
   and a narrative page discusses the test pits. Where the running header of
   an appendix would decide the answer on its own ("Test Pit Logs and
   Photographs" on every page of it), the page's OWN title is used instead —
   the lines of its bands that do not repeat across the document.
3. **The page's shape**, as `PageSummary` measures it: ruled form, figure,
   scan, word density, images.

Four more rules decide what happens when those three disagree, and each was
measured before it was kept (2026-09-16, round 5):

- **A page that names itself beats its tab.** A cue found in the page's own
  largest type always wins; a cue found only in its running bands wins when
  it rests on enough evidence to be more than a mention
  (DECISIVE_WEIGHT) -- a log form carries a dozen field labels, a
  laboratory sheet naming the pit it sampled carries one. The cue tables run
  on any page that has words, because a plan comes back measured as a form,
  a figure, a scan or a mixed page depending on how it was plotted.
- **Sitting in front of the first tab is a position, not evidence.** A page
  is the report's prose when it also carries the narrative's running band,
  its printed numbering, or the density of prose. Where a document prints NO
  tab at all, nothing inherits anything, the narrative is only what carries
  the report's own running band, and the outline says no_dividers so a
  reviewer knows to look.
- **A tab that names several things chooses on the page, or says so.** A
  ruled grid with a depth column is one of the logs; a results form or a
  plotted result is laboratory work; a page that is mostly picture is
  photographs. Where the page's shape says nothing the answer is other
  with the tab's candidates listed and a confidence under 0.5 -- never a
  confident wrong role. A RUN of such pages closed on both sides by one log
  is filled in from the document's own ordering, at 0.55.
- **A page inside a report bound into this one is a page of THAT report.**
  Its role stays appended_report -- the role is the binding -- and what
  the page itself is goes into the evidence as inner_role, so the prior
  investigation's logs and laboratory sheets can still be made into items.

Four earlier rules carry most of the work and each exists because of a
measured failure:

- **A tab's declaration is read from its own type, not the whole page.** A
  tab page inherits the PREVIOUS appendix's running footer; read from the
  foot of the page, the new appendix is handed to the old one.
- **What repeats is boilerplate.** A top- or bottom-band line printed on
  three pages or more (compared with its digits masked, so a caption that
  counts still matches itself) is the document's running band, not anything a
  page is saying.
- **A document's appendix lettering is a sequence, and a nested document
  restarts it.** A report bound inside appendix C brings its own A, B, C, D,
  E; the outer report's next tab is D, and D following the inner E is the
  outer document speaking again, not the inner one going backwards. That is
  how the extent of an appended report is found without reading it.
- **A second volume announces itself with a contents page.** A title page
  part-way through a PDF, followed within three pages by a table of contents,
  opens a new volume of the same report — its narrative is narrative. A
  laboratory certificate headed "Certificate of Analysis ... submitted to"
  has no contents page after it and opens nothing.

### Work items

One item per boring / test pit / CPT / DCP log, with its "Page 2 of 3"
continuation sheets folded in; one per laboratory sheet or multi-page test;
one per calculation printout (a run of pages with one program banner or one
calc title, not one item per page); one for each narrative run; one per
appended report. Every page row names the item it belongs to; a divider
belongs to none.

### What is measured

Two corpora, and they say different things. **The second one is the number to
read.** Both are private and neither is in this repository; the measurement
scripts and their outputs live with the corpora.

#### In sample: the 14 hand-labelled reports

Scored against 4,147 hand-labelled pages of 14 real geotechnical reports
(2026-09-16), 38 to 729 pages each, English, French, Spanish and Portuguese,
text-layer and scanned, some read through Azure Document Intelligence. Split
into nine the rules were developed on and five set aside — an
optical-character-over-scan report, a pure scan read through Azure, a short
second volume, a 729-page report from another firm and a report in a non-US
format.

| role | dev P / R | set-aside P / R | hand-labelled |
|---|---|---|---|
| `boring_log` | 0.95 / 0.92 | 0.99 / 1.00 | 273 |
| `test_pit_log` | 0.95 / 0.89 | 1.00 / 0.90 | 251 |
| `lab_test` | 0.96 / 0.99 | 0.81 / 0.98 | 992 |
| `narrative` | 0.98 / 0.95 | 0.96 / 0.79 | 433 |
| `calculation` | 1.00 / 0.99 | 0.97 / 0.90 | 1,009 |
| page accuracy | **0.92** (2,847 pages) | **0.88** (1,300 pages) | 4,147 |

`appended_report` scores 1.00 / 1.00 on the set-aside reports over 472 pages
and is reported, not gated, along with the other twelve.

**Neither column is held out.** The split was imposed after the rules were
written, and the rules had already been tuned against all fourteen reports.
So the set-aside column is a weaker statement than it looks, and the roles
that fail there fail on pages the rules had already seen. The confusions that
cost them are `other -> lab_test` and `calculation -> lab_test` (a tab of
laboratory results claiming the summary tables and the odd calculation bound
behind it), `narrative -> lab_test` and `narrative -> other`. They are one
failure class: a page that says nothing about itself taking its appendix
tab's word for what it is.

#### Out of sample: 70 pages the rules had never seen

The honest measurement. Five pages drawn at random from each of fourteen
further reports — a wider mix of firms, formats and decades than the labelled
fourteen — hand-labelled from contact sheets BEFORE the rules were run on
them, with a second acceptable label recorded where two are equally
defensible. Those reports were never opened during development.

| 70 pages, never seen | strict | accepting the alternate label |
|---|---|---|
| the rules as first written | 0.71 | 0.77 |
| **the rules as shipped** | **0.79** | **0.86** |
| as shipped, excluding four scanned pages with no text source | 0.83 | 0.91 |

**0.79 is what these rules do on the next report; 0.92 is a description of
the fourteen they were written against.** The gap is the point of the
measurement, and it is why an inherited role carries `INHERITED_CONFIDENCE`
and says which rule fired: the pages the rules get wrong out of sample are
almost exactly the pages a review pass needs to look at. Every remaining
strict miss is a page that says nothing about itself — two photograph pages
and a figure under a tab that names several things (`other` with candidates,
as designed), four scanned pages with no text source, three inherited labels
the alternate accepts, a table of contents carrying the narrative's running
header, a laboratory slip-sheet naming the laboratory, and a core-photograph
log with no caption.

### What is NOT measured, and what it will get wrong

- **`dcp_log` recall is 0.54** (29 of 54; precision 0.97). What it misses are
  dynamic-cone sheets that are scans without a text layer, or French forms
  whose title names a boring and a dynamic cone in one line. It is honest to
  say the role is under-served rather than to tune for it.
- **`other` is a residual, not a class** (precision 0.51). A page that is a
  legend, a summary table, a groundwater reading sheet or an unlabelled form
  is "other"; so is a page the rules could not place. A reader must not take
  `other` as a statement.
- **`figure` and `plan` are weak** (0.26 and 0.29 precision). A figure page
  carries the least text of any page in a report, and the rules are text
  rules.
- **A page with no text at all cannot be placed by its title.** Nine boring
  logs in one corpus report are image-only in a report with no Azure result;
  they come out `figure`. Attaching an optical text source fixes them and
  nothing else in the module needs to change.
- **Hand labels are not perfect ground truth.** Several disagreements are
  genuine ambiguity — a foundation sketch of a test pit labelled as the pit's
  log on one page and as a photograph on the next.
- Roles are NOT read from PDF structure tags, bookmarks or Azure paragraph
  roles, none of which the corpus carries usefully.

### What the report says about itself (2026-09-16)

`document_outline(doc)` returns the report's own account of its contents, and
`page_ledger(doc, roles)` returns one line per page. Both exist because a
model-based review pass is going to sit on top of these rules, and it should
read what the report PRINTS about itself before it reads any of the report.

The outline carries:

- **contents entries** from the table of contents, each with its title, the
  page number as the report WROTE it ("12", "A-3", "iv") and the PDF page the
  title was matched to;
- **the lists of figures, tables and appendices** as entries with their
  number, title and printed page;
- **every divider page** with its own text and its appendix letter;
- **the caption** of each figure-kind page, when the page prints one ("Figure
  3 - Site Plan");
- **the narrative's section headings** in order, with the page.

Read from the pages, never inferred. A line becomes an entry when it carries
a title and, where the list gives one, a trailing page number behind a dotted
leader or a run of spaces; the current list heading decides an entry's kind
where the entry does not name itself. An entry is placed on a page only when
exactly one divider, caption or heading matches it, and when both sides print
a number the numbers must agree. **An entry that cannot be placed carries
`page = None`** — a reader can then go and look, which is a better answer
than a page index that is probably wrong. The synthetic fixture lists a
figure the document does not contain, precisely so that the unplaced case is
pinned by a test.

The ledger is one line of about 150 characters per page:

```
p007 mixed  boring_log  0.90 [page-title] "LOG OF BORING" hdr="..." pp=1/3
     seg=4 chars=550 text_ok=Y di=N item_4
```

page, kind, role, confidence, **the rule that fired**, heading, running
header, printed page number, segment, text characters, whether the text layer
is reliable, whether Azure Document Intelligence supplied the text, and the
work item. A 729-page report is about 100 kB of it, which is the cheapest
way to take a long document in before opening anything.

The rule tag matters as much as the role. `[page-title]` means the page named
itself; `[tab-declares]` means the page said nothing and took its role from
the appendix tab above it, and carries a deliberately lower confidence
(`INHERITED_CONFIDENCE`) for that reason. A tab is right about most of its
appendix and wrong about the summary table, the legend and the stray
calculation bound into it, so a reviewer wants to see which rows are the tab
talking and which are the page talking.

## Not built yet

- RapidOCR (`planlens.ocr`) as a text source (it currently emits IR TextItems).
- The drawing tools (digitize / query / snip / search a drawing set) as
  planlens tools — the app's drawing adapter still owns them.
- Headings from the PDF text layer; multi-column reading order; figure
  and caption detection; tables spanning pages.
- A `dcp_log` that survives a scan (see "Page roles and work items"), and
  roles for the in-situ tests the vocabulary has no word for (pressuremeter,
  vane, dilatometer), which are answered `other` today.
