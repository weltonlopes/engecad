"""Importacao de shapefile: geometria, camadas, CRS via .prj."""

from __future__ import annotations

import pytest
import shapefile
from pyproj import CRS

from engecad.core.crs import ProjectCRS
from engecad.core.document import Document
from engecad.io.shapefile_import import (
    ShapefileImportError,
    import_shapefile,
    read_prj_crs,
    shapefile_fields,
)

E, N = 500000.0, 7400000.0


class _Ctx:
    """Stub minimo: import_shapefile so usa ctx.doc."""

    def __init__(self, doc):
        self.doc = doc


@pytest.fixture
def ctx():
    return _Ctx(Document.new("EPSG:31982"))


def _polyline_shp(tmp_path, name, shapes, shape_type=shapefile.POLYLINE, fields=None, records=None):
    """shapes: lista de shapes; cada shape e uma lista de partes; cada parte
    e uma lista de (x, y). Um shape = um registro do .dbf."""
    fields = fields or [("nome", "C")]
    records = records or [("x",)] * len(shapes)
    path = tmp_path / name
    w = shapefile.Writer(str(path), shapeType=shape_type)
    for fname, ftype in fields:
        w.field(fname, ftype)
    for shape in shapes:
        if shape_type == shapefile.POLYGON:
            w.poly(shape)
        else:
            w.line(shape)
    for r in records:
        w.record(*r)
    w.close()
    return path.with_suffix(".shp")


def _point_shp(tmp_path, name, points, fields=None, records=None):
    fields = fields or [("nome", "C")]
    records = records or [("x",)] * len(points)
    path = tmp_path / name
    w = shapefile.Writer(str(path), shapeType=shapefile.POINT)
    for fname, ftype in fields:
        w.field(fname, ftype)
    for x, y in points:
        w.point(x, y)
    for r in records:
        w.record(*r)
    w.close()
    return path.with_suffix(".shp")


def _write_prj(shp_path, crs: str) -> None:
    shp_path.with_suffix(".prj").write_text(CRS.from_user_input(crs).to_wkt(), encoding="utf-8")


# ---------------- geometria ----------------


def test_polyline_becomes_open_lwpolyline(tmp_path, ctx):
    shp = _polyline_shp(
        tmp_path, "linhas", [[[(E, N), (E + 10, N), (E + 10, N + 10)]]]
    )
    result = import_shapefile(ctx, shp)
    assert result.created == 1
    assert result.skipped == 0
    ents = list(ctx.doc.entities())
    assert len(ents) == 1
    e = ents[0]
    assert e.dxftype() == "LWPOLYLINE"
    assert not e.closed
    pts = [(x, y) for x, y, *_ in e.get_points()]
    assert pts == [(E, N), (E + 10, N), (E + 10, N + 10)]


def test_polygon_becomes_closed_lwpolyline(tmp_path, ctx):
    ring = [(E, N), (E + 10, N), (E + 10, N + 10), (E, N + 10), (E, N)]
    shp = _polyline_shp(tmp_path, "poligono", [[ring]], shape_type=shapefile.POLYGON)
    result = import_shapefile(ctx, shp)
    assert result.created == 1
    e = next(iter(ctx.doc.entities()))
    assert e.dxftype() == "LWPOLYLINE"
    assert e.closed


def test_point_becomes_point_entity(tmp_path, ctx):
    shp = _point_shp(tmp_path, "pontos", [(E, N), (E + 5, N + 5)])
    result = import_shapefile(ctx, shp)
    assert result.created == 2
    types = {e.dxftype() for e in ctx.doc.entities()}
    assert types == {"POINT"}


def test_multiple_parts_become_multiple_polylines(tmp_path, ctx):
    """Um unico shape com duas partes vira duas LWPOLYLINE (o DXF nao tem
    o conceito de multi-parte numa entidade so)."""
    shape = [[(E, N), (E + 10, N)], [(E, N + 20), (E + 10, N + 20)]]
    shp = _polyline_shp(tmp_path, "multi", [shape])
    result = import_shapefile(ctx, shp)
    assert result.created == 2
    assert len(list(ctx.doc.entities())) == 2


# ---------------- camadas ----------------


def test_default_layer_is_file_stem_uppercase(tmp_path, ctx):
    shp = _polyline_shp(tmp_path, "divisas", [[[(E, N), (E + 10, N)]]])
    result = import_shapefile(ctx, shp)
    assert result.layer == "DIVISAS"
    e = next(iter(ctx.doc.entities()))
    assert e.dxf.layer == "DIVISAS"
    assert "DIVISAS" in ctx.doc.layer_names()


