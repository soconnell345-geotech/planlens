"""Writing, removing and placing marks on a COPY (PL-29 (a)).

Moved from the app's test_wave2b_documents_offline.py (C4: the copy swapped
in, one mark replaced), test_wave2c_annotate_copy_offline.py (E9: remove by
a label's words; E10: what the copy holds), test_annotate_keys_offline.py
(DR-32: remove keys, empty comments, a markup's own author) and
test_wave2c_offline.py (D5: a short temporary name, a failed save said
plainly). Offline: planlens' synthetic review document, no look.

Left in the app: the shown schema as its agent framework converts it
(``convert_to_openai_tool``; the schema itself is pinned in
test_host_specs.py) and its refusal of an unknown top-level argument
(``strict_tool``'s ``unknown_arguments``; here :func:`planlens.review.call`
refuses one by name).
"""

from __future__ import annotations

import json
import os
import re
import time

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.review import marked_copy, specs  # noqa: E402
from planlens.review.tests.conftest import make_tools  # noqa: E402
from planlens.testing import build_synthetic_review_document  # noqa: E402

#: A made-up host signer; its app name marks this host's own comments.
SIGNER = "Test Reviewer via ReviewApp (AI draft)"
#: What the synthetic document carries before anything is written.
THEIRS = {"Reviewer A": 2, "Contractor B": 3}


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


@pytest.fixture
def folder(tmp_path):
    work = tmp_path / "conv" / "files"
    work.mkdir(parents=True)
    return work


def _setup(gt, folder, name="set.pdf"):
    tools = make_tools(None, folder, {name: gt.pdf}, signer=SIGNER)
    handle = json.loads(tools["open_document"].invoke(
        {"source": name}))["handle"]
    return tools, handle


def _invoke(tool, **kwargs):
    return json.loads(tool.invoke(kwargs))


def _annotate(tools, **kwargs):
    kwargs.setdefault("check", False)
    return json.loads(tools["annotate_document"].invoke(kwargs))


def _ours_in(folder, name):
    """This host's marks and labels in a written copy: (comments, labels)."""
    doc = pymupdf.open(str(folder / name))
    try:
        comments, labels = [], []
        for page in doc:
            for a in page.annots() or []:
                info = a.info or {}
                if "ReviewApp" not in (info.get("title") or ""):
                    continue
                if info.get("subject") == "Label":
                    labels.append(info.get("content"))
                else:
                    comments.append(info.get("content"))
        return comments, labels
    finally:
        doc.close()


def _two_marks(gt):
    return [
        {"kind": "highlight", "page": gt.narrative_page,
         "comment": "[#1] State the datum for these depths.",
         "quote": "20 to 35 feet"},
        {"kind": "box", "page": gt.narrative_page,
         "comment": "[#2] Which boring is this?", "bbox": [72, 300, 300, 330],
         "label": "#2"},
    ]


def _three_marks(gt):
    page = gt.narrative_page
    return [
        {"kind": "box", "page": page, "bbox": [72, 300, 300, 330],
         "comment": "[#1] The pile embedment depth is missing.",
         "label": "Embedment: not shown"},
        {"kind": "box", "page": page, "bbox": [72, 360, 300, 390],
         "comment": "[#2] Which boring is this? Its location is not shown.",
         "label": "Boring?"},
        {"kind": "box", "page": page, "bbox": [72, 420, 300, 450],
         "comment": "[#3] The boring depth differs from the log.",
         "label": "Depth"},
    ]


def _box(gt, y, comment="a remark", **extra):
    return {"kind": "box", "page": gt.narrative_page,
            "bbox": [72, y, 300, y + 30], "comment": comment, **extra}


# -- C4: the marked copy -----------------------------------------------------------

