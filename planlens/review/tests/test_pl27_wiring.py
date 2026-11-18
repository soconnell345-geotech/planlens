"""BACKLOG PL-27 in the host layer: the plan tools for a host that asks, a
mark's stable uid in ``remove``, and ``forget_path`` through the toolkit's
own ``forget``.

The plan tools (``plan_symbols``, ``plan_grid``) are offered only to a host
that asks (``ReviewHost(toolkit_options={"plan_tools": True})``); every
other host is offered, specified and answered what it was.
"""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import conversation, specs  # noqa: E402
from planlens.review.host import ReviewHost, using  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402
from planlens.testing.plan_location_fixtures import (  # noqa: E402
    build_plan_location_sheet)

PLAN = ("plan_symbols", "plan_grid")
BEFORE = specs.DOCUMENT_TOOLS + specs.OPTIONAL_DOCUMENT_TOOLS
ASK = {"plan_tools": True}
SIGNER = "Test Reviewer via ReviewApp (AI draft)"


@pytest.fixture(scope="module")
def plan():
    return build_plan_location_sheet()


@pytest.fixture(scope="module")
def doc():
    return build_synthetic_review_document()


@pytest.fixture
def folder(tmp_path):
    work = tmp_path / "conv" / "files"
    work.mkdir(parents=True)
    return work


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


# -- the plan tools: off unless asked ------------------------------------------

def test_a_host_that_did_not_ask_is_offered_and_specified_what_it_was(plan):
    assert specs.PLAN_TOOLS == PLAN
    assert not set(PLAN) & set(specs.HOST_TOOLS)
    assert specs.HOST_TOOLS == BEFORE + specs.LOOK_TOOLS
    assert specs.host_tool_names() == BEFORE + specs.LOOK_TOOLS
    assert specs.host_tool_names(look=False, sheet_tools=True) == \
        BEFORE + specs.SHEET_TOOLS
    assert [s["name"] for s in specs.host_specs()] == \
        list(BEFORE + specs.LOOK_TOOLS)
    tools = make_tools(None, None, {"plan.pdf": plan.pdf})
    assert tuple(tools) == BEFORE == tuple(review.tool_names())
    for name in PLAN:
        refused = json.loads(review.call(name, {"handle": "plan.pdf",
                                                "page": 0},
                                         {"plan.pdf": plan.pdf}))
        assert refused["error"] == f"unknown tool '{name}'"
        assert name not in refused["hint"]


def test_a_host_that_asks_is_offered_them_after_the_others(plan):
    plain = {s["name"]: s for s in specs.host_specs(look=False)}
    make_tools(None, None, {"plan.pdf": plan.pdf}, toolkit_options=ASK)
    assert tuple(review.tool_names()) == BEFORE + PLAN
    assert specs.host_tool_names(look=True, sheet_tools=True,
                                 plan_tools=True) == \
        BEFORE + specs.LOOK_TOOLS + specs.SHEET_TOOLS + PLAN
    shown = {s["name"]: s for s in review.tool_specs()}
    assert list(shown) == list(BEFORE + PLAN)
    # the tools every host has are specified word for word as before
    assert {n: s for n, s in shown.items() if n not in PLAN} == plain
    assert review.tool_specs("openai")[-1]["function"]["name"] == "plan_grid"


def test_their_specs_are_the_toolkits_words_with_pdf_page():
    from planlens.tools.specs import TOOL_SPECS
    kit = {s["name"]: s for s in TOOL_SPECS}
    shown = {s["name"]: s for s in specs.host_specs(look=False,
                                                    plan_tools=True)}
    for name in PLAN:
        desc = shown[name]["description"]
        assert desc == kit[name]["description"] + specs.PDF_PAGE_NOTE
        schema = shown[name]["input_schema"]
        assert schema["title"] == name and schema["type"] == "object"
        props = schema["properties"]
        # every argument the toolkit takes, and pdf_page beside page
        assert set(props) == set(kit[name]["parameters"]["properties"]) | {
            "pdf_page"}
        assert props["pdf_page"]["anyOf"] == [{"type": "integer"},
                                              {"type": "null"}]
        for arg in specs.PLAN_SHOWN_FROM_TOOLKIT[name]:
            want = dict(kit[name]["parameters"]["properties"][arg],
                        default=None, title=specs._title(arg))
            assert props[arg] == want, (name, arg)
    assert shown["plan_symbols"]["input_schema"]["required"] == ["handle"]
    # every argument optional: no "required", as the agent framework writes
    # the schema of a function with none
    assert "required" not in shown["plan_grid"]["input_schema"]
    assert list(shown["plan_symbols"]["input_schema"]["properties"]) == [
        "handle", "page", "label_pattern", "shapes", "min_size", "max_size",
        "max_label_gap", "bbox", "pdf_page"]


