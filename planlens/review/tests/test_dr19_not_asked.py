"""BACKLOG DR-19, review of DR-44: a host that did not ask for a DXF's plot
never gets one. The conversation's toolkit is shared and can plot; the host
layer knows a DXF by the name the model gave, the toolkit also by the path
that name resolves to, so a host whose resolver maps a plain name to a
``.dxf`` path reached the toolkit and was handed a plot it did not ask for.

The DXF here is made up and drawn by the test with ezdxf.
"""

import json

import pytest

ezdxf = pytest.importorskip("ezdxf")
pytest.importorskip("pymupdf")
pytest.importorskip("PIL", reason="plotting a DXF needs Pillow")

from planlens import review  # noqa: E402
from planlens.review import conversation  # noqa: E402
from planlens.review.host import ReviewHost  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

ASK = {"dxf_plots": True}
NAME = "site plan"                      # no extension: the resolver's name


def _dxf(tmp_path):
    doc = ezdxf.new()
    doc.modelspace().add_line((0, 0), (60, 30))
    path = tmp_path / "site.dxf"
    doc.saveas(str(path))
    return str(path)


def _call(host, name, arguments):
    return json.loads(review.call(name, arguments, {}, host=host))


def test_a_name_resolved_to_a_dxf_is_not_plotted_for_a_host_that_did_not_ask(
        tmp_path):
    path = _dxf(tmp_path)
    plain = ReviewHost(resolve_source=lambda source: path)
    conversation._SPACES.clear()
    out = _call(plain, "open_document", {"source": NAME})
    assert "plot" not in out
    assert out["error"].startswith(
        f"could not open '{NAME}' as a PDF or image")
    out = _call(plain, "render_page", {"handle": NAME, "page": 0})
    assert "error" in out and "image_path" not in out


def test_nor_after_a_host_that_asked_opened_the_same_file(tmp_path):
    path = _dxf(tmp_path)
    plain = ReviewHost(resolve_source=lambda source: path)
    asked = ReviewHost(resolve_source=lambda source: path,
                       toolkit_options=ASK)
    conversation._SPACES.clear()
    opened = _call(asked, "open_document", {"source": NAME})
    assert "plot" in opened and opened["n_pages"] == 1
    out = _call(plain, "open_document", {"source": NAME})
    assert "plot" not in out
    assert out["error"].startswith(
        f"could not open '{NAME}' as a PDF or image")
    # and the host that asked still has its plot
    again = _call(asked, "open_document", {"source": NAME})
    assert again["handle"] == opened["handle"]


def test_a_toolkit_can_be_asked_at_each_open(tmp_path):
    path = _dxf(tmp_path)
    asks = {"now": False}
    kit = ReviewToolkit(dxf_plots=lambda: asks["now"])
    assert "error" in kit.call("open_document", {"source": path})
    asks["now"] = True
    assert kit.call("open_document", {"source": path})["n_pages"] == 1
    asks["now"] = False
    assert "error" in kit.call("open_document", {"source": path})
