"""What the host passes in: everything the moved document tools used to
import from the app.

BACKLOG PL-29 (a). The document tools, the markup check and ``find_like``
moved here from the GeotechStaffEngineer app, where they imported the app's
file confinement, its vision call, its working folder and its signer. planlens
imports nothing of the app and makes no model call, so each of those is now
something the host hands over in one :class:`ReviewHost`, installed for the
process with :func:`configure`, or for one conversation with :func:`using` (or
``call(..., host=)``): a host that builds one agent per conversation, each
with its own look and signer, gives each its own host:

=========================  ===================================================
``resolve_source``          A ``source`` (an upload key, a file name of the
                            conversation) -> PDF/image bytes or a readable
                            path; raises ``ToolError`` with words fit for the
                            model. Default: a real file path only.
``look``                    ``(image, question, kind) -> the model's text``
                            (:data:`planlens.tools.Look`). ``None``: no
                            ``find_like``, and marks are not checked (for
                            one conversation: ``using(look=None)``).
``working_folder``          ``() -> folder or None``: the folder the host
                            bound for this turn. One toolkit per folder; a
                            marked copy is written into it.
``signer``                  Who a comment is signed by (a string, or a
                            callable returning one).
``verdict_key``             ``() -> str``: where the markup check keeps its
                            verdicts per conversation (``""``: one shared).
``find_like_budget_s``      Seconds one ``find_like`` may spend: a number,
                            text holding one, or a callable returning either
                            (read on every call). ``None``, empty or
                            unreadable: the default, 300 s; ``0`` or less: no
                            limit.
=========================  ===================================================

and, each replacing one more call the app made, all optional:

* ``display_path`` -- how a result names a real file (default: as it is);
* ``readable_file`` -- a file name -> a path the conversation may read, or
  ``None`` (default: an existing path);
* ``output_path`` -- where a bare output name goes with no working folder
  (default: as given);
* ``hide_paths`` -- a result with the server's paths taken out (default: as
  it is);
* ``render_crop`` -- ``(pdf_bytes, page, bbox) -> (image, info)`` for the
  markup check's look: the crop of exactly ``bbox``, unpadded (the check's
  box already holds its margin); default: planlens' own render at 300 dpi,
  the app's fixed size; the app passes its budget-sized render;
* ``find_like_source`` -- ``(key, attachments) -> bytes``, the PDF or image
  ``find_like`` searches, raising with the words the model is shown (default:
  the document tools' resolver, whose ``ToolError`` hint goes with them);
* ``images`` -- an :class:`ImageSupport` that decodes image files (Pillow is
  not a planlens dependency; without one, marks and searches on an image file
  say it cannot be decoded here);
* ``read_office`` -- ``(path, name, image_dir=, image_ref=) -> (markdown,
  fields)`` for a Word or Excel file (python-docx and openpyxl are not
  planlens dependencies): ``image_dir`` is the working folder's scratch
  folder pictures in the file may be saved to, and ``image_ref`` how the
  Markdown names that folder (both ``None`` with no working folder);
* ``after_marked_copy`` -- ``(result, markups) -> result``, a last word on a
  marked copy's result (the app checks the documents its notes cite);
* ``record`` -- ``(event, data)``, e.g. ``("document_lock_wait", {...})``;
* ``error_json`` -- ``(tool, exc) -> JSON text`` for a tool that raised;
* ``vision_hint`` / ``image_view_hint`` -- the hints naming the host's own
  vision tools (planlens' defaults otherwise);
* ``scratch_dir`` -- the working folder's scratch folder name;
* ``own_mark_words`` -- words in a markup's author that make it one this
  host wrote, so ``remove`` may take it out (besides the signer itself).
"""

from __future__ import annotations

import dataclasses
import json
import os
import re
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterator, Optional, Tuple, Union

#: Who a comment is signed by when the host names nobody.
DEFAULT_SIGNER = "planlens (AI draft)"

#: Seconds one ``find_like`` call may spend in all, by default.
DEFAULT_FIND_LIKE_BUDGET_S = 300.0

#: The working folder's scratch folder (tool working images, never a
#: download).
DEFAULT_SCRATCH_DIR = ".scratch"


@dataclass
class ImageSupport:
    """How the host decodes an image file (the app uses Pillow).

    ``open_image(data, name) -> (picture, refusal_json)``: a picture with
    ``width`` and ``height`` (pixels, EXIF orientation applied); or a JSON
    refusal for bytes that are not an image; or ``(None, None)`` when it
    cannot be decoded here. ``as_pdf(picture) -> bytes``: a one-page PDF whose
    page is the image at ``page_scale(picture)`` points a pixel.
    ``shown_size(data) -> (w, h)`` or ``None``: the size a viewer shows."""

    open_image: Callable[[bytes, str], Tuple[Any, Optional[str]]]
    as_pdf: Callable[[Any], bytes]
    page_scale: Callable[[Any], float] = lambda picture: 1.0
    shown_size: Optional[Callable[[bytes], Optional[Tuple[int, int]]]] = None


