"""DXF-side utilities: unit detection/conversion + native-annotation truth
extraction (:mod:`planlens.dxf.truth`). Native LEADER / MULTILEADER /
DIMENSION / ATTRIB ingest into the IR lives in :func:`planlens.ir.from_dxf`."""

from planlens.dxf.units import (  # noqa: F401
    UNIT_FACTORS,
    convert_coords,
    detect_units_from_header,
)
from planlens.dxf.truth import extract_native_annotations  # noqa: F401
