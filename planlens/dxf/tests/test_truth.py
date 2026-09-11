"""Tests for planlens.dxf.truth — native-annotation ground-truth extraction.

Phase-3.2 addition: paper-space layouts are extracted too, and annotations
authored INSIDE blocks are walked out of them (the committed Mecklenburg
corpus has none, so regeneration still reproduces all 10 committed files
byte-for-byte — re-verified 2026-09-06 after the block walk landed).
"""

from __future__ import annotations

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.dxf import truth as truth_mod
from planlens.dxf.truth import extract_native_annotations


def _doc_with_layout_text():
    doc = ezdxf.new("R2010")
    msp = doc.modelspace()
    msp.add_text("MODEL NOTE", dxfattribs={"insert": (1, 2), "height": 0.2})
    layout = doc.layout("Layout1")
    layout.add_text("SHEET TITLE", dxfattribs={"insert": (5, 1),
                                               "height": 0.25})
    return doc


class TestPaperSpaceExtraction:
    def test_layouts_extracted_under_their_names(self):
        truth = extract_native_annotations(doc=_doc_with_layout_text())
        assert set(truth["spaces"]) == {"model", "Layout1"}
        assert truth["spaces"]["model"]["text"][0]["text"] == "MODEL NOTE"
        assert truth["spaces"]["Layout1"]["text"][0]["text"] == "SHEET TITLE"

    def test_model_space_first_in_order(self):
        # Corpus files serialize "model" first; dict order is the contract.
        truth = extract_native_annotations(doc=_doc_with_layout_text())
        assert list(truth["spaces"]) == ["model", "Layout1"]

    def test_empty_layout_still_present(self):
        doc = ezdxf.new("R2010")
        doc.modelspace().add_line((0, 0), (1, 0))
        truth = extract_native_annotations(doc=doc)
        assert "Layout1" in truth["spaces"]
        assert truth["spaces"]["Layout1"]["text"] == []

    def test_exactly_one_source_required(self):
        with pytest.raises(ValueError):
            extract_native_annotations()
        with pytest.raises(ValueError):
            extract_native_annotations(filepath="x.dxf",
                                       doc=_doc_with_layout_text())


def _doc_with_block_annotations():
    """DETAIL_A holds a LEADER and a DIMENSION; inserted once at (100,100).

    Nothing is drawn at the top level except the block reference, so every
    annotation this file plots is reachable only through the block.
    """
    doc = ezdxf.new("R2018", setup=True)
    blk = doc.blocks.new("DETAIL_A")
    blk.add_line((0, 0), (10, 0))
    blk.add_leader(vertices=[(1, 1), (3, 3), (5, 3)])
    dim = blk.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(10, 0),
                             dimstyle="EZDXF")
    dim.render()
    doc.modelspace().add_blockref("DETAIL_A", (100, 100))
    return doc


