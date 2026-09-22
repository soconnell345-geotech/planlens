"""ReviewToolkit over the synthetic review document.

The properties that matter to a host: every result is valid JSON inside the
size limit; paging loses nothing; mistakes come back as instructions; and the
content is the document layer's (comments kept out of page text, locations in
the displayed frame).
"""

import json
import os

import pytest

fitz = pytest.importorskip("fitz")

from planlens.testing import (  # noqa: E402
    build_synthetic_review_document, build_synthetic_submittal,
    build_unmapped_text_pdf,
)
from planlens.tools import ReviewToolkit, ToolError  # noqa: E402

UPLOAD = "review.pdf"


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_review_document()


def _kit(gt, max_chars=7500):
    def resolve(key):
        if key == UPLOAD:
            return gt.pdf
        raise ToolError(f"no upload named '{key}'", hint=f"uploads: [{UPLOAD}]")
    return ReviewToolkit(resolve_source=resolve, max_chars=max_chars)


@pytest.fixture
def kit(gt):
    k = _kit(gt)
    yield k
    k.close()


def call(kit, name, **args):
    text = kit.call_json(name, args)
    assert len(text) <= kit.max_chars
    return json.loads(text)


def _open(kit):
    return call(kit, "open_document", source=UPLOAD)["handle"]


# -- open_document ------------------------------------------------------------

def test_open_document_names_the_pages_whose_text_cannot_be_trusted():
    """Two failures, two fields, because the answer to them differs.

    A page with no text layer has nothing to read. A page whose font carries
    no Unicode map has something to read that is not what the page says — and
    that one reads as ordinary text to every tool that does not count. The
    host sees them apart.
    """
    broken = build_unmapped_text_pdf(0.5, n_pages=2)

    def resolve(key):
        if key == "calcs.pdf":
            return broken
        raise ToolError(f"no upload named '{key}'")

    kit = ReviewToolkit(resolve_source=resolve, max_chars=7500)
    try:
        out = call(kit, "open_document", source="calcs.pdf")
        assert out["pages_with_unreliable_text"] == "0-1"
        # They are NOT also reported as having no text layer: they have one.
        assert "pages_without_text_layer" not in out
        rows = call(kit, "document_page_map", handle=out["handle"])["rows"]
        assert all(r["text_unreliable"] is True for r in rows)
    finally:
        kit.close()


def test_open_document_maps_the_whole_document(kit, gt):
    out = call(kit, "open_document", source=UPLOAD)
    assert out["handle"].startswith("doc_")
    assert out["n_pages"] == 5
    kinds = out["pages_by_kind"]
    assert kinds["text"] == "0"
    assert kinds["drawing_sheet"] == "1"
    assert kinds["blank"] == "3"
    assert kinds["scanned"] == "4"
    assert out["markups"]["n"] == 5
    assert out["markups"]["by_author"] == {gt.reviewer: 2, gt.contractor: 3}
    assert out["markups"]["pages"] == "0-1"
    assert out["pages_without_text_layer"] == "4"
    assert out["pages_with_hidden_cad_text"] == "1"
    # Nothing in this document has a broken font, so the field stays away.
    assert "pages_with_unreliable_text" not in out
    assert [t["title"] for t in out["toc"]] == [t[1] for t in gt.toc]
    assert out["drawing_sheets"] == [{"page": 1, "label": gt.sheet_label}]


def test_same_upload_same_handle(kit):
    assert _open(kit) == _open(kit)


def test_open_by_file_path(gt, tmp_path):
    path = tmp_path / "set.pdf"
    path.write_bytes(gt.pdf)
    k = ReviewToolkit()
    try:
        assert call(k, "open_document", source=str(path))["n_pages"] == 5
        missing = call(k, "open_document", source=str(tmp_path / "nope.pdf"))
        assert "not a readable file" in missing["error"]
    finally:
        k.close()


# -- mistakes come back as instructions --------------------------------------------

def test_unknown_tool_handle_and_arguments(kit):
    assert "available tools" in call(kit, "open_documnet", source=UPLOAD)["hint"]
    bad = call(kit, "read_document", handle="doc_missing")
    assert "open_document first" in bad["hint"]
    handle = _open(kit)
    typo = call(kit, "read_document", handle=handle, page=1)
    assert "bad arguments" in typo["error"]
    missing = call(kit, "search_document", handle=handle)
    assert "bad arguments" in missing["error"]
    assert "no upload named" in call(kit, "open_document", source="x.pdf")["error"]


