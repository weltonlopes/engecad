"""Tema visual e conversao de cores DXF (ACI) para cores de tela."""

from __future__ import annotations

from dataclasses import dataclass

from ezdxf import colors as ezcolors
from PySide6.QtGui import QColor


@dataclass(frozen=True)
class Theme:
    background: str = "#1c1f26"
    grid_minor: str = "#272b34"
    grid_major: str = "#333846"
    axis: str = "#4a5164"
    crosshair: str = "#8e97ad"
    cursor_box: str = "#c9d1e0"
    snap_marker: str = "#39d353"
    snap_text: str = "#39d353"
    preview: str = "#ffb347"
    selection: str = "#4da3ff"
    default_entity: str = "#e6e9ef"
    text: str = "#c9d1e0"
    raster_border: str = "#3d4350"

    def q(self, name: str) -> QColor:
        return QColor(getattr(self, name))


DARK = Theme()
LIGHT = Theme(
    background="#f4f5f7",
    grid_minor="#e6e8ec",
    grid_major="#d5d8de",
    axis="#b0b5bf",
    crosshair="#6b7280",
    cursor_box="#374151",
    snap_marker="#128a2b",
    snap_text="#0f6b22",
    preview="#c2680a",
    selection="#1f6feb",
    default_entity="#1f2430",
    text="#374151",
    raster_border="#c0c4cc",
)

_aci_cache: dict[tuple[int, bool], QColor] = {}


def aci_to_qcolor(aci: int, dark: bool = True) -> QColor:
    """Cor ACI do DXF -> QColor.

    ACI 7 e "preto ou branco conforme o fundo": no CAD ele significa
    "cor do primeiro plano", entao invertemos conforme o tema.
    """
    key = (int(aci), dark)
    hit = _aci_cache.get(key)
    if hit is not None:
        return hit
    if aci in (0, 7, 256):  # 0=BYBLOCK, 7=branco/preto, 256=BYLAYER ja resolvido
        c = QColor("#e6e9ef" if dark else "#1f2430")
    else:
        try:
            r, g, b = ezcolors.aci2rgb(int(aci))
            c = QColor(r, g, b)
        except Exception:
            c = QColor("#e6e9ef" if dark else "#1f2430")
    _aci_cache[key] = c
    return c
