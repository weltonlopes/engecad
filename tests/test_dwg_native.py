from __future__ import annotations

import struct

from engecad.dwg.bitstream import BitReader, modular_char
from engecad.dwg.container import decompress_r2004


def test_ac18_decompresses_literals_without_external_library():
    assert decompress_r2004(b"\x01ABCD", 4) == b"ABCD"


def test_ac18_decompresses_overlapping_copy():
    # Four literal bytes, then opcode 0x21 (three copied bytes) with a
    # distance of three, followed by a zero-literal terminator opcode.
    stream = b"\x01ABCD\x21\x0c\x00\x11"
    assert decompress_r2004(stream, 7) == b"ABCDABC"


def test_ac18_truncates_a_final_copy_to_declared_size():
    # Some AC18 writers leave a final copy instruction in the padding after
    # the declared output.  The declared size, not that padding instruction,
    # is authoritative.
    stream = b"\x01ABCD\x21\x0c\x00\x11"
    assert decompress_r2004(stream, 6) == b"ABCDAB"


def test_modular_char_uses_the_final_byte_sign_flag():
    # 0x40 is the negative sign marker, not an extra magnitude bit.
    assert modular_char(bytes([0x41]), signed=True) == (-1, 1)
    assert modular_char(bytes([0x81, 0x41]), signed=True) == (-0x81, 2)


def test_bitreader_reads_unaligned_little_endian_raw_values():
    # Consume the two high bits, then read the remaining bytes unaligned.
    reader = BitReader(bytes([0x00, 0x34, 0x12, 0x00]))
    assert reader.read_bits(2) == 0
    assert reader.read_raw_u16() == 0xD000


def test_bitdouble_delta_preserves_default_or_patches_halves():
    default = 123.5
    # DD code 00 means the default value verbatim.
    reader = BitReader(bytes([0x00]))
    assert reader.read_delta_double(default) == default

    # Build code 11 followed by an RD, exercising the unaligned code prefix.
    value = 456.25
    bits = "11" + "".join(f"{byte:08b}" for byte in struct.pack("<d", value))
    encoded = bytes(
        int(bits[index : index + 8].ljust(8, "0"), 2)
        for index in range(0, len(bits), 8)
    )
    reader = BitReader(encoded)
    assert reader.read_delta_double(default) == value
