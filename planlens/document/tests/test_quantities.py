"""Quantities the document STATES — the phrase table and the page pass.

The phrase table is the contract: one row per form the extractor claims to
read, each with the value, unit and kind it must produce. Every sentence is
invented. The forms come from a bake-off against a general-purpose extractor
on real submittal text (see DESIGN.md, "Quantities"), so a row removed here is
a capability removed, not a test tidied.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

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
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 90, 540, 400), PROSE, fontsize=11,
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
    doc = pymupdf.open()
    page = doc.new_page(width=1224, height=792)
    annot = page.add_rect_annot(pymupdf.Rect(72, 100, 400, 120))
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
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    note = page.add_freetext_annot(pymupdf.Rect(100, 100, 400, 160),
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


# ---------------------------------------------------------------------------
# PL-11: the unit-first form of a calculation printout, ``Label (unit): value``
# ---------------------------------------------------------------------------

#: (text, label, value, units, kind). Invented labels and values.
LABEL_UNIT = [
    ("Qx (kip): 512.0", "Qx", 512.0, "kip", "force"),
    ("Lateral Capacity Qx (kip): 512.0", "Lateral Capacity Qx", 512.0,
     "kip", "force"),
    ("Tip Depth (ft): 63.5", "Tip Depth", 63.5, "ft", "length"),
    ("Heave (in) = -0.35", "Heave", -0.35, "in", "length"),
    ("Toe Pressure (ksf): 4.75", "Toe Pressure", 4.75, "ksf",
     "pressure_or_stress"),
    ("Fill Weight (pcf): 118", "Fill Weight", 118, "pcf", "unit_weight"),
    ("Wall Batter (deg): 7.5", "Wall Batter", 7.5, "deg", "angle"),
    ("Shaft Load (kN): 1,840.2", "Shaft Load", 1840.2, "kN", "force"),
    ("Max. Shear (kN) : 96.1", "Max. Shear", 96.1, "kN", "force"),
    ("Drift (m): 2.10E-03", "Drift", 2.1e-03, "m", "length"),
    # the unit printed again after the value is the same unit, one mention
    ("Cover (in): 3.0 in", "Cover", 3.0, "in", "length"),
    # a fraction is an inch or a foot
    ("Bar Dia. (in): 3/4", "Bar Dia.", 0.75, "in", "length"),
    # a label naming an elevation gets the elevation kind main gives
    # "Elev. 12.5 m", in the unit printed
    ("Bottom Elev. (ft): 104.5", "Bottom Elev.", 104.5, "ft", "elevation"),
    ("EL (ft): 104.5", "EL", 104.5, "ft", "elevation"),
    ("Top of Wall Elevation (m): 13.5", "Top of Wall Elevation", 13.5, "m",
     "elevation"),
    # the one unit spelled with its own full stop
    ("Area (sq. ft.): 1,250", "Area", 1250, "ft^2", "area"),
]

#: The form with something after the value that does NOT make the value an
#: operand: punctuation, prose, a hedge, a check mark, a datum.
#: (text, label, value, units, kind, qualifier)
LABEL_UNIT_IN_CONTEXT = [
    ("Qx (kip) = 512.0.", "Qx", 512.0, "kip", "force", None),
    ("Qx (kip): 512.0 in the east bay", "Qx", 512.0, "kip", "force", None),
    ("Su (kPa): 64 at 3 m depth", "Su", 64, "kPa", "pressure_or_stress",
     None),
    ("Qx (kip): 512.0 OK", "Qx", 512.0, "kip", "force", None),
    ("Spacing (ft): 9 typ.", "Spacing", 9, "ft", "length", "typical"),
    ("Embedment (ft): 14.0 minimum", "Embedment", 14.0, "ft", "length",
     "minimum"),
    ("Elev. (ft) = 104.5 NAVD88", "Elev.", 104.5, "ft", "elevation", None),
]


@pytest.mark.parametrize("text,label,value,units,kind", LABEL_UNIT)
def test_label_unit_value_is_read(text, label, value, units, kind):
    got = scan_text(text)
    assert len(got) == 1, [str(m) for m in got]
    men = got[0]
    assert men.label == label
    assert men.value == pytest.approx(value)
    assert men.units == units
    assert men.kind == kind
    assert men.text == text.strip()
    assert men.to_dict()["label"] == label


@pytest.mark.parametrize("text,label,value,units,kind,qualifier",
                         LABEL_UNIT_IN_CONTEXT)
def test_label_unit_value_is_read_in_context(text, label, value, units, kind,
                                             qualifier):
    got = [m for m in scan_text(text) if m.label]
    assert len(got) == 1, [str(m) for m in scan_text(text)]
    men = got[0]
    assert (men.label, men.units, men.kind, men.qualifier) == (
        label, units, kind, qualifier)
    assert men.value == pytest.approx(value)
    assert text.startswith(men.text)


def test_a_printout_line_with_two_fields_is_two_mentions():
    got = scan_text("Qx (kip): 512.0 Qy (kip): 88.4")
    assert [(m.label, m.value) for m in got] == [("Qx", 512.0),
                                                 ("Qy", 88.4)]


def test_column_gaps_separate_fields():
    got = scan_text("Tip Depth (ft):  63.5    Mmax (kip):  12")
    assert [(m.label, m.value, m.units) for m in got] == [
        ("Tip Depth", 63.5, "ft"), ("Mmax", 12, "kip")]


def test_a_label_stops_at_the_value_before_it():
    got = scan_text("Embed (ft): 22.0 ft and Qx (kip): 512.0")
    assert [(m.label, m.value, m.units) for m in got] == [
        ("Embed", 22.0, "ft"), ("Qx", 512.0, "kip")]


def test_a_label_stops_at_a_sentence_end():
    (men,) = scan_text("The run was repeated. Qx (kip): 512.0")
    assert men.label == "Qx"


def test_other_forms_carry_no_label():
    (men,) = scan_text("Each strut takes 50 kN.")
    assert men.label is None
    assert "label" not in men.to_dict()


#: Near misses: each must still yield NOTHING.
LABEL_UNIT_NOTHING = [
    "Boring X-9 (2031): 14.5",          # a year in parentheses, not a unit
    "Load case (see Note 4): 3",        # a note, not a unit
    "Spacing (typ.): 6",                # a hedge, not a unit
    "Start (min): 9",                   # not a unit this module reads
    "Start (in): 12:30",                # a time after a unit-like word
    "Reading (ft): 7/14/2031",          # a date, not a length
    "(kip): 512.0",                     # no label to say what it is
    "Total (kip) 512.0",                # no colon or equals: not the form
    # a hand calculation: the first number after "=" is an operand
    "Vertical stress (psf) = 120 x 10 = 1,200",
    "Vertical stress (psf) = 120 × 10 = 1200",
    "Pa (kip) = 0.5(0.33)(0.120)(20)^2 = 7.9",
    "Qs (kip) = 2.5 * 3.14 * 40 = 314",
    "q (psf) = 0.5 Ka gamma H",         # symbols, not a unit or prose
    "Moment (kip) = 12 - 3",            # a backwards range is subtraction
    "gamma (pcf) = 120 Ka = 0.33",      # a second equals sign
    "Qx (kip): 512.0 < 600 OK",         # a check, not the value alone
    # a ratio is not a fraction of a percent
    "Ratio (%): 45/55",
    "Mix (%): 60/40",
    # a series: the first entry is not the value
    "Fill height (m): 0, 1.5, 3.0, 4.5",
]


@pytest.mark.parametrize("text", LABEL_UNIT_NOTHING)
def test_label_unit_near_misses_are_refused(text):
    assert scan_text(text) == [], [str(m) for m in scan_text(text)]


def test_a_station_after_the_form_stays_a_station():
    # "12+40" is a station; the parenthesized ft does not turn it into 12 ft.
    got = scan_text("Offset (ft): 12+40")
    assert [(m.label, m.units, m.kind) for m in got] == [
        (None, "sta", "station")]


def test_two_different_units_are_not_reconciled():
    # The parenthesis says m, the value says ft: the ordinary reading of the
    # value stands and no labelled mention is invented.
    got = scan_text("Depth (m): 12.5 ft")
    assert [(m.label, m.value, m.units) for m in got] == [(None, 12.5, "ft")]


#: A range is one mention with its label, never its lower bound.
#: (text, label, value, value_to, units)
LABEL_UNIT_RANGES = [
    ("Settlement (in): 0.5 to 1.0 in", "Settlement", 0.5, 1.0, "in"),
    ("Embedment (ft): 12 to 15 ft", "Embedment", 12, 15, "ft"),
    ("Sample Depth (ft): 5.0-6.5", "Sample Depth", 5.0, 6.5, "ft"),
    ("Sample Depth (ft): 5.0 - 6.5", "Sample Depth", 5.0, 6.5, "ft"),
    ("Depth (m): 1.5 to 3.0", "Depth", 1.5, 3.0, "m"),
]


@pytest.mark.parametrize("text,label,value,value_to,units", LABEL_UNIT_RANGES)
def test_a_labelled_range_is_one_mention(text, label, value, value_to, units):
    (men,) = scan_text(text)
    assert (men.label, men.units) == (label, units)
    assert (men.value, men.value_to) == (pytest.approx(value),
                                         pytest.approx(value_to))


def test_a_range_main_read_keeps_its_reading_and_gains_a_label():
    # "0.5 to 1.0 in" alone is main's reading; the label adds a name only.
    (plain,) = scan_text("0.5 to 1.0 in")
    (labelled,) = scan_text("Settlement (in): 0.5 to 1.0 in")
    assert (labelled.value, labelled.value_to, labelled.units,
            labelled.kind) == (plain.value, plain.value_to, plain.units,
                               plain.kind)


def test_feet_and_inches_after_a_label_are_one_value():
    # The feet-inches reading is taken over, with the label; it is never
    # split into 12 ft and a stray 6 in.
    for text in ("Wall Height (ft): 12'-6\"", "Height (ft): 12' 6\""):
        got = scan_text(text)
        assert [(m.label, m.value, m.units) for m in got] == [
            (text.split(" (")[0], 12.5, "ft")], [str(m) for m in got]


def test_feet_and_inches_under_another_unit_are_not_relabelled():
    # "(in)" over a feet-inches value: two units, so the plain reading
    # stands, without a label.
    got = scan_text("Height (in): 12'-6\"")
    assert [(m.label, m.value, m.units) for m in got] == [(None, 12.5, "ft")]


def test_a_label_never_swallows_the_previous_fields_value():
    cases = {
        "qa=3.0ksf Pc (kip): 437.0": [(None, 3.0, "ksf"),
                                      ("Pc", 437.0, "kip")],
        "Unit Wt=120pcf Phi (deg): 30": [(None, 120.0, "pcf"),
                                         ("Phi", 30.0, "deg")],
        # a word that starts with a digit is a value: no label is left
        "Wall 3ft (in): 4": [(None, 3.0, "ft")],
    }
    for text, want in cases.items():
        got = scan_text(text)
        assert [(m.label, m.value, m.units) for m in got] == want, text


def test_fields_separated_by_a_comma_are_both_read():
    got = scan_text("Phi (deg) = 30, c (psf) = 200")
    assert [(m.label, m.value, m.units) for m in got] == [
        ("Phi", 30.0, "deg"), ("c", 200.0, "psf")]
    got = scan_text("Qx (kip): 512.0, Qy (kip): 88.4")
    assert [(m.label, m.value) for m in got] == [("Qx", 512.0),
                                                 ("Qy", 88.4)]


def test_two_fields_joined_with_and_are_both_read():
    got = scan_text("Footing width B (ft) = 6.0 and length L (ft) = 10.0")
    assert [(m.label, m.value) for m in got] == [("Footing width B", 6.0),
                                                 ("length L", 10.0)]


def test_elevation_labels_are_found_by_the_elevation_filter():
    got = scan_text("Bottom Elev. (ft): 104.5 Wall Height (ft): 14.0")
    assert [m.label for m in filter_mentions(got, kinds=["elevation"])] == [
        "Bottom Elev."]
    assert [m.label for m in filter_mentions(got, kinds=["length"])] == [
        "Wall Height"]
    # the same rule main applies to "El. 104.5 ft"
    (plain,) = scan_text("El. 104.5 ft")
    assert plain.kind == "elevation"


#: The next field's label spelled like a unit is a label, not a unit after
#: the value: "M" is not metres and "Mn" is not meganewtons here.
#: (text, label, value, units)
NEXT_FIELD_SPELLED_LIKE_A_UNIT = [
    ("P (kN): 437.0 M (kN-m): 120.0", "P", 437.0, "kN"),
    ("P (kN): 437.0    M (kN-m): 120.0", "P", 437.0, "kN"),
    ("Pc (kip): 437.0\nM (kip-ft): 120.0", "Pc", 437.0, "kip"),
    ("Axial (kip): 437.0 Mn (kip-ft): 200.0", "Axial", 437.0, "kip"),
]


@pytest.mark.parametrize("text,label,value,units",
                         NEXT_FIELD_SPELLED_LIKE_A_UNIT)
def test_the_next_fields_label_is_not_a_unit(text, label, value, units):
    got = scan_text(text)
    assert [(m.label, m.value, m.units) for m in got] == [
        (label, value, units)], [str(m) for m in got]


def test_a_label_stays_on_its_own_line_with_line_breaks_given():
    # A document block joins its lines with ONE space; the caller passes
    # the join offsets so the label stops at the printed line's start.
    first = "PILE HEAD CONDITIONS"
    text = first + " " + "Lateral Load (kip): 25.0"
    (men,) = scan_text(text, line_breaks=[len(first)])
    assert men.label == "Lateral Load"
    assert men.span[0] == len(first) + 1
    # without the break the joined line is one run of words
    (joined,) = scan_text(text)
    assert joined.label != "Lateral Load"


def test_a_label_stays_on_its_own_line_through_a_document():
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_textbox(pymupdf.Rect(72, 90, 540, 200),
                        "PILE HEAD CONDITIONS\nLateral Load (kip): 25.0",
                        fontsize=10, fontname="helv")
    content = doc.tobytes()
    doc.close()
    with open_document(content, name="printout") as d:
        lines = d.page(0).lines
        (men,) = d.quantities()
    (second,) = [ln for ln in lines if ln.text.startswith("Lateral Load")]
    assert (men.label, men.value, men.units) == ("Lateral Load", 25.0, "kip")
    assert men.text == "Lateral Load (kip): 25.0"
    assert men.line_ids == [second.id]
    assert men.bbox == tuple(round(v, 1) for v in second.bbox)


def test_square_feet_with_full_stops_is_read():
    # "sq. ft." is the one unit whose own spelling ends in a full stop; it
    # used to raise KeyError rather than read.
    (men,) = scan_text("The slab covers 450 sq. ft. of grade.")
    assert (men.value, men.units, men.kind) == (450.0, "ft^2", "area")
