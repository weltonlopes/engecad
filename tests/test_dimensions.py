from dataclasses import replace

import pytest

from engecad.context import AppContext
from engecad.core.dimensions import (
    DIMSTYLE_NAME,
    dimension_kind,
    dimension_measurement,
    dimension_primitives,
)
from engecad.core.document import Document
from engecad.core.entities import entity_polylines, entity_snap_points
from engecad.core.geometry import Vec2
from engecad.core.grips import DIM_TEXT, VERTEX, drag_grip, entity_grips
from engecad.core.picking import pick_at
from engecad.tools.dimension import (
    AlignedDimensionTool,
    AngularDimensionTool,
    ArcLengthDimensionTool,
    DiameterDimensionTool,
    LinearDimensionTool,
    OrdinateDimensionTool,
    RadiusDimensionTool,
)


def test_linear_dimension_is_native_rendered_and_undoable():
    doc = Document.new()
    entity = doc.add_linear_dimension((0, 0), (10, 0), (0, 4))

    assert entity.dxftype() == "DIMENSION"
    assert (entity.dxf.dimtype & 15) == 0
    assert entity.dxf.layer == "COTA"
    assert entity.dxf.dimstyle == DIMSTYLE_NAME
    assert dimension_measurement(entity) == pytest.approx(10)
    assert any(p.dxftype() == "MTEXT" for p in dimension_primitives(entity))
    assert entity_polylines(entity)
    assert doc.extents().maxy > 3.9

    assert doc.undo.undo()
    assert len(doc) == 0
    assert doc.undo.redo()
    assert len(doc) == 1


def test_all_supported_dimension_kinds_have_correct_measurement():
    doc = Document.new()
    made = [
        # O DXF representa alinhada como DIMENSION linear rotacionada.
        (doc.add_aligned_dimension((0, 0), (3, 4), (0, 2)), "linear", 5.0),
        (
            doc.add_angular_dimension((10, 0), (15, 0), (10, 5), (14, 4)),
            "angular (3 pontos)",
            90.0,
        ),
        (doc.add_radius_dimension((20, 0), 5, (27, 2)), "raio", 5.0),
        (doc.add_diameter_dimension((35, 0), 5, (42, 2)), "diametro", 10.0),
        (doc.add_ordinate_dimension((50, 7), (50, 12)), "ordenada", 50.0),
        (
            doc.add_arc_length_dimension((65, 0), (70, 0), (65, 5), (70, 5)),
            "comprimento de arco",
            5 * 1.5707963267948966,
        ),
    ]

    for entity, kind, expected in made:
        assert dimension_kind(entity) == kind
        assert dimension_measurement(entity) == pytest.approx(expected)
        assert entity_polylines(entity)
    arc, kind, _ = made[-1]
    assert arc.dxftype() == "ARC_DIMENSION"
    assert dimension_kind(arc) == kind
    assert dimension_measurement(arc) == pytest.approx(5 * 1.5707963267948966)


def test_dimension_roundtrip_keeps_native_entity_and_style(tmp_path):
    doc = Document.new()
    doc.add_linear_dimension((500000, 7400000), (500025, 7400000), (500000, 7400005))
    doc.add_radius_dimension((500050, 7400000), 8, (500060, 7400002))
    target = tmp_path / "cotas.dxf"
    doc.save(target)

    reopened = Document.open(target)
    dimensions = list(reopened.entities())
    assert [e.dxftype() for e in dimensions] == ["DIMENSION", "DIMENSION"]
    assert [dimension_measurement(e) for e in dimensions] == pytest.approx([25, 8])
    assert all(e.dxf.dimstyle == DIMSTYLE_NAME for e in dimensions)
    assert all(list(dimension_primitives(e)) for e in dimensions)


