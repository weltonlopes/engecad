"""Nucleo da edicao: snapshot/undo, selecao, picking, grips, offset, aparar."""

import math

import pytest
from ezdxf.math import Matrix44

from engecad.core.document import Document
from engecad.core.geometry import BBox, Vec2
from engecad.core.grips import ANGLE, MOVE, RADIUS, VERTEX, drag_grip, entity_grips, nearest_grip
from engecad.core.offset import offset_entity, offset_points
from engecad.core.picking import entity_distance, pick_at, select_in_box
from engecad.core.selection import Selection
from engecad.core.snapshot import restore, snapshot, supports
from engecad.core.trimming import collect_shapes, extend_entity, trim_entity

E, N = 500000.0, 7400000.0


@pytest.fixture
def doc():
    return Document.new("EPSG:31982")


# ---------------- snapshot e undo exato ----------------


def test_snapshot_supports_the_types_we_create(doc):
    for e in (
        doc.add_line((E, N), (E + 1, N)),
        doc.add_lwpolyline([(E, N), (E + 1, N)]),
        doc.add_circle((E, N), 5),
        doc.add_arc((E, N), 5, 0, 90),
        doc.add_point((E, N)),
        doc.add_text("oi", (E, N)),
    ):
        assert supports(e), f"{e.dxftype()} sem suporte a snapshot"
        assert snapshot(e) is not None


def test_move_undo_restores_exact_coordinates(doc):
    """O motivo de existir o snapshot: a matriz inversa nao devolve o valor identico."""
    line = doc.add_line((E, N), (E + 10, N))
    doc.undo.clear()
    doc.transform([line], Matrix44.translate(123.456, 789.012, 0), "mover")
    doc.undo.undo()
    assert line.dxf.end.x == E + 10.0  # igualdade exata, nao approx
    assert line.dxf.end.y == N
    assert line.dxf.start.x == E


def test_repeated_undo_redo_does_not_drift(doc):
    line = doc.add_line((E, N), (E + 10, N))
    doc.undo.clear()
    doc.transform([line], Matrix44.translate(7.3, -2.1, 0), "mover")
    for _ in range(50):
        doc.undo.undo()
        doc.undo.redo()
    doc.undo.undo()
    assert line.dxf.end.x == E + 10.0
    assert line.dxf.end.y == N


def test_snapshot_restore_roundtrip_polyline(doc):
    poly = doc.add_lwpolyline([(E, N), (E + 10, N), (E + 10, N + 10)], closed=True)
    snap = snapshot(poly)
    poly.transform(Matrix44.translate(50, 50, 0))
    assert restore(poly, snap)
    pts = [(p[0], p[1]) for p in poly.get_points("xy")]
    assert pts[0] == (E, N)
    assert poly.closed


def test_editing_context_manager_is_one_undo_step(doc):
    a = doc.add_line((E, N), (E + 10, N))
    b = doc.add_line((E, N + 5), (E + 10, N + 5))
    doc.undo.clear()
    with doc.editing([a, b], "mover dois"):
        for e in (a, b):
            e.transform(Matrix44.translate(0, 100, 0))
    assert a.dxf.start.y == N + 100
    doc.undo.undo()
    assert a.dxf.start.y == N
    assert b.dxf.start.y == N + 5


def test_copy_entities_creates_independent_geometry(doc):
    line = doc.add_line((E, N), (E + 10, N))
    copies = doc.copy_entities([line], Matrix44.translate(0, 20, 0))
    assert len(copies) == 1
    assert len(doc) == 2
    assert copies[0].dxf.start.y == N + 20
    assert line.dxf.start.y == N  # o original nao se move
    doc.undo.undo()
    assert len(doc) == 1


def test_transform_updates_spatial_index(doc):
    line = doc.add_line((E, N), (E + 10, N))
    assert len(doc.query_point(Vec2(E + 5, N), 1.0)) == 1
    doc.transform([line], Matrix44.translate(0, 1000, 0), "mover")
    assert len(doc.query_point(Vec2(E + 5, N), 1.0)) == 0
    assert len(doc.query_point(Vec2(E + 5, N + 1000), 1.0)) == 1


# ---------------- selecao ----------------


