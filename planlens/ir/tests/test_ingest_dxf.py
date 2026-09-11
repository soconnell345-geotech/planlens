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


# ---------------------------------------------------------------------------
# Fixtures for the block-explosion correctness tests below. Kept local (as
# test_native_dxf.py does) so each probe's authored layers/blocks sit next
# to the assertion that reads them.
# ---------------------------------------------------------------------------

@pytest.fixture
def layer_zero_dxf(tmp_path):
    """Block content on layer "0" — the inherit-from-INSERT sentinel.

    Block STD_DETAIL: LINE on "0", CIRCLE on the real layer "INNER".
    Block A: LINE on "0". Block B: INSERT of A on "0". Block C: INSERT of
    A on "Q". Model space: STD_DETAIL on "PLACED", B on "X", C on "X".
    CAD-correct layers: PLACED / INNER / X (chains through B) / Q.
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    for lyr in ("X", "Q", "PLACED", "INNER"):
        doc.layers.add(lyr)
    std = doc.blocks.new("STD_DETAIL")
    std.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    std.add_circle((0, 0), 0.5, dxfattribs={"layer": "INNER"})
    a = doc.blocks.new("A")
    a.add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
    b = doc.blocks.new("B")
    b.add_blockref("A", (0, 0), dxfattribs={"layer": "0"})
    c = doc.blocks.new("C")
    c.add_blockref("A", (10, 0), dxfattribs={"layer": "Q"})
    msp = doc.modelspace()
    msp.add_blockref("STD_DETAIL", (0, 0), dxfattribs={"layer": "PLACED"})
    msp.add_blockref("B", (0, 0), dxfattribs={"layer": "X"})
    msp.add_blockref("C", (0, 0), dxfattribs={"layer": "X"})
    path = tmp_path / "layer_zero.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def curves_dxf(tmp_path):
    """Curve geometry inside a NON-UNIFORMLY scaled block, plus controls.

    Block CURVES: LINE, SPLINE, ELLIPSE, CIRCLE, ARC — inserted at
    xscale 3 / yscale 1, which makes ezdxf hand the CIRCLE and the ARC
    back as ELLIPSEs. Model space also draws a plain SPLINE and a DASHED
    SPLINE directly (the linetype-preservation control).
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    doc.linetypes.add("DASHED", pattern=[0.6, 0.4, -0.2])
    blk = doc.blocks.new("CURVES")
    blk.add_line((0, 0), (1, 0))
    blk.add_spline([(0, 0), (1, 1), (2, 0)])
    blk.add_ellipse((0, 0), major_axis=(1, 0), ratio=0.5)
    blk.add_circle((3, 3), 1.0)
    blk.add_arc((5, 5), 1.0, 0, 90)
    msp = doc.modelspace()
    msp.add_blockref("CURVES", (0, 0),
                     dxfattribs={"xscale": 3.0, "yscale": 1.0})
    msp.add_spline([(10, 0), (11, 1), (12, 0)],
                   dxfattribs={"layer": "DIRECT"})
    msp.add_spline([(20, 0), (21, 1), (22, 0)],
                   dxfattribs={"layer": "DIRECT", "linetype": "DASHED"})
    path = tmp_path / "curves.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def ten_lines_block_dxf(tmp_path):
    """Block BIG holding 10 LINEs, inserted once (sibling-loss probe)."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("BIG")
    for i in range(10):
        blk.add_line((i, 0), (i, 1))
    doc.modelspace().add_blockref("BIG", (0, 0))
    path = tmp_path / "big_block.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def six_lines_dxf(tmp_path):
    """Six directly drawn LINEs on layer DIRECT (top-level guard probe)."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    doc.layers.add("DIRECT")
    msp = doc.modelspace()
    for i in range(6):
        msp.add_line((i, 0), (i, 1), dxfattribs={"layer": "DIRECT"})
    path = tmp_path / "six_lines.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def unsupported_block_dxf(tmp_path):
    """Block UNSUP: 40 SOLIDs (unsupported) + 1 LINE — walks 41, ingests 1."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("UNSUP")
    for i in range(40):
        blk.add_solid([(i, 0), (i + 1, 0), (i + 1, 1), (i, 1)])
    blk.add_line((0, 0), (1, 0))
    doc.modelspace().add_blockref("UNSUP", (0, 0))
    path = tmp_path / "unsupported.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def wrapper_block_dxf(tmp_path):
    """OUTER_B holds 5 INSERTs of INNER_B{1 LINE} — 10 walked, 5 ingested."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    inner = doc.blocks.new("INNER_B")
    inner.add_line((0, 0), (1, 0))
    outer = doc.blocks.new("OUTER_B")
    for i in range(5):
        outer.add_blockref("INNER_B", (i * 5, 0))
    doc.modelspace().add_blockref("OUTER_B", (0, 0))
    path = tmp_path / "wrappers.dxf"
    doc.saveas(str(path))
    return str(path)


def _poison_nth_line(monkeypatch, nth):
    """Make the ``nth`` LINE blow up in ``_handle``'s pre-try prologue.

    ``_dxf_style`` is called before the per-entity try, so raising here is
    the verified path by which one entity's failure used to escape into
    its parent's handler and cost every sibling after it.
    """
    from planlens.ir import ingest as ing
    real = ing._dxf_style
    seen = {"n": 0}

    def fake(entity):
        if entity.dxftype() == "LINE":
            seen["n"] += 1
            if seen["n"] == nth:
                raise RuntimeError(f"malformed entity #{nth}")
        return real(entity)

    monkeypatch.setattr(ing, "_dxf_style", fake)


