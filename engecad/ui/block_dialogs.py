"""Dialogos de blocos, atributos, simbolos e dados do projeto."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QInputDialog,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.blocks import (
    DynamicParameters,
    InsertOptions,
    attribute_definitions,
    block_attribute_values,
    block_names,
    dynamic_metadata,
    dynamic_parameters,
)
from ..core.symbols import SYMBOLS, symbol_spec
from ..core.titleblocks import FIELD_LABELS


def ask_block_definition(parent=None) -> tuple[str, str] | None:
    name, ok = QInputDialog.getText(parent, "Criar bloco", "Nome do bloco:")
    if not ok or not name.strip():
        return None
    description, ok = QInputDialog.getText(parent, "Criar bloco", "Descricao:")
    if not ok:
        return None
    return name.strip(), description.strip()


@dataclass(slots=True)
class BlockInsertConfig:
    source_type: str
    source_name: str
    options: InsertOptions
    visibility: str = ""


class BlockInsertDialog(QDialog):
    def __init__(self, doc, parent=None, symbols_only: bool = False):
        super().__init__(parent)
        self.doc = doc
        self.symbols_only = symbols_only
        self.setWindowTitle("Biblioteca de simbolos" if symbols_only else "Inserir bloco")

        self.source = QComboBox()
        if not symbols_only:
            for name in block_names(doc):
                self.source.addItem(f"Bloco / {name}", ("block", name))
        for spec in SYMBOLS:
            self.source.addItem(
                f"{spec.category} / {spec.label}", ("symbol", spec.key)
            )

        self.scale_x = _spin(0.000001, 1e9, 1.0, 6)
        self.scale_y = _spin(0.000001, 1e9, 1.0, 6)
        self.rotation = _spin(-360.0, 360.0, 0.0, 3)
        self.annotative = QCheckBox("Ajustar ao tamanho no papel")
        self.paper_size = _spin(0.1, 1000.0, 5.0, 2)
        self.paper_size.setSuffix(" mm")
        self.dynamic = QCheckBox("Habilitar escala, inversao e visibilidade dinamicas")
        self.visibility = QComboBox()
        self.visibility.setEnabled(False)

        form = QFormLayout()
        form.addRow("Bloco / simbolo:", self.source)
        form.addRow("Escala X:", self.scale_x)
        form.addRow("Escala Y:", self.scale_y)
        form.addRow("Rotacao:", self.rotation)
        form.addRow("Anotativo:", self.annotative)
        form.addRow("Tamanho no papel:", self.paper_size)
        form.addRow("Dinamico:", self.dynamic)
        form.addRow("Visibilidade:", self.visibility)

        self.attribute_box = QGroupBox("Atributos")
        self.attribute_form = QFormLayout(self.attribute_box)
        self.attribute_edits: dict[str, QLineEdit] = {}
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.attribute_box)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(scroll, 1)
        layout.addWidget(buttons)
        self.source.currentIndexChanged.connect(self._source_changed)
        self._source_changed()
        self.resize(570, 580)

    def _clear_attributes(self) -> None:
        while self.attribute_form.rowCount():
            self.attribute_form.removeRow(0)
        self.attribute_edits.clear()

    def _source_changed(self) -> None:
        self._clear_attributes()
        data = self.source.currentData()
        if not data:
            return
        source_type, name = data
        if source_type == "symbol":
            spec = symbol_spec(name)
            definitions = spec.attributes
            self.annotative.setChecked(True)
            self.paper_size.setValue(spec.paper_size_mm)
            self.dynamic.setChecked(True)
            self.visibility.clear()
            self.visibility.addItems(spec.visibility_states)
            self.visibility.setEnabled(bool(spec.visibility_states))
        else:
            definitions = attribute_definitions(self.doc, name)
            self.annotative.setChecked(False)
            self.dynamic.setChecked(False)
            self.visibility.clear()
            self.visibility.setEnabled(False)
        for definition in definitions:
            edit = QLineEdit(definition.default)
            self.attribute_edits[definition.tag] = edit
            self.attribute_form.addRow(f"{definition.prompt or definition.tag}:", edit)
        self.attribute_box.setVisible(bool(definitions))

    def config(self) -> BlockInsertConfig:
        source_type, name = self.source.currentData()
        options = InsertOptions(
            scale_x=self.scale_x.value(),
            scale_y=self.scale_y.value(),
            rotation=self.rotation.value(),
            attributes={tag: edit.text() for tag, edit in self.attribute_edits.items()},
            annotative=self.annotative.isChecked(),
            paper_size_mm=self.paper_size.value(),
            annotation_scale=self.doc.annotation_scale,
            dynamic=self.dynamic.isChecked(),
        )
        return BlockInsertConfig(
            source_type, name, options, self.visibility.currentText()
        )


class AttributeEditorDialog(QDialog):
    def __init__(self, insert, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Atributos de {insert.dxf.name}")
        values = block_attribute_values(insert)
        definitions = {
            definition.tag: definition
            for definition in attribute_definitions(_DocumentAdapter(insert.doc), insert.dxf.name)
        }
        form = QFormLayout()
        self.edits: dict[str, QLineEdit] = {}
        for tag, value in values.items():
            definition = definitions.get(tag)
            label = definition.prompt if definition and definition.prompt else tag
            edit = QLineEdit(value)
            self.edits[tag] = edit
            form.addRow(f"{label} ({tag}):", edit)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def values(self) -> dict[str, str]:
        return {tag: edit.text() for tag, edit in self.edits.items()}


class DynamicBlockDialog(QDialog):
    def __init__(self, insert, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Parametros dinamicos simplificados")
        current = dynamic_parameters(insert)
        if current is None:
            base = abs(float(insert.dxf.get("xscale", 1.0) or 1.0))
            yscale = float(insert.dxf.get("yscale", 1.0) or 1.0)
            current = DynamicParameters(
                stretch_x=1.0,
                stretch_y=abs(yscale) / max(base, 1e-9),
                rotation=float(insert.dxf.get("rotation", 0.0) or 0.0),
                flip_x=float(insert.dxf.get("xscale", 1.0) or 1.0) < 0,
                flip_y=yscale < 0,
            )
        data = dynamic_metadata(insert)
        self.stretch_x = _spin(0.000001, 1e6, current.stretch_x, 6)
        self.stretch_y = _spin(0.000001, 1e6, current.stretch_y, 6)
        self.rotation = _spin(-360.0, 360.0, current.rotation, 3)
        self.flip_x = QCheckBox("Inverter horizontalmente")
        self.flip_x.setChecked(current.flip_x)
        self.flip_y = QCheckBox("Inverter verticalmente")
        self.flip_y.setChecked(current.flip_y)
        self.visibility = QComboBox()
        variants = list(data.get("variants", {}))
        self.visibility.addItems(variants)
        if current.visibility:
            self.visibility.setCurrentText(current.visibility)
        self.visibility.setEnabled(bool(variants))
        form = QFormLayout()
        form.addRow("Fator de largura:", self.stretch_x)
        form.addRow("Fator de altura:", self.stretch_y)
        form.addRow("Rotacao:", self.rotation)
        form.addRow("Espelho X:", self.flip_x)
        form.addRow("Espelho Y:", self.flip_y)
        form.addRow("Estado visivel:", self.visibility)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def parameters(self) -> DynamicParameters:
        return DynamicParameters(
            stretch_x=self.stretch_x.value(),
            stretch_y=self.stretch_y.value(),
            rotation=self.rotation.value(),
            flip_x=self.flip_x.isChecked(),
            flip_y=self.flip_y.isChecked(),
            visibility=self.visibility.currentText(),
        )


class ProjectAttributesDialog(QDialog):
    def __init__(self, values: dict[str, str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Dados do projeto")
        self.edits: dict[str, QLineEdit] = {}
        form = QFormLayout()
        for tag, label in FIELD_LABELS.items():
            if tag in ("ESCALA", "DATA"):
                continue
            edit = QLineEdit(str(values.get(tag, "")))
            self.edits[tag] = edit
            form.addRow(f"{label}:", edit)
        body = QWidget()
        body.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addWidget(scroll)
        layout.addWidget(buttons)
        self.resize(540, 550)

    def values(self) -> dict[str, str]:
        return {tag: edit.text() for tag, edit in self.edits.items()}


class _DocumentAdapter:
    """Adaptador minimo para attribute_definitions a partir do ezdxf.Drawing."""

    def __init__(self, drawing):
        self.drawing = drawing


def _spin(minimum, maximum, value, decimals) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(minimum, maximum)
    spin.setDecimals(decimals)
    spin.setValue(value)
    return spin