def test_out_of_range_pages(kit):
    handle = _open(kit)
    out = call(kit, "read_document", handle=handle, pages="3-9")
    assert "numbered 0-4" in out["error"]


def test_invalid_regex_is_explained(kit):
    handle = _open(kit)
    out = call(kit, "search_document", handle=handle, pattern="B-(", regex=True)
    assert "invalid regular expression" in out["error"]
    assert "regex=false" in out["hint"]


# -- reading ---------------------------------------------------------------------------

def _read_all(kit, handle, **kw):
    """Follow read_document's cursor to the end; return the lines seen."""
    lines, args, calls = [], dict(kw), 0
    while True:
        out = call(kit, "read_document", handle=handle, **args)
        calls += 1
        assert calls < 500, "cursor did not advance"
        for ln in out["text"].split("\n"):
            if " continued from line " not in ln:
                lines.append(ln)
        if "next" not in out:
            return lines, calls
        args = dict(kw, pages=out["next"]["pages"],
                    start_line=out["next"]["start_line"])


@pytest.mark.parametrize("with_locations", [False, True])
def test_paging_is_lossless(gt, with_locations):
    big, small = _kit(gt, max_chars=200000), _kit(gt, max_chars=1000)
    try:
        full, one_call = _read_all(big, _open(big), with_locations=with_locations)
        paged, n_calls = _read_all(small, _open(small),
                                   with_locations=with_locations)
        assert one_call == 1 and n_calls > 3
        assert paged == full
    finally:
        big.close()
        small.close()


def test_overlong_line_is_marked_not_overflowed():
    doc = fitz.open()
    page = doc.new_page(width=14400, height=200)
    page.insert_text((10, 100), "NOTE " + "x" * 2400, fontsize=4)
    data = doc.tobytes()
    doc.close()
    k = ReviewToolkit(resolve_source=lambda key: data, max_chars=1000)
    try:
        handle = _open(k)
        out = call(k, "read_document", handle=handle)
        assert "[line cut: longer than the limit]" in out["text"]
        assert "next" not in out
    finally:
        k.close()


def test_read_with_locations_and_markups(kit, gt):
    handle = _open(kit)
    text = call(kit, "read_document", handle=handle, pages=gt.sheet_page,
                with_locations=True)["text"]
    assert text.startswith(f"=== page 1 ({gt.sheet_label}) [drawing_sheet] ===")
    upright = [ln for ln in text.splitlines() if ln.endswith(gt.sheet_text_upright)]
    assert upright and upright[0].startswith("[p1.t") and "@ 300," in upright[0]
    assert any(ln.endswith(gt.hidden_cad_text) and " cad]" in ln
               for ln in text.splitlines())
    assert "points at 1500,900" in text
    reply = [ln for ln in text.splitlines() if gt.contractor in ln
             and "FreeText" in ln][0]
    assert "aimed at p1.m0" in reply


def test_page_text_excludes_reviewer_comments(kit, gt):
    handle = _open(kit)
    text = call(kit, "read_document", handle=handle, pages=gt.sheet_page,
                include_markups=False)["text"]
    assert "CONFIRM THE PILE" not in text
    assert gt.sheet_text_upright in text


def test_tables_render_as_markdown(kit, gt):
    handle = _open(kit)
    text = call(kit, "read_document", handle=handle, pages=gt.table_page)["text"]
    assert "| Boring | Depth (ft) | N-value |" in text
    assert "| B-4 | 35 | 22 |" in text


def test_scanned_page_warns(kit, gt):
    handle = _open(kit)
    text = call(kit, "read_document", handle=handle, pages=gt.scanned_page)["text"]
    assert "! no text layer" in text


# -- search, markups, page map ------------------------------------------------------------

def test_search_reaches_markups(kit, gt):
    handle = _open(kit)
    out = call(kit, "search_document", handle=handle, pattern="embedment",
               pages=[gt.sheet_page])
    assert out["n_hits"] == 2
    assert all(h.get("markup_id") for h in out["hits"])
    assert out["pages_with_hits"] == {"1": 2}


