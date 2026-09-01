"""Barra de ferramentas Ribbon, inspirada na organizacao do AutoCAD.

O componente nao conhece comandos do EngeCAD: recebe QActions prontas da
janela principal. Assim menus, atalhos, linha de comando e Ribbon continuam
passando pelo mesmo registro central.
"""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTabBar,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class RibbonPanel(QFrame):
    """Painel rotulado com comandos grandes e compactos em ate tres linhas."""

    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbonPanel")
        self._large_column = 0
        self._compact_index = 0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(4, 3, 4, 1)
        outer.setSpacing(0)
        self.grid = QGridLayout()
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setHorizontalSpacing(2)
        self.grid.setVerticalSpacing(1)
        outer.addLayout(self.grid, 1)

        label = QLabel(title, self)
        label.setObjectName("ribbonPanelTitle")
        label.setAlignment(Qt.AlignCenter)
        outer.addWidget(label)

    def add_action(self, action: QAction, *, large: bool = False) -> QToolButton:
        button = QToolButton(self)
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setCursor(Qt.PointingHandCursor)
        button.setProperty("ribbonCommand", action.property("command"))
        button.setProperty("ribbonAction", action.property("actionId"))

        if large:
            button.setProperty("ribbonSize", "large")
            button.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
            button.setIconSize(QSize(42, 42))
            button.setMinimumSize(68, 84)
            button.setMaximumHeight(88)
            column = self._large_column
            self.grid.addWidget(button, 0, column, 3, 1)
            self._large_column += 1
        else:
            button.setProperty("ribbonSize", "compact")
            button.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
            button.setIconSize(QSize(22, 22))
            button.setMinimumSize(102, 27)
            row = self._compact_index % 3
            column = self._large_column + self._compact_index // 3
            self.grid.addWidget(button, row, column)
            self._compact_index += 1
        return button


class RibbonPage(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbonPage")
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(True)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.viewport().setAutoFillBackground(False)

        self.content = QWidget(self)
        self.content.setObjectName("ribbonPageContent")
        self.layout_ = QHBoxLayout(self.content)
        self.layout_.setContentsMargins(3, 2, 3, 2)
        self.layout_.setSpacing(2)
        self.layout_.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.setWidget(self.content)

    def add_panel(self, panel: RibbonPanel) -> None:
        self.layout_.addWidget(panel)


class RibbonBar(QFrame):
    """Ribbon com guia ativa, acesso rapido e modo minimizado (Ctrl+F1)."""

    currentTabChanged = Signal(str)

    EXPANDED_HEIGHT = 160
    COLLAPSED_HEIGHT = 35

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ribbon")
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self.EXPANDED_HEIGHT)
        self._minimized = False
        self._tab_titles: list[str] = []
        self._buttons: list[QToolButton] = []

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        header = QWidget(self)
        header.setObjectName("ribbonHeader")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(5, 0, 5, 0)
        header_layout.setSpacing(2)

        self.app_button = QToolButton(header)
        self.app_button.setObjectName("ribbonAppButton")
        self.app_button.setText("E")
        self.app_button.setToolTip("EngeCAD — Arquivo")
        self.app_button.setFixedSize(29, 29)
        self.app_button.clicked.connect(lambda: self.tabs.setCurrentIndex(0))
        header_layout.addWidget(self.app_button)

        self.quick_access = QWidget(header)
        self.quick_access.setObjectName("quickAccess")
        self.quick_layout = QHBoxLayout(self.quick_access)
        self.quick_layout.setContentsMargins(2, 0, 7, 0)
        self.quick_layout.setSpacing(0)
        header_layout.addWidget(self.quick_access)

        self.tabs = QTabBar(header)
        self.tabs.setObjectName("ribbonTabs")
        self.tabs.setDrawBase(False)
        self.tabs.setExpanding(False)
        self.tabs.setUsesScrollButtons(True)
        self.tabs.currentChanged.connect(self._select_tab)
        self.tabs.tabBarDoubleClicked.connect(lambda _i: self.toggle_minimized())
        header_layout.addWidget(self.tabs, 1)

        workspace = QLabel("DESENHO 2D", header)
        workspace.setObjectName("ribbonWorkspace")
        workspace.setToolTip("Espaço de trabalho atual")
        header_layout.addWidget(workspace)

        self.minimize_button = QToolButton(header)
        self.minimize_button.setObjectName("ribbonMinimize")
        self.minimize_button.setText("⌃")
        self.minimize_button.setToolTip("Minimizar a Ribbon (Ctrl+F1)")
        self.minimize_button.setFixedSize(27, 27)
        self.minimize_button.clicked.connect(self.toggle_minimized)
        header_layout.addWidget(self.minimize_button)
        root.addWidget(header)

        self.pages = QStackedWidget(self)
        self.pages.setObjectName("ribbonPages")
        root.addWidget(self.pages, 1)

        self.setStyleSheet(_STYLE)

    @property
    def minimized(self) -> bool:
        return self._minimized

    @property
    def command_names(self) -> set[str]:
        return {
            str(button.property("ribbonCommand"))
            for button in self._buttons
            if button.property("ribbonCommand")
        }

    @property
    def action_ids(self) -> set[str]:
        return {
            str(button.property("ribbonAction"))
            for button in self._buttons
            if button.property("ribbonAction")
        }

    def add_quick_action(self, action: QAction) -> QToolButton:
        button = QToolButton(self.quick_access)
        button.setDefaultAction(action)
        button.setAutoRaise(True)
        button.setToolButtonStyle(Qt.ToolButtonIconOnly)
        button.setIconSize(QSize(18, 18))
        button.setFixedSize(27, 27)
        self.quick_layout.addWidget(button)
        return button

    def add_tab(self, title: str) -> RibbonPage:
        page = RibbonPage(self.pages)
        self._tab_titles.append(title)
        self.tabs.addTab(title)
        self.pages.addWidget(page)
        return page

    def add_panel(self, page: RibbonPage, title: str) -> RibbonPanel:
        panel = RibbonPanel(title, page.content)
        page.add_panel(panel)
        return panel

    def add_action(self, panel: RibbonPanel, action: QAction, *, large=False) -> QToolButton:
        button = panel.add_action(action, large=large)
        self._buttons.append(button)
        return button

    def button_for_command(self, command: str) -> QToolButton | None:
        command = command.upper()
        return next(
            (b for b in self._buttons if b.property("ribbonCommand") == command),
            None,
        )

    def set_minimized(self, minimized: bool) -> None:
        self._minimized = bool(minimized)
        self.pages.setVisible(not self._minimized)
        self.setFixedHeight(self.COLLAPSED_HEIGHT if self._minimized else self.EXPANDED_HEIGHT)
        self.minimize_button.setText("⌄" if self._minimized else "⌃")
        self.minimize_button.setToolTip(
            ("Expandir" if self._minimized else "Minimizar") + " a Ribbon (Ctrl+F1)"
        )

    def toggle_minimized(self) -> None:
        self.set_minimized(not self._minimized)

    def _select_tab(self, index: int) -> None:
        if index < 0 or index >= self.pages.count():
            return
        self.pages.setCurrentIndex(index)
        if self._minimized:
            self.set_minimized(False)
        self.currentTabChanged.emit(self._tab_titles[index])


