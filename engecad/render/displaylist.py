"""Display list: a geometria do desenho achatada uma vez e guardada em tiles.

PRECISAO -- ler primeiro o cabecalho de render/viewport.py.

O canvas nao entrega coordenadas de mundo ao Qt porque uma coordenada UTM tem
magnitude ~7.4e6 e o rasterizador a processa em precisao simples: o desenho
treme meio metro. A saida do modulo era transformar tudo em Python, e e por isso
que um desenho de 200 mil entidades custava segundos por quadro.

Aqui a geometria e guardada em coordenadas LOCAIS -- mundo menos uma origem
fixa, subtraida em float64 na construcao. Os numeros entregues ao QPainterPath
passam a ter magnitude de milhares, e o QTransform do painter volta a ser
seguro: o erro de arredondamento medido cai de 2.15 px (UTM cru) para 0.001 px.
Com isso a transformacao sai do laco Python e vai para o C++ do Qt.

ESTRATEGIA -- tres regimes, escolhidos pelo tamanho que a entidade tem na tela:

* menor que ~2 px: nao vale um vetor. Vira tinta numa grade de ocupacao
  rasterizada em numpy de uma vez so (200 mil entidades em ~3 ms).
* entre isso e o tile: entra num QPainterPath cacheado por tile e por oitava de
  zoom. Enquanto a vista nao troca de oitava, o quadro e so um setTransform.
* texto, ponto e atributo de bloco: dependem do tamanho da fonte em pixels,
  entao continuam sendo desenhados a cada quadro -- mas so eles, e com teto.

Os tiles sao construidos sob demanda: abrir o arquivo nao paga nada, e so a
regiao que o usuario olhou de fato ocupa memoria.
"""

from __future__ import annotations

import math

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainterPath, QPen, QTransform

from ..core.dimensions import DIMENSION_TYPES
from ..core.entities import POINT_LIKE, entity_point_lists
from .styles import aci_to_qcolor

TILE_PX = 512.0  # aresta do tile, em pixels de tela da oitava a que ele pertence
TILE_ENTITIES = 1_500  # alvo de entidades por tile, usado como piso do tamanho
TINY_PX = 2.0  # entidade menor que isto vira tinta em vez de vetor
MAX_VECTOR_ENTITIES = 30_000  # acima disso sobe uma oitava (LOD mais agressivo)
MAX_LEVEL_ESCALATION = 2  # teto da escalada: tinta nunca engole nada > ~8 px
MAX_CACHED_VERTS = 3_000_000  # teto do cache de tiles, em vertices
MAX_MARKERS = 3_000  # textos/pontos rotulados por quadro
MAX_HATCH_LINES = 5_000  # linhas de padrao por hachura

# Largura da caneta da geometria, em pixels. Nao aumentar sem medir: o motor
# raster do Qt tem um caminho rapido para caneta cosmetica de ate 1 px, e passar
# disso troca o tracado direto pelo stroker completo. Num path de 288 mil
# elementos sob QTransform com antialias, 1.2 px custou 7 635 ms contra 84 ms de
# 1.0 px -- noventa vezes, para uma diferenca de espessura invisivel.
STROKE_WIDTH = 1.0

_GEOMETRY = 1  # tem geometria para vetorizar
_MARKER = 2  # precisa de rotulo desenhado por quadro (texto, ponto, atributo)

# Entidades que o canvas ainda rotula a cada quadro, porque o tamanho do texto
# depende do zoom. INSERT e DIMENSION estao nos dois grupos: a geometria deles e
# vetorizada e cacheada, mas os ATTRIBs e o texto da cota nao.
_MARKER_TYPES = POINT_LIKE | DIMENSION_TYPES | {"ATTRIB"}


class _Cell:
    """Geometria de um tile numa oitava de zoom, agrupada por caneta."""

    __slots__ = ("strokes", "fills", "verts")

    def __init__(self):
        self.strokes: dict[tuple, QPainterPath] = {}
        self.fills: dict[tuple, QPainterPath] = {}
        self.verts = 0

    def stroke(self, key) -> QPainterPath:
        path = self.strokes.get(key)
        if path is None:
            path = self.strokes[key] = QPainterPath()
        return path

    def fill(self, key) -> QPainterPath:
        path = self.fills.get(key)
        if path is None:
            path = self.fills[key] = QPainterPath()
            path.setFillRule(Qt.OddEvenFill)
        return path


