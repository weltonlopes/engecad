"""Camada raster de fundo (ortofoto) -- e o que permite "desenhar em cima".

Duas decisoes que fazem a navegacao ser fluida:

1. Leitura DECIMADA por janela. Pedimos ao GDAL a janela visivel ja no tamanho
   em pixels da tela; ele resolve sozinho qual overview usar. Nunca lemos a
   imagem inteira -- um ECW/COG de varios GB navega igual a um pequeno.

2. Cache com folga. O canvas repinta a cada movimento do mouse (mira, snap).
   Reler o disco a cada frame travaria tudo, entao renderizamos uma area maior
   que a janela e reaproveitamos enquanto o zoom nao muda.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import rasterio
from PySide6.QtCore import QRectF
from PySide6.QtGui import QImage
from rasterio.enums import Resampling
from rasterio.vrt import WarpedVRT
from rasterio.windows import from_bounds

from ..core.geometry import BBox

# Quanto maior que a tela renderizamos, para o pan curto reaproveitar o cache.
CACHE_PAD = 1.35
MAX_TEXTURE_PX = 4096


class RasterLayer:
    def __init__(self, path: str | Path, source: str | None = None, project_crs=None):
        self.path = Path(path)
        self.source = source  # arquivo original, quando houve conversao (ex.: o .ecw)
        self.visible = True
        self.opacity = 1.0

        self._ds = rasterio.open(str(self.path))
        self._vrt: WarpedVRT | None = None
        self._reprojected = False

        self._cache_img: QImage | None = None
        self._cache_buf: np.ndarray | None = None
        self._cache_bounds: BBox | None = None
        self._cache_scale: float | None = None

        self.set_project_crs(project_crs)

    # ---------------- ciclo de vida ----------------

    def close(self) -> None:
        self._invalidate()
        if self._vrt is not None:
            self._vrt.close()
            self._vrt = None
        self._ds.close()

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def src(self):
        """Dataset efetivo: o VRT reprojetado, se houver, senao o original."""
        return self._vrt if self._vrt is not None else self._ds

    @property
    def reprojected(self) -> bool:
        return self._reprojected

    @property
    def crs_name(self) -> str:
        c = self._ds.crs
        return str(c.to_string()) if c else "sem CRS"

    @property
    def resolution(self) -> float:
        return abs(self.src.transform.a)

    # ---------------- CRS ----------------

    def set_project_crs(self, project_crs) -> None:
        """Alinha o raster ao CRS do projeto.

        Se o raster estiver em outro sistema, embrulhamos num WarpedVRT: dai em
        diante toda leitura ja sai nas coordenadas do projeto, e o resto do
        codigo nao precisa saber que houve reprojecao.
        """
        if self._vrt is not None:
            self._vrt.close()
            self._vrt = None
        self._reprojected = False
        self._invalidate()

        target = getattr(project_crs, "crs", project_crs)
        if target is None or self._ds.crs is None:
            self._update_bounds()
            return
        try:
            same = self._ds.crs == target
        except Exception:
            same = False
        if not same:
            try:
                self._vrt = WarpedVRT(self._ds, crs=target, resampling=Resampling.bilinear)
                self._reprojected = True
            except Exception:
                self._vrt = None
        self._update_bounds()

    def _update_bounds(self) -> None:
        b = self.src.bounds
        self.bounds = BBox(b.left, b.bottom, b.right, b.top)

    # ---------------- leitura ----------------

    def _invalidate(self) -> None:
        self._cache_img = None
        self._cache_buf = None
        self._cache_bounds = None
        self._cache_scale = None

    def _band_indexes(self) -> list[int]:
        n = self.src.count
        if n >= 3:
            return [1, 2, 3]
        return [1]

    def _render(self, region: BBox, scale: float) -> None:
        """Le `region` do raster no tamanho de tela dado e guarda no cache."""
        src = self.src
        out_w = int(min(MAX_TEXTURE_PX, max(1, math.ceil(region.width * scale))))
        out_h = int(min(MAX_TEXTURE_PX, max(1, math.ceil(region.height * scale))))

        window = from_bounds(
            region.minx, region.miny, region.maxx, region.maxy, transform=src.transform
        )
        idx = self._band_indexes()
        # boundless: a janela pode extrapolar o raster nas bordas
        data = src.read(
            idx,
            window=window,
            out_shape=(len(idx), out_h, out_w),
            resampling=Resampling.bilinear,
            boundless=True,
            fill_value=0,
            masked=True,
        )

        if np.ma.isMaskedArray(data):
            mask = ~np.ma.getmaskarray(data).any(axis=0)
            arr = np.ma.filled(data, 0)
        else:
            mask = np.ones((out_h, out_w), dtype=bool)
            arr = data

        arr = self._to_uint8(arr)
        if arr.shape[0] == 1:
            rgb = np.repeat(arr, 3, axis=0)
        else:
            rgb = arr[:3]

        rgba = np.empty((out_h, out_w, 4), dtype=np.uint8)
        rgba[..., 0] = rgb[0]
        rgba[..., 1] = rgb[1]
        rgba[..., 2] = rgb[2]
        rgba[..., 3] = np.where(mask, 255, 0).astype(np.uint8)

        buf = np.ascontiguousarray(rgba)
        img = QImage(buf.data, out_w, out_h, out_w * 4, QImage.Format_RGBA8888)
        # o QImage nao copia o buffer: guardamos a referencia para o numpy nao
        # coletar o array embaixo do Qt.
        self._cache_buf = buf
        self._cache_img = img
        self._cache_bounds = region
        self._cache_scale = scale

    @staticmethod
    def _to_uint8(arr: np.ndarray) -> np.ndarray:
        if arr.dtype == np.uint8:
            return arr
        a = arr.astype(np.float32)
        finite = np.isfinite(a)
        if not finite.any():
            return np.zeros(arr.shape, dtype=np.uint8)
        lo = np.percentile(a[finite], 2.0)
        hi = np.percentile(a[finite], 98.0)
        if hi <= lo:
            lo, hi = float(a[finite].min()), float(a[finite].max())
        if hi <= lo:
            return np.zeros(arr.shape, dtype=np.uint8)
        return np.clip((a - lo) / (hi - lo) * 255.0, 0, 255).astype(np.uint8)

    # ---------------- desenho ----------------

    def paint(self, painter, vp) -> None:
        if not self.visible or self.bounds.is_empty:
            return
        vis = vp.visible_bbox()
        if not vis.intersects(self.bounds):
            return

        need = BBox(
            max(vis.minx, self.bounds.minx),
            max(vis.miny, self.bounds.miny),
            min(vis.maxx, self.bounds.maxx),
            min(vis.maxy, self.bounds.maxy),
        )
        if need.is_empty or need.width <= 0 or need.height <= 0:
            return

        if not self._cache_is_usable(need, vp.scale):
            pad_x = vis.width * (CACHE_PAD - 1) / 2
            pad_y = vis.height * (CACHE_PAD - 1) / 2
            region = BBox(
                max(vis.minx - pad_x, self.bounds.minx),
                max(vis.miny - pad_y, self.bounds.miny),
                min(vis.maxx + pad_x, self.bounds.maxx),
                min(vis.maxy + pad_y, self.bounds.maxy),
            )
            self._render(region, vp.scale)

        if self._cache_img is None or self._cache_bounds is None:
            return
        b = self._cache_bounds
        x0, y0 = vp.world_to_screen_xy(b.minx, b.maxy)
        x1, y1 = vp.world_to_screen_xy(b.maxx, b.miny)
        target = QRectF(x0, y0, x1 - x0, y1 - y0)
        prev = painter.opacity()
        painter.setOpacity(self.opacity)
        painter.drawImage(target, self._cache_img)
        painter.setOpacity(prev)

    def _cache_is_usable(self, need: BBox, scale: float) -> bool:
        if self._cache_img is None or self._cache_bounds is None:
            return False
        if self._cache_scale is None or abs(self._cache_scale - scale) > 1e-12:
            return False
        c = self._cache_bounds
        return (
            c.minx <= need.minx + 1e-9
            and c.miny <= need.miny + 1e-9
            and c.maxx >= need.maxx - 1e-9
            and c.maxy >= need.maxy - 1e-9
        )

    def info(self) -> str:
        s = self.src
        lines = [
            f"Arquivo: {self.path}",
            f"Origem:  {self.source}" if self.source else None,
            f"Tamanho: {s.width} x {s.height} px, {s.count} banda(s), {s.dtypes[0]}",
            f"CRS:     {self.crs_name}" + ("  (reprojetado ao vivo)" if self._reprojected else ""),
            f"Resolucao: {self.resolution:.4f} unidades/px",
            f"Extensao: {self.bounds.minx:.3f}, {self.bounds.miny:.3f} .. "
            f"{self.bounds.maxx:.3f}, {self.bounds.maxy:.3f}",
            f"Overviews: {s.overviews(1) if s.count else []}",
        ]
        return "\n".join(x for x in lines if x)