class TestBlockLayerInheritance:
    """Layer "0" inside a block means "the placing INSERT's layer"."""

    def test_layer_zero_resolves_to_placing_insert(self, layer_zero_dxf):
        ir = from_dxf(layer_zero_dxf)
        ln = next(e for e in ir.entities if isinstance(e, Line)
                  and (e.style or "") == "block:STD_DETAIL")
        assert ln.layer == "PLACED"

    def test_named_block_internal_layer_is_kept(self, layer_zero_dxf):
        ir = from_dxf(layer_zero_dxf)
        circ = next(e for e in ir.entities if isinstance(e, Circle))
        assert circ.layer == "INNER"

    def test_resolution_chains_through_nested_inserts(self, layer_zero_dxf):
        ir = from_dxf(layer_zero_dxf)
        b = next(e for e in ir.entities if (e.style or "") == "block:B")
        c = next(e for e in ir.entities if (e.style or "") == "block:C")
        assert b.layer == "X"   # "0" INSERT inside an "X" INSERT -> X
        assert c.layer == "Q"   # first NAMED layer in the chain wins

    def test_phantom_zero_bucket_gone(self, layer_zero_dxf):
        ir = from_dxf(layer_zero_dxf)
        assert "0" not in ir.counts_by_layer()
        assert sorted(ir.counts_by_layer()) == ["INNER", "PLACED", "Q", "X"]

    def test_placed_layer_query_finds_the_geometry(self, layer_zero_dxf):
        from planlens.ir import queries as q
        ir = from_dxf(layer_zero_dxf)
        assert len(q.entities_on_layer(ir, "PLACED")) == 1

    def test_top_level_layer_zero_untouched(self, tmp_path):
        # No INSERT in sight: a directly drawn entity on "0" keeps "0".
        import ezdxf
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        doc.modelspace().add_line((0, 0), (1, 0), dxfattribs={"layer": "0"})
        path = str(tmp_path / "plain_zero.dxf")
        doc.saveas(path)
        assert from_dxf(path).entities[0].layer == "0"


class TestCurveStyleProvenance:
    """``style`` is a token list: block origin + linetype + approx note."""

    def test_all_block_curves_keep_block_provenance(self, curves_dxf):
        ir = from_dxf(curves_dxf)
        blockish = [e for e in ir.entities
                    if (e.style or "").startswith("block:CURVES")]
        # LINE + SPLINE + ELLIPSE + (CIRCLE, ARC -> ELLIPSE under the
        # non-uniform scale) = 5 of 5, where only the LINE used to carry it.
        assert len(blockish) == 5
        assert sum(1 for e in ir.entities
                   if (e.style or "").startswith("block:")) == 5

    def test_approx_note_is_appended_not_substituted(self, curves_dxf):
        ir = from_dxf(curves_dxf)
        blockish = [(e.style or "") for e in ir.entities
                    if (e.style or "").startswith("block:CURVES")]
        assert "block:CURVES|approx_from_spline" in blockish
        assert blockish.count("block:CURVES|approx_from_ellipse") == 3

    def test_direct_curve_without_linetype_is_unchanged(self, curves_dxf):
        ir = from_dxf(curves_dxf)
        direct = [e for e in ir.entities if e.layer == "DIRECT"]
        assert any(e.style == "approx_from_spline" for e in direct)

    def test_direct_curve_linetype_no_longer_destroyed(self, curves_dxf):
        ir = from_dxf(curves_dxf)
        direct = [e for e in ir.entities if e.layer == "DIRECT"]
        assert any(e.style == "DASHED|approx_from_spline" for e in direct)


class TestMalformedEntityIsolation:
    """One bad entity costs exactly itself — cap, don't delete."""

    def test_bad_child_does_not_cost_its_siblings(self, monkeypatch,
                                                  ten_lines_block_dxf):
        _poison_nth_line(monkeypatch, 5)
        ir = from_dxf(ten_lines_block_dxf)
        assert len(_by_type(ir, Line)) == 9
        assert any("inside block 'BIG'" in w for w in ir.warnings)
        # The old warning claimed the whole INSERT was skipped while four
        # of its entities were already in the IR.
        assert not any(w.startswith("Skipped INSERT") for w in ir.warnings)

    def test_bad_top_level_entity_does_not_kill_the_sheet(self, monkeypatch,
                                                          six_lines_dxf):
        _poison_nth_line(monkeypatch, 3)
        ir = from_dxf(six_lines_dxf)  # previously raised out of from_dxf
        assert len([e for e in ir.entities if e.layer == "DIRECT"]) == 5
        assert any("model-space" in w for w in ir.warnings)

    def test_generator_raise_reports_a_counted_truncation(self, monkeypatch,
                                                          blocks_dxf):
        import ezdxf.entities

        def stubborn(self, *a, **kw):
            def gen():
                for i in range(3):
                    yield ezdxf.entities.Line.new(dxfattribs={
                        "start": (i, 0), "end": (i + 1, 0),
                        "layer": "EXPLODED"})
                raise RuntimeError("generator gave up")
            return gen()

        monkeypatch.setattr(ezdxf.entities.Insert, "virtual_entities",
                            stubborn)
        ir = from_dxf(blocks_dxf)
        assert any("truncated after 3 entities" in w for w in ir.warnings)
        # Nothing is rolled back: the three that made it through stay.
        assert len([e for e in _by_type(ir, Line)
                    if e.layer == "EXPLODED"]) >= 3


