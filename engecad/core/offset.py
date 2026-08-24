"""Offset (paralela) de geometria.

Para polilinhas usamos junta em esquadria (miter): cada segmento e deslocado e
os vertices novos saem da intersecao das retas deslocadas vizinhas. E o que o
CAD faz e o que o desenhista espera -- a polilinha resultante mantem a mesma
contagem de vertices e continua correspondendo, vertice a vertice, a original.
"""

from __future__ import annotations

from .geometry import (
    EPS,
    Vec2,
    closest_point_on_segment,
    line_intersection,
    normal_left,
)

# Se a esquadria esticar mais que isto x a distancia, corta reto (canto agudo demais).
MITER_LIMIT = 10.0


def offset_points(pts: list[Vec2], distance: float, closed: bool = False) -> list[Vec2] | None:
    """Desloca a polilinha em `distance` (positivo = para a esquerda do trajeto)."""
    pts = _dedupe(pts)
    n = len(pts)
    if n < 2 or abs(distance) < EPS:
        return None

    segs = []
    count = n if closed else n - 1
    for i in range(count):
        a, b = pts[i], pts[(i + 1) % n]
        d = b - a
        if d.length < EPS:
            continue
        off = normal_left(d) * distance
        segs.append((a + off, b + off))
    if not segs:
        return None

    out: list[Vec2] = []
    m = len(segs)
    if not closed:
        out.append(segs[0][0])
    for i in range(m if closed else m - 1):
        cur = segs[i]
        nxt = segs[(i + 1) % m]
        ip = line_intersection(cur[0], cur[1], nxt[0], nxt[1], as_segments=False)
        corner = pts[(i + 1) % n]
        if ip is None or ip.distance_to(corner) > abs(distance) * MITER_LIMIT:
            # segmentos paralelos ou canto agudo demais: corta reto
            out.append(cur[1])
            out.append(nxt[0])
        else:
            out.append(ip)
    if not closed:
        out.append(segs[-1][1])
    return _dedupe(out)


def side_of_polyline(pts: list[Vec2], through: Vec2, closed: bool = False) -> float:
    """+1 se `through` esta a esquerda do trajeto, -1 se a direita."""
    best_d, best = float("inf"), None
    count = len(pts) if closed else len(pts) - 1
    for i in range(count):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        q = closest_point_on_segment(through, a, b)
        d = through.distance_to(q)
        if d < best_d:
            best_d, best = d, (a, b)
    if best is None:
        return 1.0
    a, b = best
    cross = (b - a).cross(through - a)
    return 1.0 if cross >= 0 else -1.0


def offset_distance_through(pts: list[Vec2], through: Vec2, closed: bool = False) -> float:
    """Distancia com sinal para a paralela passar pelo ponto indicado."""
    best_d = float("inf")
    count = len(pts) if closed else len(pts) - 1
    for i in range(count):
        a, b = pts[i], pts[(i + 1) % len(pts)]
        q = closest_point_on_segment(through, a, b)
        best_d = min(best_d, through.distance_to(q))
    return best_d * side_of_polyline(pts, through, closed)


def offset_circle(center: Vec2, radius: float, distance: float, through: Vec2 | None = None):
    """Novo raio do circulo/arco deslocado. None se o resultado degenerar."""
    if through is not None:
        outward = center.distance_to(through) > radius
        distance = abs(distance) if outward else -abs(distance)
    r = radius + distance
    return r if r > EPS else None


def _dedupe(pts: list[Vec2]) -> list[Vec2]:
    out: list[Vec2] = []
    for p in pts:
        if not out or out[-1].distance_to(p) > EPS:
            out.append(p)
    return out


def offset_entity(
    entity,
    distance: float,
    through: Vec2 | None = None,
    side_point: Vec2 | None = None,
) -> dict | None:
    """Descreve a paralela da entidade, sem criar nada no desenho.

    Tres modos, nesta ordem de precedencia:
      through    -- a paralela passa exatamente por este ponto;
      side_point -- usa `distance`, e o ponto so decide de que lado;
      nenhum     -- usa `distance` com sinal (positivo = esquerda do trajeto).

    Devolve None se o tipo nao suportar paralela.
    """
    t = entity.dxftype()
    dxf = entity.dxf

    if t == "LINE":
        a = Vec2(dxf.start.x, dxf.start.y)
        b = Vec2(dxf.end.x, dxf.end.y)
        d = _resolve_distance([a, b], distance, through, side_point, False)
        pts = offset_points([a, b], d)
        return {"type": "LINE", "points": pts} if pts else None

    if t == "LWPOLYLINE":
        pts = [Vec2(p[0], p[1]) for p in entity.get_points("xy")]
        closed = bool(entity.closed)
        d = _resolve_distance(pts, distance, through, side_point, closed)
        out = offset_points(pts, d, closed)
        return {"type": "LWPOLYLINE", "points": out, "closed": closed} if out else None

    if t == "CIRCLE":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = _resolve_radius(c, float(dxf.radius), distance, through, side_point)
        return {"type": "CIRCLE", "center": c, "radius": r} if r else None

    if t == "ARC":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = _resolve_radius(c, float(dxf.radius), distance, through, side_point)
        if not r:
            return None
        return {
            "type": "ARC",
            "center": c,
            "radius": r,
            "start_angle": float(dxf.start_angle),
            "end_angle": float(dxf.end_angle),
        }

    return None


def _resolve_distance(pts, distance, through, side_point, closed) -> float:
    if through is not None:
        return offset_distance_through(pts, through, closed)
    if side_point is not None:
        return abs(distance) * side_of_polyline(pts, side_point, closed)
    return distance


def _resolve_radius(center: Vec2, radius: float, distance, through, side_point):
    ref = through if through is not None else side_point
    if through is not None:
        # a paralela tem de passar pelo ponto
        r = center.distance_to(through)
        return r if r > EPS else None
    return offset_circle(center, radius, distance, ref)


def create_offset(
    doc,
    entity,
    distance: float,
    through: Vec2 | None = None,
    layer=None,
    side_point: Vec2 | None = None,
):
    """Cria no documento a paralela da entidade. Devolve a nova entidade ou None."""
    spec = offset_entity(entity, distance, through, side_point)
    if spec is None:
        return None
    layer = layer or entity.dxf.get("layer", None)
    kind = spec["type"]
    if kind == "LINE":
        p = spec["points"]
        return doc.add_line(p[0], p[-1], layer=layer)
    if kind == "LWPOLYLINE":
        return doc.add_lwpolyline(spec["points"], closed=spec["closed"], layer=layer)
    if kind == "CIRCLE":
        return doc.add_circle(spec["center"], spec["radius"], layer=layer)
    if kind == "ARC":
        return doc.add_arc(
            spec["center"], spec["radius"], spec["start_angle"], spec["end_angle"], layer=layer
        )
    return None