class TestBlockNestedAnnotations:
    """A leader inside a block is plotted on the sheet, so it is truth."""

    def test_block_leader_extracted_at_the_insert_transform(self):
        m = extract_native_annotations(
            doc=_doc_with_block_annotations())["spaces"]["model"]
        assert len(m["leaders"]) == 1          # was 0 before the walk
        assert m["leaders"][0]["vertices"][0] == [101.0, 101.0]
        assert m["leaders"][0]["block"] == "DETAIL_A"

    def test_block_dimension_extracted_at_the_insert_transform(self):
        m = extract_native_annotations(
            doc=_doc_with_block_annotations())["spaces"]["model"]
        assert len(m["dimensions"]) == 1       # was 0 before the walk
        assert m["dimensions"][0]["defpoint"] == [100.0, 98.0]
        assert m["dimensions"][0]["measurement"] == pytest.approx(10.0)
        assert m["dimensions"][0]["block"] == "DETAIL_A"

    def test_the_insert_itself_is_still_recorded_once(self):
        m = extract_native_annotations(
            doc=_doc_with_block_annotations())["spaces"]["model"]
        assert len(m["inserts"]) == 1
        assert m["inserts"][0]["name"] == "DETAIL_A"

    def test_nested_block_names_the_outermost(self):
        # OUTER{INSERT INNER{leader}} placed with rotation + scale: the
        # provenance key matches ingest's style="block:<outermost>".
        doc = ezdxf.new("R2018", setup=True)
        inner = doc.blocks.new("INNER_D")
        inner.add_leader(vertices=[(1, 1), (2, 2)])
        outer = doc.blocks.new("OUTER_D")
        outer.add_blockref("INNER_D", (0, 0))
        doc.modelspace().add_blockref(
            "OUTER_D", (50, 50),
            dxfattribs={"rotation": 30.0, "xscale": 2.0, "yscale": 2.0})
        m = extract_native_annotations(doc=doc)["spaces"]["model"]
        assert len(m["leaders"]) == 1
        assert m["leaders"][0]["block"] == "OUTER_D"

    def test_top_level_records_come_first(self):
        # Order contract: direct annotations in space order, then nested.
        doc = _doc_with_block_annotations()
        doc.modelspace().add_leader(vertices=[(0, 0), (1, 1)])
        m = extract_native_annotations(doc=doc)["spaces"]["model"]
        assert len(m["leaders"]) == 2
        assert "block" not in m["leaders"][0]
        assert m["leaders"][1]["block"] == "DETAIL_A"

    def test_block_text_deliberately_not_promoted(self):
        # Scope line: promoting block TEXT would change every committed
        # corpus file and move the published OCR-coverage denominators.
        doc = ezdxf.new("R2010")
        blk = doc.blocks.new("SEAL")
        blk.add_text("MECKLENBURG COUNTY",
                     dxfattribs={"insert": (0, 0), "height": 0.2})
        doc.modelspace().add_blockref("SEAL", (0, 0))
        m = extract_native_annotations(doc=doc)["spaces"]["model"]
        assert m["text"] == []
        assert len(m["inserts"]) == 1

    def test_paper_space_blocks_walked_too(self):
        doc = ezdxf.new("R2018", setup=True)
        blk = doc.blocks.new("PS_DETAIL")
        blk.add_leader(vertices=[(1, 1), (2, 2)])
        doc.layout("Layout1").add_blockref("PS_DETAIL", (10, 10))
        t = extract_native_annotations(doc=doc)
        assert len(t["spaces"]["Layout1"]["leaders"]) == 1
        assert t["spaces"]["model"]["leaders"] == []


class TestBlockWalkGuards:
    def test_clean_file_carries_no_warnings_key(self):
        # Additive-only: the key must be ABSENT on clean files, or every
        # committed truth file changes.
        t = extract_native_annotations(doc=_doc_with_block_annotations())
        assert "warnings" not in t
        assert list(t) == ["source_dxf", "spaces"]

    def test_entity_budget_caps_the_walk(self):
        t = extract_native_annotations(doc=_doc_with_block_annotations(),
                                       max_block_entities=1)
        assert t["spaces"]["model"]["leaders"] == []
        assert t["spaces"]["model"]["dimensions"] == []
        assert any("entity budget" in w for w in t["warnings"])

    def test_depth_cap_warns_and_names_the_unexploded_block(self):
        doc = ezdxf.new("R2010")
        b0 = doc.blocks.new("L0")
        b0.add_leader(vertices=[(0, 0), (1, 1)])
        for i in range(1, 12):
            b = doc.blocks.new(f"L{i}")
            b.add_blockref(f"L{i-1}", (0, 0))
        doc.modelspace().add_blockref("L11", (0, 0))
        t = extract_native_annotations(doc=doc)
        assert t["spaces"]["model"]["leaders"] == []
        assert any("nesting deeper than 8 levels" in w
                   for w in t["warnings"])
        assert any("'L3'" in w for w in t["warnings"])


