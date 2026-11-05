"""The host layer's tool specs: name, description and input schema.

BACKLOG PL-29 (a). These are the tools whose code moved here from the
GeotechStaffEngineer app, and their specs are the app's WORD FOR WORD, so
neither of its pages' agents changes when it binds this layer:

* the document tools (``open_document`` ... ``annotate_document``): the
  description is :data:`planlens.tools.TOOL_SPECS`' own, with the host
  layer's note where it changes the contract (:data:`DOCUMENT_TOOL_NOTES`),
  and the arguments are the host layer's (1-based ``pdf_pages`` /
  ``pdf_page`` beside the 0-based ones, ``check`` and ``remove`` on
  ``annotate_document``, no ``author``);
* ``find_like``, which reads every candidate with the host's look.

Each input schema is written the way the app's agent framework writes the
schema of the app's tool function (pydantic's JSON schema: a ``title`` per
argument, the function's default), because that is what the model was
shown. ``tests/app_tool_specs.json`` holds the app's own capture, and
``tests/test_host_specs.py`` checks these equal it.

A tool that needs the look (:data:`LOOK_TOOLS`) is left out when the host
gives none (:func:`host_tool_names`).
"""

from __future__ import annotations

import copy
from typing import Any, Dict, List, Optional, Tuple

#: What ``find_like`` does with an image file (VIS-26), in the one sentence
#: every surface's description uses (never a system prompt).
IMAGE_FIND_NOTE = (
    "attachment_key may also be an IMAGE (an upload, a pasted screenshot, a "
    "photo): then bbox and view are in its own pixels, page is not used, and "
    "the copies come back in those pixels.")

#: What ``annotate_document`` does with an image file (VIS-26), in the one
#: sentence every surface's description uses (never a system prompt).
IMAGE_MARKUP_NOTE = (
    "On an IMAGE (an upload, a pasted screenshot, a photo), bbox, point and "
    "view are in its own pixels, as its looks give them, and the marked "
    "copy is a PDF whose points are those pixels.")

#: Where the host layer changes a toolkit tool's contract, the model is told
#: (the host settles where a file belongs and who signs the review).
#: Every page-range document tool also takes the viewer's 1-based numbers
#: (live smoke wave 2c: a model passed PDF page 5 of a 5-page document).
PDF_PAGES_NOTE = (" pages counts from 0; or give pdf_pages instead, "
                   "1-based as a PDF viewer shows (e.g. '1-3,6').")
#: The same for a one-page argument.
PDF_PAGE_NOTE = (" page counts from 0; or give pdf_page instead, 1-based "
                  "as a PDF viewer shows.")

