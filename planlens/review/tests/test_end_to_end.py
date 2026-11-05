"""Offline end to end through the host layer, with a fake look (PL-29 (a)).

One made-up drawing sheet carrying a tag three times. The host is a
ReviewHost with a fake look, a working folder and a signer, nothing more:
``annotate_document`` writes a marked copy and the markup check confirms
its mark, ``find_like`` finds every copy of the tag with the look reading
the contact sheets, and no module of the app or of an agent framework is
imported along the way.
"""

import json
import subprocess
import sys
import textwrap

import pytest

pymupdf = pytest.importorskip("pymupdf")
pytest.importorskip("planlens.document.findlike")

from planlens import review  # noqa: E402

TAG = "TK-7"
PLACES = [(100, 100), (300, 400), (450, 650)]


def _sheet() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    for x, y in PLACES:
        page.insert_text((x, y), TAG, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data


class FakeLook:
    """Reads every contact-sheet cell as the tag, and says a checked mark
    is on the tag."""

    def __init__(self):
        self.kinds = []

    def __call__(self, image, question, kind):
        assert isinstance(image, (bytes, bytearray)) and image
        self.kinds.append(kind)
        if kind == "find":
            return "\n".join(f"#{n} | {TAG} | other" for n in range(1, 40))
        return json.dumps({"inside": TAG, "same_thing": True,
                           "encloses": True, "sure": True})


def test_a_marked_copy_a_search_and_a_check(tmp_path):
    look = FakeLook()
    review.configure(review.ReviewHost(
        look=look, working_folder=lambda: str(tmp_path),
        signer="Test Reviewer (AI draft)"))
    att = {"sheet.pdf": _sheet()}
    assert "find_like" in review.tool_names()

    opened = json.loads(review.call("open_document", {"source": "sheet.pdf"},
                                    attachments=att))
    hits = json.loads(review.call("search_document",
                                  {"handle": opened["handle"],
                                   "pattern": TAG}, attachments=att))
    assert hits["n_hits"] == 3
    box = hits["hits"][0]["bbox"]

    marked = json.loads(review.call("annotate_document", {
        "handle": opened["handle"], "output_path": "sheet_marked.pdf",
        "markups": [{"kind": "box", "page": 0, "comment": "confirm this tag",
                     "target": TAG, "bbox": box}]}, attachments=att))
    assert "error" not in marked, marked
    assert marked["n_written"] == 1
    assert marked["author"] == "Test Reviewer (AI draft)"
    assert (tmp_path / "sheet_marked.pdf").is_file()
    assert marked["check"]["checked"] == 1
    assert marked["check"]["confirmed"] == 1, marked["check"]

    found = json.loads(review.call("find_like", {
        "attachment_key": "sheet.pdf", "page": 0, "bbox": box,
        "text": TAG}, attachments=att))
    assert "error" not in found, found
    assert found["instances"] == 3
    assert found["instances_by_page"] == {"0": 3}
    assert found["contact_sheets"] and all(
        (tmp_path / ".scratch").joinpath(p.rsplit("/", 1)[-1]).is_file()
        for p in found["contact_sheets"])

    assert "check" in look.kinds and "find" in look.kinds


def test_no_module_of_the_app_or_an_agent_framework_is_imported():
    code = textwrap.dedent("""
        import json, sys, tempfile, pymupdf
        from planlens import review
        d = pymupdf.open(); p = d.new_page()
        p.insert_text((100, 100), "TK-7"); p.insert_text((300, 300), "TK-7")
        pdf = d.tobytes(); d.close()
        tmp = tempfile.mkdtemp()
        def look(image, question, kind):
            if kind == "find":
                return "\\n".join(f"#{n} | TK-7 | other" for n in range(1, 9))
            return json.dumps({"inside": "TK-7", "same_thing": True,
                               "encloses": True, "sure": True})
        review.configure(review.ReviewHost(look=look,
                                           working_folder=lambda: tmp))
        att = {"s.pdf": pdf}
        h = json.loads(review.call("search_document",
                       {"handle": "s.pdf", "pattern": "TK-7"}, att))
        box = h["hits"][0]["bbox"]
        review.call("annotate_document", {"handle": "s.pdf", "markups": [
            {"kind": "box", "page": 0, "comment": "c", "bbox": box}]}, att)
        review.call("find_like", {"attachment_key": "s.pdf", "page": 0,
                                  "bbox": box, "text": "TK-7"}, att)
        bad = sorted(m for m in sys.modules
                     if m.split(".")[0] in ("funhouse_agent", "webapp",
                                            "langchain", "langchain_core",
                                            "langgraph", "deepagents"))
        print(json.dumps(bad))
    """)
    out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                         text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout.strip().splitlines()[-1]) == []
