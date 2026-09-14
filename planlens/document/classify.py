"""Coarse page kinds, from cheap measurements, with the evidence attached.

The page map exists so an agent can find the right page of a long document
without asking the user — "the boring logs start around page 28", "pages 7-13
are drawing sheets". It is deliberately coarse, and every call carries the
numbers and the rule that produced it, so a caller who disagrees can see why.

Kinds:

- ``drawing_sheet`` — larger than tabloid (11x17) with linework, images or CAD
  text. Large-format pages are almost always drawings.
- ``scanned`` — letter/tabloid page that is mostly image with no text layer.
- ``form`` — a ruled grid (many horizontal AND vertical rules) on a letter or
  tabloid page: boring logs, schedules, printed tables. Measured: boring logs
  23-44 x 32-39 rules, prose pages 0 x 0, program printouts under 10.
- ``figure`` — heavy linework or image coverage with modest text.
- ``text`` — text-dense page with little graphics (prose, calcs, printouts).
- ``blank`` — nothing on it.
- ``mixed`` — none of the above clearly.

``needs_ocr`` is reported separately from the kind: a scanned drawing sheet is
still a drawing sheet, and a scanned page whose text already came from an
optical source (OCR, Azure) stays ``scanned`` without asking for OCR again.
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

#: Tabloid (11x17 in) is 187 sq in; anything clearly bigger is large format.
LARGE_FORMAT_IN2 = 250.0

#: Axis-aligned rules in EACH direction for a page to read as a ruled form.
FORM_MIN_RULES = 12


def classify_page(width_pt: float, height_pt: float, n_text_chars: int,
                  n_cad_text_chars: int, n_vector_paths: int,
                  image_coverage: float,
                  text_is_optical: bool = False,
                  ruling_h: int = 0, ruling_v: int = 0
                  ) -> Tuple[str, Dict[str, Any]]:
    """Return ``(kind, evidence)`` for one page's measurements.

    ``text_is_optical`` says ``n_text_chars`` came from reading the page image
    (OCR / Azure) rather than from the PDF text layer. ``ruling_h`` /
    ``ruling_v`` count long axis-aligned drawn lines.
    """
    area_in2 = (width_pt / 72.0) * (height_pt / 72.0)
    chars = n_text_chars + n_cad_text_chars
    image_only = image_coverage >= 0.5 and (
        n_vector_paths < 50 if text_is_optical else chars < 50)
    needs_ocr = image_only and not text_is_optical

    if chars < 5 and n_vector_paths < 5 and image_coverage < 0.02:
        kind, rule = "blank", "no text, linework or images"
    elif area_in2 >= LARGE_FORMAT_IN2 and (
            n_vector_paths >= 200 or image_coverage >= 0.5
            or n_cad_text_chars > 0):
        kind, rule = "drawing_sheet", "large format with linework/images"
    elif image_only:
        kind, rule = "scanned", ("mostly image, text read optically"
                                 if text_is_optical else
                                 "mostly image, no text layer")
    elif ruling_h >= FORM_MIN_RULES and ruling_v >= FORM_MIN_RULES:
        kind, rule = "form", "ruled grid in both directions"
    elif chars < 1500 and (n_vector_paths >= 300 or image_coverage >= 0.35):
        kind, rule = "figure", "heavy graphics, modest text"
    elif chars >= 600 and n_vector_paths < 300 and image_coverage < 0.35:
        kind, rule = "text", "text-dense, light graphics"
    else:
        kind, rule = "mixed", "no single dominant content type"

    evidence: Dict[str, Any] = {
        "rule": rule,
        "text_chars": n_text_chars,
        "vector_paths": n_vector_paths,
        "image_coverage": round(float(image_coverage), 2),
    }
    if n_cad_text_chars:
        evidence["cad_text_chars"] = n_cad_text_chars
    if needs_ocr:
        evidence["needs_ocr"] = True
    return kind, evidence
