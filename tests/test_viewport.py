import pytest

from engecad.core.geometry import BBox, Vec2
from engecad.render.viewport import Viewport

# Coordenada UTM real (SIRGAS 2000 / 22S) -- o caso que quebra CADs mal feitos.
E, N = 500000.0, 7400000.0


@pytest.fixture
def vp():
    v = Viewport(1600, 900)
    v.center = Vec2(E, N)
    v.set_scale(50.0)  # 50 px por metro
    return v


@pytest.mark.parametrize(
    "p",
    [
        Vec2(E, N),
        Vec2(E + 123.456, N + 987.123),
        Vec2(E - 0.001, N + 0.001),
        Vec2(E + 12987.654, N - 45678.001),
    ],
)
def test_world_screen_roundtrip_submillimeter(vp, p):
    """O criterio duro do projeto: ida e volta com coordenada UTM < 1 mm."""
    sx, sy = vp.world_to_screen(p)
    back = vp.screen_to_world(sx, sy)
    assert abs(back.x - p.x) < 1e-3
    assert abs(back.y - p.y) < 1e-3


def test_y_axis_points_north(vp):
    """No mundo Y cresce para o norte; na tela, para baixo."""
    _, sy_low = vp.world_to_screen(Vec2(E, N))
    _, sy_high = vp.world_to_screen(Vec2(E, N + 10))
    assert sy_high < sy_low


def test_center_maps_to_screen_center(vp):
    sx, sy = vp.world_to_screen(vp.center)
    assert sx == pytest.approx(vp.width / 2)
    assert sy == pytest.approx(vp.height / 2)


def test_zoom_anchor_does_not_drift(vp):
    """O ponto sob o cursor tem de ficar imovel durante o zoom."""
    sx, sy = 400.0, 300.0
    before = vp.screen_to_world(sx, sy)
    for _ in range(30):
        vp.zoom_at_screen(sx, sy, 1.2)
    for _ in range(30):
        vp.zoom_at_screen(sx, sy, 1 / 1.2)
    after = vp.screen_to_world(sx, sy)
    assert before.distance_to(after) < 1e-6


def test_pan_moves_by_exact_world_distance(vp):
    before = vp.center
    vp.pan_screen(100, 0)  # arrasta 100 px para a direita
    assert vp.center.x == pytest.approx(before.x - 100 / vp.scale)
    assert vp.center.y == pytest.approx(before.y)


def test_zoom_to_bbox_fits_content(vp):
    b = BBox(E, N, E + 100, N + 50)
    vp.zoom_to_bbox(b)
    vis = vp.visible_bbox()
    assert vis.minx <= b.minx and vis.maxx >= b.maxx
    assert vis.miny <= b.miny and vis.maxy >= b.maxy
    assert vp.center.x == pytest.approx(b.center.x)


def test_scale_is_clamped(vp):
    vp.set_scale(1e30)
    assert vp.scale < 1e30
    vp.set_scale(-5)
    assert vp.scale > 0


def test_map_scale_denominator_roundtrip(vp):
    for denom in (100, 500, 1000, 2000, 5000):
        vp.set_scale_denominator(denom)
        assert vp.scale_denominator() == pytest.approx(denom)


def test_nice_grid_step_is_1_2_5_decade(vp):
    for scale in (0.001, 0.01, 0.5, 5, 50, 500, 5000):
        vp.set_scale(scale)
        step = vp.nice_grid_step()
        mantissa = step / (10 ** round(__import__("math").log10(step)))
        assert step > 0
        # o passo deve ser 1, 2 ou 5 vezes uma potencia de 10
        assert any(abs(mantissa - m) < 1e-9 for m in (0.1, 0.2, 0.5, 1.0, 2.0, 5.0))


def test_flatten_tolerance_shrinks_when_zooming_in(vp):
    vp.set_scale(1.0)
    far = vp.flatten_tolerance()
    vp.set_scale(1000.0)
    near = vp.flatten_tolerance()
    assert near < far
