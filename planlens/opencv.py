"""Loading OpenCV without risking the process.

planlens needs OpenCV only for raster work: ``find_like``, the raster leg of
the drawing IR, and OCR. On some hosts loading OpenCV's native library does
not raise, it KILLS the interpreter: on a host whose OpenSSL enforces FIPS
mode, the bundled crypto library fails its self-test and aborts the process
(SIGABRT, exit -6 — Palantir Foundry, 2026-10-03). There is no exception to
catch, and in a web app the process is every user's session.

So before the first import in a process, :func:`available` tries the import
in a child interpreter; if the child dies, :func:`load` raises an
``ImportError`` that says why, and callers (and hosts deciding which tools to
offer) can leave raster work out instead of losing the process. The answer
is cached per process; once OpenCV is loaded nothing is checked again.

``PLANLENS_CV2_PROBE=0`` skips the child process and imports directly.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Optional, Tuple

PROBE_ENV = "PLANLENS_CV2_PROBE"
PROBE_TIMEOUT_S = 120

_lock = threading.Lock()
_verdict: Optional[Tuple[bool, str]] = None


def _already_loaded() -> bool:
    return "cv2" in sys.modules


def _probe() -> Tuple[bool, str]:
    if os.environ.get(PROBE_ENV, "1").strip().lower() in ("0", "false", "no",
                                                          "off"):
        return True, ""
    import importlib.util
    try:
        if importlib.util.find_spec("cv2") is None:
            return False, ("OpenCV is not installed "
                           "(pip install opencv-python-headless)")
    except (ImportError, ValueError):
        return False, "OpenCV is not installed"
    if not sys.executable:
        return True, ""                      # nothing to test with: import
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    try:
        run = subprocess.run(
            [sys.executable, "-c", "import cv2"], env=env,
            capture_output=True, timeout=PROBE_TIMEOUT_S,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except subprocess.TimeoutExpired:
        return False, (f"OpenCV did not load within {PROBE_TIMEOUT_S} s in "
                       "a test process")
    except OSError:
        return True, ""                      # cannot spawn here: import
    if run.returncode == 0:
        return True, ""
    tail = (run.stderr or b"").decode("utf-8", "replace").strip().splitlines()
    why = f": {tail[-1][:200]}" if tail else ""
    return False, ("OpenCV cannot load on this host (a test import exited "
                   f"with code {run.returncode}{why})")


def available() -> Tuple[bool, str]:
    """``(True, "")`` when OpenCV can be imported here without harm, else
    ``(False, reason)``. The first call may take a second or two."""
    global _verdict
    if _already_loaded():
        return True, ""
    with _lock:
        if _verdict is None:
            _verdict = _probe()
        return _verdict


def load():
    """``import cv2``, or ``ImportError`` with the reason it cannot load."""
    ok, reason = available()
    if not ok:
        raise ImportError(reason)
    import cv2
    return cv2


def _reset() -> None:
    """Forget the cached verdict (tests)."""
    global _verdict
    with _lock:
        _verdict = None


__all__ = ["available", "load", "PROBE_ENV"]
