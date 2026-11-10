"""The host layer's tools, run the way the app's tool functions run them.

BACKLOG PL-29 (a). :func:`call` takes a host tool's name and its arguments
as the model wrote them against :func:`planlens.review.specs.host_specs` and
returns the JSON text the model reads:

* the document tools hand their arguments on as the app's tool functions
  do (``pdf_pages`` beside ``pages``; a fuzzy ``min_score`` given without
  ``fuzzy`` said, not used) to :func:`planlens.review.conversation.
  dispatch_document_tool`;
* ``annotate_document`` turns ``pdf_page`` into ``page``, refuses a bad
  ``remove`` entry, names marks by their words, writes the marked copy
  (:func:`planlens.review.marked_copy.write_marked_copy`), says what the
  copy holds and CHECKS every mark with the host's look
  (:func:`planlens.review.markup_check.check_marks`);
* ``find_like`` searches for every copy of one mark and reads each
  candidate with the host's look (:func:`planlens.review.find_like.
  find_like`); it is not offered without a look.

The host is :func:`planlens.review.host.current`: the process's
(:func:`planlens.review.host.configure`), or one conversation's (``call(...,
host=)``, :func:`planlens.review.host.using`).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from planlens.review import conversation as _conv
from planlens.review import marked_copy as _marks
from planlens.review import specs as _specs

#: Said when a fuzzy ``min_score`` came with an exact search.
MIN_SCORE_UNUSED_NOTE = ("min_score was not used: it is the lowest score of "
                         "a fuzzy match, and this search was exact "
                         "(fuzzy=false)")

#: Said by ``find_like`` on an image about the frame of every box it returns.
FIND_ON_IMAGE_FRAME = (
    "every box here (example, found, uncertain) is in this {w} x {h} px "
    "image's own pixels (top-left origin, y down), as its looks give them "
    "and as render_region and annotate_document on it take them")


def _host():
    from planlens.review.host import current
    return current()


def _sheet_tools(host) -> bool:
    """Whether ``host`` asked for the sheet tools
    (``toolkit_options={"sheet_tools": True}``: its toolkits then serve
    them, and they are listed)."""
    return bool((getattr(host, "toolkit_options", None) or {})
                .get("sheet_tools"))


def tool_names(host=None) -> tuple:
    """The host tools offered with ``host`` (default: the current one;
    ``find_like`` only with a look, the sheet tools only where the host
    asked for them)."""
    host = host or _host()
    return _specs.host_tool_names(look=host.look is not None,
                                  sheet_tools=_sheet_tools(host))


def specs(style: str = "plain", host=None):
    """The specs of :func:`tool_names` (:func:`planlens.review.specs.
    host_specs`)."""
    host = host or _host()
    return _specs.host_specs(style, look=host.look is not None,
                             sheet_tools=_sheet_tools(host))


def _json_with(raw: str, **keys: Any) -> str:
    """A JSON tool result with ``keys`` added; an error, or a result that is
    not a JSON object, is left as it is."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return raw
    if not isinstance(data, dict) or "error" in data:
        return raw
    data.update(keys)
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _with_pdf_pages(args: Dict[str, Any], pdf_pages: Any) -> Dict[str, Any]:
    if pdf_pages not in (None, "", [], ()):
        args["pdf_pages"] = pdf_pages
    return args