def test_selection_is_unique_and_ordered(doc):
    a = doc.add_line((E, N), (E + 1, N))
    b = doc.add_line((E, N + 1), (E + 1, N + 1))
    sel = Selection(doc)
    sel.add([a, b, a])
    assert sel.items == [a, b]
    sel.toggle([a])
    assert sel.items == [b]
    sel.clear()
    assert not sel


def test_selection_prunes_deleted_entities(doc):
    a = doc.add_line((E, N), (E + 1, N))
    sel = Selection(doc)
    sel.set([a])
    doc.delete([a])
    sel.prune()
    assert len(sel) == 0, "unlink_entity mantem a entidade viva; a selecao tem de notar"


def test_undo_of_delete_restores_entity_but_not_selection(doc):
    """Como no AutoCAD: o objeto volta ao desenho, mas nao volta selecionado."""
    a = doc.add_line((E, N), (E + 1, N))
    sel = Selection(doc)
    sel.set([a])
    doc.delete([a])
    sel.prune()
    assert len(sel) == 0
    doc.undo.undo()
    assert len(doc) == 1
    assert len(sel) == 0


def test_selection_summary_counts_by_type(doc):
    sel = Selection(doc)
    sel.set([doc.add_line((E, N), (E + 1, N)), doc.add_circle((E, N), 1)])
    assert "2 selecionado" in sel.summary()


# ---------------- picking ----------------


def test_entity_distance_to_line(doc):
    line = doc.add_line((E, N), (E + 100, N))
    assert entity_distance(line, Vec2(E + 50, N + 3)) == pytest.approx(3.0)


def test_pick_finds_nearest_entity(doc):
    perto = doc.add_line((E, N), (E + 100, N))
    doc.add_line((E, N + 50), (E + 100, N + 50))
    assert pick_at(doc, Vec2(E + 50, N + 1), 5.0) is perto


def test_pick_returns_none_when_far(doc):
    doc.add_line((E, N), (E + 100, N))
    assert pick_at(doc, Vec2(E + 50, N + 80), 5.0) is None


def test_pick_ignores_hidden_layer(doc):
    doc.add_line((E, N), (E + 100, N), layer="LIMITE")
    assert pick_at(doc, Vec2(E + 50, N), 5.0) is not None
    doc.set_layer_visible("LIMITE", False)
    assert pick_at(doc, Vec2(E + 50, N), 5.0) is None


def test_window_selection_requires_full_containment(doc):
    dentro = doc.add_line((E + 10, N + 10), (E + 20, N + 20))
    doc.add_line((E + 10, N + 10), (E + 200, N + 200))  # sai da janela
    box = BBox(E, N, E + 50, N + 50)
    found = select_in_box(doc, box, crossing=False)
    assert found == [dentro]


def test_crossing_selection_catches_anything_touching(doc):
    dentro = doc.add_line((E + 10, N + 10), (E + 20, N + 20))
    atravessa = doc.add_line((E + 10, N + 10), (E + 200, N + 200))
    box = BBox(E, N, E + 50, N + 50)
    found = select_in_box(doc, box, crossing=True)
    assert set(found) == {dentro, atravessa}


def test_crossing_catches_line_passing_through_without_vertices():
    d = Document.new("EPSG:31982")
    reta = d.add_line((E - 100, N + 25), (E + 100, N + 25))  # atravessa a janela inteira
    box = BBox(E, N, E + 50, N + 50)
    assert select_in_box(d, box, crossing=True) == [reta]
    assert select_in_box(d, box, crossing=False) == []


# ---------------- grips ----------------


def test_line_has_two_vertex_grips_and_one_move_grip(doc):
    line = doc.add_line((E, N), (E + 10, N))
    grips = entity_grips(line)
    assert [g.kind for g in grips] == [VERTEX, VERTEX, MOVE]
    assert grips[2].point == Vec2(E + 5, N)


def test_drag_line_endpoint_grip(doc):
    line = doc.add_line((E, N), (E + 10, N))
    g = entity_grips(line)[1]
    with doc.editing([line], "esticar"):
        assert drag_grip(line, g, Vec2(E + 10, N + 10))
    assert line.dxf.end.y == pytest.approx(N + 10)
    assert line.dxf.start.x == pytest.approx(E)  # a outra ponta fica


