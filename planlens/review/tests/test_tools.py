"""The document tools through the host layer, no look (PL-29 (a)).

Moved from the app's test_document_tools_offline.py. Uses planlens'
synthetic review document: a report page with a review stamp, a /Rotate 90
drawing sheet with a reviewer's callout, a contractor's reply, a cloud, an
arrow and hidden CAD text, a ruled table, a blank page and a scan.

Left in the app (they test its binding, not this code): the tools on its
agent's surfaces (``make_vision_tools``), feature detection of an older
planlens (``has_tool``, ``document_tool_names``, the fuzzy refusal without a
call), its signer from ``GEOTECH_MARKUP_AUTHOR`` and the sign-in, and its own
vision-tool names in the hints (here a made-up host hint stands in).
"""

import copy
import json
import os

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import conversation, marked_copy, specs  # noqa: E402
from planlens.review.host import DEFAULT_SIGNER  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402

#: A made-up host's vision hints, standing in for the app's.
HOST_VISION_HINT = ("to look: look_at_page(source=<the source>, page=N) or "
                    "zoom_into(source=<source>, page=N, bbox=[x0,y0,x1,y1])")
HOST_IMAGE_HINT = "view the image with see_image(name=<image_path>)"


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def _tools(attachments, folder=None, **kw):
    return make_tools(None, folder, attachments, **kw)


def test_review_flow_over_an_attachment(gt):
    tools = _tools({"submittal.pdf": gt.pdf})
    opened = _invoke(tools["open_document"], source="submittal.pdf")
    handle = opened["handle"]
    assert opened["n_pages"] == 5
    assert opened["pages_by_kind"]["drawing_sheet"] == str(gt.sheet_page)
    hits = _invoke(tools["search_document"], handle=handle,
                   pattern="embedment")
    assert hits["n_hits"] == 2
    text = _invoke(tools["read_document"], handle=handle,
                   pages=str(gt.sheet_page), with_locations=True)["text"]
    assert gt.sheet_text_upright in text
    assert "points at 1500,900" in text
    assert text.index("review markup") < text.index("CONFIRM THE PILE")
    marks = _invoke(tools["document_markups"], handle=handle,
                    author="Reviewer")
    assert marks["n_markups"] == 2
    rows = _invoke(tools["document_page_map"], handle=handle,
                   pages=[0, 1])["rows"]
    assert [r["page"] for r in rows] == [0, 1]


def test_results_fit_the_cap_as_valid_json(gt):
    tools = _tools({"s.pdf": gt.pdf},
                   max_chars=conversation.budget_for_cap(3000))
    handle = _invoke(tools["open_document"], source="s.pdf")["handle"]
    raw = tools["read_document"].invoke({"handle": handle,
                                         "with_locations": True})
    assert len(raw) <= 3000
    assert "next" in json.loads(raw)


def test_truncation_disabled_still_returns_json(gt):
    tools = _tools({"s.pdf": gt.pdf}, max_chars=conversation.budget_for_cap(0))
    handle = _invoke(tools["open_document"], source="s.pdf")["handle"]
    assert "next" not in _invoke(tools["read_document"], handle=handle)


def test_real_path_and_unknown_source(gt, tmp_path):
    path = tmp_path / "set.pdf"
    path.write_bytes(gt.pdf)
    tools = _tools({"other.pdf": b"%PDF"})
    assert _invoke(tools["open_document"], source=str(path))["n_pages"] == 5
    missing = _invoke(tools["open_document"], source="nope.pdf")
    assert "other.pdf" in missing["hint"]


def test_attachments_do_not_leak_between_conversations(gt):
    mine = _tools({"mine.pdf": gt.pdf})
    assert "handle" in _invoke(mine["open_document"], source="mine.pdf")
    theirs = _tools({})
    assert "error" in _invoke(theirs["open_document"], source="mine.pdf")


def test_look_lines_name_the_hosts_vision_tools(gt):
    tools = _tools({"s.pdf": gt.pdf}, vision_hint=HOST_VISION_HINT)
    opened = _invoke(tools["open_document"], source="s.pdf")
    assert opened["source"] == "s.pdf"
    assert opened["pages_to_view"] == f"{gt.sheet_page},{gt.scanned_page}"
    text = _invoke(tools["read_document"], handle=opened["handle"],
                   pages=str(gt.scanned_page))["text"]
    assert "! look: image-only page" in text
    assert "zoom_into(source=" in text
    assert "render_page" not in review.tool_names()