def test_markups_filter_and_paging(gt):
    small = _kit(gt, max_chars=1000)
    try:
        handle = _open(small)
        assert call(small, "document_markups", handle=handle,
                    author="contractor")["n_markups"] == 3
        rows, offset = [], 0
        while True:
            out = call(small, "document_markups", handle=handle, offset=offset)
            rows.extend(out["markups"])
            if "next_offset" not in out:
                break
            offset = out["next_offset"]
        assert len(rows) == 5 and len(set(rows)) == 5
    finally:
        small.close()


def test_page_map_filter_and_evidence(kit, gt):
    handle = _open(kit)
    out = call(kit, "document_page_map", handle=handle, kind="drawing_sheet",
               with_evidence=True)
    assert [r["page"] for r in out["rows"]] == [gt.sheet_page]
    assert out["rows"][0]["evidence"]["rule"]
    plain = call(kit, "document_page_map", handle=handle)
    assert all("evidence" not in r for r in plain["rows"])
    assert [r for r in plain["rows"] if r["page"] == gt.scanned_page][0]["needs_ocr"]


# -- publication and the size guarantee ----------------------------------------------------

def test_specs_in_each_style(kit):
    names = kit.tool_names
    assert len(names) == len(set(names))
    for spec in kit.specs("plain"):
        props = spec["parameters"]["properties"]
        assert set(spec["parameters"]["required"]) <= set(props)
    assert all("input_schema" in s for s in kit.specs("anthropic"))
    assert all(s["type"] == "function" for s in kit.specs("openai"))
    with pytest.raises(ValueError):
        kit.specs("nope")


@pytest.mark.parametrize("max_chars", [1000, 2500, 7500])
def test_every_result_fits_the_limit(gt, max_chars):
    k = _kit(gt, max_chars=max_chars)
    try:
        handle = _open(k)
        calls = [
            ("open_document", {"source": UPLOAD}),
            ("document_page_map", {"handle": handle, "with_evidence": True}),
            ("read_document", {"handle": handle, "with_locations": True}),
            ("read_document", {"handle": handle, "pages": "1"}),
            ("search_document", {"handle": handle, "pattern": "e"}),
            ("document_markups", {"handle": handle}),
        ]
        for name, args in calls:
            text = k.call_json(name, args)
            assert len(text) <= max_chars, (name, len(text))
            assert "error" not in json.loads(text), (name, text[:200])
    finally:
        k.close()


@pytest.mark.parametrize("builder", [build_synthetic_review_document,
                                     build_synthetic_submittal])
def test_open_document_fits_every_size_limit(builder):
    """Whatever the host's limit, the answer carries a usable handle.

    open_document is assembled from optional facts plus three row blocks
    (segments, bookmarks, sheet labels), and a single long row used to push
    the result past the limit — the model then got "produced N characters,
    over the limit" back, with no handle in it and nothing to continue from.
    The limit is swept because the overflow only showed between two sizes
    that both looked fine (1,000 and 1,500 characters passed; 1,200 did not).
    """
    gt = builder()
    n_pages = None
    for max_chars in range(600, 3001, 100):
        if max_chars < 1000:
            # Documented floor: a limit this small cannot hold a useful
            # result, and the toolkit says so instead of guessing.
            with pytest.raises(ValueError):
                ReviewToolkit(resolve_source=lambda key: gt.pdf,
                              max_chars=max_chars)
            continue
        k = ReviewToolkit(resolve_source=lambda key: gt.pdf,
                          max_chars=max_chars)
        try:
            text = k.call_json("open_document", {"source": UPLOAD})
            assert len(text) <= max_chars, (max_chars, len(text))
            out = json.loads(text)
            assert "error" not in out, (max_chars, text[:200])
            assert out["handle"].startswith("doc_"), (max_chars, text[:200])
            assert out["pages_by_kind"]
            # The map itself never varies with the limit; only its tail does.
            n_pages = n_pages if n_pages is not None else out["n_pages"]
            assert out["n_pages"] == n_pages
        finally:
            k.close()


def test_open_document_keeps_the_handle_when_nothing_else_fits(gt):
    """A map that cannot fit at all still leaves the caller a handle."""
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf, max_chars=1000)
    try:
        out = call(k, "open_document", source="x" * 1200 + ".pdf")
        assert out["handle"].startswith("doc_")
        assert out["n_pages"] == 5
        assert "document_page_map" in out["truncated"]
    finally:
        k.close()


