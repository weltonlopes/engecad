"""Gerenciador de Propriedades de Camadas no estilo do AutoCAD."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.layers import LayerFilter
from ..render.styles import aci_to_qcolor

(
    COL_CURRENT,
    COL_ON,
    COL_FREEZE,
    COL_LOCK,
    COL_COLOR,
    COL_NAME,
    COL_LINETYPE,
    COL_LINEWEIGHT,
    COL_TRANSPARENCY,
    COL_PLOT_STYLE,
    COL_PLOT,
    COL_STATUS,
    COL_DESCRIPTION,
) = range(13)

HEADERS = [
    "Atual",
    "Ligada",
    "Congelada",
    "Bloqueada",
    "Cor",
    "Nome",
    "Tipo de linha",
    "Espessura",
    "Transp.",
    "Estilo de plotagem",
    "Plotar",
    "Status",
    "Descrição",
]

LINEWEIGHTS = [
    -3,
    -2,
    -1,
    0,
    5,
    9,
    13,
    15,
    18,
    20,
    25,
    30,
    35,
    40,
    50,
    53,
    60,
    70,
    80,
    90,
    100,
    106,
    120,
    140,
    158,
    200,
    211,
]


def _lineweight_text(value: int) -> str:
    return {-3: "Padrão", -2: "PorBloco", -1: "PorCamada"}.get(value, f"{value / 100:.2f} mm")


class LayerPanel(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._loading = False

        self.search = QLineEdit(self)
        self.search.setPlaceholderText("Pesquisar por nome ou descrição…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.reload)

        self.filter_combo = QComboBox(self)
        self.filter_combo.setMinimumWidth(145)
        self.filter_combo.currentIndexChanged.connect(self.reload)

        top = QHBoxLayout()
        top.setContentsMargins(0, 0, 0, 0)
        top.addWidget(QLabel("Filtro:"))
        top.addWidget(self.filter_combo)
        top.addWidget(self.search, 1)

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(len(HEADERS))
        self.tree.setHeaderLabels(HEADERS)
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.tree.setAlternatingRowColors(True)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(COL_NAME, Qt.AscendingOrder)
        self.tree.setHorizontalScrollMode(QAbstractItemView.ScrollPerPixel)
        header = self.tree.header()
        for column in (COL_CURRENT, COL_ON, COL_FREEZE, COL_LOCK, COL_COLOR, COL_PLOT):
            header.setSectionResizeMode(column, QHeaderView.Fixed)
        for column, width in {
            COL_CURRENT: 46,
            COL_ON: 50,
            COL_FREEZE: 72,
            COL_LOCK: 70,
            COL_COLOR: 54,
            COL_NAME: 170,
            COL_LINETYPE: 110,
            COL_LINEWEIGHT: 90,
            COL_TRANSPARENCY: 70,
            COL_PLOT_STYLE: 120,
            COL_PLOT: 50,
            COL_STATUS: 110,
            COL_DESCRIPTION: 220,
        }.items():
            self.tree.setColumnWidth(column, width)
        header.setSectionResizeMode(COL_DESCRIPTION, QHeaderView.Stretch)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for label, slot, tip in (
            ("Nova", self._new_layer, "Criar uma camada"),
            ("Excluir", self._delete_layers, "Excluir camadas vazias"),
            ("Renomear", self._rename_layer, "Renomear a camada selecionada"),
            ("Atual", self._make_current, "Definir como camada atual"),
            ("Filtros…", self._manage_filters, "Filtros de propriedades e grupos"),
            ("Estados…", self._manage_states, "Salvar e restaurar estados de camadas"),
            ("Viewport…", self._viewport_overrides, "Sobrescritas específicas por viewport"),
            ("Reconciliar", self._reconcile, "Marcar novas camadas como verificadas"),
        ):
            button = QPushButton(label, self)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addLayout(top)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)

        ctx.documentReplaced.connect(self.reload)
        ctx.documentChanged.connect(self.reload)
        self.reload()

    def reload(self) -> None:
        selected = {n.casefold() for n in self._selected_names()}
        manager = self.ctx.doc.layer_manager
        manager.detect_new_layers()
        self._loading = True
        try:
            selected_filter = self.filter_combo.currentData()
            self.filter_combo.blockSignals(True)
            self.filter_combo.clear()
            self.filter_combo.addItem("Todas as camadas", "")
            self.filter_combo.addItem("Camadas de Xref", "@xref")
            self.filter_combo.addItem("Novas não reconciliadas", "@new")
            for name in sorted(manager.filters, key=str.casefold):
                self.filter_combo.addItem(name, name)
            index = self.filter_combo.findData(selected_filter)
            self.filter_combo.setCurrentIndex(max(index, 0))
            self.filter_combo.blockSignals(False)

            current_filter = self.filter_combo.currentData()
            rows = manager.all(
                search=self.search.text(),
                filter_name=(
                    current_filter
                    if current_filter and not current_filter.startswith("@")
                    else None
                ),
            )
            if current_filter == "@xref":
                rows = [r for r in rows if r.xref]
            elif current_filter == "@new":
                rows = [r for r in rows if not r.reconciled]

            self.tree.clear()
            for props in rows:
                item = QTreeWidgetItem(self.tree)
                item.setData(COL_NAME, Qt.UserRole, props.name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                for column, checked in (
                    (COL_ON, props.on),
                    (COL_FREEZE, props.frozen),
                    (COL_LOCK, props.locked),
                    (COL_PLOT, props.plot),
                ):
                    item.setCheckState(column, Qt.Checked if checked else Qt.Unchecked)
                item.setText(
                    COL_CURRENT,
                    "●" if props.name == self.ctx.doc.current_layer else "",
                )
                item.setBackground(COL_COLOR, QBrush(aci_to_qcolor(props.color)))
                item.setText(COL_COLOR, str(props.color))
                item.setText(COL_NAME, props.name)
                item.setText(COL_LINETYPE, props.linetype)
                item.setText(COL_LINEWEIGHT, _lineweight_text(props.lineweight))
                item.setText(COL_TRANSPARENCY, f"{props.transparency}%")
                item.setText(COL_PLOT_STYLE, props.plot_style)
                statuses = []
                if props.xref:
                    statuses.append("Xref")
                if not props.reconciled:
                    statuses.append("Nova")
                item.setText(COL_STATUS, ", ".join(statuses) or "Reconciliada")
                item.setText(COL_DESCRIPTION, props.description)
                if props.name == self.ctx.doc.current_layer:
                    font = QFont(item.font(COL_NAME))
                    font.setBold(True)
                    item.setFont(COL_NAME, font)
                if props.locked:
                    item.setForeground(COL_NAME, QBrush(QColor("#8e97ad")))
                if props.xref:
                    font = QFont(item.font(COL_NAME))
                    font.setItalic(True)
                    item.setFont(COL_NAME, font)
                if props.name.casefold() in selected:
                    item.setSelected(True)
        finally:
            self._loading = False

    def _selected_names(self) -> list[str]:
        return [str(item.data(COL_NAME, Qt.UserRole)) for item in self.tree.selectedItems()]

    def _selected_name(self) -> str | None:
        names = self._selected_names()
        return names[0] if names else None

    def _report_error(self, exc: Exception) -> None:
        QMessageBox.warning(self, "Gerenciador de camadas", str(exc))

    def _on_item_changed(self, item, column) -> None:
        if self._loading or column not in (COL_ON, COL_FREEZE, COL_LOCK, COL_PLOT):
            return
        name = item.data(COL_NAME, Qt.UserRole)
        key = {
            COL_ON: "on",
            COL_FREEZE: "frozen",
            COL_LOCK: "locked",
            COL_PLOT: "plot",
        }[column]
        try:
            self.ctx.doc.layer_manager.update(name, **{key: item.checkState(column) == Qt.Checked})
            self.ctx.refresh()
        except Exception as exc:
            self._report_error(exc)
            self.reload()

    def _on_double_click(self, item, column) -> None:
        name = item.data(COL_NAME, Qt.UserRole)
        if column == COL_CURRENT:
            self._make_current(name)
        elif column == COL_COLOR:
            self._pick_color(name)
        elif column == COL_NAME:
            self._rename_layer(name)
        elif column == COL_LINETYPE:
            self._pick_linetype(name)
        elif column == COL_LINEWEIGHT:
            self._pick_lineweight(name)
        elif column == COL_TRANSPARENCY:
            self._pick_transparency(name)
        elif column == COL_PLOT_STYLE:
            self._pick_plot_style(name)
        elif column == COL_DESCRIPTION:
            self._edit_description(name)

    def _make_current(self, name: str | None = None) -> None:
        name = name or self._selected_name()
        if not name:
            return
        try:
            self.ctx.doc.layer_manager.set_current(name)
            self.ctx.message(f"Camada corrente: {name}")
        except Exception as exc:
            self._report_error(exc)
        self.reload()

    def _new_layer(self) -> None:
        name, ok = QInputDialog.getText(self, "Nova camada", "Nome:")
        if not ok or not name.strip():
            return
        try:
            self.ctx.doc.layer_manager.create(name)
            self.ctx.doc.layer_manager.set_current(name.strip())
        except Exception as exc:
            self._report_error(exc)
        self.reload()

    def _delete_layers(self) -> None:
        names = self._selected_names()
        if not names:
            return
        answer = QMessageBox.question(
            self,
            "Excluir camadas",
            f"Excluir {len(names)} camada(s) vazia(s)?",
            QMessageBox.Yes | QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        errors = []
        for name in names:
            try:
                self.ctx.doc.layer_manager.delete(name)
            except Exception as exc:
                errors.append(f"{name}: {exc}")
        if errors:
            QMessageBox.warning(self, "Camadas não excluídas", "\n".join(errors))
        self.reload()

    def _rename_layer(self, name: str | None = None) -> None:
        name = name or self._selected_name()
        if not name:
            return
        new_name, ok = QInputDialog.getText(self, "Renomear camada", "Novo nome:", text=name)
        if not ok or not new_name.strip() or new_name.strip() == name:
            return
        try:
            self.ctx.doc.layer_manager.rename(name, new_name)
        except Exception as exc:
            self._report_error(exc)
        self.reload()

    def _pick_color(self, name: str | None = None) -> None:
        name = name or self._selected_name()
        if not name:
            return
        initial = aci_to_qcolor(self.ctx.doc.layer_manager.properties(name).color)
        chosen = QColorDialog.getColor(initial, self, f"Cor da camada {name}")
        if chosen.isValid():
            self.ctx.doc.layer_manager.update(name, color=_nearest_aci(chosen))
            self.reload()

    def _pick_linetype(self, name: str) -> None:
        values = sorted(
            (str(entry.dxf.name) for entry in self.ctx.doc.drawing.linetypes),
            key=str.casefold,
        )
        current = self.ctx.doc.layer_manager.properties(name).linetype
        index = values.index(current) if current in values else 0
        value, ok = QInputDialog.getItem(self, "Tipo de linha", "Tipo:", values, index, False)
        if ok:
            self.ctx.doc.layer_manager.update(name, linetype=value)
            self.reload()

    def _pick_lineweight(self, name: str) -> None:
        labels = [_lineweight_text(value) for value in LINEWEIGHTS]
        current = self.ctx.doc.layer_manager.properties(name).lineweight
        index = LINEWEIGHTS.index(current) if current in LINEWEIGHTS else 0
        value, ok = QInputDialog.getItem(
            self, "Espessura de linha", "Espessura:", labels, index, False
        )
        if ok:
            self.ctx.doc.layer_manager.update(name, lineweight=LINEWEIGHTS[labels.index(value)])
            self.reload()

    def _pick_transparency(self, name: str) -> None:
        current = self.ctx.doc.layer_manager.properties(name).transparency
        value, ok = QInputDialog.getInt(self, "Transparência", "Percentual (0–90):", current, 0, 90)
        if ok:
            self.ctx.doc.layer_manager.update(name, transparency=value)
            self.reload()

    def _pick_plot_style(self, name: str) -> None:
        current = self.ctx.doc.layer_manager.properties(name).plot_style
        value, ok = QInputDialog.getText(
            self, "Estilo de plotagem", "Nome do estilo:", text=current
        )
        if ok:
            self.ctx.doc.layer_manager.update(name, plot_style=value)
            self.reload()

    def _edit_description(self, name: str) -> None:
        current = self.ctx.doc.layer_manager.properties(name).description
        value, ok = QInputDialog.getMultiLineText(
            self, "Descrição da camada", "Descrição:", current
        )
        if ok:
            self.ctx.doc.layer_manager.update(name, description=value)
            self.reload()

    def _reconcile(self) -> None:
        self.ctx.doc.layer_manager.reconcile(self._selected_names() or None)
        self.ctx.message("Camadas reconciliadas")
        self.reload()

    def _manage_filters(self) -> None:
        FilterDialog(self.ctx.doc.layer_manager, self).exec()
        self.reload()

    def _manage_states(self) -> None:
        StateDialog(self.ctx.doc.layer_manager, self).exec()
        self.reload()

    def _viewport_overrides(self) -> None:
        name = self._selected_name()
        if not name:
            QMessageBox.information(self, "Viewport", "Selecione uma camada.")
            return
        ViewportOverrideDialog(self.ctx.doc.layer_manager, name, self).exec()
        self.reload()


class FilterDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Filtros de camadas")
        self.resize(520, 360)
        self.list = QListWidget(self)
        self._reload()
        add_property = QPushButton("Novo filtro de propriedades…")
        add_group = QPushButton("Novo filtro de grupo…")
        remove = QPushButton("Excluir")
        add_property.clicked.connect(self._add_property)
        add_group.clicked.connect(self._add_group)
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(add_property)
        row.addWidget(add_group)
        row.addWidget(remove)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Filtros personalizados salvos no projeto:"))
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _reload(self):
        self.list.clear()
        for layer_filter in sorted(
            self.manager.filters.values(), key=lambda item: item.name.casefold()
        ):
            if layer_filter.kind == "group":
                detail = ", ".join(layer_filter.members)
            else:
                detail = ", ".join(f"{key}={value}" for key, value in layer_filter.criteria.items())
            self.list.addItem(f"{layer_filter.name}  [{layer_filter.kind}]  {detail}")

    def _add_property(self):
        name, ok = QInputDialog.getText(self, "Filtro de propriedades", "Nome do filtro:")
        if not ok or not name.strip():
            return
        fields = [
            "name",
            "on",
            "frozen",
            "locked",
            "color",
            "linetype",
            "plot",
            "xref",
            "reconciled",
        ]
        field, ok = QInputDialog.getItem(
            self, "Filtro de propriedades", "Propriedade:", fields, 0, False
        )
        if not ok:
            return
        value, ok = QInputDialog.getText(
            self, "Filtro de propriedades", "Valor (nome aceita * e ?):"
        )
        if not ok:
            return
        if field in {"on", "frozen", "locked", "plot", "xref", "reconciled"}:
            parsed = value.strip().casefold() in {"1", "true", "sim", "yes", "on"}
        elif field == "color":
            try:
                parsed = int(value)
            except ValueError:
                QMessageBox.warning(self, "Filtro", "A cor deve ser um índice ACI numérico.")
                return
        else:
            parsed = value
        self.manager.add_filter(LayerFilter(name.strip(), "property", {field: parsed}))
        self._reload()

    def _add_group(self):
        name, ok = QInputDialog.getText(self, "Filtro de grupo", "Nome do grupo:")
        if not ok or not name.strip():
            return
        available = [props.name for props in self.manager.all()]
        text, ok = QInputDialog.getMultiLineText(
            self, "Filtro de grupo", "Camadas (uma por linha):", "\n".join(available)
        )
        if ok:
            members = [line.strip() for line in text.splitlines() if line.strip()]
            self.manager.add_filter(LayerFilter(name.strip(), "group", members=members))
            self._reload()

    def _remove(self):
        row = self.list.currentRow()
        names = sorted(self.manager.filters, key=str.casefold)
        if 0 <= row < len(names):
            self.manager.remove_filter(names[row])
            self._reload()


class StateDialog(QDialog):
    def __init__(self, manager, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.setWindowTitle("Estados de camadas")
        self.resize(520, 340)
        self.list = QListWidget(self)
        self._reload()
        save = QPushButton("Salvar estado atual…")
        restore = QPushButton("Restaurar")
        remove = QPushButton("Excluir")
        save.clicked.connect(self._save)
        restore.clicked.connect(self._restore)
        remove.clicked.connect(self._remove)
        row = QHBoxLayout()
        row.addWidget(save)
        row.addWidget(restore)
        row.addWidget(remove)
        row.addStretch(1)
        layout = QVBoxLayout(self)
        layout.addWidget(self.list, 1)
        layout.addLayout(row)
        close = QDialogButtonBox(QDialogButtonBox.Close)
        close.rejected.connect(self.reject)
        layout.addWidget(close)

    def _names(self):
        return sorted(self.manager.states, key=str.casefold)

    def _reload(self):
        self.list.clear()
        for name in self._names():
            state = self.manager.states[name]
            self.list.addItem(f"{name} — {state.description}" if state.description else name)

    def _save(self):
        name, ok = QInputDialog.getText(self, "Salvar estado", "Nome:")
        if not ok or not name.strip():
            return
        description, ok = QInputDialog.getText(self, "Salvar estado", "Descrição:")
        if ok:
            self.manager.save_state(name, description)
            self._reload()

    def _restore(self):
        row = self.list.currentRow()
        names = self._names()
        if 0 <= row < len(names):
            self.manager.restore_state(names[row])
            self.accept()

    def _remove(self):
        row = self.list.currentRow()
        names = self._names()
        if 0 <= row < len(names):
            self.manager.delete_state(names[row])
            self._reload()


class ViewportOverrideDialog(QDialog):
    def __init__(self, manager, layer_name: str, parent=None):
        super().__init__(parent)
        self.manager = manager
        self.layer_name = layer_name
        self.setWindowTitle(f"Sobrescritas por viewport — {layer_name}")
        self.viewport = QComboBox(self)
        for handle, label in manager.viewport_names():
            self.viewport.addItem(label, handle)
        self.color = QSpinBox(self)
        self.color.setRange(1, 255)
        self.linetype = QComboBox(self)
        for entry in manager.document.drawing.linetypes:
            self.linetype.addItem(str(entry.dxf.name))
        self.lineweight = QComboBox(self)
        for value in LINEWEIGHTS:
            self.lineweight.addItem(_lineweight_text(value), value)
        self.transparency = QSpinBox(self)
        self.transparency.setRange(0, 90)
        self.transparency.setSuffix("%")
        self.plot_style = QLineEdit(self)
        self.frozen = QCheckBox("Congelar somente neste viewport", self)
        base = manager.properties(layer_name)
        self.color.setValue(base.color)
        self.linetype.setCurrentText(base.linetype)
        self.lineweight.setCurrentIndex(max(self.lineweight.findData(base.lineweight), 0))
        self.transparency.setValue(base.transparency)
        self.plot_style.setText(base.plot_style)
        form = QFormLayout()
        form.addRow("Viewport:", self.viewport)
        form.addRow("Cor ACI:", self.color)
        form.addRow("Tipo de linha:", self.linetype)
        form.addRow("Espessura:", self.lineweight)
        form.addRow("Transparência:", self.transparency)
        form.addRow("Estilo de plotagem:", self.plot_style)
        form.addRow("", self.frozen)
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Reset | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        reset = buttons.button(QDialogButtonBox.Reset)
        reset.setText("Remover sobrescritas")
        reset.clicked.connect(self._clear)
        layout = QVBoxLayout(self)
        if self.viewport.count() == 0:
            layout.addWidget(QLabel("O desenho não possui viewports de apresentação."))
            buttons.button(QDialogButtonBox.Save).setEnabled(False)
        layout.addLayout(form)
        layout.addWidget(buttons)

    def _save(self):
        viewport = self.viewport.currentData()
        if viewport:
            self.manager.set_viewport_override(
                viewport,
                self.layer_name,
                color=self.color.value(),
                linetype=self.linetype.currentText(),
                lineweight=self.lineweight.currentData(),
                transparency=self.transparency.value(),
                plot_style=self.plot_style.text(),
                frozen=self.frozen.isChecked(),
            )
            self.accept()

    def _clear(self):
        viewport = self.viewport.currentData()
        if viewport:
            self.manager.clear_viewport_override(viewport, self.layer_name)
            self.accept()


def _nearest_aci(qcolor) -> int:
    """Cor de tela -> indice ACI mais proximo."""
    from ezdxf import colors as ezcolors

    target = (qcolor.red(), qcolor.green(), qcolor.blue())
    best, best_distance = 7, float("inf")
    for aci in range(1, 256):
        try:
            r, g, b = ezcolors.aci2rgb(aci)
        except Exception:
            continue
        distance = (r - target[0]) ** 2 + (g - target[1]) ** 2 + (b - target[2]) ** 2
        if distance < best_distance:
            best, best_distance = aci, distance
    return best