def test_rewriting_a_copy_this_conversation_holds_open(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    first = _annotate(tools, handle=src, output_path="s_marked.pdf",
                      markups=_two_marks(gt))
    assert first["n_written"] == 2 and first["output_path"] == "s_marked.pdf"
    held = _invoke(tools["open_document"], source="s_marked.pdf")
    assert held["markups"]["n"] == 5 + 2 + 1
    again = _annotate(tools, handle=src, output_path="s_marked.pdf",
                      append=False, markups=[
                          {"kind": "note", "page": gt.narrative_page,
                           "comment": "[#1] HOLD POINT, not a suggestion.",
                           "point": [90, 90]}])
    assert "error" not in again, again
    assert again["output_path"] == "s_marked.pdf"
    marks = _invoke(tools["document_markups"], handle=held["handle"],
                    author="AI draft")
    assert marks["n_markups"] == 1
    assert "HOLD POINT" in " ".join(marks["markups"])
    leftovers = [n for root, _d, names in os.walk(folder) for n in names
                 if ".part" in n]
    assert leftovers == []


def test_a_second_call_adds_to_the_same_copy(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    _annotate(tools, handle=src, output_path="s_marked.pdf",
              markups=_two_marks(gt)[:1])
    held = _invoke(tools["open_document"], source="s_marked.pdf")
    more = _annotate(tools, handle=src, output_path="s_marked.pdf",
                     markups=_two_marks(gt)[1:])
    assert more["appended_to_existing"] is True
    ours = _invoke(tools["document_markups"], handle=held["handle"],
                   author="AI draft")
    assert ours["n_markups"] == 3


def test_one_mark_is_replaced_by_its_id_and_the_rest_stay(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    _annotate(tools, handle=src, output_path="s_marked.pdf",
              markups=_two_marks(gt))
    copy = _invoke(tools["open_document"], source="s_marked.pdf")
    listed = _invoke(tools["document_markups"], handle=copy["handle"],
                     author="AI draft")["markups"]
    box_id = next(row.split("[", 1)[1].split("]", 1)[0] for row in listed
                  if "Which boring" in row)
    out = _annotate(tools, handle=src, output_path="s_marked.pdf",
                    remove=[box_id], markups=[
                        {"kind": "box", "page": gt.narrative_page,
                         "comment": "[#2] HOLD POINT: name the boring.",
                         "bbox": [72, 300, 300, 330], "label": "#2 HOLD"}])
    assert [r["id"] for r in out["removed"]] == [box_id]
    assert out["removed"][0]["with_attached"] == 1
    assert out["n_written"] == 1
    after = _invoke(tools["document_markups"], handle=copy["handle"],
                    author="AI draft")["markups"]
    text = " ".join(after)
    assert "Which boring" not in text and "name the boring" in text
    assert "State the datum" in text
    everyone = _invoke(tools["document_markups"], handle=copy["handle"])
    assert everyone["n_markups"] == 5 + 2 + 1


def test_remove_by_words_and_what_is_refused(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    _annotate(tools, handle=src, output_path="s_marked.pdf",
              markups=_two_marks(gt))
    copy = _invoke(tools["open_document"], source="s_marked.pdf")
    theirs = _invoke(tools["document_markups"],
                     handle=copy["handle"])["markups"]
    reviewer_id = next(row.split("[", 1)[1].split("]", 1)[0] for row in theirs
                       if gt.reviewer in row)
    out = _annotate(tools, handle=src, output_path="s_marked.pdf",
                    remove=["state the datum", reviewer_id, "[#"])
    assert [r["says"][:4] for r in out["removed"]] == ["[#1]"]
    reasons = " ".join(r["reason"] for r in out["not_removed"])
    assert "not one this app wrote" in reasons
    assert "give the id" in reasons or "no mark" in reasons
    assert "ids" in out["ids_note"]


def test_removing_from_a_copy_that_does_not_exist_is_refused(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    out = _invoke(tools["annotate_document"], handle=src,
                  output_path="never.pdf", remove=["p0.m0"])
    assert "does not exist" in out["error"]


def test_rebuilding_the_copy_from_itself_is_refused(gt, folder):
    tools, src = _setup(gt, folder, "s.pdf")
    _annotate(tools, handle=src, output_path="s_marked.pdf",
              markups=_two_marks(gt))
    copy = _invoke(tools["open_document"], source="s_marked.pdf")["handle"]
    out = _annotate(tools, handle=copy, output_path="s_marked.pdf",
                    append=False, markups=_two_marks(gt))
    assert "marked copy itself" in out["error"]
    assert "remove" in out["hint"]


def test_a_copy_open_elsewhere_is_saved_under_a_new_name(gt, folder,
                                                         monkeypatch):
    real_replace = os.replace
    calls = {"n": 0}

    def locked(src, dst):
        if str(dst).endswith("s_marked.pdf"):
            calls["n"] += 1
            raise PermissionError(13, "in use")
        return real_replace(src, dst)

    tools, src = _setup(gt, folder, "s.pdf")
    monkeypatch.setattr(os, "replace", locked)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    out = _annotate(tools, handle=src, output_path="s_marked.pdf",
                    markups=_two_marks(gt))
    assert out["output_path"] == "s_marked_2.pdf"
    assert "in use elsewhere" in out["saved_elsewhere"]
    assert (folder / "s_marked_2.pdf").is_file()
    assert calls["n"] == 3


# -- E9: remove by a label's words -------------------------------------------------

def test_a_mark_is_removed_by_the_words_of_its_label(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt))
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=["Embedment: not shown"])
    assert "not_removed" not in out, out
    (gone,) = out["removed"]
    assert gone["says"].startswith("[#1]") and gone["with_attached"] >= 1
    comments, labels = _ours_in(folder, "set_marked.pdf")
    assert sorted(c[:4] for c in comments) == ["[#2]", "[#3]"]
    assert sorted(labels) == ["Boring?", "Depth"]


def test_words_in_one_marks_label_and_anothers_comment_are_refused(gt,
                                                                   folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt))
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=["not shown"])
    assert out["removed"] == []
    (refused,) = out["not_removed"]
    assert refused["remove"] == "not shown"
    assert refused["reason"].startswith("2 marks say that in their comment "
                                        "or label (p")
    assert "give the id" in refused["reason"]
    comments, labels = _ours_in(folder, "set_marked.pdf")
    assert sorted(c[:4] for c in comments) == ["[#1]", "[#2]", "[#3]"]
    assert sorted(labels) == ["Boring?", "Depth", "Embedment: not shown"]


def test_words_naming_nothing_are_refused_and_new_marks_still_land(gt,
                                                                   folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt)[:2])
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=["no such words anywhere"],
                    markups=_three_marks(gt)[2:])
    assert "error" not in out, out
    assert out["removed"] == []
    (refused,) = out["not_removed"]
    assert refused["reason"] == ("no mark this app wrote says that, in its "
                                 "comment or its label")
    comments, _labels = _ours_in(folder, "set_marked.pdf")
    assert sorted(c[:4] for c in comments) == ["[#1]", "[#2]", "[#3]"]
    assert out["in_file"]["kept_from_before"] == 5 + 2


