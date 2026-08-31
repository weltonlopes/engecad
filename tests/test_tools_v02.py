"""Ferramentas da v0.2 exercitadas pela aplicacao real (offscreen).

Aqui o mouse e simulado com QMouseEvent de verdade, passando pelo canvas, pelo
snap e pela ferramenta ativa -- o mesmo caminho de um clique do usuario.
"""

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPointF, Qt  # noqa: E402
from PySide6.QtGui import QKeyEvent, QMouseEvent  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from engecad.core.geometry import Vec2  # noqa: E402
from engecad.ui.main_window import MainWindow  # noqa: E402

E, N = 500000.0, 7400000.0


@pytest.fixture(scope="session")
def qapp():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def win(qapp):
    w = MainWindow()
    w.show()
    w.canvas.resize(1000, 700)
    w.ctx.viewport.resize(1000, 700)
    w.ctx.viewport.center = Vec2(E + 50, N + 25)
    w.ctx.viewport.set_scale(5.0)
    yield w
    w.ctx.doc._modified = False
    w.close()


def _type(win, text):
    win.cmdline.entry.setText(text)
    win.cmdline._submit()


def _evt(win, world, kind, button=Qt.LeftButton, mods=Qt.NoModifier):
    sx, sy = win.ctx.viewport.world_to_screen(Vec2.of(world))
    pos = QPointF(sx, sy)
    return QMouseEvent(kind, pos, pos, button, button, mods)


def _move(win, world):
    win.canvas.mouseMoveEvent(_evt(win, world, QEvent.MouseMove, Qt.NoButton))


def _click(win, world, mods=Qt.NoModifier, button=Qt.LeftButton):
    """Clique completo: mover, pressionar, soltar -- como um usuario faz."""
    _move(win, world)
    win.canvas.mousePressEvent(_evt(win, world, QEvent.MouseButtonPress, button, mods))
    win.canvas.mouseReleaseEvent(_evt(win, world, QEvent.MouseButtonRelease, button, mods))


def _drag(win, start, end, mods=Qt.NoModifier):
    """Arrasto: pressiona num ponto, move, solta em outro (janela de selecao)."""
    _move(win, start)
    win.canvas.mousePressEvent(_evt(win, start, QEvent.MouseButtonPress, Qt.LeftButton, mods))
    _move(win, end)
    win.canvas.mouseReleaseEvent(_evt(win, end, QEvent.MouseButtonRelease, Qt.LeftButton, mods))


def _enter(win):
    win.canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Return, Qt.NoModifier))


def _grade(win):
    """Desenha uma grade de referencia: 3 linhas horizontais bem separadas."""
    d = win.ctx.doc
    linhas = [d.add_line((E, N + i * 20), (E + 100, N + i * 20)) for i in range(3)]
    d.undo.clear()
    win.ctx.selection.clear()
    return linhas


# ---------------- selecao ----------------


def test_idle_tool_is_select(win):
    assert win.ctx.idle
    assert win.ctx.tool.name == "SELECT"


def test_click_selects_entity(win):
    linhas = _grade(win)
    _click(win, (E + 50, N))
    assert win.ctx.selection.items == [linhas[0]]


def test_click_on_empty_space_clears_selection(win):
    """Como no AutoCAD: o 1o clique em area vazia so abre a janela elastica;
    o 2o clique e que confirma (aqui, uma janela vazia -> deselecao)."""
    _grade(win)
    _click(win, (E + 50, N))
    assert len(win.ctx.selection) == 1
    _click(win, (E + 50, N + 70))
    _click(win, (E + 50, N + 70))
    assert len(win.ctx.selection) == 0


def test_shift_click_adds_to_selection(win):
    linhas = _grade(win)
    _click(win, (E + 50, N))
    _click(win, (E + 50, N + 20), mods=Qt.ShiftModifier)
    assert set(win.ctx.selection.items) == {linhas[0], linhas[1]}


