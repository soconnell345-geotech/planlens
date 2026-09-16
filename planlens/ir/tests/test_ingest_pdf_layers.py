"""PDF-vector ingest: optional-content groups (layers) and path fill.

A plotted PDF keeps two things the ingest leg used to drop: the layer each
path was drawn on (AutoCAD writes one optional-content group per CAD layer)
and whether the path is PAINTED. Both are evidence a reviewer reads directly
— "existing" vs "proposed" is often only the layer name, and a filled circle
is how a boring is drawn — so these tests pin what survives ingest, what is
deliberately NOT ingested (a layer the document hides), and that carrying the
two facts changed nothing about which entities the leg emits.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.ir import DrawingIR, from_pdf_vector, queries as q
from planlens.ir.results import Circle, Line, Polyline, TextItem

BLACK = (0.0, 0.0, 0.0)
BLUE = (0.0, 0.0, 1.0)


def _scene(tmp_path, name, fill: bool = True) -> str:
    """A 400x300 sheet drawn twice over: once painted, once outline-only.

    Same geometry either way — one stroked line and a round symbol on layer
    EXISTING, a triangle on layer PROPOSED, an unlayered border, and one line
    on SUPERSEDED, which the document hides by default. ``fill=False`` draws
    the symbol and the triangle as outlines, which is what makes "fill adds a
    field, not an entity" testable by comparison.
    """
    doc = fitz.open()
    page = doc.new_page(width=400, height=300)
    existing = doc.add_ocg("EXISTING", on=True)
    proposed = doc.add_ocg("PROPOSED", on=True)
    hidden = doc.add_ocg("SUPERSEDED", on=False)

    page.draw_line(fitz.Point(20, 20), fitz.Point(120, 20), color=BLACK,
                   oc=existing)
    sh = page.new_shape()
    sh.draw_circle(fitz.Point(60, 80), 6)
    sh.finish(color=BLACK, fill=BLACK if fill else None, oc=existing)
    sh.commit()

    sh = page.new_shape()
    sh.draw_polyline([fitz.Point(200, 40), fitz.Point(220, 50),
                      fitz.Point(200, 60)])
    sh.finish(color=BLUE, fill=BLUE if fill else None, closePath=True,
              oc=proposed)
    sh.commit()

    page.draw_rect(fitz.Rect(10, 10, 390, 290), color=(0.5, 0.5, 0.5))
    page.draw_line(fitz.Point(300, 200), fitz.Point(380, 200), color=(1, 0, 0),
                   oc=hidden)

    path = tmp_path / name
    doc.save(str(path))
    doc.close()
    return str(path)


def _zero_layer_pdf(tmp_path) -> str:
    """One path on a group genuinely NAMED "0" (AutoCAD's default layer)."""
    doc = fitz.open()
    page = doc.new_page(width=200, height=100)
    page.draw_line(fitz.Point(10, 10), fitz.Point(90, 10), color=BLACK,
                   oc=doc.add_ocg("0", on=True))
    page.draw_line(fitz.Point(10, 40), fitz.Point(90, 40), color=BLACK)
    path = tmp_path / "zero_layer.pdf"
    doc.save(str(path))
    doc.close()
    return str(path)


def _by_layer(ir):
    out = {}
    for e in ir.entities:
        out.setdefault(e.layer, []).append(e)
    return out


class TestLayers:
    def test_every_entity_carries_its_group(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "layers.pdf"))
        groups = _by_layer(ir)
        assert set(groups) == {"EXISTING", "PROPOSED", None}
        assert len(groups["EXISTING"]) == 2      # the line and the symbol
        assert len(groups["PROPOSED"]) == 1      # the triangle
        assert len(groups[None]) == 1            # the border

    def test_no_layer_is_none_never_an_empty_string(self, tmp_path):
        # PyMuPDF reports "" for a path in no group. An empty string is not a
        # layer name, and a caller asking for entities with no layer must not
        # have to know which spelling of "nothing" the extractor used.
        ir = from_pdf_vector(_scene(tmp_path, "empty.pdf"))
        assert all(e.layer is None or e.layer for e in ir.entities)
        assert "" not in ir.counts_by_layer()
        assert ir.counts_by_layer()["(none)"] == 1

    def test_group_named_zero_is_a_real_name_here(self, tmp_path):
        # The DXF leg treats layer "0" inside a block as a SENTINEL meaning
        # "inherit the INSERT's layer" (see test_ingest_dxf). A PDF has no
        # such rule: a group named "0" is AutoCAD's default layer plotted
        # under its own name, and an unlayered path is None — never "0".
        ir = from_pdf_vector(_zero_layer_pdf(tmp_path))
        assert sorted(ir.counts_by_layer()) == ["(none)", "0"]
        assert len(q.entities_on_layer(ir, "0")) == 1

    def test_metadata_tally_and_ocg_summary(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "meta.pdf"))
        # Same meaning the DXF leg gives n_layers: distinct names SEEN during
        # ingest. The hidden group is declared but nothing on it was read.
        assert ir.metadata["n_layers"] == 2
        assert ir.metadata["ocgs"] == {"EXISTING": True, "PROPOSED": True,
                                       "SUPERSEDED": False}

    def test_no_ocgs_reports_zero_rather_than_silence(self, tmp_path):
        doc = fitz.open()
        doc.new_page(width=200, height=100).draw_line(
            fitz.Point(10, 10), fitz.Point(90, 10), color=BLACK)
        path = tmp_path / "plain.pdf"
        doc.save(str(path))
        doc.close()
        ir = from_pdf_vector(str(path))
        assert ir.metadata["n_layers"] == 0
        assert "ocgs" not in ir.metadata

    def test_slice_by_layer_works_as_on_dxf(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "slice.pdf"))
        hits = q.entities_on_layer(ir, "EXISTING")
        assert len(hits) == 2
        assert all(h["layer"] == "EXISTING" for h in hits)
        assert q.entities_on_layer(ir, "SUPERSEDED") == []
        assert ir.counts_by_layer() == {"EXISTING": 2, "PROPOSED": 1,
                                        "(none)": 1}
        assert q.summary_stats(ir)["counts_by_layer"]["PROPOSED"] == 1


