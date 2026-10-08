"""Things on a page that look like a scale and are not, and grids that are.

Each case here is a general shape met on real scanned reports, drawn
synthetically: a table's row of figures over its column rules, a chart's
axis over its gridlines, a line of text read as a dashed rule, a log form's
ruled boxes, a stray blob beside an axis's labels, an axis label by the far
side of a corner, gridlines pixel-snapped by the program that drew them, a
log axis whose window guessed its decade a little short, and a chart frame
whose sides a scan broke into pieces.
"""

import fitz
import pytest

from planlens.document import Document
from planlens.document import raster as R
from planlens.document.scalefinder import (
    _collinear, _side_cover, find_scales,
)
from planlens.document.scales import index_by_spacing, log_decades
from planlens.testing.visual_scale_fixtures import (
    LogVariant, build_log, scanned,
)

A4 = (595.0, 842.0)


def _pdf(draw) -> bytes:
    d = fitz.open()
    p = d.new_page(width=A4[0], height=A4[1])
    draw(p)
    return d.tobytes()


def _table(p):
    """A sieve table: a row of sizes over a row of figures, every cell
    ruled, the rules running the table's full height."""
    x0, y0, w, rows = 80.0, 300.0, 34.0, 3
    xs = [x0 + w * k for k in range(13)]
    for x in xs:
        p.draw_line((x, y0), (x, y0 + 14.0 * rows), width=0.8)
    for r in range(rows + 1):
        p.draw_line((xs[0], y0 + 14.0 * r), (xs[-1], y0 + 14.0 * r),
                    width=0.8)
    sizes = ["0.063", "0.150", "0.212", "0.300", "0.425", "0.600", "1.18",
             "2.00", "3.35", "5.0", "6.3", "10.0"]
    for k, t in enumerate(sizes):
        p.insert_text((xs[k] + 6, y0 + 10.5), t, fontsize=8)
        p.insert_text((xs[k] + 8, y0 + 24.5), str(10 * k), fontsize=8)


def _axis_chart(p):
    """A small chart whose x labels stand under its frame: the frame's
    bottom rule runs from the first label to the last like a bar's base,
    and the gridlines stand on it like a bar's ticks."""
    x0, y0, x1, y1 = 160.0, 400.0, 380.0, 520.0
    for k in range(8):
        x = x0 + (x1 - x0) * k / 7.0
        p.draw_line((x, y0), (x, y1), width=0.5)
        p.insert_text((x - 2.2, y1 + 9.0), str(k), fontsize=8)
    for k in range(7):
        y = y1 - (y1 - y0) * k / 6.0
        p.draw_line((x0, y), (x1, y), width=0.5)
        p.insert_text((x0 - 14.0, y + 2.5), f"{0.5 * k:.1f}", fontsize=8)
    p.insert_text((230, y1 + 22), "Penetration (mm)", fontsize=8)


def _strip(p):
    """Labels 0..7 under vertical rules that run up the whole strip (a
    time strip, a column chart's guides): no grid the other way, so no
    chart claims them first, and each label stands on a long rule."""
    x0, y0, x1, y1 = 160.0, 400.0, 380.0, 520.0
    for k in range(8):
        x = x0 + (x1 - x0) * k / 7.0
        p.draw_line((x, y0), (x, y1), width=0.5)
        p.insert_text((x - 2.2, y1 + 9.0), str(k), fontsize=8)
    p.draw_line((x0, y1), (x1, y1), width=0.5)


def test_labels_standing_on_long_rules_are_not_a_scale_bar():
    ps = find_scales(Document(content=_pdf(_strip)), 0)
    assert ps.get("p0.plan.bar") is None


@pytest.mark.parametrize("raster", [False, True])
def test_a_tables_row_of_figures_is_not_a_scale_bar(raster):
    pdf = _pdf(_table)
    if raster:
        pdf = scanned(pdf, A4, skew_deg=0.3, seed=3)
    ps = find_scales(Document(content=pdf), 0)
    assert ps.get("p0.plan.bar") is None


def test_a_charts_axis_is_the_charts_not_a_scale_bar():
    ps = find_scales(Document(content=_pdf(_axis_chart)), 0)
    assert ps.get("p0.plan.bar") is None
    x = ps.get("p0.plot1.x")
    assert x is not None and x.usable
    assert x.b == pytest.approx(7.0 / 220.0, rel=1e-3)
    assert x.quantity == "penetration" and x.unit == "mm"