def test_shift_click_twice_toggles_off(win):
    _grade(win)
    _click(win, (E + 30, N))
    _click(win, (E + 30, N), mods=Qt.ShiftModifier)
    assert len(win.ctx.selection) == 0


def test_shift_click_ignores_grips(win):
    """Com Shift o usuario esta montando selecao: o grip nao pode capturar."""
    linhas = _grade(win)
    _click(win, (E + 30, N))  # seleciona -> aparecem grips
    _click(win, (E + 50, N), mods=Qt.ShiftModifier)  # em cima do grip do meio
    assert win.ctx.tool.name == "SELECT", "Shift+clique nao deve entrar em GRIP"
    assert len(win.ctx.selection) == 0  # alternou para fora
    assert linhas[0].dxf.start.y == N  # e nao esticou nada


def test_window_drag_left_to_right_requires_containment(win):
    d = win.ctx.doc
    dentro = d.add_line((E + 10, N + 5), (E + 30, N + 15))
    d.add_line((E + 10, N + 5), (E + 300, N + 200))  # sai da janela
    d.undo.clear()
    _drag(win, (E, N), (E + 50, N + 30))  # esquerda -> direita
    assert win.ctx.selection.items == [dentro]


def test_crossing_drag_right_to_left_catches_everything(win):
    d = win.ctx.doc
    dentro = d.add_line((E + 10, N + 5), (E + 30, N + 15))
    atravessa = d.add_line((E + 10, N + 5), (E + 300, N + 200))
    d.undo.clear()
    _drag(win, (E + 50, N + 30), (E, N))  # direita -> esquerda
    assert set(win.ctx.selection.items) == {dentro, atravessa}


def test_click_click_window_confirms_on_second_click(win):
    """Como no AutoCAD: clicar (sem arrastar) em area vazia abre a janela
    elastica, que so e confirmada no clique seguinte."""
    d = win.ctx.doc
    dentro = d.add_line((E + 10, N + 5), (E + 30, N + 15))
    d.add_line((E + 10, N + 5), (E + 300, N + 200))  # sai da janela
    d.undo.clear()
    _click(win, (E, N))  # 1o clique: so ancora a janela
    assert win.ctx.tool.pick.awaiting_confirm
    _click(win, (E + 50, N + 30))  # 2o clique: confirma, esquerda -> direita
    assert win.ctx.selection.items == [dentro]
    assert not win.ctx.tool.pick.awaiting_confirm


def test_click_click_crossing_catches_everything(win):
    d = win.ctx.doc
    dentro = d.add_line((E + 10, N + 5), (E + 30, N + 15))
    atravessa = d.add_line((E + 10, N + 5), (E + 300, N + 200))
    d.undo.clear()
    _click(win, (E + 50, N + 60))  # ancora a janela, em area vazia
    _click(win, (E, N))  # confirma, direita -> esquerda = captura
    assert set(win.ctx.selection.items) == {dentro, atravessa}


def test_seltudo_command(win):
    _grade(win)
    _type(win, "SELTUDO")
    assert len(win.ctx.selection) == 3
    _type(win, "SELNADA")
    assert len(win.ctx.selection) == 0


def test_delete_key_erases_selection(win):
    _grade(win)
    _type(win, "SELTUDO")
    win.canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Delete, Qt.NoModifier))
    assert len(win.ctx.doc) == 0
    assert len(win.ctx.selection) == 0


def test_escape_clears_selection(win):
    _grade(win)
    _type(win, "SELTUDO")
    win.canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, Qt.Key_Escape, Qt.NoModifier))
    assert len(win.ctx.selection) == 0


# ---------------- teclado ----------------


def test_typing_on_canvas_goes_to_command_line(win):
    """Digitar com o foco no desenho tem de cair na linha de comando."""
    win.cmdline.entry.clear()
    for ch, key in (("L", Qt.Key_L), ("I", Qt.Key_I)):
        win.canvas.keyPressEvent(QKeyEvent(QEvent.KeyPress, key, Qt.NoModifier, ch))
    assert win.cmdline.entry.text() == "LI"


