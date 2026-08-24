"""Transformacao mundo <-> tela.

PRECISAO -- a razao de existir deste modulo:

Uma coordenada UTM tem magnitude ~7.4e6. Se ela for entregue crua ao Qt (via
QTransform no QPainter, ou via QGraphicsView), o motor de rasterizacao a
processa internamente em precisao simples e o desenho passa a "tremer" na casa
do meio metro -- inaceitavel num CAD cadastral.

A solucao e fazer a transformacao aqui, em float64 do Python, e entregar ao Qt
apenas coordenadas de TELA, que sao numeros pequenos (0..alguns milhares). O
painter nunca ve um numero grande. E por isso que o EngeCAD nao usa
QGraphicsView: ele tiraria justamente esse passo das nossas maos.
"""

from __future__ import annotations

import math

from ..core.geometry import BBox, Vec2

MIN_SCALE = 1e-9  # px por unidade de mundo
MAX_SCALE = 1e9
INCH_IN_METERS = 0.0254


class Viewport:
    def __init__(self, width: int = 800, height: int = 600):
        self.width = max(1, width)
        self.height = max(1, height)
        self.center = Vec2(0.0, 0.0)  # ponto do mundo no centro da tela
        self.scale = 1.0  # pixels por unidade de mundo

    # ---------------- estado ----------------

    def resize(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))

    @property
    def units_per_pixel(self) -> float:
        return 1.0 / self.scale

    # ---------------- transformacao ----------------

    def world_to_screen_xy(self, x: float, y: float) -> tuple[float, float]:
        """Mundo -> pixel. Y invertido: no mundo cresce para o norte.

        A subtracao do centro acontece aqui, em float64, e o resultado ja e um
        numero pequeno (coordenada de tela). E este passo que impede a
        magnitude UTM de chegar ao rasterizador do Qt.
        """
        sx = (x - self.center.x) * self.scale + self.width * 0.5
        sy = self.height * 0.5 - (y - self.center.y) * self.scale
        return sx, sy

    def world_to_screen(self, p: Vec2) -> tuple[float, float]:
        return self.world_to_screen_xy(p.x, p.y)

    def screen_to_world(self, sx: float, sy: float) -> Vec2:
        wx = (sx - self.width * 0.5) / self.scale + self.center.x
        wy = (self.height * 0.5 - sy) / self.scale + self.center.y
        return Vec2(wx, wy)

    def px_to_world(self, px: float) -> float:
        """Comprimento em pixels -> comprimento no mundo."""
        return px / self.scale

    def world_to_px(self, d: float) -> float:
        return d * self.scale

    # ---------------- navegacao ----------------

    def pan_screen(self, dx: float, dy: float) -> None:
        """Arrasta a vista em pixels (dx,dy = deslocamento do mouse)."""
        self.center = Vec2(self.center.x - dx / self.scale, self.center.y + dy / self.scale)

    def set_scale(self, scale: float) -> None:
        self.scale = min(MAX_SCALE, max(MIN_SCALE, float(scale)))

    def zoom_at_screen(self, sx: float, sy: float, factor: float) -> None:
        """Zoom ancorado: o ponto do mundo sob (sx,sy) nao se move."""
        anchor = self.screen_to_world(sx, sy)
        old = self.scale
        self.set_scale(self.scale * factor)
        if self.scale == old:
            return
        # recentra para manter o ponto ancora sob o cursor
        after = self.screen_to_world(sx, sy)
        self.center = self.center + (anchor - after)

    def zoom_to_bbox(self, b: BBox, margin: float = 0.06) -> None:
        if b.is_empty:
            return
        self.center = b.center
        w = max(b.width, 1e-9)
        h = max(b.height, 1e-9)
        fit = min(self.width / w, self.height / h)
        self.set_scale(fit * (1.0 - 2 * margin))

    def zoom_to_point(self, p: Vec2, scale: float | None = None) -> None:
        self.center = p
        if scale is not None:
            self.set_scale(scale)

    def visible_bbox(self) -> BBox:
        a = self.screen_to_world(0, self.height)
        b = self.screen_to_world(self.width, 0)
        return BBox(a.x, a.y, b.x, b.y)

    # ---------------- escala cartografica ----------------

    def scale_denominator(self, dpi: float = 96.0) -> float:
        """Denominador N da escala 1:N para a tela atual."""
        meters_per_px_screen = INCH_IN_METERS / max(dpi, 1.0)
        return self.units_per_pixel / meters_per_px_screen

    def set_scale_denominator(self, denom: float, dpi: float = 96.0) -> None:
        """Ajusta o zoom para uma escala 1:N (util para plotagem 1:500, 1:1000)."""
        if denom <= 0:
            return
        meters_per_px_screen = INCH_IN_METERS / max(dpi, 1.0)
        self.set_scale(1.0 / (denom * meters_per_px_screen))

    # ---------------- apoio ao desenho ----------------

    def flatten_tolerance(self, pixels: float = 0.3) -> float:
        """Sagitta em unidades do mundo equivalente a N pixels de erro."""
        return self.px_to_world(pixels)

    def nice_grid_step(self, target_px: float = 80.0) -> float:
        """Passo de grade 1/2/5 x 10^n mais proximo de target_px pixels."""
        raw = self.px_to_world(target_px)
        if raw <= 0 or not math.isfinite(raw):
            return 1.0
        exp = math.floor(math.log10(raw))
        base = 10.0**exp
        for mult in (1.0, 2.0, 5.0, 10.0):
            if raw <= base * mult:
                return base * mult
        return base * 10.0
