import math
from types import SimpleNamespace

import pytest
from ezdxf.math import Matrix44

from engecad.context import AppContext
from engecad.core.associative import (
    anchor_for_entity,
    anchor_from_snap,
    association_status,
    get_dimension_associations,
)
from engecad.core.dimensions import dimension_measurement
from engecad.core.document import Document
from engecad.core.geometry import Vec2
from engecad.core.grips import RADIUS, VERTEX, drag_grip, entity_grips
from engecad.snap.engine import SnapResult
from engecad.tools.dimension import LinearDimensionTool


def _associated_linear(doc, line):
    return doc.add_linear_dimension(
        line.dxf.start,
        line.dxf.end,
        (5, 4),
        associations={
            "defpoint2": anchor_for_entity(line, line.dxf.start, "end"),
            "defpoint3": anchor_for_entity(line, line.dxf.end, "end"),
        },
    )


def test_source_scale_updates_dimension_and_undo_redo_together():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(doc, line)

    doc.transform([line], Matrix44.scale(2, 2, 1), "escalar")
    assert line.dxf.end.x == pytest.approx(20)
    assert dimension_measurement(dimension) == pytest.approx(20)
    assert dimension.dxf.defpoint3.x == pytest.approx(20)

    assert doc.undo.undo()
    assert line.dxf.end.x == pytest.approx(10)
    assert dimension_measurement(dimension) == pytest.approx(10)
    assert doc.undo.redo()
    assert dimension_measurement(dimension) == pytest.approx(20)


def test_aligned_dimension_rotates_with_source_and_keeps_offset():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = doc.add_aligned_dimension(
        (0, 0),
        (10, 0),
        (5, 3),
        associations={
            "defpoint2": anchor_for_entity(line, (0, 0), "end"),
            "defpoint3": anchor_for_entity(line, (10, 0), "end"),
        },
    )

    doc.transform([line], Matrix44.z_rotate(math.pi / 2), "girar")
    assert dimension.dxf.angle == pytest.approx(90)
    assert dimension_measurement(dimension) == pytest.approx(10)
    base = Vec2.of(dimension.dxf.defpoint)
    assert base.x == pytest.approx(-3)
    assert base.y == pytest.approx(0)

    assert doc.undo.undo()
    assert dimension.dxf.angle == pytest.approx(0)
    assert dimension_measurement(dimension) == pytest.approx(10)
    assert doc.undo.redo()
    assert dimension.dxf.angle == pytest.approx(90)


def test_moving_associated_dimension_repositions_line_but_keeps_source_points():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = doc.add_aligned_dimension(
        (0, 0),
        (10, 0),
        (5, 3),
        associations={
            "defpoint2": anchor_for_entity(line, (0, 0), "end"),
            "defpoint3": anchor_for_entity(line, (10, 0), "end"),
        },
    )

    doc.transform([dimension], Matrix44.translate(0, 2, 0), "mover cota")
    assert Vec2.of(dimension.dxf.defpoint2) == Vec2(0, 0)
    assert Vec2.of(dimension.dxf.defpoint3) == Vec2(10, 0)
    assert Vec2.of(dimension.dxf.defpoint).y == pytest.approx(5)


def test_interactive_tool_uses_snap_sources_as_associations():
    ctx = AppContext(Document.new())
    line = ctx.doc.add_line((0, 0), (10, 0))
    tool = LinearDimensionTool(ctx, angle=0)
    tool.add_point(Vec2(0, 0), SnapResult(Vec2(0, 0), "end", line))
    tool.add_point(Vec2(10, 0), SnapResult(Vec2(10, 0), "end", line))
    tool.add_point(Vec2(5, 4), None)
    dimension = next(e for e in ctx.doc.entities() if e.dxftype() == "DIMENSION")

    associations = get_dimension_associations(dimension)
    assert set(associations) == {"defpoint2", "defpoint3"}
    assert association_status(ctx.doc, dimension) == (2, 2)


def test_radius_dimension_follows_circle_center_and_radius_grip():
    doc = Document.new()
    circle = doc.add_circle((20, 30), 5)
    center = Vec2(20, 30)
    surface = Vec2(25, 30)
    dimension = doc.add_radius_dimension(
        center,
        5,
        (28, 30),
        associations={
            "defpoint": anchor_for_entity(circle, center, "center"),
            "defpoint4": anchor_for_entity(circle, surface, "nearest"),
        },
    )
    radius_grip = next(g for g in entity_grips(circle) if g.kind == RADIUS)

    with doc.editing([circle], "alterar raio"):
        assert drag_grip(circle, radius_grip, Vec2(30, 30))
    assert circle.dxf.radius == pytest.approx(10)
    assert dimension_measurement(dimension) == pytest.approx(10)
    assert Vec2.of(dimension.dxf.defpoint4) == Vec2(30, 30)


def test_intersection_anchor_tracks_both_source_entities():
    doc = Document.new()
    horizontal = doc.add_line((0, 0), (10, 0))
    vertical = doc.add_line((5, -5), (5, 5))
    snap = SnapResult(Vec2(5, 0), "intersection", (horizontal, vertical))
    dimension = doc.add_linear_dimension(
        (5, 0),
        (10, 0),
        (7, 3),
        associations={
            "defpoint2": anchor_from_snap(snap),
            "defpoint3": anchor_for_entity(horizontal, (10, 0), "end"),
        },
    )

    with doc.editing([vertical], "mover intersecao"):
        vertical.dxf.start = (7, -5, 0)
        vertical.dxf.end = (7, 5, 0)
    assert Vec2.of(dimension.dxf.defpoint2) == Vec2(7, 0)
    assert dimension_measurement(dimension) == pytest.approx(3)


