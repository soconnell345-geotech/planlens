"""BACKLOG DR-44 (with PL-13): ``drawing_geometry``, one page's drafting
constructs read from its drawn geometry, offered only by a toolkit built
with ``drawing_tools=True``.

Every drawing here is made up and drawn by the test (the construct
fixtures); every expected point is where the fixture drew it, in the
displayed-page frame the other tools use.
"""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing import construct_fixtures as cf  # noqa: E402
from planlens.testing.leader_fixtures import (  # noqa: E402
    PAGE_HEIGHT, PAGE_WIDTH, _arrowhead_at, _draw_filled_triangle,
    _draw_shaft)
from planlens.tools import (  # noqa: E402
    DRAWING_TOOL_SPECS, SHEET_TOOL_SPECS, TOOL_SPECS, ReviewToolkit)

NAME = "drawing_geometry"


def _kit(**kw):
    return ReviewToolkit(drawing_tools=True, **kw)


def _open(kit, path):
    out = kit.call("open_document", {"source": str(path)})
    assert "handle" in out, out
    return out["handle"]


def _near(p, q, tol=1.0):
    return abs(p[0] - q[0]) <= tol and abs(p[1] - q[1]) <= tol


def _save(doc, path):
    doc.save(str(path))
    doc.close()
    return str(path)


# -- offered only when asked ---------------------------------------------------

def test_a_toolkit_that_did_not_ask_is_offered_and_specified_what_it_was():
    for kw in ({}, {"sheet_tools": True}):
        plain = ReviewToolkit(**kw)
        asked = ReviewToolkit(drawing_tools=True, **kw)
        assert NAME not in plain.tool_names
        for style in ("anthropic", "openai", "plain"):
            # asking adds one spec, last, and changes no other
            assert asked.specs(style)[:-1] == plain.specs(style)
        assert asked.tool_names == plain.tool_names + [NAME]
    # the list every toolkit has is the one it had
    assert ReviewToolkit().tool_names == [s["name"] for s in TOOL_SPECS]
    assert not {s["name"] for s in DRAWING_TOOL_SPECS} & {
        s["name"] for s in TOOL_SPECS + SHEET_TOOL_SPECS}
    refused = ReviewToolkit().call(NAME, {"handle": "x", "page": 0})
    assert refused["error"] == f"unknown tool '{NAME}'"


# -- what it reads -------------------------------------------------------------

def test_dimensions_with_their_values_and_ends_in_the_page_frame(tmp_path):
    path, gt = cf.build_synthetic_dimension_pdf(tmp_path)
    kit = _kit()
    out = kit.call(NAME, {"handle": _open(kit, path), "page": 0,
                          "find": ["dimensions"]})
    assert "error" not in out, out
    assert "drawn geometry" in out["measured_from"]
    assert "not a picture" in out["measured_from"]
    assert "top-left" in out["frame"]
    assert out["sheet"]["is"].startswith("a vector drawing:")
    assert list(out["counts"]) == ["dimensions"]
    assert "leaders" not in out
    want = {"8'-0\"": ((150, 120), (400, 120), 250.0),
            "3.5 m": ((600, 200), (600, 450), 250.0)}
    got = {d["value"]: d for d in out["dimensions"]}
    assert set(want) <= set(got)
    for value, (a, b, length) in want.items():
        d = got[value]
        assert {tuple(d["from"]), tuple(d["to"])} == {a, b}
        assert d["length_pt"] == length
        assert 0 < d["confidence"] <= 1
    # the single-arrow decoy is not a dimension
    assert len(out["dimensions"]) == 2
    assert any("length_pt" in n for n in out["notes"])