def _default_resolver(source: str):
    from planlens.tools import ToolError
    if source and os.path.isfile(source):
        return source
    raise ToolError(
        f"'{source}' is not an attachment key or a readable file path",
        hint=("attachment keys: " + (", ".join(sorted(current_attachments()))
                                     or "none")
              + "; real paths (/tmp/..., /Volumes/...) also work"))


def current_attachments() -> Dict[str, bytes]:
    """The uploads of the call in progress (``{key: bytes}``)."""
    from planlens.review.conversation import _ATTACHMENTS
    return dict(_ATTACHMENTS.get() or {})


@dataclass
class ReviewHost:
    """Everything the host layer needs from its host (see the module
    docstring)."""

    resolve_source: Optional[Callable[[str], Any]] = None
    look: Optional[Callable[[bytes, str, str], str]] = None
    working_folder: Optional[Callable[[], Optional[str]]] = None
    signer: Union[str, Callable[[], str]] = DEFAULT_SIGNER
    verdict_key: Optional[Callable[[], str]] = None
    find_like_budget_s: Union[None, float, str, Callable[[], Any]] = \
        DEFAULT_FIND_LIKE_BUDGET_S
    display_path: Optional[Callable[[str], str]] = None
    readable_file: Optional[Callable[[str], Optional[str]]] = None
    output_path: Optional[Callable[[str], str]] = None
    hide_paths: Optional[Callable[[Any], Any]] = None
    render_crop: Optional[Callable[..., Tuple[bytes, Dict[str, Any]]]] = None
    find_like_source: Optional[Callable[[str, Dict[str, bytes]],
                                        bytes]] = None
    images: Optional[ImageSupport] = None
    read_office: Optional[Callable[..., Tuple[str, Dict[str, Any]]]] = None
    after_marked_copy: Optional[Callable[[Dict[str, Any], list],
                                         Dict[str, Any]]] = None
    record: Optional[Callable[[str, Dict[str, Any]], None]] = None
    error_json: Optional[Callable[[str, BaseException], str]] = None
    vision_hint: Optional[str] = None
    image_view_hint: Optional[str] = None
    scratch_dir: str = DEFAULT_SCRATCH_DIR
    own_mark_words: Tuple[str, ...] = ("(AI draft)",)
    max_live_toolkits: int = 8
    idle_close_s: float = 1800.0
    toolkit_options: Dict[str, Any] = field(default_factory=dict)

    # -- what the moved code asks -------------------------------------------
    def folder(self) -> Optional[str]:
        """The working folder bound for this turn, absolute, or ``None``."""
        if self.working_folder is None:
            return None
        got = self.working_folder()
        return os.path.abspath(got) if got else None

    def scratch(self, create: bool = True) -> Optional[str]:
        """The working folder's scratch folder, or ``None`` with no folder."""
        folder = self.folder()
        if not folder:
            return None
        path = os.path.join(folder, self.scratch_dir)
        if create:
            os.makedirs(path, exist_ok=True)
        return path

    def resolve(self, source: str):
        return (self.resolve_source or _default_resolver)(source)

    def signed_by(self) -> str:
        who = self.signer() if callable(self.signer) else self.signer
        return str(who or "").strip() or DEFAULT_SIGNER

    def conversation(self) -> str:
        try:
            return (self.verdict_key() if self.verdict_key else "") or ""
        except Exception:  # noqa: BLE001 - one shared key then
            return ""

    def shown(self, path: str) -> str:
        return self.display_path(path) if self.display_path else path

    def readable(self, name: str) -> Optional[str]:
        if self.readable_file is not None:
            return self.readable_file(name)
        return name if name and os.path.isfile(name) else None

    def out_path(self, name: str) -> str:
        if self.output_path is not None:
            return self.output_path(name)
        return os.path.expanduser(str(name))

    def hidden(self, obj: Any) -> Any:
        return self.hide_paths(obj) if self.hide_paths else obj

    def emit(self, event: str, data: Dict[str, Any]) -> None:
        if self.record is not None:
            try:
                self.record(event, data)
            except Exception:  # noqa: BLE001 - a record never fails a tool
                pass

    def tool_error_json(self, tool: str, exc: BaseException) -> str:
        if self.error_json is not None:
            return self.error_json(tool, exc)
        return json.dumps(tool_error(tool, exc), ensure_ascii=False)

    def find_like_budget(self) -> Any:
        """``find_like_budget_s`` as it reads now (a callable is called);
        :func:`planlens.review.find_like.budget_s` makes it seconds."""
        value = self.find_like_budget_s
        return value() if callable(value) else value


