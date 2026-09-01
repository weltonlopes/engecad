"""Invariantes do motor de renderizacao com cache.

A geometria deixou de ser reconstruida a cada quadro: ela e achatada uma vez,
guardada em tiles (render/displaylist.py) e o quadro pronto e reaproveitado
(render/framecache.py). Isso cria obrigacoes novas -- o cache tem de enxergar
edicoes, o achatamento rapido tem de bater com o generico, e o snap tem de
continuar achando o mesmo ponto mesmo com o orcamento de candidatos.
"""

import math
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ezdxf import bbox as ezbbox  # noqa: E402
from ezdxf.path import make_path  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from engecad.context import AppContext  # noqa: E402
from engecad.core.document import Document  # noqa: E402
from engecad.core.entities import _fast_points, entity_bbox, entity_point_lists  # noqa: E402
from engecad.core.geometry import BBox, Vec2  # noqa: E402
from engecad.render.canvas import CadCanvas  # noqa: E402

E, N = 674000.0, 7384000.0


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def scene(qapp):
    """Documento com um punhado de entidades e um canvas montado sobre ele."""
    doc = Document.new()
    msp = doc.msp
    doc.ensure_layer("MURO", 1)
    msp.add_line((E, N), (E + 40, N + 20), dxfattribs={"layer": "MURO"})
    msp.add_lwpolyline(
        [(E, N + 30), (E + 20, N + 30), (E + 20, N + 45), (E, N + 45)],
        close=True,
        dxfattribs={"layer": "MURO"},
    )
    msp.add_circle((E + 60, N + 20), 8)
    doc.rebuild_index()

    ctx = AppContext(doc)
    canvas = CadCanvas(ctx)
    canvas.show_grid = False  # a grade tambem vira pixel e mascara a contagem
    canvas.resize(400, 300)
    ctx.viewport.resize(400, 300)
    ctx.zoom_extents()
    yield ctx, canvas
    canvas.deleteLater()


def _ink(canvas, w=400, h=300):
    """Quantos pixels do quadro nao sao o fundo."""
    img = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
    canvas.render(img)
    background = canvas.theme.q("background").rgb()
    return sum(
        1
        for y in range(h)
        for x in range(w)
        if img.pixel(x, y) & 0xFFFFFF != background & 0xFFFFFF
    )


# ---------------- achatamento rapido ----------------


@pytest.mark.parametrize("radius", [0.05, 2.5, 100.0, 5000.0])
@pytest.mark.parametrize("sagitta", [0.01, 0.3, 2.0])
def test_fast_arc_flattening_respects_tolerance(radius, sagitta):
    """A flecha da poligonal nunca pode passar da tolerancia pedida."""
    doc = Document.new()
    arc = doc.msp.add_arc((E, N), radius, 20, 200)
    pts = _fast_points(arc, "ARC", sagitta)[0]
    assert pts is not None
    worst = 0.0
    for a, b in zip(pts, pts[1:], strict=False):
        mx, my = (a[0] + b[0]) / 2 - E, (a[1] + b[1]) / 2 - N
        worst = max(worst, abs(radius - math.hypot(mx, my)))
    assert worst <= sagitta * 1.01
    # e todo vertice tem de cair sobre o arco
    for x, y in pts:
        assert abs(math.hypot(x - E, y - N) - radius) < radius * 1e-9 + 1e-9


def test_fast_flattening_matches_generic_for_polylines():
    doc = Document.new()
    poly = doc.msp.add_lwpolyline(
        [(E, N), (E + 10, N), (E + 10, N + 5), (E, N + 5)], close=True
    )
    fast = entity_point_lists(poly, 0.01)[0]
    ref = [(v.x, v.y) for v in make_path(poly).flattening(0.01)]
    assert len(fast) == len(ref)
    for a, b in zip(fast, ref, strict=True):
        assert a == pytest.approx(b)
    assert fast[0] == pytest.approx(fast[-1]), "o contorno fechado tem de voltar ao inicio"


def test_fast_flattening_defers_to_generic_when_not_trivial():
    """Bulge e extrusao fora do plano tem de cair no caminho de referencia."""
    doc = Document.new()
    bulged = doc.msp.add_lwpolyline([(E, N, 0.0), (E + 10, N, 1.0)], format="xyb")
    assert _fast_points(bulged, "LWPOLYLINE", 0.01) is None
    tilted = doc.msp.add_line((E, N), (E + 1, N + 1), dxfattribs={"extrusion": (0, 0, -1)})
    assert _fast_points(tilted, "LINE", 0.01) is None
    # mas continuam produzindo geometria pelo caminho generico
    assert entity_point_lists(bulged, 0.01)
    assert entity_point_lists(tilted, 0.01)


