"""Native-annotation extraction from a DXF — the ground-truth API.

Extracts the annotation entities a CAD file declares as DRAWN in a space
— LEADER, MULTILEADER, DIMENSION, TEXT/MTEXT, INSERT — as plain JSON-able
dicts in raw coordinates (no unit conversion, no IR wrapping). This is the
extractor behind the drawing-intelligence ground-truth corpus: the
composition heuristics are scored against what the native CAD file says
was drawn, so this module deliberately reports raw values (MTEXT
formatting codes retained, raw group-70 ``dimtype``, coordinates rounded
to 4 decimals) exactly as the committed ``*.truth.json`` files do. "Raw"
means raw DRAWING UNITS in the WORLD coordinate system: points DXF
stores in an entity's own OCS are resolved to WCS first (see
:func:`_ocs_xy`), because an OCS number is not a position on the sheet —
and, for the same reason, a text ``rotation`` stored in that plane is
resolved with it (see :func:`_text_rotation`), because an OCS bearing is
not a direction on the sheet.

"Drawn in this space" INCLUDES annotations reached through block
references: a LEADER or DIMENSION authored inside a block is plotted on
the sheet at the exact insert transform, so it is ground truth. Walking
only the top-level entity list would have scored the ingest side (which
explodes blocks) against a denominator that denies geometry the plotter
puts on paper — see :func:`extract_native_annotations` for the walk and
its scope.

For the IR-level view of the same entities (unit-converted, plain text,
first-class :class:`planlens.ir.results.Leader` / ``Dimension`` entities
at confidence 1.0) use :func:`planlens.ir.from_dxf` — that is the surface
queries and agents consume; this one is for scoring and corpus building.

Usage::

    from planlens.dxf.truth import extract_native_annotations
    truth = extract_native_annotations("sheet.dxf")
    truth["spaces"]["model"]["dimensions"][0]["defpoint"]
"""

from __future__ import annotations

import math
import os
from typing import Any, Dict, List, Optional, Tuple

__all__ = ["extract_native_annotations"]


#: Block-explosion guards. These MUST match ``planlens.ir.ingest``'s
#: ``_MAX_BLOCK_DEPTH`` / ``_DEFAULT_MAX_BLOCK_ENTITIES``: the two surfaces
#: describe the same drawing, so a pathological file that makes them stop
#: at different points would make truth and ingest disagree again — which
#: is the very class of bug this walk exists to close. Parity is pinned by
#: a test (``test_truth.py::TestBlockCapParity``) rather than by a shared
#: import, to keep the dxf layer free of an ir-layer dependency.
MAX_BLOCK_DEPTH = 8
DEFAULT_MAX_BLOCK_ENTITIES = 50_000

#: Which top-level dxftype lands in which output bucket. One extractor
#: (:func:`_record`), two traversals — that is what stops the top-level
#: records and the block-nested ones from drifting apart later.
_BUCKET = {"LEADER": "leaders", "MULTILEADER": "multileaders",
           "DIMENSION": "dimensions", "TEXT": "text", "MTEXT": "text",
           "INSERT": "inserts"}

#: Only these are promoted OUT of block content. TEXT/MTEXT deliberately
#: are not: every Mecklenburg corpus sheet carries a county-seal block
#: holding three TEXT entities, so promoting text would change all ten
#: committed ``*.truth.json`` files and move the published OCR-coverage
#: denominators. That is a real follow-on, but it is its own commit with a
#: corpus regeneration and an OCR rerun attached — not a side effect here.
#: Nested INSERTs are not promoted either: the top-level insert record
#: already names the block, so nesting them would double-describe one
#: placement.
_BLOCK_PROMOTED = ("LEADER", "MULTILEADER", "DIMENSION")

