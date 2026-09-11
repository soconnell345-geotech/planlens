"""B1 tests: native DXF annotation entities as first-class IR citizens.

Covers: from_dxf ingest of LEADER / MULTILEADER / DIMENSION / INSERT-ATTRIB,
the Leader/Dimension IR schema (serialization round-trip), the natives-first
preference in find_leaders/find_dimensions, and the planlens.dxf.truth
ground-truth extractor (verified against the committed Mecklenburg truth
corpus offline: regenerating all 10 committed *.truth.json reproduces them
BYTE-FOR-BYTE — the extractor emits paper-space layouts as of 6ba4e90 and
walks block-nested annotations as of the Phase-3.2 close-out; re-measured
10/10 byte-identical 2026-09-06. Here it is exercised against synthetic
files).

The load-bearing contract this module pins is AGREEMENT: for the same DXF,
what truth.py says was drawn and what from_dxf + queries propose as
``native_dxf`` must be the same coordinates. They are two views of one
file, and a recall number is meaningless if its denominator and its
detections disagree about what the file contains.
"""

from __future__ import annotations

import math

import pytest

ezdxf = pytest.importorskip("ezdxf")

from planlens.dxf.truth import extract_native_annotations
from planlens.ir import DrawingIR, from_dxf, queries as q
from planlens.ir.results import (
    Dimension, Leader, Line, Polyline, entity_from_dict,
)


@pytest.fixture(scope="module")
def synthetic_dxf(tmp_path_factory):
    """A small DXF with one of each native annotation construct."""
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # meters — keeps conv factor 1.0
    msp = doc.modelspace()

    msp.add_line((0, 0), (10, 0))
    msp.add_leader(vertices=[(2.0, 2.0), (3.5, 3.0), (4.5, 3.0)],
                   dxfattribs={"layer": "ANNO"})
    dim = msp.add_linear_dim(base=(3, 2), p1=(0, 0), p2=(6, 0),
                             dimstyle="EZDXF")
    dim.render()

    blk = doc.blocks.new(name="TITLE")
    blk.add_attdef("SHEET", (0, 0), dxfattribs={"height": 0.25})
    ins = msp.add_blockref("TITLE", (8.0, 1.0))
    ins.add_auto_attribs({"SHEET": "S-101"})

    path = str(tmp_path_factory.mktemp("dxf") / "native.dxf")
    doc.saveas(path)
    return path


class TestFromDxfNative:
    def test_leader_entity(self, synthetic_dxf):
        ir = from_dxf(synthetic_dxf)
        leaders = [e for e in ir.entities if isinstance(e, Leader)]
        assert len(leaders) == 1
        L = leaders[0]
        assert L.confidence == 1.0 and L.source == "dxf"
        assert L.vertices[0] == pytest.approx((2.0, 2.0))  # tip first
        assert L.layer == "ANNO"

    def test_dimension_entity(self, synthetic_dxf):
        ir = from_dxf(synthetic_dxf)
        dims = [e for e in ir.entities if isinstance(e, Dimension)]
        assert len(dims) == 1
        D = dims[0]
        assert D.confidence == 1.0
        assert len(D.defpoints) >= 2
        assert D.measurement == pytest.approx(6.0, abs=1e-6)
        # defpoint2/3 are the measured extension origins (0,0) and (6,0).
        xs = sorted(p[0] for p in D.defpoints)
        assert xs[0] == pytest.approx(0.0, abs=1e-6)

    def test_attrib_textitem(self, synthetic_dxf):
        ir = from_dxf(synthetic_dxf)
        attribs = [e for e in ir.entities if e.KIND == "text"
                   and (e.style or "").startswith("attrib:")]
        assert len(attribs) == 1
        assert attribs[0].content == "S-101"
        assert attribs[0].style == "attrib:TITLE:SHEET"
        assert attribs[0].position[0] == pytest.approx(8.0)

    def test_serialization_round_trip(self, synthetic_dxf):
        import json
        ir = from_dxf(synthetic_dxf)
        rt = DrawingIR.from_dict(json.loads(json.dumps(ir.to_dict())))
        assert rt.counts_by_type() == ir.counts_by_type()
        L = next(e for e in rt.entities if isinstance(e, Leader))
        assert L.vertices[0] == pytest.approx((2.0, 2.0))
        D = next(e for e in rt.entities if isinstance(e, Dimension))
        assert D.measurement == pytest.approx(6.0, abs=1e-6)

    def test_entity_from_dict_direct(self):
        L = entity_from_dict({"type": "leader", "id": "x1",
                              "vertices": [[0, 0], [1, 1]],
                              "has_arrowhead": True, "text": "NOTE"})
        assert isinstance(L, Leader) and L.text == "NOTE"
        D = entity_from_dict({"type": "dimension", "id": "x2",
                              "defpoints": [[0, 0], [2, 0]],
                              "measurement": 2.0, "dimtype": 33})
        assert isinstance(D, Dimension) and D.dimtype == 33


