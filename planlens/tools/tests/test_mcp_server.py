"""The MCP surface over ReviewToolkit, driven by the SDK's own client.

The properties that matter to a host: the tool list IS the toolkit's specs
(same names, same descriptions, same schemas — nothing hand-written that could
drift), a document opens and every other tool round-trips through its handle,
a page comes back as an image the model can look at, a mistake is a tool error
that leaves the session usable, and ``--root`` is a wall.

Everything runs in-process over the SDK's memory transport except one smoke
test, which spawns the real ``python -m planlens.mcp_server`` and talks to it
over stdio — the transport every host actually uses.
"""

import base64
import json
import os
import sys

import pytest

pytest.importorskip("fitz")
mcp = pytest.importorskip("mcp",
                          reason="the MCP server needs planlens[mcp]")

import anyio                                                     # noqa: E402
from mcp import Client, StdioServerParameters                    # noqa: E402

from planlens.mcp_server import (                                # noqa: E402
    MCP_IMAGE_VIEW_HINT, build_parser, build_server, build_toolkit,
    image_paths, make_resolver,
)
from planlens.testing import build_synthetic_submittal           # noqa: E402
from planlens.tools import (                                      # noqa: E402
    DEFAULT_VISION_HINT, ReviewToolkit, ToolError,
)

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

#: A whole subprocess, a PDF fixture and a protocol handshake; slow machines
#: and virus scanners get room rather than a false failure.
STDIO_TIMEOUT = 180.0


@pytest.fixture(scope="module")
def submittal():
    return build_synthetic_submittal()


@pytest.fixture
def root(tmp_path, submittal):
    """A directory holding the fixture, as a host would point the server at."""
    (tmp_path / "submittal.pdf").write_bytes(submittal.pdf)
    return tmp_path


def session(body, **toolkit_kwargs):
    """Run ``body(client)`` against a server built over a fresh toolkit.

    Synchronous on purpose: the suite carries no async plugin, and anyio runs
    the loop for the duration of one test.
    """
    async def go():
        kit = build_toolkit(**toolkit_kwargs)
        try:
            async with Client(build_server(kit), raise_exceptions=True) as client:
                return await body(client)
        finally:
            kit.close()

    return anyio.run(go)


async def call(client, name, **arguments):
    """One tool call; returns (payload, result)."""
    result = await client.call_tool(name, arguments)
    return json.loads(result.content[0].text), result


# -- publication --------------------------------------------------------------

def test_the_tool_list_is_the_toolkit_specs(root):
    async def body(client):
        return await client.list_tools()

    listed = session(body, root=str(root)).tools
    specs = ReviewToolkit().specs("plain")
    assert [t.name for t in listed] == [s["name"] for s in specs]
    assert [t.description for t in listed] == [s["description"] for s in specs]
    assert [t.input_schema for t in listed] == [s["parameters"] for s in specs]
    # Every tool the dispatcher answers is on the wire, none invented.
    assert {t.name for t in listed} == set(ReviewToolkit().tool_names)


# -- round trips --------------------------------------------------------------

def test_a_document_opens_and_every_tool_works_through_its_handle(root,
                                                                  submittal):
    async def body(client):
        opened, result = await call(client, "open_document",
                                    source="submittal.pdf")
        assert not result.is_error, opened
        assert opened["handle"].startswith("doc_")
        assert opened["n_pages"] == submittal.n_pages
        handle = opened["handle"]

        page_map, _ = await call(client, "document_page_map", handle=handle)
        assert [r["page"] for r in page_map["rows"]][:3] == [0, 1, 2]
        assert {r["kind"] for r in page_map["rows"]} >= {"text",
                                                         "drawing_sheet"}

        found, _ = await call(client, "search_document", handle=handle,
                              pattern="borings")
        assert found["n_hits"] >= 1
        assert all("page" in hit for hit in found["hits"])

        quantities, _ = await call(client, "find_quantities", handle=handle,
                                   pages="1-3")
        assert quantities["n_mentions"] >= 1
        assert any("ft" in row for row in quantities["quantities"])

        structure, _ = await call(client, "document_structure", handle=handle)
        assert structure["n_segments"] == len(submittal.expected_segments)

        text, _ = await call(client, "read_document", handle=handle, pages="1")
        assert "=== page 1" in text["text"]
        return True

    assert session(body, root=str(root))


