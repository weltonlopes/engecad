"""Console Python embutido.

Roda na thread da interface, de proposito: scripts mexem no documento e no
canvas, e cruzar thread com Qt seria fonte de travamento. Em compensacao, cada
execucao e envolvida num macro de undo -- um script que cria 500 entidades
desfaz com um Ctrl+Z so, e um script que levanta excecao no meio nao deixa
metade do trabalho no desenho.
"""

from __future__ import annotations

import code
import contextlib
import io
import traceback

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont, QKeyEvent, QTextCursor
from PySide6.QtWidgets import QLineEdit, QPlainTextEdit, QVBoxLayout, QWidget

from .api import build_namespace

BANNER = (
    "Console Python do EngeCAD\n"
    "A API esta no escopo: add_line, add_polyline, entities, command, zoom_extents...\n"
    "Digite  help()  para a lista, ou  api.<TAB>  no seu editor.\n"
)


class _Interpreter(code.InteractiveInterpreter):
    """InteractiveInterpreter engole a excecao e apenas imprime o traceback.

    Sem este flag o console acharia que o script correu bem e faria commit do
    macro de undo, deixando no desenho as entidades criadas antes do erro.
    """

    def __init__(self, namespace):
        super().__init__(namespace)
        self.error = False

    def showtraceback(self, *args, **kwargs):
        self.error = True
        super().showtraceback(*args, **kwargs)

    def showsyntaxerror(self, *args, **kwargs):
        self.error = True
        super().showsyntaxerror(*args, **kwargs)


class _Input(QLineEdit):
    """Linha de entrada com historico (setas)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._history: list[str] = []
        self._pos = 0

    def remember(self, text: str) -> None:
        if text.strip() and (not self._history or self._history[-1] != text):
            self._history.append(text)
        self._pos = len(self._history)

    def keyPressEvent(self, ev: QKeyEvent):
        if ev.key() == Qt.Key_Up and self._history:
            self._pos = max(0, self._pos - 1)
            self.setText(self._history[self._pos])
            return
        if ev.key() == Qt.Key_Down and self._history:
            self._pos = min(len(self._history), self._pos + 1)
            self.setText("" if self._pos >= len(self._history) else self._history[self._pos])
            return
        super().keyPressEvent(ev)


class PythonConsole(QWidget):
    executed = Signal()

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._namespace = build_namespace(ctx)
        self._namespace["help"] = self._help
        self._console = _Interpreter(self._namespace)
        self._buffer: list[str] = []

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.Monospace)
        mono.setPointSize(9)

        self.output = QPlainTextEdit(self)
        self.output.setReadOnly(True)
        self.output.setFont(mono)
        self.output.setMaximumBlockCount(3000)
        self.output.setPlainText(BANNER)

        self.input = _Input(self)
        self.input.setFont(mono)
        self.input.setPlaceholderText(">>>")
        self.input.returnPressed.connect(self._on_enter)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.setSpacing(4)
        lay.addWidget(self.output, 1)
        lay.addWidget(self.input)

    # ---------------- execucao ----------------

    def _help(self, obj=None):
        if obj is None:
            self.write(self._namespace["api"].help() + "\n")
            return
        import builtins

        builtins.help(obj)

    def write(self, text: str) -> None:
        self.output.moveCursor(QTextCursor.End)
        self.output.insertPlainText(text)
        self.output.moveCursor(QTextCursor.End)

    def _on_enter(self) -> None:
        line = self.input.text()
        self.input.remember(line)
        self.input.clear()
        prompt = "... " if self._buffer else ">>> "
        self.write(f"{prompt}{line}\n")
        self.run_line(line)

    def run_line(self, line: str) -> None:
        self._buffer.append(line)
        source = "\n".join(self._buffer)
        try:
            more = self._needs_more(source)
        except SyntaxError:
            self._buffer.clear()
            self.write(traceback.format_exc(limit=0))
            return
        if more:
            return
        self._buffer.clear()
        self.execute(source)

    @staticmethod
    def _needs_more(source: str) -> bool:
        """True se o codigo esta incompleto (bloco aberto)."""
        try:
            return code.compile_command(source, "<console>", "single") is None
        except (OverflowError, ValueError):
            raise SyntaxError("entrada invalida") from None

    def execute(self, source: str) -> None:
        """Executa um trecho. Toda a execucao vira UM item de desfazer."""
        doc = self.ctx.doc
        out = io.StringIO()
        doc.undo.begin_macro("script Python")
        failed = False
        self._console.error = False
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                self._console.runsource(source, "<console>", "exec")
            failed = self._console.error
        except SystemExit:
            self.write("(SystemExit ignorado no console)\n")
            failed = True
        except BaseException:
            out.write(traceback.format_exc())
            failed = True
        finally:
            if failed:
                # nao deixa meio desenho para tras
                doc.undo.abort_macro()
            else:
                doc.undo.end_macro()

        text = out.getvalue()
        if text:
            self.write(text)
        self.ctx.refresh()
        self.executed.emit()

    def run_file(self, path) -> None:
        try:
            source = open(path, encoding="utf-8").read()
        except OSError as exc:
            self.write(f"Nao foi possivel ler {path}: {exc}\n")
            return
        self.write(f"# executando {path}\n")
        doc = self.ctx.doc
        out = io.StringIO()
        doc.undo.begin_macro(f"script {path}")
        failed = False
        try:
            compiled = compile(source, str(path), "exec")
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
                exec(compiled, self._namespace)
        except BaseException:
            out.write(traceback.format_exc())
            failed = True
        finally:
            doc.undo.abort_macro() if failed else doc.undo.end_macro()
        if out.getvalue():
            self.write(out.getvalue())
        self.ctx.refresh()
        self.executed.emit()

    def rebind(self, ctx) -> None:
        """Chamado quando o documento e trocado."""
        self.ctx = ctx
        self._namespace.update(build_namespace(ctx))
        self._namespace["help"] = self._help
