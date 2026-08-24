"""Pilha de desfazer/refazer. Sem Qt."""

from __future__ import annotations

from collections.abc import Callable


class Command:
    """Unidade atomica de alteracao. redo() tem de ser reexecutavel."""

    name: str = "comando"

    def redo(self) -> None:
        raise NotImplementedError

    def undo(self) -> None:
        raise NotImplementedError


class CallbackCommand(Command):
    def __init__(self, name: str, do: Callable[[], None], undo: Callable[[], None]):
        self.name = name
        self._do = do
        self._undo = undo

    def redo(self) -> None:
        self._do()

    def undo(self) -> None:
        self._undo()


class CompositeCommand(Command):
    """Varios comandos desfeitos como um so -- base do macro."""

    def __init__(self, name: str, children: list[Command] | None = None):
        self.name = name
        self.children: list[Command] = children or []

    def add(self, cmd: Command) -> None:
        self.children.append(cmd)

    def redo(self) -> None:
        for c in self.children:
            c.redo()

    def undo(self) -> None:
        for c in reversed(self.children):
            c.undo()

    def __len__(self) -> int:
        return len(self.children)


class UndoStack:
    def __init__(self, limit: int = 500):
        self._done: list[Command] = []
        self._undone: list[Command] = []
        self._limit = limit
        self._macros: list[CompositeCommand] = []
        self.changed: list[Callable[[], None]] = []

    def _notify(self) -> None:
        for cb in self.changed:
            cb()

    def push(self, cmd: Command, execute: bool = True) -> Command:
        """Empilha o comando. Dentro de um macro, agrega em vez de empilhar."""
        if execute:
            cmd.redo()
        if self._macros:
            self._macros[-1].add(cmd)
            return cmd
        self._done.append(cmd)
        self._undone.clear()
        if len(self._done) > self._limit:
            del self._done[0 : len(self._done) - self._limit]
        self._notify()
        return cmd

    def begin_macro(self, name: str) -> None:
        """Agrupa tudo ate end_macro() num unico item de desfazer."""
        self._macros.append(CompositeCommand(name))

    def end_macro(self) -> None:
        if not self._macros:
            return
        macro = self._macros.pop()
        if len(macro) == 0:
            return
        if self._macros:
            self._macros[-1].add(macro)
            return
        self._done.append(macro)
        self._undone.clear()
        self._notify()

    def abort_macro(self) -> None:
        """Desfaz e descarta o macro corrente (ex.: script que levantou excecao)."""
        if not self._macros:
            return
        macro = self._macros.pop()
        macro.undo()
        self._notify()

    @property
    def in_macro(self) -> bool:
        return bool(self._macros)

    @property
    def can_undo(self) -> bool:
        return bool(self._done)

    @property
    def can_redo(self) -> bool:
        return bool(self._undone)

    @property
    def undo_text(self) -> str:
        return self._done[-1].name if self._done else ""

    @property
    def redo_text(self) -> str:
        return self._undone[-1].name if self._undone else ""

    def undo(self) -> bool:
        if not self._done:
            return False
        cmd = self._done.pop()
        cmd.undo()
        self._undone.append(cmd)
        self._notify()
        return True

    def redo(self) -> bool:
        if not self._undone:
            return False
        cmd = self._undone.pop()
        cmd.redo()
        self._done.append(cmd)
        self._notify()
        return True

    def clear(self) -> None:
        self._done.clear()
        self._undone.clear()
        self._macros.clear()
        self._notify()
