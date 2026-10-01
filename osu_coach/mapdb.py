"""osu!.db: every beatmap the game knows, with what the recommender needs to pre-filter.

Per difficulty: mode, ranked status, length, main BPM, star ratings (with mod combinations),
online ids for links, and whether the player ever played it.
"""

from dataclasses import dataclass, field
from pathlib import Path

from .binary import BinaryReader

RANKED_STATUS = {0: "unknown", 1: "unsubmitted", 2: "pending", 3: "unused", 4: "ranked", 5: "approved",
                 6: "qualified", 7: "loved"}


@dataclass
class MapInfo:
    md5: str
    artist: str
    title: str
    creator: str
    version: str             # difficulty name
    path: str                # folder/file.osu, relative to Songs
    status: int
    circles: int
    sliders: int
    spinners: int
    ar: float
    cs: float
    od: float
    drain_s: int
    total_ms: int
    beatmap_id: int
    set_id: int
    mode: int
    unplayed: bool
    last_played: int         # .NET ticks
    stars: dict[int, float] = field(default_factory=dict)   # osu!standard star rating by mods (0, 64 = DT, 16 = HR...)
    bpm: float = 0.0         # main BPM: the uninherited timing point covering the most of the map
    local_offset: int = 0    # the player's local offset for the map, ms (+: hit objects later)

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title} [{self.version}]"

    @property
    def objects(self) -> int:
        return self.circles + self.sliders + self.spinners

    @property
    def ranked(self) -> bool:
        return self.status in (4, 5)


def main_bpm(points: list[tuple[float, float]], total_ms: int) -> float:
    """BPM of the uninherited timing point (beat length, offset) that lasts longest, up to the map's end."""
    points = sorted(points, key=lambda p: p[1])
    best, best_len = 0.0, -1.0
    for n, (beat, offset) in enumerate(points):
        end = points[n + 1][1] if n + 1 < len(points) else max(total_ms, offset)
        if end - offset > best_len and beat > 0:
            best, best_len = 60000 / beat, end - offset
    return best


def read_osu_db(path: Path) -> list[MapInfo]:
    r = BinaryReader(path.read_bytes())
    version = r.int()
    r.int()      # folder count
    r.bool()     # account unlocked
    r.long()     # unlock date
    r.string()   # player name
    count = r.int()

    def star_ratings() -> dict[int, float]:
        out = {}
        for _ in range(r.int()):
            r.skip(1)                # 0x08
            mods = r.int()
            kind = r.byte()          # 0x0d double, 0x0c float (2025+ databases)
            out[mods] = r.double() if kind == 0x0D else r.float()
        return out

    maps = []
    for _ in range(count):
        if version < 20191106:
            r.int()  # entry size
        artist = r.string()
        r.string()  # artist unicode
        title = r.string()
        r.string()  # title unicode
        creator = r.string()
        diff_name = r.string()
        r.string()  # audio file
        md5 = r.string()
        osu_file = r.string()
        status = r.byte()
        circles, sliders, spinners = r.short(), r.short(), r.short()
        r.long()    # last modified
        if version >= 20140609:
            ar, cs, hp, od = r.float(), r.float(), r.float(), r.float()
        else:
            ar, cs, hp, od = r.byte(), r.byte(), r.byte(), r.byte()
        r.double()  # slider velocity
        stars = {}
        if version >= 20140609:
            stars = star_ratings()   # osu!standard
            for _ in range(3):       # taiko, catch, mania
                star_ratings()
        drain, total = r.int(), r.int()
        r.int()     # preview time
        points = []
        for _ in range(r.int()):  # timing points: beat length, offset, uninherited
            beat, offset, uninherited = r.double(), r.double(), r.bool()
            if uninherited:
                points.append((beat, offset))
        beatmap_id, set_id = r.int(), r.int()
        r.int()     # thread id
        r.skip(4)   # grades
        local_offset = r.short()
        r.float()   # stack leniency
        mode = r.byte()
        r.string()  # source
        r.string()  # tags
        r.short()   # online offset
        r.string()  # title font
        unplayed = r.bool()
        last_played = r.long()
        r.bool()    # osz2
        folder = r.string()
        r.long()    # last checked
        r.skip(5)   # ignore sound/skin, disable storyboard/video, visual override
        if version < 20140609:
            r.short()
        r.int()     # last modification
        r.byte()    # mania scroll speed
        if md5:
            maps.append(MapInfo(md5, artist, title, creator, diff_name, f"{folder}/{osu_file}", status,
                                circles, sliders, spinners, float(ar), float(cs), float(od), drain, total,
                                beatmap_id, set_id, mode, unplayed, last_played, stars, main_bpm(points, total),
                                local_offset))
    return maps
