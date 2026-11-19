"""Review of the three pieces merged after 0.17.0, read as one change: the
reading options for a host that asks (PL-09), the printed scale in the sheet
index's rows (PL-37) and the region tiles (VIS-30, not wired).

What no single review could run: the sheet index with its scales on a set
whose pages are read with each reading option on and off, by hosts that
share one conversation; the region tiles on those sheets. One finding is
fixed here (the index's own note did not say what ``scale`` is).

A made-up set only: six 36 x 24 in sheets of a made-up firm, the title block
down the right edge with a scale cell; general notes in two columns drawn
row by row; one ruled schedule that runs on to the next sheet.
"""

import itertools
import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review                                   # noqa: E402
from planlens.document.budget import resolve_budget           # noqa: E402
from planlens.document.document import Document               # noqa: E402
from planlens.review import frames                            # noqa: E402
from planlens.review.host import ReviewHost                   # noqa: E402
from planlens.tools.toolkit import READING_OPTIONS, ReviewToolkit  # noqa: E402

W, H = 36 * 72.0, 24 * 72.0
BX = W - 280.0
TITLES = ("GENERAL NOTES", "PILE SCHEDULE", "PILE SCHEDULE CONTD",
          "GRADE BEAM PLAN", "PIER DETAILS", "DRAIN SECTIONS")
SCALES = ("NTS", "AS NOTED", "AS NOTED", '1/4" = 1\'-0"', "1:20", None)
WORDS = ("the gravel fill was placed in thin lifts and rolled until the "
         "made-up gauge read the figure the specification asks for").split()
COLS = (150, 330, 510, 690, 1000)
WARNING = "! reading order not reconstructed"


def _block(page, i, scale):
    def t(at, text, size):
        page.insert_text(at, text, fontsize=size)
    page.draw_line((BX - 20, 40), (BX - 20, H - 40))
    t((BX, 150), "QUARRY BEND DESIGN GROUP", 11)
    t((BX, 190), "JOB NAME", 6)
    t((BX, 210), "MILLRACE STORAGE SHED", 13)
    t((BX, H - 360), "DRAWING TITLE", 6)
    t((BX, H - 338), TITLES[i], 13)
    t((BX, H - 260), "SCALE", 6)
    if scale:
        t((BX, H - 244), scale, 8)
    t((BX, H - 120), "DRAWING NO.", 6)
    t((BX, H - 80), f"G-{301 + i}", 28)


def _table(page, top, first, n):
    grid = [("Pile", "Depth (m)", "Load (kN)", "Notes")]
    grid += [(f"P-{k}", f"{k}.2", f"{100 + k}", "driven")
             for k in range(first, first + n)]
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            page.insert_text((COLS[c] + 4, top + 18 * r + 13), text,
                             fontsize=9)
    for r in range(len(grid) + 1):
        page.draw_line((COLS[0], top + 18 * r), (COLS[-1], top + 18 * r))
    for x in COLS:
        page.draw_line((x, top), (x, top + 18 * len(grid)))


def _made_up(scales=SCALES) -> bytes:
    doc = pymupdf.open()
    for i in range(6):
        page = doc.new_page(width=W, height=H)
        page.draw_rect((40, 40, W - 40, H - 40))
        if i == 0:
            page.insert_text((150, 120), "AN INVENTED HEADING ACROSS BOTH "
                             "COLUMNS", fontsize=14)
            for r in range(90):
                for c in range(2):
                    words = " ".join(WORDS[(r + 3 * c + k) % len(WORDS)]
                                     for k in range(14))
                    page.insert_text((150 + 1100 * c, 170 + 14 * r),
                                     f"k{c}w{r} {words}", fontsize=8)
        elif i == 1:
            page.insert_text((150, 150), "Table 6: Made-up pile schedule",
                             fontsize=10)
            _table(page, 170, 1, 75)
        elif i == 2:
            _table(page, 170, 76, 10)
        else:
            for v in range(3):
                x = 200 + 700 * v
                page.draw_rect((x, 300, x + 500, 900))
                page.insert_text((x + 20, 950), f"VIEW {v + 1}", fontsize=12)
                page.insert_text((x + 20, 968), "SCALE: 1:50", fontsize=8)
                for k in range(12):
                    page.insert_text(
                        (x + 20, 340 + 40 * k),
                        f"note {k} on view {v + 1}: 450 mm gravel",
                        fontsize=6)
        _block(page, i, scales[i])
    data = doc.tobytes()
    doc.close()
    return data


@pytest.fixture(scope="module")
def pdf():
    return _made_up()


def _call(host, att, name, **arguments):
    return json.loads(review.call(name, arguments, att, 12000, host=host))