def test_through_the_host_they_read_the_plan_with_both_page_numbers(plan):
    att = {"plan.pdf": plan.pdf}
    tools = make_tools(None, None, att, toolkit_options=ASK)
    # a file name where the handle goes, and the viewer's page number
    found = _invoke(tools["plan_symbols"], handle="plan.pdf", pdf_page=1)
    assert found["page"] == 0 and found["pdf_page"] == 1
    labels = {s.get("label") for s in found["symbols"]}
    assert {t.label for t in plan.symbols} <= labels
    assert "opened" in found
    handle = found["opened"].split("handle ", 1)[1].split(";", 1)[0]
    # an argument not given is not handed on: the toolkit's defaults apply
    only = _invoke(tools["plan_symbols"], handle=handle, page=0,
                   shapes=["square"])
    assert {s["shape"] for s in only["symbols"]} == {"square"}
    pts = [{"name": s["label"], "page": s["center"]}
           for s in found["symbols"] if s.get("label")]
    grid = _invoke(tools["plan_grid"], controls=plan.controls, points=pts,
                   handle=handle, pdf_page=1, units="m")
    assert grid["handedness"] == "right" and grid["check"]["right_handed"]
    assert "point_flags" not in grid
    # a point's page position is a position, not a page number
    assert all("pdf_page" not in p for p in grid["points"])
    bad = _invoke(tools["plan_symbols"], handle=handle, pdf_page=9)
    assert "error" in bad


def test_the_calling_host_decides_whether_they_are_served(plan):
    """Hosts with no working folder share one conversation and its
    toolkit: whichever host built it, a host that asked is served the plan
    tools and a host that did not is refused them by name."""
    att = {"plan.pdf": plan.pdf}
    plain, asked = ReviewHost(), ReviewHost(toolkit_options=ASK)
    for first in (plain, asked):
        conversation._SPACES.clear()
        handle = json.loads(review.call("open_document",
                                        {"source": "plan.pdf"}, att,
                                        host=first))["handle"]
        served = json.loads(review.call("plan_symbols",
                                        {"handle": handle, "page": 0}, att,
                                        host=asked))
        assert served["symbols"], served
        with using(plain):
            raw = conversation.dispatch_document_tool(
                "plan_symbols", {"handle": handle, "page": 0}, att)
        refused = json.loads(raw)
        assert refused["error"] == "unknown tool 'plan_symbols'"
        assert "plan_symbols" not in refused["hint"]
        with using(asked):
            out = json.loads(conversation.dispatch_document_tool(
                "no_such_tool", {"handle": handle}, att))
        assert "plan_grid" in out["hint"]
        assert len(conversation._SPACES) == 1


def test_the_option_is_this_layers_and_the_toolkit_is_unchanged(plan):
    """``plan_tools`` is not a toolkit option: it is taken out before the
    conversation's toolkit is built, which serves the plan tools to
    everyone, as the toolkit always has."""
    from planlens.tools import ReviewToolkit
    assert set(PLAN) <= set(ReviewToolkit().tool_names)
    conversation._SPACES.clear()
    with using(ReviewHost(toolkit_options=dict(ASK, max_open=3))):
        kit = conversation._toolkit()
    assert kit.max_open == 3 and set(PLAN) <= set(kit.tool_names)


# -- remove takes a mark's stable uid ------------------------------------------

