"""Tables and boxes read the same on PyMuPDF 1.x and 2.x (app BACKLOG INF-17).

PyMuPDF 2.0 imports ``pymupdf4llm`` and its layout analyser with ``import
pymupdf``: the first turns the ascender/descender corrections off for the
whole process ("A-1" read "A1\\n-"), the second gates ``find_tables`` on an
ONNX model (two ruled tables side by side read as one). These tests pin the
1.x reading, and they run on either major version.
"""

from __future__ import annotations

import sys
import threading

import pytest

fitz = pytest.importorskip("fitz")

from planlens.document.pdf_text import extract_text  # noqa: E402
from planlens.document.tables import extract_tables  # noqa: E402
from planlens.pymupdf_settings import line_boxes  # noqa: E402


def _grid(page, x0, y0, widths, rows):
    height = 16.0
    xs = [x0]
    for width in widths:
        xs.append(xs[-1] + width)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text(fitz.Point(xs[c] + 3, y0 + r * height + 11),
                             cell, fontsize=8.0, fontname="helv")
    y1 = y0 + len(rows) * height
    for x in xs:
        page.draw_line(fitz.Point(x, y0), fitz.Point(x, y1), width=0.6)
    for r in range(len(rows) + 1):
        page.draw_line(fitz.Point(xs[0], y0 + r * height),
                       fitz.Point(xs[-1], y0 + r * height), width=0.6)


SIEVES = [["SIEVE", "SIZE (mm)", "PERCENT FINER"],
          ["3 in", "75", "100"],
          ["No. 4", "4.75", "87"],
          ["No. 200", "0.075", "43"]]
LIMITS = [["BORING", "SAMPLE", "LL"],
          ["A-1", "S-1", "41"],
          ["A-2", "S-4", ""],
          ["A-3", "S-2", "28"]]


@pytest.fixture
def two_tables_side_by_side():
    doc = fitz.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text(fitz.Point(150, 60), "PARTICLE SIZE DISTRIBUTION",
                     fontsize=14, fontname="hebo")
    _grid(page, 60, 375, (90, 90, 90), SIEVES)
    _grid(page, 340, 375, (70, 70, 50), LIMITS)
    yield doc
    doc.close()


@pytest.fixture
def corrections_off():
    """The state PyMuPDF 2.0 leaves the process in after ``import pymupdf``."""
    before = bool(fitz.TOOLS.unset_quad_corrections())
    fitz.TOOLS.unset_quad_corrections(True)
    yield
    fitz.TOOLS.unset_quad_corrections(before)


def test_each_ruled_table_reads_as_printed(two_tables_side_by_side,
                                           corrections_off):
    tables, warnings = extract_tables(two_tables_side_by_side[0], 0)
    assert warnings == []
    assert [t.rows for t in tables] == [SIEVES, LIMITS]


def test_line_boxes_restores_the_setting_it_found(corrections_off):
    assert fitz.TOOLS.unset_quad_corrections()
    with line_boxes():
        assert not fitz.TOOLS.unset_quad_corrections()
        with line_boxes():                      # nested reads share it
            assert not fitz.TOOLS.unset_quad_corrections()
        assert not fitz.TOOLS.unset_quad_corrections()
    assert fitz.TOOLS.unset_quad_corrections()


def test_line_boxes_leaves_the_default_alone():
    before = bool(fitz.TOOLS.unset_quad_corrections())
    fitz.TOOLS.unset_quad_corrections(False)
    try:
        with line_boxes():
            assert not fitz.TOOLS.unset_quad_corrections()
        assert not fitz.TOOLS.unset_quad_corrections()
    finally:
        fitz.TOOLS.unset_quad_corrections(before)


def test_reads_on_parallel_threads_leave_no_setting_behind(
        two_tables_side_by_side, corrections_off):
    """The app reads documents on parallel tool threads. PyMuPDF keeps the
    glyph-box switches as process globals, and ``find_tables`` flips the
    small-glyph-heights one and puts back what it found; two table reads
    interleaved used to leave it on for the process (every later line box a
    font size high) and ``sys.stdout`` on ``extract_tables``' dead buffer."""
    data = two_tables_side_by_side.tobytes()
    small = bool(fitz.TOOLS.set_small_glyph_heights())
    stdout = sys.stdout
    reads, errors = [], []

    def read():
        doc = fitz.open("pdf", data)
        try:
            for _ in range(8):
                tables, _warnings = extract_tables(doc[0], 0)
                lines, _blocks, _stats = extract_text(doc[0], 0)
                reads.append(([t.rows for t in tables], lines[0].bbox))
        except Exception as exc:  # pragma: no cover - reported below
            errors.append(exc)
        finally:
            doc.close()

    threads = [threading.Thread(target=read) for _ in range(4)]
    try:
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert sys.stdout is stdout
        assert bool(fitz.TOOLS.set_small_glyph_heights()) == small
        assert fitz.TOOLS.unset_quad_corrections()
    finally:
        sys.stdout = stdout
        fitz.TOOLS.set_small_glyph_heights(small)
    assert errors == []
    assert len(reads) == 32
    assert all(r == reads[0] for r in reads)
    assert reads[0][0] == [SIEVES, LIMITS]
