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


class TestVersionLockstep:
    def test_pyproject_and_dunder_version_agree(self):
        import planlens

        with PYPROJECT.open("rb") as fh:
            declared = tomllib.load(fh)["project"]["version"]
        assert planlens.__version__ == declared
