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
now something the host passes in, in one `ReviewHost` installed with
`planlens.review.configure(...)`:

| The app imported | The host passes in |
|---|---|
| its file confinement (`find_readable_file`, `read_roots`, `PathRefused`, `files_here_text`) | `resolve_source(source)` -> bytes or a path, raising `ToolError` in its own words; `readable_file(name)` for "is this handle a file?" |
| `vision_tools.ask_vision(engine, image, prompt, kind)` | `look(image, question, kind) -> text` |
| `_fileio.host_output_dir()` / `into_working_folder` | `working_folder()`; the path logic itself moved (`host.into_working_folder`) |
| the markup author setting | `signer` (and `own_mark_words`: whose marks `remove` may take out) |
| `vision_tools.reads_file()` as the verdicts' key | `verdict_key()` |
| `GEOTECH_FIND_LIKE_BUDGET_S` | `find_like_budget_s` |
| `vision_tools.display_path`, `_fileio.hide_working_folder` | `display_path`, `hide_paths` |
| `vision_view.render_view(..., engine=)` for the check's crop | `render_crop(pdf, page, bbox)`; default planlens' own render at 300 dpi |
| `image_view` (Pillow) | `images: ImageSupport` (`open_image`, `as_pdf`, `page_scale`, `shown_size`) |
| `office_text` (python-docx, openpyxl) | `read_office(path, name) -> (markdown, fields)` |
| `cited_documents` | `after_marked_copy(result, markups)` |
| `dispatch_custom_event` (the lock-wait record) | `record(event, data)` |
| `error_text.tool_error_json` | `error_json(tool, exc)`; a plain default here |

Small pure helpers the moved code used (path scrubbing, the nested-key
check, image magic bytes, "is this box off the image") moved as copies into
`host.py`.

`ReviewToolkit` itself gains `look` next to `resolve_source`. None of the
toolkit's own seventeen tools needs it (its `find_like` is the image match
alone), so its surface is unchanged; `ReviewToolkit.LOOK_TOOLS` is the
mechanism for one that will, and the host layer's look tools
(`specs.LOOK_TOOLS`: `find_like`) are left out of `tool_names()` and
`tool_specs()` when the host gives no look. Without a look,
`annotate_document` still writes, and its result has no `check`.

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
  host.py          ReviewHost, configure(), the small helpers
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
side-read cache are process-wide, as they were in the app: one host per
process, conversations told apart by the working folder (`working_folder`)
and the verdict key. `configure()` with no argument restores the defaults
(file paths only, no look, no working folder).
