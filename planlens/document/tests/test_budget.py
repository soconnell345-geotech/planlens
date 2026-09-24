"""Image budgets: the render is the picture the model looks at, to the pixel.

Pinned: the published limits themselves (so a change to them is a decision,
not drift), that :func:`fit_size` gives the LARGEST size a budget holds, that
a box read off an image in either convention maps back to the right points,
and that ``Document.render`` with a budget lands inside it while filling it.
"""

import pytest

from planlens.document.budget import (
    BUDGETS, ImageBudget, fit_size, image_box_to_page, resolve_budget,
)

ASPECTS = [(612, 792), (792, 612), (2592, 1728), (1224, 792), (100, 100),
           (40, 900), (3000, 211)]


def test_the_published_limits():
    b = BUDGETS
    assert (b["openai-high"].max_edge, b["openai-high"].unit,
            b["openai-high"].max_units, b["openai-high"].detail) == (2048, 32, 2500, "high")
    assert (b["openai-original"].max_edge, b["openai-original"].max_units,
            b["openai-original"].detail) == (6000, 10000, "original")
    assert (b["gpt-4.1-high"].max_edge, b["gpt-4.1-high"].short_side) == (2048, 768)
    assert (b["claude"].max_edge, b["claude"].unit, b["claude"].max_units) == (1568, 28, 1568)
    assert (b["claude-hires"].max_edge, b["claude-hires"].max_units) == (2576, 4784)
    # OpenAI advises a 0-999 grid; Anthropic advises pixels.
    assert b["openai-original"].box_units == "norm1000"
    assert b["claude"].box_units == "px"


@pytest.mark.parametrize("name", sorted(BUDGETS))
@pytest.mark.parametrize("w,h", ASPECTS)
def test_fit_size_is_the_largest_size_the_budget_holds(name, w, h):
    bud = BUDGETS[name]
    fw, fh = fit_size(w, h, bud)
    assert bud.fits(fw, fh)
    assert abs(fw / fh - w / h) / (w / h) < 0.02 or min(fw, fh) < 60
    # One more pixel on the long side (short side following the aspect)
    # no longer fits: nothing larger was left on the table.
    if w >= h:
        bigger = (fw + 1, int((fw + 1) * h / w))
    else:
        bigger = (int((fh + 1) * w / h), fh + 1)
    assert not bud.fits(*bigger)


def test_fit_size_grows_a_small_source():
    # A render can be made at any size, so a small region is blown UP.
    assert fit_size(50, 50, BUDGETS["openai-high"]) == (1600, 1600)


def test_patch_counting():
    bud = BUDGETS["openai-high"]
    assert bud.units(1600, 1600) == 2500 and bud.fits(1600, 1600)
    assert bud.units(1601, 1600) == 51 * 50 and not bud.fits(1601, 1600)


def test_resolve_budget():
    assert resolve_budget(None) is None
    assert resolve_budget("claude") is BUDGETS["claude"]
    custom = ImageBudget("mine", max_edge=1000, max_units=900)
    assert resolve_budget(custom) is custom
    with pytest.raises(ValueError, match="unknown image budget"):
        resolve_budget("gpt-9")


def test_image_box_to_page_pixels_and_grid():
    clip = (100.0, 200.0, 500.0, 400.0)          # 400 x 200 pt
    # 800 x 400 px image: 2 px per point.
    assert image_box_to_page([0, 0, 800, 400], clip, 800, 400) == clip
    assert image_box_to_page([200, 100, 400, 300], clip, 800, 400) == (
        200.0, 250.0, 300.0, 350.0)
    # The 0-999 grid does not care what size the image was looked at.
    assert image_box_to_page([0, 0, 999, 999], clip, 800, 400,
                             units="norm1000") == clip
    x0, y0, x1, y1 = image_box_to_page([499.5, 499.5, 999, 999], clip, 1, 1,
                                       units="norm1000")
    assert (round(x0, 6), round(y0, 6)) == (300.0, 300.0)


def test_image_box_to_page_orders_and_clamps():
    clip = (0.0, 0.0, 100.0, 100.0)
    assert image_box_to_page([90, 90, -10, 10], clip, 100, 100) == (
        0.0, 10.0, 90.0, 90.0)
    with pytest.raises(ValueError, match="units"):
        image_box_to_page([0, 0, 1, 1], clip, 10, 10, units="inches")
    with pytest.raises(ValueError, match="box must be"):
        image_box_to_page([0, 0, 1], clip, 10, 10)


# -- Document.render with a budget ------------------------------------------------

fitz = pytest.importorskip("fitz")


