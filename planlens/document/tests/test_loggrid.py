"""log_grid on synthetic boring logs whose answers are known.

Two pages of one form, one on a 5 ft ruler and one on a 1 m ruler, plus the
same form with the ruler taken off. Everything asserted here is stated by
:mod:`planlens.testing.loggrid_fixtures` — the ruler, the column bands, the
layer tops, the samples and the fields — so a rule that starts guessing shows
up as a failure and not as a plausible number.
"""

from __future__ import annotations

import pytest

from planlens.document import open_document
from planlens.document.loggrid import (
    Cell, LogGrid, classify_header, log_grid, numbers_in,
)
from planlens.testing import (
    build_imperial_log, build_log_without_ruler, build_metric_log,
)


@pytest.fixture(scope="module")
def imperial():
    gt = build_imperial_log()
    with open_document(gt.pdf) as doc:
        yield gt, log_grid(doc, 0)


@pytest.fixture(scope="module")
def metric():
    gt = build_metric_log()
    with open_document(gt.pdf) as doc:
        yield gt, log_grid(doc, 0)


@pytest.fixture(scope="module")
def no_ruler():
    gt = build_log_without_ruler()
    with open_document(gt.pdf) as doc:
        yield gt, log_grid(doc, 0)


# ---------------------------------------------------------------------------
# The ruler
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_ruler_is_the_printed_scale(which, request):
    gt, grid = request.getfixturevalue(which)
    assert grid.unit == gt.unit
    ruler = grid.rulers[0]
    assert ruler.kind == "depth"
    # points per unit of depth, the wrong way up
    assert ruler.slope == pytest.approx(1.0 / gt.points_per_unit, rel=1e-3)
    assert ruler.step == pytest.approx(gt.ticks[1] - gt.ticks[0], rel=1e-6)
    assert len(ruler.ticks) == len(gt.ticks)
    assert [round(v, 3) for _, v in ruler.ticks] == list(gt.ticks)
    # An exact form gives an exact fit; anything else means the reader is
    # fitting through something that is not the ruler.
    assert ruler.residual < 0.02 * ruler.step
    assert ruler.confidence > 0.9
    assert ruler.evidence.get("regular_steps") is True


@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_ruler_reads_the_body_top_as_zero(which, request):
    gt, grid = request.getfixturevalue(which)
    assert grid.layers[0].top == pytest.approx(gt.depth_at_body_top, abs=1e-6)


def test_the_layer_contact_column_does_not_win_the_ruler(imperial):
    """The margin ticks fit the same line; the printed ruler is the ruler."""
    _gt, grid = imperial
    ruler = grid.rulers[0]
    column = grid.column(ruler.column_id)
    assert column is not None
    assert "depth" in column.names
    assert column.header.startswith("DEPTH (")


# ---------------------------------------------------------------------------
# Columns
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_every_column_lands_in_its_own_band(which, request):
    gt, grid = request.getfixturevalue(which)
    for name, (x0, x1) in gt.columns.items():
        found = [c for c in grid.columns if name in c.names]
        assert found, f"no column named {name}"
        col = found[0]
        assert col.x0 == pytest.approx(x0, abs=1.0)
        assert col.x1 == pytest.approx(x1, abs=1.0)


def test_a_turned_header_is_read_in_reading_order(imperial):
    _gt, grid = imperial
    headers = {c.header for c in grid.columns}
    assert "WATER CONTENT (%)" in headers
    assert "DRY UNIT WEIGHT (pcf)" in headers
    assert "ATTERBERG LIMITS LL-PL-PI" in headers


def test_one_header_can_name_several_columns(imperial):
    _gt, grid = imperial
    atterberg = [c for c in grid.columns
                 if c.header == "ATTERBERG LIMITS LL-PL-PI"][0]
    assert set(atterberg.names) == {"plasticity_index", "liquid_limit",
                                    "plastic_limit"}
    # "42-21-21" has the shape of a blow record. The header says it is not.
    assert "blows" not in atterberg.names


