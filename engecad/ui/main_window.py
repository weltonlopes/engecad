"""Janela principal do EngeCAD."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressDialog,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..context import AppContext
from ..io.dxf_io import DxfError, new_document, open_document, save_document
from ..io.raster_import import (
    RasterImportError,
    diagnose,
    execute_plan,
    plan_import,
)
from ..render.canvas import CadCanvas
from ..render.raster_layer import RasterLayer
from ..scripting.console import PythonConsole
from .command_line import CommandLine
from .crs_dialog import CrsDialog
from .layer_panel import LayerPanel

RASTER_FILTER = (
    "Imagens georreferenciadas (*.ecw *.tif *.tiff *.jp2 *.img *.sid *.vrt *.png *.jpg);;"
    "ECW (*.ecw);;GeoTIFF/COG (*.tif *.tiff);;Todos (*)"
)
DXF_FILTER = "Desenho DXF (*.dxf);;Todos (*)"


class _ConvertWorker(QThread):
    """Converte o raster fora da thread da interface -- um ECW grande leva minutos."""

    done = Signal(object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, plan, parent=None):
        super().__init__(parent)
        self.plan = plan

    def run(self):
        try:
            self.done.emit(execute_plan(self.plan, progress=self.progress.emit))
        except (RasterImportError, OSError) as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.ctx = AppContext()
        self.setWindowTitle("EngeCAD")
        self.resize(1400, 880)

        self.canvas = CadCanvas(self.ctx, self)
        self.cmdline = CommandLine(self.ctx, self)

        central = QWidget(self)
        lay = QVBoxLayout(central)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.canvas, 1)
        lay.addWidget(self.cmdline)
        self.setCentralWidget(central)

        self._build_docks()
        self._build_status_bar()
        self._build_actions()
        self._connect()

        self.ctx.zoom_extents()
        self._update_title()
        self.cmdline.focus()

    # ---------------- montagem ----------------

    def _build_docks(self) -> None:
        self.layer_panel = LayerPanel(self.ctx, self)
        dock = QDockWidget("Camadas", self)
        dock.setObjectName("dock_camadas")
        dock.setWidget(self.layer_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, dock)
        self.dock_layers = dock

        self.console = PythonConsole(self.ctx, self)
        cdock = QDockWidget("Console Python", self)
        cdock.setObjectName("dock_console")
        cdock.setWidget(self.console)
        self.addDockWidget(Qt.BottomDockWidgetArea, cdock)
        cdock.hide()
        self.dock_console = cdock

    def _build_status_bar(self) -> None:
        sb = self.statusBar()
        self.lbl_coord = QLabel("E -  N -", self)
        self.lbl_coord.setMinimumWidth(300)
        self.lbl_snap = QLabel("", self)
        self.lbl_snap.setMinimumWidth(110)
        self.lbl_scale = QLabel("", self)
        self.lbl_scale.setMinimumWidth(100)
        self.lbl_layer = QLabel("", self)
        self.lbl_layer.setMinimumWidth(140)
        self.lbl_crs = QLabel("", self)
        for w in (self.lbl_coord, self.lbl_snap, self.lbl_scale, self.lbl_layer, self.lbl_crs):
            sb.addPermanentWidget(w)

    def _act(self, text, slot, shortcut=None, tip=""):
        a = QAction(text, self)
        a.triggered.connect(slot)
        if shortcut:
            a.setShortcut(QKeySequence(shortcut))
        if tip:
            a.setStatusTip(tip)
        return a

    def _build_actions(self) -> None:
        m_file = self.menuBar().addMenu("&Arquivo")
        m_file.addAction(self._act("&Novo...", self.on_new, "Ctrl+N"))
        m_file.addAction(self._act("&Abrir DXF...", self.on_open, "Ctrl+O"))
        m_file.addAction(self._act("&Salvar", self.on_save, "Ctrl+S"))
        m_file.addAction(self._act("Salvar &como...", self.on_save_as, "Ctrl+Shift+S"))
        m_file.addSeparator()
        m_file.addAction(
            self._act("&Importar imagem de fundo...", self.on_import_raster, "Ctrl+I")
        )
        m_file.addSeparator()
        m_file.addAction(self._act("Sai&r", self.close, "Ctrl+Q"))

        m_edit = self.menuBar().addMenu("&Editar")
        m_edit.addAction(self._act("&Desfazer", lambda: self.run("U"), "Ctrl+Z"))
        m_edit.addAction(self._act("&Refazer", lambda: self.run("REDO"), "Ctrl+Y"))

        m_draw = self.menuBar().addMenu("&Desenhar")
        m_draw.addAction(self._act("&Linha", lambda: self.run("LINE"), "L"))
        m_draw.addAction(self._act("&Polilinha", lambda: self.run("PLINE"), "P"))

        m_query = self.menuBar().addMenu("&Consultar")
        m_query.addAction(self._act("&Distancia e azimute", lambda: self.run("DIST")))
        m_query.addAction(self._act("&Area e perimetro", lambda: self.run("AREA")))

        m_view = self.menuBar().addMenu("&Vista")
        m_view.addAction(self._act("&Enquadrar tudo", lambda: self.run("ZE"), "Ctrl+E"))
        m_view.addAction(self._act("Aproximar", lambda: self.run("ZOOM", "1.25"), "Ctrl++"))
        m_view.addAction(self._act("Afastar", lambda: self.run("ZOOM", "0.8"), "Ctrl+-"))
        m_view.addSeparator()
        m_view.addAction(self._act("Alternar &grade", lambda: self.run("GRADE"), "F7"))
        m_view.addAction(self._act("Alternar &snap", lambda: self.run("OSNAP"), "F3"))

        m_proj = self.menuBar().addMenu("&Projeto")
        m_proj.addAction(self._act("&Sistema de coordenadas...", self.on_change_crs))
        m_proj.addAction(self._act("&Imagens carregadas...", self.on_raster_info))

        m_tools = self.menuBar().addMenu("&Ferramentas")
        a_console = self.dock_console.toggleViewAction()
        a_console.setText("&Console Python")
        a_console.setShortcut(QKeySequence("F9"))
        m_tools.addAction(a_console)
        m_tools.addAction(self._act("Executar script .&py...", self.on_run_script))
        m_tools.addAction(self.dock_layers.toggleViewAction())

        m_help = self.menuBar().addMenu("A&juda")
        m_help.addAction(self._act("Lista de &comandos", lambda: self.run("AJUDA"), "F1"))
        m_help.addAction(self._act("Diagnostico de &raster (ECW)...", self.on_diagnose))
        m_help.addAction(self._act("&Sobre o EngeCAD", self.on_about))

    def _connect(self) -> None:
        self.canvas.coordinateMoved.connect(self._on_coordinate)
        self.canvas.snapChanged.connect(self._on_snap)
        self.canvas.viewChanged.connect(self._on_view)
        self.ctx.statusMessage.connect(self._on_message)
        self.ctx.documentChanged.connect(self._update_title)
        self.ctx.documentReplaced.connect(self._on_document_replaced)
        self.ctx.viewChanged.connect(self._on_view)
        self._on_view()
        self._on_document_replaced()

    # ---------------- atalho de comando ----------------

    def run(self, name: str, *args) -> None:
        self.ctx.run_command(name, *args)
        self.canvas.setFocus()

    # ---------------- status ----------------

    def _on_coordinate(self, p) -> None:
        if p is None:
            self.lbl_coord.setText("E -  N -")
            return
        crs = self.ctx.doc.crs
        d = crs.decimals
        u = crs.unit_suffix
        self.lbl_coord.setText(f"E {p.x:.{d}f}   N {p.y:.{d}f} {u}")

    def _on_snap(self, snap) -> None:
        self.lbl_snap.setText(snap.label if snap else "")

    def _on_view(self) -> None:
        vp = self.ctx.viewport
        self.lbl_scale.setText(f"1:{vp.scale_denominator():,.0f}".replace(",", "."))

    def _on_message(self, text: str) -> None:
        first = text.splitlines()[0] if text else ""
        self.statusBar().showMessage(first, 8000)
        if "\n" in text:
            self.console.write(text + "\n")
            if not self.dock_console.isVisible():
                self.dock_console.show()

    def _on_document_replaced(self) -> None:
        self.console.rebind(self.ctx)
        self.layer_panel.reload()
        self._update_title()
        doc = self.ctx.doc
        self.lbl_crs.setText(doc.crs.srid)
        self.lbl_layer.setText(f"Camada: {doc.current_layer}")

    def _update_title(self) -> None:
        doc = self.ctx.doc
        mark = "*" if doc.modified else ""
        self.setWindowTitle(f"EngeCAD {__version__}  -  {doc.title}{mark}")
        self.lbl_layer.setText(f"Camada: {doc.current_layer}")
        self.lbl_crs.setText(doc.crs.srid)

    # ---------------- arquivo ----------------

    def _confirm_discard(self) -> bool:
        if not self.ctx.doc.modified:
            return True
        r = QMessageBox.question(
            self,
            "Alteracoes nao salvas",
            f"O desenho {self.ctx.doc.title} tem alteracoes nao salvas.\nSalvar antes?",
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel,
        )
        if r == QMessageBox.Cancel:
            return False
        if r == QMessageBox.Save:
            return self.on_save()
        return True

    def on_new(self) -> None:
        if not self._confirm_discard():
            return
        crs = CrsDialog.ask(self, self.ctx.doc.crs)
        if crs is None:
            return
        new_document(self.ctx, crs)
        self.ctx.message(f"Novo desenho em {crs.display}")

    def on_open(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Abrir DXF", "", DXF_FILTER)
        if not path:
            return
        try:
            open_document(self.ctx, path)
        except DxfError as exc:
            QMessageBox.critical(self, "Erro ao abrir", str(exc))
            return
        self.ctx.message(f"Aberto: {Path(path).name}")

    def on_save(self) -> bool:
        if self.ctx.doc.path is None:
            return self.on_save_as()
        try:
            p = save_document(self.ctx)
        except DxfError as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))
            return False
        self.ctx.message(f"Salvo: {p.name}  (+ {p.stem}.emap.json)")
        self._update_title()
        return True

    def on_save_as(self) -> bool:
        path, _ = QFileDialog.getSaveFileName(self, "Salvar DXF", "", DXF_FILTER)
        if not path:
            return False
        try:
            p = save_document(self.ctx, path)
        except DxfError as exc:
            QMessageBox.critical(self, "Erro ao salvar", str(exc))
            return False
        self.ctx.message(f"Salvo: {p.name}  (+ {p.stem}.emap.json)")
        self._update_title()
        return True

    # ---------------- raster ----------------

    def on_import_raster(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar imagem", "", RASTER_FILTER)
        if not path:
            return
        plan = plan_import(path)

        if plan.blocked:
            QMessageBox.warning(self, "Formato nao suportado nesta maquina", plan.message)
            return

        if plan.needs_conversion:
            r = QMessageBox.question(
                self, "Converter imagem", plan.message, QMessageBox.Yes | QMessageBox.No
            )
            if r != QMessageBox.Yes:
                return
            self._convert_then_load(plan)
            return

        self._load_raster(plan.target, source=None)
        self.ctx.message(plan.message)

    def _convert_then_load(self, plan) -> None:
        dlg = QProgressDialog("Convertendo imagem para COG...", "Cancelar", 0, 0, self)
        dlg.setWindowModality(Qt.WindowModal)
        dlg.setMinimumDuration(0)

        worker = _ConvertWorker(plan, self)
        worker.progress.connect(dlg.setLabelText)
        worker.done.connect(lambda p: (dlg.close(), self._load_raster(p, str(plan.source))))
        worker.failed.connect(
            lambda msg: (dlg.close(), QMessageBox.critical(self, "Falha na conversao", msg))
        )
        worker.finished.connect(worker.deleteLater)
        dlg.canceled.connect(worker.terminate)
        worker.start()
        dlg.exec()

    def _load_raster(self, path, source=None) -> None:
        try:
            layer = RasterLayer(path, source=source, project_crs=self.ctx.doc.crs)
        except Exception as exc:
            QMessageBox.critical(self, "Erro ao carregar imagem", str(exc))
            return
        self.ctx.rasters.append(layer)
        self.ctx.rastersChanged.emit()
        self.ctx.zoom_extents()
        extra = "  (reprojetado ao vivo)" if layer.reprojected else ""
        self.ctx.message(f"Imagem carregada: {layer.name}{extra}")

    def on_raster_info(self) -> None:
        if not self.ctx.rasters:
            QMessageBox.information(self, "Imagens", "Nenhuma imagem carregada.")
            return
        text = "\n\n".join(r.info() for r in self.ctx.rasters)
        QMessageBox.information(self, "Imagens carregadas", text)

    def on_diagnose(self) -> None:
        QMessageBox.information(self, "Diagnostico de raster", diagnose())

    # ---------------- projeto ----------------

    def on_change_crs(self) -> None:
        crs = CrsDialog.ask(self, self.ctx.doc.crs)
        if crs is None:
            return
        self.ctx.doc.crs = crs
        for layer in self.ctx.rasters:
            layer.set_project_crs(crs)
        self._update_title()
        self.ctx.refresh()
        self.ctx.message(f"CRS do projeto: {crs.display}")

    # ---------------- scripts ----------------

    def on_run_script(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Executar script", "", "Python (*.py)")
        if not path:
            return
        self.dock_console.show()
        self.console.run_file(path)

    def on_about(self) -> None:
        QMessageBox.about(
            self,
            "Sobre o EngeCAD",
            f"<h3>EngeCAD {__version__}</h3>"
            "<p>CAD livre para mapeamento e plantas cadastrais.</p>"
            "<p>Construido sobre PySide6, ezdxf, pyproj e rasterio.</p>"
            "<p>Formato nativo: DXF. Coordenadas em float64 no CRS do projeto.</p>",
        )

    # ---------------- fechamento ----------------

    def closeEvent(self, ev):
        if not self._confirm_discard():
            ev.ignore()
            return
        for layer in self.ctx.rasters:
            layer.close()
        ev.accept()
