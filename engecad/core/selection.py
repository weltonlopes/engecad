"""Conjunto de selecao do desenho."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from .entities import entity_bbox
from .geometry import BBox


class Selection:
    """Entidades selecionadas, em ordem de escolha e sem repetir.

    Guarda as entidades e nao os handles: as operacoes de edicao trabalham
    direto sobre elas, e entidades removidas sao filtradas na leitura.
    """

    def __init__(self, doc=None):
        self.doc = doc
        self._items: list = []
        self.changed: list[Callable[[], None]] = []
        #: Sobe a cada alteracao. Quem cacheia algo derivado da selecao (grips,
        #: contornos) compara este numero em vez de recalcular por quadro.
        self.revision = 0

    # ---------------- leitura ----------------

    def _in_document(self, entity) -> bool:
        """A entidade ainda pertence ao desenho?

        Nao basta checar is_alive: apagar usa unlink_entity, que tira a
        entidade do layout mas a mantem viva de proposito, para o desfazer
        poder religa-la. O criterio certo e estar no indice do documento.
        """
        if entity is None or not entity.is_alive:
            return False
        if self.doc is None:
            return True
        handle = entity.dxf.get("handle")
        return handle is not None and self.doc.entity_by_handle(handle) is entity

    @property
    def items(self) -> list:
        return [e for e in self._items if self._in_document(e)]

    def __iter__(self) -> Iterator:
        return iter(self.items)

    def __len__(self) -> int:
        return len(self.items)

    def __contains__(self, entity) -> bool:
        return entity in self._items

    def __bool__(self) -> bool:
        return bool(self.items)

    def bbox(self) -> BBox:
        b = BBox()
        for e in self.items:
            b = b.union(entity_bbox(e))
        return b

    def summary(self) -> str:
        n = len(self)
        if n == 0:
            return "nada selecionado"
        kinds: dict[str, int] = {}
        for e in self.items:
            kinds[e.dxftype()] = kinds.get(e.dxftype(), 0) + 1
        detail = ", ".join(f"{v} {k}" for k, v in sorted(kinds.items()))
        return f"{n} selecionado(s): {detail}"

    # ---------------- escrita ----------------

    def _notify(self) -> None:
        self.revision += 1
        for cb in self.changed:
            cb()

    def set(self, entities: Iterable) -> None:
        self._items = []
        for e in entities:
            if self._in_document(e) and e not in self._items:
                self._items.append(e)
        self._notify()

    def add(self, entities: Iterable) -> None:
        changed = False
        for e in entities:
            if self._in_document(e) and e not in self._items:
                self._items.append(e)
                changed = True
        if changed:
            self._notify()

    def remove(self, entities: Iterable) -> None:
        changed = False
        for e in entities:
            if e in self._items:
                self._items.remove(e)
                changed = True
        if changed:
            self._notify()

    def toggle(self, entities: Iterable) -> None:
        for e in entities:
            if not self._in_document(e):
                continue
            if e in self._items:
                self._items.remove(e)
            else:
                self._items.append(e)
        self._notify()

    def clear(self) -> None:
        if self._items:
            self._items = []
            self._notify()

    def prune(self) -> None:
        """Descarta entidades que sairam do desenho.

        Como no AutoCAD: apagar esvazia a selecao, e desfazer o apagar traz os
        objetos de volta ao desenho mas nao a selecao.
        """
        alive = [e for e in self._items if self._in_document(e)]
        if len(alive) != len(self._items):
            self._items = alive
            self._notify()
