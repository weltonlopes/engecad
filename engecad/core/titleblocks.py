"""Carimbos configuraveis como blocos DXF com atributos editaveis."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date

from .geometry import Vec2

APPID = "ENGECAD_TITLEBLOCK"
BLOCK_PREFIX = "ENGECAD_CARIMBO_"
PAPER_MM = {
    "A0": (1189.0, 841.0),
    "A1": (841.0, 594.0),
    "A2": (594.0, 420.0),
    "A3": (420.0, 297.0),
    "A4": (297.0, 210.0),
}
FIELD_LABELS = {
    "TITULO": "TITULO",
    "PROJETO": "PROJETO",
    "CLIENTE": "CLIENTE / PROPRIETARIO",
    "RESPONSAVEL": "RESPONSAVEL TECNICO",
    "CREA_CAU": "CREA / CAU",
    "MUNICIPIO": "MUNICIPIO",
    "IMOVEL": "IMOVEL",
    "MATRICULA": "MATRICULA",
    "ESCALA": "ESCALA",
    "DATA": "DATA",
    "FOLHA": "FOLHA",
    "REVISAO": "REV.",
    "CRS": "SISTEMA DE REFERENCIA",
}


@dataclass(slots=True)
class TitleBlockConfig:
    paper: str = "A4"
    landscape: bool = True
    scale_denominator: float = 1000.0
    values: dict[str, str] = field(default_factory=dict)

    def normalized_values(self) -> dict[str, str]:
        values = {tag: str(self.values.get(tag, "")) for tag in FIELD_LABELS}
        values["ESCALA"] = values["ESCALA"] or f"1:{self.scale_denominator:g}"
        values["DATA"] = values["DATA"] or date.today().strftime("%d/%m/%Y")
        values["FOLHA"] = values["FOLHA"] or "01/01"
        values["REVISAO"] = values["REVISAO"] or "00"
        return values


def is_title_block(entity) -> bool:
    return entity.dxftype() == "INSERT" and str(entity.dxf.get("name", "")).startswith(BLOCK_PREFIX)


def title_block_values(insert) -> dict[str, str]:
    return {str(attrib.dxf.tag): str(attrib.dxf.text) for attrib in insert.attribs}


def title_block_metadata(insert) -> dict:
    try:
        tags = insert.get_xdata(APPID)
        return json.loads(next(t.value for t in tags if t.code == 1000))
    except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError):
        return {}


def _block_name(paper: str, landscape: bool) -> str:
    return f"{BLOCK_PREFIX}{paper}_{'PAISAGEM' if landscape else 'RETRATO'}"


def _paper_size(paper: str, landscape: bool) -> tuple[float, float]:
    try:
        long_side, short_side = PAPER_MM[paper.upper()]
    except KeyError as exc:
        raise ValueError(f"formato de papel desconhecido: {paper}") from exc
    width, height = (long_side, short_side) if landscape else (short_side, long_side)
    return width / 1000.0, height / 1000.0


def _line(block, a, b) -> None:
    block.add_line(a, b, dxfattribs={"layer": "0", "color": 0})


def _text(block, text: str, at, height: float = 0.0022) -> None:
    entity = block.add_text(text, height=height, dxfattribs={"layer": "0", "color": 0})
    entity.set_placement(at)


def _attdef(block, tag: str, at, height: float = 0.0032) -> None:
    block.add_attdef(
        tag,
        insert=at,
        text="",
        height=height,
        dxfattribs={"layer": "0", "color": 0, "prompt": FIELD_LABELS[tag]},
    )


def ensure_title_block_definition(drawing, paper: str, landscape: bool):
    paper = paper.upper()
    name = _block_name(paper, landscape)
    if name in drawing.blocks:
        return drawing.blocks.get(name)
    block = drawing.blocks.new(name=name, base_point=(0.0, 0.0))
    width, height = _paper_size(paper, landscape)
    margin = 0.005
    _line(block, (0, 0), (width, 0))
    _line(block, (width, 0), (width, height))
    _line(block, (width, height), (0, height))
    _line(block, (0, height), (0, 0))
    _line(block, (margin, margin), (width - margin, margin))
    _line(block, (width - margin, margin), (width - margin, height - margin))
    _line(block, (width - margin, height - margin), (margin, height - margin))
    _line(block, (margin, height - margin), (margin, margin))

    # Quadro inferior dividido em campos. A definicao esta em metros de papel;
    # o INSERT recebe a escala da planta (ex.: x1000 para desenho 1:1000).
    x0, x1 = margin, width - margin
    y0, y1 = margin, min(height - margin, margin + 0.076)
    mid = x0 + (x1 - x0) * 0.62
    _line(block, (x0, y1), (x1, y1))
    _line(block, (mid, y0), (mid, y1))
    rows = 6
    row_h = (y1 - y0) / rows
    for i in range(1, rows):
        y = y0 + row_h * i
        _line(block, (x0, y), (x1, y))

    left = ["TITULO", "PROJETO", "CLIENTE", "MUNICIPIO", "IMOVEL", "MATRICULA"]
    right = ["RESPONSAVEL", "CREA_CAU", "CRS", "ESCALA", "DATA", "FOLHA"]
    for col_x, tags in ((x0, left), (mid, right)):
        col_width = (mid - x0) if col_x == x0 else (x1 - mid)
        for row, tag in enumerate(tags):
            y = y1 - row_h * (row + 1)
            _text(block, FIELD_LABELS[tag], (col_x + 0.002, y + row_h - 0.003), 0.0018)
            _attdef(block, tag, (col_x + 0.002, y + 0.0025), min(0.003, col_width / 35))

    # Revisao divide o ultimo campo direito, sem sacrificar a folha.
    split = x1 - (x1 - mid) * 0.22
    _line(block, (split, y0), (split, y0 + row_h))
    _text(block, FIELD_LABELS["REVISAO"], (split + 0.001, y0 + row_h - 0.003), 0.0018)
    _attdef(block, "REVISAO", (split + 0.001, y0 + 0.0025), 0.003)
    return block


def add_title_block(doc, insert, config: TitleBlockConfig):
    config.paper = config.paper.upper()
    block = ensure_title_block_definition(doc.drawing, config.paper, config.landscape)
    point = Vec2.of(insert)
    scale = max(float(config.scale_denominator), 1e-9)
    entity = doc.msp.add_blockref(
        block.name,
        (point.x, point.y),
        dxfattribs={"layer": doc.current_layer, "xscale": scale, "yscale": scale, "zscale": scale},
    )
    entity.add_auto_attribs(config.normalized_values())
    if APPID not in doc.drawing.appids:
        doc.drawing.appids.new(APPID)
    metadata = {
        "version": 1,
        "paper": config.paper,
        "landscape": bool(config.landscape),
        "scale_denominator": scale,
    }
    entity.set_xdata(APPID, [(1000, json.dumps(metadata, separators=(",", ":")))])
    return doc._register_new(entity, "carimbo")


def update_title_block(doc, insert, config: TitleBlockConfig) -> None:
    if not is_title_block(insert):
        raise ValueError("a entidade selecionada nao e um carimbo do EngeCAD")
    values = config.normalized_values()
    with doc.editing([insert], "editar carimbo"):
        for attrib in insert.attribs:
            if attrib.dxf.tag in values:
                attrib.dxf.text = values[attrib.dxf.tag]
