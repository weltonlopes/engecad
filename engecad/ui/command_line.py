"""Linha de comando estilo CAD.

E o que separa "desenhar com o mouse" de "desenhar com precisao topografica":
com uma ferramenta ativa, tudo que for digitado aqui e interpretado como
coordenada (absoluta, relativa @dx,dy, polar @d<ang ou por azimute @d<<az).
Sem ferramenta ativa, e um comando.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent
from PySide6.QtWidgets import QCompleter, QHBoxLayout, QLabel, QLineEdit, QWidget


class _Entry(QLineEdit):
    upPressed = Signal()
    downPressed = Signal()
    escapePressed = Signal()

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key_Up:
            self.upPressed.emit()
            return
        if ev.key() == Qt.Key_Down:
            self.downPressed.emit()
            return
        if ev.key() == Qt.Key_Escape:
            self.escapePressed.emit()
            return
        super().keyPressEvent(ev)


class CommandLine(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._history: list[str] = []
        self._pos = 0
        self._last_command = ""

        self.prompt = QLabel("Comando:", self)
        f = QFont(self.prompt.font())
        f.setBold(True)
        self.prompt.setFont(f)
        self.prompt.setMinimumWidth(150)

        self.entry = _Entry(self)
        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        self.entry.setFont(mono)
        self.entry.returnPressed.connect(self._submit)
        self.entry.upPressed.connect(self._prev)
        self.entry.downPressed.connect(self._next)
        self.entry.escapePressed.connect(self._escape)

        self._completer = QCompleter(ctx.registry.names(), self)
        self._completer.setCaseSensitivity(Qt.CaseInsensitive)
        self._completer.setCompletionMode(QCompleter.InlineCompletion)
        self.entry.setCompleter(self._completer)

        lay = QHBoxLayout(self)
        lay.setContentsMargins(6, 2, 6, 2)
        lay.setSpacing(8)
        lay.addWidget(self.prompt)
        lay.addWidget(self.entry, 1)

        ctx.promptChanged.connect(self.set_prompt)
        ctx.toolChanged.connect(self._on_tool_changed)

    # ---------------- estado ----------------

    def set_prompt(self, text: str) -> None:
        self.prompt.setText(text or "Comando:")

    def _on_tool_changed(self, tool) -> None:
        if tool is None:
            self.set_prompt("")
        self.entry.setFocus()

    def focus(self) -> None:
        self.entry.setFocus()
        self.entry.selectAll()

    # ---------------- historico ----------------

    def _remember(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._pos = len(self._history)

    def _prev(self) -> None:
        if not self._history:
            return
        self._pos = max(0, self._pos - 1)
        self.entry.setText(self._history[self._pos])

    def _next(self) -> None:
        if not self._history:
            return
        self._pos = min(len(self._history), self._pos + 1)
        self.entry.setText("" if self._pos >= len(self._history) else self._history[self._pos])

    def _escape(self) -> None:
        if self.entry.text():
            self.entry.clear()
        else:
            self.ctx.cancel_tool()

    # ---------------- despacho ----------------

    def _submit(self) -> None:
        text = self.entry.text().strip()
        self.entry.clear()

        tool = self.ctx.tool
        if tool is not None:
            if not text:
                # Enter vazio com ferramenta ativa = concluir
                tool.on_key(Qt.Key_Return)
                if self.ctx.tool is tool:
                    tool.finish()
                return
            if tool.on_text(text):
                self._remember(text)
                self.ctx.refresh()
                return
            self.ctx.message(f"Entrada nao reconhecida: {text}")
            return

        if not text:
            # Enter vazio repete o ultimo comando, como no AutoCAD
            if self._last_command:
                self.ctx.run_command(self._last_command)
            return

        self._remember(text)
        parts = text.split()
        name, args = parts[0], parts[1:]
        if self.ctx.registry.resolve(name) is None:
            matches = self.ctx.registry.matches(name)
            if len(matches) == 1:
                name = matches[0]
            elif matches:
                self.ctx.message("Ambiguo: " + ", ".join(matches))
                return
        if self.ctx.run_command(name, *args):
            self._last_command = name

    def refresh_completions(self) -> None:
        self._completer.model().setStringList(self.ctx.registry.names())
