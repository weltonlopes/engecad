"""Biblioteca interna de simbolos topograficos e cadastrais."""

from __future__ import annotations

from dataclasses import dataclass

from .blocks import AttributeDefinition

PREFIX = "ENGECAD_SIMB_"


@dataclass(frozen=True, slots=True)
class SymbolSpec:
    key: str
    label: str
    category: str
    description: str
    paper_size_mm: float = 5.0
    attributes: tuple[AttributeDefinition, ...] = ()
    visibility_states: tuple[str, ...] = ()

    @property
    def block_name(self) -> str:
        return PREFIX + self.key


SYMBOLS = (
    SymbolSpec(
        "PONTO_TOPOGRAFICO",
        "Ponto topografico",
        "Topografia",
        "Ponto levantado com identificador e cota",
        4.0,
        (AttributeDefinition("PONTO", "Numero do ponto"), AttributeDefinition("COTA", "Cota")),
    ),
    SymbolSpec(
        "MARCO_GEODESICO",
        "Marco geodesico",
        "Topografia",
        "Marco de apoio geodesico",
        6.0,
        (AttributeDefinition("ID", "Identificacao"), AttributeDefinition("COTA", "Cota")),
    ),
    SymbolSpec(
        "ESTACAO",
        "Estacao total",
        "Topografia",
        "Estacao de levantamento",
        6.0,
        (AttributeDefinition("ESTACAO", "Nome da estacao"),),
    ),
    SymbolSpec(
        "RN",
        "Referencia de nivel",
        "Topografia",
        "Referencia de nivel altimetrica",
        5.0,
        (AttributeDefinition("RN", "Identificacao"), AttributeDefinition("COTA", "Cota")),
    ),
    SymbolSpec("NORTE", "Seta de norte", "Topografia", "Indicacao do norte da planta", 12.0),
    SymbolSpec(
        "DIVISA",
        "Marco de divisa",
        "Cadastro",
        "Marco ou vertice de divisa",
        4.0,
        (AttributeDefinition("VERTICE", "Vertice"),),
    ),
    SymbolSpec("POSTE", "Poste", "Cadastro", "Poste de energia ou telecomunicacao", 4.0),
    SymbolSpec("ARVORE", "Arvore", "Cadastro", "Arvore isolada", 6.0),
    SymbolSpec("HIDRANTE", "Hidrante", "Cadastro", "Hidrante urbano", 5.0),
    SymbolSpec("BOCA_LOBO", "Boca de lobo", "Cadastro", "Dispositivo de drenagem", 5.0),
    SymbolSpec(
        "PV",
        "Poco de visita",
        "Cadastro",
        "Poco de visita de rede",
        5.0,
        (AttributeDefinition("ID", "Identificacao"),),
    ),
    SymbolSpec("CERCA", "Cerca", "Cadastro", "Indicador pontual de cerca", 5.0),
    SymbolSpec(
        "PORTAO",
        "Portao",
        "Cadastro",
        "Portao com estado simples ou duplo",
        8.0,
        visibility_states=("SIMPLES", "DUPLO"),
    ),
    SymbolSpec(
        "EDIFICACAO",
        "Identificacao de edificacao",
        "Cadastro",
        "Etiqueta de edificacao",
        8.0,
        (AttributeDefinition("NUMERO", "Numero"), AttributeDefinition("USO", "Uso")),
    ),
)

_BY_KEY = {spec.key: spec for spec in SYMBOLS}


def symbol_spec(key: str) -> SymbolSpec:
    try:
        return _BY_KEY[str(key).upper()]
    except KeyError as exc:
        raise ValueError(f"simbolo desconhecido: {key}") from exc


def symbol_categories() -> list[str]:
    return sorted({spec.category for spec in SYMBOLS})


def symbols_in_category(category: str | None = None) -> list[SymbolSpec]:
    return [spec for spec in SYMBOLS if category is None or spec.category == category]


def _attrs() -> dict:
    return {"layer": "0", "color": 0}


def _line(block, a, b) -> None:
    block.add_line(a, b, dxfattribs=_attrs())


def _circle(block, center, radius) -> None:
    block.add_circle(center, radius, dxfattribs=_attrs())


def _text(block, value, at, height=0.28) -> None:
    entity = block.add_text(value, height=height, dxfattribs=_attrs())
    entity.set_placement(at)