@pytest.fixture(scope="module")
def gt():
    from planlens.testing import build_synthetic_review_document
    return build_synthetic_review_document()


@pytest.fixture
def doc(gt):
    from planlens.document import Document
    d = Document(content=gt.pdf)
    yield d
    d.close()


@pytest.mark.parametrize("name", sorted(BUDGETS))
def test_a_page_render_fills_its_budget_exactly(doc, gt, name):
    bud = BUDGETS[name]
    data, info = doc.render(gt.sheet_page, budget=name)
    w, h = info["width_px"], info["height_px"]
    assert bud.fits(w, h)
    fw, fh = fit_size(*_clip_size(info), bud)
    assert fw - w <= 3 and fh - h <= 3             # filled, to the pixel or so
    pix = fitz.Pixmap(data)                        # the bytes ARE that size
    assert (pix.width, pix.height) == (w, h)
    assert info["budget"] == name


def test_a_region_is_redrawn_to_fill_the_budget(doc, gt):
    tx, ty = gt.reviewer_target
    box = (tx - 200, ty - 150, tx + 200, ty + 150)
    plain_png, plain = doc.render(gt.sheet_page, bbox=box)
    data, info = doc.render(gt.sheet_page, bbox=box, budget="openai-original")
    assert plain["dpi"] == 200.0
    assert info["dpi"] > 2.5 * plain["dpi"]
    assert BUDGETS["openai-original"].fits(info["width_px"], info["height_px"])
    assert info["clip"] == plain["clip"]


def test_a_given_dpi_is_lowered_to_fit_the_budget(doc, gt):
    data, info = doc.render(gt.sheet_page, dpi=400, budget="claude")
    assert info["dpi"] < 400
    assert BUDGETS["claude"].fits(info["width_px"], info["height_px"])
    small, sinfo = doc.render(gt.sheet_page, dpi=20, budget="claude")
    assert sinfo["dpi"] == 20.0                    # under the budget: as asked


def test_a_tiny_region_stops_at_the_dpi_ceiling(doc, gt):
    from planlens.document.document import MAX_RENDER_DPI
    tx, ty = gt.reviewer_target
    data, info = doc.render(gt.sheet_page, bbox=(tx - 2, ty - 2, tx + 2, ty + 2),
                            budget="openai-original", pad_frac=0.0)
    assert info["dpi"] == MAX_RENDER_DPI


def test_jpeg_output(doc, gt):
    data, info = doc.render(gt.sheet_page, budget="openai-high", fmt="jpeg")
    assert data[:3] == bytes([0xFF, 0xD8, 0xFF]) and info["format"] == "jpeg"
    png, pinfo = doc.render(gt.sheet_page, budget="openai-high")
    assert png[:4] == b"\x89PNG" and pinfo["format"] == "png"
    with pytest.raises(ValueError, match="fmt"):
        doc.render(gt.sheet_page, fmt="gif")


def test_auto_keeps_png_for_line_art_and_jpeg_for_a_scan(doc, gt):
    # Measured 2026-09-23: on a vector sheet (white paper, thin lines) PNG
    # is about HALF the size of a q92 JPEG — the cookbook's "JPEG is smaller"
    # holds for scans and photos, not for clean drawings.
    data, info = doc.render(gt.sheet_page, budget="openai-high", fmt="auto")
    assert info["format"] == "png" and data[:4] == b"\x89PNG"
    scan, sinfo = doc.render(gt.scanned_page, budget="openai-high", fmt="auto")
    jpeg, _ = doc.render(gt.scanned_page, budget="openai-high", fmt="jpeg")
    png, _ = doc.render(gt.scanned_page, budget="openai-high", fmt="png")
    assert len(scan) == min(len(jpeg), len(png))
    assert sinfo["format"] == ("jpeg" if len(jpeg) < len(png) else "png")


def test_marks_render_at_the_budget(doc, gt):
    tx, ty = gt.reviewer_target
    data, info = doc.render(gt.sheet_page, bbox=(tx - 40, ty - 40, tx + 40, ty + 40),
                            marks=[(tx, ty, "A")], budget="openai-high")
    pix = fitz.Pixmap(data)
    assert (pix.width, pix.height) == (info["width_px"], info["height_px"])
    s, n = pix.samples, pix.n
    reds = sum(1 for i in range(0, len(s), n)
               if s[i] > 200 and s[i + 1] < 80 and s[i + 2] < 80)
    assert reds > 50


def test_without_a_budget_the_page_is_the_size_it_says(doc, gt):
    data, info = doc.render(gt.sheet_page)
    pix = fitz.Pixmap(data)
    assert max(pix.width, pix.height) == 2000
    assert (pix.width, pix.height) == (info["width_px"], info["height_px"])
    assert "budget" not in info