def test_a_generic_header_is_named_by_its_values(imperial):
    _gt, grid = imperial
    field_tests = [c for c in grid.columns
                   if c.header == "FIELD TEST RESULTS"][0]
    assert "blows" in field_tests.names
    assert "n_value" in field_tests.names
    assert set(field_tests.evidence["names_from_values"]) == {"blows",
                                                              "n_value"}


def test_an_unheaded_description_column_is_found_by_its_prose(imperial):
    """The only label over the description band belongs to the depth strip."""
    _gt, grid = imperial
    desc = [c for c in grid.columns if "description" in c.names][0]
    assert desc.header == "DEPTH"          # what the page prints there
    assert desc.evidence["description_from_prose"] >= 2
    assert "depth" not in desc.names       # a column of sentences is not depth


# ---------------------------------------------------------------------------
# Cells
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_every_sample_is_a_cell_at_its_depth(which, request):
    gt, grid = request.getfixturevalue(which)
    tolerance = 0.15 if gt.unit == "m" else 0.5
    for depth, blows, n_value in gt.samples:
        for text in (blows, n_value):
            hits = [c for c in grid.cells("blows")
                    if c.text == text
                    and abs((c.depth or -999) - depth) <= tolerance]
            assert hits, f"{text} not found in a blows column near {depth}"


@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_index_properties_land_in_their_own_columns(which, request):
    gt, grid = request.getfixturevalue(which)
    tolerance = 0.15 if gt.unit == "m" else 0.5
    for depth, wc, duw in gt.index_tests:
        for name, text in (("water_content", wc), ("dry_unit_weight", duw)):
            hits = [c for c in grid.cells(name)
                    if c.text == text
                    and abs((c.depth or -999) - depth) <= tolerance]
            assert hits, f"{name} {text} not found near {depth}"


def test_a_stacked_column_keeps_both_values_as_text(imperial):
    """Blows and N share one column at one depth; neither is parsed away."""
    _gt, grid = imperial
    at_two = [c for c in grid.cells("blows")
              if 1.5 <= (c.depth or 0) <= 3.0]
    texts = {c.text for c in at_two}
    assert "5-9-12" in texts and "N=21" in texts
    triple = [c for c in at_two if c.text == "5-9-12"][0]
    assert triple.numbers == (5.0, 9.0, 12.0)


def test_a_cell_carries_the_interval_its_box_covers(imperial):
    _gt, grid = imperial
    cell = [c for c in grid.rows if c.text == "5-9-12"][0]
    assert cell.depth_top < cell.depth < cell.depth_bottom
    assert cell.depth_bottom - cell.depth_top < 1.0
    assert cell.bbox[0] > 350.0


def test_cells_are_not_interpreted(imperial):
    _gt, grid = imperial
    for cell in grid.rows:
        assert isinstance(cell, Cell)
        assert cell.text == cell.text.strip()
    assert numbers_in("5-9-12") == (5.0, 9.0, 12.0)
    assert numbers_in("N=21") == (21.0,)
    assert numbers_in("50/3\"") == (50.0, 3.0)


# ---------------------------------------------------------------------------
# Layers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_layer_tops_are_the_depths_the_form_prints(which, request):
    gt, grid = request.getfixturevalue(which)
    assert len(grid.layers) == len(gt.layers)
    for read, (top, bottom, text) in zip(grid.layers, gt.layers):
        assert read.top == pytest.approx(top, abs=1e-6)
        if bottom is None:
            assert read.bottom is None
        else:
            assert read.bottom == pytest.approx(bottom, abs=1e-6)
        assert read.description.startswith(text.split(",")[0])


def test_a_layer_name_underline_is_not_a_stratum_line(imperial):
    """Each layer name is underlined; three layers, not six."""
    _gt, grid = imperial
    assert len(grid.layers) == 3
    assert [ly.source for ly in grid.layers] == ["page_top", "depth_tick",
                                                 "depth_tick"]


