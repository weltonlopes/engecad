import pytest

from engecad.core.document import Document
from engecad.core.geometry import Vec2

E, N = 500000.0, 7400000.0


@pytest.fixture
def doc():
    return Document.new("EPSG:31982")


def test_new_document_has_default_layers(doc):
    names = doc.layer_names()
    for expected in ("LIMITE", "DIVISA", "EDIFICACAO", "TEXTO"):
        assert expected in names


def test_new_document_is_metric(doc):
    assert doc.drawing.header["$INSUNITS"] == 6  # metros


def test_new_document_starts_unmodified(doc):
    assert not doc.modified


def test_add_line_marks_modified_and_indexes(doc):
    doc.add_line((E, N), (E + 10, N))
    assert doc.modified
    assert len(doc) == 1
    assert len(doc.query_point(Vec2(E, N), 1.0)) == 1


def test_undo_removes_and_redo_restores(doc):
    doc.add_line((E, N), (E + 10, N))
    assert doc.undo.undo()
    assert len(doc) == 0
    assert len(doc.query_point(Vec2(E, N), 1.0)) == 0
    assert doc.undo.redo()
    assert len(doc) == 1
    assert len(doc.query_point(Vec2(E, N), 1.0)) == 1


def test_undo_redo_many_times_is_stable(doc):
    doc.add_line((E, N), (E + 10, N))
    doc.add_lwpolyline([(E, N), (E + 5, N), (E + 5, N + 5)], closed=True)
    for _ in range(10):
        doc.undo.undo()
        doc.undo.undo()
        assert len(doc) == 0
        doc.undo.redo()
        doc.undo.redo()
        assert len(doc) == 2


def test_macro_collapses_into_single_undo(doc):
    """Um script no console tem de desfazer num Ctrl+Z so."""
    doc.undo.begin_macro("script")
    for i in range(5):
        doc.add_line((E + i, N), (E + i, N + 5))
    doc.undo.end_macro()
    assert len(doc) == 5
    doc.undo.undo()
    assert len(doc) == 0
    doc.undo.redo()
    assert len(doc) == 5


def test_abort_macro_leaves_nothing_behind(doc):
    doc.undo.begin_macro("script que falhou")
    doc.add_line((E, N), (E + 1, N))
    doc.undo.abort_macro()
    assert len(doc) == 0
    assert not doc.undo.can_undo


def test_delete_is_undoable(doc):
    e = doc.add_line((E, N), (E + 10, N))
    doc.delete([e])
    assert len(doc) == 0
    doc.undo.undo()
    assert len(doc) == 1


def test_extents_covers_all_entities(doc):
    doc.add_line((E, N), (E + 100, N))
    doc.add_line((E, N), (E, N + 50))
    b = doc.extents()
    assert b.minx == pytest.approx(E)
    assert b.maxx == pytest.approx(E + 100)
    assert b.maxy == pytest.approx(N + 50)


def test_entities_go_to_current_layer(doc):
    doc.current_layer = "LIMITE"
    e = doc.add_line((E, N), (E + 1, N))
    assert e.dxf.layer == "LIMITE"


def test_explicit_layer_overrides_current(doc):
    doc.current_layer = "LIMITE"
    e = doc.add_line((E, N), (E + 1, N), layer="VIA")
    assert e.dxf.layer == "VIA"


def test_layer_visibility_toggle(doc):
    assert doc.layer_is_visible("LIMITE")
    doc.set_layer_visible("LIMITE", False)
    assert not doc.layer_is_visible("LIMITE")
    doc.set_layer_visible("LIMITE", True)
    assert doc.layer_is_visible("LIMITE")


def test_dxf_roundtrip_preserves_geometry(doc, tmp_path):
    """Criterio de sucesso da v0.1: o DXF salvo tem de reabrir identico."""
    doc.add_line((E, N), (E + 123.456, N + 78.9))
    doc.add_lwpolyline([(E, N), (E + 10, N), (E + 10, N + 10)], closed=True, layer="LIMITE")
    path = tmp_path / "planta.dxf"
    doc.save(path)
    assert not doc.modified

    back = Document.open(path)
    assert len(back) == 2
    line = next(e for e in back.entities() if e.dxftype() == "LINE")
    assert line.dxf.start.x == pytest.approx(E)
    assert line.dxf.end.x == pytest.approx(E + 123.456)
    assert line.dxf.end.y == pytest.approx(N + 78.9)

    poly = next(e for e in back.entities() if e.dxftype() == "LWPOLYLINE")
    assert poly.closed
    assert poly.dxf.layer == "LIMITE"
    assert len(poly) == 3


def test_saved_document_reopens_with_working_index(doc, tmp_path):
    doc.add_line((E, N), (E + 10, N))
    path = tmp_path / "a.dxf"
    doc.save(path)
    back = Document.open(path)
    assert len(back.query_point(Vec2(E + 5, N), 1.0)) == 1