def test_associations_survive_dxf_roundtrip(tmp_path):
    doc = Document.new()
    line = doc.add_line((500000, 7400000), (500010, 7400000))
    _associated_linear(doc, line)
    path = tmp_path / "associativa.dxf"
    doc.save(path)

    reopened = Document.open(path)
    source = next(e for e in reopened.entities() if e.dxftype() == "LINE")
    dimension = next(e for e in reopened.entities() if e.dxftype() == "DIMENSION")
    assert association_status(reopened, dimension) == (2, 2)

    reopened.transform([source], Matrix44.scale(2, 2, 1), "escalar")
    assert dimension_measurement(dimension) == pytest.approx(20)


def test_deleted_source_marks_orphan_and_undo_restores_association():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(doc, line)

    doc.delete([line])
    assert association_status(doc, dimension) == (2, 0)
    assert dimension_measurement(dimension) == pytest.approx(10)
    assert doc.undo.undo()
    assert association_status(doc, dimension) == (2, 2)


def test_copying_source_and_dimension_remaps_handles_to_copies():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(doc, line)
    copied_line, copied_dimension = doc.copy_entities(
        [line, dimension], Matrix44.translate(20, 0, 0)
    )

    handles = {
        anchor["h"] for anchor in get_dimension_associations(copied_dimension).values()
    }
    assert handles == {copied_line.dxf.handle}
    assert Vec2.of(copied_dimension.dxf.defpoint2) == Vec2(20, 0)
    assert association_status(doc, copied_dimension) == (2, 2)


def test_dragging_dimension_definition_point_detaches_only_that_anchor_and_is_undoable():
    doc = Document.new()
    line = doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(doc, line)
    grip = next(g for g in entity_grips(dimension) if g.kind == VERTEX and g.index == 0)

    with doc.editing([dimension], "soltar ponto associativo"):
        assert drag_grip(dimension, grip, Vec2(-2, 0))
    assert set(get_dimension_associations(dimension)) == {"defpoint3"}
    assert Vec2.of(dimension.dxf.defpoint2) == Vec2(-2, 0)

    assert doc.undo.undo()
    assert set(get_dimension_associations(dimension)) == {"defpoint2", "defpoint3"}
    assert Vec2.of(dimension.dxf.defpoint2) == Vec2(0, 0)


def test_dimdisassociate_command_and_undo():
    ctx = AppContext(Document.new())
    line = ctx.doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(ctx.doc, line)
    ctx.selection.set([dimension])

    assert ctx.run_command("DIMDISASSOCIATE")
    assert get_dimension_associations(dimension) == {}
    assert ctx.doc.undo.undo()
    assert association_status(ctx.doc, dimension) == (2, 2)


def test_dimreassociate_command_rebuilds_links_from_snaps():
    ctx = AppContext(Document.new())
    line = ctx.doc.add_line((0, 0), (10, 0))
    dimension = ctx.doc.add_linear_dimension((0, 0), (10, 0), (5, 4))
    ctx.selection.set([dimension])
    canvas = SimpleNamespace(current_snap=None, update=lambda: None)
    ctx.canvas = canvas

    assert ctx.run_command("DIMREASSOCIATE")
    tool = ctx.tool
    canvas.current_snap = SnapResult(Vec2(0, 0), "end", line)
    tool.on_click(Vec2(0, 0))
    canvas.current_snap = SnapResult(Vec2(10, 0), "end", line)
    tool.on_click(Vec2(10, 0))

    assert association_status(ctx.doc, dimension) == (2, 2)
    assert ctx.tool.is_idle


def test_trim_rebinds_anchors_on_surviving_pieces():
    doc = Document.new()
    original = doc.add_line((0, 0), (10, 0))
    dimension = _associated_linear(doc, original)
    left = doc.add_line((0, 0), (4, 0))
    right = doc.add_line((6, 0), (10, 0))

    assert doc.rebind_replacement_associations(original, [left, right]) == 1
    doc.delete([original])
    associations = get_dimension_associations(dimension)
    assert {a["h"] for a in associations.values()} == {
        left.dxf.handle,
        right.dxf.handle,
    }
    assert association_status(doc, dimension) == (2, 2)


def test_trimmed_away_anchor_remains_orphan_instead_of_jumping():
    doc = Document.new()
    original = doc.add_line((0, 0), (10, 0))
    dimension = doc.add_linear_dimension(
        (5, 0),
        (10, 0),
        (7, 3),
        associations={
            "defpoint2": anchor_for_entity(original, (5, 0), "mid"),
            "defpoint3": anchor_for_entity(original, (10, 0), "end"),
        },
    )
    left = doc.add_line((0, 0), (4, 0))
    right = doc.add_line((6, 0), (10, 0))

    doc.rebind_replacement_associations(original, [left, right])
    doc.delete([original])
    assert association_status(doc, dimension) == (2, 1)
    assert Vec2.of(dimension.dxf.defpoint2) == Vec2(5, 0)