def _document_args(name: str, a: Dict[str, Any]) -> Dict[str, Any]:
    """The arguments a document tool hands on, as the app's tool function
    builds them (defaults filled in, empty optionals left out)."""
    def given(key):
        return a.get(key) not in (None, "")

    if name == "open_document":
        return {"source": a.get("source")}
    if name == "document_structure":
        return {"handle": a.get("handle"), "offset": a.get("offset", 0)}
    args: Dict[str, Any] = {"handle": a.get("handle")}
    if name == "render_page_thumbnails":
        args["columns"] = a.get("columns", 6)
    elif name == "document_page_map":
        if a.get("kind"):
            args["kind"] = a["kind"]
        if a.get("with_evidence"):
            args["with_evidence"] = True
    elif name == "read_document":
        args.update(start_line=a.get("start_line", 0),
                    with_locations=a.get("with_locations", False),
                    include_tables=a.get("include_tables", True),
                    include_markups=a.get("include_markups", True))
    elif name == "search_document":
        args.update(pattern=a.get("pattern"), regex=a.get("regex", False),
                    case_sensitive=a.get("case_sensitive", False),
                    include_markups=a.get("include_markups", True),
                    max_hits=a.get("max_hits", 100))
        if a.get("fuzzy"):
            args["fuzzy"] = True
            if a.get("min_score") is not None:
                args["min_score"] = a["min_score"]
    elif name == "find_quantities":
        args.update(include_markups=a.get("include_markups", True),
                    offset=a.get("offset", 0))
        for key in ("kinds", "units"):
            if a.get(key) not in (None, "", [], ()):
                args[key] = a[key]
    elif name == "document_markups":
        args["offset"] = a.get("offset", 0)
        if a.get("author"):
            args["author"] = a["author"]
    elif name == "sheet_index":
        return {"handle": a.get("handle"), "offset": a.get("offset", 0)}
    elif name == "check_cross_references":
        args["offset"] = a.get("offset", 0)
        if a.get("status"):
            args["status"] = a["status"]
    if given("pages"):
        args["pages"] = a["pages"]
    return _with_pdf_pages(args, a.get("pdf_pages"))


def call(name: str, arguments: Optional[Dict[str, Any]] = None,
         attachments: Optional[Dict[str, bytes]] = None,
         max_chars: Optional[int] = None, host=None) -> str:
    """Run host tool ``name``; returns the JSON text the model reads. Never
    raises. ``host``: this conversation's host for the call (its look, its
    signer, ...; :func:`planlens.review.host.using`), else the current
    one."""
    if host is not None:
        from planlens.review.host import using
        with using(host):
            return call(name, arguments, attachments, max_chars)
    a = dict(arguments or {})
    unknown = sorted(set(a) - {arg for arg, _f, _d in
                               _specs._ARGS.get(name, ())})
    if name not in tool_names():
        return json.dumps({"error": f"unknown tool '{name}'",
                           "hint": f"available tools: {list(tool_names())}"})
    if unknown:
        return json.dumps({"error": f"{name} takes no argument(s) {unknown}",
                           "hint": "check the names against the tool's "
                                   "schema"})
    try:
        if name == "annotate_document":
            return annotate_document(attachments=attachments,
                                     max_chars=max_chars, **a)
        if name == "find_like":
            return find_like(a, attachments)
        if name == "search_document" and a.get("min_score") is not None \
                and not a.get("fuzzy"):
            raw = _conv.dispatch_document_tool(
                name, _document_args(name, a), attachments, max_chars)
            return _json_with(raw, min_score_unused=MIN_SCORE_UNUSED_NOTE)
        return _conv.dispatch_document_tool(
            name, _document_args(name, a), attachments, max_chars)
    except Exception as exc:  # noqa: BLE001 - a tool error, never the turn's
        return _host().tool_error_json(name, exc)


# -- annotate_document ---------------------------------------------------------

def _host_error(host, tool: str, exc: BaseException) -> Dict[str, Any]:
    """The host's error result for ``tool`` (its ``error_json``), as a
    dict."""
    from planlens.review.host import tool_error
    raw = host.tool_error_json(tool, exc)
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        data = None
    return data if isinstance(data, dict) else tool_error(tool, exc)


