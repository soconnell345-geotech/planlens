# CHECK: PL-39, planlens' document tools, four results that mislead

Branch `claude/pl-39-misleading-results`, from planlens main 0f87fa6. Task P1 of
the app's `module_work/cloud_briefs/DOCREVIEW_2026-11-13.md`. Evidence:
TRACE_REVIEW (brief 7) section 7 and F-6, F-7, F-9, F-10. All four pieces are
repairs: no setting, every host.

Commits (oldest first):
- `2318a49` (1), (3): pages_to_view and search_document say only what is true
- `7246bb0` (2), (4): contact-sheet labels name both counts; rebar is no length
- `23417ce` (3): search_document's description, and the app capture's entry for it
- `722f7da` CHANGELOG (one Unreleased entry per repair) and DESIGN notes
- `ee0b900` (3): a drawing with a few CAD labels stays unsearchable as text
  (a fix to my own `2318a49`, caught by `test_vision_loop.py`)
- this CHECK, in a last commit of its own

## (1) `open_document`'s `pages_to_view`

**Confirmed on main.** `tools/toolkit.py`, `_tool_open_document`:
`view = [s.page for s in summaries if s.kind in VIEW_KINDS]`, with
`VIEW_KINDS = ("scanned", "figure", "drawing_sheet", "form")` and the note
"these pages are scans, figures or drawing sheets: their text is labels at
best". `classify.classify_page` makes a large-format page a `drawing_sheet`
only with 200+ paths, images or CAD text, so a titled sheet of notes or light
linework is `text` or `mixed`. `sheets._is_sheet` (the sheet index) counts every
page 11 x 17 in or larger whatever its kind. `document_page_map`'s `look` used
the same test.

**Changed.** New `toolkit.to_view(summary)`: a view kind, or a non-blank page
whose area is at least `sheets.SHEET_MIN_AREA` (the sheet index's own
yardstick). `pages_to_view` and the page map's `look` both use it. The note
now reads: "these pages are drawings, scans, figures or forms, and every page
11 x 17 in or larger is among them whatever its text: what they say is in the
picture, so read them by viewing them. pages_by_kind classes a page by its
content, so a titled sheet can be classed text or mixed; a smaller page left
off this list can still hold a drawing", then the host's look instruction. The
page map's look line names the same set.

**Example (made up).** Six pages: three 24 x 36 in sheets A-101, A-301, A-501
of notes with title blocks (classed text), a letter prose page, a letter scan,
a 24 x 36 in sheet S-201 with heavy linework.
- Before: `"pages_to_view": "4,5"`, note "these pages are scans, figures or drawing sheets ...".
- After: `"pages_to_view": "0-2,4-5"`; every page `sheet_index` finds (0, 1, 2, 5) is on it; the prose page (3) is not.

## (2) Contact-sheet labels

**Confirmed on main.** `document/document.py`, `Document.render_thumbnails`:
`label = f"{index} {summary.kind}"` (0-based), legend "each thumbnail is
labelled '<page> <kind>'; ...".

**Changed.** New `document.thumbnail_label(index, kind, page_label=None)`:
"pg 3 | PDF 4 drawing_sheet", with the PDF's page label in brackets when it
differs from the 1-based number. The legend: "each thumbnail is labelled 'pg
<page> | PDF <pdf_page> <kind>': pg is the page counted from 0 (the tools'
page), PDF is the page counted from 1 as a PDF viewer shows it (the tools'
pdf_page); a name in brackets after it is the PDF's own page label; a red frame
means the page carries review markups". The bracketed page label is dropped if
it would push the label past the cell; the lettering shrinks (no less than
5 pt) rather than cut a number. Image sizes are unchanged.

**Example.** Before: "3 drawing_sheet". After: "pg 3 | PDF 4 drawing_sheet".

## (3) `search_document`

**Confirmed on main.** `_tool_search_document`: (a)
`unread = [s.page for s in page_map(pages) if s.kind in VIEW_KINDS]`, with no
regard to the text layer or the hits; (b) a literal pattern is
`re.escape`d in `Document.search`, so "A|B" is matched as that string; the
zero-hit hint blamed scans/figures/drawing sheets and offered only `fuzzy`.

**Changed.** (a) New `toolkit.no_text_layer(summary)`: not blank, and sent to
OCR (`needs_ocr`, which includes an unreliable text layer) or under 50 text
characters (`NO_TEXT_LAYER_CHARS`, the classifier's own line for image-only).
The list is those pages minus every page with a hit. The zero-hit hint says
"the pages listed have no usable text layer (a scan, or lettering drawn as
strokes), so the term may be on one of them". (b) When the pattern holds "|"
and neither `regex` nor `fuzzy` is set, the hint starts: 'the pattern holds
"|", which this search matched as that character; to find either term, search
again with regex=true, which reads "|" as "or"', ahead of the fuzzy tip.

