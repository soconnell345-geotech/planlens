"""PL-38: planlens.review on large files. Two annotate calls on one marked
copy keep both calls' marks; a marked copy of a document opened from bytes
is written from those bytes, not from another whole copy of the file.
Every document here is made up inside the test."""
import json
import shutil
import threading

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.document import Document  # noqa: E402
from planlens.document import document as _document  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

SIGNER = "Test Reviewer (AI draft)"


def _pdf(n_pages=2):
    doc = pymupdf.open()
    for i in range(n_pages):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 100), f"Made-up sheet {i + 1} TK-{i + 1}",
                         fontsize=12)
    data = doc.tobytes()
    doc.close()
    return data


def _box(page, comment):
    return {"kind": "box", "page": page, "comment": comment,
            "bbox": [60, 80, 300, 110]}


def _contents(path):
    with pymupdf.open(path) as doc:
        return " | ".join((a.info or {}).get("content", "")
                          for page in doc for a in page.annots() or ())


def test_two_annotate_calls_on_one_copy_keep_both_calls_marks(
        tmp_path, monkeypatch):
    review.configure(review.ReviewHost(
        working_folder=lambda: str(tmp_path), signer=SIGNER))
    att = {"set.pdf": _pdf()}
    handle = json.loads(review.call(
        "open_document", {"source": "set.pdf"}, attachments=att))["handle"]
    first = json.loads(review.call("annotate_document", {
        "handle": handle, "output_path": "set_marked.pdf",
        "markups": [_box(0, "made-up mark one")]}, attachments=att))
    assert first["n_written"] == 1, first

    # Each call copies the marked copy before adding its marks. Hold each
    # call just after its copy until the other has made its own (or a
    # second has passed): two calls that copy the same file both write
    # from it, and the second swap drops the first call's mark.
    real_copy = shutil.copyfile
    turn = threading.Condition()
    copied = [0]

    def copy_then_wait(src, dst, *a, **k):
        out = real_copy(src, dst, *a, **k)
        with turn:
            copied[0] += 1
            turn.notify_all()
            turn.wait_for(lambda: copied[0] >= 2, timeout=1.0)
        return out

    monkeypatch.setattr(shutil, "copyfile", copy_then_wait)
    results = {}

    def mark(comment, page):
        results[comment] = json.loads(review.call("annotate_document", {
            "handle": handle, "output_path": "set_marked.pdf",
            "markups": [_box(page, comment)]}, attachments=att))

    threads = [threading.Thread(target=mark, args=(c, p)) for c, p in
               (("made-up mark two", 0), ("made-up mark three", 1))]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert all(r.get("n_written") == 1 for r in results.values()), results
    held = _contents(tmp_path / "set_marked.pdf")
    for comment in ("made-up mark one", "made-up mark two",
                    "made-up mark three"):
        assert comment in held, held
    from planlens.review import marked_copy
    assert marked_copy._COPY_LOCKS == {}     # no lock outlives its calls


def test_the_copy_lock_is_one_per_file_and_re_entrant(tmp_path):
    from planlens.review.marked_copy import _COPY_LOCKS, copy_lock
    a, b = str(tmp_path / "a_marked.pdf"), str(tmp_path / "b_marked.pdf")
    got = {"a": threading.Event(), "b": threading.Event()}

    def take(name, path):
        with copy_lock(path):
            got[name].set()

    with copy_lock(a):
        with copy_lock(a):                 # the same call again: no wait
            other = threading.Thread(target=take, args=("b", b))
            other.start()
            assert got["b"].wait(5)        # another copy: never waits
            other.join(5)
            same = threading.Thread(target=take, args=("a", a))
            same.start()
            assert not got["a"].wait(0.3)  # the same copy: waits
    assert got["a"].wait(5)
    same.join(5)
    assert _COPY_LOCKS == {}


def test_a_document_opened_from_bytes_is_not_copied_again():
    pdf = _pdf()
    with Document(content=pdf) as d:
        assert d.pdf_bytes() is pdf
    import io
    png = io.BytesIO()
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 40, 30), 0)
    pix.clear_with(200)
    png.write(pix.tobytes("png"))
    with Document(content=png.getvalue()) as d:     # an image: its PDF
        assert d.source_kind == "image"
        assert d.pdf_bytes()[:5] == b"%PDF-"


@pytest.mark.parametrize("from_file", [False, True])
def test_annotate_writes_the_copy_without_another_whole_copy(
        tmp_path, monkeypatch, from_file):
    pdf = _pdf()
    src = tmp_path / "in" / "set.pdf"
    src.parent.mkdir()
    src.write_bytes(pdf)
    kit = ReviewToolkit(
        resolve_source=lambda key: str(src) if from_file else pdf,
        output_dir=str(tmp_path / "out"))
    calls = []
    real = _document.Document.tobytes

    def counted(self):
        calls.append(1)
        return real(self)

    monkeypatch.setattr(_document.Document, "tobytes", counted)
    try:
        handle = json.loads(kit.call_json(
            "open_document", {"source": "set.pdf"}))["handle"]
        fresh = json.loads(kit.call_json("annotate_document", {
            "handle": handle, "output_path": "set_marked.pdf",
            "markups": [_box(0, "made-up mark one")]}))
        assert fresh["n_written"] == 1, fresh
        # from bytes: those bytes; from a file: the file's PDF, once
        assert len(calls) == (1 if from_file else 0)
        added = json.loads(kit.call_json("annotate_document", {
            "handle": handle, "output_path": "set_marked.pdf",
            "markups": [_box(1, "made-up mark two")]}))
        assert added["n_written"] == 1, added
        # adding to the copy reads the copy, never the document
        assert len(calls) == (1 if from_file else 0)
        held = _contents(fresh["output_path"])
        assert "made-up mark one" in held and "made-up mark two" in held
    finally:
        kit.close()
