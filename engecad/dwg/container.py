"""Contêiner físico AC18 e R2007+ do DWG.

Esta camada implementa o cabeçalho, a compressão LZ específica do DWG, o mapa
de páginas, o mapa de seções e as páginas de dados das famílias AC18
(AC1018/AC1024/AC1027/AC1032) e R2007 (AC1021). A representação é convertida
para ``bytes`` compactos para que o decodificador de entidades possa trabalhar
sem I/O aleatório.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass
from pathlib import Path


class DwgFormatError(ValueError):
    """Arquivo DWG inválido ou ainda não suportado."""


@dataclass(frozen=True, slots=True)
class FileHeader:
    values: tuple[int, ...]

    @property
    def pages_map_offset(self) -> int:
        return self.values[7]

    @property
    def pages_map_id(self) -> int:
        return self.values[8]

    @property
    def pages_map_size_comp(self) -> int:
        return self.values[10]

    @property
    def pages_map_size_uncomp(self) -> int:
        return self.values[11]

    @property
    def pages_map_correction(self) -> int:
        return self.values[3]

    @property
    def sections_map_id(self) -> int:
        return self.values[24]

    @property
    def sections_map_size_comp(self) -> int:
        return self.values[22]

    @property
    def sections_map_size_uncomp(self) -> int:
        return self.values[25]

    @property
    def sections_map_correction(self) -> int:
        return self.values[27]


@dataclass(frozen=True, slots=True)
class Page:
    identifier: int
    size: int
    offset: int


@dataclass(frozen=True, slots=True)
class SectionPage:
    offset: int
    size: int
    identifier: int
    uncompressed_size: int
    compressed_size: int
    checksum: int
    crc: int


@dataclass(frozen=True, slots=True)
class Section:
    name: str
    data_size: int
    max_size: int
    encrypted: int
    hashcode: int
    encoding: int
    pages: tuple[SectionPage, ...]


@dataclass(frozen=True, slots=True)
class LegacyFileHeader:
    """Campos do cabeçalho AC18 usado por R2004 e pelas versões posteriores.

    AC1024, AC1027 e AC1032 mantêm o mesmo contêiner físico AC18.  O nome
    ``Legacy`` aqui diferencia esse formato do cabeçalho R2007 de 64 bits;
    ele não significa que o arquivo seja tratado como uma versão antiga.
    """

    raw: bytes
    section_page_map_address: int
    section_page_map_id: int
    section_map_id: int


@dataclass(frozen=True, slots=True)
class LegacyPage:
    identifier: int
    size: int
    offset: int


@dataclass(frozen=True, slots=True)
class LegacySectionPage:
    identifier: int
    data_size: int
    start_offset: int
    unknown: int


@dataclass(frozen=True, slots=True)
class LegacySection:
    name: str
    data_size: int
    max_decompressed_size: int
    compressed: int
    section_type: int
    encrypted: int
    pages: tuple[LegacySectionPage, ...]


def _align8(value: int) -> int:
    return (value + 7) & ~7


def _decode_interleaved(raw: bytes, block_count: int, data_size: int) -> bytes:
    """Remove a DWG page's 255/251-byte interleaving.

    The format stores the first byte of every codeword, then the second byte
    of every codeword, and so on.  The parity bytes are deliberately ignored
    here; validation belongs in a later CRC/RS layer and the ODA files used by
    CAD applications normally contain no correctable errors.
    """
    codeword_size = 255
    needed = block_count * codeword_size
    if len(raw) < needed:
        raise DwgFormatError("página DWG truncada no bloco intercalado")
    out = bytearray(block_count * data_size)
    dst = 0
    # The file stores codeword byte i for all codewords before advancing to
    # i+1.  Within a column, codeword n is at n (mod block_count), not in a
    # contiguous block.  Reconstruct complete codewords first, then discard
    # each one's parity tail.
    for block in range(block_count):
        for block_byte in range(data_size):
            out[dst] = raw[block_byte * block_count + block]
            dst += 1
    return bytes(out)


def _copy_from_history(out: bytearray, offset: int, length: int) -> None:
    if offset <= 0 or offset > len(out):
        raise DwgFormatError("referência inválida no compressor DWG")
    source = len(out) - offset
    for _ in range(length):
        out.append(out[source])
        source += 1


def decompress_r2007(data: bytes, expected_size: int) -> bytes:
    """Descomprime o LZ de páginas R2004/R2007 descrito pela ODA.

    O compressor mistura literais e cópias da janela já produzida.  A função
    evita objetos intermediários por byte e limita a saída ao tamanho declarado
    pela seção, protegendo o processo contra arquivos corrompidos.
    """
    if expected_size < 0:
        raise DwgFormatError("tamanho de saída negativo")
    if not data:
        return b"" if expected_size == 0 else (_ for _ in ()).throw(
            DwgFormatError("fluxo comprimido vazio")
        )
    src = 0
    out = bytearray()
    opcode = data[src]
    src += 1
    length = 0

    def literal_length(op: int) -> int:
        nonlocal src
        value = op + 8
        if value == 0x17:
            if src >= len(data):
                raise DwgFormatError("literal DWG truncado")
            n = data[src]
            src += 1
            value += n
            if n == 0xFF:
                while True:
                    if src + 2 > len(data):
                        raise DwgFormatError("literal DWG estendido truncado")
                    n = data[src] | (data[src + 1] << 8)
                    src += 2
                    value += n
                    if n != 0xFFFF:
                        break
        return value

    def instruction(op: int) -> tuple[int, int, int]:
        nonlocal src
        hi = op >> 4
        if hi == 0:
            length = (op & 0xF) + 0x13
            if src + 2 > len(data):
                raise DwgFormatError("instrução DWG truncada")
            offset_low, op2 = data[src], data[src + 1]
            src += 2
            return offset_low + ((op2 & 0x78) << 5) + 1, length + ((op2 >> 3) & 0x10), op2
        if hi == 1:
            length = (op & 0xF) + 3
            if src + 2 > len(data):
                raise DwgFormatError("instrução DWG truncada")
            offset_low, op2 = data[src], data[src + 1]
            src += 2
            return offset_low + ((op2 & 0xF8) << 5) + 1, length, op2
        if hi == 2:
            if src + 2 > len(data):
                raise DwgFormatError("instrução DWG truncada")
            offset = data[src] | (data[src + 1] << 8)
            src += 2
            length = op & 7
            if not (op & 8):
                if src >= len(data):
                    raise DwgFormatError("instrução DWG truncada")
                op2 = data[src]
                src += 1
                return offset, length + (op2 & 0xF8), op2
            offset += 1
            if src + 2 > len(data):
                raise DwgFormatError("instrução DWG estendida truncada")
            length += data[src] << 3
            src += 1
            op2 = data[src]
            src += 1
            return offset, length + ((op2 & 0xF8) << 8) + 0x100, op2
        length = op >> 4
        offset = op & 0xF
        if src >= len(data):
            raise DwgFormatError("instrução DWG truncada")
        op2 = data[src]
        src += 1
        return offset + ((op2 & 0xF8) << 1) + 1, length, op2

    if (opcode & 0xF0) == 0x20:
        if src + 3 > len(data):
            raise DwgFormatError("prefixo DWG comprimido truncado")
        src += 2
        length = data[src] & 7
        src += 1
        if length == 0:
            raise DwgFormatError("literal DWG inicial vazio")

    while src <= len(data):
        if length == 0:
            length = literal_length(opcode)
        if src + length > len(data):
            raise DwgFormatError("literal DWG ultrapassa a página")
        if len(out) + length > expected_size:
            raise DwgFormatError("saída DWG ultrapassa o tamanho declarado")
        out.extend(data[src : src + length])
        src += length
        if len(out) == expected_size:
            return bytes(out)
        if src >= len(data):
            break
        opcode = data[src]
        src += 1
        offset, length, opcode = instruction(opcode)
        while True:
            if len(out) + length > expected_size:
                raise DwgFormatError("cópia DWG ultrapassa o tamanho declarado")
            _copy_from_history(out, offset, length)
            if len(out) == expected_size:
                return bytes(out)
            length = opcode & 7
            if length:
                break
            if src >= len(data):
                return bytes(out)
            opcode = data[src]
            src += 1
            if opcode >> 4 == 0:
                break
            if opcode >> 4 == 0xF:
                opcode &= 0xF
            offset, length, opcode = instruction(opcode)
        length = 0

    if len(out) != expected_size:
        raise DwgFormatError(
            f"fluxo DWG terminou com {len(out)} bytes; esperado {expected_size}"
        )
    return bytes(out)


def decompress_r2004(data: bytes, expected_size: int) -> bytes:
    """Descomprime a variante LZ usada pelo contêiner AC18.

    A codificação tem uma sequência inicial de literais, seguida por
    instruções de cópia e por uma nova sequência de literais.  A implementação
    escreve diretamente em um ``bytearray`` e aceita cópia sobreposta, que é
    essencial para os padrões repetidos comuns em mapas e objetos DWG.
    """
    if expected_size < 0:
        raise DwgFormatError("tamanho de saída negativo")
    if expected_size == 0:
        return b""
    if not data:
        raise DwgFormatError("fluxo AC18 comprimido vazio")

    source = 0
    output = bytearray()
    pending_opcode = 0

    def read_byte() -> int:
        nonlocal source
        if source >= len(data):
            raise DwgFormatError("fluxo AC18 comprimido truncado")
        value = data[source]
        source += 1
        return value

    def read_literal_length() -> int:
        nonlocal pending_opcode
        first = read_byte()
        pending_opcode = 0
        if 1 <= first <= 0x0F:
            return first + 3
        if first == 0:
            total = 0x0F
            value = read_byte()
            while value == 0:
                total += 0xFF
                value = read_byte()
            return total + value + 3
        if first & 0xF0:
            pending_opcode = first
        return 0

    def read_long_offset() -> int:
        value = read_byte()
        if value == 0:
            value = 0xFF
            next_value = read_byte()
            while next_value == 0:
                value += 0xFF
                next_value = read_byte()
            value += next_value
        return value

    def read_two_byte_offset() -> tuple[int, int]:
        first = read_byte()
        second = read_byte()
        return (first >> 2) | (second << 6), first & 0x03

    def append_literals(length: int) -> None:
        if length < 0 or source + length > len(data):
            raise DwgFormatError("literais AC18 truncados")
        remaining = expected_size - len(output)
        if remaining <= 0:
            return
        output.extend(data[source : source + min(length, remaining)])

    def append_copy(distance: int, length: int) -> None:
        if distance < 0 or distance >= len(output):
            raise DwgFormatError("referência AC18 fora da janela produzida")
        start = len(output) - distance - 1
        copy_length = min(length, expected_size - len(output))
        if start + copy_length <= len(output):
            output.extend(output[start : start + copy_length])
            return
        # For an overlapping LZ77 copy, extending one already-produced
        # pattern at a time preserves the compressor's byte-wise semantics,
        # while avoiding a Python loop for every byte of long repetitions.
        while copy_length:
            chunk = min(copy_length, len(output) - start)
            output.extend(output[start : start + chunk])
            start += chunk
            copy_length -= chunk

    literal_length = read_literal_length()
    append_literals(literal_length)
    source += literal_length
    if len(output) == expected_size:
        return bytes(output)

    while source < len(data):
        opcode = pending_opcode or read_byte()
        pending_opcode = 0
        if opcode == 0x11:
            break

        if opcode >= 0x40:
            compressed_length = ((opcode & 0xF0) >> 4) - 1
            offset_byte = read_byte()
            distance = (offset_byte << 2) | ((opcode & 0x0C) >> 2)
            literal_length = opcode & 0x03
            if literal_length == 0:
                literal_length = read_literal_length()
        elif 0x21 <= opcode <= 0x3F:
            compressed_length = opcode - 0x1E
            distance, literal_length = read_two_byte_offset()
            if literal_length == 0:
                literal_length = read_literal_length()
        elif opcode == 0x20:
            compressed_length = read_long_offset() + 0x21
            distance, literal_length = read_two_byte_offset()
            if literal_length == 0:
                literal_length = read_literal_length()
        elif 0x12 <= opcode <= 0x1F:
            compressed_length = (opcode & 0x0F) + 2
            distance, literal_length = read_two_byte_offset()
            distance += 0x3FFF
            if literal_length == 0:
                literal_length = read_literal_length()
        elif opcode == 0x10:
            compressed_length = read_long_offset() + 9
            distance, literal_length = read_two_byte_offset()
            distance += 0x3FFF
            if literal_length == 0:
                literal_length = read_literal_length()
        else:
            raise DwgFormatError(f"opcode AC18 inválido: 0x{opcode:02X}")

        append_copy(distance, compressed_length)
        if len(output) == expected_size:
            return bytes(output)
        append_literals(literal_length)
        source += literal_length
        if len(output) == expected_size:
            return bytes(output)

    if len(output) != expected_size:
        raise DwgFormatError(
            f"fluxo AC18 terminou com {len(output)} bytes; esperado {expected_size}"
        )
    return bytes(output)


def _system_page(
    data: bytes, offset: int, compressed_size: int, uncompressed_size: int, repeat: int
) -> bytes:
    if compressed_size <= 0 or uncompressed_size < 0 or repeat <= 0:
        raise DwgFormatError("metadados inválidos da página DWG")
    encoded_size = _align8(compressed_size) * repeat
    block_count = (encoded_size + 238) // 239
    page_size = _align8(block_count * 255)
    raw = data[offset : offset + page_size]
    payload = _decode_interleaved(raw, block_count, 239)
    payload = payload[:compressed_size]
    if compressed_size < uncompressed_size:
        return decompress_r2007(payload, uncompressed_size)
    return payload[:uncompressed_size]


def _data_page(
    data: bytes,
    page_offset: int,
    page_size: int,
    compressed_size: int,
    uncompressed_size: int,
) -> bytes:
    if compressed_size < 0 or uncompressed_size < 0 or compressed_size > page_size:
        raise DwgFormatError("tamanho de página DWG inválido")
    if compressed_size == uncompressed_size:
        raw = data[page_offset : page_offset + uncompressed_size]
        if len(raw) != uncompressed_size:
            raise DwgFormatError("página DWG não comprimida truncada")
        return raw
    encoded_size = _align8(compressed_size)
    block_count = (encoded_size + 250) // 251
    raw = data[page_offset : page_offset + page_size]
    payload = _decode_interleaved(raw, block_count, 251)[:compressed_size]
    return decompress_r2007(payload, uncompressed_size)


def _legacy_magic(size: int = 0x6C) -> bytes:
    seed = 1
    result = bytearray(size)
    for index in range(size):
        seed = (seed * 0x343FD + 0x269EC3) & 0xFFFFFFFF
        result[index] = (seed >> 16) & 0xFF
    return bytes(result)


def _legacy_system_page(data: bytes, offset: int, expected_signature: int) -> bytes:
    if offset < 0 or offset + 0x14 > len(data):
        raise DwgFormatError("página de sistema AC18 fora do arquivo")
    signature, uncompressed_size, compressed_size, compression, checksum = struct.unpack_from(
        "<5I", data, offset
    )
    if signature != expected_signature:
        raise DwgFormatError(
            f"assinatura AC18 inválida: 0x{signature:08X}, esperado 0x{expected_signature:08X}"
        )
    if compressed_size > len(data) - offset - 0x14:
        raise DwgFormatError("página de sistema AC18 truncada")
    payload = data[offset + 0x14 : offset + 0x14 + compressed_size]
    if compression == 2:
        return decompress_r2004(payload, uncompressed_size)
    if compression == 0:
        if len(payload) < uncompressed_size:
            raise DwgFormatError("página de sistema AC18 não comprimida truncada")
        return payload[:uncompressed_size]
    raise DwgFormatError(f"compressão AC18 desconhecida: {compression}")


def _legacy_page_header(data: bytes, offset: int) -> tuple[int, ...]:
    if offset < 0 or offset + 0x20 > len(data):
        raise DwgFormatError("cabeçalho de página AC18 truncado")
    fields = list(struct.unpack_from("<8I", data, offset))
    mask = (0x4164536B ^ offset) & 0xFFFFFFFF
    for index, value in enumerate(fields):
        fields[index] = value ^ mask
    if fields[0] != 0x4163043B:
        raise DwgFormatError(
            f"assinatura de página AC18 inválida: 0x{fields[0]:08X}"
        )
    return tuple(fields)


class DwgContainer:
    """Contêiner lógico aberto, com seções disponíveis por nome/hash."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.data = self.path.read_bytes()
        if len(self.data) < 6:
            raise DwgFormatError("arquivo DWG vazio ou truncado")
        self.version = self.data[:6].decode("ascii", errors="replace")
        if self.version not in {"AC1018", "AC1021", "AC1024", "AC1027", "AC1032"}:
            raise DwgFormatError(
                f"versão {self.version} ainda não possui contêiner implementado"
            )
        if self.version == "AC1021":
            self.header = self._read_file_header()
            self.pages = self._read_pages()
            self.sections = self._read_sections()
            self._legacy_header = None
            self._legacy_sections = {}
        else:
            self._legacy_header = self._read_legacy_file_header()
            try:
                self.pages = self._read_legacy_pages()
            except DwgFormatError:
                self.pages = self._recover_legacy_pages()
            try:
                self._legacy_sections = self._read_legacy_sections()
            except DwgFormatError:
                self._legacy_sections = self._recover_legacy_sections()
            self.header = None
            self.sections = {}

    def _read_file_header(self) -> FileHeader:
        encoded = self.data[0x80 : 0x80 + 0x3D8]
        if len(encoded) != 0x3D8:
            raise DwgFormatError("cabeçalho DWG R2007+ truncado")
        payload = _decode_interleaved(encoded, 3, 239)
        if len(payload) < 32:
            raise DwgFormatError("cabeçalho DWG R2007+ inválido")
        compressed_size = struct.unpack_from("<i", payload, 24)[0]
        if compressed_size > 0:
            raw_header = decompress_r2007(payload[32 : 32 + compressed_size], 0x110)
        else:
            raw_header = payload[32 : 32 + 0x110]
        if len(raw_header) != 0x110:
            raise DwgFormatError("cabeçalho DWG R2007+ incompleto")
        values = struct.unpack("<34q", raw_header)
        return FileHeader(values)

    def _read_legacy_file_header(self) -> LegacyFileHeader:
        encoded = self.data[0x80 : 0x80 + 0x6C]
        if len(encoded) != 0x6C:
            raise DwgFormatError("cabeçalho DWG AC18 truncado")
        raw = bytes(
            value ^ mask for value, mask in zip(encoded, _legacy_magic(), strict=True)
        )
        if not raw.startswith(b"AcFssFcAJMB"):
            raise DwgFormatError("assinatura do cabeçalho DWG AC18 inválida")
        page_map_address = struct.unpack_from("<Q", raw, 0x54)[0] + 0x100
        page_map_id = struct.unpack_from("<I", raw, 0x50)[0]
        section_map_id = struct.unpack_from("<I", raw, 0x5C)[0]
        return LegacyFileHeader(raw, page_map_address, page_map_id, section_map_id)

    def _read_legacy_pages(self) -> dict[int, LegacyPage]:
        assert self._legacy_header is not None
        raw = _legacy_system_page(
            self.data,
            self._legacy_header.section_page_map_address,
            0x41630E3B,
        )
        pages: dict[int, LegacyPage] = {}
        offset = 0x100
        cursor = 0
        while cursor + 8 <= len(raw):
            identifier, size = struct.unpack_from("<iI", raw, cursor)
            cursor += 8
            if size == 0:
                break
            page = LegacyPage(identifier, size, offset)
            pages[identifier] = page
            pages.setdefault(abs(identifier), page)
            offset += size
            if identifier < 0:
                if cursor + 16 > len(raw):
                    raise DwgFormatError("metadados de página AC18 truncados")
                cursor += 16
        if not pages:
            raise DwgFormatError("mapa de páginas AC18 vazio")
        if (
            self._legacy_header.section_page_map_id not in pages
            or self._legacy_header.section_map_id not in pages
        ):
            raise DwgFormatError("mapa de páginas AC18 sem as páginas de sistema")
        return pages

    def _recover_legacy_pages(self) -> dict[int, LegacyPage]:
        """Rebuild the AC18 page index from authenticated physical headers.

        A few exporters have been observed to emit a valid page stream but an
        inconsistent compressed page map.  The encrypted data-page headers
        still contain the physical page sizes, so recovery is safe when every
        aligned page validates and the expected two system pages are present.
        """
        assert self._legacy_header is not None
        data_pages: list[tuple[int, tuple[int, ...]]] = []
        for offset in range(0x100, len(self.data) - 0x20, 0x20):
            mask = (0x4164536B ^ offset) & 0xFFFFFFFF
            fields = tuple(
                value ^ mask
                for value in struct.unpack_from("<8I", self.data, offset)
            )
            if fields[0] != 0x4163043B:
                continue
            if (
                fields[1] <= 0
                or fields[2] <= 0
                or fields[3] < 0x20
                or fields[3] % 0x20
                or fields[2] > fields[3] - 0x20
                or offset + fields[3] > len(self.data)
            ):
                continue
            data_pages.append((offset, fields))

        system_pages: dict[int, LegacyPage] = {}
        for offset in range(0x100, len(self.data) - 0x14, 0x20):
            signature, _uncompressed, compressed, compression, _checksum = struct.unpack_from(
                "<5I", self.data, offset
            )
            if signature not in {0x41630E3B, 0x4163003B} or compression != 2:
                continue
            if compressed <= 0 or offset + 0x14 + compressed > len(self.data):
                continue
            identifier = (
                self._legacy_header.section_page_map_id
                if signature == 0x41630E3B
                else self._legacy_header.section_map_id
            )
            system_pages[identifier] = LegacyPage(
                identifier,
                _align8(compressed + 0x14),
                offset,
            )

        if not data_pages or len(system_pages) != 2:
            raise DwgFormatError("não foi possível recuperar as páginas físicas AC18")
        pages = {
            index: LegacyPage(index, fields[3], offset)
            for index, (offset, fields) in enumerate(data_pages, start=1)
        }
        pages.update(system_pages)
        return pages

    def _read_legacy_sections(self) -> dict[str, LegacySection]:
        assert self._legacy_header is not None
        map_page = self.pages.get(self._legacy_header.section_map_id)
        if map_page is None:
            raise DwgFormatError("página do mapa de seções AC18 ausente")
        raw = _legacy_system_page(self.data, map_page.offset, 0x4163003B)
        if len(raw) < 20:
            raise DwgFormatError("mapa de seções AC18 truncado")
        count, marker, version, zero, unknown = struct.unpack_from("<5I", raw, 0)
        if count > 100_000:
            raise DwgFormatError("quantidade de seções AC18 inválida")
        cursor = 20
        sections: dict[str, LegacySection] = {}
        for _ in range(count):
            if cursor + 96 > len(raw):
                raise DwgFormatError("descrição de seção AC18 truncada")
            (
                data_size,
                unknown1,
                page_count,
                max_decompressed_size,
                unknown2,
                compressed,
                section_type,
                encrypted,
            ) = struct.unpack_from("<8I", raw, cursor)
            cursor += 32
            name_raw = raw[cursor : cursor + 64]
            cursor += 64
            name = name_raw.split(b"\0", 1)[0].decode("ascii", errors="replace")
            if page_count > 1_000_000 or cursor + page_count * 16 > len(raw):
                raise DwgFormatError("páginas de seção AC18 inválidas")
            pages: list[LegacySectionPage] = []
            for _ in range(page_count):
                identifier, page_data_size, start_offset, page_unknown = struct.unpack_from(
                    "<4I", raw, cursor
                )
                cursor += 16
                pages.append(
                    LegacySectionPage(
                        identifier,
                        page_data_size,
                        start_offset,
                        page_unknown,
                    )
                )
            sections[name] = LegacySection(
                name,
                data_size,
                max_decompressed_size,
                compressed,
                section_type,
                encrypted,
                tuple(pages),
            )
        return sections

    def _recover_legacy_sections(self) -> dict[str, LegacySection]:
        """Recover section descriptors when the local page map is malformed."""
        assert self._legacy_header is not None
        names_by_id = {
            13: "AcDb:FileDepList",
            12: "AcDb:AcDsPrototype_1b",
            11: "AcDb:AppInfo",
            10: "AcDb:Preview",
            9: "AcDb:SummaryInfo",
            8: "AcDb:RevHistory",
            7: "AcDb:AcDbObjects",
            6: "AcDb:ObjFreeSpace",
            5: "AcDb:Template",
            4: "AcDb:Handles",
            3: "AcDb:Classes",
            2: "AcDb:AuxHeader",
            1: "AcDb:Header",
            0: "",
        }
        max_sizes = {
            13: 0x300,
            12: 0x7400,
            11: 0x300,
            10: 0xA0,
            9: 0x80,
            8: 0x7400,
            7: 0x7400,
            6: 0x7400,
            5: 0x7400,
            4: 0x7400,
            3: 0x7400,
            2: 0x7400,
            1: 0x7400,
            0: 0x7400,
        }
        compressed_by_id = {13: 1, 12: 2, 11: 1, 10: 1, 9: 1, 8: 2, 7: 2}
        encrypted_by_id = {13: 2}
        metadata: dict[int, tuple[int, int, int, int, str]] = {}
        map_page = self.pages.get(self._legacy_header.section_map_id)
        if map_page is not None:
            try:
                raw = _legacy_system_page(self.data, map_page.offset, 0x4163003B)
                count = struct.unpack_from("<I", raw, 0)[0]
                cursor = 20
                for _ in range(min(count, 100_000)):
                    if cursor + 96 > len(raw):
                        break
                    fields = struct.unpack_from("<8I", raw, cursor)
                    cursor += 32
                    name = raw[cursor : cursor + 64].split(b"\0", 1)[0].decode(
                        "ascii", errors="replace"
                    )
                    cursor += 64
                    page_count = fields[2]
                    if page_count > 1_000_000 or cursor + page_count * 16 > len(raw):
                        break
                    metadata[fields[6]] = (
                        fields[0],
                        fields[3],
                        fields[5],
                        fields[7],
                        name,
                    )
                    cursor += page_count * 16
            except (DwgFormatError, struct.error):
                metadata.clear()
        for section_id, (_size, max_size, compressed, encrypted, name) in metadata.items():
            if name:
                names_by_id[section_id] = name
            max_sizes[section_id] = max_size
            compressed_by_id[section_id] = compressed
            encrypted_by_id[section_id] = encrypted
        groups: dict[int, list[LegacySectionPage]] = {}
        for identifier, physical in self.pages.items():
            if identifier in {
                self._legacy_header.section_page_map_id,
                self._legacy_header.section_map_id,
            }:
                continue
            header = _legacy_page_header(self.data, physical.offset)
            section_id = header[1]
            groups.setdefault(section_id, []).append(
                LegacySectionPage(identifier, header[2], header[4], header[7])
            )
        for pages in groups.values():
            pages.sort(key=lambda page: page.identifier)

        sections: dict[str, LegacySection] = {}
        for section_id, name in names_by_id.items():
            pages = tuple(groups.get(section_id, ()))
            page_size = max_sizes.get(section_id, 0x7400)
            data_size = metadata.get(section_id, (0, 0, 0, 0, ""))[0]
            if not data_size:
                data_size = max(
                    (page.start_offset + page_size for page in pages), default=0
                )
            sections[name] = LegacySection(
                name,
                data_size,
                page_size,
                compressed_by_id.get(section_id, 2),
                section_id,
                encrypted_by_id.get(section_id, 0),
                pages,
            )
        return sections

    def _read_pages(self) -> dict[int, Page]:
        offset = 0x480 + self.header.pages_map_offset
        raw = _system_page(
            self.data,
            offset,
            self.header.pages_map_size_comp,
            self.header.pages_map_size_uncomp,
            self.header.pages_map_correction,
        )
        pages: dict[int, Page] = {}
        logical_offset = 0x480
        for pos in range(0, len(raw) - 15, 16):
            size, identifier = struct.unpack_from("<qq", raw, pos)
            if size <= 0 or identifier == 0:
                continue
            pages[abs(identifier)] = Page(identifier, size, logical_offset)
            logical_offset += size
        if not pages:
            raise DwgFormatError("mapa de páginas DWG vazio")
        return pages

    def _read_sections(self) -> dict[int, Section]:
        page = self.pages.get(abs(self.header.sections_map_id))
        if page is None:
            raise DwgFormatError("página do mapa de seções DWG ausente")
        raw = _system_page(
            self.data,
            page.offset,
            self.header.sections_map_size_comp,
            self.header.sections_map_size_uncomp,
            self.header.sections_map_correction,
        )
        sections: dict[int, Section] = {}
        pos = 0
        while pos + 64 <= len(raw):
            fields = struct.unpack_from("<8q", raw, pos)
            pos += 64
            (
                data_size,
                max_size,
                encrypted,
                hashcode,
                name_len,
                unknown,
                encoding,
                num_pages,
            ) = fields
            if name_len < 0 or name_len > 4096 or num_pages < 0 or num_pages > 1_000_000:
                raise DwgFormatError("entrada inválida no mapa de seções DWG")
            name_bytes = raw[pos : pos + (name_len + 1) * 2]
            pos += (name_len + 1) * 2
            if len(name_bytes) != (name_len + 1) * 2:
                raise DwgFormatError("nome de seção DWG truncado")
            name = name_bytes[:-2].decode("utf-16le", errors="replace")
            page_rows: list[SectionPage] = []
            for _ in range(num_pages):
                if pos + 56 > len(raw):
                    raise DwgFormatError("página de seção DWG truncada")
                page_rows.append(SectionPage(*struct.unpack_from("<7q", raw, pos)))
                pos += 56
            sections[hashcode & 0xFFFFFFFF] = Section(
                name,
                data_size,
                max_size,
                encrypted,
                hashcode & 0xFFFFFFFF,
                encoding,
                tuple(page_rows),
            )
        return sections

    def section(self, hashcode: int) -> bytes:
        if self.version != "AC1021":
            raise DwgFormatError("seções AC18 devem ser acessadas por nome")
        section = self.sections.get(hashcode & 0xFFFFFFFF)
        if section is None:
            raise DwgFormatError(f"seção DWG 0x{hashcode:08X} ausente")
        page_size = section.max_decompressed_size
        result = bytearray(page_size * len(section.pages))
        for _page_index, page in enumerate(section.pages):
            physical = self.pages.get(abs(page.identifier))
            if physical is None:
                raise DwgFormatError("página física de seção DWG ausente")
            part = _data_page(
                self.data,
                physical.offset,
                physical.size,
                page.compressed_size,
                page.uncompressed_size,
            )
            end = page.offset + len(part)
            if page.offset < 0 or end > len(result):
                raise DwgFormatError("página de seção DWG fora dos limites")
            result[page.offset:end] = part
        return bytes(result)

    def section_named(self, name: str) -> bytes:
        """Retorna uma seção AC18 pelo nome, reconstruindo suas páginas sob demanda."""
        section = self._legacy_sections.get(name)
        if section is None:
            raise DwgFormatError(f"seção DWG {name!r} ausente")
        if section.encrypted:
            raise DwgFormatError(f"seção DWG {name!r} está criptografada")
        page_size = section.max_decompressed_size
        result_size = max(
            section.data_size,
            max(
                (page.start_offset + page_size for page in section.pages),
                default=0,
            ),
        )
        result = bytearray(result_size)
        for page in section.pages:
            physical = self.pages.get(page.identifier)
            if physical is None:
                raise DwgFormatError(
                    f"página física AC18 {page.identifier} ausente na seção {name!r}"
                )
            header = _legacy_page_header(self.data, physical.offset)
            compressed_size = header[2]
            payload_start = physical.offset + 0x20
            payload_end = payload_start + compressed_size
            if payload_end > len(self.data) or payload_end > physical.offset + physical.size:
                raise DwgFormatError("página de dados AC18 truncada")
            payload = self.data[payload_start:payload_end]
            if section.compressed == 2:
                # AC18 pages are placed in fixed-size logical slots.  The
                # data-page field is the physical page size, while the
                # section map's max_decompressed_size is the output slot
                # expected by the object streams.
                part = decompress_r2004(payload, page_size)
            else:
                if len(payload) < page_size:
                    raise DwgFormatError("página de dados AC18 não comprimida truncada")
                part = payload[:page_size]
            start = page.start_offset
            end = start + len(part)
            if start < 0 or end > len(result):
                raise DwgFormatError("página de seção AC18 fora dos limites")
            result[start:end] = part
        return bytes(result[: section.data_size])

    @property
    def section_names(self) -> tuple[str, ...]:
        return tuple(self._legacy_sections)
