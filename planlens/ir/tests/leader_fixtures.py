"""Compatibility shim — the leader fixtures moved to :mod:`planlens.testing`.

They are imported by a downstream distribution (the GeotechStaffEngineer app's
drawing tests), and this package is EXCLUDED from the wheel
(``exclude = ["*.tests", "*.tests.*"]``), so the old home only ever worked
from a source checkout. Import from ``planlens.testing.leader_fixtures``.

This shim stays for out-of-tree consumers still on the old path; the private
helpers are re-exported by name because ``import *`` does not carry
underscore-prefixed names and sibling test modules import them directly.
"""

from planlens.testing.leader_fixtures import *  # noqa: F401,F403
from planlens.testing.leader_fixtures import (  # noqa: F401
    ARROW_HALF_WIDTH, ARROW_LENGTH, LeaderGT, PAGE_HEIGHT, PAGE_WIDTH, Point,
    _arrowhead_at, _draw_filled_triangle, _draw_shaft, _plant_dimension_decoy,
    _plant_leader, _pt, _to_ir, _unit, build_synthetic_leader_pdf,
)
