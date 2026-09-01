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

import itertools
import math
import time

import numpy as np
from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QTransform

from ..core.dimensions import DIMENSION_TYPES
from ..core.entities import POINT_LIKE, entity_point_lists, insert_has_text
from ..core.geometry import decimate
from .styles import aci_to_qcolor

# Aresta da celula base, em pixels de tela da oitava. Um tile e a menor unidade
# que o desenho progressivo consegue emitir: a construcao dele e fatiavel, mas o
# drawPath do resultado nao. Com 512 px, um tile denso levava 100 ms para ser
# desenhado e furava o orcamento da fatia.
TILE_PX = 256.0
MAX_RANK = 6  # classes de tamanho acima da celula base (ver _buckets_for)
TINY_PX = 2.0  # entidade menor que isto vira tinta em vez de vetor
MAX_VECTOR_ENTITIES = 30_000  # acima disso sobe uma oitava (LOD mais agressivo)
MAX_LEVEL_ESCALATION = 2  # teto da escalada: tinta nunca engole nada > ~8 px
MAX_CACHED_VERTS = 3_000_000  # teto do cache de tiles, em vertices
MAX_MARKERS = 3_000  # textos/pontos rotulados por quadro
MAX_HATCH_LINES = 5_000  # linhas de padrao por hachura

# LOD por tipo -- tamanho na tela, em pixels, abaixo do qual nao vale desenhar a
# coisa inteira. Uma hachura de 20 px nao mostra o padrao, so uma mancha; um
# bloco de simbolo de 10 px nao mostra o desenho do simbolo, so uma marca. Sem
# esses cortes, entrar num zoom intermediario de um cadastro hachurado assava
# 2.7 milhoes de vertices e levava 20 s.
HATCH_PATTERN_MIN_PX = 64.0
HATCH_SOLID_ALPHA = 90  # opacidade da mancha que substitui o padrao
BLOCK_DETAIL_MIN_PX = 20.0
# Simplificar uma polilinha curta nunca tira vertice -- um retangulo de lote tem
# cinco e todos sao cantos. So vale a pena onde ha vertices para descartar.
DECIMATE_MIN_VERTS = 8
# Conferir o relogio a cada entidade custaria mais que processar uma; a cada 512
# o desvio do orcamento fica bem abaixo de um milissegundo.
_CHUNK = 512

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


class _InkPass:
    """A passada de tinta, fatiada em lotes.

    Com o zoom bem aberto sao centenas de milhares de entidades sub-pixel; fazer
    o scatter de uma vez custa dezenas de milissegundos e estoura o orcamento da
    fatia. A imagem so vai para a tela quando o ultimo lote entra.
    """

    BATCH = 40_000

    def __init__(self, display, vp, idx, dark, dpr):
        self.display = display
        self.vp = vp
        self.idx = idx
        self.dark = dark
        self.dpr = dpr
        self.at = 0
        self.buf = None

    def __call__(self, painter, deadline) -> bool:
        w = max(1, int(round(self.vp.width * self.dpr)))
        h = max(1, int(round(self.vp.height * self.dpr)))
        if self.buf is None:
            self.buf = self.display._ink_buffer(w, h)
        while self.at < self.idx.size:
            stop = min(self.at + self.BATCH, self.idx.size)
            self.display._scatter_ink(
                self.buf, self.vp, self.idx[self.at : stop], self.dark, self.dpr
            )
            self.at = stop
            if deadline is not None and time.perf_counter() >= deadline:
                break
        if self.at < self.idx.size:
            return False
        img = QImage(self.buf.data, w, h, w * 4, QImage.Format_RGBA8888)
        img.setDevicePixelRatio(self.dpr)
        painter.save()
        painter.resetTransform()
        painter.drawImage(0, 0, img)
        painter.restore()
        return True