class TestHiddenLayers:
    def test_hidden_group_is_omitted_and_said_so(self, tmp_path):
        # MuPDF honours the document's own default state, so nothing on a
        # group that is OFF reaches get_drawings(). The omission would be
        # invisible, hence the warning naming the group.
        ir = from_pdf_vector(_scene(tmp_path, "hidden.pdf"))
        assert not any(e.layer == "SUPERSEDED" for e in ir.entities)
        assert any("SUPERSEDED" in w and "include_hidden_layers" in w
                   for w in ir.warnings)

    def test_opt_in_reads_the_hidden_group(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "hidden_on.pdf"),
                             include_hidden_layers=True)
        hidden = [e for e in ir.entities if e.layer == "SUPERSEDED"]
        assert len(hidden) == 1
        assert ir.metadata["n_layers"] == 3
        assert ir.metadata["ocgs"]["SUPERSEDED"] is False   # as the file says
        assert not any("SUPERSEDED" in w for w in ir.warnings)


class TestFill:
    def test_painted_paths_carry_fill(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "fill.pdf"))
        filled = [e for e in ir.entities if e.filled]
        assert len(filled) == 2
        symbol = [e for e in filled if e.layer == "EXISTING"][0]
        triangle = [e for e in filled if e.layer == "PROPOSED"][0]
        assert symbol.fill_color == "#000000"
        assert triangle.fill_color == "#0000ff"

    def test_outline_only_paths_say_nothing_about_fill(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "outline.pdf", fill=False))
        assert not any(e.filled for e in ir.entities)
        assert all(e.fill_color is None for e in ir.entities)

    def test_fill_adds_a_field_not_an_entity(self, tmp_path):
        # The SAME geometry painted and unpainted must ingest to the same
        # entities: a filled triangle is the closed 3-vertex Polyline it has
        # always been, which is what the construct finders are built on.
        painted = from_pdf_vector(_scene(tmp_path, "a.pdf", fill=True))
        outline = from_pdf_vector(_scene(tmp_path, "b.pdf", fill=False))
        assert len(painted.entities) == len(outline.entities)
        for a, b in zip(painted.entities, outline.entities):
            assert type(a) is type(b)
            assert a.points() == b.points()
            assert a.layer == b.layer
            if isinstance(a, Polyline):
                assert a.closed == b.closed
        assert [e.KIND for e in painted.entities].count("polyline") == 3

    def test_arrowhead_stays_a_closed_three_vertex_polyline(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "arrow.pdf"))
        tri = [e for e in ir.entities if e.layer == "PROPOSED"][0]
        assert isinstance(tri, Polyline)
        assert tri.closed and len(tri.vertices) == 3
        assert tri.filled


class TestSerialization:
    def test_dicts_stay_compact(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "dict.pdf"))
        painted = [e for e in ir.entities if e.filled][0].to_dict()
        plain = [e for e in ir.entities if not e.filled][0].to_dict()
        assert painted["filled"] is True
        assert painted["fill_color"] == "#000000"
        assert "filled" not in plain and "fill_color" not in plain

    def test_round_trip(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "rt.pdf"))
        d = ir.to_dict()
        back = DrawingIR.from_dict(d)
        assert back.to_dict() == d
        painted = [e for e in back.entities if e.filled][0]
        assert painted.fill_color == "#000000"
        assert painted.layer == "EXISTING"

    def test_round_trip_of_a_filled_circle_entity(self):
        # The PDF leg draws a circle as a bezier ring, but a DXF hatch or a
        # raster trace can produce a real Circle — the envelope carries fill
        # for any area-bearing type, so the round trip is pinned here too.
        c = Circle(id="e0", center=(1.0, 2.0), radius=3.0, filled=True,
                   fill_color="#4080bf", layer="BORINGS")
        back = DrawingIR.from_dict(
            {"entities": [c.to_dict()]}).entities[0]
        assert isinstance(back, Circle)
        assert back.filled and back.fill_color == "#4080bf"
        assert back.layer == "BORINGS"

    def test_query_refs_carry_fill_only_when_painted(self, tmp_path):
        ir = from_pdf_vector(_scene(tmp_path, "refs.pdf"))
        refs = {r["id"]: r for r in q.entities_in_bbox(
            ir, -1e6, -1e6, 1e6, 1e6)}
        painted = [e for e in ir.entities if e.filled][0]
        plain = [e for e in ir.entities if not e.filled][0]
        assert refs[painted.id]["filled"] is True
        assert "filled" not in refs[plain.id]


class TestTextItems:
    def test_text_records_no_layer(self, cross_section_pdf):
        # PDF text spans carry no optional-content membership, so a TextItem's
        # layer is None rather than a guess at the nearest path's group.
        ir = from_pdf_vector(cross_section_pdf)
        texts = [e for e in ir.entities if isinstance(e, TextItem)]
        assert texts and all(t.layer is None for t in texts)
        assert all(isinstance(e, (Line, Polyline, TextItem))
                   for e in ir.entities)
