"""Motor de snap (osnap).

A busca e sempre feita num raio em PIXELS convertido para mundo, para o snap
"pegar" com a mesma sensibilidade em qualquer zoom. Os candidatos vem do indice
espacial -- nunca varremos o desenho inteiro a cada movimento do mouse.
"""

from __future__ import annotations

import itertools
from collections import OrderedDict
from dataclasses import dataclass

import numpy as np

from ..core.entities import closest_on_segments, entity_segments, entity_snap_points
from ..core.geometry import Vec2, line_intersection
from ..core.picking import PointerProbe, probe_at

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

# Orcamento: o snap roda a cada movimento do mouse, entao ele nao pode escalar
# com a densidade do desenho. Com o zoom aberto o raio de 14 px vale centenas de
# metros e o indice devolve milhares de candidatos -- media de 35 ms por
# movimento num desenho de 200 mil. Ficamos com os mais proximos do cursor, e as
# buscas caras (nearest e intersecao percorrem toda a geometria achatada) so
# rodam quando ha poucos candidatos.
MAX_CANDIDATES = 64
MAX_FOR_GEOMETRY = 16
MAX_CROSS_SEGMENTS = 60  # pares de segmentos para intersecao crescem ao quadrado
MAX_SNAP_POINTS = 160_000  # trabalho vetorizado maximo por movimento
MAX_GEOMETRY_POINTS = 24_000
MAX_CACHED_SNAP_POINTS = 800_000

_POINT_KINDS = ("end", "mid", "center", "quad", "node")
_KIND_CODE = {kind: i for i, kind in enumerate(_POINT_KINDS)}