def test_removing_by_id_and_by_comment_still_work(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt))
    copy = _invoke(tools["open_document"], source="set_marked.pdf")["handle"]
    listed = _invoke(tools["document_markups"], handle=copy,
                     author="AI draft")["markups"]
    id3 = next(row.split("[", 1)[1].split("]", 1)[0] for row in listed
               if "[#3]" in row)
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=[id3, "which boring"])
    assert sorted(r["says"][:4] for r in out["removed"]) == ["[#2]", "[#3]"]
    assert "not_removed" not in out


# -- E10: what the copy holds ------------------------------------------------------

def test_the_result_says_the_documents_markups_are_in_the_copy(gt, folder):
    tools, handle = _setup(gt, folder)
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[
                        {"kind": "box", "page": gt.narrative_page,
                         "bbox": [72, 300, 300, 330],
                         "comment": "Checked", "label": "Checked"}])
    assert out["n_written"] == 1
    assert out["in_file"] == {"markups": 6, "added_by_this_call": 1,
                              "kept_from_before": 5,
                              "kept_by_author": THEIRS}
    note = out["note"]
    assert note.startswith(
        "'set_marked.pdf' holds 6 markups: 1 added by this call and 5 kept "
        "from before (Reviewer A 2, Contractor B 3). Every markup already on "
        "the document is in the copy")
    assert "the document you opened is unchanged" in note
    assert "a NEW file" not in note
    assert "READ the skipped rows" in note
    doc = pymupdf.open(str(folder / "set_marked.pdf"))
    try:
        authors = [(a.info or {}).get("title") for p in doc
                   for a in p.annots() or []
                   if a.type[1] not in ("Popup", "Link", "Widget")]
    finally:
        doc.close()
    assert authors.count("AutoCAD SHX Text") == 2
    assert len(authors) == 5 + 1 + 1 + 2


