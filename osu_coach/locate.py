"""Finds the osu! install, its replays, and the .osu file for a beatmap MD5."""

import hashlib
import json
import os
from pathlib import Path


CACHE_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "osu-coach"


def default_osu_dir() -> Path | None:
    candidates = []
    if os.environ.get("OSU_DIR"):
        candidates.append(Path(os.environ["OSU_DIR"]))
    if os.environ.get("LOCALAPPDATA"):
        candidates.append(Path(os.environ["LOCALAPPDATA"]) / "osu!")
    for c in candidates:
        if (c / "osu!.exe").exists() or (c / "osu!.db").exists():
            return c
    return None


def songs_dir(osu_dir: Path) -> Path:
    """Songs folder, honouring a custom BeatmapDirectory in osu!.<user>.cfg."""
    for cfg in osu_dir.glob("osu!.*.cfg"):
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "BeatmapDirectory" and value.strip():
                p = Path(value.strip())
                return p if p.is_absolute() else osu_dir / p
    return osu_dir / "Songs"


def replays_dir(osu_dir: Path) -> Path:
    return osu_dir / "Data" / "r"


def latest_replay(osu_dir: Path) -> Path | None:
    replays = list(replays_dir(osu_dir).glob("*.osr"))
    return max(replays, key=lambda p: p.stat().st_mtime) if replays else None


def _read_osu_db(path: Path) -> dict[str, str]:
    """MD5 -> 'folder/file.osu' from osu!.db."""
    from .mapdb import read_osu_db
    return {m.md5: m.path for m in read_osu_db(path)}


def _osu_files(songs: Path):
    """(path relative to Songs, mtime, size) of every .osu file in it. os.scandir lists the folders with each
    file's size and time (on Windows), where a stat per file took ten times as long."""
    stack = [""]
    while stack:
        rel = stack.pop()
        try:
            it = os.scandir(songs / rel if rel else songs)
        except OSError:
            continue
        with it:
            for e in it:
                sub = os.path.join(rel, e.name) if rel else e.name
                if e.is_dir(follow_symlinks=False):
                    stack.append(sub)
                elif e.name.lower().endswith(".osu"):
                    st = e.stat()
                    yield sub, st.st_mtime, st.st_size


class BeatmapIndex:
    """Resolves beatmap MD5s to .osu paths, via osu!.db with a hashing fallback."""

    def __init__(self, osu_dir: Path):
        self.osu_dir = osu_dir
        self.songs = songs_dir(osu_dir)
        self._db: dict[str, str] | None = None
        self._scan_cache_path = CACHE_DIR / "beatmap_hashes.json"
        self._scan: dict[str, dict] | None = None  # rel path -> {mtime, size, md5}
        self._by_md5: dict[str, str] | None = None

    def use_db(self, paths: dict[str, str]):
        """MD5 -> 'folder/file.osu' of osu!.db as the caller read it (the window reads osu!.db for itself, and again
        when osu! saves it): not read a second time here, and up to date."""
        self._db = paths

    def _from_db(self, md5: str) -> Path | None:
        if self._db is None:
            try:
                self._db = _read_osu_db(self.osu_dir / "osu!.db")
            except Exception:  # unknown db version or corrupt file: fall back to hashing
                self._db = {}
        rel = self._db.get(md5)
        if rel:
            p = self.songs / rel
            if p.exists():
                return p
        return None

    def _rescan(self) -> None:
        """Hash .osu files not seen before (osu!.db is only written when the game exits), forget deleted ones."""
        if self._scan is None:
            try:
                self._scan = json.loads(self._scan_cache_path.read_text())
            except (OSError, ValueError):
                self._scan = {}
        if not self.songs.is_dir():   # Songs out of reach (a drive unplugged?): keep what is known
            return
        seen, changed = set(), False
        for rel, mtime, size in _osu_files(self.songs):
            seen.add(rel)
            cached = self._scan.get(rel)
            if cached and cached["mtime"] == mtime and cached["size"] == size:
                continue
            try:
                md5 = hashlib.md5((self.songs / rel).read_bytes()).hexdigest()
            except OSError:
                continue
            self._scan[rel] = {"mtime": mtime, "size": size, "md5": md5}
            changed = True
        for rel in set(self._scan) - seen:
            del self._scan[rel]
            changed = True
        if changed:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            self._scan_cache_path.write_text(json.dumps(self._scan))

    def find(self, md5: str) -> Path | None:
        found = self._from_db(md5)
        if found:
            return found
        if self._by_md5 is None:  # scan Songs once per index, not once per missing map
            self._rescan()
            self._by_md5 = {entry["md5"]: rel for rel, entry in self._scan.items()}
        rel = self._by_md5.get(md5)
        return self.songs / rel if rel else None
