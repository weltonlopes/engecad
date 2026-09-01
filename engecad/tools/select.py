"""Selecao e edicao por grip.

A SelectTool e a ferramenta "ociosa": fica ativa sempre que nenhum comando
esta rodando, e e ela que da o comportamento padrao do CAD -- clicar para
escolher, arrastar para janela, arrastar um grip para esticar.
"""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QPen

from ..core.geometry import BBox, Vec2
from ..core.grips import Grip, drag_grip, entity_grips, nearest_grip
from ..core.picking import pick_all_at, pick_at, select_in_box
from ..render.styles import DARK
from .base import Tool

PICK_TOL_PX = 8.0
GRIP_TOL_PX = 7.0
DRAG_THRESHOLD_PX = 5.0
MAX_GRIP_ENTITIES = 60  # acima disso os grips viram poluicao visual


class PickHelper:
    """Logica de escolher por clique ou por janela, compartilhada.

    Usada pela SelectTool e pela fase de selecao das ferramentas de edicao,
    para as duas se comportarem exatamente igual.
    """

    def __init__(self, ctx):
        self.ctx = ctx
        self.anchor: Vec2 | None = None
        self.anchor_screen: QPointF | None = None
        self.cursor: Vec2 | None = None
        self.box_active = False
        #: True depois de um clique (sem arrasto) em area vazia: a janela
        #: elastica fica acompanhando o mouse e so termina no proximo clique,
        #: como no AutoCAD.
        self.awaiting_confirm = False

    @property
    def crossing(self) -> bool:
        """Arrastar para a esquerda = captura (pega tudo que encostar)."""
        if self.anchor is None or self.cursor is None:
            return False
        return self.cursor.x < self.anchor.x

    def box(self) -> BBox:
        if self.anchor is None or self.cursor is None:
            return BBox()
        return BBox(
            min(self.anchor.x, self.cursor.x),
            min(self.anchor.y, self.cursor.y),
            max(self.anchor.x, self.cursor.x),
            max(self.anchor.y, self.cursor.y),
        )

    def begin(self, p: Vec2, screen: QPointF | None) -> None:
        self.anchor = p
        self.anchor_screen = screen
        self.cursor = p
        self.box_active = False

    def move(self, p: Vec2, screen: QPointF | None) -> None:
        self.cursor = p
        if self.anchor is None or screen is None or self.anchor_screen is None:
            return
        d = screen - self.anchor_screen
        if (d.x() ** 2 + d.y() ** 2) ** 0.5 > DRAG_THRESHOLD_PX:
            self.box_active = True

    def finish(self, p: Vec2, viewport) -> tuple[list, str]:
        """Devolve (entidades, modo) e encerra a operacao de escolha."""
        self.cursor = p
        doc = self.ctx.doc
        if self.box_active:
            crossing = self.crossing
            found = select_in_box(
                doc, self.box(), crossing, sagitta=viewport.flatten_tolerance(0.5)
            )
            mode = "captura" if crossing else "janela"
        else:
            tol = viewport.px_to_world(PICK_TOL_PX)
            hit = pick_at(doc, p, tol)
            found = [hit] if hit else []
            mode = "clique"
        self.reset()
        return found, mode

    def reset(self) -> None:
        self.anchor = None
        self.anchor_screen = None
        self.box_active = False
        self.awaiting_confirm = False

    def paint(self, painter, vp) -> None:
        if not self.box_active or self.anchor is None or self.cursor is None:
            return
        x0, y0 = vp.world_to_screen(self.anchor)
        x1, y1 = vp.world_to_screen(self.cursor)
        rect = QRectF(min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0))
        crossing = self.crossing
        color = QColor(DARK.selection if not crossing else "#4bd66e")
        pen = QPen(color, 1.2)
        pen.setStyle(Qt.DashLine if crossing else Qt.SolidLine)
        pen.setCosmetic(True)
        fill = QColor(color)
        fill.setAlpha(30)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRect(rect)
        painter.setBrush(Qt.NoBrush)