#: Entity types whose loss inside a block this walk REPORTS. An entity
#: ezdxf declines to transform is only a truth loss if it is one this
#: module would have recorded — plus INSERT, which hides everything nested
#: under it. An OLE2FRAME (the county seal on all ten corpus sheets) is
#: outside this extractor's declared scope exactly as block TEXT is, so
#: warning about it would be noise, and would have changed every committed
#: truth file for a record none of them ever contained.
_REPORTED_LOSSES = frozenset(_BLOCK_PROMOTED + ("INSERT",))

#: The extrusion under which an entity's OCS *is* the WCS.
_WCS_EXTRUSION = (0.0, 0.0, 1.0)


def _xy(v, nd: int = 4) -> List[float]:
    try:
        x, y = v.x, v.y
    except AttributeError:  # plain tuple/list vertex (older ezdxf paths)
        x, y = v[0], v[1]
    return [round(float(x), nd), round(float(y), nd)]


def _ocs_xy(e, v, nd: int = 4) -> List[float]:
    """An OCS-stored point of entity ``e`` → WCS, rounded like ``_xy``.

    DXF keeps a handful of the points this module reports in the
    entity's own OBJECT coordinate system, oriented by its group-210
    extrusion: a TEXT or ATTRIB insert point, an INSERT's insert point,
    and a DIMENSION's group-11 *text midpoint*. The rest — LEADER
    vertices, MTEXT's insert, a DIMENSION's group-10/13/14 definition
    points — are already WCS and go through :func:`_xy` untouched.

    A MIRRORED block placement is the case this exists for: ezdxf hands
    the mirrored content back with extrusion (0,0,-1), under which OCS x
    runs the other way, so a raw read reports a detail spanning x 40..50
    with its text at x = -45. ``planlens.ir.ingest`` resolves exactly the
    same set of points the same way; the agreement is pinned by
    ``test_truth.py::TestMirroredInsert``.
    """
    try:
        ext = tuple(e.dxf.extrusion)
    except Exception:
        ext = _WCS_EXTRUSION
    if ext != _WCS_EXTRUSION:
        from ezdxf.math import OCS
        v = OCS(ext).to_wcs((v[0], v[1], getattr(v, "z", 0.0) or 0.0))
    return _xy(v, nd)


def _ocs_rotation(e, deg: float, nd: int = 2) -> float:
    """An OCS-plane rotation of entity ``e`` → its WCS bearing, degrees.

    The ANGULAR twin of :func:`_ocs_xy`, and the same defect if it is
    skipped: a TEXT's group-50 rotation is measured in the entity's own
    xy plane, so under the MIRRORED extrusion (0,0,-1) a note authored
    horizontal comes back at rotation 0.0 while it plots right-to-left
    (bearing 180). Resolving the insert point but not the angle placed a
    33-character note's box on the opposite side of its own correctly
    placed insert point.

    The identity extrusion returns the stored value rounded and
    otherwise untouched — no wrapping — so an unmirrored file serializes
    exactly as it did before this existed.
    """
    try:
        ext = tuple(e.dxf.extrusion)
    except Exception:
        ext = _WCS_EXTRUSION
    if ext == _WCS_EXTRUSION:
        return round(float(deg), nd)
    from ezdxf.math import OCS
    ocs = OCS(ext)
    ex = ocs.to_wcs((1.0, 0.0, 0.0))
    ey = ocs.to_wcs((0.0, 1.0, 0.0))
    a = math.radians(float(deg))
    c, s = math.cos(a), math.sin(a)
    return round(math.degrees(math.atan2(c * ex.y + s * ey.y,
                                         c * ex.x + s * ey.x)) % 360.0, nd)


def _text_rotation(e, nd: int = 2) -> float:
    """A TEXT/MTEXT entity's baseline bearing in WCS degrees.

    TEXT stores only the OCS angle. MTEXT stores the same fact twice —
    group 50 in the OCS plane and group 11 ``text_direction`` as an
    explicit WCS vector — and the vector wins when present: it is what
    ezdxf writes when it transforms a mirrored placement, leaving group
    50 at its authored value. ``planlens.ir.ingest._mtext_bearing``
    reads it the same way, so the two surfaces report the same bearing.
    """
    deg = float(e.dxf.get("rotation", 0.0) or 0.0)
    if e.dxftype() == "MTEXT":
        td = e.dxf.get("text_direction", None)
        if td is not None and (td[0] or td[1]):
            return round(math.degrees(math.atan2(td[1], td[0])) % 360.0, nd)
    return _ocs_rotation(e, deg, nd)


