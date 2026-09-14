"""ReviewToolkit — planlens as LLM tools, independent of any agent framework.

A host (a chat app, an agent framework, a notebook) gives the model these tools
by publishing :meth:`ReviewToolkit.specs` and routing each tool call to
:meth:`ReviewToolkit.call_json`. Everything else — opening documents once,
remembering them by handle, keeping every result inside the host's size limit,
turning mistakes into instructions the model can act on — happens here.

Tools
-----
``open_document``      open a PDF; returns a handle and a map of the document
``document_page_map``  one row per page: kind, label, heading, counts
``read_document``      text (optionally with locations), tables and markups
``search_document``    find text, hidden CAD text and markup comments
``document_markups``   the review record: every markup, with its threads

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
import threading
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from planlens.document import Document, parse_pages
from planlens.tools.formatting import (
    compact_ranges, fit_items, json_len, render_markup, render_page,
)

Source = Union[bytes, str]

#: Default ceiling on every serialized result, below the reference host's
#: 8,000-character truncation with room for its wrapper.
DEFAULT_MAX_CHARS = 7500

#: Documents kept open at once; the least recently used is closed first.
DEFAULT_MAX_OPEN = 8

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
    def __init__(self, handle: str, name: str, doc: Document):
        self.handle = handle
        self.name = name
        self.doc = doc
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
    """

    def __init__(self, resolve_source: Optional[Callable[[str], Source]] = None,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 max_open: int = DEFAULT_MAX_OPEN,
                 text_source_for: Optional[Callable[[str, str], Any]] = None):
        if max_chars < 1000:
            raise ValueError("max_chars below 1000 cannot hold a useful result")
        self.resolve_source = resolve_source or _default_resolver
        self.max_chars = int(max_chars)
        self.max_open = int(max_open)
        self.text_source_for = text_source_for
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
                raise ToolError(f"could not open '{source}' as a PDF: "
                                f"{type(exc).__name__}: {exc}")
            handle = "doc_" + hashlib.sha1(key.encode()).hexdigest()[:10]
            entry = _Entry(handle, name, doc)
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

    def close(self) -> None:
        with self._guard:
            for entry in self._entries.values():
                with entry.lock:
                    entry.doc.close()
            self._entries.clear()
            self._by_key.clear()

    # -- tools --------------------------------------------------------------------
    def _budget(self, reserve: int = 600) -> int:
        limit = _CALL_LIMIT.get()
        return (limit if limit is not None else self.max_chars) - reserve

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
            "name": entry.name,
            "n_pages": doc.n_pages,
            "pages_by_kind": {k: compact_ranges(v) for k, v in by_kind.items()},
        }
        for k in ("title", "author", "subject", "creator", "producer"):
            if meta.get(k):
                out.setdefault("metadata", {})[k] = meta[k]
        if markups:
            out["markups"] = {
                "n": len(markups),
                "pages": compact_ranges(m.page for m in markups),
                "by_author": authors,
            }
        ocr = [s.page for s in summaries if s.evidence.get("needs_ocr")]
        if ocr:
            out["pages_without_text_layer"] = compact_ranges(ocr)
        cad = [s.page for s in summaries if s.n_cad_text]
        if cad:
            out["pages_with_hidden_cad_text"] = compact_ranges(cad)
        out["coordinates"] = ("PDF points, displayed page frame: top-left "
                              "origin, y down; pages are 0-based")
        out["next"] = ("document_page_map for per-page detail; "
                       "search_document to find a topic; read_document to "
                       "read pages; document_markups for the review record")
        # TOC and sheet labels last: they are the parts that can be long.
        remaining = self._budget() - json_len(out)
        if toc:
            rows, nxt = fit_items(toc, max(200, remaining // 2))
            out["toc"] = rows
            if nxt is not None:
                out["toc_truncated"] = f"{len(toc) - nxt} more entries"
        sheets = [s for s in summaries if s.kind == "drawing_sheet" and s.label]
        if sheets:
            remaining = self._budget() - json_len(out)
            rows, nxt = fit_items(
                sheets, max(200, remaining),
                to_payload=lambda s: {"page": s.page, "label": s.label})
            out["drawing_sheets"] = rows
            if nxt is not None:
                out["drawing_sheets_truncated"] = (
                    f"{len(sheets) - nxt} more; see document_page_map")
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
            d = s.to_dict()
            if not with_evidence:
                d.pop("evidence", None)
                if s.evidence.get("needs_ocr"):
                    d["needs_ocr"] = True
            return d

        rows, nxt = fit_items(summaries, self._budget(), to_payload=row)
        out: Dict[str, Any] = {"handle": handle, "rows": rows}
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
                rows = render_page(pc, entry.doc.summary(index),
                                   include_tables=include_tables,
                                   include_markups=include_markups,
                                   with_locations=with_locations)
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
                              max_hits: int = 100) -> Dict[str, Any]:
        if not pattern:
            raise ToolError("pattern is empty")
        entry = self._entry(handle)
        try:
            with entry.lock:
                res = entry.doc.search(pattern, pages=pages, regex=regex,
                                       case_sensitive=case_sensitive,
                                       include_markups=include_markups,
                                       max_hits=max(1, min(int(max_hits), 500)))
        except re.error as exc:
            raise ToolError(f"invalid regular expression: {exc}",
                            hint="pass regex=false for a literal search")
        hits, nxt = fit_items(res["hits"], self._budget(400))
        out = {"handle": handle, "pattern": pattern, "n_hits": res["n_hits"],
               "pages_with_hits": {str(k): v for k, v in
                                   res["pages_with_hits"].items()},
               "hits": hits}
        if res["truncated"]:
            out["max_hits_reached"] = True
        if nxt is not None:
            out["hits_omitted_for_size"] = len(res["hits"]) - nxt
            out["hint"] = ("narrow with pages=... (see pages_with_hits) to "
                           "see the rest")
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

        rows, nxt = fit_items(markups, self._budget(), to_payload=row,
                              start=offset)
        out: Dict[str, Any] = {"handle": handle, "n_markups": len(markups),
                               "markups": rows}
        if nxt is not None:
            out["next_offset"] = nxt
        if not markups:
            out["note"] = "no review markups on these pages"
        return out
