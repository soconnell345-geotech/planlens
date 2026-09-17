"""Page roles and work items, on a synthetic report with known answers.

The fixture (:func:`planlens.testing.build_synthetic_report`) is built page by
page from the wording of the trade, never from a real document: a cover, a
contents page, a narrative run with a running header and printed numbering,
an appendix tab, ruled log forms, a photograph page, laboratory sheets, a
whole report bound inside an appendix, and a calculation printout.
"""

from __future__ import annotations

import pytest

from planlens.document import open_document
from planlens.document.roles import (
    LOG_ROLES,
    ROLES,
    Item,
    PageRole,
    _compact_pages,
    _norm,
    _phrases,
    build_items,
    document_items,
    document_outline,
    page_ledger,
    page_roles,
    roles_and_items,
)
from planlens.testing import build_synthetic_report


@pytest.fixture(scope="module")
def report():
    return build_synthetic_report()


@pytest.fixture(scope="module")
def graded(report):
    with open_document(report.pdf, name="synthetic report") as doc:
        roles, items = roles_and_items(doc)
    return roles, items


# -- the whole document -----------------------------------------------------

def test_every_page_gets_its_role(graded, report):
    roles, _items = graded
    assert len(roles) == report.n_pages
    got = {r.page: r.role for r in roles}
    assert got == report.roles


def test_every_role_is_in_the_vocabulary(graded):
    roles, _items = graded
    assert {r.role for r in roles} <= set(ROLES)


def test_every_page_carries_its_evidence(graded):
    roles, _items = graded
    for r in roles:
        assert 0.0 < r.confidence <= 1.0
        assert r.evidence.get("rule"), f"page {r.page} says nothing for itself"


# -- the pages the rules have to tell apart ---------------------------------

def test_a_ruled_log_form_is_a_boring_log(graded):
    roles, _items = graded
    page = next(r for r in roles if r.page == 7)
    assert page.role == "boring_log"
    assert "log form fields" in page.evidence["why"]


def test_a_test_pit_log_is_not_a_boring_log(graded):
    roles, _items = graded
    assert next(r for r in roles if r.page == 9).role == "test_pit_log"


def test_a_lab_sheet_naming_its_boring_is_still_a_lab_sheet(graded):
    """The Atterberg sheet prints "Boring No. B-1" in its title block."""
    roles, _items = graded
    assert next(r for r in roles if r.page == 12).role == "lab_test"


def test_narrative_prose_is_not_the_tests_it_discusses(graded):
    """The prose names Atterberg limits, grain size and the test pits."""
    roles, _items = graded
    for page in (2, 3, 4):
        assert next(r for r in roles if r.page == page).role == "narrative"


def test_a_photograph_page_is_photos_not_the_pit_it_shows(graded):
    roles, _items = graded
    assert next(r for r in roles if r.page == 10).role == "photos"


def test_a_program_printout_is_a_calculation(graded):
    roles, _items = graded
    page = next(r for r in roles if r.page == 20)
    assert page.role == "calculation"
    assert "lpile" in page.evidence["why"].lower()


def test_tabs_are_dividers_and_the_contents_page_is_not(graded):
    roles, _items = graded
    got = {r.page: r.role for r in roles}
    assert [p for p, role in got.items() if role == "divider"] == [6, 11, 14, 19]
    assert got[1] == "toc"


def test_a_report_bound_inside_takes_all_of_its_pages(graded):
    """Its cover, its prose, its own tab and its own boring log."""
    roles, _items = graded
    for page in (15, 16, 17, 18):
        r = next(x for x in roles if x.page == page)
        assert r.role == "appended_report", f"page {page} broke out of it"
        assert "bound into this one" in r.evidence["rule"]


def test_the_outer_report_resumes_after_the_appended_one(graded):
    roles, _items = graded
    assert next(r for r in roles if r.page == 19).role == "divider"
    assert next(r for r in roles if r.page == 21).role == "calculation"


# -- work items -------------------------------------------------------------

def test_items_are_the_work_the_readers_get(graded, report):
    _roles, items = graded
    got = [(i.kind, i.pages[0], i.pages[-1]) for i in items
           if i.kind not in ("front_matter", "other", "figures")]
    assert got == report.items