def annotate_document(handle: str, markups: Optional[list] = None,
                      output_path: str = "", append: bool = True,
                      check: bool = True, remove: Optional[list] = None,
                      attachments: Optional[Dict[str, bytes]] = None,
                      max_chars: Optional[int] = None) -> str:
    """Write review comments onto a COPY of the PDF, signed by the host, and
    check every mark with the host's look (``check``, default on). ``remove``
    takes marks this host wrote out of an existing marked copy
    (``output_path``) first -- each a markup id as document_markups lists it
    on that copy (``"p0.m1"``) or words from its comment or its visible
    label naming one mark -- so ``remove`` plus ``markups`` changes one mark
    and keeps the rest. There is no ``author`` argument: the signature is
    always the host's."""
    from planlens.review import markup_check
    host = _host()

    def dispatch(args):
        return _conv.dispatch_document_tool(
            "annotate_document", args, attachments, max_chars)

    markups, problem = _marks.markups_with_pages(markups)
    if problem is None:
        problem = _marks.remove_problem(remove)
    if problem is not None:
        return json.dumps(problem)
    signer = host.signed_by()
    refused_here = []
    if remove:
        try:
            final = _marks.markup_output_path(output_path, handle)
            if os.path.isfile(final):
                with open(final, "rb") as fh:
                    named, refused_here = markup_check.name_marks_by_words(
                        fh.read(), remove, signer)
                remove = named or [""]
        except Exception:  # noqa: BLE001 - the remover's own matching stands
            refused_here = []
    try:
        image = _marks.image_of(handle, attachments)
        result = _marks.write_marked_copy(
            handle, markups, output_path, append, signer, dispatch,
            remove=remove, image=image)
    except Exception as exc:  # noqa: BLE001 - a tool error, not the turn's
        return json.dumps(host.hidden(
            _host_error(host, "annotate_document", exc)))
    if not isinstance(result, dict):
        return json.dumps(host.hidden({"error": str(result)}))
    if refused_here and "error" not in result:
        result["not_removed"] = (list(result.get("not_removed") or [])
                                 + refused_here)
    out_pdf = result.get("output_path")
    if out_pdf and os.path.isfile(out_pdf) and "error" not in result:
        try:
            markup_check.describe_copy(result, signer)
        except Exception:  # noqa: BLE001 - the toolkit's own note stands
            pass
    if check and out_pdf and os.path.isfile(out_pdf) \
            and "error" not in result:
        try:
            block = markup_check.check_marks(out_pdf, result,
                                             list(markups or []), host.look)
        except Exception as exc:  # noqa: BLE001 - the file is written
            # "the placement check failed: <the error, in the host's words>"
            block = {"error": _host_error(host, "the placement check",
                                          exc).get("error")}
        if block is not None:
            result["check"] = block
    _marks.image_frame_result(result)
    return json.dumps(host.hidden(result))


# -- find_like -------------------------------------------------------------------

def _page_count(data: bytes) -> Optional[int]:
    try:
        import pymupdf
        with pymupdf.open(stream=bytes(data), filetype="pdf") as doc:
            return doc.page_count
    except Exception:  # noqa: BLE001 - the search says what is wrong
        return None


def _checked_page(arguments: Dict[str, Any], data: bytes):
    """``(page, None)`` -- the 0-based page ``page`` or ``pdf_page``
    (1-based) names, checked against the document -- or ``(None,
    error_json)``."""
    from planlens.review.page_numbers import page_error, resolve_page
    given_pdf = arguments.get("pdf_page") not in (None, "")
    given_page = arguments.get("page") not in (None, "")
    page, problem = resolve_page(arguments.get("page"),
                                 arguments.get("pdf_page"))
    if problem is not None:
        return None, json.dumps(problem)
    n = _page_count(data)
    if n is not None:
        problem = page_error(page, n, as_pdf_page=given_pdf and not given_page)
        if problem is not None:
            return None, json.dumps(problem)
    return page, None