# ---------------- bbox rapido ----------------


@pytest.mark.parametrize(
    "build",
    [
        lambda m: m.add_line((E, N), (E - 7, N + 30)),
        lambda m: m.add_circle((E + 5, N + 5), 2.5),
        lambda m: m.add_arc((E, N), 10, 300, 60),
        lambda m: m.add_arc((E, N), 10, 10, 80),
        lambda m: m.add_lwpolyline([(E, N), (E + 10, N), (E + 10, N + 5)], close=True),
        lambda m: m.add_point((E + 7, N + 8)),
    ],
)
def test_fast_bbox_contains_the_generic_one(build):
    doc = Document.new()
    entity = build(doc.msp)
    got = entity_bbox(entity)
    ref = ezbbox.extents([entity], fast=False)
    assert ref.has_data
    assert got.minx <= ref.extmin.x + 1e-6
    assert got.miny <= ref.extmin.y + 1e-6
    assert got.maxx >= ref.extmax.x - 1e-6
    assert got.maxy >= ref.extmax.y - 1e-6


# ---------------- display list acompanha o documento ----------------


def test_display_list_sees_new_entities(scene):
    ctx, canvas = scene
    before = _ink(canvas)
    ctx.doc.add_line((E, N + 60), (E + 60, N + 60))
    ctx.zoom_extents()
    assert _ink(canvas) > before


def test_display_list_sees_moved_entities(scene):
    ctx, canvas = scene
    circle = next(e for e in ctx.doc.msp if e.dxftype() == "CIRCLE")
    before = _ink(canvas)
    with ctx.doc.editing([circle], "mover"):
        circle.dxf.center = (E + 60, N + 200)
    assert _ink(canvas) != before


def test_display_list_sees_deleted_entities(scene):
    ctx, canvas = scene
    before = _ink(canvas)
    ctx.doc.delete([next(iter(ctx.doc.msp))])
    assert _ink(canvas) < before


def test_hidden_layer_disappears_from_the_scene(scene):
    ctx, canvas = scene
    before = _ink(canvas)
    ctx.doc.set_layer_visible("MURO", False)
    hidden = _ink(canvas)
    assert hidden < before
    ctx.doc.set_layer_visible("MURO", True)
    assert _ink(canvas) == before


def test_display_list_survives_an_empty_document():
    """Um desenho que nasce vazio e cresce nao pode quebrar a grade de tiles."""
    doc = Document.new()
    ctx = AppContext(doc)
    canvas = CadCanvas(ctx)
    canvas.show_grid = False
    canvas.resize(200, 150)
    ctx.viewport.resize(200, 150)
    assert _ink(canvas, 200, 150) == 0
    for i in range(40):
        doc.add_line((E + i, N), (E + i, N + 10))
    ctx.zoom_extents()
    assert _ink(canvas, 200, 150) > 0
    canvas.deleteLater()


# ---------------- cache de quadro ----------------


def test_frame_cache_is_reused_and_covers_a_short_pan(scene):
    ctx, canvas = scene
    canvas.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))
    frame = canvas._frame
    assert frame.is_exact(ctx.viewport)

    spent = frame.last_ms
    canvas.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))
    assert frame.last_ms == spent, "o quadro foi refeito mesmo com o cache valido"

    ctx.viewport.pan_screen(20, 12)
    assert frame.is_exact(ctx.viewport), "um pan curto deveria caber na folga"
    ctx.viewport.pan_screen(5000, 0)
    assert not frame.is_exact(ctx.viewport)


def test_zoom_invalidates_the_frame_cache(scene):
    ctx, canvas = scene
    canvas.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))
    ctx.viewport.set_scale(ctx.viewport.scale * 2)
    assert not canvas._frame.is_exact(ctx.viewport)


# ---------------- desenho progressivo ----------------


