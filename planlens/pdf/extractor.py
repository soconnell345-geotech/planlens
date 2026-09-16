"""
Programmatic vector extraction from PDF files via PyMuPDF.

Uses page.get_drawings() to extract vector paths, groups them by stroke color,
and assigns geometric roles via user-supplied role_mapping.

Each path also carries its OPTIONAL-CONTENT GROUP (a PDF's layer) and how it
is PAINTED — see :func:`extract_colored_paths` and :func:`layer_state`.

Requires: PyMuPDF >= 1.23 (optional dependency)
"""

import math
import tempfile
from typing import Any, Dict, List, Optional, Tuple

from planlens.pdf.results import PdfParseResult


def _import_fitz():
    """Import PyMuPDF with helpful error message."""
    try:
        import fitz
        return fitz
    except ImportError:
        raise ImportError(
            "PyMuPDF (fitz) is required for PDF vector extraction. "
            "Install with: pip install PyMuPDF>=1.23"
        )


def _open_document(filepath=None, content=None):
    """Open PDF document from filepath or bytes content."""
    fitz = _import_fitz()
    if content is not None:
        if isinstance(content, (bytes, bytearray)):
            return fitz.open(stream=content, filetype="pdf")
        raise TypeError("content must be bytes or bytearray")
    if filepath is not None:
        return fitz.open(filepath)
    raise ValueError("Provide either filepath or content")


def layer_state(doc) -> Dict[str, bool]:
    """Every optional-content group in the document: name -> default ON.

    An OCG is a PDF's layer. AutoCAD's PDF export writes one per CAD layer
    (measured on the validation corpus: sheet 10.31A carries ``0``, ``BORDER``,
    ``TEXT``, ``REV``, ``PROPOSED``), so this is how a plotted sheet can still
    say "existing" vs "proposed" after the CAD file is gone.

    ``False`` means the group is HIDDEN when the document is opened — and
    MuPDF hides it too, so nothing on it reaches ``get_drawings()`` unless
    :func:`enable_all_layers` turns it on first.

    The dictionary can be EMPTY on a document whose paths nevertheless carry
    layer names — measured on a real submittal outside the validation corpus,
    which declares no groups at all while its sheets' paths carry dozens of
    distinct names each. A path's name comes from the marked content it sits
    in, which need not be registered in ``/OCProperties``, so this summary is
    a supplement to the names the paths carry — never the authority on which
    layers a page uses.
    """
    out: Dict[str, bool] = {}
    for info in (doc.get_ocgs() or {}).values():
        name = info.get("name")
        if name:
            out[str(name)] = bool(info.get("on"))
    return out


def enable_all_layers(doc) -> List[str]:
    """Turn every hidden optional-content group ON; return the names turned on.

    ``action=0`` is "set this layer ON" in PyMuPDF's ``layer_ui_configs``
    vocabulary (verified against a two-OCG synthetic: with the group off,
    ``get_drawings()`` omits its paths entirely; after this call the same
    paths arrive carrying the group's name).
    """
    turned: List[str] = []
    for cfg in doc.layer_ui_configs() or ():
        if cfg.get("on"):
            continue
        try:
            doc.set_layer_ui_config(cfg["number"], action=0)
        except Exception:  # pragma: no cover - defensive
            continue
        turned.append(str(cfg.get("text") or cfg["number"]))
    return turned


def _snap_rotation(deg: float, tol: float = 0.5) -> float:
    nearest = round(deg / 90.0) * 90.0
    if abs(deg - nearest) <= tol:
        deg = nearest
    deg = round(deg % 360.0, 1)
    return 0.0 if deg >= 359.95 else deg


def _page_text_dict_without_annotations(page) -> dict:
    """``get_text("dict")`` of the page content only, in the DISPLAYED frame.

    Ordinary ``get_text`` also reads text drawn by annotation appearances, so a
    reviewer's comment would be ingested as drawing text. See
    ``planlens.document.pdf_text`` for the verification of both properties.
    """
    fitz = _import_fitz()
    flags = (fitz.TEXT_PRESERVE_LIGATURES | fitz.TEXT_PRESERVE_WHITESPACE
             | fitz.TEXT_MEDIABOX_CLIP)
    stext = page.get_displaylist(annots=False).get_textpage(flags)
    tp = stext if isinstance(stext, fitz.TextPage) else fitz.TextPage(stext)
    tp.parent = page
    return page.get_text("dict", textpage=tp)


