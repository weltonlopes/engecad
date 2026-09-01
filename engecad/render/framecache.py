"""Cache do quadro: o desenho rasterizado uma vez, reaproveitado no pan.

Arrastar a vista nao muda a geometria nem a escala -- muda so o recorte. Se o
desenho ja rasterizado for guardado numa area maior que a janela, o pan vira um
blit de ~1 ms em vez de um redesenho. Medido no desenho de 200 mil entidades:
1.1 ms contra 8 239 ms.

A folga de 25 % em cada lado cobre um arrasto tipico sem redesenhar. Quando a
vista sai da area guardada, ou quando o zoom muda, o quadro e refeito; se esse
redesenho for lento, o canvas mostra antes o cache esticado como previa e agenda
o refino -- o mesmo que o AutoCAD faz numa regeneracao pesada.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPainter, QPixmap

from ..core.geometry import BBox, Vec2
from .viewport import Viewport

PAD = 0.25  # folga em cada lado, como fracao do tamanho da janela


class FrameCache:
    def __init__(self):
        self.pixmap: QPixmap | None = None
        self.center: Vec2 | None = None
        self.scale = 0.0
        self.last_ms = 0.0
        self._logical = (0, 0)

    @property
    def has_content(self) -> bool:
        return self.pixmap is not None and self.center is not None

    def invalidate(self) -> None:
        self.pixmap = None
        self.center = None
        self.scale = 0.0

    # ---------------- estado ----------------

    def world_rect(self) -> BBox:
        """Regiao do mundo coberta pelo pixmap."""
        if not self.has_content:
            return BBox()
        hw = self._logical[0] * 0.5 / self.scale
        hh = self._logical[1] * 0.5 / self.scale
        return BBox(
            self.center.x - hw, self.center.y - hh, self.center.x + hw, self.center.y + hh
        )

    def is_exact(self, vp: Viewport) -> bool:
        """O cache serve tal como esta: mesma escala e cobre a janela inteira."""
        if not self.has_content or self.scale != vp.scale:
            return False
        have = self.world_rect()
        want = vp.visible_bbox()
        return (
            have.minx <= want.minx
            and have.miny <= want.miny
            and have.maxx >= want.maxx
            and have.maxy >= want.maxy
        )

    # ---------------- producao ----------------

    def render(self, vp: Viewport, dpr: float, paint_scene) -> None:
        """Redesenha o cache. paint_scene(painter, viewport) desenha a cena."""
        lw = max(1, int(round(vp.width * (1 + 2 * PAD))))
        lh = max(1, int(round(vp.height * (1 + 2 * PAD))))
        dpr = max(1.0, float(dpr))

        if (
            self.pixmap is None
            or self._logical != (lw, lh)
            or abs(self.pixmap.devicePixelRatio() - dpr) > 1e-9
        ):
            self.pixmap = QPixmap(int(round(lw * dpr)), int(round(lh * dpr)))
            self.pixmap.setDevicePixelRatio(dpr)
            self._logical = (lw, lh)

        padded = Viewport(lw, lh)
        padded.center = vp.center
        padded.scale = vp.scale

        started = time.perf_counter()
        painter = QPainter(self.pixmap)
        try:
            paint_scene(painter, padded)
        finally:
            painter.end()
        self.last_ms = (time.perf_counter() - started) * 1000.0
        self.center = vp.center
        self.scale = vp.scale

    # ---------------- consumo ----------------

    def blit(self, painter: QPainter, vp: Viewport) -> bool:
        """Desenha o cache alinhado a vista atual, esticando se o zoom mudou."""
        if not self.has_content:
            return False
        box = self.world_rect()
        x0, y0 = vp.world_to_screen_xy(box.minx, box.maxy)
        x1, y1 = vp.world_to_screen_xy(box.maxx, box.miny)
        if self.scale == vp.scale:
            # Mesma escala: um blit deslocado, sem reamostragem.
            painter.drawPixmap(QPointF(x0, y0), self.pixmap)
            return True
        painter.drawPixmap(
            QRectF(x0, y0, x1 - x0, y1 - y0), self.pixmap, QRectF(self.pixmap.rect())
        )
        return True
