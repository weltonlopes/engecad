"""Registro central de comandos.

Ponto unico por onde passam os tres modos de acionar o EngeCAD:

    linha de comando   ("LINE")            -.
    console Python     api.command("LINE") -+-> registry -> Tool / acao
    AutoLISP  [v0.4]   (command "LINE")    -'

Manter esse funil unico e o que permite o interpretador LISP da v0.4 ser
um plugue e nao uma reescrita.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class CommandDef:
    """Um comando nomeado.

    handler(ctx, *args) pode:
      - devolver uma Tool  -> o app ativa a ferramenta (interativo)
      - devolver None      -> acao imediata, ja executada
    """

    name: str
    handler: Callable
    aliases: tuple[str, ...] = ()
    description: str = ""
    category: str = "geral"
    interactive: bool = True

    @property
    def all_names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


class CommandRegistry:
    def __init__(self) -> None:
        self._by_name: dict[str, CommandDef] = {}
        self._defs: list[CommandDef] = []

    def register(
        self,
        name: str,
        handler: Callable,
        aliases: tuple[str, ...] | list[str] = (),
        description: str = "",
        category: str = "geral",
        interactive: bool = True,
    ) -> CommandDef:
        cd = CommandDef(
            name=name.upper(),
            handler=handler,
            aliases=tuple(a.upper() for a in aliases),
            description=description,
            category=category,
            interactive=interactive,
        )
        for n in cd.all_names:
            if n in self._by_name:
                raise ValueError(f"comando duplicado: {n}")
            self._by_name[n] = cd
        self._defs.append(cd)
        return cd

    def command(self, name: str, **kw):
        """Uso como decorador: @registry.command("LINE", aliases=("L",))"""

        def deco(fn):
            self.register(name, fn, **kw)
            return fn

        return deco

    def resolve(self, name: str) -> CommandDef | None:
        return self._by_name.get(name.strip().upper())

    def __contains__(self, name: str) -> bool:
        return name.strip().upper() in self._by_name

    def names(self) -> list[str]:
        return sorted(self._by_name)

    def definitions(self) -> list[CommandDef]:
        return list(self._defs)

    def matches(self, prefix: str) -> list[str]:
        """Nomes que comecam com prefix -- alimenta o autocompletar."""
        p = prefix.strip().upper()
        return sorted(n for n in self._by_name if n.startswith(p))
