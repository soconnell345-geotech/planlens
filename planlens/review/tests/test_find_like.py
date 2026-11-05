"""find_like: every copy of one mark, each candidate read by the host's look.

Moved from the app's test_find_like_tool.py, test_find_like_brief4.py and
test_find_like_budget.py (PL-29 (a)). The fixture is a drawing set in
miniature (planlens.testing.tag_fixtures): lettering drawn as lines, a
legend on every sheet, GCE callouts with leaders among GCG / GPE / QCE
look-alikes. A fake look answers the contact sheets from the ground truth
(it cannot see; it must only be ASKED the right thing), so the tests pin the
tool's logic: every callout found, look-alikes rejected by the reading,
uncertain reads reported, the legend counted apart, the sheets saved, a
flooding example not read, and the whole search kept to its time budget.

The app's tests of the tiled page read, of the image budgets and of its
result caps stay in the app (they test its vision tools, not these).
"""

from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

pymupdf = pytest.importorskip("pymupdf")
tf = pytest.importorskip("planlens.testing.tag_fixtures")
pytest.importorskip("planlens.document.findlike")

from planlens.review import find_like as fl  # noqa: E402
from planlens.review import tools as review_tools  # noqa: E402
from planlens.review.tests.conftest import configure_host, look_of  # noqa: E402

#: The example a recorded run passed: a 59 pt zoom's view and the tag's box.
T1_VIEW = [419.5, 98.8, 548.2, 169.5]
T1_IMAGE_BOX = [549, 525, 628, 591]


@pytest.fixture(scope="module")
def gt():
    return tf.build_synthetic_tag_set(n_pages=2)


def _dispatch(arguments, engine, attachments, folder, **host):
    """The host layer's find_like, as the app's dispatcher was called."""
    configure_host(folder, engine, **host)
    return json.loads(review_tools.find_like(arguments, attachments))


def _candidates(gt, page, bbox, pages=None):
    """The candidates in the order the tool numbers them."""
    from planlens.document import Document
    d = Document(content=gt.pdf)
    try:
        res = d.find_like(page, bbox, pages)
        return [h for h in res["hits"] if h.context != "legend"]
    finally:
        d.close()


def _truth_of(gt, hit):
    cx, cy = hit.center
    for t in gt.tags:
        if t.page == hit.page:
            tx, ty = (t.bbox[0] + t.bbox[2]) / 2, (t.bbox[1] + t.bbox[3]) / 2
            if abs(cx - tx) < 6 and abs(cy - ty) < 6:
                return t
    return None


class TruthReader:
    """Answers every contact sheet from the ground truth — for ALL numbers;
    the tool keeps only the ones on the sheet it asked about."""

    def __init__(self, gt, cands, override=None):
        self.lines = []
        for i, h in enumerate(cands, start=1):
            t = _truth_of(gt, h)
            text = t.text if t else "-"
            kind = ("callout" if t and t.kind == "callout" else "other")
            if override and i in override:
                text = override[i]
            self.lines.append(f"#{i} | {text} | {kind}")
        self.calls = []
        self.kinds = []

    def __call__(self, image_bytes, prompt, kind):
        self.calls.append(prompt)
        self.kinds.append(kind)
        return "\n".join(self.lines)


def test_every_gce_callout_is_found_and_look_alikes_are_rejected(gt, tmp_path):
    cands = _candidates(gt, 0, gt.example_bbox)
    look = TruthReader(gt, cands)
    out = fl.find_like(gt.pdf, 0, gt.example_bbox, look, text="GCE",
                       save_dir=str(tmp_path / "sheets"))
    on_plan = [t for t in gt.of("GCE") if t.kind != "legend"]
    callouts = gt.of("GCE", "callout")
    assert out["instances"] == len(on_plan)
    assert out["callouts"] == len(callouts)
    assert out["instances_by_page"] == {
        str(p): sum(1 for t in on_plan if t.page == p) for p in (0, 1)}
    assert out["legend_entries"] >= 2 and out["legend_pages"] == [0, 1]
    assert {"GCG", "GPE", "QCE"} <= set(out["rejected_as"])
    assert out["uncertain"] == []
    got = [f for f in out["found"] if f["context"] == "callout"]
    assert got and all("points_to" in f for f in got)
    assert out["contact_sheets"] and all(os.path.isfile(p)
                                         for p in out["contact_sheets"])
    assert "GCE" in look.calls[0] and "[G/Q]CE" in look.calls[0]
    assert set(look.kinds) == {"find"}


