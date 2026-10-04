"""An MP3 song served inside an MP4 container, for the replay viewer.

Seeking an <audio> element in a plain MP3 is approximate in Chromium (WebView2): it guesses the byte to read from the
average bitrate, or from the 100-entry table of a VBR header, then says it is at the time asked for. On a VBR file, or
a CBR one whose frames are a little shorter than its bitrate says, the song then plays up to a second ahead of or
behind currentTime, more the later the seek. The same frames in an MP4, with a table of every frame's place in the
file, are found exactly. The MP3 data is not touched: the MP4 is a header built here plus the song's own bytes.
"""
import struct
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

_BITRATES = {1: (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),            # MPEG-1 layer III
             2: (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160)}                # MPEG-2 layer III
_RATES = {1: (44100, 48000, 32000), 2: (22050, 24000, 16000)}
_DECODER_DELAY = 529   # samples an MP3 decoder outputs before the first encoded one (as LAME and FFmpeg count it)


def _header(b: bytes, pos: int):
    """(MPEG version 1 or 2, frame length in bytes, sample rate, channels) of the MP3 frame starting at pos, or None
    (MPEG-2.5, free format and reserved values are not taken)."""
    if pos + 4 > len(b) or b[pos] != 0xFF:
        return None
    h = int.from_bytes(b[pos:pos + 4], "big")
    ver_bits, layer, br_i, sr_i = (h >> 19) & 3, (h >> 17) & 3, (h >> 12) & 15, (h >> 10) & 3
    if (h >> 21) & 0x7FF != 0x7FF or ver_bits not in (2, 3) or layer != 1 or br_i in (0, 15) or sr_i == 3:
        return None
    ver = 1 if ver_bits == 3 else 2
    rate = _RATES[ver][sr_i]
    length = (144 if ver == 1 else 72) * _BITRATES[ver][br_i] * 1000 // rate + ((h >> 9) & 1)
    return ver, length, rate, 1 if (h >> 6) & 3 == 3 else 2


def _box(kind: bytes, *parts: bytes) -> bytes:
    body = b"".join(parts)
    return struct.pack(">I", 8 + len(body)) + kind + body


def _full(kind: bytes, version_flags: int, *parts: bytes) -> bytes:
    return _box(kind, struct.pack(">I", version_flags), *parts)


def _descriptor(tag: int, body: bytes) -> bytes:
    return bytes([tag, 0x80, 0x80, 0x80, len(body)]) + body


_MATRIX = struct.pack(">9I", 0x10000, 0, 0, 0, 0x10000, 0, 0, 0, 0x40000000)


@dataclass
class Remux:
    """The MP4: head (ftyp, moov and the mdat header) followed by the song's bytes from data_start to data_end."""
    path: Path
    head: bytes
    data_start: int
    data_end: int

    @property
    def size(self) -> int:
        return len(self.head) + self.data_end - self.data_start

    def read(self, start: int, end: int):
        """The bytes from start to end (inclusive), in chunks."""
        if start < len(self.head):
            yield self.head[start:end + 1]
            start = len(self.head)
        if end >= start:
            with open(self.path, "rb") as f:
                f.seek(self.data_start + start - len(self.head))
                left = end - start + 1
                while left > 0 and (chunk := f.read(min(left, 1 << 16))):
                    yield chunk
                    left -= len(chunk)


