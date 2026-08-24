"""Base das ferramentas interativas.

Uma Tool e uma maquina de estados curta: recebe cliques, movimentos e texto
digitado na linha de comando, desenha um preview e, quando termina, empilha o
resultado no undo. A MESMA ferramenta e acionada pelo mouse, pela linha de
comando e (na v0.4) pelo AutoLISP -- ver core/registry.py.
"""

from __future__ import annotations

from ..core.geometry import Vec2


class Tool:
    name: str = "ferramenta"
    #: mensagem mostrada na linha de comando quando a ferramenta esta ativa
    prompt: str = ""
    #: se o comando entra no historico de "repetir com Enter" da linha de comando
    repeats: bool = True

    def __init__(self, ctx):
        self.ctx = ctx
        self.finished = False

    # ---------------- ciclo de vida ----------------

    def activate(self) -> None:
        self.ctx.set_prompt(self.prompt)

    def deactivate(self) -> None:
        pass

    def finish(self) -> None:
        """Conclui com sucesso."""
        self.finished = True
        self.ctx.end_tool(self)

    def cancel(self) -> None:
        """Aborta (Esc). Nao deve deixar nada meio feito no documento."""
        self.finished = True
        self.ctx.end_tool(self)

    # ---------------- entrada ----------------

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        pass

    def on_click(self, world: Vec2, event=None) -> None:
        pass

    def on_right_click(self, world: Vec2, event=None) -> None:
        """Botao direito = concluir, como no AutoCAD."""
        self.finish()

    def on_key(self, key, modifiers=None) -> bool:
        """True se consumiu a tecla."""
        return False

    def on_text(self, text: str) -> bool:
        """Texto vindo da linha de comando (coordenada ou opcao). True se consumiu."""
        return False

    # ---------------- desenho ----------------

    def paint(self, painter, viewport) -> None:
        """Preview elastico. Nunca desenha no documento."""
        pass

    # ---------------- apoio ----------------

    @property
    def doc(self):
        return self.ctx.doc

    def set_prompt(self, text: str) -> None:
        self.ctx.set_prompt(text)


class PointCollectorTool(Tool):
    """Ferramenta que junta N pontos e entao produz algo.

    Cobre LINE, PLINE, e (na v0.2) MOVE/COPY, que sao todas 'colete pontos e
    aja'. As subclasses so implementam min_points/max_points e commit().
    """

    min_points = 2
    max_points = 2

    def __init__(self, ctx):
        super().__init__(ctx)
        self.points: list[Vec2] = []
        self.cursor: Vec2 | None = None

    # -- pontos --

    def add_point(self, p: Vec2) -> None:
        self.points.append(p)
        self.after_point()
        if self.max_points and len(self.points) >= self.max_points:
            self.commit()
            self.finish()

    def after_point(self) -> None:
        self.update_prompt()

    def update_prompt(self) -> None:
        self.set_prompt(self.prompt)

    def commit(self) -> None:
        raise NotImplementedError

    # -- entrada --

    def on_mouse_move(self, world: Vec2, event=None) -> None:
        self.cursor = world

    def on_click(self, world: Vec2, event=None) -> None:
        self.add_point(world)

    def on_right_click(self, world: Vec2, event=None) -> None:
        if len(self.points) >= self.min_points:
            self.commit()
        self.finish()

    def on_text(self, text: str) -> bool:
        from ..core.coordinput import parse_coordinate

        last = self.points[-1] if self.points else None
        p = parse_coordinate(text, last)
        if p is None:
            return False
        self.add_point(p)
        return True

    def cancel(self) -> None:
        self.points.clear()
        super().cancel()
