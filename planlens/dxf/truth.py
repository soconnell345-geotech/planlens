"""Native-annotation extraction from a DXF — the ground-truth API.

Extracts the annotation entities a CAD file DECLARES — LEADER,
MULTILEADER, DIMENSION, TEXT/MTEXT, INSERT — as plain JSON-able dicts in
raw model-space coordinates (no unit conversion, no IR wrapping). This is
the extractor behind the drawing-intelligence ground-truth corpus: the
composition heuristics are scored against what the native CAD file says
was drawn, so this module deliberately reports raw values (MTEXT
formatting codes retained, raw group-70 ``dimtype``, coordinates rounded
to 4 decimals) exactly as the committed ``*.truth.json`` files do.

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

import os
from typing import Any, Dict, List, Optional

__all__ = ["extract_native_annotations"]


def _xy(v, nd: int = 4) -> List[float]:
    try:
        x, y = v.x, v.y
    except AttributeError:  # plain tuple/list vertex (older ezdxf paths)
        x, y = v[0], v[1]
    return [round(float(x), nd), round(float(y), nd)]


def _space_annotations(space) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {
        "leaders": [], "multileaders": [], "dimensions": [],
        "text": [], "inserts": [],
    }
    for e in space:
        etype = e.dxftype()
        layer = e.dxf.get("layer", "0")
        if etype == "LEADER":
            out["leaders"].append({
                "vertices": [_xy(v) for v in e.vertices],
                "layer": layer,
                "has_arrowhead": bool(e.dxf.get("has_arrowhead", 1)),
                "annotation_handle": e.dxf.get("annotation_handle", None)
                or "",
            })
        elif etype == "MULTILEADER":
            try:
                ctx = e.context
                mtext = getattr(ctx, "mtext", None)
                text = (mtext.default_content
                        if mtext is not None else "") or ""
                lines = [[_xy(v) for v in ln.vertices]
                         for ml in ctx.leaders for ln in ml.lines]
            except Exception:
                text, lines = "", []
            out["multileaders"].append({
                "text": text,
                "leader_lines": lines,
                "layer": layer,
            })
        elif etype == "DIMENSION":
            try:
                meas = round(float(e.get_measurement()), 4)
            except Exception:
                meas = 0.0
            dp = e.dxf.get("defpoint", None)
            tm = e.dxf.get("text_midpoint", None)
            out["dimensions"].append({
                "dimtype": int(e.dxf.get("dimtype", 0) or 0),
                "text": e.dxf.get("text", ""),
                "defpoint": _xy(dp) if dp is not None else None,
                "text_midpoint": _xy(tm) if tm is not None else None,
                "measurement": meas,
                "layer": layer,
            })
        elif etype in ("TEXT", "MTEXT"):
            ins = e.dxf.get("insert", None)
            if etype == "TEXT":
                content = e.dxf.get("text", "")
                height = e.dxf.get("height", 0.0)
            else:
                content = e.text
                height = e.dxf.get("char_height", 0.0)
            out["text"].append({
                "type": etype,
                "text": content,
                "insert": _xy(ins) if ins is not None else None,
                "height": round(float(height), 4),
                "rotation": round(float(e.dxf.get("rotation", 0.0)), 2),
                "layer": layer,
            })
        elif etype == "INSERT":
            ins = e.dxf.get("insert", None)
            rec: Dict[str, Any] = {
                "name": e.dxf.get("name", ""),
                "insert": _xy(ins) if ins is not None else None,
                "layer": layer,
            }
            attribs = [{"tag": a.dxf.get("tag", ""),
                        "text": a.dxf.get("text", ""),
                        "insert": _xy(a.dxf.insert),
                        "height": round(float(a.dxf.get("height", 0.0)), 4)}
                       for a in e.attribs if a.dxf.get("text", "")]
            if attribs:
                rec["attribs"] = attribs
            out["inserts"].append(rec)
    return out


def extract_native_annotations(filepath: Optional[str] = None,
                               doc=None) -> Dict[str, Any]:
    """Extract native annotation entities from a DXF, truth-file schema.

    Pass a ``filepath`` or an already-open ezdxf ``doc``. Returns::

        {"source_dxf": <basename>,
         "spaces": {"model": {"leaders": [...], "multileaders": [...],
                              "dimensions": [...], "text": [...],
                              "inserts": [...]}}}

    Model-space coordinates are RAW drawing units rounded to 4 decimals;
    MTEXT keeps its inline formatting codes; ``dimtype`` is the raw DXF
    group-70 value. ``inserts`` additionally carry an ``attribs`` list
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
    return {
        "source_dxf": name,
        "spaces": {"model": _space_annotations(doc.modelspace())},
    }
