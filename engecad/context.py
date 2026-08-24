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
from .render.viewport import Viewport
from .snap.engine import SnapEngine


class AppContext(QObject):
    documentReplaced = Signal()
    documentChanged = Signal()
    promptChanged = Signal(str)
    statusMessage = Signal(str)
    toolChanged = Signal(object)
    viewChanged = Signal()
    rastersChanged = Signal()

    def __init__(self, doc: Document | None = None):
        super().__init__()
        self.viewport = Viewport()
        self.registry = CommandRegistry()
        self.rasters: list = []
        self.tool = None
        self.canvas = None
        self._doc: Document | None = None
        self.snap: SnapEngine | None = None
        self.set_document(doc or Document.new())

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
        self.snap = SnapEngine(doc)
        doc.changed.append(self._on_doc_changed)
        doc.undo.changed.append(self._on_doc_changed)
        self.documentReplaced.emit()
        self.refresh()

    def _on_doc_changed(self) -> None:
        self.documentChanged.emit()
        self.refresh()

    # ---------------- ferramentas ----------------

    def set_tool(self, tool) -> None:
        self.cancel_tool()
        self.tool = tool
        if tool is not None:
            tool.activate()
        self.toolChanged.emit(tool)
        self.refresh()

    def end_tool(self, tool=None) -> None:
        """Chamado pela propria ferramenta ao concluir."""
        if self.tool is None or (tool is not None and tool is not self.tool):
            return
        finished = self.tool
        self.tool = None
        finished.deactivate()
        self.set_prompt("")
        self.toolChanged.emit(None)
        self.refresh()

    def cancel_tool(self) -> None:
        if self.tool is None:
            return
        tool = self.tool
        self.tool = None
        tool.deactivate()
        self.set_prompt("")
        self.toolChanged.emit(None)
        self.refresh()

    # ---------------- comandos ----------------

    def run_command(self, name: str, *args) -> bool:
        """Ponto unico de despacho: linha de comando, console e LISP passam aqui."""
        cd = self.registry.resolve(name)
        if cd is None:
            self.message(f"Comando desconhecido: {name}")
            return False
        self.cancel_tool()
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
        self.viewChanged.emit()
        self.refresh()

    def content_extents(self) -> BBox:
        b = self._doc.extents()
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
