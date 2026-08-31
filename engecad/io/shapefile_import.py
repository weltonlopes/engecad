"""Importacao de shapefile (.shp) para entidades do documento corrente.

Le com pyshp, que e puro Python -- ao contrario de fiona/OGR, nao carrega um
segundo GDAL no processo (ver o comentario no topo de raster_import.py sobre
por que isso e perigoso quando o rasterio ja trouxe o seu).

CRS: se houver um .prj ao lado do .shp, os pontos sao reprojetados para o CRS
do projeto: as coordenadas do desenho tem sempre que estar no CRS do doc,
igual ao raster. Sem .prj, assume-se que o shapefile ja esta no CRS do
projeto (mesma politica do DXF de terceiro sem sidecar).
"""

from __future__ import annotations

from pathlib import Path

import shapefile

from ..core.crs import ProjectCRS

POLYLINE_TYPES = {
    shapefile.POLYLINE,
    shapefile.POLYLINEZ,
    shapefile.POLYLINEM,
}
POLYGON_TYPES = {
    shapefile.POLYGON,
    shapefile.POLYGONZ,
    shapefile.POLYGONM,
}
POINT_TYPES = {
    shapefile.POINT,
    shapefile.POINTZ,
    shapefile.POINTM,
}
MULTIPOINT_TYPES = {
    shapefile.MULTIPOINT,
    shapefile.MULTIPOINTZ,
    shapefile.MULTIPOINTM,
}


class ShapefileImportError(Exception):
    pass


class ImportResult:
    def __init__(self, layer: str):
        self.layer = layer
        self.created = 0
        self.skipped = 0
        self.source_crs: ProjectCRS | None = None
        self.reprojected = False

    @property
    def summary(self) -> str:
        msg = f"{self.created} entidade(s) importada(s) na camada {self.layer}"
        if self.skipped:
            msg += f", {self.skipped} ignorada(s)"
        if self.source_crs is not None:
            msg += f"\nCRS de origem: {self.source_crs.srid}"
            msg += " (reprojetado)" if self.reprojected else " (igual ao do projeto)"
        else:
            msg += "\nSem .prj: assumido o CRS do projeto"
        return msg


def read_prj_crs(shp_path: str | Path) -> ProjectCRS | None:
    """CRS declarado no .prj ao lado do .shp, se existir e for reconhecivel."""
    prj = Path(shp_path).with_suffix(".prj")
    if not prj.exists():
        return None
    try:
        wkt = prj.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not wkt or not ProjectCRS.is_valid(wkt):
        return None
    return ProjectCRS(wkt)


def shapefile_fields(path: str | Path) -> list[str]:
    """Nomes dos campos de atributo, na ordem do .dbf (sem o DeletionFlag)."""
    try:
        with shapefile.Reader(str(path)) as sf:
            return [f[0] for f in sf.fields[1:]]
    except shapefile.ShapefileException as exc:
        raise ShapefileImportError(f"Nao foi possivel ler {Path(path).name}: {exc}") from exc


def _iter_parts(shape) -> list[list[tuple[float, float]]]:
    points = shape.points
    if not points:
        return []
    parts = list(shape.parts) + [len(points)]
    return [points[parts[i] : parts[i + 1]] for i in range(len(parts) - 1)]


def _project(points, transform):
    if transform is None:
        return points
    return [transform(x, y) for x, y in points]


def _add_shape(doc, shape, layer: str, transform) -> int:
    stype = shape.shapeType
    if stype == shapefile.NULL or not shape.points:
        return 0

    if stype in POINT_TYPES:
        x, y = _project([shape.points[0]], transform)[0]
        doc.add_point((x, y), layer=layer)
        return 1

    if stype in MULTIPOINT_TYPES:
        pts = _project(shape.points, transform)
        for x, y in pts:
            doc.add_point((x, y), layer=layer)
        return len(pts)

    if stype in POLYLINE_TYPES or stype in POLYGON_TYPES:
        closed = stype in POLYGON_TYPES
        made = 0
        for part in _iter_parts(shape):
            pts = _project(part, transform)
            if len(pts) < 2:
                continue
            doc.add_lwpolyline(pts, closed=closed, layer=layer)
            made += 1
        return made

    return 0


def import_shapefile(
    ctx,
    path: str | Path,
    layer: str | None = None,
    attribute_field: str | None = None,
) -> ImportResult:
    """Importa geometrias de um .shp como entidades DXF, num unico item de desfazer.

    layer: camada de destino para tudo (default: nome do arquivo, maiusculo).
    attribute_field: se dado, cada registro vai para uma camada nomeada pelo
    valor desse campo (util para separar LIMITE/DIVISA/EDIFICACAO vindos de
    um mesmo shapefile de cadastro).
    """
    p = Path(path)
    if not p.exists():
        raise ShapefileImportError(f"Arquivo nao encontrado: {p}")

    try:
        sf = shapefile.Reader(str(p))
    except shapefile.ShapefileException as exc:
        raise ShapefileImportError(f"Nao foi possivel ler {p.name}: {exc}") from exc

    doc = ctx.doc
    default_layer = (layer or p.stem).strip().upper() or "0"
    doc.ensure_layer(default_layer)

    src_crs = read_prj_crs(p)
    transform = None
    reprojected = False
    if src_crs is not None and src_crs != doc.crs:
        transformer = src_crs.transformer_to(doc.crs)
        transform = transformer.transform
        reprojected = True

    result = ImportResult(default_layer)
    result.source_crs = src_crs
    result.reprojected = reprojected

    field_idx = None
    if attribute_field:
        fields = [f[0] for f in sf.fields[1:]]
        if attribute_field in fields:
            field_idx = fields.index(attribute_field)

    doc.undo.begin_macro(f"importar {p.name}")
    try:
        for sr in sf.iterShapeRecords():
            entity_layer = default_layer
            if field_idx is not None:
                raw = str(sr.record[field_idx]).strip()
                if raw:
                    entity_layer = raw.upper()
                    doc.ensure_layer(entity_layer)
            made = _add_shape(doc, sr.shape, entity_layer, transform)
            if made:
                result.created += made
            else:
                result.skipped += 1
    finally:
        doc.undo.end_macro()
        sf.close()

    return result