def test_the_margin_ticks_are_not_joined_into_the_prose(imperial):
    _gt, grid = imperial
    for layer in grid.layers:
        assert " 4 " not in f" {layer.description} "
        assert " 16 " not in f" {layer.description} "
    # they are still emitted as cells, named depth, with their boxes
    ticks = [c for c in grid.rows
             if c.column == "depth" and c.bbox[0] < 100.0]
    assert {c.text for c in ticks} == {"4", "16"}


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("which", ["imperial", "metric"])
def test_the_fields_printed_outside_the_body(which, request):
    gt, grid = request.getfixturevalue(which)
    for key, value in gt.fields.items():
        assert key in grid.fields, f"{key} missing"
        assert value in grid.fields[key]


def test_a_label_takes_the_value_under_it_when_there_is_none_beside(imperial):
    _gt, grid = imperial
    assert grid.fields["drilling_method"] == "Hollow Stem Auger"


def test_the_groundwater_note_is_kept_whole(imperial):
    _gt, grid = imperial
    assert grid.fields["groundwater"].startswith("Groundwater encountered at")


def test_a_field_carries_the_page_and_box_it_came_from(imperial):
    _gt, grid = imperial
    page, bbox = grid.field_boxes["hammer_type"]
    assert page == 0
    assert len(bbox) == 4 and bbox[2] > bbox[0]


# ---------------------------------------------------------------------------
# A page with no ruler
# ---------------------------------------------------------------------------

def test_no_ruler_means_no_depths_and_a_warning(no_ruler):
    _gt, grid = no_ruler
    assert not grid.has_ruler
    assert grid.rulers == {}
    assert any("no depth ruler" in w for w in grid.warnings)
    assert grid.rows, "the cells are still placed in their columns"
    assert all(c.depth is None for c in grid.rows)
    assert all(c.depth_top is None and c.depth_bottom is None
               for c in grid.rows)
    assert grid.layers == []
    assert grid.unit is None


def test_no_ruler_still_reads_the_columns_and_the_fields(no_ruler):
    _gt, grid = no_ruler
    names = {c.name for c in grid.columns}
    assert "water_content" in names and "dry_unit_weight" in names
    assert grid.fields["boring_id"] == "B-12"


# ---------------------------------------------------------------------------
# The header vocabulary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("DEPTH (Ft.)", "depth"),
    ("Depth, feet", "depth"),
    ("PROFONDEUR (m)", "depth"),
    ("Profundidad", "depth"),
    ("ELEVATION", "elevation"),
    ("MATERIAL DESCRIPTION", "description"),
    ("DESCRIPTION DES SOLS", "description"),
    ("Descripcion de suelos", "description"),
    ("BLOWS PER 6 IN.", "blows"),
    ("Nombre de coups", "blows"),
    ("GOLPES POR 6\"", "blows"),
    ("N VALUE", "n_value"),
    ("RECOVERY %", "recovery"),
    ("RQD", "rqd"),
    ("SAMPLE TYPE", "sample_type"),
    ("ECHANTILLON", "sample_id"),
    ("WATER CONTENT (%)", "water_content"),
    ("Teneur en eau", "water_content"),
    ("DRY UNIT WEIGHT (pcf)", "dry_unit_weight"),
    ("PERCENT FINES", "fines"),
    ("POCKET PEN. (tsf)", "pocket_pen"),
    ("TORVANE", "torvane"),
    ("USCS", "uscs"),
    ("GRAPHIC LOG", "graphic"),
    ("REMARKS", "remarks"),
    ("Essais de laboratoire", "tests"),
])
def test_headers_map_onto_the_vocabulary(header, expected):
    names, _unit = classify_header(header)
    assert names and names[0] == expected


@pytest.mark.parametrize("header", [
    "WATER LEVEL OBSERVATIONS", "GROUNDWATER", "Niveau d'eau",
])
def test_a_water_level_column_is_never_a_water_content_column(header):
    names, _unit = classify_header(header)
    assert "water_content" not in names


