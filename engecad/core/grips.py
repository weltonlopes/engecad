"""Grips: os quadradinhos que aparecem na entidade selecionada e permitem
editar a geometria arrastando, sem precisar de comando nenhum.

Cada grip sabe o que faz quando arrastado -- mover a entidade inteira, mover um
vertice, mudar um raio ou um angulo. A alteracao e feita direto na entidade; o
desfazer vem de fora, pelo doc.editing() (ver core/snapshot.py).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ezdxf.math import Matrix44

from .geometry import Vec2

# O que o grip faz ao ser arrastado.
MOVE = "move"  # translada a entidade inteira
VERTEX = "vertex"  # move um vertice/extremidade
RADIUS = "radius"  # muda o raio
ANGLE = "angle"  # muda um angulo de arco


@dataclass(frozen=True)
class Grip:
    entity: object
    point: Vec2
    kind: str
    index: int = 0

    @property
    def moves_whole(self) -> bool:
        return self.kind == MOVE


def entity_grips(entity) -> list[Grip]:
    """Grips da entidade, na ordem em que aparecem."""
    if not entity.is_alive:
        return []
    t = entity.dxftype()
    dxf = entity.dxf

    if t == "LINE":
        a = Vec2(dxf.start.x, dxf.start.y)
        b = Vec2(dxf.end.x, dxf.end.y)
        return [
            Grip(entity, a, VERTEX, 0),
            Grip(entity, b, VERTEX, 1),
            Grip(entity, (a + b) * 0.5, MOVE, 2),
        ]

    if t == "LWPOLYLINE":
        return [
            Grip(entity, Vec2(p[0], p[1]), VERTEX, i)
            for i, p in enumerate(entity.get_points("xyseb"))
        ]

    if t == "CIRCLE":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        grips = [Grip(entity, c, MOVE, 0)]
        for i, ang in enumerate((0.0, math.pi / 2, math.pi, 3 * math.pi / 2)):
            grips.append(Grip(entity, Vec2.polar(c, ang, r), RADIUS, i))
        return grips

    if t == "ARC":
        c = Vec2(dxf.center.x, dxf.center.y)
        r = float(dxf.radius)
        a0 = math.radians(dxf.start_angle)
        a1 = math.radians(dxf.end_angle)
        if a1 < a0:
            a1 += math.tau
        return [
            Grip(entity, c, MOVE, 0),
            Grip(entity, Vec2.polar(c, a0, r), ANGLE, 0),
            Grip(entity, Vec2.polar(c, a1, r), ANGLE, 1),
            Grip(entity, Vec2.polar(c, (a0 + a1) / 2, r), RADIUS, 0),
        ]

    if t in ("TEXT", "MTEXT", "POINT", "INSERT"):
        from .entities import entity_insert_point

        p = entity_insert_point(entity)
        return [Grip(entity, p, MOVE, 0)] if p else []

    return []


def drag_grip(entity, grip: Grip, target: Vec2) -> bool:
    """Aplica o arraste do grip ate `target`. True se alterou a geometria."""
    if not entity.is_alive:
        return False
    t = entity.dxftype()
    dxf = entity.dxf

    if grip.kind == MOVE:
        d = target - grip.point
        if d.length == 0:
            return False
        entity.transform(Matrix44.translate(d.x, d.y, 0))
        return True

    if grip.kind == VERTEX:
        if t == "LINE":
            if grip.index == 0:
                dxf.start = (target.x, target.y, 0.0)
            else:
                dxf.end = (target.x, target.y, 0.0)
            return True
        if t == "LWPOLYLINE":
            pts = list(entity.get_points("xyseb"))
            if not 0 <= grip.index < len(pts):
                return False
            old = pts[grip.index]
            # preserva start_width, end_width e bulge do vertice
            pts[grip.index] = (target.x, target.y, old[2], old[3], old[4])
            entity.set_points(pts, format="xyseb")
            return True
        return False

    if grip.kind == RADIUS and t in ("CIRCLE", "ARC"):
        c = Vec2(dxf.center.x, dxf.center.y)
        r = c.distance_to(target)
        if r <= 1e-9:
            return False
        dxf.radius = r
        return True

    if grip.kind == ANGLE and t == "ARC":
        c = Vec2(dxf.center.x, dxf.center.y)
        d = target - c
        if d.length <= 1e-9:
            return False
        ang = math.degrees(d.angle) % 360.0
        if grip.index == 0:
            dxf.start_angle = ang
        else:
            dxf.end_angle = ang
        return True

    return False


def nearest_grip(grips: list[Grip], p: Vec2, tol: float) -> Grip | None:
    best, best_d = None, tol
    for g in grips:
        d = p.distance_to(g.point)
        if d <= best_d:
            best, best_d = g, d
    return best
