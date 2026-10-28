"""``log_grid``'s raster leg, and its labels tied to ticks on vector logs.

A scanned log used to come back empty ("no text to place") and, even with
OCR text, without a single stratum line. Now the pixels give the columns,
the ruler (values from text, or from the caller where there is none), the
anchor rule and every stratum line, each layer top with its +/-.
"""

import pytest

from planlens.document.loggrid import log_grid
from planlens.testing import build_imperial_log, build_metric_log
from planlens.testing.visual_scale_fixtures import LogVariant, build_log


def _read(fx):
    doc = fx.open()
    g = log_grid(doc, [0])
    if g.needs_values:
        g = log_grid(doc, [0], values={0: fx.values_for(
            g.needs_values[0]["labels"])})
    return g


def _tops(g):
    return [ly for ly in g.layers if ly.source == "stratum_rule"]


def test_a_scan_with_no_text_first_asks_for_its_label_values():
    fx = build_log(LogVariant("lg_nv", skew_deg=0.4, seed=61))
    g = log_grid(fx.open(), [0])
    assert 0 in g.needs_values
    assert len(g.needs_values[0]["labels"]) == len(fx.labels)
    assert not g.rulers
    assert any("values=" in w for w in g.warnings)
    assert "needs_values" in g.to_dict(rows=False)


@pytest.mark.parametrize("variant", [
    LogVariant("lg_base", skew_deg=0.4, seed=62),
    LogVariant("lg_dashed", dashed=True, skew_deg=-0.9, seed=63),
    LogVariant("lg_rot270_inside", rotate270=True, ruler="inside",
               skew_deg=0.3, seed=64),
    LogVariant("lg_continuation", start=10.0, contacts=(2.86, 4.28, 7.7),
               skew_deg=-0.2, seed=65),
    LogVariant("lg_feet", unit="ft", step=5.0, n_steps=8, pt_per_step=56.0,
               contacts=(6.2, 17.5, 29.0), label_anchor="centre",
               skew_deg=0.6, seed=66),
])
def test_every_stratum_line_on_a_scan_is_a_layer_top(variant):
    fx = build_log(variant)
    g = _read(fx)
    tops = _tops(g)
    assert len(tops) == len(fx.readings)
    for rd, ly in zip(fx.readings, tops):
        assert ly.plus_minus is not None
        assert abs(ly.top - rd.value) <= ly.plus_minus, (rd.value, ly.top)
        assert ly.evidence.get("found_in") == "pixels"
        assert ly.evidence.get("dashed", False) == (rd.tag == "dashed")
    r = g.rulers[0]
    assert r.evidence["anchor_rule_kind"] in ("frames", "ticks")
    assert r.plus_minus is not None and r.plus_minus < 0.05 * variant.step


def test_the_columns_of_a_textless_scan_come_from_its_rules():
    fx = build_log(LogVariant("lg_cols", seed=67))
    g = _read(fx)
    names = [c.name for c in g.columns]
    assert names.count("depth") == 1 and names.count("description") == 1
    assert len(g.columns) == 7
    assert all(c.evidence.get("edges") == "pixels" for c in g.columns)


def test_ocr_text_names_the_columns_and_the_rules_place_them():
    fx = build_log(LogVariant("lg_ocr", text_source="azure_di", skew_deg=0.5,
                              seed=68))
    g = _read(fx)
    desc = next(c for c in g.columns if "description" in c.names)
    truth = fx.scales["depth"]["description"]
    assert desc.evidence.get("edges") == "pixels"
    assert abs(desc.x0 - truth[0]) < 4.0 and abs(desc.x1 - truth[2]) < 4.0
    for rd, ly in zip(fx.readings, _tops(g)):
        assert abs(ly.top - rd.value) <= ly.plus_minus
    # cells are placed through the scale, skew taken out
    sample = [c for c in g.rows if c.text.startswith("5 ")]
    assert sample and all(c.depth is not None for c in sample)


