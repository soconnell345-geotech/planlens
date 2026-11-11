"""One toolkit per conversation, and the document tools as the host layer
serves them.

Moved from the GeotechStaffEngineer app's ``document_tools`` module
(BACKLOG PL-29 (a)). The app binds it with a :class:`~planlens.review.host.
ReviewHost` (its source resolver, its look, its working folder, its signer)
and keeps only the tool names, the hints that name its own vision tools and
the binding to its agent.

- a ``source`` resolves against the call's attachments first, then a
  handle this conversation opened, then the host's resolver
  (``ReviewHost.resolve_source``), which confines it;
- one toolkit PER CONVERSATION (keyed by the working folder the host bound
  for the turn, ``ReviewHost.working_folder``), so a document opened in one
  turn is still open, by handle, in the next, and a handle, the
  open-document limit and the "open handles" hint belong to the
  conversation that opened them. Idle conversations' toolkits are closed
  (``ReviewHost.max_live_toolkits``, ``ReviewHost.idle_close_s``); a handle
  they had reopens from its source the next time it is used;
- a handle and a source are interchangeable: a document tool given a file
  name or attachment key where it expects a handle opens it, and
  ``open_document`` given a handle reads the document it stands for;
- the page thumbnails go to the conversation's scratch folder, named by
  the host's ``display_path``, never by their server path;
- attachments reach the resolver through a context variable set for the
  duration of each call, so concurrent conversations resolve only their own
  uploads;
- pages: ``pdf_pages`` / ``pdf_page`` (1-based, as a viewer shows) are taken
  in place of ``pages`` / ``page``, a page out of range says the off-by-one,
  and every page index in a result gains the viewer's number beside it
  (:func:`with_viewer_pages`);
- a Word, Excel or DXF file is read as TEXT (:func:`_side_document`): a DXF
  as its text and entities (:func:`_read_cad`), Word and Excel through the
  host's ``read_office`` -- never through MuPDF;
- no document tool raises: an error comes back as JSON with no server path.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from typing import Any, Dict, Optional


def _host():
    from planlens.review.host import current
    return current()


#: Budget used when the host has disabled truncation: generous, still a bound.
UNCAPPED_BUDGET = 60000

#: Room left under the host's cap, so planlens' own limit is what binds and the
#: host's string truncation (which breaks JSON) never fires.
#: It also holds the viewer page numbers :func:`with_viewer_pages` adds (about
#: 14 characters a page reference; a 100-hit search needs ~1,400).
_CAP_MARGIN = 1500

_ATTACHMENTS: ContextVar[Optional[Dict[str, bytes]]] = ContextVar(
    "gse_document_attachments", default=None)

#: Conversations whose toolkits are kept open at once and the idle time
#: (seconds) after which one is closed are the host's
#: (``ReviewHost.max_live_toolkits``, ``ReviewHost.idle_close_s``).
#: Conversations remembered at all (handle -> source names; a few strings).
MAX_SPACES = 512
#: Handles a conversation remembers the source of.
MAX_REMEMBERED_HANDLES = 256


class _Space:
    """One conversation's documents: its toolkit (``None`` once closed for
    idleness) and the source each handle it opened came from."""

    def __init__(self, key: str, folder: Optional[str]):
        self.key = key
        self.folder = folder
        self.kit = None
        self.sources: "OrderedDict[str, str]" = OrderedDict()
        self.last_used = time.monotonic()
        self.busy = 0


_SPACES: "OrderedDict[str, _Space]" = OrderedDict()
_SPACES_LOCK = threading.Lock()

#: What a planlens document handle looks like (``doc_`` + a content hash).
_HANDLE = re.compile(r"^doc_[0-9a-f]{6,40}$")


def looks_like_handle(value: str) -> bool:
    """Whether ``value`` has the shape of a document handle."""
    return bool(_HANDLE.match(str(value or "").strip()))


def _space_key() -> tuple:
    """``(key, folder)`` of the conversation in this context: the working
    folder the host bound for the turn; ``("", None)`` for a library caller
    with none (one shared toolkit, as before)."""
    folder = _host().folder()
    if not folder:
        return "", None
    return os.path.normcase(os.path.realpath(folder)), folder


def _asked_for_sheet_tools(host) -> bool:
    """Whether ``host`` asked for the sheet tools
    (``toolkit_options={"sheet_tools": True}``)."""
    return bool((getattr(host, "toolkit_options", None) or {})
                .get("sheet_tools"))


#: The toolkit tools a host is served only when it asked for them
#: (:data:`planlens.review.specs.SHEET_TOOLS`).
_OPT_IN_TOOLS = ("sheet_index", "check_cross_references")


def _unknown_tool(name: str) -> str:
    """"unknown tool" for the calling host, naming the tools THAT host is
    offered: the conversation's toolkit serves more than some hosts are
    offered (the sheet tools), so its own list is never shown."""
    from planlens.review import specs as _specs
    host = _host()
    offered = list(_specs.host_tool_names(
        look=host.look is not None,
        sheet_tools=_asked_for_sheet_tools(host)))
    return json.dumps({"error": f"unknown tool '{name}'",
                       "hint": f"available tools: {offered}"})


def _refused_opt_in(name: str) -> Optional[str]:
    """The refusal of an opt-in tool the calling host did not ask for, or
    ``None``. A conversation's toolkit is shared by every host that calls
    into it, so it can serve the sheet tools; whether they are served is
    the CALLING host's choice, made on each call, so a host is served
    exactly what it is offered."""
    if name not in _OPT_IN_TOOLS or _asked_for_sheet_tools(_host()):
        return None
    return _unknown_tool(name)


def _new_toolkit(folder: Optional[str]):
    from planlens.tools import ReviewToolkit
    host = _host()
    kw: Dict[str, Any] = dict(host.toolkit_options)
    # shared by every host of the conversation: it can serve the sheet
    # tools, and each call decides whether they are served
    # (:func:`_refused_opt_in`)
    kw["sheet_tools"] = True
    kw.update(resolve_source=_resolve, look=host.look)
    if host.vision_hint is not None:
        kw["vision_hint"] = host.vision_hint
    if host.image_view_hint is not None:
        kw["image_view_hint"] = host.image_view_hint
    if folder:
        kw["output_dir"] = os.path.join(folder, host.scratch_dir)
    return ReviewToolkit(**kw)


def _close_quietly(kit) -> None:
    try:
        kit.close()
    except Exception:  # noqa: BLE001 - closing is housekeeping
        pass


def _space(create: bool = True, hold: bool = False) -> Optional[_Space]:
    """This conversation's space, its toolkit open; idle ones closed.
    ``hold`` marks a call in flight (release with :func:`_release`), so the
    toolkit cannot be closed under it."""
    key, folder = _space_key()
    host = _host()
    max_live, idle_s = int(host.max_live_toolkits), float(host.idle_close_s)
    to_close = []
    with _SPACES_LOCK:
        space = _SPACES.get(key)
        if space is None:
            if not create:
                return None
            space = _Space(key, folder)
            _SPACES[key] = space
        _SPACES.move_to_end(key)
        space.last_used = time.monotonic()
        if hold:
            space.busy += 1
        if space.kit is None:
            space.kit = _new_toolkit(folder)
        # Housekeeping: idle toolkits closed, then the oldest beyond the cap,
        # never one with a call in flight.
        now = time.monotonic()
        live = [s for s in _SPACES.values() if s.kit is not None]
        for s in live:
            if s is not space and not s.busy \
                    and now - s.last_used > idle_s:
                to_close.append(s.kit)
                s.kit = None
        live = [s for s in _SPACES.values() if s.kit is not None]
        for s in live:                       # oldest first
            if len(live) - len(to_close) <= max_live:
                break
            if s is not space and not s.busy and s.kit is not None:
                to_close.append(s.kit)
                s.kit = None
        while len(_SPACES) > MAX_SPACES:
            oldest = next(iter(_SPACES.values()))
            if oldest is space or oldest.busy:
                break
            _SPACES.popitem(last=False)
            if oldest.kit is not None:
                to_close.append(oldest.kit)
    for kit in to_close:                     # outside the lock: may wait
        _close_quietly(kit)
    return space


def _remember(space: _Space) -> None:
    """Note the source of every document open in ``space``'s toolkit, so its
    handle keeps working after the document is closed (the conversation's
    limit, or an idle toolkit closed)."""
    kit = space.kit
    entries = getattr(kit, "_entries", None) if kit is not None else None
    if not entries:
        return
    with _SPACES_LOCK:
        for handle, entry in list(entries.items()):
            src = getattr(entry, "source", None) or getattr(entry, "path", None)
            if src and not looks_like_handle(str(src)):
                space.sources[handle] = str(src)
                space.sources.move_to_end(handle)
        while len(space.sources) > MAX_REMEMBERED_HANDLES:
            space.sources.popitem(last=False)


def handle_source(handle: str) -> Optional[str]:
    """The source (attachment key or file) a document ``handle`` was opened
    from IN THIS CONVERSATION, else ``None`` -- another conversation's
    handle means nothing here."""
    if not handle:
        return None
    space = _space(create=False)
    if space is None:
        return None
    _remember(space)
    with _SPACES_LOCK:
        return space.sources.get(str(handle))


def _release(space: _Space) -> None:
    """End a call :func:`_space` was asked to ``hold`` for."""
    with _SPACES_LOCK:
        space.busy = max(0, space.busy - 1)
        space.last_used = time.monotonic()
    _remember(space)


def available() -> bool:
    """Whether the installed planlens has the tool layer."""
    try:
        import planlens.tools  # noqa: F401
    except ImportError:
        return False
    return True


def budget_for_cap(cap: int) -> int:
    """planlens' size limit for a host result cap (``<= 0`` = uncapped)."""
    if cap <= 0:
        return UNCAPPED_BUDGET
    return max(1000, int(cap) - _CAP_MARGIN)


