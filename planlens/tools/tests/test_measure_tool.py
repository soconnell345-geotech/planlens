"""The ``measure`` tool: listing, measuring, image boxes, refusals, size."""

import json

import pytest

from planlens.tools import ReviewToolkit
from planlens.testing.visual_scale_fixtures import (
    LogVariant, PlotVariant, build_log, build_plot,
)


@pytest.fixture(scope="module")
def log_fx():
    return build_log(LogVariant("tool_log", skew_deg=0.3, seed=81))


def _kit(fx, **kw):
    kit = ReviewToolkit(resolve_source=lambda key: fx.pdf, **kw)
    handle = kit.call("open_document", {"source": "doc.pdf"})["handle"]
    return kit, handle


def test_measure_is_published_with_its_schema():
    kit = ReviewToolkit()
    assert "measure" in kit.tool_names
    spec = next(s for s in kit.specs("plain") if s["name"] == "measure")
    props = spec["parameters"]["properties"]
    assert set(spec["parameters"]["required"]) == {"handle", "page"}
    assert props["kind"]["enum"] == ["line", "lines", "point", "edge",
                                     "curve", "distance", "text"]
    assert "needs_values" in spec["description"]


def test_no_box_lists_the_scales_and_what_they_wait_for(log_fx):
    kit, h = _kit(log_fx)
    out = json.loads(kit.call_json("measure", {"handle": h, "page": 0}))
    assert out["scales"]["frames"][0]["id"] == "p0.depth"
    assert out["scales"]["needs_values"][0]["labels"]


def test_a_box_and_label_values_give_a_depth(log_fx):
    kit, h = _kit(log_fx)
    inv = kit.call("measure", {"handle": h, "page": 0})
    labels = inv["scales"]["needs_values"][0]["labels"]
    vals = log_fx.values_for(labels)
    rd = log_fx.readings[2]
    out = json.loads(kit.call_json("measure", {
        "handle": h, "page": 0, "bbox": list(rd.box_pt), "kind": "line",
        "values": vals, "pad": 4}))
    v = out["value"]
    assert abs(v["depth"] - rd.value) <= v["plus_minus"]
    assert out["scale"]["values_from"].startswith("values supplied")
    assert out["handle"] == h


def test_an_image_box_from_a_zoom(log_fx):
    kit, h = _kit(log_fx)
    vals = log_fx.values_for(kit.call("measure", {"handle": h, "page": 0})
                             ["scales"]["needs_values"][0]["labels"])
    rd = log_fx.readings[1]
    x, y = rd.at_pt
    zoom = kit.call("render_region", {"handle": h, "page": 0,
                                      "bbox": [x - 40, y - 20, x + 40,
                                               y + 20], "pad_frac": 0.0})
    clip = zoom["clip"]
    sx = zoom["width_px"] / (clip[2] - clip[0])
    sy = zoom["height_px"] / (clip[3] - clip[1])
    box_px = [(x - 25 - clip[0]) * sx, (y - 1.5 - clip[1]) * sy,
              (x + 25 - clip[0]) * sx, (y + 1.5 - clip[1]) * sy]
    out = kit.call("measure", {"handle": h, "page": 0,
                               "image": zoom["image_path"],
                               "image_box": box_px, "box_units": "px",
                               "kind": "line", "values": vals})
    assert abs(out["value"]["depth"] - rd.value) <= out["value"]["plus_minus"]
    assert out["pad_pt"] == pytest.approx(2.0)


def test_mistakes_come_back_as_instructions(log_fx):
    kit, h = _kit(log_fx)
    bad = kit.call("measure", {"handle": h, "page": 0, "bbox": [1, 2, 3, 4],
                               "kind": "curve"})
    assert "at=" in bad["error"]
    bad = kit.call("measure", {"handle": h, "page": 0, "bbox": [1, 2, 3, 4],
                               "kind": "distance"})
    assert "to=" in bad["error"]
    bad = kit.call("measure", {"handle": h, "page": 0, "bbox": [1, 2, 3],
                               "kind": "line"})
    assert "bbox" in bad["error"]
    bad = kit.call("measure", {"handle": h, "page": 0, "kind": "wiggle"})
    assert "kind must be" in bad["error"]


@pytest.mark.parametrize("max_chars", [1000, 2500, 7500])
def test_every_measure_result_fits_the_limit(max_chars):
    fx = build_plot(PlotVariant("tool_plot", "grading"))
    kit, h = _kit(fx, max_chars=max_chars)
    calls = [{"handle": h, "page": 0},
             {"handle": h, "page": 0, "bbox": [110, 300, 520, 620],
              "kind": "lines"},
             {"handle": h, "page": 0, "bbox": list(fx.readings[3].box_pt),
              "kind": "point", "pad": 2}]
    for args in calls:
        text = kit.call_json("measure", args)
        assert len(text) <= max_chars, (args, len(text))
        assert "error" not in json.loads(text), text[:300]
