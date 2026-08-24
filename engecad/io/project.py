"""Sidecar .emap.json -- o que o DXF nao sabe guardar.

O desenho e um DXF puro (abre no AutoCAD sem exportar). O que o formato DXF
nao carrega bem -- CRS do projeto, quais rasters estao carregados e a ultima
vista -- fica num JSON ao lado, com o mesmo nome base.
"""

from __future__ import annotations

import json
from pathlib import Path

from .. import __version__
from ..core.crs import ProjectCRS
from ..core.geometry import Vec2

SIDECAR_SUFFIX = ".emap.json"


def sidecar_for(dxf_path: str | Path) -> Path:
    p = Path(dxf_path)
    return p.with_name(p.stem + SIDECAR_SUFFIX)


def _rel(path: Path, base: Path) -> str:
    """Guarda caminho relativo ao DXF quando possivel: projeto continua
    funcionando se a pasta inteira for movida ou copiada."""
    try:
        return str(Path(path).resolve().relative_to(base.resolve()))
    except (ValueError, OSError):
        return str(path)


def _abs(stored: str, base: Path) -> Path:
    p = Path(stored)
    return p if p.is_absolute() else (base / p)


def save_sidecar(ctx, dxf_path: str | Path) -> Path:
    dxf_path = Path(dxf_path)
    base = dxf_path.parent
    vp = ctx.viewport
    data = {
        "engecad": __version__,
        "crs": ctx.doc.crs.srid,
        "crs_wkt": ctx.doc.crs.to_wkt(),
        "current_layer": ctx.doc.current_layer,
        "view": {"center": [vp.center.x, vp.center.y], "scale": vp.scale},
        "rasters": [
            {
                "path": _rel(r.path, base),
                "source": str(r.source) if r.source else None,
                "visible": bool(r.visible),
                "opacity": float(r.opacity),
            }
            for r in ctx.rasters
        ],
    }
    out = sidecar_for(dxf_path)
    out.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return out


def load_sidecar(ctx, dxf_path: str | Path) -> dict | None:
    """Aplica o sidecar ao contexto. Devolve o dict lido, ou None se nao existir."""
    path = sidecar_for(dxf_path)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        ctx.message(f"Sidecar ilegivel: {path.name}")
        return None

    base = Path(dxf_path).parent

    srid = data.get("crs")
    if srid and ProjectCRS.is_valid(srid):
        ctx.doc.crs = ProjectCRS(srid)
    elif data.get("crs_wkt") and ProjectCRS.is_valid(data["crs_wkt"]):
        ctx.doc.crs = ProjectCRS(data["crs_wkt"])

    layer = data.get("current_layer")
    if layer and layer in ctx.doc.layer_names():
        ctx.doc.current_layer = layer

    from ..render.raster_layer import RasterLayer

    for item in data.get("rasters", []):
        p = _abs(item.get("path", ""), base)
        if not p.exists():
            ctx.message(f"Raster nao encontrado: {p}")
            continue
        try:
            layer_obj = RasterLayer(p, source=item.get("source"))
            layer_obj.visible = bool(item.get("visible", True))
            layer_obj.opacity = float(item.get("opacity", 1.0))
            ctx.rasters.append(layer_obj)
        except Exception as exc:
            ctx.message(f"Falha ao recarregar {p.name}: {exc}")

    view = data.get("view") or {}
    if "center" in view and "scale" in view:
        c = view["center"]
        ctx.viewport.center = Vec2(float(c[0]), float(c[1]))
        ctx.viewport.set_scale(float(view["scale"]))

    ctx.rastersChanged.emit()
    return data