_STYLE = """
QFrame#ribbon {
    background: #252a32;
    border: 0;
    border-bottom: 1px solid #101319;
}
QWidget#ribbonHeader { background: #1d2229; border-bottom: 1px solid #343b45; }
QToolButton#ribbonAppButton {
    color: white; background: #1479b8; border: 1px solid #3299d4;
    border-radius: 3px; font-size: 16px; font-weight: 700;
}
QToolButton#ribbonAppButton:hover { background: #2295d4; }
QWidget#quickAccess { border-right: 1px solid #3b424c; }
QWidget#quickAccess QToolButton, QToolButton#ribbonMinimize {
    background: transparent; border: 1px solid transparent; border-radius: 3px;
}
QWidget#quickAccess QToolButton:hover, QToolButton#ribbonMinimize:hover {
    background: #343c46; border-color: #52606e;
}
QTabBar#ribbonTabs::tab {
    min-width: 66px; height: 28px; padding: 0 10px; margin: 0 1px;
    color: #cbd3dc; background: transparent; border: 0;
}
QTabBar#ribbonTabs::tab:hover { background: #303841; color: white; }
QTabBar#ribbonTabs::tab:selected {
    color: white; background: #2a3038; border-bottom: 3px solid #2ea3e6;
}
QLabel#ribbonWorkspace {
    color: #93a0ae; font-size: 9px; font-weight: 700; padding: 0 8px;
}
QStackedWidget#ribbonPages, QScrollArea#ribbonPage, QWidget#ribbonPageContent {
    background: #252a32; border: 0;
}
QFrame#ribbonPanel {
    background: #282e37; border: 0; border-right: 1px solid #414954;
    margin-right: 1px;
}
QLabel#ribbonPanelTitle {
    color: #9ba7b5; font-size: 9px; padding-top: 1px; min-height: 14px;
}
QFrame#ribbonPanel QToolButton {
    color: #e7ebf0; background: transparent; border: 1px solid transparent;
    border-radius: 3px; padding: 2px 5px;
}
QFrame#ribbonPanel QToolButton:hover {
    color: white; background: #354451; border-color: #517089;
}
QFrame#ribbonPanel QToolButton:pressed, QFrame#ribbonPanel QToolButton:checked {
    color: white; background: #175d89; border-color: #3ba3e4;
}
QFrame#ribbonPanel QToolButton[ribbonSize="large"] { font-size: 10px; }
QFrame#ribbonPanel QToolButton[ribbonSize="compact"] { text-align: left; font-size: 10px; }
QScrollBar:horizontal {
    background: #1f242b; height: 8px; margin: 0;
}
QScrollBar::handle:horizontal { background: #56616d; min-width: 30px; border-radius: 3px; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; }
"""
