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
import os

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush,
    QColor,
    QCursor,
    QFont,
    QFontMetricsF,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
    QStaticText,
    QSurfaceFormat,
)
from PySide6.QtWidgets import QWidget

try:
    from PySide6.QtOpenGLWidgets import QOpenGLWidget
except ImportError:  # instalacoes Qt deliberadamente sem o modulo OpenGL
    QOpenGLWidget = None

from ..core.dimensions import DIMENSION_TYPES
from ..core.entities import (
    MAX_INTERACTIVE_PROXY_BYTES,
    POINT_LIKE,
    entity_insert_point,
    entity_point_lists,
    entity_primitives,
)
from ..core.geometry import Vec2, decimate
from ..core.picking import probe_at
from .displaylist import DisplayList
from .framecache import FrameCache
from .styles import DARK, aci_to_qcolor

# Um entalhe comum da roda aproxima/afasta 35%. O valor anterior (18%) exigia
# muitas voltas para navegar entre a vista geral e o detalhe de um DXF extenso.
ZOOM_STEP = 1.35
CROSSHAIR_GAP = 7  # px do quadradinho central
CROSSHAIR_RADIUS = 28  # alcance de cada braco a partir do centro
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
SNAP_WARM_SLICE_MS = 4.0
MAX_OUTLINES = 2_000  # contornos de selecao/realce desenhados por quadro
MAX_OUTLINE_VERTS = 20_000
POINTER_INTERVAL_MS = 8  # no maximo 125 resolucoes de snap/hover por segundo
TEXT_TYPES = {"TEXT", "MTEXT", "ATTRIB", "ATTDEF"}
MAX_STATIC_TEXTS = 4_096


def _use_opengl_widget() -> bool:
    """Seleciona OpenGL em tela real e conserva o QWidget em testes/headless."""
    requested = os.environ.get("ENGECAD_RENDERER", "auto").strip().lower()
    if requested in {"cpu", "qpainter", "software"} or QOpenGLWidget is None:
        return False
    platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower()
    if requested != "opengl" and platform in {"offscreen", "minimal", "minimalegl"}:
        return False
    return True


_OPENGL_CANVAS = _use_opengl_widget()
_CanvasBase = QOpenGLWidget if _OPENGL_CANVAS else QWidget
if _OPENGL_CANVAS:
    from .gl_renderer import BUILD_SLICE_MS, GpuGeometry, OpenGLRenderer
else:
    BUILD_SLICE_MS = 0.0
    GpuGeometry = OpenGLRenderer = None


class _PointerAt:
    """Posicao do cursor com a cara de um evento de mouse.

    O tratamento de um movimento e adiado ate o fim da rajada de eventos, e um
    QMouseEvent nao sobrevive a isso. As ferramentas so consomem `position()`.
    """

    __slots__ = ("_pos",)

    def __init__(self, pos):
        self._pos = pos

    def position(self):
        return self._pos


class _OverlayWidget(QWidget):
    """Camada Qt transparente acima do framebuffer OpenGL.

    Cursor, snap, grips e previas mudam centenas de vezes por segundo, mas a
    geometria quase sempre permanece identica. Um widget filho permite mover
    esses elementos sem agendar outro ``paintGL`` para todos os VBOs da cena.
    """

    def __init__(self, canvas):
        super().__init__(canvas)
        self._canvas = canvas
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAutoFillBackground(False)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setCompositionMode(QPainter.CompositionMode_Source)
        painter.fillRect(event.rect(), Qt.transparent)
        painter.setCompositionMode(QPainter.CompositionMode_SourceOver)
        self._canvas._paint_overlays(painter)
        painter.end()


