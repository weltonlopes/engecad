"""Blocos DXF, atributos, explosao e comportamento dinamico/anotativo.

Os recursos adicionais do EngeCAD sao gravados como XDATA sobre INSERTs
comuns. Assim o arquivo continua sendo um DXF convencional: outros CADs veem
o bloco com sua escala/rotacao final mesmo sem conhecer os metadados.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import ezdxf
from ezdxf.addons import Importer
from ezdxf.disassemble import recursive_decompose
from ezdxf.lldxf.validator import is_valid_block_name
from ezdxf.math import Matrix44

from .entities import invalidate_primitives
from .geometry import Vec2

DYNAMIC_APPID = "ENGECAD_DYNAMIC"
ANNOTATIVE_APPID = "ENGECAD_ANNOTATIVE"


@dataclass(slots=True)
class AttributeDefinition:
    tag: str
    prompt: str = ""
    default: str = ""


@dataclass(slots=True)
class InsertOptions:
    scale_x: float = 1.0
    scale_y: float = 1.0
    rotation: float = 0.0
    attributes: dict[str, str] | None = None
    annotative: bool = False
    paper_size_mm: float = 5.0
    annotation_scale: float = 1000.0
    dynamic: bool = False


@dataclass(slots=True)
class DynamicParameters:
    stretch_x: float = 1.0
    stretch_y: float = 1.0
    rotation: float = 0.0
    flip_x: bool = False
    flip_y: bool = False
    visibility: str = ""


def _ensure_appid(drawing, name: str) -> None:
    if name not in drawing.appids:
        drawing.appids.new(name)


def _set_json_xdata(entity, appid: str, payload: dict) -> None:
    _ensure_appid(entity.doc, appid)
    entity.set_xdata(appid, [(1000, json.dumps(payload, separators=(",", ":")))])


def _get_json_xdata(entity, appid: str) -> dict:
    try:
        tags = entity.get_xdata(appid)
        return json.loads(next(tag.value for tag in tags if tag.code == 1000))
    except (ValueError, KeyError, StopIteration, TypeError, json.JSONDecodeError):
        return {}


def validate_block_name(name: str) -> str:
    name = str(name).strip()
    if not name or name.startswith("*"):
        raise ValueError("nome de bloco invalido")
    if not is_valid_block_name(name):
        raise ValueError(f"nome de bloco invalido: {name}")
    return name


def block_names(doc, include_anonymous: bool = False) -> list[str]:
    names = []
    for block in doc.drawing.blocks:
        name = str(block.name)
        if include_anonymous or not name.startswith("*"):
            names.append(name)
    return sorted(set(names), key=str.casefold)


def attribute_definitions(doc, block_name: str) -> list[AttributeDefinition]:
    try:
        block = doc.drawing.blocks.get(block_name)
    except Exception:
        return []
    out = []
    for entity in block:
        if entity.dxftype() == "ATTDEF":
            out.append(
                AttributeDefinition(
                    tag=str(entity.dxf.tag),
                    prompt=str(entity.dxf.get("prompt", "") or entity.dxf.tag),
                    default=str(entity.dxf.get("text", "")),
                )
            )
    return out


def block_attribute_values(insert) -> dict[str, str]:
    return {str(attrib.dxf.tag): str(attrib.dxf.text) for attrib in insert.attribs}


def create_block_definition(
    doc,
    name: str,
    entities,
    base=(0, 0),
    *,
    description: str = "",
):
    """Cria uma definicao a partir de entidades, transladada para a origem local."""
    name = validate_block_name(name)
    if name in doc.drawing.blocks:
        raise ValueError(f"o bloco {name!r} ja existe")
    items = [entity for entity in entities if entity is not None and entity.is_alive]
    if not items:
        raise ValueError("selecione entidades para criar o bloco")
    point = Vec2.of(base)
    block = doc.drawing.blocks.new(name=name, base_point=(0, 0))
    block.block.dxf.description = str(description)
    transform = Matrix44.translate(-point.x, -point.y, 0.0)
    try:
        for entity in items:
            clone = entity.copy()
            clone.transform(transform)
            if clone.dxftype() == "HATCH":
                clone.remove_association()
            block.add_entity(clone)
    except Exception:
        doc.drawing.blocks.delete_block(name, safe=False)
        raise
    invalidate_primitives()
    doc.invalidate_all_geometry()
    doc._touch()
    return block


def insert_block(doc, name: str, point, options: InsertOptions | None = None):
    options = options or InsertOptions()
    if name not in doc.drawing.blocks:
        raise ValueError(f"bloco inexistente: {name}")
    p = Vec2.of(point)
    insert = doc.msp.add_blockref(
        name,
        (p.x, p.y),
        dxfattribs={
            "layer": doc.current_layer,
            "rotation": float(options.rotation),
            "xscale": float(options.scale_x),
            "yscale": float(options.scale_y),
            "zscale": 1.0,
        },
    )
    values = {
        definition.tag: definition.default
        for definition in attribute_definitions(doc, name)
    }
    values.update({str(k): str(v) for k, v in (options.attributes or {}).items()})
    if values:
        insert.add_auto_attribs(values)
    if options.annotative:
        set_annotative_metadata(
            insert,
            options.paper_size_mm,
            options.annotation_scale,
            factor_x=abs(float(options.scale_x)),
            factor_y=abs(float(options.scale_y)),
            flip_x=float(options.scale_x) < 0,
            flip_y=float(options.scale_y) < 0,
            rotation=float(options.rotation),
            apply=False,
        )
    if options.dynamic:
        _set_dynamic_metadata(
            insert,
            DynamicParameters(
                stretch_x=abs(float(options.scale_x)),
                stretch_y=abs(float(options.scale_y)),
                rotation=float(options.rotation),
                flip_x=float(options.scale_x) < 0,
                flip_y=float(options.scale_y) < 0,
            ),
            base_scale=1.0,
            original_name=name,
            variants={},
        )
    _apply_insert_parameters(insert)
    return doc._register_new(insert, "inserir bloco")


def set_block_attributes(doc, insert, values: dict[str, str], name="editar atributos") -> None:
    if insert.dxftype() != "INSERT":
        raise ValueError("a entidade nao e uma referencia de bloco")
    normalized = {str(tag): str(value) for tag, value in values.items()}
    with doc.editing([insert], name):
        for attrib in insert.attribs:
            if attrib.dxf.tag in normalized:
                attrib.dxf.text = normalized[attrib.dxf.tag]


def sync_block_attributes(doc, insert) -> None:
    """Acrescenta ATTDEFs ausentes preservando os valores ja preenchidos."""
    existing = block_attribute_values(insert)
    definitions = attribute_definitions(doc, insert.dxf.name)
    missing = [definition for definition in definitions if definition.tag not in existing]
    if not missing:
        return
    # add_auto_attribs posiciona corretamente conforme escala e rotacao do INSERT.
    insert.add_auto_attribs({definition.tag: definition.default for definition in missing})
    doc._index_update(insert)
    doc._touch()


def _text_from_attrib(layout, attrib):
    dxf = attrib.dxf
    text = layout.add_text(
        str(dxf.text),
        height=float(dxf.get("height", 1.0) or 1.0),
        dxfattribs={
            "layer": dxf.get("layer", "0"),
            "color": dxf.get("color", 256),
            "rotation": float(dxf.get("rotation", 0.0) or 0.0),
            "style": dxf.get("style", "Standard"),
        },
    )
    text.set_placement(dxf.insert)
    return text


def explode_insert(doc, insert) -> list:
    """Explode recursivamente sem destruir o INSERT, portanto com undo exato."""
    if insert.dxftype() != "INSERT" or not insert.is_alive:
        raise ValueError("selecione uma referencia de bloco")
    primitives = list(recursive_decompose([insert]))
    made = []
    doc.undo.begin_macro("explodir bloco")
    try:
        for primitive in primitives:
            t = primitive.dxftype()
            if t == "ATTRIB":
                entity = _text_from_attrib(doc.msp, primitive)
            elif t == "ATTDEF":
                continue
            else:
                entity = primitive.copy()
                doc.msp.add_entity(entity)
                if t == "HATCH":
                    entity.remove_association()
            doc._index_add(entity)
            made.append(entity)
        if made:
            from .document import AddEntities

            doc.undo.push(AddEntities(doc, made, "geometria explodida"), execute=False)
        doc.delete([insert])
    finally:
        doc.undo.end_macro()
    return made


def write_block_file(
    doc,
    path: str | Path,
    *,
    block_name: str | None = None,
    entities=None,
    base=(0, 0),
) -> Path:
    """Grava uma definicao ou selecao como DXF independente (WBLOCK)."""
    target_path = Path(path)
    if target_path.suffix.lower() != ".dxf":
        target_path = target_path.with_suffix(".dxf")
    if block_name is not None:
        if block_name not in doc.drawing.blocks:
            raise ValueError(f"bloco inexistente: {block_name}")
        source = list(doc.drawing.blocks.get(block_name))
        translation = Matrix44()
    else:
        source = [entity for entity in (entities or ()) if entity is not None and entity.is_alive]
        if not source:
            raise ValueError("nao ha entidades para gravar")
        p = Vec2.of(base)
        translation = Matrix44.translate(-p.x, -p.y, 0.0)

    target = ezdxf.new(doc.drawing.dxfversion, setup=True)
    target.header["$INSUNITS"] = doc.drawing.header.get("$INSUNITS", 6)
    importer = Importer(doc.drawing, target)
    importer.import_entities(source, target.modelspace())
    importer.finalize()
    if block_name is None:
        for entity in target.modelspace():
            try:
                entity.transform(translation)
            except (NotImplementedError, AttributeError):
                pass
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target.saveas(str(target_path))
    return target_path


def dynamic_metadata(insert) -> dict:
    return _get_json_xdata(insert, DYNAMIC_APPID)


def dynamic_parameters(insert) -> DynamicParameters | None:
    data = dynamic_metadata(insert)
    if not data:
        return None
    return DynamicParameters(
        stretch_x=float(data.get("stretch_x", 1.0)),
        stretch_y=float(data.get("stretch_y", 1.0)),
        rotation=float(data.get("rotation", insert.dxf.get("rotation", 0.0) or 0.0)),
        flip_x=bool(data.get("flip_x", False)),
        flip_y=bool(data.get("flip_y", False)),
        visibility=str(data.get("visibility", "")),
    )


def _set_dynamic_metadata(
    insert,
    params: DynamicParameters,
    *,
    base_scale: float,
    original_name: str,
    variants: dict[str, str],
) -> None:
    _set_json_xdata(
        insert,
        DYNAMIC_APPID,
        {
            "version": 1,
            "base_scale": float(base_scale),
            "original_name": str(original_name),
            "variants": {str(k): str(v) for k, v in variants.items()},
            "stretch_x": max(float(params.stretch_x), 1e-9),
            "stretch_y": max(float(params.stretch_y), 1e-9),
            "rotation": float(params.rotation),
            "flip_x": bool(params.flip_x),
            "flip_y": bool(params.flip_y),
            "visibility": str(params.visibility),
        },
    )


def make_dynamic(
    insert,
    params: DynamicParameters | None = None,
    *,
    variants: dict[str, str] | None = None,
) -> None:
    base = abs(float(insert.dxf.get("xscale", 1.0) or 1.0))
    if params is None:
        xscale = float(insert.dxf.get("xscale", 1.0) or 1.0)
        yscale = float(insert.dxf.get("yscale", 1.0) or 1.0)
        params = DynamicParameters(
            stretch_x=1.0,
            stretch_y=abs(yscale) / max(base, 1e-9),
            rotation=float(insert.dxf.get("rotation", 0.0) or 0.0),
            flip_x=xscale < 0,
            flip_y=yscale < 0,
        )
    _set_dynamic_metadata(
        insert,
        params,
        base_scale=base,
        original_name=str(insert.dxf.name),
        variants=variants or {},
    )
    _apply_insert_parameters(insert)


def set_dynamic_parameters(doc, insert, params: DynamicParameters) -> None:
    if insert.dxftype() != "INSERT":
        raise ValueError("a entidade nao e uma referencia de bloco")
    previous = dynamic_metadata(insert)
    with doc.editing([insert], "editar bloco dinamico"):
        _set_dynamic_metadata(
            insert,
            params,
            base_scale=float(previous.get("base_scale", abs(insert.dxf.xscale) or 1.0)),
            original_name=str(previous.get("original_name", insert.dxf.name)),
            variants=dict(previous.get("variants", {})),
        )
        _apply_insert_parameters(insert)


def annotative_metadata(insert) -> dict:
    return _get_json_xdata(insert, ANNOTATIVE_APPID)


def set_annotative_metadata(
    insert,
    paper_size_mm: float,
    scale_denominator: float,
    *,
    factor_x: float = 1.0,
    factor_y: float = 1.0,
    flip_x: bool = False,
    flip_y: bool = False,
    rotation: float | None = None,
    apply: bool = True,
) -> None:
    _set_json_xdata(
        insert,
        ANNOTATIVE_APPID,
        {
            "version": 1,
            "paper_size_mm": max(float(paper_size_mm), 0.01),
            "scale_denominator": max(float(scale_denominator), 1.0),
            "factor_x": max(float(factor_x), 1e-9),
            "factor_y": max(float(factor_y), 1e-9),
            "flip_x": bool(flip_x),
            "flip_y": bool(flip_y),
            "rotation": float(
                insert.dxf.get("rotation", 0.0) if rotation is None else rotation
            ),
        },
    )
    if apply:
        _apply_insert_parameters(insert)


def is_annotative(insert) -> bool:
    return bool(annotative_metadata(insert))


def _base_scale(insert) -> float:
    annotation = annotative_metadata(insert)
    if annotation:
        return annotation["paper_size_mm"] * annotation["scale_denominator"] / 1000.0
    dynamic = dynamic_metadata(insert)
    return float(dynamic.get("base_scale", abs(insert.dxf.get("xscale", 1.0) or 1.0)))


def _apply_insert_parameters(insert) -> None:
    old_matrix = insert.matrix44()
    data = dynamic_metadata(insert)
    base = _base_scale(insert)
    if data:
        sx = max(float(data.get("stretch_x", 1.0)), 1e-9)
        sy = max(float(data.get("stretch_y", 1.0)), 1e-9)
        insert.dxf.xscale = -base * sx if data.get("flip_x") else base * sx
        insert.dxf.yscale = -base * sy if data.get("flip_y") else base * sy
        insert.dxf.rotation = float(data.get("rotation", 0.0))
        variants = data.get("variants", {})
        state = str(data.get("visibility", ""))
        target_name = variants.get(state) or data.get("original_name")
        if target_name and target_name in insert.doc.blocks:
            insert.dxf.name = target_name
    elif annotative_metadata(insert):
        annotation = annotative_metadata(insert)
        sign_x = -1.0 if annotation.get("flip_x") else 1.0
        sign_y = -1.0 if annotation.get("flip_y") else 1.0
        insert.dxf.xscale = sign_x * base * float(annotation.get("factor_x", 1.0))
        insert.dxf.yscale = sign_y * base * float(annotation.get("factor_y", 1.0))
        insert.dxf.rotation = float(annotation.get("rotation", 0.0))
    new_matrix = insert.matrix44()
    try:
        inverse = old_matrix.copy()
        inverse.inverse()
        for attrib in insert.attribs:
            attrib.transform(inverse)
            attrib.transform(new_matrix)
    except (ZeroDivisionError, NotImplementedError):
        pass


def update_annotative_symbols(doc, scale_denominator: float) -> list:
    scale = max(float(scale_denominator), 1.0)
    changed = []
    for insert in doc.msp.query("INSERT"):
        data = annotative_metadata(insert)
        if not data:
            continue
        data["scale_denominator"] = scale
        _set_json_xdata(insert, ANNOTATIVE_APPID, data)
        _apply_insert_parameters(insert)
        doc._index_update(insert)
        changed.append(insert)
    if changed:
        doc._touch()
    return changed
