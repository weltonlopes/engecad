"""Editor compacto do DIMSTYLE metrico usado pelas cotas."""

from __future__ import annotations

from dataclasses import replace

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLineEdit,
    QSpinBox,
    QVBoxLayout,
)


class DimensionStyleDialog(QDialog):
    def __init__(self, doc, parent=None):
        super().__init__(parent)
        self.doc = doc
        self.settings = doc.dimension_style_settings()
        self.setWindowTitle(f"Estilo de cotas — {doc.dimension_style_name}")

        self.text_height = self._distance(self.settings.text_height)
        self.arrow_size = self._distance(self.settings.arrow_size)
        self.scale = self._distance(self.settings.scale, maximum=1_000_000.0)
        self.ext_offset = self._distance(self.settings.extension_offset)
        self.ext_beyond = self._distance(self.settings.extension_beyond)
        self.text_gap = self._distance(self.settings.text_gap)
        self.precision = QSpinBox(self)
        self.precision.setRange(0, 8)
        self.precision.setValue(self.settings.precision)
        self.angular_precision = QSpinBox(self)
        self.angular_precision.setRange(0, 8)
        self.angular_precision.setValue(self.settings.angular_precision)
        self.separator = QComboBox(self)
        self.separator.addItems([",", "."])
        self.separator.setCurrentText(self.settings.decimal_separator)
        self.trailing = QCheckBox("Suprimir zeros à direita", self)
        self.trailing.setChecked(self.settings.suppress_trailing_zeros)
        self.prefix = QLineEdit(self.settings.prefix, self)
        self.suffix = QLineEdit(self.settings.suffix, self)

        form = QFormLayout()
        form.addRow("Altura do texto:", self.text_height)
        form.addRow("Tamanho das setas:", self.arrow_size)
        form.addRow("Escala geral (DIMSCALE):", self.scale)
        form.addRow("Afastamento da origem:", self.ext_offset)
        form.addRow("Extensão além da cota:", self.ext_beyond)
        form.addRow("Folga do texto:", self.text_gap)
        form.addRow("Casas lineares:", self.precision)
        form.addRow("Casas angulares:", self.angular_precision)
        form.addRow("Separador decimal:", self.separator)
        form.addRow("Prefixo:", self.prefix)
        form.addRow("Sufixo:", self.suffix)
        form.addRow("", self.trailing)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, self)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _distance(self, value: float, maximum: float = 100_000.0) -> QDoubleSpinBox:
        spin = QDoubleSpinBox(self)
        spin.setDecimals(6)
        spin.setRange(0.000001, maximum)
        spin.setValue(value)
        return spin

    def result_settings(self):
        return replace(
            self.settings,
            text_height=self.text_height.value(),
            arrow_size=self.arrow_size.value(),
            scale=self.scale.value(),
            extension_offset=self.ext_offset.value(),
            extension_beyond=self.ext_beyond.value(),
            text_gap=self.text_gap.value(),
            precision=self.precision.value(),
            angular_precision=self.angular_precision.value(),
            decimal_separator=self.separator.currentText(),
            suppress_trailing_zeros=self.trailing.isChecked(),
            prefix=self.prefix.text(),
            suffix=self.suffix.text(),
        )

    @classmethod
    def edit(cls, doc, parent=None) -> bool:
        dialog = cls(doc, parent)
        if dialog.exec() != QDialog.Accepted:
            return False
        doc.update_dimension_style(dialog.result_settings())
        return True