def _record(e) -> Optional[Dict[str, Any]]:
    """One annotation entity → its truth-schema dict (None if unsupported).

    Used for BOTH the top-level pass and the block walk, so a block-nested
    leader is described exactly like a directly drawn one.
    """
    etype = e.dxftype()
    layer = e.dxf.get("layer", "0")
    if etype == "LEADER":
        return {
            "vertices": [_xy(v) for v in e.vertices],
            "layer": layer,
            "has_arrowhead": bool(e.dxf.get("has_arrowhead", 1)),
            "annotation_handle": e.dxf.get("annotation_handle", None) or "",
        }
    if etype == "MULTILEADER":
        try:
            ctx = e.context
            mtext = getattr(ctx, "mtext", None)
            text = (mtext.default_content if mtext is not None else "") or ""
            lines = [[_xy(v) for v in ln.vertices]
                     for ml in ctx.leaders for ln in ml.lines]
        except Exception:
            text, lines = "", []
        return {"text": text, "leader_lines": lines, "layer": layer}
    if etype == "DIMENSION":
        try:
            meas = round(float(e.get_measurement()), 4)
        except Exception:
            meas = 0.0
        # Group 10 is WCS; group 11 (the text midpoint) is OCS.
        dp = e.dxf.get("defpoint", None)
        tm = e.dxf.get("text_midpoint", None)
        return {
            "dimtype": int(e.dxf.get("dimtype", 0) or 0),
            "text": e.dxf.get("text", ""),
            "defpoint": _xy(dp) if dp is not None else None,
            "text_midpoint": _ocs_xy(e, tm) if tm is not None else None,
            "measurement": meas,
            "layer": layer,
        }
    if etype in ("TEXT", "MTEXT"):
        ins = e.dxf.get("insert", None)
        if etype == "TEXT":
            content = e.dxf.get("text", "")
            height = e.dxf.get("height", 0.0)
            # TEXT's insert is OCS; MTEXT's is WCS.
            pos = _ocs_xy(e, ins) if ins is not None else None
        else:
            content = e.text
            height = e.dxf.get("char_height", 0.0)
            pos = _xy(ins) if ins is not None else None
        return {
            "type": etype,
            "text": content,
            "insert": pos,
            "height": round(float(height), 4),
            "rotation": _text_rotation(e),
            "layer": layer,
        }
    if etype == "INSERT":
        # An INSERT's own insert point is OCS, as is each ATTRIB's.
        ins = e.dxf.get("insert", None)
        rec: Dict[str, Any] = {
            "name": e.dxf.get("name", ""),
            "insert": _ocs_xy(e, ins) if ins is not None else None,
            "layer": layer,
        }
        attribs = [{"tag": a.dxf.get("tag", ""),
                    "text": a.dxf.get("text", ""),
                    "insert": _ocs_xy(a, a.dxf.insert),
                    "height": round(float(a.dxf.get("height", 0.0)), 4)}
                   for a in e.attribs if a.dxf.get("text", "")]
        if attribs:
            rec["attribs"] = attribs
        return rec
    return None


def _warn_once(state: Dict[str, Any], msg: str) -> None:
    """Record ``msg`` unless an identical warning is already there.

    A block placed hundreds of times would otherwise report the same
    untransformable child once per placement and bury everything else.
    """
    if msg not in state["seen"]:
        state["seen"].add(msg)
        state["warnings"].append(msg)


