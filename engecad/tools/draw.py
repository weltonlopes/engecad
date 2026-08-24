"""Ferramentas de desenho da v0.1: LINE e PLINE."""

from __future__ import annotations

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPen, QPolygonF

from ..core.geometry import Vec2, polygon_area, polyline_length
from ..render.styles import DARK
from .base import PointCollectorTool


def _pen(color, width=1.4, style=Qt.SolidLine) -> QPen:
    p = QPen(color, width)
    p.setStyle(style)
    p.setCosmetic(True)
    return p


class LineTool(PointCollectorTool):
    name = "LINE"
    prompt = "Primeiro ponto:"
    repeats = True
    min_points = 2
    max_points = 2

    def update_prompt(self) -> None:
        self.set_prompt("Proximo ponto:" if self.points else "Primeiro ponto:")

    def commit(self) -> None:
        if len(self.points) < 2:
            return
        a, b = self.points[0], self.points[1]
        self.doc.add_line(a, b)
        self.ctx.message(f"Linha de {a.distance_to(b):.3f} m")

    def paint(self, painter, vp) -> None:
        if not self.points or self.cursor is None:
            return
        a = self.points[0]
        painter.setPen(_pen(DARK.q("preview")))
        x1, y1 = vp.world_to_screen(a)
        x2, y2 = vp.world_to_screen(self.cursor)
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        _draw_measure_label(painter, vp, a, self.cursor)


class PolylineTool(PointCollectorTool):
    name = "PLINE"
    prompt = "Primeiro ponto:"
    repeats = True
    min_points = 2
    max_points = 0  # sem limite: termina com Enter ou botao direito

    def __init__(self, ctx):
        super().__init__(ctx)
        self.closed = False

    def update_prompt(self) -> None:
        if not self.points:
            self.set_prompt("Primeiro ponto:")
        else:
            total = polyline_length(self.points)
            self.set_prompt(
                f"Proximo ponto  [F=fechar, Enter=terminar]  acumulado {total:.3f} m:"
            )

    def on_key(self, key, modifiers=None) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if len(self.points) >= self.min_points:
                self.commit()
            self.finish()
            return True
        return False

    def on_text(self, text: str) -> bool:
        t = text.strip().upper()
        if t in ("F", "C", "FECHAR"):
            if len(self.points) >= 3:
                self.closed = True
                self.commit()
            self.finish()
            return True
        return super().on_text(text)

    def commit(self) -> None:
        if len(self.points) < 2:
            return
        self.doc.add_lwpolyline(self.points, closed=self.closed)
        total = polyline_length(self.points, closed=self.closed)
        msg = f"Polilinha de {len(self.points)} vertices, {total:.3f} m"
        if self.closed:
            msg += f", area {polygon_area(self.points):.3f} m2"
        self.ctx.message(msg)

    def paint(self, painter, vp) -> None:
        if not self.points:
            return
        pts = list(self.points)
        if self.cursor is not None:
            pts.append(self.cursor)
        poly = QPolygonF([QPointF(*vp.world_to_screen(p)) for p in pts])
        painter.setPen(_pen(DARK.q("preview")))
        painter.drawPolyline(poly)
        if len(pts) > 2:
            # previa do fechamento, tracejada
            painter.setPen(_pen(DARK.q("preview"), 1.0, Qt.DashLine))
            x1, y1 = vp.world_to_screen(pts[-1])
            x2, y2 = vp.world_to_screen(pts[0])
            painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        if self.cursor is not None and self.points:
            _draw_measure_label(painter, vp, self.points[-1], self.cursor)


def _draw_measure_label(painter, vp, a: Vec2, b: Vec2) -> None:
    """Mostra distancia e azimute do segmento elastico, ao lado do cursor."""
    from ..core.geometry import azimuth

    d = a.distance_to(b)
    if d <= 0:
        return
    az = azimuth(a, b)
    sx, sy = vp.world_to_screen(b)
    painter.setPen(DARK.q("preview"))
    painter.drawText(QPointF(sx + 14, sy - 10), f"{d:.3f} m   Az {az:.4f}\N{DEGREE SIGN}")