def test_leaders_callouts_clouds_and_the_title_block(tmp_path):
    kit = _kit()
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    out = kit.call(NAME, {"handle": _open(kit, path), "page": 0,
                          "find": "leaders"})
    # the decoy leader, by its tip; no dimension arrow counted as a leader
    assert [(r["text"], r["tip"]) for r in out["leaders"]] == [
        ("TYP", [250.0, 520.0])]

    path, gt = cf.build_synthetic_bubble_pdf(tmp_path)
    out = kit.call(NAME, {"handle": _open(kit, path), "page": 0,
                          "find": ["callouts"]})
    found = {(r["text"], tuple(r["center"])) for r in out["callouts"]}
    for b in gt["bubbles"]:
        cx, cy = b["center"]
        assert (b["text"], (cx, PAGE_HEIGHT - cy)) in found

    path, gt = cf.build_synthetic_cloud_pdf(tmp_path)
    out = kit.call(NAME, {"handle": _open(kit, path), "page": 0,
                          "find": ["revision_clouds"]})
    x0, y0, x1, y1 = gt["cloud_bbox_ir"]
    want = (x0, PAGE_HEIGHT - y1, x1, PAGE_HEIGHT - y0)
    clouds = [r for r in out["revision_clouds"] if r["kind"] == "cloud"]
    best = clouds[0]["bbox"]
    assert all(abs(a - b) <= 8 for a, b in zip(best, want)), (best, want)
    cx, cy = gt["delta_center_ir"]
    deltas = [r for r in out["revision_clouds"]
              if r["kind"] == "revision_delta"]
    assert [d["text"] for d in deltas] == [gt["delta_text"]]
    assert _near(deltas[0]["center"], (cx, PAGE_HEIGHT - cy), tol=3)

    path, gt = cf.build_synthetic_title_block_pdf(tmp_path)
    out = kit.call(NAME, {"handle": _open(kit, path), "page": 0,
                          "find": ["title_block"]})
    (block,) = out["title_block"]
    assert block["bbox"] == [620.0, 560.0, PAGE_WIDTH - 20, PAGE_HEIGHT - 20]
    assert block["texts"] == gt["texts"]


def test_bbox_keeps_the_items_that_touch_it(tmp_path):
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    kit = _kit()
    h = _open(kit, path)
    out = kit.call(NAME, {"handle": h, "page": 0, "find": ["dimensions"],
                          "bbox": [560, 150, 640, 500]})
    assert [d["value"] for d in out["dimensions"]] == ["3.5 m"]
    # the same box given corner to corner the other way
    out = kit.call(NAME, {"handle": h, "page": 0, "find": ["dimensions"],
                          "bbox": [640, 500, 560, 150]})
    assert [d["value"] for d in out["dimensions"]] == ["3.5 m"]
    out = kit.call(NAME, {"handle": h, "page": 0, "bbox": [0, 0, 50, 50]})
    assert all(n == 0 for n in out["counts"].values())
    bad = kit.call(NAME, {"handle": h, "page": 0, "bbox": [1, 2]})
    assert "error" in bad


def test_points_follow_a_turned_page(tmp_path):
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    doc = pymupdf.open(path)
    doc[0].set_rotation(90)
    m = doc[0].rotation_matrix
    turned = _save(doc, tmp_path / "turned.pdf")
    kit = _kit()
    out = kit.call(NAME, {"handle": _open(kit, turned), "page": 0,
                          "find": ["dimensions"]})
    got = {d["value"]: d for d in out["dimensions"]}
    a, b = pymupdf.Point(150, 120) * m, pymupdf.Point(400, 120) * m
    d = got["8'-0\""]
    ends = [d["from"], d["to"]]
    assert any(_near(e, (a.x, a.y)) for e in ends)
    assert any(_near(e, (b.x, b.y)) for e in ends)


# -- what the sheet is ---------------------------------------------------------

def _walls(page, n=6):
    """Long lines, as a plan's walls are: the finders size an arrowhead off
    the sheet's typical line, so a sheet of nothing but short strokes would
    be no drawing at all."""
    for i in range(n):
        y = 420.0 + 40.0 * i
        _draw_shaft(page.new_shape(), [(80.0, y), (820.0, y)])


