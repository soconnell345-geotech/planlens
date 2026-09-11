"""
Ingest adapters — build a :class:`planlens.ir.results.DrawingIR` from a source.

Three legs, each stamping provenance + confidence on every entity:

* :func:`from_dxf`        — exact CAD geometry via ezdxf. source='dxf',
  confidence 1.0. Native model-space coordinates converted to SI meters using
  the drawing's ``$INSUNITS`` (or a supplied ``units``). Carries layers/colors.
* :func:`from_pdf_vector` — exact PDF path coordinates via PyMuPDF, reusing the
  ``pdf_import`` extractor + scale module. source='pdf_vector', confidence 1.0.
  Page-point coordinates, promoted to model meters when a scale/calibration is
  given (else page space, units='pt') with scale candidates proposed as
  metadata.
* :func:`from_raster`     — best-effort OpenCV tracing (delegates to
  ``planlens.ir.raster``). source='raster_trace', confidence < 1.

Import convention: this module (the I/O ingest layer) legally imports the
``dxf_import`` / ``pdf_import`` I/O modules — the same pattern
``geo_project/ingest.py`` uses. The "no cross-module imports" house rule targets
the 30 computational *analysis* modules; the I/O modules
(dxf_import <- pdf_import <- geo_project) already form a dependency layer, which
``drawing_ir`` joins. ``results.py`` / ``queries.py`` stay pure-schema.

Geometry only: a drawing carries geometry, never soil properties — those always
come from the user/report downstream.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from planlens.ir.results import (
    Arc, Circle, Dimension, DrawingIR, Leader, Line, Polyline, Region,
    TextItem,
)


def _mtext_plain(raw: str) -> str:
    """Strip MTEXT inline formatting codes, best-effort."""
    try:
        from ezdxf.tools.text import plain_mtext
        return plain_mtext(raw)
    except Exception:
        return raw


# ---------------------------------------------------------------------------
# DXF
# ---------------------------------------------------------------------------

def _dxf_color(entity) -> Optional[str]:
    """Best-effort color: true-color hex, else an ACI token, else None."""
    try:
        rgb = entity.rgb
    except Exception:
        rgb = None
    if rgb:
        r, g, b = rgb
        return f"#{r:02x}{g:02x}{b:02x}"
    try:
        aci = entity.dxf.color
    except Exception:
        aci = 256
    if aci and 0 < aci < 256:
        return f"ACI{aci}"
    return None


def _dxf_style(entity) -> Optional[str]:
    try:
        lt = entity.dxf.linetype
    except Exception:
        return None
    if lt and lt.upper() != "BYLAYER":
        return lt
    return None


#: The extrusion vector under which an entity's OCS *is* the WCS. Anything
#: else means the entity's stored points need the arbitrary-axis transform.
_WCS_EXTRUSION = (0.0, 0.0, 1.0)


class _Ocs:
    """One entity's OCS -> WCS map, reduced to the drawing xy plane.

    DXF stores the points of most PLANAR entities in the entity's own
    OBJECT coordinate system, whose orientation is the group-210
    extrusion vector: CIRCLE/ARC centers, LWPOLYLINE and 2D-POLYLINE
    vertices, TEXT/ATTRIB insert points, a DIMENSION's *text midpoint*
    (group 11) and HATCH boundaries. Points that are already WCS —
    LINE ends, MTEXT insert, LEADER vertices, a DIMENSION's definition
    points (10/13/14) — are read raw and never come through here.

    The case that reaches everyday drawings is the MIRRORED block
    reference. A standard detail placed with a negative scale is routine
    drafting, and ezdxf hands the mirrored copy back with extrusion
    (0,0,-1) and x negated; reading ``.x`` raw then puts the detail on
    the wrong side of the origin (measured: a detail spanning x 40..50 in
    reported its TEXT at x = -45).

    Only the xy part of the mapped basis is kept, because the IR is 2D.
    For the axis-aligned extrusions that mirroring produces the map is an
    isometry, so radii and distances are preserved; a genuinely TILTED
    extrusion projects a circle to an ellipse, which this IR cannot
    represent either before or after this transform — the projected
    centre and vertices are still strictly better than the raw OCS
    numbers they replace.
    """

    __slots__ = ("_ocs", "_ex", "_ey", "reverses")

    def __init__(self, ocs):
        self._ocs = ocs
        ex = ocs.to_wcs((1.0, 0.0, 0.0))
        ey = ocs.to_wcs((0.0, 1.0, 0.0))
        self._ex = (ex.x, ex.y)
        self._ey = (ey.x, ey.y)
        #: A NEGATIVE determinant means the map mirrors the plane, so
        #: angles run backwards: an arc's counter-clockwise start/end
        #: swap roles. This is the same fact ``flip_y`` encodes, derived
        #: from the extrusion rather than assumed.
        self.reverses = (self._ex[0] * self._ey[1]
                         - self._ex[1] * self._ey[0]) < 0.0

    def xy(self, p) -> Tuple[float, float]:
        """An OCS point (Vec3 or plain tuple) -> WCS ``(x, y)``."""
        z = getattr(p, "z", None)
        if z is None:
            z = p[2] if len(p) > 2 else 0.0
        w = self._ocs.to_wcs((p[0], p[1], z))
        return (w.x, w.y)

    def angle(self, deg: float) -> float:
        """An angle measured in the OCS xy plane -> its WCS bearing."""
        a = math.radians(deg)
        c, s = math.cos(a), math.sin(a)
        return math.degrees(math.atan2(c * self._ex[1] + s * self._ey[1],
                                       c * self._ex[0] + s * self._ey[0])
                            ) % 360.0


def _ocs_map(entity) -> Optional[_Ocs]:
    """This entity's :class:`_Ocs`, or ``None`` when its OCS is the WCS.

    The identity case is the overwhelming majority, so it costs one
    attribute read and never imports ``ezdxf.math``.
    """
    try:
        ext = tuple(entity.dxf.extrusion)
    except Exception:
        return None
    if ext == _WCS_EXTRUSION:
        return None
    from ezdxf.math import OCS
    return _Ocs(OCS(ext))


def _pt_xy(p, ocs: Optional[_Ocs]) -> Tuple[float, float]:
    """A stored point -> drawing-unit WCS ``(x, y)`` (``ocs`` None = raw)."""
    if ocs is not None:
        return ocs.xy(p)
    try:
        return (p.x, p.y)
    except AttributeError:
        return (p[0], p[1])


def _mtext_bearing(ent, ocs: Optional[_Ocs]) -> float:
    """An MTEXT's baseline bearing in WCS degrees.

    MTEXT carries the same fact twice: group 50 ``rotation``, measured in
    the entity's OCS xy plane, and group 11 ``text_direction``, an
    explicit WCS vector. The vector WINS when present — that is the DXF
    rule, and it is what ezdxf writes when it transforms a MIRRORED
    placement: it sets ``text_direction`` to the plotted direction and
    leaves group 50 at its authored value (measured: a note authored
    horizontal comes back rotation 0.0 with text_direction (-1, 0, 0),
    i.e. plotted right-to-left). Without the vector the angle is an OCS
    one and needs the same map an ARC's start/end angles do.
    """
    td = ent.dxf.get("text_direction", None)
    if td is not None and (td[0] or td[1]):
        return math.degrees(math.atan2(td[1], td[0])) % 360.0
    deg = ent.dxf.get("rotation", 0.0) or 0.0
    return ocs.angle(deg) if ocs is not None else deg


#: The dxftypes ``_handle`` turns into IR entities. Used only to decide
#: whether an entity ezdxf declined to transform is a loss WE feel: an
#: unsupported type (an OLE2FRAME county seal, say — one sits in a block on
#: all ten corpus sheets) would have been skipped here anyway, and warning
#: about it would be noise, not honesty.
_SUPPORTED_TYPES = frozenset((
    "LINE", "LWPOLYLINE", "POLYLINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE",
    "TEXT", "MTEXT", "HATCH", "LEADER", "MULTILEADER", "DIMENSION",
    "INSERT",  # a dropped INSERT hides everything nested under it
))

#: INSERT explosion guards: nesting deeper than this is pathological (real
#: title blocks nest 2-3 levels), and the entity budget stops a bomb file
#: (a block inserted thousands of times referencing thousands of entities)
#: from blowing the IR up — dense real sheets ingest ~10k entities total.
_MAX_BLOCK_DEPTH = 8
_DEFAULT_MAX_BLOCK_ENTITIES = 50_000


def from_dxf(filepath: str = None, content: bytes = None,
             units: Optional[str] = None, flip_y: bool = False,
             name: str = "DXF import", explode_blocks: bool = True,
             max_block_entities: int = _DEFAULT_MAX_BLOCK_ENTITIES
             ) -> DrawingIR:
    """Build a DrawingIR from a DXF file's model space (exact coordinates).

    Extracts LINE, LWPOLYLINE/POLYLINE, ARC, CIRCLE, ELLIPSE/SPLINE (flattened),
    TEXT/MTEXT, (best-effort) HATCH, plus the NATIVE annotation entities:
    LEADER / MULTILEADER (→ :class:`Leader`, tip-first vertices + resolved
    annotation text), DIMENSION (→ :class:`Dimension`, defpoints /
    text_midpoint / measurement / text), and INSERT block references. Each
    entity carries its layer, color and linetype. Coordinates are converted
    to SI meters using ``units`` (default: the DXF ``$INSUNITS`` header,
    else 'm').

    INSERT handling: ATTRIB values land as :class:`TextItem` with
    ``style="attrib:<block>:<tag>"`` (the title-block metadata carrier),
    and — with ``explode_blocks=True`` (default) — the referenced block's
    GEOMETRY is exploded into IR primitives via ezdxf's
    ``virtual_entities()`` (exact insert transform: position, scale,
    rotation), recursively for nested references. Exploded entities carry
    ``style="block:<name>"`` provenance, so callers can tell block
    geometry from directly drawn model-space work, and their ``layer``
    follows the CAD rule: block content authored on layer ``"0"`` is drawn
    on the PLACING reference's layer, so that is the layer recorded (the
    literal ``"0"`` would be a transcription error, not a conservative
    reading).

    ``style`` is a PIPE-SEPARATED TOKEN LIST, not a single value — an
    exploded dashed spline is honestly all three of
    ``block:DETAIL|DASHED|approx_from_spline``, and a title-block
    attribute reached through a block is
    ``block:TBLOCK|attrib:TBLOCK:SHEET_NO``. Tokens are: ``block:<name>``
    (block origin, always FIRST when present), an EXPLICIT non-BYLAYER
    linetype when the entity carries one, ``attrib:<block>:<tag>`` for an
    attribute value, and ``approx_from_<type>`` when the vertices are a
    flattening approximation of a curve. The two documented
    discriminators are ``style.startswith("block:")`` and
    ``"approx_from_" in style``.

    NATIVE-ANNOTATION IDENTITY (the contract ``queries.py`` names when a
    native supersedes a composed proposal): a ``Leader`` / ``Dimension``
    reached through a block is a first-class IR entity like any other —
    it carries a stable ``id``, the RESOLVED ``layer`` (see above), and
    ``block:<outermost name>`` as its first ``style`` token. Those four
    are enough to name the superseding native in a proposal's evidence
    without re-reading the DXF. ``planlens.dxf.truth`` reports the same
    entity with the same coordinates and the same resolved layer, under
    an additive ``"block"`` key.

    Coordinate systems: DXF stores many planar entities' points in the
    entity's own OCS (see :class:`_Ocs`), which differs from the WCS
    whenever the group-210 extrusion is not (0,0,1) — the everyday case
    being a MIRRORED block placement. Those points are resolved to WCS;
    the ones DXF already defines in WCS (LINE ends, MTEXT insert, LEADER
    vertices, a DIMENSION's definition points) are read raw. ANGLES
    stored in that same plane are resolved with it — an ARC's start/end
    and a TEXT/ATTRIB ``rotation`` — because an OCS bearing is no more a
    direction on the sheet than an OCS x is a position on it; MTEXT
    instead carries an explicit WCS direction vector that wins when
    present (:func:`_mtext_bearing`).

    Guards: nesting is capped at :data:`_MAX_BLOCK_DEPTH` levels and the
    number of block entities WALKED at ``max_block_entities`` — a
    pathological file cannot blow up the IR; hitting either cap appends a
    warning instead of failing. A malformed entity never costs its
    siblings: one bad virtual entity, one bad ATTRIB, or a lazy raise
    from ezdxf's generator, is warned about and stepped over, and the
    same holds for the top-level model-space walk. Two losses that are
    ezdxf's rather than ours are reported the same way instead of going
    silent: an entity ezdxf declines to transform (a non-uniformly scaled
    MULTILEADER, say — omitted from ``virtual_entities()`` WITHOUT
    raising) and a MINSERT array, of which only the first placement is
    resolved. Repeated identical warnings are reported once.

    Metadata: ``n_block_entities`` is the number of IR entities the
    explosion CONTRIBUTED, and ``n_block_entities_walked`` (present only
    when the two differ) the number of block entities visited to get them
    — a block of unsupported types walks wide and ingests nothing. Both
    keys are also published when a cap stopped the explosion dead, so
    "the explosion contributed nothing" is stated rather than implied by
    an absent key.
    """
    import os
    import tempfile
    try:
        import ezdxf
    except ImportError as exc:
        raise ImportError("ezdxf is required for DXF ingest. Install with: "
                          "pip install ezdxf>=1.4") from exc
    from planlens.dxf.units import UNIT_FACTORS, detect_units_from_header

    if filepath is None and content is None:
        raise ValueError("Provide either filepath or content")

    tmp_path = None
    if content is not None:
        with tempfile.NamedTemporaryFile(suffix=".dxf", delete=False) as tmp:
            tmp.write(content)
            tmp_path = tmp.name
        try:
            doc = ezdxf.readfile(tmp_path)
        finally:
            os.unlink(tmp_path)
    else:
        doc = ezdxf.readfile(filepath)

    resolved_units = units or detect_units_from_header(doc) or "m"
    factor = UNIT_FACTORS.get(resolved_units, 1.0)
    warnings: List[str] = []

    def conv(x, y):
        yy = -y if flip_y else y
        return (x * factor, yy * factor)

    def conv_angle(deg: float, ocs: Optional[_Ocs] = None) -> float:
        """A stored bearing -> the IR's angle convention, degrees CCW.

        The angular twin of :func:`conv`, and it has to be applied for the
        same reason: an angle measured in a mirrored entity's OCS is not
        the angle the entity plots at, exactly as an OCS x is not the x it
        plots at. ``ocs=None`` means the angle is already WCS (MTEXT's
        group-11 direction) or the entity's OCS is the WCS, and the
        identity case returns the stored value untouched — no
        normalisation, so an unmirrored sheet ingests exactly as before.
        """
        if ocs is not None:
            deg = ocs.angle(deg)
        return (-deg) % 360.0 if flip_y else deg

    msp = doc.modelspace()
    ir = DrawingIR(
        units="m", coordinate_space="model", origin="bottom_left",
        source="dxf", warnings=warnings,
        scale=(factor if factor != 1.0 else None),
        scale_provenance=(f"dxf_units:{resolved_units}->m"
                          if factor != 1.0 else "dxf_native_meters"),
    )

    layers = set()
    n_visited = 0    # block entities pulled from virtual_entities() (work)
    n_ingested = 0   # IR entities those visits actually contributed
    depth_warned = budget_warned = False
    warned_once: set = set()

    def _warn_once(msg: str) -> None:
        """Append ``msg`` unless it has already been reported.

        A block placed 500 times would otherwise report the same
        untransformable child 500 times and bury every other warning.
        The message names the block and the entity type, so distinct
        losses still each get their own line.
        """
        if msg not in warned_once:
            warned_once.add(msg)
            warnings.append(msg)

    def _handle(ent, block: Optional[str] = None, depth: int = 0,
                inherit_layer: Optional[str] = None):
        nonlocal n_visited, n_ingested, depth_warned, budget_warned
        etype = ent.dxftype()
        layer = getattr(ent.dxf, "layer", None)
        if inherit_layer is not None and (layer is None or layer == "0"):
            # DXF/AutoCAD rule: layer "0" inside a BLOCK definition is not
            # an ordinary layer name, it is the sentinel meaning "drawn on
            # the placing INSERT's layer". Every renderer and every layer
            # filter applies it, so the IR records the resolved layer.
            # Because the INSERT has itself already been through this,
            # the resolution chains through nested references for free and
            # stops at the first named layer — exactly the CAD rule.
            layer = inherit_layer
        layers.add(layer)
        style = _dxf_style(ent)
        if block is not None:
            style = f"block:{block}" + (f"|{style}" if style else "")
        common = dict(layer=layer, color=_dxf_color(ent),
                      style=style, source="dxf", confidence=1.0)
        # Resolved once per entity and applied ONLY in the branches whose
        # stored points are OCS (see :class:`_Ocs`).
        ocs = _ocs_map(ent)
        try:
            if etype == "LINE":
                s, e = ent.dxf.start, ent.dxf.end
                ir.add(Line(start=conv(s.x, s.y), end=conv(e.x, e.y), **common))
            elif etype == "LWPOLYLINE":
                verts = [conv(*_pt_xy(p, ocs))
                         for p in ent.get_points(format="xy")]
                ir.add(Polyline(vertices=verts, closed=bool(ent.closed),
                                **common))
            elif etype == "POLYLINE":
                # 2D polyline vertices are OCS; a 3D polyline's are WCS.
                o = ocs if getattr(ent, "is_2d_polyline", True) else None
                verts = [conv(*_pt_xy(v.dxf.location, o))
                         for v in ent.vertices]
                ir.add(Polyline(vertices=verts,
                                closed=bool(ent.is_closed), **common))
            elif etype == "ARC":
                c = ent.dxf.center
                sa = ent.dxf.start_angle
                ea = ent.dxf.end_angle
                if ocs is not None:
                    sa, ea = ocs.angle(sa), ocs.angle(ea)
                    if ocs.reverses:
                        # A mirrored plane reverses the sweep direction,
                        # so the CCW start and end exchange roles.
                        sa, ea = ea, sa
                if flip_y:
                    sa, ea = (-ea) % 360.0, (-sa) % 360.0
                ir.add(Arc(center=conv(*_pt_xy(c, ocs)),
                           radius=ent.dxf.radius * factor,
                           start_angle=sa, end_angle=ea, **common))
            elif etype == "CIRCLE":
                c = ent.dxf.center
                ir.add(Circle(center=conv(*_pt_xy(c, ocs)),
                              radius=ent.dxf.radius * factor, **common))
            elif etype in ("ELLIPSE", "SPLINE"):
                try:
                    verts = [conv(p.x, p.y) for p in ent.flattening(0.01)]
                except Exception:
                    verts = []
                if len(verts) >= 2:
                    st = dict(common)
                    # APPEND the flattening note to the style token list;
                    # do not substitute it for what is already there.
                    # Whatever tokens are already present — block origin,
                    # and an EXPLICIT non-BYLAYER linetype when the entity
                    # carries one (that is all ``_dxf_style`` reports; a
                    # BYLAYER entity contributes no token at all) — stay
                    # true of this polyline after flattening. A
                    # non-uniformly scaled block turns every CIRCLE/ARC
                    # into an ELLIPSE, so overwriting here silently
                    # stripped block provenance off most curved detail
                    # geometry.
                    approx = f"approx_from_{etype.lower()}"
                    base = common["style"]
                    st["style"] = f"{base}|{approx}" if base else approx
                    ir.add(Polyline(vertices=verts, closed=False, **st))
            elif etype == "TEXT":
                # Both the insert point AND the rotation are OCS: the
                # angle is measured in the entity's own xy plane, whose x
                # axis a mirror runs backwards. Resolving the point but
                # not the angle put a correctly-placed note's BOX on the
                # wrong side of it (measured: a 33-character note on a
                # detail spanning x 40..50 reported a bbox out to 53.9).
                ins = ent.dxf.insert
                ir.add(TextItem(content=ent.dxf.text,
                                position=conv(*_pt_xy(ins, ocs)),
                                rotation=conv_angle(
                                    getattr(ent.dxf, "rotation", 0.0), ocs),
                                height=getattr(ent.dxf, "height", 0.0) * factor,
                                **common))
            elif etype == "MTEXT":
                # MTEXT's group-10 insert is WCS, not OCS (verified
                # against a mirrored placement: ezdxf reports it already
                # mirrored), so it is read raw like a LINE end. Its
                # BEARING is not raw, though — see :func:`_mtext_bearing`.
                ins = ent.dxf.insert
                ir.add(TextItem(content=ent.text,
                                position=conv(ins.x, ins.y),
                                rotation=conv_angle(_mtext_bearing(ent, ocs)),
                                height=getattr(ent.dxf, "char_height", 0.0)
                                * factor, **common))
            elif etype == "HATCH":
                region = _hatch_region(ent, conv, common, ocs)
                if region is not None:
                    ir.add(region)
            elif etype == "LEADER":
                # Native annotation leader: exact vertices tip-first, plus
                # the annotation text resolved through annotation_handle
                # (an MTEXT elsewhere in the entity db). LEADER vertices
                # can be plain tuples rather than Vec3 (ezdxf 1.4).
                verts = [conv(v[0], v[1]) for v in ent.vertices]
                text = None
                handle = ent.dxf.get("annotation_handle", None)
                if handle:
                    annot = doc.entitydb.get(handle)
                    if annot is not None and annot.dxftype() == "MTEXT":
                        text = _mtext_plain(annot.text)
                if len(verts) >= 1:
                    ir.add(Leader(
                        vertices=verts,
                        has_arrowhead=bool(ent.dxf.get("has_arrowhead", 1)),
                        text=text, **common))
            elif etype == "MULTILEADER":
                # One Leader per leader LINE (a multileader can point at
                # several targets with one text). Vertices are the line's
                # own (tip-first); the dogleg/landing is not fabricated.
                ctx = ent.context
                mtext = getattr(ctx, "mtext", None)
                text = (_mtext_plain(mtext.default_content)
                        if mtext is not None else None) or None
                for ml_leader in ctx.leaders:
                    for line in ml_leader.lines:
                        verts = [conv(v.x, v.y) for v in line.vertices]
                        if verts:
                            ir.add(Leader(vertices=verts,
                                          has_arrowhead=True,
                                          text=text, **common))
            elif etype == "DIMENSION":
                # Group 10/13/14 (the definition points) are WCS; only
                # group 11, the text midpoint, is OCS. Mixing them up is
                # what put a mirrored detail's dimension text 90 in from
                # its own defpoints.
                defpoints = []
                for attr in ("defpoint", "defpoint2", "defpoint3"):
                    p = ent.dxf.get(attr, None)
                    if p is not None:
                        defpoints.append(conv(p.x, p.y))
                tm = ent.dxf.get("text_midpoint", None)
                try:
                    meas = float(ent.get_measurement()) * factor
                except Exception:
                    meas = None
                ir.add(Dimension(
                    defpoints=defpoints,
                    text_midpoint=(conv(*_pt_xy(tm, ocs))
                                   if tm is not None else None),
                    measurement=meas,
                    text=(ent.dxf.get("text", "") or None),
                    dimtype=int(ent.dxf.get("dimtype", 0)), **common))
            elif etype == "INSERT":
                # Block reference: ATTRIB values — the title-block
                # metadata carrier — land as TextItems with the block/tag
                # recorded in ``style``; then (explode_blocks) the block's
                # geometry is exploded via virtual_entities() with the
                # exact insert transform, recursing into nested INSERTs
                # under the depth/entity caps.
                bname = ent.dxf.get("name", "?")
                mcount = getattr(ent, "mcount", 1) or 1
                if mcount > 1:
                    # MINSERT: ezdxf's virtual_entities() resolves only the
                    # FIRST copy of the array, so the other placements are
                    # plotted on paper but absent from the IR. Array
                    # expansion is not implemented; say so rather than
                    # under-report the sheet silently.
                    _warn_once(f"MINSERT '{bname}' places {mcount} copies "
                               f"in a row/column array; only the first is "
                               f"ingested (array expansion not "
                               f"implemented).")
                for attrib in getattr(ent, "attribs", ()) or ():
                    # Per-ATTRIB guard, for the same reason the virtual
                    # entities below get one: one unreadable attribute
                    # must not cost its siblings — or, worse, abort this
                    # INSERT's whole block explosion via the outer handler.
                    try:
                        txt = attrib.dxf.get("text", "")
                        if not txt:
                            continue
                        ins = attrib.dxf.insert
                        st = dict(common)
                        # An ATTRIB reached through a block is BOTH block
                        # content and an attribute value; the style token
                        # list carries both, which is what makes
                        # ``n_block_entities`` equal the number of
                        # ``block:``-tagged IR entities.
                        tag = f"attrib:{bname}:{attrib.dxf.get('tag', '')}"
                        st["style"] = (f"block:{block}|{tag}"
                                       if block is not None else tag)
                        # An ATTRIB is a TEXT in every coordinate respect,
                        # so its rotation is OCS too and gets the same map
                        # its insert point does — one shared _ocs_map, so
                        # the two can never be resolved against different
                        # extrusions.
                        a_ocs = _ocs_map(attrib)
                        ir.add(TextItem(
                            content=txt,
                            position=conv(*_pt_xy(ins, a_ocs)),
                            rotation=conv_angle(
                                attrib.dxf.get("rotation", 0.0), a_ocs),
                            height=attrib.dxf.get("height", 0.0) * factor,
                            **st))
                    except Exception as exc:
                        warnings.append(
                            f"Skipped an ATTRIB on block "
                            f"'{bname}': {exc}")
                if not explode_blocks:
                    return
                if depth >= _MAX_BLOCK_DEPTH:
                    if not depth_warned:
                        warnings.append(
                            f"Block nesting deeper than {_MAX_BLOCK_DEPTH}"
                            f" levels ('{bname}') left unexploded.")
                        depth_warned = True
                    return
                top = block if block is not None else bname
                # Pull the virtual entities one at a time and guard each
                # one on its own. A DXF is a bag of INDEPENDENT entities:
                # one child's failure carries no information about its
                # siblings, so letting it abort the walk would model a
                # dependency that does not exist — and silently drop the
                # rest of the block. Cap, don't delete: the survivors stay
                # in the IR and the loss becomes a visible warning.
                #
                # ezdxf DROPS entities it cannot transform — a
                # non-uniformly scaled MULTILEADER is the everyday one —
                # and yields the rest WITHOUT raising, so the per-entity
                # guards below never see the loss. The
                # skipped_entity_callback is the only channel that
                # reports it, and it is what turns a silently missing
                # plotted construct into a visible warning.
                def _skipped(orig, reason, _b=bname):
                    vt = orig.dxftype()
                    if vt not in _SUPPORTED_TYPES:
                        return   # we would have skipped it ourselves
                    _warn_once(
                        f"Block '{_b}': ezdxf could not transform a "
                        f"{vt} ({reason}); that plotted geometry is "
                        f"NOT in the IR.")

                virts = iter(ent.virtual_entities(
                    skipped_entity_callback=_skipped))
                n_local = 0
                while True:
                    try:
                        virt = next(virts)
                    except StopIteration:
                        break
                    except Exception as exc:
                        # A lazy raise from ezdxf's own generator: report
                        # it as a counted TRUNCATION, not as a skip.
                        warnings.append(
                            f"Block '{bname}' explosion truncated after "
                            f"{n_local} entities: {exc}")
                        break
                    # The budget bounds WORK, not ingest: a 50k-entity
                    # block of unsupported types still costs 50k virtual
                    # entity constructions, so gating on ingests instead
                    # would let a bomb file walk unbounded.
                    if n_visited >= max_block_entities:
                        if not budget_warned:
                            warnings.append(
                                f"Block explosion stopped at the "
                                f"{max_block_entities}-entity budget "
                                f"(entities walked, not ingested); "
                                f"remaining block geometry omitted.")
                            budget_warned = True
                        return
                    n_visited += 1
                    n_local += 1
                    before = len(ir.entities)
                    try:
                        _handle(virt, block=top, depth=depth + 1,
                                inherit_layer=layer)
                    except Exception as exc:
                        vt = getattr(virt, "dxftype", lambda: "?")()
                        warnings.append(
                            f"Skipped a {vt} inside block '{bname}': {exc}")
                    if depth == 0:
                        # Count the IR delta only in the OUTERMOST frame:
                        # at depth 0 each virtual entity's whole subtree is
                        # spanned exactly once, so nested references are
                        # not double-counted (and unsupported types and
                        # bare INSERT wrappers correctly contribute 0).
                        n_ingested += len(ir.entities) - before
            # other entity types are skipped (kept honest — not fabricated)
        except Exception as exc:  # pragma: no cover - malformed entity guard
            warnings.append(f"Skipped {etype} on layer '{layer}': {exc}")

    for ent in msp:
        # Same independence rule at the top level: one malformed
        # model-space entity must not cost the whole sheet.
        try:
            _handle(ent)
        except Exception as exc:  # pragma: no cover - malformed entity guard
            warnings.append(f"Skipped a model-space entity: {exc}")

    ir.metadata = {"dxf_units": resolved_units,
                   "n_layers": len([lyr for lyr in layers if lyr is not None])}
    if n_ingested or n_visited or depth_warned or budget_warned:
        # Two distinct quantities that used to share one integer: what the
        # explosion CONTRIBUTED to the IR, and what it had to walk to get
        # there. The walked count is kept rather than discarded whenever
        # it differs, so a caller can see how much of a block was
        # unsupported or was pure INSERT scaffolding.
        #
        # A file whose explosion was stopped DEAD by a cap (a zero budget,
        # or nothing but over-deep nesting) reaches here with both counts
        # at zero, and used to publish no block metadata at all — the one
        # case where a caller most needs to be told the explosion
        # contributed nothing. The cap flags keep the keys present.
        ir.metadata["n_block_entities"] = n_ingested
        if n_visited != n_ingested:
            ir.metadata["n_block_entities_walked"] = n_visited
    if not ir.entities:
        warnings.append("No supported entities found in DXF model space.")
    return ir


def _hatch_region(ent, conv, common, ocs: Optional[_Ocs] = None
                  ) -> Optional[Region]:
    """Best-effort HATCH → Region using the first boundary path's vertices.

    HATCH boundary points are OCS, so ``ocs`` (the entity's :func:`_ocs_map`)
    resolves them; ``None`` means the OCS is the WCS.
    """
    try:
        pattern = getattr(ent.dxf, "pattern_name", None)
        for path in ent.paths:
            verts = []
            for v in getattr(path, "vertices", []) or []:
                verts.append(conv(*_pt_xy(v, ocs)))
            if len(verts) >= 3:
                st = dict(common)
                return Region(boundary=verts, pattern=pattern, **st)
    except Exception:
        return None
    return None


# ---------------------------------------------------------------------------
# PDF (vector)
# ---------------------------------------------------------------------------

def _closed(pts, tol=1e-6) -> bool:
    return (len(pts) >= 3
            and abs(pts[0][0] - pts[-1][0]) <= tol
            and abs(pts[0][1] - pts[-1][1]) <= tol)


def from_pdf_vector(filepath: str = None, content: bytes = None,
                    page: int = 0, scale: Optional[float] = None,
                    calibration: Optional[Dict[str, Any]] = None,
                    origin: str = "bottom_left",
                    name: str = "PDF vector import") -> DrawingIR:
    """Build a DrawingIR from a PDF page's vector line-work + text.

    Reuses ``pdf_import.extract_colored_paths`` (per-path point lists + color)
    and ``pdf_import.discover_pdf_content`` (page size + text). When ``scale``
    (meters per PDF point) or a two-point ``calibration`` ({p1, p2, distance_m})
    is supplied, coordinates are promoted to model meters; otherwise the IR
    stays in page points and scale CANDIDATES are attached to metadata (via the
    ``pdf_import`` scale module) as proposals, never applied.
    """
    from planlens.pdf import (
        calibrate_scale, discover_pdf_content, extract_colored_paths,
        propose_scale,
    )

    info = discover_pdf_content(filepath=filepath, content=content, page=page)
    width_pt = info["page_size"]["width"]
    height_pt = info["page_size"]["height"]
    text_blocks = info.get("text_blocks", [])
    # Page-point path coordinates (flipped per origin, scale=1.0).
    regions = extract_colored_paths(filepath=filepath, content=content,
                                    page=page, scale=1.0, origin=origin)

    # Resolve scale factor (meters per point) if any.
    sf = None
    provenance = None
    metadata: Dict[str, Any] = {}
    if scale is not None:
        sf = float(scale)
        provenance = f"explicit_scale:{sf:g} m/pt"
    elif calibration:
        sf = calibrate_scale(tuple(calibration["p1"]), tuple(calibration["p2"]),
                             calibration["distance_m"])
        provenance = "two_point_calibration"
    else:
        proposals = propose_scale(text_blocks, calibration=None)
        if proposals.get("candidates"):
            metadata["scale_candidates"] = proposals["candidates"]
            metadata["scale_note"] = proposals["note"]

    is_model = sf is not None
    smul = sf if is_model else 1.0

    def apply_scale(x, y):
        return (x * smul, y * smul)

    def text_xy(x, y):
        yy = (height_pt - y) if origin == "bottom_left" else y
        return apply_scale(x, yy)

    ir = DrawingIR(
        width=(width_pt * smul), height=(height_pt * smul),
        units=("m" if is_model else "pt"),
        coordinate_space=("model" if is_model else "page"),
        scale=sf, scale_provenance=provenance, origin=origin,
        source="pdf_vector", metadata=metadata,
    )

    for reg in regions:
        pts = [apply_scale(x, y) for x, y in reg.get("points", [])]
        color = reg.get("color")
        if len(pts) < 2:
            continue
        if len(pts) == 2:
            ir.add(Line(start=pts[0], end=pts[1], color=color,
                        source="pdf_vector", confidence=1.0))
        else:
            closed = _closed(pts)
            verts = pts[:-1] if closed else pts
            ir.add(Polyline(vertices=verts, closed=closed, color=color,
                            source="pdf_vector", confidence=1.0))

    for tb in text_blocks:
        txt = (tb.get("text") or "").strip()
        if not txt:
            continue
        size = tb.get("size", 0.0)
        ir.add(TextItem(content=txt, position=text_xy(tb["x"], tb["y"]),
                        rotation=0.0, height=size * smul,
                        source="pdf_vector", confidence=1.0))

    ir.metadata.setdefault("page_number", page)
    if not ir.entities:
        ir.warnings.append(
            "No vector paths or text found on this page — the drawing may be a "
            "raster scan (try from_raster) or a different page.")
    return ir


# ---------------------------------------------------------------------------
# Raster (delegates to the OpenCV leg — keeps cv2 optional)
# ---------------------------------------------------------------------------

def from_raster(filepath: str = None, image: Any = None, **kwargs) -> DrawingIR:
    """Build a DrawingIR by tracing a raster image (source='raster_trace').

    Thin wrapper over :func:`planlens.ir.raster.trace_raster` so the OpenCV
    dependency stays isolated to the raster leg. See that function for the full
    parameter list (scale, detectors, Hough/contour tuning, OCR).
    """
    from planlens.ir.raster import trace_raster
    return trace_raster(filepath=filepath, image=image, **kwargs)


__all__ = ["from_dxf", "from_pdf_vector", "from_raster"]
