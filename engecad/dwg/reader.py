"""Leitor de entidades DWG (implementação nativa do EngeCAD)."""

from dataclasses import dataclass, field

from .container import DwgContainer, DwgFormatError
from .entities import iter_entities
from .objects import ObjectIndex, build_object_index

DwgError = DwgFormatError


@dataclass(frozen=True, slots=True)
class DwgEntity:
    type: str
    handle: int = 0
    layer_handle: int | None = None
    owner_handle: int | None = None
    data: dict = field(default_factory=dict)
    entity_mode: int = 3


@dataclass
class DwgDocument:
    container: DwgContainer
    entities: list[DwgEntity]
    object_index: ObjectIndex | None = None
    _decoded: bool = False

    def iter_entities(self):
        if not self._decoded:
            assert self.object_index is not None
            self.entities.extend(
                DwgEntity(
                    entity.type,
                    entity.handle,
                    entity.layer_handle,
                    entity.owner_handle,
                    entity.data,
                    entity.entity_mode,
                )
                for entity in iter_entities(self.object_index)
            )
            self._decoded = True
        yield from self.entities


class DwgReader:
    def read(self, path):
        container = DwgContainer(path)
        return DwgDocument(container, [], build_object_index(container))


def read(path):
    return DwgReader().read(path)
