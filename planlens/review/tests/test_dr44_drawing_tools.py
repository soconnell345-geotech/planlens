"""BACKLOG DR-44 in the host layer: the drawing tools for a host that asks.

``drawing_geometry`` is offered only to a host built with
``ReviewHost(toolkit_options={"drawing_tools": True})``, after every other
group; every other host is offered, specified and answered what it was.
"""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import conversation, specs  # noqa: E402
from planlens.review.host import ReviewHost, using  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing import construct_fixtures as cf  # noqa: E402

DRAWING = ("drawing_geometry",)
BEFORE = specs.DOCUMENT_TOOLS + specs.OPTIONAL_DOCUMENT_TOOLS
ASK = {"drawing_tools": True}
GROUPS = [dict(look=look, sheet_tools=sheet, plan_tools=plan)
          for look in (False, True) for sheet in (False, True)
          for plan in (False, True)]


@pytest.fixture(scope="module")
def dims(tmp_path_factory):
    path, _gt = cf.build_synthetic_dimension_pdf(
        tmp_path_factory.mktemp("dr44"))
    with open(path, "rb") as fh:
        return fh.read()


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def test_a_host_that_did_not_ask_is_offered_and_specified_what_it_was(dims):
    assert specs.DRAWING_TOOLS == DRAWING
    assert not set(DRAWING) & set(specs.HOST_TOOLS + specs.SHEET_TOOLS
                                  + specs.PLAN_TOOLS)
    for group in GROUPS:
        names = specs.host_tool_names(**group)
        assert not set(DRAWING) & set(names)
        assert specs.host_tool_names(drawing_tools=False, **group) == names
        for style in ("plain", "openai"):
            plain = specs.host_specs(style, **group)
            asked = specs.host_specs(style, drawing_tools=True, **group)
            # asking adds one spec, last, and changes no other
            assert asked[:-1] == plain
            assert len(asked) == len(plain) + 1
    tools = make_tools(None, None, {"dims.pdf": dims})
    assert tuple(tools) == BEFORE == tuple(review.tool_names())
    assert [s["name"] for s in review.tool_specs()] == list(BEFORE)
    refused = json.loads(review.call(DRAWING[0], {"handle": "dims.pdf",
                                                  "page": 0},
                                     {"dims.pdf": dims}))
    assert refused["error"] == f"unknown tool '{DRAWING[0]}'"
    assert DRAWING[0] not in refused["hint"]


def test_a_host_that_asks_is_offered_it_last():
    make_tools(None, None, {}, toolkit_options={**ASK, "plan_tools": True})
    assert tuple(review.tool_names()) == BEFORE + specs.PLAN_TOOLS + DRAWING
    assert review.tool_specs("openai")[-1]["function"]["name"] == DRAWING[0]


def test_its_spec_is_the_toolkits_words_with_pdf_page():
    from planlens.tools.specs import DRAWING_TOOL_SPECS
    kit = {s["name"]: s for s in DRAWING_TOOL_SPECS}["drawing_geometry"]
    (shown,) = [s for s in specs.host_specs(look=False, drawing_tools=True)
                if s["name"] == "drawing_geometry"]
    assert shown["description"] == kit["description"] + specs.PDF_PAGE_NOTE
    schema = shown["input_schema"]
    assert list(schema["properties"]) == ["handle", "page", "find", "bbox",
                                          "pdf_page"]
    assert schema["required"] == ["handle"]
    for arg in specs.DRAWING_SHOWN_FROM_TOOLKIT["drawing_geometry"]:
        want = dict(kit["parameters"]["properties"][arg], default=None,
                    title=specs._title(arg))
        assert schema["properties"][arg] == want, arg


def test_through_the_host_it_reads_the_drawing_with_both_page_numbers(dims):
    att = {"dims.pdf": dims}
    tools = make_tools(None, None, att, toolkit_options=ASK)
    out = _invoke(tools["drawing_geometry"], handle="dims.pdf", pdf_page=1,
                  find=["dimensions"])
    assert "error" not in out, out
    assert out["page"] == 0
    assert "drawn geometry" in out["measured_from"]
    assert {d["value"] for d in out["dimensions"]} == {"8'-0\"", "3.5 m"}
    bad = _invoke(tools["drawing_geometry"], handle="dims.pdf", pdf_page=4)
    assert "error" in bad


def test_the_calling_host_decides_whether_it_is_served(dims):
    """Hosts with no working folder share one conversation and its
    toolkit: whichever host built it, a host that asked is served the
    drawing tool and a host that did not is refused it by name."""
    att = {"dims.pdf": dims}
    plain, asked = ReviewHost(), ReviewHost(toolkit_options=ASK)
    for first in (plain, asked):
        conversation._SPACES.clear()
        handle = json.loads(review.call("open_document",
                                        {"source": "dims.pdf"}, att,
                                        host=first))["handle"]
        served = json.loads(review.call("drawing_geometry",
                                        {"handle": handle, "page": 0}, att,
                                        host=asked))
        assert served["counts"]["dimensions"] == 2, served
        with using(plain):
            raw = conversation.dispatch_document_tool(
                "drawing_geometry", {"handle": handle, "page": 0}, att)
        refused = json.loads(raw)
        assert refused["error"] == "unknown tool 'drawing_geometry'"
        assert "drawing_geometry" not in refused["hint"]
        with using(asked):
            out = json.loads(conversation.dispatch_document_tool(
                "no_such_tool", {"handle": handle}, att))
        assert "drawing_geometry" in out["hint"]
        assert len(conversation._SPACES) == 1