def test_drag_line_middle_grip_moves_whole_line(doc):
    line = doc.add_line((E, N), (E + 10, N))
    g = entity_grips(line)[2]
    with doc.editing([line], "mover"):
        drag_grip(line, g, Vec2(E + 5, N + 30))
    assert line.dxf.start.y == pytest.approx(N + 30)
    assert line.dxf.end.y == pytest.approx(N + 30)


def test_drag_polyline_vertex_preserves_others(doc):
    poly = doc.add_lwpolyline([(E, N), (E + 10, N), (E + 10, N + 10)])
    g = entity_grips(poly)[1]
    with doc.editing([poly], "esticar"):
        drag_grip(poly, g, Vec2(E + 15, N + 5))
    pts = [(p[0], p[1]) for p in poly.get_points("xy")]
    assert pts[0] == (E, N)
    assert pts[1] == pytest.approx((E + 15, N + 5))
    assert pts[2] == (E + 10, N + 10)


def test_circle_grips_move_and_resize(doc):
    circ = doc.add_circle((E, N), 10)
    grips = entity_grips(circ)
    assert grips[0].kind == MOVE
    assert all(g.kind == RADIUS for g in grips[1:])
    with doc.editing([circ], "raio"):
        drag_grip(circ, grips[1], Vec2(E + 25, N))
    assert circ.dxf.radius == pytest.approx(25)


def test_arc_angle_grip(doc):
    arc = doc.add_arc((E, N), 10, 0, 90)
    grips = entity_grips(arc)
    ang = [g for g in grips if g.kind == ANGLE]
    assert len(ang) == 2
    with doc.editing([arc], "angulo"):
        drag_grip(arc, ang[1], Vec2(E - 10, N))
    assert arc.dxf.end_angle == pytest.approx(180.0)


def test_grip_edit_is_undoable(doc):
    line = doc.add_line((E, N), (E + 10, N))
    doc.undo.clear()
    g = entity_grips(line)[1]
    with doc.editing([line], "esticar"):
        drag_grip(line, g, Vec2(E + 99, N + 99))
    doc.undo.undo()
    assert line.dxf.end.x == E + 10.0


def test_nearest_grip_respects_tolerance(doc):
    line = doc.add_line((E, N), (E + 10, N))
    grips = entity_grips(line)
    assert nearest_grip(grips, Vec2(E + 0.1, N), 1.0) is not None
    assert nearest_grip(grips, Vec2(E + 2.5, N), 0.5) is None


# ---------------- offset ----------------


def test_offset_line_left_and_right(doc):
    line = doc.add_line((E, N), (E + 10, N))
    esq = offset_entity(line, 2.0)
    dir_ = offset_entity(line, -2.0)
    assert esq["points"][0].y == pytest.approx(N + 2)
    assert dir_["points"][0].y == pytest.approx(N - 2)


def test_offset_polyline_miters_the_corner():
    pts = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10)]
    out = offset_points(pts, 2.0)
    assert out[1] == Vec2(8, 2), "o vertice deve sair da intersecao das paralelas"


def test_offset_closed_polyline_keeps_vertex_count():
    quad = [Vec2(0, 0), Vec2(10, 0), Vec2(10, 10), Vec2(0, 10)]
    out = offset_points(quad, -1.0, closed=True)
    assert len(out) == 4
    xs = sorted(p.x for p in out)
    assert xs[0] == pytest.approx(-1) and xs[-1] == pytest.approx(11)


def test_offset_through_point_picks_the_side(doc):
    line = doc.add_line((E, N), (E + 10, N))
    acima = offset_entity(line, 0.0, through=Vec2(E + 5, N + 7))
    abaixo = offset_entity(line, 0.0, through=Vec2(E + 5, N - 7))
    assert acima["points"][0].y == pytest.approx(N + 7)
    assert abaixo["points"][0].y == pytest.approx(N - 7)


def test_offset_circle_side_point_decides_in_or_out(doc):
    """side_point: usa a distancia dada, o ponto so diz de que lado."""
    circ = doc.add_circle((E, N), 10)
    fora = offset_entity(circ, 3.0, side_point=Vec2(E + 20, N))
    dentro = offset_entity(circ, 3.0, side_point=Vec2(E + 2, N))
    assert fora["radius"] == pytest.approx(13)
    assert dentro["radius"] == pytest.approx(7)


