# planlens.review — design notes

The document tools as a host serves them to a model: one toolkit per
conversation, markups written onto a copy and then looked at, and
`find_like` with every candidate read. They moved here from the
GeotechStaffEngineer app (its BACKLOG PL-29, part (a)), where they had only
wrapped planlens. The app keeps the tool names, the hints that name its own
vision tools, reading Word and Excel, and the binding to its agent.

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
| `image_view` (Pillow) | `images: ImageSupport` (`open_image`, `as_pdf`, `page_scale`, `shown_size`) |
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

## Modules

```
review/
  host.py          ReviewHost, configure(), using(), the small helpers
  conversation.py  one toolkit per conversation; handles and sources
                   interchangeable; pdf_pages/pdf_page and viewer page
                   numbers; Word/Excel/DXF read as text; lock-wait timing
  marked_copy.py   annotate's copy: temp file + swap, remove by id or words,
                   marks on an image in its pixels, off-the-page refusal
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
