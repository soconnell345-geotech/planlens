"""find_like: every copy of one mark, and what each copy is.

The fixture is the case that prompted it (2026-09-25): 11x17 sheets whose
0.06 in lettering is DRAWN as strokes (no text layer), a legend table naming
every tag type in the same place on each sheet, "GCE" callouts with leaders,
look-alikes (GCG, GPE, QCE), bare tags and tags turned 90 degrees. Pinned:
every on-plan GCE is found once, each callout's leader tip is located, the
legend is set apart, a larger legend example still finds the plan's tags, and
the contact sheets come out numbered in hit order.
"""

import math

import pytest

fitz = pytest.importorskip("fitz")
pytest.importorskip("cv2")

from planlens.document import Document  # noqa: E402
from planlens.testing.tag_fixtures import build_synthetic_tag_set  # noqa: E402


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_tag_set()


@pytest.fixture(scope="module")
def result(gt):
    d = Document(content=gt.pdf)
    res = d.find_like(0, gt.example_bbox)
    yield d, res
    d.close()


def _tag_of(gt, hit, tol=6.0):
    cx, cy = hit.center
    for t in gt.tags:
        if t.page != hit.page:
            continue
        tx, ty = (t.bbox[0] + t.bbox[2]) / 2, (t.bbox[1] + t.bbox[3]) / 2
        if abs(cx - tx) < tol and abs(cy - ty) < tol:
            return t
    return None


def _hits_on(gt, res, tag):
    return [h for h in res["hits"] if _tag_of(gt, h) is tag]


def test_every_on_plan_gce_is_found_exactly_once(gt, result):
    _, res = result
    on_plan = [t for t in gt.of("GCE") if t.kind != "legend"]
    assert len(on_plan) == 33
    for t in on_plan:
        hits = _hits_on(gt, res, t)
        assert len(hits) == 1, (t, hits)
        assert hits[0].score >= 0.6


def test_callouts_are_callouts_and_their_leaders_are_followed(gt, result):
    _, res = result
    for t in gt.of("GCE", "callout"):
        (h,) = _hits_on(gt, res, t)
        assert h.context == "callout", t
        assert math.dist(h.points_to, t.points_to) < 1.0
        assert h.rotation == t.rotation


def test_bare_tags_are_unanchored_and_the_legend_is_legend(gt, result):
    _, res = result
    for t in gt.of("GCE", "bare"):
        (h,) = _hits_on(gt, res, t)
        assert h.context == "unanchored"
    for t in gt.of("GCE", "legend"):
        (h,) = _hits_on(gt, res, t)
        assert h.context == "legend"
        assert h.evidence.get("in_ruled_row") or h.evidence.get("repeated_on_pages")
    # Nothing on the plan is ever filed as legend.
    for t in gt.tags:
        if t.kind != "legend":
            assert all(h.context != "legend" for h in _hits_on(gt, res, t))


def test_look_alikes_are_candidates_not_answers(gt, result):
    """Correlation alone cannot separate GCG from GCE — that is what the
    contact sheets are for — so look-alikes come back, scored lower than the
    example's own copy."""
    _, res = result
    alike = [h for t in gt.of("GCG") + gt.of("QCE") for h in _hits_on(gt, res, t)]
    assert alike
    assert max(h.score for h in alike) < 0.9
    assert max(h.score for h in res["hits"]) > 0.95


def test_the_example_and_the_counts(gt, result):
    _, res = result
    ex = res["example"]
    assert ex["page"] == 0 and 4.0 < ex["height_pt"] < 5.5
    assert set(res["counts"]) == {"callout", "legend", "unanchored"}
    assert res["pages"] == [0, 1, 2]


def test_a_larger_legend_example_still_finds_the_plan_tags():
    gt2 = build_synthetic_tag_set(legend_cap_in=0.085, n_pages=2)
    d = Document(content=gt2.pdf)
    try:
        res = d.find_like(0, gt2.example_bbox)
        for t in [t for t in gt2.of("GCE") if t.kind != "legend"]:
            hits = _hits_on(gt2, res, t)
            assert len(hits) == 1 and hits[0].scale < 0.85, (t, hits)
    finally:
        d.close()


def test_an_empty_box_is_refused(gt, result):
    d, _ = result
    with pytest.raises(ValueError, match="no ink"):
        d.find_like(0, (5, 5, 20, 12))
    with pytest.raises(ValueError, match="bbox"):
        d.find_like(0, (20, 5, 5, 12))


def test_contact_sheets_are_numbered_in_hit_order(gt, result):
    d, res = result
    cand = [h for h in res["hits"] if h.context != "legend"]
    sheets = d.like_sheets(cand, per_sheet=20, cols=4)
    assert len(sheets) == math.ceil(len(cand) / 20)
    assert sheets[0][1][:3] == [1, 2, 3]
    assert sheets[-1][1][-1] == len(cand)
    png, _ = sheets[0]
    pix = fitz.Pixmap(png)
    assert pix.width == 4 * 460
