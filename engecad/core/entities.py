"""Adaptador entre entidades DXF do ezdxf e a geometria que desenhamos/snapamos.

Toda entidade vira uma lista de polilinhas achatadas. Assim o renderizador e o
motor de snap nao precisam saber o que e um bulge, uma spline ou uma elipse --
o ezdxf.path faz o achatamento, e com tolerancia dependente do zoom para a
curva ficar lisa quando o usuario aproxima.
"""

from __future__ import annotations

import math

from ezdxf import bbox as ezbbox
from ezdxf.path import from_hatch_boundary_path, make_path

from .dimensions import DIMENSION_TYPES, dimension_primitives
from .geometry import BBox, Vec2

# Entidades que viram texto/marcador em vez de polilinha.
POINT_LIKE = {"POINT", "TEXT", "MTEXT", "INSERT", "ATTDEF"}


def entity_polylines(entity, sagitta: float = 0.01) -> list[list[Vec2]]:
    """Polilinhas achatadas da entidade, em coordenadas do mundo.

    sagitta = erro maximo tolerado no achatamento de curvas, em unidades do
    mundo. O canvas passa o equivalente a ~0.3 px, entao a curva e lisa em
    qualquer zoom sem gerar vertices demais quando esta longe.
    """
    dxftype = entity.dxftype()
    if dxftype == "HATCH":
        out: list[list[Vec2]] = []
        for boundary in entity.paths:
            try:
                path = from_hatch_boundary_path(boundary)
                pts = [Vec2(v.x, v.y) for v in path.flattening(max(sagitta, 1e-9))]
            except (TypeError, ValueError):
                continue
            if len(pts) >= 2:
                if pts[0].distance_to(pts[-1]) > 1e-9:
                    pts.append(pts[0])
                out.append(pts)
        return out
    if dxftype in DIMENSION_TYPES:
        out: list[list[Vec2]] = []
        for primitive in dimension_primitives(entity):
            if primitive.dxftype() not in POINT_LIKE:
                out.extend(entity_polylines(primitive, sagitta))
        return out
    if dxftype in POINT_LIKE:
        return []
    try:
        path = make_path(entity)
    except (TypeError, ValueError):
        return []
    pts = [Vec2(v.x, v.y) for v in path.flattening(max(sagitta, 1e-9))]
    if len(pts) < 2:
        return []
    if path.is_closed and pts[0].distance_to(pts[-1]) > 1e-9:
        pts.append(pts[0])
    return [pts]


def entity_insert_point(entity) -> Vec2 | None:
    """Ponto de insercao de entidades pontuais (texto, bloco, ponto)."""
    dxf = entity.dxf
    for attr in ("insert", "location", "align_point"):
        if dxf.hasattr(attr):
            p = dxf.get(attr)
            return Vec2(p.x, p.y)
    return None


def entity_bbox(entity) -> BBox:
    """Bbox da entidade. Usa o extrator do ezdxf, que cobre texto e blocos."""
    try:
        ext = ezbbox.extents([entity], fast=True)
        if ext.has_data:
            return BBox(ext.extmin.x, ext.extmin.y, ext.extmax.x, ext.extmax.y)
    except Exception:
        pass
    p = entity_insert_point(entity)
    return BBox(p.x, p.y, p.x, p.y) if p else BBox()


def entity_snap_points(entity) -> list[tuple[str, Vec2]]:
    """Pontos notaveis para snap: (tipo, ponto)."""
    t = entity.dxftype()
    dxf = entity.dxf
    out: list[tuple[str, Vec2]] = []

    if t in DIMENSION_TYPES:
        for attr in ("defpoint", "defpoint2", "defpoint3", "defpoint4", "defpoint5"):
            if dxf.hasattr(attr):
                p = dxf.get(attr)
                out.append(("end", Vec2(p.x, p.y)))
        if dxf.hasattr("text_midpoint"):
            p = dxf.text_midpoint
            out.append(("mid", Vec2(p.x, p.y)))
        return out

    if t == "LINE":
        a, b = Vec2(dxf.start.x, dxf.start.y), Vec2(dxf.end.x, dxf.end.y)
        out.append(("end", a))
        out.append(("end", b))
        out.append(("mid", (a + b) * 0.5))
        return out

    if t == "CIRCLE":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        out.append(("center", c))
        for ang in (0.0, math.pi / 2, math.pi, 3 * math.pi / 2):
            out.append(("quad", Vec2.polar(c, ang, r)))
        return out

    if t == "ARC":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        a0 = math.radians(dxf.start_angle)
        a1 = math.radians(dxf.end_angle)
        if a1 < a0:
            a1 += math.tau
        out.append(("center", c))
        out.append(("end", Vec2.polar(c, a0, r)))
        out.append(("end", Vec2.polar(c, a1, r)))
        out.append(("mid", Vec2.polar(c, (a0 + a1) / 2, r)))
        return out

    if t in POINT_LIKE:
        p = entity_insert_point(entity)
        if p:
            out.append(("node", p))
        return out

    # LWPOLYLINE, POLYLINE, SPLINE, ELLIPSE...: vertices + meios dos segmentos.
    for poly in entity_polylines(entity, sagitta=0.001):
        for i, p in enumerate(poly):
            out.append(("end", p))
            if i + 1 < len(poly):
                out.append(("mid", (p + poly[i + 1]) * 0.5))
    return out


def entity_summary(entity) -> str:
    """Descricao curta para barra de status e listagens."""
    t = entity.dxftype()
    layer = entity.dxf.get("layer", "0")
    return f"{t} na camada {layer}"
