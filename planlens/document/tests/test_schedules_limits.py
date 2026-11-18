"""The schedule-marks check's four known limits, three settled and one
narrowed (app BACKLOG PL-43): never a definite ``no_mark`` or ``no_row``
where the set's text cannot settle it, and never a looser search that
finds new false marks.
Each test builds a made-up set where the check of 0.16.0 said something
definite it could not know, or said nothing it could say soundly. (Limit 2,
a scan away from the schedule, is in ``test_schedules_hard_cases``.) Made-up
sheets only."""

import pytest

pymupdf = pytest.importorskip("pymupdf")

from planlens.document import open_document  # noqa: E402
from planlens.document.schedules import (  # noqa: E402
    CANNOT_TELL, MAYBE_UNREAD, NO_MARK, NO_ROW,
)
from planlens.testing.schedule_set_fixtures import table  # noqa: E402
from planlens.testing.sheet_set_fixtures import sketch_pdf  # noqa: E402

NOTE = "GENERAL NOTES: VERIFY ALL DIMENSIONS ON SITE"


def _result(pdf):
    with open_document(pdf) as doc:
        return doc.schedule_marks()


def _items(result):
    return sorted((i.mark, i.status) for i in result.items)


def _set(*sheets):
    """One sheet per ``(number, painter)``, each with a strip title block."""
    def frame(number, paint, n, of):
        def draw(s, i):
            s.strip_block(number, "SHEET", n, of)
            paint(s)
        return draw
    return sketch_pdf([frame(num, p, k + 1, len(sheets))
                       for k, (num, p) in enumerate(sheets)])


def _plan(*painters, note=True):
    """A plan: a general note in text (so its drawing's text is read),
    then each painter."""
    def paint(s):
        if note:
            s.centred(700, 300, NOTE, 8)
        for p in painters:
            p(s)
    return paint


def _tags(marks, y=600, size=7, bold=False):
    def paint(s):
        for k, m in enumerate(marks):
            s.centred(400 + 200 * k, y, m, size, bold)
    return paint


def _lines_only(marks, y=600, hidden=True):
    """Tags drawn as lines; a mark given (and ``hidden``) is kept as a CAD
    program keeps SHX lettering, in a hidden 'AutoCAD SHX Text' annotation;
    ``None`` is a tag drawn as lines with nothing kept."""
    def paint(s):
        for k, m in enumerate(marks):
            x = 400 + 200 * k
            s.page.draw_line((x - 10, y - 3), (x + 10, y + 3), width=0.4)
            if m is None or not hidden:
                continue
            a = s.page.add_freetext_annot(
                pymupdf.Rect(x - 18, y - 5, x + 18, y + 4), m, fontsize=6)
            a.set_info(title="AutoCAD SHX Text", content=m)
            a.set_flags(pymupdf.PDF_ANNOT_IS_HIDDEN)
            a.update()
    return paint


def _fans(rows=("EF-1", "EF-2", "EF-3")):
    return lambda s: table(
        s, 150, 220, "EXHAUST FAN SCHEDULE", ("MARK", "SERVES", "CFM"),
        [(r, "TOILETS", "400") for r in rows], (90, 120, 60))


def _doors(rows):
    return lambda s: table(
        s, 150, 220, "DOOR SCHEDULE", ("NO.", "WIDTH", "HEIGHT"),
        [(n, "915", "2135") for n in rows], (60, 60, 60))


# -- limit 1: a few marks drawn as lines -------------------------------------

def test_rows_read_from_hidden_cad_text_stay_no_mark_where_every_sheet_has_it():
    """Two fan tags are drawn as lines with their hidden CAD text, the
    third as lines with none (a tag exploded). Where every sheet carries
    hidden CAD text the set was plotted whole with it, so a tag drawn as
    lines anywhere is read: EF-3 is ``no_mark``, as in 0.16.0 and as for
    tags set in a font. M-601, of another discipline than A-101 where the
    rows are found, does not count with or without hidden text."""
    def fans_with_hidden_note(s):
        _fans()(s)
        _lines_only(["SEE SPECIFICATIONS"], y=700)(s)
    tags = _plan(_lines_only(["EF-1", "EF-2", None]))
    whole = _result(_set(("A-101", tags), ("M-601", fans_with_hidden_note)))
    assert _items(whole) == [("EF-3", NO_MARK)]
    assert not any("hidden text a CAD program keeps" in n
                   for n in whole.notes)
    other = _result(_set(("A-101", tags), ("M-601", _fans())))
    assert _items(other) == [("EF-3", NO_MARK)]
    assert other.unlettered == []

    in_font = _result(_set(("A-101", _plan(_tags(["EF-1", "EF-2"]))),
                           ("M-601", _fans())))
    assert _items(in_font) == [("EF-3", NO_MARK)]


def test_a_sheet_plotted_without_the_hidden_cad_text_leaves_rows_open():
    """The same set with A-102, a plan of the discipline the rows are
    found on, whose notes are text and which carries no hidden CAD text
    (plotted apart): a tag drawn as lines on A-102 would not be read.
    0.16.0 said EF-3 ``no_mark``; now it is ``cannot_tell``, and the why
    names A-102."""
    result = _result(_set(("A-101", _plan(_lines_only(["EF-1", "EF-2",
                                                       None]))),
                          ("A-102", _plan()),
                          ("M-601", _fans())))
    assert _items(result) == [("EF-3", CANNOT_TELL)]
    why = result.items[0].why
    assert "2 of this schedule's 2 rows found are drawn as lines" in why
    assert ("(on A-101), and A-102, which could show it, carries none of "
            "that text") in why
    assert why.endswith("view A-102")
    assert any("sheets plotted apart" in n for n in result.notes)
    assert result.unlettered == []


