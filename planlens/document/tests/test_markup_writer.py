"""write_markups against planlens' own reader, on a portrait page and a sheet.

Every assertion here is a ROUND TRIP: a markup is written by displayed-frame
coordinates and then read back with :meth:`Document.markups`, which is the
contract the tool layer sells. Page 1 of the synthetic review document is
stored portrait with ``/Rotate 90``, so a writer that forgets the displayed
frame puts its boxes on the wrong quarter of the sheet and these tests say so.
"""

import json
import os

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document  # noqa: E402
from planlens.document.frame import bbox_iou  # noqa: E402
from planlens.document.markup_writer import (  # noqa: E402
    ANNOT_SUBTYPE, MarkupSpec, write_markups,
)
from planlens.testing.document_fixtures import (  # noqa: E402
    build_synthetic_review_document,
)


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


@pytest.fixture
def source(gt, tmp_path_factory):
    path = tmp_path_factory.mktemp("markup_writer") / "submittal.pdf"
    path.write_bytes(gt.pdf)
    return str(path)


def _written(doc, author):
    """Only the markups this module put on, by author, in id order."""
    return [m for m in doc.markups() if m.author == author]


def _one(doc, author, kind):
    matches = [m for m in _written(doc, author) if m.kind == kind]
    assert len(matches) == 1, [(m.kind, m.author) for m in doc.markups()]
    return matches[0]


AI = "GeotechStaffEngineer (AI draft)"


# -- every kind, on a portrait page and on a rotated sheet --------------------

@pytest.fixture
def four_kinds(source, gt, tmp_path):
    """One of each anchored kind, half on the letter page, half on the sheet."""
    out = str(tmp_path / "marked.pdf")
    note_at = (120.0, 180.0)
    box = (1200.0, 700.0, 1600.0, 820.0)
    callout_box = (1800.0, 1500.0, 2100.0, 1580.0)
    report = write_markups(source, out, [
        {"kind": "note", "page": gt.narrative_page,
         "comment": "State the datum for these elevations.",
         "point": list(note_at)},
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "Which borings reached rock?",
         "quote": "twelve borings"},
        {"kind": "box", "page": gt.sheet_page,
         "comment": "No dimension shown for this offset.",
         "bbox": list(box)},
        {"kind": "callout", "page": gt.sheet_page,
         "comment": "Confirm the pile embedment shown here.",
         "bbox": list(callout_box),
         "points_at": list(gt.reviewer_target)},
    ], author=AI)
    return report, out, {"note": note_at, "box": box, "callout": callout_box}


def test_every_kind_reads_back_as_the_annotation_it_claims(four_kinds, gt):
    report, out, _want = four_kinds
    assert report.n_written == 4 and report.n_skipped == 0
    with open_document(out) as doc:
        mine = _written(doc, AI)
        assert [m.kind for m in mine] == [
            ANNOT_SUBTYPE[k] for k in ("note", "highlight", "box", "callout")]
        assert [m.page for m in mine] == [gt.narrative_page, gt.narrative_page,
                                          gt.sheet_page, gt.sheet_page]
        assert [m.subject for m in mine] == ["Note", "Highlight", "Box",
                                             "Callout"]
        assert _one(doc, AI, "Square").text.startswith("No dimension shown")
        # Every one carries a date a reader can parse, not a raw PDF string.
        for m in mine:
            assert m.created and m.created[:2] == "20" and "T" in m.created
        # The document's own five markups are untouched beside them.
        assert len(doc.markups()) == 5 + 4


