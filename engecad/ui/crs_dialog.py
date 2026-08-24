"""Escolha do sistema de coordenadas do projeto."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)

from ..core.crs import COMMON_CRS, ProjectCRS


class CrsDialog(QDialog):
    def __init__(self, current: ProjectCRS | None = None, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sistema de coordenadas do projeto")
        self.setMinimumWidth(520)

        self.combo = QComboBox(self)
        for srid, label in COMMON_CRS:
            self.combo.addItem(f"{srid}  -  {label}", srid)
        self.combo.addItem("Outro (digitar EPSG ou WKT)...", "")

        self.custom = QLineEdit(self)
        self.custom.setPlaceholderText("ex.: EPSG:31982, +proj=utm +zone=22 +south ..., ou WKT")

        self.feedback = QLabel(self)
        self.feedback.setWordWrap(True)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self._ok = buttons.button(QDialogButtonBox.Ok)

        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Sistemas mais usados no Brasil:", self))
        lay.addWidget(self.combo)
        lay.addWidget(QLabel("Ou informe manualmente:", self))
        lay.addWidget(self.custom)
        lay.addWidget(self.feedback)
        lay.addWidget(buttons)

        self.combo.currentIndexChanged.connect(self._on_combo)
        self.custom.textChanged.connect(self._validate)

        if current is not None:
            idx = self.combo.findData(current.srid)
            if idx >= 0:
                self.combo.setCurrentIndex(idx)
            else:
                self.combo.setCurrentIndex(self.combo.count() - 1)
                self.custom.setText(current.srid)
        self._on_combo()

    def _on_combo(self) -> None:
        data = self.combo.currentData()
        self.custom.setEnabled(not data)
        if data:
            self.custom.setText(data)
        self._validate()

    def _validate(self) -> None:
        text = self.custom.text().strip()
        if not text:
            self.feedback.setText("Informe um CRS.")
            self._ok.setEnabled(False)
            return
        if not ProjectCRS.is_valid(text):
            self.feedback.setText("CRS nao reconhecido pelo PROJ.")
            self._ok.setEnabled(False)
            return
        crs = ProjectCRS(text)
        kind = "projetado" if crs.is_projected else "geografico"
        warn = ""
        if not crs.is_projected:
            warn = "\nAtencao: CRS geografico (graus). Distancias e areas nao sairao em metros."
        self.feedback.setText(f"{crs.name}  -  {kind}, unidade {crs.unit_name}{warn}")
        self._ok.setEnabled(True)

    def selected(self) -> ProjectCRS:
        return ProjectCRS(self.custom.text().strip())

    @staticmethod
    def ask(parent=None, current: ProjectCRS | None = None) -> ProjectCRS | None:
        dlg = CrsDialog(current, parent)
        if dlg.exec() == QDialog.Accepted:
            return dlg.selected()
        return None
