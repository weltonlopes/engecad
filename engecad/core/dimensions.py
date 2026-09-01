"""Apoio a cotas DXF nativas.

As cotas permanecem entidades ``DIMENSION``/``ARC_DIMENSION`` de verdade.  A
representacao grafica gravada no bloco anonimo e usada tanto pelo AutoCAD como
pelo canvas do EngeCAD, portanto o arquivo nao precisa ser explodido para ser
visualizado ou editado.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from ezdxf.disassemble import recursive_decompose

DIMSTYLE_NAME = "ENGECAD_METRICO"
DIMENSION_TYPES = {"DIMENSION", "ARC_DIMENSION"}


@dataclass(frozen=True)
class DimensionStyleSettings:
    text_height: float = 0.25
    arrow_size: float = 0.25
    scale: float = 1.0
    extension_offset: float = 0.125
    extension_beyond: float = 0.25
    text_gap: float = 0.10
    precision: int = 2
    angular_precision: int = 2
    decimal_separator: str = ","
    suppress_trailing_zeros: bool = True
    prefix: str = ""
    suffix: str = ""


def ensure_dimension_style(drawing, name: str = DIMSTYLE_NAME):
    """Devolve o estilo metrico do EngeCAD, criando-o quando necessario."""
    if name in drawing.dimstyles:
        return drawing.dimstyles.get(name)
    source = "EZDXF" if "EZDXF" in drawing.dimstyles else "Standard"
    style = drawing.dimstyles.duplicate_entry(source, name)
    apply_dimension_style(style, DimensionStyleSettings())
    return style


def _split_post(value: str) -> tuple[str, str]:
    if "<>" not in value:
        return value, ""
    return tuple(value.split("<>", 1))


def read_dimension_style(style) -> DimensionStyleSettings:
    dxf = style.dxf
    prefix, suffix = _split_post(str(dxf.get("dimpost", "<>") or "<>"))
    separator = chr(int(dxf.get("dimdsep", ord(",")) or ord(",")))
    return DimensionStyleSettings(
        text_height=float(dxf.get("dimtxt", 0.25) or 0.25),
        arrow_size=float(dxf.get("dimasz", 0.25) or 0.25),
        scale=float(dxf.get("dimscale", 1.0) or 1.0),
        extension_offset=float(dxf.get("dimexo", 0.125) or 0.0),
        extension_beyond=float(dxf.get("dimexe", 0.25) or 0.0),
        text_gap=float(dxf.get("dimgap", 0.10) or 0.0),
        precision=int(dxf.get("dimdec", 2) or 0),
        angular_precision=int(dxf.get("dimadec", 2) or 0),
        decimal_separator=separator if separator in (".", ",") else ",",
        suppress_trailing_zeros=bool(int(dxf.get("dimzin", 0) or 0) & 8),
        prefix=prefix,
        suffix=suffix,
    )


def apply_dimension_style(style, settings: DimensionStyleSettings) -> None:
    """Aplica o subconjunto de DIMSTYLE que o editor expoe ao usuario."""
    dxf = style.dxf
    dxf.dimtxt = max(float(settings.text_height), 1e-9)
    dxf.dimasz = max(float(settings.arrow_size), 1e-9)
    dxf.dimscale = max(float(settings.scale), 1e-9)
    dxf.dimexo = max(float(settings.extension_offset), 0.0)
    dxf.dimexe = max(float(settings.extension_beyond), 0.0)
    dxf.dimgap = max(float(settings.text_gap), 0.0)
    dxf.dimdec = max(0, min(8, int(settings.precision)))
    dxf.dimadec = max(0, min(8, int(settings.angular_precision)))
    separator = settings.decimal_separator if settings.decimal_separator in (".", ",") else ","
    dxf.dimdsep = ord(separator)
    # bit 3 suprime zeros a direita; os demais bits ficam intocados
    zin = int(dxf.get("dimzin", 0) or 0)
    dxf.dimzin = (zin | 8) if settings.suppress_trailing_zeros else (zin & ~8)
    dxf.dimpost = f"{settings.prefix}<>{settings.suffix}"
    dxf.dimlunit = 2  # decimal
    dxf.dimaunit = 0  # graus decimais
    dxf.dimtad = 1  # texto acima da linha
    dxf.dimtofl = 1  # mantem a linha entre as linhas de chamada
    dxf.dimtmove = 2  # mover texto sem criar chamada adicional
    # Nome vazio e o identificador DXF da seta fechada e preenchida padrao.
    style.set_arrows(blk="")


def dimension_primitives(entity):
    """Entidades simples que formam a representacao grafica da cota."""
    if entity.dxftype() not in DIMENSION_TYPES:
        return iter(())
    try:
        return iter(recursive_decompose([entity]))
    except (TypeError, ValueError, AttributeError):
        return iter(())


def rerender_dimension(entity) -> bool:
    """Reconstrói o bloco grafico depois de editar pontos de definicao."""
    if entity.dxftype() not in DIMENSION_TYPES or entity.doc is None:
        return False
    try:
        override = entity.override()
        # O ezdxf nao reidrata a pseudo-propriedade ``user_location`` apenas
        # pelo bit 128. Sem reaplica-la, qualquer regen/style apagaria a
        # posicao de texto que o usuario arrastou.
        if int(entity.dxf.get("dimtype", 0) or 0) & 128 and entity.dxf.hasattr(
            "text_midpoint"
        ):
            override.set_location(
                entity.dxf.text_midpoint,
                leader=int(override.get("dimtmove", 2) or 2) == 1,
            )
        override.render()
        return True
    except (TypeError, ValueError, AttributeError):
        return False


def dimension_kind(entity) -> str:
    """Nome amigavel do tipo DXF base (bits de estado sao ignorados)."""
    if entity.dxftype() == "ARC_DIMENSION":
        return "comprimento de arco"
    kind = int(entity.dxf.get("dimtype", 0) or 0) & 15
    return {
        0: "linear",
        1: "alinhada",
        2: "angular",
        3: "diametro",
        4: "raio",
        5: "angular (3 pontos)",
        6: "ordenada",
    }.get(kind, "cota")


def dimension_anchor_attributes(entity) -> tuple[str, ...]:
    """Pontos de definicao que podem ser associados a geometria externa."""
    if entity.dxftype() == "ARC_DIMENSION":
        return ("defpoint4", "defpoint2", "defpoint3")
    kind = int(entity.dxf.get("dimtype", 0) or 0) & 15
    return {
        0: ("defpoint2", "defpoint3"),
        1: ("defpoint2", "defpoint3"),
        2: ("defpoint2", "defpoint3", "defpoint4"),
        3: ("defpoint", "defpoint4"),
        4: ("defpoint", "defpoint4"),
        5: ("defpoint4", "defpoint2", "defpoint3"),
        6: ("defpoint2",),
    }.get(kind, ())


def dimension_measurement(entity) -> float:
    """Valor escalar medido, inclusive para tipos que o ezdxf devolve em tupla."""
    if entity.dxftype() == "ARC_DIMENSION":
        dxf = entity.dxf
        center = dxf.defpoint4
        p1, p2 = dxf.defpoint2, dxf.defpoint3
        radius = math.hypot(p1.x - center.x, p1.y - center.y)
        a0 = math.atan2(p1.y - center.y, p1.x - center.x)
        a1 = math.atan2(p2.y - center.y, p2.x - center.x)
        return radius * ((a1 - a0) % math.tau)
    value = entity.get_measurement()
    if isinstance(value, (tuple, list)) or hasattr(value, "x"):
        x, y = float(value[0]), float(value[1])
        return x if int(entity.dxf.get("dimtype", 0) or 0) & 64 else y
    return float(value)
