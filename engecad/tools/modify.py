"""Ferramentas de edicao: mover, copiar, girar, espelhar, escalar, paralela, apagar.

Todas seguem o fluxo do AutoCAD: se ja houver selecao, agem sobre ela; se nao,
entram numa fase de selecao (clique ou janela) encerrada com Enter.
"""

from __future__ import annotations

import math

from ezdxf.math import Matrix44
from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QPen

from ..core.coordinput import parse_coordinate
from ..core.entities import entity_polylines
from ..core.geometry import Vec2
from ..core.offset import create_offset
from ..render.styles import DARK
from .base import Tool
from .select import PICK_TOL_PX, PickHelper


def _pen(color, width=1.2, style=Qt.SolidLine) -> QPen:
    p = QPen(color, width)
    p.setStyle(style)
    p.setCosmetic(True)
    return p


def _ghost(painter, vp, entities, matrix, sagitta):
    """Desenha a previa das entidades ja transformadas."""
    from PySide6.QtGui import QPolygonF

    painter.setPen(_pen(DARK.q("preview"), 1.2, Qt.DashLine))
    for e in entities:
        for poly in entity_polylines(e, sagitta):
            pts = []
            for p in poly:
                v = matrix.transform((p.x, p.y, 0))
                pts.append(QPointF(*vp.world_to_screen_xy(v.x, v.y)))
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))


class ModifyTool(Tool):
    """Base das ferramentas que agem sobre uma selecao."""

    needs_points = 2
    prompt_select = "Selecione os objetos e tecle Enter:"
    prompt_points = ("Ponto base:", "Segundo ponto:")

    def __init__(self, ctx):
        super().__init__(ctx)
        self.entities = list(ctx.selection)
        self.points: list[Vec2] = []
        self.cursor: Vec2 | None = None
        self.pick = PickHelper(ctx)
        self.phase = "points" if self.entities else "select"

    # ---------------- prompts ----------------

    def activate(self) -> None:
        self.update_prompt()

    def update_prompt(self) -> None:
        if self.phase == "select":
            n = len(self.entities)
            extra = f"  ({n} selecionado{'s' if n != 1 else ''})" if n else ""
            self.set_prompt(self.prompt_select + extra)
            return
        i = min(len(self.points), len(self.prompt_points) - 1)
        self.set_prompt(self.prompt_points[i])

    # ---------------- fase de selecao ----------------

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world
        if self.phase == "select":
            self.pick.move(world, event.position() if event is not None else None)

    def on_click(self, world: Vec2, event=None) -> None:
        if self.phase == "select":
            self.pick.begin(world, event.position() if event is not None else None)
            return
        self.add_point(world)

    def on_release(self, world: Vec2, event=None) -> None:
        if self.phase != "select" or self.pick.anchor is None:
            return
        found, _ = self.pick.finish(world, self.ctx.viewport)
        for e in found:
            if e not in self.entities:
                self.entities.append(e)
        self.update_prompt()
        self.ctx.refresh()

    def on_key(self, key, modifiers=None) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            if self.phase == "select":
                self.confirm_selection()
            else:
                self.try_finish()
            return True
        return False

    def on_right_click(self, world: Vec2, event=None) -> None:
        if self.phase == "select":
            self.confirm_selection()
        else:
            self.try_finish()

    def confirm_selection(self) -> None:
        if not self.entities:
            self.ctx.message("Nada selecionado")
            self.finish()
            return
        self.ctx.selection.set(self.entities)
        self.phase = "points"
        self.update_prompt()
        self.ctx.refresh()

    def try_finish(self) -> None:
        self.finish()

    # ---------------- fase de pontos ----------------

    def add_point(self, p: Vec2) -> None:
        self.points.append(p)
        if len(self.points) >= self.needs_points:
            self.commit()
            self.finish()
        else:
            self.update_prompt()

    def on_text(self, text: str) -> bool:
        if self.phase == "select":
            return False
        last = self.points[-1] if self.points else None
        p = parse_coordinate(text, last)
        if p is None:
            return self.on_value(text)
        self.add_point(p)
        return True

    def on_value(self, text: str) -> bool:
        """Entrada numerica pura (angulo, fator). Sobrescrita pelas subclasses."""
        return False

    def commit(self) -> None:
        raise NotImplementedError

    # ---------------- desenho ----------------

    def paint(self, painter, vp) -> None:
        if self.phase == "select":
            self.pick.paint(painter, vp)
            return
        m = self.preview_matrix()
        if m is not None:
            _ghost(painter, vp, self.entities, m, vp.flatten_tolerance(0.5))
        if self.points and self.cursor is not None:
            painter.setPen(_pen(DARK.q("preview"), 1.0, Qt.DotLine))
            painter.drawLine(
                QPointF(*vp.world_to_screen(self.points[0])),
                QPointF(*vp.world_to_screen(self.cursor)),
            )

    def preview_matrix(self):
        return None