def test_per_call_limit_overrides_the_toolkit_limit(kit, gt):
    handle = _open(kit)
    small = kit.call_json("read_document", {"handle": handle}, max_chars=1000)
    large = kit.call_json("read_document", {"handle": handle}, max_chars=60000)
    assert len(small) <= 1000 and "next" in json.loads(small)
    assert "next" not in json.loads(large)
    # The toolkit's own limit is untouched afterwards.
    assert len(kit.call_json("read_document", {"handle": handle})) <= 7500
    with pytest.raises(ValueError):
        kit.call_json("read_document", {"handle": handle}, max_chars=10)


# -- forgiving (fuzzy) search --------------------------------------------------

def test_fuzzy_search_spec_is_published(kit):
    spec = [s for s in kit.specs("plain") if s["name"] == "search_document"][0]
    props = spec["parameters"]["properties"]
    assert "fuzzy" in props and props["fuzzy"]["type"] == "boolean"
    assert props["min_score"]["type"] == "integer"
    assert "fuzzy=true" in spec["description"]


def test_fuzzy_search_dispatches_and_scores_its_hits(kit, gt):
    pytest.importorskip("rapidfuzz")
    handle = _open(kit)
    # One letter wrong in a phrase the report really contains.
    out = call(kit, "search_document", handle=handle,
               pattern="approximately 40-foot centbrs", fuzzy=True)
    assert out["fuzzy"] is True and out["min_score"] == 80
    assert out["n_hits"] >= 1
    assert all("score" in h and "source" in h for h in out["hits"])
    scores = [h["score"] for h in out["hits"]]
    assert scores == sorted(scores, reverse=True)
    assert "40-foot" in out["hits"][0]["snippet"]


def test_fuzzy_search_min_score_is_passed_through(kit):
    pytest.importorskip("rapidfuzz")
    handle = _open(kit)
    out = call(kit, "search_document", handle=handle, pattern="borings",
               fuzzy=True, min_score=95)
    assert out["min_score"] == 95
    assert all(h["score"] >= 95 for h in out["hits"])


def test_an_exact_miss_suggests_fuzzy(kit):
    pytest.importorskip("rapidfuzz")
    handle = _open(kit)
    out = call(kit, "search_document", handle=handle, pattern="Zeppelin")
    assert out["n_hits"] == 0
    assert "fuzzy=true" in out["hint"]


def test_a_fuzzy_hit_does_not_suggest_fuzzy_again(kit):
    pytest.importorskip("rapidfuzz")
    handle = _open(kit)
    out = call(kit, "search_document", handle=handle, pattern="borings")
    assert out["n_hits"] >= 1
    assert "fuzzy=true" not in out.get("hint", "")


def test_fuzzy_without_rapidfuzz_is_an_instruction_not_a_crash(kit,
                                                              monkeypatch):
    import sys
    handle = _open(kit)
    monkeypatch.setitem(sys.modules, "rapidfuzz", None)
    out = call(kit, "search_document", handle=handle, pattern="borings",
               fuzzy=True)
    assert "pip install rapidfuzz" in out["error"]
    assert "without fuzzy" in out["hint"]


def test_fuzzy_results_stay_inside_the_size_limit(gt):
    pytest.importorskip("rapidfuzz")
    k = _kit(gt, max_chars=1500)
    try:
        handle = _open(k)
        text = k.call_json("search_document",
                           {"handle": handle, "pattern": "borings were drilled",
                            "fuzzy": True, "min_score": 60})
        assert len(text) <= 1500
        assert "error" not in json.loads(text)
    finally:
        k.close()


# -- stated quantities ---------------------------------------------------------

def test_find_quantities_spec_is_published(kit):
    spec = [s for s in kit.specs("plain") if s["name"] == "find_quantities"][0]
    props = spec["parameters"]["properties"]
    assert props["kinds"]["items"]["enum"][:3] == ["length", "area", "volume"]
    assert "units" in props and "offset" in props
    assert spec["parameters"]["required"] == ["handle"]
    # published in every style the hosts use
    assert any(s["name"] == "find_quantities" for s in kit.specs("anthropic"))
    assert any(s["function"]["name"] == "find_quantities"
               for s in kit.specs("openai"))
    assert "find_quantities" in kit.tool_names