_HOST = ReviewHost()

#: The host of one conversation, set around its calls by :func:`using` (a
#: ``call(..., host=)``); ``None``: the process's host (:func:`configure`).
_OVERRIDE: ContextVar[Optional[ReviewHost]] = ContextVar(
    "planlens_review_host", default=None)


def configure(host: Optional[ReviewHost] = None, **fields: Any) -> ReviewHost:
    """Install the process's host (a :class:`ReviewHost`, or its fields by
    name) for every later call that :func:`using` does not serve with its
    own; returns it. ``configure()`` restores the defaults."""
    global _HOST
    if host is None:
        host = ReviewHost(**fields)
    elif fields:
        raise TypeError("pass a ReviewHost or its fields, not both")
    _HOST = host
    return host


def current() -> ReviewHost:
    """The host serving this context: the one :func:`using` set, else the
    process's (:func:`configure`)."""
    host = _OVERRIDE.get()
    return host if host is not None else _HOST


@contextmanager
def using(host: Optional[ReviewHost] = None,
          **fields: Any) -> Iterator[ReviewHost]:
    """Serve every call inside the block with ``host`` -- or the current
    host with ``fields`` replaced -- in this context only; other threads and
    conversations keep theirs. ``using(look=None)``: no look for this
    conversation (no ``find_like``, marks written and not checked).
    ``using(host, signer=...)`` changes that host's fields. The markup
    check's and ``find_like``'s worker threads run in a copy of the context,
    so the host reaches them too."""
    chosen = host if host is not None else current()
    if fields:
        chosen = dataclasses.replace(chosen, **fields)
    token = _OVERRIDE.set(chosen)
    try:
        yield chosen
    finally:
        _OVERRIDE.reset(token)


# -- small helpers the moved code used from the app ---------------------------

def _inside(path: str, folder: str) -> bool:
    try:
        p = os.path.normcase(os.path.abspath(path))
        f = os.path.normcase(os.path.abspath(folder))
        return os.path.commonpath([p, f]) == f
    except ValueError:              # another drive (Windows)
        return False


def into_working_folder(path: str, folder: str) -> str:
    """Where a tool writes the file a model named ``path``, given the
    working folder: inside it (a bare name, a sub-path, or an absolute path
    already in it) the path is kept; anywhere else the FILE NAME is kept and
    the directory replaced by the working folder (a tool never writes into
    another conversation's folder)."""
    if not folder or not isinstance(path, str) or not path.strip():
        return path
    folder = os.path.abspath(folder)
    candidate = os.path.abspath(os.path.join(
        folder, os.path.expanduser(path.strip())))
    if _inside(candidate, folder):
        return candidate
    name = os.path.basename(candidate.rstrip("/\\"))
    return os.path.join(folder, name) if name else folder


#: A quoted absolute path (Windows, UNC or POSIX).
_QUOTED = re.compile(
    r"""(?P<q>['"])(?P<p>(?:[A-Za-z]:[\\/]|\\\\|/)[^'"\n]*?)(?P=q)""")
#: An unquoted Windows or UNC path (up to whitespace or a closing bracket).
_WIN = re.compile(r"(?<![\w/\\])(?:[A-Za-z]:[\\/]|\\\\)[^\s'\"<>|()\[\]{}]+")
#: An unquoted POSIX path of two or more parts, not part of a URL.
_POSIX = re.compile(
    r"(?<![\w:/.~\\])/(?:[^\s/'\"<>|()\[\]{}]+/)+[^\s/'\"<>|()\[\]{}]*")


def _name_of(path: str) -> str:
    """The last part of ``path``; a folder's own name for a trailing slash."""
    parts = [p for p in re.split(r"[\\/]+", path.strip()) if p]
    return parts[-1] if parts else "(a folder)"


def scrub_paths(text: Any) -> str:
    """``text`` with every absolute path replaced by its file name (the
    host's ``hide_paths`` first). Never raises."""
    s = "" if text is None else str(text)
    if not s:
        return s
    try:
        hidden = current().hidden(s)
        if isinstance(hidden, str):
            s = hidden
    except Exception:  # noqa: BLE001 - a scrub never fails the caller
        pass
    try:
        s = _QUOTED.sub(lambda m: f"{m.group('q')}{_name_of(m.group('p'))}"
                                  f"{m.group('q')}", s)
        s = _WIN.sub(lambda m: _name_of(m.group(0)), s)
        s = _POSIX.sub(lambda m: _name_of(m.group(0)), s)
    except Exception:  # noqa: BLE001
        pass
    return s


