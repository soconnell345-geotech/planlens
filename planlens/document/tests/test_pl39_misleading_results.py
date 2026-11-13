"""PL-39 (2) and (4): contact-sheet labels name both page counts, and a rebar
callout is not read as a length range. Made-up pages and wording only."""

import pytest

from planlens.document.quantities import scan_text

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import Document  # noqa: E402
from planlens.document.document import thumbnail_label  # noqa: E402


# -- (2) contact-sheet labels -------------------------------------------------

def _three_pages() -> bytes:
    doc = pymupdf.open()
    for n in range(3):
        page = doc.new_page(width=612, height=792)
        page.insert_text((72, 72), f"Made-up page {n + 1} of a test report")
    data = doc.tobytes()
    doc.close()
    return data


def test_a_thumbnail_label_names_both_counts():
    assert thumbnail_label(3, "drawing_sheet") == \
        "pg 3 | PDF 4 drawing_sheet"
    assert thumbnail_label(0, "text", "A-101 FLOOR PLAN") == \
        "pg 0 | PDF 1 text (A-101 FLOOR )"


def test_the_legend_says_which_count_each_number_is():
    with Document(content=_three_pages()) as doc:
        sheets = doc.render_thumbnails()
    assert len(sheets) == 1
    legend = sheets[0][1]["legend"]
    assert "pg <page> | PDF <pdf_page> <kind>" in legend
    assert "counted from 0" in legend and "counted from 1" in legend
    assert "pdf_page" in legend and "red frame" in legend
    assert sheets[0][1]["pages"] == [0, 1, 2]


# -- (4) a rebar callout is not a length --------------------------------------

def _readings(text):
    return [(m.value, m.value_to, m.units, m.text) for m in scan_text(text)]


@pytest.mark.parametrize("text", [
    "BOTTOM 10-20M EACH WAY",
    "TOP 8-15M EACH WAY",
    "ADD 4-25M CONT. AT EDGE",
    "2 - 30M BARS",
    "8-15M @ 300",
    "4-20M T&B",
    "3-10M STIRRUPS",
    "10-15M E.F.",
    "TOP: 4-25M",
    "4-25M x 3000 LG",
])
def test_a_rebar_callout_is_no_quantity(text):
    assert _readings(text) == []


@pytest.mark.parametrize("text, expected", [
    ("PILE TIP 15-20M BELOW EXISTING GRADE", (15.0, 20.0, "m")),
    ("BEDROCK EXPECTED AT 5-10M DEPTH", (5.0, 10.0, "m")),
    ("SETBACK 2-25M FROM PROPERTY LINE", (2.0, 25.0, "m")),
    ("PILES 10-15M LONG", (10.0, 15.0, "m")),
])
def test_a_range_in_capital_metres_with_no_rebar_word_is_a_length(
        text, expected):
    """PL-39 review: a count and a bar size alone matched these, and a
    length range stated on a drawing lettered in capitals was dropped."""
    assert [r[:3] for r in _readings(text)] == [expected]


def test_a_rebar_callout_leaves_the_values_beside_it_alone():
    got = _readings("FOOTING 1500 mm WIDE, 10-20M EACH WAY, TOP EL. 98.50")
    assert (1500.0, None, "mm", "1500 mm") in got
    assert all(r[2] != "m" for r in got)
    assert any(r[3].startswith("EL.") for r in got)


@pytest.mark.parametrize("text, expected", [
    ("pile length 10-20 m", (10.0, 20.0, "m", "10-20 m")),
    ("bearing stratum at 10-20m depth", (10.0, 20.0, "m", "10-20m")),
    ("10 to 20 M", (10.0, 20.0, "m", "10 to 20 M")),
    ("6-12M", (6.0, 12.0, "m", "6-12M")),      # 12 is no bar size
])
def test_a_length_range_in_metres_is_still_one(text, expected):
    assert _readings(text) == [expected]
