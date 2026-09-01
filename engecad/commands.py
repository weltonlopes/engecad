"""Comandos embutidos do EngeCAD.

Todo comando e registrado aqui e passa a estar disponivel, de graca, nos tres
acionadores: linha de comando, console Python (api.command) e AutoLISP (v0.4).
"""

from __future__ import annotations

from .core.coordinput import parse_coordinate
from .tools.dimension import (
    AlignedDimensionTool,
    AngularDimensionTool,
    ArcLengthDimensionTool,
    DiameterDimensionTool,
    LinearDimensionTool,
    OrdinateDimensionTool,
    RadiusDimensionTool,
    ReassociateDimensionTool,
)
from .tools.draw import LineTool, PolylineTool
from .tools.hatch import edit_hatch, edit_title_block, start_hatch, start_title_block
from .tools.measure import AreaTool, DistanceTool
from .tools.modify import (
    CopyTool,
    EraseTool,
    MirrorTool,
    MoveTool,
    OffsetTool,
    RotateTool,
    ScaleTool,
)
from .tools.shapes import ArcTool, CircleTool, RectangleTool, TextTool
from .tools.trim import ExtendTool, TrimTool


def register_builtin_commands(reg) -> None:
    # ---- desenho (devolvem uma Tool: o app ativa) ----
    reg.register(
        "LINE", lambda ctx, *a: LineTool(ctx), ("L",), "Desenha uma linha", "desenho"
    )
    reg.register(
        "PLINE", lambda ctx, *a: PolylineTool(ctx), ("PL", "POL"), "Desenha polilinha", "desenho"
    )
    reg.register(
        "RECT", lambda ctx, *a: RectangleTool(ctx), ("REC", "RETANGULO"),
        "Retangulo por dois cantos", "desenho",
    )
    reg.register(
        "CIRCLE", lambda ctx, *a: CircleTool(ctx), ("C", "CIRCULO"),
        "Circulo por centro e raio", "desenho",
    )
    reg.register(
        "ARC", lambda ctx, *a: ArcTool(ctx), ("A", "ARCO"),
        "Arco por tres pontos", "desenho",
    )
    reg.register(
        "TEXT", lambda ctx, *a: TextTool(ctx), ("T", "TEXTO"),
        "Insere um texto", "desenho",
    )
    reg.register(
        "HATCH", start_hatch, ("H", "HACHURA"),
        "Cria hachura por contorno selecionado ou ponto interno", "desenho",
    )
    reg.register(
        "HATCHEDIT", edit_hatch, ("HE", "HACHURAEDITAR"),
        "Edita padrao, escala, angulo, cor e ilhas", "desenho", False,
    )
    reg.register(
        "HATCHREGEN", _hatch_regen, ("HREGEN",),
        "Regenera as hachuras associativas", "desenho", False,
    )
    reg.register(
        "HATCHDISASSOCIATE", _hatch_disassociate, ("HDESASSOCIAR",),
        "Remove os vinculos da hachura selecionada", "desenho", False,
    )
    reg.register(
        "CARIMBO", start_title_block, ("TITLEBLOCK",),
        "Insere carimbo configuravel como bloco DXF", "desenho",
    )
    reg.register(
        "CARIMBOEDIT", edit_title_block, ("TITLEBLOCKEDIT",),
        "Edita os atributos do carimbo selecionado", "desenho", False,
    )

    # ---- cotas DXF nativas ----
    reg.register(
        "DIMLINEAR", lambda ctx, *a: LinearDimensionTool(ctx), ("DLI", "COTALINEAR"),
        "Cota linear horizontal/vertical automatica", "cotas",
    )
    reg.register(
        "DIMALIGNED", lambda ctx, *a: AlignedDimensionTool(ctx),
        ("DAL", "DIM", "COTAALINHADA"), "Cota alinhada a dois pontos", "cotas",
    )
    reg.register(
        "DIMROTATED", lambda ctx, *a: LinearDimensionTool(ctx, ask_angle=True),
        ("DRO", "COTAROTACIONADA"), "Cota linear em angulo informado", "cotas",
    )
    reg.register(
        "DIMHORIZONTAL", lambda ctx, *a: LinearDimensionTool(ctx, angle=0.0),
        ("DHO",), "Cota linear horizontal", "cotas",
    )
    reg.register(
        "DIMVERTICAL", lambda ctx, *a: LinearDimensionTool(ctx, angle=90.0),
        ("DVE",), "Cota linear vertical", "cotas",
    )
    reg.register(
        "DIMANGULAR", lambda ctx, *a: AngularDimensionTool(ctx), ("DAN", "COTAANGULAR"),
        "Cota angular por vertice e lados", "cotas",
    )
    reg.register(
        "DIMRADIUS", lambda ctx, *a: RadiusDimensionTool(ctx), ("DRA", "COTARAIO"),
        "Cota o raio de circulo ou arco", "cotas",
    )
    reg.register(
        "DIMDIAMETER", lambda ctx, *a: DiameterDimensionTool(ctx), ("DDI", "COTADIAMETRO"),
        "Cota o diametro de circulo ou arco", "cotas",
    )
    reg.register(
        "DIMARC", lambda ctx, *a: ArcLengthDimensionTool(ctx), ("DAR", "COTAARCO"),
        "Cota o comprimento de um arco", "cotas",
    )
    reg.register(
        "DIMORDINATE", lambda ctx, *a: OrdinateDimensionTool(ctx), ("DOR", "COTAORDENADA"),
        "Cota ordenada X ou Y", "cotas",
    )
    reg.register(
        "DIMSTYLE", _dimension_style, ("D", "COTAESTILO"),
        "Consulta/altera o estilo de cotas", "cotas", False,
    )
    reg.register(
        "DIMREASSOCIATE", lambda ctx, *a: ReassociateDimensionTool(ctx),
        ("DRE", "COTAREASSOCIAR"), "Reassocia pontos de uma cota", "cotas",
    )
    reg.register(
        "DIMDISASSOCIATE", _dimension_disassociate, ("DDA", "COTADESASSOCIAR"),
        "Remove os vinculos associativos das cotas selecionadas", "cotas", False,
    )
    reg.register(
        "DIMREGEN", _dimension_regen, (), "Regenera todas as cotas associativas", "cotas", False
    )

    # ---- modificar ----
    reg.register(
        "MOVE", lambda ctx, *a: MoveTool(ctx), ("M", "MOVER"), "Move a selecao", "modificar"
    )
    reg.register(
        "COPY", lambda ctx, *a: CopyTool(ctx), ("CO", "COPIAR"), "Copia a selecao", "modificar"
    )
    reg.register(
        "ROTATE", lambda ctx, *a: RotateTool(ctx), ("RO", "GIRAR"),
        "Gira a selecao em torno de um ponto", "modificar",
    )
    reg.register(
        "MIRROR", lambda ctx, *a: MirrorTool(ctx), ("MI", "ESPELHAR"),
        "Espelha a selecao em torno de um eixo", "modificar",
    )
    reg.register(
        "SCALE", lambda ctx, *a: ScaleTool(ctx), ("SC", "ESCALAR"),
        "Escala a selecao a partir de um ponto base", "modificar",
    )
    reg.register(
        "OFFSET", lambda ctx, *a: OffsetTool(ctx), ("O", "PARALELA"),
        "Cria uma paralela a distancia dada", "modificar",
    )
    reg.register(
        "TRIM", lambda ctx, *a: TrimTool(ctx), ("TR", "APARAR"),
        "Apara no cruzamento com outras entidades", "modificar",
    )
    reg.register(
        "EXTEND", lambda ctx, *a: ExtendTool(ctx), ("EX", "ESTENDER"),
        "Estica ate encontrar outra entidade", "modificar",
    )
    reg.register(
        "ERASE", lambda ctx, *a: EraseTool(ctx), ("E", "APAGAR"),
        "Apaga a selecao", "modificar",
    )

    # ---- selecao ----
    reg.register(
        "SELTUDO", _select_all, ("SELALL",), "Seleciona tudo que estiver visivel",
        "selecao", False,
    )
    reg.register(
        "SELNADA", _select_none, ("DESSEL",), "Limpa a selecao", "selecao", False
    )

    # ---- consulta ----
    reg.register(
        "DIST", lambda ctx, *a: DistanceTool(ctx), ("DI",), "Mede distancia e azimute", "consulta"
    )
    reg.register("AREA", lambda ctx, *a: AreaTool(ctx), (), "Mede area e perimetro", "consulta")

    # ---- vista (acao imediata: devolvem None) ----
    reg.register("ZE", _zoom_extents, ("ZOOMEXT",), "Enquadra todo o desenho", "vista", False)
    reg.register("ZOOM", _zoom, ("Z",), "ZOOM <fator> ou ZOOM E", "vista", False)
    reg.register(
        "ESCALA", _set_scale, ("PLOTESC",), "ESCALA 500 ajusta a vista para 1:500", "vista", False
    )
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