def test_a_second_call_counts_this_apps_earlier_marks(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt)[:2])
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    markups=_three_marks(gt)[2:])
    assert out["in_file"] == {
        "markups": 8, "added_by_this_call": 1, "kept_from_before": 7,
        "kept_by_author": {**THEIRS, "this app, earlier calls": 2}}
    assert "(Reviewer A 2, Contractor B 3, this app, earlier calls 2)" in \
        out["note"]


def test_a_removal_is_counted(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=_three_marks(gt))
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=["Embedment: not shown"])
    assert out["in_file"] == {
        "markups": 7, "added_by_this_call": 0, "kept_from_before": 7,
        "kept_by_author": {**THEIRS, "this app, earlier calls": 2},
        "removed_by_this_call": 1}
    assert out["note"].startswith(
        "'set_marked.pdf' holds 7 markups: 0 added by this call and 7 kept "
        "from before")
    assert "; 1 removed by this call." in out["note"]


def test_the_tool_describes_its_result():
    desc = specs.tool_description("annotate_document")
    assert "in_file" in desc and "kept from before" in desc
    assert "words from its comment or its label" in desc
    assert "never compared with what is under the mark" in desc


# -- DR-32: remove keys, the shown markups, empty comments, own authors ------------

def test_a_remove_object_with_an_unknown_key_is_refused(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=[_box(gt, 300, "[#1] first"),
                       _box(gt, 360, "[#2] second")])
    before = (folder / "set_marked.pdf").read_bytes()
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=[{"markup_id": "p0.m0"}],
                    markups=[_box(gt, 420, "[#3] third")])
    assert out["error"] == (
        "annotate_document: remove[0] has unknown key(s) ['markup_id']. "
        "Did you mean: markup_id -> id? Valid keys: ['id', 'text'].")
    assert "nothing was removed or written" in out["hint"]
    assert (folder / "set_marked.pdf").read_bytes() == before


def test_remove_objects_with_id_or_text_still_work(gt, folder):
    tools, handle = _setup(gt, folder)
    _annotate(tools, handle=handle, output_path="set_marked.pdf",
              markups=[_box(gt, 300, "[#1] first"),
                       _box(gt, 360, "[#2] second")])
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    remove=[{"text": "[#2] second"}])
    assert "error" not in out, out
    assert [r["says"] for r in out["removed"]] == ["[#2] second"]


@pytest.mark.parametrize("remove, ok", [
    (["p0.m1", "words"], True),
    ([{"id": "p0.m1"}, {"text": "words"}, {"id": "p0.m2", "text": "w"}],
     True),
    ("p0.m1", True),
    ([""], True),
    (None, True),
    ({"markup_id": "p0.m1"}, False),
    ([{"comment": "words"}], False),
    (["p0.m1", {"id": "p0.m1", "colour": "red"}], False),
])
def test_remove_problem(remove, ok):
    problem = marked_copy.remove_problem(remove)
    assert (problem is None) is ok, problem


def test_the_did_you_mean_points_words_at_text():
    problem = marked_copy.remove_problem([{"comment": "words"}])
    assert "Did you mean: comment -> text?" in problem["error"]