def test_a_two_sheet_log_is_one_item(graded):
    _roles, items = graded
    log = next(i for i in items if i.kind == "boring_log")
    assert log.pages == [7, 8]
    assert log.title == "B-1"


def test_two_lab_sheets_are_two_items(graded):
    _roles, items = graded
    labs = [i for i in items if i.kind == "lab_test"]
    assert [i.pages for i in labs] == [[12], [13]]
    assert labs[0].title and "ATTERBERG" in labs[0].title.upper()


def test_a_two_page_printout_is_one_item(graded):
    _roles, items = graded
    calc = next(i for i in items if i.kind == "calculation")
    assert calc.pages == [20, 21]


def test_the_appended_report_is_one_item(graded):
    _roles, items = graded
    nested = next(i for i in items if i.kind == "appended_report")
    assert nested.pages == [15, 16, 17, 18]


def test_every_page_of_an_item_points_back_at_it(graded):
    roles, items = graded
    by_page = {r.page: r for r in roles}
    for item in items:
        for page in item.pages:
            assert by_page[page].item_id == item.id


def test_dividers_belong_to_no_item(graded):
    roles, _items = graded
    for r in roles:
        if r.role == "divider":
            assert r.item_id is None


# -- the public entry points ------------------------------------------------

def test_page_roles_and_document_items_agree(report):
    with open_document(report.pdf, name="synthetic report") as doc:
        roles = page_roles(doc)
        items = document_items(doc)
    assert [r.role for r in roles] == [report.roles[r.page] for r in roles]
    assert any(i.kind == "boring_log" for i in items)


def test_results_serialize_compactly(graded):
    roles, items = graded
    row = next(r for r in roles if r.page == 7).to_dict()
    assert row["page"] == 7 and row["role"] == "boring_log"
    assert isinstance(row["confidence"], float)
    log = next(i for i in items if i.kind == "boring_log").to_dict()
    assert log["pages"] == "7-8" and log["n_pages"] == 2


def test_page_zero_survives_serialization(graded):
    roles, _items = graded
    assert next(r for r in roles if r.page == 0).to_dict()["page"] == 0


# -- the small pieces -------------------------------------------------------

def test_normalisation_folds_accents_case_and_punctuation():
    assert _norm("ANALYSE GRANULOMÉTRIQUE") == " analyse granulometrique "
    assert _norm("Boring No.:") == " boring no "


def test_phrases_match_the_plural_a_tab_page_prints():
    rx = _phrases(("boring log", "photograph"))
    assert rx.search(" appendix a boring logs and photographs ")
    assert not rx.search(" boringlogs ")


def test_compact_pages_reads_as_a_range():
    assert _compact_pages([4, 5, 6, 9]) == "4-6,9"
    assert _compact_pages([3]) == "3"
    assert _compact_pages([]) == ""


def test_log_roles_are_all_in_the_vocabulary():
    assert set(LOG_ROLES) <= set(ROLES)


def test_build_items_is_callable_on_stated_results():
    """A caller can group pages it labelled itself."""
    from planlens.document.roles import PageFacts

    facts = [PageFacts(page=i) for i in range(3)]
    roles = [PageRole(0, "narrative", 0.9), PageRole(1, "narrative", 0.9),
             PageRole(2, "divider", 0.9)]
    items = build_items(facts, roles)
    assert [(i.kind, i.pages) for i in items] == [("narrative", [0, 1])]
    assert isinstance(items[0], Item)


# -- the report's account of itself -----------------------------------------

@pytest.fixture(scope="module")
def outline(report):
    with open_document(report.pdf, name="synthetic report") as doc:
        return document_outline(doc)


def test_the_contents_page_is_read_line_by_line(outline, report):
    got = [(e.kind, e.number, e.title, e.printed_page, e.page)
           for e in outline.entries]
    assert got == report.outline_entries


def test_the_three_lists_are_kept_apart(outline):
    assert len(outline.contents) == 5
    assert len(outline.figures) == 2
    assert len(outline.appendices) == 4
    assert outline.tables == []


def test_printed_page_numbers_are_kept_as_written(outline):
    """The reader cites the number on the page, not the PDF index."""
    entry = next(e for e in outline.contents
                 if e.title.startswith("3.0"))
    assert entry.printed_page == "2"
    assert entry.page == 4


