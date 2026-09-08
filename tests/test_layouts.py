from engecad.context import AppContext
from engecad.core.document import Document
from engecad.core.geometry import Vec2
from engecad.io.dxf_io import open_document, save_document
from engecad.render.displaylist import DisplayList
from engecad.render.viewport import Viewport


def test_document_switches_between_model_and_paper_layouts():
    doc = Document.new()
    doc.add_line((0, 0), (10, 0))
    paper = doc.drawing.layouts.new("Folha A1")
    paper.add_line((100, 100), (110, 100))

    assert doc.layout_names() == ["Model", "Layout1", "Folha A1"]
    assert len(doc) == 1

    assert doc.set_layout("folha a1")
    assert doc.current_layout == "Folha A1"
    assert len(doc) == 1
    assert next(doc.entities()).dxf.start.x == 100

    assert doc.set_layout("MODEL")
    assert len(doc) == 1
    assert next(doc.entities()).dxf.start.x == 0


def test_current_layout_round_trips_through_sidecar(tmp_path):
    ctx = AppContext(Document.new())
    ctx.doc.drawing.layouts.new("Folha A1")
    ctx.set_layout("Folha A1")
    ctx.doc.add_line((1, 2), (3, 4))

    path = tmp_path / "folhas.dxf"
    save_document(ctx, path)

    reopened = AppContext(Document.new())
    open_document(reopened, path)
    assert reopened.doc.current_layout == "Folha A1"
    assert len(reopened.doc) == 1


def test_layout_switch_reuses_each_spaces_spatial_index():
    doc = Document.new()
    doc.add_line((0, 0), (10, 0))
    paper = doc.drawing.layouts.get("Layout1")
    paper.add_line((100, 100), (110, 100))

    model_index = doc.index
    assert doc.set_layout("Layout1")
    paper_index = doc.index
    assert paper_index is not model_index

    assert doc.set_layout("Model")
    assert doc.index is model_index
    assert doc.set_layout("Layout1")
    assert doc.index is paper_index


def test_model_viewport_can_freeze_a_layer_without_hiding_it_globally():
    doc = Document.new()
    doc.ensure_layer("DETALHE", color=2)
    doc.add_line((0, 0), (10, 0), layer="DETALHE")
    viewport = doc.drawing.layouts.get("Layout1").add_viewport(
        center=(5, 5), size=(100, 100), view_center_point=(5, 0), view_height=100
    )
    handle = str(viewport.dxf.handle)
    doc.layer_manager.set_viewport_override(handle, "DETALHE", frozen=True)

    display = DisplayList(doc, layout="Model")
    display.sync()
    view = Viewport(100, 100)
    view.center = Vec2(5, 0)
    view.set_scale(1.0)

    steps, markers = display.plan(view, viewport_handle=handle)
    assert steps == []
    assert markers == []
    assert doc.layer_is_visible("DETALHE")
