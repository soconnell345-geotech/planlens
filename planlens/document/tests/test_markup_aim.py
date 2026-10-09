"""What a callout is "aimed at": only markups drawn BEFORE it.

Live smoke wave 3 (F5): on a marked copy, a reviewer's original callout was
listed as "aimed at p1.m6" -- the box an app had drawn round the ring the
callout points to, hours later. A callout aims at what was on the sheet when
it was drawn; a markup added round its target later is never its aim. Drawn
before or after is read off the creation dates where both markups carry one
and they differ, else off their places in the page's annotation list (a
viewer appends a new markup at its end).
"""

from __future__ import annotations

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document.annotations import extract_annotations  # noqa: E402

TIP = (300.0, 300.0)
BOX = (280.0, 280.0, 320.0, 320.0)       # round the tip

EARLY = "D:20260801090000-04'00'"
LATE = "D:20261009141000-04'00'"


def _callout(page, created):
    a = page.add_freetext_annot(
        fitz.Rect(400, 100, 560, 140), "Confirm the pile tip.", fontsize=10,
        callout=(fitz.Point(*TIP), fitz.Point(400, 140)),
        line_end=fitz.PDF_ANNOT_LE_OPEN_ARROW)
    a.set_info(title="Reviewer A", creationDate=created)
    a.update()


def _box(page, created):
    a = page.add_rect_annot(fitz.Rect(*BOX))
    a.set_info(title="Alice via App (AI draft)", content="pile tip",
               creationDate=created)
    a.update()


def _markups(*draw):
    """The page's markups after calling each ``draw(page)`` in turn."""
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    for d in draw:
        d(page)
    data = doc.tobytes()
    doc.close()
    doc = fitz.open(stream=data, filetype="pdf")
    try:
        markups, _cad = extract_annotations(doc[0], 0)
    finally:
        doc.close()
    return {m.author: m for m in markups}


def _aim(by) -> object:
    callout = by["Reviewer A"]
    assert callout.points_at is not None
    return callout.points_to_markup


def test_a_box_drawn_later_round_the_target_is_not_the_aim():
    by = _markups(lambda p: _callout(p, EARLY), lambda p: _box(p, LATE))
    assert _aim(by) is None


def test_a_box_drawn_earlier_is_still_the_aim():
    """A reply callout aimed into an earlier comment box stays linked (the
    contractor's replies into the reviewer's boxes on a real submittal)."""
    by = _markups(lambda p: _box(p, EARLY), lambda p: _callout(p, LATE))
    assert _aim(by) == by["Alice via App (AI draft)"].id


@pytest.mark.parametrize("box_first, linked", [(True, True), (False, False)])
def test_with_equal_dates_the_annotation_order_decides(box_first, linked):
    draw = [lambda p: _box(p, LATE), lambda p: _callout(p, LATE)]
    by = _markups(*(draw if box_first else draw[::-1]))
    assert (_aim(by) is not None) is linked


def test_the_dates_win_over_the_order():
    """A box listed first but dated after the callout is not its aim."""
    by = _markups(lambda p: _box(p, LATE), lambda p: _callout(p, EARLY))
    assert _aim(by) is None