def test_a_sheet_with_no_text_outside_its_title_block_leaves_rows_open():
    """A-101 tags EF-1 and EF-2 in text; A-102's drawing is lettered as
    lines with nothing kept, so its only text is its title block. 0.16.0
    said EF-3 ``no_mark`` (two of three rows found); its tag may be on
    A-102, which is not read."""
    result = _result(_set(("A-101", _plan(_tags(["EF-1", "EF-2"]))),
                          ("A-102", _plan(_lines_only([None, None]),
                                          note=False)),
                          ("M-601", _fans())))
    assert _items(result) == [("EF-3", MAYBE_UNREAD)]
    assert result.unlettered == [1]
    assert ("1 sheet(s) have no text outside their title block, so their "
            "drawing is lettered as lines if at all, and not read (page(s) "
            "1)") in result.items[0].why
    assert any("no text outside their title block" in n
               for n in result.notes)


# -- limit 3: marks of one or two characters ---------------------------------

def test_a_short_mark_drawn_nowhere_is_told_and_never_definite():
    """A fixture schedule of types A, B, C; the plan tags A and B, and a
    stray D. 0.16.0 looked for none of them and said nothing. Now C,
    drawn alone on no sheet, is ``cannot_tell`` (a found A or B may be a
    grid line, so nothing shows the tags are text); D, a letter with no
    row, is still not looked for: every grid letter would be one."""
    fixtures = lambda s: table(  # noqa: E731
        s, 150, 220, "LIGHT FIXTURE SCHEDULE",
        ("MARK", "DESCRIPTION", "LAMPS"),
        [(m, "TROFFER", "2") for m in ("A", "B", "C")], (60, 120, 60))
    result = _result(_set(("E-101", _plan(_tags(["A", "B", "D"]))),
                          ("E-601", fixtures)))
    assert result.schedules[0].marks == ["A", "B", "C"]
    assert _items(result) == [("C", CANNOT_TELL)]
    assert result.items[0].why.startswith(
        "row C is drawn alone on no sheet; but a mark of one or two "
        "characters is not counted found")
    s = result.schedules[0]
    assert (s.rows_found, s.rows_not_looked_for) == (0, 3)


# -- limit 4: plain numbers on a sheet with fewer than two marks -------------

def test_a_plain_number_on_a_sheet_without_two_marks_borrows_the_lettering():
    """Doors 101 to 103 are tagged in bold on A-101; A-102 tags 201 and
    202 the same way, and the schedule has no row for either. 0.16.0 did
    not look at A-102 (it shows none of the schedule's marks) and said
    nothing. Now each is ``cannot_tell`` in the lettering borrowed from
    A-101, never ``no_row``. Bold numbers on S-101, another discipline's
    sheet, are not looked at."""
    result = _result(_set(
        ("A-101", _plan(_tags(["101", "102", "103"], bold=True))),
        ("A-102", _plan(_tags(["201", "202"], bold=True))),
        ("S-101", _plan(_tags(["450", "600"], bold=True))),
        ("A-601", _doors(["101", "102", "103"]))))
    assert _items(result) == [("201", CANNOT_TELL), ("202", CANNOT_TELL)]
    why = result.items[0].why
    assert "lettering this schedule's marks have on A-101" in why
    assert "shows fewer than two of the schedule's marks" in why

    # a floor whose doors the schedule leaves out: one item for the sheet
    floor = _result(_set(
        ("A-101", _plan(_tags(["101", "102", "103"], bold=True))),
        ("A-102", _plan(_tags(["301", "302", "303", "304", "305"],
                              bold=True))),
        ("A-601", _doors(["101", "102", "103"]))))
    assert [(i.status, i.n_marks, i.marks) for i in floor.items] == [
        (CANNOT_TELL, 5, ["301", "302", "303", "304", "305"])]
    assert "a floor it leaves out" in floor.items[0].why

    # in another lettering than the marks have on A-101: not looked at
    plain = _result(_set(
        ("A-101", _plan(_tags(["101", "102", "103"], bold=True))),
        ("A-102", _plan(_tags(["201", "202"], size=9))),
        ("A-601", _doors(["101", "102", "103"]))))
    assert plain.items == []


def test_a_number_told_where_the_marks_settle_it_is_not_told_again():
    """Door 109, with no row, is tagged on A-101 among doors 101 to 103 in
    their lettering, where it is ``no_row``, and again on A-401, an
    enlarged plan where it is the only door. The lettering borrowed for
    A-401 adds no second item for 109: one mark, one item. 110, tagged
    only on A-401, is still ``cannot_tell`` there."""
    def doors_on(enlarged):
        return _result(_set(
            ("A-101", _plan(_tags(["101", "102", "103", "109"],
                                  bold=True))),
            ("A-401", _plan(_tags(enlarged, bold=True))),
            ("A-601", _doors(["101", "102", "103"]))))
    alone = doors_on(["109"])
    assert [(i.mark, i.status, i.sheet) for i in alone.items] == [
        ("109", NO_ROW, "A-101")]
    beside = doors_on(["109", "110"])
    assert sorted((i.mark, i.status, i.sheet) for i in beside.items) == [
        ("109", NO_ROW, "A-101"), ("110", CANNOT_TELL, "A-401")]
