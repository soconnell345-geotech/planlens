"""One toolkit per conversation, handles and sources interchangeable,
viewer page numbers, Word/Excel/DXF read as text (PL-29 (a)).

Moved from the app's test_conversation_isolation.py and
test_wave2b_documents_offline.py. Two conversations are two working folders
the host binds in turn (``ReviewHost.working_folder``), each with a resolver
confined to its own files (``conftest.confined``), the way the app's
confinement resolves them.

Left in the app: its file tools (``list_files``, ``read_text_file``,
``read_pdf_text``, ``save_file``), its reference-library and Databricks read
roots, and its own Word/Excel reader (python-docx and openpyxl are not
planlens dependencies: here a made-up reader stands in for the host's).
"""

import json
import os

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.review import conversation, marked_copy  # noqa: E402
from planlens.review.tests.conftest import configure_host  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402
from planlens.tools import ToolError  # noqa: E402


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def _pdf(text: str) -> bytes:
    d = pymupdf.open()
    d.new_page().insert_text((72, 72), text)
    data = d.tobytes()
    d.close()
    return data


def _doc(name, args, attachments=None, max_chars=7500):
    return json.loads(conversation.dispatch_document_tool(
        name, args, attachments=attachments or {}, max_chars=max_chars))


@pytest.fixture
def people(tmp_path, monkeypatch):
    """Two conversations' working folders; the server process runs in a
    folder holding a secret."""
    server = tmp_path / "server"
    server.mkdir()
    (server / ".env").write_text("SECRET=1")
    monkeypatch.chdir(server)
    out = {}
    for who in ("alice", "bob"):
        files = tmp_path / "data" / f"{who}01" / "files"
        files.mkdir(parents=True)
        out[who] = files
    (out["alice"] / "alice_report.pdf").write_bytes(_pdf("ALICE ONLY"))
    (out["bob"] / "plan.pdf").write_bytes(_pdf("BOB PLAN"))
    (out["bob"] / "bob_notes.txt").write_text("Bob's notes")
    return out


def as_(folder, **kw):
    return configure_host(folder, None, **kw)


# -- confinement ------------------------------------------------------------

def test_alices_document_is_invisible_to_bob(people):
    alice = people["alice"]
    as_(people["bob"])
    opened = _doc("open_document", {"source": str(alice / "alice_report.pdf")})
    server = _doc("open_document", {"source": str(os.getcwd()) + "/.env"})
    for out in (opened, server):
        assert "error" in out, out
        text = json.dumps(out)
        assert "ALICE ONLY" not in text and "SECRET" not in text
        assert "plan.pdf" in text and str(people["bob"]) not in text


def test_a_marked_up_copy_never_goes_into_another_folder(people):
    as_(people["bob"])
    where = marked_copy.markup_output_path(
        str(people["alice"] / "x_marked.pdf"))
    assert where == str(people["bob"] / "x_marked.pdf")


# -- a document handle belongs to the conversation that opened it -----------

def test_alices_handle_is_invisible_to_bob(people, gt):
    as_(people["alice"])
    h_alice = _doc("open_document", {"source": "set.pdf"},
                   {"set.pdf": gt.pdf})["handle"]
    as_(people["bob"])
    out = _doc("read_document", {"handle": h_alice})
    h_bob = _doc("open_document", {"source": "plan.pdf"})["handle"]
    miss = _doc("read_document", {"handle": "doc_0000000000"})
    assert "error" in out and "Alice" not in json.dumps(out)
    assert h_alice not in out.get("hint", "")
    assert h_bob in miss["hint"] and h_alice not in miss["hint"]
    as_(people["alice"])
    assert "error" not in _doc("read_document", {"handle": h_alice})


def test_the_open_document_limit_is_per_conversation(people):
    as_(people["bob"])
    h_bob = _doc("open_document", {"source": "plan.pdf"})["handle"]
    as_(people["alice"])
    for i in range(10):
        _doc("open_document", {"source": f"a{i}.pdf"},
             {f"a{i}.pdf": _pdf(f"alice document {i}")})
    as_(people["bob"])
    assert h_bob in conversation._toolkit()._entries
    assert "BOB PLAN" in json.dumps(_doc("read_document", {"handle": h_bob}))


def test_idle_toolkits_close_and_their_handles_reopen(people):
    as_(people["alice"], max_live_toolkits=1)
    h = _doc("open_document", {"source": "alice_report.pdf"})["handle"]
    alice_space = conversation._space()
    as_(people["bob"], max_live_toolkits=1)
    _doc("open_document", {"source": "plan.pdf"})
    assert alice_space.kit is None
    as_(people["alice"], max_live_toolkits=1)
    out = _doc("read_document", {"handle": h})
    assert "ALICE ONLY" in json.dumps(out) and "opened" not in out


