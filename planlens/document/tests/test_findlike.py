"""find_like: every copy of one mark, and what each copy is.

The fixture is the case that prompted it (2026-09-25): 11x17 sheets whose
0.06 in lettering is DRAWN as strokes (no text layer), a legend table naming
every tag type in the same place on each sheet, "GCE" callouts with leaders,
look-alikes (GCG, GPE, QCE), bare tags and tags turned 90 degrees. Pinned:
every on-plan GCE is found once, each callout's leader tip is located, the
legend is set apart, a larger legend example still finds the plan's tags, and
the contact sheets come out numbered in hit order.

Every search runs on BOTH matchers — OpenCV where it loads, numpy anywhere
(the FIPS hosts, where loading OpenCV aborts the process) — and the two are
held to the same hits: the matcher itself to OpenCV's ``matchTemplate``, the
numpy resize to ``cv2.resize``.
"""

import math

import pytest

fitz = pytest.importorskip("fitz")
np = pytest.importorskip("numpy")

from planlens import opencv  # noqa: E402
from planlens.document import Document  # noqa: E402
from planlens.document import findlike as fl  # noqa: E402
from planlens.testing.tag_fixtures import build_synthetic_tag_set  # noqa: E402


def _need_opencv():
    ok, why = opencv.available()
    if not ok:
        pytest.skip(why)
    import cv2
    return cv2


@pytest.fixture(scope="module", params=fl.BACKENDS)
def backend(request):
    if request.param == "opencv":
        _need_opencv()
    return request.param


@pytest.fixture(scope="module")
def gt():
    return build_synthetic_tag_set()


@pytest.fixture(scope="module")
def searches(gt):
    """One search of the fixture per matcher, shared by the module."""
    done, docs = {}, []

    def run(backend):
        if backend not in done:
            d = Document(content=gt.pdf)
            docs.append(d)
            done[backend] = (d, d.find_like(0, gt.example_bbox, backend=backend))
        return done[backend]

    yield run
    for d in docs:
        d.close()


@pytest.fixture(scope="module")
def result(searches, backend):
    return searches(backend)


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


def test_the_example_and_the_counts(gt, result, backend):
    _, res = result
    ex = res["example"]
    assert ex["page"] == 0 and 4.0 < ex["height_pt"] < 5.5
    assert set(res["counts"]) == {"callout", "legend", "unanchored"}
    assert res["pages"] == [0, 1, 2]
    assert res["backend"] == backend


def test_a_larger_legend_example_still_finds_the_plan_tags(backend):
    gt2 = build_synthetic_tag_set(legend_cap_in=0.085, n_pages=2)
    d = Document(content=gt2.pdf)
    try:
        res = d.find_like(0, gt2.example_bbox, backend=backend)
        for t in [t for t in gt2.of("GCE") if t.kind != "legend"]:
            hits = _hits_on(gt2, res, t)
            assert len(hits) == 1 and hits[0].scale < 0.85, (t, hits)
    finally:
        d.close()


def test_an_empty_box_is_refused(gt, result, backend):
    d, _ = result
    with pytest.raises(ValueError, match="no ink"):
        d.find_like(0, (5, 5, 20, 12), backend=backend)
    with pytest.raises(ValueError, match="bbox"):
        d.find_like(0, (20, 5, 5, 12), backend=backend)


def test_a_box_inside_solid_ink_is_refused():
    doc = fitz.open()
    page = doc.new_page(width=200, height=200)
    page.draw_rect(fitz.Rect(20, 20, 120, 120), color=(0, 0, 0), fill=(0, 0, 0))
    d = Document(content=doc.tobytes())
    doc.close()
    try:
        with pytest.raises(ValueError, match="solid ink"):
            d.find_like(0, (40, 40, 60, 60), backend="numpy")
    finally:
        d.close()


def test_contact_sheets_are_numbered_in_hit_order(gt, result, backend):
    d, res = result
    cand = [h for h in res["hits"] if h.context != "legend"]
    sheets = d.like_sheets(cand, per_sheet=20, cols=4, backend=backend)
    assert len(sheets) == math.ceil(len(cand) / 20)
    assert sheets[0][1][:3] == [1, 2, 3]
    assert sheets[-1][1][-1] == len(cand)
    png, _ = sheets[0]
    pix = fitz.Pixmap(png)
    assert pix.width == 4 * 460
    assert pix.height == 5 * (190 + 30)


# -- the two matchers are one answer ---------------------------------------------

