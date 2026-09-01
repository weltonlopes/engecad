"""Ferramentas interativas de cotagem compativeis com DIMENSION do DXF."""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF
from PySide6.QtGui import QPolygonF

from ..core.dimensions import dimension_measurement
from ..core.geometry import Vec2
from ..core.picking import pick_at
from ..render.styles import DARK
from .base import PointCollectorTool, Tool
from .draw import _pen


def _line(painter, vp, a: Vec2, b: Vec2) -> None:
    painter.drawLine(QPointF(*vp.world_to_screen(a)), QPointF(*vp.world_to_screen(b)))


def _label(painter, vp, p: Vec2, text: str) -> None:
    x, y = vp.world_to_screen(p)
    painter.drawText(QPointF(x + 10, y - 8), text)


class LinearDimensionTool(PointCollectorTool):
    name = "DIMLINEAR"
    min_points = 3
    max_points = 3

    def __init__(self, ctx, angle: float | None = None, ask_angle: bool = False):
        super().__init__(ctx)
        self.angle = angle
        self.ask_angle = ask_angle

    def activate(self) -> None:
        self.update_prompt()

    def update_prompt(self) -> None:
        if self.ask_angle:
            self.set_prompt("Angulo da linha de cota (graus):")
        elif not self.points:
            self.set_prompt("Primeira origem da linha de chamada:")
        elif len(self.points) == 1:
            self.set_prompt("Segunda origem da linha de chamada:")
        else:
            mode = "automatica" if self.angle is None else f"{self.angle:g} graus"
            self.set_prompt(f"Posicao da linha de cota  [orientacao {mode}]:")

    def on_click(self, world: Vec2, event=None) -> None:
        if self.ask_angle:
            self.ctx.message("Informe primeiro o angulo pela linha de comando")
            return
        super().on_click(world, event)

    def on_text(self, text: str) -> bool:
        if self.ask_angle:
            try:
                self.angle = float(text.strip().replace(",", ".")) % 360.0
            except ValueError:
                return False
            self.ask_angle = False
            self.update_prompt()
            return True
        return super().on_text(text)

    def resolved_angle(self, base: Vec2) -> float:
        if self.angle is not None:
            return self.angle
        mid = (self.points[0] + self.points[1]) * 0.5
        delta = base - mid
        return 0.0 if abs(delta.y) >= abs(delta.x) else 90.0

    def commit(self) -> None:
        p1, p2, base = self.points
        if p1.distance_to(p2) <= 1e-9:
            self.ctx.message("Pontos de medicao coincidentes")
            return
        entity = self.doc.add_linear_dimension(p1, p2, base, self.resolved_angle(base))
        self.ctx.message(f"Cota linear criada: {dimension_measurement(entity):.3f} m")

    def paint(self, painter, vp) -> None:
        if len(self.points) < 2 or self.cursor is None:
            return
        p1, p2, base = self.points[0], self.points[1], self.cursor
        angle = math.radians(self.resolved_angle(base))
        axis = Vec2(math.cos(angle), math.sin(angle))
        q1 = base + axis * (p1 - base).dot(axis)
        q2 = base + axis * (p2 - base).dot(axis)
        painter.setPen(_pen(DARK.q("preview")))
        _line(painter, vp, p1, q1)
        _line(painter, vp, p2, q2)
        _line(painter, vp, q1, q2)
        value = abs((p2 - p1).dot(axis))
        _label(painter, vp, (q1 + q2) * 0.5, f"{value:.2f}")