DOCUMENT_TOOL_NOTES = {
    # Live smoke wave 2b, C2 / C8: MuPDF read a workbook as nine digits and
    # could not open a DXF; both are read as text by the app instead.
    "open_document": (
        " In this app it also opens a Word (.docx), Excel (.xlsx, .xlsm) or "
        "DXF file -- not as pages but as text: a Word or Excel file as "
        "Markdown (one table per sheet), a DXF as its text, leaders and "
        "dimensions with their positions, layers and entity counts. Its file "
        "name is then its handle for read_document (start_line pages "
        "through it) and search_document; the page, markup and vision tools "
        "do not apply to it."),
    "read_document": (
        " For a Word, Excel or DXF file, handle is its file name and "
        "start_line pages through its lines (pages does not apply)."
        + PDF_PAGES_NOTE),
    "search_document": (
        " For a Word, Excel or DXF file, handle is its file name; hits are "
        "its lines." + PDF_PAGES_NOTE),
    "document_page_map": PDF_PAGES_NOTE,
    "document_markups": PDF_PAGES_NOTE,
    "find_quantities": PDF_PAGES_NOTE,
    "render_page_thumbnails": PDF_PAGES_NOTE,
    "annotate_document": (
        " In this app give output_path a bare file name: it is written into "
        "this conversation's working folder, attached to the reply as a "
        "download, and it defaults to <document>_marked.pdf. The app signs "
        "every comment itself — the person using it, via this app, as an AI "
        "draft, which is what the reviewer must be able to see — so there is "
        "no author argument. For a thing you found by looking, pass the view and "
        "image_box of the ZOOMED look in which the thing is legible (a view "
        "of 300 pt or less; a whole-page look only says where to zoom, and "
        "a small mark from it is refused) — never a box from memory or an "
        "estimate. Every mark is then CHECKED, whatever its anchor (box, "
        "point, quote or note): a crop of the marked copy is looked at and "
        "the result's `check` says which marks are on the thing their "
        "target or quoted words name — or, naming neither, the thing their "
        "comment is about — and which are misplaced. A mark put on blank "
        "paper on purpose (a stamp in an empty corner) names that area as "
        "its target; the check reports it under on_blank_paper, which a "
        "look cannot confirm: tell the user where it sits. A label is only the "
        "text the reader sees beside a mark (a tag, a verdict such as "
        "'460.1?'): it is never compared with what is under the mark, so it "
        "need not match it. Never hand over a file with misplaced marks: "
        "find those things again and rewrite the copy with append=false "
        "(a misplaced ring or box may come back with found_bbox, where the "
        "check saw the thing: pass it as that mark's bbox). Keep correcting "
        "without stopping to ask, up to the number of checks the note "
        "gives; after the last, hand the file over and say which marks are "
        "unconfirmed. "
        "To change or delete ONE mark of a marked copy you wrote, call again "
        "with output_path = that copy and remove = [its id as "
        "document_markups lists it on that copy, e.g. 'p0.m1', or words "
        "from its comment or its label]; the marks you give in markups are "
        "added, every other mark stays (only this app's marks can be "
        "removed; ids change after a removal, so list them again before "
        "removing another). The result's in_file counts what the copy "
        "holds: the marks this call added and those kept from before, by "
        "author — the document's own markups are always kept. A markup may "
        "give pdf_page (1-based, as a PDF viewer shows) instead of page "
        "(0-based). " + IMAGE_MARKUP_NOTE),
}


#: ``find_like``'s description (the app's, word for word).
FIND_LIKE_DESCRIPTION = (
    "Find EVERY copy of one mark (tag, code, symbol) across a drawing "
    "set — even lettering drawn as lines — from one zoomed, confirmed "
    "example box + the text it reads; every candidate is verified by "
    "vision. Returns instances by page, callouts (with where each "
    "leader points) apart from legend entries, and uncertain reads. "
    "page / pages count from 0; pdf_page / pdf_pages are the same "
    "1-based, as a PDF viewer shows. " + IMAGE_FIND_NOTE)

#: The document tools of the host layer, in the order the app offers them.
DOCUMENT_TOOLS = (
    "open_document", "document_structure", "document_page_map",
    "read_document", "search_document", "document_markups",
    "render_page_thumbnails",
)
#: Served only when the installed toolkit publishes them.
OPTIONAL_DOCUMENT_TOOLS = ("find_quantities", "annotate_document")

#: Tools that need the host's look: left out without one.
LOOK_TOOLS = ("find_like",)

#: Every tool this layer specifies.
HOST_TOOLS = DOCUMENT_TOOLS + OPTIONAL_DOCUMENT_TOOLS + LOOK_TOOLS

# -- the arguments, as the app's tool functions declare them -----------------
# (name, form, default); a name with no default is required. Forms:
# "str", "int", "bool", "any" (no type), and "opt_int" / "opt_str" /
# "opt_list" (a type or null).
_REQUIRED = object()