def _select_all(ctx, *args):
    visiveis = [
        e
        for e in ctx.doc.entities()
        if e.is_alive and ctx.doc.layer_is_visible(e.dxf.get("layer", "0"))
    ]
    ctx.selection.set(visiveis)
    ctx.refresh()
    ctx.message(ctx.selection.summary())


def _select_none(ctx, *args):
    ctx.selection.clear()
    ctx.refresh()
    ctx.message("Selecao limpa")


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


def _dimension_style(ctx, *args):
    """DIMSTYLE sem argumentos consulta; com chave=valor altera o estilo."""
    from dataclasses import replace

    settings = ctx.doc.dimension_style_settings()
    if not args:
        ctx.message(
            f"Estilo {ctx.doc.dimension_style_name}: texto={settings.text_height:g}, "
            f"seta={settings.arrow_size:g}, escala={settings.scale:g}, "
            f"precisao={settings.precision}, angular={settings.angular_precision}, "
            f"separador={settings.decimal_separator}, prefixo={settings.prefix!r}, "
            f"sufixo={settings.suffix!r}"
        )
        return

    aliases = {
        "texto": "text_height", "text": "text_height", "altura": "text_height",
        "seta": "arrow_size", "arrow": "arrow_size",
        "escala": "scale", "scale": "scale",
        "afastamento": "extension_offset", "offset": "extension_offset",
        "extensao": "extension_beyond", "extension": "extension_beyond",
        "gap": "text_gap", "folga": "text_gap",
        "precisao": "precision", "precision": "precision",
        "angular": "angular_precision",
        "separador": "decimal_separator", "separator": "decimal_separator",
        "zeros": "suppress_trailing_zeros",
        "prefixo": "prefix", "prefix": "prefix",
        "sufixo": "suffix", "suffix": "suffix",
    }
    changes = {}
    tokens = " ".join(str(a) for a in args).split()
    for token in tokens:
        if "=" not in token:
            ctx.message(f"Opcao invalida: {token}. Use chave=valor")
            return
        raw_key, raw_value = token.split("=", 1)
        key = aliases.get(raw_key.strip().lower())
        if key is None:
            ctx.message(f"Opcao de estilo desconhecida: {raw_key}")
            return
        value = raw_value.strip().strip('"')
        try:
            if key in ("precision", "angular_precision"):
                changes[key] = int(value)
            elif key == "suppress_trailing_zeros":
                changes[key] = value.lower() not in ("0", "nao", "não", "false", "off")
            elif key in ("decimal_separator", "prefix", "suffix"):
                changes[key] = value
            else:
                changes[key] = float(value.replace(",", "."))
        except ValueError:
            ctx.message(f"Valor invalido para {raw_key}: {raw_value}")
            return
    updated = replace(settings, **changes)
    ctx.doc.update_dimension_style(updated)
    ctx.message(f"Estilo de cotas {ctx.doc.dimension_style_name} atualizado")