def _resolve(source: str):
    """An attachment key, a handle this conversation opened, or a source the
    host resolves (``ReviewHost.resolve_source``: a file of this
    conversation) -> PDF bytes or a readable path (the toolkit's resolver).
    The host refuses a path outside what this conversation may read, with
    its own words (a ``ToolError``)."""
    attachments = _ATTACHMENTS.get() or {}
    if source in attachments:
        return attachments[source]
    remembered = handle_source(source) if looks_like_handle(source) else None
    if remembered and remembered != source:
        if remembered in attachments:
            return attachments[remembered]
        source = remembered
    return _host().resolve(source)


def _toolkit():
    """The toolkit of the conversation in this context (one per bound
    working folder; one shared toolkit for a library caller with none)."""
    return _space().kit


#: The waits for planlens' per-document lock during the call running in this
#: context, added up (BACKLOG EV-14). Field session 2026-10-14: raster
#: ``log_grid`` calls held one document's lock 14-20 s each, other tools
#: queued behind them for up to 10 minutes, and the record could not tell a
#: slow tool from a queued one.
_LOCK_WAIT: "ContextVar[Optional[list]]" = ContextVar(
    "geotech_document_lock_wait", default=None)

#: A wait shorter than this is not put on the record.
LOCK_WAIT_MIN_S = 0.05


class _TimedLock:
    """planlens' per-document lock with each wait for it timed: the same
    lock, held over the same work, only the wait is measured and added to
    the running call's tally (:data:`_LOCK_WAIT`)."""

    def __init__(self, lock):
        self._lock = lock

    def acquire(self, blocking: bool = True, timeout: float = -1):
        t0 = time.monotonic()
        got = self._lock.acquire(blocking, timeout)
        tally = _LOCK_WAIT.get()
        if tally is not None:
            tally[0] += time.monotonic() - t0
        return got

    def release(self) -> None:
        self._lock.release()

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc) -> bool:
        self.release()
        return False

    def __getattr__(self, name):
        return getattr(self._lock, name)


def _time_document_lock(space, arguments: Dict[str, Any]) -> None:
    """Time the lock of the document this call names (best effort: needs
    planlens' entry lookup; nothing changes when it is not there)."""
    handle = str((arguments or {}).get("handle") or "").strip()
    lookup = getattr(getattr(space, "kit", None), "_entry", None)
    if not handle or not callable(lookup):
        return
    try:
        entry = lookup(handle)
        lock = getattr(entry, "lock", None)
        if lock is not None and not isinstance(lock, _TimedLock):
            entry.lock = _TimedLock(lock)
    except Exception:  # noqa: BLE001 - an unknown handle: the call says so
        pass


def _report_lock_wait(name: str, waited: float) -> None:
    if waited < LOCK_WAIT_MIN_S:
        return
    _host().emit("document_lock_wait",
                 {"tool": name, "waited_s": round(waited, 3)})


