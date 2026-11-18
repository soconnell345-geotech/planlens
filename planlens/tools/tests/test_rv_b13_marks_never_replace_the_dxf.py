"""Release review of the unreleased change (DR-19 meeting ``annotate_document``):
a toolkit that opens a DXF as a plot holds the plot's pages in memory, so
the document has no file of its own, and the rule that a marked-up copy is
never written onto the document it was read from did not see the DXF. With
``append=False`` the marked PDF replaced the drawing; with ``append=True``
the DXF was read as the copy to add to and the call raised.

Made-up drawing; the plot itself is stood in for by a one-page PDF made
here, so the test needs neither Pillow nor ezdxf's drawing add-on.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.tools import ReviewToolkit  # noqa: E402

DXF = b"  0\nSECTION\n  2\nENTITIES\n  0\nENDSEC\n  0\nEOF\n"
MARK = [{"kind": "box", "page": 0, "bbox": [100, 100, 200, 150],
         "comment": "made up"}]


def _plot(filepath=None, content=None, **_kw):
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=300)
    page.draw_line((20, 20), (380, 280))
    return doc.tobytes()


@pytest.fixture
def kit(tmp_path, monkeypatch):
    from planlens.dxf import plot
    monkeypatch.setattr(plot, "dxf_to_pdf", _plot)
    made = ReviewToolkit(dxf_plots=True, output_dir=str(tmp_path / "out"))
    yield made
    made.close()


@pytest.fixture
def drawing(tmp_path):
    path = tmp_path / "site.dxf"
    path.write_bytes(DXF)
    return str(path)


@pytest.mark.parametrize("append", [True, False])
def test_marks_are_refused_onto_the_dxf_the_plot_was_made_from(
        kit, drawing, append):
    opened = kit.call("open_document", {"source": drawing})
    assert opened.get("n_pages") == 1 and "plot" in opened, opened
    out = kit.call("annotate_document", {
        "handle": opened["handle"], "output_path": drawing,
        "markups": MARK, "append": append})
    assert "error" in out and "DXF" in out["error"], out
    assert "n_written" not in out
    with open(drawing, "rb") as fh:
        assert fh.read() == DXF


def test_marks_still_go_on_a_new_file_beside_it(kit, drawing, tmp_path):
    opened = kit.call("open_document", {"source": drawing})
    target = str(tmp_path / "site_marked.pdf")
    out = kit.call("annotate_document", {
        "handle": opened["handle"], "output_path": target, "markups": MARK})
    assert out.get("n_written") == 1, out
    with open(drawing, "rb") as fh:
        assert fh.read() == DXF
    with open(target, "rb") as fh:
        assert fh.read(5) == b"%PDF-"
