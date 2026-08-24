"""Raster de fundo: georreferenciamento, reprojecao e a cadeia do ECW."""

import os

import numpy as np
import pytest
import rasterio
from rasterio.transform import from_origin

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from engecad.core.crs import ProjectCRS  # noqa: E402
from engecad.core.geometry import Vec2  # noqa: E402
from engecad.io.raster_import import (  # noqa: E402
    INSTALL_HINT,
    cog_target_for,
    driver_for,
    plan_import,
    rasterio_can_open,
)
from engecad.render.raster_layer import RasterLayer  # noqa: E402
from engecad.render.viewport import Viewport  # noqa: E402

E, N = 500000.0, 7400000.0
RES = 0.25  # 25 cm/px, tipico de ortofoto urbana
W = H = 200  # 50 m x 50 m


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


def _make_raster(path, crs="EPSG:31982", left=E, top=N + 50, res=RES, w=W, h=H):
    """Ortofoto sintetica com um padrao reconhecivel."""
    data = np.zeros((3, h, w), dtype=np.uint8)
    data[0, : h // 2, :] = 200  # metade de cima avermelhada
    data[1, :, : w // 2] = 180  # metade esquerda esverdeada
    data[2] = 60
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=w,
        height=h,
        count=3,
        dtype="uint8",
        crs=crs,
        transform=from_origin(left, top, res, res),
        tiled=True,
        blockxsize=128,
        blockysize=128,
    ) as ds:
        ds.write(data)
    return path


@pytest.fixture
def ortofoto(tmp_path):
    return _make_raster(tmp_path / "orto.tif")


# ---------------- georreferenciamento ----------------


def test_layer_bounds_match_world_coordinates(ortofoto):
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        b = layer.bounds
        assert b.minx == pytest.approx(E)
        assert b.maxx == pytest.approx(E + W * RES)
        assert b.maxy == pytest.approx(N + 50)
        assert b.miny == pytest.approx(N + 50 - H * RES)
        assert not layer.reprojected
    finally:
        layer.close()


def test_resolution_is_reported(ortofoto):
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        assert layer.resolution == pytest.approx(RES)
    finally:
        layer.close()


def test_render_produces_image_aligned_to_request(qapp, ortofoto):
    """A leitura decimada tem de devolver a janela pedida, no tamanho da tela."""
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        vp = Viewport(400, 300)
        vp.center = layer.bounds.center
        vp.set_scale(4.0)  # 4 px por metro
        layer._render(layer.bounds, vp.scale)
        assert layer._cache_img is not None
        # 50 m x 4 px/m = 200 px
        assert layer._cache_img.width() == pytest.approx(200, abs=2)
        assert layer._cache_img.height() == pytest.approx(200, abs=2)
    finally:
        layer.close()


def test_cache_is_reused_while_zoom_is_unchanged(qapp, ortofoto):
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        vp = Viewport(400, 300)
        vp.center = layer.bounds.center
        vp.set_scale(4.0)
        layer._render(layer.bounds, vp.scale)
        first = layer._cache_img
        # a mesma regiao, mesmo zoom: nao pode reler
        assert layer._cache_is_usable(layer.bounds, vp.scale)
        assert layer._cache_img is first
        # zoom diferente invalida
        assert not layer._cache_is_usable(layer.bounds, vp.scale * 2)
    finally:
        layer.close()


def test_reprojection_when_raster_crs_differs(tmp_path):
    """Raster em WGS84 num projeto UTM: o WarpedVRT tem de alinhar tudo."""
    path = _make_raster(
        tmp_path / "geo.tif", crs="EPSG:4326", left=-51.0, top=-23.5, res=0.0005, w=100, h=100
    )
    layer = RasterLayer(path, project_crs=ProjectCRS("EPSG:31982"))
    try:
        assert layer.reprojected, "deveria ter criado um WarpedVRT"
        b = layer.bounds
        # bounds agora em metros UTM, nao em graus
        assert b.minx > 100_000 and b.minx < 1_000_000
        assert b.miny > 1_000_000
        assert b.width > 100  # ~50 m de largura, nao 0.05 graus
    finally:
        layer.close()


def test_no_reprojection_when_crs_matches(ortofoto):
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        assert not layer.reprojected
    finally:
        layer.close()


def test_changing_project_crs_rebuilds_the_vrt(ortofoto):
    layer = RasterLayer(ortofoto, project_crs=ProjectCRS("EPSG:31982"))
    try:
        assert not layer.reprojected
        layer.set_project_crs(ProjectCRS("EPSG:3857"))
        assert layer.reprojected
        assert layer._cache_img is None, "o cache tinha de ser invalidado"
    finally:
        layer.close()


# ---------------- cadeia do ECW ----------------


def test_geotiff_is_opened_directly(ortofoto):
    plan = plan_import(ortofoto)
    assert plan.action == "direct"
    assert plan.target == ortofoto
    assert not plan.blocked


def test_missing_file_is_blocked(tmp_path):
    plan = plan_import(tmp_path / "nao_existe.tif")
    assert plan.blocked


def test_ecw_without_driver_gives_actionable_instructions(tmp_path):
    """Numa maquina sem gdal-ecw, o usuario tem de saber exatamente o que fazer."""
    fake = tmp_path / "orto.ecw"
    fake.write_bytes(b"nao e um ECW de verdade")
    assert not rasterio_can_open(fake), "o rasterio nao deveria abrir ECW"

    plan = plan_import(fake)
    # sem GDAL externo nesta maquina: bloqueado, com instrucao
    if plan.blocked:
        assert "gdal-ecw" in plan.message
        assert "OSGeo4W" in plan.message
        assert INSTALL_HINT in plan.message
    else:
        # ha um GDAL com ECW instalado: entao o plano e converter
        assert plan.needs_conversion
        assert plan.gdal_bin is not None


def test_existing_conversion_is_reused(tmp_path):
    """Reimportar o mesmo ECW nao pode reconverter do zero."""
    fake_ecw = tmp_path / "orto.ecw"
    fake_ecw.write_bytes(b"x")
    _make_raster(cog_target_for(fake_ecw))  # simula conversao anterior
    plan = plan_import(fake_ecw)
    assert plan.action == "direct"
    assert plan.target.name == "orto_cog.tif"


def test_cog_target_naming():
    assert cog_target_for("/tmp/foto.ecw").name == "foto_cog.tif"


def test_driver_for_extension():
    assert driver_for("a.ecw") == "ECW"
    assert driver_for("a.tif") == "TIF"


# ---------------- integracao com o app ----------------


def test_raster_extends_zoom_extents(qapp, ortofoto):
    """Carregar a imagem tem de enquadrar a area dela, para poder desenhar em cima."""
    from engecad.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    try:
        win.canvas.resize(800, 600)
        win.ctx.viewport.resize(800, 600)
        win._load_raster(ortofoto)
        assert len(win.ctx.rasters) == 1
        vis = win.ctx.viewport.visible_bbox()
        b = win.ctx.rasters[0].bounds
        assert vis.minx <= b.minx and vis.maxx >= b.maxx
        assert vis.miny <= b.miny and vis.maxy >= b.maxy
    finally:
        for r in win.ctx.rasters:
            r.close()
        win.ctx.doc._modified = False
        win.close()


def test_drawing_over_raster_lands_on_correct_coordinates(qapp, ortofoto, tmp_path):
    """O criterio de sucesso da v0.1: o que se desenha sobre a foto tem de
    sair no DXF com a coordenada do mundo real."""
    from engecad.io.dxf_io import save_document
    from engecad.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    try:
        win.canvas.resize(800, 600)
        win.ctx.viewport.resize(800, 600)
        win._load_raster(ortofoto)
        b = win.ctx.rasters[0].bounds

        # desenha o retangulo do canto da imagem clicando "na tela"
        vp = win.ctx.viewport
        corner = Vec2(b.minx, b.maxy)
        sx, sy = vp.world_to_screen(corner)
        back = vp.screen_to_world(sx, sy)
        assert back.distance_to(corner) < 1e-6

        win.ctx.run_command("PLINE")
        for p in (
            Vec2(b.minx, b.miny),
            Vec2(b.maxx, b.miny),
            Vec2(b.maxx, b.maxy),
        ):
            win.ctx.tool.on_click(p)
        win.ctx.tool.on_text("F")

        path = tmp_path / "sobre_a_foto.dxf"
        save_document(win.ctx, path)

        with rasterio.open(ortofoto) as ds:
            r = ds.bounds

        import ezdxf

        reread = ezdxf.readfile(str(path))
        poly = next(e for e in reread.modelspace() if e.dxftype() == "LWPOLYLINE")
        pts = [(x, y) for x, y, *_ in poly.get_points()]
        assert min(p[0] for p in pts) == pytest.approx(r.left)
        assert max(p[0] for p in pts) == pytest.approx(r.right)
        assert min(p[1] for p in pts) == pytest.approx(r.bottom)
        assert max(p[1] for p in pts) == pytest.approx(r.top)
    finally:
        for r_ in win.ctx.rasters:
            r_.close()
        win.ctx.doc._modified = False
        win.close()


def test_sidecar_reloads_the_raster(qapp, ortofoto, tmp_path):
    from engecad.io.dxf_io import open_document, save_document
    from engecad.ui.main_window import MainWindow

    win = MainWindow()
    win.show()
    try:
        win._load_raster(ortofoto)
        path = tmp_path / "com_foto.dxf"
        save_document(win.ctx, path)
        assert len(win.ctx.rasters) == 1

        open_document(win.ctx, path)
        assert len(win.ctx.rasters) == 1, "o raster nao voltou pelo sidecar"
        assert win.ctx.rasters[0].path.resolve() == ortofoto.resolve()
    finally:
        for r in win.ctx.rasters:
            r.close()
        win.ctx.doc._modified = False
        win.close()
