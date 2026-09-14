"""ReviewToolkit over the synthetic review document.

The properties that matter to a host: every result is valid JSON inside the
size limit; paging loses nothing; mistakes come back as instructions; and the
content is the document layer's (comments kept out of page text, locations in
the displayed frame).
"""

import json

import pytest

fitz = pytest.importorskip("fitz")

from planlens.testing import build_synthetic_review_document  # noqa: E402
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