@pytest.mark.parametrize("header,unit", [
    ("DEPTH (Ft.)", "ft"), ("DEPTH (m)", "m"), ("Depth, feet", "ft"),
    ("PROFONDEUR (metres)", "m"), ("Elevation (FT)", "ft"),
    ("WATER CONTENT (%)", None),
])
def test_the_unit_comes_off_the_header(header, unit):
    _names, found = classify_header(header)
    assert found == unit


def test_an_unknown_header_keeps_its_text(imperial):
    """Nothing is forced into the vocabulary; ``other`` keeps the words."""
    names, _unit = classify_header("BOREHOLE STABILITY INDEX")
    assert names == ()


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------

def test_to_dict_is_json_ready(imperial):
    import json
    _gt, grid = imperial
    payload = grid.to_dict()
    text = json.dumps(payload)
    assert '"depth_unit": "ft"' in text
    assert payload["n_rows"] == len(grid.rows)
    assert payload["layers"][0]["top"] == 0.0
    compact = grid.to_dict(rows=False)
    assert "rows" not in compact and compact["n_rows"] == payload["n_rows"]


def test_empty_grid_is_still_a_grid():
    grid = LogGrid()
    assert grid.to_dict()["n_rows"] == 0
    assert grid.cells("depth") == []
    assert grid.column("nope") is None


def test_a_page_that_is_not_a_log_at_all_still_says_it_has_no_depths():
    """The two ways of having no ruler are reported in the same words.

    One page holds a form whose ruler is missing; another holds no form at
    all and the reading stops before the ruler is ever looked for. A caller
    checking whether it may trust a depth should not have to know which
    happened, so both say "no depth ruler was found".
    """
    from planlens.document.loggrid import NO_RULER
    from planlens.testing import build_synthetic_report

    gt = build_synthetic_report()
    with open_document(gt.pdf) as doc:
        grid = log_grid(doc, 0)          # the report's cover page
    assert not grid.has_ruler
    assert any(NO_RULER in w for w in grid.warnings)
    assert all(c.depth is None for c in grid.rows)


# ---------------------------------------------------------------------------
# Signed numbers, and the contest a header must win
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,value", [
    ("-2.5", -2.5), ("−4.0", -4.0), ("–5.5", -5.5),
    ("-8.9", -8.9), ("13-", 13.0), ("13", 13.0), ("+7", 7.0),
    ("0.2", 0.2), ("-", None), ("--", None), ("N=21", None), ("5-9-12", None),
])
def test_a_leading_dash_is_a_minus_and_a_trailing_one_is_a_tick(text, value):
    from planlens.document.loggrid import _single_number
    assert _single_number(text) == value


def test_elevations_below_datum_do_not_make_a_depth_ruler():
    """A column reading -2.5, -4.0, -5.5 falls; it is not a depth scale.

    Read unsigned it RISES, and it used to be chosen as the ruler over the
    scale the page prints.
    """
    from planlens.document.loggrid import _fit_ruler
    ticks = [(100.0, -2.5), (140.0, -4.0), (180.0, -5.5), (220.0, -7.0),
             (260.0, -8.5)]
    assert _fit_ruler(ticks, rising=True) is None
    falling = _fit_ruler(ticks, rising=False)
    assert falling is not None
    assert falling[1] < 0                       # slope: value falls down y


def test_a_column_the_form_calls_depth_beats_a_longer_unnamed_one():
    from planlens.document.loggrid import Column, _ruler_candidates

    named = Column(id="a", page=0, x0=0.0, x1=10.0, name="depth",
                   names=("depth",), header="Depth Scale (m)")
    anonymous = Column(id="b", page=0, x0=20.0, x1=30.0, name="other")
    ticks = {
        # three even ticks under a header that says depth
        "a": [(100.0, 1.0), (200.0, 2.0), (300.0, 3.0)],
        # nineteen even ticks under a header that says nothing
        "b": [(100.0 + 10.0 * i, float(i)) for i in range(19)],
    }
    rising, _falling = _ruler_candidates(ticks, {"a": named, "b": anonymous}, 0)
    assert rising[0].column_id == "a"


