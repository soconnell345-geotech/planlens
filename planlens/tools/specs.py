"""JSON-Schema definitions of the planlens tools — the text the model reads.

Descriptions are instructions: they say what each tool is for, when to reach for
it, and the conventions (0-based pages, displayed-frame points, cursors) the
results use. Keep them short; they are sent with every model request.
"""

PAGES_SCHEMA = {
    "description": ("0-based pages: an integer, a list, or a range string "
                    "like \"0-4,9\". Omit for all pages."),
    "anyOf": [{"type": "integer"}, {"type": "string"},
              {"type": "array", "items": {"type": "integer"}}],
}

HANDLE_SCHEMA = {"type": "string",
                 "description": "Handle returned by open_document."}

TOOL_SPECS = [
    {
        "name": "open_document",
        "description": (
            "Open a PDF (or an image file) for review — report, drawing set, "
            "submittal, calcs, scan. Returns a handle plus a map of the whole "
            "document: which pages are text, drawing sheets, figures or "
            "scans; sheet labels; bookmarks; how many review markups there "
            "are and by whom; and pages_to_view — the pages whose content is "
            "a picture, to be read by looking. Call this first for any "
            "document question — you do not need to know in advance whether "
            "the answer is in prose, a table, a drawing, an image or a "
            "reviewer's comment."),
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string",
                           "description": "Upload name or file path of the PDF."},
            },
            "required": ["source"],
        },
    },
    {
        "name": "document_page_map",
        "description": (
            "One row per page: kind (text / drawing_sheet / form / figure / "
            "scanned / blank / mixed), segment id, label, largest heading, "
            "word count, the page number PRINTED on the page (printed_page / "
            "printed_of — cite these to the reader), sheet reference, scale "
            "notes on drawing sheets, divider title, markup and "
            "hidden-CAD-text counts, duplicate_of (with duplicate_rule: "
            "'text' if the page repeats an earlier page's words, 'image' if "
            "it repeats its picture). with_evidence adds the "
            "measurements, header and footer text. Use it to find the pages "
            "that matter before reading. Continues via next_pages."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "kind": {"type": "string",
                         "enum": ["text", "drawing_sheet", "form", "figure",
                                  "scanned", "blank", "mixed"],
                         "description": "Only pages of this kind."},
                "with_evidence": {"type": "boolean",
                                  "description": "Include the measurements "
                                                 "behind each kind."},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "read_document",
        "description": (
            "Read pages: their text, tables (as markdown) and review markups. "
            "with_locations=true prefixes each line with its id and box "
            "[id @ x0,y0,x1,y1] in PDF points (displayed page, top-left "
            "origin) plus its direction when not horizontal — use it to cite "
            "or zoom into a spot. Text drawn by reviewers is NOT mixed into "
            "page text; it appears under 'review markup(s)'. Lines tagged "
            "'cad' are AutoCAD hidden text behind stroked lettering. A line "
            "starting '! look:' means the text is not the page — a scan, a "
            "figure, a drawing sheet, a form — and says how to view it; do "
            "that before concluding anything from such a page. When the "
            "result has 'next', call again with those pages and start_line."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "start_line": {"type": "integer", "minimum": 0,
                               "description": "Resume inside the first "
                                              "page (from 'next')."},
                "with_locations": {"type": "boolean"},
                "include_tables": {"type": "boolean",
                                   "description": "Default true."},
                "include_markups": {"type": "boolean",
                                    "description": "Default true."},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "search_document",
        "description": (
            "Find text anywhere in the document — page text, hidden CAD text "
            "and reviewers' markup comments — including phrases broken "
            "across lines. Each hit has page, label, a snippet, the matched "
            "line ids or markup id, and a box. Use it to locate a topic, a "
            "value, a boring or sheet id before reading those pages. It "
            "cannot see into scans, figures or drawing sheets: "
            "pages_not_searchable_as_text lists those, and a miss there is "
            "not absence — view them. fuzzy=true matches approximately, for "
            "text read optically or plotted as strokes, where a letter may be "
            "wrong: use it when an exact search of a page you believe holds "
            "the term comes back empty. Fuzzy hits carry a score (0-100), "
            "best first."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pattern": {"type": "string",
                            "description": "Literal text, or a regular "
                                           "expression with regex=true."},
                "pages": PAGES_SCHEMA,
                "regex": {"type": "boolean"},
                "case_sensitive": {"type": "boolean"},
                "include_markups": {"type": "boolean",
                                    "description": "Default true."},
                "max_hits": {"type": "integer", "minimum": 1,
                             "maximum": 500},
                "fuzzy": {"type": "boolean",
                          "description": "Match approximately, tolerating "
                                         "wrong, missing or swapped letters. "
                                         "Ignores regex."},
                "min_score": {"type": "integer", "minimum": 50, "maximum": 100,
                              "description": "Lowest fuzzy score to return "
                                             "(default 80). A short query "
                                             "scores lower for the same one "
                                             "letter wrong, so drop to about "
                                             "75 for a word under 8 letters."},
            },
            "required": ["handle", "pattern"],
        },
    },
    {
        "name": "find_quantities",
        "description": (
            "Every number WITH A UNIT the document states — \"approximately "
            "40-foot centers\", \"6300 mm\", \"21 degrees\", \"2,500 psf\", "
            "\"EL. 1684\", \"2H:1V\", \"STA 10+50\", \"20 to 35 ft\" — each "
            "with its raw wording, any qualifier (approximately / minimum / "
            "typ. / plus-minus), its page, line ids and box. Use it to check "
            "what the report or the calculations CLAIM against what the "
            "drawing shows, to collect every design parameter, or to find "
            "where a value is stated. Filter with kinds and units. A bare "
            "number with no unit is never returned, and nothing is converted: "
            "the units are as the page wrote them."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "kinds": {
                    "type": "array",
                    "items": {"type": "string",
                              "enum": ["length", "area", "volume",
                                       "pressure_or_stress", "force",
                                       "unit_weight", "angle", "percent",
                                       "elevation", "station", "slope",
                                       "count", "other"]},
                    "description": "Only these kinds of quantity."},
                "units": {
                    "type": "array", "items": {"type": "string"},
                    "description": "Only these units, spelled as the result "
                                   "does: ft, in, mm, m, ft^2, yd^3, psf, "
                                   "pcf, kPa, kN, deg, %, sta, H:V."},
                "include_markups": {"type": "boolean",
                                    "description": "Default true."},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "document_markups",
        "description": (
            "The review record: every comment, callout, cloud, arrow and "
            "stamp placed on the PDF, with author, date, what it says or "
            "shows, the exact point a callout/arrow points at, which markup a "
            "reply is aimed at, and reply links. Filter by pages or author "
            "(substring). Continues via next_offset."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "author": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "annotate_document",
        "description": (
            "Write your review comments onto a COPY of the PDF as ordinary "
            "annotations — the reviewer opens the result in Bluebeam or "
            "Acrobat and sees notes, highlights, boxes, callouts and replies "
            "in the comments list like anyone else's. Use it to DELIVER a "
            "review, once you have read the pages and know what to say. Each "
            "markup names its kind, its 0-based page, its comment and ONE "
            "anchor. Anchor by quote whenever the comment is about text on "
            "the page — pass the words as printed and they are found for you, "
            "even if a letter was read wrong; that is what puts the mark on "
            "the right words. For a spot on a drawing use box or callout with "
            "a bbox from read_document(with_locations=true), a markup, or the "
            "region you rendered — same frame, no conversion. Reply to an "
            "existing comment with reply_to and its markup id from "
            "document_markups. Every comment is a DRAFT attributed to the AI "
            "author unless you are told to sign it otherwise, so say what is "
            "unresolved rather than asserting. The source document is never "
            "changed: output_path is a NEW file, and calling again with the "
            "same output_path adds to it. The result lists what went on and "
            "WHAT DID NOT — a markup whose quote is not on the page is "
            "skipped with a reason, not moved somewhere else, so read the "
            "skipped list and re-anchor those comments."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "output_path": {
                    "type": "string",
                    "description": "Where to write the marked-up copy, e.g. "
                                   "'<document>_marked.pdf'. Never the source."},
                "markups": {
                    "type": "array",
                    "description": "The comments to write, in order.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["note", "highlight", "box", "callout",
                                         "reply"],
                                "description": "note = a sticky note at a "
                                               "spot; highlight = over the "
                                               "words quoted; box = a "
                                               "rectangle round a region; "
                                               "callout = a text box with a "
                                               "leader pointing at a spot; "
                                               "reply = an answer threaded "
                                               "onto an existing markup."},
                            "page": {"type": "integer", "minimum": 0,
                                     "description": "0-based, as everywhere "
                                                    "else."},
                            "comment": {"type": "string",
                                        "description": "What the markup says. "
                                                       "One point per markup."},
                            "quote": {"type": "string",
                                      "description": "Words on that page the "
                                                     "comment is about. "
                                                     "Matched exactly first, "
                                                     "then approximately."},
                            "bbox": {"type": "array",
                                     "items": {"type": "number"},
                                     "minItems": 4, "maxItems": 4,
                                     "description": "[x0,y0,x1,y1] in PDF "
                                                    "points, displayed page, "
                                                    "top-left origin. For a "
                                                    "callout this is where "
                                                    "its text box goes."},
                            "point": {"type": "array",
                                      "items": {"type": "number"},
                                      "minItems": 2, "maxItems": 2,
                                      "description": "[x,y] in the same "
                                                     "frame."},
                            "points_at": {"type": "array",
                                          "items": {"type": "number"},
                                          "minItems": 2, "maxItems": 2,
                                          "description": "Callout only: the "
                                                         "spot its leader "
                                                         "aims at."},
                            "reply_to": {"type": "string",
                                         "description": "Reply only: the "
                                                        "markup id from "
                                                        "document_markups "
                                                        "(e.g. 'p3.m1')."},
                            "author": {"type": "string",
                                       "description": "Overrides the author "
                                                      "for this one markup."},
                            "min_score": {"type": "integer", "minimum": 50,
                                          "maximum": 100,
                                          "description": "Lowest fuzzy score "
                                                         "a quote may match "
                                                         "at (default 80; "
                                                         "about 75 for a "
                                                         "short quote)."},
                        },
                        "required": ["kind", "page", "comment"],
                    },
                },
                "author": {"type": "string",
                           "description": "Who every markup is signed by. "
                                          "Defaults to the host's AI author."},
                "append": {"type": "boolean",
                           "description": "Default true: an output_path that "
                                          "already exists is added to. false "
                                          "starts the marked-up copy again "
                                          "from the source."},
            },
            "required": ["handle", "output_path", "markups"],
        },
    },
    {
        "name": "document_structure",
        "description": (
            "The constituent documents inside a stapled PDF — transmittal, "
            "drawing set, calculation package, the report nested inside it, "
            "appendices — as segments: a run of pages, its title (from a "
            "divider page or its running header/footer), the page numbers "
            "printed on those pages (e.g. calc package pages 1-245 vs the "
            "PDF's own 0-based pages), sheet references, and the mix of page "
            "kinds. Use it to orient in a long document and to translate a "
            "citation like 'see page 24 of the calcs' into a PDF page."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "log_grid",
        "description": (
            "Read a boring log or a test-pit log as the grid it is. Give it "
            "the pages of ONE log (its continuation sheets included, as "
            "document_roles groups them) and it returns: the COLUMNS with "
            "their x bands and what each header calls them (depth, "
            "elevation, sample_id, sample_type, blows, n_value, recovery, "
            "rqd, description, uscs, graphic, water_content, "
            "dry_unit_weight, liquid_limit, plastic_limit, "
            "plasticity_index, fines, qu, pocket_pen, torvane, remarks, "
            "tests, or other keeping the printed header); the depth RULER "
            "fitted to the printed scale, with its unit and the residual of "
            "the fit; every remaining line of text as a ROW carrying its "
            "column, its depth, the depth range its box covers, its numbers "
            "and its box; the LAYERS the description column is cut into, "
            "each with its top depth, its bottom and its text; and the "
            "FIELDS printed outside the body (boring number, ground surface "
            "elevation, dates, hammer type, driller, total depth, "
            "groundwater). Values are returned AS PRINTED and never parsed "
            "into a meaning: a blow record stays \"5-9-12\" and an N value "
            "stays \"N=21\". Depths are in the unit the log prints, which "
            "depth_unit names. A page whose ruler cannot be found returns "
            "its cells with NO depths and says so in warnings - read the "
            "warnings before using anything. This is data with boxes, not a "
            "reading of the log; look at the page for what the geometry "
            "cannot say (sample symbols, water-level symbols, refusal "
            "notation)."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": {
                    "type": "string",
                    "description": "The pages of one log, 0-based: \"35\" or "
                                   "\"35-37\" or \"35,36,37\". Required in "
                                   "practice - a whole document is not one "
                                   "log.",
                },
                "rows": {"type": "boolean",
                         "description": "Include the placed cells (default "
                                        "true). false returns only the "
                                        "columns, the ruler, the layers and "
                                        "the fields."},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "document_roles",
        "description": (
            "What each page of a report IS, and the work items its pages "
            "make. Roles: narrative, figure, plan, profile, boring_log, "
            "test_pit_log, cpt_log, dcp_log, lab_test, field_test, "
            "calculation, appended_report, photos, divider, cover, letter, "
            "toc, other — each with the evidence that decided it. Items "
            "group the pages a reader takes in together: one per boring or "
            "test pit (its \"Page 2 of 3\" continuation sheets folded in), "
            "one per laboratory sheet or multi-page test, one per "
            "calculation printout, one for the narrative, one per report "
            "bound inside this one. Use it to find every log or every lab "
            "sheet in a long report, and to read one of them at a time. "
            "items_only=true returns the items without the per-page rows. "
            "outline=true adds what the report says about ITSELF: its table "
            "of contents, its lists of figures, tables and appendices (each "
            "entry with the page number as printed, and the PDF page it was "
            "matched to, or nothing when it could not be matched), every "
            "divider page with its text, each figure page's caption, and the "
            "narrative's section headings in order. ledger=true returns one "
            "compact line per page instead of the rows - page, kind, role, "
            "confidence, the rule that fired, heading, running header, "
            "printed page, segment, text characters, whether the text is "
            "reliable and whether it came from Azure - which is the cheapest "
            "way to take a long report in before opening anything."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "items_only": {"type": "boolean",
                               "description": "Return only the work items."},
                "outline": {"type": "boolean",
                            "description": "Add the report's contents, "
                                           "lists, dividers, captions and "
                                           "section headings."},
                "ledger": {"type": "boolean",
                           "description": "One line per page instead of the "
                                          "per-page rows."},
                "offset": {"type": "integer", "minimum": 0},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "render_page_thumbnails",
        "description": (
            "Contact sheets of the document: every page as a small thumbnail "
            "with its page number and kind beneath, in a grid like a PDF "
            "viewer's page panel (48 pages per sheet). Look at them to take a "
            "long document in at a glance — spot the plan, the logs, the "
            "tables, the marked-up pages (red frame) — before reading. Pass "
            "pages to sheet only a range (e.g. pages_to_view)."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "columns": {"type": "integer", "minimum": 1, "maximum": 12},
            },
            "required": ["handle"],
        },
    },
    {
        "name": "render_page",
        "description": (
            "Render one page to an image file and return its path, so you can "
            "look at the page. Use it for any page the other tools flag with "
            "'! look:' (scans, figures, drawing sheets, forms) and whenever a "
            "text or table result looks incomplete or wrong. The image is in "
            "the displayed-page frame: boxes from read_document and markups "
            "map onto it directly. Resolution is chosen automatically (the "
            "largest your vision model reads without shrinking); pass dpi to "
            "change it. The result gives the image's pixel size; to zoom on "
            "something you see in it, pass its image_path to render_region."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "page": {"type": "integer", "minimum": 0},
                "dpi": {"type": "number", "minimum": 36, "maximum": 400},
            },
            "required": ["handle", "page"],
        },
    },
    {
        "name": "find_like",
        "description": (
            "Find EVERY copy of one mark — a tag, a code, a symbol — across "
            "the document, including drawing sheets whose lettering is drawn "
            "as lines and cannot be searched as text. Give the box round ONE "
            "copy (tight round its lettering; a legend row is fine): handle + "
            "page + bbox in PDF points, or image + image_box from an earlier "
            "render. Every page is image-matched at any scale and rotation in "
            "seconds; each hit is labelled callout (a leader is drawn from it; "
            "points_to is where it points), unanchored (on the plan, no "
            "leader) or legend (a legend/schedule row, or the same place on "
            "most sheets — left out unless include_legend). Hits are "
            "CANDIDATES: read the numbered contact sheets it writes to keep "
            "only exact matches. Use it when every occurrence of one "
            "repeated mark across many sheets is wanted."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "page": {"type": "integer", "minimum": 0},
                "bbox": {"type": "array", "items": {"type": "number"},
                         "minItems": 4, "maxItems": 4},
                "image": {"type": "string"},
                "image_box": {"type": "array", "items": {"type": "number"},
                              "minItems": 4, "maxItems": 4},
                "box_units": {"type": "string", "enum": ["px", "norm1000"]},
                "pages": PAGES_SCHEMA,
                "threshold": {"type": "number", "minimum": 0.3, "maximum": 0.95},
                "include_legend": {"type": "boolean"},
            },
            "required": [],
        },
    },
    {
        "name": "render_region",
        "description": (
            "Render a zoomed-in region of a page to an image file — to read "
            "small lettering, see what a markup points at, or check a "
            "dimension, symbol, curve or detail. Say WHERE one of two ways: "
            "(1) handle + page + bbox = [x0, y0, x1, y1] in PDF points, "
            "displayed page, top-left origin — a box straight from a text "
            "line, table or markup; or (2) image = the image_path of an "
            "earlier render + image_box = [x0, y0, x1, y1] on THAT image, "
            "top-left origin, in box_units ('px' = its pixels, 'norm1000' = "
            "a 0-999 grid over it; the render's note says which to use) — "
            "for something you found by looking. The region is re-drawn "
            "from the PDF as large as your vision model reads, so zooming "
            "twice keeps gaining detail. Optional marks = [[x, y, label], "
            "...] (PDF points) draw numbered circles so you can ask 'what is "
            "at mark 2'. Padded 10%."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "page": {"type": "integer", "minimum": 0},
                "bbox": {"type": "array", "items": {"type": "number"},
                         "minItems": 4, "maxItems": 4},
                "image": {"type": "string"},
                "image_box": {"type": "array", "items": {"type": "number"},
                              "minItems": 4, "maxItems": 4},
                "box_units": {"type": "string", "enum": ["px", "norm1000"]},
                "marks": {"type": "array",
                          "items": {"type": "array", "minItems": 2,
                                    "maxItems": 3}},
                "dpi": {"type": "number", "minimum": 36, "maximum": 1200},
                "pad_frac": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": [],
        },
    },
]
