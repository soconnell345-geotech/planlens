"""Frames, tiles, zoom windows and turned lettering (PL-29 (a)).

Moved from the app's test_vision_view.py, test_vision_turned.py,
test_vision_locations.py and test_vision_brief4.py: the tests of the
geometry that moved into :mod:`planlens.review.frames`. The app's tests of
its render (image budgets, detail, the prompt sentences, the tiled page read
through its vision tools) stay in the app.
"""

from __future__ import annotations

import pytest

pymupdf = pytest.importorskip("pymupdf")
np = pytest.importorskip("numpy")

from planlens.review import frames as vision_view  # noqa: E402

#: The red mark, as drawn (PDF points on the unrotated page, top-left origin).
MARK = (400.0, 300.0, 430.0, 312.0)
#: One line of a note, about 240 pt long at 9 pt.
NOTE = "UNDERDRAIN PIPE SHALL BE 6 IN MIN AT 3 IN SPACING"


def _sheet(text_rotate=0, n_lines=4):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for i in range(n_lines):
        step = 14 * i
        origin = {0: (60, 450 + step), 90: (100 + step, 700),
                  180: (360, 450 + step), 270: (100 + step, 450)}[text_rotate]
        page.insert_text(origin, NOTE, fontsize=9, rotate=text_rotate)
    page.draw_rect(pymupdf.Rect(*MARK), color=(1, 0, 0), fill=(1, 0, 0))
    data = doc.tobytes()
    doc.close()
    return data


def _layer(*lines, size=(612.0, 792.0)):
    return vision_view.TextLayer(
        [vision_view.LayerLine(t, b, r) for t, b, r in lines], *size)


# -- view + 0-999 box, and pixel boxes ----------------------------------------

def test_image_box_to_page():
    view = [100.0, 200.0, 1099.0, 699.0]                     # 999 x 499 pt
    assert vision_view.image_box_to_page(view, [0, 0, 999, 999]) == tuple(view)
    assert vision_view.image_box_to_page(view, [100, 200, 300, 400]) == (
        pytest.approx(200.0), pytest.approx(299.9, abs=0.01),
        pytest.approx(400.0), pytest.approx(399.8, abs=0.01))
    with pytest.raises(ValueError):
        vision_view.image_box_to_page([0, 0, 0, 10], [0, 0, 9, 9])
    with pytest.raises(ValueError):
        vision_view.image_box_to_page(view, [0, 0, 9])


def test_an_old_style_grid_answer_still_parses():
    text, counts = vision_view.boxes_to_grid(
        "the rectangle is at [490, 505, 572, 568]", (2048, 1326))
    assert text == "the rectangle is at [490, 505, 572, 568]"
    assert counts == {"converted": 0, "grid": 1}
    assert "without units" in vision_view.boxes_note(counts, (2048, 1326))


def test_box_conversion_rules():
    size = (2048, 1326)
    text, counts = vision_view.boxes_to_grid(
        "A px=[1024, 663, 1034, 673]; B px: [0, 0, 2048, 1326]; "
        "C [100, 50, 140, 60] px; D [100, 50, 140, 60]; "
        "readings [5.3, 4.1, 3.2, 2.5]; prose (1, 2, 3, 4)", size)
    assert "A [500, 500, 504, 507]" in text
    assert "B [0, 0, 999, 999]" in text
    assert "C [49, 38, 68, 45]" in text
    assert "D [49, 38, 68, 45]" in text
    assert "readings [5.3, 4.1, 3.2, 2.5]" in text
    assert "prose (1, 2, 3, 4)" in text
    assert counts == {"converted": 4, "grid": 1}
    text, counts = vision_view.boxes_to_grid("X [1500, 10, 1520, 20]", size)
    assert text == "X [732, 8, 741, 15]" and counts["converted"] == 1
    assert vision_view.boxes_to_grid("px=[1, 2, 3, 4]", None)[0] == \
        "px=[1, 2, 3, 4]"
    # a chart read-off converts tagged boxes alone
    text, counts = vision_view.boxes_to_grid(
        "Kp: [500, 1200, 900, 1300]; label px=[1000, 500, 1100, 540]",
        size, tagged_only=True)
    assert "[500, 1200, 900, 1300]" in text and "px=" not in text
    assert "converted in code" in vision_view.boxes_note(counts, size)


