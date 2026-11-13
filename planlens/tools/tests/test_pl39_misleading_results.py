"""PL-39 (1) and (3): what open_document, document_page_map and
search_document say about which pages to view and which a text search could
not read. Made-up pages and wording only."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.tools import ReviewToolkit  # noqa: E402

#: 24 x 36 in, a drawing size.
ARCH_D = (2592, 1728)
LETTER = (612, 792)

SHEETS = [("A-101", "FIRST FLOOR PLAN"), ("A-301", "WALL SECTIONS"),
          ("A-501", "DETAILS")]


def _title_block(page, number, title):
    w, h = page.rect.width, page.rect.height
    x = w - 300
    page.draw_rect(pymupdf.Rect(x - 10, h - 220, w - 20, h - 20), width=1)
    page.insert_text((x, h - 190), "MADE-UP ARCHITECTS", fontsize=12)
    page.insert_text((x, h - 160), "PROJECT: TEST BUILDING", fontsize=10)
    page.insert_text((x, h - 120), "SHEET TITLE", fontsize=8)
    page.insert_text((x, h - 105), title, fontsize=12)
    page.insert_text((x, h - 70), "SHEET NO.", fontsize=8)
    page.insert_text((x, h - 45), number, fontsize=22)


def _notes(page, lines):
    for n, line in enumerate(lines):
        page.insert_text((72, 100 + 16 * n), line, fontsize=10)


def build_set() -> bytes:
    """Three titled drawing-size sheets whose content classes them as text
    (notes, little linework), a letter page of prose, a letter page that is
    only a picture (a scan with no text layer), and a drawing-size sheet
    drawn with heavy linework (a drawing sheet by its content)."""
    doc = pymupdf.open()
    for number, title in SHEETS:
        page = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
        _notes(page, [f"GENERAL NOTE {k}: ALL WORK TO CONFORM TO THE "
                      f"MADE-UP SPECIFICATION SECTION {k:02d} 00 00 AND "
                      f"THE DETAILS ON THIS SHEET." for k in range(1, 30)]
               + ["SEE 7/A-501 FOR THE PARAPET DETAIL."])
        _title_block(page, number, title)
    prose = doc.new_page(width=LETTER[0], height=LETTER[1])
    _notes(prose, ["This made-up report page is prose. " * 2] * 30)
    scan = doc.new_page(width=LETTER[0], height=LETTER[1])
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 260), False)
    pix.set_rect(pix.irect, (200, 200, 200))
    scan.insert_image(scan.rect, pixmap=pix)
    drawing = doc.new_page(width=ARCH_D[0], height=ARCH_D[1])
    for k in range(400):
        drawing.draw_line((100 + 5 * k, 100), (100 + 5 * k, 1500), width=0.3)
    _title_block(drawing, "S-201", "FOUNDATION PLAN")
    data = doc.tobytes()
    doc.close()
    return data


TEXT_SHEETS, PROSE, SCAN, DRAWING = [0, 1, 2], 3, 4, 5


@pytest.fixture(scope="module")
def pdf():
    return build_set()


def _kit(pdf, **kw):
    return ReviewToolkit(resolve_source=lambda key: pdf,
                         vision_hint="look_at(source, page)", **kw)


def call(kit, name, **args):
    return json.loads(kit.call_json(name, args))


def _pages(spec):
    out = []
    for part in str(spec).split(","):
        if "-" in part:
            a, b = part.split("-")
            out.extend(range(int(a), int(b) + 1))
        elif part.strip():
            out.append(int(part))
    return out


def test_the_made_up_set_is_what_the_test_says(pdf):
    kit = _kit(pdf)
    h = call(kit, "open_document", source="set.pdf")["handle"]
    rows = {r["page"]: r for r in
            call(kit, "document_page_map", handle=h)["rows"]}
    for p in TEXT_SHEETS:
        assert rows[p]["kind"] in ("text", "mixed")
    assert rows[PROSE]["kind"] == "text"
    assert rows[SCAN]["kind"] == "scanned"
    assert rows[DRAWING]["kind"] == "drawing_sheet"


# -- (1) pages_to_view, look --------------------------------------------------

def test_pages_to_view_holds_every_drawing_size_sheet(pdf):
    kit = _kit(pdf)
    out = call(kit, "open_document", source="set.pdf")
    view = _pages(out["pages_to_view"])
    assert view == TEXT_SHEETS + [SCAN, DRAWING]
    assert PROSE not in view
    note = out["pages_to_view_note"]
    assert "11 x 17 in or larger" in note
    assert "text or mixed" in note
    assert "look_at(source, page)" in note


def test_pages_to_view_holds_every_sheet_the_index_finds(pdf):
    kit = _kit(pdf, sheet_tools=True)
    out = call(kit, "open_document", source="set.pdf")
    index = call(kit, "sheet_index", handle=out["handle"])
    indexed = {r["page"] for r in index["sheets"]}
    assert indexed == set(TEXT_SHEETS + [DRAWING])
    assert indexed <= set(_pages(out["pages_to_view"]))


def test_the_page_map_marks_the_same_pages_look(pdf):
    kit = _kit(pdf)
    out = call(kit, "open_document", source="set.pdf")
    pm = call(kit, "document_page_map", handle=out["handle"])
    look = [r["page"] for r in pm["rows"] if r.get("look")]
    assert look == _pages(out["pages_to_view"])
    assert "11 x 17 in or larger" in pm["look"]


# -- (3) search_document ------------------------------------------------------

def test_no_page_with_a_text_layer_or_a_hit_is_called_unsearchable(pdf):
    kit = _kit(pdf)
    h = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "search_document", handle=h, pattern="7/A-501")
    assert sorted(int(p) for p in out["pages_with_hits"]) == TEXT_SHEETS
    assert _pages(out["pages_not_searchable_as_text"]) == [SCAN]


def test_a_miss_names_only_the_pages_without_a_text_layer(pdf):
    kit = _kit(pdf)
    h = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "search_document", handle=h, pattern="NO SUCH WORDING")
    assert out["n_hits"] == 0
    assert _pages(out["pages_not_searchable_as_text"]) == [SCAN]
    assert "no usable text layer" in out["hint"]


def test_no_list_when_every_page_searched_has_a_text_layer(pdf):
    kit = _kit(pdf)
    h = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "search_document", handle=h, pattern="SHEET NO",
               pages=f"0-{PROSE}")
    assert out["n_hits"] > 0
    assert "pages_not_searchable_as_text" not in out


def test_a_literal_bar_is_said_to_mean_or_only_with_regex(pdf):
    kit = _kit(pdf)
    h = call(kit, "open_document", source="set.pdf")["handle"]
    out = call(kit, "search_document", handle=h, pattern="A-101|A-301")
    assert out["n_hits"] == 0
    assert out["hint"].startswith('the pattern holds "|"')
    assert 'regex=true, which reads "|" as "or"' in out["hint"]
    as_or = call(kit, "search_document", handle=h, pattern="A-101|A-301",
                 regex=True)
    assert as_or["n_hits"] >= 2
    assert '"|"' not in as_or.get("hint", "")
    plain = call(kit, "search_document", handle=h, pattern="NO SUCH WORDING")
    assert '"|"' not in plain.get("hint", "")
