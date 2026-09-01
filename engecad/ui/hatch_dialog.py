"""Configuracao de preenchimento para HATCH."""

from __future__ import annotations

from pathlib import Path

from ezdxf.tools.pattern import parse
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QPushButton,
    QVBoxLayout,
)

from ..core.hatches import HatchSettings, available_patterns


class HatchDialog(QDialog):
    def __init__(self, settings: HatchSettings | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Hachura")
        self._custom: dict[str, list] = {}
        current = settings or HatchSettings()
        self.pattern = QComboBox()
        self.pattern.setEditable(True)
        self.pattern.addItems(available_patterns())
        self.pattern.setCurrentText(current.pattern)
        self.scale = QDoubleSpinBox()
        self.scale.setRange(0.000001, 1e9)
        self.scale.setDecimals(6)
        self.scale.setValue(current.scale)
        self.angle = QDoubleSpinBox()
        self.angle.setRange(-360.0, 360.0)
        self.angle.setValue(current.angle)
        self.transparency = QDoubleSpinBox()
        self.transparency.setRange(0.0, 90.0)
        self.transparency.setSuffix(" %")
        self.transparency.setValue(current.transparency * 100)
        self.color = QComboBox()
        for aci, label in (
            (7, "Por camada / branco"), (1, "Vermelho"), (2, "Amarelo"),
            (3, "Verde"), (4, "Ciano"), (5, "Azul"), (6, "Magenta"),
        ):
            self.color.addItem(label, aci)
        index = self.color.findData(current.color)
        self.color.setCurrentIndex(max(0, index))
        self.islands = QComboBox()
        self.islands.addItem("Normal (alternadas)", 0)
        self.islands.addItem("Somente contorno externo", 1)
        self.islands.addItem("Ignorar ilhas", 2)
        self.islands.setCurrentIndex(max(0, self.islands.findData(current.island_style)))

        form = QFormLayout()
        form.addRow("Padrao:", self.pattern)
        form.addRow("Escala:", self.scale)
        form.addRow("Angulo:", self.angle)
        form.addRow("Transparencia:", self.transparency)
        form.addRow("Cor:", self.color)
        form.addRow("Ilhas:", self.islands)
        load_button = QPushButton("Carregar padrao .PAT...")
        load_button.clicked.connect(self._load_pat)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(load_button)
        layout.addWidget(buttons)

    def _load_pat(self) -> None:
        filename, _ = QFileDialog.getOpenFileName(
            self, "Carregar padrao", "", "AutoCAD PAT (*.pat)"
        )
        if not filename:
            return
        definitions = parse(Path(filename).read_text(encoding="utf-8-sig"))
        self._custom.update(
            {str(name).upper(): definition for name, definition in definitions.items()}
        )
        for name in definitions:
            if self.pattern.findText(name.upper()) < 0:
                self.pattern.addItem(name.upper())
        if definitions:
            self.pattern.setCurrentText(next(iter(definitions)).upper())

    def settings(self) -> HatchSettings:
        name = self.pattern.currentText().strip().upper() or "SOLID"
        return HatchSettings(
            pattern=name,
            scale=self.scale.value(),
            angle=self.angle.value(),
            color=int(self.color.currentData()),
            transparency=self.transparency.value() / 100.0,
            island_style=int(self.islands.currentData()),
            custom_definition=self._custom.get(name),
        )