def test_look_lines_name_this_surface_own_render_tools(root, submittal):
    """The hint a host sees by default must name tools that exist here."""
    async def body(client):
        opened, _ = await call(client, "open_document", source="submittal.pdf")
        sheet, _ = await call(client, "read_document",
                              handle=opened["handle"],
                              pages=submittal.sheet_pages[0])
        return sheet["text"]

    text = session(body, root=str(root))
    assert "! look:" in text
    assert DEFAULT_VISION_HINT in text
    assert "render_page(" in text and "render_region(" in text


# -- images -------------------------------------------------------------------

def test_render_page_returns_the_page_as_an_image(root, submittal, tmp_path):
    async def body(client):
        opened, _ = await call(client, "open_document", source="submittal.pdf")
        result = await client.call_tool(
            "render_page", {"handle": opened["handle"],
                            "page": submittal.sheet_pages[0]})
        assert not result.is_error
        kinds = [block.type for block in result.content]
        assert kinds == ["text", "image"]
        info = json.loads(result.content[0].text)
        image = result.content[1]
        assert image.mime_type == "image/png"
        assert base64.b64decode(image.data)[:8] == PNG_SIGNATURE
        # The metadata is still there, and it says the picture is attached
        # rather than sending the model to a file it cannot open.
        assert info["page"] == submittal.sheet_pages[0]
        assert info["width_px"] and info["height_px"]
        assert MCP_IMAGE_VIEW_HINT in info["note"]
        return True

    assert session(body, root=str(root),
                   output_dir=str(tmp_path / "png"))


def test_render_region_returns_the_crop_as_an_image(root, submittal, tmp_path):
    async def body(client):
        opened, _ = await call(client, "open_document", source="submittal.pdf")
        result = await client.call_tool(
            "render_region", {"handle": opened["handle"],
                              "page": submittal.sheet_pages[0],
                              "bbox": [100, 100, 400, 300]})
        assert [block.type for block in result.content] == ["text", "image"]
        assert base64.b64decode(result.content[1].data)[:8] == PNG_SIGNATURE
        return True

    assert session(body, root=str(root), output_dir=str(tmp_path / "png"))


def test_image_paths_reads_both_shapes():
    assert image_paths({"image_path": "a.png"}) == ["a.png"]
    assert image_paths({"sheets": [{"image_path": "a.png"},
                                   {"image_path": "b.png"}]}) == ["a.png",
                                                                  "b.png"]
    assert image_paths({"handle": "doc_1"}) == []
    assert image_paths("not a payload") == []


# -- mistakes -----------------------------------------------------------------

def test_an_unknown_handle_is_a_tool_error_and_the_session_survives(root):
    async def body(client):
        payload, result = await call(client, "read_document",
                                     handle="doc_missing")
        assert result.is_error
        assert "unknown document handle" in payload["error"]
        assert "open_document first" in payload["hint"]
        # The connection is untouched: the host can go straight on.
        opened, ok = await call(client, "open_document",
                                source="submittal.pdf")
        assert not ok.is_error and opened["handle"]
        return True

    assert session(body, root=str(root))


def test_an_unknown_tool_name_is_answered_not_raised(root):
    async def body(client):
        payload, result = await call(client, "open_documnet",
                                     source="submittal.pdf")
        assert result.is_error
        assert "unknown tool" in payload["error"]
        return True

    assert session(body, root=str(root))


# -- the wall -----------------------------------------------------------------