# ---------------- mover, copiar, girar ----------------


def test_move_selection_by_typed_coordinates(win):
    linhas = _grade(win)
    win.ctx.selection.set([linhas[0]])
    _type(win, "MOVE")
    _type(win, f"{E},{N}")
    _type(win, "@0,100")
    assert linhas[0].dxf.start.y == pytest.approx(N + 100)
    assert linhas[1].dxf.start.y == pytest.approx(N + 20)  # as outras nao se mexem


def test_move_is_one_undo_step(win):
    linhas = _grade(win)
    win.ctx.selection.set(linhas)
    _type(win, "MOVE")
    _type(win, f"{E},{N}")
    _type(win, "@0,100")
    assert linhas[0].dxf.start.y == pytest.approx(N + 100)
    _type(win, "U")
    assert linhas[0].dxf.start.y == N  # exato


def test_move_without_selection_enters_selection_phase(win):
    linhas = _grade(win)
    _type(win, "MOVE")
    assert win.ctx.tool.phase == "select"
    _click(win, (E + 50, N))
    _enter(win)
    assert win.ctx.tool.phase == "points"
    _type(win, f"{E},{N}")
    _type(win, "@0,50")
    assert linhas[0].dxf.start.y == pytest.approx(N + 50)


def test_copy_repeats_until_finished(win):
    linhas = _grade(win)
    win.ctx.selection.set([linhas[0]])
    _type(win, "COPY")
    _type(win, f"{E},{N}")
    _type(win, "@0,100")
    _type(win, "@0,200")
    assert len(win.ctx.doc) == 5  # 3 originais + 2 copias
    assert linhas[0].dxf.start.y == N  # o original fica


def test_rotate_by_typed_angle(win):
    d = win.ctx.doc
    linha = d.add_line((E, N), (E + 10, N))
    d.undo.clear()
    win.ctx.selection.set([linha])
    _type(win, "ROTATE")
    _type(win, f"{E},{N}")
    _type(win, "90")
    assert linha.dxf.end.x == pytest.approx(E, abs=1e-6)
    assert linha.dxf.end.y == pytest.approx(N + 10)


def test_scale_by_typed_factor(win):
    d = win.ctx.doc
    linha = d.add_line((E, N), (E + 10, N))
    d.undo.clear()
    win.ctx.selection.set([linha])
    _type(win, "SCALE")
    _type(win, f"{E},{N}")
    _type(win, "2")
    assert linha.dxf.end.x == pytest.approx(E + 20)


def test_mirror_keeps_original_by_default(win):
    d = win.ctx.doc
    linha = d.add_line((E, N + 10), (E + 10, N + 10))
    d.undo.clear()
    win.ctx.selection.set([linha])
    _type(win, "MIRROR")
    _type(win, f"{E},{N}")
    _type(win, f"{E + 100},{N}")  # eixo horizontal em N
    assert len(d) == 2
    ys = sorted(e.dxf.start.y for e in d.entities())
    assert ys[0] == pytest.approx(N - 10)
    assert ys[1] == pytest.approx(N + 10)


def test_mirror_can_delete_original(win):
    d = win.ctx.doc
    linha = d.add_line((E, N + 10), (E + 10, N + 10))
    d.undo.clear()
    win.ctx.selection.set([linha])
    _type(win, "MIRROR")
    _type(win, f"{E},{N}")
    _type(win, "A")  # apagar o original
    _type(win, f"{E + 100},{N}")
    assert len(d) == 1
    assert next(d.entities()).dxf.start.y == pytest.approx(N - 10)


def test_erase_command_removes_selection(win):
    linhas = _grade(win)
    win.ctx.selection.set([linhas[0]])
    _type(win, "ERASE")
    assert len(win.ctx.doc) == 2
    _type(win, "U")
    assert len(win.ctx.doc) == 3


# ---------------- paralela ----------------


