"""The find_like tool: one example box in, candidates + contact sheets out."""

import json
import os

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("cv2")

from planlens.testing.tag_fixtures import build_synthetic_tag_set  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_tag_set(n_pages=2)


@pytest.fixture
def kit(gt, tmp_path):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf,
                      output_dir=str(tmp_path / "img"), max_chars=60000)
    yield k
    k.close()


def call(kit, name, **args):
    return json.loads(kit.call_json(name, args))


def test_find_like_returns_candidates_and_sheets(kit, gt):
    handle = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "find_like", handle=handle, page=0,
               bbox=list(gt.example_bbox))
    assert "error" not in out, out
    assert out["pages_searched"] == "0-1"
    assert all(c["context"] in ("callout", "unanchored") for c in out["candidates"])
    assert [c["id"] for c in out["candidates"]] == list(
        range(1, len(out["candidates"]) + 1))
    n_gce = len([t for t in gt.of("GCE") if t.kind != "legend"])
    assert len(out["candidates"]) >= n_gce
    assert out["legend_hits"]["n"] >= 2 and out["legend_hits"]["pages"] == "0-1"
    assert out["sheets"] and all(os.path.isfile(s["image_path"])
                                 for s in out["sheets"])
    assert out["sheets"][0]["ids"].startswith("1-")
    assert "CANDIDATES" in out["note"]
    callouts = [c for c in out["candidates"] if c["context"] == "callout"]
    assert callouts and all("points_to" in c for c in callouts)


def test_include_legend(kit, gt):
    handle = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "find_like", handle=handle, page=0,
               bbox=list(gt.example_bbox), include_legend=True, pages="0")
    assert any(c["context"] == "legend" for c in out["candidates"])


def test_the_example_can_be_a_box_on_an_earlier_image(kit, gt):
    handle = call(kit, "open_document", source="set.pdf")["handle"]
    page = call(kit, "render_page", handle=handle, page=0)
    x0, y0, x1, y1 = page["clip"]
    w, h = page["width_px"], page["height_px"]
    ex = gt.example_bbox
    box = [(ex[0] - x0) / (x1 - x0) * w - 1, (ex[1] - y0) / (y1 - y0) * h - 1,
           (ex[2] - x0) / (x1 - x0) * w + 1, (ex[3] - y0) / (y1 - y0) * h + 1]
    out = call(kit, "find_like", image=page["image_path"], image_box=box,
               box_units="px", pages="0")
    assert "error" not in out, out
    assert out["handle"] == handle and out["example"]["page"] == 0


def test_find_like_mistakes_are_instructions(kit, gt):
    handle = call(kit, "open_document", source="set.pdf")["handle"]
    assert "needs handle, page and bbox" in call(kit, "find_like",
                                                 handle=handle)["error"]
    assert "bbox must be" in call(kit, "find_like", handle=handle, page=0,
                                  bbox=[1, 2, 3])["error"]
    out = call(kit, "find_like", handle=handle, page=0, bbox=[5, 5, 20, 12])
    assert "no ink" in out["error"] and "tight" in out["hint"]


def test_a_stroke_lettered_page_says_so_when_rendered(kit, gt):
    handle = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "render_page", handle=handle, page=0)
    assert out["text_chars"] == 0 and "text_px" not in out
    assert "not in its text layer" in out["note"]
    assert "find_like" not in out["note"]      # optional, not pushed