def _hit_key(h):
    return (h.page, tuple(round(v, 3) for v in h.bbox), h.rotation, h.scale,
            h.context, h.points_to)


def test_the_two_matchers_find_the_same_hits(searches):
    """Same hits (page, box, turn, scale, context, leader tip), scores within
    1e-4. Only the ORDER of hits whose scores tie may differ: the legend
    repeats the same strokes, and float32 (OpenCV) and float64 (numpy) break
    a tie of equal scores differently."""
    _need_opencv()
    _, a = searches("opencv")
    _, b = searches("numpy")
    sa = {_hit_key(h): h.score for h in a["hits"]}
    sb = {_hit_key(h): h.score for h in b["hits"]}
    assert len(sa) == len(a["hits"]) and len(sb) == len(b["hits"])
    assert sa.keys() == sb.keys()
    assert max(abs(sa[k] - sb[k]) for k in sa) < 1e-4
    assert a["counts"] == b["counts"]
    for x, y in zip(a["hits"], b["hits"]):          # order: up to ties
        assert x.page == y.page and abs(x.score - y.score) < 1e-4


def _passes(cv2, tpl, scales=(0.5, 1.0, 1.948)):
    out = []
    for sc in scales:
        t = tpl if sc == 1 else cv2.resize(
            tpl, None, fx=sc, fy=sc,
            interpolation=cv2.INTER_AREA if sc < 1 else cv2.INTER_LINEAR)
        out.append(t)
    return out


@pytest.mark.parametrize("threshold", [fl.DEFAULT_THRESHOLD, 0.4])
def test_the_numpy_matcher_is_opencvs_matchtemplate(gt, threshold):
    """Same template in, same hill tops out, scores within 1e-5 (OpenCV
    correlates in float32, numpy in float64). At the default threshold the
    positions are identical. Lower, a small template sliding along a wall
    scores EXACTLY the same at neighbouring positions, and which of those
    tied positions counts as the top is decided by rounding: every position
    the two disagree on must be such a tie."""
    cv2 = _need_opencv()
    d = Document(content=gt.pdf)
    try:
        tpl, _ = fl._example(d._doc[0], gt.example_bbox, fl.DEFAULT_DPI)
        ink = fl._ink(d._doc[1], fl.DEFAULT_DPI)
    finally:
        d.close()
    turns = [0, 1, 2, 3]
    n = 0
    for t in _passes(cv2, tpl):
        a = fl._peaks_opencv(ink, t, turns, threshold)
        b = fl._peaks_numpy(ink, t, turns, threshold)
        for k, (ya, xa, va), (yb, xb, vb) in zip(turns, a, b):
            pa = dict(zip(zip(ya.tolist(), xa.tolist()), va.tolist()))
            pb = dict(zip(zip(yb.tolist(), xb.tolist()), vb.tolist()))
            both = pa.keys() & pb.keys()
            assert all(abs(pa[p] - pb[p]) < 1e-5 for p in both)
            n += len(both)
            if threshold == fl.DEFAULT_THRESHOLD:
                assert pa.keys() == pb.keys()
                continue
            tr = np.ascontiguousarray(np.rot90(t, k))
            res = cv2.matchTemplate(ink, tr, cv2.TM_CCOEFF_NORMED)
            ry, rx = fl._peak_radii(*tr.shape)
            for y, x in pa.keys() ^ pb.keys():
                win = res[max(0, y - ry):y + ry + 1, max(0, x - rx):x + rx + 1]
                assert win.max() - res[y, x] < 1e-6, (k, y, x)
    assert n > 50


@pytest.mark.parametrize("threshold", [0.3, 0.0, -0.5])
def test_the_numpy_matcher_at_any_threshold(threshold):
    """On noise (no tied scores) every window can be a candidate: the dense
    peak filter and a threshold at or below zero agree with OpenCV too."""
    _need_opencv()
    rng = np.random.default_rng(7)
    ink = rng.integers(0, 256, (180, 260)).astype(np.uint8)
    ink[60:120, 90:170] = 0                       # flat paper scores 0
    t = ink[20:31, 30:47].copy()
    a = fl._peaks_opencv(ink, t, [0, 1, 2, 3], threshold)
    b = fl._peaks_numpy(ink, t, [0, 1, 2, 3], threshold)
    for (ya, xa, va), (yb, xb, vb) in zip(a, b):
        assert np.array_equal(ya, yb) and np.array_equal(xa, xb)
        if len(ya):
            assert np.abs(va - vb).max() < 1e-5
    assert len(a[0][0])                           # the template's own place