def test_offset_creates_parallel_on_clicked_side(win):
    d = win.ctx.doc
    d.add_line((E, N), (E + 100, N))
    d.undo.clear()
    _type(win, "OFFSET")
    _type(win, "5")
    _click(win, (E + 50, N))  # escolhe o objeto
    _click(win, (E + 50, N + 30))  # lado de cima
    assert len(d) == 2
    nova = [e for e in d.entities() if e.dxf.start.y != N][0]
    assert nova.dxf.start.y == pytest.approx(N + 5)


def test_offset_other_side(win):
    d = win.ctx.doc
    d.add_line((E, N), (E + 100, N))
    d.undo.clear()
    _type(win, "OFFSET")
    _type(win, "5")
    _click(win, (E + 50, N))
    _click(win, (E + 50, N - 30))
    nova = [e for e in d.entities() if e.dxf.start.y != N][0]
    assert nova.dxf.start.y == pytest.approx(N - 5)


# ---------------- aparar e estender ----------------


def test_trim_removes_clicked_piece(win):
    d = win.ctx.doc
    d.add_line((E, N), (E + 100, N))
    d.add_line((E + 50, N - 20), (E + 50, N + 20))
    d.undo.clear()
    _type(win, "TRIM")
    _click(win, (E + 25, N))
    restantes = [e for e in d.entities() if e.dxftype() == "LINE"]
    horizontais = [e for e in restantes if e.dxf.start.y == e.dxf.end.y]
    assert len(horizontais) == 1
    assert horizontais[0].dxf.start.x == pytest.approx(E + 50)


def test_trim_is_undoable_as_one_step(win):
    d = win.ctx.doc
    d.add_line((E, N), (E + 100, N))
    d.add_line((E + 50, N - 20), (E + 50, N + 20))
    d.undo.clear()
    _type(win, "TRIM")
    _click(win, (E + 25, N))
    _type(win, "U")  # dentro do TRIM, U desfaz o ultimo corte
    horizontais = [
        e for e in d.entities() if e.dxftype() == "LINE" and e.dxf.start.y == e.dxf.end.y
    ]
    assert len(horizontais) == 1
    assert horizontais[0].dxf.start.x == pytest.approx(E)
    assert horizontais[0].dxf.end.x == pytest.approx(E + 100)


def test_trim_without_cutter_leaves_drawing_untouched(win):
    d = win.ctx.doc
    d.add_line((E, N), (E + 100, N))
    d.undo.clear()
    antes = len(d)
    _type(win, "TRIM")
    _click(win, (E + 50, N))
    assert len(d) == antes
    assert not d.undo.can_undo, "uma tentativa falha nao pode empilhar undo"


def test_extend_reaches_boundary(win):
    d = win.ctx.doc
    alvo = d.add_line((E, N), (E + 40, N))
    d.add_line((E + 80, N - 20), (E + 80, N + 20))
    d.undo.clear()
    _type(win, "EXTEND")
    _click(win, (E + 39, N))
    assert alvo.dxf.end.x == pytest.approx(E + 80)


# ---------------- grips ----------------


def test_grip_drag_stretches_line(win):
    d = win.ctx.doc
    linha = d.add_line((E, N), (E + 50, N))
    d.undo.clear()
    _click(win, (E + 25, N))  # seleciona
    assert len(win.ctx.selection) == 1
    _click(win, (E + 50, N))  # pega o grip da ponta
    assert win.ctx.tool.name == "GRIP"
    _click(win, (E + 50, N + 30))  # solta no destino
    assert linha.dxf.end.y == pytest.approx(N + 30)
    assert linha.dxf.start.y == pytest.approx(N)


def test_grip_drag_is_undoable(win):
    d = win.ctx.doc
    linha = d.add_line((E, N), (E + 50, N))
    d.undo.clear()
    _click(win, (E + 25, N))
    _click(win, (E + 50, N))
    _click(win, (E + 50, N + 30))
    _type(win, "U")
    assert linha.dxf.end.y == N  # exato