The tool description (`tools/specs.py`) said the search "cannot see into scans,
figures or drawing sheets: pages_not_searchable_as_text lists those"; it now
says "cannot read a page without a usable text layer (a scan, or lettering
drawn as strokes): pages_not_searchable_as_text lists those it searched and
found nothing on". `planlens/review/tests/app_tool_specs.json` is changed for
exactly that tool (both copies of the sentence), **by hand**: the capture
script imports the geotech app (`funhouse_agent.deep.vision_kit`), so it needs
venv (b), and installing the app into a venv was refused by this session's
sandbox. Venv (a) (core + Document Review) did install, see below.

**Example.** Same six pages, search "7/A-501" (on the three notes sheets).
- Before: hits on 0, 1, 2 and `"pages_not_searchable_as_text": "4,5"` (5 has a full text layer).
- After: hits on 0, 1, 2 and `"pages_not_searchable_as_text": "4"`.
Search "A-101|A-301": before, 0 hits and a hint offering `fuzzy`; after, the
hint first says `regex=true` reads "|" as "or".

## (4) `find_quantities` and a rebar callout

**Confirmed on main.** `document/quantities.py`, `_RE_VALUE` = number +
`_RANGE_TAIL` + unit, `re.I`: "10-20M EACH WAY" gave value 10, value_to 20,
unit m.

**Changed.** New `_RE_REBAR` (case-sensitive): a count of 1 to 3 digits, a
hyphen (spaces allowed), a CSA metric bar size (10, 15, 20, 25, 30, 35, 45, 55)
with a capital M against it, not followed by a letter or digit. In
`scan_text`, any candidate overlapping such a span is dropped before the
non-overlap pass, so neither the range nor "20M" alone is read. "10-20 m",
"10-20m", "10 to 20 M" and "6-12M" (12 is no bar size) are still ranges.

**Example.** "BOT 10-20M EACH WAY, TOP 8-15M EACH WAY": before, two lengths
(10 to 20 m, 8 to 15 m); after, no quantity.

## Tests

New: `planlens/tools/tests/test_pl39_misleading_results.py` (8 tests) and
`planlens/document/tests/test_pl39_misleading_results.py` (11 tests, with
parametrisation). Run against main's source (tests kept, source stashed):

| test | main | branch |
|---|---|---|
| tools: test_pages_to_view_holds_every_drawing_size_sheet | fail | pass |
| tools: test_pages_to_view_holds_every_sheet_the_index_finds | fail | pass |
| tools: test_the_page_map_marks_the_same_pages_look | fail | pass |
| tools: test_no_page_with_a_text_layer_or_a_hit_is_called_unsearchable | fail | pass |
| tools: test_a_miss_names_only_the_pages_without_a_text_layer | fail | pass |
| tools: test_a_literal_bar_is_said_to_mean_or_only_with_regex | fail | pass |
| document: test_a_thumbnail_label_names_both_counts | fail | pass |
| document: test_the_legend_says_which_count_each_number_is | fail | pass |
| document: test_a_rebar_callout_is_no_quantity (4 cases) | fail | pass |
| document: test_a_rebar_callout_leaves_the_values_beside_it_alone | fail | pass |
| tools: test_the_made_up_set_is_what_the_test_says | pass | pass (guard: the fixture's kinds) |
| tools: test_no_list_when_every_page_searched_has_a_text_layer | pass | pass (guard) |
| document: test_a_length_range_in_metres_is_still_one (4 cases) | pass | pass (guard: no over-reach) |

13 fail on main, 19 pass on the branch. The 6 that pass on main are guards
against over-reach, by design, and are named as such.

Runs (planlens venv, `/tmp/pl`, Python 3.12):
- The brief's list plus the new files and `document/tests/test_structure.py`:
  `python -m pytest -q -p no:cacheprovider planlens/tools/tests/test_toolkit.py planlens/tools/tests/test_sheet_tools.py planlens/tools/tests/test_structure_tools.py planlens/document/tests/test_document.py planlens/document/tests/test_quantities.py planlens/document/tests/test_sheets.py planlens/review/tests/test_host_specs.py planlens/review/tests/test_tools.py planlens/review/tests/test_end_to_end.py planlens/tools/tests/test_pl39_misleading_results.py planlens/document/tests/test_pl39_misleading_results.py planlens/document/tests/test_structure.py`: exit 0, 444 passed, 1 skipped.
- The whole suite, `python -m pytest -q -p no:cacheprovider planlens`, at the
  fifth commit: exit 0, 2289 passed, 6 skipped. (A first run, before the fifth
  commit, stopped on `test_vision_loop.py::test_search_miss_points_at_unsearchable_pages`:
  my first rule exempted a page with any CAD text, which dropped a 23-character
  drawing sheet from the list. The rule was wrong, not the test; fixed in code,
  the test is unedited.)

No existing test was edited.

**The app as a consumer** (venv (a) built from `/tmp/gse-src`, this checkout
installed with `--no-deps --force-reinstall -e`), from
`packages/aec-document-review`:
`MPLBACKEND=Agg python -m pytest -q -p no:cacheprovider tests/test_review_binding_offline.py tests/test_review_sheet_tools_offline.py tests/test_review_eval_coordination_offline.py tests/test_review_fingerprints.py`
- this branch: exit 1, 69 passed, 1 failed, 2 skipped.
- planlens main 0f87fa6 in the same venv: exit 1, 71 passed, 1 failed.
- The failure is the same on both, `test_review_binding_offline.py::test_the_process_host_is_the_apps`
  (`'Document Review' in host.own_mark_words`, got `('AEC agent', '(AI draft)')`):
  not from this branch.
- The 2 skips are `test_review_fingerprints.py`'s planlens part: "No planlens
  tools are recorded for the installed planlens (... surface 3e415249e4551735)".
  Expected: the search_document description moved, so the surface hash did.

## Fingerprints

planlens has none of its own. The app's Document Review fingerprints (their
planlens part) move with `search_document`'s description; not re-recorded here
(the app is read-only from this session). The lead records them under the new
planlens version when the pin is raised.

