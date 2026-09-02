"""Modelo completo de propriedades de camadas.

As propriedades que fazem parte do DXF sao gravadas diretamente na tabela
LAYER. Metadados de organizacao (filtros, estados nomeados, reconciliacao e
congelamento por viewport) sao serializados pelo sidecar do projeto.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from fnmatch import fnmatchcase
from typing import Any

PROTECTED_LAYERS = {"0", "DEFPOINTS"}


@dataclass(frozen=True)
class LayerProperties:
    name: str
    on: bool = True
    frozen: bool = False
    locked: bool = False
    color: int = 7
    linetype: str = "Continuous"
    lineweight: int = -3
    transparency: int = 0
    plot_style: str = "Normal"
    plot: bool = True
    description: str = ""
    xref: bool = False
    reconciled: bool = True


@dataclass
class LayerFilter:
    name: str
    kind: str = "property"  # property | group
    criteria: dict[str, Any] = field(default_factory=dict)
    members: list[str] = field(default_factory=list)

    def matches(self, layer: LayerProperties) -> bool:
        if self.kind == "group":
            return layer.name.casefold() in {n.casefold() for n in self.members}
        for key, expected in self.criteria.items():
            actual = getattr(layer, key, None)
            if key == "name":
                if not fnmatchcase(str(actual).casefold(), str(expected).casefold()):
                    return False
            elif isinstance(actual, str):
                if str(actual).casefold() != str(expected).casefold():
                    return False
            elif actual != expected:
                return False
        return True


@dataclass
class LayerState:
    name: str
    description: str = ""
    layers: dict[str, dict[str, Any]] = field(default_factory=dict)


class LayerManager:
    """Facade consistente sobre a tabela LAYER do ezdxf."""

    def __init__(self, document):
        self.document = document
        self.filters: dict[str, LayerFilter] = {}
        self.states: dict[str, LayerState] = {}
        self.plot_styles: dict[str, str] = {}
        self.viewport_plot_styles: dict[str, dict[str, str]] = {}
        self.viewport_frozen: dict[str, set[str]] = {}
        self.unreconciled: set[str] = set()
        self._known: set[str] = {n.casefold() for n in document.layer_names()}

    @staticmethod
    def is_xref(name: str) -> bool:
        return "|" in name

    def _entry(self, name: str):
        return self.document.drawing.layers.get(name)

    def _viewport_entity(self, handle: str | None):
        if not handle:
            return None
        viewport = self.document.drawing.entitydb.get(str(handle))
        if viewport is not None and viewport.dxftype() == "VIEWPORT":
            return viewport
        return None

    def properties(self, name: str, viewport: str | None = None) -> LayerProperties:
        layer = self._entry(name)
        actual_name = str(layer.dxf.name)
        color = abs(int(layer.dxf.get("color", 7) or 7))
        linetype = str(layer.dxf.get("linetype", "Continuous") or "Continuous")
        lineweight = int(layer.dxf.get("lineweight", -3))
        transparency = round(float(layer.transparency) * 100)
        if viewport:
            overrides = layer.get_vp_overrides()
            if overrides.has_overrides(viewport):
                color = int(overrides.get_color(viewport))
                linetype = str(overrides.get_linetype(viewport))
                lineweight = int(overrides.get_lineweight(viewport))
                transparency = round(float(overrides.get_transparency(viewport)) * 100)
        viewport_entity = self._viewport_entity(viewport)
        viewport_frozen = bool(
            viewport_entity is not None and viewport_entity.is_frozen(actual_name)
        )
        return LayerProperties(
            name=actual_name,
            on=bool(layer.is_on()),
            frozen=bool(layer.is_frozen())
            or (
                bool(viewport)
                and actual_name.casefold() in self.viewport_frozen.get(viewport, set())
            )
            or viewport_frozen,
            locked=bool(layer.is_locked()),
            color=color,
            linetype=linetype,
            lineweight=lineweight,
            transparency=transparency,
            plot_style=self.viewport_plot_styles.get(viewport or "", {}).get(
                actual_name.casefold(),
                self.plot_styles.get(actual_name.casefold(), "Normal"),
            ),
            plot=bool(layer.dxf.get("plot", 1)),
            description=str(layer.description or ""),
            xref=self.is_xref(actual_name),
            reconciled=actual_name.casefold() not in self.unreconciled,
        )

    def all(
        self,
        *,
        search: str = "",
        filter_name: str | None = None,
        sort_by: str = "name",
        descending: bool = False,
        viewport: str | None = None,
    ) -> list[LayerProperties]:
        rows = [self.properties(name, viewport) for name in self.document.layer_names()]
        needle = search.strip().casefold()
        if needle:
            rows = [
                r for r in rows if needle in r.name.casefold() or needle in r.description.casefold()
            ]
        if filter_name:
            layer_filter = self.filters.get(filter_name)
            if layer_filter:
                rows = [r for r in rows if layer_filter.matches(r)]
        key = sort_by if sort_by in LayerProperties.__dataclass_fields__ else "name"
        return sorted(
            rows, key=lambda row: (getattr(row, key) is None, getattr(row, key)), reverse=descending
        )

    def create(self, name: str, *, color: int = 7, reconciled: bool = False):
        clean = name.strip()
        if not clean:
            raise ValueError("O nome da camada nao pode ser vazio")
        if clean in self.document.drawing.layers:
            raise ValueError(f"A camada {clean!r} ja existe")
        layer = self.document.drawing.layers.add(clean, color=int(color))
        if not reconciled:
            self.unreconciled.add(clean.casefold())
        self._known.add(clean.casefold())
        self._changed(geometry=True)
        return layer

    def delete(self, name: str) -> None:
        props = self.properties(name)
        folded = props.name.casefold()
        if folded in {n.casefold() for n in PROTECTED_LAYERS}:
            raise ValueError(f"A camada {props.name} e protegida")
        if props.name.casefold() == self.document.current_layer.casefold():
            raise ValueError("A camada corrente nao pode ser excluida")
        if props.xref:
            raise ValueError("Camadas dependentes de Xref nao podem ser excluidas")
        if any(e.dxf.get("layer", "0").casefold() == folded for e in self.document.entities()):
            raise ValueError("A camada contem objetos e nao pode ser excluida")
        self.document.drawing.layers.remove(props.name)
        self.unreconciled.discard(folded)
        self.plot_styles.pop(folded, None)
        for frozen in self.viewport_frozen.values():
            frozen.discard(folded)
        for styles in self.viewport_plot_styles.values():
            styles.pop(folded, None)
        for layer_filter in self.filters.values():
            layer_filter.members = [n for n in layer_filter.members if n.casefold() != folded]
        self._changed(geometry=True)

    def rename(self, old_name: str, new_name: str) -> str:
        props = self.properties(old_name)
        clean = new_name.strip()
        if not clean:
            raise ValueError("O nome da camada nao pode ser vazio")
        if props.name.casefold() in {n.casefold() for n in PROTECTED_LAYERS}:
            raise ValueError(f"A camada {props.name} nao pode ser renomeada")
        if props.xref:
            raise ValueError("Camadas dependentes de Xref nao podem ser renomeadas")
        if clean in self.document.drawing.layers and clean.casefold() != props.name.casefold():
            raise ValueError(f"A camada {clean!r} ja existe")
        self._entry(props.name).rename(clean)
        old_folded, new_folded = props.name.casefold(), clean.casefold()
        if self.document.current_layer.casefold() == old_folded:
            self.document._current_layer = clean
            self.document.drawing.header["$CLAYER"] = clean
        if old_folded in self.unreconciled:
            self.unreconciled.remove(old_folded)
            self.unreconciled.add(new_folded)
        if old_folded in self.plot_styles:
            self.plot_styles[new_folded] = self.plot_styles.pop(old_folded)
        for frozen in self.viewport_frozen.values():
            if old_folded in frozen:
                frozen.remove(old_folded)
                frozen.add(new_folded)
        for styles in self.viewport_plot_styles.values():
            if old_folded in styles:
                styles[new_folded] = styles.pop(old_folded)
        for layer_filter in self.filters.values():
            layer_filter.members = [
                clean if n.casefold() == old_folded else n for n in layer_filter.members
            ]
        self._known.discard(old_folded)
        self._known.add(new_folded)
        self._changed(geometry=True)
        return clean

    def set_current(self, name: str) -> None:
        props = self.properties(name)
        if props.xref:
            raise ValueError("Uma camada dependente de Xref nao pode ser corrente")
        layer = self._entry(props.name)
        layer.on()
        layer.thaw()
        self.document._current_layer = props.name
        self.document.drawing.header["$CLAYER"] = props.name
        self._changed()

    def update(self, name: str, **changes: Any) -> LayerProperties:
        layer = self._entry(name)
        actual_name = str(layer.dxf.name)
        if "on" in changes:
            layer.on() if changes["on"] else layer.off()
        if "frozen" in changes:
            if (
                changes["frozen"]
                and actual_name.casefold() == self.document.current_layer.casefold()
            ):
                raise ValueError("A camada corrente nao pode ser congelada")
            layer.freeze() if changes["frozen"] else layer.thaw()
        if "locked" in changes:
            layer.lock() if changes["locked"] else layer.unlock()
        if "color" in changes:
            color = int(changes["color"])
            if not 1 <= color <= 255:
                raise ValueError("A cor ACI deve estar entre 1 e 255")
            # ``dxf.color`` fica negativo quando a camada esta desligada;
            # a propriedade segura preserva esse sinal/estado.
            layer.color = color
        if "linetype" in changes:
            value = str(changes["linetype"])
            if value not in self.document.drawing.linetypes:
                raise ValueError(f"Tipo de linha inexistente: {value}")
            layer.dxf.linetype = value
        if "lineweight" in changes:
            layer.dxf.lineweight = int(changes["lineweight"])
        if "transparency" in changes:
            value = max(0, min(90, int(changes["transparency"])))
            layer.transparency = value / 100.0
        if "plot" in changes:
            layer.dxf.plot = int(bool(changes["plot"]))
        if "description" in changes:
            layer.description = str(changes["description"])
        if "plot_style" in changes:
            self.plot_styles[actual_name.casefold()] = str(changes["plot_style"] or "Normal")
        self._changed(
            geometry=any(
                k in changes for k in ("color", "linetype", "lineweight", "transparency", "locked")
            )
        )
        return self.properties(actual_name)

    def add_filter(self, layer_filter: LayerFilter) -> None:
        name = layer_filter.name.strip()
        if not name:
            raise ValueError("Informe um nome para o filtro")
        layer_filter.name = name
        self.filters[name] = layer_filter
        self._changed()

    def remove_filter(self, name: str) -> None:
        self.filters.pop(name, None)
        self._changed()

    def reconcile(self, names: Iterable[str] | None = None) -> None:
        targets = {n.casefold() for n in names} if names is not None else set(self.unreconciled)
        self.unreconciled.difference_update(targets)
        self._changed()

    def detect_new_layers(self) -> list[str]:
        current = {name.casefold(): name for name in self.document.layer_names()}
        added = [actual for folded, actual in current.items() if folded not in self._known]
        self.unreconciled.update(n.casefold() for n in added)
        self._known.update(current)
        if added:
            self._changed()
        return sorted(added)

    def save_state(self, name: str, description: str = "") -> LayerState:
        clean = name.strip()
        if not clean:
            raise ValueError("Informe um nome para o estado")
        saved = {}
        for props in self.all():
            values = asdict(props)
            for derived in ("name", "xref", "reconciled"):
                values.pop(derived, None)
            saved[props.name] = values
        state = LayerState(clean, description, saved)
        self.states[clean] = state
        self._changed()
        return state

    def restore_state(self, name: str) -> list[str]:
        state = self.states.get(name)
        if state is None:
            raise ValueError(f"Estado de camadas inexistente: {name}")
        restored = []
        for layer_name, values in state.layers.items():
            if layer_name not in self.document.drawing.layers:
                continue
            self.update(layer_name, **values)
            restored.append(layer_name)
        return restored

    def delete_state(self, name: str) -> None:
        self.states.pop(name, None)
        self._changed()

    def set_viewport_override(self, viewport: str, name: str, **changes: Any) -> None:
        if not viewport:
            raise ValueError("Viewport invalida")
        layer = self._entry(name)
        overrides = layer.get_vp_overrides()
        if "color" in changes:
            overrides.set_color(viewport, int(changes["color"]))
        if "linetype" in changes:
            overrides.set_linetype(viewport, str(changes["linetype"]))
        if "lineweight" in changes:
            overrides.set_lineweight(viewport, int(changes["lineweight"]))
        if "transparency" in changes:
            overrides.set_transparency(viewport, int(changes["transparency"]) / 100.0)
        overrides.commit()
        if "frozen" in changes:
            frozen = self.viewport_frozen.setdefault(viewport, set())
            folded = str(layer.dxf.name).casefold()
            frozen.add(folded) if changes["frozen"] else frozen.discard(folded)
            viewport_entity = self._viewport_entity(viewport)
            if viewport_entity is not None:
                names = {value.casefold(): value for value in viewport_entity.frozen_layers}
                if changes["frozen"]:
                    names[folded] = str(layer.dxf.name)
                else:
                    names.pop(folded, None)
                viewport_entity.frozen_layers = list(names.values())
        if "plot_style" in changes:
            self.viewport_plot_styles.setdefault(viewport, {})[str(layer.dxf.name).casefold()] = (
                str(changes["plot_style"] or "Normal")
            )
        self._changed(geometry=True)

    def clear_viewport_override(self, viewport: str, name: str) -> None:
        layer = self._entry(name)
        overrides = layer.get_vp_overrides()
        overrides.discard(viewport)
        overrides.commit()
        folded = str(layer.dxf.name).casefold()
        self.viewport_frozen.get(viewport, set()).discard(folded)
        viewport_entity = self._viewport_entity(viewport)
        if viewport_entity is not None:
            viewport_entity.frozen_layers = [
                value for value in viewport_entity.frozen_layers if value.casefold() != folded
            ]
        self.viewport_plot_styles.get(viewport, {}).pop(str(layer.dxf.name).casefold(), None)
        self._changed(geometry=True)

    def viewport_names(self) -> list[tuple[str, str]]:
        result: list[tuple[str, str]] = []
        for layout in self.document.drawing.layouts:
            if layout.name.casefold() == "model":
                continue
            for viewport in layout.query("VIEWPORT"):
                handle = str(viewport.dxf.get("handle", ""))
                if handle:
                    result.append(
                        (handle, f"{layout.name} / viewport {viewport.dxf.get('id', handle)}")
                    )
        return result

    def export_metadata(self) -> dict[str, Any]:
        return {
            "filters": [asdict(item) for item in self.filters.values()],
            "states": [asdict(item) for item in self.states.values()],
            "plot_styles": dict(self.plot_styles),
            "viewport_plot_styles": {
                viewport: dict(values) for viewport, values in self.viewport_plot_styles.items()
            },
            "viewport_frozen": {key: sorted(value) for key, value in self.viewport_frozen.items()},
            "unreconciled": sorted(self.unreconciled),
        }

    def import_metadata(self, data: dict[str, Any] | None) -> None:
        if not isinstance(data, dict):
            return
        self.filters = {}
        for item in data.get("filters", []):
            try:
                layer_filter = LayerFilter(**item)
                self.filters[layer_filter.name] = layer_filter
            except (TypeError, ValueError):
                continue
        self.states = {}
        for item in data.get("states", []):
            try:
                state = LayerState(**item)
                self.states[state.name] = state
            except (TypeError, ValueError):
                continue
        self.plot_styles = {str(k): str(v) for k, v in data.get("plot_styles", {}).items()}
        self.viewport_plot_styles = {
            str(viewport): {str(k).casefold(): str(v) for k, v in values.items()}
            for viewport, values in data.get("viewport_plot_styles", {}).items()
            if isinstance(values, dict)
        }
        self.viewport_frozen = {
            str(k): {str(v).casefold() for v in values}
            for k, values in data.get("viewport_frozen", {}).items()
        }
        existing = {n.casefold() for n in self.document.layer_names()}
        self.unreconciled = {str(n).casefold() for n in data.get("unreconciled", [])} & existing
        self._known = existing

    def _changed(self, *, geometry: bool = False) -> None:
        self.document.invalidate_layer_cache()
        if geometry:
            self.document.invalidate_all_geometry()
        self.document._touch()