class MoveTool(ModifyTool):
    name = "MOVE"
    prompt_points = ("Ponto base:", "Deslocar para:")

    def preview_matrix(self):
        if not self.points or self.cursor is None:
            return None
        d = self.cursor - self.points[0]
        return Matrix44.translate(d.x, d.y, 0)

    def commit(self) -> None:
        d = self.points[1] - self.points[0]
        self.doc.transform(self.entities, Matrix44.translate(d.x, d.y, 0), "mover")
        self.ctx.message(f"{len(self.entities)} movido(s)  dX {d.x:+.3f}  dY {d.y:+.3f}")


class CopyTool(ModifyTool):
    """Copia repetidamente ate Esc, como o COPY do AutoCAD."""

    name = "COPY"
    prompt_points = ("Ponto base:", "Copiar para  [Esc termina]:")
    needs_points = 99  # nunca conclui sozinha

    def __init__(self, ctx):
        super().__init__(ctx)
        self.made = 0

    def add_point(self, p: Vec2) -> None:
        self.points.append(p)
        if len(self.points) == 1:
            self.update_prompt()
            return
        base = self.points[0]
        d = p - base
        self.doc.copy_entities(
            self.entities, Matrix44.translate(d.x, d.y, 0), f"copiar {len(self.entities)}"
        )
        self.made += 1
        self.points = [base]
        self.ctx.message(f"{self.made} copia(s) feita(s)")
        self.update_prompt()

    def preview_matrix(self):
        if not self.points or self.cursor is None:
            return None
        d = self.cursor - self.points[0]
        return Matrix44.translate(d.x, d.y, 0)

    def commit(self) -> None:
        pass


class RotateTool(ModifyTool):
    name = "ROTATE"
    prompt_points = ("Ponto base:", "Angulo (graus) ou ponto:")

    def preview_matrix(self):
        if not self.points or self.cursor is None:
            return None
        base = self.points[0]
        ang = (self.cursor - base).angle
        return self._matrix(base, ang)

    @staticmethod
    def _matrix(base: Vec2, rad: float):
        return Matrix44.chain(
            Matrix44.translate(-base.x, -base.y, 0),
            Matrix44.z_rotate(rad),
            Matrix44.translate(base.x, base.y, 0),
        )

    def on_value(self, text: str) -> bool:
        if not self.points:
            return False
        try:
            deg = float(text.replace(",", "."))
        except ValueError:
            return False
        self._apply(math.radians(deg))
        return True

    def add_point(self, p: Vec2) -> None:
        if not self.points:
            self.points.append(p)
            self.update_prompt()
            return
        self._apply((p - self.points[0]).angle)

    def _apply(self, rad: float) -> None:
        self.doc.transform(self.entities, self._matrix(self.points[0], rad), "girar")
        self.ctx.message(f"{len(self.entities)} girado(s) {math.degrees(rad):.4f} graus")
        self.finish()

    def commit(self) -> None:
        pass