def test_a_note_and_a_box_land_on_the_box_that_was_asked_for(four_kinds):
    report, out, want = four_kinds
    with open_document(out) as doc:
        note = _one(doc, AI, "Text")
        square = _one(doc, AI, "Square")
    # The note is an icon of MuPDF's own size hung from the point given.
    nx, ny = want["note"]
    assert abs(note.bbox[0] - nx) < 0.5 and abs(note.bbox[1] - ny) < 0.5
    assert 8.0 <= note.bbox[2] - note.bbox[0] <= 32.0
    # The Square is compensated for MuPDF's /Rect padding, so it comes back as
    # the box the caller named — on a /Rotate 90 sheet.
    assert bbox_iou(square.bbox, want["box"]) >= 0.9
    for got, asked in zip(square.bbox, want["box"]):
        assert abs(got - asked) < 0.5
    # The report says what the READER will see, not what was asked for.
    by_kind = {w.kind: w for w in report.written}
    assert tuple(round(v, 1) for v in by_kind["box"].bbox) == \
        tuple(round(v, 1) for v in square.bbox)


def test_a_callout_on_the_rotated_sheet_points_at_the_spot(four_kinds, gt):
    report, out, want = four_kinds
    with open_document(out) as doc:
        callout = _one(doc, AI, "FreeText")
    assert callout.intent == "FreeTextCallout"
    tx, ty = gt.reviewer_target
    assert abs(callout.points_at[0] - tx) < 1.0
    assert abs(callout.points_at[1] - ty) < 1.0
    # Its /Rect encloses the leader as well as the text box (PDF 32000-1
    # 12.5.6.6), so the box asked for is INSIDE what reads back rather than
    # equal to it — which is why the report carries the read-back box.
    x0, y0, x1, y1 = want["callout"]
    cx0, cy0, cx1, cy1 = callout.bbox
    assert cx0 <= x0 + 2 and cy0 <= y0 + 2 and cx1 >= x1 - 2 and cy1 >= y1 - 2
    written = next(w for w in report.written if w.kind == "callout")
    assert [round(v, 1) for v in written.points_at] == [tx, ty]


# -- quotes -------------------------------------------------------------------

def test_a_highlight_covers_the_words_and_not_the_whole_line(source, gt,
                                                             tmp_path):
    out = str(tmp_path / "quoted.pdf")
    write_markups(source, out, [
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "state the datum", "quote": "approximately 40-foot"},
    ], author=AI)
    with open_document(out) as doc:
        hl = _one(doc, AI, "Highlight")
        line = next(ln for ln in doc.page(gt.narrative_page).lines
                    if "approximately 40-foot" in ln.text)
    # The search hit names a LINE; the line's own word boxes narrow it to the
    # words quoted, so a three-word comment does not paint the whole column.
    assert hl.bbox[2] - hl.bbox[0] < 0.5 * (line.bbox[2] - line.bbox[0])
    # MuPDF pads a text-markup /Rect around its quads, so the read-back box
    # CONTAINS the words rather than matching them to the point.
    assert hl.bbox[1] <= line.bbox[1] + 1 and hl.bbox[3] >= line.bbox[3] - 1


def test_writing_a_highlight_leaves_the_text_layer_searchable(source, gt,
                                                              tmp_path):
    out = str(tmp_path / "searchable.pdf")
    write_markups(source, out, [
        {"kind": "highlight", "page": gt.narrative_page, "comment": "check",
         "quote": "twelve borings"},
    ], author=AI)
    with open_document(source) as before, open_document(out) as after:
        assert (after.search("twelve borings")["n_hits"]
                == before.search("twelve borings")["n_hits"] >= 1)
        assert after.page(gt.narrative_page).text() == \
            before.page(gt.narrative_page).text()


def test_a_quote_across_a_line_break_highlights_both_lines(source, gt,
                                                           tmp_path):
    out = str(tmp_path / "wrapped.pdf")
    quote = " ".join(gt.phrase_across_lines)
    report = write_markups(source, out, [
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "one sentence, two lines", "quote": quote},
    ], author=AI)
    assert report.n_written == 1
    doc = fitz.open(out)
    try:
        annot = next(a for a in doc[gt.narrative_page].annots()
                     if (a.info or {}).get("title") == AI)
        quads = doc.xref_get_key(annot.xref, "QuadPoints")[1]
        # Eight numbers per quad: one for each line the phrase crosses.
        assert len([v for v in quads.strip("[] ").split() if v]) == 16
    finally:
        doc.close()


