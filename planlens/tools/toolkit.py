"""ReviewToolkit — planlens as LLM tools, independent of any agent framework.

A host (a chat app, an agent framework, a notebook) gives the model these tools
by publishing :meth:`ReviewToolkit.specs` and routing each tool call to
:meth:`ReviewToolkit.call_json`. Everything else — opening documents once,
remembering them by handle, keeping every result inside the host's size limit,
turning mistakes into instructions the model can act on — happens here.

Tools
-----
``open_document``      open a PDF or image; returns a handle and a document map
``document_page_map``  one row per page: kind, label, heading, counts
``read_document``      text (optionally with locations), tables and markups
``search_document``    find text, hidden CAD text and markup comments,
                       exactly or approximately (``fuzzy``)
``find_quantities``    every number WITH A UNIT the document states, located
``document_markups``   the review record: every markup, with its threads
``annotate_document``  write review comments onto a COPY of the PDF, as
                       ordinary annotations a reviewer opens in Bluebeam
``document_structure`` the constituent documents: segments with their
                       running headers/footers and printed page numbers
``document_roles``     what each page IS — narrative, boring log, lab sheet,
                       calculation printout, appended report — the work items
                       those pages make, and on request the report's own
                       outline or one ledger line per page
``render_page``        a page as a PNG image, for the model to look at
``render_region``      a zoomed region (with optional numbered marks) as a PNG
``render_page_thumbnails``  contact sheets of every page, like a viewer's
                       page panel, to take a long document in at a glance

Text first, eyes second — and the tools say when. Every result that touches a
page the text cannot represent (a scan, a figure, a drawing sheet, a ruled form
read as a sparse grid) carries a ``! look:`` line saying so, followed by the
host's :attr:`ReviewToolkit.vision_hint` — its own instructions for how the
model views a page or region. A host whose model can see images gets them from
:meth:`ReviewToolkit.render` (bytes) or the two render tools (PNG files).

Conventions the model is told: pages are 0-based; coordinates are PDF points in
the displayed page frame (top-left origin, y down), the frame a rendered page
image uses; long results continue through a returned cursor.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import re
import tempfile
import threading
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from planlens.document import (
    DEFAULT_FUZZY_MIN_SCORE, Document, fuzzy_search_available, page_advice,
    parse_pages,
)
from planlens.document.quantities import KINDS as QUANTITY_KINDS
from planlens.tools.formatting import (
    compact_ranges, fit_items, json_len, render_markup, render_page,
    render_quantity,
)

Source = Union[bytes, str]

#: Default ceiling on every serialized result, below the reference host's
#: 8,000-character truncation with room for its wrapper.
DEFAULT_MAX_CHARS = 7500

#: Documents kept open at once; the least recently used is closed first.
DEFAULT_MAX_OPEN = 8

#: How the model views a page when no host instruction is configured: with
#: this toolkit's own render tools.
DEFAULT_VISION_HINT = ("to view it: render_page(handle, page) for a page, "
                       "render_region(handle, page, bbox) for a spot; then "
                       "look at the image")

#: Page kinds whose content is mostly not in the text.
VIEW_KINDS = ("scanned", "figure", "drawing_sheet", "form")

#: How the model views a PNG this toolkit wrote, when the host has no other
#: way to say it.
DEFAULT_IMAGE_VIEW_HINT = "open the image file at image_path and look at it"

#: The size limit of the call in progress (set by call_json; a ContextVar so
#: concurrent calls with different limits cannot see each other's).
_CALL_LIMIT: ContextVar[Optional[int]] = ContextVar("planlens_call_limit",
                                                    default=None)


class ToolError(Exception):
    """A mistake the model can fix; its message is returned, not raised."""

    def __init__(self, message: str, hint: Optional[str] = None):
        super().__init__(message)
        self.hint = hint


def _default_resolver(source: str) -> Source:
    if os.path.isfile(source):
        return source
    raise ToolError(f"'{source}' is not a readable file path",
                    hint="pass the path of a PDF the host can read")


class _Entry:
    def __init__(self, handle: str, name: str, doc: Document,
                 path: Optional[str] = None):
        self.handle = handle
        self.name = name
        self.doc = doc
        #: The file this document was opened FROM, when it came from a file
        #: rather than from bytes — what a tool that WRITES must not write to.
        self.path = path
        self.lock = threading.RLock()


class ReviewToolkit:
    """LLM tools over planlens.document.

    Parameters
    ----------
    resolve_source : callable, optional
        Maps the ``source`` string the model passes to either PDF bytes or a
        readable file path. Hosts use it to resolve upload keys; the default
        accepts file paths only.
    max_chars : int
        Ceiling on every result :meth:`call_json` returns.
    max_open : int
        Documents kept open before the least recently used is closed.
    text_source_for : callable, optional
        ``(name, content_key) -> text_source or None``, to attach e.g. an
        :class:`~planlens.document.AzureLayout` when a document is opened.
    vision_hint : str, optional
        The host's instruction for how the model views a page or region —
        appended to every ``! look:`` line. Name the host's own vision tools
        here (for example a whole-page vision tool and a zoom tool that take
        the same ``source`` and displayed-frame box). Defaults to this
        toolkit's ``render_page`` / ``render_region``.
    output_dir : str, optional
        Where the render tools write PNG files, and where ``annotate_document``
        puts a marked-up copy whose ``output_path`` is relative. Defaults to a
        temporary directory created on first use.
    output_root : str, optional
        Confines every file a tool WRITES to one directory tree: a relative
        ``output_path`` resolves against it and anything escaping it is
        refused. A host that confines what may be read (the MCP server's
        ``--root``) sets this too, so the confinement covers both directions.
    image_view_hint : str, optional
        How the model views a PNG file the render tools wrote (a host with an
        image-analysis tool names it here, e.g. "analyze_image(path)").
    author : str, optional
        Who ``annotate_document`` attributes a comment to when the call names
        nobody — the app's own AI identity, so a reader can always tell a
        drafted comment from a person's.
    """

    def __init__(self, resolve_source: Optional[Callable[[str], Source]] = None,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 max_open: int = DEFAULT_MAX_OPEN,
                 text_source_for: Optional[Callable[[str, str], Any]] = None,
                 vision_hint: Optional[str] = None,
                 output_dir: Optional[str] = None,
                 image_view_hint: Optional[str] = None,
                 output_root: Optional[str] = None,
                 author: Optional[str] = None):
        if max_chars < 1000:
            raise ValueError("max_chars below 1000 cannot hold a useful result")
        self.resolve_source = resolve_source or _default_resolver
        self.max_chars = int(max_chars)
        self.max_open = int(max_open)
        self.text_source_for = text_source_for
        self.vision_hint = (vision_hint if vision_hint is not None
                            else DEFAULT_VISION_HINT)
        self._output_dir = output_dir
        self._output_root = output_root
        self.author = author
        self.image_view_hint = (image_view_hint if image_view_hint is not None
                                else DEFAULT_IMAGE_VIEW_HINT)
        self._entries: "OrderedDict[str, _Entry]" = OrderedDict()
        self._by_key: Dict[str, str] = {}
        self._guard = threading.Lock()

    # -- publication ----------------------------------------------------------
    def specs(self, style: str = "anthropic") -> List[Dict[str, Any]]:
        """Tool definitions: ``anthropic`` (input_schema), ``openai``
        (type=function) or ``plain`` (name/description/parameters)."""
        from planlens.tools.specs import TOOL_SPECS
        out = []
        for spec in TOOL_SPECS:
            if style == "anthropic":
                out.append({"name": spec["name"],
                            "description": spec["description"],
                            "input_schema": spec["parameters"]})
            elif style == "openai":
                out.append({"type": "function", "function": dict(spec)})
            elif style == "plain":
                out.append(dict(spec))
            else:
                raise ValueError("style must be anthropic, openai or plain")
        return out

    @property
    def tool_names(self) -> List[str]:
        from planlens.tools.specs import TOOL_SPECS
        return [s["name"] for s in TOOL_SPECS]

    # -- dispatch ---------------------------------------------------------------
    def call(self, name: str, arguments: Optional[Dict[str, Any]] = None
             ) -> Dict[str, Any]:
        """Run one tool. Mistakes come back as ``{"error", "hint"}``."""
        arguments = dict(arguments or {})
        handler = getattr(self, f"_tool_{name}", None)
        if handler is None or name not in self.tool_names:
            return {"error": f"unknown tool '{name}'",
                    "hint": f"available tools: {self.tool_names}"}
        try:
            inspect.signature(handler).bind(**arguments)
        except TypeError as exc:
            return {"error": f"bad arguments for {name}: {exc}",
                    "hint": "check parameter names against the tool schema"}
        try:
            return handler(**arguments)
        except ToolError as exc:
            out = {"error": str(exc)}
            if exc.hint:
                out["hint"] = exc.hint
            return out
        except (IndexError, ValueError) as exc:   # page specs, int parsing
            return {"error": str(exc)}

    def call_json(self, name: str, arguments: Optional[Dict[str, Any]] = None,
                  max_chars: Optional[int] = None) -> str:
        """:meth:`call`, serialized, never longer than the limit.

        ``max_chars`` overrides the toolkit's limit for this call — a host
        that gives text-reading tools a larger budget than the rest passes it
        here.
        """
        limit = int(max_chars) if max_chars is not None else self.max_chars
        if limit < 1000:
            raise ValueError("max_chars below 1000 cannot hold a useful result")
        token = _CALL_LIMIT.set(limit)
        try:
            text = json.dumps(self.call(name, arguments), ensure_ascii=False,
                              separators=(",", ":"))
        finally:
            _CALL_LIMIT.reset(token)
        if len(text) <= limit:
            return text
        # Every tool budgets its own payload; reaching here is a defect, and
        # the model must get valid JSON saying what to do rather than a cut.
        return json.dumps({
            "error": f"{name} produced {len(text)} characters, over the "
                     f"{limit} limit",
            "hint": "narrow the request (fewer pages, a page range, a "
                    "pattern) and retry"})

    # -- documents --------------------------------------------------------------
    def _content_key(self, resolved: Source) -> str:
        if isinstance(resolved, (bytes, bytearray)):
            return "sha1:" + hashlib.sha1(resolved).hexdigest()
        st = os.stat(resolved)
        return f"path:{os.path.abspath(resolved)}:{st.st_mtime_ns}:{st.st_size}"

    def open(self, source: str) -> _Entry:
        resolved = self.resolve_source(source)
        key = self._content_key(resolved)
        with self._guard:
            handle = self._by_key.get(key)
            if handle and handle in self._entries:
                self._entries.move_to_end(handle)
                return self._entries[handle]
            name = os.path.basename(source) or source
            text_source = (self.text_source_for(name, key)
                           if self.text_source_for else None)
            try:
                if isinstance(resolved, (bytes, bytearray)):
                    doc = Document(content=bytes(resolved), name=name,
                                   text_source=text_source)
                else:
                    doc = Document(filepath=resolved, name=name,
                                   text_source=text_source)
            except Exception as exc:
                raise ToolError(f"could not open '{source}' as a PDF or "
                                f"image: {type(exc).__name__}: {exc}")
            handle = "doc_" + hashlib.sha1(key.encode()).hexdigest()[:10]
            entry = _Entry(handle, name, doc,
                           path=(None if isinstance(resolved, (bytes, bytearray))
                                 else str(resolved)))
            entry.source = source
            self._entries[handle] = entry
            self._by_key[key] = handle
            while len(self._entries) > self.max_open:
                _, old = self._entries.popitem(last=False)
                self._by_key = {k: h for k, h in self._by_key.items()
                                if h != old.handle}
                with old.lock:
                    old.doc.close()
            return entry

    def _entry(self, handle: str) -> _Entry:
        with self._guard:
            entry = self._entries.get(handle)
            if entry is None:
                raise ToolError(
                    f"unknown document handle '{handle}'",
                    hint=("call open_document first; open handles: "
                          f"{list(self._entries) or 'none'}"))
            self._entries.move_to_end(handle)
            return entry

    def document_name(self, handle: str) -> Optional[str]:
        """The file name a handle was opened under, or None if it is not open.

        A host that names an OUTPUT file after the document it is marking up
        has nothing else to get it from: a handle is a hash of the content.
        """
        with self._guard:
            entry = self._entries.get(handle)
            return entry.name if entry is not None else None

    def close(self) -> None:
        with self._guard:
            for entry in self._entries.values():
                with entry.lock:
                    entry.doc.close()
            self._entries.clear()
            self._by_key.clear()

    # -- rendering (for hosts that hand images to the model themselves) ---------
    def render(self, handle: str, page: int, bbox=None, dpi=None,
               marks=None, pad_frac: float = 0.1):
        """PNG bytes + info for a page or region (see :meth:`Document.render`)."""
        entry = self._entry(handle)
        with entry.lock:
            return entry.doc.render(page, bbox=bbox, dpi=dpi, marks=marks,
                                    pad_frac=pad_frac)

    def _resolve_output(self, output_path: str) -> str:
        """Where a tool that WRITES a document file may put it.

        A relative path goes into :attr:`output_dir`, beside the PNGs the
        render tools write; an absolute path is honoured, because a host that
        names one has already decided where its files belong (the app resolves
        the model's bare filename into the conversation's folder before the
        call reaches here). ``output_root`` overrides both and confines every
        write to one tree.
        """
        path = os.path.expanduser(str(output_path or "").strip())
        if not path:
            raise ToolError("output_path is required",
                            hint="name the file to write, e.g. "
                                 "'<document>_marked.pdf'")
        if self._output_root is not None:
            base = os.path.realpath(self._output_root)
            full = os.path.realpath(os.path.join(base, path))
            if full != base and not full.startswith(base + os.sep):
                raise ToolError(
                    f"'{output_path}' is outside the directory this server may "
                    f"write to", hint=f"pass a path inside {base}")
            return full
        if os.path.isabs(path):
            return path
        if self._output_dir is None:
            self._output_dir = tempfile.mkdtemp(prefix="planlens_")
        os.makedirs(self._output_dir, exist_ok=True)
        return os.path.join(self._output_dir, path)

    def _write_png(self, png: bytes, handle: str, info: Dict[str, Any]) -> str:
        if self._output_dir is None:
            self._output_dir = tempfile.mkdtemp(prefix="planlens_")
        os.makedirs(self._output_dir, exist_ok=True)
        stem = f"{handle}_p{info['page']}_" + "_".join(
            str(int(round(v))) for v in info["clip"])
        path = os.path.join(self._output_dir, stem + ".png")
        with open(path, "wb") as fh:
            fh.write(png)
        return path

    # -- tools --------------------------------------------------------------------
    def _budget(self, reserve: int = 600) -> int:
        limit = _CALL_LIMIT.get()
        return (limit if limit is not None else self.max_chars) - reserve

    def _look(self, statements) -> List[str]:
        """Advice statements with the host's how-to-view instruction."""
        out = list(statements)
        if out and self.vision_hint:
            out[-1] = out[-1] + " — " + self.vision_hint
        return out

    def _tool_open_document(self, source: str) -> Dict[str, Any]:
        entry = self.open(source)
        doc = entry.doc
        with entry.lock:
            summaries = doc.page_map()
            markups = doc.markups()
            toc = doc.toc()
            meta = doc.metadata
        by_kind: Dict[str, List[int]] = {}
        for s in summaries:
            by_kind.setdefault(s.kind, []).append(s.page)
        authors: Dict[str, int] = {}
        for m in markups:
            authors[m.author or "(no author)"] = authors.get(
                m.author or "(no author)", 0) + 1
        out: Dict[str, Any] = {
            "handle": entry.handle,
            # The name the CALLER used this time, not the one the cached
            # document was first opened under: a host resolves it against
            # this conversation's uploads, and the same bytes can arrive under
            # different names in different conversations.
            "source": source,
            "name": entry.name,
            "kind": doc.source_kind,
            "n_pages": doc.n_pages,
            "pages_by_kind": {k: compact_ranges(v) for k, v in by_kind.items()},
        }
        # This dict IS the JSON the host receives, so measure everything
        # against the whole limit rather than reserving a flat guess for the
        # parts still to come: the look line below carries the HOST's own
        # vision instruction and can be any length, and the row blocks are as
        # long as the document's segments, bookmarks and sheet labels happen
        # to be. A flat reserve is how this result quietly grew past the limit
        # and came back to the model as "over the limit" with no handle in it.
        ceiling = self._budget(0)
        if json_len(out) > ceiling:
            # Not even the map fits. The handle is the one thing the caller
            # cannot continue without, so keep it and name the tools that
            # report the rest in pages.
            return {"handle": entry.handle, "n_pages": doc.n_pages,
                    "truncated": ("the document map does not fit this size "
                                  "limit; call document_page_map and "
                                  "document_structure for it")}

        def add(key: str, value: Any) -> None:
            # Optional facts, in priority order: each is kept only if the
            # result still fits, so a tight limit trims the tail, never the
            # handle and the map.
            if value in (None, "", [], {}):
                return
            trial = dict(out)
            trial[key] = value
            if json_len(trial) <= ceiling:
                out[key] = value

        def add_rows(key: str, items: List[Any], room: int,
                     to_payload: Callable[[Any], Any] = lambda x: x,
                     more: Optional[Callable[[int], str]] = None) -> None:
            """Rows under ``key`` — only rows that fit, and the count dropped.

            ``fit_items`` returns one payload even when it is larger than the
            budget, so a caller paging through a cursor can never stall. There
            is no cursor here (``next`` names the tool that pages), so an
            oversized row must be dropped rather than sent past the host's
            limit. Each candidate list is measured as the whole result,
            truncation note included, and shortened until it fits.
            """
            if not items:
                return
            rows, _ = fit_items(items, max(0, room), to_payload=to_payload)
            while rows:
                trial = dict(out)
                trial[key] = rows
                if len(rows) < len(items) and more is not None:
                    trial[key + "_truncated"] = more(len(items) - len(rows))
                if json_len(trial) <= ceiling:
                    out.update(trial)
                    return
                rows = rows[:-1]

        view = [s.page for s in summaries if s.kind in VIEW_KINDS]
        add("pages_to_view", compact_ranges(view))
        if markups:
            add("markups", {"n": len(markups),
                            "pages": compact_ranges(m.page for m in markups),
                            "by_author": authors})
        dups = [s.page for s in summaries if s.duplicate_of is not None]
        add("duplicate_pages", compact_ranges(dups))
        # Two different failures, kept apart because the answer differs: one
        # page has nothing to read, the other has something that is not what
        # the page says. Both are offered to OCR; only the second can mislead
        # a model that reads it without looking.
        add("pages_without_text_layer", compact_ranges(
            s.page for s in summaries
            if s.evidence.get("needs_ocr") and s.text_reliable))
        add("pages_with_unreliable_text", compact_ranges(
            s.page for s in summaries if not s.text_reliable))
        add("pages_with_hidden_cad_text", compact_ranges(
            s.page for s in summaries if s.n_cad_text))
        add("coordinates", "PDF points, displayed page frame: top-left "
                           "origin, y down; pages are 0-based")
        add("next", "document_structure for the constituent documents and "
                    "their printed page numbers; document_page_map for "
                    "per-page detail; search_document to find a topic; "
                    "read_document to read pages; document_markups for the "
                    "review record; render_page_thumbnails to see the whole "
                    "document at a glance")
        if view:
            add("pages_to_view_note", self._look([
                "these pages are scans, figures or drawing sheets: their "
                "text is labels at best, so read them by viewing them"])[0])
        add("metadata", {k: meta[k] for k in
                         ("title", "author", "subject", "creator", "producer")
                         if meta.get(k)})
        with entry.lock:
            segs = doc.segments()
        if segs:
            brief = [{"id": g["id"], "pages": g["pages"], "title": g["title"]}
                     for g in segs]
            # Half the room left, so bookmarks and sheet labels below still
            # have some; add_rows enforces the ceiling either way.
            add_rows("segments", brief, (ceiling - json_len(out)) // 2,
                     more=lambda n: f"{n} more; see document_structure")
        if toc:
            add_rows("toc", list(toc), (ceiling - json_len(out)) // 2,
                     more=lambda n: f"{n} more entries")
        sheets = [s for s in summaries if s.kind == "drawing_sheet" and s.label]
        if sheets:
            add_rows("drawing_sheets", sheets, ceiling - json_len(out) - 60,
                     to_payload=lambda s: {"page": s.page, "label": s.label},
                     more=lambda n: f"{n} more; see document_page_map")
        return out

    def _tool_document_page_map(self, handle: str, pages: Any = None,
                                kind: Optional[str] = None,
                                with_evidence: bool = False) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            wanted = parse_pages(pages, entry.doc.n_pages)
            summaries = entry.doc.page_map(wanted)
        if kind:
            summaries = [s for s in summaries if s.kind == kind]

        def row(s):
            d = s.to_dict(detail=with_evidence)
            if not with_evidence and s.evidence.get("needs_ocr"):
                d["needs_ocr"] = True
            if s.kind in VIEW_KINDS:
                d["look"] = True
            return d

        rows, nxt = fit_items(summaries, self._budget(300), to_payload=row)
        out: Dict[str, Any] = {"handle": handle, "rows": rows}
        if any(r.get("look") for r in rows):
            out["look"] = self._look(["pages marked look are scans, figures "
                                      "or drawing sheets: view them"])[0]
        if nxt is not None:
            out["next_pages"] = compact_ranges(s.page for s in summaries[nxt:])
        return out

    def _tool_read_document(self, handle: str, pages: Any = None,
                            start_line: int = 0, with_locations: bool = False,
                            include_tables: bool = True,
                            include_markups: bool = True) -> Dict[str, Any]:
        entry = self._entry(handle)
        budget = self._budget()
        out_rows: List[str] = []
        used = 0
        content_rows = 0         # rows other than page headers/markers
        returned: List[int] = []
        cursor: Optional[Dict[str, Any]] = None
        with entry.lock:
            wanted = parse_pages(pages, entry.doc.n_pages)
            for n, index in enumerate(wanted):
                pc = entry.doc.page(index, tables=include_tables)
                summary = entry.doc.summary(index)
                rows = render_page(pc, summary,
                                   include_tables=include_tables,
                                   include_markups=include_markups,
                                   with_locations=with_locations,
                                   advice=self._look(page_advice(summary, pc)))
                first = start_line if n == 0 else 0
                if first >= len(rows):
                    raise ToolError(
                        f"start_line {first} is past the end of page {index} "
                        f"({len(rows)} lines)")
                if first:
                    out_rows.append(f"=== page {index} continued from line "
                                    f"{first} ===")
                for i in range(first, len(rows)):
                    row = rows[i]
                    # Serialized size: quotes and escapes count, and the
                    # joining newline becomes the two characters "\n".
                    size = json_len(row)
                    if used + size > budget:
                        if content_rows == 0 and i > 0:
                            # Nothing but a page header or continuation
                            # marker is out yet, so this line cannot fit in
                            # ANY call: cut it to the room left and mark the
                            # cut, instead of handing back a cursor that
                            # points at the same line forever.
                            room = budget - used
                            keep = max(0, room - 60)
                            while True:
                                row = (rows[i][:keep] + " ...[line cut: "
                                       "longer than the limit]")
                                if json_len(row) <= room or keep == 0:
                                    break
                                keep = int(keep * 0.9)
                            size = json_len(row)
                        elif out_rows:
                            cursor = {"pages": compact_ranges(wanted[n:]),
                                      "start_line": i}
                            break
                    out_rows.append(row)
                    used += size
                    if i > 0:
                        content_rows += 1
                if cursor:
                    if i > first:
                        returned.append(index)
                    break
                returned.append(index)
        out: Dict[str, Any] = {"handle": handle,
                               "pages_returned": compact_ranges(returned),
                               "text": "\n".join(out_rows)}
        if cursor:
            out["next"] = cursor
            out["note"] = ("output reached the size limit; call read_document "
                           "again with the 'next' pages and start_line")
        return out

    def _tool_search_document(self, handle: str, pattern: str,
                              pages: Any = None, regex: bool = False,
                              case_sensitive: bool = False,
                              include_markups: bool = True,
                              max_hits: int = 100, fuzzy: bool = False,
                              min_score: int = DEFAULT_FUZZY_MIN_SCORE
                              ) -> Dict[str, Any]:
        if not pattern:
            raise ToolError("pattern is empty")
        entry = self._entry(handle)
        try:
            with entry.lock:
                res = entry.doc.search(pattern, pages=pages, regex=regex,
                                       case_sensitive=case_sensitive,
                                       include_markups=include_markups,
                                       max_hits=max(1, min(int(max_hits), 500)),
                                       fuzzy=bool(fuzzy),
                                       min_score=int(min_score))
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}",
                            hint="pass regex=false for a literal search")
        except ImportError as exc:
            raise ToolError(str(exc),
                            hint="retry without fuzzy for an exact search")
        hits, nxt = fit_items(res["hits"], self._budget(500))
        out = {"handle": handle, "pattern": pattern, "n_hits": res["n_hits"],
               "pages_with_hits": {str(k): v for k, v in
                                   res["pages_with_hits"].items()},
               "hits": hits}
        if res.get("fuzzy"):
            out["fuzzy"] = True
            out["min_score"] = res["min_score"]
            out["note"] = ("approximate matches, best score first; a hit's "
                           "score is 0-100 and its source says which "
                           "extractor read the text")
        # Text search cannot see into scans, figures or sheets: say which
        # pages in the searched range it could not read, so a miss there is
        # not taken as absence.
        with entry.lock:
            unread = [s.page for s in entry.doc.page_map(pages)
                      if s.kind in VIEW_KINDS]
        if unread:
            out["pages_not_searchable_as_text"] = compact_ranges(unread)
            if not hits:
                out["hint"] = self._look([
                    "no text hit; the pages listed are scans, figures or "
                    "drawing sheets whose content is not in the text — the "
                    "term may be on one of them"])[0]
        # An exact miss has two explanations, and the model should hear both:
        # the text is not there, or the text is there with a letter wrong.
        # Only offered when rapidfuzz is actually installed — advice that
        # would raise is worse than none.
        if not fuzzy and not hits and fuzzy_search_available():
            tip = ("retry with fuzzy=true if the term may be spelled "
                   "differently or was read with a wrong letter (optical or "
                   "stroke-plotted text); for a word under 8 letters also "
                   "pass min_score=75")
            out["hint"] = (out["hint"] + " — " + tip if out.get("hint")
                           else tip)
        if res["truncated"]:
            out["max_hits_reached"] = True
        if nxt is not None:
            out["hits_omitted_for_size"] = len(res["hits"]) - nxt
            out["hint"] = ("narrow with pages=... (see pages_with_hits) to "
                           "see the rest")
        return out

    def _tool_find_quantities(self, handle: str, pages: Any = None,
                              kinds: Any = None, units: Any = None,
                              include_markups: bool = True,
                              offset: int = 0) -> Dict[str, Any]:
        entry = self._entry(handle)
        kind_list = [kinds] if isinstance(kinds, str) else kinds
        unit_list = [units] if isinstance(units, str) else units
        bad = [k for k in (kind_list or []) if k not in QUANTITY_KINDS]
        if bad:
            raise ToolError(f"unknown quantity kind(s) {bad}",
                            hint=f"kinds are: {list(QUANTITY_KINDS)}")
        with entry.lock:
            mentions = entry.doc.quantities(
                pages=pages, kinds=kind_list, units=unit_list,
                include_markups=include_markups)
        if offset < 0 or (mentions and offset >= len(mentions)):
            raise ToolError(f"offset {offset} is outside 0-{len(mentions) - 1}")
        # A quantity printed inside a drawing, a scan or a figure is ink, not
        # text, so this tool cannot see it — the same caveat search carries.
        with entry.lock:
            unread = [s.page for s in entry.doc.page_map(pages)
                      if s.kind in VIEW_KINDS]
        trailer: Dict[str, Any] = {
            "note": ("no stated quantity on these pages; a number without a "
                     "unit is not reported") if not mentions else
                    ("each row is: page, value (and 'to' value for a range), "
                     "kind, qualifier, the raw wording, and where it is; "
                     "units are as the page wrote them and nothing was "
                     "converted")}
        if unread:
            trailer["pages_not_searchable_as_text"] = compact_ranges(unread)
            trailer["look"] = self._look([
                "the pages listed are scans, figures or drawing sheets: the "
                "numbers printed on them are ink, not text, so a value "
                "missing here may still be on one of them"])[0]
        # Measure the fixed parts instead of reserving a flat guess for them:
        # the look line carries the HOST's own vision instruction and can be
        # any length, so a constant reserve is how a result quietly grows past
        # the limit the host set.
        head = {"handle": handle, "n_mentions": len(mentions)}
        fixed = json_len(head) + json_len(trailer) + 40
        room = self._budget(0) - fixed
        if room < 200 and "note" in trailer:
            del trailer["note"]             # the rows matter more than the gloss
            room = self._budget(0) - json_len(head) - json_len(trailer) - 40
        rows, nxt = fit_items(mentions, max(0, room),
                              to_payload=render_quantity, start=offset)
        out: Dict[str, Any] = dict(head)
        out["quantities"] = rows
        if nxt is not None:
            out["next_offset"] = nxt
        out.update(trailer)
        return out

    def _tool_document_markups(self, handle: str, pages: Any = None,
                               author: Optional[str] = None,
                               offset: int = 0) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            markups = entry.doc.markups(pages=pages, author=author)
            labels = {m.page: entry.doc.label(m.page) for m in markups}
        if offset < 0 or (markups and offset >= len(markups)):
            raise ToolError(f"offset {offset} is outside 0-{len(markups) - 1}")

        def row(m):
            lab = labels.get(m.page)
            return f"p{m.page}" + (f" ({lab})" if lab else "") + " " + \
                render_markup(m)

        rows, nxt = fit_items(markups, self._budget(300), to_payload=row,
                              start=offset)
        out: Dict[str, Any] = {"handle": handle, "n_markups": len(markups),
                               "markups": rows}
        if nxt is not None:
            out["next_offset"] = nxt
        if not markups:
            out["note"] = "no review markups on these pages"
        elif any(m.points_at is not None for m in markups):
            out["look"] = self._look([
                "a markup's 'points at' / 'box' is where to view to see what "
                "is being commented on"])[0]
        return out

    def _tool_annotate_document(self, handle: str, output_path: str,
                                markups: Any = None,
                                author: Optional[str] = None,
                                append: bool = True) -> Dict[str, Any]:
        from planlens.document.markup_writer import DEFAULT_AUTHOR, write_markups

        entry = self._entry(handle)
        if isinstance(markups, dict):
            markups = [markups]
        if not markups:
            raise ToolError(
                "markups is empty: nothing would be written",
                hint="each markup is {kind, page, comment} plus ONE anchor: "
                     "quote, bbox, point or reply_to")
        out_path = self._resolve_output(output_path)
        if entry.path and os.path.abspath(entry.path) == os.path.abspath(out_path):
            raise ToolError(
                "output_path is the document itself; a marked-up copy is a "
                "NEW file", hint="use a name like '<document>_marked.pdf'")
        try:
            with entry.lock:
                # The bytes come off the OPEN document rather than from
                # resolving its source again: one document can be reached by
                # several names, and the one it was first opened under may not
                # even resolve for this caller.
                report = write_markups(
                    entry.doc.tobytes(), out_path, markups,
                    author=author or self.author or DEFAULT_AUTHOR,
                    append=bool(append))
        except ValueError as exc:
            raise ToolError(str(exc),
                            hint="fix that markup and call again; the ones "
                                 "before it were not written either")
        out: Dict[str, Any] = {
            "handle": handle,
            "output_path": report.output,
            "author": report.author,
            "appended_to_existing": report.appended,
            "n_written": report.n_written,
            "n_skipped": report.n_skipped,
            "note": ("a NEW file: the document you opened is unchanged. Each "
                     "written row gives the box the reader will see (a "
                     "highlight's and a callout's are wider than what was "
                     "asked for — the leader and the appearance margin are "
                     "inside them). READ the skipped rows: those comments are "
                     "NOT on the page. open_document on output_path to check "
                     "the result"),
        }
        ceiling = self._budget(0)
        rows, nxt = fit_items([w.to_dict() for w in report.written],
                              max((ceiling - json_len(out)) // 2, 400))
        out["written"] = rows
        if nxt is not None:
            out["written_truncated_after"] = nxt
        if report.skipped:
            rows, nxt = fit_items(report.skipped,
                                  max(ceiling - json_len(out) - 40, 300))
            out["skipped"] = rows
            if nxt is not None:
                out["skipped_truncated_after"] = nxt
        return out

    def _tool_document_structure(self, handle: str,
                                 offset: int = 0) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            segs = entry.doc.segments()
        if offset < 0 or (segs and offset >= len(segs)):
            raise ToolError(f"offset {offset} is outside 0-{len(segs) - 1}")
        rows, nxt = fit_items(segs, self._budget(400), start=offset)
        out: Dict[str, Any] = {
            "handle": handle, "n_segments": len(segs), "segments": rows,
            "note": ("segments are runs of pages sharing a running header/"
                     "footer and printed numbering, split at dividers, page-"
                     "size changes, drawing sheets and where printed numbering "
                     "restarts; printed_pages are the numbers printed ON the "
                     "pages (cite those to the reader) and 'pages' are the "
                     "0-based PDF pages these tools take")}
        if nxt is not None:
            out["next_offset"] = nxt
        return out

    def _tool_document_roles(self, handle: str, items_only: bool = False,
                             outline: bool = False, ledger: bool = False,
                             offset: int = 0) -> Dict[str, Any]:
        from planlens.document.roles import (
            SOURCE_AZURE_DI, assign, build_items, facts_from_document,
            ledger_from, outline_from,
        )

        entry = self._entry(handle)
        with entry.lock:
            facts = facts_from_document(entry.doc)
            roles = assign(facts)
            items = build_items(facts, roles)
            outline_dict = None
            ledger_lines = None
            if outline:
                def lines_of(index: int):
                    return [ln.text
                            for ln in entry.doc.page(index, tables=False).lines
                            if (ln.text or "").strip()]

                outline_dict = outline_from(facts, roles, lines_of).to_dict()
            if ledger:
                di = [s.page for s in entry.doc.page_map()
                      if s.evidence.get("text_source") == SOURCE_AZURE_DI]
                ledger_lines = ledger_from(facts, roles, di)
        counts: Dict[str, int] = {}
        for r in roles:
            counts[r.role] = counts.get(r.role, 0) + 1
        item_rows = [i.to_dict() for i in items]
        out: Dict[str, Any] = {
            "handle": handle, "n_pages": len(roles),
            "roles": dict(sorted(counts.items(), key=lambda kv: -kv[1])),
            "note": ("a role per page and the work items those pages make: "
                     "one item per boring / test pit / CPT / DCP log with its "
                     "continuation sheets folded in, one per laboratory sheet "
                     "or multi-page test, one per calculation printout, one "
                     "for the narrative, one per report bound inside this "
                     "one. Read an item's pages together. Pages are 0-based; "
                     "roles read from the pages' own titles, their appendix "
                     "tabs and their shape, with the evidence on each row")}
        out["n_items"] = len(item_rows)

        if outline_dict is not None:
            # The outline is the report's own account of itself and is what a
            # reviewer reads first, so it is fitted before anything else and
            # its entries are paged rather than dropped.
            out["outline"] = {k: v for k, v in outline_dict.items()
                              if k in ("n_entries", "n_entries_placed")}
            room = self._budget(200) - json_len(out)
            for part in ("entries", "dividers", "captions", "headings"):
                rows, nxt = fit_items(outline_dict[part], max(room // 4, 400))
                out["outline"][part] = rows
                if nxt is not None:
                    out["outline"][f"{part}_truncated_after"] = nxt

        if ledger_lines is not None:
            out["ledger_note"] = ("one line per page: page, kind, role, "
                                  "confidence, [the rule that fired], "
                                  "heading, running header, printed page, "
                                  "segment, text characters, whether the "
                                  "text layer is reliable, whether Azure "
                                  "Document Intelligence supplied it, and "
                                  "the work item")
            out["ledger"] = []
            room = self._budget(200) - json_len(out)
            rows, nxt = fit_items(ledger_lines, max(room, 500), start=offset)
            out["ledger"] = rows
            if nxt is not None:
                out["next_offset"] = nxt
                out["hint"] = "call again with offset for the rest of the pages"
            return out

        if items_only:
            out["items"] = []
            budget = self._budget(150) - json_len(out)
            rows, nxt = fit_items(item_rows, max(budget, 500), start=offset)
            out["items"] = rows
            if nxt is not None:
                out["next_offset"] = nxt
            return out
        payload = [r.to_dict() for r in roles]
        out["items"] = []
        out["pages"] = []
        item_budget = self._budget(150) - json_len(out)
        out["items"], item_next = fit_items(item_rows,
                                            max(item_budget // 2, 500))
        if item_next is not None:
            out["hint"] = ("more work items than fit: call again with "
                           "items_only=true and offset to page through them")
        # What is left once the items and the wrapper are counted: the items
        # are the answer a reader acts on, so they are never the part cut.
        budget = self._budget(200) - json_len(out)
        rows, nxt = fit_items(payload, max(budget, 500), start=offset)
        out["pages"] = rows
        if nxt is not None:
            out["next_pages_offset"] = nxt
            out.setdefault("hint", "call again with offset for more pages")
        return out

    def _tool_log_grid(self, handle: str, pages: Any = None,
                       rows: bool = True, offset: int = 0) -> Dict[str, Any]:
        from planlens.document.loggrid import log_grid

        entry = self._entry(handle)
        with entry.lock:
            grid = log_grid(entry.doc, pages)
        payload = grid.to_dict(rows=False)
        out: Dict[str, Any] = {
            "handle": handle,
            "pages": payload["pages"],
            "depth_unit": payload["depth_unit"],
            "n_columns": payload["n_columns"],
            "n_layers": len(payload["layers"]),
            "n_rows": payload["n_rows"],
            "note": ("columns and depths read off the page's own ruling "
                     "lines, column headers and depth ruler. Values are as "
                     "printed and not parsed: a blow record stays "
                     "\"5-9-12\". Depths are in depth_unit. Anything the "
                     "geometry could not settle is in warnings"),
        }
        if grid.warnings:
            out["warnings"] = payload["warnings"]
            out["look"] = self._look([
                f"page {p} of this log" for p in grid.pages[:3]])
        if payload["fields"]:
            out["fields"] = payload["fields"]
        ceiling = self._budget(300)

        # The ruler on every sheet of one log is the same ruler shifted down
        # the hole, so on a long log the evidence is said once and not
        # twelve times.
        rulers = payload["rulers"]
        if len(rulers) > 3:
            rulers = [{k: v for k, v in r.items() if k != "evidence"}
                      for r in rulers]
        out["rulers"], nxt = fit_items(rulers,
                                       max(ceiling // 6 - json_len(out), 300))
        if nxt is not None:
            out["rulers_truncated_after"] = nxt

        room = ceiling - json_len(out)
        out["columns"], nxt = fit_items(payload["columns"],
                                        max(room // 3, 400))
        if nxt is not None:
            out["columns_truncated_after"] = nxt

        room = ceiling - json_len(out) - (600 if rows else 0)
        out["layers"], nxt = fit_items(payload["layers"], max(room, 400))
        if nxt is not None:
            out["layers_truncated_after"] = nxt
            out["hint"] = ("more layers than fit: call again with a shorter "
                           "page range and rows=false to read the rest")
        if not rows:
            return out

        room = ceiling - json_len(out)
        placed, nxt = fit_items([c.to_dict() for c in grid.rows],
                                max(room, 500), start=offset)
        out["rows"] = placed
        if nxt is not None:
            out["next_offset"] = nxt
            out.setdefault(
                "hint",
                "call again with offset for the rest of the rows, or with "
                "rows=false for the columns, the ruler, the layers and the "
                "fields alone")
        return out

    def _tool_render_page_thumbnails(self, handle: str, pages: Any = None,
                                     columns: int = 6) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            sheets = entry.doc.render_thumbnails(pages, columns=columns)
        out_sheets = []
        for n, (png, info) in enumerate(sheets):
            pages_txt = compact_ranges(info["pages"])
            first, last = info["pages"][0], info["pages"][-1]
            if self._output_dir is None:
                self._output_dir = tempfile.mkdtemp(prefix="planlens_")
            os.makedirs(self._output_dir, exist_ok=True)
            path = os.path.join(self._output_dir,
                                f"{handle}_thumbs_{first}-{last}.png")
            with open(path, "wb") as fh:
                fh.write(png)
            out_sheets.append({"image_path": path, "pages": pages_txt,
                               "n_pages": len(info["pages"]),
                               "width_px": info["width_px"],
                               "height_px": info["height_px"]})
        return {"handle": handle, "sheets": out_sheets,
                "legend": sheets[0][1]["legend"] if sheets else "",
                "note": f"reading order is left to right, top to bottom; "
                        f"{self.image_view_hint}"}

    def _tool_render_page(self, handle: str, page: int,
                          dpi: Optional[float] = None) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            png, info = entry.doc.render(page, dpi=dpi)
        path = self._write_png(png, handle, info)
        info.update({"handle": handle, "image_path": path,
                     "note": f"{self.image_view_hint}; the image is the "
                             f"displayed page, so boxes from read_document / "
                             f"markups map onto it directly"})
        return info

    def _tool_render_region(self, handle: str, page: int, bbox: Any,
                            marks: Any = None, dpi: Optional[float] = None,
                            pad_frac: float = 0.1) -> Dict[str, Any]:
        try:
            box = [float(v) for v in bbox]
            if len(box) != 4:
                raise ValueError
        except (TypeError, ValueError):
            raise ToolError("bbox must be [x0, y0, x1, y1] in PDF points "
                            "(displayed page, top-left origin)")
        mk = None
        if marks:
            try:
                mk = [(float(m[0]), float(m[1]),
                       str(m[2]) if len(m) > 2 else str(i + 1))
                      for i, m in enumerate(marks)]
            except (TypeError, ValueError, IndexError):
                raise ToolError("marks must be [[x, y, label], ...]")
        entry = self._entry(handle)
        with entry.lock:
            png, info = entry.doc.render(page, bbox=box, dpi=dpi, marks=mk,
                                         pad_frac=pad_frac)
        path = self._write_png(png, handle, info)
        info.update({"handle": handle, "image_path": path, "bbox": box,
                     "note": self.image_view_hint})
        if mk:
            info["marks"] = [[x, y, lab] for x, y, lab in mk]
        return info
