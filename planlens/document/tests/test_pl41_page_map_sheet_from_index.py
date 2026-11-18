"""PL-41: once a document's sheet index has been made, the page map's sheet
number follows it. A page read on its own takes a labelled number anywhere
in its title-block area, so a reference-drawings table listing "DWG NO."
over another sheet's number beat the sheet's own unlabelled number, which
the index finds by its place across the set. The page map never makes the
index: without the sheet tools every result is what it was. Every sheet
here is made up inside the test."""
import functools
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.sheets import HOW_PAGE_LABEL, HOW_SLOT  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

W, H = 36 * 72.0, 24 * 72.0          # a 36 x 24 in sheet

#: Each sheet's texts as ``(x_fraction, y_fraction, text, fontsize)``, y
#: the baseline. Pages 0-3 print their number alone in the title block's
#: corner, at the same place on each; page 0 also carries a table of
#: reference drawings in its title-block area, a labelled number of
#: another sheet. Page 4 prints its number only in the header band, which
#: the index does not read; page 5 prints none, and its PDF page label
#: names it.
_SET = "PROJECT ALPHA", "SCALE 1:50", "CHECKED: XX"
_REFERENCES = [(0.72, 0.30, "REFERENCE DRAWINGS", 8),
               (0.72, 0.32, "DWG NO.", 8),
               (0.72, 0.32 + 14 / H, "C-101", 8)]
_CORNER = [(0.90, 0.96, number, 24)
           for number in ("S-201A", "S-202", "S-203", "S-204")]
_HEADER = [(0.10, 0.04, "SHEET NO. S-205", 10)]


def _page_texts(i):
    texts = [(0.80, 0.88 + 0.02 * k, text, 8) for k, text in enumerate(_SET)]
    if i < 4:
        texts.append(_CORNER[i])
    if i == 0:
        texts += _REFERENCES
    if i == 4:
        texts += _HEADER
    return texts


@functools.lru_cache(maxsize=None)
def _set_pdf():
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page(width=W, height=H)
        shape = page.new_shape()
        shape.draw_rect(pymupdf.Rect(20, 20, W - 20, H - 20))
        for k in range(12):
            shape.draw_line((60 + 40 * k + 7 * i, 80),
                            (60 + 40 * k + 7 * i, H * 0.5))
        shape.finish(color=(0, 0, 0), width=0.5)
        shape.commit()
        for fx, fy, text, size in _page_texts(i):
            page.insert_text((fx * W, fy * H), text, fontsize=size)
    # lower-case roman labels are no sheet numbers; page 5's label is one
    doc.set_page_labels([
        {"startpage": 0, "prefix": "", "style": "r", "firstpagenum": 1},
        {"startpage": 5, "prefix": "A-901 DETAILS", "style": "",
         "firstpagenum": 1}])
    pdf = doc.tobytes()
    doc.close()
    return pdf


_ALONE = ["C-101", "S-202", "S-203", "S-204", "S-205", None]
_WITH_INDEX = ["S-201A", "S-202", "S-203", "S-204", "S-205", None]


def test_the_set_reads_as_the_case_needs():
    # the page read on its own takes the labelled table entry; the index
    # takes the slot the sheets share, leaves page 4 unnumbered and names
    # page 5 from its PDF page label
    with Document(content=_set_pdf(), name="set.pdf") as d:
        assert [d.summary(i).sheet for i in range(6)] == _ALONE
        rows = d.sheet_index().rows
    assert [r.number for r in rows] == [
        "S-201A", "S-202", "S-203", "S-204", None, "A-901"]
    assert [r.how for r in rows[:4]] == [HOW_SLOT] * 4
    assert rows[5].how.startswith(HOW_PAGE_LABEL)


@pytest.mark.parametrize("summaries_first", [True, False])
def test_once_the_index_is_made_the_page_map_takes_its_numbers(
        summaries_first):
    with Document(content=_set_pdf(), name="set.pdf") as d:
        if summaries_first:
            d.page_map()
            d.segments()
        d.sheet_index()
        rows = d.page_map()
        segs = d.segments()
    assert [s.sheet for s in rows] == _WITH_INDEX
    # where the two readings differ, the page's own is kept as evidence
    assert rows[0].evidence["sheet_read_alone"] == "C-101"
    assert rows[0].to_dict()["evidence"]["sheet_read_alone"] == "C-101"
    # where they agree, or the index gave no number, or took it from the
    # page label, nothing is added
    assert all("sheet_read_alone" not in s.evidence for s in rows[1:])
    # the drawing segments read the page map's numbers
    assert [n for g in segs for n in g.get("sheets", [])] == [
        n for n in _WITH_INDEX if n]


def _calls(kit, handle):
    """The default path's results: the map, the structure, the text."""
    out = {}
    for name, args in (
            ("document_page_map", {}),
            ("document_page_map", {"with_evidence": True}),
            ("document_structure", {}),
            ("read_document", {"pages": "0"})):
        out[f"{name} {sorted(args)}"] = json.loads(
            kit.call_json(name, {"handle": handle, **args}))
    return out


def test_without_the_sheet_tools_the_index_is_never_made(
        tmp_path, monkeypatch):
    made = []
    real = Document.sheet_index

    def counted(self):
        made.append(self)
        return real(self)
    monkeypatch.setattr(Document, "sheet_index", counted)
    pdf = _set_pdf()
    kit = ReviewToolkit(resolve_source=lambda key: pdf,
                        output_dir=str(tmp_path))
    try:
        opened = json.loads(kit.call_json(
            "open_document", {"source": "set.pdf"}))
        out = _calls(kit, opened["handle"])
    finally:
        kit.close()
    # no index, so nothing of PL-41 ran: each result is the page read on
    # its own, as before
    assert made == []
    assert "sheet_index" not in opened
    rows = out["document_page_map []"]["rows"]
    assert [r.get("sheet") for r in rows] == _ALONE
    assert "sheet_read_alone" not in json.dumps(out)
    sheets = [n for g in out["document_structure []"]["segments"]
              for n in g.get("sheets", [])]
    assert sheets == [n for n in _ALONE if n]


def test_with_the_sheet_tools_the_page_map_says_the_index_number(tmp_path):
    pdf = _set_pdf()
    kit = ReviewToolkit(resolve_source=lambda key: pdf,
                        output_dir=str(tmp_path), sheet_tools=True)
    try:
        opened = json.loads(kit.call_json(
            "open_document", {"source": "set.pdf"}))
        out = _calls(kit, opened["handle"])
    finally:
        kit.close()
    # open_document made the index for the sheet tools; the page map
    # follows it
    assert opened["sheet_index"]
    rows = out["document_page_map []"]["rows"]
    assert [r.get("sheet") for r in rows] == _WITH_INDEX
    assert "sheet_read_alone" not in rows[0]
    detail = out["document_page_map ['with_evidence']"]["rows"]
    assert detail[0]["evidence"]["sheet_read_alone"] == "C-101"
    assert ["sheet_read_alone" in r["evidence"] for r in detail] == [
        True] + [False] * 5
    sheets = [n for g in out["document_structure []"]["segments"]
              for n in g.get("sheets", [])]
    assert sheets == [n for n in _WITH_INDEX if n]