class TestBlockEntityAccounting:
    """n_block_entities counts what reached the IR, not what was walked."""

    def test_unsupported_block_walks_wide_and_ingests_one(
            self, unsupported_block_dxf):
        ir = from_dxf(unsupported_block_dxf)
        assert ir.metadata["n_block_entities"] == 1
        assert ir.metadata["n_block_entities_walked"] == 41

    def test_budget_caps_the_walk_and_says_so(self, unsupported_block_dxf):
        ir = from_dxf(unsupported_block_dxf, max_block_entities=20)
        assert ir.metadata["n_block_entities"] == 0
        assert ir.metadata["n_block_entities_walked"] == 20
        assert any("entities walked, not ingested" in w for w in ir.warnings)

    def test_nested_wrappers_counted_once(self, wrapper_block_dxf):
        ir = from_dxf(wrapper_block_dxf)
        assert ir.metadata["n_block_entities"] == 5   # not 10
        assert ir.metadata["n_block_entities_walked"] == 10

    def test_count_matches_block_tagged_entities(self, blocks_dxf):
        # The meaning of the key, asserted rather than assumed. Valid only
        # because every exploded entity now carries block: provenance.
        ir = from_dxf(blocks_dxf)
        assert ir.metadata["n_block_entities"] == sum(
            1 for e in ir.entities if (e.style or "").startswith("block:"))

    def test_walked_key_absent_when_it_adds_nothing(self, curves_dxf):
        ir = from_dxf(curves_dxf)
        assert ir.metadata["n_block_entities"] == 5
        assert "n_block_entities_walked" not in ir.metadata

    def test_nested_attrib_carries_block_provenance(self,
                                                    nested_attrib_dxf):
        # A block-nested ATTRIB is BOTH block content and an attribute
        # value; carrying only the attrib token made n_block_entities
        # (2) disagree with the block-tagged count (1) — i.e. broke the
        # invariant test_count_matches_block_tagged_entities asserts.
        ir = from_dxf(nested_attrib_dxf)
        att = next(e for e in _by_type(ir, TextItem) if e.content == "V1")
        assert att.style == "block:OUTER_T|attrib:INNER_T:TAG1"

    def test_count_matches_block_tagged_with_nested_attribs(
            self, nested_attrib_dxf):
        ir = from_dxf(nested_attrib_dxf)
        assert ir.metadata["n_block_entities"] == 2
        assert ir.metadata["n_block_entities"] == sum(
            1 for e in ir.entities if (e.style or "").startswith("block:"))

    def test_top_level_attrib_style_unchanged(self, blocks_dxf):
        # A directly placed INSERT is not block content, so its ATTRIBs
        # keep the bare attrib token they always had.
        ir = from_dxf(blocks_dxf)
        att = next(e for e in _by_type(ir, TextItem) if e.content == "S-1")
        assert att.style == "attrib:TBLOCK:SHEET_NO"

    def test_a_cap_that_stops_the_walk_dead_still_reports(self, blocks_dxf):
        # A zero budget used to warn and then publish NO block metadata
        # at all — the one case a caller most needs the count for.
        ir = from_dxf(blocks_dxf, max_block_entities=0)
        assert ir.metadata["n_block_entities"] == 0
        assert any("entity budget" in w for w in ir.warnings)

    def test_no_block_metadata_when_no_block_was_touched(self,
                                                         six_lines_dxf):
        ir = from_dxf(six_lines_dxf)
        assert "n_block_entities" not in ir.metadata


# ---------------------------------------------------------------------------
# Losses that are ezdxf's, not ours. Cap-don't-delete applies to geometry we
# never receive, too: if a construct is plotted on paper and absent from the
# IR, that has to be visible.
# ---------------------------------------------------------------------------

@pytest.fixture
def nested_attrib_dxf(tmp_path):
    """OUTER_T holds an INSERT of INNER_T{ATTDEF + LINE} with a value."""
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    inner = doc.blocks.new("INNER_T")
    inner.add_attdef("TAG1", (0, 0), dxfattribs={"height": 0.25})
    inner.add_line((0, 0), (1, 0))
    outer = doc.blocks.new("OUTER_T")
    outer.add_blockref("INNER_T", (0, 0)).add_auto_attribs({"TAG1": "V1"})
    doc.modelspace().add_blockref("OUTER_T", (0, 0))
    path = tmp_path / "nested_attrib.dxf"
    doc.saveas(str(path))
    return str(path)


@pytest.fixture
def untransformable_multileader_dxf(tmp_path):
    """Block MLB{LINE + MULTILEADER} placed at a NON-UNIFORM scale.

    ezdxf omits the MULTILEADER from ``virtual_entities()`` and does not
    raise, so every per-entity guard in the explosion loop sees a clean
    run — the plotted callout simply is not there.
    """
    import ezdxf
    from ezdxf.render import mleader
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("MLB")
    blk.add_line((0, 0), (5, 0))
    ml = blk.add_multileader_mtext("Standard")
    ml.set_content("CALLOUT")
    ml.add_leader_line(mleader.ConnectionSide.left,
                       [ezdxf.math.Vec2(2, 2)])
    ml.build(insert=ezdxf.math.Vec2(4, 4))
    doc.modelspace().add_blockref("MLB", (0, 0),
                                  dxfattribs={"xscale": 2.0, "yscale": 1.0})
    path = tmp_path / "untransformable_ml.dxf"
    doc.saveas(str(path))
    return str(path)