def _dimension_disassociate(ctx, *args):
    from .core.associative import detach_dimension_anchor
    from .core.dimensions import DIMENSION_TYPES

    dimensions = [e for e in ctx.selection if e.dxftype() in DIMENSION_TYPES]
    if not dimensions:
        ctx.message("Selecione uma ou mais cotas")
        return
    with ctx.doc.editing(dimensions, "desassociar cotas"):
        for entity in dimensions:
            detach_dimension_anchor(entity)
    ctx.message(f"{len(dimensions)} cota(s) desassociada(s)")


def _dimension_regen(ctx, *args):
    from .core.associative import associated_dimensions

    dimensions = associated_dimensions(ctx.doc)
    changed = ctx.doc._update_associative_dimensions(dimensions=dimensions)
    if changed:
        ctx.doc._touch()
    ctx.message(f"{len(dimensions)} cota(s) associativa(s) regenerada(s)")


def _hatch_regen(ctx, *args):
    from .core.hatches import associated_hatches

    selected = [e for e in ctx.selection if e.dxftype() == "HATCH"]
    hatches = selected or associated_hatches(ctx.doc)
    changed = ctx.doc._update_associative_hatches(hatches=hatches)
    if changed:
        ctx.doc._touch()
    ctx.message(f"{len(changed)}/{len(hatches)} hachura(s) regenerada(s)")


def _hatch_disassociate(ctx, *args):
    from .core.hatches import detach_hatch

    hatches = [e for e in ctx.selection if e.dxftype() == "HATCH"]
    if not hatches:
        ctx.message("Selecione uma ou mais hachuras")
        return
    with ctx.doc.editing(hatches, "desassociar hachuras"):
        for hatch in hatches:
            detach_hatch(hatch)
    ctx.message(f"{len(hatches)} hachura(s) desassociada(s)")