def test_offset_circle_through_point_passes_exactly_there(doc):
    """through: a paralela passa pelo ponto, a distancia e ignorada."""
    circ = doc.add_circle((E, N), 10)
    spec = offset_entity(circ, 3.0, through=Vec2(E + 20, N))
    assert spec["radius"] == pytest.approx(20)


def test_offset_line_side_point_keeps_given_distance(doc):
    line = doc.add_line((E, N), (E + 10, N))
    acima = offset_entity(line, 5.0, side_point=Vec2(E + 5, N + 100))
    abaixo = offset_entity(line, 5.0, side_point=Vec2(E + 5, N - 100))
    assert acima["points"][0].y == pytest.approx(N + 5)
    assert abaixo["points"][0].y == pytest.approx(N - 5)


def test_offset_rejects_unsupported_type(doc):
    txt = doc.add_text("oi", (E, N))
    assert offset_entity(txt, 1.0) is None


# ---------------- aparar e estender ----------------


def test_trim_line_removes_clicked_side(doc):
    alvo = doc.add_line((E, N), (E + 100, N))
    corte = doc.add_line((E + 50, N - 10), (E + 50, N + 10))
    restos = trim_entity(doc, alvo, collect_shapes([corte]), Vec2(E + 25, N))
    assert len(restos) == 1
    assert restos[0].dxf.start.x == pytest.approx(E + 50)
    assert restos[0].dxf.end.x == pytest.approx(E + 100)


def test_trim_line_between_two_cutters_leaves_two_pieces(doc):
    alvo = doc.add_line((E, N), (E + 100, N))
    c1 = doc.add_line((E + 30, N - 5), (E + 30, N + 5))
    c2 = doc.add_line((E + 70, N - 5), (E + 70, N + 5))
    restos = trim_entity(doc, alvo, collect_shapes([c1, c2]), Vec2(E + 50, N))
    assert len(restos) == 2
    limites = sorted(r.dxf.start.x for r in restos)
    assert limites[0] == pytest.approx(E)
    assert limites[1] == pytest.approx(E + 70)


def test_trim_uses_exact_circle_intersection(doc):
    """A linha aparada tem de terminar exatamente sobre o circulo."""
    alvo = doc.add_line((E - 20, N), (E + 20, N))
    circ = doc.add_circle((E, N), 5)
    restos = trim_entity(doc, alvo, collect_shapes([circ]), Vec2(E, N))
    assert len(restos) == 2
    for r in restos:
        for ponto in (Vec2(r.dxf.start.x, r.dxf.start.y), Vec2(r.dxf.end.x, r.dxf.end.y)):
            d = ponto.distance_to(Vec2(E, N))
            # ou e a ponta original, ou esta exatamente no raio
            assert abs(d - 5.0) < 1e-9 or abs(d - 20.0) < 1e-9


def test_trim_circle_becomes_arc(doc):
    circ = doc.add_circle((E, N), 10)
    corte = doc.add_line((E - 20, N), (E + 20, N))
    restos = trim_entity(doc, circ, collect_shapes([corte]), Vec2(E, N + 10))
    assert len(restos) == 1
    arc = restos[0]
    assert arc.dxftype() == "ARC"
    assert arc.dxf.start_angle == pytest.approx(180.0)


def test_trim_polyline_keeps_remaining_vertices(doc):
    poly = doc.add_lwpolyline([(E, N), (E + 50, N), (E + 50, N + 50)])
    corte = doc.add_line((E + 25, N - 5), (E + 25, N + 5))
    restos = trim_entity(doc, poly, collect_shapes([corte]), Vec2(E + 10, N))
    assert len(restos) == 1
    pts = [(p[0], p[1]) for p in restos[0].get_points("xy")]
    assert pts[0] == pytest.approx((E + 25, N))
    assert pts[-1] == pytest.approx((E + 50, N + 50))


def test_trim_returns_none_without_cutters(doc):
    alvo = doc.add_line((E, N), (E + 100, N))
    longe = doc.add_line((E, N + 500), (E + 100, N + 500))
    assert trim_entity(doc, alvo, collect_shapes([longe]), Vec2(E + 50, N)) is None


def test_trim_refuses_polyline_with_bulge(doc):
    poly = doc.msp.add_lwpolyline(
        [(E, N, 0, 0, 0.5), (E + 50, N, 0, 0, 0), (E + 50, N + 50, 0, 0, 0)], format="xyseb"
    )
    doc.rebuild_index()
    corte = doc.add_line((E + 25, N - 20), (E + 25, N + 20))
    assert trim_entity(doc, poly, collect_shapes([corte]), Vec2(E + 10, N)) is None