def test_a_quote_nobody_can_find_is_skipped_with_a_reason(source, gt,
                                                          tmp_path):
    out = str(tmp_path / "missing.pdf")
    report = write_markups(source, out, [
        {"kind": "highlight", "page": gt.narrative_page, "comment": "x",
         "quote": "PERMEABILITY OF THE CURTAIN WALL"},
        {"kind": "note", "page": gt.narrative_page, "comment": "this one goes on",
         "point": [100.0, 100.0]},
    ], author=AI)
    assert report.n_written == 1 and report.n_skipped == 1
    skipped = report.skipped[0]
    assert skipped["kind"] == "highlight" and skipped["index"] == 0
    assert "is not on page 0" in skipped["reason"]
    with open_document(out) as doc:
        assert [m.kind for m in _written(doc, AI)] == ["Text"]


def test_a_quote_with_a_letter_wrong_is_found_by_the_fuzzy_fallback(
        source, gt, tmp_path):
    pytest.importorskip("rapidfuzz")
    out = str(tmp_path / "fuzzy.pdf")
    report = write_markups(source, out, [
        {"kind": "highlight", "page": gt.narrative_page, "comment": "x",
         "quote": "Standard penetratian tests were performed"},
    ], author=AI)
    assert report.n_written == 1
    written = report.written[0]
    assert written.anchored_by == "quote_fuzzy" and written.score >= 80
    assert report.to_dict()["written"][0]["score"] >= 80


# -- replies ------------------------------------------------------------------

def test_a_reply_links_to_the_markup_it_answers(source, gt, tmp_path):
    out = str(tmp_path / "replied.pdf")
    # The fixture's own reviewer callout is p1.m0 on the sheet.
    report = write_markups(source, out, [
        {"kind": "reply", "page": gt.sheet_page,
         "comment": "Embedment revised to 6 m; see sheet S-2.",
         "reply_to": "p1.m0"},
    ], author=AI)
    assert report.n_written == 1
    assert report.written[0].in_reply_to == "p1.m0"
    with open_document(out) as doc:
        reply = _one(doc, AI, "Text")
        original = next(m for m in doc.markups() if m.id == "p1.m0")
        assert reply.in_reply_to == original.id
        assert reply.id in original.replies
        assert reply.text.startswith("Embedment revised")


def test_a_reply_to_a_markup_that_is_not_there_says_so(source, gt, tmp_path):
    out = str(tmp_path / "no_parent.pdf")
    report = write_markups(source, out, [
        {"kind": "reply", "page": gt.narrative_page, "comment": "x",
         "reply_to": "p1.m0"},
        {"kind": "reply", "page": gt.sheet_page, "comment": "x",
         "reply_to": "p1.m99"},
    ], author=AI)
    assert report.n_written == 0 and report.n_skipped == 2
    assert "on page 1, not page 0" in report.skipped[0]["reason"]
    assert "document_markups" in report.skipped[1]["reason"]


# -- the file on disk ---------------------------------------------------------

def test_append_accumulates_and_no_append_starts_again(source, gt, tmp_path):
    out = str(tmp_path / "running.pdf")
    first = write_markups(source, out, [
        {"kind": "note", "page": 0, "comment": "one", "point": [100.0, 100.0]},
    ], author=AI)
    assert first.appended is False
    second = write_markups(source, out, [
        {"kind": "note", "page": 0, "comment": "two", "point": [100.0, 140.0]},
    ], author=AI)
    assert second.appended is True
    with open_document(out) as doc:
        assert [m.text for m in _written(doc, AI)] == ["one", "two"]
    again = write_markups(source, out, [
        {"kind": "note", "page": 0, "comment": "three", "point": [100.0, 180.0]},
    ], author=AI, append=False)
    assert again.appended is False
    with open_document(out) as doc:
        assert [m.text for m in _written(doc, AI)] == ["three"]


