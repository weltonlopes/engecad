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
from ..io.dwg_io import DwgError
from ..io.dwg_io import diagnose as diagnose_dwg
from ..io.dwg_io import export_document as export_dwg_document
from ..io.dwg_io import open_document as open_dwg_document
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
from .ribbon import RibbonBar
from .ribbon_icons import cad_icon

RASTER_FILTER = (
    "Imagens georreferenciadas (*.ecw *.tif *.tiff *.jp2 *.img *.sid *.vrt *.png *.jpg);;"
    "ECW (*.ecw);;GeoTIFF/COG (*.tif *.tiff);;Todos (*)"
)
DXF_FILTER = "Desenho DXF (*.dxf);;Todos (*)"
DWG_FILTER = "Desenho DWG (*.dwg);;Todos (*)"
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
        self._build_ribbon()
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
        for w in (
            self.lbl_coord,
            self.lbl_snap,
            self.lbl_sel,
            self.lbl_scale,
            self.lbl_layer,
            self.lbl_crs,
        ):
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
        m_file.addAction(self._act("Abrir DW&G...", self.on_open_dwg))
        m_file.addAction(self._act("&Salvar", self.on_save, "Ctrl+S"))
        m_file.addAction(self._act("Salvar &como...", self.on_save_as, "Ctrl+Shift+S"))
        m_file.addAction(self._act("Exportar &DWG...", self.on_export_dwg))
        m_file.addSeparator()
        m_file.addAction(self._act("&Importar imagem de fundo...", self.on_import_raster, "Ctrl+I"))
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
        m_draw.addAction(self._act("&Desassociar hachura", lambda: self.run("HATCHDISASSOCIATE")))
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
        m_blocks.addAction(self._act("Escala a&notativa...", lambda: self.run("ESCALAANOTATIVA")))

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
        m_mod.addAction(self._act("Selecionar &tudo", lambda: self.run("SELTUDO"), "Ctrl+A"))
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
        m_help.addAction(self._act("Diagnostico de D&WG...", self.on_diagnose_dwg))
        m_help.addAction(self._act("&Sobre o EngeCAD", self.on_about))

    def _build_ribbon(self) -> None:
        """Monta a Ribbon a partir dos mesmos comandos usados pela linha de comando."""
        # Ao substituir visualmente o menu, mantemos suas acoes registradas na
        # janela para que atalhos como Ctrl+S, F3 e Del continuem globais.
        menu_bar = self.menuBar()
        for menu_action in menu_bar.actions():
            menu = menu_action.menu()
            if menu is None:
                continue
            for action in menu.actions():
                if not action.isSeparator():
                    self.addAction(action)

        self.ribbon = RibbonBar(self)
        # QMainWindow reserva esta faixa acima de docks e area central; desse
        # modo a Ribbon ocupa a largura inteira, como em um CAD desktop.
        self.setMenuWidget(self.ribbon)
        self.ribbon_command_actions: dict[str, QAction] = {}

        labels = {
            "LINE": "Linha",
            "PLINE": "Polilinha",
            "RECT": "Retângulo",
            "CIRCLE": "Círculo",
            "ARC": "Arco",
            "TEXT": "Texto",
            "HATCH": "Hachura",
            "HATCHEDIT": "Editar hachura",
            "HATCHREGEN": "Regenerar",
            "HATCHDISASSOCIATE": "Desassociar",
            "CARIMBO": "Carimbo",
            "CARIMBOEDIT": "Editar carimbo",
            "BLOCK": "Criar bloco",
            "INSERT": "Inserir bloco",
            "WBLOCK": "Gravar WBLOCK",
            "EXPLODE": "Explodir",
            "ATTEDIT": "Atributos",
            "DYNEDIT": "Parâmetros",
            "SIMBOLO": "Símbolos",
            "ESCALAANOTATIVA": "Escala anotativa",
            "DADOSPROJETO": "Dados do projeto",
            "DIMLINEAR": "Linear",
            "DIMALIGNED": "Alinhada",
            "DIMROTATED": "Rotacionada",
            "DIMHORIZONTAL": "Horizontal",
            "DIMVERTICAL": "Vertical",
            "DIMANGULAR": "Angular",
            "DIMRADIUS": "Raio",
            "DIMDIAMETER": "Diâmetro",
            "DIMARC": "Compr. de arco",
            "DIMORDINATE": "Ordenada",
            "DIMSTYLE": "Estilo de cotas",
            "DIMREASSOCIATE": "Reassociar",
            "DIMDISASSOCIATE": "Desassociar",
            "DIMREGEN": "Regenerar cotas",
            "MOVE": "Mover",
            "COPY": "Copiar",
            "ROTATE": "Girar",
            "MIRROR": "Espelhar",
            "SCALE": "Escalar",
            "OFFSET": "Paralela",
            "TRIM": "Aparar",
            "EXTEND": "Estender",
            "ERASE": "Apagar",
            "SELTUDO": "Selecionar tudo",
            "SELNADA": "Limpar seleção",
            "DIST": "Distância",
            "AREA": "Área",
            "ZE": "Enquadrar tudo",
            "ZOOM": "Zoom por fator",
            "ESCALA": "Escala da vista",
            "PAN": "Panorâmica",
            "GRADE": "Grade",
            "U": "Desfazer",
            "REDO": "Refazer",
            "CAMADA": "Camada corrente",
            "OSNAP": "Snap ao objeto",
            "AJUDA": "Comandos",
        }

        for definition in self.ctx.registry.definitions():
            name = definition.name
            action = QAction(cad_icon(name), labels.get(name, name.title()), self)
            action.setProperty("command", name)
            aliases = ", ".join(definition.aliases)
            tip = f"{definition.description}\nComando: {name}"
            if aliases:
                tip += f"  •  Atalhos: {aliases}"
            action.setToolTip(tip)
            action.setStatusTip(definition.description)
            if definition.interactive or name in {"GRADE", "OSNAP"}:
                action.setCheckable(True)
            if name == "GRADE":
                action.setChecked(self.canvas.show_grid)
            elif name == "OSNAP":
                action.setChecked(self.ctx.snap.active)
            if name == "DIMSTYLE":
                action.triggered.connect(lambda _checked=False: self.on_dimension_style())
            else:
                action.triggered.connect(
                    lambda _checked=False, command=name: self._run_ribbon_command(command)
                )
            self.ribbon_command_actions[name] = action

        window_actions = self._ribbon_window_actions()
        for key in ("NEW", "OPEN", "SAVE", "U", "REDO"):
            action = window_actions.get(key) or self.ribbon_command_actions[key]
            self.ribbon.add_quick_action(action)

        layout = (
            (
                "Início",
                (
                    (
                        "Arquivo",
                        (("@NEW", True), ("@OPEN", False), ("@SAVE", False), ("@SAVE_AS", False)),
                    ),
                    (
                        "Desenhar",
                        (
                            ("LINE", True),
                            ("PLINE", False),
                            ("RECT", False),
                            ("CIRCLE", False),
                            ("ARC", False),
                            ("TEXT", False),
                        ),
                    ),
                    (
                        "Modificar",
                        (
                            ("MOVE", True),
                            ("COPY", False),
                            ("ROTATE", False),
                            ("MIRROR", False),
                            ("SCALE", False),
                            ("OFFSET", False),
                            ("TRIM", False),
                            ("EXTEND", False),
                            ("ERASE", False),
                        ),
                    ),
                    ("Seleção", (("SELTUDO", True), ("SELNADA", False))),
                    ("Organizar", (("CAMADA", True), ("U", False), ("REDO", False))),
                ),
            ),
            (
                "Inserir",
                (
                    (
                        "Bloco",
                        (
                            ("BLOCK", True),
                            ("INSERT", True),
                            ("WBLOCK", False),
                            ("EXPLODE", False),
                            ("ATTEDIT", False),
                            ("DYNEDIT", False),
                        ),
                    ),
                    ("Biblioteca", (("SIMBOLO", True), ("ESCALAANOTATIVA", False))),
                    (
                        "Projeto",
                        (("DADOSPROJETO", True), ("CARIMBO", False), ("CARIMBOEDIT", False)),
                    ),
                    (
                        "Referência",
                        (("@IMPORT_RASTER", True), ("@IMPORT_SHP", False), ("@RASTER_INFO", False)),
                    ),
                ),
            ),
            (
                "Anotar",
                (
                    (
                        "Cotas lineares",
                        (
                            ("DIMLINEAR", True),
                            ("DIMALIGNED", True),
                            ("DIMROTATED", False),
                            ("DIMHORIZONTAL", False),
                            ("DIMVERTICAL", False),
                        ),
                    ),
                    (
                        "Outras cotas",
                        (
                            ("DIMANGULAR", True),
                            ("DIMRADIUS", False),
                            ("DIMDIAMETER", False),
                            ("DIMARC", False),
                            ("DIMORDINATE", False),
                        ),
                    ),
                    (
                        "Gerenciar cotas",
                        (
                            ("DIMSTYLE", True),
                            ("DIMREASSOCIATE", False),
                            ("DIMDISASSOCIATE", False),
                            ("DIMREGEN", False),
                        ),
                    ),
                    (
                        "Detalhamento",
                        (
                            ("HATCH", True),
                            ("HATCHEDIT", False),
                            ("HATCHREGEN", False),
                            ("HATCHDISASSOCIATE", False),
                        ),
                    ),
                ),
            ),
            (
                "Vista",
                (
                    ("Navegar", (("ZE", True), ("ZOOM", False), ("PAN", False))),
                    ("Escala e grade", (("ESCALA", True), ("GRADE", False), ("OSNAP", False))),
                    ("Consultar", (("DIST", True), ("AREA", True))),
                    ("Paletas", (("@PROPERTIES", True), ("@LAYERS", False), ("@CONSOLE", False))),
                ),
            ),
            (
                "Gerenciar",
                (
                    ("Automação", (("@SCRIPT", True), ("@CONSOLE", False))),
                    (
                        "Coordenadas",
                        (("@CRS", True), ("@RASTER_INFO", False), ("@RASTER_DIAG", False)),
                    ),
                    ("Ajuda", (("AJUDA", True), ("@ABOUT", False))),
                    ("Aplicativo", (("@EXIT", True),)),
                ),
            ),
        )

        for tab_title, panels in layout:
            page = self.ribbon.add_tab(tab_title)
            for panel_title, items in panels:
                panel = self.ribbon.add_panel(page, panel_title)
                for key, large in items:
                    action = (
                        window_actions[key[1:]]
                        if key.startswith("@")
                        else self.ribbon_command_actions[key]
                    )
                    self.ribbon.add_action(panel, action, large=large)

        # Extensoes futuras do registro nunca ficam invisiveis: comandos que
        # ainda nao tenham posicao explicita entram na ultima guia.
        remaining = set(self.ribbon_command_actions) - self.ribbon.command_names
        if remaining:
            panel = self.ribbon.add_panel(page, "Outros comandos")
            for name in sorted(remaining):
                self.ribbon.add_action(panel, self.ribbon_command_actions[name])

        toggle_ribbon = QAction("Minimizar/expandir Ribbon", self)
        toggle_ribbon.setShortcut(QKeySequence("Ctrl+F1"))
        toggle_ribbon.triggered.connect(self.ribbon.toggle_minimized)
        self.addAction(toggle_ribbon)
        self.action_toggle_ribbon = toggle_ribbon

        # Os menus continuam construidos (e seus atalhos ativos), mas a Ribbon
        # passa a ocupar a faixa de menu e evita uma segunda navegacao redundante.

    def _ribbon_window_actions(self) -> dict[str, QAction]:
        specs = {
            "NEW": ("Novo", self.on_new, "Criar um novo desenho"),
            "OPEN": ("Abrir", self.on_open, "Abrir desenho DXF"),
            "SAVE": ("Salvar", self.on_save, "Salvar o desenho atual"),
            "SAVE_AS": ("Salvar como", self.on_save_as, "Salvar em outro arquivo"),
            "IMPORT_RASTER": ("Imagem", self.on_import_raster, "Importar imagem georreferenciada"),
            "IMPORT_SHP": ("Shapefile", self.on_import_shapefile, "Importar arquivo shapefile"),
            "RASTER_INFO": ("Imagens", self.on_raster_info, "Ver imagens carregadas"),
            "PROPERTIES": (
                "Propriedades",
                self.dock_properties.toggleViewAction().trigger,
                "Mostrar ou ocultar propriedades",
            ),
            "LAYERS": (
                "Camadas",
                self.dock_layers.toggleViewAction().trigger,
                "Mostrar ou ocultar camadas",
            ),
            "CONSOLE": (
                "Console Python",
                self.dock_console.toggleViewAction().trigger,
                "Mostrar ou ocultar o console Python",
            ),
            "SCRIPT": ("Executar script", self.on_run_script, "Executar arquivo Python"),
            "CRS": ("Sistema de coordenadas", self.on_change_crs, "Alterar o CRS do projeto"),
            "RASTER_DIAG": (
                "Diagnóstico raster",
                self.on_diagnose,
                "Diagnosticar suporte a raster",
            ),
            "ABOUT": ("Sobre", self.on_about, "Sobre o EngeCAD"),
            "EXIT": ("Sair", self.close, "Fechar o EngeCAD"),
        }
        icon_keys = {
            "PROPERTIES": "ATTEDIT",
            "LAYERS": "CAMADA",
            "CONSOLE": "SCRIPT",
            "SCRIPT": "SCRIPT",
            "CRS": "GLOBE",
            "RASTER_INFO": "IMPORT_RASTER",
            "RASTER_DIAG": "DIAG",
            "ABOUT": "AJUDA",
            "EXIT": "EXIT",
        }
        actions = {}
        for key, (label, slot, tip) in specs.items():
            docks = {
                "PROPERTIES": self.dock_properties,
                "LAYERS": self.dock_layers,
                "CONSOLE": self.dock_console,
            }
            if key in docks:
                action = docks[key].toggleViewAction()
                action.setText(label)
                action.setIcon(cad_icon(icon_keys.get(key, key)))
            else:
                action = QAction(cad_icon(icon_keys.get(key, key)), label, self)
                action.triggered.connect(slot)
            action.setProperty("actionId", key)
            action.setToolTip(tip)
            action.setStatusTip(tip)
            actions[key] = action
        return actions

    def _run_ribbon_command(self, name: str) -> None:
        if name == "ZOOM":
            factor, ok = QInputDialog.getDouble(
                self, "Zoom", "Fator de aproximação:", 1.25, 0.01, 100.0, 2
            )
            if not ok:
                return
            self.run(name, str(factor))
            return
        if name == "ESCALA":
            current = max(1, round(self.ctx.viewport.scale_denominator()))
            denominator, ok = QInputDialog.getInt(
                self, "Escala da vista", "Denominador (1:N):", current, 1, 100_000_000
            )
            if not ok:
                return
            self.run(name, str(denominator))
            return
        if name == "PAN":
            center = self.ctx.viewport.center
            coordinate, ok = QInputDialog.getText(
                self,
                "Centralizar vista",
                "Coordenada E,N:",
                text=f"{center.x:.3f},{center.y:.3f}",
            )
            if not ok or not coordinate.strip():
                return
            self.run(name, coordinate)
            return
        if name == "CAMADA":
            self.dock_layers.show()
            self.dock_layers.raise_()
            self.ctx.message(f"Camada corrente: {self.ctx.doc.current_layer}")
            return
        self.run(name)
        if name == "GRADE":
            self.ribbon_command_actions[name].setChecked(self.canvas.show_grid)
        elif name == "OSNAP":
            self.ribbon_command_actions[name].setChecked(self.ctx.snap.active)

    def _sync_ribbon_tool(self, tool) -> None:
        active = "" if tool is None or tool.is_idle else getattr(tool, "name", "")
        for name, action in self.ribbon_command_actions.items():
            if action.isCheckable() and name not in {"GRADE", "OSNAP"}:
                action.setChecked(name == active)

    def _connect(self) -> None:
        self.canvas.coordinateMoved.connect(self._on_coordinate)
        self.canvas.snapChanged.connect(self._on_snap)
        self.canvas.viewChanged.connect(self._on_view)
        self.ctx.statusMessage.connect(self._on_message)
        self.ctx.documentChanged.connect(self._update_title)
        self.ctx.documentReplaced.connect(self._on_document_replaced)
        self.ctx.viewChanged.connect(self._on_view)
        self.ctx.toolChanged.connect(self._sync_ribbon_tool)
        self.ctx.layerManagerRequested.connect(self._show_layer_manager)
        self._on_view()
        self._on_document_replaced()
        self._on_selection()

    def _show_layer_manager(self) -> None:
        self.dock_layers.show()
        self.dock_layers.raise_()
        self.layer_panel.setFocus()

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
        if hasattr(self, "ribbon_command_actions"):
            self.ribbon_command_actions["GRADE"].setChecked(self.canvas.show_grid)
            self.ribbon_command_actions["OSNAP"].setChecked(self.ctx.snap.active)
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

    def on_open_dwg(self) -> None:
        if not self._confirm_discard():
            return
        path, _ = QFileDialog.getOpenFileName(self, "Abrir DWG", "", DWG_FILTER)
        if not path:
            return
        try:
            open_dwg_document(self.ctx, path)
        except DwgError as exc:
            QMessageBox.critical(self, "Erro ao abrir DWG", str(exc))
            return
        self.ctx.message(f"Aberto (convertido de DWG): {Path(path).name}")

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

    def on_export_dwg(self) -> None:
        default_name = self.ctx.doc.path.stem if self.ctx.doc.path else "desenho"
        path, _ = QFileDialog.getSaveFileName(
            self, "Exportar DWG", default_name, DWG_FILTER
        )
        if not path:
            return
        try:
            p = export_dwg_document(self.ctx, path)
        except DwgError as exc:
            QMessageBox.critical(self, "Erro ao exportar DWG", str(exc))
            return
        self.ctx.message(f"Exportado: {p.name}")

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

    def on_diagnose_dwg(self) -> None:
        QMessageBox.information(self, "Diagnostico de DWG", diagnose_dwg())

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
