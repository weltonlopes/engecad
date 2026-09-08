"""Decodificação das entidades geométricas mais comuns do DWG.

O módulo trabalha diretamente sobre ``BitReader`` e ``ObjectRecord``. Registros
de classes proprietárias permanecem preservados no índice e não interrompem a
abertura do desenho.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .bitstream import BitReader
from .container import DwgFormatError
from .objects import ObjectIndex, ObjectRecord, ObjectRef

LINE = 0x13
ARC = 0x11
CIRCLE = 0x12
POINT = 0x1B
LWPOLYLINE = 0x4D

# DWG stores coordinates as unrestricted doubles, but a value this large is
# not useful to a CAD drawing and, in practice, indicates that a record was
# decoded with the wrong object layout.  Letting one such value reach the
# document extents makes Zoom Extents reduce every real entity to a sub-pixel
# and produces the misleading "arquivo vazio" symptom.
MAX_GEOMETRY_COORDINATE = 1.0e9


@dataclass(frozen=True, slots=True)
class DecodedEntity:
    type: str
    handle: int
    layer_handle: int | None
    owner_handle: int | None
    data: dict
    entity_mode: int = 3


@dataclass(frozen=True, slots=True)
class _Common:
    object_size_bits: int
    handle: int
    color_index: int | None
    true_color: int | None
    entity_mode: int
    reactors: int
    xdic_missing: bool
    ltype_flags: int
    plotstyle_flags: int
    material_flags: int
    full_visual_style: bool
    face_visual_style: bool
    edge_visual_style: bool


def _read_eed(reader: BitReader) -> None:
    size = reader.read_bitshort()
    while size:
        reader.read_handle()
        reader.read_bytes(size)
        size = reader.read_bitshort()


def _read_common(
    reader: BitReader,
    *,
    r21: bool,
    r2013: bool,
    object_data_end_bits: int | None,
) -> _Common:
    object_size_bits = object_data_end_bits if r21 else reader.read_raw_u32()
    kind, handle = reader.read_handle()
    del kind
    _read_eed(reader)
    if reader.read_bit():
        graphics_size = reader.read_bitlonglong() if r21 else reader.read_raw_u32()
        reader.read_bytes(graphics_size)

    entity_mode = reader.read_bits(2)
    reactors = reader.read_bitlong()
    if reactors > 1_000_000:
        raise DwgFormatError("quantidade de reactors DWG inválida")
    xdic_missing = bool(reader.read_bit())
    if r2013:
        reader.read_bit()  # DS binary data flag

    color_index: int | None = None
    true_color: int | None = None
    no_links = reader.read_bit()
    if no_links == 0:
        color_mode = reader.read_bit()
        if color_mode == 1:
            color_index = reader.read_raw_u8()
        else:
            flags = reader.read_raw_u16()
            color_index = flags & 0x01FF
            if flags & 0x8000:
                true_color = reader.read_raw_u32()
            if flags & 0x2000:
                reader.read_raw_u32()  # transparency
    else:
        reader.read_bit()  # unknown color flag

    reader.read_bitdouble()  # linetype scale
    ltype_flags = reader.read_bits(2)
    plotstyle_flags = reader.read_bits(2)
    material_flags = 0
    if r21:
        material_flags = reader.read_bits(2)
        reader.read_raw_u8()  # shadow flags
    full_visual = face_visual = edge_visual = False
    if r21:
        full_visual = bool(reader.read_bit())
        face_visual = bool(reader.read_bit())
        edge_visual = bool(reader.read_bit())
    reader.read_bitshort()  # invisibility
    reader.read_raw_u8()  # line weight
    return _Common(
        object_size_bits,
        handle,
        color_index,
        true_color,
        entity_mode,
        reactors,
        xdic_missing,
        ltype_flags,
        plotstyle_flags,
        material_flags,
        full_visual,
        face_visual,
        edge_visual,
    )


def _handle_reference(reader: BitReader, base_handle: int) -> int:
    code, value = reader.read_handle()
    if code == 0x06:
        return base_handle + 1
    if code == 0x08:
        return max(0, base_handle - 1)
    if code == 0x0A:
        return base_handle + value
    if code == 0x0C:
        return max(0, base_handle - value)
    return value


def _read_common_handles(reader: BitReader, common: _Common) -> tuple[int | None, int | None]:
    reader.set_bit_position(common.object_size_bits)
    owner = None
    if common.entity_mode == 0:
        owner = _handle_reference(reader, common.handle)
    for _ in range(common.reactors):
        _handle_reference(reader, common.handle)
    if not common.xdic_missing:
        _handle_reference(reader, common.handle)
    layer = _handle_reference(reader, common.handle)
    if common.ltype_flags == 3:
        _handle_reference(reader, common.handle)
    if common.plotstyle_flags == 3:
        _handle_reference(reader, common.handle)
    if common.material_flags == 3:
        _handle_reference(reader, common.handle)
    if common.full_visual_style:
        _handle_reference(reader, common.handle)
    if common.face_visual_style:
        _handle_reference(reader, common.handle)
    if common.edge_visual_style:
        _handle_reference(reader, common.handle)
    return owner, layer


def _prefix(index: ObjectIndex, record: ObjectRecord) -> tuple[BitReader, int, _Common]:
    reader = BitReader(record.body)
    r21 = index._r21
    if r21:
        reader.read_modular_char()  # handle-stream size; already used for bounds
        type_code = reader.read_object_type_r2010()
    else:
        type_code = reader.read_bitshort()
    # Most records in a DWG are tables, dictionaries, or application objects.
    # Reject them before decoding the comparatively expensive common entity
    # header.  This is the hot path when opening a large drawing and avoids
    # doing all optional-handle work for objects that cannot be rendered here.
    if type_code not in {LINE, ARC, CIRCLE, POINT, LWPOLYLINE}:
        raise DwgFormatError("tipo de objeto não geométrico")
    if r21:
        common = _read_common(
            reader,
            r21=True,
            r2013=index.container.version in {"AC1027", "AC1032"},
            object_data_end_bits=record.object_data_end_bits,
        )
    else:
        common = _read_common(
            reader,
            r21=False,
            r2013=False,
            object_data_end_bits=None,
        )
    if common.handle == 0:
        common = _Common(
            common.object_size_bits,
            record.handle,
            common.color_index,
            common.true_color,
            common.entity_mode,
            common.reactors,
            common.xdic_missing,
            common.ltype_flags,
            common.plotstyle_flags,
            common.material_flags,
            common.full_visual_style,
            common.face_visual_style,
            common.edge_visual_style,
        )
    return reader, type_code, common


def _read_line(reader: BitReader) -> dict:
    z_zero = reader.read_bit()
    x0 = reader.read_raw_f64()
    x1 = reader.read_delta_double(x0)
    y0 = reader.read_raw_f64()
    y1 = reader.read_delta_double(y0)
    if z_zero:
        z0 = z1 = 0.0
    else:
        z0 = reader.read_raw_f64()
        z1 = reader.read_delta_double(z0)
    reader.read_bitthickness()
    extrusion = reader.read_bitextrusion()
    return {"start": (x0, y0, z0), "end": (x1, y1, z1), "extrusion": extrusion}


def _read_circle(reader: BitReader) -> dict:
    center = tuple(reader.read_bitdouble() for _ in range(3))
    radius = reader.read_bitdouble()
    reader.read_bitthickness()
    extrusion = reader.read_bitextrusion()
    return {"center": center, "radius": radius, "extrusion": extrusion}


def _read_arc(reader: BitReader) -> dict:
    result = _read_circle(reader)
    result["start_angle"] = reader.read_bitdouble()
    result["end_angle"] = reader.read_bitdouble()
    return result


def _read_lwpolyline(reader: BitReader) -> dict:
    flags = reader.read_bitshort()
    const_width = reader.read_bitdouble() if flags & 0x04 else None
    if flags & 0x08:
        reader.read_bitdouble()  # elevation
    if flags & 0x02:
        reader.read_bitdouble()  # thickness
    if flags & 0x01:
        tuple(reader.read_bitdouble() for _ in range(3))
    count = reader.read_bitlong()
    if count > 1_000_000:
        raise DwgFormatError("LWPOLYLINE com quantidade de vértices inválida")
    bulge_count = reader.read_bitlong() if flags & 0x10 else 0
    vertex_id_count = reader.read_bitlong() if flags & 0x0400 else 0
    width_count = reader.read_bitlong() if flags & 0x20 else 0
    if max(bulge_count, vertex_id_count, width_count) > 1_000_000:
        raise DwgFormatError("LWPOLYLINE com vetor auxiliar inválido")
    vertices: list[tuple[float, float]] = []
    if count:
        x = reader.read_raw_f64()
        y = reader.read_raw_f64()
        vertices.append((x, y))
        for _ in range(1, count):
            x = reader.read_delta_double(x)
            y = reader.read_delta_double(y)
            vertices.append((x, y))
    bulges = [reader.read_bitdouble() for _ in range(bulge_count)]
    for _ in range(vertex_id_count):
        reader.read_bitlong()
    widths = [(reader.read_bitdouble(), reader.read_bitdouble()) for _ in range(width_count)]
    return {
        "flags": flags,
        "vertices": vertices,
        "const_width": const_width,
        "bulges": bulges,
        "widths": widths,
    }


def decode_entity(
    index: ObjectIndex,
    ref: ObjectRef,
    entity_modes: set[int] | frozenset[int] | None = None,
) -> DecodedEntity | None:
    try:
        record = index.record(ref)
        reader, type_code, common = _prefix(index, record)
        # Placement is known immediately after the common header.  Skipping
        # block-definition and paper-space records before decoding their
        # geometry is a major opening-time win for drawings with many INSERTs.
        if entity_modes is not None and common.entity_mode not in entity_modes:
            return None
        if type_code == LINE:
            data = _read_line(reader)
            name = "LINE"
        elif type_code == ARC:
            data = _read_arc(reader)
            name = "ARC"
        elif type_code == CIRCLE:
            data = _read_circle(reader)
            name = "CIRCLE"
        elif type_code == LWPOLYLINE:
            data = _read_lwpolyline(reader)
            name = "LWPOLYLINE"
        elif type_code == POINT:
            data = {
                "point": tuple(reader.read_bitdouble() for _ in range(3)),
            }
            reader.read_bitthickness()
            reader.read_bitextrusion()
            name = "POINT"
        else:
            return None
        try:
            owner, layer = _read_common_handles(reader, common)
        except DwgFormatError:
            # The geometry stream is independently recoverable in R2010+;
            # some producers omit or reorder optional handle links. Preserve
            # the drawable entity instead of dropping it altogether.
            owner, layer = None, None
        if any(
            not math.isfinite(value) or abs(value) > MAX_GEOMETRY_COORDINATE
            for value in _walk_numbers(data)
        ):
            return None
        return DecodedEntity(name, ref.handle, layer, owner, data, common.entity_mode)
    except (DwgFormatError, IndexError, OverflowError, ValueError):
        return None


def _walk_numbers(value):
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, (tuple, list)):
        for item in value:
            yield from _walk_numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_numbers(item)


def iter_entities(index: ObjectIndex, entity_modes=None):
    for ref in index.refs:
        entity = decode_entity(index, ref, entity_modes)
        if entity is not None:
            yield entity
