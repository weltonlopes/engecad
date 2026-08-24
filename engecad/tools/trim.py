"""APARAR e ESTENDER.

As arestas de corte sao, por padrao, todas as entidades visiveis na tela --
como no AutoCAD moderno, que dispensa selecionar as arestas antes. Basta
clicar no trecho que deve sumir (ou na ponta que deve crescer).
"""

from __future__ import annotations

from PySide6.QtCore import Qt

from ..core.geometry import Vec2
from ..core.picking import pick_at
from ..core.trimming import collect_shapes, extend_entity, trim_entity
from .base import Tool
from .select import PICK_TOL_PX


class _EdgeTool(Tool):
    """Base de APARAR/ESTENDER: repete a acao ate Esc."""

    verb = "aparar"

    def __init__(self, ctx):
        super().__init__(ctx)
        self.count = 0

    def _shapes_for(self, target):
        """Arestas: tudo que estiver visivel na tela, menos o proprio alvo."""
        vp = self.ctx.viewport
        candidates = [
            e
            for e in self.doc.query(vp.visible_bbox())
            if e is not target
            and e.is_alive
            and self.doc.layer_is_visible(e.dxf.get("layer", "0"))
        ]
        return collect_shapes(candidates, sagitta=vp.flatten_tolerance(0.2))

    def _target_at(self, world: Vec2):
        tol = self.ctx.viewport.px_to_world(PICK_TOL_PX)
        return pick_at(self.doc, world, tol)

    def on_right_click(self, world: Vec2, event=None) -> None:
        self.finish()

    def on_key(self, key, modifiers=None) -> bool:
        if key in (Qt.Key_Return, Qt.Key_Enter):
            self.finish()
            return True
        return False

    def on_text(self, text: str) -> bool:
        """U desfaz o ultimo corte sem sair da ferramenta, como no AutoCAD."""
        if text.strip().upper() in ("U", "UNDO", "DESFAZER"):
            if self.doc.undo.undo():
                self.count = max(0, self.count - 1)
                self.ctx.selection.prune()
                self.ctx.message(f"Desfeito; {self.count} {self.verb} restante(s)")
            else:
                self.ctx.message("Nada a desfazer")
            return True
        return False


class TrimTool(_EdgeTool):
    verb = "corte(s)"
    name = "TRIM"
    prompt = "Clique o trecho a apagar  [Esc termina]:"

    def on_click(self, world: Vec2, event=None) -> None:
        target = self._target_at(world)
        if target is None:
            self.ctx.message("Nenhum objeto sob o cursor")
            return

        shapes = self._shapes_for(target)
        self.doc.undo.begin_macro("aparar")
        try:
            result = trim_entity(self.doc, target, shapes, world)
            if result is None:
                self.doc.undo.abort_macro()
                self.ctx.message(
                    f"Nada corta este {target.dxftype()} aqui "
                    "(ou o tipo nao suporta aparar)"
                )
                return
            self.doc.delete([target])
        except Exception:
            self.doc.undo.abort_macro()
            raise
        self.doc.undo.end_macro()
        self.ctx.selection.prune()
        self.count += 1
        self.ctx.message(f"{self.count} corte(s); restaram {len(result)} pedaco(s)")


class ExtendTool(_EdgeTool):
    verb = "extensao(oes)"
    name = "EXTEND"
    prompt = "Clique perto da ponta a esticar  [Esc termina]:"

    def on_click(self, world: Vec2, event=None) -> None:
        target = self._target_at(world)
        if target is None:
            self.ctx.message("Nenhum objeto sob o cursor")
            return
        shapes = self._shapes_for(target)
        if extend_entity(self.doc, target, shapes, world):
            self.count += 1
            self.ctx.message(f"{self.count} extensao(oes)")
        else:
            self.ctx.message(
                f"Nao ha aresta a alcancar nessa direcao "
                f"(ou {target.dxftype()} nao suporta estender)"
            )
