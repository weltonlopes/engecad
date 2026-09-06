from engecad.context import AppContext
from engecad.core.document import Document
from engecad.io.dxf_io import open_document, save_document


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
