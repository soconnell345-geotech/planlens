"""Release review of the unreleased change (DR-19 meeting PL-27):
``ReviewToolkit.forget(path)`` closes the documents read from a file a host
replaced, and ``planlens.review``'s ``forget_path`` now relies on it. A DXF
opened as a plot is held in memory, with no file of its own, so it was
matched only when it had been opened under that exact path: opened under a
name the host's resolver maps to the file, the plot of the OLD drawing
stayed open under its handle after the drawing was replaced.

Made-up drawing; the plot is stood in for by a one-page PDF made here, so
the test needs neither Pillow nor ezdxf's drawing add-on.
"""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.tools import ReviewToolkit  # noqa: E402

NAME = "site plan"                      # the resolver's name, no extension


def _plot(filepath=None, content=None, **_kw):
    doc = pymupdf.open()
    doc.new_page(width=400, height=300).draw_line((20, 20), (380, 280))
    return doc.tobytes()


@pytest.fixture
def drawing(tmp_path):
    path = tmp_path / "site.dxf"
    path.write_bytes(b"  0\nSECTION\n  2\nENTITIES\n  0\nENDSEC\n  0\nEOF\n")
    return str(path)


@pytest.fixture
def kit(drawing, monkeypatch):
    from planlens.dxf import plot
    monkeypatch.setattr(plot, "dxf_to_pdf", _plot)
    made = ReviewToolkit(resolve_source=lambda source: drawing,
                         dxf_plots=True)
    yield made
    made.close()


def test_forget_closes_the_plot_of_the_file_it_was_made_from(kit, drawing):
    opened = kit.call("open_document", {"source": NAME})
    assert opened.get("n_pages") == 1 and "plot" in opened, opened
    assert kit.forget(drawing) == [opened["handle"]]
    again = kit.call("document_page_map", {"handle": opened["handle"]})
    assert "unknown document handle" in again.get("error", ""), again


def test_the_host_layers_forget_path_closes_it_too(drawing, monkeypatch,
                                                   tmp_path):
    import json

    from planlens import review
    from planlens.dxf import plot
    from planlens.review import conversation
    from planlens.review.host import ReviewHost
    monkeypatch.setattr(plot, "dxf_to_pdf", _plot)
    host = ReviewHost(resolve_source=lambda source: drawing,
                      toolkit_options={"dxf_plots": True})
    conversation._SPACES.clear()
    out = json.loads(review.call("document_page_map", {"handle": "site.dxf"},
                                 {}, host=host))
    assert "rows" in out, out
    with review.using(host):
        assert conversation.forget_path(drawing) == 1
    conversation._SPACES.clear()