def test_px_box_to_page_is_exact():
    view = [100.0, 200.0, 1100.0, 700.0]
    assert vision_view.px_box_to_page(view, [0, 0, 2000, 1000],
                                      (2000, 1000)) == tuple(view)
    assert vision_view.px_box_to_page(view, [500, 250, 1000, 500],
                                      (2000, 1000)) == (350.0, 325.0, 600.0,
                                                        450.0)
    with pytest.raises(ValueError):
        vision_view.px_box_to_page([0, 0, 0, 1], [0, 0, 1, 1], (10, 10))


def test_px_to_grid_turns_back_a_turned_image():
    assert vision_view.px_to_grid([0, 0, 100, 50], (200, 100)) == \
        [0, 0, 500, 500]
    # sent turned 90: the box is turned back onto the view as shown
    turned = vision_view.px_to_grid([90, 0, 100, 10], (100, 200), 90)
    assert turned == vision_view.px_to_grid([0, 0, 10, 10], (200, 100))


# -- how far a location can be trusted ----------------------------------------

def test_every_result_says_how_far_its_boxes_can_be_trusted():
    wide = vision_view.precision_note([0, 0, 1224, 792])
    assert "NOT for placing a mark" in wide and "~122 x 79 pt" in wide
    small = vision_view.precision_note([0, 0, 280, 200])
    assert "may be anchored on this view" in small
    assert vision_view.NOT_A_MEASUREMENT in small


def test_the_location_error_has_a_floor():
    assert vision_view.location_error([0, 0, 1224, 792]) == \
        pytest.approx((122.4, 79.2))
    assert vision_view.location_error([0, 0, 50, 40]) == (12.0, 12.0)
    assert vision_view.zoom_pad([0, 0, 100, 100], [0, 0, 400, 100]) == \
        pytest.approx((60.0, 60.0))


def test_a_zoom_window_is_padded_only_off_a_wide_view():
    assert vision_view.zoom_window([0, 0, 200, 200], [10, 10, 20, 20]) is None
    win = vision_view.zoom_window([0, 0, 1224, 792], [600, 400, 610, 405])
    assert win == [pytest.approx(600 - 122.4, abs=0.1),
                   pytest.approx(400 - 79.2, abs=0.1),
                   pytest.approx(610 + 122.4, abs=0.1),
                   pytest.approx(405 + 79.2, abs=0.1)]


def test_answer_boxes_reads_labels_and_skips_regions():
    text = ("1. **GCE** callout [100, 200, 110, 205]\n"
            "| GCG | [300, 300, 310, 305] |\n"
            "The whole drawing area [0, 0, 999, 999]\n"
            "readings 5.3, 4.1 and nothing else")
    got = vision_view.answer_boxes(text, (0.0, 0.0, 999.0, 999.0))
    assert [g[0] for g in got] == ["GCE callout", "GCG"]
    assert got[0][1] == pytest.approx((100, 200, 110, 205))


def test_the_aim_tolerance_is_a_fraction_of_the_source_view():
    assert vision_view.aim_tolerance([0, 0, 1224, 792]) == pytest.approx(30.6)
    assert vision_view.aim_tolerance([0, 0, 100, 60]) == 12.0


# -- tiles ----------------------------------------------------------------------

def test_tile_boxes_cover_the_clip_with_overlap():
    boxes = vision_view.tile_boxes((0, 0, 100, 200), 2)
    assert len(boxes) == 4
    assert boxes[0] == (0, 0, pytest.approx(54.0), pytest.approx(108.0))
    assert boxes[-1][2:] == (100, 200)


def test_tile_grid_from_the_lettering_size():
    info = {"clip": (0, 0, 1000, 1000), "width_px": 1000, "height_px": 1000}
    assert vision_view.tile_grid(dict(info, text_px=14.0)) == 1
    assert vision_view.tile_grid(dict(info, text_px=5.0)) == 4
    assert vision_view.tile_grid(dict(info, text_px=8.0)) == 2
    # prose with no measured size reads; strokes are assumed small
    assert vision_view.tile_grid(dict(info, text_chars=500)) == 1
    assert vision_view.tile_grid(dict(info, text_chars=0)) == 4
    # a turned image is judged along the view's own x
    turned = {"clip": (0, 0, 1000, 500), "width_px": 500, "height_px": 1000,
              "turn": 90, "text_px": 5.0}
    flat = {"clip": (0, 0, 1000, 500), "width_px": 1000, "height_px": 500,
            "text_px": 5.0}
    assert vision_view.tile_grid(turned) == vision_view.tile_grid(flat)


