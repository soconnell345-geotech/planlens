# planlens.review — design notes

The document tools as a host serves them to a model: one toolkit per
conversation, markups written onto a copy and then looked at, and
`find_like` with every candidate read. They moved here from the
GeotechStaffEngineer app (its BACKLOG PL-29, part (a)), where they had only
wrapped planlens. The host keeps the tool names, the hints that name its own
vision tools, reading Word and Excel, and the binding to its agent. Since
the app split that host is the shared core `aec-agent-core`
(`aec_agent_core/document_tools.py`, with the tool functions in
`aec_agent_core/deep/vision_kit.py`), which serves both GeotechStaffEngineer
and Document Review (`aec-document-review`); the app modules the table
below names (`vision_tools`, `vision_view`, `image_view`, `office_text`,
`_fileio`, `cited_documents`, `error_text`) are in the core too.

## The rule: planlens imports nothing of the host and calls no model

Three of the moved files were not pure wrappers in the app: the markup check
and `find_like` asked the vision model, and the document tools reached into
the app's file confinement, attachments and working folder. Each of those is
now something the host passes in, in one `ReviewHost` installed for the
process with `planlens.review.configure(...)` or for one conversation (see
"State" below):

| The app imported | The host passes in |
|---|---|
| its file confinement (`find_readable_file`, `read_roots`, `PathRefused`, `files_here_text`) | `resolve_source(source)` -> bytes or a path, raising `ToolError` in its own words; `readable_file(name)` for "is this handle a file?" |
| `vision_tools.ask_vision(engine, image, prompt, kind)` | `look(image, question, kind) -> text` |
| `_fileio.host_output_dir()` / `into_working_folder` | `working_folder()`; the path logic itself moved (`host.into_working_folder`) |
| the markup author setting | `signer` (and `own_mark_words`: whose marks `remove` may take out) |
| `vision_tools.reads_file()` as the verdicts' key | `verdict_key()` |
| `GEOTECH_FIND_LIKE_BUDGET_S` | `find_like_budget_s`: a value or a callable read on every call; unset (`None`) is the app's 300 s, `0` no limit |
| `vision_tools.display_path`, `_fileio.hide_working_folder` | `display_path`, `hide_paths` |
| `vision_view.render_view(..., pad_frac=0.0, engine=)` for the check's crop | `render_crop(pdf, page, bbox)`, the box unpadded; default planlens' own render at 300 dpi |
| `vision_tools._resolve_attachment_or_path` in `find_like` | `find_like_source(key, attachments) -> bytes`, raising in the host's words; default the document tools' resolver |
| `image_view` (Pillow) | `images: ImageSupport` (`open_image`, `as_pdf`, `page_scale`, `shown_size`, and optionally `sent_scale`: see "Marks on an image") |
| `office_text` (python-docx, openpyxl) | `read_office(path, name, image_dir=, image_ref=) -> (markdown, fields)`; planlens passes the working folder's scratch folder and its name, as the app did |
| `cited_documents` | `after_marked_copy(result, markups)` |
| `dispatch_custom_event` (the lock-wait record) | `record(event, data)` |
| `error_text.tool_error_json` | `error_json(tool, exc)`, used by every tool, `annotate_document` and its check's failure line included; a plain default here |

Small pure helpers the moved code used (path scrubbing, the nested-key
check with its did-you-mean ranked as the app's `suggest_params` ranks keys
without units, image magic bytes, "is this box off the image") moved as
copies into `host.py`.

`ReviewToolkit` itself gains `look` next to `resolve_source`. None of the
toolkit's own seventeen tools needs it (its `find_like` is the image match
alone), so its surface is unchanged; `ReviewToolkit.LOOK_TOOLS` is the
mechanism for one that will, and the toolkit only stores the look it is
given. **The look the review tools use is `ReviewHost.look`**, read at call
time from the host serving the call: the markup check and `find_like` take
it from there, and the host layer's look tools (`specs.LOOK_TOOLS`:
`find_like`) are left out of `tool_names()` and `tool_specs()` when that
host gives no look. Without a look, `annotate_document` still writes, and
its result has no `check`.

