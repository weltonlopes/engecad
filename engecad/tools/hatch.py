"""Ferramentas interativas de hachura e carimbo."""

from __future__ import annotations

from PySide6.QtWidgets import QDialog

from ..core.hatches import (
    HatchSettings,
    apply_hatch_settings,
    is_closed_boundary,
    read_hatch_settings,
)
from ..core.titleblocks import (
    TitleBlockConfig,
    is_title_block,
    title_block_metadata,
    title_block_values,
    update_title_block,
)
from ..ui.hatch_dialog import HatchDialog
from ..ui.title_block_dialog import TitleBlockDialog
from .base import PointCollectorTool


class HatchPointTool(PointCollectorTool):
    name = "HATCH"
    prompt = "Indique um ponto interno da regiao"
    min_points = 1
    max_points = 1

    def __init__(self, ctx, settings: HatchSettings):
        super().__init__(ctx)
        self.settings = settings

    def commit(self) -> None:
        try:
            hatch = self.doc.add_hatch(seed=self.points[0], settings=self.settings)
        except ValueError as exc:
            self.ctx.message(str(exc))
            return
        self.ctx.selection.set([hatch])
        self.ctx.message("Hachura associativa criada")


class TitleBlockTool(PointCollectorTool):
    name = "CARIMBO"
    prompt = "Indique o canto inferior esquerdo do carimbo"
    min_points = 1
    max_points = 1

    def __init__(self, ctx, config: TitleBlockConfig):
        super().__init__(ctx)
        self.config = config

    def commit(self) -> None:
        entity = self.doc.add_title_block(self.points[0], self.config)
        self.ctx.selection.set([entity])
        self.ctx.message(
            f"Carimbo {self.config.paper} inserido na escala 1:{self.config.scale_denominator:g}"
        )


def start_hatch(ctx, *args):
    dialog = HatchDialog(parent=ctx.canvas)
    if dialog.exec() != QDialog.Accepted:
        return None
    settings = dialog.settings()
    boundaries = [e for e in ctx.selection if is_closed_boundary(e)]
    if boundaries:
        hatch = ctx.doc.add_hatch(boundaries, settings=settings)
        ctx.selection.set([hatch])
        ctx.message(f"Hachura associativa criada com {len(boundaries)} contorno(s)")
        return None
    return HatchPointTool(ctx, settings)


def edit_hatch(ctx, *args):
    hatches = [e for e in ctx.selection if e.dxftype() == "HATCH"]
    if len(hatches) != 1:
        ctx.message("Selecione uma hachura para editar")
        return None
    hatch = hatches[0]
    dialog = HatchDialog(read_hatch_settings(hatch), parent=ctx.canvas)
    if dialog.exec() == QDialog.Accepted:
        with ctx.doc.editing([hatch], "editar hachura"):
            apply_hatch_settings(hatch, dialog.settings())
        ctx.message("Hachura atualizada")
    return None


def start_title_block(ctx, *args):
    config = TitleBlockConfig(scale_denominator=ctx.viewport.scale_denominator())
    config.values["CRS"] = ctx.doc.crs.display
    dialog = TitleBlockDialog(config, parent=ctx.canvas)
    if dialog.exec() != QDialog.Accepted:
        return None
    return TitleBlockTool(ctx, dialog.config())


def edit_title_block(ctx, *args):
    inserts = [e for e in ctx.selection if is_title_block(e)]
    if len(inserts) != 1:
        ctx.message("Selecione um carimbo do EngeCAD para editar")
        return None
    insert = inserts[0]
    metadata = title_block_metadata(insert)
    config = TitleBlockConfig(
        paper=str(metadata.get("paper", "A4")),
        landscape=bool(metadata.get("landscape", True)),
        scale_denominator=float(metadata.get("scale_denominator", 1000.0)),
        values=title_block_values(insert),
    )
    dialog = TitleBlockDialog(config, parent=ctx.canvas, editing=True)
    if dialog.exec() == QDialog.Accepted:
        update_title_block(ctx.doc, insert, dialog.config())
        ctx.message("Dados do carimbo atualizados")
    return None
