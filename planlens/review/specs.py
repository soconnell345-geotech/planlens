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

Three groups are offered only to a host that asks: the sheet tools
(:data:`SHEET_TOOLS`), the plan tools (:data:`PLAN_TOOLS`, BACKLOG PL-27)
and the drawing tools (:data:`DRAWING_TOOLS`, BACKLOG DR-44), each
specified the way the others are.

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
    "check_cross_references": PDF_PAGES_NOTE,
    "check_schedule_marks": PDF_PAGES_NOTE,
    "plan_symbols": PDF_PAGE_NOTE,
    "plan_grid": PDF_PAGE_NOTE,
    "drawing_geometry": PDF_PAGE_NOTE,
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

#: The sheet tools (a drawing set's sheets from their title blocks, its
#: callouts checked against them, and the marks on its sheets checked
#: against its schedules): offered only to a host that asks, with
#: ``ReviewHost(toolkit_options={"sheet_tools": True})``. Not in
#: :data:`HOST_TOOLS`: that list is what every host has had.
SHEET_TOOLS = ("sheet_index", "check_cross_references",
               "check_schedule_marks")

#: The plan tools (a vector plan's symbols with their exact centres and
#: labels, and page points turned into local easting / northing): offered
#: only to a host that asks, with ``ReviewHost(toolkit_options={"plan_tools":
#: True})`` (BACKLOG PL-27). The toolkit itself serves them to everyone, as
#: it always has; this layer offers them to the hosts that asked. Not in
#: :data:`HOST_TOOLS`.
PLAN_TOOLS = ("plan_symbols", "plan_grid")

#: The drawing tools (one page's dimensions, leaders, callouts, revision
#: clouds and title block read from its drawn geometry: a fallback for a CAD
#: sheet the text and the look have not settled; BACKLOG DR-44): offered
#: only to a host that asks, with ``ReviewHost(toolkit_options=
#: {"drawing_tools": True})``, after every other group. Not in
#: :data:`HOST_TOOLS`.
DRAWING_TOOLS = ("drawing_geometry",)

# -- the arguments, as the app's tool functions declare them -----------------
# (name, form, default); a name with no default is required. Forms:
# "str", "int", "bool", "any" (no type), and "opt_int" / "opt_str" /
# "opt_list" / "opt_num" / "opt_dict" (a type or null).
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
    "sheet_index": [("handle", "str", _REQUIRED), ("offset", "int", 0)],
    "check_cross_references": [("handle", "str", _REQUIRED),
                               ("pages", "any", None),
                               ("status", "opt_str", None),
                               ("offset", "int", 0),
                               ("pdf_pages", "any", None)],
    "check_schedule_marks": [("handle", "str", _REQUIRED),
                             ("pages", "any", None),
                             ("status", "opt_str", None),
                             ("offset", "int", 0),
                             ("pdf_pages", "any", None)],
    # The plan tools: every argument but handle, page and pdf_page shown
    # as the toolkit shows it (:data:`PLAN_SHOWN_FROM_TOOLKIT`); nothing
    # given is nothing handed on, so the toolkit's defaults apply.
    "plan_symbols": [("handle", "str", _REQUIRED), ("page", "opt_int", None),
                     ("label_pattern", "opt_str", None),
                     ("shapes", "opt_list", None),
                     ("min_size", "opt_num", None),
                     ("max_size", "opt_num", None),
                     ("max_label_gap", "opt_num", None),
                     ("bbox", "opt_list", None),
                     ("pdf_page", "opt_int", None)],
    "plan_grid": [("controls", "opt_list", None), ("north", "opt_list", None),
                  ("scale", "opt_num", None), ("origin", "opt_dict", None),
                  ("points", "opt_list", None),
                  ("on_mirror", "opt_str", None), ("units", "opt_str", None),
                  ("handle", "opt_str", None), ("page", "opt_int", None),
                  ("pdf_page", "opt_int", None)],
    # The drawing tools: shown and handed on as the plan tools are.
    "drawing_geometry": [("handle", "str", _REQUIRED),
                         ("page", "opt_int", None),
                         ("find", "opt_list", None),
                         ("bbox", "opt_list", None),
                         ("pdf_page", "opt_int", None)],
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
    "opt_num": {"anyOf": [{"type": "number"}, {"type": "null"}]},
    "opt_dict": {"anyOf": [{"additionalProperties": True, "type": "object"},
                           {"type": "null"}]},
}

