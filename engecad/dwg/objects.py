"""Índice e registros de objetos do contêiner DWG nativo.

O mapa de handles é pequeno em relação ao fluxo de objetos e é decodificado
uma única vez. Os registros continuam em ``bytes`` compartilhados e só são
materializados quando o parser de entidades solicita um handle específico.
"""

from __future__ import annotations

from dataclasses import dataclass

from .bitstream import BitReader, modular_char
from .container import DwgContainer, DwgFormatError


@dataclass(frozen=True, slots=True)
class ObjectRef:
    handle: int
    offset: int


@dataclass(frozen=True, slots=True)
class ObjectRecord:
    handle: int
    offset: int
    size: int
    body_offset: int
    body: memoryview
    payload_offset: int
    payload_end: int
    object_data_end_bits: int
    handle_stream_size_bits: int

    @property
    def payload(self) -> memoryview:
        return self.body[
            self.payload_offset - self.body_offset : self.payload_end - self.body_offset
        ]


class ObjectIndex:
    """Índice de objetos com acesso por handle e leitura sob demanda."""

    __slots__ = ("container", "object_data", "refs", "by_handle", "_r21")

    def __init__(
        self,
        container: DwgContainer,
        object_data: bytes,
        refs: tuple[ObjectRef, ...],
    ) -> None:
        self.container = container
        self.object_data = object_data
        self.refs = refs
        # Um DWG válido normalmente tem um handle por objeto. A lista mantém
        # duplicatas para que arquivos parcialmente corrompidos não percam
        # dados silenciosamente; a consulta escolhe a última ocorrência.
        self.by_handle = {ref.handle: ref for ref in refs}
        self._r21 = container.version in {"AC1024", "AC1027", "AC1032"}

    def __len__(self) -> int:
        return len(self.refs)

    def record(self, ref: ObjectRef) -> ObjectRecord:
        view = memoryview(self.object_data)
        reader = BitReader(view[ref.offset:])
        size = reader.read_modular_short()
        body_start = ref.offset + (reader.bitpos >> 3)
        if size == 0:
            raise DwgFormatError(f"registro DWG vazio no offset {ref.offset}")

        handle_stream_bytes = 0
        handle_stream_size_bits = 0
        object_data_end_bits = 0
        payload_offset = body_start
        if self._r21:
            probe = BitReader(view[body_start:])
            # The MC value is the size of the trailing handle stream in bits;
            # probe.bitpos is only the size of the MC header itself.  The MS
            # ``size`` already includes the object data and that trailing
            # stream, but not this MC header.
            handle_stream_size_bits = probe.read_modular_char()
            handle_stream_header_bytes = probe.bitpos >> 3
            handle_stream_bytes = (handle_stream_size_bits + 7) >> 3
            if handle_stream_bytes > size:
                raise DwgFormatError("fluxo de handles DWG maior que o registro")
            payload_offset += handle_stream_header_bytes
            object_data_end_bits = probe.bitpos + (size - handle_stream_bytes) * 8
        else:
            object_data_end_bits = size * 8

        body_end = body_start + size + (payload_offset - body_start)
        raw_end = body_end + 2
        if raw_end > len(self.object_data):
            raise DwgFormatError(
                f"registro DWG no offset {ref.offset} ultrapassa AcDb:AcDbObjects"
            )
        return ObjectRecord(
            ref.handle,
            ref.offset,
            size,
            body_start,
            view[body_start:body_end],
            payload_offset,
            body_end,
            object_data_end_bits,
            handle_stream_size_bits,
        )

    def records(self):
        for ref in self.refs:
            try:
                yield self.record(ref)
            except DwgFormatError:
                continue

    def type_code(self, record: ObjectRecord) -> int:
        view = memoryview(self.object_data)
        reader = BitReader(view[record.payload_offset : record.payload_end])
        if self._r21:
            return reader.read_object_type_r2010()
        return reader.read_bitshort()

    def object_type(self, ref: ObjectRef) -> tuple[int, ObjectRecord]:
        record = self.record(ref)
        return self.type_code(record), record


def _section_data(container: DwgContainer, name: str) -> bytes:
    if container.version == "AC1021":
        for section in container.sections.values():
            if section.name == name:
                return container.section(section.hashcode)
        raise DwgFormatError(f"seção DWG {name!r} ausente")
    return container.section_named(name)


def build_object_index(container: DwgContainer) -> ObjectIndex:
    """Constrói o índice handle -> offset sem decodificar as entidades."""
    handles = _section_data(container, "AcDb:Handles")
    objects = _section_data(container, "AcDb:AcDbObjects")
    refs: list[ObjectRef] = []
    cursor = 0
    while cursor + 2 <= len(handles):
        block_start = cursor
        block_size = int.from_bytes(handles[cursor : cursor + 2], "big")
        cursor += 2
        if block_size == 0:
            break
        block_end = block_start + block_size
        if block_size < 2 or block_end + 2 > len(handles):
            raise DwgFormatError("bloco do mapa de handles DWG inválido")
        block_handle = 0
        block_offset = 0
        while cursor < block_end:
            previous = cursor
            delta_handle, cursor = modular_char(handles, cursor)
            delta_offset, cursor = modular_char(handles, cursor, signed=True)
            if cursor <= previous or cursor > block_end:
                raise DwgFormatError("entrada inválida no mapa de handles DWG")
            block_handle += delta_handle
            block_offset += delta_offset
            if block_handle < 0 or block_offset < 0:
                raise DwgFormatError("delta negativo inválido no mapa de handles DWG")
            if block_offset < len(objects):
                refs.append(ObjectRef(block_handle, block_offset))
        cursor = block_end + 2  # CRC do bloco; validá-lo fica para a camada CRC.
    if not refs:
        raise DwgFormatError("mapa de handles DWG vazio")
    return ObjectIndex(container, objects, tuple(refs))