class CadCanvas(_CanvasBase):
    coordinateMoved = Signal(object)  # Vec2 no CRS do projeto
    snapChanged = Signal(object)  # SnapResult | None
    viewChanged = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        if _OPENGL_CANVAS:
            # Solicita MSAA no framebuffer do QOpenGLWidget. A geometria chega
            # como GL_LINES, portanto o antialiasing precisa acontecer nas
            # amostras do framebuffer e nao no QPainter de sobreposicao.
            surface = QSurfaceFormat(self.format())
            surface.setSamples(4)
            surface.setDepthBufferSize(0)
            surface.setStencilBufferSize(0)
            surface.setSwapInterval(0)
            self.setFormat(surface)
            self.setUpdateBehavior(QOpenGLWidget.UpdateBehavior.NoPartialUpdate)
        self.ctx = ctx
        ctx.canvas = self
        self.theme = DARK
        self._show_grid = True
        self._show_crosshair = True

        self._cursor_screen: QPointF | None = None
        self._cursor_world: Vec2 | None = None
        self._snap = None
        self._panning = False
        self._pan_anchor: QPointF | None = None

        self._display = DisplayList(ctx.doc)
        self._frame = FrameCache()
        self._gpu_geometry = GpuGeometry(ctx.doc) if _OPENGL_CANVAS else None
        self._gpu_renderer = None
        self._gpu_error: str | None = None
        self._gpu_build = QTimer(self)
        self._gpu_build.setSingleShot(True)
        self._gpu_build.setInterval(0)
        self._gpu_build.timeout.connect(self._advance_gpu)
        if self._gpu_geometry is not None:
            self._gpu_build.start()
        self._snap_warm = QTimer(self)
        self._snap_warm.setSingleShot(True)
        self._snap_warm.setInterval(0)
        self._snap_warm.timeout.connect(self._advance_snap_warm)
        self._snap_warm_revision = -1
        if self._gpu_geometry is None:
            self._start_snap_warm()
        self._interactive = False
        self._sel_key: tuple | None = None
        self._sel_outlines: list = []
        self._sel_grips: list = []
        self._refine = QTimer(self)
        self._refine.setSingleShot(True)
        self._refine.timeout.connect(self._finish_gesture)
        # Continua um redesenho fatiado na proxima volta do laco de eventos.
        self._advance = QTimer(self)
        self._advance.setSingleShot(True)
        self._advance.setInterval(0)
        self._advance.timeout.connect(self.update)
        # Junta a rajada de eventos de mouse numa resolucao so.
        self._pointer_dirty = False
        self._pointer_at: tuple[float, float] | None = None
        self._pointer_probe = None
        self._pointer = QTimer(self)
        self._pointer.setSingleShot(True)
        self._pointer.setInterval(POINTER_INTERVAL_MS)
        self._pointer.timeout.connect(self._resolve_pointer)
        self._hover_key: tuple | None = None
        self._hover_shapes: list = []
        self._static_texts: dict[tuple[str, int, str], tuple[QStaticText, float]] = {}
        ctx.documentReplaced.connect(self._on_document_replaced)
        # Qualquer mutacao do documento (geometria, cor ou visibilidade de
        # camada) invalida o quadro guardado; a display list so reconstroi os
        # tiles que a entidade alterada tocava.
        ctx.documentChanged.connect(self.invalidate_scene)
        ctx.rastersChanged.connect(self.invalidate_scene)

        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        self.setAutoFillBackground(False)
        self._overlay = _OverlayWidget(self) if _OPENGL_CANVAS else None
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())
            self._overlay.show()
            self._overlay.raise_()
        self._apply_native_cursor()

    def update(self, *args) -> None:
        """Atualiza a cena e mantem o overlay sincronizado com atualizacoes externas."""
        super().update(*args)
        overlay = getattr(self, "_overlay", None)
        if overlay is not None:
            overlay.update(*args)

    # ---------------- invalidacao da cena ----------------

    def _on_document_replaced(self) -> None:
        self._display = DisplayList(self.ctx.doc)
        self._snap_warm.stop()
        self._snap_warm_revision = -1
        if self._gpu_geometry is not None:
            self._gpu_geometry.start(self.ctx.doc)
            self._gpu_build.start()
        else:
            self._start_snap_warm()
        self._pointer_probe = None
        self._hover_key = None
        self._hover_shapes = []
        self.invalidate_scene()

    def invalidate_scene(self) -> None:
        """Descarta o quadro guardado. A geometria em si so e refeita se mudou."""
        self._frame.invalidate()
        if self._gpu_geometry is not None and self._gpu_geometry.ensure_current():
            self._gpu_build.start()
        self._update_scene_and_overlay()

    def _update_overlay(self) -> None:
        """Invalida somente a camada interativa quando ela esta separada."""
        if self._overlay is not None:
            self._overlay.update()
        else:
            self.update()

    def _update_scene_and_overlay(self) -> None:
        self.update()

    def _advance_gpu(self) -> None:
        """Prepara VBOs sem monopolizar a thread da interface."""
        geometry = self._gpu_geometry
        if geometry is None:
            return
        if not geometry.advance(BUILD_SLICE_MS):
            self._gpu_build.start()
        else:
            self._start_snap_warm()
        self.update()

    def _start_snap_warm(self) -> None:
        engine = self.ctx.snap
        revision = self.doc.geometry_revision
        if engine is None or self._snap_warm_revision == revision:
            return
        self._snap_warm_revision = revision
        engine.start_prewarm()
        self._snap_warm.start()

    def _advance_snap_warm(self) -> None:
        engine = self.ctx.snap
        if engine is not None and not engine.prewarm_step(SNAP_WARM_SLICE_MS):
            self._snap_warm.start()

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

    def _apply_native_cursor(self) -> None:
        """Instala a mira como cursor do sistema, fora do ciclo de pintura Qt."""
        if self._panning:
            self.setCursor(Qt.ClosedHandCursor)
            return
        if not self.show_crosshair:
            self.setCursor(Qt.ArrowCursor)
            return

        margin = CROSSHAIR_RADIUS + 4
        size = margin * 2 + 1
        center = margin
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing, False)
        pen = QPen(self.theme.q("crosshair"), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        radius = CROSSHAIR_RADIUS
        gap = CROSSHAIR_GAP
        painter.drawLine(QPointF(center - radius, center), QPointF(center - gap, center))
        painter.drawLine(QPointF(center + gap, center), QPointF(center + radius, center))
        painter.drawLine(QPointF(center, center - radius), QPointF(center, center - gap))
        painter.drawLine(QPointF(center, center + gap), QPointF(center, center + radius))
        painter.setPen(QPen(self.theme.q("cursor_box"), 1))
        painter.drawRect(
            QRectF(
                center - PICKBOX,
                center - PICKBOX,
                2 * PICKBOX,
                2 * PICKBOX,
            )
        )
        painter.end()
        self.setCursor(QCursor(pixmap, center, center))

    # ---------------- eventos de janela ----------------

    def resizeEvent(self, ev):
        self.vp.resize(self.width(), self.height())
        self._frame.invalidate()
        super().resizeEvent(ev)
        if self._overlay is not None:
            self._overlay.setGeometry(self.rect())
            self._overlay.raise_()

    # ---------------- ponteiro ----------------

    def _resolve_pointer(self) -> None:
        """Resolve snap, realce e previa da ferramenta na posicao mais recente."""
        self._pointer.stop()
        if not self._pointer_dirty or self._cursor_world is None:
            return
        self._pointer_dirty = False
        # Um mouse manda posicoes repetidas e sub-pixel; nenhuma delas muda o
        # snap, o realce nem a mira desenhada.
        pos = self._cursor_screen
        last = self._pointer_at
        if last is not None and pos is not None:
            if abs(pos.x() - last[0]) < 1.0 and abs(pos.y() - last[1]) < 1.0:
                return
        self._pointer_at = (pos.x(), pos.y()) if pos is not None else None
        tool = self.ctx.tool
        exclude = tool.snap_exclude() if tool is not None else ()
        radius = self.vp.px_to_world(self.ctx.snap.pixel_radius)
        self._pointer_probe = probe_at(self.doc, self._cursor_world, radius, exclude)
        self._snap = self.ctx.snap.snap(
            self._cursor_world, self.vp, exclude=exclude, probe=self._pointer_probe
        )
        self.snapChanged.emit(self._snap)
        self.coordinateMoved.emit(self.effective_point())
        if tool is not None:
            tool.on_mouse_move(self.effective_point(), _PointerAt(self._cursor_screen))
        self._update_overlay()

    # ---------------- mouse ----------------

    def mouseMoveEvent(self, ev):
        pos = ev.position()
        if self._panning and self._pan_anchor is not None:
            d = pos - self._pan_anchor
            self.vp.pan_screen(d.x(), d.y())
            self._pan_anchor = pos
            self._interactive = True
            self._emit_view_changed()
            self._update_scene_and_overlay()
            return

        self._cursor_screen = pos
        self._cursor_world = self.vp.screen_to_world(pos.x(), pos.y())
        # Um mouse reporta ate mil posicoes por segundo; resolver snap e realce
        # em cada uma custa mais do que o intervalo entre elas, e a fila cresce
        # sem parar -- e a sensacao de arrasto. Aqui so anotamos onde o cursor
        # esta; o trabalho de verdade acontece uma vez por volta do laco de
        # eventos, ja com a ultima posicao.
        self._pointer_dirty = True
        if not self._pointer.isActive():
            self._pointer.start()

    def mousePressEvent(self, ev):
        if ev.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_anchor = ev.position()
            self.setCursor(Qt.ClosedHandCursor)
            return
        # Um clique nao pode usar um snap de uma posicao anterior.
        self._resolve_pointer()
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
        self._update_overlay()

    def mouseReleaseEvent(self, ev):
        if ev.button() == Qt.MiddleButton and self._panning:
            self._panning = False
            self._pan_anchor = None
            self._apply_native_cursor()
            if self._interactive:  # o gesto acabou: refina agora
                self._finish_gesture()
            return
        if ev.button() == Qt.LeftButton:
            self._resolve_pointer()
            tool = self.ctx.tool
            p = self.effective_point()
            if tool is not None and p is not None:
                tool.on_release(p, ev)
                self._update_overlay()

    def wheelEvent(self, ev):
        delta = ev.angleDelta().y()
        if delta == 0:
            return
        # 120 unidades correspondem a um entalhe de uma roda tradicional.
        # Respeitar a magnitude mantem trackpads/rodas de alta resolucao suaves,
        # enquanto o expoente torna varios entalhes acumulados consistentes.
        steps = max(-4.0, min(4.0, delta / 120.0))
        factor = ZOOM_STEP**steps
        pos = ev.position()
        self.vp.zoom_at_screen(pos.x(), pos.y(), factor)
        self._cursor_screen = pos
        self._cursor_world = self.vp.screen_to_world(pos.x(), pos.y())
        self._interactive = True
        # O raio de captura muda com o zoom: o snap tem de ser refeito, mas pode
        # esperar o fim da rajada da roda como qualquer movimento.
        self._pointer_dirty = True
        if not self._pointer.isActive():
            self._pointer.start()
        self._emit_view_changed()
        self._update_scene_and_overlay()

    def keyPressEvent(self, ev):
        tool = self.ctx.tool
        if tool is not None and tool.on_key(ev.key(), ev.modifiers()):
            self._update_overlay()
            return
        if ev.key() == Qt.Key_Escape:
            self.ctx.cancel_tool()
            self._update_overlay()
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
        self._pointer.stop()
        self._pointer_dirty = False
        self._cursor_screen = None
        self._snap = None
        self._pointer_probe = None
        self._update_overlay()
        super().leaveEvent(ev)

    def _emit_view_changed(self):
        # Zoom e pan mudam o raio de captura e o que esta sob o cursor, mesmo com
        # o mouse parado: o filtro de posicao repetida nao vale mais.
        self._pointer_at = None
        self.viewChanged.emit()
        self.ctx.viewChanged.emit()

    # ---------------- desenho ----------------

    def paintEvent(self, ev):
        if _OPENGL_CANVAS:
            # QOpenGLWidget controla o FBO e chama paintGL. Sobrescrever o
            # paintEvent sem este desvio impediria a composicao do OpenGL.
            return super().paintEvent(ev)
        painter = QPainter(self)
        self._draw_scene(painter)

        self._paint_overlays(painter)
        painter.end()

    def _paint_overlays(self, painter) -> None:
        """Desenha a camada pequena e dinamica comum aos dois backends."""

        painter.setRenderHint(QPainter.Antialiasing, False)
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

    # ---------------- backend OpenGL ----------------

    def initializeGL(self) -> None:
        if not _OPENGL_CANVAS:
            return
        try:
            self._gpu_renderer = OpenGLRenderer(self.context())
            self.context().aboutToBeDestroyed.connect(self._destroy_gl)
        except (RuntimeError, AttributeError) as exc:
            self._gpu_error = str(exc)
            self._gpu_renderer = None
            self.ctx.message(f"OpenGL indisponivel; usando QPainter: {exc}")

    def paintGL(self) -> None:
        """Compoe base Qt, vetores GPU e overlays Qt no mesmo framebuffer."""
        renderer = self._gpu_renderer
        geometry = self._gpu_geometry
        if renderer is None or geometry is None:
            self._paint_cpu_on_current_surface()
            return

        renderer.request(geometry.snapshot)
        try:
            complete = renderer.upload_step()
        except RuntimeError as exc:
            self._gpu_error = str(exc)
            self._gpu_renderer = None
            self.ctx.message(f"Falha ao enviar geometria para a GPU: {exc}")
            self._paint_cpu_on_current_surface()
            return
        if not complete:
            self.update()

        snapshot = renderer.active_snapshot
        if geometry.snapshot is None:
            # Troca de documento: jamais mostre os VBOs do arquivo anterior.
            snapshot = None
        if snapshot is None:
            # O primeiro upload e incremental. O cache CPU mantem o arquivo
            # utilizavel enquanto os VBOs sao montados.
            self._paint_cpu_on_current_surface()
            return

        dpr = self.devicePixelRatioF()
        renderer.clear(self.theme.q("background"), self.vp, dpr)
        painter = QPainter(self)
        self._paint_rasters(painter, self.vp)
        if self.show_grid:
            self._paint_grid(painter, self.vp)
        geometry.paint_fills(painter, snapshot, self.vp, self.theme is DARK)
        painter.beginNativePainting()
        renderer.draw(
            self.vp,
            self.doc,
            self.theme is DARK,
            dpr,
        )
        painter.endNativePainting()
        geometry.paint_placeholders(painter, snapshot, self.vp, self.theme is DARK)
        markers = geometry.visible_markers(snapshot, self.vp)
        if markers:
            self._paint_markers(painter, self.vp, markers)
        painter.end()

    def _destroy_gl(self) -> None:
        renderer = self._gpu_renderer
        if renderer is None:
            return
        self.makeCurrent()
        renderer.destroy()
        self.doneCurrent()

    def _paint_cpu_on_current_surface(self) -> None:
        painter = QPainter(self)
        self._draw_scene(painter)
        if self._overlay is None:
            self._paint_overlays(painter)
        painter.end()

    @property
    def renderer_name(self) -> str:
        if not _OPENGL_CANVAS:
            return "QPainter"
        if self._gpu_renderer is None:
            return "QPainter (fallback OpenGL)"
        return "OpenGL/VBO"

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
        if not self.doc.is_model_layout:
            return
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
        pens: dict[int, QPen] = {}
        last_aci = None
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
            if aci != last_aci:
                pen = pens.get(aci)
                if pen is None:
                    pen = QPen(aci_to_qcolor(aci, dark), 1.2)
                    pen.setCosmetic(True)
                    pens[aci] = pen
                painter.setPen(pen)
                last_aci = aci

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
        rendered, ascent = self._static_text(str(text), font)
        painter.save()
        painter.translate(sx, sy)
        painter.rotate(-rotation)
        if centered:
            # As cotas do ezdxf usam ponto de anexacao central para o MTEXT.
            size = rendered.size()
            painter.drawStaticText(QPointF(-size.width() * 0.5, -size.height() * 0.5), rendered)
        else:
            # drawText usa a origem como baseline; QStaticText usa topo/esquerda.
            painter.drawStaticText(QPointF(0, -ascent), rendered)
        painter.restore()

    def _static_text(self, text: str, font: QFont) -> tuple[QStaticText, float]:
        """Cacheia layout e glifos; pan nao volta a analisar cada string DXF."""
        key = (text, font.pixelSize(), font.family())
        hit = self._static_texts.get(key)
        if hit is not None:
            return hit
        if len(self._static_texts) >= MAX_STATIC_TEXTS:
            self._static_texts.clear()
        rendered = QStaticText(text)
        rendered.setPerformanceHint(QStaticText.AggressiveCaching)
        rendered.prepare(font=font)
        hit = (rendered, QFontMetricsF(font).ascent())
        self._static_texts[key] = hit
        return hit

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
        key = (
            e.dxf.get("handle"),
            self.doc.geometry_revision,
            self.vp.center.x,
            self.vp.center.y,
            self.vp.scale,
            self.vp.width,
            self.vp.height,
        )
        if key != self._hover_key:
            self._hover_shapes = self._outline_shapes(e, self.vp.flatten_tolerance(0.65))
            self._hover_key = key
        for shape in self._hover_shapes:
            if isinstance(shape, QRectF):
                painter.drawRect(shape)
            else:
                painter.drawPolyline(shape)

    def _selection_key(self) -> tuple:
        vp = self.vp
        sel = self.ctx.selection
        return (
            vp.center.x,
            vp.center.y,
            vp.scale,
            vp.width,
            vp.height,
            sel.revision if sel is not None else 0,
            self.doc.geometry_revision,
            self.ctx.tool,  # trocar de ferramenta muda quais grips aparecem
        )

    def _selection_shapes(self) -> tuple[list, list]:
        """Contornos e grips da selecao, ja em coordenadas de tela.

        Nada disso muda quando o mouse anda -- so quando a selecao, a vista ou a
        geometria mudam. Refazer o achatamento a cada movimento fazia uma selecao
        de 200 entidades custar 6 ms por evento, e os eventos chegam mais rapido
        do que isso.
        """
        key = self._selection_key()
        if key == self._sel_key:
            return self._sel_outlines, self._sel_grips

        vp = self.vp
        sel = self.ctx.selection
        outlines: list = []
        if sel is not None and len(sel._items):
            tol = vp.flatten_tolerance(0.4)
            vis = vp.visible_bbox()
            boxes = self.doc.index._boxes
            for e in sel:
                if len(outlines) >= MAX_OUTLINES:
                    break  # selecao enorme: o tracejado nao pode custar o quadro
                box = boxes.get(e.dxf.get("handle"))
                if box is not None and not box.intersects(vis):
                    continue
                outlines.extend(self._outline_shapes(e, tol))

        tool = self.ctx.tool
        grips: list = []
        if tool is not None and tool.is_idle:
            for g in tool.visible_grips():
                x, y = vp.world_to_screen(g.point)
                grips.append((g, x, y))

        self._sel_key = key
        self._sel_outlines = outlines
        self._sel_grips = grips
        return outlines, grips

    def _outline_shapes(self, entity, tol) -> list:
        """Formas de tela que contornam a entidade: poligonais ou um quadradinho."""
        vp = self.vp
        if (
            entity.dxftype() == "ACAD_PROXY_ENTITY"
            and len(entity.proxy_graphic or b"") > MAX_INTERACTIVE_PROXY_BYTES
        ):
            box = self.doc.index._boxes.get(entity.dxf.get("handle"))
            if box is None or box.is_empty:
                return []
            x0, y1 = vp.world_to_screen_xy(box.minx, box.miny)
            x1, y0 = vp.world_to_screen_xy(box.maxx, box.maxy)
            return [QRectF(QPointF(x0, y0), QPointF(x1, y1)).normalized()]
        if entity.dxftype() in POINT_LIKE:
            p = entity_insert_point(entity)
            if p is None:
                return []
            x, y = vp.world_to_screen(p)
            return [QRectF(x - 5, y - 5, 10, 10)]
        out = []
        for poly in entity_point_lists(entity, tol):
            if len(poly) < 2:
                continue
            if len(poly) > 8:
                poly = decimate(poly, tol)
            if len(poly) > MAX_OUTLINE_VERTS:
                stride = math.ceil((len(poly) - 1) / (MAX_OUTLINE_VERTS - 1))
                poly = [*poly[::stride], poly[-1]]
            out.append(QPolygonF([QPointF(*vp.world_to_screen_xy(x, y)) for x, y in poly]))
        return out

    def _paint_selection(self, painter):
        outlines, _ = self._selection_shapes()
        if not outlines:
            return
        pen = QPen(self.theme.q("selection"), 1.8)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        for shape in outlines:
            if isinstance(shape, QRectF):
                painter.drawRect(shape)
            else:
                painter.drawPolyline(shape)

    def _paint_grips(self, painter):
        tool = self.ctx.tool
        if tool is None or not tool.is_idle:
            return
        _, grips = self._selection_shapes()
        if not grips:
            return
        hovered = getattr(tool, "hover_grip", None)
        base = QColor(self.theme.selection)
        hot = QColor("#ff8c1a")
        pen = QPen(base.darker(150), 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.setBrush(QBrush(base))
        hot_rect = None
        for g, x, y in grips:
            if (
                hovered is not None
                and hovered.entity is g.entity
                and hovered.kind == g.kind
                and hovered.index == g.index
            ):
                hot_rect = QRectF(x - 6, y - 6, 12, 12)
                continue
            painter.drawRect(QRectF(x - 4.5, y - 4.5, 9, 9))
        if hot_rect is not None:
            painter.setBrush(QBrush(hot))
            painter.drawRect(hot_rect)
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

    # ---------------- utilidades de vista ----------------

    def zoom_extents(self):
        self.ctx.zoom_extents()
        self._update_scene_and_overlay()

    def set_theme(self, theme):
        self.theme = theme
        self._apply_native_cursor()
        self.invalidate_scene()

    @property
    def show_crosshair(self) -> bool:
        return self._show_crosshair

    @show_crosshair.setter
    def show_crosshair(self, on: bool) -> None:
        self._show_crosshair = bool(on)
        if hasattr(self, "theme"):
            self._apply_native_cursor()

    @property
    def show_grid(self) -> bool:
        return self._show_grid

    @show_grid.setter
    def show_grid(self, on: bool) -> None:
        # A grade faz parte da cena guardada: liga-la ou desliga-la obriga a
        # refazer o quadro, nao so a repintar o sobreposto.
        self._show_grid = bool(on)
        self.invalidate_scene()