class SelectTool(Tool):
    """Ferramenta ociosa: selecao e grips."""

    name = "SELECT"
    is_idle = True
    prompt = ""

    def __init__(self, ctx):
        super().__init__(ctx)
        self.pick = PickHelper(ctx)
        self.hover = None
        self.hover_grip: Grip | None = None
        self._additive = False
        self._grips: list[Grip] = []
        self._grips_key: tuple | None = None

    # ---------------- grips ----------------

    def visible_grips(self) -> list[Grip]:
        """Grips da selecao, com cache.

        E consultado duas vezes por movimento do mouse -- para achar o grip sob o
        cursor e para desenhar. Extrair os grips achata a geometria de cada
        entidade selecionada; refazer isso a cada movimento pesava mais que o
        teste de acerto.
        """
        sel = self.ctx.selection
        key = (sel.revision, self.ctx.doc.geometry_revision)
        if key == self._grips_key:
            return self._grips
        items = sel.items
        out: list[Grip] = []
        if len(items) <= MAX_GRIP_ENTITIES:
            for e in items:
                out.extend(entity_grips(e))
        self._grips_key = key
        self._grips = out
        return out

    # ---------------- entrada ----------------

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        vp = self.ctx.viewport
        screen = event.position() if event is not None else None
        if self.pick.anchor is not None:
            self.pick.move(world, screen)
            return
        tol = vp.px_to_world(PICK_TOL_PX)
        # O canvas ja consultou o maior raio para o snap. Reusar o mesmo probe
        # elimina uma segunda passagem pelo indice e mantem o hover sob a
        # posicao fisica do cursor, nao sob o ponto para o qual o snap puxou.
        probe = getattr(self.ctx.canvas, "_pointer_probe", None)
        hover_world = probe.point if probe is not None else world
        self.hover = pick_at(self.ctx.doc, hover_world, tol, probe=probe)
        self.hover_grip = nearest_grip(
            self.visible_grips(), hover_world, vp.px_to_world(GRIP_TOL_PX)
        )

    def on_click(self, world: Vec2, event=None) -> None:
        # Segundo clique de uma janela elastica pendente: confirma a selecao
        # no ponto atual, exatamente como soltar o botao faria no arrasto.
        if self.pick.awaiting_confirm:
            found, mode = self.pick.finish(world, self.ctx.viewport)
            self._apply_pick(found, mode)
            return

        mods = event.modifiers() if event is not None else Qt.NoModifier
        self._additive = bool(mods & (Qt.ShiftModifier | Qt.ControlModifier))

        # Grip sob o cursor: entra em modo de esticar. Com Shift o usuario
        # esta somando/tirando da selecao, entao o grip nao interfere.
        if not self._additive:
            grip = nearest_grip(
                self.visible_grips(), world, self.ctx.viewport.px_to_world(GRIP_TOL_PX)
            )
            if grip is not None:
                self.ctx.set_tool(GripEditTool(self.ctx, grip))
                return

        self.pick.begin(world, event.position() if event is not None else None)

    def on_release(self, world: Vec2, event=None) -> None:
        if self.pick.anchor is None:
            return
        if self.pick.box_active:
            # Arrasto classico: soltar o botao ja termina a janela.
            found, mode = self.pick.finish(world, self.ctx.viewport)
            self._apply_pick(found, mode)
            return

        # Botao solto sem arrastar. Se ha algo sob o cursor, selecao
        # imediata de clique. Senao, como no AutoCAD, o primeiro clique so
        # abre a janela elastica -- ela fica seguindo o mouse e o proximo
        # clique (em on_click) e que confirma.
        tol = self.ctx.viewport.px_to_world(PICK_TOL_PX)
        hit = pick_at(self.ctx.doc, world, tol)
        if hit is not None:
            self.pick.reset()
            self._apply_pick([hit], "clique")
            return
        self.pick.cursor = world
        self.pick.box_active = True
        self.pick.awaiting_confirm = True
        self.ctx.refresh()

    def _apply_pick(self, found: list, mode: str) -> None:
        sel = self.ctx.selection
        if not found:
            if not self._additive:
                sel.clear()
        elif self._additive:
            sel.toggle(found)
        else:
            sel.set(found)
        if found or mode != "clique":
            self.ctx.message(sel.summary())
        self.ctx.refresh()

    def on_right_click(self, world: Vec2, event=None) -> None:
        # botao direito com selecao ativa nao deve encerrar a ferramenta ociosa
        self.pick.reset()
        self.ctx.selection.clear()
        self.ctx.refresh()

    def deactivate(self) -> None:
        self.pick.reset()

    def on_key(self, key, modifiers=None) -> bool:
        if key == Qt.Key_Escape:
            self.pick.reset()
            self.ctx.selection.clear()
            self.ctx.refresh()
            return True
        if key == Qt.Key_Delete:
            sel = list(self.ctx.selection)
            if sel:
                self.ctx.doc.delete(sel)
                self.ctx.selection.clear()
                self.ctx.message(f"{len(sel)} objeto(s) apagado(s)")
            return True
        return False

    def cycle_under_cursor(self, world: Vec2) -> None:
        """Alterna entre entidades sobrepostas no mesmo ponto."""
        tol = self.ctx.viewport.px_to_world(PICK_TOL_PX)
        hits = pick_all_at(self.ctx.doc, world, tol)
        if len(hits) < 2:
            return
        sel = self.ctx.selection
        current = sel.items[0] if len(sel) == 1 else None
        idx = hits.index(current) + 1 if current in hits else 0
        sel.set([hits[idx % len(hits)]])
        self.ctx.message(sel.summary())

    # ---------------- desenho ----------------

    def paint(self, painter, vp) -> None:
        self.pick.paint(painter, vp)


class GripEditTool(Tool):
    """Arrasta um grip. Termina no clique seguinte; Esc cancela."""

    name = "GRIP"
    prompt = "Novo ponto do grip:"

    def __init__(self, ctx, grip: Grip):
        super().__init__(ctx)
        self.grip = grip
        self.cursor: Vec2 | None = grip.point

    def activate(self) -> None:
        kind = {
            "move": "Mover entidade",
            "vertex": "Mover vertice",
            "radius": "Alterar raio",
            "angle": "Alterar angulo",
        }.get(self.grip.kind, "Editar")
        self.set_prompt(f"{kind} - novo ponto:")

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world

    def on_text(self, text: str) -> bool:
        from ..core.coordinput import parse_coordinate

        p = parse_coordinate(text, self.grip.point)
        if p is None:
            return False
        self.apply(p)
        return True

    def on_click(self, world: Vec2, event=None) -> None:
        self.apply(world)

    def apply(self, target: Vec2) -> None:
        entity = self.grip.entity
        with self.doc.editing([entity], "esticar"):
            drag_grip(entity, self.grip, target)
        self.ctx.message(f"Grip movido para {target.x:.3f}, {target.y:.3f}")
        self.finish()

    def cancel(self) -> None:
        super().cancel()

    def paint(self, painter, vp) -> None:
        if self.cursor is None:
            return
        pen = QPen(DARK.q("preview"), 1.2)
        pen.setStyle(Qt.DashLine)
        pen.setCosmetic(True)
        painter.setPen(pen)
        painter.drawLine(
            QPointF(*vp.world_to_screen(self.grip.point)),
            QPointF(*vp.world_to_screen(self.cursor)),
        )
