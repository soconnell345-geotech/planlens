"""A host per conversation, not only per process (PL-29 (a) review).

The app builds one agent per conversation, each with its own vision engine
(the look) and its own signer (the person signed in); two testers can be
signed in to one process. ``review.call(..., host=)`` and ``review.using()``
serve one conversation's calls with its own host, in its own context only.
Made-up names and sheets throughout.
"""

import dataclasses
import json
import threading

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402

TAG = "TK-7"
CONFIRMED = json.dumps({"inside": TAG, "same_thing": True, "encloses": True,
                        "sure": True})


def _sheet():
    """A made-up sheet carrying the tag once; ``(pdf, box round it)``."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((200, 300), TAG, fontsize=12)
    r = page.search_for(TAG)[0]
    box = [r.x0 - 3, r.y0 - 3, r.x1 + 3, r.y1 + 3]
    data = doc.tobytes()
    doc.close()
    return data, box


class _Look:
    """Says a checked mark is on the tag, after meeting the other
    conversation's look at ``barrier`` (both are checking at once)."""

    def __init__(self, barrier=None):
        self.barrier = barrier
        self.kinds = []

    def __call__(self, image, question, kind):
        self.kinds.append(kind)
        if self.barrier is not None:
            self.barrier.wait(timeout=60)
        if kind == "find":
            return "\n".join(f"#{n} | {TAG} | other" for n in range(1, 9))
        return CONFIRMED


class _Signer:
    """A signer whose first reading waits for the other conversation's
    (both are writing at once)."""

    def __init__(self, name, barrier):
        self.name, self.barrier, self.waited = name, barrier, False

    def __call__(self):
        if not self.waited:
            self.waited = True
            self.barrier.wait(timeout=60)
        return self.name


def _crop(seen):
    """The default 300 dpi crop, noting each one rendered."""
    def render(pdf, page, bbox):
        from planlens.document import Document
        seen.append(page)
        doc = Document(content=pdf)
        try:
            return doc.render(int(page), bbox=tuple(bbox), pad_frac=0.0,
                              dpi=300)
        finally:
            doc.close()
    return render


def _markup(box):
    return [{"kind": "box", "page": 0, "comment": "confirm this tag",
             "target": TAG, "bbox": box}]


def _authors(path):
    with pymupdf.open(str(path)) as doc:
        return {a.info.get("title") for p in doc for a in p.annots()}


def test_two_conversations_marking_at_once_each_sign_their_own(tmp_path):
    pdf, box = _sheet()
    process_look = _Look()
    review.configure(review.ReviewHost(look=process_look,
                                       signer="Nobody (AI draft)"))
    signing, checking = threading.Barrier(2), threading.Barrier(2)
    hosts, looks, crops = {}, {}, {}
    for who in ("a", "b"):
        folder = tmp_path / who
        folder.mkdir()
        looks[who], crops[who] = _Look(checking), []
        hosts[who] = review.ReviewHost(
            look=looks[who],
            signer=_Signer(f"Reviewer {who.upper()} via TestApp (AI draft)",
                           signing),
            working_folder=lambda f=str(folder): f,
            verdict_key=lambda f=str(folder): f,
            render_crop=_crop(crops[who]))
    results, errors = {}, []

    def mark(who):
        try:
            results[who] = json.loads(review.call(
                "annotate_document",
                {"handle": "sheet.pdf", "output_path": "sheet_marked.pdf",
                 "markups": _markup(box)},
                {"sheet.pdf": pdf}, host=hosts[who]))
        except Exception as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=mark, args=(w,)) for w in ("a", "b")]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=120)
    assert not errors and not any(t.is_alive() for t in threads), errors
    for who in ("a", "b"):
        out, signer = results[who], hosts[who].signer.name
        assert "error" not in out, out
        assert out["author"] == signer
        assert out["check"]["confirmed"] == 1, out["check"]
        assert _authors(tmp_path / who / "sheet_marked.pdf") == {signer}
        assert looks[who].kinds == ["check"]
        # The check's worker thread ran in this conversation's context.
        assert crops[who] == [0]
    assert process_look.kinds == []
    assert review.current().signer == "Nobody (AI draft)"


