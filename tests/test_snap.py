import pytest

from engecad.core.document import Document
from engecad.core.geometry import Vec2
from engecad.render.viewport import Viewport
from engecad.snap.engine import SnapEngine

E, N = 500000.0, 7400000.0


@pytest.fixture
def scene():
    doc = Document.new("EPSG:31982")
    doc.add_line((E, N), (E + 100, N))  # horizontal
    doc.add_line((E + 50, N - 50), (E + 50, N + 50))  # vertical, cruza no meio
    doc.add_circle((E + 20, N + 20), 10)
    vp = Viewport(1600, 900)
    vp.center = Vec2(E + 50, N)
    vp.set_scale(5.0)  # raio de snap = 14px / 5 = 2.8 m
    return doc, vp, SnapEngine(doc)


def test_snaps_to_endpoint(scene):
    doc, vp, snap = scene
    r = snap.snap(Vec2(E + 0.5, N + 0.4), vp)
    assert r.kind == "end"
    assert r.point.x == pytest.approx(E)
    assert r.point.y == pytest.approx(N)


def test_snaps_to_circle_center(scene):
    doc, vp, snap = scene
    r = snap.snap(Vec2(E + 20.4, N + 20.2), vp)
    assert r.kind == "center"
    assert r.point.x == pytest.approx(E + 20)


def test_snaps_to_circle_quadrant(scene):
    doc, vp, snap = scene
    r = snap.snap(Vec2(E + 30.3, N + 20.1), vp)
    assert r.kind == "quad"
    assert r.point.x == pytest.approx(E + 30)


def test_snaps_to_nearest_on_long_line(scene):
    doc, vp, snap = scene
    r = snap.snap(Vec2(E + 80.0, N + 0.5), vp)
    assert r.kind == "nearest"
    assert r.point.y == pytest.approx(N)


def test_finds_intersection_of_long_lines():
    """Regressao: o filtro por distancia usava os extremos do segmento, e uma
    linha longa passando sob o cursor era descartada."""
    doc = Document.new("EPSG:31982")
    doc.add_line((E, N), (E + 200, N))
    doc.add_line((E + 100, N - 100), (E + 100, N + 100))
    vp = Viewport(1600, 900)
    vp.center = Vec2(E + 100, N)
    vp.set_scale(5.0)
    snap = SnapEngine(doc)
    snap.enabled.discard("mid")  # o cruzamento coincide com o meio das duas
    r = snap.snap(Vec2(E + 99.6, N - 0.3), vp)
    assert r.kind == "intersection"
    assert r.point.x == pytest.approx(E + 100)
    assert r.point.y == pytest.approx(N, abs=1e-9)


def test_returns_none_when_far_from_everything(scene):
    doc, vp, snap = scene
    assert snap.snap(Vec2(E + 80, N + 300), vp) is None


def test_disabled_engine_returns_none(scene):
    doc, vp, snap = scene
    snap.active = False
    assert snap.snap(Vec2(E, N), vp) is None


def test_capture_radius_follows_zoom(scene):
    """O snap tem de pegar com a mesma sensibilidade em pixels em qualquer zoom."""
    doc, vp, snap = scene
    far = Vec2(E + 2.0, N)  # 2 m do extremo
    vp.set_scale(5.0)  # raio 2.8 m -> pega
    assert snap.snap(far, vp) is not None
    vp.set_scale(200.0)  # raio 0.07 m -> nao pega o extremo
    r = snap.snap(far, vp)
    assert r is None or r.kind == "nearest"


def test_hidden_layer_is_not_snapped():
    doc = Document.new("EPSG:31982")
    doc.add_line((E, N), (E + 10, N), layer="LIMITE")
    vp = Viewport(800, 600)
    vp.center = Vec2(E, N)
    vp.set_scale(5.0)
    snap = SnapEngine(doc)
    assert snap.snap(Vec2(E + 0.2, N), vp) is not None
    doc.set_layer_visible("LIMITE", False)
    assert snap.snap(Vec2(E + 0.2, N), vp) is None


def test_grid_snap_only_when_nothing_else():
    doc = Document.new("EPSG:31982")
    vp = Viewport(800, 600)
    vp.center = Vec2(E, N)
    vp.set_scale(5.0)
    snap = SnapEngine(doc)
    snap.grid_step = 1.0
    snap.enabled.add("grid")
    r = snap.snap(Vec2(E + 0.2, N + 0.1), vp)
    assert r.kind == "grid"
    assert r.point.x == pytest.approx(E)