# -- the arithmetic of a quarter turn -------------------------------------------

@pytest.mark.parametrize("turn", [0, 90, 180, 270])
def test_a_box_turned_and_turned_back_is_the_same_box(turn):
    size = (1583, 2048)
    sent = vision_view.unturned_size(size, turn)
    for box in ([0, 0, 10, 20], [100.5, 200, 400, 260], [1500, 1900, 1583,
                                                         2048]):
        there = vision_view.turn_px_box(box, size, turn)
        assert all(0 <= v <= max(sent) for v in there)
        assert vision_view.unturn_px_box(there, sent, turn) == \
            pytest.approx(box)


def test_a_quarter_turn_clockwise_moves_the_top_left_to_the_top_right():
    assert vision_view.turn_px_box([0, 0, 10, 10], (200, 100), 90) == \
        [90, 0, 100, 10]
    assert vision_view.turn_px_box([0, 0, 10, 10], (200, 100), 180) == \
        [190, 90, 200, 100]
    assert vision_view.turn_px_box([0, 0, 10, 10], (200, 100), 270) == \
        [0, 190, 10, 200]
    assert vision_view.turn_edge("top", 90) == "right"
    assert vision_view.turn_edge("left", 90) == "top"
    assert vision_view.turn_edge("top", 180) == "bottom"
    assert vision_view.turn_edge("top", 270) == "left"


def test_which_way_a_view_is_turned():
    col = (100.0, 450.0, 110.0, 700.0)
    row = (60.0, 443.0, 300.0, 452.0)
    whole = (0, 0, 612, 792)
    turned = _layer(*[(NOTE, col, 90.0)] * 4)
    assert vision_view.reading_turn(turned, whole) == 90
    assert vision_view.reading_turn(_layer((NOTE, col, 270.0)), col) == 270
    assert vision_view.reading_turn(_layer((NOTE, row, 180.0)), row) == 180
    mixed = _layer((NOTE, col, 90.0), (NOTE, row, 0.0))
    assert vision_view.reading_turn(mixed, whole) == 0
    assert vision_view.reading_turn(_layer((NOTE, col, None)), col) == 0
    assert vision_view.reading_turn(
        _layer((NOTE, col, 90.0), (NOTE * 2, row, 45.0)), whole) == 0
    assert vision_view.reading_turn(_layer((NOTE, col, 93.0)), col) == 90
    assert vision_view.reading_turn(turned, (300, 0, 612, 400)) == 0
    assert vision_view.reading_turn(None, whole) == 0


def test_a_whole_sheet_is_not_turned_for_one_turned_stamp():
    stamp = (580.0, 300.0, 590.0, 500.0)
    layer = _layer(("PRELIMINARY NOT FOR CONSTRUCTION", stamp, 90.0))
    assert vision_view.reading_turn(layer, (0, 0, 612, 792)) == 0
    assert vision_view.reading_turn(layer, (560, 280, 612, 520)) == 90


def test_the_text_layer_of_a_page_says_which_way_it_runs():
    layer = vision_view.page_text_layer(_sheet(text_rotate=90), 0)
    assert layer is not None and layer.width == 612.0
    assert layer.lines and all(ln.rotation == pytest.approx(90.0)
                               for ln in layer.lines)
    assert vision_view.reading_turn(layer, (0, 0, 612, 792)) == 90
    assert vision_view.page_text_layer(b"not a pdf", 0) is None


def test_a_turned_image_moves_whole_pixels():
    doc = pymupdf.open(stream=_sheet(), filetype="pdf")
    png = doc[0].get_pixmap(dpi=36).tobytes("png")
    doc.close()
    pix = pymupdf.Pixmap(png)
    info = {"width_px": pix.width, "height_px": pix.height, "format": "png",
            "clip": (0, 0, 612, 792)}
    out, turned = vision_view.turn_image(png, info, 90)
    assert turned["turn"] == 90 and turned["clip"] == info["clip"]
    assert (turned["width_px"], turned["height_px"]) == (pix.height,
                                                         pix.width)
    assert vision_view.turn_image(png, info, 0) == (png, info)
    assert vision_view.turn_image(b"junk", info, 90) == (b"junk", info)


