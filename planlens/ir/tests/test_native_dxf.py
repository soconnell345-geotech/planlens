"""B1 tests: native DXF annotation entities as first-class IR citizens.

Covers: from_dxf ingest of LEADER / MULTILEADER / DIMENSION / INSERT-ATTRIB,
the Leader/Dimension IR schema (serialization round-trip), the natives-first
preference in find_leaders/find_dimensions, and the planlens.dxf.truth
ground-truth extractor (verified 10/10 byte-identical against the committed
Mecklenburg truth corpus offline; here against a synthetic file).
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

    def test_composed_duplicate_near_native_tip_suppressed(self):
        # A manually drafted arrow+shaft at the SAME tip as the native
        # leader must not double-report; one far away must still compose.
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
        assert len(composed) == 1
        # tip_xy is the SHAFT endpoint (the deterministic anchor), which
        # sits at the arrow base — one arrow-length shy of the apex.
        assert composed[0]["tip_xy"] == pytest.approx([4.93, 5.0], abs=0.01)

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