def _color_to_hex(color) -> str:
    """Convert PyMuPDF color tuple to hex string."""
    if color is None or len(color) == 0:
        return "#000000"
    if len(color) == 1:
        # Grayscale
        v = int(color[0] * 255)
        return f"#{v:02x}{v:02x}{v:02x}"
    if len(color) == 3:
        r, g, b = (int(c * 255) for c in color)
        return f"#{r:02x}{g:02x}{b:02x}"
    return "#000000"


def _fill_to_hex(color) -> Optional[str]:
    """A fill colour in the SAME hex spelling ``_color_to_hex`` gives a stroke.

    The difference is the absence: a stroke always has a colour, so
    ``_color_to_hex`` answers ``"#000000"`` when the PDF names none, while a
    path may genuinely have no fill at all. That case is ``None`` here, and
    only that case — an unpainted path says nothing about fill rather than
    claiming to be black.
    """
    if color is None or len(color) == 0:
        return None
    return _color_to_hex(color)


def discover_pdf_content(
    filepath=None, content=None, page: int = 0,
    include_hidden_layers: bool = False,
) -> Dict[str, Any]:
    """Inventory a PDF page: vector paths by color, text blocks, dimensions.

    Parameters
    ----------
    filepath : str, optional
        Path to PDF file.
    content : bytes, optional
        PDF file content as bytes.
    page : int
        Page number (0-indexed).
    include_hidden_layers : bool
        Read content on optional-content groups that are OFF by default
        (see :func:`enable_all_layers`). Off by default, so what is reported
        is what the document SHOWS when opened.

    Returns
    -------
    dict with keys:
        'page_size' : dict with 'width' and 'height' in points
        'n_drawings' : int — total vector path count
        'colors' : dict — {hex_color: count}
        'text_blocks' : list of dict — {text, x, y, size, rotation}
        'has_images' : bool — whether page contains raster images
        'ocgs' : dict — {layer name: default ON}, DOCUMENT-level
          (:func:`layer_state`); empty when the document declares none

    Text blocks are spans. ``x``/``y`` is the span's baseline origin in
    PyMuPDF's UNROTATED page space (top-left origin, y down), as it always was;
    ``rotation`` is the span's reading direction in that same unrotated frame,
    degrees counter-clockwise as seen (0 = left-to-right), snapped to the
    nearest multiple of 90 within 0.5 deg. Text drawn by ANNOTATIONS — a
    reviewer's FreeText comment, a stamp — is excluded: it is not part of the
    drawing (``planlens.document`` reports it as attributed markups).
    """
    doc = _open_document(filepath, content)
    n_pages = len(doc)
    if page >= n_pages:
        doc.close()
        raise ValueError(f"Page {page} out of range (document has {n_pages} pages)")

    ocgs = layer_state(doc)
    if include_hidden_layers:
        enable_all_layers(doc)
    pg = doc[page]
    rect = pg.rect

    # Count drawings by color
    drawings = pg.get_drawings()
    colors: Dict[str, int] = {}
    for d in drawings:
        c = _color_to_hex(d.get("color"))
        colors[c] = colors.get(c, 0) + 1

    # Extract text blocks from the annotation-free display list. It reports in
    # the DISPLAYED frame, so each origin and direction is derotated back to
    # the unrotated frame this function has always returned.
    text_blocks = []
    text_dict = _page_text_dict_without_annotations(pg)
    dm = pg.derotation_matrix
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:  # text block
            for line in block.get("lines", []):
                ddx, ddy = line.get("dir", (1.0, 0.0))
                ux = dm.a * ddx + dm.c * ddy
                uy = dm.b * ddx + dm.d * ddy
                rotation = _snap_rotation(
                    math.degrees(math.atan2(-uy, ux)) % 360.0)
                for span in line.get("spans", []):
                    ox, oy = span.get("origin", (0, 0))
                    x = dm.a * ox + dm.c * oy + dm.e
                    y = dm.b * ox + dm.d * oy + dm.f
                    text_blocks.append({
                        "text": span.get("text", "").strip(),
                        "x": round(x, 2),
                        "y": round(y, 2),
                        "size": round(span.get("size", 0), 1),
                        "rotation": rotation,
                    })

    # Check for images
    has_images = len(pg.get_images()) > 0

    doc.close()
    return {
        "page_size": {
            "width": round(rect.width, 2),
            "height": round(rect.height, 2),
        },
        "n_drawings": len(drawings),
        "colors": colors,
        "text_blocks": [tb for tb in text_blocks if tb["text"]],
        "has_images": has_images,
        "ocgs": ocgs,
    }