class ScaleTool(ModifyTool):
    name = "SCALE"
    prompt_points = ("Ponto base:", "Fator ou ponto de referencia:")

    def preview_matrix(self):
        if not self.points or self.cursor is None:
            return None
        base = self.points[0]
        d = base.distance_to(self.cursor)
        ref = getattr(self, "_ref", None) or 1.0
        f = d / ref if ref else 1.0
        return self._matrix(base, f) if f > 1e-12 else None

    @staticmethod
    def _matrix(base: Vec2, f: float):
        return Matrix44.chain(
            Matrix44.translate(-base.x, -base.y, 0),
            Matrix44.scale(f, f, 1.0),
            Matrix44.translate(base.x, base.y, 0),
        )

    def on_value(self, text: str) -> bool:
        if not self.points:
            return False
        try:
            f = float(text.replace(",", "."))
        except ValueError:
            return False
        if f <= 0:
            self.ctx.message("Fator tem de ser positivo")
            return True
        self._apply(f)
        return True

    def add_point(self, p: Vec2) -> None:
        if not self.points:
            self.points.append(p)
            self._ref = 1.0
            self.update_prompt()
            return
        f = self.points[0].distance_to(p)
        if f <= 1e-12:
            return
        self._apply(f)

    def _apply(self, f: float) -> None:
        self.doc.transform(self.entities, self._matrix(self.points[0], f), "escalar")
        self.ctx.message(f"{len(self.entities)} escalado(s) por {f:.6g}")
        self.finish()

    def commit(self) -> None:
        pass


class MirrorTool(ModifyTool):
    """Espelha em torno do eixo definido por dois pontos.

    Por padrao mantem o original, como o MIRROR do AutoCAD; digite A para
    apagar o original antes de concluir.
    """

    name = "MIRROR"
    prompt_points = ("Primeiro ponto do eixo:", "Segundo ponto do eixo  [A=apagar original]:")
    keep_original = True

    @staticmethod
    def _matrix(a: Vec2, b: Vec2):
        d = b - a
        if d.length < 1e-12:
            return None
        ang = d.angle
        return Matrix44.chain(
            Matrix44.translate(-a.x, -a.y, 0),
            Matrix44.z_rotate(-ang),
            Matrix44.scale(1.0, -1.0, 1.0),
            Matrix44.z_rotate(ang),
            Matrix44.translate(a.x, a.y, 0),
        )

    def preview_matrix(self):
        if not self.points or self.cursor is None:
            return None
        return self._matrix(self.points[0], self.cursor)

    def on_text(self, text: str) -> bool:
        if text.strip().upper() in ("A", "APAGAR"):
            self.keep_original = False
            self.ctx.message("O original sera apagado")
            return True
        return super().on_text(text)

    def commit(self) -> None:
        m = self._matrix(self.points[0], self.points[1])
        if m is None:
            return
        self.doc.undo.begin_macro("espelhar")
        try:
            if self.keep_original:
                self.doc.copy_entities(self.entities, m, "espelhar")
            else:
                self.doc.transform(self.entities, m, "espelhar")
        finally:
            self.doc.undo.end_macro()
        self.ctx.message(f"{len(self.entities)} espelhado(s)")


class EraseTool(Tool):
    """Apaga a selecao; se nao houver, deixa selecionar antes."""

    name = "ERASE"
    prompt = "Selecione o que apagar e tecle Enter:"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.entities = list(ctx.selection)
        self.pick = PickHelper(ctx)

    def activate(self) -> None:
        # Precisa ser aqui e nao no __init__: no __init__ a ferramenta ainda
        # nao foi instalada no contexto, e o finish() seria ignorado.
        if self.entities:
            self._erase()
        else:
            super().activate()

    def on_mouse_move(self, world, event=None) -> None:
        self.pick.move(world, event.position() if event is not None else None)

    def on_click(self, world, event=None) -> None:
        self.pick.begin(world, event.position() if event is not None else None)

    def on_release(self, world, event=None) -> None:
        if self.pick.anchor is None:
            return
        found, _ = self.pick.finish(world, self.ctx.viewport)
        for e in found:
            if e not in self.entities:
                self.entities.append(e)
        self.set_prompt(f"Selecione o que apagar e tecle Enter:  ({len(self.entities)})")
        self.ctx.refresh()

    def on_key(self, key, modifiers=None) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self._erase()
            return True
        return False

    def on_right_click(self, world, event=None) -> None:
        self._erase()

    def _erase(self) -> None:
        if self.entities:
            n = len(self.entities)
            self.doc.delete(self.entities)
            self.ctx.selection.clear()
            self.ctx.message(f"{n} objeto(s) apagado(s)")
        self.finish()

    def paint(self, painter, vp) -> None:
        self.pick.paint(painter, vp)


