"""BACKLOG DR-19, review of DR-44: the plot of a DXF is bounded. It is made
while its conversation's toolkit is locked, so a very large or a crafted DXF
must be refused in plain words, not hold the conversation or fill memory.

Every DXF here is made up and drawn by the test with ezdxf.
"""

import time

import pytest

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("pymupdf")
pytest.importorskip("PIL", reason="plotting a DXF needs Pillow")

from planlens.dxf import plot  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402


def _nested(path, depth, fan=10):
    """A few lines of DXF that draw ``fan ** depth`` lines: each block
    holds ``fan`` copies of the one below it."""
    doc = ezdxf.new()
    doc.blocks.new("L0").add_line((0, 0), (1, 1))
    for level in range(1, depth + 1):
        block = doc.blocks.new(f"L{level}")
        for i in range(fan):
            block.add_blockref(f"L{level - 1}", (i * 2.0 ** level, level))
    doc.modelspace().add_blockref(f"L{depth}", (0, 0))
    doc.saveas(str(path))
    return str(path)


def _lines(path, n):
    doc = ezdxf.new()
    msp = doc.modelspace()
    for i in range(n):
        msp.add_line((i, 0), (i + 0.5, 5))
    doc.saveas(str(path))
    return str(path)


def test_the_bounds_are_stated():
    assert plot.MAX_FILE_MB == 16.0
    assert plot.MAX_ENTITIES == 50_000
    assert plot.MAX_SECONDS == 30.0


def test_a_small_file_of_nested_blocks_is_refused_not_drawn(tmp_path):
    # one INSERT in the model space, 10 ** 8 lines behind it
    path = _nested(tmp_path / "nested.dxf", depth=8)
    t0 = time.monotonic()
    with pytest.raises(plot.PlotTooLarge, match="more than 50000 entities"):
        plot.dxf_to_pdf(filepath=path)
    assert time.monotonic() - t0 < plot.MAX_SECONDS


def test_entities_are_counted_with_blocks_expanded(tmp_path):
    path = _nested(tmp_path / "nested.dxf", depth=2)       # 100 lines
    with pytest.raises(plot.PlotTooLarge, match="more than 50 entities"):
        plot.dxf_to_pdf(filepath=path, max_entities=50)
    assert plot.dxf_to_pdf(filepath=path, max_entities=500)[:4] == b"%PDF"


def test_a_plot_that_takes_too_long_stops(tmp_path):
    path = _lines(tmp_path / "lines.dxf", 200)
    with pytest.raises(plot.PlotTooLarge, match="took longer"):
        plot.dxf_to_pdf(filepath=path, max_seconds=0.0)


def test_the_pdf_writer_is_held_to_the_time_too(tmp_path, monkeypatch):
    # drawing is quick and writing the PDF is the slow part: the time runs
    # out only once the writing has begun
    path = _lines(tmp_path / "lines.dxf", 50)
    from ezdxf.addons.drawing.pymupdf import PyMuPdfBackend
    real = PyMuPdfBackend.get_replay
    ticks = {"now": 0.0}
    monkeypatch.setattr(plot.time, "monotonic", lambda: ticks["now"])

    def late(self, *args, **kwargs):
        ticks["now"] += 1000.0
        return real(self, *args, **kwargs)
    monkeypatch.setattr(PyMuPdfBackend, "get_replay", late)
    with pytest.raises(plot.PlotTooLarge, match="took longer"):
        plot.dxf_to_pdf(filepath=path)


def test_a_file_too_large_is_not_read(tmp_path):
    path = _lines(tmp_path / "lines.dxf", 20)
    with pytest.raises(plot.PlotTooLarge, match="MB"):
        plot.dxf_to_pdf(filepath=path, max_file_mb=0.001)
    with pytest.raises(plot.PlotTooLarge, match="MB"):
        plot.dxf_to_pdf(content=open(path, "rb").read(), max_file_mb=0.001)


def test_the_toolkit_says_it_in_plain_words(tmp_path, monkeypatch):
    path = _lines(tmp_path / "lines.dxf", 20)
    monkeypatch.setattr(plot, "MAX_ENTITIES", 5)
    kit = ReviewToolkit(dxf_plots=True)
    out = kit.call("open_document", {"source": path})
    assert "could not plot" in out["error"]
    assert "more than 5 entities" in out["error"]
    assert "read_document" in out["error"]
    # nothing was opened, and the toolkit is not left locked
    assert kit.call("open_document", {"source": path})["error"] == out[
        "error"]
