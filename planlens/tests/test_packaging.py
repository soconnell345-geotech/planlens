"""Packaging invariants — what the WHEEL actually contains.

Two facts here are only observable at install time, which is exactly why they
went wrong silently:

* ``[tool.setuptools.packages.find]`` excludes ``*.tests`` / ``*.tests.*``, so
  anything under ``planlens.ir.tests`` exists in a source checkout and is
  ABSENT from a released install. The synthetic drawing fixtures lived there
  while a downstream distribution imported them — measured 2026-09-06, under a
  simulated wheel the consumer's two drawing test modules lost all 45 tests to
  ``ModuleNotFoundError``. They now live in the shipped :mod:`planlens.testing`.
* the distribution version is stored twice (``pyproject.toml`` and
  ``planlens.__version__``); a stale second copy makes an installed package
  misreport itself at runtime.

The package listing is recomputed here from the real pyproject patterns rather
than asserted against a hard-coded list, so tightening the exclude rules cannot
quietly re-strand a shipped module. setuptools is deliberately NOT imported —
its ``find_packages`` is plain fnmatch over dotted names, and requiring a build
tool would make this test skip in the very environments it protects.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PYPROJECT = REPO_ROOT / "pyproject.toml"

pytestmark = pytest.mark.skipif(
    not PYPROJECT.is_file(),
    reason="installed (non-source) tree: no pyproject.toml to read")


def _find_config() -> dict:
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    return data["tool"]["setuptools"]["packages"]["find"]


def _shipped_packages() -> set[str]:
    """Dotted names of every package the wheel would carry."""
    cfg = _find_config()
    include = cfg.get("include") or ["*"]
    exclude = cfg.get("exclude") or []
    found = set()
    for init in REPO_ROOT.glob("**/__init__.py"):
        rel = init.parent.relative_to(REPO_ROOT)
        if any(part in (".venv", "build", "dist", "__pycache__")
               for part in rel.parts):
            continue
        name = ".".join(rel.parts)
        if not any(fnmatch.fnmatchcase(name, p) for p in include):
            continue
        if any(fnmatch.fnmatchcase(name, p) for p in exclude):
            continue
        found.add(name)
    return found


class TestShippedPackages:
    def test_testing_package_ships(self):
        # The fixture builders are public API of this distribution: another
        # distribution's test suite imports them.
        assert "planlens.testing" in _shipped_packages()

    def test_ir_tests_package_does_not_ship(self):
        # Pins the reason planlens.testing exists — if this ever flips, the
        # old home would work again and the trap would come back invisibly.
        assert "planlens.ir.tests" not in _shipped_packages()

    def test_shipped_fixture_builders_are_importable(self):
        from planlens.testing import (
            build_synthetic_bubble_pdf, build_synthetic_cloud_pdf,
            build_synthetic_dimension_pdf, build_synthetic_drawing_set_pdf,
            build_synthetic_leader_pdf, build_synthetic_title_block_pdf,
        )
        for fn in (build_synthetic_leader_pdf, build_synthetic_dimension_pdf,
                   build_synthetic_title_block_pdf, build_synthetic_bubble_pdf,
                   build_synthetic_cloud_pdf, build_synthetic_drawing_set_pdf):
            assert callable(fn)

    def test_shipped_fixtures_need_no_test_dependency(self):
        # A shipped module must not drag pytest into the runtime package.
        import planlens.testing.construct_fixtures as cf
        import planlens.testing.leader_fixtures as lf
        for mod in (lf, cf):
            src = Path(mod.__file__).read_text(encoding="utf-8")
            assert "import pytest" not in src

    def test_old_import_path_still_delegates(self):
        # Out-of-tree consumers on the old path keep working — including the
        # private helpers, which `import *` would not carry.
        from planlens.ir.tests.leader_fixtures import (
            _draw_shaft, _pt, _to_ir, build_synthetic_leader_pdf,
        )
        from planlens.testing.leader_fixtures import (
            build_synthetic_leader_pdf as shipped,
        )
        assert build_synthetic_leader_pdf is shipped
        assert callable(_draw_shaft) and callable(_pt) and callable(_to_ir)


class TestDependencies:
    """What a plain `pip install planlens` pulls in, and what it does not.

    0.4.0 folded the `raster` and `text` extras into core: an install without
    OpenCV read a scanned sheet as an empty drawing, and one without rapidfuzz
    refused the fuzzy search the tools advertise. Both names stay as EMPTY
    extras so `planlens[raster]` / `planlens[text]` keep resolving. What must
    NOT follow is eager importing — a core dependency is still paid for at the
    moment it is used.
    """

    CORE = {"numpy", "ezdxf", "pymupdf", "opencv-python-headless", "rapidfuzz"}

    @staticmethod
    def _project() -> dict:
        with PYPROJECT.open("rb") as fh:
            return tomllib.load(fh)["project"]

    @staticmethod
    def _names(requirements) -> set[str]:
        import re
        return {re.split(r"[\[<>=!~; ]", r, maxsplit=1)[0].strip().lower()
                for r in requirements}

    def test_core_dependencies_are_exactly_the_documented_five(self):
        assert self._names(self._project()["dependencies"]) == self.CORE

    def test_the_folded_extras_stay_as_empty_aliases(self):
        extras = self._project()["optional-dependencies"]
        # Kept so an existing `pip install "planlens[raster]"` still resolves.
        assert extras["raster"] == []
        assert extras["text"] == []

    def test_the_mcp_sdk_is_an_extra_not_a_dependency(self):
        project = self._project()
        assert "mcp" not in self._names(project["dependencies"])
        # Bounded to the major: v1 and v2 of the SDK are different APIs, so an
        # unbounded pin would install a version this server cannot run on.
        assert project["optional-dependencies"]["mcp"] == ["mcp>=2,<3"]

    def test_the_ocr_engine_stays_optional(self):
        # It hard-requires the GUI OpenCV build, which owns the same `cv2`
        # namespace as the headless build core installs.
        project = self._project()
        assert "rapidocr-onnxruntime" not in self._names(
            project["dependencies"])
        assert project["optional-dependencies"]["ocr"]

    def test_importing_planlens_loads_no_heavy_dependency(self):
        # `import planlens` must cost nothing but the version string: cv2
        # alone is tens of MB of shared libraries. Run out-of-process because
        # the test session has already imported plenty.
        import subprocess
        import sys
        probe = (
            "import sys; import planlens;"
            "heavy = [m for m in sys.modules"
            " if m.split('.')[0] in {'cv2', 'rapidfuzz', 'mcp', 'fitz',"
            " 'pymupdf', 'ezdxf', 'rapidocr_onnxruntime'}];"
            "print(','.join(sorted(heavy)))"
        )
        out = subprocess.run([sys.executable, "-c", probe], check=True,
                             capture_output=True, text=True).stdout.strip()
        assert out == "", f"import planlens pulled in: {out}"

    def test_the_server_module_is_reachable_without_the_mcp_sdk(self):
        # The server module is reachable by name and imported by nobody: the
        # import guard inside it is the only thing that ever mentions `mcp`.
        src = (REPO_ROOT / "planlens" / "__init__.py").read_text(
            encoding="utf-8")
        assert "mcp" not in src
        assert (REPO_ROOT / "planlens" / "mcp_server.py").is_file()

    def test_the_console_script_points_at_the_server(self):
        assert self._project()["scripts"]["planlens-mcp"] == \
            "planlens.mcp_server:main"


class TestVersionLockstep:
    def test_pyproject_and_dunder_version_agree(self):
        import planlens

        with PYPROJECT.open("rb") as fh:
            declared = tomllib.load(fh)["project"]["version"]
        assert planlens.__version__ == declared