def test_an_elevation_ruler_still_needs_a_header_that_says_elevation():
    from planlens.document.loggrid import Column, _ruler_candidates

    unnamed = Column(id="b", page=0, x0=0.0, x1=10.0, name="other")
    ticks = {"b": [(100.0, -2.5), (200.0, -4.0), (300.0, -5.5)]}
    _rising, falling = _ruler_candidates(ticks, {"b": unnamed}, 0)
    assert falling == []


# ---------------------------------------------------------------------------
# The gINT default template's headers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("header,expected", [
    ("Depth Scale (m)", "depth"),
    ("Depth Scale (feet)", "depth"),
    ("Elev. (ft)", "elevation"),
    ("Elev. (m)", "elevation"),
    ("ELEV", "elevation"),
    ("Number", "sample_id"),
    ("Sample Data", "sample_id"),
    ("Type", "sample_type"),
    ("Recov. (in)", "recovery"),
    ("Recov. (cm)", "recovery"),
    ("Penetr. resist. BL/6in", "blows"),
    ("Penetr. resist. BL/15cm", "blows"),
    ("N-Value (Blows/ft)", "n_value"),
    ("N-Value (Blows/30cm)", "n_value"),
    ("USCS", "uscs"),
    ("Sample Description", "description"),
    ("MATERIAL SYMBOL", "graphic"),
    ("Remarks (Drilling Fluid, Depth of Casing, Water Level)", "remarks"),
])
def test_the_gint_default_template_reads(header, expected):
    names, _unit = classify_header(header)
    assert names and names[0] == expected, names


def test_a_header_names_itself_before_it_qualifies_itself():
    """The earliest phrase wins, and the longest of two starting together."""
    assert classify_header("Remarks (Depth of Casing)")[0][0] == "remarks"
    assert classify_header("Sample Description")[0][0] == "description"
    assert classify_header("TEST TYPE")[0][0] == "tests"
    assert classify_header("SAMPLE TYPE")[0][0] == "sample_type"
    # and a header naming several still names all of them
    assert set(classify_header("N-Value (Blows/ft)")[0]) == {"n_value",
                                                             "blows"}


@pytest.mark.parametrize("header,unit", [
    ("Depth Scale (m)", "m"), ("Depth Scale (feet)", "ft"),
    ("Elev. (ft)", "ft"), ("Recov. (in)", None),
])
def test_a_parenthesised_unit_is_read_off_the_gint_headers(header, unit):
    _names, found = classify_header(header)
    assert found == unit


# ---------------------------------------------------------------------------
# A form drawn twice, with a title block across its head
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def overprinted():
    from planlens.testing import build_overprinted_log
    gt = build_overprinted_log()
    with open_document(gt.pdf) as doc:
        yield gt, doc, log_grid(doc, 0)


def test_a_line_drawn_twice_is_one_line(overprinted):
    """Overprinting to fake a bold weight must not double the page."""
    _gt, doc, _grid = overprinted
    page = doc.page(0, tables=False)
    assert page.stats["n_overprinted_lines"] > 20
    texts = [ln.text for ln in page.lines]
    seen = {(ln.text, tuple(round(v, 1) for v in ln.bbox))
            for ln in page.lines}
    assert len(seen) == len(page.lines)
    assert texts.count("5-9-12") == 1
    assert texts.count("5") == 1


def test_a_doubled_ruler_still_fits(overprinted):
    """"5, 5, 10, 10, 15, 15" has no strictly rising run of three in it."""
    gt, _doc, grid = overprinted
    assert grid.has_ruler
    ruler = grid.rulers[0]
    assert ruler.step == pytest.approx(gt.ticks[1] - gt.ticks[0])
    assert len(ruler.ticks) == len(gt.ticks)
    assert grid.unit == "ft"


