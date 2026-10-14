"""Different drawing sheets are not duplicates; a sheet placed twice is.

The review suite's 24-sheet set (2026-10-08, BACKLOG PL-07) came back from
``open_document`` with ``"duplicate_pages": "1-2,4-23"``: its lettering is
drawn as strokes, so no sheet has a text layer, every sheet has the same
number of paths, and the duplicate key was kind + words + path count. Every
agent looked at every page anyway, but the field invites skipping pages that
differ. These tests pin both sides: sheets that share a border, a legend and a
title block but differ in what is drawn on them are NOT repeats — however
little differs — and the same sheet placed twice still is.
"""

import random

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document                      # noqa: E402
from planlens.document.imagehash import (                        # noqa: E402
    DUP_HASH_DISTANCE, FINE_TOLERANCE, fine_grid, hamming, same_picture,
)
from planlens.document.structure import (                        # noqa: E402
    TEXT_DUP_MIN_WORDS, body_word_count,
)
from planlens.document.model import TextLine                     # noqa: E402
from planlens.testing.tag_fixtures import (                      # noqa: E402
    build_synthetic_tag_set, draw_text,
)

SHEET = (1224.0, 792.0)

#: A title block typed in the text layer, the same on every sheet: twenty
#: words of the sheet's own, outside the header and footer bands.
TITLE_BLOCK = ("EXAMPLE ENGINEERING GROUP  PROJECT ALPHA STATION  "
               "DRAWN BY AB  CHECKED BY CD  DATE 2026-10-01  "
               "SCALE AS NOTED  NOT FOR CONSTRUCTION  ISSUED FOR REVIEW")


def _stroke_sheet(doc, variant="base", seed=5, typed_title=False):
    """One 11x17 sheet: a room grid, a stroked title block and twenty 0.06 in
    stroke-lettered tags at seeded places. ``variant`` changes ONE tag's
    letter (``letter``) or moves it 30 pt (``moved``) — a revision of the
    sheet — or keeps it (``base``)."""
    W, H = SHEET
    page = doc.new_page(width=W, height=H)
    sh = page.new_shape()
    for i in range(10):
        sh.draw_line((40 + i * 90, 40), (40 + i * 90, H - 60))
    for j in range(8):
        sh.draw_line((40, 40 + j * 90), (850, 40 + j * 90))
    sh.finish(color=(0, 0, 0), width=0.8)
    sh.draw_rect(fitz.Rect(W - 300, H - 150, W - 40, H - 100))
    sh.finish(color=(0, 0, 0), width=0.8)
    draw_text(sh, "TYPE-GPE-XPC", W - 290, H - 135, 6.5)
    rnd = random.Random(seed)
    for k in range(20):
        x, y = rnd.uniform(90, 800), rnd.uniform(70, 640)
        tag = "GCG" if (variant == "letter" and k == 7) else "GCE"
        if variant == "moved" and k == 7:
            x += 30
        draw_text(sh, tag, x, y, 4.3)
    sh.commit()
    if typed_title:
        page.insert_textbox(fitz.Rect(W - 300, H - 230, W - 40, H - 155),
                            TITLE_BLOCK, fontsize=7)


def _pdf(*variants, typed_title=False, seeds=None):
    doc = fitz.open()
    for n, v in enumerate(variants):
        _stroke_sheet(doc, v, seed=(seeds[n] if seeds else 5),
                      typed_title=typed_title)
    data = doc.tobytes()
    doc.close()
    return data


def _placed_twice(pdf: bytes, page: int) -> bytes:
    """``pdf`` with its page ``page`` copied onto the end, as an assembler
    binds a sheet in twice."""
    src = fitz.open(stream=pdf, filetype="pdf")
    out = fitz.open()
    out.insert_pdf(src)
    out.insert_pdf(src, from_page=page, to_page=page)
    data = out.tobytes()
    out.close()
    src.close()
    return data


# -- the measured case -------------------------------------------------------

def test_the_suites_24_stroke_lettered_sheets_are_not_duplicates():
    """22 of 24 were flagged before (kind + no words + one path count)."""
    gt = build_synthetic_tag_set(n_pages=24, gce_growth=0,
                                 extra_callouts={"FPG": [3, 11, 19]})
    with open_document(gt.pdf) as doc:
        rows = doc.page_map()
    assert len(rows) == 24
    assert all(s.n_words == 0 for s in rows)
    assert [s.page for s in rows if s.duplicate_of is not None] == []


