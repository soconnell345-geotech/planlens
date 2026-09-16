"""The measurement calibration a PDF stores: ratios, viewports, dimension markups.

The synthetic sheet is built at /Rotate 0 and /Rotate 90 from the same numbers,
and the truth comes from ``planlens.testing.scale_fixtures``, which derives
displayed coordinates by hand rather than through the code under test. Two
things a page rotation must not do are asserted directly: it must not leave the
viewport box in the unrotated frame, and it must not change a derived length by
any amount.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document  # noqa: E402
from planlens.document.advice import page_advice  # noqa: E402
from planlens.document.scale import (  # noqa: E402
    SOURCE_MEASUREMENT_MARKUP, SOURCE_VIEWPORT, Viewport, page_viewports,
    parse_pdf_object, parse_ratio, parse_stated_value, ratio_magnification,
    scale_factor, to_quantity, viewport_at,
)
from planlens.testing.scale_fixtures import (  # noqa: E402
    FEET_PER_POINT, RATIO_TEXT, build_synthetic_scaled_sheet_pdf,
    build_synthetic_uncalibrated_sheet_pdf,
)
from planlens.tools.formatting import render_markup  # noqa: E402


# ---------------------------------------------------------------------------
# Ratio strings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected,magnification", [
    ("1 in = 20 ft", (1.0, "in", 20.0, "ft"), 240.0),
    ('1" = 20\'', (1.0, "in", 20.0, "ft"), 240.0),
    ("1:100", (1.0, None, 100.0, None), 100.0),
    ("1 cm = 1 m", (1.0, "cm", 1.0, "m"), 100.0),
    ('1/8" = 1\'-0"', (0.125, "in", 1.0, "ft"), 96.0),
    ('3/32" = 1\'', (0.09375, "in", 1.0, "ft"), 128.0),
    ("SCALE: 1\" = 40'", (1.0, "in", 40.0, "ft"), 480.0),
    ("1 : 200", (1.0, None, 200.0, None), 200.0),
])
def test_parse_ratio_forms(text, expected, magnification):
    got = parse_ratio(text)
    assert got is not None, text
    for a, b in zip(got, expected):
        if isinstance(b, str) or b is None:
            assert a == b, text
        else:
            assert a == pytest.approx(b), text
    assert ratio_magnification(got) == pytest.approx(magnification)


@pytest.mark.parametrize("text", [
    None, "", " ", "  ", "banana", "N.T.S.", "AS NOTED", "1:0", "0:100",
    "= 20 ft", "1 in =", "see the title block",
])
def test_parse_ratio_rejects_junk(text):
    """A blank /R is what one real file writes; it must read as no ratio."""
    assert parse_ratio(text) is None


def test_ratio_magnification_needs_both_units():
    assert ratio_magnification(parse_ratio("1:100")) == pytest.approx(100.0)
    assert ratio_magnification((1.0, "in", 20.0, None)) is None
    assert ratio_magnification(None) is None


@pytest.mark.parametrize("text,value,unit", [
    ("12'-6\"", 12.5, "ft"),
    ("24.5 ft", 24.5, "ft"),
    ("3.05 m", 3.05, "m"),
    ("1,250 SF", 1250.0, "ft^2"),
    ("Length: 24.5 ft", 24.5, "ft"),
    ("8' 3\"", 8.25, "ft"),
])
def test_parse_stated_value(text, value, unit):
    got = parse_stated_value(text)
    assert got is not None, text
    assert got[0] == pytest.approx(value)
    assert got[1] == unit


@pytest.mark.parametrize("text", [None, "", "see note 4", "45.0", "REVISE"])
def test_parse_stated_value_rejects_non_values(text):
    assert parse_stated_value(text) is None


# ---------------------------------------------------------------------------
# The raw-syntax reader
# ---------------------------------------------------------------------------

def test_parse_pdf_object_reads_the_shapes_real_files_use():
    obj = parse_pdf_object(
        "[ << /Type /Viewport /Name (Plan) /BBox [ 0 0 612 792 ] "
        "/Measure 12 0 R >> ]")
    assert isinstance(obj, list) and len(obj) == 1
    entry = obj[0]
    assert entry["Type"] == "/Viewport"
    assert entry["Name"] == "Plan"
    assert entry["BBox"] == [0, 0, 612, 792]
    assert entry["Measure"].num == 12


def test_parse_pdf_object_tolerates_nonsense():
    assert parse_pdf_object("") is None
    assert parse_pdf_object(None) is None
    assert parse_pdf_object("<< /R ") == {"R": None}


# ---------------------------------------------------------------------------
# Viewports on the page
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", [0, 90])
def test_viewport_read_in_the_displayed_frame(rotation):
    gt = build_synthetic_scaled_sheet_pdf(rotation)
    doc = fitz.open(stream=gt.pdf, filetype="pdf")
    try:
        viewports, warnings = page_viewports(doc, doc[0], 0)
        assert warnings == []
        assert len(viewports) == 1
        vp = viewports[0]
        assert vp.kind == "rectilinear"
        assert vp.name == gt.viewport_name
        assert vp.source == SOURCE_VIEWPORT
        assert vp.ratio == RATIO_TEXT
        assert vp.x_unit == "ft" and vp.y_unit == "ft"
        assert vp.x_per_point == pytest.approx(FEET_PER_POINT)
        assert vp.y_per_point == pytest.approx(FEET_PER_POINT)
        assert vp.distance_unit == "ft"
        assert vp.area_unit == "sq ft"
        assert vp.is_calibrated and not vp.is_identity
        assert vp.magnification == pytest.approx(240.0)
        assert vp.bbox == pytest.approx(gt.viewport_bbox_displayed)
        # The box must be inside the page AS DISPLAYED, which is the whole
        # point of converting it.
        assert vp.bbox[2] <= gt.page_size[0] + 0.01
        assert vp.bbox[3] <= gt.page_size[1] + 0.01
    finally:
        doc.close()


def test_viewport_bbox_actually_moves_with_rotation():
    """/BBox is unrotated in the file, so the two rotations must differ."""
    flat = build_synthetic_scaled_sheet_pdf(0).viewport_bbox_displayed
    turned = build_synthetic_scaled_sheet_pdf(90).viewport_bbox_displayed
    assert flat != turned


def test_no_viewports_is_an_empty_list_not_a_warning():
    doc = fitz.open()
    doc.new_page()
    try:
        viewports, warnings = page_viewports(doc, doc[0], 0)
        assert viewports == []
        assert warnings == []
    finally:
        doc.close()


def test_empty_vp_array_is_normal():
    """Seven sheets of a real submittal carry /VP [] — not an error."""
    doc = fitz.open()
    page = doc.new_page()
    doc.xref_set_key(page.xref, "VP", "[]")
    try:
        viewports, warnings = page_viewports(doc, doc[0], 0)
        assert viewports == []
        assert warnings == []
    finally:
        doc.close()


def test_identity_viewport_is_read_but_not_called_a_scale():
    """The 1:1 default a real sheet stores: present, and not a drawing scale."""
    gt = build_synthetic_uncalibrated_sheet_pdf()
    doc = fitz.open(stream=gt.pdf, filetype="pdf")
    try:
        viewports, _ = page_viewports(doc, doc[0], 0)
        assert len(viewports) == 1
        vp = viewports[0]
        assert vp.kind == "rectilinear"
        assert vp.x_per_point == pytest.approx(0.01389)
        assert vp.x_unit is None
        assert vp.is_identity
        assert not vp.is_calibrated
        assert vp.label == "1:1 (uncalibrated default)"
        assert any("blank unit" in w for w in vp.warnings)
        assert scale_factor(vp) is None
    finally:
        doc.close()


def test_geo_measure_is_reported_not_parsed():
    doc = fitz.open()
    page = doc.new_page()
    doc.xref_set_key(page.xref, "VP",
                     "[ << /Type /Viewport /Name (Layer) /BBox [0 0 612 792] "
                     "/Measure << /Type /Measure /Subtype /GEO "
                     "/GPTS [ 45 -93 45 -92 46 -92 46 -93 ]"
                     " /Bounds [0 1 0 0 1 0 1 1] >> >> ]")
    try:
        viewports, warnings = page_viewports(doc, doc[0], 0)
        assert warnings == []
        vp = viewports[0]
        assert vp.kind == "geo"
        assert not vp.is_calibrated
        assert vp.label == "georeferenced"
        assert any("georeferenced" in w for w in vp.warnings)
        assert scale_factor(vp) is None
    finally:
        doc.close()


@pytest.mark.parametrize("measure,expect", [
    ("<< /Type /Measure /Subtype /RL >>", "no readable /X"),
    ("<< /Type /Measure /Subtype /RL /X [ << /C 0 /U (ft) >> ] >>",
     "not a positive factor"),
    ("<< /Type /Measure /Subtype /XX /X [ << /C 1 /U (ft) >> ] >>",
     "not understood"),
    ("<< /Type /Measure /Subtype /RL /R (wibble) "
     "/X [ << /C 0.2777 /U (ft) >> ] >>", "not a ratio"),
    ("<< /Type /Measure /Subtype /RL /R (1 in = 20 ft) "
     "/X [ << /C 0.5 /U (ft) >> ] >>", "disagree"),
])
def test_malformed_measure_warns_and_does_not_raise(measure, expect):
    doc = fitz.open()
    page = doc.new_page()
    doc.xref_set_key(page.xref, "VP",
                     f"[ << /Type /Viewport /BBox [0 0 612 792] "
                     f"/Measure {measure} >> ]")
    try:
        viewports, _ = page_viewports(doc, doc[0], 0)
        assert len(viewports) == 1
        assert any(expect in w for w in viewports[0].warnings), \
            viewports[0].warnings
    finally:
        doc.close()


def test_viewport_without_a_bbox_governs_the_page():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    doc.xref_set_key(page.xref, "VP",
                     "[ << /Type /Viewport /Measure << /Type /Measure "
                     "/Subtype /RL /R (1:100) /X [ << /C 1.389 /U (cm) >> ] "
                     ">> >> ]")
    try:
        viewports, _ = page_viewports(doc, doc[0], 0)
        vp = viewports[0]
        assert vp.bbox == (0.0, 0.0, 612.0, 792.0)
        assert any("no readable /BBox" in w for w in vp.warnings)
        assert vp.is_calibrated
    finally:
        doc.close()


# ---------------------------------------------------------------------------
# viewport_at
# ---------------------------------------------------------------------------

def _vp(bbox, **kw):
    return Viewport(page=0, bbox=bbox, kind="rectilinear", x_unit="ft",
                    x_per_point=FEET_PER_POINT, y_unit="ft",
                    y_per_point=FEET_PER_POINT, **kw)


def test_viewport_at_picks_the_smallest_containing_box():
    outer = _vp((0.0, 0.0, 600.0, 800.0), name="sheet")
    inner = _vp((100.0, 100.0, 200.0, 200.0), name="detail")
    assert viewport_at([outer, inner], (150.0, 150.0)) is inner
    assert viewport_at([outer, inner], (400.0, 400.0)) is outer


def test_viewport_at_falls_back_to_a_whole_page_viewport():
    whole = _vp((72.0, 72.0, 1656.0, 2520.0))
    outside = (10.0, 10.0)
    assert not whole.contains(outside)
    assert viewport_at([whole], outside, page_size=(1728.0, 2592.0)) is whole


def test_viewport_at_refuses_a_small_viewport_for_an_outside_point():
    detail = _vp((100.0, 100.0, 200.0, 200.0))
    assert viewport_at([detail], (10.0, 10.0),
                       page_size=(1728.0, 2592.0)) is None
    assert viewport_at([], (10.0, 10.0)) is None


def test_viewport_at_will_not_choose_between_two_boxes():
    a = _vp((0.0, 0.0, 100.0, 100.0))
    b = _vp((200.0, 200.0, 300.0, 300.0))
    assert viewport_at([a, b], (150.0, 150.0)) is None


# ---------------------------------------------------------------------------
# Measurement markups
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("rotation", [0, 90])
def test_markup_states_and_derives_the_same_length(rotation):
    gt = build_synthetic_scaled_sheet_pdf(rotation)
    with open_document(gt.pdf, name="scaled") as doc:
        dims = [m for m in doc.markups() if m.measure is not None]
        assert len(dims) == 1
        mm = dims[0].measure
        assert mm.intent == gt.markup_intent
        assert mm.scale.source == SOURCE_MEASUREMENT_MARKUP
        assert mm.scale.ratio == RATIO_TEXT
        assert mm.stated is not None
        assert mm.stated.units == "ft"
        assert mm.stated.value == pytest.approx(gt.stated_ft, rel=1e-4)
        assert mm.derived is not None
        assert mm.derived.units == "ft"
        assert mm.derived.value == pytest.approx(gt.length_ft, rel=1e-6)
        assert mm.agreement == pytest.approx(0.0, abs=1e-4)
        assert mm.warnings == []


def test_a_rotation_does_not_change_a_derived_length():
    """The measurement is of the ground, not of how the page is displayed."""
    lengths = []
    for rotation in (0, 90, 180, 270):
        gt = build_synthetic_scaled_sheet_pdf(rotation)
        with open_document(gt.pdf) as doc:
            mm = [m.measure for m in doc.markups() if m.measure][0]
            lengths.append(mm.derived.value)
    assert lengths[0] == pytest.approx(350.0)
    for value in lengths[1:]:
        assert value == pytest.approx(lengths[0], rel=1e-12)


def test_ordinary_markups_carry_no_measure():
    from planlens.testing.document_fixtures import (
        build_synthetic_review_document)
    gt = build_synthetic_review_document()
    with open_document(gt.pdf) as doc:
        assert doc.markups()
        assert all(m.measure is None for m in doc.markups())


def test_dimension_intent_without_a_measure_says_so():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    annot = page.add_line_annot(fitz.Point(100, 100), fitz.Point(200, 100))
    annot.set_info(title="Someone", content="10 ft")
    annot.update()
    doc.xref_set_key(annot.xref, "IT", "/LineDimension")
    data = doc.tobytes()
    doc.close()
    with open_document(data) as d:
        mm = [m.measure for m in d.markups() if m.measure][0]
        assert mm.intent == "LineDimension"
        assert mm.derived is None
        assert mm.stated is not None and mm.stated.units == "ft"
        assert any("no /Measure dictionary" in w for w in mm.scale.warnings)


def test_a_disagreeing_markup_is_reported_not_reconciled():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    annot = page.add_line_annot(fitz.Point(100, 100), fitz.Point(172, 100))
    annot.set_info(title="Someone", content="99 ft")   # 72 pt = 20 ft at 1"=20'
    annot.update()
    doc.xref_set_key(annot.xref, "IT", "/LineDimension")
    doc.xref_set_key(annot.xref, "Measure",
                     "<< /Type /Measure /Subtype /RL /R (1 in = 20 ft) "
                     "/X [ << /U (ft) /C 0.2777777778 >> ] >>")
    data = doc.tobytes()
    doc.close()
    with open_document(data) as d:
        mm = [m.measure for m in d.markups() if m.measure][0]
        assert mm.stated.value == pytest.approx(99.0)
        assert mm.derived.value == pytest.approx(20.0)
        assert mm.agreement == pytest.approx(0.798, abs=0.01)
        assert any("differ by" in w for w in mm.warnings)


# ---------------------------------------------------------------------------
# The bridge to planlens.ir.measure
# ---------------------------------------------------------------------------

def test_to_quantity_converts_points_through_a_stored_scale():
    gt = build_synthetic_scaled_sheet_pdf(0)
    doc = fitz.open(stream=gt.pdf, filetype="pdf")
    try:
        vp = page_viewports(doc, doc[0], 0)[0][0]
    finally:
        doc.close()
    q = to_quantity(vp, 72.0)
    assert q.units == "ft"
    assert q.value == pytest.approx(20.0)
    assert q.confidence == 1.0
    assert q.scale_known is True
    assert "pdf_viewport" in q.basis
    assert RATIO_TEXT in q.basis
    assert q.to("m").value == pytest.approx(6.096)
    assert to_quantity(vp, 72.0, units="m").value == pytest.approx(6.096)


def test_to_quantity_refuses_without_a_resolved_scale():
    q = to_quantity(None, 72.0)
    assert q.units == "pt"
    assert q.scale_known is False
    assert q.confidence == 1.0          # the geometry is still exact
    with pytest.raises(ValueError):
        q.to("ft")


def test_scale_factor_names_the_markup_source():
    gt = build_synthetic_scaled_sheet_pdf(0)
    with open_document(gt.pdf) as doc:
        mm = [m.measure for m in doc.markups() if m.measure][0]
    factor, unit, basis = scale_factor(mm.scale)
    assert factor == pytest.approx(FEET_PER_POINT)
    assert unit == "ft"
    assert basis.startswith(SOURCE_MEASUREMENT_MARKUP)


# ---------------------------------------------------------------------------
# What reaches the reader
# ---------------------------------------------------------------------------

def test_page_map_row_carries_the_stored_scale():
    gt = build_synthetic_scaled_sheet_pdf(0)
    with open_document(gt.pdf) as doc:
        summary = doc.page_map()[0]
        assert len(summary.viewports) == 1
        assert summary.stored_scale == f"{RATIO_TEXT} [{SOURCE_VIEWPORT}]"
        row = summary.to_dict(detail=False)
        assert row["scale"] == f"{RATIO_TEXT} [{SOURCE_VIEWPORT}]"


def test_page_map_row_omits_scale_when_the_file_stores_none():
    from planlens.testing.document_fixtures import (
        build_synthetic_review_document)
    gt = build_synthetic_review_document()
    with open_document(gt.pdf) as doc:
        row = doc.page_map([gt.sheet_page])[0].to_dict(detail=False)
        assert "scale" not in row


def test_toolkit_page_map_shows_the_scale():
    from planlens.tools import ReviewToolkit
    gt = build_synthetic_scaled_sheet_pdf(0)
    kit = ReviewToolkit(resolve_source=lambda key: gt.pdf)
    try:
        handle = kit.call("open_document", {"source": "sheet.pdf"})["handle"]
        out = kit.call("document_page_map", {"handle": handle})
        assert any(RATIO_TEXT in str(row.get("scale", ""))
                   for row in out["rows"])
    finally:
        kit.close()


def test_markup_rendering_shows_stated_and_derived():
    gt = build_synthetic_scaled_sheet_pdf(0)
    with open_document(gt.pdf) as doc:
        markup = [m for m in doc.markups() if m.measure][0]
        text = render_markup(markup)
    assert RATIO_TEXT in text
    assert "states" in text and "path measures" in text
    payload = markup.to_dict()
    assert payload["measure"]["units"] == "ft"
    assert payload["measure"]["scale"] == RATIO_TEXT


def test_advice_says_when_a_sheet_stores_no_scale():
    from planlens.testing.document_fixtures import (
        build_synthetic_review_document)
    gt = build_synthetic_review_document()
    with open_document(gt.pdf) as doc:
        summary = doc.summary(gt.sheet_page)
        assert summary.kind == "drawing_sheet"
        assert any("stores no calibrated scale" in a
                   for a in page_advice(summary))


def test_advice_is_silent_when_the_sheet_is_calibrated():
    gt = build_synthetic_scaled_sheet_pdf(0)
    with open_document(gt.pdf) as doc:
        summary = doc.summary(0)
        assert summary.kind == "drawing_sheet"
        assert not any("stores no calibrated scale" in a
                       for a in page_advice(summary))
