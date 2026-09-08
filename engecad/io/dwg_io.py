"""Adaptação do leitor DWG nativo para o modelo editável do EngeCAD.

O parser binário está em ``engecad.dwg`` e não depende de ezdxf para ler o
arquivo. ezdxf é usado apenas como o modelo geométrico interno já adotado
pelas ferramentas de edição e renderização do aplicativo.
"""

from __future__ import annotations

import math
from pathlib import Path

from ..core.document import Document
from ..dwg import DwgError, read
from ..dwg.entities import iter_entities as decode_entities


def load_document(path: str | Path) -> Document:
    """Lê e materializa um DWG sem tocar na interface.

    A função é deliberadamente independente de ``AppContext``. Isso permite
    que a leitura pesada rode em uma thread de trabalho e que a instalação no
    documento visível aconteça somente na thread Qt principal.
    """
    p = Path(path)
    try:
        native = read(p)
    except (OSError, DwgError) as exc:
        raise DwgError(f"{p.name} não é um DWG válido ou suportado: {exc}") from exc

    doc = Document.new()
    doc.path = p
    decoded = 0
    assert native.object_index is not None
    # ENTMode=2 is the Model Space stream.  ENTMode=0 also contains valid
    # geometry, but it belongs to block definitions (and ENTMode=1 to paper
    # space); importing those records into Model Space both duplicates INSERT
    # contents and can make Zoom Extents span unrelated coordinate systems.
    model_entities = list(decode_entities(native.object_index, {2}))
    if not model_entities:
        # A few legacy/proprietary writers omit the mode bit.  Keep the useful
        # fallback rather than reporting an empty drawing, while still
        # excluding records explicitly marked as paper space.
        model_entities = list(decode_entities(native.object_index, {0, 2}))

    for entity in model_entities:
        layer = "0"
        data = entity.data
        if entity.type == "LINE":
            doc.msp.add_line(data["start"], data["end"], dxfattribs={"layer": layer})
        elif entity.type == "CIRCLE":
            doc.msp.add_circle(
                data["center"], data["radius"], dxfattribs={"layer": layer}
            )
        elif entity.type == "ARC":
            doc.msp.add_arc(
                data["center"],
                data["radius"],
                math.degrees(data["start_angle"]),
                math.degrees(data["end_angle"]),
                dxfattribs={"layer": layer},
            )
        elif entity.type == "POINT":
            doc.msp.add_point(data["point"], dxfattribs={"layer": layer})
        elif entity.type == "LWPOLYLINE":
            vertices = data["vertices"]
            if vertices:
                doc.msp.add_lwpolyline(
                    vertices,
                    close=bool(data["flags"] & 1),
                    dxfattribs={"layer": layer},
                )
        else:
            continue
        decoded += 1

    doc.rebuild_index()
    doc.mark_saved()
    if decoded:
        return doc
    raise DwgError(f"{p.name} não contém entidades geométricas suportadas")


def install_document(ctx, doc: Document) -> Document:
    """Troca o documento visível; deve ser chamado na thread da interface."""
    for raster in ctx.rasters:
        raster.close()
    ctx.rasters.clear()
    ctx.set_document(doc)
    for raster in ctx.rasters:
        raster.set_project_crs(doc.crs)
    ctx.zoom_extents()
    return doc


def open_document(ctx, path: str | Path) -> Document:
    return install_document(ctx, load_document(path))