class TestNativePreference:
    def _ir_with_native_leader(self):
        ir = DrawingIR(units="m", coordinate_space="model",
                       origin="bottom_left", source="dxf")
        ir.add(Leader(id="L1", vertices=[(2.0, 2.0), (3.5, 3.0)],
                      text="NOTE 1", source="dxf", confidence=1.0))
        return ir

    def test_native_leader_surfaces_at_full_confidence(self):
        ir = self._ir_with_native_leader()
        props = q.find_leaders(ir)
        assert len(props) == 1
        p = props[0]
        assert p["confidence"] == 1.0
        assert p["evidence"]["path"] == "native_dxf"
        assert p["tip_xy"] == [2.0, 2.0]
        assert p["text"] == "NOTE 1"

    def test_composed_duplicate_near_native_tip_is_capped_not_deleted(self):
        # A manually drafted arrow+shaft at the SAME tip as the native
        # leader must not double-report AT THE CALL THRESHOLD; one far
        # away must still compose. The duplicate is CAPPED rather than
        # deleted, so it stays visible to a caller who lowers
        # min_confidence and says which native outranked it.
        ir = self._ir_with_native_leader()
        mas = 0.1

        def plant(tip, direction, eid):
            dx, dy = direction
            px, py = -dy, dx
            base = (tip[0] - dx * 0.07, tip[1] - dy * 0.07)
            ir.add(Polyline(id=f"{eid}a", closed=True, vertices=[
                (base[0] + px * 0.025, base[1] + py * 0.025),
                (base[0] - px * 0.025, base[1] - py * 0.025), tip]))
            ir.add(Line(id=f"{eid}s", start=base,
                        end=(base[0] - dx * 0.5, base[1] - dy * 0.5)))

        plant((2.0, 2.0), (0.0, -1.0), "dup")     # on the native tip
        plant((5.0, 5.0), (1.0, 0.0), "manual")   # elsewhere
        props = q.find_leaders(ir, max_arrowhead_size=mas)
        natives = [p for p in props
                   if p["evidence"].get("path") == "native_dxf"]
        composed = [p for p in props
                    if p["evidence"].get("path") != "native_dxf"]
        assert len(natives) == 1
        # Both compose, but the one on the native tip is capped and names
        # the native that superseded it.
        assert len(composed) == 2
        dup = [p for p in composed
               if "superseded_by_native" in p["evidence"]]
        far = [p for p in composed
               if "superseded_by_native" not in p["evidence"]]
        assert len(dup) == 1 and len(far) == 1
        assert dup[0]["evidence"]["superseded_by_native"] == "L1"
        assert dup[0]["confidence"] <= q._UNCORROBORATED_CAP
        # tip_xy is the SHAFT endpoint (the deterministic anchor), which
        # sits at the arrow base — one arrow-length shy of the apex.
        assert far[0]["tip_xy"] == pytest.approx([4.93, 5.0], abs=0.01)

        # The CALLER-FACING behaviour is unchanged: at the conventional
        # threshold only the native and the unrelated composed leader are
        # returned, exactly as when the duplicate was deleted outright.
        called = q.find_leaders(ir, max_arrowhead_size=mas,
                                min_confidence=0.5)
        assert sorted(p["evidence"].get("path", "composed")
                      for p in called) == ["composed", "native_dxf"]

    def test_native_dimension_surfaces_and_needs_no_linework(self):
        ir = DrawingIR(units="m", coordinate_space="model",
                       origin="bottom_left", source="dxf")
        ir.add(Dimension(id="D1", defpoints=[(3.0, 2.0), (0.0, 0.0)],
                         text_midpoint=(1.5, 2.1), measurement=6.0,
                         text="6.0 m", dimtype=1,
                         source="dxf", confidence=1.0))
        props = q.find_dimensions(ir)
        assert len(props) == 1
        p = props[0]
        assert p["confidence"] == 1.0
        assert p["evidence"]["path"] == "native_dxf"
        assert p["end_a_xy"] == [3.0, 2.0]
        assert p["text"] == "6.0 m"


class TestTruthExtractor:
    def test_schema_and_values(self, synthetic_dxf):
        truth = extract_native_annotations(synthetic_dxf)
        m = truth["spaces"]["model"]
        assert set(m) == {"leaders", "multileaders", "dimensions",
                          "text", "inserts"}
        assert len(m["leaders"]) == 1
        assert m["leaders"][0]["vertices"][0] == [2.0, 2.0]
        assert len(m["dimensions"]) == 1
        assert m["dimensions"][0]["measurement"] == pytest.approx(6.0)
        assert m["inserts"][0]["name"] == "TITLE"
        assert m["inserts"][0]["attribs"][0]["text"] == "S-101"

    def test_requires_exactly_one_source(self):
        with pytest.raises(ValueError):
            extract_native_annotations()