def test_the_source_is_never_written_to(source, gt, tmp_path):
    before = open(source, "rb").read()
    write_markups(source, str(tmp_path / "copy.pdf"), [
        {"kind": "box", "page": gt.sheet_page, "comment": "x",
         "bbox": [100.0, 100.0, 400.0, 300.0]},
    ], author=AI)
    assert open(source, "rb").read() == before
    with pytest.raises(ValueError, match="never modifies"):
        write_markups(source, source, [], author=AI)


def test_bytes_in_a_path_out(gt, tmp_path):
    out = str(tmp_path / "from_bytes.pdf")
    report = write_markups(gt.pdf, out, [
        {"kind": "note", "page": 0, "comment": "from bytes",
         "point": [100.0, 100.0]},
    ], author=AI)
    assert report.n_written == 1 and os.path.isfile(out)


# -- what a caller gets wrong -------------------------------------------------

def test_a_spec_with_no_anchor_or_two_is_refused(source, tmp_path):
    out = str(tmp_path / "anchors.pdf")
    report = write_markups(source, out, [
        {"kind": "box", "page": 0, "comment": "nowhere"},
        {"kind": "box", "page": 0, "comment": "two places",
         "bbox": [1.0, 1.0, 2.0, 2.0], "point": [3.0, 3.0]},
        {"kind": "highlight", "page": 0, "comment": "a point is not text",
         "point": [100.0, 100.0]},
        {"kind": "note", "page": 99, "comment": "off the end",
         "point": [1.0, 1.0]},
    ], author=AI)
    assert report.n_written == 0
    reasons = [s["reason"] for s in report.skipped]
    assert "needs an anchor" in reasons[0]
    assert "names 2 anchors" in reasons[1]
    assert "needs text to sit over" in reasons[2]
    assert "pages 0-4" in reasons[3]


def test_a_bad_spec_is_a_ValueError_naming_what_is_wrong(source, tmp_path):
    out = str(tmp_path / "bad.pdf")
    with pytest.raises(ValueError, match="unknown markup kind"):
        write_markups(source, out, [{"kind": "scribble", "page": 0}])
    with pytest.raises(ValueError, match="unknown markup field"):
        write_markups(source, out, [{"kind": "note", "page": 0,
                                     "font": "bold"}])
    with pytest.raises(ValueError, match="has no page"):
        write_markups(source, out, [{"kind": "note", "comment": "x"}])
    with pytest.raises(ValueError, match="bbox must be"):
        write_markups(source, out, [{"kind": "box", "page": 0,
                                     "bbox": [1.0, 2.0]}])
    assert not os.path.exists(out)


def test_a_spec_object_works_as_well_as_a_dict(source, tmp_path):
    out = str(tmp_path / "typed.pdf")
    report = write_markups(source, out, [
        MarkupSpec(kind="note", page=0, comment="typed", point=(90.0, 90.0)),
    ], author=AI)
    assert report.n_written == 1
    assert json.loads(json.dumps(report.to_dict()))["n_written"] == 1


def test_a_per_markup_author_overrides_the_run_author(source, tmp_path):
    out = str(tmp_path / "authors.pdf")
    write_markups(source, out, [
        {"kind": "note", "page": 0, "comment": "mine", "point": [90.0, 90.0]},
        {"kind": "note", "page": 0, "comment": "theirs", "point": [90.0, 120.0],
         "author": "Reviewer C"},
    ], author=AI)
    with open_document(out) as doc:
        assert [m.author for m in doc.markups(pages=0)][-2:] == [AI,
                                                                 "Reviewer C"]


# -- circles, visible labels, and boxes read off an image (0.11.0) ------------

def _inside_ellipse(ring, x, y):
    x0, y0, x1, y1 = ring
    cx, cy = (x0 + x1) / 2.0, (y0 + y1) / 2.0
    a, b = (x1 - x0) / 2.0, (y1 - y0) / 2.0
    return ((x - cx) / a) ** 2 + ((y - cy) / b) ** 2 <= 1.0 + 1e-9


