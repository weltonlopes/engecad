"""Invariantes da preparacao de geometria para a GPU."""

import ezdxf
import numpy as np

from engecad.core.document import Document
from engecad.core.geometry import BBox, Vec2
from engecad.render.gl_renderer import GpuGeometry, split_coordinates, split_scalar
from engecad.render.viewport import Viewport


def test_high_low_coordinates_preserve_utm_precision_near_view_center():
    points = np.array(
        [
            [674_000.000_7, 7_384_000.000_3],
            [674_000.003_1, 7_384_000.004_9],
        ]
    )
    packed = split_coordinates(points)
    cx, cy = 674_000.002, 7_384_000.002
    hx, lx = split_scalar(cx)
    hy, ly = split_scalar(cy)

    relative = (packed[:, :2] - np.array([hx, hy], np.float32)) + (
        packed[:, 2:] - np.array([lx, ly], np.float32)
    )

    np.testing.assert_allclose(relative, points - (cx, cy), atol=2e-6)


def test_gpu_geometry_turns_polylines_into_gl_lines_and_groups_styles():
    doc = Document.new()
    doc.ensure_layer("A", 1)
    doc.add_lwpolyline([(0, 0), (10, 0), (10, 10)], layer="A")
    doc.add_line((20, 0), (30, 0), layer="A")

    snapshot = GpuGeometry(doc).sync()

    assert snapshot.entities == 2
    assert snapshot.vertices == 6  # dois segmentos da polyline + uma linha
    assert sum(batch.vertex_count for batch in snapshot.batches) == 6
    assert {batch.layer for batch in snapshot.batches} == {"A"}
    assert {batch.aci for batch in snapshot.batches} == {1}


def test_gpu_geometry_rebuilds_after_document_edit():
    doc = Document.new()
    doc.add_line((0, 0), (1, 0))
    geometry = GpuGeometry(doc)
    first = geometry.sync()

    doc.add_line((0, 1), (1, 1))
    assert geometry.ensure_current()
    second = geometry.sync()

    assert second.revision > first.revision
    assert second.vertices == first.vertices + 2


def test_gpu_marker_culling_respects_view_and_layer_visibility():
    doc = Document.new()
    doc.ensure_layer("TXT", 2)
    doc.add_text("perto", (100, 100), layer="TXT")
    doc.add_text("longe", (10_000, 10_000), layer="TXT")
    geometry = GpuGeometry(doc)
    snapshot = geometry.sync()
    viewport = Viewport(400, 300)
    viewport.center = Vec2(100, 100)
    viewport.set_scale(1.0)

    visible = geometry.visible_markers(snapshot, viewport)
    assert [entity.dxf.text for entity in visible] == ["perto"]

    doc.set_layer_visible("TXT", False)
    assert geometry.visible_markers(snapshot, viewport) == []


def test_unsupported_large_proxy_becomes_screen_placeholder_not_bbox_cross():
    drawing = ezdxf.new()
    proxy = drawing.modelspace().new_entity("ACAD_PROXY_ENTITY", {})
    proxy.proxy_graphic = b"x" * 30_001
    doc = Document(drawing)
    box = BBox(0, 0, 10_000, 10_000)
    doc.index.build([(proxy.dxf.handle, box)])

    snapshot = GpuGeometry(doc).sync()

    assert snapshot.batches == ()
    assert len(snapshot.placeholders) == 1
    assert snapshot.placeholders[0].bbox == box
