"""Indice espacial hierarquico em grades esparsas.

Cada entidade entra na oitava cuja celula comporta o seu bbox. Assim um eixo de
rodovia de dezenas de quilometros nao e copiado para milhoes de celulas e tambem
nao vira uma excecao consultada em todo movimento do mouse. As linhas e colunas
ocupadas ficam ordenadas sob demanda; uma consulta grande visita somente celulas
que existem, nunca o retangulo inteiro de celulas vazias.
"""

from __future__ import annotations

import bisect
import math
import statistics
from collections.abc import Iterable

from .geometry import BBox, Vec2


class GridIndex:
    """Grade esparsa multinivel com insercao e remocao incrementais."""

    def __init__(self, cell_size: float | None = None):
        #: Aresta da celula do nivel zero. Niveis seguintes dobram a aresta.
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int, int], set] = {}
        self._rows: dict[int, dict[int, dict[int, set]]] = {}
        self._boxes: dict[object, BBox] = {}
        self._levels: dict[object, int] = {}
        self._row_keys: dict[int, list[int]] = {}
        self._x_keys: dict[tuple[int, int], list[int]] = {}
        # Mantido por compatibilidade com diagnosticos antigos. A grade
        # hierarquica nao precisa mais de entidades "grandes" globais.
        self._large: set = set()

    def __len__(self) -> int:
        return len(self._boxes)

    def clear(self) -> None:
        self._cells.clear()
        self._rows.clear()
        self._boxes.clear()
        self._levels.clear()
        self._row_keys.clear()
        self._x_keys.clear()
        self._large.clear()

    def _cell_size(self, level: int) -> float:
        return (self.cell_size or 1.0) * (2.0**level)

    def _level_for(self, b: BBox) -> int:
        base = self.cell_size or 1.0
        size = max(b.width, b.height)
        if size <= base:
            return 0
        return max(0, int(math.ceil(math.log2(size / base))))

    def _keys_for(self, b: BBox, level: int | None = None) -> list[tuple[int, int]]:
        level = self._level_for(b) if level is None else level
        cs = self._cell_size(level)
        x0, y0 = int(math.floor(b.minx / cs)), int(math.floor(b.miny / cs))
        x1, y1 = int(math.floor(b.maxx / cs)), int(math.floor(b.maxy / cs))
        return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    def build(self, items: Iterable[tuple[object, BBox]]) -> None:
        """Reconstroi o indice. ``items`` contem pares ``(chave, bbox)``."""
        pairs = [(k, b) for k, b in items if not b.is_empty]
        self.clear()
        if not pairs:
            return
        if self.cell_size is None:
            # A media era dominada por poucos eixos gigantes e deixava dezenas
            # de milhares de objetos na mesma celula. A mediana descreve o porte
            # da entidade comum; as grandes sobem de nivel por conta propria.
            sizes = [max(b.width, b.height) for _, b in pairs]
            positive = [s for s in sizes if s > 0.0]
            typical = statistics.median(positive) if positive else 1.0
            self.cell_size = max(typical * 2.0, 1e-6)
        for key, box in pairs:
            self._insert(key, box)

    def _insert(self, key, b: BBox) -> None:
        level = self._level_for(b)
        self._boxes[key] = b
        self._levels[key] = level
        rows = self._rows.setdefault(level, {})
        for x, y in self._keys_for(b, level):
            cell = self._cells.setdefault((level, x, y), set())
            cell.add(key)
            rows.setdefault(y, {})[x] = cell
            self._row_keys.pop(level, None)
            self._x_keys.pop((level, y), None)

    def insert(self, key, b: BBox) -> None:
        if b.is_empty:
            return
        if self.cell_size is None:
            self.cell_size = max(max(b.width, b.height) * 2.0, 1e-6)
        if key in self._boxes:
            self.remove(key)
        self._insert(key, b)

    def remove(self, key) -> None:
        b = self._boxes.get(key)
        level = self._levels.get(key)
        if b is None or level is None:
            return
        rows = self._rows.get(level, {})
        for x, y in self._keys_for(b, level):
            cell_key = (level, x, y)
            cell = self._cells.get(cell_key)
            if cell is None:
                continue
            cell.discard(key)
            if cell:
                continue
            del self._cells[cell_key]
            row = rows.get(y)
            if row is not None:
                row.pop(x, None)
                self._x_keys.pop((level, y), None)
                if not row:
                    rows.pop(y, None)
                    self._row_keys.pop(level, None)
        if not rows:
            self._rows.pop(level, None)
            self._row_keys.pop(level, None)
        self._boxes.pop(key, None)
        self._levels.pop(key, None)

    def _sorted_rows(self, level: int, rows: dict[int, dict[int, set]]) -> list[int]:
        hit = self._row_keys.get(level)
        if hit is None:
            hit = self._row_keys[level] = sorted(rows)
        return hit

    def _sorted_x(self, level: int, y: int, row: dict[int, set]) -> list[int]:
        cache_key = (level, y)
        hit = self._x_keys.get(cache_key)
        if hit is None:
            hit = self._x_keys[cache_key] = sorted(row)
        return hit

    def query(self, b: BBox) -> set:
        """Chaves cujo bbox intersecta ``b`` sem percorrer celulas vazias."""
        if b.is_empty or not self._boxes:
            return set()
        out: set = set()
        for level, rows in self._rows.items():
            cs = self._cell_size(level)
            x0, y0 = int(math.floor(b.minx / cs)), int(math.floor(b.miny / cs))
            x1, y1 = int(math.floor(b.maxx / cs)), int(math.floor(b.maxy / cs))
            ys = self._sorted_rows(level, rows)
            ya = bisect.bisect_left(ys, y0)
            yb = bisect.bisect_right(ys, y1)
            for y in ys[ya:yb]:
                row = rows[y]
                xs = self._sorted_x(level, y, row)
                xa = bisect.bisect_left(xs, x0)
                xb = bisect.bisect_right(xs, x1)
                for x in xs[xa:xb]:
                    out.update(row[x])
        boxes = self._boxes
        return {key for key in out if boxes[key].intersects(b)}

    def query_point(self, p: Vec2, radius: float) -> set:
        return self.query(BBox(p.x - radius, p.y - radius, p.x + radius, p.y + radius))

    def extents(self) -> BBox:
        out = BBox()
        for b in self._boxes.values():
            out = out.union(b)
        return out