## Calls made

1. **(1) list, not only the note.** `pages_to_view` lists every non-blank page
   11 x 17 in or larger as well as the view kinds, and the note also says that
   kinds are by content and a smaller page can still hold a drawing. Why: the
   list is what models act on, and the evidence is a model taking the list as
   the set; the sheet index's area test costs nothing (the page map is read
   anyway), where reading title blocks would be the opt-in sheet index's full
   read. Cost: more looks, on the coordination sets every sheet, which is what
   the page's rule (look at every page in scope) asks. A tabloid page of prose
   is listed too.
2. **Blank pages** of drawing size are left off both lists (nothing to view or
   search).
3. **"No usable text layer"** = sent to OCR, or under 50 text characters,
   whatever hidden CAD text the page holds (the classifier's own line).
4. **The "|" hint** is given whether or not the search found hits, and not
   with `fuzzy` (which ignores `regex`). It goes first in `hint`.
5. **(2) both counts** printed, "pg <page> | PDF <pdf_page>", not only the PDF
   page: the tools take both, and naming both leaves nothing to infer.
6. **(4) not a quantity at all**, rather than a new rebar kind: a new kind
   would change `find_quantities`' `kinds` vocabulary, which the brief keeps
   fixed. The M must be a capital against one of the CSA bar sizes.

## Release note

planlens: four tool results no longer mislead. `open_document`'s
`pages_to_view` (and `document_page_map`'s `look`) now include every page
11 x 17 in or larger, so titled drawing sheets classed text or mixed are on the
list, and its note says the list is by content. Contact-sheet thumbnails are
labelled "pg <page> | PDF <pdf_page>", both counts named in the legend.
`search_document`'s `pages_not_searchable_as_text` names only pages without a
usable text layer and never one with a hit, and a literal pattern holding "|"
is told that `regex=true` reads it as "or". `find_quantities` no longer reads a
rebar callout such as "10-20M EACH WAY" as a length range. A Document Review
tester will see agents look at more sheets on a drawing set (more vision calls
and time on such sets).

## For the lead

- **Release and pin.** A planlens release with these commits, then the app's
  planlens pin raise; record Document Review's fingerprints (planlens part)
  under the new version (`python -m tests.page_builds record`).
- **Re-capture `app_tool_specs.json`** from venv (b) with this branch installed
  (`python planlens/review/tests/capture_app_specs.py --app <app checkout>`)
  and confirm it equals the hand edit (only `search_document` should differ
  from main). Needs a session that may install the geotech app.
- **`test_the_process_host_is_the_apps`** fails on planlens main as well in
  venv (a) from app master: worth a look on the app side (own mark words).
- **Live check.** On a coordination set (e.g. the 40-sheet one), run one review
  and confirm `pages_to_view` holds every sheet and the run's vision cost per
  turn; the look count goes up by design.

## Noted, not fixed

- `find_quantities`' own `pages_not_searchable_as_text` and look line are
  still by kind (`VIEW_KINDS`), so a drawing sheet with a full text layer is
  still called unsearchable there; outside the four repairs.
- A bare metric bar size ("15M @ 300") is still read as 15 m; it cannot be told
  from a length in capitals without more context.
- `open_document`'s `drawing_sheets` rows (sheet labels) list only pages
  classed `drawing_sheet`, so a sheet classed text or mixed has no row there.
- `_fit_hits` replaces `hint` when it cuts a long hit list, so the "|" hint is
  lost on a result over the size limit.
