"""Icones vetoriais leves para a Ribbon.

Os desenhos sao feitos em coordenadas normalizadas e rasterizados pelo Qt nas
resolucoes usuais. Isso mantem a interface autocontida e nitida em telas HiDPI.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap

INK = QColor("#e8edf4")
MUTED = QColor("#9ba8b7")
ACCENT = QColor("#38a9f0")
ACCENT_2 = QColor("#f0a43b")


def _pen(color=INK, width=4.0, style=Qt.SolidLine) -> QPen:
    pen = QPen(color, width, style)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    return pen


def _line(p: QPainter, x1, y1, x2, y2, color=INK, width=4.0, style=Qt.SolidLine):
    p.setPen(_pen(color, width, style))
    p.drawLine(QPointF(x1, y1), QPointF(x2, y2))


def _arrow(p: QPainter, a: QPointF, b: QPointF, color=INK, width=4.0):
    _line(p, a.x(), a.y(), b.x(), b.y(), color, width)
    angle = math.atan2(b.y() - a.y(), b.x() - a.x())
    for delta in (2.55, -2.55):
        q = QPointF(b.x() + 8 * math.cos(angle + delta), b.y() + 8 * math.sin(angle + delta))
        _line(p, b.x(), b.y(), q.x(), q.y(), color, width)


def _badge(p: QPainter, text: str):
    if not text:
        return
    r = QRectF(37, 41, 24, 19)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#1674ae"))
    p.drawRoundedRect(r, 4, 4)
    font = QFont("Arial", 8)
    font.setBold(True)
    p.setFont(font)
    p.setPen(QColor("#ffffff"))
    p.drawText(r, Qt.AlignCenter, text[:4])


def _draw_geometry(p: QPainter, key: str):
    if key == "LINE":
        _line(p, 11, 49, 52, 14, ACCENT, 5)
        for x, y in ((11, 49), (52, 14)):
            p.setPen(_pen(INK, 3))
            p.setBrush(QColor("#252a32"))
            p.drawEllipse(QPointF(x, y), 5, 5)
    elif key == "PLINE":
        p.setPen(_pen(ACCENT, 5))
        p.drawPolyline([QPointF(8, 47), QPointF(22, 18), QPointF(39, 38), QPointF(56, 12)])
        p.setBrush(INK)
        p.setPen(Qt.NoPen)
        for q in ((8, 47), (22, 18), (39, 38), (56, 12)):
            p.drawRect(QRectF(q[0] - 3, q[1] - 3, 6, 6))
    elif key == "RECT":
        p.setPen(_pen(ACCENT, 5))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(10, 14, 43, 34))
        p.setBrush(INK)
        p.setPen(Qt.NoPen)
        p.drawRect(QRectF(7, 44, 8, 8))
        p.drawRect(QRectF(49, 10, 8, 8))
    elif key == "CIRCLE":
        p.setPen(_pen(ACCENT, 5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(31, 31), 21, 21)
        _line(p, 31, 31, 47, 19, MUTED, 3)
        p.setBrush(INK)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(31, 31), 3, 3)
    elif key == "ARC":
        p.setPen(_pen(ACCENT, 5))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(9, 10, 46, 46), 25 * 16, 245 * 16)
        p.setBrush(INK)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(12, 42), 4, 4)
        p.drawEllipse(QPointF(51, 20), 4, 4)
    elif key == "TEXT":
        font = QFont("Arial", 39)
        font.setBold(True)
        p.setFont(font)
        p.setPen(ACCENT)
        p.drawText(QRectF(8, 5, 49, 51), Qt.AlignCenter, "A")
        _line(p, 11, 53, 53, 53, MUTED, 3)


def _draw_modify(p: QPainter, key: str):
    if key == "MOVE":
        _arrow(p, QPointF(9, 32), QPointF(55, 32), ACCENT)
        _arrow(p, QPointF(32, 55), QPointF(32, 9), ACCENT)
    elif key == "COPY":
        p.setPen(_pen(MUTED, 4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(9, 18, 31, 32))
        p.setPen(_pen(ACCENT, 4))
        p.drawRect(QRectF(23, 9, 31, 32))
    elif key == "ROTATE":
        p.setPen(_pen(ACCENT, 5))
        p.setBrush(Qt.NoBrush)
        p.drawArc(QRectF(11, 11, 42, 42), 35 * 16, 285 * 16)
        p.setBrush(ACCENT)
        p.setPen(Qt.NoPen)
        path = QPainterPath(QPointF(51, 9))
        path.lineTo(55, 25)
        path.lineTo(39, 19)
        path.closeSubpath()
        p.drawPath(path)
    elif key == "MIRROR":
        _line(p, 32, 7, 32, 57, MUTED, 3, Qt.DashLine)
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(Qt.NoBrush)
        p.drawPolygon([QPointF(8, 49), QPointF(26, 14), QPointF(26, 49)])
        p.setPen(_pen(INK, 4))
        p.drawPolygon([QPointF(56, 49), QPointF(38, 14), QPointF(38, 49)])
    elif key == "SCALE":
        p.setPen(_pen(MUTED, 3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(12, 26, 25, 25))
        p.setPen(_pen(ACCENT, 4))
        p.drawRect(QRectF(23, 11, 32, 32))
        _arrow(p, QPointF(20, 45), QPointF(51, 14), ACCENT, 3)
    elif key == "OFFSET":
        p.setPen(_pen(ACCENT, 5))
        p.drawPolyline([QPointF(8, 44), QPointF(24, 17), QPointF(55, 35)])
        p.setPen(_pen(INK, 4))
        p.drawPolyline([QPointF(12, 55), QPointF(28, 29), QPointF(57, 46)])
    elif key in {"TRIM", "EXTEND"}:
        _line(p, 8, 18, 56, 45, MUTED, 4)
        _line(p, 9, 48, 52, 13, ACCENT, 5)
        if key == "TRIM":
            p.setPen(_pen(ACCENT_2, 4))
            p.drawLine(QPointF(13, 52), QPointF(28, 40))
        else:
            _line(p, 42, 21, 57, 8, ACCENT_2, 4, Qt.DashLine)
    elif key == "ERASE":
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(QColor("#314b5b"))
        p.drawRoundedRect(QRectF(14, 14, 37, 34), 5, 5)
        _line(p, 19, 44, 48, 17, INK, 3)


def _draw_dimension(p: QPainter, key: str):
    if key in {"DIMRADIUS", "DIMDIAMETER", "DIMARC"}:
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(31, 31), 20, 20)
        if key == "DIMRADIUS":
            _arrow(p, QPointF(31, 31), QPointF(47, 19), INK, 3)
        elif key == "DIMDIAMETER":
            _arrow(p, QPointF(15, 43), QPointF(47, 19), INK, 3)
        else:
            p.setPen(_pen(INK, 3))
            p.drawArc(QRectF(7, 7, 50, 50), 20 * 16, 140 * 16)
    elif key == "DIMANGULAR":
        _line(p, 12, 50, 31, 25, ACCENT, 4)
        _line(p, 31, 25, 55, 47, ACCENT, 4)
        p.setPen(_pen(INK, 3))
        p.drawArc(QRectF(17, 24, 29, 29), 38 * 16, 100 * 16)
    else:
        _line(p, 11, 17, 11, 51, MUTED, 3)
        _line(p, 53, 17, 53, 51, MUTED, 3)
        _arrow(p, QPointF(13, 30), QPointF(51, 30), ACCENT, 3)
    badge = key.removeprefix("DIM")[:2]
    _badge(p, badge)


def _draw_view(p: QPainter, key: str):
    if key in {"ZOOM", "ZE"}:
        p.setPen(_pen(ACCENT, 5))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(27, 26), 17, 17)
        _line(p, 39, 39, 55, 55, INK, 6)
        if key == "ZE":
            _badge(p, "E")
    elif key == "PAN":
        _arrow(p, QPointF(8, 32), QPointF(56, 32), ACCENT, 3)
        _arrow(p, QPointF(32, 56), QPointF(32, 8), ACCENT, 3)
        p.setPen(_pen(INK, 4))
        p.setBrush(QColor("#252a32"))
        p.drawEllipse(QPointF(32, 32), 8, 8)
    elif key in {"GRADE", "GRID"}:
        p.setPen(_pen(ACCENT, 3))
        for q in (15, 27, 39, 51):
            _line(p, q, 8, q, 56, ACCENT, 2)
            _line(p, 8, q, 56, q, ACCENT, 2)
    elif key == "OSNAP":
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(10, 10, 44, 44))
        p.setBrush(ACCENT_2)
        p.setPen(Qt.NoPen)
        p.drawEllipse(QPointF(32, 32), 7, 7)
    elif key == "ESCALA":
        _line(p, 8, 42, 56, 42, ACCENT, 5)
        for x, h in ((10, 13), (22, 8), (34, 13), (46, 8), (56, 13)):
            _line(p, x, 42, x, 42 - h, INK, 3)


def _draw_file(p: QPainter, key: str):
    if key in {"OPEN", "IMPORT_RASTER", "IMPORT_SHP"}:
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(QColor("#2d4454"))
        p.drawPath(QPainterPath(QPointF(7, 22)))
        p.drawRoundedRect(QRectF(7, 20, 50, 33), 4, 4)
        _line(p, 10, 20, 25, 9, INK, 4)
        _line(p, 25, 9, 37, 20, INK, 4)
        _badge(p, "IMG" if key == "IMPORT_RASTER" else "SHP" if key == "IMPORT_SHP" else "")
    elif key in {"SAVE", "SAVE_AS", "WBLOCK"}:
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(QColor("#2d4454"))
        p.drawRoundedRect(QRectF(9, 8, 46, 48), 4, 4)
        p.setBrush(INK)
        p.drawRect(QRectF(18, 10, 28, 15))
        p.setBrush(QColor("#252a32"))
        p.drawRect(QRectF(18, 36, 28, 18))
        _badge(p, "AS" if key == "SAVE_AS" else "")
    elif key == "NEW":
        p.setPen(_pen(INK, 4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(15, 7, 34, 49))
        _line(p, 38, 7, 49, 18, MUTED, 3)
        _line(p, 38, 7, 38, 19, MUTED, 3)
        _line(p, 38, 19, 49, 19, MUTED, 3)
        _line(p, 22, 38, 43, 38, ACCENT, 4)
        _line(p, 32, 28, 32, 48, ACCENT, 4)


def _draw_block(p: QPainter, key: str):
    if key == "EXPLODE":
        for r in (
            QRectF(7, 9, 18, 18),
            QRectF(39, 8, 15, 15),
            QRectF(12, 40, 15, 15),
            QRectF(40, 38, 18, 18),
        ):
            p.setPen(_pen(ACCENT, 3))
            p.setBrush(Qt.NoBrush)
            p.drawRect(r)
        _line(p, 31, 31, 18, 18, ACCENT_2, 3)
        _line(p, 31, 31, 47, 46, ACCENT_2, 3)
    else:
        p.setPen(_pen(ACCENT, 4))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(10, 10, 22, 22))
        p.drawRect(QRectF(31, 31, 22, 22))
        p.setPen(_pen(INK, 3))
        p.drawRect(QRectF(25, 17, 22, 22))
        _badge(p, {"INSERT": "+", "ATTEDIT": "A", "DYNEDIT": "D", "SIMBOLO": "S"}.get(key, ""))


def _draw_generic(p: QPainter, key: str):
    short = {
        "U": "↶",
        "REDO": "↷",
        "SELTUDO": "ALL",
        "SELNADA": "Ø",
        "DIST": "↔",
        "AREA": "m²",
        "CAMADA": "L",
        "AJUDA": "?",
        "HATCH": "H",
        "HATCHEDIT": "HE",
        "CARIMBO": "A4",
    }.get(key, key[:3])
    if key.startswith("HATCH"):
        p.setPen(_pen(ACCENT, 3))
        p.setBrush(Qt.NoBrush)
        p.drawRect(QRectF(10, 10, 44, 44))
        p.save()
        p.setClipRect(QRectF(10, 10, 44, 44))
        for x in range(-30, 70, 11):
            _line(p, x, 55, x + 45, 10, MUTED, 2)
        p.restore()
        _badge(p, "E" if key == "HATCHEDIT" else "R" if key == "HATCHREGEN" else "")
        return
    p.setPen(_pen(ACCENT, 3))
    p.setBrush(QColor("#293b49"))
    p.drawRoundedRect(QRectF(7, 8, 50, 48), 8, 8)
    font = QFont("Arial", 15 if len(short) < 3 else 10)
    font.setBold(True)
    p.setFont(font)
    p.setPen(INK)
    p.drawText(QRectF(7, 8, 50, 48), Qt.AlignCenter, short)


def _paint_icon(key: str, size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    p = QPainter(pixmap)
    p.setRenderHint(QPainter.Antialiasing, True)
    p.scale(size / 64.0, size / 64.0)
    key = key.upper()
    if key in {"LINE", "PLINE", "RECT", "CIRCLE", "ARC", "TEXT"}:
        _draw_geometry(p, key)
    elif key in {"MOVE", "COPY", "ROTATE", "MIRROR", "SCALE", "OFFSET", "TRIM", "EXTEND", "ERASE"}:
        _draw_modify(p, key)
    elif key.startswith("DIM"):
        _draw_dimension(p, key)
    elif key in {"ZOOM", "ZE", "PAN", "GRADE", "GRID", "OSNAP", "ESCALA"}:
        _draw_view(p, key)
    elif key in {"NEW", "OPEN", "SAVE", "SAVE_AS", "IMPORT_RASTER", "IMPORT_SHP", "WBLOCK"}:
        _draw_file(p, key)
    elif key in {"BLOCK", "INSERT", "EXPLODE", "ATTEDIT", "DYNEDIT", "SIMBOLO", "ESCALAANOTATIVA"}:
        _draw_block(p, key)
    else:
        _draw_generic(p, key)
    p.end()
    return pixmap


def cad_icon(key: str) -> QIcon:
    """Devolve um QIcon multirresolucao para uma acao CAD."""
    icon = QIcon()
    for size in (16, 24, 32, 48, 64):
        icon.addPixmap(_paint_icon(key, size))
    return icon
