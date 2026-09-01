"""Teste de acerto: descobrir que entidade esta sob o cursor ou dentro de uma janela.

Como no AutoCAD, a janela tem dois sentidos:
  - arrastada da ESQUERDA para a direita = janela  -> pega so o que estiver
    inteiramente dentro;
  - arrastada da DIREITA para a esquerda = captura -> pega tudo que encostar.
"""

from __future__ import annotations

import heapq
from dataclasses import dataclass

from .entities import (
    POINT_LIKE,
    closest_on_segments,
    entity_bbox,
    entity_insert_point,
    entity_polylines,
    entity_segments,
)
from .geometry import BBox, Vec2, line_intersection

MAX_PROBE_CANDIDATES = 512
MAX_PROBE_SCAN = 1_024
MIN_PROBE_FRACTION = 8.0 / 14.0  # cobre integralmente o pickbox dentro do snap


def entity_distance(entity, p: Vec2, sagitta: float = 0.01) -> float:
    """Menor distancia de p ate a geometria da entidade."""
    if entity.dxftype() == "HATCH":
        from shapely.geometry import Point, Polygon

        inside = False
        point = Point(p.x, p.y)
        for polyline in entity_polylines(entity, sagitta):
            if len(polyline) >= 3:
                polygon = Polygon([(v.x, v.y) for v in polyline])
                if polygon.is_valid and polygon.covers(point):
                    inside = not inside
        if inside:
            return 0.0
    if entity.dxftype() in POINT_LIKE:
        ins = entity_insert_point(entity)
        return p.distance_to(ins) if ins else float("inf")
    segs = entity_segments(entity, sagitta)
    if segs is None:
        return float("inf")
    distances, _, _ = closest_on_segments(segs, p.x, p.y)
    return float(distances.min())


def _visible(doc, entity) -> bool:
    layer = entity.dxf.get("layer", "0")
    return doc.layer_is_visible(layer) and not doc.layer_is_locked(layer)


def _box_distance(box: BBox, p: Vec2) -> float:
    """Distancia de p ate o retangulo -- um piso barato para a distancia real."""
    dx = max(box.minx - p.x, 0.0, p.x - box.maxx)
    dy = max(box.miny - p.y, 0.0, p.y - box.maxy)
    return (dx * dx + dy * dy) ** 0.5


@dataclass(frozen=True, slots=True)
class PointerProbe:
    """Candidatos de uma unica consulta do ponteiro, ordenados pelo bbox."""

    point: Vec2
    radius: float
    ranked: tuple[tuple[float, object], ...]
    truncated: bool = False


def probe_at(doc, p: Vec2, radius: float, exclude=()) -> PointerProbe:
    """Consulta compartilhada por snap e hover.

    Camadas travadas continuam aqui porque participam do snap; ``pick_at`` as
    elimina ao consumir o probe. A ordenacao inclui o handle para que empates de
    bboxes grandes nao dependam da ordem de um ``set``.
    """
    boxes = doc.index._boxes

    def key(handle):
        box = boxes.get(handle)
        return (_box_distance(box, p) if box is not None else 0.0, str(handle))

    excluded_handles = {
        str(handle)
        for entity in exclude
        if (handle := entity.dxf.get("handle")) is not None
    }
    query = BBox(p.x - radius, p.y - radius, p.x + radius, p.y + radius)
    handles = doc.index.query(query)
    truncated = False
    if len(handles) > MAX_PROBE_SCAN:
        # Em zoom extremamente aberto o raio do snap pode conter dezenas de
        # milhares de objetos. A regiao interna ainda cobre todo o raio de hover
        # (8 px) e fornece os candidatos espacialmente mais relevantes.
        inner = radius * MIN_PROBE_FRACTION
        inner_query = BBox(p.x - inner, p.y - inner, p.x + inner, p.y + inner)
        nearby = doc.index.query(inner_query)
        if len(nearby) >= 64:
            handles = nearby
        if len(handles) > MAX_PROBE_SCAN:
            handles = set(heapq.nsmallest(MAX_PROBE_SCAN, handles, key=key))
        truncated = True

    # O probe alimenta snap e hover, ambos sob orçamento. Ordenar 28 mil
    # entidades para consumir 64 custava dezenas de milissegundos; um heap
    # mantem somente a vizinhanca que realmente pode ser refinada.
    nearest = heapq.nsmallest(MAX_PROBE_CANDIDATES + 1, handles, key=key)
    heap_truncated = len(nearest) > MAX_PROBE_CANDIDATES
    if heap_truncated:
        nearest.pop()
    ranked = []
    for handle in nearest:
        if str(handle) in excluded_handles:
            continue
        entity = doc.entity_by_handle(handle)
        if entity is None or not entity.is_alive:
            continue
        if not doc.layer_is_visible(entity.dxf.get("layer", "0")):
            continue
        ranked.append((key(handle)[0], entity))
    ranked.sort(key=lambda item: (item[0], str(item[1].dxf.get("handle", ""))))
    return PointerProbe(p, radius, tuple(ranked), truncated or heap_truncated)


def pick_at(doc, p: Vec2, tol: float, exclude=(), probe: PointerProbe | None = None):
    """Entidade mais proxima de p dentro do raio tol, ou None.

    Roda a cada movimento do mouse. Com o zoom aberto o raio de captura vale
    dezenas de metros e o indice devolve dezenas de candidatos; medir a geometria
    de todos custava mais que o resto do movimento somado.

    A bbox da entidade da um piso para a distancia real, e ela ja esta no indice.
    Ordenando por esse piso, o primeiro acerto costuma ser o vencedor e o corte
    dispensa o resto sem tocar na geometria.
    """
    if probe is not None and probe.point == p and probe.radius >= tol:
        candidates = probe.ranked
    else:
        candidates = probe_at(doc, p, tol, exclude).ranked
    excluded = set(exclude)

    best, best_d = None, float("inf")
    sagitta = tol * 0.1
    for floor, e in candidates:
        if floor > tol or floor >= best_d:
            break  # dai em diante nenhuma pode ganhar
        if not e.is_alive or e in excluded or not _visible(doc, e):
            continue
        d = entity_distance(e, p, sagitta)
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