def test_extend_line_reaches_the_boundary(doc):
    alvo = doc.add_line((E, N), (E + 40, N))
    limite = doc.add_line((E + 80, N - 20), (E + 80, N + 20))
    assert extend_entity(doc, alvo, collect_shapes([limite]), Vec2(E + 39, N))
    assert alvo.dxf.end.x == pytest.approx(E + 80)


def test_extend_picks_the_end_nearest_the_click(doc):
    alvo = doc.add_line((E + 40, N), (E + 80, N))
    limite = doc.add_line((E, N - 20), (E, N + 20))
    assert extend_entity(doc, alvo, collect_shapes([limite]), Vec2(E + 41, N))
    assert alvo.dxf.start.x == pytest.approx(E)
    assert alvo.dxf.end.x == pytest.approx(E + 80)


def test_extend_is_undoable(doc):
    alvo = doc.add_line((E, N), (E + 40, N))
    limite = doc.add_line((E + 80, N - 20), (E + 80, N + 20))
    doc.undo.clear()
    extend_entity(doc, alvo, collect_shapes([limite]), Vec2(E + 39, N))
    doc.undo.undo()
    assert alvo.dxf.end.x == E + 40.0


def test_extend_fails_when_nothing_ahead(doc):
    alvo = doc.add_line((E, N), (E + 40, N))
    atras = doc.add_line((E - 50, N - 20), (E - 50, N + 20))
    assert not extend_entity(doc, alvo, collect_shapes([atras]), Vec2(E + 39, N))


def test_trim_arc(doc):
    arc = doc.add_arc((E, N), 10, 0, 180)
    corte = doc.add_line((E - 20, N + 10), (E + 20, N + 10))
    restos = trim_entity(doc, arc, collect_shapes([corte]), Vec2(E, N + 10))
    assert restos
    assert all(r.dxftype() == "ARC" for r in restos)
    # o topo (90 graus) foi removido
    for r in restos:
        span_start = r.dxf.start_angle
        span_end = r.dxf.end_angle
        assert not (span_start < 90 < span_end)


def test_arc_solver_orientation():
    from engecad.tools.shapes import ArcTool

    # anti-horario: (10,0) -> (0,10) -> (-10,0)
    c, r, a0, a1 = ArcTool.solve(Vec2(10, 0), Vec2(0, 10), Vec2(-10, 0))
    assert r == pytest.approx(10)
    assert a0 == pytest.approx(0)
    assert a1 == pytest.approx(180)
    # horario: os extremos trocam para o DXF continuar anti-horario
    c2, r2, b0, b1 = ArcTool.solve(Vec2(-10, 0), Vec2(0, 10), Vec2(10, 0))
    assert b0 == pytest.approx(0)
    assert b1 == pytest.approx(180)


def test_arc_solver_rejects_collinear():
    from engecad.tools.shapes import ArcTool

    assert ArcTool.solve(Vec2(0, 0), Vec2(5, 0), Vec2(10, 0)) is None


def test_mirror_matrix_reflects_across_axis():
    from engecad.tools.modify import MirrorTool

    m = MirrorTool._matrix(Vec2(0, 0), Vec2(1, 0))  # eixo horizontal
    v = m.transform((3.0, 5.0, 0.0))
    assert v.x == pytest.approx(3.0)
    assert v.y == pytest.approx(-5.0)


def test_mirror_matrix_on_diagonal_axis():
    from engecad.tools.modify import MirrorTool

    m = MirrorTool._matrix(Vec2(0, 0), Vec2(1, 1))  # eixo a 45 graus
    v = m.transform((1.0, 0.0, 0.0))
    assert v.x == pytest.approx(0.0, abs=1e-9)
    assert v.y == pytest.approx(1.0)


def test_rotate_matrix_about_base():
    from engecad.tools.modify import RotateTool

    m = RotateTool._matrix(Vec2(E, N), math.radians(90))
    v = m.transform((E + 10.0, N, 0.0))
    assert v.x == pytest.approx(E, abs=1e-6)
    assert v.y == pytest.approx(N + 10)
