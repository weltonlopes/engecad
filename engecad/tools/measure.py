"""Ferramentas de medicao: distancia/azimute e area."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QBrush, QColor, QPen, QPolygonF

from ..core.geometry import azimuth, format_dms, polygon_area, polyline_length
from ..render.styles import DARK
from .base import PointCollectorTool


class DistanceTool(PointCollectorTool):
    name = "DIST"
    prompt = "Primeiro ponto da medicao:"
    min_points = 2
    max_points = 2

    def update_prompt(self) -> None:
        self.set_prompt("Segundo ponto:" if self.points else "Primeiro ponto da medicao:")

    def commit(self) -> None:
        a, b = self.points[0], self.points[1]
        d = a.distance_to(b)
        az = azimuth(a, b)
        self.ctx.message(
            f"Distancia {d:.3f} m   dX {b.x - a.x:+.3f}   dY {b.y - a.y:+.3f}   "
            f"Azimute {format_dms(az)}"
        )

    def paint(self, painter, vp) -> None:
        if not self.points or self.cursor is None:
            return
        a = self.points[0]
        pen = QPen(DARK.q("preview"), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(*vp.world_to_screen(a)), QPointF(*vp.world_to_screen(self.cursor)))
        d = a.distance_to(self.cursor)
        sx, sy = vp.world_to_screen(self.cursor)
        painter.drawText(
            QPointF(sx + 14, sy - 10),
            f"{d:.3f} m   Az {format_dms(azimuth(a, self.cursor))}",
        )


class AreaTool(PointCollectorTool):
    name = "AREA"
    prompt = "Primeiro vertice do poligono:"
    min_points = 3
    max_points = 0

    def update_prompt(self) -> None:
        if len(self.points) < 3:
            self.set_prompt("Proximo vertice  [Enter=terminar]:")
        else:
            a = polygon_area(self.points)
            self.set_prompt(f"Proximo vertice  [Enter=terminar]  area {a:.3f} m2:")

    def on_key(self, key, modifiers=None) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if len(self.points) >= self.min_points:
                self.commit()
            self.finish()
            return True
        return False

    def commit(self) -> None:
        a = polygon_area(self.points)
        per = polyline_length(self.points, closed=True)
        self.ctx.message(
            f"Area {a:.3f} m2  ({a / 10000:.4f} ha)   Perimetro {per:.3f} m   "
            f"{len(self.points)} vertices"
        )

    def paint(self, painter, vp) -> None:
        if not self.points:
            return
        pts = list(self.points)
        if self.cursor is not None:
            pts.append(self.cursor)
        poly = QPolygonF([QPointF(*vp.world_to_screen(p)) for p in pts])
        if len(pts) >= 3:
            fill = QColor(DARK.preview)
            fill.setAlpha(40)
            painter.setBrush(QBrush(fill))
        pen = QPen(DARK.q("preview"), 1.4)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawPolygon(poly)
        painter.setBrush(Qt.NoBrush)
        if len(pts) >= 3:
            c = QPolygonF(poly).boundingRect().center()
            painter.drawText(c, f"{polygon_area(pts):.2f} m2")