def test_find_quantities_reads_the_narrative(kit, gt):
    handle = _open(kit)
    out = call(kit, "find_quantities", handle=handle, pages=[gt.narrative_page])
    assert out["n_mentions"] >= 3
    rows = " | ".join(out["quantities"])
    assert "approximately" in rows          # the qualifier is kept
    assert "40 ft [length]" in rows
    assert "20 to 35 ft [length]" in rows   # a range is one row
    assert "p0" in rows


def test_find_quantities_filters(kit, gt):
    handle = _open(kit)
    lengths = call(kit, "find_quantities", handle=handle,
                   pages=[gt.narrative_page], kinds=["length"])
    assert all("[length]" in r for r in lengths["quantities"])
    feet = call(kit, "find_quantities", handle=handle,
                pages=[gt.narrative_page], units=["ft"])
    assert feet["n_mentions"] == lengths["n_mentions"]
    none = call(kit, "find_quantities", handle=handle,
                pages=[gt.narrative_page], kinds=["volume"])
    assert none["n_mentions"] == 0 and "no stated quantity" in none["note"]


def test_find_quantities_rejects_an_unknown_kind(kit):
    handle = _open(kit)
    out = call(kit, "find_quantities", handle=handle, kinds=["pressure"])
    assert "unknown quantity kind" in out["error"]
    assert "pressure_or_stress" in out["hint"]


def test_find_quantities_flags_pages_it_cannot_read_as_text(kit, gt):
    handle = _open(kit)
    out = call(kit, "find_quantities", handle=handle)
    assert out["pages_not_searchable_as_text"] == "1,4"
    assert out["look"].startswith("the pages listed are scans")


def test_find_quantities_pages_and_fits_the_limit(kit):
    handle = _open(kit)
    limit = 1000              # per-call, the way a host budgets one tool
    rows, offset, calls = [], 0, 0
    while True:
        text = kit.call_json("find_quantities",
                             {"handle": handle, "offset": offset},
                             max_chars=limit)
        assert len(text) <= limit
        out = json.loads(text)
        rows.extend(out["quantities"])
        calls += 1
        if "next_offset" not in out:
            break
        assert out["next_offset"] > offset
        offset = out["next_offset"]
        assert calls < 20
    assert len(rows) == out["n_mentions"]
    assert calls > 1                        # it really did page
    bad = call(kit, "find_quantities", handle=handle, offset=999)
    assert "outside" in bad["error"]


# -- annotate_document ---------------------------------------------------------

def _kit_writing_to(gt, tmp_path, **kw):
    """A toolkit that writes into ``tmp_path`` and can reopen what it wrote.

    The resolver takes real paths as well as the upload key, because reading
    the marked-up copy back through ``open_document`` is how a model checks
    its own review.
    """
    def resolve(key):
        if key == UPLOAD:
            return gt.pdf
        if os.path.isfile(key):
            return key
        raise ToolError(f"no upload named '{key}'", hint=f"uploads: [{UPLOAD}]")

    return ReviewToolkit(resolve_source=resolve, output_dir=str(tmp_path), **kw)


def test_annotate_document_writes_a_new_file_the_reader_can_open(gt, tmp_path):
    kit = _kit_writing_to(gt, tmp_path, author="GSE (AI draft)")
    try:
        handle = _open(kit)
        out = call(kit, "annotate_document", handle=handle,
                   output_path="review_marked.pdf", markups=[
                       {"kind": "highlight", "page": gt.narrative_page,
                        "comment": "State the datum for these depths.",
                        "quote": "20 to 35 feet"},
                       {"kind": "callout", "page": gt.sheet_page,
                        "comment": "Confirm the pile embedment.",
                        "points_at": list(gt.reviewer_target)},
                   ])
        assert out["n_written"] == 2 and out["n_skipped"] == 0
        assert out["author"] == "GSE (AI draft)"
        assert out["appended_to_existing"] is False
        assert {w["kind"] for w in out["written"]} == {"highlight", "callout"}
        # A NEW file, in the directory the host configured; the opened one is
        # byte-for-byte what it was.
        written = out["output_path"]
        assert os.path.dirname(written) == str(tmp_path)
        # The tools read their own output back, which is how a model checks it.
        second = call(kit, "open_document", source=written)
        assert second["handle"] != handle
        assert second["markups"]["by_author"]["GSE (AI draft)"] == 2
    finally:
        kit.close()