def test_a_vector_ruler_is_tied_to_the_ticks_beside_its_labels():
    fx = build_log(LogVariant("lg_vec_ticks", raster=False, ticks=True,
                              label_anchor="top", ruler="right"))
    g = log_grid(fx.open(), [0])
    r = g.rulers[0]
    assert r.evidence["anchor_rule_kind"] == "ticks"
    assert r.evidence["moved_by_pt"] > 1.0       # labels hang below
    for rd, ly in zip(fx.readings, _tops(g)):
        assert ly.top == pytest.approx(rd.value, abs=0.003)


def test_a_vector_ruler_off_its_frames_is_corrected():
    fx = build_log(LogVariant("lg_vec_base", raster=False,
                              label_anchor="baseline"))
    g = log_grid(fx.open(), [0])
    r = g.rulers[0]
    assert r.evidence["anchor_rule_kind"] == "frames"
    for rd, ly in zip(fx.readings, _tops(g)):
        assert ly.top == pytest.approx(rd.value, abs=0.005)


@pytest.mark.parametrize("builder", [build_imperial_log, build_metric_log])
def test_the_existing_vector_logs_read_exactly_as_before(builder):
    """The loggrid fixtures centre their labels and draw no ticks: the ruler
    is left exactly as it was fitted, with its anchor rule recorded."""
    from planlens.document import Document
    from planlens.document import loggrid as LG
    gt = builder()
    doc = Document(content=gt.pdf)
    g = log_grid(doc, [0])
    orig = LG._vector_anchor
    try:
        LG._vector_anchor = lambda d, pg: None
        g0 = LG.log_grid(Document(content=gt.pdf), [0])
    finally:
        LG._vector_anchor = orig
    r, r0 = g.rulers[0], g0.rulers[0]
    assert (r.slope, r.intercept, r.ticks) == (r0.slope, r0.intercept,
                                               r0.ticks)
    assert r.evidence.get("anchor_rule_kind") in ("frames", "centred_assumed")
    assert [ly.top for ly in g.layers] == [ly.top for ly in g0.layers]


def _printed(fx, boxes):
    """The labels as PRINTED for each box (the text a reader of the crops
    would write), so the print's resolution travels with the values."""
    out = []
    for v in fx.values_for(boxes):
        if v is None:
            out.append(None)
            continue
        lab = next(lb for lb in fx.labels if lb.value == v)
        out.append(lab.text)
    return out


def test_values_given_as_printed_labels_read_the_same_page():
    fx = build_log(LogVariant("lg_printed", skew_deg=0.3, seed=67))
    doc = fx.open()
    g = log_grid(doc, [0])
    boxes = g.needs_values[0]["labels"]
    by_number = log_grid(doc, [0], values={0: fx.values_for(boxes)})
    by_text = log_grid(doc, [0], values={0: _printed(fx, boxes)})
    assert not by_text.needs_values and by_text.rulers
    assert [round(ly.top, 4) for ly in _tops(by_text)] == \
        [round(ly.top, 4) for ly in _tops(by_number)]


def _relabelled(fx, label_text, header=None):
    """``fx`` with its OCR text changed as a scan would give it: each depth
    label's text from ``label_text(value)``, and the depth column's header
    replaced by ``header`` where one is given. The pixels are unchanged."""
    import dataclasses
    from planlens.testing.visual_scale_fixtures import OcrLines
    by_text = {lb.text: lb.value for lb in fx.labels}
    lines = []
    for text, box in fx.text_source._lines:
        if text in by_text:
            text = label_text(by_text[text])
        elif header is not None and text == "Depth":
            text = header
        lines.append((text, box))
    return dataclasses.replace(fx, text_source=OcrLines(lines, page=0,
                                                        name="ocr"))


def _flanked(v):
    return f"-{v:g}-"


@pytest.fixture(scope="module")
def ocr_log():
    return build_log(LogVariant("lg_flanked", text_source="ocr",
                                skew_deg=0.5, seed=71))


