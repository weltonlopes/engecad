"""Editor dos campos e do formato de carimbos."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.titleblocks import FIELD_LABELS, PAPER_MM, TitleBlockConfig


class TitleBlockDialog(QDialog):
    def __init__(self, config: TitleBlockConfig | None = None, parent=None, editing=False):
        super().__init__(parent)
        self.setWindowTitle("Editar carimbo" if editing else "Inserir carimbo")
        current = config or TitleBlockConfig()
        self.paper = QComboBox()
        self.paper.addItems(PAPER_MM)
        self.paper.setCurrentText(current.paper)
        self.landscape = QCheckBox("Paisagem")
        self.landscape.setChecked(current.landscape)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(1.0, 1e9)
        self.scale.setDecimals(3)
        self.scale.setValue(current.scale_denominator)
        self.fields: dict[str, QLineEdit] = {}
        form = QFormLayout()
        if not editing:
            form.addRow("Papel:", self.paper)
            form.addRow("Orientacao:", self.landscape)
            form.addRow("Escala da planta (1:n):", self.scale)
        for tag, label in FIELD_LABELS.items():
            edit = QLineEdit(str(current.values.get(tag, "")))
            self.fields[tag] = edit
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
        self.resize(560, 650)

    def config(self) -> TitleBlockConfig:
        return TitleBlockConfig(
            paper=self.paper.currentText(),
            landscape=self.landscape.isChecked(),
            scale_denominator=self.scale.value(),
            values={tag: edit.text() for tag, edit in self.fields.items()},
        )
