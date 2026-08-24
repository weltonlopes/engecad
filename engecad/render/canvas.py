"""Canvas do CAD.

Widget proprio com QPainter, e nao QGraphicsView. Motivo em render/viewport.py:
toda a transformacao mundo->tela e feita em float64 no Python e o painter
recebe apenas coordenadas de tela, que sao numeros pequenos. Nenhuma
QTransform com valores de magnitude UTM chega ao motor de rasterizacao.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..core.entities import POINT_LIKE, entity_insert_point, entity_polylines
from ..core.geometry import Vec2
from .styles import DARK, aci_to_qcolor

ZOOM_STEP = 1.18
CROSSHAIR_GAP = 7  # px do quadradinho central
PICKBOX = 6  # meio-lado do quadradinho de selecao, em px
MAX_GRID_LINES = 400


class CadCanvas(QWidget):
    coordinateMoved = Signal(object)  # Vec2 no CRS do projeto
    snapChanged = Signal(object)  # SnapResult | None
    viewChanged = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        ctx.canvas = self
        self.theme = DARK
        self.show_grid = True
        self.show_crosshair = True

        self._cursor_screen: QPointF | None = None
        self._cursor_world: Vec2 | None = None
        self._snap = None
        self._panning = False
        self._pan_anchor: QPointF | None = None

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)  # desenhamos a mira nos mesmos
        self.setAutoFillBackground(False)

    # ---------------- atalhos ----------------

    @property
    def vp(self):
        return self.ctx.viewport

    @property
    def doc(self):
        return self.ctx.doc

    def effective_point(self) -> Vec2 | None:
        """Ponto que um clique produziria: o snap se houver, senao o cursor."""
        if self._snap is not None:
            return self._snap.point
        return self._cursor_world

    # ---------------- eventos de janela ----------------

    def resizeEvent(self, ev):
        self.vp.resize(self.width(), self.height())
        super().resizeEvent(ev)

    # ---------------- mouse ----------------

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._panning and self._pan_anchor is not None:
            d = pos - self._pan_anchor
            self.vp.pan_screen(d.x(), d.y())
            self._pan_anchor = pos
            self._emit_view_changed()
            self.update()
            return

        self._cursor_screen = pos
        self._cursor_world = self.vp.screen_to_world(pos.x(), pos.y())
        self._snap = self.ctx.snap.snap(self._cursor_world, self.vp)
        self.snapChanged.emit(self._snap)
        self.coordinateMoved.emit(self.effective_point())

        tool = self.ctx.tool
        if tool is not None:
            tool.on_mouse_move(self.effective_point(), ev)
        self.update()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_anchor = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        p = self.effective_point()
        if p is None:
            return
        tool = self.ctx.tool
        if tool is None:
            return
        if ev.button() == Qt.LeftButton:
            tool.on_click(p, ev)
        elif ev.button() == Qt.RightButton:
            tool.on_right_click(p, ev)
        self.update()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self._pan_anchor = None
            self.setCursor(Qt.BlankCursor)
            return
        if ev.button() == Qt.LeftButton:
            tool = self.ctx.tool
            p = self.effective_point()
            if tool is not None and p is not None:
                tool.on_release(p, ev)
                self.update()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        factor = ZOOM_STEP if delta > 0 else 1 / ZOOM_STEP
        pos = ev.position()
        self.vp.zoom_at_screen(pos.x(), pos.y(), factor)
        self._cursor_world = self.vp.screen_to_world(pos.x(), pos.y())
        self._snap = self.ctx.snap.snap(self._cursor_world, self.vp)
        self.coordinateMoved.emit(self.effective_point())
        self._emit_view_changed()
        self.update()

    def keyPressEvent(self, ev):
        tool = self.ctx.tool
        if tool is not None and tool.on_key(ev.key(), ev.modifiers()):
            self.update()
            return
        if ev.key() == Qt.Key_Escape:
            self.ctx.cancel_tool()
            self.update()
            return

        # Como no AutoCAD: digitar com o foco no desenho cai na linha de
        # comando. Evita que o usuario tenha de clicar la embaixo antes de
        # cada comando, e dispensa atalhos de uma letra so no menu (que
        # roubariam a tecla de quem esta digitando).
        text = ev.text()
        blocked = ev.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)
        cl = getattr(self.ctx, "command_line", None)
        if cl is not None and text and text.isprintable() and not blocked:
            cl.entry.setFocus()
            cl.entry.setText(cl.entry.text() + text)
            return
        super().keyPressEvent(ev)

    def leaveEvent(self, ev):
        self._cursor_screen = None
        self._snap = None
        self.update()
        super().leaveEvent(ev)

    def _emit_view_changed(self):
        self.viewChanged.emit()
        self.ctx.viewChanged.emit()

    # ---------------- desenho ----------------

    def paintEvent(self, ev):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.fillRect(self.rect(), self.theme.q("background"))

        self._paint_rasters(painter)
        if self.show_grid:
            self._paint_grid(painter)
        self._paint_entities(painter)
        self._paint_hover(painter)
        self._paint_selection(painter)

        tool = self.ctx.tool
        if tool is not None:
            painter.save()
            tool.paint(painter, self.vp)
            painter.restore()

        self._paint_grips(painter)
        self._paint_snap(painter)
        if self.show_crosshair:
            self._paint_cursor(painter)
        painter.end()

    def _paint_rasters(self, painter):
        for layer in self.ctx.rasters:
            if not layer.visible:
                continue
            try:
                layer.paint(painter, self.vp)
            except Exception as exc:  # um raster problematico nao pode matar o frame
                self.ctx.message(f"Falha ao desenhar raster: {exc}")
                layer.visible = False

    def _paint_grid(self, painter):
        vp = self.vp
        step = vp.nice_grid_step()
        vis = vp.visible_bbox()
        if step <= 0 or vis.width / step > MAX_GRID_LINES:
            return

        minor = QPen(self.theme.q("grid_minor"), 1)
        minor.setCosmetic(True)
        major = QPen(self.theme.q("grid_major"), 1)
        major.setCosmetic(True)

        import math

        x0 = math.floor(vis.minx / step) * step
        y0 = math.floor(vis.miny / step) * step
        n = 0
        x = x0
        while x <= vis.maxx and n < MAX_GRID_LINES:
            sx, _ = vp.world_to_screen(Vec2(x, 0))
            painter.setPen(major if abs(round(x / step)) % 5 == 0 else minor)
            painter.drawLine(QPointF(sx, 0), QPointF(sx, self.height()))
            x += step
            n += 1
        n = 0
        y = y0
        while y <= vis.maxy and n < MAX_GRID_LINES:
            _, sy = vp.world_to_screen(Vec2(0, y))
            painter.setPen(major if abs(round(y / step)) % 5 == 0 else minor)
            painter.drawLine(QPointF(0, sy), QPointF(self.width(), sy))
            y += step
            n += 1

    def _paint_entities(self, painter):
        vp = self.vp
        doc = self.doc
        vis = vp.visible_bbox()
        tol = vp.flatten_tolerance(0.3)
        dark = self.theme is DARK

        font = QFont(painter.font())
        for e in doc.query(vis):
            if not e.is_alive:
                continue
            layer = e.dxf.get("layer", "0")
            if not doc.layer_is_visible(layer):
                continue
            color = e.dxf.get("color", 256)
            aci = doc.layer_color(layer) if color in (256, 0) else color
            pen = QPen(aci_to_qcolor(aci, dark), 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)

            t = e.dxftype()
            if t in POINT_LIKE:
                self._paint_point_like(painter, e, t, font)
                continue
            for poly in entity_polylines(e, tol):
                pts = [QPointF(*vp.world_to_screen(p)) for p in poly]
                if len(pts) >= 2:
                    painter.drawPolyline(QPolygonF(pts))

    def _paint_point_like(self, painter, e, dxftype, font):
        vp = self.vp
        p = entity_insert_point(e)
        if p is None:
            return
        sx, sy = vp.world_to_screen(p)
        if dxftype == "POINT":
            painter.drawLine(QPointF(sx - 4, sy), QPointF(sx + 4, sy))
            painter.drawLine(QPointF(sx, sy - 4), QPointF(sx, sy + 4))
            return
        if dxftype in ("TEXT", "MTEXT"):
            height = float(e.dxf.get("height", 1.0) or 1.0)
            px = vp.world_to_px(height)
            if px < 3:  # ilegivel: vira um tracinho
                painter.drawLine(QPointF(sx, sy), QPointF(sx + 6, sy))
                return
            font.setPixelSize(max(3, int(px)))
            painter.setFont(font)
            text = e.dxf.get("text", "") if dxftype == "TEXT" else e.text
            painter.drawText(QPointF(sx, sy), str(text))
            return
        # INSERT e outros: marcador simples
        painter.drawRect(QRectF(sx - 3, sy - 3, 6, 6))

    def _entity_outline(self, painter, entity, tol):
        """Traca o contorno da entidade com a caneta ja escolhida."""
        vp = self.vp
        if entity.dxftype() in POINT_LIKE:
            p = entity_insert_point(entity)
            if p is not None:
                x, y = vp.world_to_screen(p)
                painter.drawRect(QRectF(x - 5, y - 5, 10, 10))
            return
        for poly in entity_polylines(entity, tol):
            pts = [QPointF(*vp.world_to_screen(p)) for p in poly]
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))

    def _paint_hover(self, painter):
        """Realce leve da entidade sob o cursor, antes de clicar."""
        tool = self.ctx.tool
        if tool is None or not tool.is_idle:
            return
        e = getattr(tool, "hover", None)
        sel = self.ctx.selection
        if e is None or not e.is_alive or (sel is not None and e in sel):
            return
        color = QColor(self.theme.selection)
        color.setAlpha(150)
        pen = QPen(color, 3.0)
        pen.setCosmetic(True)
        painter.setPen(pen)
        self._entity_outline(painter, e, self.vp.flatten_tolerance(0.5))

    def _paint_selection(self, painter):
        sel = self.ctx.selection
        if sel is None or not len(sel):
            return
        pen = QPen(self.theme.q("selection"), 1.8)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        tol = self.vp.flatten_tolerance(0.4)
        for e in sel:
            self._entity_outline(painter, e, tol)

    def _paint_grips(self, painter):
        tool = self.ctx.tool
        if tool is None or not tool.is_idle:
            return
        grips = tool.visible_grips()
        if not grips:
            return
        hovered = getattr(tool, "hover_grip", None)
        vp = self.vp
        base = QColor(self.theme.selection)
        hot = QColor("#ff8c1a")
        pen = QPen(base.darker(150), 1)
        pen.setCosmetic(True)
        for g in grips:
            x, y = vp.world_to_screen(g.point)
            is_hot = (
                hovered is not None
                and hovered.entity is g.entity
                and hovered.kind == g.kind
                and hovered.index == g.index
            )
            s = 6 if is_hot else 4.5
            painter.setPen(pen)
            painter.setBrush(QBrush(hot if is_hot else base))
            painter.drawRect(QRectF(x - s, y - s, 2 * s, 2 * s))
        painter.setBrush(Qt.NoBrush)

    def _paint_snap(self, painter):
        if self._snap is None:
            return
        sx, sy = self.vp.world_to_screen(self._snap.point)
        pen = QPen(self.theme.q("snap_marker"), 1.8)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        k = self._snap.kind
        s = 6
        if k == "end":
            painter.drawRect(QRectF(sx - s, sy - s, 2 * s, 2 * s))
        elif k == "mid":
            painter.drawPolygon(
                QPolygonF([QPointF(sx, sy - s), QPointF(sx + s, sy + s), QPointF(sx - s, sy + s)])
            )
        elif k in ("center", "node"):
            painter.drawEllipse(QPointF(sx, sy), s, s)
        elif k == "quad":
            painter.drawPolygon(
                QPolygonF(
                    [
                        QPointF(sx, sy - s),
                        QPointF(sx + s, sy),
                        QPointF(sx, sy + s),
                        QPointF(sx - s, sy),
                    ]
                )
            )
        elif k == "intersection":
            painter.drawLine(QPointF(sx - s, sy - s), QPointF(sx + s, sy + s))
            painter.drawLine(QPointF(sx - s, sy + s), QPointF(sx + s, sy - s))
        else:  # nearest, grid
            painter.drawLine(QPointF(sx - s, sy + s), QPointF(sx + s, sy + s))
            painter.drawLine(QPointF(sx - s, sy - s), QPointF(sx - s, sy + s))
        painter.setPen(self.theme.q("snap_text"))
        painter.drawText(QPointF(sx + 12, sy + 18), self._snap.label)

    def _paint_cursor(self, painter):
        if self._cursor_screen is None:
            return
        x, y = self._cursor_screen.x(), self._cursor_screen.y()
        pen = QPen(self.theme.q("crosshair"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(QPointF(0, y), QPointF(x - CROSSHAIR_GAP, y))
        painter.drawLine(QPointF(x + CROSSHAIR_GAP, y), QPointF(self.width(), y))
        painter.drawLine(QPointF(x, 0), QPointF(x, y - CROSSHAIR_GAP))
        painter.drawLine(QPointF(x, y + CROSSHAIR_GAP), QPointF(x, self.height()))
        painter.setPen(QPen(self.theme.q("cursor_box"), 1))
        painter.drawRect(QRectF(x - PICKBOX, y - PICKBOX, 2 * PICKBOX, 2 * PICKBOX))

    # ---------------- utilidades de vista ----------------

    def zoom_extents(self):
        self.ctx.zoom_extents()
        self.update()

    def set_theme(self, theme):
        self.theme = theme
        self.update()