def test_what_the_toolkit_forgives_still_passes(gt, folder):
    tools, handle = _setup(gt, folder)
    page = gt.narrative_page
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[
                        {"kind": "box", "page": page, "comment": "c0",
                         "box": [72, 300, 300, 330], "color": "blue"},
                        {"kind": "box", "page": page, "comment": "c1",
                         "page_bbox": [72, 360, 300, 390], "colour": "red"},
                        {"kind": "note", "page": page, "comment": "c2",
                         "anchor": {"point": [120, 120]}},
                        {"kind": "text", "pdf_page": page + 1, "comment": "c3",
                         "point": [150, 150]},
                    ])
    assert "error" not in out, out
    assert out["n_written"] == 4 and not out.get("skipped")
    notes = {row["index"]: " ".join(row["notes"]) for row in out["adjusted"]}
    assert "'color' is not a markup field" in notes[0]
    assert "'colour' is not a markup field" in notes[1]
    assert "anchor object" in notes[2]
    assert "kind 'text' was written as a note" in notes[3]


def test_a_markups_own_author_does_not_sign_it(gt, folder):
    tools, handle = _setup(gt, folder)
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[
                        _box(gt, 300, "[#1] signed", author="Someone Else"),
                        _box(gt, 360, "[#2] plain")])
    assert out["n_written"] == 2
    (row,) = out["adjusted"]
    assert row["index"] == 0
    assert row["notes"] == [marked_copy.AUTHOR_IGNORED_NOTE]
    doc = pymupdf.open(str(folder / "set_marked.pdf"))
    try:
        rows = [((a.info or {}).get("title"), (a.info or {}).get("content"))
                for p in doc for a in p.annots() or []
                if (a.info or {}).get("subject") != "Label"
                and (a.info or {}).get("content") in ("[#1] signed",
                                                      "[#2] plain")]
    finally:
        doc.close()
    assert sorted(c for _a, c in rows) == ["[#1] signed", "[#2] plain"]
    assert all(a == SIGNER for a, _c in rows), rows


def test_marks_with_no_comment_are_said(gt, folder):
    tools, handle = _setup(gt, folder)
    page = gt.narrative_page
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[
                        {"kind": "circle", "page": page,
                         "bbox": [72, 300, 300, 330]},
                        _box(gt, 360, "said"),
                        {"kind": "circle", "page": page, "comment": "  ",
                         "bbox": [72, 420, 300, 450], "label": "A"},
                        {"kind": "box", "page": 99, "bbox": [1, 2, 3, 4]},
                    ])
    assert out["n_written"] == 3
    assert out["empty_comments"].startswith(
        "markups 0, 2 have no comment, so each was written as a mark with "
        "an empty note.")
    assert "remove=[its id]" in out["empty_comments"]


def test_a_cut_skipped_list_is_said_with_the_empty_comments():
    marks = [{"kind": "box", "page": 0}, {"kind": "box", "page": 0}]
    full = marked_copy.empty_comments_note(marks, {"skipped": [{"index": 1}]})
    assert full.startswith("markup 0 has no comment")
    assert "cut short" not in full
    cut = marked_copy.empty_comments_note(
        marks, {"skipped": [], "skipped_truncated_after": 0})
    assert cut.startswith("markups 0, 1 have no comment")
    assert "The skipped list was cut short" in cut


def test_adjusted_rows_merge_and_sort_with_any_index():
    result = {"adjusted": [{"index": 2, "notes": ["planlens"]},
                           {"index": "x", "notes": ["odd"]}]}
    marked_copy._merge_adjusted(result, [
        {"index": 2, "notes": ["app"]}, {"index": 0, "notes": ["app"]},
        {"index": None, "notes": ["none"]}])
    assert [r["index"] for r in result["adjusted"]] == [0, 2, None, "x"]
    assert result["adjusted"][1]["notes"] == ["planlens", "app"]


def test_one_mark_with_no_comment_and_none(gt, folder):
    tools, handle = _setup(gt, folder)
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[
                        _box(gt, 300, "said"),
                        {"kind": "box", "page": gt.narrative_page,
                         "bbox": [72, 360, 300, 390]}])
    assert out["empty_comments"].startswith(
        "markup 1 has no comment, so it was written")
    out = _annotate(tools, handle=handle, output_path="set_marked.pdf",
                    append=False, markups=[_box(gt, 300, "said")])
    assert "empty_comments" not in out