@pytest.fixture
def crowded(qapp):
    """Desenho grande o bastante para o quadro nao caber numa fatia so."""
    doc = Document.new()
    for i in range(6000):
        x = E + (i % 100) * 3.0
        y = N + (i // 100) * 3.0
        doc.msp.add_lwpolyline([(x, y), (x + 2, y), (x + 2, y + 2), (x, y + 2)], close=True)
    doc.rebuild_index()
    ctx = AppContext(doc)
    canvas = CadCanvas(ctx)
    canvas.show_grid = False
    canvas.resize(400, 300)
    ctx.viewport.resize(400, 300)
    ctx.zoom_extents()
    yield ctx, canvas
    canvas.deleteLater()


def test_heavy_frame_is_drawn_in_slices(crowded):
    """Cada fatia devolve o controle; o quadro so vale quando fecha."""
    ctx, canvas = crowded
    img = QImage(400, 300, QImage.Format_ARGB32_Premultiplied)
    slices = 0
    while not canvas._frame.complete:
        assert not canvas._frame.is_exact(ctx.viewport), "quadro parcial nao pode valer"
        canvas.render(img)
        slices += 1
        assert slices < 500, "o quadro nunca fechou"
    assert slices > 1, "este desenho deveria precisar de mais de uma fatia"
    assert canvas._frame.is_exact(ctx.viewport)


def test_progressive_result_matches_the_direct_one(crowded):
    """Fatiar muda quando o desenho aparece, nunca o que aparece."""
    ctx, canvas = crowded
    fatiado = QImage(400, 300, QImage.Format_ARGB32_Premultiplied)
    while not canvas._frame.complete:
        canvas.render(fatiado)

    canvas.invalidate_scene()
    canvas._display.clear()
    canvas.render_scene_now()
    assert canvas._frame.complete
    direto = QImage(400, 300, QImage.Format_ARGB32_Premultiplied)
    canvas.render(direto)
    assert fatiado == direto


def test_render_scene_now_needs_no_event_loop(crowded):
    ctx, canvas = crowded
    canvas.invalidate_scene()
    canvas.render_scene_now()
    assert canvas._frame.complete
    assert canvas._frame.is_exact(ctx.viewport)


def test_partial_rebuild_never_reaches_the_planner():
    """Planejar com a display list pela metade descreveria o desenho errado."""
    doc = Document.new()
    for i in range(2000):
        doc.msp.add_line((E + i, N), (E + i, N + 5))
    doc.rebuild_index()
    ctx = AppContext(doc)
    display = ctx.canvas._display if ctx.canvas else None
    if display is None:
        canvas = CadCanvas(ctx)
        display = canvas._display
    # um prazo ja vencido: a preparacao tem de parar no meio e admitir isso
    assert display.prepare(time.perf_counter() - 1.0) is False
    assert len(display._slot) < 2000
    while not display.prepare(None):
        pass
    assert len(display._slot) == 2000


# ---------------- precisao do rebase ----------------


def test_local_coordinates_stay_small_enough_for_the_rasterizer(scene):
    """O motivo de existir da display list: numeros pequenos chegam ao Qt."""
    ctx, canvas = scene
    canvas.render(QImage(400, 300, QImage.Format_ARGB32_Premultiplied))
    display = canvas._display
    ox, oy = display._origin
    assert abs(E - ox) < 1e4 and abs(N - oy) < 1e4
    for cell in display._cells.values():
        for path in cell.strokes.values():
            box = path.boundingRect()
            # em float32 um valor desta ordem tem erro submilimetrico
            assert max(abs(box.left()), abs(box.top())) < 1e5


# ---------------- snap sob orcamento ----------------


def test_snap_still_finds_the_endpoint_under_the_candidate_budget():
    """O teto de candidatos guarda os mais proximos, nunca os primeiros."""
    doc = Document.new()
    for i in range(400):  # ruido longe do cursor, dentro do raio de captura
        doc.add_line((E + 200 + i, N + 200), (E + 210 + i, N + 200))
    doc.add_line((E, N), (E + 5, N))
    ctx = AppContext(doc)
    ctx.viewport.resize(800, 600)
    ctx.viewport.zoom_to_bbox(BBox(E - 400, N - 400, E + 800, N + 800))

    result = ctx.snap.snap(Vec2(E + 0.2, N + 0.2), ctx.viewport)
    assert result is not None
    assert result.kind == "end"
    assert result.point.distance_to(Vec2(E, N)) < 1e-9


def test_snap_point_cache_follows_edits():
    doc = Document.new()
    line = doc.add_line((E, N), (E + 10, N))
    ctx = AppContext(doc)
    ctx.viewport.resize(800, 600)
    ctx.viewport.zoom_to_bbox(BBox(E - 20, N - 20, E + 30, N + 30))

    assert ctx.snap.snap(Vec2(E + 10.05, N), ctx.viewport).point.distance_to(
        Vec2(E + 10, N)
    ) < 1e-9
    with doc.editing([line], "mover"):
        line.dxf.end = (E + 20, N)
    hit = ctx.snap.snap(Vec2(E + 20.05, N), ctx.viewport)
    assert hit is not None and hit.point.distance_to(Vec2(E + 20, N)) < 1e-9
