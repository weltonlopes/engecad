"""Ferramentas e acoes de BLOCK, INSERT, WBLOCK, EXPLODE e atributos."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog

from ..core.blocks import (
    InsertOptions,
    create_block_definition,
    explode_insert,
    set_block_attributes,
    set_dynamic_parameters,
    write_block_file,
)
from ..core.symbols import insert_symbol, symbol_spec
from ..core.titleblocks import update_title_blocks_from_project
from ..ui.block_dialogs import (
    AttributeEditorDialog,
    BlockInsertConfig,
    BlockInsertDialog,
    DynamicBlockDialog,
    ProjectAttributesDialog,
    ask_block_definition,
)
from .base import PointCollectorTool


class BlockBasePointTool(PointCollectorTool):
    name = "BLOCK"
    prompt = "Indique o ponto base do bloco"
    min_points = 1
    max_points = 1

    def __init__(self, ctx, name: str, description: str, entities: list):
        super().__init__(ctx)
        self.block_name = name
        self.description = description
        self.entities = entities

    def snap_exclude(self):
        return ()

    def commit(self) -> None:
        base = self.points[0]
        try:
            create_block_definition(
                self.doc,
                self.block_name,
                self.entities,
                base,
                description=self.description,
            )
        except ValueError as exc:
            self.ctx.message(str(exc))
            return
        self.doc.undo.begin_macro("criar bloco")
        try:
            insert = self.doc.insert_block(self.block_name, base)
            self.doc.delete(self.entities)
        finally:
            self.doc.undo.end_macro()
        self.ctx.selection.set([insert])
        self.ctx.message(f"Bloco {self.block_name} criado com {len(self.entities)} objeto(s)")


class BlockInsertTool(PointCollectorTool):
    name = "INSERT"
    prompt = "Indique o ponto de insercao"
    min_points = 1
    max_points = 1

    def __init__(self, ctx, config: BlockInsertConfig):
        super().__init__(ctx)
        self.config = config

    def commit(self) -> None:
        point = self.points[0]
        config = self.config
        if config.source_type == "symbol":
            insert = insert_symbol(
                self.doc,
                config.source_name,
                point,
                attributes=config.options.attributes,
                annotation_scale=config.options.annotation_scale,
                state=config.visibility,
                stretch_x=config.options.scale_x,
                stretch_y=config.options.scale_y,
                rotation=config.options.rotation,
                paper_size_mm=config.options.paper_size_mm,
            )
        else:
            insert = self.doc.insert_block(config.source_name, point, config.options)
        self.ctx.selection.set([insert])
        self.ctx.message(f"Bloco {insert.dxf.name} inserido")


class WBlockBasePointTool(PointCollectorTool):
    name = "WBLOCK"
    prompt = "Indique o ponto base do arquivo externo"
    min_points = 1
    max_points = 1

    def __init__(self, ctx, entities: list, path: Path):
        super().__init__(ctx)
        self.entities = entities
        self.path = path

    def commit(self) -> None:
        result = write_block_file(
            self.doc, self.path, entities=self.entities, base=self.points[0]
        )
        self.ctx.message(f"WBLOCK gravado: {result.name}")


def start_block(ctx, *args):
    entities = list(ctx.selection)
    if not entities:
        ctx.message("Selecione os objetos que formarao o bloco")
        return None
    if args:
        name = str(args[0]).strip()
        description = " ".join(str(arg) for arg in args[1:])
    else:
        requested = ask_block_definition(ctx.canvas)
        if requested is None:
            return None
        name, description = requested
    if name in ctx.doc.drawing.blocks:
        ctx.message(f"O bloco {name!r} ja existe")
        return None
    return BlockBasePointTool(ctx, name, description, entities)


def start_insert(ctx, *args, symbols_only=False):
    if args:
        name = str(args[0]).strip()
        source_type = "symbol" if symbols_only else "block"
        if source_type == "block" and name not in ctx.doc.drawing.blocks:
            ctx.message(f"Bloco inexistente: {name}")
            return None
        if source_type == "symbol":
            try:
                symbol_spec(name)
            except ValueError as exc:
                ctx.message(str(exc))
                return None
        return BlockInsertTool(
            ctx,
            BlockInsertConfig(
                source_type,
                name,
                InsertOptions(annotation_scale=ctx.doc.annotation_scale),
            ),
        )
    dialog = BlockInsertDialog(ctx.doc, parent=ctx.canvas, symbols_only=symbols_only)
    if dialog.exec() != QDialog.Accepted:
        return None
    return BlockInsertTool(ctx, dialog.config())


def start_symbol(ctx, *args):
    return start_insert(ctx, *args, symbols_only=True)


def run_wblock(ctx, *args):
    entities = list(ctx.selection)
    if not entities:
        ctx.message("Selecione um bloco ou objetos para WBLOCK")
        return None
    if args:
        path = Path(str(args[0]))
    else:
        filename, _ = QFileDialog.getSaveFileName(
            ctx.canvas, "Gravar bloco", "", "DXF (*.dxf)"
        )
        if not filename:
            return None
        path = Path(filename)
    if len(entities) == 1 and entities[0].dxftype() == "INSERT":
        result = write_block_file(ctx.doc, path, block_name=str(entities[0].dxf.name))
        ctx.message(f"WBLOCK gravado: {result.name}")
        return None
    return WBlockBasePointTool(ctx, entities, path)


def run_explode(ctx, *args):
    inserts = [entity for entity in ctx.selection if entity.dxftype() == "INSERT"]
    if not inserts:
        ctx.message("Selecione uma ou mais referencias de bloco")
        return None
    made = []
    ctx.doc.undo.begin_macro("explodir blocos")
    try:
        for insert in inserts:
            made.extend(explode_insert(ctx.doc, insert))
    finally:
        ctx.doc.undo.end_macro()
    ctx.selection.set(made)
    ctx.message(f"{len(inserts)} bloco(s) explodido(s) em {len(made)} objeto(s)")
    return None


def run_attribute_edit(ctx, *args):
    inserts = [entity for entity in ctx.selection if entity.dxftype() == "INSERT"]
    if len(inserts) != 1:
        ctx.message("Selecione uma referencia de bloco")
        return None
    insert = inserts[0]
    if not insert.attribs:
        ctx.message("O bloco selecionado nao possui atributos")
        return None
    dialog = AttributeEditorDialog(insert, ctx.canvas)
    if dialog.exec() == QDialog.Accepted:
        set_block_attributes(ctx.doc, insert, dialog.values())
        ctx.message("Atributos atualizados")
    return None


def run_dynamic_edit(ctx, *args):
    inserts = [entity for entity in ctx.selection if entity.dxftype() == "INSERT"]
    if len(inserts) != 1:
        ctx.message("Selecione uma referencia de bloco")
        return None
    insert = inserts[0]
    dialog = DynamicBlockDialog(insert, ctx.canvas)
    if dialog.exec() == QDialog.Accepted:
        set_dynamic_parameters(ctx.doc, insert, dialog.parameters())
        ctx.message("Parametros dinamicos atualizados")
    return None


def run_project_attributes(ctx, *args):
    dialog = ProjectAttributesDialog(ctx.doc.project_attributes, ctx.canvas)
    if dialog.exec() != QDialog.Accepted:
        return None
    ctx.doc.project_attributes.update(dialog.values())
    ctx.doc.project_attributes["CRS"] = ctx.doc.crs.display
    count = update_title_blocks_from_project(ctx.doc)
    ctx.doc._touch()
    ctx.message(f"Dados do projeto salvos; {count} carimbo(s) atualizado(s)")
    return None


def run_annotation_scale(ctx, *args):
    if args:
        raw = str(args[0]).replace("1:", "").replace(":", "")
        try:
            scale = float(raw)
        except ValueError:
            ctx.message(f"Escala invalida: {args[0]}")
            return None
    else:
        scale, ok = QInputDialog.getDouble(
            ctx.canvas,
            "Escala anotativa",
            "Denominador (1:n):",
            ctx.doc.annotation_scale,
            1.0,
            1e9,
            2,
        )
        if not ok:
            return None
    changed = ctx.doc.set_annotation_scale(scale)
    ctx.message(f"Escala anotativa 1:{scale:g}; {len(changed)} simbolo(s) atualizado(s)")
    return None
