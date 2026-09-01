"""Hachuras DXF nativas, incluindo limites associativos mantidos pelo EngeCAD."""

from __future__ import annotations

import json
from dataclasses import dataclass

from ezdxf.lldxf.const import BOUNDARY_PATH_EXTERNAL, BOUNDARY_PATH_OUTERMOST
from ezdxf.path import from_hatch_boundary_path
from ezdxf.tools.pattern import load as load_patterns
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import polygonize, unary_union

from .entities import entity_polylines
from .geometry import Vec2

APPID = "ENGECAD_HATCH"
SUPPORTED_BOUNDARIES = {"LWPOLYLINE", "POLYLINE", "CIRCLE", "ELLIPSE"}


@dataclass(slots=True)
class HatchSettings:
    pattern: str = "ANSI31"
    scale: float = 1.0
    angle: float = 0.0
    color: int = 7
    transparency: float = 0.0
    island_style: int = 0  # 0 normal, 1 externo, 2 ignorar
    custom_definition: list | None = None

    @property
    def solid(self) -> bool:
        return self.pattern.upper() == "SOLID"


def available_patterns() -> list[str]:
    return ["SOLID", *sorted(load_patterns(measurement=1))]


def _ensure_appid(drawing) -> None:
    if APPID not in drawing.appids:
        drawing.appids.new(APPID)


def _set_metadata(hatch, mode: str, seed: Vec2 | None = None) -> None:
    _ensure_appid(hatch.doc)
    payload = {"version": 1, "mode": mode}
    if seed is not None:
        payload["seed"] = [seed.x, seed.y]
    hatch.set_xdata(APPID, [(1000, json.dumps(payload, separators=(",", ":")))])


def hatch_metadata(hatch) -> dict:
    try:
        tags = hatch.get_xdata(APPID)
        return json.loads(next(t.value for t in tags if t.code == 1000))
    except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError):
        return {}


def hatch_source_handles(hatch) -> set[str]:
    handles: set[str] = set()
    for path in hatch.paths:
        handles.update(str(h) for h in path.source_boundary_objects if h)
    return handles


def hatch_association_status(doc, hatch) -> tuple[int, int]:
    handles = hatch_source_handles(hatch)
    return len(handles), sum(doc.entity_by_handle(h) is not None for h in handles)


def associated_hatches(doc, handles: set[str] | None = None) -> list:
    wanted = {str(h) for h in handles} if handles else None
    out = []
    for entity in doc.msp:
        if entity.dxftype() != "HATCH" or not entity.is_alive:
            continue
        refs = hatch_source_handles(entity)
        if refs and (wanted is None or refs & wanted):
            out.append(entity)
    return out


def _closed_polyline(entity, sagitta: float = 0.01) -> list[tuple[float, float]]:
    polys = entity_polylines(entity, sagitta)
    if not polys:
        return []
    points = [(p.x, p.y) for p in polys[0]]
    if len(points) >= 3 and points[0] != points[-1]:
        points.append(points[0])
    return points


def is_closed_boundary(entity) -> bool:
    t = entity.dxftype()
    if t in ("CIRCLE", "ELLIPSE"):
        return True
    if t in ("LWPOLYLINE", "POLYLINE"):
        return bool(entity.closed)
    points = _closed_polyline(entity)
    return len(points) >= 4 and points[0] == points[-1]


def _entity_polygon(entity) -> Polygon | None:
    points = _closed_polyline(entity, 0.005)
    if len(points) < 4:
        return None
    poly = Polygon(points)
    return poly if poly.is_valid and poly.area > 1e-12 else None


def _add_entity_path(hatch, entity, flags: int):
    """Adiciona um limite preservando arcos/bulges quando o DXF permite."""
    t = entity.dxftype()
    if t == "LWPOLYLINE":
        vertices = [(float(x), float(y), float(b)) for x, y, b in entity.get_points("xyb")]
        return hatch.paths.add_polyline_path(vertices, is_closed=True, flags=flags)
    if t == "CIRCLE":
        dxf = entity.dxf
        path = hatch.paths.add_edge_path(flags=flags)
        path.add_arc((dxf.center.x, dxf.center.y), float(dxf.radius), 0.0, 360.0)
        return path
    if t == "ELLIPSE":
        dxf = entity.dxf
        path = hatch.paths.add_edge_path(flags=flags)
        path.add_ellipse(
            (dxf.center.x, dxf.center.y),
            (dxf.major_axis.x, dxf.major_axis.y),
            float(dxf.ratio),
            start_angle=0.0,
            end_angle=360.0,
        )
        return path
    points = _closed_polyline(entity)
    if len(points) > 1 and points[0] == points[-1]:
        points.pop()
    return hatch.paths.add_polyline_path(points, is_closed=True, flags=flags)


def _depth(poly: Polygon, polygons: list[Polygon]) -> int:
    representative = poly.representative_point()
    return sum(1 for other in polygons if other is not poly and other.contains(representative))


def set_hatch_boundaries(hatch, entities: list) -> None:
    entities = [e for e in entities if e.is_alive and is_closed_boundary(e)]
    if not entities:
        raise ValueError("selecione ao menos um contorno fechado")
    polygons = [_entity_polygon(e) for e in entities]
    if any(poly is None for poly in polygons):
        raise ValueError("um dos contornos selecionados e invalido")
    valid_polygons = [poly for poly in polygons if poly is not None]
    hatch.remove_association()
    hatch.paths.clear()
    for entity, poly in zip(entities, valid_polygons, strict=True):
        nesting = _depth(poly, valid_polygons)
        if nesting == 0:
            flags = BOUNDARY_PATH_EXTERNAL
        elif nesting == 1:
            flags = BOUNDARY_PATH_OUTERMOST
        else:
            flags = 0
        path = _add_entity_path(hatch, entity, flags)
        hatch.associate(path, [entity])
    hatch.dxf.associative = 1
    _set_metadata(hatch, "entities")