def _geometry(block, key: str, state: str = "") -> None:
    if key == "PONTO_TOPOGRAFICO":
        _circle(block, (0, 0), 0.22)
        _line(block, (-0.45, 0), (0.45, 0))
        _line(block, (0, -0.45), (0, 0.45))
    elif key == "MARCO_GEODESICO":
        block.add_lwpolyline(
            [(-0.48, -0.35), (0.48, -0.35), (0, 0.5)], close=True, dxfattribs=_attrs()
        )
        _circle(block, (0, 0), 0.16)
    elif key == "ESTACAO":
        _circle(block, (0, 0.25), 0.2)
        _line(block, (0, 0.05), (-0.45, -0.5))
        _line(block, (0, 0.05), (0.45, -0.5))
        _line(block, (-0.28, -0.28), (0.28, -0.28))
    elif key == "RN":
        block.add_lwpolyline(
            [(-0.5, 0.25), (0.5, 0.25), (0, -0.4)], close=True, dxfattribs=_attrs()
        )
        _line(block, (-0.5, 0.42), (0.5, 0.42))
    elif key == "NORTE":
        block.add_lwpolyline(
            [(-0.18, -0.5), (0, 0.55), (0.18, -0.5), (0, -0.28)], close=True, dxfattribs=_attrs()
        )
        _text(block, "N", (-0.12, 0.62), 0.38)
    elif key == "DIVISA":
        _circle(block, (0, 0), 0.3)
        _circle(block, (0, 0), 0.08)
    elif key == "POSTE":
        _circle(block, (0, 0), 0.42)
        _line(block, (-0.3, -0.3), (0.3, 0.3))
        _line(block, (-0.3, 0.3), (0.3, -0.3))
    elif key == "ARVORE":
        _circle(block, (0, 0), 0.5)
        _circle(block, (0, 0), 0.18)
        for a, b in (
            ((0.18, 0), (0.5, 0)),
            ((-0.18, 0), (-0.5, 0)),
            ((0, 0.18), (0, 0.5)),
            ((0, -0.18), (0, -0.5)),
        ):
            _line(block, a, b)
    elif key == "HIDRANTE":
        block.add_lwpolyline(
            [(-0.38, -0.45), (0.38, -0.45), (0.38, 0.35), (-0.38, 0.35)],
            close=True,
            dxfattribs=_attrs(),
        )
        _line(block, (-0.5, 0.1), (0.5, 0.1))
        _line(block, (0, 0.35), (0, 0.52))
    elif key == "BOCA_LOBO":
        block.add_lwpolyline(
            [(-0.5, -0.3), (0.5, -0.3), (0.5, 0.3), (-0.5, 0.3)], close=True, dxfattribs=_attrs()
        )
        for x in (-0.3, -0.1, 0.1, 0.3):
            _line(block, (x, -0.3), (x, 0.3))
    elif key == "PV":
        _circle(block, (0, 0), 0.48)
        _circle(block, (0, 0), 0.36)
        _text(block, "PV", (-0.2, -0.1), 0.24)
    elif key == "CERCA":
        _line(block, (-0.5, 0), (0.5, 0))
        _line(block, (-0.35, -0.3), (-0.05, 0.3))
        _line(block, (0.05, -0.3), (0.35, 0.3))
    elif key == "PORTAO":
        if state == "DUPLO":
            _line(block, (-0.5, -0.4), (0, 0))
            _line(block, (0.5, -0.4), (0, 0))
            block.add_arc((-0.5, -0.4), 0.64, 0, 39, dxfattribs=_attrs())
            block.add_arc((0.5, -0.4), 0.64, 141, 180, dxfattribs=_attrs())
        else:
            _line(block, (-0.5, -0.4), (0.5, 0.4))
            block.add_arc((-0.5, -0.4), 1.28, 0, 39, dxfattribs=_attrs())
    elif key == "EDIFICACAO":
        block.add_lwpolyline(
            [(-0.5, -0.35), (0.5, -0.35), (0.5, 0.35), (-0.5, 0.35)],
            close=True,
            dxfattribs=_attrs(),
        )


def _definition_name(spec: SymbolSpec, state: str = "") -> str:
    return spec.block_name + (f"_{state}" if state else "")


def ensure_symbol_definition(doc, key: str) -> tuple[str, dict[str, str]]:
    spec = symbol_spec(key)
    states = spec.visibility_states or ("",)
    variants: dict[str, str] = {}
    for state in states:
        name = _definition_name(spec, state)
        if state:
            variants[state] = name
        if name in doc.drawing.blocks:
            continue
        block = doc.drawing.blocks.new(name=name, base_point=(0, 0))
        block.block.dxf.description = spec.description
        _geometry(block, spec.key, state)
        for index, definition in enumerate(spec.attributes):
            block.add_attdef(
                definition.tag,
                insert=(0.58, 0.18 - index * 0.3),
                text=definition.default,
                height=0.22,
                dxfattribs={
                    **_attrs(),
                    "prompt": definition.prompt or definition.tag,
                },
            )
    primary = variants.get(states[0], spec.block_name)
    return primary, variants


def insert_symbol(
    doc,
    key: str,
    point,
    *,
    attributes=None,
    annotation_scale=None,
    state="",
    stretch_x=1.0,
    stretch_y=1.0,
    rotation=0.0,
    paper_size_mm=None,
):
    from .blocks import DynamicParameters, InsertOptions, insert_block, make_dynamic

    spec = symbol_spec(key)
    primary, variants = ensure_symbol_definition(doc, key)
    if state and state in variants:
        primary = variants[state]
    scale = float(annotation_scale or doc.annotation_scale)
    insert = insert_block(
        doc,
        primary,
        point,
        InsertOptions(
            attributes=attributes or {},
            annotative=True,
            paper_size_mm=float(paper_size_mm or spec.paper_size_mm),
            annotation_scale=scale,
        ),
    )
    make_dynamic(
        insert,
        DynamicParameters(
            stretch_x=float(stretch_x),
            stretch_y=float(stretch_y),
            rotation=float(rotation),
            visibility=state or (spec.visibility_states[0] if variants else ""),
        ),
        variants=variants,
    )
    doc._index_update(insert)
    return insert