def test_the_host_passes_its_look_to_each_toolkit(people):
    def look(image, question, kind):
        return "seen"

    as_(people["bob"], look=look)
    _doc("open_document", {"source": "plan.pdf"})
    assert conversation._toolkit().look is look


# -- a handle and a source are interchangeable --------------------------------

def test_a_file_name_works_where_a_handle_goes(people, gt):
    atts = {"submittal.pdf": gt.pdf}
    as_(people["bob"])
    out = _doc("search_document", {"handle": "submittal.pdf",
                                   "pattern": "embedment"}, atts)
    h = _doc("open_document", {"source": "submittal.pdf"}, atts)["handle"]
    bad = _doc("read_document", {"handle": "nothing_like_this.pdf"}, atts)
    assert out["n_hits"] == 2 and h in out["opened"]
    assert "did not open" in bad["error"] and "submittal.pdf" in bad["hint"]


def test_a_handle_works_where_a_file_goes(people, gt):
    atts = {"submittal.pdf": gt.pdf}
    as_(people["bob"])
    h = _doc("open_document", {"source": "submittal.pdf"}, atts)["handle"]
    again = _doc("open_document", {"source": h}, atts)
    assert again["handle"] == h and again["name"] == "submittal.pdf"
    assert conversation.handle_source(h) == "submittal.pdf"
    assert conversation.resolve_document_source(h, atts) == gt.pdf
    file_h = _doc("open_document", {"source": "plan.pdf"})["handle"]
    path = conversation.resolve_document_source(file_h, {})
    assert open(path, "rb").read()[:4] == b"%PDF"


def test_another_conversations_handle_is_not_a_file_here(people, gt):
    as_(people["alice"])
    h = _doc("open_document", {"source": "set.pdf"},
             {"set.pdf": gt.pdf})["handle"]
    as_(people["bob"])
    assert conversation.handle_source(h) is None
    with pytest.raises(ToolError):
        conversation.resolve_document_source(h, {})


def test_an_open_entry_is_found_by_handle_or_source(people, gt):
    as_(people["bob"])
    entry = conversation.open_document_entry("plan.pdf")
    assert conversation.document_entry(entry.handle) is entry
    assert conversation.document_entry("doc_ffffffffff") is None
    assert conversation.forget_path(str(people["bob"] / "plan.pdf")) == 1
    # closed, its handle reopens from the source it came from
    assert conversation.document_entry(entry.handle).handle == entry.handle


# -- scratch images, one place per conversation, short names -----------------