class _Cell:
    """Geometria de um tile numa oitava de zoom, agrupada por caneta.

    `pending` guarda as entidades que ainda faltam assar. Um tile denso leva
    dezenas de milissegundos para ser construido; poder parar no meio e o que
    permite fatiar a regeneracao sem travar a interface.
    """

    __slots__ = ("strokes", "fills", "verts", "pending")

    def __init__(self, pending: list[int] | None = None):
        self.strokes: dict[tuple, QPainterPath] = {}
        self.fills: dict[tuple, QPainterPath] = {}
        self.verts = 0
        self.pending: list[int] = pending or []

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
        #: Um a mais que o maior slot ja usado. Os arrays sao alocados com folga
        #: para o crescimento; varrer so a parte viva corta o culling pela metade.
        self._high = 0
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
        #: Reconstrucao em andamento: (entidades, quantas ja entraram, cores).
        self._rebuilding: tuple | None = None
        self._dirty_queue: list[str] = []
        self._rgb: dict[bool, np.ndarray] = {}
        self._ink_buf: np.ndarray | None = None

    # ---------------- sincronizacao com o documento ----------------

    def prepare(self, deadline: float | None = None) -> bool:
        """Absorve as mudancas do documento. True quando esta pronta para desenhar.

        Indexar 200 mil entidades leva quase um segundo -- tempo demais para uma
        etapa so. O trabalho e fatiado como o resto do quadro, e quem chama
        insiste ate receber True.
        """
        if self.doc.geometry_revision != self._revision:
            self._revision = self.doc.geometry_revision
            full, dirty = self.doc.consume_geometry_changes()
            if full or self._rebuilding is not None:
                # Mudanca durante uma reconstrucao: recomecar e mais simples do
                # que costurar o novo estado no meio do antigo, e e raro.
                self._start_rebuild()
            else:
                self._dirty_queue.extend(dirty)

        if self._rebuilding is not None:
            return self._advance_rebuild(deadline)
        if self._dirty_queue:
            return self._advance_dirty(deadline)
        return True

    def sync(self) -> None:
        """Absorve tudo de uma vez. Atalho para quem nao fatia."""
        while not self.prepare(None):
            pass

    def _start_rebuild(self) -> None:
        """Prepara a varredura do modelspace. As entidades vem por lotes."""
        doc = self.doc
        cap = max(len(doc) * 2, 64)
        self._slot = {}
        self._ents = [None] * cap
        self._free = []
        self._high = 0
        self._bbox = np.full((cap, 4), np.nan)
        self._size = np.zeros(cap)
        self._aci = np.zeros(cap, np.int32)
        self._layer = np.zeros(cap, np.int32)
        self._flags = np.zeros(cap, np.uint8)
        self._layer_ids = {}
        self._layer_names = []
        self._dirty_queue.clear()
        self.clear()
        self._rebuilding = (iter(doc.msp), {})

    def _advance_rebuild(self, deadline: float | None) -> bool:
        source, colors = self._rebuilding
        boxes = self.doc.index._boxes
        done = False
        while True:
            batch = list(itertools.islice(source, _CHUNK))
            if not batch:
                done = True
                break
            for e in batch:
                handle = e.dxf.get("handle")
                if handle is None or not e.is_alive:
                    continue
                i = self._high
                if i >= len(self._ents):
                    self._grow()
                self._high = i + 1
                self._slot[handle] = i
                self._ents[i] = e
                self._fill_slot(i, e, boxes.get(handle), colors)
            if deadline is not None and time.perf_counter() >= deadline:
                break
        if not done:
            return False
        self._rebuilding = None
        self._free = list(range(self._high, len(self._ents)))
        self._origin = self._pick_origin()
        self._measure_density()
        return True

    def _grow(self) -> None:
        old = len(self._ents)
        extra = max(old, 64)
        self._ents.extend([None] * extra)
        self._bbox = np.vstack([self._bbox, np.full((extra, 4), np.nan)])
        self._size = np.concatenate([self._size, np.zeros(extra)])
        self._aci = np.concatenate([self._aci, np.zeros(extra, np.int32)])
        self._layer = np.concatenate([self._layer, np.zeros(extra, np.int32)])
        self._flags = np.concatenate([self._flags, np.zeros(extra, np.uint8)])

    def _advance_dirty(self, deadline: float | None) -> bool:
        queue = self._dirty_queue
        done = 0
        for handle in queue:
            self._refresh_slot(handle)
            done += 1
            if deadline is not None and done % _CHUNK == 0 and time.perf_counter() >= deadline:
                break
        del queue[:done]
        if queue:
            return False
        # Um desenho que comeca vazio e cresce muda de densidade: quando o porte
        # do documento dobra, a grade de tiles e redimensionada.
        n = len(self._slot)
        if n > 2 * self._measured_n or n * 2 < self._measured_n:
            self._origin = self._pick_origin()
            self._measure_density()
            self.clear()
        return True

    def clear(self) -> None:
        self._cells.clear()
        self._order.clear()
        self._verts = 0
        self._buckets = None

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
            # Um bloco so precisa do passe de rotulo se tiver texto dentro; a
            # maioria num cadastro e simbolo puro, e decompor cada um para
            # descobrir que nao ha texto custava quase um segundo por vista.
            if t != "INSERT" or insert_has_text(entity):
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
        if not self._free:
            old = len(self._ents)
            self._grow()
            self._free = list(range(old, len(self._ents)))
        i = self._free.pop()
        self._high = max(self._high, i + 1)
        return i

    def _drop_cells_at(self, box) -> None:
        """Descarta os tiles que cobrem `box`, em todas as oitavas."""
        if not np.isfinite(box).all():
            return
        ox, oy = self._origin
        dead = []
        for key in self._cells:
            level, (rank, tx, ty) = key
            cell = self._tile_size(level) * (2.0**rank)
            if (
                box[0] - cell <= ox + (tx + 1) * cell
                and box[2] + cell >= ox + tx * cell
                and box[1] - cell <= oy + (ty + 1) * cell
                and box[3] + cell >= oy + ty * cell
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
        """Aresta da celula base: 512 px da oitava.

        Encolhe junto com o zoom, e e o que se quer -- assim uma vista de perto
        carrega so o que esta perto. Quem nao cabe nessa celula nao vira excecao:
        entra numa classe de tamanho maior (ver _buckets_for).
        """
        return min(max((2.0**level) * TILE_PX, self._floor_tile), self._ceil_tile)

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
        # Piso: uma celula menor que a entidade mediana joga quase tudo nas
        # classes maiores e desperdica a divisao.
        self._floor_tile = max(float(np.median(self._size[ok])) * 2.0, 1e-6)
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

    def plan(self, vp, dark: bool = True, dpr: float = 1.0) -> tuple[list, list]:
        """Monta a lista de etapas de desenho e as entidades a rotular.

        Cada etapa e `f(painter, deadline)` e devolve True quando terminou. Um
        tile ainda nao construido consome varias chamadas ate ficar pronto, de
        modo que nem a construcao nem a rasterizacao travem a interface.

        Exige prepare() concluido: com uma reconstrucao pela metade os arrays
        ainda nao descrevem o desenho.
        """
        self.sync()
        if not self._slot:
            return [], []

        vis = vp.visible_bbox()
        n = self._high
        bb = self._bbox[:n]
        with np.errstate(invalid="ignore"):
            m = (
                (bb[:, 2] >= vis.minx)
                & (bb[:, 0] <= vis.maxx)
                & (bb[:, 3] >= vis.miny)
                & (bb[:, 1] <= vis.maxy)
            )
        visible = self._visible_layers()
        m &= visible[self._layer[:n]]
        idx = np.flatnonzero(m)
        if idx.size == 0:
            return [], []

        upw = 1.0 / max(vp.scale, 1e-12)
        level = self._choose_level(upw, idx)
        thr = self._threshold(level)

        flags = self._flags[idx]
        size = self._size[idx]
        has_geom = (flags & _GEOMETRY).astype(bool)
        tiny = has_geom & (size < thr)
        # Um bloco sub-pixel ja virou tinta: rotula-lo seria trabalho dobrado
        # para o mesmo pixel.
        markers = idx[(flags & _MARKER).astype(bool) & ~tiny]
        vectors = idx[has_geom & (size >= thr)]
        ink = idx[tiny]

        if markers.size > MAX_MARKERS:
            # Rotular nao cabe no orcamento: o texto vira tinta, como o QTEXT do
            # AutoCAD faz quando a fonte fica ilegivel.
            ink = np.concatenate([ink, markers])
            markers = markers[:0]

        steps = []
        if ink.size:
            # A tinta vem primeiro: poe na tela a silhueta do desenho inteiro
            # enquanto os tiles ainda estao sendo montados.
            steps.append(_InkPass(self, vp, ink, dark, dpr))
        for key in self._cell_keys(vp, vectors, level):
            steps.append(
                lambda p, d, k=key: self._draw_cell_step(p, vp, level, k, visible, dark, d)
            )
        entities = [self._ents[i] for i in markers.tolist() if self._ents[i] is not None]
        return steps, entities

    def paint(self, painter, vp, dark: bool = True, dpr: float = 1.0) -> list:
        """Desenha a cena inteira de uma vez. Atalho para quem nao fatia."""
        steps, entities = self.plan(vp, dark, dpr)
        for step in steps:
            while not step(painter, math.inf):
                pass
        return entities

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

    def _scatter_ink(self, buf, vp, idx: np.ndarray, dark: bool, dpr: float) -> None:
        """Marca na grade de ocupacao as entidades sub-pixel do lote."""
        h, w = buf.shape[0], buf.shape[1]
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
        iy, ix = iy[keep], ix[keep]
        buf[iy, ix, 0:3] = self._rgb_table(dark)[np.clip(aci[keep], 0, 259)]
        buf[iy, ix, 3] = 255

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

    def _cell_keys(self, vp, idx: np.ndarray, level: int) -> list:
        """Tiles a desenhar, do centro da vista para fora.

        A ordem importa no desenho progressivo: numa regeneracao longa o usuario
        ve primeiro o que esta olhando, e a periferia chega depois. As classes
        maiores vem antes, porque sao poucas e cobrem area grande.
        """
        if idx.size == 0:
            return []
        groups, ranks = self._buckets_for(level)
        if not groups:
            return []
        ox, oy = self._origin
        base = self._tile_size(level)
        vis = vp.visible_bbox()
        cx, cy = vp.center.x - ox, vp.center.y - oy

        keys = []
        for rank in reversed(ranks):
            cell = base * (2.0**rank)
            tx0 = int(math.floor((vis.minx - ox) / cell)) - 1
            tx1 = int(math.floor((vis.maxx - ox) / cell)) + 1
            ty0 = int(math.floor((vis.miny - oy) / cell)) - 1
            ty1 = int(math.floor((vis.maxy - oy) / cell)) + 1
            if (tx1 - tx0 + 1) * (ty1 - ty0 + 1) > 4096:  # vista absurda
                continue
            near = []
            for tx in range(tx0, tx1 + 1):
                for ty in range(ty0, ty1 + 1):
                    if (rank, tx, ty) not in groups:
                        continue
                    dx = (tx + 0.5) * cell - cx
                    dy = (ty + 0.5) * cell - cy
                    near.append((dx * dx + dy * dy, (rank, tx, ty)))
            near.sort(key=lambda item: item[0])
            keys.extend(k for _, k in near)
        return keys

    def _transform(self, vp) -> QTransform:
        """Local -> tela. Os numeros que chegam ao Qt sao pequenos; ver o topo."""
        ox, oy = self._origin
        s = vp.scale
        return QTransform(
            s,
            0.0,
            0.0,
            -s,
            (ox - vp.center.x) * s + vp.width * 0.5,
            vp.height * 0.5 + (vp.center.y - oy) * s,
        )

    def _draw_cell_step(self, painter, vp, level, key, visible, dark, deadline) -> bool:
        """Constroi o tile dentro do orcamento e, quando pronto, desenha."""
        cell = self._cell(level, key, deadline)
        if cell.pending:
            return False  # a construcao continua na proxima fatia de tempo

        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setWorldTransform(self._transform(vp), True)
        try:
            # Preenchimento antes do traco: uma hachura nao pode cobrir a
            # geometria que a delimita. O tile guarda a geometria agrupada por
            # camada justamente para que apagar uma camada seja pular um grupo,
            # e nao reconstruir o cache.
            painter.setPen(Qt.NoPen)
            for k, path in cell.fills.items():
                if not visible[k[0]]:
                    continue
                painter.setBrush(QBrush(self._qcolor(k, dark)))
                painter.drawPath(path)
            painter.setBrush(Qt.NoBrush)
            for k, path in cell.strokes.items():
                if not visible[k[0]]:
                    continue
                pen = QPen(self._qcolor(k, dark), STROKE_WIDTH)
                pen.setCosmetic(True)  # espessura em pixels, nao em metros
                painter.setPen(pen)
                painter.drawPath(path)
        finally:
            painter.restore()
        return True

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

    def _cell(self, level: int, tile, deadline: float | None = None) -> _Cell:
        """Tile pronto para desenho, ou parcialmente construido se o tempo acabou."""
        key = (level, tile)
        cell = self._cells.get(key)
        if cell is None:
            cell = _Cell(self._members(level, tile))
            self._cells[key] = cell
            self._order.append(key)
        else:
            try:
                self._order.remove(key)
            except ValueError:
                pass
            self._order.append(key)
        if cell.pending:
            self._bake_pending(cell, level, deadline)
            if not cell.pending:
                self._evict()
        return cell

    def _evict(self) -> None:
        """Descarta os tiles menos usados quando o cache passa do teto."""
        while self._verts > MAX_CACHED_VERTS and len(self._order) > 1:
            victim = self._order[0]
            if self._cells[victim].pending:  # nao joga fora o que esta sendo feito
                break
            self._forget(victim)

    def _buckets_for(self, level: int):
        """Entidades agrupadas por tile, em classes de tamanho.

        Uma grade so nao serve: o tile precisa ser pequeno para o zoom fundo
        carregar pouca coisa, e grande para uma curva de nivel de 300 m caber
        dentro dele. Mandar as grandes para um balde unico resolvia o primeiro
        problema e criava outro -- o balde acabava desenhado em toda vista.

        Entao cada entidade entra na grade cuja celula a comporta: classe 0 usa a
        celula do nivel, classe r usa uma celula 2^r vezes maior. Toda entidade
        fica numa grade recortavel, e nenhuma vista carrega o que esta longe.
        """
        if self._buckets is not None and self._buckets[0] == level:
            return self._buckets[1], self._buckets[2]

        ox, oy = self._origin
        base = self._tile_size(level)
        thr = self._threshold(level)
        n = self._high
        bb = self._bbox[:n]
        size = self._size[:n]
        with np.errstate(invalid="ignore"):
            cx = (bb[:, 0] + bb[:, 2]) * 0.5 - ox
            cy = (bb[:, 1] + bb[:, 3]) * 0.5 - oy
            keep = (self._flags[:n] & _GEOMETRY).astype(bool) & (size >= thr)
            keep &= np.isfinite(cx) & np.isfinite(cy)
            idx = np.flatnonzero(keep)
            if idx.size == 0:
                self._buckets = (level, {}, (), idx)
                return {}, ()
            ratio = np.maximum(size[idx] / base, 1.0)
            rank = np.ceil(np.log2(ratio)).astype(np.int64)
            np.clip(rank, 0, MAX_RANK, out=rank)
            cell = base * (2.0**rank)
            tx = np.floor(cx[idx] / cell).astype(np.int64)
            ty = np.floor(cy[idx] / cell).astype(np.int64)

        # Uma ordenacao agrupa tudo; sem ela seria uma varredura por tile.
        key = (rank << 48) + (tx << 24) + (ty + (1 << 23))
        order = np.argsort(key, kind="stable")
        key, members = key[order], idx[order]
        rank, tx, ty = rank[order], tx[order], ty[order]
        cuts = np.flatnonzero(key[1:] != key[:-1]) + 1
        starts = np.concatenate(([0], cuts))
        stops = np.concatenate((cuts, [key.size]))
        # O grupo guarda a FATIA do array, nao a lista: converter as 137 mil
        # entidades para listas Python aqui custava 300 ms numa etapa que nao da
        # para fatiar. Cada tile materializa a sua parte quando for construido.
        groups: dict[tuple[int, int, int], tuple[int, int]] = {}
        for a, b in zip(starts.tolist(), stops.tolist(), strict=True):
            groups[(int(rank[a]), int(tx[a]), int(ty[a]))] = (a, b)
        ranks = tuple(sorted({int(r) for r in np.unique(rank)}))

        self._buckets = (level, groups, ranks, members)
        return groups, ranks

    def _members(self, level: int, key) -> list[int]:
        """Entidades que pertencem ao tile nesta oitava."""
        groups, _ = self._buckets_for(level)
        span = groups.get(key)
        if span is None:
            return []
        return self._buckets[3][span[0] : span[1]].tolist()

    def _bake_pending(self, cell: _Cell, level: int, deadline: float | None) -> None:
        """Assa a fila do tile ate o prazo. O que sobrar fica para a proxima."""
        ox, oy = self._origin
        sagitta = self._sagitta(level)
        layers = self._layer
        acis = self._aci
        pending = cell.pending
        done = 0
        upw = 2.0**level  # unidades do mundo por pixel nesta oitava
        sizes = self._size
        for i in pending:
            entity = self._ents[i]
            done += 1
            if entity is not None and entity.is_alive:
                before = cell.verts
                self._bake(
                    cell, entity, sagitta, ox, oy, int(layers[i]), int(acis[i]),
                    float(sizes[i]) / upw,
                )
                self._verts += cell.verts - before
            # A granularidade tem de acompanhar a entidade mais cara, nao a
            # media: uma polilinha de 8 mil vertices com bulge leva 19 ms
            # sozinha, e conferir o relogio a cada oito entidades custa menos de
            # um microssegundo.
            if deadline is not None and done % 8 == 0 and time.perf_counter() >= deadline:
                break
        del pending[:done]

    def _bake(self, cell: _Cell, entity, sagitta: float, ox: float, oy: float,
              layer: int, aci: int, size_px: float) -> None:
        """Assa a entidade no tile, no detalhe que a escala do nivel comporta."""
        t = entity.dxftype()
        if t == "HATCH":
            self._bake_hatch(cell, entity, sagitta, ox, oy, layer, aci, size_px)
            return
        key = (layer, aci, 255)
        if t == "INSERT" and size_px < BLOCK_DETAIL_MIN_PX:
            # O simbolo do bloco nao se le neste tamanho: uma cruz do tamanho
            # dele diz a mesma coisa por dois segmentos em vez de dezenas.
            self._bake_mark(cell.stroke(key), entity, ox, oy)
            cell.verts += 4
            return
        path = None
        for poly in entity_point_lists(entity, sagitta, expand_blocks=True):
            if len(poly) < 2:
                continue
            if len(poly) > DECIMATE_MIN_VERTS:
                poly = decimate(poly, sagitta)
            if path is None:
                path = cell.stroke(key)
            path.moveTo(poly[0][0] - ox, poly[0][1] - oy)
            for x, y in poly[1:]:
                path.lineTo(x - ox, y - oy)
            cell.verts += len(poly)

    def _bake_mark(self, path: QPainterPath, entity, ox: float, oy: float) -> None:
        """Cruz do tamanho da entidade, no lugar do desenho dela."""
        i = self._slot.get(entity.dxf.get("handle"))
        if i is None:
            return
        minx, miny, maxx, maxy = self._bbox[i]
        cx, cy = (minx + maxx) * 0.5 - ox, (miny + maxy) * 0.5 - oy
        r = max(maxx - minx, maxy - miny) * 0.5
        path.moveTo(cx - r, cy)
        path.lineTo(cx + r, cy)
        path.moveTo(cx, cy - r)
        path.lineTo(cx, cy + r)

    def _bake_hatch(self, cell: _Cell, hatch, sagitta: float, ox: float, oy: float,
                    layer: int, aci: int, size_px: float) -> None:
        try:
            alpha = int(round(255 * (1.0 - float(hatch.transparency))))
        except (TypeError, ValueError):
            alpha = 255
        alpha = max(25, min(alpha, 255))
        solid = bool(hatch.dxf.get("solid_fill", 0))

        # Um padrao de hachura numa area pequena na tela nao mostra padrao
        # nenhum: mostra uma mancha. Desenhar a mancha custa um poligono; o
        # padrao custa milhares de segmentos para o mesmo resultado visual.
        if not solid and size_px < HATCH_PATTERN_MIN_PX:
            solid = True
            alpha = min(alpha, HATCH_SOLID_ALPHA)

        key = (layer, aci, alpha)
        if solid:
            path = cell.fill(key)
            for poly in entity_point_lists(hatch, sagitta):
                if len(poly) < 3:
                    continue
                if len(poly) > DECIMATE_MIN_VERTS:
                    poly = decimate(poly, sagitta)
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