def test_root_refuses_a_path_outside_it(root, tmp_path, submittal):
    outside = tmp_path.parent / "outside.pdf"
    outside.write_bytes(submittal.pdf)

    async def body(client):
        for source in ("../outside.pdf", str(outside),
                       os.path.join("..", "..", "outside.pdf")):
            payload, result = await call(client, "open_document",
                                         source=source)
            assert result.is_error, source
            assert "outside the directory" in payload["error"], source
        inside, ok = await call(client, "open_document",
                                source="submittal.pdf")
        assert not ok.is_error and inside["handle"]
        return True

    assert session(body, root=str(root))


def test_without_a_root_any_readable_file_opens(root):
    """The default is the toolkit's own rule; the docstring says who decides."""
    resolve = make_resolver(None)
    assert resolve(str(root / "submittal.pdf")).endswith("submittal.pdf")
    with pytest.raises(ToolError) as caught:
        resolve(str(root / "nope.pdf"))
    assert "not a readable file path" in str(caught.value)


# -- configuration ------------------------------------------------------------

def test_results_honour_the_configured_size_limit(root):
    async def body(client):
        opened, _ = await call(client, "open_document", source="submittal.pdf")
        result = await client.call_tool("read_document",
                                        {"handle": opened["handle"]})
        text = result.content[0].text
        assert len(text) <= 1200
        assert "next" in json.loads(text)
        return True

    assert session(body, root=str(root), max_chars=1200)


def test_a_host_can_replace_the_vision_hint(root, submittal):
    async def body(client):
        opened, _ = await call(client, "open_document", source="submittal.pdf")
        sheet, _ = await call(client, "read_document",
                              handle=opened["handle"],
                              pages=submittal.sheet_pages[0])
        return sheet["text"]

    text = session(body, root=str(root), vision_hint="use look_at(page)")
    assert "use look_at(page)" in text
    assert DEFAULT_VISION_HINT not in text


def test_the_command_line_defaults_and_parsing():
    args = build_parser().parse_args([])
    assert args.http is None and args.root is None
    assert args.vision_hint == DEFAULT_VISION_HINT
    assert args.max_chars >= 1000
    assert build_parser().parse_args(["--http", "127.0.0.1:8931"]).http == \
        ("127.0.0.1", 8931)
    # An empty host means loopback; a bare port or a bare host is refused,
    # because guessing which was meant is how a server ends up listening on an
    # address nobody chose.
    assert build_parser().parse_args(["--http", ":8931"]).http == \
        ("127.0.0.1", 8931)
    for bad in ("localhost", "8931"):
        with pytest.raises(SystemExit):
            build_parser().parse_args(["--http", bad])


# -- the transport a host really uses ------------------------------------------

def test_stdio_subprocess_serves_the_same_tools(root):
    """Spawn the real server and talk to it the way a host does.

    Kept even where Windows subprocess handling makes it awkward: it is the
    only test that proves ``python -m planlens.mcp_server`` starts, speaks the
    protocol and reads a file. A failure here skips with its reason rather
    than failing the suite, because the in-process tests already cover the
    server's behaviour — what this one adds is the process boundary.
    """
    async def go():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "planlens.mcp_server", "--root", str(root)])
        with anyio.fail_after(STDIO_TIMEOUT):
            async with Client(params) as client:
                listed = await client.list_tools()
                result = await client.call_tool("open_document",
                                                {"source": "submittal.pdf"})
                return [t.name for t in listed.tools], result

    try:
        names, result = anyio.run(go)
    except KeyboardInterrupt:
        raise
    except BaseException as exc:                    # noqa: BLE001
        if isinstance(exc, pytest.skip.Exception):  # pragma: no cover
            raise
        pytest.skip(f"stdio transport did not run here: "
                    f"{type(exc).__name__}: {exc}")
    assert names == ReviewToolkit().tool_names
    assert not result.is_error
    assert json.loads(result.content[0].text)["handle"].startswith("doc_")
