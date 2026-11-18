"""BACKLOG DR-19: a DXF opened as a PDF plot of its model space, so the page
tools can look at it -- in the toolkit with ``dxf_plots=True``, and in the
host layer for a host that asks (``toolkit_options={"dxf_plots": True}``).
Every other toolkit and host is answered as before.
"""

import json

import importlib.util

import pytest

ezdxf = pytest.importorskip("ezdxf")
pymupdf = pytest.importorskip("pymupdf")

#: ezdxf's drawing add-on imports Pillow, which planlens does not require:
#: the tests that make a plot are skipped where it is not installed
#: (``test_dr19_plot_bounds.py`` covers what is said then).
needs_pillow = pytest.mark.skipif(
    importlib.util.find_spec("PIL") is None,
    reason="plotting a DXF needs Pillow (ezdxf's drawing add-on)")

from planlens import review  # noqa: E402
from planlens.dxf.plot import PLOT_NOTE  # noqa: E402
from planlens.review import conversation  # noqa: E402
from planlens.review.host import ReviewHost  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

ASK = {"dxf_plots": True}


def _dxf_bytes(tmp_path):
    doc = ezdxf.new()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (60, 0), (60, 30), (0, 30)], close=True)
    msp.add_line((0, 15), (60, 15))
    msp.add_text("GRID B", height=2.0).set_placement((4, 22))
    path = tmp_path / "site.dxf"
    doc.saveas(str(path))
    return str(path), path.read_bytes()


# -- the toolkit ---------------------------------------------------------------

def test_a_toolkit_that_did_not_ask_refuses_a_dxf_as_before(tmp_path):
    path, _data = _dxf_bytes(tmp_path)
    out = ReviewToolkit().call("open_document", {"source": path})
    assert "error" in out and "plot" not in out
    assert out["error"].startswith(f"could not open '{path}' as a PDF")


@needs_pillow
def test_a_toolkit_that_asks_opens_a_plot_the_page_tools_read(tmp_path):
    path, data = _dxf_bytes(tmp_path)
    kit = ReviewToolkit(dxf_plots=True)
    out = kit.call("open_document", {"source": path})
    assert "error" not in out, out
    assert out["n_pages"] == 1 and out["name"] == "site.dxf"
    assert out["plot"] == PLOT_NOTE
    image, _info = kit.render(out["handle"], 0)
    assert image[:8] == b"\x89PNG\r\n\x1a\n"
    # the same from bytes under a DXF's name
    kit = ReviewToolkit(dxf_plots=True, resolve_source=lambda s: data)
    again = kit.call("open_document", {"source": "upload.dxf"})
    assert again["n_pages"] == 1 and again["plot"] == PLOT_NOTE
    # a PDF opens as it always has, with nothing said about a plot
    pdf = pymupdf.open()
    pdf.new_page()
    kit = ReviewToolkit(dxf_plots=True,
                        resolve_source=lambda s: pdf.tobytes())
    plain = kit.call("open_document", {"source": "sheet.pdf"})
    assert "plot" not in plain and plain["n_pages"] == 1


def test_a_file_that_will_not_plot_says_so(tmp_path):
    kit = ReviewToolkit(dxf_plots=True,
                        resolve_source=lambda s: b"0\nSECTION\n2\nNONSENSE")
    out = kit.call("open_document", {"source": "broken.dxf"})
    assert out["error"].startswith("could not plot 'broken.dxf'")


# -- the host layer --------------------------------------------------------------

def test_a_host_that_did_not_ask_is_answered_as_before(tmp_path):
    _path, data = _dxf_bytes(tmp_path)
    att = {"site.dxf": data}
    plain, asked = ReviewHost(), ReviewHost(toolkit_options=ASK)
    conversation._SPACES.clear()
    before = json.loads(review.call("open_document", {"source": "site.dxf"},
                                    att, host=plain))
    after = json.loads(review.call("open_document", {"source": "site.dxf"},
                                   att, host=asked))
    assert "plot" not in before and "text" in before
    # asking adds the plot's line and changes nothing else
    assert after.pop("plot").endswith(PLOT_NOTE)
    assert after == before
    refused = json.loads(review.call("document_page_map",
                                     {"handle": "site.dxf"}, att,
                                     host=plain))
    assert refused["error"].startswith("'site.dxf' is a DXF drawing")


@needs_pillow
def test_a_host_that_asks_gets_the_page_tools_on_a_plot(tmp_path):
    _path, data = _dxf_bytes(tmp_path)
    att = {"site.dxf": data}
    asked = ReviewHost(toolkit_options=ASK)
    conversation._SPACES.clear()
    pages = json.loads(review.call("document_page_map",
                                   {"handle": "site.dxf"}, att, host=asked))
    assert "error" not in pages, pages
    # read and search still read the DXF's own text
    read = json.loads(review.call("read_document", {"handle": "site.dxf"},
                                  att, host=asked))
    assert "GRID B" in read["text"]
