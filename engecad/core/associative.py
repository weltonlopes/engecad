"""Associatividade de cotas persistida como XDATA no proprio DXF.

O AutoCAD preserva XDATA desconhecida, portanto os vinculos continuam no
arquivo mesmo quando ele passa por outro editor. Cada ponto de definicao da
cota aponta para um handle e uma ancora semantica da geometria de origem.
"""

from __future__ import annotations

import json
import math
from copy import deepcopy

from .dimensions import DIMENSION_TYPES, rerender_dimension
from .geometry import Vec2, closest_point_on_segment, line_intersection

ASSOC_APPID = "ENGECAD_ASSOC"
ASSOC_VERSION = 1
_CHUNK = 240


def _handle(entity) -> str | None:
    value = entity.dxf.get("handle") if entity is not None else None
    return str(value) if value else None


def _association_payload(entity) -> dict:
    if entity.dxftype() not in DIMENSION_TYPES or not entity.has_xdata(ASSOC_APPID):
        return {}
    try:
        raw = "".join(str(tag.value) for tag in entity.get_xdata(ASSOC_APPID) if tag.code == 1000)
        data = json.loads(raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return {}
    if int(data.get("v", 0)) != ASSOC_VERSION:
        return {}
    return data


def get_dimension_associations(entity) -> dict[str, dict]:
    anchors = _association_payload(entity).get("a", {})
    return deepcopy(anchors) if isinstance(anchors, dict) else {}


def get_dimension_association_mode(entity) -> str:
    return str(_association_payload(entity).get("m", ""))


def set_dimension_associations(
    entity, associations: dict[str, dict] | None, mode: str | None = None
) -> None:
    if entity.dxftype() not in DIMENSION_TYPES:
        return
    anchors = {str(k): deepcopy(v) for k, v in (associations or {}).items() if v}
    if not anchors:
        entity.discard_xdata(ASSOC_APPID)
        return
    drawing = entity.doc
    if drawing is not None and ASSOC_APPID not in drawing.appids:
        drawing.appids.add(ASSOC_APPID)
    if mode is None:
        mode = get_dimension_association_mode(entity)
    data = {"v": ASSOC_VERSION, "a": anchors}
    if mode:
        data["m"] = str(mode)
    raw = json.dumps(data, separators=(",", ":"))
    entity.set_xdata(ASSOC_APPID, [(1000, raw[i : i + _CHUNK]) for i in range(0, len(raw), _CHUNK)])


def detach_dimension_anchor(entity, attribute: str | None = None) -> None:
    associations = get_dimension_associations(entity)
    if attribute is None:
        associations.clear()
    else:
        associations.pop(attribute, None)
    set_dimension_associations(entity, associations)


def _line_anchor(entity, point: Vec2) -> dict:
    a = Vec2.of(entity.dxf.start)
    b = Vec2.of(entity.dxf.end)
    edge = b - a
    t = (point - a).dot(edge) / edge.length_sq if edge.length_sq > 1e-18 else 0.0
    return {"h": _handle(entity), "k": "line", "t": t}


def _polyline_anchor(entity, point: Vec2, kind: str) -> dict | None:
    points = [Vec2(p[0], p[1]) for p in entity.get_points("xy")]
    if not points:
        return None
    if kind in ("end", "node"):
        index = min(range(len(points)), key=lambda i: points[i].distance_to(point))
        return {"h": _handle(entity), "k": "vertex", "i": index}
    segments = [(i, points[i], points[i + 1]) for i in range(len(points) - 1)]
    if entity.closed and len(points) > 2:
        segments.append((len(points) - 1, points[-1], points[0]))
    if not segments:
        return {"h": _handle(entity), "k": "vertex", "i": 0}
    i, a, b = min(
        segments,
        key=lambda item: closest_point_on_segment(point, item[1], item[2]).distance_to(point),
    )
    edge = b - a
    t = (point - a).dot(edge) / edge.length_sq if edge.length_sq > 1e-18 else 0.0
    return {"h": _handle(entity), "k": "segment", "i": i, "t": max(0.0, min(1.0, t))}


def anchor_for_entity(entity, point, kind: str = "nearest") -> dict | None:
    """Cria uma ancora resolvivel para um ponto sobre ``entity``."""
    handle = _handle(entity)
    if handle is None or entity.dxftype() in DIMENSION_TYPES:
        return None
    point = Vec2.of(point)
    t = entity.dxftype()
    dxf = entity.dxf
    if t == "LINE":
        return _line_anchor(entity, point)
    if t == "LWPOLYLINE":
        return _polyline_anchor(entity, point, kind)
    if t == "CIRCLE":
        center = Vec2.of(dxf.center)
        if kind == "center" or center.distance_to(point) <= 1e-9:
            return {"h": handle, "k": "center"}
        if kind == "quad":
            angle = round((point - center).angle / (math.pi / 2)) * (math.pi / 2)
            return {"h": handle, "k": "circle", "a": angle, "q": 1}
        return {"h": handle, "k": "circle", "a": (point - center).angle}
    if t == "ARC":
        center = Vec2.of(dxf.center)
        if kind == "center" or center.distance_to(point) <= 1e-9:
            return {"h": handle, "k": "center"}
        start = math.radians(float(dxf.start_angle))
        span = math.radians((float(dxf.end_angle) - float(dxf.start_angle)) % 360.0 or 360.0)
        fraction = ((point - center).angle - start) % math.tau
        fraction = max(0.0, min(1.0, fraction / span))
        if kind == "end":
            fraction = 0.0 if fraction < 0.5 else 1.0
        elif kind == "mid":
            fraction = 0.5
        return {"h": handle, "k": "arc", "t": fraction}
    if t in ("POINT", "TEXT", "MTEXT", "INSERT"):
        return {"h": handle, "k": "insert"}
    return None


def anchor_from_snap(snap) -> dict | None:
    if snap is None or snap.source is None:
        return None
    sources = snap.source if isinstance(snap.source, tuple) else (snap.source,)
    if snap.kind == "intersection" and len(sources) == 2:
        handles = [_handle(e) for e in sources]
        if all(handles):
            return {"h": handles, "k": "intersection"}
    return anchor_for_entity(sources[0], snap.point, snap.kind)


def _entity_by_handle(doc, handle: str):
    return doc.entity_by_handle(str(handle))


def _resolve_single(doc, anchor: dict) -> Vec2 | None:
    entity = _entity_by_handle(doc, anchor.get("h", ""))
    if entity is None or not entity.is_alive:
        return None
    kind = anchor.get("k")
    dxf = entity.dxf
    if kind == "line" and entity.dxftype() == "LINE":
        a, b = Vec2.of(dxf.start), Vec2.of(dxf.end)
        return a + (b - a) * float(anchor.get("t", 0.0))
    if kind in ("vertex", "segment") and entity.dxftype() == "LWPOLYLINE":
        points = [Vec2(p[0], p[1]) for p in entity.get_points("xy")]
        if not points:
            return None
        index = int(anchor.get("i", 0))
        if kind == "vertex":
            return points[index] if 0 <= index < len(points) else None
        if not 0 <= index < len(points):
            return None
        next_index = (index + 1) % len(points)
        if next_index == 0 and not entity.closed:
            return None
        t = float(anchor.get("t", 0.0))
        return points[index] + (points[next_index] - points[index]) * t
    if kind == "center" and entity.dxftype() in ("CIRCLE", "ARC"):
        return Vec2.of(dxf.center)
    if kind == "circle" and entity.dxftype() == "CIRCLE":
        center = Vec2.of(dxf.center)
        return Vec2.polar(center, float(anchor.get("a", 0.0)), float(dxf.radius))
    if kind == "arc" and entity.dxftype() == "ARC":
        center = Vec2.of(dxf.center)
        start = math.radians(float(dxf.start_angle))
        span = math.radians((float(dxf.end_angle) - float(dxf.start_angle)) % 360.0 or 360.0)
        angle = start + span * float(anchor.get("t", 0.0))
        return Vec2.polar(center, angle, float(dxf.radius))
    if kind == "insert" and entity.dxftype() in ("POINT", "TEXT", "MTEXT", "INSERT"):
        from .entities import entity_insert_point

        return entity_insert_point(entity)
    return None


def _resolve_intersection(doc, anchor: dict, hint: Vec2 | None) -> Vec2 | None:
    handles = anchor.get("h", [])
    if not isinstance(handles, list) or len(handles) != 2:
        return None
    first, second = (_entity_by_handle(doc, h) for h in handles)
    if first is None or second is None:
        return None
    from .entities import entity_polylines

    intersections = []
    for pa in entity_polylines(first, 0.001):
        for pb in entity_polylines(second, 0.001):
            for i in range(len(pa) - 1):
                for j in range(len(pb) - 1):
                    point = line_intersection(pa[i], pa[i + 1], pb[j], pb[j + 1], as_segments=True)
                    if point is not None:
                        intersections.append(point)
    if not intersections:
        return None
    return min(intersections, key=lambda p: p.distance_to(hint)) if hint else intersections[0]


def resolve_anchor(doc, anchor: dict, hint: Vec2 | None = None) -> Vec2 | None:
    if anchor.get("k") == "intersection":
        return _resolve_intersection(doc, anchor, hint)
    return _resolve_single(doc, anchor)


def anchor_handles(anchor: dict) -> set[str]:
    value = anchor.get("h")
    if isinstance(value, list):
        return {str(h) for h in value}
    return {str(value)} if value else set()


def dimension_source_handles(entity) -> set[str]:
    handles: set[str] = set()
    for anchor in get_dimension_associations(entity).values():
        handles.update(anchor_handles(anchor))
    return handles


def associated_dimensions(doc, source_handles: set[str] | None = None) -> list:
    result = []
    for entity in doc.entities():
        if entity.dxftype() not in DIMENSION_TYPES:
            continue
        handles = dimension_source_handles(entity)
        if handles and (source_handles is None or handles & source_handles):
            result.append(entity)
    return result


def update_associative_dimension(
    doc, entity, preserve_dimension_location: bool = False
) -> bool:
    associations = get_dimension_associations(entity)
    mode = get_dimension_association_mode(entity)
    old_offset = None
    aligned_attrs = ("defpoint2", "defpoint3", "defpoint")
    if (
        mode == "aligned"
        and not preserve_dimension_location
        and all(entity.dxf.hasattr(a) for a in aligned_attrs)
    ):
        old_p1 = Vec2.of(entity.dxf.defpoint2)
        old_p2 = Vec2.of(entity.dxf.defpoint3)
        old_edge = old_p2 - old_p1
        if old_edge.length > 1e-12:
            old_offset = old_edge.cross(Vec2.of(entity.dxf.defpoint) - old_p1) / old_edge.length
    changed = False
    for attribute, anchor in associations.items():
        if not entity.dxf.hasattr(attribute):
            continue
        current = Vec2.of(entity.dxf.get(attribute))
        resolved = resolve_anchor(doc, anchor, current)
        if resolved is not None and resolved.distance_to(current) > 1e-10:
            setattr(entity.dxf, attribute, (resolved.x, resolved.y, 0.0))
            changed = True
    if mode == "aligned" and entity.dxf.hasattr("defpoint2") and entity.dxf.hasattr(
        "defpoint3"
    ):
        p1 = Vec2.of(entity.dxf.defpoint2)
        p2 = Vec2.of(entity.dxf.defpoint3)
        edge = p2 - p1
        if edge.length > 1e-12:
            angle = math.degrees(edge.angle) % 360.0
            if abs(float(entity.dxf.get("angle", 0.0) or 0.0) - angle) > 1e-10:
                entity.dxf.angle = angle
                changed = True
            if old_offset is not None:
                normal = Vec2(-edge.y / edge.length, edge.x / edge.length)
                base = p1 + normal * old_offset
                if Vec2.of(entity.dxf.defpoint).distance_to(base) > 1e-10:
                    entity.dxf.defpoint = (base.x, base.y, 0.0)
                    changed = True
    if changed:
        rerender_dimension(entity)
    return changed


def association_status(doc, entity) -> tuple[int, int]:
    associations = get_dimension_associations(entity)
    resolved = 0
    for attribute, anchor in associations.items():
        hint = Vec2.of(entity.dxf.get(attribute)) if entity.dxf.hasattr(attribute) else None
        if resolve_anchor(doc, anchor, hint) is not None:
            resolved += 1
    return len(associations), resolved


def remap_dimension_associations(entity, handle_map: dict[str, str]) -> None:
    associations = get_dimension_associations(entity)
    for anchor in associations.values():
        value = anchor.get("h")
        if isinstance(value, list):
            anchor["h"] = [handle_map.get(str(h), str(h)) for h in value]
        elif value:
            anchor["h"] = handle_map.get(str(value), str(value))
    set_dimension_associations(entity, associations)


def rebind_replacement_associations(doc, old, replacements) -> int:
    """Transfere ancoras que sobreviveram a TRIM para as novas entidades."""
    old_handle = _handle(old)
    candidates = [e for e in replacements if e is not None and e.is_alive]
    if old_handle is None or not candidates:
        return 0
    from .entities import entity_bbox
    from .picking import entity_distance

    bbox = entity_bbox(old)
    tolerance = max(1e-6, math.hypot(bbox.width, bbox.height) * 1e-8)
    count = 0
    for dimension in associated_dimensions(doc, {old_handle}):
        associations = get_dimension_associations(dimension)
        changed = False
        for attribute, anchor in associations.items():
            if old_handle not in anchor_handles(anchor):
                continue
            hint = (
                Vec2.of(dimension.dxf.get(attribute))
                if dimension.dxf.hasattr(attribute)
                else None
            )
            point = resolve_anchor(doc, anchor, hint)
            if point is None:
                continue
            candidate = min(candidates, key=lambda e: entity_distance(e, point, tolerance * 0.1))
            if entity_distance(candidate, point, tolerance * 0.1) > tolerance:
                continue  # o trecho que continha a ancora foi removido
            if anchor.get("k") == "intersection":
                replacement = deepcopy(anchor)
                replacement["h"] = [
                    _handle(candidate) if str(h) == old_handle else str(h)
                    for h in anchor.get("h", [])
                ]
            else:
                semantic = {
                    "center": "center",
                    "vertex": "end",
                    "insert": "node",
                }.get(anchor.get("k"), "nearest")
                replacement = anchor_for_entity(candidate, point, semantic)
            if replacement is not None:
                associations[attribute] = replacement
                changed = True
        if changed:
            set_dimension_associations(dimension, associations)
            update_associative_dimension(doc, dimension)
            count += 1
    return count
