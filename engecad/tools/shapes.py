"""Ferramentas de forma: retangulo, circulo, arco e texto.

Separadas de tools/draw.py, que cuida do desenho linear (linha e polilinha).
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPolygonF

from ..core.geometry import Vec2, circle_from_3points, polygon_area
from ..render.styles import DARK
from .base import PointCollectorTool, Tool
from .draw import _draw_measure_label, _pen


class RectangleTool(PointCollectorTool):
    """Retangulo por dois cantos -- vira polilinha fechada de 4 vertices."""

    name = "RECT"
    prompt = "Primeiro canto:"
    min_points = 2
    max_points = 2

    def update_prompt(self) -> None:
        if not self.points:
            self.set_prompt("Primeiro canto:")
        elif self.cursor is not None:
            a = self.points[0]
            w = abs(self.cursor.x - a.x)
            h = abs(self.cursor.y - a.y)
            self.set_prompt(f"Canto oposto  ({w:.3f} x {h:.3f} m):")
        else:
            self.set_prompt("Canto oposto:")

    @staticmethod
    def corners(a: Vec2, b: Vec2) -> list[Vec2]:
        return [a, Vec2(b.x, a.y), b, Vec2(a.x, b.y)]

    def commit(self) -> None:
        a, b = self.points[0], self.points[1]
        if abs(b.x - a.x) < 1e-9 or abs(b.y - a.y) < 1e-9:
            self.ctx.message("Retangulo degenerado")
            return
        pts = self.corners(a, b)
        self.doc.add_lwpolyline(pts, closed=True)
        self.ctx.message(
            f"Retangulo {abs(b.x - a.x):.3f} x {abs(b.y - a.y):.3f} m, "
            f"area {polygon_area(pts):.3f} m2"
        )

    def paint(self, painter, vp) -> None:
        if not self.points or self.cursor is None:
            return
        pts = self.corners(self.points[0], self.cursor)
        poly = QPolygonF([QPointF(*vp.world_to_screen(p)) for p in pts])
        painter.setPen(_pen(DARK.q("preview")))
        painter.drawPolygon(poly)
        self.update_prompt()


class CircleTool(PointCollectorTool):
    """Circulo por centro e raio. O raio pode ser digitado."""

    name = "CIRCLE"
    prompt = "Centro do circulo:"
    min_points = 2
    max_points = 2

    def update_prompt(self) -> None:
        if not self.points:
            self.set_prompt("Centro do circulo:")
        else:
            r = self.points[0].distance_to(self.cursor) if self.cursor else 0.0
            self.set_prompt(f"Raio (ou ponto)  [{r:.3f} m]:")

    def on_text(self, text: str) -> bool:
        if self.points:
            try:
                r = float(text.strip().replace(",", "."))
            except ValueError:
                return super().on_text(text)
            if r <= 0:
                self.ctx.message("O raio tem de ser positivo")
                return True
            self._create(self.points[0], r)
            return True
        return super().on_text(text)

    def commit(self) -> None:
        c = self.points[0]
        self._create(c, c.distance_to(self.points[1]))

    def _create(self, center: Vec2, radius: float) -> None:
        if radius <= 1e-9:
            return
        self.doc.add_circle(center, radius)
        self.ctx.message(f"Circulo raio {radius:.3f} m, area {math.pi * radius**2:.3f} m2")
        self.finish()

    def paint(self, painter, vp) -> None:
        if not self.points or self.cursor is None:
            return
        c = self.points[0]
        r = vp.world_to_px(c.distance_to(self.cursor))
        painter.setPen(_pen(DARK.q("preview")))
        painter.drawEllipse(QPointF(*vp.world_to_screen(c)), r, r)
        _draw_measure_label(painter, vp, c, self.cursor)


class ArcTool(PointCollectorTool):
    """Arco por tres pontos: inicio, um ponto no meio, fim.

    E a forma mais util em mapeamento, porque os tres pontos costumam vir
    direto de coordenadas levantadas em campo.
    """

    name = "ARC"
    prompt = "Inicio do arco:"
    min_points = 3
    max_points = 3

    def update_prompt(self) -> None:
        rotulos = ("Inicio do arco:", "Ponto sobre o arco:", "Fim do arco:")
        self.set_prompt(rotulos[min(len(self.points), 2)])

    @staticmethod
    def solve(a: Vec2, mid: Vec2, b: Vec2):
        """(centro, raio, angulo inicial, angulo final) em graus, anti-horario."""
        fit = circle_from_3points(a, mid, b)
        if fit is None:
            return None
        center, radius = fit
        ang_a = math.degrees((a - center).angle) % 360.0
        ang_b = math.degrees((b - center).angle) % 360.0
        # O DXF guarda o arco sempre no sentido anti-horario: se o trajeto
        # a -> mid -> b for horario, trocamos os extremos.
        if (mid - a).cross(b - mid) < 0:
            ang_a, ang_b = ang_b, ang_a
        return center, radius, ang_a, ang_b

    def commit(self) -> None:
        sol = self.solve(self.points[0], self.points[1], self.points[2])
        if sol is None:
            self.ctx.message("Os tres pontos sao colineares - nao definem um arco")
            return
        center, radius, a0, a1 = sol
        self.doc.add_arc(center, radius, a0, a1)
        span = (a1 - a0) % 360.0 or 360.0
        self.ctx.message(
            f"Arco raio {radius:.3f} m, abertura {span:.4f} graus, "
            f"desenvolvimento {math.radians(span) * radius:.3f} m"
        )

    def paint(self, painter, vp) -> None:
        pts = list(self.points)
        if self.cursor is not None:
            pts.append(self.cursor)
        painter.setPen(_pen(DARK.q("preview")))
        if len(pts) == 2:
            painter.drawLine(
                QPointF(*vp.world_to_screen(pts[0])),
                QPointF(*vp.world_to_screen(pts[1])),
            )
            return
        if len(pts) < 3:
            return
        sol = self.solve(pts[0], pts[1], pts[2])
        if sol is None:
            return
        center, radius, a0, a1 = sol
        span = (a1 - a0) % 360.0 or 360.0
        steps = max(8, int(span / 3))
        poly = QPolygonF(
            [
                QPointF(
                    *vp.world_to_screen(
                        Vec2.polar(center, math.radians(a0 + span * i / steps), radius)
                    )
                )
                for i in range(steps + 1)
            ]
        )
        painter.drawPolyline(poly)


class TextTool(Tool):
    """Texto: ponto, altura e conteudo, tudo pela linha de comando."""

    name = "TEXT"
    default_height = 2.5

    def __init__(self, ctx):
        super().__init__(ctx)
        self.point: Vec2 | None = None
        self.height: float | None = None
        self.cursor: Vec2 | None = None

    def activate(self) -> None:
        self.set_prompt("Ponto de insercao do texto:")

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world

    def on_click(self, world: Vec2, event=None) -> None:
        if self.point is None:
            self.point = world
            self.set_prompt(f"Altura do texto <{self.default_height:g}>:")

    def on_key(self, key, modifiers=None) -> bool:
        # Enter na fase da altura aceita o valor padrao
        if key in (Qt.Key_Return, Qt.Key_Enter) and self.point is not None and self.height is None:
            self.height = self.default_height
            self.set_prompt("Texto:")
            return True
        return False

    def on_text(self, text: str) -> bool:
        if self.point is None:
            from ..core.coordinput import parse_coordinate

            p = parse_coordinate(text)
            if p is None:
                return False
            self.point = p
            self.set_prompt(f"Altura do texto <{self.default_height:g}>:")
            return True

        if self.height is None:
            try:
                h = float(text.strip().replace(",", "."))
                if h <= 0:
                    raise ValueError
                self.height = h
            except ValueError:
                self.height = self.default_height
            self.set_prompt("Texto:")
            return True

        self.doc.add_text(text, self.point, height=self.height)
        self.ctx.message(f"Texto inserido com altura {self.height:g} m")
        self.finish()
        return True

    def paint(self, painter, vp) -> None:
        if self.point is None:
            return
        painter.setPen(_pen(DARK.q("preview")))
        x, y = vp.world_to_screen(self.point)
        painter.drawLine(QPointF(x - 6, y), QPointF(x + 6, y))
        painter.drawLine(QPointF(x, y - 6), QPointF(x, y + 6))
