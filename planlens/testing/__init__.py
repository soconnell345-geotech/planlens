"""Synthetic drawing fixtures — PUBLIC API for downstream consumers.

The builders here plant known constructs (leaders, dimensions, title blocks,
bubble callouts, revision clouds) on a programmatic PyMuPDF sheet and return
ground truth in the IR ``bottom_left`` frame, so a *consuming* package can
test its own wiring against planlens' constructs without shipping drawings.

They live in a shipped package on purpose. They were born under
``planlens.ir.tests``, which ``[tool.setuptools.packages.find]`` excludes
from the wheel (``exclude = ["*.tests", "*.tests.*"]``) — so every
out-of-tree import of them worked from a source checkout and broke on a
released install (the app's two drawing test modules lost all 45 tests to
``ModuleNotFoundError`` under a simulated wheel, 2026-09-06). A fixture
builder that a DIFFERENT distribution imports is public API of this one;
``planlens.ir.tests`` keeps thin re-export shims for the transition.

Nothing here imports pytest, so this adds no test-only dependency to the
runtime package; ``fitz`` (PyMuPDF, a hard dependency) is imported lazily
inside the builders.
"""

from planlens.testing.construct_fixtures import (
    build_synthetic_bubble_pdf,
    build_synthetic_cloud_pdf,
    build_synthetic_dimension_pdf,
    build_synthetic_drawing_set_pdf,
    build_synthetic_title_block_pdf,
)
from planlens.testing.leader_fixtures import (
    ARROW_HALF_WIDTH,
    ARROW_LENGTH,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    LeaderGT,
    build_synthetic_leader_pdf,
)

__all__ = [
    "ARROW_HALF_WIDTH",
    "ARROW_LENGTH",
    "PAGE_HEIGHT",
    "PAGE_WIDTH",
    "LeaderGT",
    "build_synthetic_bubble_pdf",
    "build_synthetic_cloud_pdf",
    "build_synthetic_dimension_pdf",
    "build_synthetic_drawing_set_pdf",
    "build_synthetic_leader_pdf",
    "build_synthetic_title_block_pdf",
]
