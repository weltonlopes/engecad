"""Painel de camadas: visibilidade, cor e camada corrente."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QColorDialog,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QPushButton,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..render.styles import aci_to_qcolor

COL_VIS, COL_COLOR, COL_NAME = 0, 1, 2


class LayerPanel(QWidget):
    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._loading = False

        self.tree = QTreeWidget(self)
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["", "Cor", "Camada"])
        self.tree.setRootIsDecorated(False)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        hdr = self.tree.header()
        hdr.setSectionResizeMode(COL_VIS, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_COLOR, QHeaderView.Fixed)
        hdr.setSectionResizeMode(COL_NAME, QHeaderView.Stretch)
        self.tree.setColumnWidth(COL_VIS, 28)
        self.tree.setColumnWidth(COL_COLOR, 46)
        self.tree.itemChanged.connect(self._on_item_changed)
        self.tree.itemDoubleClicked.connect(self._on_double_click)

        add = QPushButton("Nova", self)
        add.clicked.connect(self._new_layer)
        color = QPushButton("Cor", self)
        color.clicked.connect(self._pick_color)
        current = QPushButton("Tornar corrente", self)
        current.clicked.connect(self._make_current)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        for b in (add, color, current):
            buttons.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.tree, 1)
        lay.addLayout(buttons)

        ctx.documentReplaced.connect(self.reload)
        ctx.documentChanged.connect(self.reload)
        self.reload()

    # ---------------- carga ----------------

    def reload(self) -> None:
        self._loading = True
        try:
            doc = self.ctx.doc
            current = doc.current_layer
            self.tree.clear()
            for name in doc.layer_names():
                item = QTreeWidgetItem(self.tree)
                item.setData(COL_NAME, Qt.UserRole, name)
                item.setFlags(item.flags() | Qt.ItemIsUserCheckable)
                item.setCheckState(
                    COL_VIS, Qt.Checked if doc.layer_is_visible(name) else Qt.Unchecked
                )
                aci = doc.layer_color(name)
                item.setBackground(COL_COLOR, QBrush(aci_to_qcolor(abs(aci))))
                item.setText(COL_NAME, name)
                if name == current:
                    f = QFont(item.font(COL_NAME))
                    f.setBold(True)
                    item.setFont(COL_NAME, f)
                    item.setText(COL_NAME, f"{name}  (corrente)")
        finally:
            self._loading = False

    def _selected_name(self) -> str | None:
        items = self.tree.selectedItems()
        return items[0].data(COL_NAME, Qt.UserRole) if items else None

    # ---------------- acoes ----------------

    def _on_item_changed(self, item, column) -> None:
        if self._loading or column != COL_VIS:
            return
        name = item.data(COL_NAME, Qt.UserRole)
        self.ctx.doc.set_layer_visible(name, item.checkState(COL_VIS) == Qt.Checked)
        self.ctx.refresh()

    def _on_double_click(self, item, column) -> None:
        name = item.data(COL_NAME, Qt.UserRole)
        if column == COL_COLOR:
            self._pick_color()
        else:
            self.ctx.doc.current_layer = name
            self.reload()

    def _make_current(self) -> None:
        name = self._selected_name()
        if name:
            self.ctx.doc.current_layer = name
            self.ctx.message(f"Camada corrente: {name}")
            self.reload()

    def _new_layer(self) -> None:
        name, ok = QInputDialog.getText(self, "Nova camada", "Nome:")
        if not ok or not name.strip():
            return
        self.ctx.doc.ensure_layer(name.strip())
        self.ctx.doc.current_layer = name.strip()
        self.reload()

    def _pick_color(self) -> None:
        name = self._selected_name()
        if not name:
            return
        doc = self.ctx.doc
        initial = aci_to_qcolor(abs(doc.layer_color(name)))
        chosen = QColorDialog.getColor(initial, self, f"Cor da camada {name}")
        if not chosen.isValid():
            return
        doc.set_layer_color(name, _nearest_aci(chosen))
        self.reload()
        self.ctx.refresh()


def _nearest_aci(qcolor) -> int:
    """Cor de tela -> indice ACI mais proximo (o DXF guarda ACI, nao RGB)."""
    from ezdxf import colors as ezcolors

    target = (qcolor.red(), qcolor.green(), qcolor.blue())
    best, best_d = 7, float("inf")
    for aci in range(1, 256):
        try:
            r, g, b = ezcolors.aci2rgb(aci)
        except Exception:
            continue
        d = (r - target[0]) ** 2 + (g - target[1]) ** 2 + (b - target[2]) ** 2
        if d < best_d:
            best, best_d = aci, d
    return best
