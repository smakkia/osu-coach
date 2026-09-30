"""Parser for osu! stable replay files (.osr).

Format reference: https://osu.ppy.sh/wiki/en/Client/File_formats/osr_%28file_format%29
"""

import lzma
from dataclasses import dataclass, field
from pathlib import Path

from .binary import BinaryReader

# Key bits in replay frames. K1/K2 are always reported together with M1/M2.
M1, M2, K1, K2, SMOKE = 1, 2, 4, 8, 16

SEED_FRAME_TIME = -12345


@dataclass
class Frame:
    time: int  # absolute song time (ms)
    x: float
    y: float
    keys: int

    @property
    def left(self) -> bool:
        return bool(self.keys & M1)

    @property
    def right(self) -> bool:
        return bool(self.keys & M2)


@dataclass
class Replay:
    mode: int
    version: int
    beatmap_md5: str
    player: str
    replay_md5: str
    count_300: int
    count_100: int
    count_50: int
    count_geki: int
    count_katu: int
    count_miss: int
    score: int
    max_combo: int
    perfect: bool
    mods: int
    timestamp: int
    online_id: int
    frames: list[Frame] = field(default_factory=list)
    path: Path | None = None


def _parse_frames(raw: bytes) -> list[Frame]:
    text = lzma.decompress(raw, format=lzma.FORMAT_AUTO).decode("ascii", errors="replace")
    deltas = []
    for chunk in text.split(","):
        if not chunk:
            continue
        parts = chunk.split("|")
        if len(parts) != 4:
            continue
        w, x, y, z = parts
        deltas.append((int(w), float(x), float(y), int(float(z))))

    # Same cleanup danser applies before replaying: drop the RNG seed frame and
    # the bogus leading frame with a zero delta.
    deltas = [d for d in deltas if d[0] != SEED_FRAME_TIME]
    if deltas and deltas[0][0] == 0:
        deltas = deltas[1:]

    frames = []
    time = 0
    for w, x, y, z in deltas:
        time += w
        frames.append(Frame(time, x, y, z))
    return frames


def parse_replay(path: str | Path, frames: bool = True) -> Replay:
    """`frames=False` reads the header only (no LZMA decompression): much faster."""
    path = Path(path)
    r = BinaryReader(path.read_bytes())

    mode = r.byte()
    version = r.int()
    beatmap_md5 = r.string()
    player = r.string()
    replay_md5 = r.string()
    c300, c100, c50, geki, katu, miss = (r.short() for _ in range(6))
    score = r.int()
    max_combo = r.short()
    perfect = r.bool()
    mods = r.int()
    r.string()  # life bar graph
    timestamp = r.long()
    frames_raw = r.raw(r.int())
    online_id = r.long() if r.pos + 8 <= len(r.data) else 0

    return Replay(
        mode=mode, version=version, beatmap_md5=beatmap_md5, player=player,
        replay_md5=replay_md5, count_300=c300, count_100=c100, count_50=c50,
        count_geki=geki, count_katu=katu, count_miss=miss, score=score,
        max_combo=max_combo, perfect=perfect, mods=mods, timestamp=timestamp,
        online_id=online_id, frames=_parse_frames(frames_raw) if frames and frames_raw else [],
        path=path,
    )
