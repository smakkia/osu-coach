"""An on-disk index of the local replays: who played what, with which mods, when, at which AR.

Choosing plays (the last 100, the last 100 below AR 9...) used to mean opening every
replay and its map. The index keeps the header of each replay and the difficulty of
each map, and only reads files that are new or changed since the last run.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path

from .advice import effective_ar
from .difficulty import Difficulty
from .locate import CACHE_DIR, BeatmapIndex, replays_dir
from .mods import clock_rate
from .replay import parse_replay

INDEX_PATH = CACHE_DIR / "replay_index.json"
UNSUPPORTED_MODS = 128 | 8192   # Relax, Autopilot
TICKS_AT_UNIX_EPOCH = 621355968000000000


@dataclass
class ReplayEntry:
    name: str             # file name in the replays folder
    mtime: float
    size: int
    mode: int
    player: str
    mods: int
    md5: str              # beatmap MD5
    time: float           # when it was played, unix seconds
    ar: float | None = None   # effective AR (mods and rate applied); None if the map isn't found

    @property
    def usable(self) -> bool:
        """osu!standard, no Relax/Autopilot, map available."""
        return self.mode == 0 and not self.mods & UNSUPPORTED_MODS and self.ar is not None


def read_difficulty(path: Path) -> tuple[float, float, float] | None:
    """(CS, AR, OD) from the [Difficulty] section only; old maps without AR use OD."""
    cs = od = ar = None
    section = ""
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if line.startswith("["):
                if section == "[Difficulty]":
                    break
                section = line
                continue
            if section != "[Difficulty]" or ":" not in line:
                continue
            key, value = (x.strip() for x in line.split(":", 1))
            try:
                if key == "CircleSize":
                    cs = float(value)
                elif key == "OverallDifficulty":
                    od = float(value)
                elif key == "ApproachRate":
                    ar = float(value)
            except ValueError:
                continue
    if cs is None or od is None:
        return None
    return cs, (ar if ar is not None else od), od


class ReplayIndex:
    def __init__(self, osu_dir: Path, maps: BeatmapIndex):
        self.folder = replays_dir(osu_dir)
        self.maps = maps
        self._difficulty: dict[str, tuple[float, float, float] | None] = {}  # map md5 -> (CS, AR, OD)
        self.entries: dict[str, ReplayEntry] = {}
        self._songs_mtime: float | None = None
        self._load()
        # maps missing last time are only looked for again when the Songs folder changed
        songs_mtime = maps.songs.stat().st_mtime if maps.songs.exists() else None
        self._retry_missing = songs_mtime != self._songs_mtime
        self._songs_mtime = songs_mtime
        self._update()

    def _load(self):
        try:
            data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        self.entries = {e["name"]: ReplayEntry(**e) for e in data.get("replays", [])}
        self._difficulty = {k: tuple(v) if v else None for k, v in data.get("maps", {}).items()}
        self._songs_mtime = data.get("songs_mtime")

    def _map_difficulty(self, md5: str) -> tuple[float, float, float] | None:
        if md5 not in self._difficulty or (self._difficulty[md5] is None and self._retry_missing):
            path = self.maps.find(md5)
            self._difficulty[md5] = read_difficulty(path) if path else None
        return self._difficulty[md5]

    def _update(self):
        seen, changed = set(), False
        try:   # os.scandir: each replay's size and time come with the folder's listing, no stat per file
            files = [e for e in os.scandir(self.folder) if e.name.lower().endswith(".osr") and e.is_file()]
        except OSError:
            files = []
        for e in files:
            seen.add(e.name)
            st = e.stat()
            old = self.entries.get(e.name)
            if (old and old.mtime == st.st_mtime and old.size == st.st_size
                    and (old.ar is not None or old.mode != 0 or not self._retry_missing)):
                continue
            if old and old.mtime == st.st_mtime and old.size == st.st_size:
                entry = old  # known replay whose map was missing: try the map again
            else:
                try:
                    r = parse_replay(Path(e.path), frames=False)
                except Exception:
                    continue
                entry = ReplayEntry(e.name, st.st_mtime, st.st_size, r.mode, r.player, r.mods, r.beatmap_md5,
                                    (r.timestamp - TICKS_AT_UNIX_EPOCH) / 1e7)
            if entry.mode == 0:
                d = self._map_difficulty(entry.md5)
                if d is not None:
                    cs, ar, od = d
                    entry.ar = effective_ar(Difficulty.from_map(cs, ar, od, entry.mods), clock_rate(entry.mods))
            self.entries[e.name] = entry
            changed = True
        for name in set(self.entries) - seen:  # deleted replays
            del self.entries[name]
            changed = True
        if changed or self._retry_missing:
            self.save()

    def save(self):
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {"replays": [asdict(e) for e in self.entries.values()],
                "maps": {k: list(v) if v else None for k, v in self._difficulty.items()},
                "songs_mtime": self._songs_mtime}
        INDEX_PATH.write_text(json.dumps(data), encoding="utf-8")

    @property
    def owner(self) -> str | None:
        """The player with the most replays: whose osu! this is (others' replays can be in the folder)."""
        counts: dict[str, int] = {}
        for e in self.entries.values():
            counts[e.player] = counts.get(e.player, 0) + 1
        return max(counts, key=counts.get) if counts else None

    def recent(self, player: str | None = None, select=None) -> list[ReplayEntry]:
        """Usable replays by `player` (default: the owner), most recent first, passing `select(entry)`."""
        player = player or self.owner
        out = [e for e in self.entries.values() if e.usable
               and (player is None or e.player.lower() == player.lower())
               and (select is None or select(e))]
        return sorted(out, key=lambda e: e.time, reverse=True)

    def path(self, entry: ReplayEntry) -> Path:
        return self.folder / entry.name