def extract_vector_geometry(
    filepath=None, content=None, page: int = 0,
    scale: float = 1.0, origin: str = "bottom_left",
    role_mapping: Optional[Dict[str, str]] = None,
) -> PdfParseResult:
    """Extract geometry from PDF vector drawings via PyMuPDF.

    Parameters
    ----------
    filepath : str, optional
        Path to PDF file.
    content : bytes, optional
        PDF file content as bytes.
    page : int
        Page number (0-indexed).
    scale : float
        Scale factor: drawing_units * scale = meters.
    origin : str
        Coordinate origin: 'bottom_left' (default, flips Y from PDF top-left)
        or 'top_left' (raw PDF coordinates).
    role_mapping : dict, optional
        Maps hex color strings to roles:
            {"#000000": "surface", "#0000ff": "gwt", "#808080": "boundary_Clay"}
        Boundary roles must start with "boundary_" prefix.

    Returns
    -------
    PdfParseResult
        Extracted geometry with coordinates in meters.
    """
    doc = _open_document(filepath, content)
    n_pages = len(doc)
    if page >= n_pages:
        doc.close()
        raise ValueError(f"Page {page} out of range (document has {n_pages} pages)")

    pg = doc[page]
    page_height = pg.rect.height
    drawings = pg.get_drawings()

    # Group paths by color
    paths_by_color: Dict[str, List[List[Tuple[float, float]]]] = {}
    for d in drawings:
        color_hex = _color_to_hex(d.get("color"))
        items = d.get("items", [])
        path_points = _extract_path_points(items)
        if path_points:
            if color_hex not in paths_by_color:
                paths_by_color[color_hex] = []
            paths_by_color[color_hex].append(path_points)

    # Extract text
    text_annotations = []
    text_dict = pg.get_text("dict")
    for block in text_dict.get("blocks", []):
        if block.get("type") == 0:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if text:
                        ox, oy = span.get("origin", (0, 0))
                        if origin == "bottom_left":
                            oy = page_height - oy
                        text_annotations.append({
                            "text": text,
                            "x": round(ox * scale, 4),
                            "y": round(oy * scale, 4),
                        })

    doc.close()

    # Apply role mapping
    surface_points = []
    boundary_profiles = {}
    gwt_points = None
    warnings = []

    if role_mapping:
        for color_hex, role in role_mapping.items():
            color_lower = color_hex.lower()
            if color_lower not in paths_by_color:
                warnings.append(f"Color {color_hex} (role '{role}') not found in drawings")
                continue

            all_points = []
            for path in paths_by_color[color_lower]:
                all_points.extend(path)

            # Transform coordinates
            transformed = []
            for x, y in all_points:
                if origin == "bottom_left":
                    y = page_height - y
                transformed.append((round(x * scale, 4), round(y * scale, 4)))

            # Sort by x
            transformed.sort(key=lambda p: p[0])

            if role == "surface":
                surface_points = transformed
            elif role == "gwt":
                gwt_points = transformed
            elif role.startswith("boundary_"):
                name = role[len("boundary_"):]
                boundary_profiles[name] = transformed
            else:
                warnings.append(f"Unknown role '{role}' for color {color_hex}")
    else:
        # No mapping — collect all paths as surface (first color found)
        if paths_by_color:
            first_color = next(iter(paths_by_color))
            all_points = []
            for path in paths_by_color[first_color]:
                all_points.extend(path)
            transformed = []
            for x, y in all_points:
                if origin == "bottom_left":
                    y = page_height - y
                transformed.append((round(x * scale, 4), round(y * scale, 4)))
            transformed.sort(key=lambda p: p[0])
            surface_points = transformed
            if len(paths_by_color) > 1:
                warnings.append(
                    f"No role_mapping provided — only extracted {len(surface_points)} "
                    f"points from color {first_color}. Provide role_mapping for "
                    f"multi-color drawings."
                )

    return PdfParseResult(
        surface_points=surface_points,
        boundary_profiles=boundary_profiles,
        gwt_points=gwt_points,
        text_annotations=text_annotations,
        page_number=page,
        extraction_method="vector",
        scale_factor=scale,
        confidence=1.0,
        warnings=warnings,
    )


