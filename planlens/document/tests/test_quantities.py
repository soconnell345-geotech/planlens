"""Quantities the document STATES — the phrase table and the page pass.

The phrase table is the contract: one row per form the extractor claims to
read, each with the value, unit and kind it must produce. Every sentence is
invented. The forms come from a bake-off against a general-purpose extractor
on real submittal text (see DESIGN.md, "Quantities"), so a row removed here is
a capability removed, not a test tidied.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document  # noqa: E402
from planlens.document.model import SOURCE_CAD_HIDDEN  # noqa: E402
from planlens.document.quantities import (  # noqa: E402
    KINDS, QuantityMention, filter_mentions, scan_text,
)

# (sentence, value, value_to, units, kind, qualifier)
TABLE = [
    # feet and inches are ONE value, in feet
    ("The toe extends 7'-6\" below subgrade.", 7.5, None, "ft", "length", None),
    ("Provide 12 ft 3 in of embedment.", 12.25, None, "ft", "length", None),
    # a length written three ways
    ("Anchors at 40-foot centers.", 40, None, "ft", "length", None),
    ("Anchors at 40 ft centers.", 40, None, "ft", "length", None),
    ("Anchors at 40 feet centers.", 40, None, "ft", "length", None),
    # metric
    ("The wall is 6300 mm deep.", 6300, None, "mm", "length", None),
    ("The wall is 6.3 m deep.", 6.3, None, "m", "length", None),
    # angle, three ways
    ("A back slope of 21° was assumed.", 21, None, "deg", "angle", None),
    ("A friction angle of 21 deg was assumed.", 21, None, "deg", "angle", None),
    ("A friction angle of 21 degrees was assumed.", 21, None, "deg", "angle",
     None),
    # pressure / stress, unit weight, force
    ("Bearing pressure of 2,500 psf governs.", 2500, None, "psf",
     "pressure_or_stress", None),
    ("Backfill at 120 pcf.", 120, None, "pcf", "unit_weight", None),
    ("Residual soil at 18 kN/m³ saturated.", 18, None, "kN/m^3",
     "unit_weight", None),
    ("Undrained strength of 150 kPa.", 150, None, "kPa", "pressure_or_stress",
     None),
    ("End bearing of 2 tsf was used.", 2, None, "tsf", "pressure_or_stress",
     None),
    ("Each strut takes 50 kN.", 50, None, "kN", "force", None),
    ("Lock off at 171 kN pre-load.", 171, None, "kN", "force", None),
    # elevation: the label is the claim, the unit may be absent
    ("Top of beam at EL. 1684.", 1684, None, "", "elevation", None),
    ("The soffit is at Elev. 12.5 m.", 12.5, None, "m", "elevation", None),
    # station and slope
    ("Work starts at STA 10+50.", 1050, None, "sta", "station", None),
    ("Cut at 2H:1V for the temporary face.", 2, None, "H:V", "slope", None),
    ("Regrade to 1V:3H before the rains.", 3, None, "H:V", "slope", None),
    # percent
    ("Allow 1% of the retained height.", 1, None, "%", "percent", None),
    ("Compact to 95 percent of maximum dry density.", 95, None, "%",
     "percent", None),
    # area, volume, count
    ("The slab covers 4,500 sf.", 4500, None, "ft^2", "area", None),
    ("Remove 1,200 cy of fill.", 1200, None, "yd^3", "volume", None),
    ("A 3 m³ test pit was opened.", 3, None, "m^3", "volume", None),
    ("4 borings were completed.", 4, None, "borings", "count", None),
    # ranges are ONE mention
    ("Depths of 20 to 35 feet below grade.", 20, 35, "ft", "length", None),
    ("Pressures of 2,500 to 3,200 psf apply.", 2500, 3200, "psf",
     "pressure_or_stress", None),
    # qualifiers
    ("Borings at approximately 40-foot centers.", 40, None, "ft", "length",
     "approximately"),
    ("A minimum of 12 ft of embedment.", 12, None, "ft", "length", "minimum"),
    ("Cover shall be 3 in. minimum.", 3, None, "in", "length", "minimum"),
    ("Waler spacing 8 ft typ.", 8, None, "ft", "length", "typical"),
    ("The crest sits at 6300 mm ± 50.", 6300, None, "mm", "length",
     "plus_minus"),
    ("Loads exceeding 171 kN need review.", 171, None, "kN", "force",
     "greater_than"),
    # a calculation printout's scientific notation
    ("DEFLECTION TOLERANCE 2.540E-07 M", 2.54e-07, None, "m", "length", None),
]

#: Sentences that must yield NOTHING. A bare number is not a mention, and
#: these are the forms that most tempt an extractor into inventing one.
NOTHING = [
    "See Section 4 and Table 3 for the parameters.",
    "Page 7 of 42 summarizes the testing.",
    "The factor of safety is 1.4 under drained conditions.",
    "Borings were advanced 12 in the northern block of the site.",
    "The contract was awarded in 2024.",
    "Twelve borings were completed.",          # a word is not a number
    "-4.348E+01 3.532E+01 -3.691E+01 2.657E-01 3.970E+05",
    "Reinforcement complies with EN 10080.",
]


@pytest.mark.parametrize("sentence,value,value_to,units,kind,qualifier", TABLE)
def test_phrase_table(sentence, value, value_to, units, kind, qualifier):
    got = scan_text(sentence)
    assert len(got) == 1, [str(m) for m in got]
    men = got[0]
    assert men.value == pytest.approx(value)
    assert men.value_to == (None if value_to is None
                            else pytest.approx(value_to))
    assert men.units == units
    assert men.kind == kind
    assert men.qualifier == qualifier
    assert men.text and men.text in sentence
    assert men.kind in KINDS


@pytest.mark.parametrize("sentence", NOTHING)
def test_a_bare_number_is_not_a_mention(sentence):
    assert scan_text(sentence) == [], [str(m) for m in scan_text(sentence)]


def test_units_known_only_where_measure_can_convert():
    (feet,) = scan_text("The toe is 12 ft deep.")
    (pressure,) = scan_text("Bearing pressure of 2,500 psf.")
    assert feet.units_known is True
    assert pressure.units_known is False


def test_nothing_is_converted_silently():
    (metric,) = scan_text("The wall is 6300 mm deep.")
    assert (metric.value, metric.units) == (6300.0, "mm")
    # and the caller converts deliberately, through measure
    from planlens.ir.measure import Quantity
    assert Quantity(metric.value, metric.units).to("m").value == pytest.approx(6.3)


def test_an_exponent_is_not_read_as_a_bare_digit():
    # The failure this pins: "2.540E-07 M" read as SEVEN metres, which is a
    # plausible number seven orders of magnitude wrong. Measured on a real
    # submittal's program output, where the form is everywhere.
    (men,) = scan_text("CLOSURE TOLERANCE 2.540E-07 M")
    assert men.value == pytest.approx(2.54e-07)


def test_a_range_is_one_mention_not_two():
    got = scan_text("Depths of 20 to 35 feet below existing grade.")
    assert len(got) == 1
    assert (got[0].value, got[0].value_to) == (20.0, 35.0)


def test_a_tolerance_is_two_mentions_not_a_range():
    # "45 ft +/- 2 ft" states a nominal AND a tolerance, both in feet. They
    # are not a range (a range runs 45 to 47), so both are reported, and the
    # plus-minus marks both as parts of one tolerance statement.
    got = scan_text("Free length 45 ft ± 2 ft as installed.")
    assert [(m.value, m.qualifier) for m in got] == [
        (45.0, "plus_minus"), (2.0, "plus_minus")]
    assert all(m.value_to is None for m in got)


def test_a_qualifier_is_not_borrowed_from_the_previous_clause():
    got = scan_text("Spacing is 8 ft typ. and the anchor is 45 ft long.")
    assert [m.qualifier for m in got] == ["typical", None]


def test_filter_by_kind_and_unit():
    mentions = scan_text("12 ft of wall at 2,500 psf, cut at 2H:1V.")
    assert len(filter_mentions(mentions, kinds=["length"])) == 1
    assert len(filter_mentions(mentions, units=["PSF"])) == 1
    assert len(filter_mentions(mentions, kinds=["slope", "length"])) == 2
    assert filter_mentions(mentions, kinds=["volume"]) == []


def test_mention_serializes_compactly():
    (men,) = scan_text("Borings at approximately 40-foot centers.")
    d = men.to_dict()
    assert d["value"] == 40.0 and d["units"] == "ft"
    assert d["qualifier"] == "approximately"
    assert d["units_known"] is True
    assert "value_to" not in d and "markup_id" not in d


# -- through a document ---------------------------------------------------

PROSE = (
    "Twelve borings were advanced at approximately 40-foot centers and "
    "extended to depths of 20 to 35 feet below existing grade. The design "
    "bearing pressure is 2,500 psf and the backfill unit weight is 120 pcf. "
    "Temporary cut slopes shall be 2H:1V."
)


@pytest.fixture(scope="module")
def prose_pdf():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(fitz.Rect(72, 90, 540, 400), PROSE, fontsize=11,
                        fontname="helv")
    out = doc.tobytes()
    doc.close()
    return out


def test_a_prose_page_reports_its_quantities(prose_pdf):
    with open_document(prose_pdf, name="prose") as doc:
        mentions = doc.quantities()
    kinds = [m.kind for m in mentions]
    assert kinds.count("length") == 2
    assert kinds.count("pressure_or_stress") == 1
    assert kinds.count("unit_weight") == 1
    assert kinds.count("slope") == 1
    for men in mentions:
        assert men.page == 0
        assert men.line_ids and men.bbox is not None
        assert men.source == "pdf_text"


def test_a_value_broken_across_a_line_break_is_still_one_mention(prose_pdf):
    # The paragraph wraps; "approximately 40-foot" is split by the textbox.
    with open_document(prose_pdf, name="prose") as doc:
        mentions = doc.quantities(kinds=["length"])
    forty = [m for m in mentions if m.value == 40.0]
    assert forty and forty[0].qualifier == "approximately"


def test_kind_and_unit_filters_reach_the_document(prose_pdf):
    with open_document(prose_pdf, name="prose") as doc:
        assert all(m.kind == "slope" for m in doc.quantities(kinds=["slope"]))
        assert all(m.units == "psf" for m in doc.quantities(units=["psf"]))
        assert doc.quantities(kinds=["volume"]) == []


def test_max_mentions_caps_after_filtering(prose_pdf):
    with open_document(prose_pdf, name="prose") as doc:
        assert len(doc.quantities(max_mentions=2)) == 2
        assert len(doc.quantities(kinds=["slope"], max_mentions=2)) == 1


# -- the displayed frame on a rotated page ---------------------------------

def test_bbox_is_in_the_displayed_frame_on_a_rotated_page():
    """A quantity's box must land on the page as a viewer shows it.

    The fixture writes the note at a known DISPLAYED position on a /Rotate 90
    sheet; a box in the unrotated frame would fall outside the displayed page
    entirely, which is what this asserts against.
    """
    from planlens.testing.document_fixtures import (
        build_synthetic_review_document,
    )
    gt = build_synthetic_review_document()
    with open_document(gt.pdf, name="synthetic") as doc:
        mentions = doc.quantities(pages=gt.sheet_page)
        summary = doc.summary(gt.sheet_page)
    elevations = [m for m in mentions if m.kind == "elevation"]
    assert elevations, [str(m) for m in mentions]
    x0, y0, x1, y1 = elevations[0].bbox
    assert 0 <= x0 < x1 <= summary.width
    assert 0 <= y0 < y1 <= summary.height
    # the sheet is displayed landscape (36 x 24 in), so the box proves it
    assert summary.width > summary.height


def test_hidden_cad_text_quantities_say_so():
    doc = fitz.open()
    page = doc.new_page(width=1224, height=792)
    annot = page.add_rect_annot(fitz.Rect(72, 100, 400, 120))
    annot.set_info(title="AutoCAD SHX Text", content="TOP OF WALL EL. 1684")
    annot.set_border(width=0)
    annot.update(opacity=0)
    content = doc.tobytes()
    doc.close()
    with open_document(content, name="sheet") as d:
        mentions = d.quantities()
    assert [m.source for m in mentions] == [SOURCE_CAD_HIDDEN]
    assert mentions[0].kind == "elevation" and mentions[0].value == 1684.0


def test_a_reviewers_comment_is_read_for_quantities():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    note = page.add_freetext_annot(fitz.Rect(100, 100, 400, 160),
                                   "Revise the embedment to 6.5 m.",
                                   fontsize=11)
    note.set_info(title="Reviewer Q", subject="Callout")
    note.update()
    content = doc.tobytes()
    doc.close()
    with open_document(content, name="marked") as d:
        mentions = d.quantities()
        without = d.quantities(include_markups=False)
    assert len(mentions) == 1
    assert mentions[0].value == pytest.approx(6.5)
    assert mentions[0].source == "pdf_annotation"
    assert mentions[0].markup_id
    assert without == []