def _marks(doc):
    page = doc.narrative_page
    return [{"kind": "box", "page": page, "bbox": [72, y, 300, y + 30],
             "comment": f"[#{i}] remark {i}"}
            for i, y in enumerate((300, 360, 420), start=1)]


def test_a_mark_is_removed_by_its_uid_after_the_ids_have_moved(doc, folder):
    tools = make_tools(None, folder, {"set.pdf": doc.pdf}, signer=SIGNER)
    src = _invoke(tools["open_document"], source="set.pdf")["handle"]
    wrote = _invoke(tools["annotate_document"], handle=src, check=False,
                    output_path="set_marked.pdf", markups=_marks(doc))
    uids = [row["uid"] for row in wrote["written"]]
    assert len(uids) == 3 and all(u.startswith("p") for u in uids)
    gone = _invoke(tools["annotate_document"], handle=src, check=False,
                   output_path="set_marked.pdf", remove=[uids[0]])
    assert [r["says"] for r in gone["removed"]] == ["[#1] remark 1"]
    # the positional ids moved; the uids did not -- as the entry or as id
    gone = _invoke(tools["annotate_document"], handle=src, check=False,
                   output_path="set_marked.pdf",
                   remove=[uids[2], {"id": uids[1]}])
    assert sorted(r["says"] for r in gone["removed"]) == [
        "[#2] remark 2", "[#3] remark 3"]
    assert "not_removed" not in gone


def test_a_uid_follows_the_rules_an_id_does(doc, folder):
    from planlens.document.annotations import extract_annotations
    tools = make_tools(None, folder, {"set.pdf": doc.pdf}, signer=SIGNER)
    src = _invoke(tools["open_document"], source="set.pdf")["handle"]
    _invoke(tools["annotate_document"], handle=src, check=False,
            output_path="set_marked.pdf", markups=_marks(doc)[:1])
    copy = pymupdf.open(str(folder / "set_marked.pdf"))
    try:
        theirs = next(m for i in range(copy.page_count)
                      for m in extract_annotations(copy[i], i)[0]
                      if m.author and "AI draft" not in m.author)
    finally:
        copy.close()
    out = _invoke(tools["annotate_document"], handle=src, check=False,
                  output_path="set_marked.pdf",
                  remove=[theirs.uid, "p0.nplanlens-0000000000000000"])
    assert out["removed"] == []
    reasons = {r["remove"]: r["reason"] for r in out["not_removed"]}
    assert "not one this app wrote" in reasons[theirs.uid]
    # a uid no mark has is read as words, as before
    assert reasons["p0.nplanlens-0000000000000000"] == (
        "no mark this app wrote says that, in its comment or its label")
    # an object with a uid key is refused as any other key, pointing at id
    refused = _invoke(tools["annotate_document"], handle=src, check=False,
                      output_path="set_marked.pdf",
                      remove=[{"uid": theirs.uid}])
    assert "uid -> id" in refused["error"]
    assert "Valid keys: ['id', 'text']" in refused["error"]


# -- forget_path through ReviewToolkit.forget ----------------------------------

def test_forget_path_closes_through_the_toolkits_forget(plan, tmp_path,
                                                       monkeypatch):
    from planlens.tools import ReviewToolkit
    pdf = tmp_path / "plan.pdf"
    pdf.write_bytes(plan.pdf)
    conversation._SPACES.clear()
    with using(ReviewHost(resolve_source=lambda s: str(pdf))):
        entry = conversation.open_document_entry("plan.pdf")
        seen = []
        real = ReviewToolkit.forget

        def forget(self, path):
            seen.append(path)
            return real(self, path)

        monkeypatch.setattr(ReviewToolkit, "forget", forget)
        assert conversation.forget_path(str(pdf)) == 1
        assert seen == [str(pdf)]
        # closed, its handle reopens from the source it came from
        assert conversation.document_entry(entry.handle).handle == \
            entry.handle
        monkeypatch.setattr(ReviewToolkit, "forget",
                            lambda self, path: 1 / 0)
        assert conversation.forget_path(str(pdf)) == 0
