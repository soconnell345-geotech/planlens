"""Review of PL-09's host options (branch b15): what the reviewer ran, kept.
Two hosts of ONE conversation on one made-up file, one asking for a reading
option and one not: every reading tool, both orders, the file forgotten and
opened again by either; the marks both write; the open-document limit; a
wrong option value. Made-up pages only."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.review import conversation  # noqa: E402
from planlens.review.host import ReviewHost, using  # noqa: E402
from planlens.review.tests.conftest import (  # noqa: E402
    _close_toolkits, _relative, confined,
)

WORDS = ("the gravel fill was placed in thin lifts and rolled until the "
         "made-up gauge read the figure the specification asks for").split()
COLS = (72, 172, 272, 372, 540)
WARNING = "! reading order not reconstructed"
NOTES = ("note: continues on page 2 as table p2.tbl0",
         "note: continues table p1.tbl0 from page 1")
ADDED = {"column_warning": (WARNING,), "continued_tables": NOTES}
BOTH = {"column_warning": True, "continued_tables": True}


def _table(page, top, first, n, titles):
    grid = ([("Pit", "Depth (m)", "Fines (%)", "Notes")] if titles else [])
    grid += [(f"TP-{k}", f"{k}.2", f"{10 + k}", "dry")
             for k in range(first, first + n)]
    for r, row in enumerate(grid):
        for c, text in enumerate(row):
            page.insert_text((COLS[c] + 4, top + 18 * r + 13), text,
                             fontsize=9)
    for r in range(len(grid) + 1):
        page.draw_line((COLS[0], top + 18 * r), (COLS[-1], top + 18 * r))
    for x in COLS:
        page.draw_line((x, top), (x, top + 18 * len(grid)))


def _made_up(tail: bytes = b"") -> bytes:
    """Page 0: two columns drawn row by row. Pages 1-2: one table that runs
    on. Page 3: a list of tables. Page 4: a caption the list does not name."""
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 60), "AN INVENTED HEADING ACROSS BOTH COLUMNS",
                     fontsize=13)
    for r in range(18):
        for c in range(2):
            words = " ".join(WORDS[(r + 3 * c + k) % len(WORDS)]
                             for k in range(8))
            page.insert_text((72 + 234 * c, 100 + 14 * r),
                             f"k{c}w{r} {words}", fontsize=8)
    page = doc.new_page(width=612, height=792)
    page.insert_text((72, 90), "Table 6: Fines contents of the pit samples",
                     fontsize=10)
    _table(page, 110, 1, 30, True)
    _table(doc.new_page(width=612, height=792), 90, 31, 8, True)
    for lines in (["LIST OF TABLES", "Table 1 Compaction Results ...... 9"],
                  ["Table 8: Sand Replacement Readings"]
                  + [f"S-{n}   hole {n}   volume {n}.31 litres   mass "
                     f"{n * 2}.4 kg   day {n}" for n in range(1, 25)]):
        page = doc.new_page(width=612, height=792)
        for i, text in enumerate(lines):
            page.insert_text((72, 72 + 15 * i), text, fontsize=10)
    data = doc.tobytes()
    doc.close()
    return data + tail


def _folder_host(folder, **options):
    shown, hide = _relative(folder)
    resolve = confined(folder)

    def readable(name):
        try:
            return resolve(name)
        except Exception:  # noqa: BLE001 - not a file here
            return None

    return ReviewHost(working_folder=lambda: str(folder), display_path=shown,
                      hide_paths=hide, resolve_source=resolve,
                      readable_file=readable, toolkit_options=options)


def _call(host, name, att=None, **arguments):
    return json.loads(review.call(name, arguments, att or {}, 12000,
                                  host=host))


def _every_reading_tool(host, att, handle):
    """Each reading tool's result with the handle it names taken out (a
    call answers under the handle it was given)."""
    calls = [("document_structure", {}),
             ("document_page_map", {"with_evidence": True}),
             ("read_document", {"pages": "0"}),
             ("read_document", {"pages": "1-2", "include_tables": True}),
             ("search_document", {"pattern": "gauge"}),
             ("document_markups", {}),
             ("find_quantities", {})]
    outs = [_call(host, n, att, handle=handle, **a) for n, a in calls]
    return json.loads(json.dumps(outs).replace(handle, "H"))


@pytest.mark.parametrize("option", ["column_warning", "continued_tables"])
@pytest.mark.parametrize("asked_first", [False, True])
@pytest.mark.parametrize("who_forgets", ["asked", "plain"])
def test_every_reading_tool_both_orders_and_after_forgetting(
        option, asked_first, who_forgets):
    att = {"set.pdf": _made_up()}
    alone_handle = _call(ReviewHost(), "open_document", att,
                         source="set.pdf")["handle"]
    alone = _every_reading_tool(ReviewHost(), att, alone_handle)
    _close_toolkits()
    hosts = {"plain": ReviewHost(),
             "asked": ReviewHost(toolkit_options={option: True})}
    handles = {}

    def check(who, handle):
        got = _every_reading_tool(hosts[who], att, handle)
        text = got[2]["text"] + got[3]["text"]
        if who == "plain":
            assert got == alone
        else:
            assert all(a in text for a in ADDED[option])
            assert [g for i, g in enumerate(got) if i not in (2, 3)] == [
                a for i, a in enumerate(alone) if i not in (2, 3)]

    for who in (("asked", "plain") if asked_first else ("plain", "asked")):
        handles[who] = _call(hosts[who], "open_document", att,
                             source="set.pdf")["handle"]
        check(who, handles[who])
    assert handles["plain"] == alone_handle != handles["asked"]
    check("plain", handles["asked"])       # each with the other's handle
    check("asked", handles["plain"])
    with using(hosts[who_forgets]):
        token = conversation._ATTACHMENTS.set(att)
        try:
            assert conversation.forget_path("set.pdf") == 2
        finally:
            conversation._ATTACHMENTS.reset(token)
    other = "plain" if who_forgets == "asked" else "asked"
    for who in (who_forgets, other, who_forgets):
        again = _call(hosts[who], "open_document", att, source="set.pdf")
        assert again["handle"] == handles[who]
        check(who, handles[who])


def test_the_marks_of_both_hosts_are_in_the_one_copy(tmp_path):
    (tmp_path / "set.pdf").write_bytes(_made_up())
    plain, asked = _folder_host(tmp_path), _folder_host(tmp_path, **BOTH)
    for host, page, words in ((asked, 0, "made-up remark A"),
                              (plain, 1, "made-up remark B")):
        out = _call(host, "annotate_document", handle="set.pdf", markups=[
            {"page": page, "kind": "note", "comment": words,
             "bbox": [80, 90, 200, 120]}])
        assert out["n_written"] == 1, out
    assert out["in_file"]["markups"] == 2
    for host in (plain, asked, plain):
        marks = _call(host, "document_markups", handle="set_marked.pdf")
        assert marks["n_markups"] == 2
        assert "remark A" in marks["markups"][0]
        assert "remark B" in marks["markups"][1]
    read = {h is asked: _call(h, "read_document", handle="set_marked.pdf",
                              pages="0")["text"] for h in (plain, asked)}
    assert WARNING in read[True] and WARNING not in read[False]


def test_a_handle_closed_under_the_limit_by_the_asking_host_still_answers():
    att = {f"f{i}.pdf": _made_up(b"\n%" + bytes([48 + i])) for i in range(3)}
    asked = ReviewHost(toolkit_options=dict(BOTH, max_open=2))
    plain = ReviewHost()
    _call(asked, "open_document", att, source="f2.pdf")   # builds the toolkit
    handle = _call(plain, "open_document", att, source="f0.pdf")["handle"]
    before = _every_reading_tool(plain, att, handle)
    for name in ("f0.pdf", "f1.pdf"):
        _call(asked, "open_document", att, source=name)
    assert handle not in conversation._toolkit()._entries
    assert _every_reading_tool(plain, att, handle) == before
    assert WARNING not in json.dumps(before)


@pytest.mark.parametrize("value, on", [("yes", True), ("false", True),
                                       (1, True), (0, False), (None, False)])
def test_a_value_is_read_as_true_or_false_as_the_other_options_read_it(
        value, on):
    att = {"set.pdf": _made_up()}
    out = _call(ReviewHost(toolkit_options={"column_warning": value}),
                "read_document", att, handle="set.pdf", pages="0")
    assert (WARNING in out["text"]) is on


def test_a_name_that_is_no_option_is_refused_by_name_as_before():
    att = {"set.pdf": _made_up()}
    for name in ("column_warnings", "dxf_plot"):
        _close_toolkits()
        out = _call(ReviewHost(toolkit_options={name: True}),
                    "open_document", att, source="set.pdf")
        assert name in out["error"] and "handle" not in out


def test_a_caption_no_list_names_is_the_asking_hosts_alone():
    att = {"set.pdf": _made_up()}
    seen = {}
    for who, host in (("asked", ReviewHost(
            toolkit_options={"captions_on_every_page": True})),
            ("plain", ReviewHost())):
        handle = _call(host, "open_document", att, source="set.pdf")["handle"]
        with using(host):
            token = conversation._ATTACHMENTS.set(att)
            try:
                seen[who] = (handle, conversation._toolkit().call(
                    "document_roles", {"handle": handle, "outline": True}
                )["outline"]["captions"])
            finally:
                conversation._ATTACHMENTS.reset(token)
    assert seen["asked"][0] == seen["plain"][0]
    assert seen["plain"][1] == []
    assert [(c["page"], c["number"]) for c in seen["asked"][1]] == [(4, "8")]