def _index(kit):
    handle = kit.call("open_document", {"source": "set.pdf"})["handle"]
    return kit.call("sheet_index", {"handle": handle})


# --- the finding fixed here -------------------------------------------------

def test_the_index_says_what_a_rows_scale_is(pdf):
    # the result's note names every key of a row (page, sheet, title, rev,
    # views; from and duplicate where a row has one) but not the two the
    # scale added: nothing in the result said the scale is the title
    # block's and never a view's, or what scale_from is
    kit = ReviewToolkit(resolve_source=lambda s: pdf, sheet_tools=True)
    out = _index(kit)
    kit.close()
    assert [r.get("scale") for r in out["sheets"]] == list(SCALES)
    assert "scale: " in out["note"] and "scale_from" in out["note"]
    assert "title block" in out["note"].split("scale: ")[1]


def test_a_set_that_prints_no_scale_has_the_note_it_had():
    plain = _made_up((None,) * 6)
    kit = ReviewToolkit(resolve_source=lambda s: plain, sheet_tools=True)
    out = _index(kit)
    kit.close()
    assert not any("scale" in r for r in out["sheets"])
    assert out["note"] == (
        "page is the 0-based PDF page these tools take; sheet, title and "
        "rev are as the sheet's own title block prints them; views are the "
        "view marks recognised on the sheet")


# --- the three pieces together (they hold before and after the fix) ---------

def test_the_index_and_its_scales_are_the_same_under_every_reading_option(
        pdf):
    results = {}
    for asked in itertools.product([False, True], repeat=3):
        options = dict(zip(READING_OPTIONS, asked))
        kit = ReviewToolkit(resolve_source=lambda s: pdf, sheet_tools=True,
                            **options)
        handle = kit.call("open_document", {"source": "set.pdf"})["handle"]
        index = json.dumps(kit.call("sheet_index", {"handle": handle}))
        notes = kit.call("read_document", {"handle": handle,
                                           "sheets": "G-301"})["text"]
        # the option is at work on this very set, and the index is not moved
        assert (WARNING in notes) == options["column_warning"]
        results[asked] = index.replace(handle, "H")
        kit.close()
    assert len(set(results.values())) == 1
    rows = json.loads(results[(False,) * 3])["sheets"]
    assert [r.get("scale") for r in rows] == list(SCALES)


def test_hosts_of_one_conversation_read_one_index_and_their_own_pages(pdf):
    att = {"set.pdf": pdf}
    asked = ReviewHost(toolkit_options=dict(
        dict.fromkeys(READING_OPTIONS, True), sheet_tools=True))
    sheets_only = ReviewHost(toolkit_options={"sheet_tools": True})
    plain = ReviewHost()
    mine = {id(h): _call(h, att, "open_document", source="set.pdf")["handle"]
            for h in (asked, sheets_only, plain)}
    rows = []
    for host in (asked, sheets_only):
        for handle in set(mine.values()):      # its own and the other's
            out = _call(host, att, "sheet_index", handle=handle)
            rows.append(out["sheets"])
            by_sheet = _call(host, att, "read_document", handle=handle,
                             sheets="G-301")
            assert (WARNING in by_sheet["text"]) == (host is asked)
            tables = _call(host, att, "read_document", handle=handle,
                           sheets="G-302, G-303", include_tables=True)
            assert ("continues" in tables["text"]) == (host is asked)
    assert all(r == rows[0] for r in rows)
    assert [r.get("scale") for r in rows[0]] == list(SCALES)
    assert "unknown tool" in _call(plain, att, "sheet_index",
                                   handle=mine[id(asked)])["error"]
    page = _call(plain, att, "read_document", handle=mine[id(asked)],
                 pages="0")
    assert WARNING not in page["text"]


def test_region_tiles_and_the_index_do_not_move_each_other(pdf):
    budget = resolve_budget("openai-high")

    def tiles(doc):
        return [tuple(frames.region_tiles(frames.page_marks(doc, p), budget))
                for p in range(6)]

    seen = []
    for column_warning, continued_tables in itertools.product(
            [False, True], repeat=2):
        with Document(content=pdf, name="set.pdf",
                      column_warning=column_warning,
                      continued_tables=continued_tables) as doc:
            before = tiles(doc)
            rows = [r.to_dict() for r in doc.sheet_index().rows]
            assert tiles(doc) == before
        with Document(content=pdf, name="set.pdf") as doc:
            assert [r.to_dict() for r in doc.sheet_index().rows] == rows
        seen.append((before, rows))
    assert all(s == seen[0] for s in seen)
    assert [r.get("scale") for r in seen[0][1]] == list(SCALES)
