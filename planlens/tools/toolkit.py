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
``search_document``    find text, hidden CAD text and markup comments
``document_markups``   the review record: every markup, with its threads
``render_page``        a page as a PNG image, for the model to look at
``render_region``      a zoomed region (with optional numbered marks) as a PNG

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

from planlens.document import Document, page_advice, parse_pages
from planlens.tools.formatting import (
    compact_ranges, fit_items, json_len, render_markup, render_page,
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
VIEW_KINDS = ("scanned", "figure", "drawing_sheet")

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
    vision_hint : str, optional
        The host's instruction for how the model views a page or region —
        appended to every ``! look:`` line. Name the host's own vision tools
        here (for example a whole-page vision tool and a zoom tool that take
        the same ``source`` and displayed-frame box). Defaults to this
        toolkit's ``render_page`` / ``render_region``.
    output_dir : str, optional
        Where the render tools write PNG files. Defaults to a temporary
        directory created on first use.
    """

    def __init__(self, resolve_source: Optional[Callable[[str], Source]] = None,
                 max_chars: int = DEFAULT_MAX_CHARS,
                 max_open: int = DEFAULT_MAX_OPEN,
                 text_source_for: Optional[Callable[[str, str], Any]] = None,
                 vision_hint: Optional[str] = None,
                 output_dir: Optional[str] = None):
        if max_chars < 1000:
            raise ValueError("max_chars below 1000 cannot hold a useful result")
        self.resolve_source = resolve_source or _default_resolver
        self.max_chars = int(max_chars)
        self.max_open = int(max_open)
        self.text_source_for = text_source_for
        self.vision_hint = (vision_hint if vision_hint is not None
                            else DEFAULT_VISION_HINT)
        self._output_dir = output_dir
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
            entry = _Entry(handle, name, doc)
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
        budget = self._budget(200)

        def add(key: str, value: Any) -> None:
            # Optional facts, in priority order: each is kept only if the
            # result still fits, so a tight limit trims the tail, never the
            # handle and the map.
            if value in (None, "", [], {}):
                return
            trial = dict(out)
            trial[key] = value
            if json_len(trial) <= budget:
                out[key] = value

        view = [s.page for s in summaries if s.kind in VIEW_KINDS]
        add("pages_to_view", compact_ranges(view))
        if markups:
            add("markups", {"n": len(markups),
                            "pages": compact_ranges(m.page for m in markups),
                            "by_author": authors})
        add("pages_without_text_layer", compact_ranges(
            s.page for s in summaries if s.evidence.get("needs_ocr")))
        add("pages_with_hidden_cad_text", compact_ranges(
            s.page for s in summaries if s.n_cad_text))
        add("coordinates", "PDF points, displayed page frame: top-left "
                           "origin, y down; pages are 0-based")
        add("next", "document_page_map for per-page detail; search_document "
                    "to find a topic; read_document to read pages; "
                    "document_markups for the review record")
        if view:
            add("pages_to_view_note", self._look([
                "these pages are scans, figures or drawing sheets: their "
                "text is labels at best, so read them by viewing them"])[0])
        add("metadata", {k: meta[k] for k in
                         ("title", "author", "subject", "creator", "producer")
                         if meta.get(k)})
        if toc:
            rows, nxt = fit_items(toc, max(0, (budget - json_len(out)) // 2))
            if rows:
                out["toc"] = rows
                if nxt is not None:
                    out["toc_truncated"] = f"{len(toc) - nxt} more entries"
        sheets = [s for s in summaries if s.kind == "drawing_sheet" and s.label]
        if sheets:
            rows, nxt = fit_items(
                sheets, max(0, budget - json_len(out) - 60),
                to_payload=lambda s: {"page": s.page, "label": s.label})
            if rows:
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
        hits, nxt = fit_items(res["hits"], self._budget(500))
        out = {"handle": handle, "pattern": pattern, "n_hits": res["n_hits"],
               "pages_with_hits": {str(k): v for k, v in
                                   res["pages_with_hits"].items()},
               "hits": hits}
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

    def _tool_render_page(self, handle: str, page: int,
                          dpi: Optional[float] = None) -> Dict[str, Any]:
        entry = self._entry(handle)
        with entry.lock:
            png, info = entry.doc.render(page, dpi=dpi)
        path = self._write_png(png, handle, info)
        info.update({"handle": handle, "image_path": path,
                     "note": "look at the image; its frame is the displayed "
                             "page, so boxes from read_document / markups "
                             "map onto it directly"})
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
        info.update({"handle": handle, "image_path": path, "bbox": box})
        if mk:
            info["marks"] = [[x, y, lab] for x, y, lab in mk]
        return info