def test_a_conversation_with_no_look_gets_no_check(tmp_path):
    # As the app with engine=None: the copy is written and has no check
    # block, and find_like is not offered.
    pdf, box = _sheet()
    att = {"sheet.pdf": pdf}
    look = _Look()
    base = review.configure(review.ReviewHost(
        look=look, working_folder=lambda: str(tmp_path),
        signer="Test Reviewer (AI draft)"))
    assert "find_like" in review.tool_names()
    with review.using(look=None) as mine:
        assert mine.look is None and mine.signer == base.signer
        assert "find_like" not in review.tool_names()
        out = json.loads(review.call("annotate_document", {
            "handle": "sheet.pdf", "output_path": "noengine.pdf",
            "markups": _markup(box)}, att))
        refused = json.loads(review.call("find_like", {
            "attachment_key": "sheet.pdf", "page": 0, "bbox": box}, att))
    assert "error" not in out and out["n_written"] == 1, out
    assert "check" not in out
    assert "unknown tool" in refused["error"]
    assert look.kinds == []
    assert review.current() is base

    no_look = dataclasses.replace(base, look=None)
    out = json.loads(review.call("annotate_document", {
        "handle": "sheet.pdf", "output_path": "noengine2.pdf",
        "markups": _markup(box)}, att, host=no_look))
    assert "check" not in out and look.kinds == []
    assert "find_like" not in review.tool_names(no_look)
    assert "find_like" not in [s["name"] for s in
                               review.tool_specs(host=no_look)]

    out = json.loads(review.call("annotate_document", {
        "handle": "sheet.pdf", "output_path": "checked.pdf",
        "markups": _markup(box)}, att))
    assert out["check"]["confirmed"] == 1 and look.kinds == ["check"]


def test_using_serves_the_block_only():
    base = review.configure(review.ReviewHost(signer="P (AI draft)"))
    a = review.ReviewHost(signer="A (AI draft)")
    with review.using(a):
        assert review.current() is a
        with review.using(signer="B (AI draft)") as b:
            assert review.current() is b
            assert b.signer == "B (AI draft)" and a.signer == "A (AI draft)"
        assert review.current() is a
    assert review.current() is base
    with pytest.raises(RuntimeError):
        with review.using(a):
            raise RuntimeError("a turn that failed")
    assert review.current() is base


def test_find_like_says_a_missing_key_in_the_hosts_words(tmp_path):
    # The app's find_like resolved keys with its vision tools' resolver,
    # whose words differ from the document tools'; the host hands it over.
    pdf, box = _sheet()

    def source(key, attachments):
        if key in attachments:
            return attachments[key]
        raise FileNotFoundError(
            f"'{key}' is not an attachment key, a document handle this "
            "conversation opened, or a file in this conversation's working "
            f"folder. Attachment keys: {sorted(attachments)}. Files here: "
            "none.")

    review.configure(review.ReviewHost(look=_Look(), find_like_source=source,
                                       working_folder=lambda: str(tmp_path)))
    att = {"sheet.pdf": pdf}
    out = json.loads(review.call("find_like", {
        "attachment_key": "nope.pdf", "page": 0, "bbox": box}, att))
    assert out == {"error": (
        "'nope.pdf' is not an attachment key, a document handle this "
        "conversation opened, or a file in this conversation's working "
        "folder. Attachment keys: ['sheet.pdf']. Files here: none.")}
    pytest.importorskip("planlens.document.findlike")
    found = json.loads(review.call("find_like", {
        "attachment_key": "sheet.pdf", "page": 0, "bbox": box,
        "text": TAG}, att))
    assert "error" not in found and found["instances"] == 1, found


def test_annotate_errors_are_said_in_the_hosts_words(tmp_path, monkeypatch):
    from planlens.review import marked_copy, markup_check
    pdf, box = _sheet()
    said = []

    def error_json(tool, exc):
        said.append(tool)
        return json.dumps({"error": f"{tool} failed: the host's words "
                                    f"({exc})", "hint": "the host's hint"})

    review.configure(review.ReviewHost(
        look=_Look(), working_folder=lambda: str(tmp_path),
        error_json=error_json))

    def boom(*args, **kwargs):
        raise RuntimeError("made-up failure")

    args = {"handle": "sheet.pdf", "output_path": "sheet_marked.pdf",
            "markups": _markup(box)}
    with monkeypatch.context() as m:
        m.setattr(marked_copy, "write_marked_copy", boom)
        out = json.loads(review.call("annotate_document", args,
                                     {"sheet.pdf": pdf}))
    assert out == {"error": "annotate_document failed: the host's words "
                            "(made-up failure)", "hint": "the host's hint"}
    with monkeypatch.context() as m:
        m.setattr(markup_check, "check_marks", boom)
        out = json.loads(review.call("annotate_document", args,
                                     {"sheet.pdf": pdf}))
    assert out["n_written"] == 1
    assert out["check"] == {"error": "the placement check failed: the "
                                     "host's words (made-up failure)"}
    assert said == ["annotate_document", "the placement check"]


def test_without_the_hosts_words_the_errors_read_as_before(tmp_path,
                                                          monkeypatch):
    from planlens.review import markup_check
    from planlens.review.host import error_line
    pdf, box = _sheet()
    review.configure(review.ReviewHost(look=_Look(),
                                       working_folder=lambda: str(tmp_path)))
    exc = RuntimeError("made-up failure")

    def boom(*args, **kwargs):
        raise exc

    monkeypatch.setattr(markup_check, "check_marks", boom)
    out = json.loads(review.call("annotate_document", {
        "handle": "sheet.pdf", "output_path": "sheet_marked.pdf",
        "markups": _markup(box)}, {"sheet.pdf": pdf}))
    assert out["check"] == {"error": "the placement check failed: "
                                     + error_line(exc)}