def test_ticks_seen_twice_collapse_even_if_they_reach_the_fitter():
    from planlens.document.loggrid import _collapse_ticks, _fit_ruler
    doubled = [(100.0, 5.0), (100.0, 5.0), (200.0, 10.0), (200.2, 10.0),
               (300.0, 15.0), (300.0, 15.0), (400.0, 20.0)]
    assert _collapse_ticks(doubled) == [(100.0, 5.0), (200.0, 10.0),
                                        (300.0, 15.0), (400.0, 20.0)]
    fitted = _fit_ruler(doubled, rising=True)
    assert fitted is not None and len(fitted[0]) == 4


def test_a_title_block_line_never_names_a_column(overprinted):
    """It crosses the ruler's column; the words inside the column win."""
    _gt, _doc, grid = overprinted
    depth = [c for c in grid.columns if c.name == "depth"]
    assert depth, [c.header for c in grid.columns]
    assert "elevation" not in depth[0].names
    assert depth[0].header.startswith("DEPTH")
    # and it is still a line the fields can read, not a line thrown away
    assert grid.fields["ground_surface_elevation"].startswith("104.5")


def test_the_overprinted_form_reads_like_the_plain_one(overprinted):
    """Every answer of the imperial log, off a page drawn twice."""
    gt, _doc, grid = overprinted
    assert [ly.top for ly in grid.layers] == [t for t, _b, _d in gt.layers]
    for depth, blows, n_value in gt.samples:
        for text in (blows, n_value):
            assert [c for c in grid.cells("blows")
                    if c.text == text
                    and abs((c.depth or -999) - depth) <= 0.5], text


# ---------------------------------------------------------------------------
# A rule drawn in pieces
# ---------------------------------------------------------------------------

def test_collinear_pieces_join_into_the_run_they_make():
    from planlens.document.loggrid import _Rule, join_segments

    # The four strokes one corpus form emits for the line under its header.
    pieces = [_Rule(170.0, 50.4, 338.4), _Rule(170.0, 331.2, 337.0),
              _Rule(170.4, 352.8, 590.4), _Rule(170.0, 354.2, 360.0)]
    joined = join_segments(pieces)
    assert len(joined) == 1
    assert (round(joined[0].lo, 1), round(joined[0].hi, 1)) == (50.4, 590.4)
    assert joined[0].length > 500


def test_two_rules_a_whole_column_apart_stay_two():
    from planlens.document.loggrid import _Rule, join_segments

    apart = [_Rule(170.0, 50.0, 150.0), _Rule(170.0, 260.0, 400.0)]
    assert len(join_segments(apart)) == 2
    # and a different y is a different rule however it overlaps
    stacked = [_Rule(170.0, 50.0, 400.0), _Rule(220.0, 50.0, 400.0)]
    assert len(join_segments(stacked)) == 2


def test_a_header_rule_in_four_pieces_still_marks_the_body(request):
    """Stroke by stroke there is no line under the header row at all."""
    from planlens.testing import build_segmented_rule_log

    gt = build_segmented_rule_log()
    with open_document(gt.pdf) as doc:
        grid = log_grid(doc, 0)
    assert grid.has_ruler and grid.unit == "ft"
    named = {c.name for c in grid.columns if c.names}
    assert {"depth", "description", "water_content",
            "dry_unit_weight"} <= named
    assert [ly.top for ly in grid.layers] == [t for t, _b, _d in gt.layers]
    for depth, blows, n_value in gt.samples:
        for text in (blows, n_value):
            assert [c for c in grid.cells("blows")
                    if c.text == text
                    and abs((c.depth or -999) - depth) <= 0.5], text


@pytest.mark.parametrize("text,expected", [
    ("S-7", (7.0,)), ("SB-01", (1.0,)), ("TP-2A", (2.0,)),
    ("5-9-12", (5.0, 9.0, 12.0)), ("-2.5", (-2.5,)), ("N=21", (21.0,)),
    ("REC=25cm, 56%", (25.0, 56.0)), ("Longitude: -117.887", (-117.887,)),
])
def test_a_dash_after_a_letter_names_a_sample_and_is_not_a_minus(text,
                                                                expected):
    assert numbers_in(text) == expected
