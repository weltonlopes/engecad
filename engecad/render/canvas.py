"""Canvas do CAD.

Widget proprio com QPainter, e nao QGraphicsView. Motivo em render/viewport.py:
o Qt nunca pode receber coordenadas de magnitude UTM.

O quadro e montado em duas camadas:

* a CENA -- rasters, grade e geometria -- sai da display list (render/
  displaylist.py) e fica guardada num pixmap maior que a janela (render/
  framecache.py). Arrastar a vista e um blit; mover o mouse nao a toca.
* o SOBREPOSTO -- mira, snap, selecao, grips, previa da ferramenta -- e
  redesenhado a cada quadro, mas custa quase nada porque sao poucos objetos.

Era essa separacao que faltava: antes, cada movimento do mouse repintava o
desenho inteiro so para mover a cruz do cursor.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QWidget

from ..core.dimensions import DIMENSION_TYPES
from ..core.entities import POINT_LIKE, entity_insert_point, entity_polylines, entity_primitives
from ..core.geometry import Vec2
from .displaylist import DisplayList
from .framecache import FrameCache
from .styles import DARK, aci_to_qcolor

ZOOM_STEP = 1.18
CROSSHAIR_GAP = 7  # px do quadradinho central
PICKBOX = 6  # meio-lado do quadradinho de selecao, em px
MAX_GRID_LINES = 400

# Acima disto o redesenho da cena atrapalha o gesto: durante um pan ou um zoom
# continuo mostramos o cache esticado e refinamos quando o movimento para.
SLOW_FRAME_MS = 25.0
REFINE_MS = 70  # espera antes do redesenho fino, em ms
# Orcamento de cada fatia do redesenho. Abaixo de um quadro de 60 Hz, para o
# canvas devolver o controle ao Qt antes de a interface parecer travada.
STEP_BUDGET_MS = 12.0
FIRST_STEP_BUDGET_MS = 60.0  # a primeira fatia acomoda um desenho comum inteiro
MAX_OUTLINES = 2_000  # contornos de selecao/realce desenhados por quadro
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}


class CadCanvas(QWidget):
    coordinateMoved = Signal(object)  # Vec2 no CRS do projeto
    snapChanged = Signal(object)  # SnapResult | None
    viewChanged = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        ctx.canvas = self
        self.theme = DARK
        self._show_grid = True
        self.show_crosshair = True

        self._cursor_screen: QPointF | None = None
        self._cursor_world: Vec2 | None = None
        self._snap = None
        self._panning = False
        self._pan_anchor: QPointF | None = None

        self._display = DisplayList(ctx.doc)
        self._frame = FrameCache()
        self._interactive = False
        self._refine = QTimer(self)
        self._refine.setSingleShot(True)
        self._refine.timeout.connect(self._finish_gesture)
        # Continua um redesenho fatiado na proxima volta do laco de eventos.
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.setInterval(0)
        self._advance.timeout.connect(self.update)
        ctx.documentReplaced.connect(self._on_document_replaced)
        # Qualquer mutacao do documento (geometria, cor ou visibilidade de
        # camada) invalida o quadro guardado; a display list so reconstroi os
        # tiles que a entidade alterada tocava.
        ctx.documentChanged.connect(self.invalidate_scene)
        ctx.rastersChanged.connect(self.invalidate_scene)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setCursor(Qt.BlankCursor)  # desenhamos a mira nos mesmos
        self.setAutoFillBackground(False)

    # ---------------- invalidacao da cena ----------------

    def _on_document_replaced(self) -> None:
        self._display = DisplayList(self.ctx.doc)
        self.invalidate_scene()

    def invalidate_scene(self) -> None:
        """Descarta o quadro guardado. A geometria em si so e refeita se mudou."""
        self._frame.invalidate()
        self.update()

    def _finish_gesture(self) -> None:
        self._interactive = False
        self._frame.invalidate()
        self.update()

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

    @property
    def current_snap(self):
        """Snap que originou o ponto efetivo atual, para ferramentas associativas."""
        return self._snap

    # ---------------- eventos de janela ----------------

    def resizeEvent(self, ev):
        self.vp.resize(self.width(), self.height())
        self._frame.invalidate()
        super().resizeEvent(ev)

    # ---------------- mouse ----------------

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._panning and self._pan_anchor is not None:
            d = pos - self._pan_anchor
            self.vp.pan_screen(d.x(), d.y())
            self._pan_anchor = pos
            self._interactive = True
            self._emit_view_changed()
            self.update()
            return

        self._cursor_screen = pos
        self._cursor_world = self.vp.screen_to_world(pos.x(), pos.y())
        tool = self.ctx.tool
        exclude = tool.snap_exclude() if tool is not None else ()
        self._snap = self.ctx.snap.snap(self._cursor_world, self.vp, exclude=exclude)
        self.snapChanged.emit(self._snap)
        self.coordinateMoved.emit(self.effective_point())

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
            if self._interactive:  # o gesto acabou: refina agora
                self._finish_gesture()
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
        self._interactive = True
        tool = self.ctx.tool
        exclude = tool.snap_exclude() if tool is not None else ()
        self._snap = self.ctx.snap.snap(self._cursor_world, self.vp, exclude=exclude)
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
        self._draw_scene(painter)

        painter.setRenderHint(QPainter.Antialiasing, True)
        self._paint_hover(painter)
        self._paint_selection(painter)

        tool = self.ctx.tool
        if tool is not None:
            painter.save()
            tool.paint(painter, self.vp)
            painter.restore()

        self._paint_grips(painter)
        self._paint_vertex_focus(painter)
        self._paint_snap(painter)
        if self.show_crosshair:
            self._paint_cursor(painter)
        painter.end()

    # ---------------- cena (rasters + grade + geometria) ----------------

    def _draw_scene(self, painter):
        """Coloca a cena na tela, redesenhando-a so quando o cache nao serve.

        Um redesenho pesado nao acontece de uma vez: ele avanca um pedaco por
        quadro, dentro de um orcamento de tempo, e o canvas mostra o que ja foi
        montado. Entre um pedaco e o outro o controle volta ao Qt, entao o mouse,
        o teclado e as ferramentas continuam respondendo enquanto o desenho
        aparece.
        """
        vp = self.vp
        frame = self._frame
        if frame.is_exact(vp):
            frame.blit(painter, vp)
            return
        if self._interactive and frame.has_content and frame.last_ms > SLOW_FRAME_MS:
            # Gesto em andamento e redesenho caro: mostra o cache esticado e
            # refina quando o movimento parar.
            painter.fillRect(self.rect(), self.theme.q("background"))
            frame.blit(painter, vp)
            self._refine.start(REFINE_MS)
            return
        if not frame.building_for(vp):
            frame.begin(vp, self.devicePixelRatioF(), self._scene_steps)
        # A primeira fatia e generosa: um desenho comum fecha nela, e nao paga
        # uma volta a toa no laco de eventos. So a cena que estoura esse limite
        # passa a ser desenhada aos pedacos.
        done = frame.step(STEP_BUDGET_MS if frame.started else FIRST_STEP_BUDGET_MS)
        frame.blit(painter, vp)
        if not done:
            self._advance.start(0)  # devolve o controle ao laco e continua

    def render_scene_now(self) -> None:
        """Completa a cena sem depender do laco de eventos (testes, exportacao)."""
        self._frame.render_now(self.vp, self.devicePixelRatioF(), self._scene_steps)

    def _scene_steps(self, vp):
        """Etapas do quadro, na ordem em que valem mais para quem olha.

        Gerador, e nao lista: o planejamento da geometria so acontece depois que
        a display list terminou de se preparar, e essa preparacao tambem e uma
        etapa com orcamento.
        """
        yield lambda p, deadline: self._paint_base(p, vp)
        yield lambda p, deadline: self._display.prepare(deadline)
        # Decidir o que desenhar tambem custa (culling e escolha de nivel sobre
        # centenas de milhares de linhas), entao tem fatia propria.
        planned = []
        yield lambda p, deadline: bool(
            planned.append(self._display.plan(vp, self.theme is DARK, self.devicePixelRatioF()))
            or True
        )
        geometry, markers = planned[0]
        yield from geometry
        if markers:
            yield lambda p, deadline: self._paint_markers(p, vp, markers)

    def _paint_base(self, painter, vp) -> bool:
        painter.fillRect(0, 0, vp.width, vp.height, self.theme.q("background"))
        self._paint_rasters(painter, vp)
        if self.show_grid:
            self._paint_grid(painter, vp)
        return True

    def _paint_rasters(self, painter, vp):
        for layer in self.ctx.rasters:
            if not layer.visible:
                continue
            try:
                layer.paint(painter, vp)
            except Exception as exc:  # um raster problematico nao pode matar o frame
                self.ctx.message(f"Falha ao desenhar raster: {exc}")
                layer.visible = False

    def _paint_grid(self, painter, vp):
        step = vp.nice_grid_step()
        vis = vp.visible_bbox()
        if step <= 0 or vis.width / step > MAX_GRID_LINES:
            return

        minor = QPen(self.theme.q("grid_minor"), 1)
        minor.setCosmetic(True)
        major = QPen(self.theme.q("grid_major"), 1)
        major.setCosmetic(True)

        x0 = math.floor(vis.minx / step) * step
        y0 = math.floor(vis.miny / step) * step
        n = 0
        x = x0
        while x <= vis.maxx and n < MAX_GRID_LINES:
            sx, _ = vp.world_to_screen(Vec2(x, 0))
            painter.setPen(major if abs(round(x / step)) % 5 == 0 else minor)
            painter.drawLine(QPointF(sx, 0), QPointF(sx, vp.height))
            x += step
            n += 1
        n = 0
        y = y0
        while y <= vis.maxy and n < MAX_GRID_LINES:
            _, sy = vp.world_to_screen(Vec2(0, y))
            painter.setPen(major if abs(round(y / step)) % 5 == 0 else minor)
            painter.drawLine(QPointF(0, sy), QPointF(vp.width, sy))
            y += step
            n += 1

    # ---------------- rotulos (texto, ponto, atributo, cota) ----------------

    def _paint_markers(self, painter, vp, entities) -> bool:
        """Desenha o que depende do tamanho da fonte em pixels, e so isso.

        A geometria dessas entidades ja veio da display list; aqui entra apenas o
        texto e o marcador de ponto, que nao podem ser cacheados em coordenadas
        de mundo porque o corpo da fonte e medido em pixels de tela.
        """
        doc = self.doc
        dark = self.theme is DARK
        painter.setRenderHint(QPainter.Antialiasing, True)
        font = QFont(painter.font())
        colors: dict[str, int] = {}
        for e in entities:
            if not e.is_alive:
                continue
            layer = e.dxf.get("layer", "0")
            color = e.dxf.get("color", 256)
            if color in (256, 0):
                aci = colors.get(layer)
                if aci is None:
                    aci = colors[layer] = doc.layer_color(layer)
            else:
                aci = color
            pen = QPen(aci_to_qcolor(aci, dark), 1.2)
            pen.setCosmetic(True)
            painter.setPen(pen)

            t = e.dxftype()
            if t == "POINT":
                self._paint_point_marker(painter, vp, e)
            elif t in TEXT_TYPES:
                self._paint_text_primitive(painter, vp, e, font)
            elif t == "INSERT" or t in DIMENSION_TYPES:
                self._paint_composite_text(painter, vp, e, font, centered=t in DIMENSION_TYPES)
        return True

    def _paint_point_marker(self, painter, vp, e):
        p = entity_insert_point(e)
        if p is None:
            return
        sx, sy = vp.world_to_screen(p)
        painter.drawLine(QPointF(sx - 4, sy), QPointF(sx + 4, sy))
        painter.drawLine(QPointF(sx, sy - 4), QPointF(sx, sy + 4))

    def _paint_composite_text(self, painter, vp, entity, font, centered: bool):
        """Texto que vive dentro de um bloco: ATTRIBs e o rotulo da cota."""
        for primitive in entity_primitives(entity):
            if primitive.dxftype() in TEXT_TYPES:
                self._paint_text_primitive(painter, vp, primitive, font, centered)

    def _paint_text_primitive(self, painter, vp, entity, font, centered: bool = False):
        p = entity_insert_point(entity)
        if p is None:
            return
        sx, sy = vp.world_to_screen(p)
        t = entity.dxftype()
        attr = "char_height" if t == "MTEXT" else "height"
        height = float(entity.dxf.get(attr, 1.0) or 1.0)
        px = vp.world_to_px(height)
        if px < 3:  # ilegivel: vira um tracinho
            painter.drawLine(QPointF(sx, sy), QPointF(sx + 5, sy))
            return
        font.setPixelSize(max(3, int(px)))
        painter.setFont(font)
        if t == "MTEXT":
            try:
                text = entity.plain_text()
            except AttributeError:
                text = entity.text
        else:
            text = entity.dxf.get("text", "")
        rotation = float(entity.dxf.get("rotation", 0.0) or 0.0)
        painter.save()
        painter.translate(sx, sy)
        painter.rotate(-rotation)
        if centered:
            # As cotas do ezdxf usam ponto de anexacao central para o MTEXT.
            half = max(30.0, len(str(text)) * px)
            painter.drawText(QRectF(-half, -px, half * 2, px * 2), Qt.AlignCenter, str(text))
        else:
            painter.drawText(QPointF(0, 0), str(text))
        painter.restore()

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
        vis = self.vp.visible_bbox()
        drawn = 0
        for e in sel:
            if drawn >= MAX_OUTLINES:
                break  # selecao enorme: o contorno tracejado nao pode custar o quadro
            box = self.doc.index._boxes.get(e.dxf.get("handle"))
            if box is not None and not box.intersects(vis):
                continue
            self._entity_outline(painter, e, tol)
            drawn += 1

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

    def _paint_vertex_focus(self, painter):
        """Realce do vertice navegado no painel de propriedades (Ctrl+1)."""
        focus = getattr(self.ctx, "vertex_focus", None)
        if focus is None or not focus.entity.is_alive:
            return
        x, y = self.vp.world_to_screen(focus.point)
        pen = QPen(QColor("#ffb347"), 2.2)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        r = 9
        painter.drawEllipse(QPointF(x, y), r, r)
        painter.drawLine(QPointF(x - r - 5, y), QPointF(x - r + 2, y))
        painter.drawLine(QPointF(x + r - 2, y), QPointF(x + r + 5, y))
        painter.drawLine(QPointF(x, y - r - 5), QPointF(x, y - r + 2))
        painter.drawLine(QPointF(x, y + r - 2), QPointF(x, y + r + 5))

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
        self.invalidate_scene()

    @property
    def show_grid(self) -> bool:
        return self._show_grid

    @show_grid.setter
    def show_grid(self, on: bool) -> None:
        # A grade faz parte da cena guardada: liga-la ou desliga-la obriga a
        # refazer o quadro, nao so a repintar o sobreposto.
        self._show_grid = bool(on)
        self.invalidate_scene()
