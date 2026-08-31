"""Documento do EngeCAD: um DXF do ezdxf + CRS + undo + indice espacial.

O formato nativo E o DXF -- nao ha serializacao propria a manter, e o arquivo
abre no AutoCAD/QGIS sem exportar. O que o ezdxf nao oferece (desfazer e busca
espacial) esta empilhado aqui por cima.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

from .crs import ProjectCRS
from .entities import entity_bbox
from .geometry import BBox, Vec2
from .snapshot import restore, snapshot
from .spatial_index import GridIndex
from .undo import Command, UndoStack

DEFAULT_DXFVERSION = "R2018"

# Cores ACI usadas nas camadas padrao de planta cadastral.
ACI_WHITE, ACI_RED, ACI_YELLOW, ACI_GREEN, ACI_CYAN, ACI_BLUE, ACI_MAGENTA = 7, 1, 2, 3, 4, 5, 6

DEFAULT_LAYERS = [
    ("0", ACI_WHITE),
    ("LIMITE", ACI_RED),
    ("DIVISA", ACI_YELLOW),
    ("EDIFICACAO", ACI_CYAN),
    ("VIA", ACI_GREEN),
    ("TEXTO", ACI_WHITE),
    ("COTA", ACI_MAGENTA),
]


class _EntityCommand(Command):
    """Base dos comandos que adicionam/removem entidades.

    Desfazer nao destroi a entidade: unlink_entity a tira do layout mas a
    mantem viva, entao refazer e so religa-la. Sem clonar documento.
    """

    def __init__(self, doc: Document, entities: list, name: str):
        self.doc = doc
        self.entities = entities
        self.name = name

    def _link(self) -> None:
        msp = self.doc.msp
        for e in self.entities:
            if e.is_alive:
                msp.add_entity(e)
                self.doc._index_add(e)
        self.doc._touch()

    def _unlink(self) -> None:
        msp = self.doc.msp
        for e in self.entities:
            if e.is_alive:
                self.doc._index_remove(e)
                msp.unlink_entity(e)
        self.doc._touch()


class AddEntities(_EntityCommand):
    redo = _EntityCommand._link
    undo = _EntityCommand._unlink


class DeleteEntities(_EntityCommand):
    redo = _EntityCommand._unlink
    undo = _EntityCommand._link


class ModifyGeometry(Command):
    """Edicao de geometria desfeita por instantaneo.

    Cobre mover, girar, espelhar, esticar grip, aparar e estender -- tudo que
    altera a forma sem criar nem apagar entidade. Ver core/snapshot.py para a
    razao de nao usarmos matriz inversa.
    """

    def __init__(self, doc: Document, records: list[tuple], name: str):
        self.doc = doc
        self.records = records  # (entidade, antes, depois)
        self.name = name

    def _apply(self, after: bool) -> None:
        for entity, before, depois in self.records:
            if entity.is_alive:
                restore(entity, depois if after else before)
                self.doc._index_update(entity)
        self.doc._touch()

    def redo(self) -> None:
        self._apply(True)

    def undo(self) -> None:
        self._apply(False)


class SetAttribs(Command):
    """Muda atributos DXF simples (camada, cor...) das entidades, desfazivel.

    Usado pelo painel de propriedades: nao mexe em geometria (isso e
    ModifyGeometry/snapshot), so em pares chave/valor do namespace dxf.
    """

    def __init__(self, doc: Document, records: list[tuple], name: str):
        self.doc = doc
        self.records = records  # (entidade, {attr: antes}, {attr: depois})
        self.name = name

    def _apply(self, after: bool) -> None:
        for entity, before, depois in self.records:
            if not entity.is_alive:
                continue
            for k, v in (depois if after else before).items():
                setattr(entity.dxf, k, v)
        self.doc._touch()

    def redo(self) -> None:
        self._apply(True)

    def undo(self) -> None:
        self._apply(False)


class Document:
    def __init__(
        self,
        drawing: Drawing,
        crs: ProjectCRS | None = None,
        path: str | Path | None = None,
    ):
        self.drawing = drawing
        self.crs = crs or ProjectCRS()
        self.path: Path | None = Path(path) if path else None
        self.undo = UndoStack()
        self.index = GridIndex()
        self._by_handle: dict[str, object] = {}
        self._modified = False
        self._current_layer = "0"
        self.changed: list[Callable[[], None]] = []
        self.rebuild_index()

    # ---------------- construcao ----------------

    @classmethod
    def new(
        cls, crs: ProjectCRS | str | None = None, dxfversion: str = DEFAULT_DXFVERSION
    ) -> Document:
        drawing = ezdxf.new(dxfversion, setup=True)
        drawing.header["$INSUNITS"] = 6  # metros: o mundo do EngeCAD e metrico
        drawing.header["$MEASUREMENT"] = 1
        doc = cls(drawing, crs if isinstance(crs, ProjectCRS) else ProjectCRS(crs))
        doc.setup_default_layers()
        doc._modified = False
        return doc

    @classmethod
    def open(cls, path: str | Path, crs: ProjectCRS | None = None) -> Document:
        p = Path(path)
        drawing = ezdxf.readfile(str(p))
        return cls(drawing, crs, path=p)

    def setup_default_layers(self) -> None:
        for name, color in DEFAULT_LAYERS:
            self.ensure_layer(name, color)

    # ---------------- estado ----------------

    @property
    def msp(self):
        return self.drawing.modelspace()

    @property
    def modified(self) -> bool:
        return self._modified

    @property
    def title(self) -> str:
        return self.path.name if self.path else "sem titulo"

    def _touch(self) -> None:
        self._modified = True
        for cb in self.changed:
            cb()

    def mark_saved(self) -> None:
        self._modified = False
        for cb in self.changed:
            cb()

    # ---------------- camadas ----------------

    @property
    def current_layer(self) -> str:
        return self._current_layer

    @current_layer.setter
    def current_layer(self, name: str) -> None:
        self.ensure_layer(name)
        self._current_layer = name

    def ensure_layer(self, name: str, color: int = ACI_WHITE):
        if name in self.drawing.layers:
            return self.drawing.layers.get(name)
        return self.drawing.layers.add(name, color=color)

    def layer_names(self) -> list[str]:
        return sorted(layer.dxf.name for layer in self.drawing.layers)

    def layer_is_visible(self, name: str) -> bool:
        try:
            layer = self.drawing.layers.get(name)
        except Exception:
            return True
        return layer.is_on() and not layer.is_frozen()

    def set_layer_visible(self, name: str, visible: bool) -> None:
        layer = self.drawing.layers.get(name)
        layer.on() if visible else layer.off()
        self._touch()

    def layer_color(self, name: str) -> int:
        try:
            return int(self.drawing.layers.get(name).dxf.color)
        except Exception:
            return ACI_WHITE

    def set_layer_color(self, name: str, aci: int) -> None:
        self.drawing.layers.get(name).dxf.color = int(aci)
        self._touch()

    def layer_is_locked(self, name: str) -> bool:
        try:
            return bool(self.drawing.layers.get(name).is_locked())
        except Exception:
            return False

    # ---------------- indice espacial ----------------

    def rebuild_index(self) -> None:
        self._by_handle.clear()
        items = []
        for e in self.msp:
            h = e.dxf.get("handle")
            if h is None:
                continue
            self._by_handle[h] = e
            items.append((h, entity_bbox(e)))
        self.index.build(items)

    def _index_add(self, entity) -> None:
        h = entity.dxf.get("handle")
        if h is None:
            return
        self._by_handle[h] = entity
        self.index.insert(h, entity_bbox(entity))

    def _index_update(self, entity) -> None:
        """Reposiciona a entidade no indice apos a geometria mudar."""
        h = entity.dxf.get("handle")
        if h is None:
            return
        self.index.remove(h)
        self.index.insert(h, entity_bbox(entity))
        self._by_handle[h] = entity

    def _index_remove(self, entity) -> None:
        h = entity.dxf.get("handle")
        if h is None:
            return
        self._by_handle.pop(h, None)
        self.index.remove(h)

    def entity_by_handle(self, handle: str):
        return self._by_handle.get(handle)

    def query(self, box: BBox) -> list:
        """Entidades cujo bbox intersecta box (candidatas -- refine depois)."""
        return [self._by_handle[h] for h in self.index.query(box) if h in self._by_handle]

    def query_point(self, p: Vec2, radius: float) -> list:
        return self.query(BBox(p.x - radius, p.y - radius, p.x + radius, p.y + radius))

    # ---------------- entidades ----------------

    def entities(self) -> Iterator:
        return iter(self.msp)

    def __len__(self) -> int:
        return len(self._by_handle)

    def extents(self) -> BBox:
        return self.index.extents()

    def _attribs(self, layer: str | None, extra: dict | None = None) -> dict:
        d = {"layer": layer or self._current_layer}
        if extra:
            d.update(extra)
        return d

    def _register_new(self, entity, name: str):
        """Entidade recem criada pelo ezdxf ja esta no layout: so indexa e empilha."""
        self._index_add(entity)
        self.undo.push(AddEntities(self, [entity], name), execute=False)
        self._touch()
        return entity

    def add_line(self, a, b, layer: str | None = None, **kw):
        a, b = Vec2.of(a), Vec2.of(b)
        e = self.msp.add_line((a.x, a.y), (b.x, b.y), dxfattribs=self._attribs(layer, kw))
        return self._register_new(e, "linha")

    def add_lwpolyline(self, points, closed: bool = False, layer: str | None = None, **kw):
        pts = [(p.x, p.y) for p in (Vec2.of(q) for q in points)]
        e = self.msp.add_lwpolyline(
            pts, format="xy", close=closed, dxfattribs=self._attribs(layer, kw)
        )
        return self._register_new(e, "polilinha")

    def add_circle(self, center, radius: float, layer: str | None = None, **kw):
        c = Vec2.of(center)
        e = self.msp.add_circle((c.x, c.y), float(radius), dxfattribs=self._attribs(layer, kw))
        return self._register_new(e, "circulo")

    def add_point(self, p, layer: str | None = None, **kw):
        v = Vec2.of(p)
        e = self.msp.add_point((v.x, v.y), dxfattribs=self._attribs(layer, kw))
        return self._register_new(e, "ponto")

    def add_text(self, text: str, at, height: float = 1.0, layer: str | None = None, **kw):
        v = Vec2.of(at)
        e = self.msp.add_text(
            str(text), height=float(height), dxfattribs=self._attribs(layer or "TEXTO", kw)
        )
        e.set_placement((v.x, v.y))
        return self._register_new(e, "texto")

    def add_arc(
        self, center, radius: float, start_angle: float, end_angle: float,
        layer: str | None = None, **kw
    ):
        c = Vec2.of(center)
        e = self.msp.add_arc(
            (c.x, c.y),
            float(radius),
            float(start_angle),
            float(end_angle),
            dxfattribs=self._attribs(layer, kw),
        )
        return self._register_new(e, "arco")

    # ---------------- edicao ----------------

    @contextmanager
    def editing(self, entities: Iterable, name: str = "editar"):
        """Envolve uma edicao de geometria num item de desfazer exato.

        Uso:
            with doc.editing(sel, "mover"):
                for e in sel:
                    e.transform(matrix)

        Entidades de tipo nao suportado pelo snapshot sao ignoradas em vez de
        entrarem no undo pela metade.
        """
        items = [e for e in entities if e is not None and e.is_alive]
        before = [(e, snapshot(e)) for e in items]
        yield items
        records = []
        for entity, antes in before:
            if antes is None or not entity.is_alive:
                continue
            records.append((entity, antes, snapshot(entity)))
            self._index_update(entity)
        if records:
            self.undo.push(ModifyGeometry(self, records, name), execute=False)
            self._touch()

    def transform(self, entities: Iterable, matrix, name: str = "transformar") -> list:
        """Aplica uma Matrix44 as entidades, de forma desfazivel."""
        with self.editing(entities, name) as items:
            for e in items:
                if snapshot(e) is not None:
                    e.transform(matrix)
        return items

    def copy_entities(self, entities: Iterable, matrix=None, name: str = "copiar") -> list:
        """Duplica entidades (opcionalmente transformadas) como novas do desenho."""
        made = []
        for e in entities:
            if e is None or not e.is_alive:
                continue
            clone = e.copy()
            if matrix is not None:
                clone.transform(matrix)
            self.msp.add_entity(clone)
            self._index_add(clone)
            made.append(clone)
        if made:
            self.undo.push(AddEntities(self, made, name), execute=False)
            self._touch()
        return made

    def replace(self, old: Iterable, new_factory, name: str = "substituir") -> list:
        """Troca entidades por outras (aparar, estender, explodir).

        new_factory(entity) devolve uma lista de entidades novas -- vazia para
        simplesmente apagar. Tudo entra como um unico item de desfazer.
        """
        old_items = [e for e in old if e is not None and e.is_alive]
        if not old_items:
            return []
        self.undo.begin_macro(name)
        created = []
        try:
            for entity in old_items:
                created.extend(new_factory(entity) or [])
            self.delete(old_items)
        finally:
            self.undo.end_macro()
        return created

    # atributos opcionais do DXF: sem isso, dxf.get() devolve None para uma
    # entidade que nunca teve o atributo setado, e regravar None quebra o ezdxf.
    _ATTRIB_DEFAULTS = {"layer": "0", "color": 256}

    def set_entity_attribs(self, entities: Iterable, name: str = "propriedades", **attrs) -> None:
        """Altera atributos DXF simples (camada, cor...) das entidades, com desfazer."""
        items = [e for e in entities if e is not None and e.is_alive]
        if not items:
            return
        records = [
            (e, {k: e.dxf.get(k, self._ATTRIB_DEFAULTS.get(k)) for k in attrs}, dict(attrs))
            for e in items
        ]
        self.undo.push(SetAttribs(self, records, name))

    def delete(self, entities: Iterable) -> None:
        items = [e for e in entities if e is not None and e.is_alive]
        if not items:
            return
        self.undo.push(DeleteEntities(self, items, f"apagar {len(items)}"))

    # ---------------- persistencia ----------------

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path else self.path
        if target is None:
            raise ValueError("documento sem caminho definido")
        target.parent.mkdir(parents=True, exist_ok=True)
        self.drawing.saveas(str(target))
        self.path = target
        self.mark_saved()
        return target