def _named_contact_sheets(out):
    """``find_like``'s result with its contact sheets named by the host's
    ``display_path``; the server path stays internal."""
    if isinstance(out, dict) and out.get("contact_sheets"):
        host = _host()
        out["contact_sheets"] = [host.shown(p) for p in out["contact_sheets"]]
        out.setdefault("contact_sheets_note", (
            "the contact sheets are working images in this conversation's "
            "scratch folder: view one with analyze_image(attachment_key=<its "
            "name>)"))
    return out


def _source_bytes(key: str, attachments: Optional[Dict[str, bytes]]):
    """The bytes ``find_like`` searches: the host's ``find_like_source``
    (the app's own words for a key it cannot find), else the document
    tools' resolver."""
    own = _host().find_like_source
    if own is not None:
        return bytes(own(key, dict(attachments or {})))
    resolved = _conv.resolve_document_source(key, attachments)
    if isinstance(resolved, (bytes, bytearray)):
        return bytes(resolved)
    with open(resolved, "rb") as fh:
        return fh.read()


def find_like(arguments: Dict[str, Any],
              attachments: Optional[Dict[str, bytes]] = None) -> str:
    """Every copy of one mark across a drawing set, each read with the
    host's look. Arguments as :data:`planlens.review.specs` gives them."""
    from planlens.review import find_like as _fl
    from planlens.review import frames
    from planlens.review.host import image_kind
    from planlens.review.page_numbers import pdf_pages_to_pages, range_hint
    host = _host()
    key = arguments.get("attachment_key", "")
    bbox = arguments.get("bbox")
    view, image_box = arguments.get("view"), arguments.get("image_box")
    if view is not None or image_box is not None:
        if bbox is not None:
            return json.dumps({"error": "pass bbox OR view + image_box, "
                                        "not both"})
        try:
            bbox = list(frames.image_box_to_page(view or [], image_box or []))
        except (TypeError, ValueError) as e:
            return json.dumps({"error": f"view + image_box: {e}"})
    if not bbox or len(bbox) != 4:
        return json.dumps({
            "error": "find_like needs the box round ONE copy of the mark",
            "hint": "zoom (render_region) until one copy is legible, then "
                    "pass its bbox in PDF points — or that result's view + "
                    "the 0-999 image_box of the copy; a legend row is a fine "
                    "example"})
    try:
        pdf_bytes = _source_bytes(key, attachments)
    except Exception as e:  # noqa: BLE001 - ToolError or OSError, said
        out = {"error": str(e)}
        if getattr(e, "hint", None):
            out["hint"] = e.hint
        return json.dumps(out)
    if image_kind(pdf_bytes[:16]) is not None:
        return _find_like_on_image(arguments, pdf_bytes, key, bbox)
    page, refused = _checked_page(arguments, pdf_bytes)
    if refused is not None:
        return refused
    pages = arguments.get("pages")
    one_based = arguments.get("pdf_pages") not in (None, "", [], ())
    if one_based:
        if pages not in (None, "", [], ()):
            return json.dumps({"error": "give pages (0-based) or pdf_pages "
                                        "(1-based), not both"})
        pages, problem = pdf_pages_to_pages(arguments.get("pdf_pages"))
        if problem is not None:
            return json.dumps(problem)
    try:
        save_dir = host.scratch()
    except OSError:
        save_dir = None
    try:
        out = _fl.find_like(
            pdf_bytes, int(page), [float(v) for v in bbox], host.look,
            text=(arguments.get("text") or None), pages=pages,
            include_legend=bool(arguments.get("include_legend", False)),
            threshold=arguments.get("threshold"), save_dir=save_dir,
            budget=host.find_like_budget())
    except ImportError as e:
        return json.dumps({"error": f"find_like cannot run here: {e}"})
    except (ValueError, IndexError) as e:
        hint = range_hint(str(e), one_based)
        return json.dumps({"error": str(e),
                           "hint": hint or (
                               "box the mark's lettering tightly, with no "
                               "leader or table rule inside the box")})
    return json.dumps(_named_contact_sheets(out))