def test_the_hill_top_filters_agree():
    """The per-candidate window test and the dense max filter are the same
    rule (the dense one takes over when nearly everything is a candidate)."""
    rng = np.random.default_rng(3)
    num = rng.normal(size=(40, 70))
    den = np.ones_like(num) * 3.0
    den[5:9, 10:20] = np.inf
    cy, cx = np.nonzero(num / den > -0.2)
    m1, v1 = fl._hill_tops(num, den, cy, cx, 2, 4, -0.2)
    r = fl._clamp(num / den)
    m2 = (r[cy, cx] >= -0.2) & (r[cy, cx] >= fl._max_filter(r, 2, 4)[cy, cx])
    assert np.array_equal(m1, m2) and m1.any() and not m1.all()
    sub = slice(None, None, 37)                   # few candidates: gathered
    m3, _ = fl._hill_tops(num, den, cy[sub], cx[sub], 2, 4, -0.2)
    assert np.array_equal(m3, m2[sub])


def test_the_numpy_resize_is_cv2s(gt):
    """Area shrink pixel for pixel; bilinear enlargement within one grey
    level on under 1 % of pixels."""
    cv2 = _need_opencv()
    d = Document(content=gt.pdf)
    try:
        tpl, _ = fl._example(d._doc[0], gt.example_bbox, fl.DEFAULT_DPI)
    finally:
        d.close()
    rng = np.random.default_rng(1)
    imgs = [tpl, rng.integers(0, 256, (37, 81)).astype(np.uint8),
            rng.integers(0, 256, (11, 9)).astype(np.uint8)]
    lin_diff = lin_px = 0
    for img in imgs:
        for f in list(fl.DEFAULT_SCALES) + [0.25, 0.333, 0.62, 0.73, 2.5]:
            a = cv2.resize(img, None, fx=f, fy=f, interpolation=(
                cv2.INTER_AREA if f < 1 else cv2.INTER_LINEAR))
            b = fl._np_resize(img, f)
            assert a.shape == b.shape, (img.shape, f)
            dd = np.abs(a.astype(int) - b.astype(int))
            if f < 1:
                assert not dd.any(), (img.shape, f)
            else:
                assert dd.max() <= 1, (img.shape, f)
                lin_diff += int((dd > 0).sum())
                lin_px += dd.size
    assert lin_diff < 0.01 * lin_px


# -- choosing the matcher ----------------------------------------------------------

@pytest.fixture
def no_opencv(monkeypatch):
    monkeypatch.setattr(opencv, "available",
                        lambda: (False, "OpenCV cannot load on this host"))
    monkeypatch.delenv(fl.BACKEND_ENV, raising=False)


def test_auto_falls_back_to_numpy_where_opencv_cannot_load(no_opencv):
    assert fl.resolve_backend() == "numpy"
    assert fl.available() == (True, "")
    with pytest.raises(ImportError, match="cannot load"):
        fl.resolve_backend("opencv")


def test_the_switch_and_the_argument(no_opencv, monkeypatch):
    monkeypatch.setenv(fl.BACKEND_ENV, "opencv")
    ok, why = fl.available()
    assert not ok and "cannot load" in why
    assert fl.resolve_backend("numpy") == "numpy"   # the argument wins
    monkeypatch.setenv(fl.BACKEND_ENV, "numpy")
    monkeypatch.setattr(opencv, "available", lambda: pytest.fail("probed"))
    assert fl.resolve_backend() == "numpy" and fl.available() == (True, "")
    monkeypatch.setenv(fl.BACKEND_ENV, "gpu")
    ok, why = fl.available()
    assert not ok and fl.BACKEND_ENV in why
    with pytest.raises(ValueError, match="auto, numpy or opencv"):
        fl.resolve_backend()


def test_the_numpy_path_never_loads_opencv(gt, no_opencv, monkeypatch):
    monkeypatch.setattr(opencv, "load", lambda: pytest.fail("OpenCV loaded"))
    d = Document(content=gt.pdf)
    try:
        res = d.find_like(0, gt.example_bbox, pages="0", scales=(0.5, 1.0, 1.5))
        assert res["backend"] == "numpy" and res["counts"]["callout"] >= 5
        sheets = d.like_sheets(res["hits"][:3])
        assert fitz.Pixmap(sheets[0][0]).width == 4 * 460
    finally:
        d.close()


# -- an example crossed by linework (Foundry brief 4, 2026-10-07) -----------------