class AlignedDimensionTool(PointCollectorTool):
    name = "DIMALIGNED"
    min_points = 3
    max_points = 3

    def update_prompt(self) -> None:
        prompts = (
            "Primeira origem da linha de chamada:",
            "Segunda origem da linha de chamada:",
            "Posicao da linha de cota:",
        )
        self.set_prompt(prompts[min(len(self.points), 2)])

    def commit(self) -> None:
        p1, p2, base = self.points
        if p1.distance_to(p2) <= 1e-9:
            self.ctx.message("Pontos de medicao coincidentes")
            return
        entity = self.doc.add_aligned_dimension(p1, p2, base)
        self.ctx.message(f"Cota alinhada criada: {dimension_measurement(entity):.3f} m")

    def paint(self, painter, vp) -> None:
        if len(self.points) < 2 or self.cursor is None:
            return
        p1, p2, base = self.points[0], self.points[1], self.cursor
        edge = p2 - p1
        if edge.length <= 1e-9:
            return
        axis = edge / edge.length
        normal = Vec2(-axis.y, axis.x)
        distance = edge.cross(base - p1) / edge.length
        q1, q2 = p1 + normal * distance, p2 + normal * distance
        painter.setPen(_pen(DARK.q("preview")))
        _line(painter, vp, p1, q1)
        _line(painter, vp, p2, q2)
        _line(painter, vp, q1, q2)
        _label(painter, vp, (q1 + q2) * 0.5, f"{edge.length:.2f}")