def remux(path: Path) -> Remux | None:
    """The song at path (an MP3) as an MP4, or None when it isn't a plain MPEG-1/2 layer III stream of one sample
    rate (then it is served as it is)."""
    b = Path(path).read_bytes()
    pos = 0
    if b[:3] == b"ID3" and len(b) >= 10:
        pos = 10 + ((b[6] & 127) << 21 | (b[7] & 127) << 14 | (b[8] & 127) << 7 | (b[9] & 127)) + (10 if b[5] & 0x10 else 0)
    frames, first, delay = [], None, 0
    while pos < len(b):
        h = _header(b, pos)
        if h is None or pos + h[1] > len(b):
            if b[pos:pos + 3] == b"TAG" or b[pos:pos + 8] in (b"APETAGEX", b"LYRICSBE"):
                break
            # junk between frames: on to the next place two frames follow each other
            nxt = b.find(b"\xff", pos + 1)
            while nxt != -1 and not ((h2 := _header(b, nxt)) and _header(b, nxt + h2[1])):
                nxt = b.find(b"\xff", nxt + 1)
            if nxt == -1:
                break
            pos = nxt
            continue
        if first is None:
            first = h
            # a Xing/Info/VBRI header frame holds no sound: skipped, its LAME tag says how much encoder delay to drop
            side = 4 + (32 if h[3] == 2 else 17) if h[0] == 1 else 4 + (17 if h[3] == 2 else 9)
            tag = b[pos + side:pos + side + 4]
            if tag in (b"Xing", b"Info") or b[pos + 36:pos + 40] == b"VBRI":
                if tag in (b"Xing", b"Info"):
                    flags = int.from_bytes(b[pos + side + 4:pos + side + 8], "big")
                    lame = pos + side + 8 + 4 * bool(flags & 1) + 4 * bool(flags & 2) + 100 * bool(flags & 4) + 4 * bool(flags & 8)
                    if b[lame:lame + 4] in (b"LAME", b"Lavf", b"Lavc"):
                        delay = (int.from_bytes(b[lame + 21:lame + 24], "big") >> 12) + _DECODER_DELAY
                pos += h[1]
                continue
        elif h[0] != first[0] or h[2] != first[2]:
            return None
        frames.append((pos, h[1]))
        pos += h[1]
    if len(frames) < 2:
        return None
    ver, _, rate, channels = first
    per_frame = 1152 if ver == 1 else 576
    samples = per_frame * len(frames)
    seconds = samples / rate
    data_start, data_end = frames[0][0], frames[-1][0] + frames[-1][1]
    avg = round((data_end - data_start) * 8 / seconds)
    peak = max(n for _, n in frames) * 8 * rate // per_frame

    def build(mdat_offset: int) -> bytes:
        esds = _full(b"esds", 0, _descriptor(3, struct.pack(">HB", 1, 0) + _descriptor(
            4, struct.pack(">BB", 0x6B if ver == 1 else 0x69, 0x15) + b"\0\0\0" + struct.pack(">II", peak, avg))
            + _descriptor(6, b"\x02")))
        mp4a = _box(b"mp4a", b"\0" * 6, struct.pack(">H", 1), b"\0" * 8, struct.pack(">HHHHI", channels, 16, 0, 0, rate << 16), esds)
        stbl = _box(b"stbl",
                    _full(b"stsd", 0, struct.pack(">I", 1), mp4a),
                    _full(b"stts", 0, struct.pack(">III", 1, len(frames), per_frame)),
                    _full(b"stsc", 0, struct.pack(">IIII", 1, 1, 1, 1)),   # one frame per chunk: each has its place
                    _full(b"stsz", 0, struct.pack(">II", 0, len(frames)), b"".join(struct.pack(">I", n) for _, n in frames)),
                    _full(b"stco", 0, struct.pack(">I", len(frames)),
                          b"".join(struct.pack(">I", mdat_offset + p - data_start) for p, _ in frames)))
        minf = _box(b"minf", _full(b"smhd", 0, b"\0" * 4),
                    _box(b"dinf", _full(b"dref", 0, struct.pack(">I", 1), _full(b"url ", 1))), stbl)
        mdia = _box(b"mdia", _full(b"mdhd", 0, struct.pack(">IIIIHH", 0, 0, rate, samples, 0x55C4, 0)),
                    _full(b"hdlr", 0, b"\0" * 4, b"soun", b"\0" * 12, b"SoundHandler\0"), minf)
        # the edit list drops the encoder delay, as WebView2 does playing the MP3 itself; in whole frames (LAME's
        # usual 1105 samples: one frame, 1 ms more), as WebView2 drops a part of a frame twice when it plays from the
        # start
        skip = min(round(delay / per_frame), len(frames) - 1) * per_frame
        shown = samples - skip
        edts = _box(b"edts", _full(b"elst", 0, struct.pack(">IIiHH", 1, shown, skip, 1, 0))) if skip else b""
        trak = _box(b"trak", _full(b"tkhd", 7, struct.pack(">IIIII", 0, 0, 1, 0, shown), b"\0" * 8,
                                   struct.pack(">HHHH", 0, 0, 0x100, 0), _MATRIX, struct.pack(">II", 0, 0)), edts, mdia)
        moov = _box(b"moov", _full(b"mvhd", 0, struct.pack(">IIIIIH", 0, 0, rate, shown, 0x10000, 0x100), b"\0" * 10,
                                   _MATRIX, b"\0" * 24, struct.pack(">I", 2)), trak)
        ftyp = _box(b"ftyp", b"M4A ", struct.pack(">I", 0), b"M4A mp42isom")
        return ftyp + moov + struct.pack(">I", 8 + data_end - data_start) + b"mdat"

    head = build(0)
    head = build(len(head))   # the same length: now with the frames' real places
    return Remux(Path(path), head, data_start, data_end)


@lru_cache(maxsize=4)
def _song(path: str, stamp: tuple) -> Remux | None:
    return remux(Path(path))


def song(path: Path) -> Remux | None:
    """remux(path), kept for the last few songs (the viewer asks for parts of the song again at every seek) until
    the file changes."""
    st = Path(path).stat()
    return _song(str(path), (st.st_mtime_ns, st.st_size))