def test_an_entry_that_cannot_be_placed_says_so(outline):
    """Figure 2 is listed and is not in the document."""
    figure2 = next(e for e in outline.figures if e.number == "2")
    assert figure2.page is None
    assert figure2.printed_page == "5"
    assert figure2.title == "Lateral Earth Pressure Diagram"


def test_appendix_entries_are_placed_on_their_tabs(outline, report):
    placed = {e.number: e.page for e in outline.appendices}
    assert placed == {"A": 6, "B": 11, "C": 14, "D": 19}
    for page in placed.values():
        assert report.roles[page] == "divider"


def test_every_divider_is_reported_with_its_text(outline):
    assert [m.page for m in outline.dividers] == [6, 11, 14, 19]
    tab = next(m for m in outline.dividers if m.page == 11)
    assert tab.number == "B"
    assert "laboratory test data" in tab.text.lower()


def test_the_figure_page_caption_is_read(outline, report):
    assert len(outline.captions) == 1
    caption = outline.captions[0]
    assert caption.page == report.caption_page
    assert caption.number == "1"
    assert caption.text == "Figure 1 - Footing Undercut Detail"


def test_the_narrative_section_headings_come_out_in_order(outline):
    assert [(m.number, m.text, m.page) for m in outline.headings] == [
        ("1.0", "Introduction", 2),
        ("2.0", "Site Conditions", 3),
        ("3.0", "Subsurface Exploration", 4),
    ]


def test_the_outline_serializes_with_its_placement_count(outline):
    d = outline.to_dict()
    assert d["n_entries"] == 11
    assert d["n_entries_placed"] == 8
    assert d["entries"][0]["title"] == "1.0 Introduction"
    assert "page" not in d["entries"][3]      # unplaced, and says nothing


def test_a_list_heading_alone_is_not_an_entry(outline):
    titles = [e.title.lower() for e in outline.entries]
    assert "list of figures" not in titles
    assert "appendices" not in titles


# -- the page ledger --------------------------------------------------------

@pytest.fixture(scope="module")
def ledger(report):
    with open_document(report.pdf, name="synthetic report") as doc:
        return page_ledger(doc)


def test_one_line_per_page_in_page_order(ledger, report):
    assert len(ledger) == report.n_pages
    assert ledger[0].startswith("p000 ")
    assert ledger[-1].startswith("p021 ")


def test_a_ledger_line_carries_what_a_reader_needs(ledger):
    line = ledger[7]                      # the first boring log
    assert line.startswith("p007 ")
    assert "boring_log" in line
    assert "[page-title]" in line
    assert "LOG OF BORING" in line
    assert "chars=" in line and "text_ok=Y" in line and "di=N" in line
    assert "item_" in line


def test_the_narrative_lines_carry_the_running_header_and_printed_number(
        ledger):
    line = ledger[2]
    assert "narrative" in line and "[narrative-block]" in line
    assert 'hdr="Rosewood Terrace Development' in line
    assert "pp=1/3" in line
    assert "seg=" in line


def test_a_divider_has_no_item(ledger):
    assert "item_" not in ledger[6]
    assert "[tab]" in ledger[6]


def test_every_line_names_the_rule_that_fired(ledger):
    import re as _re
    tags = {_re.search(r"\[([a-z+-]+)\]", line).group(1) for line in ledger}
    assert tags <= {"cover", "toc", "letter", "tab", "blank", "nested-report",
                    "narrative-block", "page-title", "page-title+tab",
                    "tab-declares", "page-shape", "run-continuation",
                    "prose-in-tab", "sheet-in-tab", "-"}
    assert "page-title" in tags and "nested-report" in tags


def test_the_ledger_is_compact_enough_to_read_a_long_document(ledger):
    assert max(len(line) for line in ledger) < 260


def test_ledger_from_is_callable_on_stated_results():
    from planlens.document.roles import PageFacts, ledger_from

    facts = [PageFacts(page=0, kind="text", n_text_chars=12,
                       heading="A HEADING")]
    roles = [PageRole(0, "narrative", 0.9, {"tag": "narrative-block"})]
    line = ledger_from(facts, roles, di_pages=[0])[0]
    assert line.startswith("p000 ")
    assert "[narrative-block]" in line and "di=Y" in line
