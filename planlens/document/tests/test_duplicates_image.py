"""Duplicate pages a scan's TEXT cannot reveal, found by the page's picture.

The text rule behind ``duplicate_of`` reads characters and path counts, so it
is blind on a scanned page: two scans of one sheet both carry nothing, and
nothing never matches. These tests build the documents that rule has to get
right — the same scan bound in twice, two different scans, blank pages, a
repeat someone turned on its side — and pin which rule fired, because a
reader weighs "the same words" and "the same picture" differently.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document                      # noqa: E402
from planlens.document.imagehash import (                        # noqa: E402
    DUP_HASH_DISTANCE, HASH_SIDE, MIN_CONFIDENT_BITS, _gray_grid,
    confident_bits, hamming, page_dhash, wants_image_hash,
)
from planlens.testing.submittal_fixtures import (                # noqa: E402
    build_synthetic_submittal,
)

LETTER = (612.0, 792.0)


def _drawn(shift: float = 0.0, label: str = "A") -> "fitz.Pixmap":
    """Pixels of a page with real ink on it — what a scanner would return."""
    src = fitz.open()
    page = src.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(40, 40 + shift, 572, 300 + shift),
                   color=(0, 0, 0), fill=(0.1, 0.1, 0.1), width=2)
    page.insert_text((60, 420 + shift), f"LOG OF BORING {label}", fontsize=36)
    for i in range(12):
        y = 460 + shift + i * 22
        page.draw_line((60, y), (60 + 30 * ((i * 7) % 17), y), width=3)
    pix = page.get_pixmap(dpi=96)
    src.close()
    return pix


def _scan_doc(pixmaps, rotations=None):
    """A PDF whose pages are nothing but placed images: no text layer."""
    doc = fitz.open()
    for n, pix in enumerate(pixmaps):
        page = doc.new_page(width=LETTER[0], height=LETTER[1])
        page.insert_image(page.rect, pixmap=pix)
        if rotations and rotations[n]:
            doc[n].set_rotation(rotations[n])
    data = doc.tobytes()
    doc.close()
    return data


# -- the hash itself --------------------------------------------------------

def test_hamming_counts_differing_bits():
    assert hamming("0000000000000000", "0000000000000000") == 0
    assert hamming("0000000000000000", "0000000000000001") == 1
    assert hamming("0000000000000000", "ffffffffffffffff") == 64
    assert hamming("00ff00ff00ff00ff", "ff00ff00ff00ff00") == 64
    assert hamming("8000000000000000", "0000000000000001") == 2
    with pytest.raises(ValueError, match="different lengths"):
        hamming("0000", "0000000000000000")


def test_a_hash_is_256_bits_of_hex_and_survives_a_second_open():
    data = _scan_doc([_drawn()])
    with open_document(data) as first, open_document(data) as second:
        a, b = first.summary(0), second.summary(0)
    assert a.image_hash is not None
    assert HASH_SIDE == 16
    assert len(a.image_hash) == 64 and int(a.image_hash, 16) >= 0
    assert a.image_hash == b.image_hash


def test_the_gate_is_about_pages_whose_text_cannot_decide():
    assert wants_image_hash("scanned", 0, needs_ocr=True)
    assert wants_image_hash("figure", 4000)
    assert wants_image_hash("drawing_sheet", 4000)
    assert wants_image_hash("text", 12)          # a footer is not text
    assert not wants_image_hash("text", 4000)
    assert not wants_image_hash("form", 4000)
    # Never a blank page: every blank page looks like every other one, and
    # "page 40 repeats page 2" must not mean "both are empty".
    assert not wants_image_hash("blank", 0, needs_ocr=True)


def test_a_page_with_no_picture_gets_no_hash():
    """A page of one flat tone hashes to zeros — and so does the next one.

    Withholding the hash is what stops two different near-blank scans being
    called the same page (:data:`MIN_CONFIDENT_BITS`).
    """
    doc = fitz.open()
    doc.new_page(width=LETTER[0], height=LETTER[1])
    page = doc[0]
    page.draw_rect(page.rect, color=None, fill=(0.97, 0.97, 0.97))
    assert page_dhash(page) is None
    assert page_dhash(page, guard=False) == "0" * 64
    doc.close()


def _divider(title: str) -> "fitz.Pixmap":
    """A scanned appendix divider: white paper, a printed rule, two words."""
    src = fitz.open()
    page = src.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(36, 36, 576, 756), color=(0, 0, 0), width=1)
    page.insert_text((230, 380), title, fontsize=13)
    pix = page.get_pixmap(dpi=96)
    src.close()
    return pix


def test_a_rule_and_two_words_of_type_is_not_a_picture():
    """The case the whole-grid grey RANGE floor let through.

    An appendix divider is white paper inside a printed border. The border
    alone spans the range of greys, so a RANGE floor passes the page — and the
    next divider, whose two words say something else entirely, hashes to the
    same almost-nothing. Measured on two real reports, pages like these score
    2 confident bits of 256 while every page carrying a figure, a form or a
    scan of one scores at least 10.
    """
    data = _scan_doc([_divider("APPENDIX D"), _divider("APPENDIX E")])
    raw = fitz.open(stream=data, filetype="pdf")
    # Hashed anyway, the two would have been the same picture at distance 0.
    forced = [page_dhash(p, guard=False) for p in raw]
    assert hamming(*forced) <= DUP_HASH_DISTANCE
    assert all(confident_bits(_gray_grid(p, HASH_SIDE + 1, HASH_SIDE))
               < MIN_CONFIDENT_BITS for p in raw)
    raw.close()

    with open_document(data) as doc:
        rows = doc.page_map()
    assert [s.image_hash for s in rows] == [None, None]
    assert [s.duplicate_of for s in rows] == [None, None]


# -- the rule ---------------------------------------------------------------

def test_the_same_scan_on_two_pages_is_a_duplicate_by_image():
    pix = _drawn()
    pages = [pix, _drawn(shift=120, label="B"), pix]
    with open_document(_scan_doc(pages)) as doc:
        rows = doc.page_map()
    assert [s.kind for s in rows] == ["scanned"] * 3
    assert rows[2].duplicate_of == 0
    assert rows[2].duplicate_rule == "image"
    assert rows[0].duplicate_of is None and rows[1].duplicate_of is None
    # and the row a caller reads says which rule spoke, without the hash
    row = rows[2].to_dict(detail=False)
    assert row["duplicate_of"] == 0 and row["duplicate_rule"] == "image"
    assert "image_hash" not in row


def test_two_different_scans_are_not_duplicates():
    with open_document(_scan_doc([_drawn(label="A"),
                                  _drawn(shift=160, label="B")])) as doc:
        rows = doc.page_map()
    assert [s.duplicate_of for s in rows] == [None, None]
    assert hamming(rows[0].image_hash, rows[1].image_hash) > DUP_HASH_DISTANCE


def test_blank_pages_are_never_duplicates_of_each_other():
    doc = fitz.open()
    for _ in range(3):
        doc.new_page(width=LETTER[0], height=LETTER[1])
    data = doc.tobytes()
    doc.close()
    with open_document(data) as opened:
        rows = opened.page_map()
    assert [s.kind for s in rows] == ["blank"] * 3
    assert [s.duplicate_of for s in rows] == [None] * 3
    assert [s.image_hash for s in rows] == [None] * 3


def test_a_repeat_someone_turned_is_not_called_a_duplicate():
    """DECIDED: a page a viewer shows sideways or upside down is NOT a repeat.

    The hash is taken in the displayed orientation, like every coordinate in
    this package, so a turned copy simply does not match. That is the intended
    answer rather than an accident of the method: ``duplicate_of`` tells a
    reviewer they have already seen this page, and a page presented the other
    way up is a page they have to look at — its text boxes, its renders and
    what a vision model makes of it all differ. A reader who wants
    orientation-independent matching is asking a different question, and the
    honest place to answer it is a separate field, not this one.

    /Rotate 90 fails twice over (the displayed page is landscape, so the size
    guard rejects it before the hash is consulted); /Rotate 180 keeps the size
    and is rejected on the picture alone.
    """
    pix = _drawn()
    with open_document(_scan_doc([pix, pix], rotations=[0, 90])) as doc:
        turned = doc.page_map()
    assert (turned[1].width, turned[1].height) == (LETTER[1], LETTER[0])
    assert turned[1].duplicate_of is None

    with open_document(_scan_doc([pix, pix], rotations=[0, 180])) as doc:
        flipped = doc.page_map()
    assert (flipped[1].width, flipped[1].height) == LETTER
    assert flipped[1].duplicate_of is None
    assert hamming(flipped[0].image_hash,
                   flipped[1].image_hash) > DUP_HASH_DISTANCE


def test_the_picture_never_overrules_the_words():
    """Two sheets off one border differ by a sheet number and little else.

    The synthetic submittal draws its two D-size sheets from the same 300
    lines and the same title block, so they are the same picture at this
    resolution — but their text is not the same text, and a drawing set must
    not collapse into its first sheet.
    """
    gt = build_synthetic_submittal()
    with open_document(gt.pdf) as doc:
        rows = doc.page_map()
    a, b = rows[gt.sheet_pages[0]], rows[gt.sheet_pages[1]]
    assert a.image_hash is not None and b.image_hash is not None
    assert hamming(a.image_hash, b.image_hash) <= DUP_HASH_DISTANCE
    assert b.duplicate_of is None


def test_a_text_rule_duplicate_still_reports_rule_text():
    gt = build_synthetic_submittal()
    with open_document(gt.pdf) as doc:
        rows = doc.page_map()
        segs = doc.segments()
    dup = rows[gt.duplicate_page]
    assert (dup.duplicate_of, dup.duplicate_rule) == (gt.duplicate_of, "text")
    assert dup.to_dict(detail=False)["duplicate_rule"] == "text"
    # A duplicate is a page like any other to the segmenter, and the fixture's
    # 7 segments are unchanged by any of this.
    assert len(segs) == len(gt.expected_segments)
    assert dup.segment is not None


def test_an_image_duplicate_segments_exactly_like_a_text_one():
    """Segmentation reads headers, numbering and size — never duplicates.

    Whichever rule found the repeat, the page stays in the run it sits in.
    """
    pix = _drawn()
    with open_document(_scan_doc([pix, _drawn(shift=120, label="B"),
                                  pix, _drawn(shift=240, label="C")])) as doc:
        rows = doc.page_map()
        segs = doc.segments()
    assert rows[2].duplicate_rule == "image"
    assert [s.segment for s in rows] == [0, 0, 0, 0]
    assert len(segs) == 1 and segs[0]["n_pages"] == 4
    assert segs[0]["kinds"] == {"scanned": 4}


def test_the_hash_is_withheld_rather_than_guessed_on_a_flat_scan():
    """Two DIFFERENT near-blank scans must not become one page."""
    src = fitz.open()
    page = src.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(page.rect, color=None, fill=(0.98, 0.98, 0.98))
    faint = page.get_pixmap(dpi=96)
    page.draw_rect(fitz.Rect(0, 0, 612, 300), color=None,
                   fill=(0.99, 0.99, 0.99))
    fainter = page.get_pixmap(dpi=96)
    src.close()
    with open_document(_scan_doc([faint, fainter])) as doc:
        rows = doc.page_map()
    assert [s.image_hash for s in rows] == [None, None]
    assert [s.duplicate_of for s in rows] == [None, None]
    # ... and they would have been "identical" without the floor
    assert MIN_CONFIDENT_BITS > 0


def _lab_sheet(sample: str, values, curve: bool = True) -> "fitz.Pixmap":
    """One filled-in copy of a printed laboratory form, under a watermark.

    The template — the rules, the boxes, the headings, the diagonal DRAFT —
    is identical on every sheet. What differs is the sample, the numbers in
    the table and whether the chart carries a plotted line: a few percent of
    the ink, which is exactly the proportion that makes a filled-in form the
    hard case for a picture hash.
    """
    src = fitz.open()
    page = src.new_page(width=LETTER[0], height=LETTER[1])
    page.draw_rect(fitz.Rect(60, 56, 552, 84), color=None,
                   fill=(0.75, 0.75, 0.75))
    page.insert_text((120, 76), "FOUNDATION INDICATOR TEST RESULTS",
                     fontsize=16)
    page.draw_rect(fitz.Rect(60, 90, 552, 150), color=(0, 0, 0), width=1.6)
    page.insert_text((70, 110), "CLIENT   Example Client Ltd", fontsize=12)
    page.insert_text((70, 132), "BOREHOLE", fontsize=12)
    page.draw_rect(fitz.Rect(60, 160, 552, 430), color=(0, 0, 0), width=1.6)
    for i in range(13):                       # the template's own ruling
        page.draw_line((60, 160 + i * 22.5), (552, 160 + i * 22.5), width=1.6)
    for x in (180, 300, 430):
        page.draw_line((x, 160), (x, 430), width=1.6)
    page.draw_rect(fitz.Rect(60, 450, 552, 720), color=(0, 0, 0), width=1.6)
    page.insert_text((210, 468), "PARTICLE SIZE ANALYSIS", fontsize=14)
    for i in range(9):
        page.draw_line((90 + i * 55, 480), (90 + i * 55, 710), width=1.6)
    for i in range(7):
        page.draw_line((90, 480 + i * 38), (530, 480 + i * 38), width=1.6)
    page.insert_text((140, 560), "D R A F T", fontsize=72,
                     fill=(0.82, 0.82, 0.82), color=(0.82, 0.82, 0.82))
    # ... and here is everything that makes this sheet THIS sheet
    page.insert_text((200, 132), sample, fontsize=12)
    for i in range(11):
        y = 160 + (i + 1) * 22.5 - 5
        v = values[i]
        page.insert_text((66, y), f"{25.4 / (i + 1):.3f}", fontsize=12)
        page.insert_text((188, y), f"{v}", fontsize=12)
        page.insert_text((306, y), f"TEST {i % 4 + 1}  {v / 3:.2f}",
                         fontsize=12)
        page.insert_text((436, y), f"{v * 7 % 100:.1f} %", fontsize=12)
    if curve:
        pts = [(90 + i * 44, 700 - i * i * 2.4) for i in range(11)]
        for a, b in zip(pts, pts[1:]):
            page.draw_line(a, b, width=2.2)
    pix = page.get_pixmap(dpi=96)
    src.close()
    return pix


LAB_VALUES = (
    (100, 100, 100, 100, 99, 94, 79, 44, 38, 20, 13),
    (62, 48, 31, 19, 12, 8, 5, 4, 3, 2, 1),
    (100, 100, 100, 96, 88, 71, 52, 31, 24, 15, 9),
)


def test_filled_in_copies_of_one_form_are_not_the_same_page():
    """An appendix of laboratory sheets: one template, different results.

    MEASURED on a real 202-page report whose 73-page laboratory appendix is
    exactly this — every sheet a different sample off one printed form, under
    one diagonal watermark. At 8x8 the hash claimed 69 of those pages as
    duplicates of the first, and an ingest that skipped duplicates would have
    dropped the whole appendix. At 16x16 no two of them come within 5 bits.
    """
    sheets = [_lab_sheet("SB-01 / 0.50", LAB_VALUES[0]),
              _lab_sheet("SB-02 / 1.20", LAB_VALUES[1]),
              _lab_sheet("SB-03 / 2.70", LAB_VALUES[2], curve=False)]
    data = _scan_doc(sheets)
    with open_document(data) as doc:
        rows = doc.page_map()
    assert [s.kind for s in rows] == ["scanned"] * 3
    assert [s.duplicate_of for s in rows] == [None, None, None]
    # Every sheet still HAS a hash: a filled-in form is picture enough to
    # compare, it simply does not match. Withholding would be the blunt fix,
    # and it would lose the repeat that IS bound in twice.
    assert all(s.image_hash is not None for s in rows)
    pairs = ((0, 1), (0, 2), (1, 2))
    fine = [hamming(rows[a].image_hash, rows[b].image_hash) for a, b in pairs]
    assert min(fine) > DUP_HASH_DISTANCE

    # ... and the coarser grid this replaced could not tell them apart.
    raw = fitz.open(stream=data, filetype="pdf")
    coarse = [page_dhash(p, side=8, guard=False) for p in raw]
    raw.close()
    assert min(hamming(coarse[a], coarse[b]) for a, b in pairs) \
        <= DUP_HASH_DISTANCE


def test_one_of_those_sheets_bound_in_twice_is_still_caught():
    """Sharpening the hash must not cost the claim it exists to make."""
    pix = _lab_sheet("SB-01 / 0.50", LAB_VALUES[0])
    sheets = [pix, _lab_sheet("SB-02 / 1.20", LAB_VALUES[1]), pix]
    with open_document(_scan_doc(sheets)) as doc:
        rows = doc.page_map()
    assert (rows[2].duplicate_of, rows[2].duplicate_rule) == (0, "image")
    assert rows[1].duplicate_of is None
    assert hamming(rows[0].image_hash, rows[2].image_hash) == 0
