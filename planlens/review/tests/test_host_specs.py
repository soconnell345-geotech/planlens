"""The host layer's tool specs equal the app's, word for word (PL-29 (a)).

``app_tool_specs.json`` is the app's own capture of the moved tools (name,
description, input schema), written by ``capture_app_specs.py`` from the
app's ``make_vision_tools``. planlens builds the same specs from its own
sources (:mod:`planlens.review.specs`); any difference is a change the app's
agents would see.
"""

import json
import os

import pytest

from planlens.review import specs as host_specs
from planlens.tools import ReviewToolkit

HERE = os.path.dirname(os.path.abspath(__file__))

with open(os.path.join(HERE, "app_tool_specs.json"), encoding="utf-8") as fh:
    APP = json.load(fh)

MINE = {s["name"]: s for s in host_specs.host_specs()}


def test_the_fixture_holds_every_moved_tool():
    assert list(APP["tools"]) == list(host_specs.HOST_TOOLS) or \
        set(APP["tools"]) == set(host_specs.HOST_TOOLS)
    assert set(MINE) == set(APP["tools"])


@pytest.mark.parametrize("name", sorted(APP["tools"]))
def test_description_word_for_word(name):
    assert MINE[name]["description"] == APP["tools"][name]["description"]


@pytest.mark.parametrize("name", sorted(APP["tools"]))
def test_input_schema_word_for_word(name):
    assert MINE[name]["input_schema"] == APP["tools"][name]["input_schema"]


def test_the_markups_schema_follows_the_toolkit():
    shown = host_specs.annotate_markups_schema()
    fields = shown["items"]["properties"]
    assert "author" not in fields and fields["pdf_page"]["minimum"] == 1
    assert shown["items"]["additionalProperties"] is False


def test_look_tools_are_left_out_without_a_look():
    assert "find_like" in host_specs.host_tool_names(look=True)
    assert "find_like" not in host_specs.host_tool_names(look=False)
    names = [s["name"] for s in host_specs.host_specs(look=False)]
    assert "find_like" not in names and "annotate_document" in names


def test_openai_style_carries_the_same_schema():
    out = host_specs.host_specs("openai")
    by = {s["function"]["name"]: s["function"] for s in out}
    assert by["find_like"]["parameters"] == MINE["find_like"]["input_schema"]
    with pytest.raises(ValueError):
        host_specs.host_specs("xml")


def test_the_toolkit_takes_a_look_next_to_its_resolver():
    seen = []
    kit = ReviewToolkit(resolve_source=lambda s: s,
                        look=lambda image, question, kind:
                        seen.append(kind) or "text")
    assert kit.look(b"png", "what is here?", "check") == "text"
    assert seen == ["check"]
    plain = ReviewToolkit()
    assert plain.look is None
    assert plain.tool_names == kit.tool_names      # none of its own looks
    with pytest.raises(TypeError):
        ReviewToolkit(look="not callable")


def test_a_toolkit_look_tool_is_offered_only_with_a_look():
    class Looking(ReviewToolkit):
        LOOK_TOOLS = ("find_like",)

    assert "find_like" not in Looking().tool_names
    assert "find_like" not in [s["name"] for s in Looking().specs()]
    assert "find_like" in Looking(look=lambda i, q, k: "").tool_names