class DisplayList:
    """Cache de desenho de um documento. Um por canvas."""

    def __init__(self, doc):
        self.doc = doc
        self._revision = -1
        self._origin = (0.0, 0.0)

        # Arrays paralelos, indexados por "slot". Um slot vago tem bbox vazia,
        # entao jamais sobrevive a um culling.
        self._slot: dict[str, int] = {}
        self._ents: list = []
        self._free: list[int] = []
        self._bbox = np.zeros((0, 4))
        self._size = np.zeros(0)
        self._aci = np.zeros(0, np.int32)
        self._layer = np.zeros(0, np.int32)
        self._flags = np.zeros(0, np.uint8)

        self._layer_ids: dict[str, int] = {}
        self._layer_names: list[str] = []

        self._cells: dict[tuple[int, int, int], _Cell] = {}
        self._order: list[tuple[int, int, int]] = []
        self._verts = 0
        self._buckets: tuple | None = None  # (nivel, tx, ty, mascara de vetor)
        self._floor_tile = 1.0
        self._ceil_tile = math.inf
        self._measured_n = 0
        self._rgb: dict[bool, np.ndarray] = {}
        self._ink_buf: np.ndarray | None = None

    # ---------------- sincronizacao com o documento ----------------

    def sync(self) -> None:
        """Absorve as mudancas do documento desde o ultimo quadro."""
        if self.doc.geometry_revision == self._revision:
            return
        self._revision = self.doc.geometry_revision
        full, dirty = self.doc.consume_geometry_changes()
        if full:
            self._rebuild()
            return
        for handle in dirty:
            self._refresh_slot(handle)
        # Um desenho que comeca vazio e cresce muda de densidade: quando o porte
        # do documento dobra, a grade de tiles e redimensionada.
        n = len(self._slot)
        if n > 2 * self._measured_n or n * 2 < self._measured_n:
            self._measure_density()
            self.clear()

    def clear(self) -> None:
        self._cells.clear()
        self._order.clear()
        self._verts = 0
        self._buckets = None

    def _rebuild(self) -> None:
        doc = self.doc
        ents = [e for e in doc.msp if e.is_alive and e.dxf.get("handle") is not None]
        n = len(ents)
        cap = max(n * 2, 64)

        self._slot = {}
        self._ents = [None] * cap
        self._free = list(range(n, cap))
        self._bbox = np.full((cap, 4), np.nan)
        self._size = np.zeros(cap)
        self._aci = np.zeros(cap, np.int32)
        self._layer = np.zeros(cap, np.int32)
        self._flags = np.zeros(cap, np.uint8)
        self._layer_ids = {}
        self._layer_names = []
        self.clear()

        boxes = doc.index._boxes
        colors: dict[str, int] = {}
        for i, e in enumerate(ents):
            handle = e.dxf.get("handle")
            self._slot[handle] = i
            self._ents[i] = e
            self._fill_slot(i, e, boxes.get(handle), colors)

        self._origin = self._pick_origin()
        self._measure_density()

    def _pick_origin(self) -> tuple[float, float]:
        """Canto do desenho, arredondado, usado como zero das coordenadas locais."""
        bb = self._bbox
        if bb.size == 0 or not np.isfinite(bb).any():
            return (0.0, 0.0)
        minx = float(np.nanmin(bb[:, 0]))
        miny = float(np.nanmin(bb[:, 1]))
        return (math.floor(minx / 1000.0) * 1000.0, math.floor(miny / 1000.0) * 1000.0)

    def _layer_id(self, name: str) -> int:
        hit = self._layer_ids.get(name)
        if hit is None:
            hit = self._layer_ids[name] = len(self._layer_names)
            self._layer_names.append(name)
        return hit

    def _fill_slot(self, i: int, entity, box, colors: dict[str, int]) -> None:
        if box is None or box.is_empty:
            self._bbox[i] = np.nan
            self._size[i] = 0.0
            self._flags[i] = 0
            return
        self._bbox[i] = (box.minx, box.miny, box.maxx, box.maxy)
        self._size[i] = max(box.width, box.height)

        layer = entity.dxf.get("layer", "0")
        self._layer[i] = self._layer_id(layer)

        color = entity.dxf.get("color", 256)
        if color in (256, 0):
            aci = colors.get(layer)
            if aci is None:
                aci = colors[layer] = self.doc.layer_color(layer)
        else:
            aci = color
        self._aci[i] = int(aci)

        t = entity.dxftype()
        flags = 0
        if t not in POINT_LIKE or t == "INSERT":
            flags |= _GEOMETRY
        if t in _MARKER_TYPES:
            flags |= _MARKER
        self._flags[i] = flags

    def _refresh_slot(self, handle: str) -> None:
        """Reposiciona uma entidade que mudou e invalida os tiles que ela tocava."""
        i = self._slot.get(handle)
        if i is not None:
            self._drop_cells_at(self._bbox[i])
        entity = self.doc.entity_by_handle(handle)
        if entity is None or not entity.is_alive:
            if i is not None:
                self._slot.pop(handle, None)
                self._ents[i] = None
                self._bbox[i] = np.nan
                self._size[i] = 0.0
                self._flags[i] = 0
                self._free.append(i)
            return
        if i is None:
            i = self._alloc()
            self._slot[handle] = i
        self._ents[i] = entity
        self._fill_slot(i, entity, self.doc.index._boxes.get(handle), {})
        self._drop_cells_at(self._bbox[i])
        self._buckets = None

    def _alloc(self) -> int:
        if self._free:
            return self._free.pop()
        old = len(self._ents)
        cap = max(old * 2, 64)
        self._ents.extend([None] * (cap - old))
        self._bbox = np.vstack([self._bbox, np.full((cap - old, 4), np.nan)])
        self._size = np.concatenate([self._size, np.zeros(cap - old)])
        self._aci = np.concatenate([self._aci, np.zeros(cap - old, np.int32)])
        self._layer = np.concatenate([self._layer, np.zeros(cap - old, np.int32)])
        self._flags = np.concatenate([self._flags, np.zeros(cap - old, np.uint8)])
        self._free = list(range(old + 1, cap))
        return old

    def _drop_cells_at(self, box) -> None:
        """Descarta os tiles que cobrem `box`, em todas as oitavas."""
        if not np.isfinite(box).all():
            return
        ox, oy = self._origin
        dead = []
        for key in self._cells:
            level, tx, ty = key
            if tx is None:  # balde dos grandes demais: sempre suspeito
                dead.append(key)
                continue
            ts = self._tile_size(level)
            if (
                box[0] - ts <= ox + (tx + 1) * ts
                and box[2] + ts >= ox + tx * ts
                and box[1] - ts <= oy + (ty + 1) * ts
                and box[3] + ts >= oy + ty * ts
            ):
                dead.append(key)
        for key in dead:
            self._forget(key)

    def _forget(self, key) -> None:
        cell = self._cells.pop(key, None)
        if cell is None:
            return
        self._verts -= cell.verts
        try:
            self._order.remove(key)
        except ValueError:
            pass

    # ---------------- parametros de nivel ----------------

    def _tile_size(self, level: int) -> float:
        """Aresta do tile: 512 px da oitava, mas nunca menor que o piso do dado.

        Fazer o tile encolher junto com o zoom parece natural e esta errado: la
        embaixo ele fica menor que as proprias entidades, todas caem no balde dos
        grandes demais e o cache deixa de recortar coisa alguma. O piso vem da
        densidade do desenho, para o tile guardar sempre um punhado de entidades.
        """
        return min(max(self._floor_tile, (2.0**level) * TILE_PX), self._ceil_tile)

    def _measure_density(self) -> None:
        """Piso do tamanho do tile, a partir da densidade e do porte das entidades."""
        bb = self._bbox
        ok = np.isfinite(bb[:, 0])
        n = int(ok.sum())
        self._measured_n = n
        if n == 0:
            self._floor_tile = 1.0
            self._ceil_tile = math.inf
            return
        width = float(np.nanmax(bb[:, 2]) - np.nanmin(bb[:, 0]))
        height = float(np.nanmax(bb[:, 3]) - np.nanmin(bb[:, 1]))
        area = max(width * height, 1e-9)
        by_density = math.sqrt(TILE_ENTITIES * area / n)
        # E o tile precisa acomodar a entidade tipica, senao ela vira "grande
        # demais" e passa a ser desenhada em toda vista.
        by_size = float(np.percentile(self._size[ok], 95)) * 4.0
        self._floor_tile = max(by_density, by_size, 1e-6)
        # Teto: um tile do tamanho do desenho inteiro faria o cache guardar tudo
        # num unico path, e editar uma entidade jogaria o desenho todo fora.
        self._ceil_tile = max(max(width, height) / 4.0, self._floor_tile)

    @staticmethod
    def _threshold(level: int) -> float:
        return (2.0**level) * TINY_PX

    @staticmethod
    def _sagitta(level: int) -> float:
        return (2.0**level) * 0.4

    def _choose_level(self, upw: float, idx: np.ndarray) -> int:
        """Oitava de zoom, subindo enquanto houver vetores demais para um quadro."""
        level = int(math.floor(math.log2(max(upw, 1e-12))))
        size = self._size[idx]
        for bump in range(MAX_LEVEL_ESCALATION + 1):
            if int((size >= self._threshold(level + bump)).sum()) <= MAX_VECTOR_ENTITIES:
                return level + bump
        return level + MAX_LEVEL_ESCALATION

    # ---------------- desenho ----------------

    def paint(self, painter, vp, dark: bool = True, dpr: float = 1.0) -> list:
        """Desenha a geometria e devolve as entidades que o canvas deve rotular."""
        self.sync()
        if not self._slot:
            return []

        vis = vp.visible_bbox()
        bb = self._bbox
        with np.errstate(invalid="ignore"):
            m = (
                (bb[:, 2] >= vis.minx)
                & (bb[:, 0] <= vis.maxx)
                & (bb[:, 3] >= vis.miny)
                & (bb[:, 1] <= vis.maxy)
            )
        visible = self._visible_layers()
        m &= visible[self._layer]
        idx = np.flatnonzero(m)
        if idx.size == 0:
            return []

        upw = 1.0 / max(vp.scale, 1e-12)
        level = self._choose_level(upw, idx)
        thr = self._threshold(level)

        flags = self._flags[idx]
        size = self._size[idx]
        has_geom = (flags & _GEOMETRY).astype(bool)
        markers = idx[(flags & _MARKER).astype(bool)]
        vectors = idx[has_geom & (size >= thr)]
        ink = idx[has_geom & (size < thr)]

        if markers.size > MAX_MARKERS:
            # Rotular nao cabe no orcamento: o texto vira tinta, como o QTEXT do
            # AutoCAD faz quando a fonte fica ilegivel.
            ink = np.concatenate([ink, markers])
            markers = markers[:0]

        self._paint_ink(painter, vp, ink, dark, dpr)
        self._paint_vectors(painter, vp, vectors, level, dark, visible)
        return [self._ents[i] for i in markers.tolist() if self._ents[i] is not None]

    def _visible_layers(self) -> np.ndarray:
        doc = self.doc
        out = np.ones(max(len(self._layer_names), 1), dtype=bool)
        for i, name in enumerate(self._layer_names):
            out[i] = doc.layer_is_visible(name)
        return out

    def _rgb_table(self, dark: bool) -> np.ndarray:
        hit = self._rgb.get(dark)
        if hit is None:
            hit = np.zeros((260, 3), np.uint8)
            for aci in range(260):
                c = aci_to_qcolor(aci, dark)
                hit[aci] = (c.red(), c.green(), c.blue())
            self._rgb[dark] = hit
        return hit

    def _paint_ink(self, painter, vp, idx: np.ndarray, dark: bool, dpr: float) -> None:
        """Entidades sub-pixel: um scatter numpy, uma imagem, um blit."""
        if idx.size == 0:
            return
        w = max(1, int(round(vp.width * dpr)))
        h = max(1, int(round(vp.height * dpr)))
        s = vp.scale * dpr
        ax = -vp.center.x * s + w * 0.5
        ay = h * 0.5 + vp.center.y * s

        bb = self._bbox[idx]
        # Tres amostras na diagonal da bbox: um ponto so perderia a entidade que
        # ainda mede dois ou tres pixels quando o LOD escala.
        px = np.concatenate([bb[:, 0], (bb[:, 0] + bb[:, 2]) * 0.5, bb[:, 2]])
        py = np.concatenate([bb[:, 1], (bb[:, 1] + bb[:, 3]) * 0.5, bb[:, 3]])
        aci = np.tile(self._aci[idx], 3)

        ix = (px * s + ax).astype(np.int32)
        iy = (ay - py * s).astype(np.int32)
        keep = (ix >= 0) & (ix < w) & (iy >= 0) & (iy < h)
        if not keep.any():
            return

        buf = self._ink_buffer(w, h)
        iy, ix = iy[keep], ix[keep]
        buf[iy, ix, 0:3] = self._rgb_table(dark)[np.clip(aci[keep], 0, 259)]
        buf[iy, ix, 3] = 255
        img = QImage(buf.data, w, h, w * 4, QImage.Format_RGBA8888)
        img.setDevicePixelRatio(dpr)
        painter.save()
        painter.resetTransform()
        painter.drawImage(0, 0, img)
        painter.restore()

    def _ink_buffer(self, w: int, h: int) -> np.ndarray:
        """Buffer da tinta, reaproveitado entre quadros.

        Realoca-lo a cada quadro custava 26 ms num viewport grande -- mais que a
        rasterizacao inteira dos vetores. O QImage tambem nao copia o buffer,
        entao a instancia precisa sobreviver ao blit de qualquer forma.
        """
        buf = self._ink_buf
        if buf is None or buf.shape[0] != h or buf.shape[1] != w:
            buf = self._ink_buf = np.zeros((h, w, 4), np.uint8)
        else:
            buf[:] = 0
        return buf

    def _paint_vectors(
        self, painter, vp, idx: np.ndarray, level: int, dark: bool, visible: np.ndarray
    ) -> None:
        if idx.size == 0:
            return
        ox, oy = self._origin
        ts = self._tile_size(level)
        vis = vp.visible_bbox()
        tx0 = int(math.floor((vis.minx - ox) / ts)) - 1
        tx1 = int(math.floor((vis.maxx - ox) / ts)) + 1
        ty0 = int(math.floor((vis.miny - oy) / ts)) - 1
        ty1 = int(math.floor((vis.maxy - oy) / ts)) + 1
        if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 4096:  # vista absurda: nada a fazer
            return

        s = vp.scale
        world = QTransform(
            s,
            0.0,
            0.0,
            -s,
            (ox - vp.center.x) * s + vp.width * 0.5,
            vp.height * 0.5 + (vp.center.y - oy) * s,
        )
        cells = [self._cell(level, None, None)]
        for tx in range(tx0, tx1 + 1):
            for ty in range(ty0, ty1 + 1):
                cells.append(self._cell(level, tx, ty))

        painter.save()
        painter.setWorldTransform(world, True)
        try:
            # Preenchimentos primeiro, e de todos os tiles: uma hachura nao pode
            # cobrir a geometria de um tile vizinho desenhado antes dela.
            # O tile guarda a geometria agrupada por camada justamente para que
            # apagar uma camada seja pular um grupo, e nao reconstruir o cache.
            painter.setPen(Qt.NoPen)
            for cell in cells:
                for key, path in cell.fills.items():
                    if not visible[key[0]]:
                        continue
                    painter.setBrush(QBrush(self._qcolor(key, dark)))
                    painter.drawPath(path)
            painter.setBrush(Qt.NoBrush)
            for cell in cells:
                for key, path in cell.strokes.items():
                    if not visible[key[0]]:
                        continue
                    pen = QPen(self._qcolor(key, dark), STROKE_WIDTH)
                    pen.setCosmetic(True)  # espessura em pixels, nao em metros
                    painter.setPen(pen)
                    painter.drawPath(path)
        finally:
            painter.restore()

    @staticmethod
    def _qcolor(key, dark: bool) -> QColor:
        _, aci, alpha = key
        c = aci_to_qcolor(int(aci), dark)
        if alpha >= 255:
            return c
        c = QColor(c)  # o cache de estilos devolve a mesma instancia: nao mexer nela
        c.setAlpha(alpha)
        return c

    # ---------------- construcao dos tiles ----------------

    def _cell(self, level: int, tx, ty) -> _Cell | None:
        key = (level, tx, ty)
        cell = self._cells.get(key)
        if cell is not None:
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
            return cell
        cell = self._build_cell(level, tx, ty)
        self._cells[key] = cell
        self._order.append(key)
        self._verts += cell.verts
        while self._verts > MAX_CACHED_VERTS and len(self._order) > 1:
            self._forget(self._order[0])
        return cell

    def _bucket_arrays(self, level: int):
        """Indices de tile e mascara de vetor para a oitava, calculados uma vez."""
        if self._buckets is not None and self._buckets[0] == level:
            return self._buckets[1:]
        ox, oy = self._origin
        ts = self._tile_size(level)
        thr = self._threshold(level)
        bb = self._bbox
        with np.errstate(invalid="ignore"):
            cx = (bb[:, 0] + bb[:, 2]) * 0.5 - ox
            cy = (bb[:, 1] + bb[:, 3]) * 0.5 - oy
            big = (self._flags & _GEOMETRY).astype(bool) & (self._size >= thr)
            big &= np.isfinite(cx) & np.isfinite(cy)
            tx = np.where(big, np.floor(np.nan_to_num(cx) / ts), 0).astype(np.int64)
            ty = np.where(big, np.floor(np.nan_to_num(cy) / ts), 0).astype(np.int64)
        self._buckets = (level, tx, ty, big)
        return tx, ty, big

    def _build_cell(self, level: int, tx, ty) -> _Cell:
        tx_all, ty_all, big = self._bucket_arrays(level)
        ts = self._tile_size(level)
        if tx is None:
            # Uma entidade maior que o proprio tile nao cabe na grade: vai para o
            # balde dos grandes, desenhado sempre. Sao poucas, por definicao.
            sel = np.flatnonzero(big & (self._size > ts))
        else:
            sel = np.flatnonzero(big & (self._size <= ts) & (tx_all == tx) & (ty_all == ty))

        cell = _Cell()
        if sel.size == 0:
            return cell
        ox, oy = self._origin
        sagitta = self._sagitta(level)
        layers = self._layer
        acis = self._aci
        for i in sel.tolist():
            entity = self._ents[i]
            if entity is None or not entity.is_alive:
                continue
            self._bake(cell, entity, sagitta, ox, oy, int(layers[i]), int(acis[i]))
        return cell

    def _bake(self, cell: _Cell, entity, sagitta: float, ox: float, oy: float,
              layer: int, aci: int) -> None:
        if entity.dxftype() == "HATCH":
            self._bake_hatch(cell, entity, sagitta, ox, oy, layer, aci)
            return
        key = (layer, aci, 255)
        path = None
        for poly in entity_point_lists(entity, sagitta, expand_blocks=True):
            if len(poly) < 2:
                continue
            if path is None:
                path = cell.stroke(key)
            path.moveTo(poly[0][0] - ox, poly[0][1] - oy)
            for x, y in poly[1:]:
                path.lineTo(x - ox, y - oy)
            cell.verts += len(poly)

    def _bake_hatch(self, cell: _Cell, hatch, sagitta: float, ox: float, oy: float,
                    layer: int, aci: int) -> None:
        try:
            alpha = int(round(255 * (1.0 - float(hatch.transparency))))
        except (TypeError, ValueError):
            alpha = 255
        key = (layer, aci, max(25, min(alpha, 255)))

        if bool(hatch.dxf.get("solid_fill", 0)):
            path = cell.fill(key)
            for poly in entity_point_lists(hatch, sagitta):
                if len(poly) < 3:
                    continue
                path.moveTo(poly[0][0] - ox, poly[0][1] - oy)
                for x, y in poly[1:]:
                    path.lineTo(x - ox, y - oy)
                path.closeSubpath()
                cell.verts += len(poly)
            return

        path = cell.stroke(key)
        try:
            for n, line in enumerate(hatch.render_pattern_lines()):
                if n >= MAX_HATCH_LINES:  # protege contra escala acidentalmente minuscula
                    break
                start, end = line
                path.moveTo(start.x - ox, start.y - oy)
                path.lineTo(end.x - ox, end.y - oy)
                cell.verts += 2
        except (ValueError, ZeroDivisionError, AttributeError):
            return