## The specs are the app's, word for word

`specs.py` builds each tool's description and input schema from planlens'
own sources: the toolkit's description plus the host layer's note where it
changes the contract, and an input schema written the way the app's agent
framework writes the schema of its tool function (pydantic's JSON schema: a
title per argument, the function's default, `markups` with the toolkit's
own item schema). `tests/app_tool_specs.json` is the app's own capture
(`tests/capture_app_specs.py`, run against an app checkout), and
`tests/test_host_specs.py` checks the two are equal, tool for tool. A
difference is a change the app's agents would see; re-capture only when the
app changes a spec on purpose.

The sheet tools (`sheet_index`, `check_cross_references`,
`check_schedule_marks`; see `planlens/document/DESIGN.md`, "Sheet index and
cross-references" and "Schedules and the marks on the sheets") are not
among them. For a host that asked, `open_document` also brings the sheet
index and the schedule check (the toolkit's `sheets_at_open`, set by the
dispatcher from the CALLING host, as below); for every other host it is
byte for byte what it was. `HOST_TOOLS` and `host_tool_names()` are what they were, and
`SHEET_TOOLS` is a list of its own. A host asks for them with
`ReviewHost(toolkit_options={"sheet_tools": True})`: its toolkits then
serve them, `tool_names()` and `specs()` list them after the others, and
the dispatcher treats them as any document tool (a file name where the
handle goes, `pdf_pages`, the viewer's page beside every page in the
result). Whether they are served is decided by the host making each call,
not by the host whose call built the conversation's toolkit (hosts with no
working folder share one conversation): a host that asked is served them,
and a host that did not is answered "unknown tool" with its own tool names,
exactly as each is offered them. Any name the toolkit does not have is
answered the same way, with the calling host's list, never the toolkit's.
The conversation's toolkit is built with them on, so the order of the
calls does not matter. A host that asked is also offered `sheets` beside
`pages` (and `sheet` beside `page` on the plan tools and the drawing
tool; `find_like` takes neither, `specs.NO_SHEET_ARGS`) after its other
arguments (`specs.sheet_args`, app BACKLOG PL-37): printed sheet numbers,
turned into pages by the toolkit's sheet index; every other host is
offered, and refused, them as before (`specs.host_arg_names`).

The plan tools (`plan_symbols`, `plan_grid`; see
`planlens/document/DESIGN.md` on plan locations) are offered the same way
(app BACKLOG PL-27): `PLAN_TOOLS` is a list of its own, a host asks with
`ReviewHost(toolkit_options={"plan_tools": True})`, `tool_names()` and
`specs()` then list them after the sheet tools, and the dispatcher serves
them to the calling host that asked and refuses them by name to any other.
The difference is in the toolkit: it has always served the plan tools to
everyone, so there is no toolkit option to turn on; `plan_tools` is this
layer's option and is taken out before the toolkit is built. Their specs
follow the toolkit's: each argument but `handle`, `page` and `pdf_page` is
shown as the toolkit's spec shows it (`PLAN_SHOWN_FROM_TOOLKIT`, read off
that spec, so its words cannot drift), every argument is optional but
`plan_symbols`' handle, and an argument not given is not handed on, so the
toolkit's defaults apply.

The drawing tool (`drawing_geometry`; app BACKLOG DR-44, see
`planlens/ir/DESIGN.md` under "LLM surfaces") is offered as the sheet tools
are: `DRAWING_TOOLS` is a list of its own, a host asks with
`ReviewHost(toolkit_options={"drawing_tools": True})`, `tool_names()` and
`specs()` then list it after every other group, and the dispatcher serves
it to the calling host that asked and refuses it by name to any other. The
toolkit has an option of its own for it (`drawing_tools`, off by default,
so a toolkit nobody asked for, and the MCP server, list what they did);
the conversation's toolkit is built with it on, as with the sheet tools.
Its spec follows the toolkit's as the plan tools' do
(`DRAWING_SHOWN_FROM_TOOLKIT`). It is a fallback the agent reaches for,
known only through its own description: nothing calls it on its own, and
no system prompt names it.