def test_annotate_document_says_what_it_would_not_place(gt, tmp_path):
    kit = _kit_writing_to(gt, tmp_path)
    try:
        handle = _open(kit)
        out = call(kit, "annotate_document", handle=handle,
                   output_path="skips.pdf", markups=[
                       {"kind": "highlight", "page": gt.narrative_page,
                        "comment": "x", "quote": "CURTAIN WALL PERMEABILITY"},
                       {"kind": "box", "page": gt.sheet_page,
                        "comment": "no dimension", "bbox": [1200, 700, 1600, 820]},
                   ])
        assert out["n_written"] == 1 and out["n_skipped"] == 1
        assert out["skipped"][0]["reason"].startswith("'CURTAIN WALL")
        assert "NOT on the page" in out["note"]
    finally:
        kit.close()


def test_annotate_document_appends_to_the_same_copy(gt, tmp_path):
    kit = _kit_writing_to(gt, tmp_path)
    try:
        handle = _open(kit)
        args = {"handle": handle, "output_path": "running.pdf"}
        call(kit, "annotate_document", markups=[
            {"kind": "note", "page": 0, "comment": "one", "point": [90, 90]}],
            **args)
        again = call(kit, "annotate_document", markups=[
            {"kind": "note", "page": 0, "comment": "two", "point": [90, 130]}],
            **args)
        assert again["appended_to_existing"] is True
        marked = call(kit, "open_document", source=again["output_path"])
        assert marked["markups"]["n"] == 5 + 2
    finally:
        kit.close()


def test_annotate_document_refuses_what_it_cannot_write(gt, tmp_path):
    kit = _kit_writing_to(gt, tmp_path)
    try:
        handle = _open(kit)
        empty = call(kit, "annotate_document", handle=handle,
                     output_path="none.pdf", markups=[])
        assert "nothing would be written" in empty["error"]
        assert "reply_to" in empty["hint"]
        bad = call(kit, "annotate_document", handle=handle,
                   output_path="bad.pdf",
                   markups=[{"kind": "scribble", "page": 0, "comment": "x"}])
        assert "unknown markup kind" in bad["error"]
        nowhere = call(kit, "annotate_document", handle=handle, output_path="",
                       markups=[{"kind": "note", "page": 0, "comment": "x",
                                 "point": [1, 1]}])
        assert "output_path is required" in nowhere["error"]
    finally:
        kit.close()


def test_annotate_document_will_not_write_over_the_document_it_read(gt,
                                                                    tmp_path):
    original = tmp_path / "set.pdf"
    original.write_bytes(gt.pdf)
    kit = _kit_writing_to(gt, tmp_path)
    try:
        handle = call(kit, "open_document", source=str(original))["handle"]
        out = call(kit, "annotate_document", handle=handle,
                   output_path=str(original),
                   markups=[{"kind": "note", "page": 0, "comment": "x",
                             "point": [90, 90]}])
        assert "the document itself" in out["error"]
        assert original.read_bytes() == gt.pdf
    finally:
        kit.close()


def test_a_write_root_confines_the_output(gt, tmp_path):
    kit = _kit_writing_to(gt, tmp_path, output_root=str(tmp_path))
    try:
        handle = _open(kit)
        mark = [{"kind": "note", "page": 0, "comment": "x", "point": [90, 90]}]
        inside = call(kit, "annotate_document", handle=handle,
                      output_path="inside.pdf", markups=mark)
        assert inside["output_path"] == os.path.join(str(tmp_path),
                                                     "inside.pdf")
        out = call(kit, "annotate_document", handle=handle,
                   output_path=os.path.join("..", "escape.pdf"), markups=mark)
        assert "outside the directory" in out["error"]
    finally:
        kit.close()


def test_annotate_document_fits_the_limit(gt, tmp_path):
    kit = _kit(gt, max_chars=1500)
    kit._output_dir = str(tmp_path)
    try:
        handle = _open(kit)
        text = kit.call_json("annotate_document", {
            "handle": handle, "output_path": "many.pdf",
            "markups": [{"kind": "note", "page": 0,
                         "comment": f"comment number {n}",
                         "point": [90, 90 + 4 * n]} for n in range(40)]})
        assert len(text) <= 1500
        out = json.loads(text)               # valid JSON, not a cut string
        assert out["n_written"] == 40
        assert len(out["written"]) < 40 and "written_truncated_after" in out
    finally:
        kit.close()