_ARGS: Dict[str, List[Tuple[str, str, Any]]] = {
    "open_document": [("source", "str", _REQUIRED)],
    "document_structure": [("handle", "str", _REQUIRED),
                           ("offset", "int", 0)],
    "render_page_thumbnails": [("handle", "str", _REQUIRED),
                               ("pages", "any", None),
                               ("columns", "int", 6),
                               ("pdf_pages", "any", None)],
    "document_page_map": [("handle", "str", _REQUIRED),
                          ("pages", "any", None), ("kind", "str", ""),
                          ("with_evidence", "bool", False),
                          ("pdf_pages", "any", None)],
    "read_document": [("handle", "str", _REQUIRED), ("pages", "any", None),
                      ("start_line", "int", 0),
                      ("with_locations", "bool", False),
                      ("include_tables", "bool", True),
                      ("include_markups", "bool", True),
                      ("pdf_pages", "any", None)],
    "search_document": [("handle", "str", _REQUIRED),
                        ("pattern", "str", _REQUIRED),
                        ("pages", "any", None), ("regex", "bool", False),
                        ("case_sensitive", "bool", False),
                        ("include_markups", "bool", True),
                        ("max_hits", "int", 100), ("fuzzy", "bool", False),
                        ("min_score", "opt_int", None),
                        ("pdf_pages", "any", None)],
    "find_quantities": [("handle", "str", _REQUIRED), ("pages", "any", None),
                        ("kinds", "any", None), ("units", "any", None),
                        ("include_markups", "bool", True),
                        ("offset", "int", 0), ("pdf_pages", "any", None)],
    "document_markups": [("handle", "str", _REQUIRED),
                         ("pages", "any", None), ("author", "str", ""),
                         ("offset", "int", 0), ("pdf_pages", "any", None)],
    "annotate_document": [("handle", "str", _REQUIRED),
                          ("markups", "opt_list", None),
                          ("output_path", "str", ""),
                          ("append", "bool", True), ("check", "bool", True),
                          ("remove", "opt_list", None)],
    "find_like": [("attachment_key", "str", _REQUIRED),
                  ("page", "opt_int", None), ("bbox", "opt_list", None),
                  ("text", "str", ""), ("pages", "opt_str", None),
                  ("include_legend", "bool", False),
                  ("view", "opt_list", None),
                  ("image_box", "opt_list", None),
                  ("pdf_page", "opt_int", None),
                  ("pdf_pages", "opt_str", None)],
}

_FORMS = {
    "str": {"type": "string"}, "int": {"type": "integer"},
    "bool": {"type": "boolean"}, "any": {},
    "opt_int": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
    "opt_str": {"anyOf": [{"type": "string"}, {"type": "null"}]},
    "opt_list": {"anyOf": [{"items": {}, "type": "array"},
                           {"type": "null"}]},
}


def _title(name: str) -> str:
    """An argument's title as pydantic writes it (``pdf_pages`` ->
    ``Pdf Pages``)."""
    return " ".join(w.capitalize() for w in name.split("_"))


#: A markup's fields the host layer takes beside the toolkit's own:
#: ``pdf_page`` is turned into ``page`` (1-based, as a viewer shows).
APP_MARKUP_FIELDS = {
    "pdf_page": {"type": "integer", "minimum": 1,
                 "description": "1-based, instead of page."},
}

#: The toolkit's markup fields the host layer does not offer: it signs every
#: comment itself.
WITHHELD_MARKUP_FIELDS = ("author",)

#: The markup fields shown with the toolkit's description of them. The
#: others' descriptions repeat the tool's own (view + image_box, target,
#: quote, reply_to, page ...), so they are shown by name and type only.
DESCRIBED_MARKUP_FIELDS = ("kind", "bbox", "points_at", "min_score",
                           "pdf_page")


def _toolkit_spec(name: str) -> Optional[Dict[str, Any]]:
    """The installed toolkit's spec for ``name``, or ``None``."""
    from planlens.tools.specs import TOOL_SPECS
    for spec in TOOL_SPECS:
        if spec.get("name") == name:
            return spec
    return None


def annotate_markups_schema() -> Optional[Dict[str, Any]]:
    """``annotate_document``'s ``markups`` as the model is shown it: the
    toolkit's own item schema (its fields, kinds and words, read off its
    spec rather than copied here, so it cannot drift), with ``pdf_page``,
    without ``author`` (the host signs), described only where the tool's
    description does not already say it (:data:`DESCRIBED_MARKUP_FIELDS`)
    and closed to any other key. ``None`` when the spec has no item schema.

    It is what the model is SHOWN; nothing checks a call against it. The
    tool's own check takes any list, so the names the toolkit forgives
    (``color``/``colour`` dropped with a note, ``box``/``page_bbox`` read
    as ``bbox``, a nested ``anchor``, a kind's other name) still reach it."""
    spec = _toolkit_spec("annotate_document") or {}
    schema = spec.get("parameters") or spec.get("input_schema") or {}
    prop = (schema.get("properties") or {}).get("markups")
    items = prop.get("items") if isinstance(prop, dict) else None
    if not isinstance(items, dict) or \
            not isinstance(items.get("properties"), dict):
        return None
    out = copy.deepcopy(prop)
    items = out["items"]
    fields = items["properties"]
    for name in WITHHELD_MARKUP_FIELDS:
        fields.pop(name, None)
    fields.update(copy.deepcopy(APP_MARKUP_FIELDS))
    for name, field in fields.items():
        if name not in DESCRIBED_MARKUP_FIELDS and isinstance(field, dict):
            field.pop("description", None)
    # page or pdf_page: neither alone is required.
    items["required"] = [r for r in items.get("required") or ()
                         if r != "page" and r in fields]
    items["additionalProperties"] = False
    return out