def _stroke_lettered(tmp_path):
    """A dimension whose value is lettered with strokes: no text layer at
    all."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _walls(page)
    a, b = (150.0, 300.0), (400.0, 300.0)
    for x in (a[0], b[0]):
        _draw_shaft(page.new_shape(), [(x, 295.0), (x, 330.0)])
    _draw_shaft(page.new_shape(), [a, b])
    for tip, d in ((a, (-1.0, 0.0)), (b, (1.0, 0.0))):
        _draw_filled_triangle(page.new_shape(), *_arrowhead_at(tip, d))
    # "10" in strokes above the line: a bar and a box
    _draw_shaft(page.new_shape(), [(268.0, 284.0), (268.0, 292.0)])
    _draw_shaft(page.new_shape(), [(272.0, 284.0), (278.0, 284.0),
                                   (278.0, 292.0), (272.0, 292.0),
                                   (272.0, 284.0)])
    return _save(doc, tmp_path / "strokes.pdf")


def test_lettering_drawn_as_lines_is_said(tmp_path):
    kit = _kit()
    out = kit.call(NAME, {"handle": _open(kit, _stroke_lettered(tmp_path)),
                          "page": 0, "find": ["dimensions"]})
    assert "error" not in out, out
    assert "lettering is drawn as lines" in out["sheet"]["is"]
    assert out["sheet"]["text_items"] == 0
    assert out["dimensions"], out
    assert all(d["value"] is None for d in out["dimensions"])


def _picture(page, rect):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 30), 0)
    pix.clear_with(180)
    page.insert_image(rect, pixmap=pix)


def test_a_scan_is_refused_in_plain_words(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _picture(page, page.rect)
    page.insert_text(pymupdf.Point(100, 100), "SECTION A", fontsize=9)
    scan = _save(doc, tmp_path / "scan.pdf")
    kit = _kit()
    out = kit.call(NAME, {"handle": _open(kit, scan), "page": 0})
    assert out["error"] == ("page 0 has no drawn geometry (no vector "
                            "lines): it is a scan or a picture of a "
                            "drawing, which this tool cannot read")
    assert "looking" in out["hint"] and "render_region" in out["hint"]


def test_a_picture_with_lines_over_it_is_said(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _picture(page, page.rect)
    _draw_shaft(page.new_shape(), [(100.0, 100.0), (300.0, 100.0)])
    pasted = _save(doc, tmp_path / "pasted.pdf")
    kit = _kit()
    out = kit.call(NAME, {"handle": _open(kit, pasted), "page": 0})
    assert out["sheet"]["is"].startswith("mostly a picture")
    # the picture keeps its own shape inside the page: nearly all of it
    assert out["sheet"]["picture_share"] >= 0.9


# -- the call's limits ---------------------------------------------------------

def test_bad_arguments_and_pages(tmp_path):
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    kit = _kit()
    h = _open(kit, path)
    bad = kit.call(NAME, {"handle": h, "page": 0, "find": ["arrows"]})
    assert "find takes" in bad["error"] and "dimensions" in bad["error"]
    out = kit.call(NAME, {"handle": h, "page": 3})
    assert out["error"] == "page 3 is out of range"


def test_a_dense_sheet_is_cut_to_fit_most_confident_first(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    n = 14
    for i in range(n):
        y = 40.0 + 48.0 * i
        cf._plant_dimension(page, (100.0, y), (300.0 + 20 * i, y),
                            f"{i + 2}'-0\"")
    for i in range(30):                      # the plan's own long lines
        x = 620.0 + 9.0 * i
        _draw_shaft(page.new_shape(), [(x, 20.0), (x, 680.0)])
    dense = _save(doc, tmp_path / "dense.pdf")
    kit = _kit(max_chars=1800)
    h = _open(kit, dense)
    text = kit.call_json(NAME, {"handle": h, "page": 0,
                                "find": ["dimensions"]})
    assert len(text) <= 1800
    out = json.loads(text)
    assert out["counts"]["dimensions"] == n
    assert 0 < len(out["dimensions"]) < n
    assert out["dimensions_truncated"].startswith(
        f"{n - len(out['dimensions'])} more")
    confs = [d["confidence"] for d in out["dimensions"]]
    assert confs == sorted(confs, reverse=True)
    # the page is read once: a second question reuses it
    first = kit._drawn[(h, 0)][1]
    kit.call(NAME, {"handle": h, "page": 0, "find": ["leaders"]})
    assert kit._drawn[(h, 0)][1] is first
