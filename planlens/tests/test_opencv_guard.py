"""planlens.opencv: OpenCV is test-loaded in a child process first, so a host
where loading it kills the interpreter (a FIPS-mode OpenSSL failing its
self-test: Palantir Foundry, 2026-10-03, exit -6) gets an ImportError instead
of a dead process."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from planlens import opencv


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    opencv._reset()
    monkeypatch.delenv(opencv.PROBE_ENV, raising=False)
    yield
    opencv._reset()


def _not_loaded(monkeypatch):
    monkeypatch.setattr(opencv, "_already_loaded", lambda: False)


def _child_exits(monkeypatch, code, stderr=b""):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=code, stderr=stderr)

    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr("importlib.util.find_spec",
                        lambda name, *a, **k: object())
    return calls


def test_a_child_that_dies_makes_load_refuse(monkeypatch):
    _not_loaded(monkeypatch)
    calls = _child_exits(monkeypatch, -6,
                         b"fips.c: FATAL FIPS SELFTEST FAILURE\n")
    ok, why = opencv.available()
    assert not ok and "-6" in why and "FIPS" in why
    with pytest.raises(ImportError, match="cannot load on this host"):
        opencv.load()
    assert len(calls) == 1                     # cached: probed once


def test_a_clean_child_lets_it_load(monkeypatch):
    _not_loaded(monkeypatch)
    _child_exits(monkeypatch, 0)
    assert opencv.available() == (True, "")


def test_the_switch_skips_the_child(monkeypatch):
    _not_loaded(monkeypatch)
    calls = _child_exits(monkeypatch, -6)
    monkeypatch.setenv(opencv.PROBE_ENV, "0")
    assert opencv.available() == (True, "")
    assert calls == []


def test_already_loaded_needs_no_child(monkeypatch):
    monkeypatch.setattr(opencv, "_already_loaded", lambda: True)
    calls = _child_exits(monkeypatch, -6)
    assert opencv.available() == (True, "")
    assert calls == []


def test_not_installed_says_so(monkeypatch):
    _not_loaded(monkeypatch)
    monkeypatch.setattr("importlib.util.find_spec", lambda name, *a, **k: None)
    ok, why = opencv.available()
    assert not ok and "not installed" in why


def test_find_like_raises_instead_of_dying(monkeypatch):
    fitz = pytest.importorskip("fitz")
    from planlens.document import Document
    from planlens.testing.tag_fixtures import build_synthetic_tag_set
    gt = build_synthetic_tag_set()
    monkeypatch.setattr(opencv, "available",
                        lambda: (False, "OpenCV cannot load on this host"))
    d = Document(content=gt.pdf)
    try:
        with pytest.raises(ImportError, match="cannot load"):
            d.find_like(0, gt.example_bbox)
    finally:
        d.close()


def test_the_real_probe_passes_here():
    pytest.importorskip("cv2")
    # a real child interpreter, as the first call in a process would run it
    ok, why = opencv._probe()
    assert ok, why