def test_a_bracketed_read_is_uncertain_not_counted(gt):
    cands = _candidates(gt, 0, gt.example_bbox)
    first_gce = next(i for i, h in enumerate(cands, start=1)
                     if (_truth_of(gt, h) or tf.TagTruth(
                         0, "", (0,) * 4, 0, "")).text == "GCE")
    look = TruthReader(gt, cands, override={first_gce: "[G/Q]CE"})
    out = fl.find_like(gt.pdf, 0, gt.example_bbox, look, text="GCE")
    on_plan = [t for t in gt.of("GCE") if t.kind != "legend"]
    assert out["instances"] == len(on_plan) - 1
    assert [u["id"] for u in out["uncertain"]] == [first_gce]


def test_without_a_look_the_candidates_are_marked_unverified(gt):
    out = fl.find_like(gt.pdf, 0, gt.example_bbox, None, text="GCE", pages="0")
    assert "UNVERIFIED" in out["note"]
    assert out["instances"] == 0 and out["unverified"]


def test_the_dispatcher_saves_to_the_conversation_scratch(gt, tmp_path):
    """The contact sheets are working images -- in the conversation's
    scratch folder (no download cards), named by a short name the host's
    image tool resolves, never by their server path."""
    cands = _candidates(gt, 0, gt.example_bbox)
    work = tmp_path / "work"
    work.mkdir()
    out = _dispatch({"attachment_key": "set", "page": 0,
                     "bbox": list(gt.example_bbox), "text": "GCE"},
                    TruthReader(gt, cands), {"set": gt.pdf}, work)
    assert "error" not in out, out
    first = out["contact_sheets"][0]
    assert first.startswith(".scratch/") and str(tmp_path) not in first
    assert (work / first).is_file()
    assert (work / first).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(json.dumps(out)) <= fl.RESULT_BUDGET_CHARS


def test_the_dispatcher_explains_a_missing_example(gt, tmp_path):
    out = _dispatch({"attachment_key": "set", "page": 0},
                    TruthReader(gt, []), {"set": gt.pdf}, tmp_path)
    assert "ONE copy" in out["error"] and "render_region" in out["hint"]
    out = _dispatch({"attachment_key": "set", "page": 0,
                     "bbox": [5, 5, 20, 12]},
                    TruthReader(gt, []), {"set": gt.pdf}, tmp_path)
    assert "no ink" in out["error"]


def test_the_dispatcher_takes_pdf_pages_and_a_view(gt, tmp_path):
    out = _dispatch({"attachment_key": "set", "pdf_page": 9,
                     "bbox": list(gt.example_bbox)},
                    TruthReader(gt, []), {"set": gt.pdf}, tmp_path)
    assert "error" in out
    out = _dispatch({"attachment_key": "set", "page": 0,
                     "bbox": list(gt.example_bbox), "view": [0, 0, 9, 9],
                     "image_box": [0, 0, 9, 9]},
                    TruthReader(gt, []), {"set": gt.pdf}, tmp_path)
    assert "not both" in out["error"]


def test_parse_readings_and_verdicts():
    got = fl.parse_readings("noise\n#1 | GCE | callout\n#2|GCG|other\n"
                            "3: [G/Q]CE | callout\n`#4 | - | other`")
    assert got[1] == {"read": "GCE", "kind": "callout"}
    assert got[2]["read"] == "GCG" and got[3]["read"] == "[G/Q]CE"
    assert got[4]["read"] == "-"
    assert fl._verdict("GCE", "GCE") == "confirmed"
    assert fl._verdict("G C E", "gce") == "confirmed"
    assert fl._verdict("GCG", "GCE") == "rejected"
    assert fl._verdict("[G/Q]CE", "GCE") == "uncertain"
    assert fl._verdict("[C/O]CG", "GCE") == "rejected"
    assert fl._verdict("-", "GCE") == "no_lettering"