def test_grip_edit_accepts_typed_coordinate(win):
    d = win.ctx.doc
    linha = d.add_line((E, N), (E + 50, N))
    d.undo.clear()
    _click(win, (E + 25, N))
    _click(win, (E + 50, N))
    _type(win, "@0,15")
    assert linha.dxf.end.y == pytest.approx(N + 15)


# ---------------- novas formas ----------------


def test_rectangle_is_closed_polyline(win):
    _type(win, "RECT")
    _type(win, f"{E},{N}")
    _type(win, "@40,20")
    poly = next(win.ctx.doc.entities())
    assert poly.dxftype() == "LWPOLYLINE"
    assert poly.closed
    assert len(poly) == 4
    xs = [p[0] for p in poly.get_points("xy")]
    assert max(xs) - min(xs) == pytest.approx(40)


def test_circle_with_typed_radius(win):
    _type(win, "CIRCLE")
    _type(win, f"{E},{N}")
    _type(win, "12.5")
    circ = next(win.ctx.doc.entities())
    assert circ.dxftype() == "CIRCLE"
    assert circ.dxf.radius == pytest.approx(12.5)


def test_circle_by_two_points(win):
    _type(win, "CIRCLE")
    _type(win, f"{E},{N}")
    _type(win, "@10,0")
    circ = next(win.ctx.doc.entities())
    assert circ.dxf.radius == pytest.approx(10)


def test_arc_by_three_points(win):
    _type(win, "ARC")
    _type(win, f"{E + 10},{N}")
    _type(win, f"{E},{N + 10}")
    _type(win, f"{E - 10},{N}")
    arc = next(win.ctx.doc.entities())
    assert arc.dxftype() == "ARC"
    assert arc.dxf.radius == pytest.approx(10)
    assert arc.dxf.center.x == pytest.approx(E)


def test_arc_collinear_points_are_rejected(win):
    _type(win, "ARC")
    _type(win, f"{E},{N}")
    _type(win, f"{E + 5},{N}")
    _type(win, f"{E + 10},{N}")
    assert len(win.ctx.doc) == 0


def test_text_with_height_and_content(win):
    _type(win, "TEXT")
    _type(win, f"{E},{N}")
    _type(win, "3")
    _type(win, "LOTE 15")
    txt = next(win.ctx.doc.entities())
    assert txt.dxftype() == "TEXT"
    assert txt.dxf.text == "LOTE 15"
    assert txt.dxf.height == pytest.approx(3.0)


def test_text_enter_accepts_default_height(win):
    _type(win, "TEXT")
    _type(win, f"{E},{N}")
    _enter(win)
    _type(win, "AREA REMANESCENTE")
    txt = next(win.ctx.doc.entities())
    assert txt.dxf.height == pytest.approx(2.5)


# ---------------- API de script ----------------


def test_script_api_move_and_undo(win):
    win.console.execute(
        f"e = add_line(({E}, {N}), ({E + 10}, {N}))\nmove(e, 0, 100)\n"
    )
    linha = next(win.ctx.doc.entities())
    assert linha.dxf.start.y == pytest.approx(N + 100)
    win.ctx.doc.undo.undo()
    assert len(win.ctx.doc) == 0, "criar e mover no mesmo script desfaz junto"


def test_script_api_rotate_and_offset(win):
    win.console.execute(
        f"e = add_line(({E}, {N}), ({E + 10}, {N}))\n"
        f"rotate(e, ({E}, {N}), 90)\n"
        f"p = offset(e, 2)\nprint('ok' if p else 'falhou')\n"
    )
    assert "ok" in win.console.output.toPlainText()
    assert len(win.ctx.doc) == 2


def test_script_api_selection(win):
    _grade(win)
    win.console.execute("sel = select_all()\nprint(len(sel))")
    assert "3" in win.console.output.toPlainText()
    assert len(win.ctx.selection) == 3
