"""Page-map metrics and document structure on the synthetic submittal.

Truth is the fixture's own statement of how it was built: which pages are
report pages with a running header and "Page N" footer, which are ruled forms,
which are dividers, which sheet is which, and which page repeats another.
"""

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document import open_document  # noqa: E402
from planlens.document.structure import (  # noqa: E402
    _norm_key, _same_running, divider_title, printed_numbers,
)
from planlens.testing.submittal_fixtures import (  # noqa: E402
    build_synthetic_submittal,
)


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_submittal()


@pytest.fixture(scope="module")
def doc(gt):
    d = open_document(gt.pdf)
    d.page_map()
    yield d
    d.close()


def test_metrics_separate_prose_forms_and_dividers(doc, gt):
    report = doc.summary(gt.report_pages[0])
    assert report.kind == "text"
    assert report.n_words > 150 and report.text_density > 1.5
    assert report.ruling_h == 0 and report.ruling_v == 0
    form = doc.summary(gt.form_pages[0])
    assert form.kind == "form"
    assert form.ruling_h >= 12 and form.ruling_v >= 12
    assert form.text_density < 1.0
    cover = doc.summary(gt.cover_page)
    assert cover.n_words < 20


def test_headers_footers_and_printed_numbers(doc, gt):
    for n, index in enumerate(gt.report_pages, start=1):
        s = doc.summary(index)
        assert s.header == gt.report_header
        assert s.footer.startswith("Project 1234 | Page")
        assert s.printed_page == n and s.printed_of is None
    for n, index in enumerate(gt.form_pages, start=1):
        s = doc.summary(index)
        assert (s.printed_page, s.printed_of) == (n, 2)
    for n, index in enumerate(gt.sheet_pages, start=1):
        s = doc.summary(index)
        assert s.sheet == f"{n} of 2"
        assert s.scales == [f"SCALE: {gt.sheet_scale}"]


def test_dividers_and_duplicates(doc, gt):
    assert doc.summary(gt.cover_page).divider_title == "Project Alpha"
    assert doc.summary(gt.appendix_divider_page).divider_title == gt.appendix_title
    assert doc.summary(gt.attachment_divider_page).divider_title == gt.attachment_title
    assert doc.summary(gt.report_pages[0]).divider_title is None
    assert doc.summary(gt.duplicate_page).duplicate_of == gt.duplicate_of
    assert all(doc.summary(p).duplicate_of is None
               for p in gt.report_pages + gt.form_pages)


def test_segments_follow_the_documents_own_structure(doc, gt):
    segs = doc.segments()
    got = [(int(g["pages"].split("-")[0]), int(g["pages"].split("-")[-1]),
            g["title"]) for g in segs]
    assert len(got) == len(gt.expected_segments)
    for (a, b, frag), (ga, gb, title) in zip(gt.expected_segments, got):
        assert (ga, gb) == (a, b), (title, frag)
        assert frag in title or frag in (
            next(g.get("first_heading", "") for g in segs
                 if g["pages"].startswith(str(a)))), (title, frag)
    report = segs[1]
    assert report["printed_pages"] == "1-3"
    assert report["header"] == gt.report_header
    forms = segs[3]
    assert forms["kinds"] == {"form": 2}
    assert forms["printed_of"] == 2
    assert forms["first_heading"].startswith("Log of Boring")
    sheets = segs[5]
    assert sheets["sheets"] == ["1 of 2", "2 of 2"]
    assert sheets["title"].startswith("Drawing sheets")
    # every page knows its segment
    assert [doc.summary(i).segment for i in range(doc.n_pages)] == [
        0, 1, 1, 1, 2, 3, 3, 4, 5, 5, 6]


def test_page_map_row_is_compact_and_detail_is_optional(doc, gt):
    s = doc.summary(gt.form_pages[0])
    row = s.to_dict(detail=False)
    assert "evidence" not in row
    assert row["printed_page"] == 1 and row["segment"] == 3
    full = s.to_dict()
    assert full["evidence"]["ruling_lines"] == [s.ruling_h, s.ruling_v]
    assert full["evidence"]["footer"].startswith("Page 1 of 2")


def test_running_keys_ignore_stray_numbers_and_match_by_containment():
    stamp = _norm_key("Page 15 of 245 | Checked: CHW/MS 2026-08-20")
    assert stamp == _norm_key("5 | 5 | Page 14 of 245 | Checked: CHW/MS 2026-08-20")
    longer = _norm_key("TEST | SB-04 | Project: CAA | Page 17 of 245 | "
                       "Checked: CHW/MS 2026-08-20")
    assert _same_running(stamp, longer)
    assert not _same_running(stamp, _norm_key("Design By: JAS | Page 1 of 4"))
    assert not _same_running("", stamp)


def test_printed_numbers_and_divider_titles():
    assert printed_numbers(["Page 7"]) == {"printed_page": 7}
    assert printed_numbers(["Page 15 of 245", "Checked"]) == {
        "printed_page": 15, "printed_of": 245}
    assert printed_numbers(["SHEET", "2", "OF 7"])["sheet"] == "2 of 7"
    assert printed_numbers(["DWG NO. S-101"])["sheet"] == "S-101"
    assert printed_numbers(["nothing here"]) == {}
    assert divider_title("APPENDIX C", 12, ["APPENDIX C"]) == "APPENDIX C"
    assert divider_title("Project Alpha", 6, ["Project Alpha", "Cover"]) == "Project Alpha"
    assert divider_title("Section 3.2 Loads", 400, []) is None
    assert divider_title("Results", 10, ["Results"]) is None


def test_thumbnail_sheets_cover_every_page_in_order(doc):
    sheets = doc.render_thumbnails(columns=4, thumb_px=100, per_sheet=8)
    assert [i["pages"] for _, i in sheets] == [list(range(8)), [8, 9, 10]]
    png, info = sheets[0]
    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert info["columns"] == 4 and info["width_px"] == 4 * 110 + 10
    pix = fitz.Pixmap(png)
    assert (pix.width, pix.height) == (info["width_px"], info["height_px"])
    assert "red frame" in info["legend"]
