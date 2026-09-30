"""Little-endian reader for osu!'s binary formats (.osr, osu!.db)."""

import struct


class BinaryReader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def _unpack(self, fmt: str, size: int):
        value = struct.unpack_from(fmt, self.data, self.pos)[0]
        self.pos += size
        return value

    def byte(self) -> int:
        return self._unpack("<B", 1)

    def bool(self) -> bool:
        return self.byte() != 0

    def short(self) -> int:
        return self._unpack("<h", 2)

    def int(self) -> int:
        return self._unpack("<i", 4)

    def long(self) -> int:
        return self._unpack("<q", 8)

    def float(self) -> float:
        return self._unpack("<f", 4)

    def double(self) -> float:
        return self._unpack("<d", 8)

    def uleb128(self) -> int:
        result = shift = 0
        while True:
            b = self.byte()
            result |= (b & 0x7F) << shift
            if not b & 0x80:
                return result
            shift += 7

    def string(self) -> str:
        marker = self.byte()
        if marker == 0x00:
            return ""
        if marker != 0x0B:
            raise ValueError(f"invalid string marker 0x{marker:02x} at offset {self.pos - 1}")
        length = self.uleb128()
        value = self.data[self.pos:self.pos + length].decode("utf-8", errors="replace")
        self.pos += length
        return value

    def raw(self, length: int) -> bytes:
        value = self.data[self.pos:self.pos + length]
        self.pos += length
        return value

    def skip(self, length: int):
        self.pos += length
