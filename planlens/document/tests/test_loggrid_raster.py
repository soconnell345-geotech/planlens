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