@pytest.mark.parametrize("which", ["narrative_page", "sheet_page"])
def test_a_circle_goes_round_its_whole_box_on_either_frame(source, gt,
                                                           tmp_path, which):
    """Portrait page and the /Rotate 90 sheet: the ring reads back as a red
    Circle whose ellipse holds every corner of the box it was given, and the
    box the report gives is the box the reader sees."""
    page = getattr(gt, which)
    box = (300.0, 200.0, 340.0, 214.0) if which == "narrative_page" \
        else (1300.0, 760.0, 1330.0, 790.0)
    out = str(tmp_path / "circle.pdf")
    report = write_markups(source, out, [
        {"kind": "circle", "page": page, "comment": "tag", "bbox": list(box)},
    ], author=AI)
    assert report.n_written == 1
    w = report.written[0]
    assert w.kind == "circle" and w.target == box
    assert all(_inside_ellipse(w.bbox, x, y)
               for x in (box[0], box[2]) for y in (box[1], box[3]))
    with open_document(out) as doc:
        ring = _one(doc, AI, "Circle")
        assert ring.subject == "Circle" and ring.color == "#d92626"
        assert bbox_iou(ring.bbox, w.bbox) > 0.97, (ring.bbox, w.bbox)


def test_a_tiny_target_still_gets_a_ring_a_reader_can_see(source, gt,
                                                          tmp_path):
    out = str(tmp_path / "tiny.pdf")
    report = write_markups(source, out, [
        {"kind": "circle", "page": gt.narrative_page, "comment": "dot",
         "bbox": [200.0, 200.0, 202.0, 202.0]}], author=AI)
    x0, y0, x1, y1 = report.written[0].bbox
    assert x1 - x0 >= 13.5 and y1 - y0 >= 13.5


@pytest.mark.parametrize("kind", ["circle", "box", "highlight"])
def test_a_label_is_drawn_beside_its_mark_and_tied_to_it(source, gt,
                                                         tmp_path, kind):
    out = str(tmp_path / f"label_{kind}.pdf")
    report = write_markups(source, out, [
        {"kind": kind, "page": gt.sheet_page, "comment": "penetration tag",
         "label": "GCE", "bbox": [1300.0, 760.0, 1330.0, 790.0]}], author=AI)
    w = report.written[0]
    assert w.label == "GCE" and w.label_bbox is not None
    with open_document(out) as doc:
        mine = _written(doc, AI)
        mark = next(m for m in mine if m.text == "penetration tag")
        label = next(m for m in mine if m.subject == "Label")
        assert label.kind == "FreeText" and label.text == "GCE"
        assert label.in_reply_to == mark.id and label.id in mark.replies
        # no stray leader: PyMuPDF's default /CL is stripped from a label
        assert label.points_at is None and not label.vertices
        assert bbox_iou(label.bbox, w.label_bbox) > 0.9
        # the label sits next to the mark, not across the sheet
        lx = (label.bbox[0] + label.bbox[2]) / 2.0
        ly = (label.bbox[1] + label.bbox[3]) / 2.0
        mx = (mark.bbox[0] + mark.bbox[2]) / 2.0
        my = (mark.bbox[1] + mark.bbox[3]) / 2.0
        assert abs(lx - mx) < 60 and abs(ly - my) < 60


def test_a_label_on_a_note_is_refused(source, tmp_path):
    with pytest.raises(ValueError, match="label is drawn beside"):
        write_markups(source, str(tmp_path / "x.pdf"), [
            {"kind": "note", "page": 0, "comment": "c", "label": "L",
             "point": [90.0, 90.0]}])


