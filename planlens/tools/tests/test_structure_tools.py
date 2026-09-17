"""document_structure, render_page_thumbnails and the richer page-map rows."""

import json
import os

import pytest

fitz = pytest.importorskip("fitz")

from planlens.testing.submittal_fixtures import build_synthetic_submittal  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

IMG_HINT = "look_at_file(image_path)"


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_submittal()


@pytest.fixture
def kit(gt, tmp_path):
    k = ReviewToolkit(resolve_source=lambda key: gt.pdf,
                      output_dir=str(tmp_path), image_view_hint=IMG_HINT)
    yield k
    k.close()


def call(kit, name, **args):
    text = kit.call_json(name, args)
    assert len(text) <= kit.max_chars
    return json.loads(text)


def test_open_document_outlines_the_segments(kit, gt):
    out = call(kit, "open_document", source="sub.pdf")
    assert out["pages_by_kind"]["form"] == "5-6"
    assert out["duplicate_pages"] == str(gt.duplicate_page)
    titles = [s["title"] for s in out["segments"]]
    assert titles[0] == "Project Alpha"
    assert gt.appendix_title in titles and gt.attachment_title in titles
    assert "document_structure" in out["next"]
    assert "render_page_thumbnails" in out["next"]


def test_document_structure_tool(kit, gt):
    handle = call(kit, "open_document", source="sub.pdf")["handle"]
    out = call(kit, "document_structure", handle=handle)
    assert out["n_segments"] == len(gt.expected_segments)
    forms = [s for s in out["segments"] if s["kinds"] == {"form": 2}][0]
    assert forms["printed_pages"] == "1-2" and forms["printed_of"] == 2
    assert "printed ON the pages" in out["note"]
    bad = call(kit, "document_structure", handle=handle, offset=99)
    assert "outside" in bad["error"]


def test_page_map_rows_carry_printed_numbers_and_segments(kit, gt):
    handle = call(kit, "open_document", source="sub.pdf")["handle"]
    rows = call(kit, "document_page_map", handle=handle)["rows"]
    by_page = {r["page"]: r for r in rows}
    assert by_page[gt.report_pages[1]]["printed_page"] == 2
    assert by_page[gt.report_pages[1]]["segment"] == 1
    assert by_page[gt.sheet_pages[0]]["sheet"] == "1 of 2"
    assert by_page[gt.sheet_pages[0]]["scales"] == [f"SCALE: {gt.sheet_scale}"]
    assert by_page[gt.duplicate_page]["duplicate_of"] == gt.duplicate_of
    assert by_page[gt.form_pages[0]]["look"] is True
    assert "evidence" not in by_page[0]
    detail = call(kit, "document_page_map", handle=handle, pages=[gt.form_pages[0]],
                  with_evidence=True)["rows"][0]
    assert detail["evidence"]["footer"].startswith("Page 1 of 2")
    forms = call(kit, "document_page_map", handle=handle, kind="form")["rows"]
    assert [r["page"] for r in forms] == list(gt.form_pages)


def test_render_page_thumbnails_tool(kit, gt, tmp_path):
    handle = call(kit, "open_document", source="sub.pdf")["handle"]
    out = call(kit, "render_page_thumbnails", handle=handle, columns=4)
    (sheet,) = out["sheets"]
    assert sheet["pages"] == "0-10" and sheet["n_pages"] == 11
    assert os.path.isfile(sheet["image_path"])
    assert sheet["image_path"].startswith(str(tmp_path))
    assert IMG_HINT in out["note"] and "red frame" in out["legend"]
    part = call(kit, "render_page_thumbnails", handle=handle, pages="5-6")
    assert part["sheets"][0]["pages"] == "5-6"


def test_render_notes_carry_the_image_view_hint(kit, gt):
    handle = call(kit, "open_document", source="sub.pdf")["handle"]
    page = call(kit, "render_page", handle=handle, page=0)
    assert IMG_HINT in page["note"]
    region = call(kit, "render_region", handle=handle, page=0, bbox=[0, 0, 100, 100])
    assert region["note"] == IMG_HINT


# -- document_roles ---------------------------------------------------------

@pytest.fixture(scope="module")
def report_gt():
    from planlens.testing import build_synthetic_report
    return build_synthetic_report()


@pytest.fixture
def report_kit(report_gt, tmp_path):
    k = ReviewToolkit(resolve_source=lambda key: report_gt.pdf,
                      output_dir=str(tmp_path), image_view_hint=IMG_HINT)
    yield k
    k.close()


def test_document_roles_tool_answers_pages_and_items(report_kit, report_gt):
    call(report_kit, "open_document", source="report.pdf")
    handle = report_kit.open("report.pdf").handle
    out = call(report_kit, "document_roles", handle=handle)
    assert out["n_pages"] == report_gt.n_pages
    got = {row["page"]: row["role"] for row in out["pages"]}
    assert got == {p: r for p, r in report_gt.roles.items() if p in got}
    assert len(got) >= 18
    kinds = [(i["kind"], i["pages"]) for i in out["items"]]
    assert ("boring_log", "7-8") in kinds
    assert ("appended_report", "15-18") in kinds
    assert ("calculation", "20-21") in kinds


def test_document_roles_items_only(report_kit):
    report_kit.open("report.pdf")
    handle = report_kit.open("report.pdf").handle
    out = call(report_kit, "document_roles", handle=handle, items_only=True)
    assert "pages" not in out
    assert out["n_items"] == len(out["items"])
    assert all(i["id"] and i["kind"] for i in out["items"])


def test_document_roles_is_published_and_budgeted(report_kit):
    assert "document_roles" in report_kit.tool_names
    spec = next(s for s in report_kit.specs("plain")
                if s["name"] == "document_roles")
    assert "boring_log" in spec["description"]
    assert set(spec["parameters"]["properties"]) == {
        "handle", "items_only", "outline", "ledger", "offset"}
    handle = report_kit.open("report.pdf").handle
    text = report_kit.call_json("document_roles", {"handle": handle},
                                max_chars=1200)
    assert len(text) <= 1200 and json.loads(text)


def test_document_roles_outline_reads_the_contents_page(report_kit,
                                                        report_gt):
    handle = report_kit.open("report.pdf").handle
    out = call(report_kit, "document_roles", handle=handle, outline=True)
    o = out["outline"]
    assert o["n_entries"] == len(report_gt.outline_entries)
    titles = [e["title"] for e in o["entries"]]
    assert "Footing Undercut Detail" in titles
    appendix_b = next(e for e in o["entries"]
                      if e.get("number") == "B" and e["kind"] == "appendix")
    assert appendix_b["page"] == 11
    unplaced = next(e for e in o["entries"]
                    if e.get("number") == "2" and e["kind"] == "figure")
    assert "page" not in unplaced
    assert [m["page"] for m in o["dividers"]] == [6, 11, 14, 19]
    assert o["captions"][0]["page"] == report_gt.caption_page


def test_document_roles_ledger_is_one_line_per_page(report_kit, report_gt):
    handle = report_kit.open("report.pdf").handle
    out = call(report_kit, "document_roles", handle=handle, ledger=True)
    assert "pages" not in out
    assert out["ledger"][0].startswith("p000 ")
    assert len(out["ledger"]) + out.get("next_offset", len(out["ledger"])) \
        >= report_gt.n_pages - len(out["ledger"])
    assert "[page-title]" in " ".join(out["ledger"])
    assert "ledger_note" in out
