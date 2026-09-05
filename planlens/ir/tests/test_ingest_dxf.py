"""DXF ingest tests (ezdxf)."""

import pytest

pytest.importorskip("ezdxf")

from planlens.ir import DrawingIR, from_dxf
from planlens.ir.results import Arc, Circle, Line, Polyline, TextItem


def _by_type(ir, cls):
    return [e for e in ir.entities if isinstance(e, cls)]


class TestDxfIngest:
    def test_entity_types_and_space(self, simple_dxf):
        ir = from_dxf(simple_dxf)
        assert ir.source == "dxf"
        assert ir.coordinate_space == "model"
        assert ir.units == "m"
        assert ir.counts_by_type() == {
            "polyline": 1, "line": 1, "circle": 1, "arc": 1, "text": 1}
        assert all(e.confidence == 1.0 for e in ir.entities)
        assert all(e.source == "dxf" for e in ir.entities)

    def test_exact_coordinates_preserved(self, simple_dxf):
        ir = from_dxf(simple_dxf)
        poly = _by_type(ir, Polyline)[0]
        assert poly.vertices == [(0, 10), (10, 10), (20, 5), (30, 5)]
        ln = _by_type(ir, Line)[0]
        assert ln.start == (5, 8) and ln.end == (25, 3)
        circ = _by_type(ir, Circle)[0]
        assert circ.center == (15, 7) and circ.radius == 2.0
        arc = _by_type(ir, Arc)[0]
        assert arc.center == (10, 4) and arc.radius == 3.0
        assert arc.start_angle == pytest.approx(0)
        assert arc.end_angle == pytest.approx(90)

    def test_layers_and_text(self, simple_dxf):
        ir = from_dxf(simple_dxf)
        layers = set(ir.counts_by_layer())
        assert {"SURFACE", "GWT", "DETAIL", "NOTES"} <= layers
        txt = _by_type(ir, TextItem)[0]
        assert txt.content == "Clay"
        assert txt.position == (12, 6)
        assert txt.layer == "NOTES"

    def test_native_meters_no_scale(self, simple_dxf):
        ir = from_dxf(simple_dxf)
        assert ir.scale is None
        assert ir.scale_provenance == "dxf_native_meters"
        assert ir.metadata["dxf_units"] == "m"

    def test_imperial_units_converted_to_meters(self, imperial_dxf):
        ir = from_dxf(imperial_dxf)
        assert ir.units == "m"
        assert ir.scale == pytest.approx(0.3048)
        assert ir.scale_provenance == "dxf_units:ft->m"
        poly = _by_type(ir, Polyline)[0]
        # (0,30) ft -> (0, 9.144) m ; (100,15) ft -> (30.48, 4.572) m
        assert poly.vertices[0] == pytest.approx((0.0, 9.144))
        assert poly.vertices[-1] == pytest.approx((30.48, 4.572))

    def test_units_override(self, simple_dxf):
        # Force mm interpretation: every coord scales by 0.001.
        ir = from_dxf(simple_dxf, units="mm")
        poly = _by_type(ir, Polyline)[0]
        assert poly.vertices[1] == pytest.approx((0.01, 0.01))

    def test_content_bytes_path(self, simple_dxf):
        with open(simple_dxf, "rb") as f:
            data = f.read()
        ir = from_dxf(content=data)
        assert len(ir.entities) == 5

    def test_round_trip_after_ingest(self, simple_dxf):
        ir = from_dxf(simple_dxf)
        d = ir.to_dict()
        assert DrawingIR.from_dict(d).to_dict() == d

    def test_requires_a_source(self):
        with pytest.raises(ValueError):
            from_dxf()


class TestInsertExplosion:
    """Phase-3.2: INSERT block-geometry explosion (exact transforms)."""

    def test_block_geometry_exploded_with_transform(self, blocks_dxf):
        from planlens.ir import from_dxf
        from planlens.ir.results import Circle, Line
        ir = from_dxf(blocks_dxf)
        # PART at (10,5), rot 90, scale 2: line (0,0)->(1,0) maps to
        # (10,5)->(10,7); circle center (0.5,0.5) -> (10-1, 5+1), r 0.5.
        lines = [e for e in ir.entities if isinstance(e, Line)
                 and (e.style or "").startswith("block:PART")]
        assert any(abs(ln.start[0] - 10) < 1e-6 and abs(ln.end[1] - 7) < 1e-6
                   for ln in lines)
        circles = [e for e in ir.entities if isinstance(e, Circle)
                   and (e.style or "").startswith("block:PART")]
        assert any(abs(c.radius - 0.5) < 1e-6 and abs(c.center[0] - 9) < 1e-6
                   and abs(c.center[1] - 6) < 1e-6 for c in circles)

    def test_nested_reference_exploded_under_top_block_name(self, blocks_dxf):
        from planlens.ir import from_dxf
        from planlens.ir.results import Line
        ir = from_dxf(blocks_dxf)
        # ASM at (100,100) contains PART at (2,0): line lands at
        # (102,100)->(103,100), tagged with the TOP block name.
        lines = [e for e in ir.entities if isinstance(e, Line)
                 and (e.style or "").startswith("block:ASM")]
        assert any(abs(ln.start[0] - 102) < 1e-6
                   and abs(ln.start[1] - 100) < 1e-6 for ln in lines)

    def test_attribs_still_extracted(self, blocks_dxf):
        from planlens.ir import from_dxf
        from planlens.ir.results import TextItem
        ir = from_dxf(blocks_dxf)
        att = [e for e in ir.entities if isinstance(e, TextItem)
               and (e.style or "").startswith("attrib:TBLOCK:SHEET_NO")]
        assert len(att) == 1 and att[0].content == "S-1"

    def test_explosion_metadata_and_direct_geometry_untagged(self,
                                                             blocks_dxf):
        from planlens.ir import from_dxf
        from planlens.ir.results import Line
        ir = from_dxf(blocks_dxf)
        assert ir.metadata.get("n_block_entities", 0) >= 4
        direct = [e for e in ir.entities if isinstance(e, Line)
                  and e.layer == "DIRECT"]
        assert direct and not (direct[0].style or "").startswith("block:")

    def test_explode_blocks_off_keeps_old_behavior(self, blocks_dxf):
        from planlens.ir import from_dxf
        from planlens.ir.results import TextItem
        ir = from_dxf(blocks_dxf, explode_blocks=False)
        assert not any((e.style or "").startswith("block:")
                       for e in ir.entities)
        assert any(isinstance(e, TextItem)
                   and (e.style or "").startswith("attrib:")
                   for e in ir.entities)  # attribs unaffected

    def test_entity_budget_cap_warns(self, blocks_dxf):
        from planlens.ir import from_dxf
        ir = from_dxf(blocks_dxf, max_block_entities=1)
        assert any("entity budget" in w for w in ir.warnings)
        assert ir.metadata.get("n_block_entities", 0) <= 2

    def test_depth_cap_warns_not_crashes(self, deep_blocks_dxf):
        from planlens.ir import from_dxf
        ir = from_dxf(deep_blocks_dxf)
        assert any("nesting deeper" in w for w in ir.warnings)