def test_an_image_upload_is_reviewable(gt):
    src = pymupdf.open("pdf", gt.pdf)
    png = src[gt.scanned_page].get_pixmap(dpi=40).tobytes("png")
    src.close()
    tools = _tools({"scan.png": png})
    opened = _invoke(tools["open_document"], source="scan.png")
    assert opened["kind"] == "image" and opened["pages_by_kind"] == {
        "scanned": "0"}


def test_structure_and_thumbnails_reach_the_agent(tmp_path):
    from planlens.testing import build_synthetic_submittal
    sub = build_synthetic_submittal()
    tools = _tools({"sub.pdf": sub.pdf}, tmp_path,
                   image_view_hint=HOST_IMAGE_HINT)
    opened = _invoke(tools["open_document"], source="sub.pdf")
    assert any(s["title"] == sub.appendix_title for s in opened["segments"])
    structure = _invoke(tools["document_structure"], handle=opened["handle"])
    assert structure["n_segments"] == len(sub.expected_segments)
    thumbs = _invoke(tools["render_page_thumbnails"],
                     handle=opened["handle"], pages="0-3")
    assert thumbs["sheets"][0]["pages"] == "0-3"
    assert "see_image(name=" in thumbs["note"]
    name = thumbs["sheets"][0]["image_path"]
    assert (tmp_path / name).read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"


def test_without_the_tool_layer_the_tools_say_so(monkeypatch):
    monkeypatch.setattr(conversation, "available", lambda: False)
    out = json.loads(conversation.dispatch_document_tool(
        "open_document", {"source": "x.pdf"}))
    assert "planlens" in out["error"]


def test_find_quantities_follows_the_installed_toolkit(monkeypatch):
    assert "find_quantities" in review.tool_names()
    from planlens.tools import specs as toolkit_specs
    kept = [s for s in toolkit_specs.TOOL_SPECS
            if s["name"] != "find_quantities"]
    monkeypatch.setattr(toolkit_specs, "TOOL_SPECS", kept)
    names = review.tool_names()
    assert "find_quantities" not in names
    assert set(specs.DOCUMENT_TOOLS) <= set(names)


def test_stated_quantities_come_back_located_and_within_the_cap(gt):
    tools = _tools({"report.pdf": gt.pdf},
                   max_chars=conversation.budget_for_cap(3000))
    handle = _invoke(tools["open_document"], source="report.pdf")["handle"]
    raw = tools["find_quantities"].invoke(
        {"handle": handle, "pages": str(gt.narrative_page)})
    assert len(raw) <= 3000
    out = json.loads(raw)
    assert "error" not in out and out["n_mentions"] >= 1
    rows = out["quantities"]
    assert any("ft" in row for row in rows)
    assert any("[length]" in row for row in rows)
    assert any(f"p{gt.narrative_page}" in row for row in rows)
    none = _invoke(tools["find_quantities"], handle=handle,
                   pages=str(gt.narrative_page), kinds=["volume"])
    assert none["n_mentions"] == 0


def test_fuzzy_search_finds_a_word_with_a_letter_wrong(gt):
    tools = _tools({"s.pdf": gt.pdf})
    handle = _invoke(tools["open_document"], source="s.pdf")["handle"]
    exact = _invoke(tools["search_document"], handle=handle,
                    pattern="EMBEDMANT")
    assert exact["n_hits"] == 0
    loose = _invoke(tools["search_document"], handle=handle,
                    pattern="EMBEDMANT", fuzzy=True)
    assert loose["n_hits"] >= 1
    assert any("EMBEDMENT" in json.dumps(hit) for hit in loose["hits"])


def test_annotate_document_says_what_the_host_settles():
    desc = specs.tool_description("annotate_document")
    assert "working folder" in desc and "_marked.pdf" in desc