#: The plan tools' arguments shown with the toolkit's own schema of them
#: (type, limits and words, read off its spec so they cannot drift): all
#: but ``handle`` (shown as every document tool shows it) and ``page`` /
#: ``pdf_page`` (either may be given, so neither is required here).
PLAN_SHOWN_FROM_TOOLKIT = {
    "plan_symbols": ("label_pattern", "shapes", "min_size", "max_size",
                     "max_label_gap", "bbox"),
    "plan_grid": ("controls", "north", "scale", "origin", "points",
                  "on_mirror", "units"),
}

#: The drawing tools' arguments shown with the toolkit's own schema of them,
#: as :data:`PLAN_SHOWN_FROM_TOOLKIT`.
DRAWING_SHOWN_FROM_TOOLKIT = {"drawing_geometry": ("find", "bbox")}


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
    from planlens.tools import specs as toolkit_specs
    for spec in list(toolkit_specs.TOOL_SPECS) + list(
            getattr(toolkit_specs, "SHEET_TOOL_SPECS", ())) + list(
            getattr(toolkit_specs, "DRAWING_TOOL_SPECS", ())):
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
    ``search_document``'s ``min_score`` with the toolkit's default, 80;
    ``check_cross_references``' and ``check_schedule_marks``' ``status``
    with the toolkit's words; the plan tools' arguments as the toolkit
    shows them (:data:`PLAN_SHOWN_FROM_TOOLKIT`)."""
    if name == "annotate_document":
        shown = {"remove": {"type": "array", "items": {"type": "string"}}}
        item = annotate_markups_schema()
        if item is not None:
            shown["markups"] = item
        return shown
    if name == "search_document":
        return {"min_score": {"type": "integer", "default": 80}}
    if name in ("check_cross_references", "check_schedule_marks"):
        # the toolkit's words for an item's status, read off its spec
        spec = _toolkit_spec(name) or {}
        status = ((spec.get("parameters") or {}).get("properties")
                  or {}).get("status") or {}
        if status.get("enum"):
            return {"status": {"anyOf": [
                {"type": "string", "enum": list(status["enum"])},
                {"type": "null"}]}}
    from_toolkit = (PLAN_SHOWN_FROM_TOOLKIT.get(name)
                    or DRAWING_SHOWN_FROM_TOOLKIT.get(name))
    if from_toolkit:
        spec = _toolkit_spec(name) or {}
        props = (spec.get("parameters") or {}).get("properties") or {}
        return {arg: copy.deepcopy(props[arg])
                for arg in from_toolkit
                if isinstance(props.get(arg), dict)}
    return {}


#: Host tools that do not take sheet numbers though they take a page:
#: ``find_like`` reads its document from an attachment's bytes, with no
#: handle whose sheet index could say where a sheet is.
NO_SHEET_ARGS = ("find_like",)


def sheet_args(name: str, sheet_tools: bool = False
               ) -> List[Tuple[str, Dict[str, Any]]]:
    """The sheet-number arguments host tool ``name`` takes for a host that
    asked for the sheet tools (BACKLOG PL-37; none for any other host):
    ``sheets`` where it takes ``pages``, ``sheet`` where it takes ``page``,
    each with its schema as the model is shown it."""
    if not sheet_tools or name in NO_SHEET_ARGS:
        return []
    from planlens.tools.sheet_addresses import (SHEET_ARGS,
                                                SHEET_DESCRIPTION,
                                                SHEETS_DESCRIPTION)
    forms = {
        "sheets": {"anyOf": [{"type": "string"},
                             {"type": "array", "items": {"type": "string"}},
                             {"type": "null"}],
                   "default": None, "description": SHEETS_DESCRIPTION},
        "sheet": {"anyOf": [{"type": "string"}, {"type": "null"}],
                  "default": None, "description": SHEET_DESCRIPTION},
    }
    takes = {arg for arg, _form, _default in _ARGS.get(name, ())}
    return [(SHEET_ARGS[arg], forms[SHEET_ARGS[arg]])
            for arg in ("pages", "page") if arg in takes]


def host_arg_names(name: str, sheet_tools: bool = False) -> List[str]:
    """Every argument host tool ``name`` takes: the app's own, then, for a
    host that asked for the sheet tools, its sheet-number arguments
    (:func:`sheet_args`)."""
    return ([arg for arg, _form, _default in _ARGS.get(name, ())]
            + [arg for arg, _schema in sheet_args(name, sheet_tools)])


def input_schema(name: str, description: str,
                 sheet_tools: bool = False) -> Dict[str, Any]:
    """The input schema of host tool ``name``, as the app showed it; for a
    host that asked for the sheet tools, with its sheet-number arguments
    after the others (:func:`sheet_args`)."""
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
    for arg, field in sheet_args(name, sheet_tools):
        props[arg] = dict(field, title=_title(arg))
    schema: Dict[str, Any] = {"description": description,
                              "properties": props}
    if required:
        # pydantic writes no "required" for a function that has none
        # (plan_grid; every other tool has a required argument)
        schema["required"] = required
    schema.update(title=name, type="object")
    return schema


def tool_description(name: str) -> str:
    """Host tool ``name``'s model-facing description."""
    if name == "find_like":
        return FIND_LIKE_DESCRIPTION
    spec = _toolkit_spec(name)
    if spec is None:
        raise KeyError(name)
    return spec["description"] + DOCUMENT_TOOL_NOTES.get(name, "")


