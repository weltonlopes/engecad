from __future__ import annotations

import math

from ezdxf.math import Matrix44

from engecad.core.document import Document
from engecad.core.geometry import Vec2
from engecad.core.hatches import (
    HatchSettings,
    apply_hatch_settings,
    available_patterns,
    detach_hatch,
    hatch_area,
    hatch_association_status,
    hatch_metadata,
    read_hatch_settings,
)


def rectangle(doc, x0=0, y0=0, x1=10, y1=5):
    return doc.add_lwpolyline(
        [Vec2(x0, y0), Vec2(x1, y0), Vec2(x1, y1), Vec2(x0, y1)], closed=True
    )


def test_native_pattern_hatch_is_associative_and_has_area():
    doc = Document.new()
    boundary = rectangle(doc)
    hatch = doc.add_hatch(
        [boundary], settings=HatchSettings(pattern="ANSI31", scale=0.5, angle=30, color=3)
    )

    assert hatch.dxftype() == "HATCH"
    assert hatch.dxf.pattern_name == "ANSI31"
    assert hatch.dxf.associative == 1
    assert hatch_association_status(doc, hatch) == (1, 1)
    assert math.isclose(hatch_area(hatch), 50.0)
    assert list(hatch.render_pattern_lines())
    assert hatch_metadata(hatch)["mode"] == "entities"


def test_associative_hatch_regenerates_and_undoes_with_boundary():
    doc = Document.new()
    boundary = rectangle(doc)
    hatch = doc.add_hatch([boundary], settings=HatchSettings(pattern="SOLID"))

    doc.transform([boundary], Matrix44.scale(2, 2, 1), "escalar limite")
    assert math.isclose(hatch_area(hatch), 200.0)

    assert doc.undo.undo()
    assert math.isclose(hatch_area(hatch), 50.0)
    assert doc.undo.redo()
    assert math.isclose(hatch_area(hatch), 200.0)


def test_selected_island_is_subtracted_from_area():
    doc = Document.new()
    outer = rectangle(doc, 0, 0, 10, 10)
    island = rectangle(doc, 2, 2, 4, 5)
    hatch = doc.add_hatch([outer, island], settings=HatchSettings(pattern="SOLID"))

    assert len(hatch.paths) == 2
    assert math.isclose(hatch_area(hatch), 94.0)
    assert hatch_association_status(doc, hatch) == (2, 2)


def test_seed_point_finds_region_and_persists_seed():
    doc = Document.new()
    lines = [
        doc.add_line((0, 0), (10, 0)),
        doc.add_line((10, 0), (10, 5)),
        doc.add_line((10, 5), (0, 5)),
        doc.add_line((0, 5), (0, 0)),
    ]
    hatch = doc.add_hatch(seed=(3, 2), settings=HatchSettings(pattern="ANSI32"))

    assert math.isclose(hatch_area(hatch), 50.0)
    assert hatch_metadata(hatch) == {"version": 1, "mode": "seed", "seed": [3.0, 2.0]}
    assert hatch_association_status(doc, hatch) == (4, 4)

    with doc.editing([lines[1]], "mover lado"):
        lines[1].dxf.start = (12, 0)
        lines[1].dxf.end = (12, 5)
    # O contorno ficou aberto: a hachura conserva o ultimo limite valido.
    assert math.isclose(hatch_area(hatch), 50.0)


def test_hatch_settings_are_editable_and_undoable():
    doc = Document.new()
    hatch = doc.add_hatch([rectangle(doc)], settings=HatchSettings(pattern="SOLID"))
    with doc.editing([hatch], "editar hachura"):
        apply_hatch_settings(
            hatch,
            HatchSettings(pattern="ANSI37", scale=2.5, angle=42, transparency=0.35, color=5),
        )
    settings = read_hatch_settings(hatch)
    assert settings.pattern == "ANSI37"
    assert math.isclose(settings.scale, 2.5)
    assert math.isclose(settings.transparency, 0.35, abs_tol=0.005)

    assert doc.undo.undo()
    assert read_hatch_settings(hatch).solid


def test_hatch_survives_roundtrip_and_audit(tmp_path):
    path = tmp_path / "hatches.dxf"
    doc = Document.new()
    doc.add_hatch([rectangle(doc)], settings=HatchSettings(pattern="ANSI31", scale=0.25))
    assert not doc.drawing.audit().has_errors
    doc.save(path)

    reopened = Document.open(path)
    hatch = next(e for e in reopened.entities() if e.dxftype() == "HATCH")
    assert hatch_association_status(reopened, hatch) == (1, 1)
    assert math.isclose(hatch_area(hatch), 50.0)
    assert not reopened.drawing.audit().has_errors


def test_standard_pattern_catalog_is_available():
    patterns = available_patterns()
    assert "SOLID" in patterns
    assert "ANSI31" in patterns
    assert "AR-CONC" in patterns


def test_copy_remaps_association_to_copied_boundary():
    doc = Document.new()
    boundary = rectangle(doc)
    hatch = doc.add_hatch([boundary], settings=HatchSettings(pattern="SOLID"))
    copied_boundary, copied_hatch = doc.copy_entities(
        [boundary, hatch], Matrix44.translate(20, 0, 0)
    )

    copied_handle = str(copied_boundary.dxf.handle)
    assert hatch_association_status(doc, copied_hatch) == (1, 1)
    assert copied_hatch.paths[0].source_boundary_objects == [copied_handle]
    assert math.isclose(hatch_area(copied_hatch), 50.0)


def test_detach_and_undo_restore_native_association():
    doc = Document.new()
    boundary = rectangle(doc)
    hatch = doc.add_hatch([boundary], settings=HatchSettings(pattern="SOLID"))
    with doc.editing([hatch], "desassociar"):
        detach_hatch(hatch)
    assert hatch_association_status(doc, hatch) == (0, 0)
    assert hatch.dxf.associative == 0

    assert doc.undo.undo()
    assert hatch_association_status(doc, hatch) == (1, 1)
    assert hatch.dxf.associative == 1
    assert hatch.dxf.handle in boundary.get_reactors()