class TestBlockCapParity:
    """truth and ingest must stop exploding at the SAME point.

    Different caps would let a pathological file make the two surfaces
    disagree again — the exact class of bug the block walk closes.
    """

    def test_caps_match_the_ingest_side(self):
        from planlens.ir import ingest as ing
        assert truth_mod.MAX_BLOCK_DEPTH == ing._MAX_BLOCK_DEPTH
        assert (truth_mod.DEFAULT_MAX_BLOCK_ENTITIES
                == ing._DEFAULT_MAX_BLOCK_ENTITIES)

    def test_depth_cap_agrees_entity_for_entity(self):
        import tempfile, os
        from planlens.ir import from_dxf
        from planlens.ir.results import Leader
        doc = ezdxf.new("R2010")
        doc.header["$INSUNITS"] = 6
        b0 = doc.blocks.new("L0")
        b0.add_leader(vertices=[(0, 0), (1, 1)])
        for i in range(1, 12):
            b = doc.blocks.new(f"L{i}")
            b.add_blockref(f"L{i-1}", (0, 0))
        doc.modelspace().add_blockref("L11", (0, 0))
        path = os.path.join(tempfile.mkdtemp(), "deep.dxf")
        doc.saveas(path)
        t = extract_native_annotations(path)
        ir = from_dxf(path)
        assert t["spaces"]["model"]["leaders"] == []
        assert [e for e in ir.entities if isinstance(e, Leader)] == []
        assert any("nesting deeper" in w for w in t["warnings"])
        assert any("nesting deeper" in w for w in ir.warnings)

    def test_block_nested_layer_agrees_with_the_ingest_side(self, tmp_path):
        # Layer "0" inside a block means "the PLACING reference's layer".
        # truth reported the literal "0" while from_dxf resolved it, so
        # the two surfaces described one leader as being on two different
        # layers while the docstring claimed entity-for-entity agreement.
        from planlens.ir import from_dxf
        from planlens.ir.results import Leader
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6
        doc.layers.add("PLACED")
        blk = doc.blocks.new("DET_L")
        blk.add_leader(vertices=[(1, 1), (2, 2)], dxfattribs={"layer": "0"})
        doc.modelspace().add_blockref("DET_L", (0, 0),
                                      dxfattribs={"layer": "PLACED"})
        path = str(tmp_path / "layer_agreement.dxf")
        doc.saveas(path)
        t = extract_native_annotations(path)["spaces"]["model"]
        ir = from_dxf(path)
        assert t["leaders"][0]["layer"] == "PLACED"
        assert [e for e in ir.entities
                if isinstance(e, Leader)][0].layer == "PLACED"

    def test_nested_chain_resolves_to_the_first_named_layer(self, tmp_path):
        doc = ezdxf.new("R2018", setup=True)
        doc.header["$INSUNITS"] = 6
        for lyr in ("X", "Q"):
            doc.layers.add(lyr)
        inner = doc.blocks.new("IN_L")
        inner.add_leader(vertices=[(1, 1), (2, 2)],
                         dxfattribs={"layer": "0"})
        b = doc.blocks.new("B_L")
        b.add_blockref("IN_L", (0, 0), dxfattribs={"layer": "0"})
        c = doc.blocks.new("C_L")
        c.add_blockref("IN_L", (10, 0), dxfattribs={"layer": "Q"})
        msp = doc.modelspace()
        msp.add_blockref("B_L", (0, 0), dxfattribs={"layer": "X"})
        msp.add_blockref("C_L", (0, 0), dxfattribs={"layer": "X"})
        path = str(tmp_path / "chain.dxf")
        doc.saveas(path)
        m = extract_native_annotations(path)["spaces"]["model"]
        assert sorted(l["layer"] for l in m["leaders"]) == ["Q", "X"]


class TestUntransformableEntitiesAreReported:
    """A native annotation that ezdxf drops must not vanish silently.

    ezdxf omits an entity it cannot transform — a non-uniformly scaled
    MULTILEADER is the everyday one — from ``virtual_entities()`` WITHOUT
    raising, so every guard in the walk sees a clean run. In GROUND TRUTH
    that is a recall denominator moving with nobody told.
    """

    def _doc(self, scale=(2.0, 1.0)):
        from ezdxf.render import mleader
        doc = ezdxf.new("R2018", setup=True)
        blk = doc.blocks.new("MLB")
        blk.add_line((0, 0), (5, 0))
        ml = blk.add_multileader_mtext("Standard")
        ml.set_content("CALLOUT")
        ml.add_leader_line(mleader.ConnectionSide.left,
                           [ezdxf.math.Vec2(2, 2)])
        ml.build(insert=ezdxf.math.Vec2(4, 4))
        doc.modelspace().add_blockref(
            "MLB", (0, 0), dxfattribs={"xscale": scale[0],
                                       "yscale": scale[1]})
        return doc

    def test_dropped_multileader_becomes_a_warning(self):
        t = extract_native_annotations(doc=self._doc())
        assert t["spaces"]["model"]["multileaders"] == []
        assert any("MULTILEADER" in w and "NOT in this truth record" in w
                   for w in t["warnings"])

    def test_uniform_scale_still_extracts_it_with_no_warning(self):
        t = extract_native_annotations(doc=self._doc(scale=(2.0, 2.0)))
        assert len(t["spaces"]["model"]["multileaders"]) == 1
        assert "warnings" not in t

    def test_a_type_this_module_never_records_is_not_a_loss(self,
                                                            monkeypatch):
        # An OLE2FRAME sits inside the county-seal block on ALL TEN
        # committed sheets. It is not an annotation, so it was never
        # going to appear in a truth record — reporting it added a
        # ``warnings`` key to all ten files and broke the byte-identical
        # regeneration contract for a record none of them contained.
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
        t = extract_native_annotations(doc=_doc_with_block_annotations())
        assert "warnings" not in t