def test_thumbnails_go_to_the_conversations_scratch(people, gt):
    as_(people["bob"])
    h = _doc("open_document", {"source": "s.pdf"}, {"s.pdf": gt.pdf})["handle"]
    thumbs = _doc("render_page_thumbnails", {"handle": h, "pages": "0-3"})
    name = thumbs["sheets"][0]["image_path"]
    assert name.startswith(".scratch/") and "planlens_" not in name
    assert (people["bob"] / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert not (people["alice"] / ".scratch").exists()


# -- a document tool that raises -----------------------------------------------

def test_a_raising_tool_is_a_json_error_without_a_server_path(
        gt, tmp_path, monkeypatch):
    from planlens.tools import ReviewToolkit
    folder = tmp_path / "conv" / "files"
    folder.mkdir(parents=True)
    as_(folder)

    def explode(self, name, arguments, max_chars=None):
        raise RuntimeError(f"code=2: cannot remove file '{folder}"
                           f"{os.sep}review_set_marked.pdf': Permission "
                           "denied")

    monkeypatch.setattr(ReviewToolkit, "call_json", explode)
    out = json.loads(conversation.dispatch_document_tool(
        "open_document", {"source": "s.pdf"}, attachments={"s.pdf": gt.pdf}))
    assert out["error"].startswith("open_document failed: RuntimeError")
    assert "review_set_marked.pdf" in out["error"]
    assert str(folder) not in json.dumps(out)
    assert "hint" in out


def test_the_host_words_a_raised_error_when_it_says_how(gt, monkeypatch):
    from planlens.tools import ReviewToolkit
    configure_host(None, None,
                   error_json=lambda tool, exc: json.dumps(
                       {"error": f"{tool}: host words"}))
    monkeypatch.setattr(ReviewToolkit, "call_json",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
    out = json.loads(conversation.dispatch_document_tool(
        "open_document", {"source": "s.pdf"}, attachments={"s.pdf": gt.pdf}))
    assert out == {"error": "open_document: host words"}


def test_a_lock_wait_is_put_on_the_hosts_record(gt):
    seen = []
    configure_host(None, None, record=lambda event, data: seen.append(event))
    conversation._report_lock_wait("read_document", 0.5)
    conversation._report_lock_wait("read_document", 0.001)
    assert seen == ["document_lock_wait"]


# -- page ranges in the viewer's numbers ---------------------------------------

def test_every_page_range_carries_the_viewers_numbers(gt):
    configure_host(None, None)
    out = _doc("open_document", {"source": "s.pdf"}, {"s.pdf": gt.pdf})
    by_kind, shown = out["pages_by_kind"], out["pdf_pages_by_kind"]
    for kind, ranges in by_kind.items():
        assert shown[kind] == conversation._viewer_ranges(ranges)
    read = _doc("read_document", {"handle": out["handle"], "pages": "0-1"},
                {"s.pdf": gt.pdf})
    assert read["pages_returned"] == "0-1"
    assert read["pdf_pages_returned"] == "1-2"


def test_ranges_convert_and_cursors_are_left_alone():
    data = {"pages": "0-2,5", "n_pages": 9, "pages_with_hits": {"3": 2},
            "next": {"pages": "4-8", "start_line": 3},
            "segments": [{"id": 0, "pages": "0-1", "title": "x"}]}
    out = json.loads(conversation.with_viewer_pages(json.dumps(data)))
    assert out["pdf_pages"] == "1-3,6"
    assert out["pdf_pages_with_hits"] == {"4": 2}
    assert "pdf_n_pages" not in out
    assert "pdf_pages" not in out["next"]
    assert out["segments"][0]["pdf_pages"] == "1-2"


# -- a DXF is read as CAD data ---------------------------------------------------

def _boring_plan(path):
    import ezdxf
    doc = ezdxf.new()
    doc.header["$INSUNITS"] = 2                         # feet
    msp = doc.modelspace()
    doc.layers.add("BORINGS")
    msp.add_line((0, 0), (400, 0), dxfattribs={"layer": "SITE"})
    msp.add_circle((100, 190), 4, dxfattribs={"layer": "BORINGS"})
    msp.add_text("B-2", dxfattribs={"insert": (100, 200), "height": 5,
                                    "layer": "BORINGS"})
    msp.add_text("C-2 BORING PLAN", dxfattribs={"insert": (10, 380),
                                               "height": 8, "layer": "TITLE"})
    doc.saveas(path)


def test_a_dxf_is_read_as_cad_data_in_its_own_units(tmp_path):
    pytest.importorskip("ezdxf")
    folder = tmp_path / "conv" / "files"
    folder.mkdir(parents=True)
    _boring_plan(folder / "C-2_Boring_Plan.dxf")
    as_(folder)
    out = _doc("open_document", {"source": "C-2_Boring_Plan.dxf"})
    assert "error" not in out, out
    assert out["kind"] == "dxf drawing" and out["drawing_units"] == "ft"
    assert out["layers"]["BORINGS"] == 2
    assert 'TEXT "B-2" at (100, 200) [layer BORINGS]' in out["text"]
    assert out["text"].index("BORING PLAN") < out["text"].index('"B-2"')
    assert "CIRCLE centre (100, 190) radius 4" in out["text"]
    assert 'near "B-2"' in out["text"]
    assert "PDF plot" in out["note"]
    hits = _doc("search_document", {"handle": "C-2_Boring_Plan.dxf",
                                    "pattern": "B-2"})
    assert hits["n_hits"] >= 1
    refused = _doc("document_page_map", {"handle": "C-2_Boring_Plan.dxf"})
    assert "no pages" in refused["error"] and "annotate_dxf" in \
        refused["hint"]


# -- Word and Excel through the host's reader ------------------------------------

def test_an_office_file_is_read_by_the_hosts_reader(tmp_path):
    folder = tmp_path / "conv" / "files"
    folder.mkdir(parents=True)
    (folder / "Submittal Log.xlsx").write_bytes(b"PK\x03\x04 made up")
    rows = ["| No. | Item | Status |", "|---|---|---|",
            "| S-01 | Concrete mix 3000 psi | Submitted |",
            "| S-02 | Curb ramp detail | Resubmit |"]
    calls = []

    def reader(path, name):
        calls.append(name)
        return "\n".join(rows), {"sheets": ["Log"]}

    as_(folder, read_office=reader)
    opened = _doc("open_document", {"source": "Submittal Log.xlsx"})
    assert "error" not in opened, opened
    assert opened["kind"] == "excel workbook"
    assert opened["handle"] == "Submittal Log.xlsx"
    assert "Resubmit" in opened["text"] and "no pages" in opened["note"]
    assert opened["sheets"] == ["Log"]
    hits = _doc("search_document", {"handle": "Submittal Log.xlsx",
                                    "pattern": "resubmit"})
    assert hits["n_hits"] == 1 and "S-02" in hits["hits"][0]["text"]
    assert calls == ["Submittal Log.xlsx"]          # read once, then cached


def test_without_a_reader_an_office_file_is_said_plainly(tmp_path):
    folder = tmp_path / "conv" / "files"
    folder.mkdir(parents=True)
    (folder / "spec.docx").write_bytes(b"PK\x03\x04 made up")
    as_(folder)
    out = _doc("open_document", {"source": "spec.docx"})
    assert "reads none" in out["error"] and str(folder) not in json.dumps(out)
