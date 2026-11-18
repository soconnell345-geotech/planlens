"""PL-09: captions on pages that are not figures (a table's caption over the
table), read only for a host that asks (``captions_on_every_page``, off by
default), so that a list of tables can be placed. Made-up report only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.roles import document_outline  # noqa: E402

FILLER = ("The made-up values below were measured on samples taken from the "
          "trial holes and are given for the purposes of this test only")


def _report() -> bytes:
    doc = pymupdf.open()

    def page(lines, size=10, step=15, top=72):
        p = doc.new_page(width=612, height=792)
        for i, text in enumerate(lines):
            p.insert_text((72, top + step * i), text, fontsize=size)

    page(["LIST OF TABLES",
          "Table 1 Summary of Laboratory Test Results ........ 3",
          "Table 2 Groundwater Level Readings ........ 4",
          "Table 3 Pile Capacity Summary ........ 9",
          "LIST OF FIGURES",
          "Figure 2 Groundwater Level Readings ........ 4"])
    page(["1.0 INTRODUCTION"]
         + [FILLER] * 6
         + ["Table 1 summarizes the laboratory test results for the site."]
         + [FILLER] * 4
         + ["Table 2. The water levels were measured in each well over the "
            "two months that followed the drilling of the holes"]
         + [FILLER] * 6)
    rows = [f"B-{n}   {n}.5 m   moisture {20 + n} %   density {17 + n % 3}.{n} "
            f"kN/m3   remarks none" for n in range(1, 25)]
    page(["Table 1: Summary of Laboratory Test Results"] + rows)
    page(["TABLE 2", "GROUNDWATER LEVEL READINGS"]
         + [f"Well W-{n}   reading {n}   depth {n}.2 m below ground   on day "
            f"{n * 3} of the readings" for n in range(1, 25)])
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def outlines():
    with Document(content=_report()) as doc:
        return document_outline(doc), document_outline(
            doc, captions_on_every_page=True)


def _placed(outline, kind):
    return {e.number: e.page for e in outline.of_kind(kind)}


def test_by_default_the_list_of_tables_is_unplaced_as_before(outlines):
    default, _ = outlines
    assert _placed(default, "table") == {"1": None, "2": None, "3": None}
    assert default.captions == []


def test_asked_for_the_table_captions_place_the_list(outlines):
    _, asked = outlines
    assert _placed(asked, "table") == {"1": 2, "2": 3, "3": None}
    assert [(m.page, m.number, m.text) for m in asked.captions] == [
        (2, "1", "Table 1: Summary of Laboratory Test Results"),
        (3, "2", "TABLE 2 GROUNDWATER LEVEL READINGS"),
    ]


def test_a_sentence_naming_a_table_is_not_a_caption(outlines):
    _, asked = outlines
    assert all(m.page != 1 for m in asked.captions)


def test_a_figure_entry_is_never_placed_on_a_table_caption(outlines):
    """Figure 2 shares its number and title with Table 2's caption."""
    default, asked = outlines
    assert _placed(default, "figure") == {"2": None}
    assert _placed(asked, "figure") == {"2": None}


def test_the_switch_leaves_a_report_with_figure_captions_as_it_was():
    from planlens.testing.report_fixtures import build_synthetic_report
    with Document(content=build_synthetic_report().pdf) as doc:
        default = document_outline(doc).to_dict()
        asked = document_outline(doc, captions_on_every_page=True).to_dict()
    assert asked == default
