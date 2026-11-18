"""BACKLOG DR-44, review: a dimension's ``value`` and a leader's ``text``
are the NEAREST text, and the finders reach far for it on a sheet of long
lines. A dimension lettered in strokes on a sheet that has other text came
back with a note 150 points away as its value, stated as measured. Each row
now says how far its text is, and the result says what that means.

Every drawing here is made up and drawn by the test.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.testing import construct_fixtures as cf  # noqa: E402
from planlens.testing.leader_fixtures import (  # noqa: E402
    PAGE_HEIGHT, PAGE_WIDTH, _arrowhead_at, _draw_filled_triangle,
    _draw_shaft)
from planlens.tools import ReviewToolkit  # noqa: E402

NAME = "drawing_geometry"


def _read(path, **arguments):
    kit = ReviewToolkit(drawing_tools=True)
    handle = kit.call("open_document", {"source": str(path)})["handle"]
    return kit.call(NAME, {"handle": handle, "page": 0, **arguments})


def _stroke_lettered_beside_a_note(tmp_path, note_above):
    """A dimension from (150, 300) to (400, 300) whose value is lettered in
    strokes, on a sheet whose only text is a general note ``note_above``
    points above the line."""
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    for i in range(6):                              # walls: long lines
        y = 560.0 + 20.0 * i
        _draw_shaft(page.new_shape(), [(80.0, y), (820.0, y)])
    a, b = (150.0, 300.0), (400.0, 300.0)
    for x in (a[0], b[0]):
        _draw_shaft(page.new_shape(), [(x, 295.0), (x, 330.0)])
    _draw_shaft(page.new_shape(), [a, b])
    for tip, d in ((a, (-1.0, 0.0)), (b, (1.0, 0.0))):
        _draw_filled_triangle(page.new_shape(), *_arrowhead_at(tip, d))
    page.insert_text((260.0, 300.0 - note_above), "SEE NOTE 4", fontsize=8)
    path = tmp_path / "strokes_and_a_note.pdf"
    doc.save(str(path))
    doc.close()
    return path


def test_a_far_note_given_as_a_value_says_how_far_it_is(tmp_path):
    out = _read(_stroke_lettered_beside_a_note(tmp_path, 150.0),
                find=["dimensions"])
    (dim,) = out["dimensions"]
    assert {tuple(dim["from"]), tuple(dim["to"])} == {(150.0, 300.0),
                                                      (400.0, 300.0)}
    # the finder still ties the nearest text: the row says it is far off
    assert dim["value"] == "SEE NOTE 4"
    assert dim["value_offset_pt"] > 100
    said = [n for n in out["notes"] if "value_offset_pt" in n]
    assert said and "NEAREST" in said[0] and "another note" in said[0]


def test_a_value_beside_its_line_is_near(tmp_path):
    path, _gt = cf.build_synthetic_dimension_pdf(tmp_path)
    out = _read(path, find=["dimensions", "leaders"])
    assert len(out["dimensions"]) == 2
    assert all(d["value_offset_pt"] < 20 for d in out["dimensions"])
    assert all(r["text_offset_pt"] < 20 for r in out["leaders"])
    assert out["leaders"]


def test_no_text_no_offset(tmp_path):
    # a sheet with no text at all: nothing is tied, nothing is said of it
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    for i in range(6):
        y = 560.0 + 20.0 * i
        _draw_shaft(page.new_shape(), [(80.0, y), (820.0, y)])
    a, b = (150.0, 300.0), (400.0, 300.0)
    for x in (a[0], b[0]):
        _draw_shaft(page.new_shape(), [(x, 295.0), (x, 330.0)])
    _draw_shaft(page.new_shape(), [a, b])
    for tip, d in ((a, (-1.0, 0.0)), (b, (1.0, 0.0))):
        _draw_filled_triangle(page.new_shape(), *_arrowhead_at(tip, d))
    path = tmp_path / "strokes.pdf"
    doc.save(str(path))
    doc.close()
    out = _read(path, find=["dimensions"])
    assert out["dimensions"] and all(
        "value_offset_pt" not in d for d in out["dimensions"])
    assert not any("value_offset_pt" in n for n in out["notes"])