def dispatch_document_tool(name: str, arguments: Dict[str, Any],
                           attachments: Optional[Dict[str, bytes]] = None,
                           max_chars: Optional[int] = None,
                           cap: Optional[int] = None) -> str:
    """Run one document tool; returns a JSON string within ``max_chars``
    (``cap``, when given, is the host's own limit: the viewer page numbers
    are added only while the result stays under it).

    It never raises (live smoke wave 2b, C3: MuPDF's ``FzErrorSystem`` out
    of ``annotate_document`` ended a tester's whole turn and showed the
    server path): an error comes back as ``{"error", "hint"}`` with no
    server path in it, and the conversation goes on. A Word, Excel or DXF
    file is read here as text (:func:`_side_document`), never through
    MuPDF, which read a workbook as nine digits (C2) and could not open a
    DXF at all (C8).

    Pages: ``pdf_pages`` (1-based, as a viewer shows) is taken in place of
    ``pages`` and ``pdf_page`` in place of ``page``; a page the document
    does not have comes back as an error with a hint ("pages are 0-based
    here; PDF page 5 is page 4") -- live smoke wave 2c."""
    if not available():
        return json.dumps({
            "error": "document tools need planlens with planlens.tools "
                     "(0.3 or later)",
            "hint": "pip install -U planlens"})
    refused = _refused_opt_in(name)
    if refused is not None:
        return refused
    # A shallow snapshot: the host mutates its attachments dict between turns.
    token = _ATTACHMENTS.set(dict(attachments or {}))
    space = None
    opened_note = None
    one_based = False
    try:
        space = _space(hold=True)
        if name not in space.kit.tool_names:
            return _unknown_tool(name)
        arguments = dict(arguments or {})
        one_based, refused = _one_based_pages(arguments)
        if refused is not None:
            return refused
        side = _side_document(name, arguments, max_chars)
        if side is not None:
            return side
        refused, opened_note = _as_handle_or_source(space, name, arguments)
        if refused is not None:
            return refused
        _time_document_lock(space, arguments)
        tally = [0.0]
        lock_token = _LOCK_WAIT.set(tally)
        try:
            out = space.kit.call_json(name, arguments, max_chars=max_chars)
        finally:
            _LOCK_WAIT.reset(lock_token)
            _report_lock_wait(name, tally[0])
        if name == "open_document":
            out = _image_coordinates(out, arguments.get("source"))
    except Exception as exc:  # noqa: BLE001 - a tool error, never the turn's
        return _with_range_hint(_host().tool_error_json(name, exc),
                                one_based)
    finally:
        if space is not None:
            _release(space)
        _ATTACHMENTS.reset(token)
    out = _with_range_hint(out, one_based)
    # The margin planlens was given below the host's cap is where the viewer
    # page numbers fit; a result they would push past it goes out as it came.
    if cap and cap > 0:
        limit: Optional[int] = int(cap) - 20
    elif max_chars:
        limit = max_chars + _CAP_MARGIN - 20
    else:
        limit = None
    out = _short_image_paths(out)
    out = with_viewer_pages(out, limit)
    if opened_note:
        out = _with_note(out, "opened", opened_note, limit)
    return out


#: ``open_document``'s ``coordinates`` for an IMAGE (BACKLOG VIS-26): it
#: said "PDF points", and a model that took a look's pixels as those points
#: drew a ring off a phone photo's page twice (live wave w4, F39).
IMAGE_COORDINATES = (
    "this image's own pixels ({w} x {h} px), top-left origin, y down: what "
    "its looks give and what render_region, find_like and annotate_document "
    "take on it; page 0 is the image")


def _image_coordinates(out: str, source: Any) -> str:
    """``open_document``'s result with its ``coordinates`` said in the
    image's own pixels when the document is an image file; as it came
    otherwise. Runs while the call's attachments are set."""
    if not isinstance(out, str) or '"image"' not in out:
        return out
    try:
        data = json.loads(out)
    except ValueError:
        return out
    if not isinstance(data, dict) or data.get("kind") != "image" \
            or "coordinates" not in data:
        return out
    try:
        resolved = _resolve(str(source or ""))
        if isinstance(resolved, (bytes, bytearray)):
            raw = bytes(resolved)
        else:
            with open(resolved, "rb") as fh:
                raw = fh.read()
        images = _host().images
        size = (images.shown_size(raw)
                if images is not None and images.shown_size else None)
    except Exception:  # noqa: BLE001 - the line stays as planlens wrote it
        size = None
    if not size:
        return out
    data["coordinates"] = IMAGE_COORDINATES.format(w=size[0], h=size[1])
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"))


def _one_based_pages(arguments: Dict[str, Any]):
    """Rewrite ``pdf_pages`` / ``pdf_page`` (1-based) in ``arguments`` into
    the 0-based ``pages`` / ``page`` planlens takes, in place. Returns
    ``(one_based, error_json or None)``: giving both forms of one argument
    is refused unless they agree (never a guess)."""
    from planlens.review.page_numbers import (PAGE_ARGS_NOTE,
                                              pdf_pages_to_pages,
                                              resolve_page)
    one_based = False
    if "pdf_pages" in arguments:
        spec = arguments.pop("pdf_pages")
        if spec not in (None, "", [], ()):
            if arguments.get("pages") not in (None, "", [], ()):
                return True, json.dumps({
                    "error": "give pages (0-based) or pdf_pages (1-based), "
                             "not both",
                    "hint": "pdf_pages counts as a PDF viewer does; pages "
                            "counts from 0"})
            pages, problem = pdf_pages_to_pages(spec)
            if problem is not None:
                return True, json.dumps(problem)
            arguments["pages"] = pages
            one_based = True
    if "pdf_page" in arguments:
        given = arguments.pop("pdf_page")
        if given not in (None, ""):
            page, problem = resolve_page(arguments.get("page"), given)
            if problem is not None:
                problem.setdefault("hint", PAGE_ARGS_NOTE)
                return True, json.dumps(problem)
            arguments["page"] = page
            one_based = True
    return one_based, None


def _with_range_hint(out: str, one_based: bool = False) -> str:
    """``out`` (a tool's JSON) with a hint added when its error is a page
    out of range (:func:`planlens.review.page_numbers.range_hint`)."""
    if not isinstance(out, str) or '"error"' not in out[:400]:
        return out
    try:
        data = json.loads(out)
    except ValueError:
        return out
    if not isinstance(data, dict) or "error" not in data:
        return out
    from planlens.review.page_numbers import range_hint
    hint = range_hint(str(data["error"]), one_based)
    if not hint:
        return out
    data["hint"] = f"{hint}. {data['hint']}" if data.get("hint") else hint
    return json.dumps(data, ensure_ascii=False)


def _as_handle_or_source(space: _Space, name: str,
                         arguments: Dict[str, Any]):
    """A7 (live smoke 1: 29 of 43 tool errors): a handle and a source are
    interchangeable. ``open_document`` given a handle this conversation
    opened reads the document it stands for; any other document tool given
    a file name or attachment key where its ``handle`` goes opens that
    document first. Returns ``(error_json or None, note or None)`` and
    rewrites ``arguments`` in place."""
    kit = space.kit
    if name == "open_document":
        src = str(arguments.get("source") or "")
        if looks_like_handle(src):
            remembered = handle_source(src)
            if remembered:
                arguments["source"] = remembered
        return None, None
    if "handle" not in arguments:
        return None, None
    given = str(arguments.get("handle") or "").strip()
    if not given or given in getattr(kit, "_entries", {}):
        return None, None
    # A handle-shaped value (``doc_`` and no file extension) is a handle --
    # planlens answers an unknown one itself, naming this conversation's
    # open handles; anything else is a file name or attachment key.
    handle_like = looks_like_handle(given) or (
        given.startswith("doc_") and not os.path.splitext(given)[1])
    source = handle_source(given) if handle_like else given
    if not source:
        return None, None            # planlens says "unknown handle" itself
    from planlens.tools import ToolError
    try:
        entry = kit.open(source)
    except Exception as exc:  # noqa: BLE001 - ToolError, or a resolver slip
        if not isinstance(exc, ToolError):
            exc = ToolError(f"{type(exc).__name__}: {exc}")
        if handle_like:
            return None, None
        out = {"error": (f"'{given}' is not a document handle this "
                         f"conversation opened, and it did not open as a "
                         f"document: {exc}"),
               "hint": (getattr(exc, "hint", None)
                        or "call open_document with the file's name first")}
        return json.dumps(out, ensure_ascii=False), None
    arguments["handle"] = entry.handle
    if handle_like:
        return None, None            # reopened after it was closed: silent
    return None, (f"'{given}' was opened as document handle "
                  f"{entry.handle}; either name works in later calls")