def test_a_mark_off_the_page_is_refused_before_anything_is_written(gt,
                                                                  folder):
    tools, handle = _setup(gt, folder)
    out = _annotate(tools, handle=handle, output_path="off.pdf",
                    markups=[_box(gt, 300), {
                        "kind": "box", "page": gt.narrative_page,
                        "comment": "x", "bbox": [5000, 5000, 5010, 5010]}])
    assert "would fall off the page" in out["error"]
    assert "markup 1" in out["error"]
    assert not (folder / "off.pdf").exists()


def test_the_host_has_the_last_word_on_a_marked_copy(gt, folder):
    seen = []

    def after(result, markups):
        seen.append(len(markups))
        return dict(result, host_note="checked by the host")

    tools = make_tools(None, folder, {"set.pdf": gt.pdf}, signer=SIGNER,
                       after_marked_copy=after)
    handle = _invoke(tools["open_document"], source="set.pdf")["handle"]
    out = _annotate(tools, handle=handle, output_path="host.pdf",
                    markups=[_box(gt, 300)])
    assert out["host_note"] == "checked by the host" and seen == [1]


# -- D5: a short temporary name, and a failed save said plainly --------------------

def test_the_temporary_name_is_short(folder):
    make_tools(None, folder)
    tmp = marked_copy._temp_beside(str(
        folder / ("Sample_Civil_Set_IFC_with_a_long_descriptive_name"
                  "_marked.pdf")))
    assert os.path.dirname(tmp) == str(folder / ".scratch")
    assert re.fullmatch(r"[0-9a-f]{8}\.part\.pdf", os.path.basename(tmp))


def test_an_unwritable_temporary_copy_is_said_plainly(folder, monkeypatch):
    make_tools(None, folder)
    blocker = folder.parent / "not_a_folder"
    blocker.write_text("x")
    monkeypatch.setattr(marked_copy, "_temp_beside",
                        lambda final: str(blocker / "abcd1234.part.pdf"))
    calls = []
    out = marked_copy.write_marked_copy(
        "set.pdf", [{"kind": "note", "page": 0, "comment": "x",
                     "point": [1, 1]}],
        "set_marked.pdf", True, "me", lambda args: calls.append(args))
    assert out["error"].startswith(
        "could not write the marked copy on the server")
    assert "will fail the same way" in out["hint"]
    assert calls == []
    assert str(folder.parent) not in json.dumps(out)


def test_mupdfs_save_failure_is_said_plainly(folder):
    make_tools(None, folder)

    def planlens(args):
        return json.dumps({
            "error": "annotate_document failed: FzErrorSystem: code=2: "
                     f"cannot open file '.scratch/"
                     f"{os.path.basename(args['output_path'])}': No such "
                     "file or directory",
            "hint": "The tool raised an error instead of answering; ... Try "
                    "it again once if the cause looks passing"})
    out = marked_copy.write_marked_copy(
        "set.pdf", [{"kind": "note", "page": 0, "comment": "x",
                     "point": [1, 1]}], "set_marked.pdf", True, "me", planlens)
    assert out["error"].startswith(
        "could not write the marked copy on the server")
    assert "FzErrorSystem" in out["error"]
    assert "will fail the same way" in out["hint"]
    assert "Try it again once" not in out["hint"]
    leftovers = [n for _r, _d, names in os.walk(folder) for n in names
                 if ".part" in n]
    assert leftovers == []


@pytest.mark.skipif(os.name != "nt", reason="the 260-character limit is "
                                            "Windows'")
def test_a_path_too_long_for_windows_is_named_as_the_cause(tmp_path):
    long_path = str(tmp_path / ("x" * (300 - len(str(tmp_path)))))
    exc = marked_copy._write_failure(long_path, OSError(2, "No such file"))
    assert "characters long, over Windows' 260-character limit" in str(exc)
