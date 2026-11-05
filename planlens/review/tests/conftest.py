"""Shared helpers for the planlens.review tests (PL-29 (a)).

The app's tests built its agent's tools (``make_vision_tools``) with a fake
vision engine and a working folder bound for the turn. Here the same tests
run against :func:`planlens.review.call` with a :class:`ReviewHost` holding
a fake look (an engine's ``analyze_image`` wrapped as ``look``), the test's
folder as the working folder, and the conversation-relative naming the app's
host gives results (``hide_paths`` / ``display_path``).
"""

import json
import os

import pytest

from planlens import review
from planlens.review import markup_check


def _close_toolkits():
    """Every conversation's toolkit closed and forgotten, so a test sees the
    toolkit its own host builds (a conversation's toolkit outlives a host
    change, as it outlives a turn)."""
    from planlens.review import conversation
    with conversation._SPACES_LOCK:
        spaces = list(conversation._SPACES.values())
        conversation._SPACES.clear()
    for space in spaces:
        if space.kit is not None:
            conversation._close_quietly(space.kit)


@pytest.fixture(autouse=True)
def _fresh_host():
    review.configure()
    markup_check.clear_kept_verdicts()
    _close_toolkits()
    yield
    review.configure()
    markup_check.clear_kept_verdicts()
    _close_toolkits()


def look_of(engine):
    """An engine with ``analyze_image(image, prompt)`` as a look; ``None``
    (no engine, or one that cannot see) as no look."""
    if engine is None:
        return None
    if callable(engine) and not hasattr(engine, "analyze_image"):
        return engine
    if not hasattr(engine, "analyze_image"):
        return None
    return lambda image, question, kind: engine.analyze_image(image, question)


def _relative(folder):
    folder = os.path.abspath(str(folder))

    def shown(path):
        if not isinstance(path, str):
            return path
        a = os.path.abspath(path)
        if a == folder:
            return "."
        if a.startswith(folder + os.sep):
            return os.path.relpath(a, folder).replace(os.sep, "/")
        return path

    def hide(obj):
        if isinstance(obj, dict):
            return {k: hide(v) for k, v in obj.items()}
        if isinstance(obj, list):
            return [hide(v) for v in obj]
        if isinstance(obj, str):
            if obj == folder:
                return "."
            return obj.replace(folder + os.sep, "").replace(folder, "the "
                                                           "working folder")
        return obj

    return shown, hide


def confined(folder):
    """A resolver for one conversation's files, as the app's confinement
    resolves them: a name inside ``folder`` (or an absolute path inside it)
    to its path; anything outside refused with the conversation's own file
    names, never a server path."""
    from planlens.review.host import current_attachments
    from planlens.tools import ToolError
    root = os.path.realpath(str(folder))

    def files_here():
        try:
            names = sorted(n for n in os.listdir(root)
                           if not n.startswith("."))
        except OSError:
            names = []
        return ", ".join(names) or "none"

    def resolve(source):
        src = str(source or "")
        path = os.path.realpath(os.path.join(root, os.path.expanduser(src)))
        inside = os.path.commonpath([path, root]) == root
        if not inside:
            raise ToolError(
                f"'{os.path.basename(src)}' is outside this conversation's "
                "files", hint=f"files here: {files_here()}")
        if os.path.isfile(path):
            return path
        raise ToolError(
            f"'{src}' is not an attachment key, a document handle this "
            "conversation opened, or a file in this conversation's working "
            "folder",
            hint=(f"attachment keys: {sorted(current_attachments()) or 'none'}"
                  f"; files here: {files_here()}"))

    return resolve


def configure_host(folder=None, engine=None, **kw):
    """Install a host for a test: ``folder`` as the working folder (with the
    app's relative naming), ``engine`` as the look."""
    fields = dict(kw)
    if "look" not in fields:
        fields["look"] = look_of(engine)
    if folder is not None:
        shown, hide = _relative(folder)
        fields.setdefault("working_folder", lambda: str(folder))
        fields.setdefault("display_path", shown)
        fields.setdefault("hide_paths", hide)
        fields.setdefault("verdict_key", lambda: str(folder))
        resolver = confined(folder)
        fields.setdefault("resolve_source", resolver)

        def readable(name, _r=resolver):
            try:
                return _r(name)
            except Exception:  # noqa: BLE001 - not a file here
                return None

        fields.setdefault("readable_file", readable)
    return review.configure(review.ReviewHost(**fields))


class Tool:
    """A host tool with the ``invoke(dict)`` the app's tools had."""

    def __init__(self, name, attachments, max_chars=None):
        self.name = name
        self.attachments = attachments
        self.max_chars = max_chars

    def invoke(self, arguments):
        return review.call(self.name, dict(arguments), self.attachments,
                           self.max_chars)


def make_tools(engine=None, folder=None, attachments=None, max_chars=12000,
               **host):
    """``{name: Tool}`` for every tool the configured host offers."""
    configure_host(folder, engine, **host)
    att = attachments if attachments is not None else {}
    return {n: Tool(n, att, max_chars) for n in review.tool_names()}


def open_handle(tools, source):
    out = json.loads(tools["open_document"].invoke({"source": source}))
    assert "handle" in out, out
    return out["handle"]
