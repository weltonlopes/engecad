"""Contexto da aplicacao: o objeto que costura documento, vista e ferramentas.

Tudo que a interface faz passa por aqui, e nao o contrario -- assim a linha de
comando, o console Python e (v0.4) o AutoLISP acionam exatamente o mesmo
caminho que os botoes da barra de ferramentas.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from .core.document import Document
from .core.geometry import BBox, Vec2
from .core.registry import CommandRegistry
from .core.selection import Selection
from .render.viewport import Viewport
from .snap.engine import SnapEngine


class AppContext(QObject):
    documentReplaced = Signal()
    documentChanged = Signal()
    promptChanged = Signal(str)
    statusMessage = Signal(str)
    toolChanged = Signal(object)
    viewChanged = Signal()
    layoutChanged = Signal(str)
    rastersChanged = Signal()
    layerManagerRequested = Signal()

    def __init__(self, doc: Document | None = None):
        super().__init__()
        self.viewport = Viewport()
        self.registry = CommandRegistry()
        self.rasters: list = []
        self.tool = None
        self.canvas = None
        self.command_line = None
        self._doc: Document | None = None
        self.snap: SnapEngine | None = None
        self.selection: Selection | None = None
        #: Vista independente de cada aba de layout.
        self._layout_views: dict[str, tuple[Vec2, float]] = {}
        #: Grip de vertice em foco no painel de propriedades (realce no canvas).
        self.vertex_focus = None
        # `doc or Document.new()` descartaria um documento vazio: Document tem
        # __len__, entao um desenho sem entidades e falsy.
        self.set_document(Document.new() if doc is None else doc)

        from .commands import register_builtin_commands

        register_builtin_commands(self.registry)

    # ---------------- documento ----------------

    @property
    def doc(self) -> Document:
        return self._doc

    @property
    def crs(self):
        return self._doc.crs

    def set_document(self, doc: Document) -> None:
        self.cancel_tool()
        self._doc = doc
        self._layout_views = {}
        self.snap = SnapEngine(doc)
        self.selection = Selection(doc)
        self.vertex_focus = None
        doc.changed.append(self._on_doc_changed)
        doc.undo.changed.append(self._on_doc_changed)
        # desfazer pode ressuscitar ou matar entidades: a selecao acompanha
        doc.undo.changed.append(self.selection.prune)
        self.documentReplaced.emit()
        self.set_tool(None)
        self.refresh()

    def _on_doc_changed(self) -> None:
        self.documentChanged.emit()
        self.refresh()

    # ---------------- ferramentas ----------------

    def set_tool(self, tool) -> None:
        """Troca a ferramenta ativa. tool=None volta a ferramenta ociosa (selecao)."""
        old = self.tool
        self.tool = None
        if old is not None:
            old.deactivate()
        if tool is None:
            from .tools.select import SelectTool

            tool = SelectTool(self)
        self.tool = tool
        tool.activate()
        self.toolChanged.emit(tool)
        self.refresh()

    def end_tool(self, tool=None) -> None:
        """Chamado pela propria ferramenta ao concluir."""
        if tool is not None and tool is not self.tool:
            return
        self.set_prompt("")
        self.set_tool(None)

    def cancel_tool(self) -> None:
        self.set_prompt("")
        self.set_tool(None)

    # ---------------- layouts ----------------

    @property
    def layout_names(self) -> list[str]:
        return self._doc.layout_names()

    def set_layout(self, name: str) -> bool:
        """Troca a aba visível e restaura a vista que ela tinha anteriormente."""
        actual = self._doc.resolve_layout_name(name)
        if actual.casefold() == self._doc.current_layout.casefold():
            return False

        self.cancel_tool()
        current = self._doc.current_layout
        self._layout_views[current] = (self.viewport.center, self.viewport.scale)
        self._doc.set_layout(actual)
        self.selection.clear()
        self.vertex_focus = None
        self.documentChanged.emit()

        saved = self._layout_views.get(self._doc.current_layout)
        if saved is None:
            self.zoom_extents()
        else:
            self.viewport.center, scale = saved
            self.viewport.set_scale(scale)
            self.view_changed()
        self.layoutChanged.emit(self._doc.current_layout)
        self.refresh()
        return True

    @property
    def idle(self) -> bool:
        """Nenhum comando rodando (so a ferramenta de selecao)."""
        return self.tool is None or self.tool.is_idle

    # ---------------- comandos ----------------

    def run_command(self, name: str, *args) -> bool:
        """Ponto unico de despacho: linha de comando, console e LISP passam aqui."""
        cd = self.registry.resolve(name)
        if cd is None:
            self.message(f"Comando desconhecido: {name}")
            return False
        self.set_tool(None)
        try:
            result = cd.handler(self, *args)
        except Exception as exc:  # nao derruba o app por causa de um comando
            self.message(f"Erro em {cd.name}: {exc}")
            return False
        if result is not None:
            self.set_tool(result)
        return True

    # ---------------- mensagens e vista ----------------

    def set_prompt(self, text: str) -> None:
        self.promptChanged.emit(text or "")

    def message(self, text: str) -> None:
        self.statusMessage.emit(text)

    def refresh(self) -> None:
        if self.canvas is not None:
            self.canvas.update()

    def view_changed(self) -> None:
        if self._doc is not None:
            self._layout_views[self._doc.current_layout] = (
                self.viewport.center,
                self.viewport.scale,
            )
        self.viewChanged.emit()
        self.refresh()

    def content_extents(self) -> BBox:
        b = self._doc.extents()
        # Rasters georreferenciados pertencem ao model space; não devem
        # deslocar o enquadramento de uma folha de apresentação.
        if self._doc.is_model_layout:
            for r in self.rasters:
                b = b.union(r.bounds)
        return b

    def zoom_extents(self) -> None:
        b = self.content_extents()
        if b.is_empty:
            self.viewport.center = Vec2(0, 0)
            self.viewport.set_scale(1.0)
        else:
            self.viewport.zoom_to_bbox(b)
        self.view_changed()