def test_the_marked_up_copy_lands_in_the_working_folder(tmp_path):
    make_tools(None, tmp_path)
    bare = marked_copy.markup_output_path("review_marked.pdf")
    assert bare == os.path.join(str(tmp_path), "review_marked.pdf")
    assert marked_copy.markup_output_path("notes").endswith("notes.pdf")
    elsewhere = str(tmp_path / "sub" / "given.pdf")
    assert marked_copy.markup_output_path(elsewhere) == elsewhere


def test_no_output_path_is_named_after_the_document(gt, tmp_path):
    tools = _tools({"submittal.pdf": gt.pdf}, tmp_path)
    handle = _invoke(tools["open_document"], source="submittal.pdf")["handle"]
    assert marked_copy.markup_output_path("", handle) == \
        os.path.join(str(tmp_path), "submittal_marked.pdf")
    assert marked_copy.markup_output_path("", "doc_nope").endswith(
        "document_marked.pdf")


def test_a_review_is_written_and_reads_back_through_the_same_tools(
        gt, tmp_path):
    tools = _tools({"submittal.pdf": gt.pdf}, tmp_path)
    handle = _invoke(tools["open_document"], source="submittal.pdf")["handle"]
    out = _invoke(tools["annotate_document"], handle=handle, markups=[
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "State the datum for these depths.",
         "quote": "20 to 35 feet"},
        {"kind": "callout", "page": gt.sheet_page,
         "comment": "Confirm the pile embedment.",
         "points_at": list(gt.reviewer_target)},
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "not on this page", "quote": "CURTAIN WALL GROUT TAKE"},
    ])
    assert out["n_written"] == 2 and out["n_skipped"] == 1
    assert out["author"] == DEFAULT_SIGNER
    assert out["output_path"] == "submittal_marked.pdf"
    assert os.path.isfile(os.path.join(str(tmp_path), "submittal_marked.pdf"))
    assert str(tmp_path) not in json.dumps(out)
    assert _invoke(tools["document_markups"], handle=handle)[
        "n_markups"] == 5
    marked = _invoke(tools["open_document"], source=out["output_path"])
    assert marked["markups"]["by_author"][DEFAULT_SIGNER] == 2
    written = _invoke(tools["document_markups"], handle=marked["handle"],
                      author="AI draft")
    assert written["n_markups"] == 2
    assert "State the datum" in " ".join(written["markups"])


def test_the_author_comes_from_the_host_never_the_call(gt, tmp_path):
    tools = _tools({"s.pdf": gt.pdf}, tmp_path,
                   signer="Test Reviewer via ReviewApp (AI draft)")
    handle = _invoke(tools["open_document"], source="s.pdf")["handle"]
    mark = [{"kind": "note", "page": 0, "comment": "x", "point": [90, 90]}]
    out = _invoke(tools["annotate_document"], handle=handle,
                  output_path="env.pdf", markups=mark)
    assert out["author"] == "Test Reviewer via ReviewApp (AI draft)"
    assert "author" not in specs.input_schema(
        "annotate_document", "")["properties"]
    named = _invoke(tools["annotate_document"], handle=handle,
                    output_path="named.pdf", markups=mark,
                    author="AI Draft Review")
    assert "author" in named["error"]
    assert not (tmp_path / "named.pdf").exists()
    # a callable signer is asked at each call
    who = ["First Person (AI draft)"]
    tools = _tools({"s.pdf": gt.pdf}, tmp_path, signer=lambda: who[0])
    h2 = _invoke(tools["open_document"], source="s.pdf")["handle"]
    signed = _invoke(tools["annotate_document"], handle=h2,
                     output_path="bound.pdf", markups=mark)
    assert signed["author"] == "First Person (AI draft)"


def test_an_empty_markup_list_is_refused_as_json(gt, tmp_path):
    tools = _tools({"s.pdf": gt.pdf}, tmp_path)
    handle = _invoke(tools["open_document"], source="s.pdf")["handle"]
    out = _invoke(tools["annotate_document"], handle=handle,
                  output_path="none.pdf")
    assert "nothing would be written" in out["error"]
    assert not os.path.exists(os.path.join(str(tmp_path), "none.pdf"))


