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
            "Open a PDF for review (report, drawing set, submittal, calcs). "
            "Returns a handle plus a map of the whole document: which pages "
            "are text, drawing sheets, figures or scans; sheet labels; "
            "bookmarks; how many review markups there are and by whom. Call "
            "this first for any document question — you do not need to know "
            "in advance whether the answer is in prose, a table, a drawing or "
            "a reviewer's comment."),
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
            "One row per page: kind (text / drawing_sheet / figure / scanned "
            "/ blank / mixed), label, largest heading, size, rotation, "
            "markup and hidden-CAD-text counts. Use it to find the pages "
            "that matter before reading. Continues via next_pages."),
        "parameters": {
            "type": "object",
            "properties": {
                "handle": HANDLE_SCHEMA,
                "pages": PAGES_SCHEMA,
                "kind": {"type": "string",
                         "enum": ["text", "drawing_sheet", "figure",
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
            "'cad' are AutoCAD hidden text behind stroked lettering. When the "
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
            "value, a boring or sheet id before reading those pages."),
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
]