def _shown(name: str) -> Dict[str, Dict[str, Any]]:
    """Arguments shown to the model with a schema of their own rather than
    the one their declared type gives: ``annotate_document``'s ``markups``
    with the toolkit's markup fields and ``remove`` as strings;
    ``search_document``'s ``min_score`` with the toolkit's default, 80."""
    if name == "annotate_document":
        shown = {"remove": {"type": "array", "items": {"type": "string"}}}
        item = annotate_markups_schema()
        if item is not None:
            shown["markups"] = item
        return shown
    if name == "search_document":
        return {"min_score": {"type": "integer", "default": 80}}
    return {}


def input_schema(name: str, description: str) -> Dict[str, Any]:
    """The input schema of host tool ``name``, as the app showed it."""
    shown = _shown(name)
    props: Dict[str, Any] = {}
    required = []
    for arg, form, default in _ARGS[name]:
        if arg in shown:
            field = copy.deepcopy(shown[arg])
            if "default" not in field:
                field["default"] = default
        else:
            field = copy.deepcopy(_FORMS[form])
            if default is _REQUIRED:
                required.append(arg)
            else:
                field["default"] = default
        field["title"] = _title(arg)
        props[arg] = field
    return {"description": description, "properties": props,
            "required": required, "title": name, "type": "object"}


def tool_description(name: str) -> str:
    """Host tool ``name``'s model-facing description."""
    if name == "find_like":
        return FIND_LIKE_DESCRIPTION
    spec = _toolkit_spec(name)
    if spec is None:
        raise KeyError(name)
    return spec["description"] + DOCUMENT_TOOL_NOTES.get(name, "")


def host_tool_names(look: bool = True) -> Tuple[str, ...]:
    """The host tools to offer: the document tools the installed toolkit
    publishes, and the look tools only when the host gave a look."""
    names = DOCUMENT_TOOLS + tuple(n for n in OPTIONAL_DOCUMENT_TOOLS
                                   if _toolkit_spec(n) is not None)
    return names + (LOOK_TOOLS if look else ())


def host_specs(style: str = "plain", look: bool = True
               ) -> List[Dict[str, Any]]:
    """The host tools' specs: ``plain`` (name / description /
    input_schema), ``anthropic`` (the same) or ``openai``
    (``type=function``)."""
    out = []
    for name in host_tool_names(look):
        desc = tool_description(name)
        schema = input_schema(name, desc)
        if style in ("plain", "anthropic"):
            out.append({"name": name, "description": desc,
                        "input_schema": schema})
        elif style == "openai":
            out.append({"type": "function", "function": {
                "name": name, "description": desc, "parameters": schema}})
        else:
            raise ValueError("style must be plain, anthropic or openai")
    return out


__all__ = ["DOCUMENT_TOOL_NOTES", "PDF_PAGES_NOTE", "PDF_PAGE_NOTE",
           "IMAGE_FIND_NOTE", "IMAGE_MARKUP_NOTE", "FIND_LIKE_DESCRIPTION",
           "DOCUMENT_TOOLS", "OPTIONAL_DOCUMENT_TOOLS", "LOOK_TOOLS",
           "HOST_TOOLS", "APP_MARKUP_FIELDS", "WITHHELD_MARKUP_FIELDS",
           "DESCRIBED_MARKUP_FIELDS", "annotate_markups_schema",
           "input_schema", "tool_description", "host_tool_names",
           "host_specs"]
