from __future__ import annotations

import math

from engecad.context import AppContext
from engecad.core.blocks import (
    DynamicParameters,
    InsertOptions,
    annotative_metadata,
    attribute_definitions,
    block_attribute_values,
    create_block_definition,
    dynamic_parameters,
    explode_insert,
    insert_block,
    is_annotative,
    make_dynamic,
    set_block_attributes,
    set_dynamic_parameters,
    write_block_file,
)
from engecad.core.document import Document
from engecad.core.symbols import SYMBOLS, ensure_symbol_definition, insert_symbol
from engecad.core.titleblocks import TitleBlockConfig, title_block_values
from engecad.io.project import load_sidecar, save_sidecar


def test_create_native_block_uses_local_coordinates():
    doc = Document.new()
    line = doc.add_line((100, 200), (110, 200))
    block = create_block_definition(doc, "TRECHO", [line], base=(100, 200))

    primitive = next(iter(block))
    assert primitive.dxftype() == "LINE"
    assert primitive.dxf.start.isclose((0, 0, 0))
    assert primitive.dxf.end.isclose((10, 0, 0))
    assert block.block.dxf.base_point.isclose((0, 0, 0))


def test_insert_is_native_and_populates_attdefs():
    doc = Document.new()
    block = doc.drawing.blocks.new("PONTO", base_point=(0, 0))
    block.add_circle((0, 0), 1)
    block.add_attdef(
        "ID",
        (1.2, 0),
        text="SEM_ID",
        height=0.25,
        dxfattribs={"prompt": "Identificador"},
    )

    insert = insert_block(
        doc,
        "PONTO",
        (500, 600),
        InsertOptions(scale_x=2, scale_y=3, rotation=30, attributes={"ID": "P-01"}),
    )
    assert insert.dxftype() == "INSERT"
    assert insert.dxf.name == "PONTO"
    assert insert.dxf.insert.isclose((500, 600, 0))
    assert insert.dxf.xscale == 2
    assert insert.dxf.yscale == 3
    assert block_attribute_values(insert) == {"ID": "P-01"}
    assert attribute_definitions(doc, "PONTO")[0].prompt == "Identificador"


def test_attribute_edit_is_undoable():
    doc = Document.new()
    block = doc.drawing.blocks.new("ETIQUETA")
    block.add_attdef("CODIGO", (0, 0), text="-", height=0.2)
    insert = insert_block(doc, "ETIQUETA", (0, 0), InsertOptions(attributes={"CODIGO": "A"}))

    set_block_attributes(doc, insert, {"CODIGO": "B"})
    assert block_attribute_values(insert)["CODIGO"] == "B"
    assert doc.undo.undo()
    assert block_attribute_values(insert)["CODIGO"] == "A"
    assert doc.undo.redo()
    assert block_attribute_values(insert)["CODIGO"] == "B"


def test_recursive_explosion_converts_attributes_to_text_and_undoes():
    doc = Document.new()
    inner = doc.drawing.blocks.new("INTERNO")
    inner.add_circle((0, 0), 1)
    outer = doc.drawing.blocks.new("EXTERNO")
    outer.add_blockref("INTERNO", (2, 0))
    outer.add_attdef("NOME", (0, 0), text="-", height=0.2)
    insert = insert_block(doc, "EXTERNO", (10, 20), InsertOptions(attributes={"NOME": "LOTE"}))

    made = explode_insert(doc, insert)
    assert {entity.dxftype() for entity in made} == {"CIRCLE", "TEXT"}
    assert insert not in list(doc.entities())
    assert any(entity.dxf.text == "LOTE" for entity in made if entity.dxftype() == "TEXT")
    assert doc.undo.undo()
    assert insert in list(doc.entities())
    assert all(entity not in list(doc.entities()) for entity in made)


def test_wblock_selection_writes_translated_standalone_dxf(tmp_path):
    doc = Document.new()
    line = doc.add_line((100, 200), (110, 200))
    path = write_block_file(doc, tmp_path / "trecho", entities=[line], base=(100, 200))

    reopened = Document.open(path)
    exported = next(reopened.entities())
    assert exported.dxf.start.isclose((0, 0, 0))
    assert exported.dxf.end.isclose((10, 0, 0))
    assert not reopened.drawing.audit().has_errors


def test_wblock_definition_includes_attributes(tmp_path):
    doc = Document.new()
    block = doc.drawing.blocks.new("MARCO")
    block.add_circle((0, 0), 1)
    block.add_attdef("ID", (1, 0), text="M", height=0.2)
    path = write_block_file(doc, tmp_path / "marco.dxf", block_name="MARCO")

    reopened = Document.open(path)
    assert {entity.dxftype() for entity in reopened.entities()} == {"CIRCLE", "ATTDEF"}
    assert not reopened.drawing.audit().has_errors


