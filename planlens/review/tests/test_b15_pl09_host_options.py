"""PL-09: the three reading options through the host layer, each for a host
that asks (``ReviewHost(toolkit_options={"column_warning": True})``,
``"continued_tables"``, ``"captions_on_every_page"``), off by default. The
hosts of one conversation share its toolkit, so a host that did not ask must
never be answered from a document a host that asked opened: two hosts on one
file, in both orders. Made-up pages only."""

import json

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens import review  # noqa: E402
from planlens.document.tests.test_pl09_captions import _report  # noqa: E402
from planlens.document.tests.test_pl09_continued_tables import (  # noqa: E402
    _doc as _two_page_table,
)
from planlens.document.tests.test_pl09_reading_order import (  # noqa: E402
    ACROSS, _columns,
)
from planlens.review import conversation  # noqa: E402
from planlens.review.host import ReviewHost, using  # noqa: E402
from planlens.tools import ReviewToolkit  # noqa: E402

OPTIONS = ("column_warning", "continued_tables", "captions_on_every_page")
NOTES = ("note: continues on page 1 as table ",
         "note: continues table ")

#: option -> (the made-up file, the read_document arguments, the lines the
#: option adds to the text).
CASES = {
    "column_warning": (lambda: _columns(2), {"pages": "0"}, (ACROSS,)),
    "continued_tables": (_two_page_table,
                         {"pages": "0-1", "include_tables": True}, NOTES),
}


def _read(host, att, handle="set.pdf", **arguments):
    return json.loads(review.call(
        "read_document", dict(arguments, handle=handle), att, host=host))


def _same_but_for(asked, plain, added):
    """``asked`` is ``plain`` with the ``added`` lines in its text and its
    own handle, and nothing else."""
    assert asked["handle"] != plain["handle"]
    rows = asked["text"].split("\n")
    extra = [r for r in rows if any(a in r for a in added)]
    assert len(extra) == len(added), asked["text"]
    kept = "\n".join(r for r in rows if r not in extra)
    assert kept == plain["text"]
    swap = lambda out: {  # noqa: E731
        k: (v.replace(out["handle"], "H") if isinstance(v, str) else v)
        for k, v in out.items() if k != "text"}
    assert swap(asked) == swap(plain)


@pytest.mark.parametrize("option", sorted(CASES))
@pytest.mark.parametrize("asked_first", [False, True])
def test_two_hosts_on_one_file_each_get_their_own_reading(option,
                                                          asked_first):
    build, arguments, added = CASES[option]
    att = {"set.pdf": build()}
    # what a host is answered with nobody else in the conversation
    alone = _read(ReviewHost(), att, **arguments)
    assert not any(a in alone["text"] for a in added)
    conversation._SPACES.clear()
    plain, asked = ReviewHost(), ReviewHost(toolkit_options={option: True})
    outs = {}
    for host in ((asked, plain) if asked_first else (plain, asked)):
        outs[host is asked] = _read(host, att, **arguments)
    assert outs[False] == alone
    _same_but_for(outs[True], alone, added)
    # the handle a host that asked was given, in the hands of one that did
    # not: the same file read without the option (the result names the
    # handle the call gave)
    again = _read(plain, att, handle=outs[True]["handle"], **arguments)
    assert again == {k: v for k, v in dict(
        alone, handle=outs[True]["handle"]).items() if k != "opened"}
    # and the other way round
    theirs = _read(asked, att, handle=alone["handle"], **arguments)
    assert theirs["text"] == outs[True]["text"]
    # every later answer to the host that did not ask is still the plain one
    assert _read(plain, att, **arguments) == alone


@pytest.mark.parametrize("option", sorted(CASES))
def test_each_option_is_asked_for_by_itself(option):
    """Asking for the other two options does not turn this one on."""
    build, arguments, added = CASES[option]
    att = {"set.pdf": build()}
    others = {o: True for o in OPTIONS if o != option}
    out = _read(ReviewHost(toolkit_options=others), att, **arguments)
    assert not any(a in out["text"] for a in added)


def _outline(kit, handle=None):
    handle = handle or kit.call("open_document",
                                {"source": "report.pdf"})["handle"]
    out = kit.call("document_roles", {"handle": handle, "outline": True})
    return out["outline"]


def test_the_captions_are_the_calling_hosts_on_the_shared_toolkit():
    """``document_roles`` is the toolkit's tool (the host layer does not
    serve it): on the conversation's shared toolkit its outline reads the
    captions on every page only under a host that asked, on one document."""
    att = {"report.pdf": _report()}
    plain = ReviewHost()
    asked = ReviewHost(toolkit_options={"captions_on_every_page": True})
    seen = []
    for host in (asked, plain, asked, plain):
        with using(host):
            token = conversation._ATTACHMENTS.set(att)
            try:
                kit = conversation._toolkit()
                handle = kit.call("open_document",
                                  {"source": "report.pdf"})["handle"]
                seen.append((handle, _outline(kit, handle)))
            finally:
                conversation._ATTACHMENTS.reset(token)
    assert len({handle for handle, _ in seen}) == 1
    assert seen[0][1] == seen[2][1] and seen[1][1] == seen[3][1]
    assert seen[1][1]["captions"] == []
    assert seen[1][1]["n_entries_placed"] == 0
    assert [(c["page"], c["number"]) for c in seen[0][1]["captions"]] == [
        (2, "1"), (3, "2")]
    assert seen[0][1]["n_entries_placed"] == 2


def test_a_toolkit_takes_each_option_and_is_plain_without():
    data = _report()
    plain = ReviewToolkit(resolve_source=lambda s: data)
    asked = ReviewToolkit(resolve_source=lambda s: data,
                          captions_on_every_page=True)
    assert _outline(plain)["captions"] == []
    assert len(_outline(asked)["captions"]) == 2
    for option, (build, arguments, added) in CASES.items():
        data = build()
        kit = ReviewToolkit(resolve_source=lambda s: data, **{option: True})
        handle = kit.call("open_document", {"source": "set.pdf"})["handle"]
        out = kit.call("read_document", dict(arguments, handle=handle))
        assert all(a in out["text"] for a in added), option


def test_asking_moves_no_tool_name_and_no_specification():
    """0 characters: the options change results only."""
    plain = ReviewHost()
    asked = ReviewHost(toolkit_options={o: True for o in OPTIONS})
    with using(plain):
        names, specs = review.tool_names(), review.tool_specs()
    with using(asked):
        assert review.tool_names() == names
        assert review.tool_specs() == specs
    assert json.dumps(ReviewToolkit(**{o: True for o in OPTIONS}).specs()) \
        == json.dumps(ReviewToolkit().specs())
