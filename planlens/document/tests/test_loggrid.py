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