def test_an_unknown_tool_or_argument_is_refused_by_name():
    out = json.loads(review.call("render_page", {}))
    assert "unknown tool" in out["error"]
    out = json.loads(review.call("open_document", {"source": "x",
                                                   "detail": 1}))
    assert "detail" in out["error"]


def test_min_score_without_fuzzy_is_said(gt, tmp_path):
    from planlens.review.tools import MIN_SCORE_UNUSED_NOTE
    tools = _tools({"set.pdf": gt.pdf}, tmp_path)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    out = _invoke(tools["search_document"], handle=handle,
                  pattern="EMBEDMENT", min_score=70)
    assert out["n_hits"] >= 1
    assert out["min_score_unused"] == MIN_SCORE_UNUSED_NOTE
    out = _invoke(tools["search_document"], handle=handle,
                  pattern="EMBEDMENT")
    assert "min_score_unused" not in out


def test_min_score_with_fuzzy_is_handed_on(gt, tmp_path, monkeypatch):
    tools = _tools({"set.pdf": gt.pdf}, tmp_path)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    sent = []
    real = conversation.dispatch_document_tool

    def spy(name, args, *a, **kw):
        sent.append(dict(args))
        return real(name, args, *a, **kw)

    monkeypatch.setattr(conversation, "dispatch_document_tool", spy)
    out = _invoke(tools["search_document"], handle=handle,
                  pattern="EMBEDMANT", fuzzy=True, min_score=75)
    assert "min_score_unused" not in out and out["n_hits"] >= 1
    assert sent[-1]["min_score"] == 75
    _invoke(tools["search_document"], handle=handle, pattern="EMBEDMANT",
            fuzzy=True)
    assert "min_score" not in sent[-1]


def test_the_model_is_still_shown_min_score_80():
    shown = specs.input_schema("search_document", "")["properties"][
        "min_score"]
    assert shown == {"type": "integer", "default": 80, "title": "Min Score"}


def test_the_shown_markups_follow_the_installed_toolkit(monkeypatch):
    from planlens.tools import specs as toolkit_specs
    newer = copy.deepcopy(toolkit_specs.TOOL_SPECS)
    spec = next(s for s in newer if s["name"] == "annotate_document")
    spec["parameters"]["properties"]["markups"]["items"]["properties"][
        "stamp"] = {"type": "string", "description": "A new field."}
    monkeypatch.setattr(toolkit_specs, "TOOL_SPECS", newer)
    items = specs.annotate_markups_schema()["items"]
    assert items["properties"]["stamp"] == {"type": "string"}
    older = copy.deepcopy(newer)
    spec = next(s for s in older if s["name"] == "annotate_document")
    del spec["parameters"]["properties"]["markups"]["items"]
    monkeypatch.setattr(toolkit_specs, "TOOL_SPECS", older)
    assert specs.annotate_markups_schema() is None
    shown = specs.input_schema("annotate_document", "")["properties"][
        "markups"]
    assert shown["anyOf"][0] == {"items": {}, "type": "array"}


def test_markups_are_shown_with_the_toolkits_own_fields():
    from planlens.tools.specs import TOOL_SPECS
    tk = next(s for s in TOOL_SPECS if s["name"] == "annotate_document")
    tk_items = tk["parameters"]["properties"]["markups"]["items"]
    shown = specs.input_schema("annotate_document", "")["properties"][
        "markups"]
    items = shown["items"]
    assert shown["type"] == "array" and items["additionalProperties"] is False
    for name, field in tk_items["properties"].items():
        if name == "author":
            continue
        if name in specs.DESCRIBED_MARKUP_FIELDS:
            assert items["properties"][name] == field, name
        else:
            bare = {k: v for k, v in field.items() if k != "description"}
            assert items["properties"][name] == bare, name
    assert "author" not in items["properties"]
    assert items["properties"]["pdf_page"]["type"] == "integer"
    assert "page" not in items["required"]
    assert {"kind", "comment"} <= set(items["required"])
    props = specs.input_schema("annotate_document", "")["properties"]
    assert props["remove"]["items"] == {"type": "string"}
    assert set(props) == {"handle", "markups", "output_path", "append",
                          "check", "remove"}
    full = json.dumps(specs.host_specs("openai"))
    assert "$ref" not in full and "$defs" not in full