The reading options (app BACKLOG PL-09) are a host's choice too, each by
itself: `ReviewHost(toolkit_options={"column_warning": True})`,
`{"continued_tables": True}` and `{"captions_on_every_page": True}`, the
names `Document` and the outline give them. The first two are options of
`Document`, so they change the page `read_document` shows (a `!` warning
line under the page's header; a `note:` line under a table read with
`include_tables`). The conversation's toolkit is given a callable for each
(`ReviewToolkit(column_warning=...)`), asked for the CALLING host, as for
`dxf_plots`: a document opened with one on is kept under a key and a handle
of its own (the content key with `:column_warning` / `:continued_tables`;
nothing added with both off, so a host that did not ask has the key and the
handle it had), and a handle given by a host whose options are not the ones
its document was opened with is answered from the same source opened with
that host's options. A host that did not ask is therefore never handed a
page another host read with an option on, whichever opened the file first.
`captions_on_every_page` is the outline's option and is read at each
`document_roles` call, so it needs no document of its own; `document_roles`
is the toolkit's tool and the host layer does not serve it, so for a
`ReviewHost` this option changes no result today. No spec changes.

A DXF's page tools (app BACKLOG DR-19) are a host's choice too: a host
that asks with `ReviewHost(toolkit_options={"dxf_plots": True})` has a
DXF's page tools (render, zoom, page map, marks, the drawing tool) look at
a one-page PDF plot of its model space (`planlens.dxf.plot`), where any
other host is refused them ("it has no pages here"). `_side_document`
decides on each call, for the calling host. It knows a DXF by the name the
model gave; the toolkit knows one also by the path that name resolves to,
so a host that did not ask could still reach the shared toolkit with a
DXF (a name its resolver maps to a `.dxf` path). The conversation's
toolkit is therefore not built with `dxf_plots` on: it is given a
callable (`ReviewToolkit(dxf_plots=...)` takes one, asked at each open)
that answers for the CALLING host, and a plot is kept under a key of its
own (the file's content key with `:dxf-plot`), so an open that does not
plot never finds one another host opened. A host that did not ask is
answered as the toolkit answers any file that is not a PDF or an image
("could not open '...' as a PDF or image").
Reading and searching a DXF stay with its own text; its `open_document`
result gains one `plot` line saying what the plot is. No spec changes: the
tools' descriptions are the same for every host.
`find_like` is not one of those page tools: it reads the file's own bytes
and never the toolkit's document, so on a DXF it fails to open the file
for every host, as in 0.16.0. Two rules follow the plot back to its
drawing (release review of the whole change): `annotate_document` on the
toolkit refuses an `output_path` that is the DXF the plot was made from
(the plot is held in memory, so the document has no file of its own to
compare; the marked PDF would have replaced the drawing), and
`ReviewToolkit.forget(path)`, so `forget_path`, closes the plot made from
that file under whatever name it was opened.

The plot itself (`planlens.dxf.plot.dxf_to_pdf`) is made inside the
toolkit's `open`, under the toolkit's lock, so it is bounded: a file over
`MAX_FILE_MB` (16), more than `MAX_ENTITIES` (50,000) entities drawn with
blocks expanded (an INSERT counts, and so does each entity it brings), or
drawing and writing that pass `MAX_SECONDS` (30; checked between entities
and between the shapes the PDF writer writes, so one very heavy entity
overruns by its own length) raise `PlotTooLarge`. The page is at most
`MAX_SIDE_MM` (1189, A0's long side) a side; ezdxf plots one drawing unit
to the millimetre and only scales down, so a drawing whose longer side is
under `MIN_SIDE_MM` (297) less the two margins is scaled up to that. An
install without Pillow (ezdxf's drawing add-on imports it; planlens does
not require it) raises `PlotUnavailable`, which names Pillow. Both are
ValueErrors, and the toolkit answers each as "could not plot '<name>':
..." with the reason; any other failure is answered as a DXF ezdxf cannot
read. The tests that make a plot are skipped where Pillow is not
installed.

`annotate_document`'s `remove` takes a mark's stable `uid` (the
annotation's `/NM`, as the call that wrote the mark returns it; it does not
move when another mark is removed, as the positional id does) wherever it
takes an id, as the entry or under `id`. The keys of a `remove` object are
still `id` and `text`: the spec shows `remove` as strings, so no spec text
changes. A refusal of a wrong key reads as it did, with one addition: a
`uid` or `markupuid` key is told to pass the uid under `id`. `forget_path` closes
a file's documents with the toolkit's own `ReviewToolkit.forget`.

## Modules

```
review/
  host.py          ReviewHost, configure(), using(), the small helpers
  conversation.py  one toolkit per conversation; handles and sources
                   interchangeable; pdf_pages/pdf_page and viewer page
                   numbers (and a note where they did not fit, PL-38);
                   Word/Excel/DXF read as text; lock-wait timing
  marked_copy.py   annotate's copy: temp file + swap, remove by id, uid or words,
                   marks on an image in its pixels (its views judged by
                   the scale they were sent at; a copy of another image
                   passed over), off-the-page refusal;
                   one lock per copy, so two calls on it keep both (PL-38)
  markup_check.py  every mark looked at on the marked copy
  find_like.py     every copy of one mark, each candidate read by the look,
                   a flood guard and a time budget
  page_numbers.py  0-based page / 1-based pdf_page, the off-by-one hint
  frames.py        page and image frames, tiles, zoom windows, location
                   error, turned lettering and cut lines
  specs.py         the tools' specs
  tools.py         call(): each tool run the way the app's tool function ran
```

## State

The per-conversation toolkits, the markup check's kept verdicts and the
side-read cache are process-wide, as they were in the app, with
conversations told apart by the working folder (`working_folder`) and the
verdict key. `configure()` installs the process's host; with no argument it
restores the defaults (file paths only, no look, no working folder).

The host itself can be one conversation's. The app builds one agent per
conversation, each with its own vision engine and signer (two people can be
signed in to one process), so a host that is only process-wide would sign
everyone's marks with the name set last. `call(..., host=)` and
`with using(host):` serve the calls inside with that host, through a
context variable that `current()` reads first: other threads keep theirs,
and the markup check's and `find_like`'s worker threads run in a copy of the
calling context, so it reaches them. `using(look=None)` is "no look for
this conversation" (no `find_like`, marks written and not checked).

## Marks on an image

A mark on an IMAGE file is given in the image's own pixels, as its looks
give them, and goes on a copy whose page is the image at one point a pixel
(`ImageSupport.as_pdf`), or converted exactly onto an existing copy at
another scale. Two rules judge such a mark by the image, not by the page
rules the writer applies to points (app BACKLOG VIS-26):

- **A view is judged by the scale it was sent at.** The writer refuses a
  small mark read off a view wider than 300 units, because a box read off
  a page view that wide can be off by about a tenth of it. A look at an
  image is cut from the original: a crop that fits the host's vision budget
  reaches the model at full resolution or larger, and a box read off it is
  good to a few of the image's pixels however wide it is. When the host's
  `ImageSupport.sent_scale(w, h)` says a view went at 1 or more, the mark
  is handed to the writer as the box its view + image_box name (converted
  as the writer converts them, so the written row is the same) and is
  placed; a view sent shrunk is refused as before. Either way a refusal
  is said in pixels, with the scale the view went at when the host gives
  it (`judge_image_views`). A host with no `sent_scale` keeps the page's
  rule. The marks keep their order, so the writer's skipped rows still
  name each mark by its index.
- **A copy is added to only when it holds this image.** With append on, a
  second image under a name that already has a marked copy went onto the
  first image's copy, converted by that copy's size. A copy is added to
  only when its page holds an image of this image's pixels (as shown, or as
  stored before an EXIF turn) drawn at one scale (`_copy_fits_image`);
  otherwise the marks go on a new copy under the next free name, as for a
  file that is not a copy of this host's, and `remove` with marks on such a
  copy writes nothing. Only the size can be told: a different image of the
  same size under the same name is still added to.