@dataclass(frozen=True, slots=True)
class _SnapCloud:
    codes: np.ndarray
    xs: np.ndarray
    ys: np.ndarray

    @property
    def size(self) -> int:
        return int(self.xs.size)


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
        self._points: OrderedDict[str, _SnapCloud] = OrderedDict()
        self._point_count = 0
        self._points_revision = -1

    def toggle(self, kind: str, on: bool | None = None) -> None:
        if on is None:
            on = kind not in self.enabled
        self.enabled.add(kind) if on else self.enabled.discard(kind)

    def _snap_cloud(self, entity) -> _SnapCloud:
        """Pontos notaveis em arrays compactos, com LRU por quantidade de pontos."""
        revision = self.doc.geometry_revision
        if revision != self._points_revision:
            self._points.clear()
            self._point_count = 0
            self._points_revision = revision
        handle = entity.dxf.get("handle")
        if handle is None:
            points = entity_snap_points(entity)
            return self._cloud_of(points)
        hit = self._points.get(handle)
        if hit is None:
            hit = self._cloud_of(entity_snap_points(entity))
            self._points[handle] = hit
            self._point_count += hit.size
            while self._point_count > MAX_CACHED_SNAP_POINTS and len(self._points) > 1:
                _, old = self._points.popitem(last=False)
                self._point_count -= old.size
        else:
            self._points.move_to_end(handle)
        return hit

    @staticmethod
    def _cloud_of(points: list[tuple[str, Vec2]]) -> _SnapCloud:
        if not points:
            empty = np.empty(0, dtype=np.float64)
            return _SnapCloud(np.empty(0, dtype=np.uint8), empty, empty.copy())
        keep = [(kind, point) for kind, point in points if kind in _KIND_CODE]
        return _SnapCloud(
            np.fromiter((_KIND_CODE[kind] for kind, _ in keep), dtype=np.uint8, count=len(keep)),
            np.fromiter((point.x for _, point in keep), dtype=np.float64, count=len(keep)),
            np.fromiter((point.y for _, point in keep), dtype=np.float64, count=len(keep)),
        )

    def _candidates(
        self,
        world: Vec2,
        radius: float,
        exclude,
        probe: PointerProbe | None = None,
    ) -> list:
        """(piso da distancia, entidade), das mais proximas para as mais longe."""
        if probe is None or probe.point != world or probe.radius < radius:
            probe = probe_at(self.doc, world, radius, exclude)
        return list(probe.ranked[:MAX_CANDIDATES])

    def snap(
        self,
        world: Vec2,
        viewport,
        exclude=(),
        probe: PointerProbe | None = None,
    ) -> SnapResult | None:
        """Melhor ponto de snap perto de `world`, ou None."""
        if not self.active or not self.enabled:
            return None
        radius = viewport.px_to_world(self.pixel_radius)
        if radius <= 0:
            return None

        ranked = self._candidates(world, radius, exclude, probe)

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

        enabled_codes = [(_KIND_CODE[kind], kind) for kind in _POINT_KINDS if kind in self.enabled]
        top = PRIORITY["end"]
        point_count = 0
        geometry_entities = []
        for floor, e in ranked:
            # Ja temos uma extremidade mais perto que o piso deste candidato:
            # nada dele pode ganhar, nem em prioridade nem em distancia.
            if best is not None and best[0] == top and floor > best[1]:
                break
            cloud = self._snap_cloud(e)
            if cloud.size:
                # Distancias de dezenas de milhares de vertices saem em C/NumPy,
                # em vez de uma chamada Python de ``consider`` por ponto.
                dx = cloud.xs - world.x
                dy = cloud.ys - world.y
                d2 = dx * dx + dy * dy
                inside = d2 <= radius * radius
                if inside.any():
                    for code, kind in enabled_codes:
                        candidates = np.flatnonzero(inside & (cloud.codes == code))
                        if candidates.size:
                            at = int(candidates[int(np.argmin(d2[candidates]))])
                            consider(kind, Vec2(float(cloud.xs[at]), float(cloud.ys[at])), e)
            point_count += cloud.size
            geometry_entities.append(e)
            if point_count >= MAX_SNAP_POINTS:
                break

        entities = geometry_entities
        # Nearest e intersecao percorrem a geometria achatada de cada candidato:
        # so valem a pena quando o cursor esta sobre poucas entidades.
        geometry_ok = len(entities) <= MAX_FOR_GEOMETRY and point_count <= MAX_GEOMETRY_POINTS

        want_near = geometry_ok and "nearest" in self.enabled
        want_cross = geometry_ok and "intersection" in self.enabled and len(entities) > 1
        if want_near or want_cross:
            sagitta = radius * 0.05
            near_segments = []
            for e in entities:
                segs = entity_segments(e, sagitta)
                if segs is None:
                    continue
                distances, px, py = closest_on_segments(segs, world.x, world.y)
                if want_near:
                    i = int(np.argmin(distances))
                    consider("nearest", Vec2(float(px[i]), float(py[i])), e)
                if want_cross:
                    # So os segmentos que passam perto do cursor entram no
                    # cruzamento par a par -- a distancia e ate o SEGMENTO, nao
                    # ate os extremos: uma linha longa passa sob o cursor com os
                    # extremos a dezenas de metros.
                    ax, ay, dx, dy = segs
                    for i in np.flatnonzero(distances < radius * 3).tolist():
                        near_segments.append(
                            (
                                Vec2(float(ax[i]), float(ay[i])),
                                Vec2(float(ax[i] + dx[i]), float(ay[i] + dy[i])),
                                e,
                            )
                        )
                        if len(near_segments) >= MAX_CROSS_SEGMENTS:
                            break

            for (a1, a2, e1), (b1, b2, e2) in itertools.combinations(near_segments, 2):
                if e1 is e2:
                    continue
                ip = line_intersection(a1, a2, b1, b2, as_segments=True)
                if ip is not None:
                    consider("intersection", ip, (e1, e2))

        if best is None and self.grid_step > 0 and "grid" in self.enabled:
            g = self.grid_step
            gp = Vec2(round(world.x / g) * g, round(world.y / g) * g)
            if world.distance_to(gp) <= radius:
                return SnapResult(gp, "grid")

        return best[2] if best else None