class TestEzdxfSideLossesAreReported:
    def test_untransformable_multileader_warns(
            self, untransformable_multileader_dxf):
        from planlens.ir.results import Leader
        ir = from_dxf(untransformable_multileader_dxf)
        assert _by_type(ir, Leader) == []          # the loss is real
        assert any("MULTILEADER" in w and "NOT in the IR" in w
                   for w in ir.warnings)           # and now visible

    def test_the_rest_of_the_block_still_lands(
            self, untransformable_multileader_dxf):
        ir = from_dxf(untransformable_multileader_dxf)
        assert len(_by_type(ir, Line)) == 1

    def test_repeated_placements_warn_once(self, tmp_path):
        import ezdxf
        from ezdxf.render import mleader
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6
        blk = doc.blocks.new("MLB2")
        ml = blk.add_multileader_mtext("Standard")
        ml.set_content("CALLOUT")
        ml.add_leader_line(mleader.ConnectionSide.left,
                           [ezdxf.math.Vec2(2, 2)])
        ml.build(insert=ezdxf.math.Vec2(4, 4))
        msp = doc.modelspace()
        for i in range(6):
            msp.add_blockref("MLB2", (i * 10, 0),
                             dxfattribs={"xscale": 2.0, "yscale": 1.0})
        path = str(tmp_path / "many_ml.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        assert sum(1 for w in ir.warnings if "MULTILEADER" in w) == 1

    def test_a_type_we_never_ingest_is_not_reported_as_a_loss(
            self, monkeypatch, blocks_dxf):
        # An OLE2FRAME sits inside the county-seal block on all ten
        # ground-truth sheets. It is not a type from_dxf ingests, so
        # ezdxf declining to transform it costs the IR nothing —
        # reporting it would be noise on every real sheet, not honesty.
        import ezdxf.entities

        class _Ole:
            def dxftype(self):
                return "OLE2FRAME"

        def announcing(self, *a, skipped_entity_callback=None, **kw):
            if skipped_entity_callback is not None:
                skipped_entity_callback(_Ole(), "non copyable")
            return iter(())

        monkeypatch.setattr(ezdxf.entities.Insert, "virtual_entities",
                            announcing)
        assert from_dxf(blocks_dxf).warnings == []

    def test_minsert_array_reports_its_missing_copies(self, tmp_path):
        import ezdxf
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        blk = doc.blocks.new("CELL")
        blk.add_line((0, 0), (1, 0))
        ins = doc.modelspace().add_blockref("CELL", (0, 0))
        ins.dxf.row_count, ins.dxf.column_count = 3, 4
        ins.dxf.row_spacing = ins.dxf.column_spacing = 5.0
        path = str(tmp_path / "minsert.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        # Array expansion is NOT implemented (out of scope); the point is
        # that 1 of 12 placements is stated, not assumed.
        assert len(_by_type(ir, Line)) == 1
        assert any("MINSERT" in w and "12 copies" in w for w in ir.warnings)

    def test_a_bad_attrib_costs_only_itself(self, monkeypatch, blocks_dxf):
        # The F9 try-scope defect, one loop over: the ATTRIB loop sat
        # inside the whole-INSERT try, so one unreadable attribute aborted
        # that INSERT's block explosion as well. Poisoned the same way
        # ``_poison_nth_line`` does — from inside the per-item body.
        from planlens.ir import ingest as ing
        real = ing._ocs_map

        def fake(entity):
            if entity.dxftype() == "ATTRIB":
                raise RuntimeError("unreadable ATTRIB")
            return real(entity)

        monkeypatch.setattr(ing, "_ocs_map", fake)
        ir = from_dxf(blocks_dxf)
        assert any("ATTRIB" in w for w in ir.warnings)
        # The block geometry under that same INSERT is unaffected: the
        # TBLOCK reference still explodes.
        assert any((e.style or "").startswith("block:TBLOCK")
                   for e in ir.entities)


# ---------------------------------------------------------------------------
# Mirrored placements — the OCS/extrusion transform.
#
# A standard detail placed with a negative scale is everyday drafting, and
# ezdxf hands the mirrored copy back with extrusion (0,0,-1): the points DXF
# stores in the entity's OWN coordinate system come back with x negated.
# Reading them raw put a detail spanning x 40..50 at x = -45. There are zero
# negative-scale INSERTs across the ten committed corpus sheets, so nothing
# here is observable on that corpus — these fixtures are the measurement.
# ---------------------------------------------------------------------------

@pytest.fixture
def mirrored_dxf(tmp_path):
    """Block DET (one of every OCS-stored shape) placed MIRRORED.

    DET is authored in x 0..10, y -2..4 and inserted at (50, 48) with
    xscale -1, so every point it plots must land in x 40..50. Units are
    meters, so the ingest conversion factor is 1.0 and the assertions
    below are the hand-computed WCS positions, not scaled ones.
    """
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("DET")
    blk.add_line((0, 0), (10, 0))                 # WCS control
    blk.add_circle((2, 1), 0.5)                   # OCS center
    blk.add_arc((3, 1), 1.0, 0, 90)               # OCS center + OCS angles
    blk.add_lwpolyline([(1, 3), (4, 3), (4, 4)])  # OCS vertices
    hatch = blk.add_hatch(color=2)
    hatch.paths.add_polyline_path([(6, 1), (7, 1), (7, 2)], is_closed=True)
    blk.add_text("NOTE", dxfattribs={"insert": (5, 2), "height": 0.25})
    blk.add_mtext("MNOTE", dxfattribs={"insert": (6, 3),
                                       "char_height": 0.25})
    blk.add_leader(vertices=[(1, 1), (3, 3)])     # WCS vertices
    dim = blk.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(10, 0),
                             dimstyle="EZDXF")
    dim.render()
    doc.modelspace().add_blockref("DET", (50, 48),
                                  dxfattribs={"xscale": -1.0, "yscale": 1.0})
    path = tmp_path / "mirrored.dxf"
    doc.saveas(str(path))
    return str(path)


class TestMirroredInsertOcs:
    """Every plotted point of a mirrored detail lands in the detail."""

    def test_nothing_lands_outside_the_detail_footprint(self, mirrored_dxf):
        # The headline regression: the TEXT used to be reported at
        # x = -45 for a detail whose geometry spans x 40..50.
        ir = from_dxf(mirrored_dxf)
        boxes = [e.compute_bbox() for e in ir.entities]
        assert all(b is not None for b in boxes)
        assert min(b[0] for b in boxes) == pytest.approx(40.0, abs=1e-6)
        assert max(b[2] for b in boxes) == pytest.approx(50.0, abs=1e-6)

    def test_text_insert_is_ocs(self, mirrored_dxf):
        ir = from_dxf(mirrored_dxf)
        txt = next(e for e in _by_type(ir, TextItem) if e.content == "NOTE")
        assert txt.position == pytest.approx((45.0, 50.0))

    def test_mtext_insert_is_already_wcs(self, mirrored_dxf):
        # MTEXT's group-10 insert is WCS: applying the OCS map to it too
        # would mirror it a second time, back out to x = 56.
        ir = from_dxf(mirrored_dxf)
        txt = next(e for e in _by_type(ir, TextItem) if e.content == "MNOTE")
        assert txt.position == pytest.approx((44.0, 51.0))

    def test_circle_polyline_and_hatch_vertices(self, mirrored_dxf):
        from planlens.ir.results import Region
        ir = from_dxf(mirrored_dxf)
        assert _by_type(ir, Circle)[0].center == pytest.approx((48.0, 49.0))
        poly = next(e for e in _by_type(ir, Polyline)
                    if len(e.vertices) == 3)
        assert poly.vertices[0] == pytest.approx((49.0, 51.0))
        assert poly.vertices[2] == pytest.approx((46.0, 52.0))
        reg = _by_type(ir, Region)[0]
        assert reg.boundary[0] == pytest.approx((44.0, 49.0))

    def test_arc_angles_follow_the_mirror(self, mirrored_dxf):
        # A mirrored plane reverses the sweep, so the CCW 0->90 arc plots
        # as 90->180 about the mirrored center. Its bbox is the quadrant
        # up and to the LEFT of the center, which is the honest check.
        ir = from_dxf(mirrored_dxf)
        arc = _by_type(ir, Arc)[0]
        assert arc.center == pytest.approx((47.0, 49.0))
        assert arc.start_angle == pytest.approx(90.0)
        assert arc.end_angle == pytest.approx(180.0)
        assert arc.compute_bbox() == pytest.approx((46.0, 49.0, 47.0, 50.0))

    def test_dimension_defpoints_wcs_text_midpoint_ocs(self, mirrored_dxf):
        from planlens.ir.results import Dimension
        ir = from_dxf(mirrored_dxf)
        dim = _by_type(ir, Dimension)[0]
        assert dim.defpoints[1] == pytest.approx((50.0, 48.0))
        assert dim.defpoints[2] == pytest.approx((40.0, 48.0))
        # The text midpoint belongs BETWEEN its own defpoints.
        assert dim.text_midpoint[0] == pytest.approx(45.0)

    def test_leader_vertices_are_wcs(self, mirrored_dxf):
        from planlens.ir.results import Leader
        ir = from_dxf(mirrored_dxf)
        ldr = _by_type(ir, Leader)[0]
        assert tuple(ldr.vertices[0]) == pytest.approx((49.0, 49.0))
        assert tuple(ldr.vertices[1]) == pytest.approx((47.0, 51.0))

    def test_multileader_context_vertices_stay_wcs(self, tmp_path):
        # A mirror is UNIFORM scaling (|-1| == |1|), so ezdxf does
        # transform the MULTILEADER — and hands its context vertices back
        # already in WCS. Applying the OCS map to them too would mirror
        # them a second time, out to x = 52.
        import ezdxf
        from ezdxf.render import mleader
        from planlens.ir.results import Leader
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6
        blk = doc.blocks.new("MLM")
        ml = blk.add_multileader_mtext("Standard")
        ml.set_content("CALLOUT")
        ml.add_leader_line(mleader.ConnectionSide.left,
                           [ezdxf.math.Vec2(2, 2)])
        ml.build(insert=ezdxf.math.Vec2(4, 4))
        doc.modelspace().add_blockref("MLM", (50, 0),
                                      dxfattribs={"xscale": -1.0,
                                                  "yscale": 1.0})
        path = str(tmp_path / "ml_mirror.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        assert ir.warnings == []
        ldr = _by_type(ir, Leader)[0]
        assert tuple(ldr.vertices[0]) == pytest.approx((48.0, 2.0))

    def test_unmirrored_control_is_untouched(self, tmp_path):
        # The identity extrusion must cost nothing and change nothing.
        import ezdxf
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        blk = doc.blocks.new("PLAIN")
        blk.add_circle((2, 1), 0.5)
        blk.add_text("NOTE", dxfattribs={"insert": (5, 2), "height": 0.25})
        doc.modelspace().add_blockref("PLAIN", (50, 48))
        path = str(tmp_path / "plain.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        assert _by_type(ir, Circle)[0].center == pytest.approx((52.0, 49.0))
        assert _by_type(ir, TextItem)[0].position == pytest.approx(
            (55.0, 50.0))


# ---------------------------------------------------------------------------
# Text BEARING under a mirrored placement — the angular half of the OCS fix.
#
# A TEXT's group-50 ``rotation`` is measured in the entity's OWN xy plane,
# exactly like its insert point, so a mirror runs it backwards too. Resolving
# the point but not the angle left the insert point right and the direction
# wrong: measured on the fixture below, a 33-character note on a detail
# spanning x 40..50 reported its box out to x = 53.95, on the far side of its
# own correctly-placed insert point at x = 49.
#
# WHY THESE ASSERT AGAINST THE INSERT, NOT AGAINST truth.py: ``truth.py``
# repeated the identical raw read, so an ingest-versus-truth agreement test
# compared two identically wrong numbers and passed. An agreement test cannot
# see an error both surfaces share. ``_plotted_bearing`` below therefore
# derives the expected answer from the INSERT's own transform — the block-local
# baseline direction pushed through ``matrix44()`` — which is what the plotter
# does and is independent of both surfaces.
# ---------------------------------------------------------------------------

def _plotted_bearing(insert_ent, authored_deg):
    """Bearing a baseline authored at ``authored_deg`` PLOTS at, degrees.

    Ground truth from the DXF INSERT definition itself: take the block-local
    unit vector along the text baseline and push it through the reference's
    own transform as a DIRECTION. Nothing in planlens is consulted.
    """
    import math
    from ezdxf.math import Vec3
    d = Vec3(math.cos(math.radians(authored_deg)),
             math.sin(math.radians(authored_deg)), 0.0)
    w = insert_ent.matrix44().transform_direction(d)
    return math.degrees(math.atan2(w.y, w.x)) % 360.0


#: (xscale, yscale, insert rotation) placements, each one a mirror of a
#: different flavor plus one pure rotation as the non-mirrored control.
_PLACEMENTS = [(-1.0, 1.0, 0.0), (1.0, -1.0, 0.0), (-1.0, 1.0, 30.0),
               (-1.0, -1.0, 0.0), (1.0, 1.0, 40.0)]

#: Baseline angles authored inside the block, including a couple that are
#: not multiples of 90 so a sign error cannot hide behind symmetry.
_AUTHORED = [0.0, 30.0, 90.0, 200.0, 315.0]


def _bearing_doc(xscale, yscale, rot):
    """DET holding one TEXT / MTEXT / ATTRIB per authored angle."""
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("DET")
    blk.add_line((0, 0), (10, 0))
    for i, a in enumerate(_AUTHORED):
        y = 1.0 + i
        blk.add_text(f"T{i}", dxfattribs={"insert": (1, y), "height": 0.25,
                                          "rotation": a})
        blk.add_mtext(f"M{i}", dxfattribs={"insert": (4, y),
                                           "char_height": 0.25,
                                           "rotation": a})
        blk.add_attdef(f"A{i}", insert=(7, y), height=0.25,
                       dxfattribs={"rotation": a})
    ref = doc.modelspace().add_blockref(
        "DET", (50, 48), dxfattribs={"xscale": xscale, "yscale": yscale,
                                     "rotation": rot})
    ref.add_auto_attribs({f"A{i}": f"V{i}" for i in range(len(_AUTHORED))})
    return doc, ref


class TestMirroredTextBearing:
    """Text plots in the direction the INSERT transform says it does."""

    @pytest.mark.parametrize("xscale,yscale,rot", _PLACEMENTS)
    def test_text_bearing_matches_the_insert_transform(self, tmp_path,
                                                       xscale, yscale, rot):
        doc, ref = _bearing_doc(xscale, yscale, rot)
        path = str(tmp_path / "bearing.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        for i, authored in enumerate(_AUTHORED):
            txt = next(e for e in _by_type(ir, TextItem)
                       if e.content == f"T{i}")
            assert txt.rotation % 360.0 == pytest.approx(
                _plotted_bearing(ref, authored), abs=1e-6), (i, authored)

    @pytest.mark.parametrize("xscale,yscale,rot", _PLACEMENTS)
    def test_attrib_bearing_matches_the_insert_transform(self, tmp_path,
                                                         xscale, yscale, rot):
        # An ATTRIB is a TEXT in every coordinate respect and was the other
        # raw read: the title-block metadata carrier on every real sheet.
        doc, ref = _bearing_doc(xscale, yscale, rot)
        path = str(tmp_path / "bearing.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        for i, authored in enumerate(_AUTHORED):
            txt = next(e for e in _by_type(ir, TextItem)
                       if e.content == f"V{i}")
            assert txt.rotation % 360.0 == pytest.approx(
                _plotted_bearing(ref, authored), abs=1e-6), (i, authored)

    @pytest.mark.parametrize("xscale,yscale,rot", _PLACEMENTS)
    def test_mtext_bearing_matches_the_insert_transform(self, tmp_path,
                                                        xscale, yscale, rot):
        # MTEXT keeps its bearing in group 11 (a WCS vector) once ezdxf has
        # transformed it, leaving group 50 at the AUTHORED value — so the raw
        # group-50 read was wrong here too, and wrong in a way the extrusion
        # alone does not explain.
        doc, ref = _bearing_doc(xscale, yscale, rot)
        path = str(tmp_path / "bearing.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        for i, authored in enumerate(_AUTHORED):
            txt = next(e for e in _by_type(ir, TextItem)
                       if e.content == f"M{i}")
            assert txt.rotation % 360.0 == pytest.approx(
                _plotted_bearing(ref, authored), abs=1e-6), (i, authored)

    def test_the_note_box_stays_on_its_own_detail(self, tmp_path):
        # The headline regression, stated the way it was measured.
        import ezdxf
        note = "SEE STRUCTURAL DETAIL 3 SHEET S-4"     # 33 characters
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6
        blk = doc.blocks.new("DET")
        blk.add_line((0, 0), (10, 0))
        blk.add_text(note, dxfattribs={"insert": (1, 2), "height": 0.25})
        doc.modelspace().add_blockref("DET", (50, 48),
                                      dxfattribs={"xscale": -1.0,
                                                  "yscale": 1.0})
        path = str(tmp_path / "note.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        txt = next(e for e in _by_type(ir, TextItem) if e.content == note)
        assert txt.position == pytest.approx((49.0, 50.0))   # already right
        x0, _, x1, _ = txt.compute_bbox()
        assert (x0, x1) == pytest.approx((44.05, 49.0), abs=1e-6)
        # It ran to 53.95 — 3.95 units off the end of a detail that spans
        # x 40..50, and on the opposite side of its own insert point.
        assert x1 <= 50.0 and x0 >= 40.0

    def test_unmirrored_rotation_is_returned_untouched(self, tmp_path):
        # Cap, don't delete — and do not normalise either. With the identity
        # extrusion the stored value must come through bit-for-bit, so an
        # unmirrored sheet ingests exactly as it did before this existed.
        import ezdxf
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        msp = doc.modelspace()
        msp.add_text("A", dxfattribs={"insert": (1, 1), "height": 0.25,
                                      "rotation": 30.0})
        msp.add_text("B", dxfattribs={"insert": (2, 1), "height": 0.25})
        path = str(tmp_path / "plain.dxf")
        doc.saveas(path)
        ir = from_dxf(path)
        rots = {e.content: e.rotation for e in _by_type(ir, TextItem)}
        assert rots == {"A": 30.0, "B": 0.0}

    def test_flip_y_negates_the_bearing(self, tmp_path):
        # ``flip_y`` mirrors the sheet about y, which reverses every bearing
        # — the ARC branch already said so; text used to disagree with it.
        import ezdxf
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        doc.modelspace().add_text("A", dxfattribs={"insert": (1, 1),
                                                   "height": 0.25,
                                                   "rotation": 30.0})
        path = str(tmp_path / "flip.dxf")
        doc.saveas(path)
        assert _by_type(from_dxf(path, flip_y=True),
                        TextItem)[0].rotation == pytest.approx(330.0)


# ---------------------------------------------------------------------------
# A TEXT-BEARING scene. The ten corpus sheets are SHX-stroked with NO text
# layer, so text_score is always 0 on them and every text-gated behavior is
# invisible there BY CONSTRUCTION. This scene is the standing measurement
# for the text leg: a hand-drafted dimension whose value text is inside a
# MIRRORED block. Before the OCS fix the text landed ~91 m from its own
# shaft and the proposal came back text-less.
# ---------------------------------------------------------------------------

MAS_M = 0.5   # arrowhead scale for this scene, in drawing meters


def _arrow_at(apex, direction, leg=0.39, base=0.13):
    """[base-corner, base-corner, apex] open chain, the native-dim flavor."""
    import math
    dx, dy = direction
    px, py = -dy, dx
    h = math.sqrt(leg * leg - (base / 2) ** 2)
    bx, by = apex[0] - dx * h, apex[1] - dy * h
    return [(bx + px * base / 2, by + py * base / 2),
            (bx - px * base / 2, by - py * base / 2), apex]


@pytest.fixture
def mirrored_text_dimension_dxf(tmp_path):
    """A hand-drafted, TEXT-LABELLED dimension inside a mirrored block.

    Block DIMDET draws the full anatomy the composition family looks for
    — two witness lines, a shaft between two outward arrows, and the
    value text near the shaft midpoint — and nothing in it is a native
    DIMENSION entity, so the proposal must be COMPOSED. Mirrored at
    (50, 20), the shaft plots from x 42 to x 48 and the text at
    (45.4, 20.75).
    """
    import ezdxf
    doc = ezdxf.new("R2010")
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("DIMDET")
    blk.add_line((2, 0.0), (2, 1.2))            # witness
    blk.add_line((8, 0.0), (8, 1.2))            # witness
    blk.add_line((2, 0.6), (8, 0.6))            # shaft
    blk.add_lwpolyline(_arrow_at((2, 0.6), (-1.0, 0.0)))
    blk.add_lwpolyline(_arrow_at((8, 0.6), (1.0, 0.0)))
    blk.add_text("24 FT", dxfattribs={"insert": (4.6, 0.75), "height": 0.3})
    doc.modelspace().add_blockref("DIMDET", (50, 20),
                                  dxfattribs={"xscale": -1.0, "yscale": 1.0})
    path = tmp_path / "mirrored_text_dim.dxf"
    doc.saveas(str(path))
    return str(path)


class TestMirroredTextBearingScene:
    """The text leg the SHX-stroked corpus cannot see, measured directly."""

    def _proposal(self, path):
        from planlens.ir import queries as q
        ir = from_dxf(path)
        props = [p for p in q.find_dimensions(ir, max_arrowhead_size=MAS_M,
                                              min_confidence=0.0)
                 if p["evidence"]["path"] != "native_dxf"]
        assert len(props) == 1, props
        return ir, props[0]

    def test_the_text_sits_on_its_own_shaft(self, mirrored_text_dimension_dxf):
        ir, _ = self._proposal(mirrored_text_dimension_dxf)
        txt = next(e for e in _by_type(ir, TextItem) if e.content == "24 FT")
        assert txt.position == pytest.approx((45.4, 20.75))

    def test_composed_dimension_reads_its_value(self,
                                                mirrored_text_dimension_dxf):
        _, p = self._proposal(mirrored_text_dimension_dxf)
        assert p["text"] == "24 FT"
        assert p["text_distance"] < MAS_M * 4.0
        assert sorted([p["end_a_xy"][0], p["end_b_xy"][0]]) == \
            pytest.approx([42.0, 48.0], abs=0.05)

    def test_text_unavailable_is_not_claimed(self,
                                             mirrored_text_dimension_dxf):
        # The failure mode a text-blind corpus hides: the construct is
        # still found, but reported as if the drawing carried no text.
        _, p = self._proposal(mirrored_text_dimension_dxf)
        assert "text_unavailable" not in p["evidence"]


# ---------------------------------------------------------------------------
# The identity a native annotation must carry so that ``queries.py`` can CAP
# a composed proposal a native supersedes — naming the native — instead of
# deleting it. Block explosion made block-nested natives common, which is
# what turned that deletion from rare into routine, so the contract is
# pinned on a BLOCK-NESTED native specifically.
# ---------------------------------------------------------------------------

@pytest.fixture
def block_nested_native_dxf(tmp_path):
    """DETAIL_A{LEADER + DIMENSION} on layer "0", placed on layer ANNO."""
    import ezdxf
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    doc.layers.add("ANNO")
    blk = doc.blocks.new("DETAIL_A")
    blk.add_line((0, 0), (10, 0))
    blk.add_leader(vertices=[(1, 1), (3, 3)], dxfattribs={"layer": "0"})
    dim = blk.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(10, 0),
                             dimstyle="EZDXF")
    dim.render()
    doc.modelspace().add_blockref("DETAIL_A", (100, 100),
                                  dxfattribs={"layer": "ANNO"})
    path = tmp_path / "block_native.dxf"
    doc.saveas(str(path))
    return str(path)


class TestNativeAnnotationIdentity:
    def test_block_nested_native_is_a_first_class_entity(
            self, block_nested_native_dxf):
        from planlens.ir.results import Dimension, Leader
        ir = from_dxf(block_nested_native_dxf)
        assert len(_by_type(ir, Leader)) == 1
        assert len(_by_type(ir, Dimension)) == 1

    def test_it_can_be_named_and_resolved_by_id(self,
                                                block_nested_native_dxf):
        from planlens.ir.results import Dimension
        ir = from_dxf(block_nested_native_dxf)
        dim = _by_type(ir, Dimension)[0]
        assert dim.id and ir.by_id(dim.id) is dim

    def test_block_provenance_is_the_first_style_token(
            self, block_nested_native_dxf):
        from planlens.ir.results import Dimension, Leader
        ir = from_dxf(block_nested_native_dxf)
        for e in _by_type(ir, Leader) + _by_type(ir, Dimension):
            assert (e.style or "").split("|")[0] == "block:DETAIL_A"

    def test_layer_is_the_resolved_one_not_the_sentinel(
            self, block_nested_native_dxf):
        from planlens.ir.results import Leader
        ir = from_dxf(block_nested_native_dxf)
        assert _by_type(ir, Leader)[0].layer == "ANNO"

    def test_truth_names_the_same_block_for_the_same_entity(
            self, block_nested_native_dxf):
        from planlens.dxf.truth import extract_native_annotations
        from planlens.ir.results import Leader
        ir = from_dxf(block_nested_native_dxf)
        t = extract_native_annotations(
            block_nested_native_dxf)["spaces"]["model"]
        ldr = _by_type(ir, Leader)[0]
        assert t["leaders"][0]["block"] == (ldr.style or "").split("|")[0][6:]
        assert t["leaders"][0]["layer"] == ldr.layer