def _find_like_on_image(arguments, data, key, bbox) -> str:
    """``find_like`` on an IMAGE file: the example box is in the image's own
    pixels, the image is searched at its own resolution, and every box comes
    back in those pixels. Needs the host's image support."""
    from planlens.review import find_like as _fl
    from planlens.review.host import off_image_note
    host = _host()
    images = host.images
    picture, refusal = (images.open_image(data, key) if images is not None
                        else (None, None))
    if refusal is not None:
        return refusal
    if picture is None:
        return json.dumps({"error": "this image could not be decoded here "
                                    "(Pillow is missing, or the file is "
                                    "damaged), so it cannot be searched"})
    W, H = picture.width, picture.height
    try:
        box = [float(v) for v in bbox]
        x0, x1 = min(box[0], box[2]), max(box[0], box[2])
        y0, y1 = min(box[1], box[3]), max(box[1], box[3])
    except (TypeError, ValueError, IndexError):
        return json.dumps({"error": "bbox must be [x0, y0, x1, y1] in the "
                                    "image's own pixels"})
    off = off_image_note((x0, y0, x1, y1), W, H)
    if off:
        return json.dumps({
            "error": f"the example box is {off}",
            "hint": ("on an image, bbox and view are in its own pixels "
                     "(top-left origin, y down), as analyze_image and "
                     "render_region on it give them")})
    notes: Dict[str, Any] = {}
    if any(arguments.get(k) not in (None, "", [], (), 0, "0")
           for k in ("page", "pages")) or any(
            arguments.get(k) not in (None, "", [], (), 1, "1")
            for k in ("pdf_page", "pdf_pages")):
        notes["page_note"] = ("an image has one page: page and pages were "
                              "not used")
    s = images.page_scale(picture)
    try:
        save_dir = host.scratch()
    except OSError:
        save_dir = None
    try:
        out = _fl.find_like(
            images.as_pdf(picture), 0, [x0 * s, y0 * s, x1 * s, y1 * s],
            host.look, text=(arguments.get("text") or None), pages=None,
            include_legend=bool(arguments.get("include_legend", False)),
            threshold=arguments.get("threshold"), save_dir=save_dir,
            dpi=72.0 / s, budget=host.find_like_budget())
    except ImportError as e:
        return json.dumps({"error": f"find_like cannot run here: {e}"})
    except (ValueError, IndexError) as e:
        return json.dumps({"error": str(e),
                           "hint": ("box the mark's lettering tightly, in "
                                    "the image's own pixels, with no leader "
                                    "or table rule inside the box")})
    if isinstance(out, dict):
        _image_find_boxes(out, s)
        out["frame"] = FIND_ON_IMAGE_FRAME.format(w=W, h=H)
        out.update(notes)
    return json.dumps(_named_contact_sheets(out))


def _image_find_boxes(out: Dict[str, Any], s: float) -> None:
    """``find_like``'s boxes, searched on a page of ``s`` points a pixel,
    as the image's own pixels; the example's height is said in pixels."""
    def px(values):
        return [round(float(v) / s, 1) for v in values]

    example = out.get("example")
    if isinstance(example, dict):
        if isinstance(example.get("bbox"), (list, tuple)):
            example["bbox"] = px(example["bbox"])
        if "height_pt" in example:
            example["height_px"] = round(
                float(example.pop("height_pt")) / s, 1)
    if s == 1.0:
        return
    for key in ("found", "uncertain", "unverified"):
        for row in out.get(key) or ():
            if not isinstance(row, dict):
                continue
            for field in ("bbox", "points_to"):
                if isinstance(row.get(field), (list, tuple)):
                    row[field] = px(row[field])


__all__ = ["call", "tool_names", "specs", "annotate_document", "find_like",
           "MIN_SCORE_UNUSED_NOTE", "FIND_ON_IMAGE_FRAME"]