def host_tool_names(look: bool = True, sheet_tools: bool = False,
                    plan_tools: bool = False,
                    drawing_tools: bool = False) -> Tuple[str, ...]:
    """The host tools to offer: the document tools the installed toolkit
    publishes, the look tools only when the host gave a look, the sheet
    tools (:data:`SHEET_TOOLS`) only when the host asked for them, the
    plan tools (:data:`PLAN_TOOLS`) after them, only when the host asked
    for those, and the drawing tools (:data:`DRAWING_TOOLS`) last, only
    when the host asked for those."""
    names = DOCUMENT_TOOLS + tuple(n for n in OPTIONAL_DOCUMENT_TOOLS
                                   if _toolkit_spec(n) is not None)
    return (names + (LOOK_TOOLS if look else ())
            + (SHEET_TOOLS if sheet_tools else ())
            + (PLAN_TOOLS if plan_tools else ())
            + (DRAWING_TOOLS if drawing_tools else ()))


def host_specs(style: str = "plain", look: bool = True,
               sheet_tools: bool = False,
               plan_tools: bool = False,
               drawing_tools: bool = False) -> List[Dict[str, Any]]:
    """The host tools' specs: ``plain`` (name / description /
    input_schema), ``anthropic`` (the same) or ``openai``
    (``type=function``)."""
    out = []
    for name in host_tool_names(look, sheet_tools, plan_tools,
                                drawing_tools):
        desc = tool_description(name)
        schema = input_schema(name, desc, sheet_tools=sheet_tools)
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
           "HOST_TOOLS", "SHEET_TOOLS", "PLAN_TOOLS", "DRAWING_TOOLS",
           "PLAN_SHOWN_FROM_TOOLKIT", "DRAWING_SHOWN_FROM_TOOLKIT",
           "APP_MARKUP_FIELDS",
           "WITHHELD_MARKUP_FIELDS",
           "DESCRIBED_MARKUP_FIELDS", "NO_SHEET_ARGS",
           "annotate_markups_schema", "sheet_args", "host_arg_names",
           "input_schema", "tool_description", "host_tool_names",
           "host_specs"]
