import pytest

from engecad.core.document import Document
from engecad.core.layers import LayerFilter, LayerManager


def test_layer_properties_keep_off_and_frozen_as_distinct_states():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("ELETRICA", color=2)

    manager.update(
        "ELETRICA",
        on=False,
        frozen=True,
        locked=True,
        color=4,
        lineweight=25,
        transparency=35,
        plot=False,
        plot_style="Monochrome",
        description="Instalações elétricas",
    )

    props = manager.properties("ELETRICA")
    assert props.on is False
    assert props.frozen is True
    assert props.locked is True
    assert props.color == 4
    assert props.lineweight == 25
    assert props.transparency == 35
    assert props.plot is False
    assert props.plot_style == "Monochrome"
    assert props.description == "Instalações elétricas"
    assert doc.layer_is_visible("ELETRICA") is False


def test_current_layer_is_turned_on_and_thawed_and_cannot_be_frozen():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("ATUAL")
    manager.update("ATUAL", on=False, frozen=True)

    manager.set_current("ATUAL")

    assert doc.current_layer == "ATUAL"
    assert manager.properties("ATUAL").on is True
    assert manager.properties("ATUAL").frozen is False
    with pytest.raises(ValueError, match="corrente"):
        manager.update("ATUAL", frozen=True)


def test_rename_updates_entities_filters_and_current_layer():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("ANTIGA")
    doc.add_line((0, 0), (1, 1), layer="ANTIGA")
    manager.add_filter(LayerFilter("Grupo", "group", members=["ANTIGA"]))
    manager.set_current("ANTIGA")

    manager.rename("ANTIGA", "NOVA")

    assert doc.current_layer == "NOVA"
    assert "NOVA" in doc.layer_names()
    assert next(doc.entities()).dxf.layer == "NOVA"
    assert manager.filters["Grupo"].members == ["NOVA"]


def test_delete_rejects_protected_current_xref_and_nonempty_layers():
    doc = Document.new()
    manager = doc.layer_manager
    with pytest.raises(ValueError, match="protegida"):
        manager.delete("0")

    manager.create("COM_OBJETO")
    doc.add_line((0, 0), (1, 1), layer="COM_OBJETO")
    with pytest.raises(ValueError, match="objetos"):
        manager.delete("COM_OBJETO")

    manager.create("VAZIA")
    manager.set_current("VAZIA")
    with pytest.raises(ValueError, match="corrente"):
        manager.delete("VAZIA")

    doc.drawing.layers.add("ARQ|PAREDES")
    with pytest.raises(ValueError, match="Xref"):
        manager.delete("ARQ|PAREDES")


def test_property_and_group_filters_search_sort_and_reconciliation():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("ELETRICA", color=2)
    manager.create("ESTRUTURA", color=3)
    manager.update("ELETRICA", description="Tomadas e iluminação", locked=True)
    manager.add_filter(LayerFilter("Bloqueadas", criteria={"locked": True}))
    manager.add_filter(LayerFilter("Disciplinas", "group", members=["ELETRICA", "ESTRUTURA"]))

    assert [row.name for row in manager.all(filter_name="Bloqueadas")] == ["ELETRICA"]
    assert [row.name for row in manager.all(search="iluminação")] == ["ELETRICA"]
    assert {row.name for row in manager.all(filter_name="Disciplinas")} == {
        "ELETRICA",
        "ESTRUTURA",
    }
    assert manager.properties("ELETRICA").reconciled is False
    manager.reconcile(["ELETRICA"])
    assert manager.properties("ELETRICA").reconciled is True


def test_layer_state_round_trip_preserves_all_standard_properties():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("IMPRESSAO")
    manager.update(
        "IMPRESSAO",
        on=False,
        frozen=True,
        locked=True,
        color=1,
        lineweight=50,
        transparency=20,
        plot=False,
        description="Configuração de saída",
    )
    manager.save_state("Plotagem", "Estado para impressão")
    manager.update(
        "IMPRESSAO",
        on=True,
        frozen=False,
        locked=False,
        color=6,
        lineweight=5,
        transparency=0,
        plot=True,
        description="Alterada",
    )

    manager.restore_state("Plotagem")

    props = manager.properties("IMPRESSAO")
    assert props.on is False
    assert props.frozen is True
    assert props.locked is True
    assert props.color == 1
    assert props.lineweight == 50
    assert props.transparency == 20
    assert props.plot is False
    assert props.description == "Configuração de saída"


def test_viewport_overrides_and_metadata_round_trip():
    doc = Document.new()
    manager = doc.layer_manager
    manager.create("DETALHE")
    paper = doc.drawing.layouts.new("Folha A1")
    viewport = paper.add_viewport(
        center=(5, 5), size=(10, 10), view_center_point=(0, 0), view_height=10
    )
    handle = str(viewport.dxf.handle)

    manager.set_viewport_override(
        handle,
        "DETALHE",
        color=5,
        linetype="Continuous",
        lineweight=70,
        transparency=40,
        plot_style="VP mono",
        frozen=True,
    )

    props = manager.properties("DETALHE", handle)
    assert props.color == 5
    assert props.lineweight == 70
    assert props.transparency == 40
    assert props.plot_style == "VP mono"
    assert props.frozen is True
    assert viewport.is_frozen("DETALHE") is True

    clone = Document.new()
    clone.layer_manager.create("DETALHE")
    clone.layer_manager.import_metadata(manager.export_metadata())
    assert clone.layer_manager.viewport_frozen[handle] == {"detalhe"}
    assert clone.layer_manager.viewport_plot_styles[handle]["detalhe"] == "VP mono"


def test_xref_layers_are_identified_by_dependent_name():
    doc = Document.new()
    doc.drawing.layers.add("ARQUITETURA|PAREDES")
    doc.layer_manager.detect_new_layers()

    props = doc.layer_manager.properties("ARQUITETURA|PAREDES")
    assert props.xref is True
    assert props.reconciled is False
    with pytest.raises(ValueError, match="Xref"):
        doc.layer_manager.set_current("ARQUITETURA|PAREDES")


def test_metadata_import_ignores_invalid_entries():
    doc = Document.new()
    manager = LayerManager(doc)
    manager.import_metadata(
        {
            "filters": [{"bad": True}],
            "states": [{"bad": True}],
            "unreconciled": ["inexistente"],
        }
    )
    assert manager.filters == {}
    assert manager.states == {}
    assert manager.unreconciled == set()
