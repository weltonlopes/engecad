"""Motor de snap (osnap).

A busca e sempre feita num raio em PIXELS convertido para mundo, para o snap
"pegar" com a mesma sensibilidade em qualquer zoom. Os candidatos vem do indice
espacial -- nunca varremos o desenho inteiro a cada movimento do mouse.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass

from ..core.entities import entity_polylines, entity_snap_points
from ..core.geometry import (
    Vec2,
    closest_point_on_segment,
    distance_to_segment,
    line_intersection,
)

# Prioridade: menor numero vence empate dentro do raio de captura.
PRIORITY = {
    "end": 0,
    "intersection": 1,
    "mid": 2,
    "center": 3,
    "quad": 4,
    "node": 5,
    "perp": 6,
    "nearest": 7,
    "grid": 8,
}

LABELS = {
    "end": "Extremidade",
    "mid": "Ponto medio",
    "center": "Centro",
    "quad": "Quadrante",
    "node": "No",
    "intersection": "Intersecao",
    "perp": "Perpendicular",
    "nearest": "Proximo",
    "grid": "Grade",
}

DEFAULT_ENABLED = {"end", "mid", "center", "quad", "node", "intersection", "nearest"}


@dataclass(frozen=True)
class SnapResult:
    point: Vec2
    kind: str
    source: object = None

    @property
    def label(self) -> str:
        return LABELS.get(self.kind, self.kind)


class SnapEngine:
    def __init__(self, doc, pixel_radius: float = 14.0):
        self.doc = doc
        self.pixel_radius = pixel_radius
        self.enabled: set[str] = set(DEFAULT_ENABLED)
        self.grid_step: float = 0.0  # 0 = sem snap em grade
        self.active = True

    def toggle(self, kind: str, on: bool | None = None) -> None:
        if on is None:
            on = kind not in self.enabled
        self.enabled.add(kind) if on else self.enabled.discard(kind)

    def snap(self, world: Vec2, viewport, exclude=()) -> SnapResult | None:
        """Melhor ponto de snap perto de `world`, ou None."""
        if not self.active or not self.enabled:
            return None
        radius = viewport.px_to_world(self.pixel_radius)
        if radius <= 0:
            return None

        entities = [
            e
            for e in self.doc.query_point(world, radius)
            if e.is_alive
            and e not in exclude
            and self.doc.layer_is_visible(e.dxf.get("layer", "0"))
        ]

        best: tuple[int, float, SnapResult] | None = None

        def consider(kind: str, p: Vec2, source=None) -> None:
            nonlocal best
            if kind not in self.enabled:
                return
            d = world.distance_to(p)
            if d > radius:
                return
            key = (PRIORITY.get(kind, 99), d)
            if best is None or key < (best[0], best[1]):
                best = (key[0], key[1], SnapResult(p, kind, source))

        for e in entities:
            for kind, p in entity_snap_points(e):
                consider(kind, p, e)

        # Intersecao: so entre segmentos que passam perto do cursor.
        if "intersection" in self.enabled and len(entities) > 1:
            segs = []
            for e in entities:
                for poly in entity_polylines(e, sagitta=radius * 0.05):
                    for i in range(len(poly) - 1):
                        a, b = poly[i], poly[i + 1]
                        # distancia ate o SEGMENTO, nao ate os extremos: uma linha
                        # longa passa sob o cursor com os extremos a dezenas de metros.
                        if distance_to_segment(world, a, b) < radius * 3:
                            segs.append((a, b, e))
            for (a1, a2, e1), (b1, b2, e2) in itertools.combinations(segs[:80], 2):
                if e1 is e2:
                    continue
                ip = line_intersection(a1, a2, b1, b2, as_segments=True)
                if ip is not None:
                    consider("intersection", ip, (e1, e2))

        # Nearest: ponto mais proximo sobre a geometria.
        if "nearest" in self.enabled:
            for e in entities:
                for poly in entity_polylines(e, sagitta=radius * 0.05):
                    for i in range(len(poly) - 1):
                        p = closest_point_on_segment(world, poly[i], poly[i + 1])
                        consider("nearest", p, e)

        if best is None and self.grid_step > 0 and "grid" in self.enabled:
            g = self.grid_step
            gp = Vec2(round(world.x / g) * g, round(world.y / g) * g)
            if world.distance_to(gp) <= radius:
                return SnapResult(gp, "grid")

        return best[2] if best else None