def extract_colored_paths(
    filepath=None, content=None, page: int = 0,
    scale: float = 1.0, origin: str = "bottom_left",
    include_hidden_layers: bool = False,
) -> List[Dict[str, Any]]:
    """Return the page's vector paths as coloured regions for label association.

    Companion to ``discover_pdf_content`` (which gives text blocks) and
    ``planlens.pdf.labels.propose_role_mapping``: returns one entry per drawing path
    as ``{"color": hex, "points": [(x, y), ...], "layer": str|None,
    "filled": bool, "fill_color": hex|None}`` (same coordinate convention
    as ``extract_vector_geometry``). No role_mapping is required.

    ``layer`` is the path's optional-content group — a PDF's layer — or None
    when it belongs to none. PyMuPDF reports the EMPTY STRING for the latter;
    it is normalized to None here, because "" is not a layer name and a caller
    filtering by layer must be able to ask for "no layer" without matching a
    real group. A group genuinely NAMED ``"0"`` (AutoCAD's default layer,
    present on corpus sheet 10.31A) is a real name and is kept verbatim —
    unlike DXF, where ``"0"`` inside a block is an inheritance sentinel.

    ``filled`` is True when the path is PAINTED with a fill (PyMuPDF ``type``
    "f" or "fs"), and ``fill_color`` is that fill's colour as hex ``#rrggbb``
    — the same spelling ``color`` uses, so one reader parses both — or None
    when the path is not painted. A filled path is CLOSED by PDF semantics whatever
    its ``closePath`` flag says — measured across the ten-sheet corpus, that
    flag is False on all 6,669 filled paths — so ``filled`` is the reliable
    "this is an area, not a line" signal.

    ``include_hidden_layers`` also reads groups that are OFF by default
    (see :func:`enable_all_layers`).

    Returns
    -------
    list of dict
        One per vector path, keys as above.
    """
    doc = _open_document(filepath, content)
    if page >= len(doc):
        doc.close()
        raise ValueError(f"Page {page} out of range")
    if include_hidden_layers:
        enable_all_layers(doc)
    pg = doc[page]
    page_height = pg.rect.height
    regions = []
    for d in pg.get_drawings():
        color_hex = _color_to_hex(d.get("color"))
        pts = _extract_path_points(d.get("items", []))
        if not pts:
            continue
        out_pts = []
        for x, y in pts:
            yy = page_height - y if origin == "bottom_left" else y
            out_pts.append((round(x * scale, 4), round(yy * scale, 4)))
        filled = d.get("type") in ("f", "fs")
        regions.append({
            "color": color_hex,
            "points": out_pts,
            "layer": d.get("layer") or None,
            "filled": filled,
            "fill_color": _fill_to_hex(d.get("fill")) if filled else None,
        })
    doc.close()
    return regions


#: Cubic-bezier subdivisions per curve segment. 8 keeps a 4-bezier circle to
#: ~1.3% radial error while adding only ~32 points per circle.
_BEZIER_SAMPLES = 8


def _sample_cubic_bezier(p1, p2, p3, p4, n: int = _BEZIER_SAMPLES
                         ) -> List[Tuple[float, float]]:
    """Sample a cubic bezier (PyMuPDF Points) at n+1 parameter steps."""
    out = []
    for i in range(n + 1):
        t = i / n
        mt = 1.0 - t
        a = mt * mt * mt
        b = 3.0 * mt * mt * t
        c = 3.0 * mt * t * t
        d = t * t * t
        out.append((a * p1.x + b * p2.x + c * p3.x + d * p4.x,
                    a * p1.y + b * p2.y + c * p3.y + d * p4.y))
    return out


def _extract_path_points(items) -> List[Tuple[float, float]]:
    """Extract (x, y) points from PyMuPDF drawing items."""
    points = []
    for item in items:
        kind = item[0]  # "l" for line, "c" for curve, "re" for rect
        if kind == "l":
            # Line: ("l", Point(x1,y1), Point(x2,y2))
            p1, p2 = item[1], item[2]
            points.append((p1.x, p1.y))
            points.append((p2.x, p2.y))
        elif kind == "c":
            # Bezier curve: ("c", p1, p2, p3, p4) — cubic control points.
            # Sampled along the curve (not just endpoints): a circle drawn as
            # 4 beziers must arrive as a circle-like point ring, and a
            # revision-cloud scallop must keep its bump, or downstream
            # curve-aware detection (bubbles, clouds) is impossible.
            points.extend(_sample_cubic_bezier(item[1], item[2],
                                               item[3], item[4]))
        elif kind == "re":
            # Rectangle: ("re", Rect)
            r = item[1]
            points.append((r.x0, r.y0))
            points.append((r.x1, r.y0))
            points.append((r.x1, r.y1))
            points.append((r.x0, r.y1))
    # Deduplicate consecutive identical points
    if len(points) > 1:
        deduped = [points[0]]
        for p in points[1:]:
            if abs(p[0] - deduped[-1][0]) > 1e-6 or abs(p[1] - deduped[-1][1]) > 1e-6:
                deduped.append(p)
        return deduped
    return points