# ---------------------------------------------------------------------------
# Mirrored placements — the OCS/extrusion transform (see :func:`_ocs_xy`).
# There are zero negative-scale INSERTs across the ten committed corpus
# sheets, so this class is the only measurement of the behavior.
# ---------------------------------------------------------------------------

def _mirrored_doc():
    """DET (LINE + TEXT + LEADER + DIMENSION) mirrored at (50, 48).

    The block occupies x 0..10, so everything it plots must land in
    x 40..50. Also carries a top-level TEXT and INSERT authored on a
    mirrored UCS, which is how a directly drawn entity acquires an
    extrusion of (0,0,-1) without any block being involved.
    """
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6
    blk = doc.blocks.new("DET")
    blk.add_line((0, 0), (10, 0))
    blk.add_text("NOTE", dxfattribs={"insert": (5, 2), "height": 0.25})
    blk.add_leader(vertices=[(1, 1), (3, 3)])
    dim = blk.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(10, 0),
                             dimstyle="EZDXF")
    dim.render()
    msp = doc.modelspace()
    msp.add_blockref("DET", (50, 48),
                     dxfattribs={"xscale": -1.0, "yscale": 1.0})
    msp.add_text("TOP", dxfattribs={"insert": (-20.0, 5.0), "height": 0.25,
                                    "extrusion": (0, 0, -1)})
    msp.add_blockref("DET", (-30.0, 5.0),
                     dxfattribs={"extrusion": (0, 0, -1)})
    return doc


class TestMirroredInsert:
    def test_dimension_text_midpoint_is_ocs(self):
        m = extract_native_annotations(doc=_mirrored_doc())["spaces"]["model"]
        d = m["dimensions"][0]
        # Group 10 is WCS and was always right; group 11 was read raw and
        # came back at x = -45 for a detail spanning x 40..50.
        assert d["defpoint"] == [50.0, 46.0]
        assert d["text_midpoint"][0] == pytest.approx(45.0)

    def test_top_level_mirrored_text_insert_is_ocs(self):
        m = extract_native_annotations(doc=_mirrored_doc())["spaces"]["model"]
        top = next(t for t in m["text"] if t["text"] == "TOP")
        assert top["insert"] == [20.0, 5.0]

    def test_top_level_mirrored_insert_point_is_ocs(self):
        m = extract_native_annotations(doc=_mirrored_doc())["spaces"]["model"]
        mirrored = [i for i in m["inserts"]
                    if i["insert"] == [30.0, 5.0]]
        assert len(mirrored) == 1

    def test_leader_vertices_stay_wcs(self):
        m = extract_native_annotations(doc=_mirrored_doc())["spaces"]["model"]
        block_leaders = [l for l in m["leaders"] if l.get("block") == "DET"]
        assert [49.0, 49.0] in [v for l in block_leaders
                                for v in l["vertices"]]

    def test_truth_and_ingest_agree_on_the_mirrored_dimension(self,
                                                              tmp_path):
        # The load-bearing contract of this module: two views of one
        # file must report the same coordinates. Both surfaces read the
        # text midpoint as WCS, so both were wrong by the same 90 units
        # and AGREED — agreement alone was never the whole check.
        from planlens.ir import from_dxf
        from planlens.ir.results import Dimension
        path = str(tmp_path / "mirrored.dxf")
        _mirrored_doc().saveas(path)
        t = extract_native_annotations(path)["spaces"]["model"]
        ir = from_dxf(path)
        dim = [e for e in ir.entities if isinstance(e, Dimension)][0]
        td = t["dimensions"][0]
        assert dim.defpoints[0] == pytest.approx(tuple(td["defpoint"]),
                                                 abs=1e-4)
        assert dim.text_midpoint == pytest.approx(
            tuple(td["text_midpoint"]), abs=1e-4)
        # ...and both sit inside the footprint the block actually plots.
        assert 40.0 <= dim.text_midpoint[0] <= 50.0
