"""A markup-only divider page starts and titles a segment (PL-02), and
markup ids that survive a re-read and an edit (PL-06)."""

import os

import pytest

from planlens.document import Document


def _report(tab_markup=None, kind="freetext"):
    import fitz
    doc = fitz.open()
    for i in range(3):
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 30), "Geotechnical Report  Project 101", fontsize=9)
        p.insert_text((72, 770), f"Page {i + 1}", fontsize=9)
        y = 100
        for k in range(30):
            p.insert_text((72, y), f"Narrative line {k} of the report body "
                                   f"on page {i + 1}.", fontsize=10)
            y += 18
    # a scanned-looking tab: no text layer, just a markup
    tab = doc.new_page(width=612, height=792)
    if tab_markup:
        r = fitz.Rect(150, 330, 460, 400)
        if kind == "freetext":
            tab.add_freetext_annot(r, tab_markup, fontsize=24)
        else:
            tab.add_text_annot((200, 350), tab_markup)
    # the appendix: a different document
    for i in range(2):
        p = doc.new_page(width=612, height=792)
        p.insert_text((72, 30), "Laboratory Data", fontsize=9)
        for k in range(30):
            p.insert_text((72, 100 + 18 * k), f"Test result row {k}.",
                          fontsize=10)
    out = doc.tobytes()
    doc.close()
    return out


def test_markup_only_divider_starts_and_titles_a_segment():
    with Document(content=_report("APPENDIX B"), name="r.pdf") as d:
        s = d.summary(3)
        assert s.divider_title == "APPENDIX B"
        assert s.evidence["divider_from_markup"] == "p3.m0"
        segs = d.segments()
    tab = next(g for g in segs if g["pages"].startswith("3"))
    assert tab["title"] == "APPENDIX B"


def test_without_the_markup_the_tab_titles_nothing():
    with Document(content=_report(None), name="r.pdf") as d:
        assert d.summary(3).divider_title is None
        assert all(g["title"] != "APPENDIX B" for g in d.segments())


@pytest.mark.parametrize("text,kind", [
    ("Section 3 needs a revised bearing capacity check before we can "
     "accept it", "freetext"),        # a reviewer's comment, not a tab
    ("APPENDIX B", "note"),           # a sticky note's words are in a pop-up
])
def test_a_comment_is_not_a_divider(text, kind):
    with Document(content=_report(text, kind), name="r.pdf") as d:
        assert d.summary(3).divider_title is None


def _with_markups(n):
    import fitz
    doc = fitz.open()
    p = doc.new_page()
    p.insert_text((72, 72), "The bearing pressure is 2,500 psf.")
    for i in range(n):
        p.add_freetext_annot(fitz.Rect(72, 100 + 40 * i, 300, 130 + 40 * i),
                             f"comment {i}")
    out = doc.tobytes()
    doc.close()
    return out


def test_uid_survives_a_reread_and_deleting_an_earlier_markup(tmp_path):
    import fitz
    pdf = _with_markups(3)
    with Document(content=pdf, name="a.pdf") as d:
        first = {m.text: m for m in d.markups()}
    with Document(content=pdf, name="a.pdf") as d:
        again = {m.text: m for m in d.markups()}
    assert {k: m.uid for k, m in first.items()} == \
        {k: m.uid for k, m in again.items()}
    assert len({m.uid for m in first.values()}) == 3
    # delete the first comment: positional ids shift, uids do not
    doc = fitz.open(stream=pdf)
    page = doc[0]
    page.delete_annot(next(iter(page.annots())))
    edited = doc.tobytes(garbage=3)
    doc.close()
    with Document(content=edited, name="a.pdf") as d:
        after = {m.text: m for m in d.markups()}
    assert set(after) == {"comment 1", "comment 2"}
    for k in after:
        assert after[k].uid == first[k].uid
    assert after["comment 2"].id != first["comment 2"].id
    assert first["comment 1"].to_dict()["uid"] == first["comment 1"].uid


def test_uid_without_nm_is_a_content_hash():
    from planlens.document.annotations import stable_markup_id
    a = stable_markup_id(2, None, "Square", (1, 2, 3, 4), None, "R", "x")
    b = stable_markup_id(2, None, "Square", (1.2, 2, 3, 4), None, "R", "x")
    assert a == b and a.startswith("p2.h")
    assert stable_markup_id(2, "ABC", "Square", (1, 2, 3, 4), None, "R",
                            "x") == "p2.nABC"


def test_reply_by_uid_and_atomic_save(tmp_path):
    from planlens.document.markup_writer import write_markups
    src = tmp_path / "a.pdf"
    src.write_bytes(_with_markups(2))
    with Document(filepath=str(src)) as d:
        target = d.markups()[1]
    out = tmp_path / "out.pdf"
    rep = write_markups(str(src), str(out), [
        {"kind": "reply", "page": 0, "reply_to": target.uid,
         "comment": "agreed"}])
    assert not rep.skipped and rep.written[0].in_reply_to == target.id
    assert rep.written[0].to_dict()["uid"].startswith("p0.n")
    # nothing but the output is left in the folder: the temp file was renamed
    assert sorted(os.listdir(tmp_path)) == ["a.pdf", "out.pdf"]
    with Document(filepath=str(out)) as d:
        reply = next(m for m in d.markups() if m.text == "agreed")
    assert reply.uid == rep.written[0].uid


def test_failed_save_leaves_the_last_good_copy(tmp_path, monkeypatch):
    import fitz
    from planlens.document.markup_writer import write_markups
    src = tmp_path / "a.pdf"
    src.write_bytes(_with_markups(1))
    out = tmp_path / "out.pdf"
    write_markups(str(src), str(out), [
        {"kind": "note", "page": 0, "quote": "bearing pressure",
         "comment": "first"}])
    good = out.read_bytes()

    def boom(self, *a, **k):
        raise OSError("disk full")
    monkeypatch.setattr(fitz.Document, "save", boom)
    with pytest.raises(OSError):
        write_markups(str(src), str(out), [
            {"kind": "note", "page": 0, "quote": "bearing pressure",
             "comment": "second"}])
    assert out.read_bytes() == good
    assert sorted(os.listdir(tmp_path)) == ["a.pdf", "out.pdf"]


def test_forget_closes_and_reopens(tmp_path):
    from planlens.tools import ReviewToolkit
    src = tmp_path / "a.pdf"
    src.write_bytes(_with_markups(1))
    tk = ReviewToolkit()
    try:
        h = tk.call("open_document", {"source": str(src)})["handle"]
        assert tk.forget(str(src)) == [h]
        assert "error" in tk.call("document_markups", {"handle": h})
        assert tk.forget(str(src)) == []
        h2 = tk.call("open_document", {"source": str(src)})["handle"]
        assert "error" not in tk.call("document_markups", {"handle": h2})
    finally:
        tk.close()
