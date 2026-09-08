"""Leitor de primitivas compactadas do fluxo de objetos DWG.

O fluxo de entidades DWG mistura campos alinhados a byte com códigos cujo
primeiro bit é o mais significativo.  Esta classe mantém somente um cursor e
evita criar fatias do ``bytes`` de origem; isso é importante para desenhos com
milhões de entidades.
"""

from __future__ import annotations

import struct

from .container import DwgFormatError


class BitReader:
    """Leitor MSB-first com operações primitivas usadas pelo DWG."""

    __slots__ = ("data", "bitpos", "limit")

    def __init__(self, data: bytes | memoryview, bit_length: int | None = None):
        self.data = memoryview(data)
        self.bitpos = 0
        self.limit = len(self.data) * 8 if bit_length is None else bit_length
        if self.limit < 0 or self.limit > len(self.data) * 8:
            raise ValueError("limite de bits inválido")

    @property
    def remaining(self) -> int:
        return self.limit - self.bitpos

    def _need(self, count: int) -> None:
        if count < 0 or self.bitpos + count > self.limit:
            raise DwgFormatError("fluxo de bits DWG truncado")

    def read_bits(self, count: int) -> int:
        if count < 0 or count > 64:
            raise ValueError("quantidade de bits inválida")
        self._need(count)
        value = 0
        while count:
            byte = self.data[self.bitpos >> 3]
            available = 8 - (self.bitpos & 7)
            take = min(count, available)
            shift = available - take
            value = (value << take) | ((byte >> shift) & ((1 << take) - 1))
            self.bitpos += take
            count -= take
        return value

    def read_bit(self) -> int:
        return self.read_bits(1)

    def read_bool(self) -> bool:
        return bool(self.read_bit())

    def align_byte(self) -> None:
        self.bitpos = (self.bitpos + 7) & ~7
        if self.bitpos > self.limit:
            raise DwgFormatError("alinhamento fora do fluxo DWG")

    def set_bit_position(self, bitpos: int) -> None:
        if bitpos < 0 or bitpos > self.limit:
            raise DwgFormatError("posição de bits DWG fora dos limites")
        self.bitpos = bitpos

    def read_bytes(self, count: int) -> bytes:
        if count < 0:
            raise ValueError("quantidade de bytes inválida")
        if self.bitpos & 7 == 0:
            self._need(count * 8)
            start = self.bitpos >> 3
            self.bitpos += count * 8
            return self.data[start : start + count].tobytes()
        self._need(count * 8)
        start = self.bitpos >> 3
        shift = self.bitpos & 7
        result = bytearray(count)
        for index in range(count):
            result[index] = (
                (self.data[start + index] << 8 | self.data[start + index + 1])
                >> (8 - shift)
            ) & 0xFF
        self.bitpos += count * 8
        return bytes(result)

    def read_raw_u8(self) -> int:
        return self.read_bits(8)

    def read_raw_i8(self) -> int:
        value = self.read_raw_u8()
        return value - 0x100 if value & 0x80 else value

    def read_raw_u16(self) -> int:
        return int.from_bytes(self.read_bytes(2), "little", signed=False)

    def read_raw_i16(self) -> int:
        return int.from_bytes(self.read_bytes(2), "little", signed=True)

    def read_raw_u32(self) -> int:
        return int.from_bytes(self.read_bytes(4), "little", signed=False)

    def read_raw_i32(self) -> int:
        return int.from_bytes(self.read_bytes(4), "little", signed=True)

    def read_raw_u64(self) -> int:
        return int.from_bytes(self.read_bytes(8), "little", signed=False)

    def read_raw_i64(self) -> int:
        return int.from_bytes(self.read_bytes(8), "little", signed=True)

    def read_raw_f64(self) -> float:
        return struct.unpack("<d", self.read_bytes(8))[0]

    def read_bitshort(self) -> int:
        code = self.read_bits(2)
        if code == 0:
            return self.read_raw_i16()
        if code == 1:
            return self.read_raw_u8()
        if code == 2:
            return 0
        return 256

    def read_bitlong(self) -> int:
        code = self.read_bits(2)
        if code == 0:
            return self.read_raw_i32()
        if code == 1:
            return self.read_raw_u8()
        if code == 2:
            return 0
        return 0

    def read_object_type_r2010(self) -> int:
        """Lê OT do fluxo R2010+ (código curto, estendido ou RS)."""
        code = self.read_bits(2)
        if code == 0:
            return self.read_raw_u8()
        if code == 1:
            return self.read_raw_u8() + 0x01F0
        return self.read_raw_u16()

    def read_bitdouble(self) -> float:
        code = self.read_bits(2)
        if code == 0:
            return self.read_raw_f64()
        if code == 1:
            return 1.0
        if code == 2:
            return 0.0
        return 0.0

    def read_bitlonglong(self) -> int:
        count = self.read_bits(3)
        if count > 8:
            raise DwgFormatError("quantidade BLL inválida no DWG")
        value = 0
        for _ in range(count):
            value = (value << 8) | self.read_raw_u8()
        return value

    def read_bitthickness(self) -> float:
        return 0.0 if self.read_bit() else self.read_bitdouble()

    def read_bitextrusion(self) -> tuple[float, float, float]:
        if self.read_bit():
            return 0.0, 0.0, 1.0
        return self.read_bitdouble(), self.read_bitdouble(), self.read_bitdouble()

    def read_delta_double(self, default: float) -> float:
        code = self.read_bits(2)
        if code == 0:
            return default
        raw = bytearray(struct.pack("<d", default))
        if code == 1:
            raw[0] = self.read_raw_u8()
            raw[1] = self.read_raw_u8()
            raw[2] = self.read_raw_u8()
            raw[3] = self.read_raw_u8()
        elif code == 2:
            raw[4] = self.read_raw_u8()
            raw[5] = self.read_raw_u8()
            raw[0] = self.read_raw_u8()
            raw[1] = self.read_raw_u8()
            raw[2] = self.read_raw_u8()
            raw[3] = self.read_raw_u8()
        else:
            return self.read_raw_f64()
        return struct.unpack("<d", raw)[0]

    def read_modular_char(self, *, signed: bool = False) -> int:
        """Lê MC/MC signed, cuja continuação usa o bit 7."""
        value = 0
        shift = 0
        for _ in range(10):
            byte = self.read_raw_u8()
            if not (byte & 0x80):
                negative = signed and bool(byte & 0x40)
                if negative:
                    byte &= 0x3F
                value |= byte << shift
                if negative:
                    value = -value
                return value
            value |= (byte & 0x7F) << shift
            shift += 7
        raise DwgFormatError("MC DWG excessivamente longo")

    def read_modular_short(self) -> int:
        """Lê MS: palavras de 15 bits com bit 15 de continuação."""
        value = 0
        shift = 0
        for _ in range(2):
            word = self.read_raw_u16()
            value |= (word & 0x7FFF) << shift
            if not (word & 0x8000):
                return value
            shift += 15
        return value

    def read_handle(self) -> tuple[int, int]:
        """Lê H e retorna ``(código, valor)``."""
        code = self.read_raw_u8()
        counter = code & 0x0F
        if counter > 4:
            raise DwgFormatError("contador de handle DWG inválido")
        value = 0
        for _ in range(counter):
            value = (value << 8) | self.read_raw_u8()
        return code >> 4, value


def modular_char(
    data: bytes | memoryview, offset: int = 0, *, signed: bool = False
) -> tuple[int, int]:
    """Decodifica MC sem instanciar ``BitReader`` para mapas de objetos."""
    value = 0
    shift = 0
    view = memoryview(data)
    for _ in range(10):
        if offset >= len(view):
            raise DwgFormatError("MC DWG truncado")
        byte = view[offset]
        offset += 1
        if not byte & 0x80:
            negative = signed and bool(byte & 0x40)
            if negative:
                byte &= 0x3F
            value |= byte << shift
            if negative:
                value = -value
            return value, offset
        value |= (byte & 0x7F) << shift
        shift += 7
    raise DwgFormatError("MC DWG excessivamente longo")