def error_line(exc: BaseException, limit: int = 300) -> str:
    """``"<Type>: <message>"`` on one line, paths scrubbed."""
    msg = " ".join(scrub_paths(str(exc)).split())
    if len(msg) > limit:
        msg = msg[:limit] + " …"
    name = type(exc).__name__
    return f"{name}: {msg}" if msg else name


def tool_error(tool: str, exc: BaseException,
               hint: Optional[str] = None) -> Dict[str, Any]:
    """The result a tool gives instead of raising ``exc``: what failed (no
    server path in it), and that the conversation goes on."""
    return {
        "error": f"{tool or 'the tool'} failed: {error_line(exc)}",
        "hint": hint or (
            "The tool raised an error instead of answering; nothing it was "
            "doing was finished. Try it again once if the cause looks "
            "passing, or another way; tell the user plainly what could not "
            "be done rather than stopping."),
    }


def _key_letters(name: Any) -> str:
    return "".join(ch for ch in str(name).lower() if ch.isalnum())


def suggest_keys(unknown: Any, valid, n: int = 2) -> list:
    """Valid keys closest to an unknown one, as the app's ``suggest_params``
    ranks them for keys without units: the same letters in any case or
    spacing (``'ID'`` -> ``'id'``), then a spelling slip (close at 0.85),
    then the leading words of a longer name (a guess of four letters or
    more). Suggestions only: nothing is renamed."""
    import difflib
    valid = list(valid)
    key = _key_letters(unknown)
    exact = [v for v in valid if _key_letters(v) == key]
    if exact:
        return exact[:n]
    by_key = {_key_letters(v): v for v in valid}
    near = [by_key[k] for k in difflib.get_close_matches(
        key, list(by_key), n=n, cutoff=0.85)]
    if near:
        return near[:n]
    head = str(unknown).lower().strip("_")
    if len(head) >= 4:
        near = sorted((v for v in valid
                       if str(v).lower().startswith(head + "_")),
                      key=lambda v: (len(str(v)), str(v)))
    return near[:n]


def check_nested_keys(item, valid, *, method: str, label: str,
                      hints=None) -> None:
    """Raise a clear ValueError when a nested dict carries a key the tool
    does not read: the unknown key(s), the closest valid key(s) as a
    did-you-mean (``hints`` first, then :func:`suggest_keys`) and the valid
    keys. Nothing is renamed."""
    if not isinstance(item, dict):
        raise ValueError(f"{method}: {label} must be a dict of named values, "
                         f"got {type(item).__name__} {item!r}.")
    unknown = sorted((k for k in item if k not in valid), key=str)
    if not unknown:
        return
    parts = []
    for k in unknown:
        hinted = (hints or {}).get(_key_letters(k))
        if hinted is None:
            near = suggest_keys(k, valid)
        else:
            near = [h for h in hinted if h in valid]
            if hinted and not near:
                near = suggest_keys(k, valid)
        if near:
            parts.append(f"{k} -> {' or '.join(near)}")
    hint = f" Did you mean: {'; '.join(parts)}?" if parts else ""
    raise ValueError(
        f"{method}: {label} has unknown key(s) {unknown}.{hint} "
        f"Valid keys: {sorted(valid)}.")


_IMAGE_MAGIC = (
    (b"\x89PNG\r\n\x1a\n", "png"), (b"\xff\xd8\xff", "jpeg"),
    (b"GIF87a", "gif"), (b"GIF89a", "gif"), (b"II*\x00", "tiff"),
    (b"MM\x00*", "tiff"), (b"BM", "bmp"),
)


def image_kind(data: bytes) -> Optional[str]:
    """The image format the bytes start with, or ``None``."""
    data = bytes(data or b"")
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    for magic, kind in _IMAGE_MAGIC:
        if data[:len(magic)] == magic:
            return kind
    return None


def off_image_note(box, width, height, unit: str = "px",
                   what: str = "image") -> Optional[str]:
    """Why ``box`` (or a point given as a zero-size box) is not on a
    ``width`` x ``height`` image or page -- its centre lies off it -- or
    ``None`` when it is on it."""
    try:
        x0, y0, x1, y1 = (float(v) for v in box)
    except (TypeError, ValueError):
        return None
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    tol = 1.0
    if -tol <= cx <= width + tol and -tol <= cy <= height + tol:
        return None
    return (f"centred at [{cx:.0f}, {cy:.0f}] {unit}, off the {width:.0f} x "
            f"{height:.0f} {unit} {what}")


__all__ = ["ReviewHost", "ImageSupport", "configure", "current", "using",
           "current_attachments", "DEFAULT_SIGNER",
           "DEFAULT_FIND_LIKE_BUDGET_S", "DEFAULT_SCRATCH_DIR",
           "into_working_folder", "scrub_paths", "error_line", "tool_error",
           "suggest_keys", "check_nested_keys", "image_kind",
           "off_image_note"]