def _view_box(view, image_box):
    """A 0-999 box on a view's image as page points (the host app's
    conversion), so the test boxes are the ones the live runs passed."""
    vx0, vy0, vx1, vy1 = view
    x0, y0, x1, y1 = image_box
    w, h = vx1 - vx0, vy1 - vy0
    return (vx0 + x0 / 999 * w, vy0 + y0 / 999 * h,
            vx0 + x1 / 999 * w, vy0 + y1 / 999 * h)


#: T1, the callout whose lettering sits on a heavy vertical grid line, as
#: two live runs boxed it (a 258 pt zoom and a 59 pt zoom); and T3, the clean
#: example a third run used.
T1_LIVE = (_view_box((357.9, 53.2, 616.2, 221.1), (510, 491, 553, 521)),
           _view_box((419.5, 98.8, 548.2, 169.5), (549, 525, 628, 591)))
T3_LIVE = _view_box((197.4, 208.6, 494.9, 396.3), (553, 546, 592, 575))


def test_an_example_on_a_grid_line_does_not_flood_the_search(gt):
    """Replayed from the live runs: with T1's boxes the search returned 400
    candidates (the cap) holding one of the seven callouts, and 367 holding
    all seven with no warning — the template was mostly the grid line and
    matched grid ticks everywhere, and the host read every candidate (180 and
    289 s). The line runs on past the box's edge where the lettering stops,
    so it is left out of the template: a few dozen candidates, every
    callout."""
    callouts = [t for t in gt.of("GCE", "callout") if t.page == 0]
    assert len(callouts) == 7
    t1 = min(callouts, key=lambda t: math.dist(
        ((t.bbox[0] + t.bbox[2]) / 2, (t.bbox[1] + t.bbox[3]) / 2),
        (495.0, 138.0)))
    d = Document(content=gt.pdf)
    try:
        for box in T1_LIVE + (t1.bbox,):
            res = d.find_like(0, box, "0", backend="numpy")
            assert "linework_left_out" in res["example"], box
            assert len(res["hits"]) < 100, (box, len(res["hits"]))
            missed = [t for t in callouts if not _hits_on(gt, res, t)]
            assert not missed, (box, missed)
            assert not res["warnings"]
    finally:
        d.close()


def test_a_clean_example_is_unchanged(gt):
    """An example nothing crosses keeps exactly the template it always had
    (the T3 run: 43 candidates, every callout)."""
    d = Document(content=gt.pdf)
    try:
        res = d.find_like(0, T3_LIVE, "0", backend="numpy")
        assert "linework_left_out" not in res["example"]
        assert len(res["hits"]) == 43
        made = {}
        tpl, _tb = fl._example(d._doc[0], T3_LIVE, 150.0, made)
        plain = fl._ink(d._doc[0], 150.0, clip=T3_LIVE)
        assert made["dropped"] == 0 and tpl.size < plain.size
    finally:
        d.close()


def _crossed_mark(line=True):
    """A small square mark, and (``line``) a long rule straight through it."""
    doc = fitz.open()
    page = doc.new_page(width=300, height=200)
    page.draw_rect(fitz.Rect(100, 90, 112, 102), color=(0, 0, 0), width=1.0)
    page.draw_line((106, 93), (106, 99), color=(0, 0, 0), width=1.0)
    if line:
        page.draw_line((40, 96), (260, 96), color=(0, 0, 0), width=1.5)
    data = doc.tobytes()
    doc.close()
    return data


def test_a_line_through_the_box_is_left_out_and_the_mark_kept():
    d = Document(content=_crossed_mark())
    a, b = {}, {}
    try:
        crossed, _tb = fl._example(d._doc[0], (97, 87, 115, 105), 150.0, a)
    finally:
        d.close()
    d = Document(content=_crossed_mark(line=False))
    try:
        clean, _tb2 = fl._example(d._doc[0], (97, 87, 115, 105), 150.0, b)
    finally:
        d.close()
    assert a["dropped"] > 0 and b["dropped"] == 0
    # the mark's own frame and stroke are still in the template
    assert (crossed > 64).sum() > 0.6 * (clean > 64).sum()
    # the rule's row runs straight across the clean mark's middle; in the
    # crossed template that row holds only the mark's two sides and stroke
    mid = crossed.shape[0] // 2
    assert (crossed[mid] > 64).sum() < 0.5 * crossed.shape[1]


def test_a_box_holding_only_a_line_is_refused():
    d = Document(content=_crossed_mark())
    try:
        with pytest.raises(ValueError, match="only linework"):
            d.find_like(0, (150, 93, 170, 99), backend="numpy")
    finally:
        d.close()