def test_a_box_read_off_an_image_is_converted_here(source, gt, tmp_path):
    """view + image_box (0-999 over the rendered view) lands exactly where
    image_box_to_page puts it — the field session's own numbers: a tag at
    [671,109,722,161] on view [467,487,733,773] is [645.7,518.2,659.2,533.1]."""
    from planlens.document.budget import image_box_to_page
    view, ibox = [467.0, 487.0, 733.0, 773.0], [671, 109, 722, 161]
    want = image_box_to_page(ibox, view, 1, 1, units="norm1000")
    assert [round(v, 1) for v in want] == [645.7, 518.2, 659.2, 533.1]
    out = str(tmp_path / "image_box.pdf")
    report = write_markups(source, out, [
        {"kind": "box", "page": gt.sheet_page, "comment": "c",
         "view": view, "image_box": ibox}], author=AI)
    w = report.written[0]
    assert w.anchored_by == "bbox"
    assert bbox_iou(w.bbox, want) > 0.97, (w.bbox, want)


def test_box_and_page_bbox_are_names_for_bbox(source, gt, tmp_path):
    out = str(tmp_path / "aliases.pdf")
    box = [1300.0, 760.0, 1330.0, 790.0]
    report = write_markups(source, out, [
        {"kind": "box", "page": gt.sheet_page, "comment": "a", "box": box},
        {"kind": "box", "page": gt.sheet_page, "comment": "b",
         "page_bbox": box}], author=AI)
    assert report.n_written == 2
    assert bbox_iou(report.written[0].bbox, report.written[1].bbox) > 0.99


@pytest.mark.parametrize("extra,match", [
    ({"view": [0, 0, 100, 100]}, "view and image_box go together"),
    ({"image_box": [0, 0, 10, 10]}, "view and image_box go together"),
    ({"bbox": [1, 1, 5, 5], "box": [1, 1, 5, 5]}, "gives its box 2 ways"),
    ({"bbox": [1, 1, 5, 5], "view": [0, 0, 100, 100],
      "image_box": [0, 0, 10, 10]}, "gives its box 2 ways"),
])
def test_a_box_given_two_ways_or_half_given_is_refused(source, tmp_path,
                                                      extra, match):
    spec = {"kind": "box", "page": 0, "comment": "c", **extra}
    with pytest.raises(ValueError, match=match):
        write_markups(source, str(tmp_path / "x.pdf"), [spec])


# -- the obvious guesses are read, not refused (Foundry brief 4, 2026-10-07) --

def test_the_obvious_guesses_are_read_with_a_note(source, gt, tmp_path):
    """8 of 14 marking runs on Foundry lost a round trip to a refusal: a
    ``color`` on every ring, kinds ``comment`` and ``text`` for a sticky
    note, and the anchor nested as ``{"anchor": {"quote": ...}}``. Each is
    read as what it plainly means, and the report says so per markup."""
    out = str(tmp_path / "guesses.pdf")
    report = write_markups(source, out, [
        {"kind": "circle", "page": gt.sheet_page, "comment": "tag",
         "label": "GCE", "bbox": [1200, 700, 1230, 712], "color": "#FF0000"},
        {"kind": "comment", "page": gt.narrative_page,
         "comment": "Which borings reached rock?", "quote": "twelve borings"},
        {"kind": "Text", "page": gt.narrative_page, "comment": "A note.",
         "point": [120, 180]},
        {"kind": "note", "page": gt.narrative_page, "comment": "Nested.",
         "anchor": {"quote": "twelve borings"}},
        {"kind": "note", "page": gt.narrative_page, "comment": "Plain.",
         "point": [130, 190]},
    ], author=AI)
    assert report.n_written == 5 and report.n_skipped == 0
    assert [w.kind for w in report.written] == ["circle", "note", "note",
                                                "note", "note"]
    notes = {a["index"]: " ".join(a["notes"]) for a in report.adjusted}
    assert set(notes) == {0, 1, 2, 3}
    assert "'color' is not a markup field" in notes[0] and "red" in notes[0]
    assert "kind 'comment' was written as a note" in notes[1]
    assert "kind 'text' was written as a note" in notes[2]
    assert "anchor object was read as the markup's own quote" in notes[3]
    assert report.written[3].anchored_by == "quote"
    assert json.loads(json.dumps(report.to_dict()))["adjusted"][0]["index"] == 0