def test_dimension_grip_edit_rerenders_and_undo_is_exact():
    doc = Document.new()
    entity = doc.add_linear_dimension((0, 0), (10, 0), (0, 4))
    before = tuple(entity.dxf.defpoint2)
    grip = next(g for g in entity_grips(entity) if g.kind == VERTEX)

    with doc.editing([entity], "editar cota"):
        assert drag_grip(entity, grip, Vec2(-2, 0))
    assert dimension_measurement(entity) == pytest.approx(12)
    assert entity_polylines(entity)

    assert doc.undo.undo()
    assert tuple(entity.dxf.defpoint2) == before
    assert dimension_measurement(entity) == pytest.approx(10)
    assert doc.undo.redo()
    assert dimension_measurement(entity) == pytest.approx(12)


def test_dimension_text_grip_survives_regen_style_update_and_undo():
    doc = Document.new()
    entity = doc.add_linear_dimension((0, 0), (10, 0), (0, 4))
    original = Vec2.of(entity.dxf.text_midpoint)
    grip = next(g for g in entity_grips(entity) if g.kind == DIM_TEXT)
    target = Vec2(7, 8)

    with doc.editing([entity], "mover texto da cota"):
        assert drag_grip(entity, grip, target)
    assert Vec2.of(entity.dxf.text_midpoint) == target

    doc.update_dimension_style(replace(doc.dimension_style_settings(), precision=4))
    assert Vec2.of(entity.dxf.text_midpoint) == target
    # O update de estilo nao entra no undo; o proximo item e o arraste.
    assert doc.undo.undo()
    assert Vec2.of(entity.dxf.text_midpoint) == original


def test_dimension_participates_in_snap_and_picking():
    doc = Document.new()
    entity = doc.add_linear_dimension((0, 0), (10, 0), (0, 4))
    points = entity_snap_points(entity)
    assert ("end", Vec2(0, 0)) in points
    assert ("end", Vec2(10, 0)) in points
    assert pick_at(doc, Vec2(5, 4), 0.2) is entity


def test_dimension_style_update_is_persisted_and_rerenders_existing():
    doc = Document.new()
    entity = doc.add_linear_dimension((0, 0), (10, 0), (0, 4))
    settings = doc.dimension_style_settings()
    changed = replace(
        settings,
        text_height=0.5,
        arrow_size=0.4,
        precision=3,
        decimal_separator=".",
        prefix="~",
        suffix=" m",
    )
    doc.update_dimension_style(changed)

    current = doc.dimension_style_settings()
    assert current == changed
    assert doc.dimension_style().dxf.dimpost == "~<> m"
    assert list(dimension_primitives(entity))


def test_dimension_commands_are_registered_with_expected_tools():
    ctx = AppContext(Document.new())
    expected = {
        "DIMLINEAR": LinearDimensionTool,
        "DIMALIGNED": AlignedDimensionTool,
        "DIMANGULAR": AngularDimensionTool,
        "DIMRADIUS": RadiusDimensionTool,
        "DIMDIAMETER": DiameterDimensionTool,
        "DIMARC": ArcLengthDimensionTool,
        "DIMORDINATE": OrdinateDimensionTool,
    }
    for name, tool_type in expected.items():
        definition = ctx.registry.resolve(name)
        assert definition is not None
        assert isinstance(definition.handler(ctx), tool_type)
    assert ctx.registry.resolve("DLI").name == "DIMLINEAR"
    assert ctx.registry.resolve("DAL").name == "DIMALIGNED"


def test_linear_command_collects_three_points_and_finishes():
    ctx = AppContext(Document.new())
    assert ctx.run_command("DIMLINEAR")
    tool = ctx.tool
    tool.on_click(Vec2(0, 0))
    tool.on_click(Vec2(10, 0))
    tool.on_click(Vec2(5, 4))

    dimensions = list(ctx.doc.entities())
    assert len(dimensions) == 1
    assert dimensions[0].dxftype() == "DIMENSION"
    assert dimension_measurement(dimensions[0]) == pytest.approx(10)
    assert ctx.tool.is_idle
