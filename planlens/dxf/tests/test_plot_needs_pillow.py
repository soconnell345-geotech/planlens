"""BACKLOG DR-19, review of DR-44: ezdxf's drawing add-on imports Pillow,
which planlens does not require. An install without it must say that, not
that the DXF cannot be read.

The DXF here is made up and drawn by the test with ezdxf.
"""

import sys

import pytest

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("pymupdf")

from planlens.dxf import plot  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402


def _without_pillow(monkeypatch):
    """This process as an install with no Pillow: the drawing add-on is
    imported afresh and its ``import PIL`` fails."""
    for name in list(sys.modules):
        if name == "PIL" or name.startswith(("PIL.",
                                             "ezdxf.addons.drawing")):
            monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "PIL", None)


def _dxf(path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (40, 20))
    doc.saveas(str(path))
    return str(path)


def test_without_pillow_the_plot_says_what_is_missing(tmp_path, monkeypatch):
    path = _dxf(tmp_path / "plan.dxf")
    _without_pillow(monkeypatch)
    with pytest.raises(plot.PlotUnavailable, match="Pillow"):
        plot.dxf_to_pdf(filepath=path)


def test_without_pillow_the_toolkit_does_not_blame_the_file(tmp_path,
                                                            monkeypatch):
    path = _dxf(tmp_path / "plan.dxf")
    _without_pillow(monkeypatch)
    out = ReviewToolkit(dxf_plots=True).call("open_document",
                                             {"source": path})
    assert "Pillow" in out["error"]
    assert "not a DXF" not in out["error"]