def test_find_like_is_offered_only_with_a_look(tmp_path):
    from planlens import review
    configure_host(tmp_path, None)
    assert "find_like" not in review.tool_names()
    out = json.loads(review.call("find_like", {"attachment_key": "x"}))
    assert "unknown tool" in out["error"]
    configure_host(tmp_path, look_of(lambda i, q, k: ""))
    assert "find_like" in review.tool_names()


# -- an example crossed by linework (brief 4) ---------------------------------

class Counts:
    """Reads every cell as the target (what it reads does not matter here;
    how many sheets it is asked to read does)."""

    def __init__(self):
        self.calls = 0

    def __call__(self, image_bytes, prompt, kind):
        self.calls += 1
        return "\n".join(f"#{i} | GCE | callout" for i in range(1, 400))


def test_the_grid_line_example_is_read_in_a_few_sheets_not_twenty(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PLANLENS_FINDLIKE_BACKEND", "numpy")
    gt = tf.build_synthetic_tag_set()
    look = Counts()
    out = _dispatch({"attachment_key": "t", "page": 0, "view": T1_VIEW,
                     "image_box": T1_IMAGE_BOX, "text": "GCE", "pages": "0"},
                    look, {"t": gt.pdf}, tmp_path)
    assert "error" not in out and "status" not in out, out
    assert "linework_left_out" in out["example"]
    assert 1 <= look.calls <= 4, look.calls
    callouts = [t for t in gt.tags if t.page == 0 and t.text == "GCE"
                and t.kind == "callout"]
    for t in callouts:
        cx, cy = (t.bbox[0] + t.bbox[2]) / 2, (t.bbox[1] + t.bbox[3]) / 2
        assert any(f["bbox"][0] - 2 <= cx <= f["bbox"][2] + 2
                   and f["bbox"][1] - 2 <= cy <= f["bbox"][3] + 2
                   for f in out["found"]), t


def test_a_flooding_example_is_not_read(monkeypatch, tmp_path):
    from planlens.document import Document

    def flooded(self, page, bbox, pages=None, **kw):
        hits = [SimpleNamespace(page=0, bbox=(i, 10.0, i + 5.0, 14.0),
                                context="unanchored", score=0.6,
                                points_to=None)
                for i in range(250)]
        hits.append(SimpleNamespace(page=1, bbox=(5.0, 5.0, 9.0, 9.0),
                                    context="callout", score=0.9,
                                    points_to=(20.0, 20.0)))
        return {"hits": hits, "pages": [0, 1], "warnings": [],
                "example": {"page": 0, "bbox": list(bbox), "height_pt": 4.0}}

    monkeypatch.setattr(Document, "find_like", flooded)
    monkeypatch.setattr(Document, "like_sheets",
                        lambda self, *a, **k: pytest.fail("sheets drawn"))
    gt = tf.build_synthetic_tag_set(n_pages=2)
    look = Counts()
    out = _dispatch({"attachment_key": "t", "page": 0,
                     "bbox": [490, 136, 501, 141], "text": "GCE"},
                    look, {"t": gt.pdf}, tmp_path)
    assert look.calls == 0
    assert out["status"] == "example_matches_linework"
    assert out["candidates"] == 251
    assert out["candidates_by_page"] == {"0": 250, "1": 1}
    assert "instances" not in out
    assert "NOT a count of instances" in out["note"]
    assert "not crossed by linework" in out["note"]
    assert "page(s) 0" in out["note"]


def test_flooded_pages():
    hits = ([SimpleNamespace(page=3)] * (fl.FLOOD_PER_PAGE + 1)
            + [SimpleNamespace(page=4)] * fl.FLOOD_PER_PAGE)
    assert fl.flooded_pages(hits) == {3: fl.FLOOD_PER_PAGE + 1}
    assert fl.flooded_pages(hits[-10:]) == {}


# -- one time budget for the whole search (brief 5) ---------------------------

N_PAGES, PER_PAGE = 24, 41
SECONDS_PER_READ = 795 / 49


class _Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class _Reader:
    """Reads every cell; the first of each page's cells as the target."""

    def __init__(self, clock):
        self.clock = clock
        self.calls = 0

    def __call__(self, png, prompt, kind):
        self.calls += 1
        self.clock.t += SECONDS_PER_READ
        ids = [int(x) for x in png.decode().split(",")]
        return "\n".join(f"#{i} | {'GCE' if i % PER_PAGE == 1 else 'GCG'} "
                         f"| callout" for i in ids)


@pytest.fixture
def loose_example(monkeypatch):
    from planlens.document import Document

    hits = [SimpleNamespace(page=p, bbox=(10.0 + k, 10.0, 15.0 + k, 14.0),
                            context="callout", score=0.6, points_to=None)
            for p in range(N_PAGES) for k in range(PER_PAGE)]

    def search(self, page, bbox, pages=None, **kw):
        return {"hits": hits, "pages": list(range(N_PAGES)), "warnings": [],
                "example": {"page": 0, "bbox": list(bbox), "height_pt": 18.7}}

    def sheets(self, cands, per_sheet=20):
        out = []
        for s in range(0, len(cands), per_sheet):
            ids = list(range(s + 1, min(s + per_sheet, len(cands)) + 1))
            out.append((",".join(map(str, ids)).encode(), ids))
        return out

    monkeypatch.setattr(Document, "find_like", search)
    monkeypatch.setattr(Document, "like_sheets", sheets)
    monkeypatch.setattr(fl, "VERIFY_WORKERS", 1)
    clock = _Clock()
    monkeypatch.setattr(fl, "_clock", clock)
    return clock


def _run(clock, **kw):
    look = _Reader(clock)
    pdf = tf.build_synthetic_tag_set(n_pages=2).pdf
    out = fl.find_like(pdf, 0, [592.8, 364.3, 639.7, 382.7], look,
                       text="GCE", **kw)
    return out, look


def test_the_default_budget():
    assert fl.budget_s() == fl.DEFAULT_BUDGET_S < 795
    assert fl.budget_s(0) is None and fl.budget_s("0") is None
    assert fl.budget_s(None) is None
    assert fl.budget_s("not a number") == fl.DEFAULT_BUDGET_S


def test_a_loose_example_stops_at_the_budget_and_says_so(loose_example):
    out, look = _run(loose_example)
    total = N_PAGES * PER_PAGE
    n_sheets = -(-total // fl.PER_SHEET)
    assert look.calls == 19 < n_sheets
    assert out["status"] == "cut_short"
    assert out["candidates"] == total
    assert out["candidates_read"] == 19 * fl.PER_SHEET
    assert out["candidates_read"] + out["not_read"] == total
    assert "CUT SHORT" in out["note"] and "NOT read" in out["note"]
    assert f'pages="{out["not_read_pages"]}"' in out["note"]
    first_unread_page = (19 * fl.PER_SHEET) // PER_PAGE
    assert out["not_read_pages"].startswith(str(first_unread_page))
    assert out["not_read_pages"].endswith(str(N_PAGES - 1))
    assert out["instances"] == len({(i - 1) // PER_PAGE
                                    for i in range(1, 19 * 20 + 1)
                                    if i % PER_PAGE == 1})
    assert len(out["uncertain"]) <= 20


def test_with_no_budget_every_sheet_is_read(loose_example):
    out, look = _run(loose_example, budget=0)
    assert look.calls == -(-N_PAGES * PER_PAGE // fl.PER_SHEET)
    assert "status" not in out and "not_read" not in out


def test_a_search_inside_the_budget_is_unchanged(loose_example):
    out, look = _run(loose_example, budget=100000)
    assert "status" not in out and "note" not in out
    assert out["instances"] == N_PAGES


def test_the_host_budget_reaches_the_search(loose_example, tmp_path):
    look = _Reader(loose_example)
    pdf = tf.build_synthetic_tag_set(n_pages=2).pdf
    out = _dispatch({"attachment_key": "t", "page": 0,
                     "bbox": [592.8, 364.3, 639.7, 382.7], "text": "GCE"},
                    look, {"t": pdf}, tmp_path, find_like_budget_s=None)
    assert "status" not in out
    assert look.calls == -(-N_PAGES * PER_PAGE // fl.PER_SHEET)


def test_page_ranges():
    assert fl._page_ranges([5, 3, 4, 9, 11, 10]) == "3-5,9-11"
    assert fl._page_ranges([7]) == "7"