def test_one_of_those_sheets_bound_in_twice_is_still_a_duplicate():
    gt = build_synthetic_tag_set(n_pages=3)
    with open_document(_placed_twice(gt.pdf, 1)) as doc:
        rows = doc.page_map()
    assert [(s.page, s.duplicate_of, s.duplicate_rule) for s in rows
            if s.duplicate_of is not None] == [(3, 1, "image")]


# -- a revision is not a repeat ------------------------------------------------

@pytest.mark.parametrize("variant", ["letter", "moved"])
def test_a_revision_that_changes_one_small_tag_is_not_a_duplicate(variant):
    """The 256-bit hash cannot see one 0.06 in tag change — the two sheets
    are within its threshold — so it only nominates; the fine render
    decides."""
    with open_document(_pdf("base", variant)) as doc:
        rows = doc.page_map()
    assert rows[0].image_hash is not None and rows[1].image_hash is not None
    assert hamming(rows[0].image_hash, rows[1].image_hash) <= DUP_HASH_DISTANCE
    assert rows[1].duplicate_of is None


def test_the_same_revision_placed_twice_is_a_duplicate():
    with open_document(_placed_twice(_pdf("base", "letter"), 0)) as doc:
        rows = doc.page_map()
    assert rows[1].duplicate_of is None
    assert (rows[2].duplicate_of, rows[2].duplicate_rule) == (0, "image")


# -- words that are only the title block ---------------------------------------

def test_a_typed_title_block_does_not_make_different_sheets_one():
    """Twenty identical typed words and the same path count: the words name
    a repeat, the picture refuses it."""
    data = _pdf("base", "base", typed_title=True, seeds=[5, 6])
    with open_document(data) as doc:
        rows = doc.page_map()
        lines = doc.page(1, tables=False).lines
        same_words = doc._content_keys[0] == doc._content_keys[1]
    assert body_word_count(lines, SHEET[1]) >= TEXT_DUP_MIN_WORDS
    assert same_words                     # the words alone would name it
    assert rows[1].duplicate_of is None


def test_a_sheet_with_a_typed_title_block_placed_twice_is_a_text_duplicate():
    data = _placed_twice(_pdf("base", "base", typed_title=True,
                              seeds=[5, 6]), 0)
    with open_document(data) as doc:
        rows = doc.page_map()
    assert (rows[2].duplicate_of, rows[2].duplicate_rule) == (0, "text")
    assert rows[1].duplicate_of is None


def test_header_and_footer_words_do_not_count_as_the_pages_own():
    H = 792.0
    lines = [TextLine("l0", 0, "RUNNING HEADER " * 10, (72, 20, 500, 30)),
             TextLine("l1", 0, "Page 4 of 9 | Project 1234 " * 4,
                      (72, 770, 500, 780)),
             TextLine("l2", 0, "three body words", (72, 300, 300, 310))]
    assert body_word_count(lines, H) == 3


def test_the_earliest_page_is_named_whatever_order_pages_are_read_in():
    data = _placed_twice(_pdf("base", "letter"), 0)
    with open_document(data) as doc:
        late = doc.summary(2)                 # the repeat, read first
        doc.summary(1)
        doc.summary(0)
        assert (late.duplicate_of, late.duplicate_rule) == (0, "image")
        assert doc.summary(0).duplicate_of is None
        assert doc.summary(1).duplicate_of is None


# -- the fine comparison itself ----------------------------------------------

def test_the_fine_render_is_exact_for_a_page_placed_twice():
    data = _placed_twice(_pdf("base", "letter"), 0)
    raw = fitz.open(stream=data, filetype="pdf")
    try:
        grids = [fine_grid(p) for p in raw]
    finally:
        raw.close()
    assert max(grids[0].shape) >= 1024
    assert int(abs(grids[0].astype(int) - grids[2].astype(int)).max()) == 0
    assert same_picture(grids[0], grids[2])
    diff = int(abs(grids[0].astype(int) - grids[1].astype(int)).max())
    assert diff > FINE_TOLERANCE and not same_picture(grids[0], grids[1])
    assert not same_picture(grids[0], None)