def _walk_block(insert, top_name: str, out: Dict[str, List[Dict[str, Any]]],
                state: Dict[str, Any], max_block_entities: int,
                depth: int = 0, inherit_layer: Optional[str] = None) -> None:
    """Depth-first walk of one INSERT, collecting block-nested annotations.

    ``top_name`` is the OUTERMOST block name, matching the ingest side's
    ``style="block:<name>"`` convention, so both surfaces name the same
    provenance for the same entity.

    ``inherit_layer`` carries the CAD layer-"0" rule down the nesting
    chain: block content authored on layer ``"0"`` plots on the PLACING
    reference's layer, so that is the layer recorded — the same
    resolution ``planlens.ir.ingest`` performs, which is what lets the
    two surfaces be compared entity-for-entity on layer as well as on
    coordinates.
    """
    bname = insert.dxf.get("name", "") or "?"
    ins_layer = insert.dxf.get("layer", "0") or "0"
    if ins_layer == "0" and inherit_layer is not None:
        ins_layer = inherit_layer
    if depth >= MAX_BLOCK_DEPTH:
        if not state["depth_warned"]:
            state["warnings"].append(
                f"Block nesting deeper than {MAX_BLOCK_DEPTH} levels "
                f"('{bname}') left unexploded.")
            state["depth_warned"] = True
        return
    try:
        # ezdxf DROPS entities it cannot transform — a non-uniformly
        # scaled MULTILEADER is the everyday one — and yields the rest
        # WITHOUT raising, so the guards below never see the loss. The
        # callback is the only channel that reports it, and a native
        # annotation vanishing from GROUND TRUTH silently would move a
        # recall denominator without anyone noticing.
        def _skipped(orig, reason, _b=bname):
            vt = orig.dxftype()
            if vt not in _REPORTED_LOSSES:
                return   # outside this extractor's scope either way
            _warn_once(state,
                       f"Block '{_b}': ezdxf could not transform a "
                       f"{vt} ({reason}); that plotted annotation is "
                       f"NOT in this truth record.")

        virts = iter(insert.virtual_entities(
            skipped_entity_callback=_skipped))
    except Exception as exc:
        # A ground-truth extractor must not die on one bad block; the
        # loss becomes a visible warning instead.
        state["warnings"].append(
            f"Block '{bname}' could not be exploded: {exc}")
        return
    while True:
        try:
            virt = next(virts)
        except StopIteration:
            break
        except Exception as exc:
            state["warnings"].append(
                f"Block '{bname}' explosion truncated: {exc}")
            break
        if state["visited"] >= max_block_entities:
            if not state["budget_warned"]:
                state["warnings"].append(
                    f"Block explosion stopped at the {max_block_entities}"
                    f"-entity budget; remaining block annotations omitted.")
                state["budget_warned"] = True
            return
        state["visited"] += 1
        vt = virt.dxftype()
        if vt == "INSERT":
            _walk_block(virt, top_name, out, state, max_block_entities,
                        depth + 1, inherit_layer=ins_layer)
        elif vt in _BLOCK_PROMOTED:
            rec = _record(virt)
            if rec is not None:
                if rec.get("layer") in (None, "", "0"):
                    rec["layer"] = ins_layer
                rec["block"] = top_name
                out[_BUCKET[vt]].append(rec)


def _space_annotations(space, max_block_entities: int
                       = DEFAULT_MAX_BLOCK_ENTITIES
                       ) -> Tuple[Dict[str, List[Dict[str, Any]]],
                                  List[str]]:
    """Two passes over one space: top-level entities, then block content.

    Order contract: top-level records first in space order, then the
    block-nested ones depth-first in the order their INSERTs appear. A
    file with no block-nested annotations therefore serializes exactly as
    it did before the walk existed.
    """
    out: Dict[str, List[Dict[str, Any]]] = {
        "leaders": [], "multileaders": [], "dimensions": [],
        "text": [], "inserts": [],
    }
    inserts = []
    for e in space:
        bucket = _BUCKET.get(e.dxftype())
        if bucket is None:
            continue
        rec = _record(e)
        if rec is None:
            continue
        out[bucket].append(rec)
        if e.dxftype() == "INSERT":
            inserts.append(e)
    state: Dict[str, Any] = {"visited": 0, "warnings": [], "seen": set(),
                             "depth_warned": False, "budget_warned": False}
    for ins in inserts:
        _walk_block(ins, ins.dxf.get("name", "") or "?", out, state,
                    max_block_entities)
    return out, state["warnings"]


