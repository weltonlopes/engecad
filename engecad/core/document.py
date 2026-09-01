"""Documento do EngeCAD: um DXF do ezdxf + CRS + undo + indice espacial.

O formato nativo E o DXF -- nao ha serializacao propria a manter, e o arquivo
abre no AutoCAD/QGIS sem exportar. O que o ezdxf nao oferece (desfazer e busca
espacial) esta empilhado aqui por cima.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path

import ezdxf
from ezdxf.document import Drawing

from .associative import (
    associated_dimensions,
    rebind_replacement_associations,
    remap_dimension_associations,
    set_dimension_associations,
    update_associative_dimension,
)
from .crs import ProjectCRS
from .dimensions import (
    DIMENSION_TYPES,
    DIMSTYLE_NAME,
    DimensionStyleSettings,
    apply_dimension_style,
    ensure_dimension_style,
    read_dimension_style,
    rerender_dimension,
)
from .entities import entity_bbox
from .geometry import BBox, Vec2
from .hatches import (
    HatchSettings,
    apply_hatch_settings,
    associated_hatches,
    set_hatch_boundaries,
    set_hatch_seed_boundary,
    update_associative_hatch,
)
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
        self.doc._update_associative_dimensions(
            {_entity_handle(e) for e in self.entities if _entity_handle(e)}
        )
        self.doc._update_associative_hatches(
            {_entity_handle(e) for e in self.entities if _entity_handle(e)}
        )
        self.doc._touch()

    def _unlink(self) -> None:
        msp = self.doc.msp
        for e in self.entities:
            if e.is_alive:
                self.doc._index_remove(e)
                msp.unlink_entity(e)
        self.doc._update_associative_dimensions(
            {_entity_handle(e) for e in self.entities if _entity_handle(e)}
        )
        self.doc._update_associative_hatches(
            {_entity_handle(e) for e in self.entities if _entity_handle(e)}
        )
        self.doc._touch()


class AddEntities(_EntityCommand):
    redo = _EntityCommand._link
    undo = _EntityCommand._unlink


class DeleteEntities(_EntityCommand):
    redo = _EntityCommand._unlink
    undo = _EntityCommand._link


def _entity_handle(entity) -> str | None:
    handle = entity.dxf.get("handle") if entity is not None else None
    return str(handle) if handle else None


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
        ensure_dimension_style(drawing, DIMSTYLE_NAME)
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

    @property
    def dimension_style_name(self) -> str:
        return DIMSTYLE_NAME

    def dimension_style(self):
        return ensure_dimension_style(self.drawing, self.dimension_style_name)

    def dimension_style_settings(self) -> DimensionStyleSettings:
        if self.dimension_style_name not in self.drawing.dimstyles:
            return DimensionStyleSettings()
        return read_dimension_style(self.drawing.dimstyles.get(self.dimension_style_name))

    def update_dimension_style(self, settings: DimensionStyleSettings) -> None:
        """Atualiza o estilo corrente e todas as suas representacoes graficas."""
        apply_dimension_style(self.dimension_style(), settings)
        for entity in self.msp:
            if (
                entity.dxftype() in DIMENSION_TYPES
                and entity.dxf.get("dimstyle", "") == self.dimension_style_name
            ):
                rerender_dimension(entity)
                self._index_update(entity)
        self._touch()

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

    def _update_associative_dimensions(
        self, source_handles: set[str] | None = None, dimensions: Iterable = ()
    ) -> list:
        """Regenera dependentes; nao cria item de undo por conta propria."""
        explicit = {e for e in dimensions if e is not None and e.is_alive}
        targets = set(explicit)
        if source_handles:
            targets.update(associated_dimensions(self, {str(h) for h in source_handles}))
        changed = []
        for dimension in targets:
            if update_associative_dimension(
                self,
                dimension,
                preserve_dimension_location=dimension in explicit,
            ):
                self._index_update(dimension)
                changed.append(dimension)
        return changed

    def _update_associative_hatches(
        self, source_handles: set[str] | None = None, hatches: Iterable = ()
    ) -> list:
        explicit = {e for e in hatches if e is not None and e.is_alive}
        targets = set(explicit)
        if source_handles:
            targets.update(associated_hatches(self, {str(h) for h in source_handles}))
        changed = []
        for hatch in targets:
            if update_associative_hatch(self, hatch):
                self._index_update(hatch)
                changed.append(hatch)
        return changed

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

    def _add_dimension(
        self,
        override,
        name: str,
        associations: dict | None = None,
        association_mode: str | None = None,
    ):
        override.render()
        dimension = override.dimension
        set_dimension_associations(dimension, associations, association_mode)
        return self._register_new(dimension, name)

    def _dim_attribs(self, layer: str | None, extra: dict | None = None) -> dict:
        return self._attribs(layer or "COTA", extra)

    def add_linear_dimension(
        self, p1, p2, base, angle: float = 0.0, *, text: str = "<>",
        layer: str | None = None, style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        p1, p2, base = Vec2.of(p1), Vec2.of(p2), Vec2.of(base)
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_linear_dim(
            base=(base.x, base.y), p1=(p1.x, p1.y), p2=(p2.x, p2.y),
            angle=float(angle), text=text, dimstyle=dimstyle, override=override,
            dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota linear", associations)

    def add_aligned_dimension(
        self, p1, p2, base, *, text: str = "<>", layer: str | None = None,
        style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        p1, p2, base = Vec2.of(p1), Vec2.of(p2), Vec2.of(base)
        edge = p2 - p1
        if edge.length <= 1e-9:
            raise ValueError("pontos de medicao coincidentes")
        distance = edge.cross(base - p1) / edge.length
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_aligned_dim(
            p1=(p1.x, p1.y), p2=(p2.x, p2.y), distance=distance,
            text=text, dimstyle=dimstyle, override=override,
            dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota alinhada", associations, "aligned")

    def add_angular_dimension(
        self, center, p1, p2, base, *, text: str = "<>", layer: str | None = None,
        style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        center, p1, p2, base = map(Vec2.of, (center, p1, p2, base))
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_angular_dim_3p(
            base=(base.x, base.y), center=(center.x, center.y),
            p1=(p1.x, p1.y), p2=(p2.x, p2.y), text=text,
            dimstyle=dimstyle, override=override, dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota angular", associations)

    def add_radius_dimension(
        self, center, radius: float, placement, *, text: str = "<>",
        layer: str | None = None, style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        center, placement = Vec2.of(center), Vec2.of(placement)
        if radius <= 1e-9:
            raise ValueError("raio invalido")
        direction = placement - center
        angle = 0.0 if direction.length <= 1e-9 else math.degrees(direction.angle)
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_radius_dim(
            center=(center.x, center.y), radius=float(radius), angle=angle,
            location=(placement.x, placement.y), text=text, dimstyle=dimstyle,
            override=override, dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota de raio", associations)

    def add_diameter_dimension(
        self, center, radius: float, placement, *, text: str = "<>",
        layer: str | None = None, style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        center, placement = Vec2.of(center), Vec2.of(placement)
        if radius <= 1e-9:
            raise ValueError("raio invalido")
        direction = placement - center
        angle = 0.0 if direction.length <= 1e-9 else math.degrees(direction.angle)
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_diameter_dim(
            center=(center.x, center.y), radius=float(radius), angle=angle,
            location=(placement.x, placement.y), text=text, dimstyle=dimstyle,
            override=override, dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota de diametro", associations)

    def add_ordinate_dimension(
        self, feature, leader_end, *, x_type: bool | None = None, origin=(0, 0),
        text: str = "<>", layer: str | None = None, style: str | None = None,
        override: dict | None = None, associations: dict | None = None,
    ):
        feature, leader_end, origin = Vec2.of(feature), Vec2.of(leader_end), Vec2.of(origin)
        offset = leader_end - feature
        if x_type is None:
            x_type = abs(offset.y) >= abs(offset.x)
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_ordinate_dim(
            feature_location=(feature.x, feature.y), offset=(offset.x, offset.y),
            dtype=1 if x_type else 0, origin=(origin.x, origin.y), text=text,
            dimstyle=dimstyle, override=override, dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "cota ordenada", associations)

    def add_arc_length_dimension(
        self, center, p1, p2, base, *, text: str = "<>", layer: str | None = None,
        style: str | None = None, override: dict | None = None,
        associations: dict | None = None,
    ):
        center, p1, p2, base = map(Vec2.of, (center, p1, p2, base))
        dimstyle = style or self.dimension_style_name
        ensure_dimension_style(self.drawing, dimstyle)
        obj = self.msp.add_arc_dim_3p(
            base=(base.x, base.y), center=(center.x, center.y),
            p1=(p1.x, p1.y), p2=(p2.x, p2.y), text=text,
            dimstyle=dimstyle, override=override, dxfattribs=self._dim_attribs(layer),
        )
        return self._add_dimension(obj, "comprimento de arco", associations)

    def add_hatch(
        self,
        boundaries: Iterable | None = None,
        *,
        seed=None,
        settings: HatchSettings | None = None,
        layer: str | None = None,
    ):
        """Cria HATCH nativa por contornos selecionados ou por ponto interno."""
        hatch = self.msp.add_hatch(dxfattribs=self._attribs(layer))
        try:
            apply_hatch_settings(hatch, settings or HatchSettings())
            if seed is not None:
                set_hatch_seed_boundary(self, hatch, seed)
            else:
                set_hatch_boundaries(hatch, list(boundaries or ()))
        except Exception:
            self.msp.delete_entity(hatch)
            raise
        return self._register_new(hatch, "hachura")

    def add_title_block(self, insert, config):
        from .titleblocks import add_title_block

        return add_title_block(self, insert, config)

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
        items = list(dict.fromkeys(e for e in entities if e is not None and e.is_alive))
        source_handles = {_entity_handle(e) for e in items if e.dxftype() not in DIMENSION_TYPES}
        source_handles.discard(None)
        dependents = associated_dimensions(self, source_handles) if source_handles else []
        hatch_dependents = associated_hatches(self, source_handles) if source_handles else []
        tracked = list(dict.fromkeys([*items, *dependents, *hatch_dependents]))
        before = [(e, snapshot(e)) for e in tracked]
        yield items
        for entity in items:
            if entity.is_alive:
                self._index_update(entity)
        explicit_dimensions = [e for e in items if e.dxftype() in DIMENSION_TYPES]
        self._update_associative_dimensions(source_handles, explicit_dimensions)
        explicit_hatches = [e for e in items if e.dxftype() == "HATCH"]
        self._update_associative_hatches(source_handles, explicit_hatches)
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
        originals = [e for e in entities if e is not None and e.is_alive]
        made = []
        pairs = []
        for e in originals:
            clone = e.copy()
            if matrix is not None:
                clone.transform(matrix)
            self.msp.add_entity(clone)
            self._index_add(clone)
            made.append(clone)
            pairs.append((e, clone))
        if made:
            handle_map = {
                old: new
                for original, clone in pairs
                if (old := _entity_handle(original)) and (new := _entity_handle(clone))
            }
            copied_dimensions = []
            copied_hatches = []
            for original, clone in pairs:
                if clone.dxftype() in DIMENSION_TYPES:
                    remap_dimension_associations(clone, handle_map)
                    copied_dimensions.append(clone)
                elif clone.dxftype() == "HATCH":
                    from .hatches import remap_copied_hatch

                    remap_copied_hatch(self, original, clone, handle_map, matrix)
                    copied_hatches.append(clone)
            self._update_associative_dimensions(dimensions=copied_dimensions)
            self._update_associative_hatches(hatches=copied_hatches)
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

    def rebind_replacement_associations(self, old, replacements) -> int:
        """Mantem cotas ligadas aos pedacos sobreviventes de uma substituicao."""
        handle = _entity_handle(old)
        dimensions = associated_dimensions(self, {handle}) if handle else []
        if not dimensions:
            return 0
        count = 0
        with self.editing(dimensions, "reassociar apos aparar"):
            count = rebind_replacement_associations(self, old, replacements)
        return count

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
