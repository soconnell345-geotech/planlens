"""planlens over the Model Context Protocol — the same tools, any MCP host.

:mod:`planlens.tools` is already framework-neutral: JSON-Schema specs plus a
dispatcher that returns valid JSON inside a size limit. What it is not is
*discoverable*. A host has to be programmed against it. MCP is the wire that
removes that step: a host process (Claude Desktop, Claude Code, an editor, a
LangChain or deepagents program through ``langchain-mcp-adapters``) launches
this server, asks it what tools it has, and calls them — no planlens-specific
integration code anywhere.

So the tool list here is GENERATED from :meth:`ReviewToolkit.specs`, never
written out a second time. Names, descriptions and input schemas are the ones
the toolkit publishes; a tool added to :mod:`planlens.tools.specs` appears on
this surface with no edit to this file, and the two can never drift.

Two things the wire adds:

* **Images.** ``render_page`` / ``render_region`` / ``render_page_thumbnails``
  write a PNG and return its path. Over MCP the picture itself travels: every
  image a result names is attached as image content, so a host whose model can
  see gets the page without touching the filesystem. The path stays in the
  JSON for hosts that share one. The toolkit is therefore built with an
  ``image_view_hint`` that says the image is attached, not that a file is
  waiting to be opened.
* **Vision instructions that match this surface.** ``! look:`` lines carry the
  HOST's way of viewing a page. Here the render tools are on the same
  connection as everything else, so the default hint names them — an app that
  hands pages to its own vision tool passes ``--vision-hint`` instead.

Security. This server opens the files the caller names, with the privileges of
whoever launched it, and ``annotate_document`` WRITES one. It performs no
authentication of its own: the host that starts it decides who may run it.
``--root`` narrows the blast radius to one directory tree in both directions
(relative ``source`` and ``output_path`` alike resolve against it, anything
resolving outside it is refused), and a shared HTTP deployment needs
authentication from the platform in front of it — see the README.

Run it::

    python -m planlens.mcp_server            # stdio, the usual case
    planlens-mcp --root ./documents          # console script, one directory
    planlens-mcp --http 127.0.0.1:8931       # streamable HTTP
"""

from __future__ import annotations

import argparse
import base64
import functools
import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from planlens.tools import (
    DEFAULT_MAX_CHARS, DEFAULT_VISION_HINT, ReviewToolkit, ToolError,
)

_NEEDS_MCP = ('the MCP server needs the optional package mcp: '
              'pip install "planlens[mcp]"')

try:
    import anyio
    from mcp import types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
except ImportError as exc:      # pragma: no cover - the guard, not the server
    raise ImportError(_NEEDS_MCP) from exc

#: What the model is told about a PNG this server returns. The toolkit's own
#: default sends it to a file on disk; here the image is in the result.
MCP_IMAGE_VIEW_HINT = "the image is attached to this result — look at it"

SERVER_NAME = "planlens"

#: The first bytes of every JPEG file (a render made with image_format="jpeg").
JPEG_MAGIC = bytes([0xFF, 0xD8, 0xFF])


# -- the files the caller may name --------------------------------------------
def make_resolver(root: Optional[str] = None) -> Callable[[str], str]:
    """Map a ``source`` string to a readable path, optionally confined to root.

    Without a root this is the toolkit's own rule — any file the process can
    read — and the host is the only thing standing between a caller and the
    filesystem. With one, a relative source resolves against it and anything
    that resolves outside it (``../``, an absolute path elsewhere, a symlink
    pointing out) is refused as a tool error the model can act on, not an
    exception.
    """
    if root is None:
        def anywhere(source: str) -> str:
            if os.path.isfile(source):
                return source
            raise ToolError(f"'{source}' is not a readable file path",
                            hint="pass the path of a PDF the host can read")
        return anywhere

    base = os.path.realpath(root)

    def inside(source: str) -> str:
        # join() lets an absolute source through unchanged; realpath() then
        # resolves it, and the prefix test below is what refuses it. Symlinks
        # are resolved before the test for the same reason.
        path = os.path.realpath(os.path.join(base, source))
        if path != base and not path.startswith(base + os.sep):
            raise ToolError(
                f"'{source}' is outside the directory this server may read",
                hint=f"pass a path inside {base}; relative paths resolve "
                     f"against it")
        if not os.path.isfile(path):
            raise ToolError(f"'{source}' is not a readable file path",
                            hint=f"paths are relative to {base}")
        return path

    return inside


def build_toolkit(max_chars: int = DEFAULT_MAX_CHARS,
                  vision_hint: Optional[str] = None,
                  root: Optional[str] = None,
                  output_dir: Optional[str] = None) -> ReviewToolkit:
    """The toolkit this server publishes, configured for an MCP host."""
    return ReviewToolkit(
        resolve_source=make_resolver(root),
        max_chars=max_chars,
        vision_hint=vision_hint if vision_hint is not None
        else DEFAULT_VISION_HINT,
        image_view_hint=MCP_IMAGE_VIEW_HINT,
        output_dir=output_dir,
        # A root that confines reading confines writing too, or the one tool
        # that writes a file would step straight out of it.
        output_root=root)


# -- results ------------------------------------------------------------------
def image_paths(payload: Any) -> List[str]:
    """Every image a tool result names, in the order it names them.

    One rule for every tool instead of a per-tool table: a result that names
    an image file gets that image attached. ``render_page`` and
    ``render_region`` name one; ``render_page_thumbnails`` names one contact
    sheet per 48 pages; everything else names none.
    """
    if not isinstance(payload, dict):
        return []
    out = []
    top = payload.get("image_path")
    if isinstance(top, str):
        out.append(top)
    for sheet in payload.get("sheets") or []:
        if isinstance(sheet, dict) and isinstance(sheet.get("image_path"), str):
            out.append(sheet["image_path"])
    return out


