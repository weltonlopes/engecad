"""Cache do quadro: o desenho rasterizado uma vez, reaproveitado no pan.

Arrastar a vista nao muda a geometria nem a escala -- muda so o recorte. Se o
desenho ja rasterizado for guardado numa area maior que a janela, o pan vira um
blit de ~1 ms em vez de um redesenho. Medido no desenho de 200 mil entidades:
0.7 ms contra 8 239 ms. A folga de 25 % em cada lado cobre um arrasto tipico sem
redesenhar.

DESENHO PROGRESSIVO -- quando o cache nao serve, o quadro e refeito por ETAPAS,
com orcamento de tempo. Cada etapa desenha o que couber e devolve o controle ao
laco de eventos; o canvas repinta o que ja existe e agenda a proxima. A interface
continua viva durante uma regeneracao de segundos, e o desenho aparece em ondas
-- o mesmo comportamento de um regen pesado do AutoCAD.

A alternativa seria rasterizar numa thread. Ajudaria menos do que parece: o custo
maior nos casos grandes e construir a geometria dos tiles, que e Python e ezdxf
puros e segura a GIL, alem de exigir que a thread lesse o documento enquanto o
usuario o edita. Fatiar no proprio laco de eventos resolve os dois e nao tem
concorrencia nenhuma.
"""

from __future__ import annotations

import math
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
        #: Quadro terminado. Um quadro em construcao ja pode ser exibido, mas
        #: nao pode ser considerado valido para a vista.
        self.complete = False
        self._logical = (0, 0)
        self._steps = None
        self._current = None
        self._padded: Viewport | None = None
        self._spent = 0.0

    @property
    def has_content(self) -> bool:
        return self.pixmap is not None and self.center is not None

    @property
    def building(self) -> bool:
        return self._steps is not None

    @property
    def started(self) -> bool:
        """Ja gastou tempo neste quadro (ou seja, nao e a primeira fatia)."""
        return self._spent > 0.0

    def invalidate(self) -> None:
        self.pixmap = None
        self.center = None
        self.scale = 0.0
        self.complete = False
        self._steps = None
        self._current = None

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
        """O cache serve tal como esta: pronto, mesma escala, cobre a janela."""
        if not self.has_content or not self.complete or self.scale != vp.scale:
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

    def building_for(self, vp: Viewport) -> bool:
        """Ha uma construcao em andamento, e ela e desta vista."""
        return self.building and self.center == vp.center and self.scale == vp.scale

    def begin(self, vp: Viewport, dpr: float, steps) -> None:
        """Comeca um quadro novo. `steps(viewport)` produz as etapas de desenho.

        Cada etapa e um chamavel `f(painter, deadline)` que devolve True quando
        terminou o seu pedaco; False pede para ser chamada de novo na proxima
        fatia de tempo.

        `steps` e consumido preguicosamente -- de proposito. Assim uma etapa pode
        depender do que a anterior preparou, e o trabalho de decidir o que
        desenhar tambem cabe dentro do orcamento, em vez de acontecer inteiro no
        instante em que o quadro comeca.
        """
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
        self._padded = padded

        # O centro e a escala valem desde ja: o quadro parcial precisa ser
        # posicionavel na tela. Quem separa pronto de incompleto e `complete`.
        self.center = vp.center
        self.scale = vp.scale
        self.complete = False
        self._spent = 0.0
        self._steps = iter(steps(padded))
        self._current = None

    def step(self, budget_ms: float) -> bool:
        """Executa etapas ate estourar o orcamento. True quando o quadro fecha."""
        if self._steps is None:
            self.complete = self.has_content
            return True
        started = time.perf_counter()
        deadline = started + max(budget_ms, 0.0) / 1000.0
        finished = False
        painter = QPainter(self.pixmap)
        try:
            while True:
                if self._current is None:
                    self._current = next(self._steps, None)
                    if self._current is None:
                        finished = True
                        break
                if self._current(painter, deadline):
                    self._current = None
                if time.perf_counter() >= deadline:
                    break
        finally:
            painter.end()
        self._spent += (time.perf_counter() - started) * 1000.0
        if not finished:
            return False
        self._steps = None
        self.last_ms = self._spent
        self.complete = True
        return True

    def render_now(self, vp: Viewport, dpr: float, steps) -> None:
        """Monta o quadro inteiro sem passar pelo laco de eventos.

        Serve a testes e a exportacao, onde nao ha laco que devolva o controle.
        """
        self.begin(vp, dpr, steps)
        while not self.step(math.inf):
            pass

    # ---------------- consumo ----------------

    def blit(self, painter: QPainter, vp: Viewport) -> bool:
        """Desenha o cache alinhado a vista atual, esticando se o zoom mudou."""
        if not self.has_content:
            return False
        box = self.world_rect()
        x0, y0 = vp.world_to_screen_xy(box.minx, box.maxy)
        x1, y1 = vp.world_to_screen_xy(box.maxx, box.miny)
        if self.scale == vp.scale:
            # Mesma escala: um blit deslocado, sem reamostragem. So o pedaco que
            # cabe na janela -- o pixmap e 2.25x maior que ela por causa da folga
            # do pan, e copia-lo inteiro a cada movimento do mouse era o maior
            # custo isolado de um quadro ocioso.
            dpr = self.pixmap.devicePixelRatio()
            sx = max(0.0, -x0)
            sy = max(0.0, -y0)
            w = min(self._logical[0] - sx, vp.width - max(x0, 0.0))
            h = min(self._logical[1] - sy, vp.height - max(y0, 0.0))
            if w <= 0 or h <= 0:
                return False
            painter.drawPixmap(
                QPointF(x0 + sx, y0 + sy),
                self.pixmap,
                QRectF(sx * dpr, sy * dpr, w * dpr, h * dpr),
            )
            return True
        painter.drawPixmap(
            QRectF(x0, y0, x1 - x0, y1 - y0), self.pixmap, QRectF(self.pixmap.rect())
        )
        return True
