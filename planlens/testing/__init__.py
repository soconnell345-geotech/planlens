"""Synthetic drawing fixtures — PUBLIC API for downstream consumers.

The builders here plant known constructs (leaders, dimensions, title blocks,
bubble callouts, revision clouds) on a programmatic PyMuPDF sheet and return
ground truth in the IR ``bottom_left`` frame, so a *consuming* package can
test its own wiring against planlens' constructs without shipping drawings.
:mod:`planlens.testing.visual_scale_fixtures` adds pages with known SCALES —
scanned-looking boring logs, plots, plans, profiles and chart families — with
every contact, marker, curve and distance stated, for the visual-scales code
and the app's measurement harness.

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

from planlens.testing.document_fixtures import (
    DocumentGT,
    build_synthetic_review_document,
    build_unmapped_text_pdf,
)
from planlens.testing.loggrid_fixtures import (
    LogGridGT,
    build_imperial_log,
    build_log_without_ruler,
    build_metric_log,
    build_overprinted_log,
    build_segmented_rule_log,
)
from planlens.testing.report_fixtures import (
    ReportGT,
    build_synthetic_report,
)
from planlens.testing.submittal_fixtures import (
    SubmittalGT,
    build_synthetic_submittal,
)
from planlens.testing.construct_fixtures import (
    build_synthetic_bubble_pdf,
    build_synthetic_cloud_pdf,
    build_synthetic_dimension_pdf,
    build_synthetic_drawing_set_pdf,
    build_synthetic_title_block_pdf,
)
from planlens.testing.scale_fixtures import (
    FEET_PER_POINT,
    RATIO_TEXT,
    ScaledSheetGT,
    build_synthetic_scaled_sheet_pdf,
    build_synthetic_uncalibrated_sheet_pdf,
)
from planlens.testing.visual_scale_fixtures import (
    LogVariant,
    OcrLines,
    PlanVariant,
    PlotVariant,
    ScaleFixture,
    TruthLabel,
    TruthReading,
    all_fixtures as all_scale_fixtures,
    build_chart_family,
    build_log,
    build_pit_sketch,
    build_plan,
    build_plot,
    build_profile,
    log_variants,
    plan_variants,
    plot_variants,
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
    "LogVariant",
    "OcrLines",
    "PlanVariant",
    "PlotVariant",
    "ScaleFixture",
    "TruthLabel",
    "TruthReading",
    "all_scale_fixtures",
    "build_chart_family",
    "build_log",
    "build_pit_sketch",
    "build_plan",
    "build_plot",
    "build_profile",
    "log_variants",
    "plan_variants",
    "plot_variants",
    "DocumentGT",
    "LogGridGT",
    "ReportGT",
    "build_imperial_log",
    "build_log_without_ruler",
    "build_metric_log",
    "build_overprinted_log",
    "build_segmented_rule_log",
    "build_synthetic_report",
    "FEET_PER_POINT",
    "RATIO_TEXT",
    "ScaledSheetGT",
    "build_synthetic_review_document",
    "build_unmapped_text_pdf",
    "build_synthetic_scaled_sheet_pdf",
    "build_synthetic_uncalibrated_sheet_pdf",
    "SubmittalGT",
    "build_synthetic_submittal",
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