def _clip_size(info):
    x0, y0, x1, y1 = info["clip"]
    return x1 - x0, y1 - y0


# -- which budget a model needs: by name, and by measurement ------------------------

from planlens.document.budget import (  # noqa: E402
    budget_for_model, budget_from_probe, legible_window,
)


def test_the_gpt_5_2_budget():
    b = BUDGETS["gpt-5.2-high"]
    assert (b.max_edge, b.unit, b.max_units, b.box_units) == (2048, 32, 6144, "norm1000")


@pytest.mark.parametrize("model,general,detailed", [
    ("gpt-5.1-2025-11-13", "gpt-4.1-high", "gpt-4.1-high"),    # Tiny Apps, measured
    ("gpt-5.4-2026-03-05", "openai-high", "openai-original"),  # funhouse-gpt-high
    ("gpt-5.6-sol", "openai-high", "openai-original"),
    ("gpt-5.2", "gpt-5.2-high", "gpt-5.2-high"),
    ("gpt-4.1-mini-2025-04-14", "gpt-5.2-high", "gpt-5.2-high"),
    ("gpt-4.1-2025-04-14", "gpt-4.1-high", "gpt-4.1-high"),
    ("gpt-4o", "gpt-4.1-high", "gpt-4.1-high"),
    ("claude-sonnet-5", "claude-hires", "claude-hires"),
    ("claude-haiku-4-5", "claude", "claude"),
])
def test_budget_for_model(model, general, detailed):
    g, d = budget_for_model(model)
    assert (g.name, d.name) == (general, detailed)


def test_an_alias_names_no_model():
    assert budget_for_model("tinyapp-gpt-medium") is None
    assert budget_for_model("funhouse-gpt-high") is None
    assert budget_for_model(None) is None


def test_budget_from_probe_reads_the_measured_patterns():
    # GPT-5.1 on Tiny Apps, 2026-09-24: 630 tokens for every image, and
    # detail="original" accepted but ignored.
    g, d, r = budget_from_probe(630, 630, 630)
    assert (g.name, d.name, r["high"]) == ("gpt-4.1-high", "gpt-4.1-high", 1.0)
    # A 2,500-patch model with original honoured (GPT-5.4): any multiplier.
    for m in (1.0, 1.62):
        g, d, r = budget_from_probe(1024 * m, 2500 * m, 4096 * m)
        assert (g.name, d.name) == ("openai-high", "openai-original")
    # The same model with original ignored stays at high for charts too.
    g, d, _ = budget_from_probe(1024, 2500, 2500)
    assert (g.name, d.name) == ("openai-high", "openai-high")
    # A 6,144-patch model takes the 2048 square whole.
    g, d, _ = budget_from_probe(1024, 4096, None)
    assert (g.name, d.name) == ("gpt-5.2-high", "gpt-5.2-high")
    with pytest.raises(ValueError):
        budget_from_probe(0, 630)


def test_legible_window():
    # 5 pt lettering at 14 px on a 768 px square: 274 pt across.
    assert round(legible_window(5, BUDGETS["gpt-4.1-high"])) == 274
    assert legible_window(5, BUDGETS["openai-high"]) > 2 * legible_window(
        5, BUDGETS["gpt-4.1-high"])
    with pytest.raises(ValueError):
        legible_window(0, BUDGETS["claude"])


def test_text_size_and_text_px(doc, gt):
    size = doc.text_size(gt.sheet_page)
    assert size is not None and size > 0
    data, info = doc.render(gt.sheet_page, budget="gpt-4.1-high")
    assert info["text_px"] == pytest.approx(size * info["dpi"] / 72.0, abs=0.1)
    # A scanned page has no text to measure: no text_px, not a zero.
    assert doc.text_size(gt.scanned_page) is None
    _, sinfo = doc.render(gt.scanned_page, budget="gpt-4.1-high")
    assert "text_px" not in sinfo


def test_text_size_weights_lines_by_their_characters():
    import fitz as _fitz
    from planlens.document import Document
    pdf = _fitz.open()
    page = pdf.new_page(width=612, height=792)
    page.insert_text((72, 72), "TITLE", fontsize=24)
    for i in range(12):
        page.insert_text((72, 120 + 14 * i), "#5 BARS @ 12 IN. MAX. SPACING",
                         fontsize=5)
    data = pdf.tobytes()
    pdf.close()
    d = Document(content=data)
    try:
        assert d.text_size(0) == pytest.approx(5.0, abs=0.2)
        assert d.text_size(0, quantile=1.0) == pytest.approx(24.0, abs=0.5)
    finally:
        d.close()