def _with_note(result: str, key: str, note: str,
               limit: Optional[int]) -> str:
    """``result`` with ``{key: note}`` added, while it stays under ``limit``."""
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return result
    if not isinstance(data, dict) or key in data:
        return result
    data[key] = note
    out = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return result if limit is not None and len(out) > limit else out


def _short_image_paths(result: str) -> str:
    """Images the toolkit wrote (page thumbnails) named by their place in
    the conversation (``.scratch/doc_…_thumbs_0-29.png``), which the vision
    tools resolve -- never by their server path (A6, A9). Unchanged with no
    working folder bound."""
    if '"image_path"' not in result:
        return result
    host = _host()
    if host.display_path is None:
        return result
    display_path = host.display_path
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return result

    def walk(obj):
        if isinstance(obj, dict):
            return {k: (display_path(v) if k == "image_path"
                        and isinstance(v, str) else walk(v))
                    for k, v in obj.items()}
        if isinstance(obj, list):
            return [walk(v) for v in obj]
        return obj

    return json.dumps(walk(data), ensure_ascii=False, separators=(",", ":"))


_PAGE_HEADER = re.compile(r"=== page (\d+)( continued)?")

#: A planlens page-range string ("0-2,5,7-9").
_RANGES = re.compile(r"^\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*$")


def _viewer_ranges(text: str) -> str:
    """``"0-2,5"`` -> ``"1-3,6"``: a 0-based range string as a viewer counts."""
    return re.sub(r"\d+", lambda m: str(int(m.group(0)) + 1),
                  re.sub(r"\s+", "", text))


def _is_pages_key(key: Any) -> bool:
    """A key that holds page indexes (``pages``, ``pages_to_view``,
    ``duplicate_pages``, ``pages_with_hits`` ...), never a cursor."""
    k = str(key)
    return k != "next" and not k.startswith("pdf_") and (
        k == "pages" or k.startswith("pages_") or k.endswith("_pages"))


def _viewer_pages_value(value: Any) -> Any:
    """The viewer's numbering of a page-range value, or ``None`` when it is
    not one: a range string, a ``{kind: ranges}`` map, or a
    ``{"<page>": n}`` map."""
    if isinstance(value, str) and _RANGES.match(value):
        return _viewer_ranges(value)
    if isinstance(value, dict) and value:
        if all(str(k).isdigit() for k in value):
            return {str(int(k) + 1): v for k, v in value.items()}
        if all(isinstance(v, str) and _RANGES.match(v)
               for v in value.values()):
            return {k: _viewer_ranges(v) for k, v in value.items()}
    return None


def _viewer_pages(obj: Any) -> Any:
    if isinstance(obj, dict):
        out: Dict[str, Any] = {}
        for k, v in obj.items():
            # A cursor ("next") is handed back to the tool as it is: no
            # viewer numbers in it to be mistaken for an argument.
            out[k] = v if k == "next" else _viewer_pages(v)
            if (k == "page" and isinstance(v, int) and not isinstance(v, bool)
                    and "pdf_page" not in obj):
                out["pdf_page"] = v + 1
            elif _is_pages_key(k) and f"pdf_{k}" not in obj:
                # Live smoke wave 2b (C9): a single page had its viewer
                # number, a range did not, and chapter starts were cited one
                # page low. Every range now carries the viewer's numbers too.
                shown = _viewer_pages_value(v)
                if shown is not None:
                    out[f"pdf_{k}"] = shown
        return out
    if isinstance(obj, list):
        return [_viewer_pages(v) for v in obj]
    if isinstance(obj, str) and "=== page " in obj:
        return _PAGE_HEADER.sub(
            lambda m: f"=== page {m.group(1)} [pdf_page "
                      f"{int(m.group(1)) + 1}]{m.group(2) or ''}", obj)
    return obj


def with_viewer_pages(result: str, limit: Optional[int] = None) -> str:
    """``result`` with the page a PDF VIEWER shows beside every page index.

    planlens (like every tool here) numbers pages from 0, and its results
    cite "page 28"; a reader opening the file in Bluebeam or Acrobat finds
    that content on page 29. Every citation drawn from a result with no
    printed page number was one page early (review of 2026-09-26). So each
    ``"page": n`` gains ``"pdf_page": n + 1``, each ``=== page n`` header
    in read text gains ``[pdf_page n+1]`` — the number the prompt says to cite
    — and each page RANGE (``"pages": "0-12"``, ``pages_by_kind``,
    ``pages_to_view``, ``pages_with_hits`` ...) gains a ``pdf_`` twin in the
    viewer's numbers (``"pdf_pages": "1-13"``; live smoke wave 2b, C9). The
    tools still TAKE 0-based pages, as their descriptions say.
    Unparseable results, and results the additions would push past
    ``limit``, are returned unchanged.
    """
    try:
        data = json.loads(result)
    except (TypeError, ValueError):
        return result
    # Serialized the way planlens serializes, so the length compares like for
    # like with the budget it was given.
    out = json.dumps(_viewer_pages(data), ensure_ascii=False,
                     separators=(",", ":"))
    if limit is not None and len(out) > limit:
        return result
    return out


# ---------------------------------------------------------------------------
# Word, Excel and DXF files: read as text, never as pages
# ---------------------------------------------------------------------------
# Live smoke wave 2b. C2: an .xlsx fetched from SharePoint went to MuPDF's
# Office conversion, which dropped every shared-string cell; read_document
# returned "0 0 0 0 0 1 0 1 0" and the tester was told three times their log
# was "probably damaged". C8: a DXF could not be opened at all ("could not
# open ... as a PDF or image: FileDataError"), though planlens reads DXF
# exactly (planlens.ir.ingest.from_dxf). Both are now read here, by name:
# open_document returns the text (and, for a DXF, its layers and entities),
# read_document pages through it by line, search_document finds lines in it.
# The file's NAME stands in for a handle -- there are no pages to hand to the
# page, markup and vision tools, and those say so instead of guessing.

#: Word and Excel files (the older .doc / .xls are named so the reader can
#: say what to save them as).
OFFICE_DOCUMENT_EXTENSIONS = (".docx", ".xlsx", ".xlsm", ".doc", ".xls")
#: CAD drawings read as data.
CAD_DOCUMENT_EXTENSIONS = (".dxf",)
#: Tools that work on such a file (the rest need pages).
SIDE_READ_TOOLS = ("open_document", "read_document", "search_document")
#: Files read this way kept in memory (by path, mtime and size).
_SIDE_CACHE_MAX = 16
_SIDE_CACHE: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
_SIDE_LOCK = threading.Lock()


