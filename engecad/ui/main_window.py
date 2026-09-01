"""Janela principal do EngeCAD."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QDockWidget,
    QFileDialog,
    QInputDialog,
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
from ..io.shapefile_import import ShapefileImportError, import_shapefile, shapefile_fields
from ..render.canvas import CadCanvas
from ..render.raster_layer import RasterLayer
from ..scripting.console import PythonConsole
from .command_line import CommandLine
from .crs_dialog import CrsDialog
from .dimension_style_dialog import DimensionStyleDialog
from .layer_panel import LayerPanel
from .properties_panel import PropertiesPanel

RASTER_FILTER = (
    "Imagens georreferenciadas (*.ecw *.tif *.tiff *.jp2 *.img *.sid *.vrt *.png *.jpg);;"
    "ECW (*.ecw);;GeoTIFF/COG (*.tif *.tiff);;Todos (*)"
)
DXF_FILTER = "Desenho DXF (*.dxf);;Todos (*)"
SHAPEFILE_FILTER = "Shapefile (*.shp);;Todos (*)"


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
        self.ctx.command_line = self.cmdline

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

        self.properties_panel = PropertiesPanel(self.ctx, self)
        pdock = QDockWidget("Propriedades", self)
        pdock.setObjectName("dock_propriedades")
        pdock.setWidget(self.properties_panel)
        self.addDockWidget(Qt.RightDockWidgetArea, pdock)
        self.tabifyDockWidget(dock, pdock)
        pdock.hide()
        self.dock_properties = pdock

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
        self.lbl_sel = QLabel("", self)
        self.lbl_sel.setMinimumWidth(120)
        self.lbl_crs = QLabel("", self)
        for w in (self.lbl_coord, self.lbl_snap, self.lbl_sel,
                  self.lbl_scale, self.lbl_layer, self.lbl_crs):
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
        m_file.addAction(
            self._act("Importar &shapefile...", self.on_import_shapefile, "Ctrl+Shift+I")
        )
        m_file.addSeparator()
        m_file.addAction(self._act("Sai&r", self.close, "Ctrl+Q"))

        m_edit = self.menuBar().addMenu("&Editar")
        m_edit.addAction(self._act("&Desfazer", lambda: self.run("U"), "Ctrl+Z"))
        m_edit.addAction(self._act("&Refazer", lambda: self.run("REDO"), "Ctrl+Y"))

        # Sem atalhos de uma letra: no CAD as abreviacoes (L, PL, C, A...) sao
        # digitadas na linha de comando, e um QAction de tecla unica competiria
        # com a digitacao.
        m_draw = self.menuBar().addMenu("&Desenhar")
        m_draw.addAction(self._act("&Linha", lambda: self.run("LINE"), tip="alias: L"))
        m_draw.addAction(self._act("&Polilinha", lambda: self.run("PLINE"), tip="alias: PL"))
        m_draw.addAction(self._act("&Retangulo", lambda: self.run("RECT"), tip="alias: REC"))
        m_draw.addAction(self._act("&Circulo", lambda: self.run("CIRCLE"), tip="alias: C"))
        m_draw.addAction(self._act("&Arco (3 pontos)", lambda: self.run("ARC"), tip="alias: A"))
        m_draw.addAction(self._act("&Texto", lambda: self.run("TEXT"), tip="alias: T"))
        m_draw.addSeparator()
        m_draw.addAction(self._act("&Hachura...", lambda: self.run("HATCH"), tip="alias: H"))
        m_draw.addAction(self._act("Editar hac&hura...", lambda: self.run("HATCHEDIT"), tip="HE"))
        m_draw.addAction(self._act("&Regenerar hachuras", lambda: self.run("HATCHREGEN")))
        m_draw.addAction(
            self._act("&Desassociar hachura", lambda: self.run("HATCHDISASSOCIATE"))
        )
        m_draw.addSeparator()
        m_draw.addAction(self._act("&Carimbo configuravel...", lambda: self.run("CARIMBO")))
        m_draw.addAction(self._act("Editar car&imbo...", lambda: self.run("CARIMBOEDIT")))

        m_blocks = self.menuBar().addMenu("&Blocos")
        m_blocks.addAction(self._act("&Criar bloco...", lambda: self.run("BLOCK"), tip="alias: B"))
        m_blocks.addAction(
            self._act("&Inserir bloco...", lambda: self.run("INSERT"), tip="alias: I")
        )
        m_blocks.addAction(
            self._act("Gravar &WBLOCK...", lambda: self.run("WBLOCK"), tip="alias: W")
        )
        m_blocks.addAction(self._act("E&xplodir", lambda: self.run("EXPLODE"), tip="alias: X"))
        m_blocks.addSeparator()
        m_blocks.addAction(self._act("Biblioteca de &simbolos...", lambda: self.run("SIMBOLO")))
        m_blocks.addAction(self._act("Editar &atributos...", lambda: self.run("ATTEDIT")))
        m_blocks.addAction(self._act("Parametros &dinamicos...", lambda: self.run("DYNEDIT")))
        m_blocks.addAction(
            self._act("Escala a&notativa...", lambda: self.run("ESCALAANOTATIVA"))
        )

        m_dim = self.menuBar().addMenu("&Cotas")
        m_dim.addAction(self._act("&Linear", lambda: self.run("DIMLINEAR"), tip="alias: DLI"))
        m_dim.addAction(self._act("&Alinhada", lambda: self.run("DIMALIGNED"), tip="alias: DAL"))
        m_dim.addAction(self._act("&Rotacionada", lambda: self.run("DIMROTATED"), tip="alias: DRO"))
        m_dim.addAction(self._act("A&ngular", lambda: self.run("DIMANGULAR"), tip="alias: DAN"))
        m_dim.addSeparator()
        m_dim.addAction(self._act("&Raio", lambda: self.run("DIMRADIUS"), tip="alias: DRA"))
        m_dim.addAction(self._act("&Diametro", lambda: self.run("DIMDIAMETER"), tip="alias: DDI"))
        m_dim.addAction(
            self._act("Comprimento de &arco", lambda: self.run("DIMARC"), tip="alias: DAR")
        )
        m_dim.addAction(self._act("&Ordenada", lambda: self.run("DIMORDINATE"), tip="alias: DOR"))
        m_dim.addSeparator()
        m_dim.addAction(self._act("&Estilo de cotas...", self.on_dimension_style, tip="DIMSTYLE"))
        m_dim.addAction(
            self._act("&Reassociar cota", lambda: self.run("DIMREASSOCIATE"), tip="DRE")
        )
        m_dim.addAction(
            self._act("&Desassociar cota", lambda: self.run("DIMDISASSOCIATE"), tip="DDA")
        )

        m_mod = self.menuBar().addMenu("&Modificar")
        m_mod.addAction(self._act("&Mover", lambda: self.run("MOVE"), tip="alias: M"))
        m_mod.addAction(self._act("&Copiar", lambda: self.run("COPY"), tip="alias: CO"))
        m_mod.addAction(self._act("&Girar", lambda: self.run("ROTATE"), tip="alias: RO"))
        m_mod.addAction(self._act("&Espelhar", lambda: self.run("MIRROR"), tip="alias: MI"))
        m_mod.addAction(self._act("Escala&r", lambda: self.run("SCALE"), tip="alias: SC"))
        m_mod.addAction(self._act("&Paralela", lambda: self.run("OFFSET"), tip="alias: O"))
        m_mod.addSeparator()
        m_mod.addAction(self._act("&Aparar", lambda: self.run("TRIM"), tip="alias: TR"))
        m_mod.addAction(self._act("E&stender", lambda: self.run("EXTEND"), tip="alias: EX"))
        m_mod.addAction(self._act("Apa&gar", lambda: self.run("ERASE"), "Del", "alias: E"))
        m_mod.addSeparator()
        m_mod.addAction(
            self._act("Selecionar &tudo", lambda: self.run("SELTUDO"), "Ctrl+A")
        )
        m_mod.addAction(self._act("&Limpar selecao", lambda: self.run("SELNADA")))

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
        m_proj.addAction(self._act("&Dados do projeto...", lambda: self.run("DADOSPROJETO")))
        m_proj.addAction(self._act("&Sistema de coordenadas...", self.on_change_crs))
        m_proj.addAction(self._act("&Imagens carregadas...", self.on_raster_info))

        m_tools = self.menuBar().addMenu("&Ferramentas")
        a_props = self.dock_properties.toggleViewAction()
        a_props.setText("&Propriedades")
        a_props.setShortcut(QKeySequence("Ctrl+1"))
        m_tools.addAction(a_props)
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
        self._on_selection()

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

    def _on_selection(self) -> None:
        n = len(self.ctx.selection) if self.ctx.selection else 0
        self.lbl_sel.setText(f"{n} selecionado(s)" if n else "")

    def _on_document_replaced(self) -> None:
        if self.ctx.selection is not None:
            self.ctx.selection.changed.append(self._on_selection)
        self._on_selection()
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

    # ---------------- shapefile ----------------

    def on_import_shapefile(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Importar shapefile", "", SHAPEFILE_FILTER)
        if not path:
            return

        try:
            fields = shapefile_fields(path)
        except ShapefileImportError as exc:
            QMessageBox.critical(self, "Erro ao ler shapefile", str(exc))
            return

        default_layer = Path(path).stem.upper()
        layer, ok = QInputDialog.getText(
            self, "Importar shapefile", "Camada de destino:", text=default_layer
        )
        if not ok:
            return
        layer = layer.strip() or default_layer

        attribute_field = None
        if fields:
            options = ["(nenhum - tudo em uma camada)"] + fields
            choice, ok = QInputDialog.getItem(
                self,
                "Importar shapefile",
                "Separar em camadas pelo campo:",
                options,
                0,
                False,
            )
            if not ok:
                return
            if choice != options[0]:
                attribute_field = choice

        try:
            result = import_shapefile(self.ctx, path, layer=layer, attribute_field=attribute_field)
        except ShapefileImportError as exc:
            QMessageBox.critical(self, "Erro ao importar shapefile", str(exc))
            return

        self.ctx.zoom_extents()
        self.ctx.message(result.summary)

    # ---------------- projeto ----------------

    def on_change_crs(self) -> None:
        from ..core.titleblocks import update_title_blocks_from_project

        crs = CrsDialog.ask(self, self.ctx.doc.crs)
        if crs is None:
            return
        self.ctx.doc.crs = crs
        self.ctx.doc.project_attributes["CRS"] = crs.display
        update_title_blocks_from_project(self.ctx.doc)
        for layer in self.ctx.rasters:
            layer.set_project_crs(crs)
        self._update_title()
        self.ctx.refresh()
        self.ctx.message(f"CRS do projeto: {crs.display}")

    def on_dimension_style(self) -> None:
        if DimensionStyleDialog.edit(self.ctx.doc, self):
            self.ctx.message(f"Estilo de cotas {self.ctx.doc.dimension_style_name} atualizado")

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