def test_explicit_layer_overrides_stem(tmp_path, ctx):
    shp = _polyline_shp(tmp_path, "qualquer", [[[(E, N), (E + 10, N)]]])
    result = import_shapefile(ctx, shp, layer="LIMITE")
    assert result.layer == "LIMITE"
    e = next(iter(ctx.doc.entities()))
    assert e.dxf.layer == "LIMITE"


def test_attribute_field_splits_into_layers(tmp_path, ctx):
    shapes = [
        [[(E, N), (E + 10, N)]],
        [[(E, N + 20), (E + 10, N + 20)]],
    ]
    shp = _polyline_shp(
        tmp_path,
        "cadastro",
        shapes,
        fields=[("tipo", "C")],
        records=[("limite",), ("via",)],
    )
    result = import_shapefile(ctx, shp, attribute_field="tipo")
    layers = sorted({e.dxf.layer for e in ctx.doc.entities()})
    assert layers == ["LIMITE", "VIA"]
    assert result.created == 2


def test_shapefile_fields_lists_attribute_names(tmp_path):
    shp = _polyline_shp(
        tmp_path,
        "campos",
        [[[(E, N), (E + 10, N)]]],
        fields=[("tipo", "C"), ("area", "N")],
        records=[("a", 1)],
    )
    assert shapefile_fields(shp) == ["tipo", "area"]


# ---------------- CRS ----------------


def test_no_prj_assumes_project_crs(tmp_path, ctx):
    shp = _polyline_shp(tmp_path, "sem_prj", [[[(E, N), (E + 10, N)]]])
    result = import_shapefile(ctx, shp)
    assert result.source_crs is None
    assert not result.reprojected
    e = next(iter(ctx.doc.entities()))
    pts = [(x, y) for x, y, *_ in e.get_points()]
    assert pts[0] == pytest.approx((E, N))


def test_prj_in_different_crs_is_reprojected(tmp_path, ctx):
    """Shapefile em WGS84 (lon/lat) sobre projeto em UTM: coordenadas tem de
    sair transformadas, nao coladas cruas."""
    lon, lat = -51.0, -23.5
    shp = _polyline_shp(tmp_path, "geografico", [[[(lon, lat), (lon + 0.01, lat)]]])
    _write_prj(shp, "EPSG:4326")

    result = import_shapefile(ctx, shp)
    assert result.reprojected
    assert result.source_crs.epsg == 4326

    expected = ProjectCRS("EPSG:4326").transformer_to(ctx.doc.crs)
    ex0, ey0 = expected.transform(lon, lat)

    e = next(iter(ctx.doc.entities()))
    pts = [(x, y) for x, y, *_ in e.get_points()]
    assert pts[0] == pytest.approx((ex0, ey0))
    # UTM 22S por perto de -51 graus: coordenadas na casa das centenas de mil, nao graus
    assert pts[0][0] > 100_000


def test_prj_same_crs_as_project_is_not_reprojected(tmp_path, ctx):
    shp = _polyline_shp(tmp_path, "mesmo_crs", [[[(E, N), (E + 10, N)]]])
    _write_prj(shp, "EPSG:31982")
    result = import_shapefile(ctx, shp)
    assert not result.reprojected
    e = next(iter(ctx.doc.entities()))
    pts = [(x, y) for x, y, *_ in e.get_points()]
    assert pts[0] == pytest.approx((E, N))


def test_read_prj_crs_returns_none_without_file(tmp_path):
    assert read_prj_crs(tmp_path / "nao_existe.shp") is None


# ---------------- desfazer ----------------


def test_import_is_a_single_undo_step(tmp_path, ctx):
    shapes = [
        [[(E, N), (E + 10, N)]],
        [[(E, N + 20), (E + 10, N + 20)]],
    ]
    shp = _polyline_shp(tmp_path, "duas_linhas", shapes)
    import_shapefile(ctx, shp)
    assert len(list(ctx.doc.entities())) == 2
    assert ctx.doc.undo.undo()
    assert len(list(ctx.doc.entities())) == 0
    assert not ctx.doc.undo.undo()
    assert ctx.doc.undo.redo()
    assert len(list(ctx.doc.entities())) == 2


# ---------------- erros ----------------


def test_missing_file_raises(tmp_path, ctx):
    with pytest.raises(ShapefileImportError):
        import_shapefile(ctx, tmp_path / "nao_existe.shp")