@pytest.mark.parametrize("first_tick_lost", [False, True])
def test_labels_printed_between_two_ticks_are_depths(ocr_log,
                                                     first_tick_lost):
    """A ruler printing each numeral between two short ticks reaches the
    text as "-1-", "-2-" (PL-35); optical text can lose one tick ("1 -")."""
    first = min(lb.value for lb in ocr_log.labels)

    def text(v):
        return f"{v:g} -" if first_tick_lost and v == first else _flanked(v)

    g = log_grid(_relabelled(ocr_log, text).open(), [0])
    r = g.rulers[0]
    assert r.kind == "depth"
    assert [v for _y, v in r.ticks] == sorted(lb.value for lb in
                                              ocr_log.labels)
    assert g.scales[0].provenance.get("tick_flanked")
    tops = _tops(g)
    assert len(tops) == len(ocr_log.readings)
    for rd, ly in zip(ocr_log.readings, tops):
        assert abs(ly.top - rd.value) <= ly.plus_minus, (rd.value, ly.top)
    placed = [c for c in g.rows if c.depth is not None]
    assert placed and all(c.depth > 0 for c in placed)


def test_tick_flanked_labels_under_an_elevation_header_stay_signed(ocr_log):
    """Where the header over the labels says elevation, "-2-" is an
    elevation below datum as printed, and no depth ruler is made of it."""
    g = log_grid(_relabelled(ocr_log, _flanked, header="Elevation").open(),
                 [0])
    assert not g.rulers
    ticks = [v for _y, v in g.elevation_rulers[0].ticks]
    assert ticks and all(v < 0 for v in ticks)


@pytest.mark.parametrize("header,elevation", [("Depth", False),
                                              ("Elevation", True)])
def test_a_falling_scale_never_fills_the_depth_slot(ocr_log, header,
                                                    elevation):
    """Elevations crossing datum with a tick after each label ("2-", "1-",
    "0-", "-1-") stay signed (read unsigned they fold back on themselves),
    and a scale that falls down the page is never the depth ruler: it is
    the page's elevation scale where the header says elevation, else it is
    left unused and said (PL-35)."""
    top = max(lb.value for lb in ocr_log.labels) / 2.0
    g = log_grid(_relabelled(ocr_log, lambda v: f"{top - v:g}-",
                             header=header).open(), [0])
    assert not g.rulers
    if elevation:
        ticks = [v for _y, v in sorted(g.elevation_rulers[0].ticks)]
        assert ticks == sorted((top - lb.value for lb in ocr_log.labels),
                               reverse=True)
    else:
        assert not g.elevation_rulers
        assert any("falls down the page" in w and "not taken as the depth "
                   "ruler" in w for w in g.warnings)


def test_an_elevation_band_below_datum_beside_the_ruler_is_not_the_ruler():
    """Elevations below datum printed with a tick after each label ("-11-")
    read like a ruler printed between two ticks, and with the depth column's
    header in reach their band is called depth too. Read unsigned it rises;
    it is the depth ruler only where no other y-scale rises as read, and
    here the depth ruler beside it does (PL-35)."""
    fx = build_log(LogVariant("lg_elev_flanked", text_source="ocr",
                              skew_deg=0.4, seed=73, elevation_top=-10.0,
                              elevation_text="{:g}-"))
    g = log_grid(fx.open(), [0])
    r = g.rulers[0]
    assert r.kind == "depth"
    assert [v for _y, v in r.ticks] == sorted(lb.value for lb in fx.labels)
    assert not g.scales[0].provenance.get("tick_flanked")
    tops = _tops(g)
    assert len(tops) == len(fx.readings)
    for rd, ly in zip(fx.readings, tops):
        assert abs(ly.top - rd.value) <= ly.plus_minus, (rd.value, ly.top)


def test_values_of_the_wrong_count_are_refused_and_said_so():
    fx = build_log(LogVariant("lg_count", skew_deg=0.3, seed=68))
    doc = fx.open()
    boxes = log_grid(doc, [0]).needs_values[0]["labels"]
    short = fx.values_for(boxes)[:-2]
    g = log_grid(doc, [0], values={0: short})
    assert 0 in g.needs_values and not g.rulers
    said = " ".join(g.warnings)
    assert f"{len(short)} value(s) given" in said
    assert f"for {len(boxes)} label box(es)" in said
