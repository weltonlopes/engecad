"""Abrir e salvar: DXF (o desenho) + sidecar .emap.json (CRS, rasters, vista)."""

from __future__ import annotations

from pathlib import Path

import ezdxf

from ..core.crs import ProjectCRS
from ..core.document import Document
from .project import load_sidecar, save_sidecar


class DxfError(Exception):
    pass


def open_document(ctx, path: str | Path) -> Document:
    """Abre o DXF, aplica o sidecar e instala no contexto."""
    p = Path(path)
    try:
        doc = Document.open(p)
    except OSError as exc:
        raise DxfError(f"Nao foi possivel ler {p.name}: {exc}") from exc
    except ezdxf.DXFStructureError as exc:
        raise DxfError(f"{p.name} nao e um DXF valido: {exc}") from exc

    for layer in ctx.rasters:
        layer.close()
    ctx.rasters.clear()

    ctx.set_document(doc)
    data = load_sidecar(ctx, p)
    if data is None:
        # DXF de terceiro, sem sidecar: mantem o CRS corrente e avisa.
        ctx.message(
            f"{p.name} aberto sem sidecar .emap.json - o CRS ficou como "
            f"{doc.crs.srid}. Confira em Projeto > Sistema de coordenadas."
        )
    for layer in ctx.rasters:
        layer.set_project_crs(doc.crs)

    doc.mark_saved()
    if data is None or "view" not in (data or {}):
        ctx.zoom_extents()
    else:
        ctx.view_changed()
    return doc


def save_document(ctx, path: str | Path | None = None) -> Path:
    doc = ctx.doc
    target = Path(path) if path else doc.path
    if target is None:
        raise DxfError("documento sem caminho: use Salvar como")
    if target.suffix.lower() != ".dxf":
        target = target.with_suffix(".dxf")
    try:
        doc.save(target)
        save_sidecar(ctx, target)
    except OSError as exc:
        raise DxfError(f"Falha ao salvar {target.name}: {exc}") from exc
    return target


def new_document(ctx, crs: str | ProjectCRS = "EPSG:31982") -> Document:
    for layer in ctx.rasters:
        layer.close()
    ctx.rasters.clear()
    doc = Document.new(crs)
    ctx.set_document(doc)
    ctx.viewport.set_scale(1.0)
    ctx.view_changed()
    return doc