def test_a_turned_view_says_so():
    note = vision_view.turned_note(90)
    assert "turned 90 degrees clockwise" in note
    assert "from bottom to top" in note
    assert "earlier looks" in vision_view.turned_note(270, "remembered")


# -- lettering cut by a window's edge -------------------------------------------

def test_the_cut_note_names_at_most_a_few_lines():
    layer = _layer(*[(NOTE, (0.0, 10.0 * i, 600.0, 10.0 * i + 8), 0.0)
                     for i in range(20)])
    cuts = vision_view.cut_lines((100, 0, 200, 300), layer)
    assert len(cuts) == 20
    note = vision_view.cut_note(cuts, (100, 0, 200, 300), (683, 2048))
    assert note.count("px=") == vision_view.CUT_LINES_LISTED
    assert "and 12 more" in note
    assert vision_view.cut_note([], (0, 0, 1, 1), (10, 10)) is None


def test_a_turned_zoom_names_the_cut_edges_of_the_image_it_got():
    layer = _layer(*[(NOTE, (90.0 + 14 * i, 457.0, 99.0 + 14 * i, 700.0),
                      90.0) for i in range(4)])
    clip = (80, 520, 170, 620)
    cuts = vision_view.cut_lines(clip, layer)
    assert [e for _ln, e in cuts] == [["top", "bottom"]] * 4
    note = vision_view.cut_note(cuts, clip, (1000, 900), turn=90)
    assert note.count("(cut by the right and left edge)") == 4


def test_the_window_is_widened_within_its_limit_and_held_to_the_page():
    layer = _layer(("A LINE", (5.0, 100.0, 150.0, 108.0), 0.0),
                   ("FAR", (5.0, 200.0, 900.0, 208.0), 0.0),
                   size=(612.0, 792.0))
    clip, n = vision_view.widen_to_lines((20, 90, 220, 300), layer)
    assert n == 1
    assert clip[0] == pytest.approx(3.0) and clip[2] == 220
    assert vision_view.cut_lines((0, 0, 612, 792), _layer(
        ("IN", (10.0, 10.0, 50.0, 18.0), 0.0))) == []
    assert vision_view.cut_lines((0, 0, 100, 100), _layer(
        ("OUT", (200.0, 10.0, 250.0, 18.0), 0.0))) == []
    edge = _layer(("EDGE", (-4.0, 100.0, 40.0, 108.0), 0.0))
    clip, n = vision_view.widen_to_lines((10, 90, 200, 300), edge)
    assert n == 1 and clip[0] == 0.0


def test_a_window_narrow_enough_to_mark_from_stays_that_narrow():
    layer = _layer(("NEAR", (90.0, 100.0, 300.0, 108.0), 0.0),
                   ("FAR", (60.0, 120.0, 300.0, 128.0), 0.0))
    clip, n = vision_view.widen_to_lines((100, 90, 380, 200), layer)
    assert n == 1
    assert clip[2] - clip[0] <= vision_view.MARK_VIEW_PT
    clip, n = vision_view.widen_to_lines((100, 90, 420, 200), layer)
    assert n == 2 and clip[0] == pytest.approx(58.0)


# -- the TURNED line a look may end with ------------------------------------------

@pytest.mark.parametrize("line,turn", [
    ("TURNED: bottom-to-top", 90), ("**TURNED:** top to bottom.", 270),
    ("- TURNED: upside-down", 180), ("Turned: reads upward", 90),
    ("TURNED: left-to-right", None), ("TURNED: none", None)])
def test_the_turned_line_is_read_and_dropped(line, turn):
    text, got = vision_view.read_turn_tag(f"MARK [1, 2, 3, 4]\n{line}")
    assert got == turn and text == "MARK [1, 2, 3, 4]"
    assert vision_view.read_turn_tag("the sheet is turned") == \
        ("the sheet is turned", None)
    assert "TURNED:" in vision_view.TURN_TAG_INSTRUCTION