def _linework(doc, exclude=()) -> tuple[list[LineString], list]:
    excluded = {id(e) for e in exclude}
    lines: list[LineString] = []
    sources: list = []
    for entity in doc.msp:
        if id(entity) in excluded or entity.dxftype() in ("HATCH", "DIMENSION", "ARC_DIMENSION"):
            continue
        for points in entity_polylines(entity, 0.01):
            coords = [(p.x, p.y) for p in points]
            if len(coords) >= 2:
                lines.append(LineString(coords))
                sources.append(entity)
    return lines, sources


def find_region(doc, seed) -> tuple[Polygon, list]:
    seed = Vec2.of(seed)
    lines, line_sources = _linework(doc)
    if not lines:
        raise ValueError("nao ha geometria fechada ao redor do ponto")
    polygons = list(polygonize(unary_union(lines)))
    point = Point(seed.x, seed.y)
    candidates = [poly for poly in polygons if poly.covers(point)]
    if not candidates:
        raise ValueError("nao foi encontrada uma regiao fechada nesse ponto")
    region = min(candidates, key=lambda p: p.area)
    boundary = region.boundary.buffer(1e-7)
    sources = []
    for line, entity in zip(lines, line_sources, strict=True):
        if line.intersects(boundary) and entity not in sources:
            sources.append(entity)
    return region, sources


def set_hatch_seed_boundary(doc, hatch, seed) -> None:
    seed = Vec2.of(seed)
    region, sources = find_region(doc, seed)
    hatch.remove_association()
    hatch.paths.clear()
    exterior = list(region.exterior.coords)[:-1]
    path = hatch.paths.add_polyline_path(exterior, is_closed=True, flags=BOUNDARY_PATH_EXTERNAL)
    if sources:
        hatch.associate(path, sources)
    for ring in region.interiors:
        hatch.paths.add_polyline_path(
            list(ring.coords)[:-1], is_closed=True, flags=BOUNDARY_PATH_OUTERMOST
        )
    hatch.dxf.associative = 1 if sources else 0
    _set_metadata(hatch, "seed", seed)


def apply_hatch_settings(hatch, settings: HatchSettings) -> None:
    if settings.solid:
        hatch.set_solid_fill(color=int(settings.color), style=int(settings.island_style))
    else:
        hatch.set_pattern_fill(
            settings.pattern.upper(),
            color=int(settings.color),
            angle=float(settings.angle),
            scale=max(float(settings.scale), 1e-9),
            style=int(settings.island_style),
            definition=settings.custom_definition,
        )
    hatch.dxf.color = int(settings.color)
    hatch.transparency = max(0.0, min(float(settings.transparency), 0.9))


def read_hatch_settings(hatch) -> HatchSettings:
    return HatchSettings(
        pattern=str(hatch.dxf.get("pattern_name", "SOLID")),
        scale=float(hatch.dxf.get("pattern_scale", 1.0) or 1.0),
        angle=float(hatch.dxf.get("pattern_angle", 0.0) or 0.0),
        color=int(hatch.dxf.get("color", 7) or 7),
        transparency=float(hatch.transparency or 0.0),
        island_style=int(hatch.dxf.get("hatch_style", 0) or 0),
    )


def hatch_area(hatch) -> float:
    """Area liquida mostrada nas propriedades, respeitando ilhas."""
    polygons: list[Polygon] = []
    for boundary in hatch.paths:
        try:
            path = from_hatch_boundary_path(boundary)
            points = [(p.x, p.y) for p in path.flattening(0.0005)]
        except (TypeError, ValueError):
            continue
        if len(points) >= 3:
            poly = Polygon(points)
            if poly.is_valid and poly.area > 1e-12:
                polygons.append(poly)
    style = int(hatch.dxf.get("hatch_style", 0) or 0)
    total = 0.0
    for poly in polygons:
        nesting = _depth(poly, polygons)
        if style == 2 and nesting > 0:
            continue
        if style == 1 and nesting > 1:
            continue
        total += poly.area if nesting % 2 == 0 else -poly.area
    return max(0.0, total)


def update_associative_hatch(doc, hatch) -> bool:
    metadata = hatch_metadata(hatch)
    try:
        if metadata.get("mode") == "seed":
            x, y = metadata["seed"]
            set_hatch_seed_boundary(doc, hatch, Vec2(float(x), float(y)))
        else:
            sources = [doc.entity_by_handle(h) for h in hatch_source_handles(hatch)]
            sources = [e for e in sources if e is not None and e.is_alive]
            if not sources:
                return False
            set_hatch_boundaries(hatch, sources)
        return True
    except (KeyError, TypeError, ValueError):
        return False


def detach_hatch(hatch) -> None:
    hatch.remove_association()
    hatch.discard_xdata(APPID)


def remap_copied_hatch(doc, original, clone, handle_map: dict[str, str], matrix=None) -> None:
    """Faz a copia depender das copias dos limites, quando elas existem."""
    metadata = hatch_metadata(original)
    if metadata.get("mode") == "seed":
        try:
            x, y = map(float, metadata["seed"])
            if matrix is not None:
                transformed = matrix.transform((x, y, 0.0))
                x, y = float(transformed.x), float(transformed.y)
            set_hatch_seed_boundary(doc, clone, Vec2(x, y))
        except (KeyError, TypeError, ValueError):
            pass
        return
    handles = [handle_map.get(h, h) for h in hatch_source_handles(original)]
    sources = [doc.entity_by_handle(handle) for handle in handles]
    sources = [entity for entity in sources if entity is not None and entity.is_alive]
    if sources:
        set_hatch_boundaries(clone, sources)