def test_symbol_library_creates_native_annotative_symbols():
    doc = Document.new()
    insert = insert_symbol(
        doc,
        "PONTO_TOPOGRAFICO",
        (0, 0),
        attributes={"PONTO": "101", "COTA": "734.25"},
        annotation_scale=500,
    )
    assert len(SYMBOLS) >= 12
    assert insert.dxf.name == "ENGECAD_SIMB_PONTO_TOPOGRAFICO"
    assert is_annotative(insert)
    assert math.isclose(insert.dxf.xscale, 2.0)  # 4 mm em 1:500 = 2 m
    assert block_attribute_values(insert)["PONTO"] == "101"
    assert not doc.drawing.audit().has_errors


def test_annotation_scale_updates_all_tagged_symbols_only():
    doc = Document.new()
    symbol = insert_symbol(doc, "POSTE", (0, 0), annotation_scale=500)
    ordinary_block = doc.drawing.blocks.new("COMUM")
    ordinary_block.add_line((0, 0), (1, 0))
    ordinary = insert_block(doc, "COMUM", (10, 0), InsertOptions(scale_x=7, scale_y=7))

    changed = doc.set_annotation_scale(1000)
    assert changed == [symbol]
    assert symbol.dxf.xscale == 4.0
    assert annotative_metadata(symbol)["scale_denominator"] == 1000
    assert ordinary.dxf.xscale == 7


def test_simplified_dynamic_parameters_and_visibility_are_undoable():
    doc = Document.new()
    primary, variants = ensure_symbol_definition(doc, "PORTAO")
    insert = insert_block(doc, primary, (0, 0))
    make_dynamic(insert, variants=variants)

    set_dynamic_parameters(
        doc,
        insert,
        DynamicParameters(
            stretch_x=2.5,
            stretch_y=0.75,
            rotation=35,
            flip_x=True,
            visibility="DUPLO",
        ),
    )
    params = dynamic_parameters(insert)
    assert params is not None
    assert params.flip_x
    assert insert.dxf.xscale == -2.5
    assert insert.dxf.yscale == 0.75
    assert insert.dxf.rotation == 35
    assert insert.dxf.name.endswith("_DUPLO")

    assert doc.undo.undo()
    assert insert.dxf.name == primary
    assert dynamic_parameters(insert) is not None  # volta ao estado dinamico inicial


def test_dynamic_transform_moves_attached_attributes_and_undo_restores_them():
    doc = Document.new()
    block = doc.drawing.blocks.new("ROTULO")
    block.add_line((0, 0), (1, 0))
    block.add_attdef("ID", (1, 0), text="A", height=0.2)
    insert = insert_block(doc, "ROTULO", (10, 20), InsertOptions(attributes={"ID": "R1"}))
    original = insert.attribs[0].dxf.insert

    set_dynamic_parameters(
        doc,
        insert,
        DynamicParameters(stretch_x=2, stretch_y=2, rotation=90),
    )
    moved = insert.attribs[0].dxf.insert
    assert moved.isclose((10, 22, 0))
    assert doc.undo.undo()
    assert insert.attribs[0].dxf.insert.isclose(original)


def test_title_block_uses_project_attributes_as_defaults():
    doc = Document.new()
    doc.project_attributes.update(
        {"TITULO": "PLANTA DO IMOVEL", "CLIENTE": "Maria", "MATRICULA": "12.345"}
    )
    insert = doc.add_title_block((0, 0), TitleBlockConfig(scale_denominator=1000))
    values = title_block_values(insert)
    assert values["TITULO"] == "PLANTA DO IMOVEL"
    assert values["CLIENTE"] == "Maria"
    assert values["MATRICULA"] == "12.345"


def test_blocks_roundtrip_with_xdata_and_audit(tmp_path):
    path = tmp_path / "blocos.dxf"
    doc = Document.new()
    insert_symbol(doc, "PORTAO", (100, 200), state="DUPLO", annotation_scale=750)
    doc.save(path)

    reopened = Document.open(path)
    loaded = next(entity for entity in reopened.entities() if entity.dxftype() == "INSERT")
    assert dynamic_parameters(loaded).visibility == "DUPLO"
    assert annotative_metadata(loaded)["scale_denominator"] == 750
    assert loaded.dxf.name.endswith("_DUPLO")
    assert not reopened.drawing.audit().has_errors


def test_project_attributes_and_annotation_scale_persist_in_sidecar(tmp_path):
    path = tmp_path / "projeto.dxf"
    source = AppContext(Document.new())
    source.doc.project_attributes.update({"PROJETO": "Rodovia BR-000", "CLIENTE": "DNIT"})
    source.doc.annotation_scale = 2500
    source.doc.save(path)
    save_sidecar(source, path)

    target = AppContext(Document.open(path))
    assert load_sidecar(target, path) is not None
    assert target.doc.project_attributes["PROJETO"] == "Rodovia BR-000"
    assert target.doc.project_attributes["CLIENTE"] == "DNIT"
    assert target.doc.annotation_scale == 2500