class AngularDimensionTool(PointCollectorTool):
    name = "DIMANGULAR"
    min_points = 4
    max_points = 4

    def update_prompt(self) -> None:
        prompts = (
            "Vertice do angulo:",
            "Ponto no primeiro lado:",
            "Ponto no segundo lado:",
            "Posicao do arco de cota:",
        )
        self.set_prompt(prompts[min(len(self.points), 3)])

    @staticmethod
    def _measure(center: Vec2, p1: Vec2, p2: Vec2) -> float:
        return (math.degrees((p2 - center).angle - (p1 - center).angle) % 360.0)

    def commit(self) -> None:
        center, p1, p2, base = self.points
        if min(center.distance_to(p1), center.distance_to(p2)) <= 1e-9:
            self.ctx.message("Os lados do angulo precisam de comprimento")
            return
        entity = self.doc.add_angular_dimension(center, p1, p2, base)
        self.ctx.message(f"Cota angular criada: {dimension_measurement(entity):.3f} graus")

    def paint(self, painter, vp) -> None:
        if len(self.points) < 3 or self.cursor is None:
            return
        center, p1, p2, base = self.points[0], self.points[1], self.points[2], self.cursor
        radius = center.distance_to(base)
        if radius <= 1e-9:
            return
        a0, span = (p1 - center).angle, math.radians(self._measure(center, p1, p2))
        steps = max(12, int(math.degrees(span) / 4))
        pts = [Vec2.polar(center, a0 + span * i / steps, radius) for i in range(steps + 1)]
        painter.setPen(_pen(DARK.q("preview")))
        painter.drawPolyline(QPolygonF([QPointF(*vp.world_to_screen(p)) for p in pts]))
        _line(painter, vp, p1, pts[0])
        _line(painter, vp, p2, pts[-1])
        _label(painter, vp, pts[len(pts) // 2], f"{math.degrees(span):.2f}\N{DEGREE SIGN}")


class _RadialDimensionTool(Tool):
    diameter = False
    name = "DIMRADIUS"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.entity = None
        self.cursor: Vec2 | None = None

    def activate(self) -> None:
        self.set_prompt("Selecione um circulo ou arco:")

    def _pick_world(self, world: Vec2, event) -> Vec2:
        if event is not None and hasattr(event, "position"):
            pos = event.position()
            return self.ctx.viewport.screen_to_world(pos.x(), pos.y())
        return world

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world

    def on_click(self, world: Vec2, event=None) -> None:
        if self.entity is None:
            raw = self._pick_world(world, event)
            tol = self.ctx.viewport.px_to_world(10)
            entity = pick_at(self.doc, raw, tol)
            if entity is None or entity.dxftype() not in ("CIRCLE", "ARC"):
                self.ctx.message("Selecione a borda de um circulo ou arco")
                return
            self.entity = entity
            self.set_prompt("Posicao do texto e da linha de cota:")
            return
        dxf = self.entity.dxf
        center = Vec2(dxf.center.x, dxf.center.y)
        radius = float(dxf.radius)
        if self.diameter:
            made = self.doc.add_diameter_dimension(center, radius, world)
            self.ctx.message(f"Cota de diametro criada: {dimension_measurement(made):.3f} m")
        else:
            made = self.doc.add_radius_dimension(center, radius, world)
            self.ctx.message(f"Cota de raio criada: {dimension_measurement(made):.3f} m")
        self.finish()

    def paint(self, painter, vp) -> None:
        if self.entity is None or self.cursor is None:
            return
        dxf = self.entity.dxf
        center = Vec2(dxf.center.x, dxf.center.y)
        painter.setPen(_pen(DARK.q("preview")))
        if self.diameter:
            direction = self.cursor - center
            if direction.length > 1e-9:
                opposite = center - direction / direction.length * float(dxf.radius)
                _line(painter, vp, opposite, self.cursor)
            _label(painter, vp, self.cursor, f"\N{DIAMETER SIGN}{2 * float(dxf.radius):.2f}")
        else:
            _line(painter, vp, center, self.cursor)
            _label(painter, vp, self.cursor, f"R{float(dxf.radius):.2f}")


class RadiusDimensionTool(_RadialDimensionTool):
    pass


class DiameterDimensionTool(_RadialDimensionTool):
    name = "DIMDIAMETER"
    diameter = True


class OrdinateDimensionTool(PointCollectorTool):
    name = "DIMORDINATE"
    min_points = 2
    max_points = 2

    def update_prompt(self) -> None:
        self.set_prompt("Ponto a cotar:" if not self.points else "Extremidade da chamada:")

    def commit(self) -> None:
        feature, leader = self.points
        entity = self.doc.add_ordinate_dimension(feature, leader)
        axis = "X" if (int(entity.dxf.dimtype) & 64) else "Y"
        self.ctx.message(f"Cota ordenada {axis}: {dimension_measurement(entity):.3f} m")

    def paint(self, painter, vp) -> None:
        if not self.points or self.cursor is None:
            return
        feature = self.points[0]
        painter.setPen(_pen(DARK.q("preview")))
        _line(painter, vp, feature, self.cursor)
        x_type = abs((self.cursor - feature).y) >= abs((self.cursor - feature).x)
        axis = "X" if x_type else "Y"
        value = feature.x if x_type else feature.y
        _label(painter, vp, self.cursor, f"{axis} {value:.2f}")


class ArcLengthDimensionTool(_RadialDimensionTool):
    name = "DIMARC"

    def activate(self) -> None:
        self.set_prompt("Selecione um arco:")

    def on_click(self, world: Vec2, event=None) -> None:
        if self.entity is None:
            raw = self._pick_world(world, event)
            entity = pick_at(self.doc, raw, self.ctx.viewport.px_to_world(10))
            if entity is None or entity.dxftype() != "ARC":
                self.ctx.message("Selecione a borda de um arco")
                return
            self.entity = entity
            self.set_prompt("Posicao do arco de cota:")
            return
        dxf = self.entity.dxf
        center = Vec2(dxf.center.x, dxf.center.y)
        p1 = Vec2.polar(center, math.radians(float(dxf.start_angle)), float(dxf.radius))
        p2 = Vec2.polar(center, math.radians(float(dxf.end_angle)), float(dxf.radius))
        made = self.doc.add_arc_length_dimension(center, p1, p2, world)
        self.ctx.message(f"Comprimento de arco cotado: {dimension_measurement(made):.3f} m")
        self.finish()

    def paint(self, painter, vp) -> None:
        if self.entity is None or self.cursor is None:
            return
        dxf = self.entity.dxf
        center = Vec2(dxf.center.x, dxf.center.y)
        radius = center.distance_to(self.cursor)
        a0 = float(dxf.start_angle)
        span = (float(dxf.end_angle) - a0) % 360.0 or 360.0
        steps = max(12, int(span / 4))
        pts = [
            Vec2.polar(center, math.radians(a0 + span * i / steps), radius)
            for i in range(steps + 1)
        ]
        painter.setPen(_pen(DARK.q("preview")))
        painter.drawPolyline(QPolygonF([QPointF(*vp.world_to_screen(p)) for p in pts]))
        length = math.radians(span) * float(dxf.radius)
        _label(painter, vp, pts[len(pts) // 2], f"⌒{length:.2f}")