def test_a_label_by_the_far_side_of_a_corner_stays_out_of_the_run():
    """The y axis's top label sits just above the frame's top-left corner,
    level with nothing on the x axis: the x run is the labels below."""
    ps = find_scales(Document(content=_pdf(_axis_chart)), 0)
    x = ps.get("p0.plot1.x")
    assert len(x.label_boxes) == 8
    assert all(b[1] > 515 for b in x.label_boxes)


def test_a_scanned_log_form_is_a_log_and_nothing_else():
    """Its ruled boxes are regular one way at most, its header words space
    out evenly by chance, its title is underlined: no plot, no bar."""
    for v in (LogVariant("ff_log_a", skew_deg=0.4, seed=91),
              LogVariant("ff_log_b", skew_deg=-0.6, ticks=True, seed=92)):
        ps = find_scales(build_log(v).open(), 0)
        assert [f.kind for f in ps.frames] == ["log"], v.name


def test_a_line_of_text_on_a_scan_is_not_a_dashed_rule():
    def draw(p):
        p.insert_text((80, 300), "Stiff mottled CLAY with occasional "
                                 "rootlets and gravel bands", fontsize=9)
        p.draw_line((80, 360), (420, 360), width=0.8, dashes="[6 4] 0")
    pdf = scanned(_pdf(draw), A4, skew_deg=0.4, seed=5)
    ras = R.render(Document(content=pdf)._doc[0], R.DEFAULT_DPI)
    lines, _ = R.find_lines(ras, orientation="h")
    dashed = [L for L in lines if L.dashed]
    assert any(abs(L.at(250.0) - 360.0) < 2.0 for L in dashed)
    assert not any(abs(L.at(250.0) - 297.0) < 6.0 for L in dashed)


def test_pixel_snapped_gridlines_are_all_indexed():
    """A program that snaps lines to device pixels draws a 6.66 pt grid
    as 6.0 / 6.7 / 6.8 pt steps; counted from the first line with a step a
    little off, the far lines drift off the grid unless indexed again from
    the fitted line."""
    pos = [571.9, 577.9, 584.6, 591.4, 598.1, 604.9, 611.6, 618.3, 625.1,
           631.1, 637.8, 644.6, 651.3, 658.1, 664.8, 671.5, 678.3, 685.0,
           691.0]
    idx = index_by_spacing(pos)
    assert idx["index"] == list(range(19))
    assert idx["residual"] < 0.5


def test_a_log_axis_places_each_line_once():
    """The 90 line stands 3 pt from the 100 line at 71 pt a decade; a
    decade guessed a little short from one window puts both on 100."""
    import math
    dec, x0 = 71.3, 139.0
    pos = []
    for d in range(5):
        for k in range(1, 10):
            pos.append(x0 + dec * (d + math.log10(k)))
    pos.append(x0 + 5 * dec)
    # the scan stretched the first decade a little
    pos = [p - 0.4 if p < x0 + dec else p for p in pos]
    out = log_decades(pos)
    places = [q for q in out["places"] if q is not None]
    assert len(places) == len(set(places)) == 46
    assert out["residual"] < 0.8


def test_a_scanned_rule_in_pieces_is_one_rule():
    spans = [(633.8, 140.0, 467.0), (633.7, 452.0, 497.0),
             (617.2, 139.0, 439.0), (617.1, 417.0, 497.0),
             (600.0, 139.0, 300.0), (600.1, 320.0, 497.0)]
    got = sorted(_collinear(spans, gap=8.0))
    assert [(round(p), round(a), round(b)) for p, a, b in got] == [
        (600, 139, 300), (600, 320, 497), (617, 139, 497), (634, 140, 497)]
    # a side drawn in two overlapping pieces covers its whole height
    sides = [(496.0, 458.0, 602.0), (496.1, 583.0, 635.0)]
    assert _side_cover(sides, 496.0, 458.0, 635.0) == pytest.approx(1.0)
    assert _side_cover([(139.2, 485.0, 635.0)], 139.0, 458.0, 635.0) \
        == pytest.approx(150.0 / 177.0)