@pytest.fixture(scope="module")
def block_annotated_dxf(tmp_path_factory):
    """Annotations reachable ONLY through blocks, at real transforms.

    DETAIL_A (leader + linear dim) placed plainly at (100,100); OUTER_D
    wrapping INNER_D (leader + dim) placed at (50,50) with rotation 30 and
    uniform scale 2 — depth-2 nesting under a transform, which is where a
    truth extractor and an ingest explosion would drift apart if either
    reconstructed coordinates instead of reading them.
    """
    doc = ezdxf.new("R2018", setup=True)
    doc.header["$INSUNITS"] = 6  # meters — keeps the conv factor 1.0
    a = doc.blocks.new("DETAIL_A")
    a.add_line((0, 0), (10, 0))
    a.add_leader(vertices=[(1, 1), (3, 3), (5, 3)])
    a.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(10, 0),
                     dimstyle="EZDXF").render()
    inner = doc.blocks.new("INNER_D")
    inner.add_leader(vertices=[(1, 1), (2, 2)])
    inner.add_linear_dim(base=(0, -2), p1=(0, 0), p2=(4, 0),
                         dimstyle="EZDXF").render()
    outer = doc.blocks.new("OUTER_D")
    outer.add_blockref("INNER_D", (0, 0))
    msp = doc.modelspace()
    msp.add_blockref("DETAIL_A", (100, 100))
    msp.add_blockref("OUTER_D", (50, 50),
                     dxfattribs={"rotation": 30.0, "xscale": 2.0,
                                 "yscale": 2.0})
    path = str(tmp_path_factory.mktemp("dxf") / "block_annotated.dxf")
    doc.saveas(path)
    return path


class TestTruthAgreesWithIngest:
    """Block-nested natives: one file, two surfaces, one answer.

    truth.py used to walk only the top-level entity list, so a leader
    drawn inside a detail block was invisible to the ground truth while
    from_dxf proposed it at confidence 1.0 — a denominator that denied
    geometry the plotter puts on paper. Demoting the ingest side was ruled
    out by measurement: ezdxf hands back the DIMENSION entity, never its
    rendered arrows, so dropping it erases the annotation with no
    geometric residue for composition to recover.
    """

    def test_counts_agree(self, block_annotated_dxf):
        truth = extract_native_annotations(block_annotated_dxf)
        m = truth["spaces"]["model"]
        ir = from_dxf(block_annotated_dxf)
        assert len(m["leaders"]) == len(
            [e for e in ir.entities if isinstance(e, Leader)]) == 2
        assert len(m["dimensions"]) == len(
            [e for e in ir.entities if isinstance(e, Dimension)]) == 2

    def test_coordinates_agree_through_nesting_and_transform(
            self, block_annotated_dxf):
        truth = extract_native_annotations(block_annotated_dxf)
        m = truth["spaces"]["model"]
        ir = from_dxf(block_annotated_dxf)
        leader_props = q.find_leaders(ir, min_confidence=0.3,
                                      exclude_dimensions=True)
        dim_props = q.find_dimensions(ir, min_confidence=0.3)
        tips = [tuple(p["tip_xy"]) for p in leader_props
                if p["evidence"]["path"] == "native_dxf"]
        ends = [tuple(p["end_a_xy"]) for p in dim_props
                if p["evidence"]["path"] == "native_dxf"]
        for rec in m["leaders"]:
            want = tuple(rec["vertices"][0])
            assert any(math.dist(want, got) < 1e-6 for got in tips), rec
        for rec in m["dimensions"]:
            want = tuple(rec["defpoint"])
            assert any(math.dist(want, got) < 1e-6 for got in ends), rec

    def test_provenance_names_the_same_block_on_both_sides(
            self, block_annotated_dxf):
        truth = extract_native_annotations(block_annotated_dxf)
        m = truth["spaces"]["model"]
        ir = from_dxf(block_annotated_dxf)
        assert sorted(r["block"] for r in m["leaders"]) == ["DETAIL_A",
                                                            "OUTER_D"]
        ir_blocks = sorted((e.style or "").split("|")[0]
                           for e in ir.entities if isinstance(e, Leader))
        assert ir_blocks == ["block:DETAIL_A", "block:OUTER_D"]

    def test_explode_blocks_off_diverges_by_request(self,
                                                    block_annotated_dxf):
        # Documented, not a defect: a caller who turns the explosion off
        # is asking for the top-level view and gets it.
        ir = from_dxf(block_annotated_dxf, explode_blocks=False)
        assert [e for e in ir.entities if isinstance(e, Leader)] == []
