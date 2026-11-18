"""BACKLOG DR-44, review: an empty list is "none found", not "none drawn".
The finders know a dimension by a filled arrowhead sized against the
sheet's typical line length, so a dimension ended with tick marks, a very
short one, or one on a sheet of mostly short strokes is not found; the
result said nothing and read as "this sheet has no dimensions".

Every drawing here is made up and drawn by the test.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing import construct_fixtures as cf  # noqa: E402
from planlens.testing.construct_fixtures import _plant_dimension  # noqa: E402
from planlens.testing.leader_fixtures import (  # noqa: E402
    PAGE_HEIGHT, PAGE_WIDTH, _draw_shaft)
from planlens.tools import ReviewToolkit  # noqa: E402

NAME = "drawing_geometry"


def _read(doc, path, **arguments):
    doc.save(str(path))
    doc.close()
    kit = ReviewToolkit(drawing_tools=True)
    handle = kit.call("open_document", {"source": str(path)})["handle"]
    return kit.call(NAME, {"handle": handle, "page": 0, **arguments})


def _said(out):
    return [n for n in out["notes"] if n.startswith("none found")]


def test_a_dimension_among_hatching_is_missed_and_the_result_says_so(
        tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    _plant_dimension(page, (150, 120), (400, 120), "2500")
    for i in range(300):                    # hatching: 300 short strokes
        x, y = 100.0 + (i % 60) * 12, 300.0 + (i // 60) * 12
        _draw_shaft(page.new_shape(), [(x, y), (x + 4, y + 3)])
    out = _read(doc, tmp_path / "hatched.pdf", find=["dimensions"])
    assert out["dimensions"] == [] and out["counts"] == {"dimensions": 0}
    (said,) = _said(out)
    assert "(dimensions)" in said
    assert "does not show the sheet has none" in said
    assert "render" in said


def test_tick_mark_dimensions_are_missed_and_the_result_says_so(tmp_path):
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    for i in range(6):                              # walls: long lines
        y = 560.0 + 20.0 * i
        _draw_shaft(page.new_shape(), [(80.0, y), (820.0, y)])
    for x in (150.0, 400.0):                        # witness lines, ticks
        _draw_shaft(page.new_shape(), [(x, 295.0), (x, 330.0)])
        _draw_shaft(page.new_shape(), [(x - 4, 304.0), (x + 4, 296.0)])
    _draw_shaft(page.new_shape(), [(150.0, 300.0), (400.0, 300.0)])
    page.insert_text((265.0, 292.0), "2500", fontsize=8)
    out = _read(doc, tmp_path / "ticks.pdf")
    assert out["counts"]["dimensions"] == 0
    (said,) = _said(out)
    assert "tick marks" in said
    # every kind that came back empty is named, in the order asked
    assert said.startswith("none found (dimensions, leaders, callouts, "
                           "revision clouds, title block)")


def test_what_was_found_is_not_called_missing(tmp_path):
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    kit = ReviewToolkit(drawing_tools=True)
    handle = kit.call("open_document", {"source": path})["handle"]
    out = kit.call(NAME, {"handle": handle, "page": 0,
                          "find": ["dimensions", "leaders"]})
    assert out["counts"] == {"dimensions": 2, "leaders": 1}
    assert not _said(out)
