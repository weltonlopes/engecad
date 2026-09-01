from __future__ import annotations

from engecad.core.document import Document
from engecad.core.titleblocks import (
    TitleBlockConfig,
    is_title_block,
    title_block_metadata,
    title_block_values,
    update_title_block,
)


def test_title_block_is_native_insert_with_attributes():
    doc = Document.new()
    insert = doc.add_title_block(
        (100, 200),
        TitleBlockConfig(
            paper="A3",
            landscape=True,
            scale_denominator=500,
            values={"TITULO": "PLANTA CADASTRAL", "RESPONSAVEL": "Eng. Ada"},
        ),
    )

    assert insert.dxftype() == "INSERT"
    assert is_title_block(insert)
    assert insert.dxf.name == "ENGECAD_CARIMBO_A3_PAISAGEM"
    assert insert.dxf.xscale == 500
    values = title_block_values(insert)
    assert values["TITULO"] == "PLANTA CADASTRAL"
    assert values["RESPONSAVEL"] == "Eng. Ada"
    assert values["ESCALA"] == "1:500"
    assert len(values) == 13
    assert title_block_metadata(insert)["paper"] == "A3"


def test_portrait_and_landscape_use_distinct_reusable_definitions():
    doc = Document.new()
    a = doc.add_title_block((0, 0), TitleBlockConfig(paper="A4", landscape=False))
    b = doc.add_title_block((1, 1), TitleBlockConfig(paper="A4", landscape=False))
    c = doc.add_title_block((2, 2), TitleBlockConfig(paper="A4", landscape=True))
    assert a.dxf.name == b.dxf.name
    assert a.dxf.name != c.dxf.name


def test_editing_attributes_is_undoable():
    doc = Document.new()
    insert = doc.add_title_block(
        (0, 0), TitleBlockConfig(values={"TITULO": "ANTES", "REVISAO": "00"})
    )
    update_title_block(
        doc,
        insert,
        TitleBlockConfig(
            values={**title_block_values(insert), "TITULO": "DEPOIS", "REVISAO": "01"}
        ),
    )
    assert title_block_values(insert)["TITULO"] == "DEPOIS"
    assert doc.undo.undo()
    assert title_block_values(insert)["TITULO"] == "ANTES"
    assert doc.undo.redo()
    assert title_block_values(insert)["REVISAO"] == "01"


def test_title_block_roundtrip_and_audit(tmp_path):
    path = tmp_path / "carimbo.dxf"
    doc = Document.new()
    doc.add_title_block(
        (500000, 7000000),
        TitleBlockConfig(
            paper="A1",
            scale_denominator=2000,
            values={"TITULO": "LEVANTAMENTO", "CRS": "SIRGAS 2000 / UTM 22S"},
        ),
    )
    assert not doc.drawing.audit().has_errors
    doc.save(path)

    reopened = Document.open(path)
    insert = next(e for e in reopened.entities() if is_title_block(e))
    assert title_block_values(insert)["TITULO"] == "LEVANTAMENTO"
    assert title_block_metadata(insert)["scale_denominator"] == 2000
    assert not reopened.drawing.audit().has_errors
