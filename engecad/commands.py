"""Comandos embutidos do EngeCAD.

Todo comando e registrado aqui e passa a estar disponivel, de graca, nos tres
acionadores: linha de comando, console Python (api.command) e AutoLISP (v0.4).
"""

from __future__ import annotations

from .core.coordinput import parse_coordinate
from .tools.draw import LineTool, PolylineTool
from .tools.measure import AreaTool, DistanceTool


def register_builtin_commands(reg) -> None:
    # ---- desenho (devolvem uma Tool: o app ativa) ----
    reg.register(
        "LINE", lambda ctx, *a: LineTool(ctx), ("L",), "Desenha uma linha", "desenho"
    )
    reg.register(
        "PLINE", lambda ctx, *a: PolylineTool(ctx), ("PL", "POL"), "Desenha polilinha", "desenho"
    )

    # ---- consulta ----
    reg.register(
        "DIST", lambda ctx, *a: DistanceTool(ctx), ("DI",), "Mede distancia e azimute", "consulta"
    )
    reg.register("AREA", lambda ctx, *a: AreaTool(ctx), (), "Mede area e perimetro", "consulta")

    # ---- vista (acao imediata: devolvem None) ----
    reg.register("ZE", _zoom_extents, ("ZOOMEXT",), "Enquadra todo o desenho", "vista", False)
    reg.register("ZOOM", _zoom, ("Z",), "ZOOM <fator> ou ZOOM E", "vista", False)
    reg.register("ESCALA", _set_scale, ("SC",), "ESCALA 500 ajusta para 1:500", "vista", False)
    reg.register("PAN", _pan_to, ("P",), "PAN <x,y> centraliza na coordenada", "vista", False)
    reg.register("GRADE", _toggle_grid, ("GRID",), "Liga/desliga a grade", "vista", False)

    # ---- edicao ----
    reg.register("U", _undo, ("UNDO", "DESFAZER"), "Desfaz", "edicao", False)
    reg.register("REDO", _redo, ("REFAZER",), "Refaz", "edicao", False)

    # ---- organizacao ----
    reg.register(
        "CAMADA", _set_layer, ("LAYER", "LA"), "CAMADA <nome> torna corrente", "camadas", False
    )
    reg.register("OSNAP", _toggle_osnap, ("OS",), "Liga/desliga o snap", "desenho", False)

    # ---- ajuda ----
    reg.register("AJUDA", _help, ("HELP", "?"), "Lista os comandos", "geral", False)


# ---------------- implementacoes ----------------


def _zoom_extents(ctx, *args):
    ctx.zoom_extents()
    ctx.message("Zoom estendido")


def _zoom(ctx, *args):
    if not args:
        ctx.message("Uso: ZOOM <fator>  ou  ZOOM E")
        return
    a = str(args[0]).strip().upper()
    if a in ("E", "EXT", "EXTENTS"):
        ctx.zoom_extents()
        return
    try:
        factor = float(a)
    except ValueError:
        ctx.message(f"Fator invalido: {a}")
        return
    vp = ctx.viewport
    vp.zoom_at_screen(vp.width / 2, vp.height / 2, factor)
    ctx.view_changed()


def _set_scale(ctx, *args):
    if not args:
        ctx.message(f"Escala atual 1:{ctx.viewport.scale_denominator():.0f}")
        return
    txt = str(args[0]).strip().lstrip("1:").replace(":", "")
    try:
        denom = float(txt)
    except ValueError:
        ctx.message(f"Escala invalida: {args[0]}")
        return
    ctx.viewport.set_scale_denominator(denom)
    ctx.view_changed()
    ctx.message(f"Escala 1:{denom:.0f}")


def _pan_to(ctx, *args):
    if not args:
        ctx.message("Uso: PAN <x,y>")
        return
    p = parse_coordinate(" ".join(str(a) for a in args))
    if p is None:
        ctx.message("Coordenada invalida")
        return
    ctx.viewport.center = p
    ctx.view_changed()
    ctx.message(f"Centralizado em {p.x:.3f}, {p.y:.3f}")


def _toggle_grid(ctx, *args):
    if ctx.canvas is None:
        return
    ctx.canvas.show_grid = not ctx.canvas.show_grid
    ctx.refresh()
    ctx.message("Grade ligada" if ctx.canvas.show_grid else "Grade desligada")


def _undo(ctx, *args):
    if ctx.doc.undo.undo():
        ctx.message("Desfeito")
    else:
        ctx.message("Nada a desfazer")


def _redo(ctx, *args):
    if ctx.doc.undo.redo():
        ctx.message("Refeito")
    else:
        ctx.message("Nada a refazer")


def _set_layer(ctx, *args):
    if not args:
        ctx.message(f"Camada corrente: {ctx.doc.current_layer}")
        return
    name = str(args[0]).strip()
    ctx.doc.current_layer = name
    ctx.documentChanged.emit()
    ctx.message(f"Camada corrente: {name}")


def _toggle_osnap(ctx, *args):
    ctx.snap.active = not ctx.snap.active
    ctx.refresh()
    ctx.message("Snap ligado" if ctx.snap.active else "Snap desligado")


def _help(ctx, *args):
    by_cat: dict[str, list[str]] = {}
    for cd in ctx.registry.definitions():
        alias = f" ({', '.join(cd.aliases)})" if cd.aliases else ""
        by_cat.setdefault(cd.category, []).append(f"  {cd.name}{alias} - {cd.description}")
    lines = []
    for cat in sorted(by_cat):
        lines.append(f"[{cat}]")
        lines.extend(sorted(by_cat[cat]))
    ctx.message("\n".join(lines))
