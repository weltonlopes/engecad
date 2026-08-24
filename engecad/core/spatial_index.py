"""Indice espacial em grade uniforme.

Serve para achar candidatos a snap/selecao perto do cursor sem varrer o
desenho inteiro a cada movimento do mouse. Grade uniforme e o suficiente
para desenhos cadastrais (entidades de tamanho parecido, bem distribuidas);
se um dia precisar, trocar por R-tree e local unico.
"""

from __future__ import annotations

import math
from collections.abc import Iterable

from .geometry import BBox, Vec2


class GridIndex:
    def __init__(self, cell_size: float | None = None):
        self.cell_size = cell_size
        self._cells: dict[tuple[int, int], set] = {}
        self._boxes: dict[object, BBox] = {}
        # entidades cujo bbox cobre celulas demais: varridas sempre
        self._large: set = set()

    def __len__(self) -> int:
        return len(self._boxes)

    def clear(self) -> None:
        self._cells.clear()
        self._boxes.clear()
        self._large.clear()

    def _keys_for(self, b: BBox):
        cs = self.cell_size or 1.0
        x0, y0 = int(math.floor(b.minx / cs)), int(math.floor(b.miny / cs))
        x1, y1 = int(math.floor(b.maxx / cs)), int(math.floor(b.maxy / cs))
        # Um bbox gigante (ex.: entidade que cruza o desenho todo) pode gerar
        # milhoes de celulas; nesse caso vai para a lista de "grandes".
        if (x1 - x0 + 1) * (y1 - y0 + 1) > 4096:
            return None
        return [(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)]

    def build(self, items: Iterable[tuple[object, BBox]]) -> None:
        """Reconstroi o indice. items = (chave, bbox)."""
        pairs = [(k, b) for k, b in items if not b.is_empty]
        self.clear()
        if not pairs:
            return
        if self.cell_size is None:
            # celula ~ 2x o tamanho medio das entidades: poucas celulas por
            # consulta e poucas entidades por celula.
            avg = sum(max(b.width, b.height) for _, b in pairs) / len(pairs)
            self.cell_size = max(avg * 2.0, 1e-6)
        for k, b in pairs:
            self._insert(k, b)

    def _insert(self, key, b: BBox) -> None:
        self._boxes[key] = b
        keys = self._keys_for(b)
        if keys is None:
            self._large.add(key)
            return
        for ck in keys:
            self._cells.setdefault(ck, set()).add(key)

    def insert(self, key, b: BBox) -> None:
        if b.is_empty:
            return
        if self.cell_size is None:
            self.cell_size = max(max(b.width, b.height) * 2.0, 1e-6)
        if key in self._boxes:
            self.remove(key)
        self._insert(key, b)

    def remove(self, key) -> None:
        b = self._boxes.pop(key, None)
        if b is None:
            return
        self._large.discard(key)
        keys = self._keys_for(b)
        if keys is None:
            return
        for ck in keys:
            cell = self._cells.get(ck)
            if cell is not None:
                cell.discard(key)
                if not cell:
                    del self._cells[ck]

    def query(self, b: BBox) -> set:
        """Chaves cujo bbox pode intersectar b (pode devolver falsos positivos)."""
        if b.is_empty or not self._boxes:
            return set()
        keys = self._keys_for(b)
        out: set = set(self._large)
        if keys is None:
            return set(self._boxes)
        for ck in keys:
            out |= self._cells.get(ck, set())
        return {k for k in out if self._boxes[k].intersects(b)}

    def query_point(self, p: Vec2, radius: float) -> set:
        return self.query(BBox(p.x - radius, p.y - radius, p.x + radius, p.y + radius))

    def extents(self) -> BBox:
        out = BBox()
        for b in self._boxes.values():
            out = out.union(b)
        return out
