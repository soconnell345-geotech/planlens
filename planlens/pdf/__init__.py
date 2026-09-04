"""
PDF Import Module for Geotechnical Cross-Section Extraction

Extracts geometry from PDF drawings using two methods:
    1. Vector extraction — PyMuPDF path analysis (exact, requires role_mapping)
    2. Vision extraction — LLM image analysis (approximate, any drawing)

Output is compatible with both slope_stability and fem2d via
to_dxf_parse_result() adapter for use with build_slope_geometry() and
build_fem_inputs().

Requires: PyMuPDF >= 1.23 (optional dependency)
"""

from planlens.pdf.results import PdfParseResult
from planlens.pdf.extractor import (
    discover_pdf_content, extract_vector_geometry, extract_colored_paths,
)
from planlens.pdf.vision import extract_geometry_vision, render_page_with_grid
from planlens.pdf.scale import (
    calibrate_scale, parse_scale_annotations, propose_scale,
)
from planlens.pdf.labels import (
    classify_label, associate_labels_to_regions, propose_role_mapping,
)
from planlens.pdf.cleanup import (
    dedupe_consecutive, merge_collinear, cleanup_polyline,
    snap_endpoints, join_polylines, cleanup_geometry,
)
from planlens.pdf.crosscheck import cross_check, polyline_deviation

__all__ = [
    'PdfParseResult',
    'discover_pdf_content',
    'extract_vector_geometry',
    'extract_colored_paths',
    'extract_geometry_vision',
    'render_page_with_grid',
    'calibrate_scale',
    'parse_scale_annotations',
    'propose_scale',
    'classify_label',
    'associate_labels_to_regions',
    'propose_role_mapping',
    'dedupe_consecutive',
    'merge_collinear',
    'cleanup_polyline',
    'snap_endpoints',
    'join_polylines',
    'cleanup_geometry',
    'cross_check',
    'polyline_deviation',
]
