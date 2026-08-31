"""Painel de propriedades do objeto: camada, cor, medidas e navegacao de vertices."""

from __future__ import annotations

import math

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ..core.entities import entity_insert_point, entity_polylines
from ..core.geometry import Vec2, azimuth, format_dms, polygon_area, polyline_length
from ..core.grips import VERTEX, drag_grip, entity_grips
from ..render.styles import aci_to_qcolor
from .layer_panel import _nearest_aci

BYLAYER = 256


class PropertiesPanel(QWidget):
    """Mostra e edita as propriedades da entidade (ou entidades) selecionada.

    Ativado/desativado pelo dock "Propriedades" (Ctrl+1). Segue a selecao
    corrente -- nao tem estado proprio de selecao, so de qual vertice esta em
    foco na navegacao.
    """

    def __init__(self, ctx, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self._loading = False
        self._entity = None
        self._vertices: list = []
        self._vertex_index = 0

        self._build_ui()

        ctx.documentReplaced.connect(self._on_document_replaced)
        ctx.documentChanged.connect(self.reload)
        self._bind_selection()
        self.reload()

    def _bind_selection(self) -> None:
        if self.ctx.selection is not None:
            self.ctx.selection.changed.append(self.reload)

    def _on_document_replaced(self) -> None:
        self._bind_selection()
        self.reload()

    # ---------------- montagem ----------------

    def _build_ui(self) -> None:
        lay = QVBoxLayout(self)
        lay.setContentsMargins(6, 6, 6, 6)
        lay.setSpacing(8)

        self.lbl_title = QLabel(self)
        f = self.lbl_title.font()
        f.setBold(True)
        self.lbl_title.setFont(f)
        self.lbl_title.setWordWrap(True)
        lay.addWidget(self.lbl_title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignRight)

        self.cmb_layer = QComboBox(self)
        self.cmb_layer.activated.connect(self._on_layer_changed)
        form.addRow("Camada:", self.cmb_layer)

        color_row = QHBoxLayout()
        self.btn_color = QPushButton(self)
        self.btn_color.setFixedWidth(46)
        self.btn_color.clicked.connect(self._on_pick_color)
        self.btn_bylayer = QPushButton("Por camada", self)
        self.btn_bylayer.clicked.connect(self._on_color_bylayer)
        color_row.addWidget(self.btn_color)
        color_row.addWidget(self.btn_bylayer)
        color_row.addStretch(1)
        form.addRow("Cor:", color_row)

        lay.addLayout(form)
        lay.addWidget(_hline(self))

        self.lbl_measures = QLabel(self)
        self.lbl_measures.setWordWrap(True)
        self.lbl_measures.setTextInteractionFlags(Qt.TextSelectableByMouse)
        lay.addWidget(self.lbl_measures)

        self.sep_vertex = _hline(self)
        lay.addWidget(self.sep_vertex)

        self.lbl_vertex_title = QLabel("Vertices", self)
        fv = self.lbl_vertex_title.font()
        fv.setBold(True)
        self.lbl_vertex_title.setFont(fv)
        lay.addWidget(self.lbl_vertex_title)

        nav_row = QHBoxLayout()
        self.btn_prev = QPushButton("< Anterior", self)
        self.btn_prev.clicked.connect(self._on_prev_vertex)
        self.lbl_vertex_pos = QLabel("-/-", self)
        self.lbl_vertex_pos.setAlignment(Qt.AlignCenter)
        self.btn_next = QPushButton("Proximo >", self)
        self.btn_next.clicked.connect(self._on_next_vertex)
        nav_row.addWidget(self.btn_prev)
        nav_row.addWidget(self.lbl_vertex_pos, 1)
        nav_row.addWidget(self.btn_next)
        lay.addLayout(nav_row)

        coord_form = QFormLayout()
        self.spin_x = QDoubleSpinBox(self)
        self.spin_y = QDoubleSpinBox(self)
        for sp in (self.spin_x, self.spin_y):
            sp.setDecimals(4)
            sp.setRange(-1.0e9, 1.0e9)
            sp.editingFinished.connect(self._on_vertex_edited)
        coord_form.addRow("X:", self.spin_x)
        coord_form.addRow("Y:", self.spin_y)
        lay.addLayout(coord_form)

        self.btn_center = QPushButton("Centralizar vertice na vista", self)
        self.btn_center.clicked.connect(self._on_center_vertex)
        lay.addWidget(self.btn_center)

        self._vertex_widgets = [
            self.sep_vertex, self.lbl_vertex_title, self.btn_prev, self.lbl_vertex_pos,
            self.btn_next, self.spin_x, self.spin_y, self.btn_center,
        ]

        lay.addStretch(1)

    # ---------------- carga ----------------

    def reload(self) -> None:
        self._loading = True
        try:
            sel = self.ctx.selection
            items = list(sel) if sel is not None else []
            self._reload_layer_combo()
            if not items:
                self._show_empty()
            elif len(items) > 1:
                self._show_multi(items)
            else:
                self._show_single(items[0])
        finally:
            self._loading = False

    def _reload_layer_combo(self) -> None:
        self.cmb_layer.clear()
        self.cmb_layer.addItems(self.ctx.doc.layer_names())

    def _set_combo_layer(self, name: str) -> None:
        idx = self.cmb_layer.findText(name)
        if idx < 0:
            self.cmb_layer.addItem(name)
            idx = self.cmb_layer.findText(name)
        self.cmb_layer.setCurrentIndex(idx)

    def _show_empty(self) -> None:
        self._entity = None
        self._vertices = []
        self.ctx.vertex_focus = None
        self.lbl_title.setText("Nenhuma selecao")
        self.cmb_layer.setEnabled(False)
        self.btn_color.setEnabled(False)
        self.btn_color.setStyleSheet("")
        self.btn_bylayer.setEnabled(False)
        self.lbl_measures.setText("")
        self._set_vertex_ui_visible(False)

    def _show_multi(self, items: list) -> None:
        self._entity = None
        self._vertices = []
        self.ctx.vertex_focus = None
        kinds: dict[str, int] = {}
        total = 0.0
        for e in items:
            kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
            total += _entity_length(e)
        detail = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        self.lbl_title.setText(f"{len(items)} objetos selecionados\n{detail}")

        self.cmb_layer.setEnabled(True)
        layers = {e.dxf.get("layer", "0") for e in items}
        if len(layers) == 1:
            self._set_combo_layer(next(iter(layers)))
        else:
            self.cmb_layer.setCurrentIndex(-1)

        self.btn_color.setEnabled(True)
        self.btn_color.setStyleSheet("")
        self.btn_bylayer.setEnabled(True)

        self.lbl_measures.setText(f"Comprimento total: {total:.3f} m")
        self._set_vertex_ui_visible(False)

    def _show_single(self, entity) -> None:
        self._entity = entity
        doc = self.ctx.doc
        t = entity.dxftype()
        layer = entity.dxf.get("layer", "0")
        self.lbl_title.setText(t)

        self.cmb_layer.setEnabled(True)
        self._set_combo_layer(layer)

        self.btn_color.setEnabled(True)
        color = entity.dxf.get("color", BYLAYER)
        if color in (BYLAYER, 0):
            aci = doc.layer_color(layer)
            self.btn_bylayer.setEnabled(False)
        else:
            aci = color
            self.btn_bylayer.setEnabled(True)
        self.btn_color.setStyleSheet(f"background-color: {aci_to_qcolor(abs(aci)).name()};")

        self.lbl_measures.setText(_entity_measures(entity))

        self._vertices = [g for g in entity_grips(entity) if g.kind == VERTEX]
        if self._vertices:
            self._vertex_index = min(self._vertex_index, len(self._vertices) - 1)
            self._set_vertex_ui_visible(True)
            self._refresh_vertex_fields()
        else:
            self.ctx.vertex_focus = None
            self._set_vertex_ui_visible(False)

    def _set_vertex_ui_visible(self, visible: bool) -> None:
        for w in self._vertex_widgets:
            w.setVisible(visible)
        if not visible:
            self.lbl_vertex_pos.setText("-/-")

    def _refresh_vertex_fields(self) -> None:
        if not self._vertices:
            return
        g = self._vertices[self._vertex_index]
        self._loading = True
        try:
            self.lbl_vertex_pos.setText(f"{self._vertex_index + 1}/{len(self._vertices)}")
            self.spin_x.setValue(g.point.x)
            self.spin_y.setValue(g.point.y)
        finally:
            self._loading = False
        self.ctx.vertex_focus = g
        self.ctx.refresh()

    # ---------------- acoes: camada/cor ----------------

    def _selected_entities(self) -> list:
        if self._entity is not None:
            return [self._entity]
        return list(self.ctx.selection) if self.ctx.selection is not None else []

    def _on_layer_changed(self, index: int) -> None:
        if self._loading:
            return
        name = self.cmb_layer.currentText()
        items = self._selected_entities()
        if not name or not items:
            return
        self.ctx.doc.set_entity_attribs(items, "mudar camada", layer=name)
        self.ctx.message(f"Camada alterada para {name}")

    def _on_pick_color(self) -> None:
        items = self._selected_entities()
        if not items:
            return
        initial = self.btn_color.palette().button().color()
        chosen = QColorDialog.getColor(initial, self, "Cor do objeto")
        if not chosen.isValid():
            return
        aci = _nearest_aci(chosen)
        self.ctx.doc.set_entity_attribs(items, "mudar cor", color=aci)
        self.ctx.message("Cor alterada")

    def _on_color_bylayer(self) -> None:
        items = self._selected_entities()
        if not items:
            return
        self.ctx.doc.set_entity_attribs(items, "cor por camada", color=BYLAYER)
        self.ctx.message("Cor definida por camada")

    # ---------------- acoes: vertices ----------------

    def _on_prev_vertex(self) -> None:
        if not self._vertices:
            return
        self._vertex_index = (self._vertex_index - 1) % len(self._vertices)
        self._refresh_vertex_fields()

    def _on_next_vertex(self) -> None:
        if not self._vertices:
            return
        self._vertex_index = (self._vertex_index + 1) % len(self._vertices)
        self._refresh_vertex_fields()

    def _on_vertex_edited(self) -> None:
        if self._loading or not self._vertices or self._entity is None:
            return
        target = Vec2(self.spin_x.value(), self.spin_y.value())
        grip = self._vertices[self._vertex_index]
        if target.distance_to(grip.point) < 1e-9:
            return
        with self.ctx.doc.editing([self._entity], "editar vertice"):
            drag_grip(self._entity, grip, target)
        self.ctx.message(f"Vertice {self._vertex_index + 1} movido para {target.x:.3f}, {target.y:.3f}")
        self._vertices = [g for g in entity_grips(self._entity) if g.kind == VERTEX]
        self.lbl_measures.setText(_entity_measures(self._entity))
        self._refresh_vertex_fields()

    def _on_center_vertex(self) -> None:
        if not self._vertices:
            return
        g = self._vertices[self._vertex_index]
        self.ctx.viewport.zoom_to_point(g.point)
        self.ctx.view_changed()


def _hline(parent) -> QFrame:
    line = QFrame(parent)
    line.setFrameShape(QFrame.HLine)
    line.setFrameShadow(QFrame.Sunken)
    return line


def _entity_length(entity) -> float:
    """Comprimento/perimetro aproximado, usado no total da selecao multipla."""
    t = entity.dxftype()
    if t == "LINE":
        dxf = entity.dxf
        a, b = Vec2(dxf.start.x, dxf.start.y), Vec2(dxf.end.x, dxf.end.y)
        return a.distance_to(b)
    if t == "CIRCLE":
        return 2 * math.pi * float(entity.dxf.radius)
    total = 0.0
    for poly in entity_polylines(entity, sagitta=0.01):
        total += polyline_length(poly, closed=bool(getattr(entity, "closed", False)))
    return total


def _entity_measures(entity) -> str:
    """Descricao das medidas relevantes, uma por linha, para a entidade unica."""
    t = entity.dxftype()
    dxf = entity.dxf
    lines: list[str] = []

    if t == "LINE":
        a, b = Vec2(dxf.start.x, dxf.start.y), Vec2(dxf.end.x, dxf.end.y)
        lines.append(f"Comprimento: {a.distance_to(b):.3f} m")
        lines.append(f"Azimute: {format_dms(azimuth(a, b))}")

    elif t in ("LWPOLYLINE", "POLYLINE"):
        polys = entity_polylines(entity, sagitta=0.001)
        pts = polys[0] if polys else []
        closed = bool(getattr(entity, "closed", False))
        # entity_polylines fecha o contorno duplicando o primeiro vertice no
        # final -- nao conta como vertice extra na exibicao.
        n_vertices = len(pts)
        if closed and len(pts) > 1 and pts[0].distance_to(pts[-1]) < 1e-9:
            n_vertices -= 1
        lines.append(f"Vertices: {n_vertices}")
        lines.append(f"{'Perimetro' if closed else 'Comprimento'}: {polyline_length(pts, closed=closed):.3f} m")
        if closed and len(pts) >= 3:
            area = polygon_area(pts)
            lines.append(f"Area: {area:.3f} m2  ({area / 10000:.4f} ha)")

    elif t == "CIRCLE":
        r = float(dxf.radius)
        lines.append(f"Raio: {r:.3f} m   Diametro: {2 * r:.3f} m")
        lines.append(f"Area: {math.pi * r * r:.3f} m2")
        lines.append(f"Circunferencia: {2 * math.pi * r:.3f} m")

    elif t == "ARC":
        r = float(dxf.radius)
        a0 = math.radians(dxf.start_angle)
        a1 = math.radians(dxf.end_angle)
        if a1 < a0:
            a1 += math.tau
        lines.append(f"Raio: {r:.3f} m")
        lines.append(f"Angulo: {math.degrees(a1 - a0):.3f} graus")
        lines.append(f"Comprimento do arco: {r * (a1 - a0):.3f} m")

    elif t in ("TEXT", "MTEXT", "POINT", "INSERT"):
        p = entity_insert_point(entity)
        if p is not None:
            lines.append(f"Insercao: {p.x:.3f}, {p.y:.3f}")

    else:
        polys = entity_polylines(entity, sagitta=0.001)
        total = sum(polyline_length(p) for p in polys)
        if total:
            lines.append(f"Comprimento: {total:.3f} m")

    return "\n".join(lines) if lines else "-"