def side_kind(name: Any) -> Optional[str]:
    """``"office"`` / ``"cad"`` for a file read as text rather than pages
    (by its extension), else ``None``."""
    ext = os.path.splitext(str(name or "").strip())[1].lower()
    if ext in OFFICE_DOCUMENT_EXTENSIONS:
        return "office"
    if ext in CAD_DOCUMENT_EXTENSIONS:
        return "cad"
    return None


def _side_source(name: str, arguments: Dict[str, Any]) -> Optional[str]:
    """The Word/Excel/DXF file this call is about, as the model named it (a
    handle this conversation opened from one counts), or ``None``."""
    key = "source" if name == "open_document" else "handle"
    given = str(arguments.get(key) or "").strip()
    if not given:
        return None
    if side_kind(given):
        return given
    if looks_like_handle(given):
        remembered = handle_source(given)
        if remembered and side_kind(remembered):
            return remembered
    return None


def _side_path(source: str):
    """``(path, temporary)`` of a readable copy of ``source``: the file in
    the working folder, or the attachment's bytes written to a temporary
    file of the same type. Raises planlens' ``ToolError`` as the document
    tools do."""
    resolved = _resolve(source)
    if isinstance(resolved, (bytes, bytearray)):
        import tempfile
        ext = os.path.splitext(source)[1] or ".bin"
        fd, tmp = tempfile.mkstemp(suffix=ext, prefix="gse_side_")
        with os.fdopen(fd, "wb") as fh:
            fh.write(bytes(resolved))
        return tmp, True
    return str(resolved), False


def _dxf_units_in(path: str) -> Optional[int]:
    """The ``$INSUNITS`` code a text DXF's header states, if any."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(400_000).decode("latin-1", errors="replace")
    except OSError:
        return None
    m = re.search(r"\$INSUNITS\s*\r?\n\s*70\s*\r?\n\s*(-?\d+)", head)
    return int(m.group(1)) if m else None


#: $INSUNITS codes (DXF reference) -> unit names.
_INSUNITS_NAMES = {0: None, 1: "in", 2: "ft", 3: "mi", 4: "mm", 5: "cm",
                   6: "m", 7: "km", 8: "microinches", 9: "mils", 10: "yd"}


def _fmt(v: float) -> str:
    return f"{v:.6g}" if abs(v) < 1e6 else f"{v:.1f}"


# -- A DXF's geometry, listed with the text it sits by (wave 2c, E4) ----------
# F40: the boring circles, the building outline and the arrow line were
# counted but not listed, so `search_document("CIRCLE")` found nothing and
# the model read coordinates off raw DXF pages, 3-4 calls a turn.

#: Geometry lines listed per DXF at most (the text is always listed whole).
CAD_GEOMETRY_MAX = 20000
#: Vertices written out per polyline or hatch boundary.
CAD_VERTICES_SHOWN = 12
#: Grid cells one entity may search for nearby text (a sheet border's
#: outline is not "near" any one label).
_NEAR_MAX_CELLS = 2500
#: Geometry kinds listed, in this order.
_CAD_GEOMETRY_KINDS = ("circle", "arc", "polyline", "line", "region")


def _seg_dist(p, a, b) -> float:
    ax, ay, bx, by = a[0], a[1], b[0], b[1]
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    t = 0.0 if L2 == 0 else max(0.0, min(1.0, ((p[0] - ax) * dx
                                               + (p[1] - ay) * dy) / L2))
    return ((p[0] - ax - t * dx) ** 2 + (p[1] - ay - t * dy) ** 2) ** 0.5


def _inside(p, ring) -> bool:
    """Whether point ``p`` lies inside the closed ``ring`` (ray casting)."""
    x, y, hit = p[0], p[1], False
    n = len(ring)
    for i in range(n):
        x1, y1 = ring[i][0], ring[i][1]
        x2, y2 = ring[(i + 1) % n][0], ring[(i + 1) % n][1]
        if (y1 > y) != (y2 > y) and \
                x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1:
            hit = not hit
    return hit


def _ring_of(e):
    kind = getattr(e, "KIND", "")
    if kind == "polyline":
        return list(getattr(e, "vertices", None) or []), \
            bool(getattr(e, "closed", False))
    if kind == "region":
        return list(getattr(e, "boundary", None) or []), True
    return [], False


def _distance_to(e, p) -> float:
    """Distance from point ``p`` to entity ``e``'s drawn geometry."""
    kind = getattr(e, "KIND", "")
    if kind in ("circle", "arc"):
        c, r = getattr(e, "center", (0.0, 0.0)), getattr(e, "radius", 0.0)
        return abs(((p[0] - c[0]) ** 2 + (p[1] - c[1]) ** 2) ** 0.5 - r)
    if kind == "line":
        return _seg_dist(p, e.start, e.end)
    pts, closed = _ring_of(e)
    if not pts:
        return float("inf")
    if len(pts) == 1:
        return _seg_dist(p, pts[0], pts[0])
    segs = list(zip(pts, pts[1:])) + ([(pts[-1], pts[0])] if closed else [])
    return min(_seg_dist(p, a, b) for a, b in segs)


