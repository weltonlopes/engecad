"""Sobe a aplicacao inteira em modo offscreen e exercita o caminho real.

Cobre o que os testes de nucleo nao pegam: a fiacao entre contexto, canvas,
linha de comando, ferramentas e console -- inclusive se o canvas realmente
pinta alguma coisa.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, Qt  # noqa: E402
from PySide6.QtGui import QImage, QPainter  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from engecad.core.geometry import Vec2  # noqa: E402
from engecad.ui.main_window import MainWindow  # noqa: E402

E, N = 500000.0, 7400000.0


@pytest.fixture(scope="session")
def qapp():
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()  # sem show() o QWidget nao pinta em render()
    w.resize(1000, 700)
    w.canvas.resize(1000, 700)
    w.ctx.viewport.resize(1000, 700)
    w.ctx.viewport.center = Vec2(E, N)
    w.ctx.viewport.set_scale(5.0)
    yield w
    w.ctx.doc._modified = False  # nao dispara dialogo de descarte
    w.close()


def _type(win, text):
    """Digita na linha de comando e pressiona Enter."""
    win.cmdline.entry.setText(text)
    win.cmdline._submit()


def test_window_boots_with_empty_metric_document(win):
    assert win.ctx.doc.crs.srid == "EPSG:31982"
    assert len(win.ctx.doc) == 0
    assert win.ctx.registry.resolve("LINE") is not None


def test_command_line_activates_tool(win):
    _type(win, "LINE")
    assert win.ctx.tool is not None
    assert win.ctx.tool.name == "LINE"


def test_unknown_command_is_rejected_gracefully(win):
    _type(win, "COMANDOINEXISTENTE")
    assert win.ctx.tool is None


def test_command_abbreviation_resolves(win):
    _type(win, "L")  # alias de LINE
    assert win.ctx.tool is not None and win.ctx.tool.name == "LINE"


def test_draw_line_entirely_by_typed_coordinates(win):
    """O fluxo topografico: comando + coordenada absoluta + relativa."""
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@50,0")
    doc = win.ctx.doc
    assert len(doc) == 1
    line = next(doc.entities())
    assert line.dxftype() == "LINE"
    assert line.dxf.start.x == pytest.approx(E)
    assert line.dxf.end.x == pytest.approx(E + 50)
    assert line.dxf.end.y == pytest.approx(N)
    # a linha tem exatamente 50 m
    a = Vec2(line.dxf.start.x, line.dxf.start.y)
    b = Vec2(line.dxf.end.x, line.dxf.end.y)
    assert a.distance_to(b) == pytest.approx(50.0)


def test_draw_line_by_azimuth(win):
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@100<<90")  # 100 m para leste
    line = next(win.ctx.doc.entities())
    assert line.dxf.end.x == pytest.approx(E + 100)
    assert line.dxf.end.y == pytest.approx(N, abs=1e-6)


def test_polyline_closes_with_option_f(win):
    _type(win, "PLINE")
    _type(win, f"{E},{N}")
    _type(win, "@10,0")
    _type(win, "@0,10")
    _type(win, "F")
    poly = next(win.ctx.doc.entities())
    assert poly.dxftype() == "LWPOLYLINE"
    assert poly.closed
    assert len(poly) == 3


def test_escape_cancels_tool_without_drawing(win):
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    win.ctx.cancel_tool()
    assert win.ctx.tool is None
    assert len(win.ctx.doc) == 0


def test_undo_command_removes_entity(win):
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@50,0")
    assert len(win.ctx.doc) == 1
    _type(win, "U")
    assert len(win.ctx.doc) == 0
    _type(win, "REDO")
    assert len(win.ctx.doc) == 1


def test_line_lands_on_current_layer(win):
    _type(win, "CAMADA LIMITE")
    assert win.ctx.doc.current_layer == "LIMITE"
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@10,0")
    assert next(win.ctx.doc.entities()).dxf.layer == "LIMITE"


def test_escala_command_sets_map_scale(win):
    _type(win, "ESCALA 500")
    assert win.ctx.viewport.scale_denominator() == pytest.approx(500, rel=1e-6)


def test_pan_command_centers_on_coordinate(win):
    _type(win, f"PAN {E + 100},{N + 200}")
    assert win.ctx.viewport.center.x == pytest.approx(E + 100)
    assert win.ctx.viewport.center.y == pytest.approx(N + 200)


def test_zoom_extents_frames_the_drawing(win):
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@100,0")
    _type(win, "ZE")
    vis = win.ctx.viewport.visible_bbox()
    assert vis.minx <= E and vis.maxx >= E + 100


def _render(win, w=400, h=300):
    win.canvas.resize(w, h)
    win.ctx.viewport.resize(w, h)
    img = QImage(w, h, QImage.Format_RGB32)
    img.fill(0)
    p = QPainter(img)
    win.canvas.render(p, QPoint(0, 0))
    p.end()
    return img


def _color_histogram(img):
    from collections import Counter

    hist = Counter()
    for y in range(0, img.height(), 2):
        for x in range(0, img.width(), 2):
            hist[img.pixel(x, y)] += 1
    return hist


def test_canvas_actually_paints_entities(win):
    """Renderiza de verdade e confere que a entidade virou pixels."""
    _type(win, "LINE")
    _type(win, f"{E - 50},{N}")
    _type(win, "@100,0")
    _type(win, "ZE")

    before = _color_histogram(_render(win))
    win.ctx.doc.undo.undo()  # tira a linha
    after = _color_histogram(_render(win))

    assert before != after, "o desenho da entidade nao mudou nenhum pixel"
    # a linha atravessa a tela: tem de haver uma cor que sumiu em quantidade
    lost = sum(before.values()) - sum(v for k, v in before.items() if after.get(k, 0) >= v)
    assert lost > 50, f"poucos pixels mudaram ({lost}) - a linha nao foi desenhada"


def test_canvas_paints_background_and_grid(win):
    """A grade tem de sumir quando desligada."""
    win.canvas.show_grid = True
    with_grid = _color_histogram(_render(win))
    win.canvas.show_grid = False
    without_grid = _color_histogram(_render(win))
    assert len(with_grid) > len(without_grid), "a grade nao foi desenhada"


def test_console_executes_and_collapses_into_one_undo(win):
    console = win.console
    console.execute("for i in range(5):\n    add_line((0, 0), (i, 10))\n")
    assert len(win.ctx.doc) == 5
    win.ctx.doc.undo.undo()
    assert len(win.ctx.doc) == 0, "o script deveria desfazer num passo so"


def test_console_error_leaves_no_half_drawing(win):
    console = win.console
    console.execute("add_line((0,0),(1,1))\nraise ValueError('falha proposital')\n")
    assert len(win.ctx.doc) == 0, "script que falhou nao pode deixar entidade para tras"
    assert "ValueError" in console.output.toPlainText()


def test_console_api_command_uses_same_registry(win):
    win.console.execute("command('LINE')")
    assert win.ctx.tool is not None and win.ctx.tool.name == "LINE"


def test_console_geodesy_roundtrip(win):
    win.console.execute(f"lon, lat = to_wgs84({E}, {N})\nprint(round(lon, 6), round(lat, 6))")
    text = win.console.output.toPlainText()
    assert "-51.0" in text


def test_save_and_reopen_through_the_app(win, tmp_path):
    _type(win, "LINE")
    _type(win, f"{E},{N}")
    _type(win, "@123.456,0")
    path = tmp_path / "planta.dxf"

    from engecad.io.dxf_io import open_document, save_document

    save_document(win.ctx, path)
    assert path.exists()
    assert (tmp_path / "planta.emap.json").exists(), "sidecar nao foi escrito"

    open_document(win.ctx, path)
    assert len(win.ctx.doc) == 1
    assert win.ctx.doc.crs.srid == "EPSG:31982", "o CRS nao voltou do sidecar"
    line = next(win.ctx.doc.entities())
    assert line.dxf.end.x == pytest.approx(E + 123.456)


def test_layer_panel_lists_layers(win):
    assert win.layer_panel.tree.topLevelItemCount() == len(win.ctx.doc.layer_names())


def test_help_command_lists_registered_commands(win):
    _type(win, "AJUDA")
    text = win.console.output.toPlainText()
    assert "LINE" in text and "PLINE" in text


def test_mouse_click_draws_with_snap(win):
    """Clique do mouse tem de passar pelo snap e cair no ponto exato."""
    win.ctx.doc.add_line((E, N), (E + 100, N))
    win.ctx.doc.undo.clear()
    _type(win, "LINE")
    vp = win.ctx.viewport
    # ponto quase em cima do extremo (E,N): o snap deve corrigir
    sx, sy = vp.world_to_screen(Vec2(E + 0.4, N + 0.3))
    win.canvas._cursor_world = vp.screen_to_world(sx, sy)
    win.canvas._snap = win.ctx.snap.snap(win.canvas._cursor_world, vp)
    p = win.canvas.effective_point()
    assert p.x == pytest.approx(E), "o clique nao foi corrigido pelo snap"
    assert p.y == pytest.approx(N)


def test_keyboard_shortcut_key_constants_exist():
    assert Qt.Key_Escape is not None
