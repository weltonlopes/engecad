"""Teste de acerto: descobrir que entidade esta sob o cursor ou dentro de uma janela.

Como no AutoCAD, a janela tem dois sentidos:
  - arrastada da ESQUERDA para a direita = janela  -> pega so o que estiver
    inteiramente dentro;
  - arrastada da DIREITA para a esquerda = captura -> pega tudo que encostar.
"""

from __future__ import annotations

from .entities import POINT_LIKE, entity_bbox, entity_insert_point, entity_polylines
from .geometry import BBox, Vec2, distance_to_segment, line_intersection


def entity_distance(entity, p: Vec2, sagitta: float = 0.01) -> float:
    """Menor distancia de p ate a geometria da entidade."""
    if entity.dxftype() in POINT_LIKE:
        ins = entity_insert_point(entity)
        return p.distance_to(ins) if ins else float("inf")
    best = float("inf")
    for poly in entity_polylines(entity, sagitta):
        for i in range(len(poly) - 1):
            d = distance_to_segment(p, poly[i], poly[i + 1])
            if d < best:
                best = d
    return best


def _visible(doc, entity) -> bool:
    layer = entity.dxf.get("layer", "0")
    return doc.layer_is_visible(layer) and not doc.layer_is_locked(layer)


def pick_at(doc, p: Vec2, tol: float, exclude=()):
    """Entidade mais proxima de p dentro do raio tol, ou None."""
    best, best_d = None, float("inf")
    for e in doc.query_point(p, tol):
        if not e.is_alive or e in exclude or not _visible(doc, e):
            continue
        d = entity_distance(e, p, sagitta=tol * 0.1)
        if d <= tol and d < best_d:
            best, best_d = e, d
    return best


def pick_all_at(doc, p: Vec2, tol: float) -> list:
    """Todas as entidades sob o cursor, da mais proxima para a mais distante."""
    hits = []
    for e in doc.query_point(p, tol):
        if not e.is_alive or not _visible(doc, e):
            continue
        d = entity_distance(e, p, sagitta=tol * 0.1)
        if d <= tol:
            hits.append((d, e))
    hits.sort(key=lambda t: t[0])
    return [e for _, e in hits]


def _segment_crosses_box(a: Vec2, b: Vec2, box: BBox) -> bool:
    corners = [
        Vec2(box.minx, box.miny),
        Vec2(box.maxx, box.miny),
        Vec2(box.maxx, box.maxy),
        Vec2(box.minx, box.maxy),
    ]
    for i in range(4):
        if line_intersection(a, b, corners[i], corners[(i + 1) % 4], as_segments=True):
            return True
    return False


def entity_crosses(entity, box: BBox, sagitta: float) -> bool:
    """A geometria encosta na janela (vertice dentro ou segmento cruzando)?"""
    if entity.dxftype() in POINT_LIKE:
        ins = entity_insert_point(entity)
        return bool(ins and box.contains(ins))
    for poly in entity_polylines(entity, sagitta):
        for i, pt in enumerate(poly):
            if box.contains(pt):
                return True
            if i + 1 < len(poly) and _segment_crosses_box(pt, poly[i + 1], box):
                return True
    return False


def select_in_box(doc, box: BBox, crossing: bool, sagitta: float = 0.01) -> list:
    """Entidades dentro da janela (crossing=False) ou que a tocam (crossing=True)."""
    if box.is_empty:
        return []
    out = []
    for e in doc.query(box):
        if not e.is_alive or not _visible(doc, e):
            continue
        eb = entity_bbox(e)
        if eb.is_empty:
            continue
        if not crossing:
            # janela: precisa estar inteiramente contida
            if (
                eb.minx >= box.minx
                and eb.maxx <= box.maxx
                and eb.miny >= box.miny
                and eb.maxy <= box.maxy
            ):
                out.append(e)
        else:
            if entity_crosses(e, box, sagitta):
                out.append(e)
    return out
