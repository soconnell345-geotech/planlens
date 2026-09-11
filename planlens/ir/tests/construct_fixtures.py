"""Compatibility shim — the construct fixtures moved to :mod:`planlens.testing`.

See :mod:`planlens.ir.tests.leader_fixtures` for why: this package is excluded
from the wheel, so the old home never survived a released install. Import from
``planlens.testing.construct_fixtures``.
"""

from planlens.testing.construct_fixtures import *  # noqa: F401,F403
from planlens.testing.construct_fixtures import (  # noqa: F401
    Point, _plant_cloud, _plant_dimension, _steps,
    build_synthetic_bubble_pdf, build_synthetic_cloud_pdf,
    build_synthetic_dimension_pdf, build_synthetic_drawing_set_pdf,
    build_synthetic_title_block_pdf,
)