class _TextGrid:
    """The drawing's text items in grid cells of the "near" radius."""

    def __init__(self, texts, radius):
        self.texts, self.r = texts, radius
        self.cells: Dict[tuple, list] = {}
        for i, t in enumerate(texts):
            self.cells.setdefault(self._cell(t[0]), []).append(i)

    def _cell(self, p):
        return (int(p[0] // self.r), int(p[1] // self.r))

    def candidates(self, box):
        """Text indices within the radius of ``box``, or ``None`` when the
        box is too large to say one label is near it."""
        x0, y0 = self._cell((box[0] - self.r, box[1] - self.r))
        x1, y1 = self._cell((box[2] + self.r, box[3] + self.r))
        if (x1 - x0 + 1) * (y1 - y0 + 1) > _NEAR_MAX_CELLS:
            return None
        out = []
        for ix in range(x0, x1 + 1):
            for iy in range(y0, y1 + 1):
                out.extend(self.cells.get((ix, iy), ()))
        return out


def _near_text(e, grid: Optional[_TextGrid]) -> str:
    """`` near "B-2"`` (the closest text within the radius) and, for a
    closed outline, `` encloses "BUILDING"``; ``""`` when neither."""
    if grid is None or getattr(e, "bbox", None) is None:
        return ""
    idx = grid.candidates(e.bbox)
    if not idx:
        return ""
    bits = []
    ring, closed = _ring_of(e)
    if closed and 3 <= len(ring) <= 2000:
        # The label of an outline sits near its middle: the texts whose
        # middles are nearest its middle first.
        cx, cy = (e.bbox[0] + e.bbox[2]) / 2, (e.bbox[1] + e.bbox[3]) / 2
        inside = sorted(
            (grid.texts[i] for i in idx if _inside(grid.texts[i][0], ring)),
            key=lambda t: (t[2][0] - cx) ** 2 + (t[2][1] - cy) ** 2)
        if inside:
            more = len(inside) - 2
            bits.append("encloses " + ", ".join(
                f'"{t[1]}"' for t in inside[:2]) + (
                f" and {more} more text{'s' if more > 1 else ''}"
                if more > 0 else ""))
    best, best_d = None, grid.r
    for i in idx:
        d = _distance_to(e, grid.texts[i][0])
        if d <= best_d:
            best, best_d = grid.texts[i][1], d
    if best is not None and not any(best in b for b in bits):
        bits.append(f'near "{best}"')
    return (" " + "; ".join(bits)) if bits else ""


def _cad_geometry(ir, pt, scale, texts):
    """``(rows, n_listed, n_all)``: one line per circle, arc, polyline, line
    and hatch, in drawing units, with the text it sits by."""
    heights = sorted(float(getattr(e, "height", 0) or 0) for e in ir.entities
                     if getattr(e, "KIND", "") == "text"
                     and (getattr(e, "height", 0) or 0) > 0)
    box = ir.bbox()
    diag = (((box[2] - box[0]) ** 2 + (box[3] - box[1]) ** 2) ** 0.5
            if box is not None else 0.0)
    radius = max(4 * heights[len(heights) // 2] if heights else 0.0,
                 0.02 * diag)
    grid = _TextGrid(texts, radius) if texts and radius > 0 else None

    def num(v) -> str:
        return _fmt(v * scale)

    def where(e) -> str:
        layer = getattr(e, "layer", None) or "0"
        style = str(getattr(e, "style", None) or "")
        block = (f" [block {style.split('|')[0][len('block:'):]}]"
                 if style.startswith("block:") else "")
        return f" [layer {layer}]{block}"

    def verts(points) -> str:
        shown = " ".join(pt(p) for p in points[:CAD_VERTICES_SHOWN])
        more = len(points) - CAD_VERTICES_SHOWN
        return shown + (f" ... (+{more} more)" if more > 0 else "")

    picked = [e for e in ir.entities
              if getattr(e, "KIND", "") in _CAD_GEOMETRY_KINDS]
    order = {k: i for i, k in enumerate(_CAD_GEOMETRY_KINDS)}

    def key(e):
        b = getattr(e, "bbox", None) or (0.0, 0.0, 0.0, 0.0)
        return (order[e.KIND], -round(b[3], 6), round(b[0], 6))

    picked.sort(key=key)
    rows = []
    for e in picked[:CAD_GEOMETRY_MAX]:
        kind = e.KIND
        if kind == "circle":
            text = (f"CIRCLE centre {pt(e.center)} radius {num(e.radius)}")
        elif kind == "arc":
            text = (f"ARC centre {pt(e.center)} radius {num(e.radius)}, "
                    f"{_fmt(e.start_angle)} to {_fmt(e.end_angle)} deg")
        elif kind == "line":
            text = (f"LINE {pt(e.start)} to {pt(e.end)}, length "
                    f"{num(e.length())}")
        elif kind == "polyline":
            vs = list(e.vertices or [])
            text = (f"POLYLINE {'closed' if e.closed else 'open'}, "
                    f"{len(vs)} vertices: {verts(vs)}")
        else:
            vs = list(getattr(e, "boundary", None) or [])
            area = e.area() * scale * scale
            text = (f"HATCH area {_fmt(area)}, boundary of {len(vs)} "
                    f"vertices: {verts(vs)}")
        rows.append(text + where(e) + _near_text(e, grid))
    return rows, len(rows), len(picked)


def _read_cad(path: str, name: str) -> Dict[str, Any]:
    """A DXF as its text lines (every TEXT/MTEXT/ATTRIB, leader and
    dimension, in reading order, in DRAWING units) and a summary."""
    from planlens.dxf.units import UNIT_FACTORS
    from planlens.ir.ingest import from_dxf
    code = _dxf_units_in(path)
    unit = _INSUNITS_NAMES.get(code) if code is not None else None
    factor = UNIT_FACTORS.get(unit or "", None)
    # planlens converts to metres by the header's units; reading in the
    # drawing's own units is what the user's CAD program shows, so the
    # conversion is undone (a unit planlens does not convert stays as read).
    ir = from_dxf(filepath=path, units=(unit if factor else "m"))
    scale = 1.0 / factor if factor else 1.0

    def pt(p) -> str:
        return f"({_fmt(p[0] * scale)}, {_fmt(p[1] * scale)})"

    rows = []
    texts = []         # (position, text, middle): what geometry sits by
    for e in ir.entities:
        kind = getattr(e, "KIND", "")
        layer = getattr(e, "layer", None) or "0"
        if kind == "text":
            text = " ".join(str(getattr(e, "content", "") or "").split())
            if text:
                pos = getattr(e, "position", (0.0, 0.0))
                b = getattr(e, "bbox", None)
                mid = (((b[0] + b[2]) / 2, (b[1] + b[3]) / 2) if b
                       else tuple(pos))
                texts.append((pos, text[:60], mid))
                rows.append((pos, f"TEXT \"{text}\" at {pt(pos)} "
                                  f"[layer {layer}]"))
        elif kind == "leader":
            verts = list(getattr(e, "vertices", None) or [])
            tip = verts[0] if verts else (0.0, 0.0)
            text = " ".join(str(getattr(e, "text", "") or "").split())
            rows.append((tip, f"LEADER \"{text}\" pointing at {pt(tip)} "
                              f"[layer {layer}]"))
        elif kind == "dimension":
            mid = getattr(e, "text_midpoint", None) or (0.0, 0.0)
            text = " ".join(str(getattr(e, "text", "") or "").split())
            meas = getattr(e, "measurement", None)
            val = (f" = {_fmt(meas * scale)}" if isinstance(meas, (int, float))
                   and meas else "")
            rows.append((mid, f"DIMENSION \"{text or '<>'}\"{val} at "
                              f"{pt(mid)} [layer {layer}]"))
    # Reading order on a plan: top to bottom, then left to right.
    rows.sort(key=lambda r: (-round(r[0][1] * scale, 3),
                             round(r[0][0] * scale, 3)))
    geometry, n_listed, n_geometry = _cad_geometry(ir, pt, scale, texts)
    layers = sorted(ir.counts_by_layer().items(), key=lambda kv: -kv[1])
    by_layer: Dict[str, Dict[str, int]] = {}
    for e in ir.entities:
        d = by_layer.setdefault(getattr(e, "layer", None) or "0", {})
        k = getattr(e, "KIND", "") or "other"
        d[k] = d.get(k, 0) + 1
    box = ir.bbox()
    header: Dict[str, Any] = {
        "kind": "dxf drawing",
        "read_as": "cad data (exact text and entities, drawing units)",
        "drawing_units": (unit or "not stated in the file"),
        "n_entities": len(ir.entities),
        "entities_by_type": ir.counts_by_type(),
        "layers": {k: v for k, v in layers[:40]},
        "entities_by_layer": {k: by_layer.get(k, {}) for k, _ in layers[:40]},
        "n_text_lines": len(rows),
        "n_geometry_lines": n_listed,
    }
    if n_geometry > n_listed:
        header["geometry_not_listed"] = n_geometry - n_listed
    if len(layers) > 40:
        header["layers_not_listed"] = len(layers) - 40
    if box is not None:
        header["extent"] = [float(_fmt(v * scale)) for v in box]
    if ir.warnings:
        header["warnings"] = [str(w)[:200] for w in ir.warnings[:5]]
    header["note"] = (
        f"'{name}' is a DXF drawing, read as CAD data, in drawing units: "
        "every piece of text, leader and dimension with its position, then "
        "the geometry - each CIRCLE (centre, radius), ARC, POLYLINE "
        "(vertices, closed or open), LINE (ends) and HATCH - with the text "
        "it sits by ('near', 'encloses'); and the entity counts per layer. "
        f"read_document(handle='{name}', start_line=N) pages through it all; "
        f"search_document(handle='{name}', pattern=...) finds a line (e.g. "
        "'CIRCLE', or a label). To add a note or comment to the drawing, "
        "use annotate_dxf (a copy, written by a CAD library) - never retype "
        "the DXF with save_file. There are no pages here: to LOOK at the "
        "sheet, or for a marked-up PDF, ask the user for a PDF plot of it.")
    lines = [r[1] for r in rows]
    if geometry:
        lines += ["GEOMETRY (drawing units; 'near' = the closest text):"] \
            + geometry
    return {"lines": lines, "header": header}


def _read_office(path: str, name: str) -> Dict[str, Any]:
    """A Word or Excel file as Markdown lines (the host's ``read_office``:
    python-docx and openpyxl are not planlens dependencies) and what it
    is. Pictures in the file may go to the working folder's scratch folder,
    named in the Markdown by that folder's name (none with no folder)."""
    from planlens.tools import ToolError
    host = _host()
    reader = host.read_office
    if reader is None:
        raise ToolError(
            f"'{name}' is a Word or Excel file, and this host reads none",
            hint="save it as a PDF and open that")
    try:
        image_dir = host.scratch(create=False)
    except Exception:  # noqa: BLE001 - pictures are named, not saved
        image_dir = None
    image_ref = host.scratch_dir if image_dir else None
    markdown, fields = reader(path, name, image_dir=image_dir,
                              image_ref=image_ref)
    ext = os.path.splitext(name)[1].lower()
    header: Dict[str, Any] = {
        "kind": ("word document" if ext in (".docx", ".doc")
                 else "excel workbook"),
        "read_as": "markdown"}
    note = fields.pop("note", None) if isinstance(fields, dict) else None
    if isinstance(fields, dict):
        header.update({k: v for k, v in fields.items()
                       if k not in ("read_as",)})
    tail = (f" There are no pages here, so the page, markup and vision tools "
            f"do not apply: read_document(handle='{name}', start_line=N) "
            f"pages through it, search_document(handle='{name}', "
            f"pattern=...) finds lines in it, and read_text_file reads it "
            f"the same way.")
    header["note"] = (note or f"'{name}' read as Markdown.") + tail
    return {"lines": str(markdown or "").splitlines(), "header": header}


def _side_read(source: str) -> Dict[str, Any]:
    """The text of a Word/Excel/DXF ``source`` (cached by path, mtime and
    size). Raises a ``ToolError`` / ``OfficeReadError`` with words fit to
    show."""
    path, temporary = _side_path(source)
    name = os.path.basename(str(source).replace("\\", "/")) or str(source)
    try:
        st = os.stat(path)
        key = (os.path.normcase(os.path.abspath(path)), st.st_mtime_ns,
               st.st_size)
        if not temporary:
            with _SIDE_LOCK:
                hit = _SIDE_CACHE.get(key)
                if hit is not None:
                    _SIDE_CACHE.move_to_end(key)
                    return hit
        kind = side_kind(name) or side_kind(path)
        read = _read_cad(path, name) if kind == "cad" \
            else _read_office(path, name)
        read["name"] = name
        if not temporary:
            with _SIDE_LOCK:
                _SIDE_CACHE[key] = read
                while len(_SIDE_CACHE) > _SIDE_CACHE_MAX:
                    _SIDE_CACHE.popitem(last=False)
        return read
    finally:
        if temporary:
            try:
                os.remove(path)
            except OSError:
                pass


def _lines_within(lines, start: int, budget: int):
    """``(text, next_start)``: lines from ``start`` whose JSON fits
    ``budget``; ``next_start`` is ``None`` at the end."""
    used, out = 0, []
    i = start
    while i < len(lines):
        size = len(json.dumps(lines[i], ensure_ascii=False)) + 2
        if out and used + size > budget:
            return "\n".join(out), i
        if not out and size > budget:      # one line longer than the room
            out.append(lines[i][:max(200, budget - 80)] + " ...[line cut]")
            return "\n".join(out), (i + 1 if i + 1 < len(lines) else None)
        out.append(lines[i])
        used += size
        i += 1
    return "\n".join(out), None


def _side_search(lines, pattern: str, regex: bool, case_sensitive: bool,
                 fuzzy: bool, min_score: int, max_hits: int):
    hits = []
    if regex:
        rx = re.compile(pattern, 0 if case_sensitive else re.IGNORECASE)
        test = lambda s: rx.search(s) is not None  # noqa: E731
    else:
        needle = pattern if case_sensitive else pattern.lower()
        test = lambda s: needle in (s if case_sensitive  # noqa: E731
                                    else s.lower())
    scorer = None
    if fuzzy and not regex:
        try:
            from rapidfuzz import fuzz
            scorer = fuzz.partial_ratio
        except ImportError:
            scorer = None
    for i, line in enumerate(lines):
        if scorer is not None:
            score = scorer(pattern.lower(), line.lower())
            if score >= min_score:
                hits.append({"line": i, "text": line[:400],
                             "score": round(float(score), 1)})
        elif test(line):
            hits.append({"line": i, "text": line[:400]})
    if scorer is not None:
        hits.sort(key=lambda h: -h["score"])
    return hits[:max_hits], len(hits)


def _side_document(name: str, arguments: Dict[str, Any],
                   max_chars: Optional[int]) -> Optional[str]:
    """The result of a document tool called on a Word, Excel or DXF file,
    or ``None`` when the call is about a PDF or an image (planlens' own)."""
    source = _side_source(name, arguments)
    if source is None:
        return None
    shown = os.path.basename(source.replace("\\", "/")) or source
    kind = side_kind(source)
    if name not in SIDE_READ_TOOLS:
        what = ("a DXF drawing, read as CAD data" if kind == "cad"
                else "a Word or Excel file, read as Markdown")
        hint = (f"read_document(handle='{shown}') reads it and "
                f"search_document(handle='{shown}', pattern=...) searches "
                "it.")
        hint += (" To add a note to the drawing, use annotate_dxf (it "
                 "writes a copy); to look at the sheet or for a marked-up "
                 "PDF, ask the user for a PDF plot of it." if kind == "cad"
                 else
                 " Comments on it go in a document you write (write_docx, "
                 "write_xlsx).")
        return json.dumps({
            "error": f"'{shown}' is {what}: it has no pages here, so "
                     f"{name} does not apply to it.",
            "hint": hint}, ensure_ascii=False)
    try:
        read = _side_read(source)
    except Exception as exc:  # noqa: BLE001 - ToolError / OfficeReadError
        from planlens.review.host import scrub_paths
        out = {"error": scrub_paths(str(exc)) or type(exc).__name__}
        hint = getattr(exc, "hint", None)
        if hint:
            out["hint"] = scrub_paths(hint)
        return json.dumps(out, ensure_ascii=False)
    lines = read["lines"]
    budget = max(1000, int(max_chars or 12000) - 600)
    if name == "search_document":
        pattern = str(arguments.get("pattern") or "")
        if not pattern:
            return json.dumps({"error": "pattern is empty"})
        try:
            hits, n = _side_search(
                lines, pattern, bool(arguments.get("regex")),
                bool(arguments.get("case_sensitive")),
                bool(arguments.get("fuzzy")),
                int(arguments.get("min_score") or 80),
                max(1, min(int(arguments.get("max_hits") or 100), 500)))
        except re.error as exc:
            return json.dumps({"error": f"invalid regular expression: {exc}",
                               "hint": "pass regex=false for a literal "
                                       "search"})
        out: Dict[str, Any] = {"handle": shown, "pattern": pattern,
                               "n_hits": n, "hits": []}
        room = budget - len(json.dumps(out))
        for h in hits:
            size = len(json.dumps(h, ensure_ascii=False)) + 1
            if size > room:
                out["hits_omitted_for_size"] = len(hits) - len(out["hits"])
                break
            out["hits"].append(h)
            room -= size
        out["note"] = ("hits are LINES of the file's text (line = the "
                       "start_line read_document takes); a Word/Excel/DXF "
                       "file has no pages")
        return json.dumps(out, ensure_ascii=False)
    try:
        start = max(0, int(arguments.get("start_line") or 0))
    except (TypeError, ValueError):
        start = 0
    if lines and start >= len(lines):
        return json.dumps({"error": f"start_line {start} is past the end "
                                    f"({len(lines)} lines)"})
    out = {"handle": shown, "source": source, "name": read.get("name", shown)}
    if name == "open_document" or start == 0:
        out.update({k: v for k, v in read["header"].items() if k != "note"})
    out["n_lines"] = len(lines)
    note = read["header"].get("note") if name == "open_document" else None
    reserve = len(json.dumps(out, ensure_ascii=False)) + len(
        json.dumps(note or "", ensure_ascii=False)) + 120
    text, nxt = _lines_within(lines, start, max(400, budget - reserve))
    out["lines_returned"] = (f"{start}-{start + max(0, text.count(chr(10)))}"
                             if text else "none")
    out["text"] = text
    if nxt is not None:
        out["next"] = {"start_line": nxt}
        out["more"] = (f"call read_document(handle='{shown}', "
                       f"start_line={nxt}) for the rest")
    if note:
        out["note"] = note
    return json.dumps(out, ensure_ascii=False)



def forget_path(path: str) -> int:
    """Close every document THIS conversation's toolkit holds open from the
    file ``path`` (so it can be replaced, and is re-read when next used: its
    handle still resolves, by the name it was opened under). Returns how
    many were closed. planlens has no public call for one document; this
    reaches into the toolkit's own entries under its lock."""
    space = _space(create=False)
    kit = space.kit if space is not None else None
    if kit is None or not path:
        return 0
    _remember(space)
    target = os.path.normcase(os.path.abspath(str(path)))
    entries = getattr(kit, "_entries", None)
    if not isinstance(entries, dict):
        return 0
    guard = getattr(kit, "_guard", None)
    closed = []
    if guard is not None:
        guard.acquire()
    try:
        for handle, entry in list(entries.items()):
            p = getattr(entry, "path", None)
            if p and os.path.normcase(os.path.abspath(str(p))) == target:
                entries.pop(handle, None)
                closed.append(entry)
        by_key = getattr(kit, "_by_key", None)
        if closed and isinstance(by_key, dict):
            gone = {e.handle for e in closed}
            for k in [k for k, h in by_key.items() if h in gone]:
                by_key.pop(k, None)
    finally:
        if guard is not None:
            guard.release()
    for entry in closed:
        try:
            with entry.lock:
                entry.doc.close()
        except Exception:  # noqa: BLE001 - closing is housekeeping
            pass
    return len(closed)



def open_document_entry(source: str,
                        attachments: Optional[Dict[str, bytes]] = None):
    """The toolkit's open entry for ``source`` (an attachment key or a path),
    opening it if it is not open yet. Raises planlens' ``ToolError`` when the
    source resolves to nothing. The coverage ledger
    (the host's coverage ledger) uses it to know which document a vision
    tool's ``attachment_key`` points at; the entry carries ``handle``,
    ``name``, ``doc`` (the planlens Document) and ``lock``. A handle this
    conversation opened is accepted too (A7)."""
    token = _ATTACHMENTS.set(dict(attachments or {}))
    try:
        if looks_like_handle(source):
            entry = document_entry(source)
            if entry is not None:
                return entry
            source = handle_source(source) or source
        return _toolkit().open(source)
    finally:
        _ATTACHMENTS.reset(token)


def document_entry(handle: str):
    """The open entry behind a document ``handle`` THIS conversation opened,
    or ``None``. A handle whose document was closed since (the
    conversation's open-document limit, or an idle conversation's toolkit)
    is reopened from the source it came from, where that still resolves."""
    if not handle or not available():
        return None
    kit = _toolkit()
    try:
        return kit._entry(handle)
    except Exception:            # an unknown or closed handle
        pass
    source = handle_source(handle)
    if not source:
        return None
    try:
        entry = kit.open(source)
    except Exception:            # noqa: BLE001 - its source is gone
        return None
    return entry if entry.handle == handle else None


def resolve_document_source(source: str,
                            attachments: Optional[Dict[str, bytes]] = None):
    """An attachment key or a path, resolved the way the document tools do.

    Returns the attachment's BYTES or a real path, which is what planlens'
    ``open_document`` takes either of. Raises planlens' ``ToolError`` when it
    is neither, with the same wording the document tools give.
    """
    token = _ATTACHMENTS.set(dict(attachments or {}))
    try:
        return _resolve(source)
    finally:
        _ATTACHMENTS.reset(token)


__all__ = ["UNCAPPED_BUDGET", "MAX_SPACES", "MAX_REMEMBERED_HANDLES",
           "looks_like_handle", "handle_source", "available",
           "budget_for_cap", "dispatch_document_tool", "IMAGE_COORDINATES",
           "with_viewer_pages", "OFFICE_DOCUMENT_EXTENSIONS",
           "CAD_DOCUMENT_EXTENSIONS", "SIDE_READ_TOOLS", "side_kind",
           "CAD_GEOMETRY_MAX", "CAD_VERTICES_SHOWN", "forget_path",
           "open_document_entry", "document_entry",
           "resolve_document_source", "LOCK_WAIT_MIN_S"]