class OffsetTool(Tool):
    """Paralela: distancia, objeto, lado. Repete ate Esc."""

    name = "OFFSET"
    prompt = "Distancia da paralela (ou tecle P para passar por um ponto):"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.distance: float | None = None
        self.through_mode = False
        self.target = None
        self.cursor: Vec2 | None = None
        self.made = 0

    def activate(self) -> None:
        self.update_prompt()

    def update_prompt(self) -> None:
        if self.distance is None and not self.through_mode:
            self.set_prompt("Distancia da paralela  [P=passar por ponto]:")
        elif self.target is None:
            self.set_prompt("Selecione o objeto  [Esc termina]:")
        else:
            self.set_prompt("Clique o lado da paralela:")

    def on_text(self, text: str) -> bool:
        t = text.strip().upper()
        if self.distance is None and not self.through_mode:
            if t in ("P", "PONTO"):
                self.through_mode = True
                self.update_prompt()
                return True
            try:
                d = float(text.replace(",", "."))
            except ValueError:
                return False
            if d <= 0:
                self.ctx.message("A distancia tem de ser positiva")
                return True
            self.distance = d
            self.update_prompt()
            return True
        return False

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world

    def on_click(self, world: Vec2, event=None) -> None:
        if self.distance is None and not self.through_mode:
            return
        if self.target is None:
            from ..core.picking import pick_at

            tol = self.ctx.viewport.px_to_world(PICK_TOL_PX)
            hit = pick_at(self.doc, world, tol)
            if hit is None:
                self.ctx.message("Nenhum objeto ali")
                return
            self.target = hit
            self.ctx.selection.set([hit])
            self.update_prompt()
            return

        made = create_offset(
            self.doc,
            self.target,
            self.distance or 0.0,
            through=world if self.through_mode else None,
            side_point=None if self.through_mode else world,
        )
        if made is None:
            self.ctx.message(f"{self.target.dxftype()} nao suporta paralela")
        else:
            self.made += 1
            self.ctx.message(f"{self.made} paralela(s) criada(s)")
        self.target = None
        self.ctx.selection.clear()
        self.update_prompt()

    def on_right_click(self, world: Vec2, event=None) -> None:
        self.finish()

    def paint(self, painter, vp) -> None:
        if self.target is None or self.cursor is None:
            return
        from ..core.offset import offset_entity

        spec = offset_entity(
            self.target,
            self.distance or 0.0,
            through=self.cursor if self.through_mode else None,
            side_point=None if self.through_mode else self.cursor,
        )
        if spec is None:
            return
        painter.setPen(_pen(DARK.q("preview"), 1.4, Qt.DashLine))
        from PySide6.QtGui import QPolygonF

        if spec["type"] in ("LINE", "LWPOLYLINE"):
            pts = [QPointF(*vp.world_to_screen(p)) for p in spec["points"]]
            if spec.get("closed"):
                pts.append(pts[0])
            if len(pts) >= 2:
                painter.drawPolyline(QPolygonF(pts))
        else:
            c = spec["center"]
            r = vp.world_to_px(spec["radius"])
            painter.drawEllipse(QPointF(*vp.world_to_screen(c)), r, r)
