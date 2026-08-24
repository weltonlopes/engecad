"""Ponto de entrada do EngeCAD."""

from __future__ import annotations

import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QApplication

from . import __version__


def _apply_dark_palette(app: QApplication) -> None:
    """Paleta escura, que e o padrao em CAD (menos fadiga sobre ortofoto)."""
    from PySide6.QtGui import QColor

    app.setStyle("Fusion")
    p = QPalette()
    bg = QColor("#22262e")
    base = QColor("#1c1f26")
    text = QColor("#d7dbe3")
    p.setColor(QPalette.Window, bg)
    p.setColor(QPalette.WindowText, text)
    p.setColor(QPalette.Base, base)
    p.setColor(QPalette.AlternateBase, QColor("#272b34"))
    p.setColor(QPalette.Text, text)
    p.setColor(QPalette.Button, bg)
    p.setColor(QPalette.ButtonText, text)
    p.setColor(QPalette.ToolTipBase, base)
    p.setColor(QPalette.ToolTipText, text)
    p.setColor(QPalette.Highlight, QColor("#3d6fb5"))
    p.setColor(QPalette.HighlightedText, QColor("#ffffff"))
    p.setColor(QPalette.Disabled, QPalette.Text, QColor("#6b7280"))
    p.setColor(QPalette.Disabled, QPalette.ButtonText, QColor("#6b7280"))
    app.setPalette(p)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)

    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    app = QApplication(argv)
    app.setApplicationName("EngeCAD")
    app.setApplicationVersion(__version__)
    app.setOrganizationName("EngeCAD")
    _apply_dark_palette(app)

    from .io.dxf_io import DxfError, open_document
    from .ui.main_window import MainWindow

    win = MainWindow()
    win.show()

    # engecad desenho.dxf abre direto
    for arg in argv[1:]:
        if arg.lower().endswith(".dxf"):
            try:
                open_document(win.ctx, arg)
            except DxfError as exc:
                win.ctx.message(str(exc))
            break

    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