def _image_content(path: str) -> Optional["types.ImageContent"]:
    try:
        with open(path, "rb") as fh:
            data = fh.read()
    except OSError:
        # The metadata still names the path; a missing file is not worth
        # failing a result the model can otherwise use.
        return None
    mime = "image/jpeg" if data.startswith(JPEG_MAGIC) else "image/png"
    return types.ImageContent(type="image", mime_type=mime,
                              data=base64.b64encode(data).decode("ascii"))


def tool_result(text: str) -> "types.CallToolResult":
    """One toolkit result as MCP content: the JSON, plus any image it names.

    ``call_json`` never raises and never exceeds the host's size limit: a
    mistake comes back as ``{"error", "hint"}``, which is the model's
    instruction for fixing it. That is exactly what an MCP tool error is for,
    so it travels as one — ``is_error`` set, the message intact.
    """
    try:
        payload = json.loads(text)
    except ValueError:           # pragma: no cover - call_json guarantees JSON
        payload = {}
    content: List[Any] = [types.TextContent(type="text", text=text)]
    for path in image_paths(payload):
        block = _image_content(path)
        if block is not None:
            content.append(block)
    return types.CallToolResult(
        content=content,
        is_error=isinstance(payload, dict) and "error" in payload)


# -- the server ---------------------------------------------------------------
def build_server(toolkit: ReviewToolkit, name: str = SERVER_NAME) -> "Server":
    """An MCP server publishing ``toolkit``'s tools, generated from its specs."""
    from planlens import __version__

    tools = [types.Tool(name=spec["name"],
                        description=spec["description"],
                        input_schema=spec["parameters"])
             for spec in toolkit.specs("plain")]

    async def on_list_tools(ctx: Any, params: Any) -> "types.ListToolsResult":
        return types.ListToolsResult(tools=tools)

    async def on_call_tool(ctx: Any, params: Any) -> "types.CallToolResult":
        arguments: Dict[str, Any] = dict(params.arguments or {})
        work = functools.partial(toolkit.call_json, params.name, arguments)
        try:
            # The document layer is synchronous and CPU-bound (PyMuPDF), so a
            # long render must not hold the server's event loop.
            text = await anyio.to_thread.run_sync(work)
        except Exception as exc:  # pragma: no cover - the toolkit handles its own
            # Nothing reaches the host as a crashed connection: an unexpected
            # failure is reported as this tool's error and the session lives.
            text = json.dumps({"error": f"{params.name} failed: "
                                        f"{type(exc).__name__}: {exc}"})
        return tool_result(text)

    return Server(name, version=__version__,
                  title="planlens document review",
                  instructions=(
                      "Review any AEC document — report, drawing set, "
                      "submittal, calculation package, scan. Call "
                      "open_document first for a map of the whole document "
                      "and a handle; every other tool takes that handle. "
                      "Pages are 0-based and coordinates are PDF points in "
                      "the displayed page frame (top-left origin, y down), "
                      "which is the frame a rendered page image uses. A "
                      "result line starting '! look:' means the page's "
                      "content is not in its text: render it and look."),
                  on_list_tools=on_list_tools,
                  on_call_tool=on_call_tool)


async def serve_stdio(server: "Server") -> None:
    """Serve one stdio connection until the host closes it."""
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream,
                         server.create_initialization_options())


def serve_http(server: "Server", host: str, port: int) -> None:
    """Serve streamable HTTP. Authentication is the platform's job, not this
    server's: anything beyond localhost needs a gateway in front of it."""
    try:
        import uvicorn
    except ImportError as exc:   # pragma: no cover - uvicorn ships with mcp
        raise SystemExit("--http needs uvicorn: pip install uvicorn") from exc
    uvicorn.run(server.streamable_http_app(host=host), host=host, port=port)


# -- command line -------------------------------------------------------------
def _parse_address(value: str) -> "tuple[str, int]":
    host, sep, port = value.rpartition(":")
    if not sep or not port.isdigit():
        raise argparse.ArgumentTypeError("expected HOST:PORT, e.g. "
                                         "127.0.0.1:8931")
    return (host or "127.0.0.1", int(port))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="planlens-mcp",
        description="Serve planlens' document-review tools over MCP.")
    parser.add_argument(
        "--max-chars", type=int, default=DEFAULT_MAX_CHARS,
        help="ceiling on every result, to match the host's own limit "
             f"(default {DEFAULT_MAX_CHARS})")
    parser.add_argument(
        "--vision-hint", default=DEFAULT_VISION_HINT,
        help="what a '! look:' line tells the model to do to see a page. The "
             "default names this server's own render_page / render_region "
             "tools; pass your host's vision tool instead if it has one.")
    parser.add_argument(
        "--root", default=None,
        help="confine reading to this directory: relative source paths "
             "resolve against it and anything outside it is refused. "
             "Without it, any file this process can read can be opened.")
    parser.add_argument(
        "--http", metavar="HOST:PORT", type=_parse_address, default=None,
        help="serve streamable HTTP instead of stdio. This server provides "
             "no authentication; a shared deployment needs it from the "
             "platform in front.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.root is not None and not os.path.isdir(args.root):
        raise SystemExit(f"--root: not a directory: {args.root}")
    if args.max_chars < 1000:
        raise SystemExit("--max-chars: below 1000 cannot hold a useful result")
    toolkit = build_toolkit(max_chars=args.max_chars,
                            vision_hint=args.vision_hint,
                            root=args.root)
    server = build_server(toolkit)
    try:
        if args.http:
            serve_http(server, *args.http)
        else:
            anyio.run(serve_stdio, server)
    except KeyboardInterrupt:    # pragma: no cover - a host closing the pipe
        pass
    finally:
        toolkit.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