def extract_native_annotations(filepath: Optional[str] = None, doc=None,
                               max_block_entities: int
                               = DEFAULT_MAX_BLOCK_ENTITIES
                               ) -> Dict[str, Any]:
    """Extract native annotation entities from a DXF, truth-file schema.

    Pass a ``filepath`` or an already-open ezdxf ``doc``. Returns::

        {"source_dxf": <basename>,
         "spaces": {"model": {"leaders": [...], "multileaders": [...],
                              "dimensions": [...], "text": [...],
                              "inserts": [...]}}}

    ``spaces`` carries ``"model"`` plus one entry PER PAPER-SPACE LAYOUT
    under the layout's own name (e.g. ``"Layout1"``) — the committed
    corpus records paper space too (empty on the Mecklenburg sheets, which
    draw everything in model space at paper scale), so regeneration
    reproduces the committed files byte-for-byte, layouts included.

    BLOCK-NESTED ANNOTATIONS: each space is walked twice — its top-level
    entities, then the content of every top-level INSERT (recursively,
    under the same depth/entity caps :func:`planlens.ir.from_dxf` uses).
    LEADER / MULTILEADER / DIMENSION found inside a block are appended
    after the top-level records, at their exact transformed coordinates,
    carrying one additive key ``"block"`` naming the outermost block. That
    matches the ingest side's ``style="block:<name>"``, so the two
    surfaces agree entity-for-entity (``from_dxf``'s default
    ``explode_blocks=True``; a caller passing ``False`` is asking for
    divergence and gets it). ``layer`` agrees too: content authored on
    layer ``"0"`` inside a block is reported on the PLACING reference's
    layer, chaining through nested references, exactly as ``from_dxf``
    resolves it. Block-nested TEXT/MTEXT and nested INSERTs are
    deliberately NOT promoted — see ``_BLOCK_PROMOTED``.

    ``warnings`` appears as a top-level key ONLY when non-empty (a depth
    or budget cap was hit, a block would not explode, or ezdxf declined
    to transform one of a block's entities — a non-uniformly scaled
    MULTILEADER is the everyday case, and it is dropped from
    ``virtual_entities()`` WITHOUT raising), so clean files serialize
    exactly as before. Repeated identical warnings are recorded once.

    Coordinates are RAW drawing units rounded to 4 decimals; MTEXT keeps
    its inline formatting codes; ``dimtype`` is the raw DXF group-70
    value. ``inserts`` additionally carry an ``attribs`` list
    (tag/text/insert/height) when the reference has attribute values — a
    superset of the original truth schema, additive only.
    """
    if (filepath is None) == (doc is None):
        raise ValueError("pass exactly one of filepath / doc")
    if doc is None:
        try:
            import ezdxf
        except ImportError as exc:
            raise ImportError("ezdxf is required: pip install ezdxf>=1.4"
                              ) from exc
        doc = ezdxf.readfile(filepath)
        name = os.path.basename(filepath)
    else:
        name = getattr(doc, "filename", None) or ""
        name = os.path.basename(name) if name else ""
    warnings: List[str] = []
    spaces, warn = _space_annotations(doc.modelspace(), max_block_entities)
    spaces = {"model": spaces}
    warnings.extend(warn)
    for layout_name in doc.layout_names_in_taborder():
        if layout_name == "Model":
            continue
        spaces[layout_name], warn = _space_annotations(
            doc.layout(layout_name), max_block_entities)
        warnings.extend(warn)
    out: Dict[str, Any] = {"source_dxf": name, "spaces": spaces}
    if warnings:
        out["warnings"] = warnings
    return out
