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
            "hidden-CAD-text counts, duplicate_of. with_evidence adds the "
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
            "not absence — view them."),
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
            },
            "required": ["handle", "pattern"],
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
            "Render one page to a PNG file and return its path, so you can "
            "look at the page. Use it for any page the other tools flag with "
            "'! look:' (scans, figures, drawing sheets, forms) and whenever a "
            "text or table result looks incomplete or wrong. The image is in "
            "the displayed-page frame: boxes from read_document and markups "
            "map onto it directly. Resolution is chosen automatically (about "
            "2000 px on the long side); pass dpi to change it."),
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
        "name": "render_region",
        "description": (
            "Render a zoomed-in region of a page to a PNG file — to read "
            "small lettering, see what a markup points at, or check a "
            "dimension, symbol or detail. bbox is [x0, y0, x1, y1] in PDF "
            "points, displayed page, top-left origin: pass a box straight "
            "from a text line, table or markup. Optional marks = [[x, y, "
            "label], ...] draw numbered circles so you can ask 'what is at "
            "mark 2'. Default 200 dpi, padded 10%."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "page": {"type": "integer", "minimum": 0},
                "bbox": {"type": "array", "items": {"type": "number"},
                         "minItems": 4, "maxItems": 4},
                "marks": {"type": "array",
                          "items": {"type": "array", "minItems": 2,
                                    "maxItems": 3}},
                "dpi": {"type": "number", "minimum": 36, "maximum": 600},
                "pad_frac": {"type": "number", "minimum": 0, "maximum": 1},
            },
            "required": ["handle", "page", "bbox"],
        },
    },
]