def test_a_nested_anchor_that_contradicts_itself_is_refused(source, tmp_path):
    out = str(tmp_path / "bad_anchor.pdf")
    with pytest.raises(ValueError, match="given twice"):
        write_markups(source, out, [{"kind": "note", "page": 0, "comment": "x",
                                     "quote": "a", "anchor": {"quote": "b"}}])
    with pytest.raises(ValueError, match="not an anchor"):
        write_markups(source, out, [{"kind": "note", "page": 0, "comment": "x",
                                     "anchor": {"colour": "red"}}])


def test_target_names_what_the_mark_is_on(source, gt, tmp_path):
    out = str(tmp_path / "target.pdf")
    spec = MarkupSpec.from_dict({
        "kind": "callout", "page": gt.sheet_page,
        "comment": "Please confirm the embedment.", "target": " PILE TIP EL. ",
        "points_at": list(gt.reviewer_target)})
    assert spec.target == "PILE TIP EL." and not spec.adjustments
    assert "target" in MarkupSpec.fields_accepted()
    assert "adjustments" not in MarkupSpec.fields_accepted()
    report = write_markups(source, out, [spec], author=AI)
    assert report.n_written == 1
    with pytest.raises(ValueError, match="text, not a box"):
        MarkupSpec.from_dict({"kind": "box", "page": 0, "comment": "x",
                              "bbox": [1, 1, 5, 5], "target": [1, 1, 5, 5]})


# -- one thing marked twice (Foundry brief 4: a tag ringed twice) --------------

def test_one_thing_marked_twice_is_flagged(source, gt, tmp_path):
    """Two rings round one tag from two zooms (boxes a point or two apart)
    are one thing marked twice; rings round different things, or a box and a
    ring round the same thing, or two rings saying different things, are
    not."""
    out = str(tmp_path / "twice.pdf")
    p = gt.sheet_page
    report = write_markups(source, out, [
        {"kind": "circle", "page": p, "comment": "tag", "label": "GCE",
         "bbox": [1200.0, 700.0, 1210.4, 704.3]},
        {"kind": "circle", "page": p, "comment": "tag", "label": "GCE",
         "bbox": [1400.0, 900.0, 1410.4, 904.3]},
        {"kind": "circle", "page": p, "comment": "tag", "label": "GCE",
         "bbox": [1200.6, 700.4, 1211.5, 704.9]},           # T6 again
        {"kind": "box", "page": p, "comment": "tag", "label": "GCE",
         "bbox": [1200.0, 700.0, 1210.4, 704.3]},           # another kind
        {"kind": "circle", "page": p, "comment": "tag", "label": "GCG",
         "bbox": [1400.0, 900.0, 1410.4, 904.3]},           # another label
    ], author=AI)
    assert report.n_written == 5
    assert [(d["index"], d["same_as"]) for d in report.duplicates] == [(2, 0)]
    dup = report.to_dict()["duplicates"][0]
    assert dup["kind"] == "circle" and dup["label"] == "GCE"
    assert dup["overlap"] > 0.5


def test_two_comments_on_one_spot_are_two_comments(source, gt, tmp_path):
    out = str(tmp_path / "two_comments.pdf")
    report = write_markups(source, out, [
        {"kind": "note", "page": gt.narrative_page, "comment": "First point.",
         "point": [120, 180]},
        {"kind": "note", "page": gt.narrative_page, "comment": "Second point.",
         "point": [120, 180]},
        {"kind": "note", "page": gt.narrative_page, "comment": "First point.",
         "point": [121, 181]},
    ], author=AI)
    assert [(d["index"], d["same_as"]) for d in report.duplicates] == [(2, 0)]
