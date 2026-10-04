"""Judges many replays in parallel and turns them into samples.

Judging a play takes a fraction of a second and the profile reads hundreds of plays every time it is computed: each
judged play is kept on disk (PLAYS_DB, compressed) and judged again only when its replay or map file changes, or
when the code that judges plays does (_pipeline_signature).
"""

import functools
import hashlib
import os
import pickle
import sqlite3
import sys
import time
import zlib
from contextlib import closing
from pathlib import Path

from .advice import Sample, samples_from_play
from .beatmap import parse_beatmap
from .cachedb import CHUNK
from .curves import PathLength
from .features import RUN_PATTERNS, extract
from .judge import judge
from .keys import TapStats, tap_stats
from .locate import CACHE_DIR
from .mods import Mods, clock_rate
from .replay import parse_replay
from .workers import imap

PLAYS_DB = CACHE_DIR / "plays.db"
PLAYS_MAX = 1000             # plays kept (~130 KB each), the least recently used dropped first...
PLAYS_RECENT_S = 3600        # ...but never the ones used in the last hour (a profile reads three windows of plays)
PLAYS_STALE_S = 30 * 86400   # a play unused for this long goes in any case
SAVE_EVERY = 25
# the modules a judged play depends on: a change to any of them judges every play again
PIPELINE = ("replay", "beatmap", "curves", "difficulty", "mods", "judge", "features", "advice", "keys", "collect")


def play_samples(replay_path: str, map_path: str) -> tuple[list[Sample], TapStats] | None:
    """One play: judged, with features, as samples (play id 0) plus its key statistics."""
    try:
        replay, beatmap = parse_replay(Path(replay_path)), parse_beatmap(Path(map_path))
        results, diff = judge(replay, beatmap)
    except Exception:  # corrupt files exist in the wild
        return None
    rate = clock_rate(replay.mods)
    feats = extract(results, diff, rate, beatmap)
    samples = samples_from_play(feats, diff, rate, hidden=bool(replay.mods & Mods.Hidden))
    taps = tap_stats(replay.frames, results, {f.r.obj.index for f in feats if f.pattern in RUN_PATTERNS})
    slim(samples)
    return samples, taps


def slim(samples: list[Sample]):
    """Drop what only a single play's viewer and explanations use: the slider polylines (their length stays), score
    points and judged points. Half the memory of a judged play, and of what a worker sends back."""
    for s in samples:
        o = s.r.obj
        if o.path is not None:
            o.path = PathLength(o.path.length)
        o.score_points = []
        s.r.points = []


def _job(args: tuple[str, str]):
    return play_samples(*args)


def combo_breaks_job(args: tuple[str, str]) -> dict | None:
    """A replay's slider breaks and dropped slider ends (analysis.combo_breaks), judged in a worker process."""
    from .analysis import combo_breaks
    replay_path, map_path = args
    try:
        results, _ = judge(parse_replay(Path(replay_path)), parse_beatmap(Path(map_path)))
    except Exception:   # corrupt files exist in the wild
        return None
    return combo_breaks(results)


# --- judged plays, kept -------------------------------------------------------------------------------------------

@functools.cache
def _pipeline_signature() -> str:
    """The code the cached plays were judged with: the source of the PIPELINE modules; in the packaged app, where
    there is no source, its version and executable (which holds the code, so a rebuild or an update changes it)."""
    from . import __version__
    if getattr(sys, "frozen", False):
        st = Path(sys.executable).stat()
        return f"{__version__}:{st.st_size}:{st.st_mtime_ns}"
    h = hashlib.md5()
    for name in PIPELINE:
        h.update(Path(__file__).with_name(f"{name}.py").read_bytes())
    return h.hexdigest()


def _stamp(replay_path: str, map_path: str) -> str | None:
    """The replay and the map as files: a kept play is good while both are the same. None: one is missing."""
    try:
        r, m = os.stat(replay_path), os.stat(map_path)
    except OSError:
        return None
    return f"{r.st_mtime_ns}:{r.st_size}:{m.st_mtime_ns}:{m.st_size}"


def _packed_job(args: tuple[str, str]) -> bytes:
    """One play judged in a worker and packed there, as the cache keeps it (empty: it couldn't be judged)."""
    out = play_samples(*args)
    return b"" if out is None else zlib.compress(pickle.dumps(out, protocol=pickle.HIGHEST_PROTOCOL), 6)


def _unpacked(packed: bytes):
    return pickle.loads(zlib.decompress(packed)) if packed else None


class PlayCache:
    """The judged plays, by replay file: when its files were judged (stamp), when last used, and the play packed."""

    def __init__(self, path: Path | None = None):
        path = path or PLAYS_DB
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS plays (replay TEXT PRIMARY KEY, stamp TEXT, used REAL, value BLOB)")
            self.db.execute("CREATE INDEX IF NOT EXISTS plays_used ON plays (used)")
            row = self.db.execute("SELECT value FROM meta WHERE name = 'code'").fetchone()
            if row is None or row[0] != _pipeline_signature():
                self.db.execute("DELETE FROM plays")
                self.db.execute("INSERT OR REPLACE INTO meta VALUES ('code', ?)", (_pipeline_signature(),))

    def close(self):
        self.db.close()

    def get(self, stamps: dict[str, str]) -> dict[str, bytes]:
        """The packed plays of these replays (path -> stamp) that were judged from the same files; marked as used."""
        keys, out = list(stamps), {}
        for i in range(0, len(keys), CHUNK):
            part = keys[i:i + CHUNK]
            sql = f"SELECT replay, stamp, value FROM plays WHERE replay IN ({','.join('?' * len(part))})"
            out.update((k, v) for k, stamp, v in self.db.execute(sql, part) if stamp == stamps[k])
        now = time.time()
        with self.db:
            self.db.executemany("UPDATE plays SET used = ? WHERE replay = ?", ((now, k) for k in out))
        return out

    def put(self, plays: dict[str, tuple[str, bytes]]):
        """Keep plays: replay path -> (stamp, packed play)."""
        now = time.time()
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO plays VALUES (?, ?, ?, ?)",
                                ((k, stamp, now, packed) for k, (stamp, packed) in plays.items()))

    def trim(self):
        """Drop the plays unused for PLAYS_STALE_S, then the least recently used beyond PLAYS_MAX (not the ones used in
        the last PLAYS_RECENT_S)."""
        now = time.time()
        with self.db:
            self.db.execute("DELETE FROM plays WHERE used < ?", (now - PLAYS_STALE_S,))
            self.db.execute("DELETE FROM plays WHERE replay IN (SELECT replay FROM plays WHERE used < ? ORDER BY used "
                            "LIMIT max(0, (SELECT COUNT(*) FROM plays) - ?))", (now - PLAYS_RECENT_S, PLAYS_MAX))


def _open_cache() -> PlayCache:
    try:
        return PlayCache()
    except sqlite3.DatabaseError:   # a damaged file: start again
        for suffix in ("", "-wal", "-shm"):
            Path(f"{PLAYS_DB}{suffix}").unlink(missing_ok=True)
        return PlayCache()


def judged(jobs: list[tuple[str, str]]):
    """(samples, key statistics) of each (replay path, map path), in order; None for a play that can't be judged.
    Kept plays come from the cache, the others are judged by the workers and kept. Closing the generator early
    (contextlib.closing) keeps what was judged so far."""
    if not jobs:
        return
    stamps = [_stamp(*job) for job in jobs]
    with closing(_open_cache()) as cache:
        kept = cache.get({job[0]: stamp for job, stamp in zip(jobs, stamps) if stamp})
        todo = [job for job, stamp in zip(jobs, stamps) if stamp and job[0] not in kept]
        new = {}
        try:
            with closing(imap(_packed_job, todo)) as computed:
                for job, stamp in zip(jobs, stamps):
                    if stamp is None:
                        yield None
                        continue
                    packed = kept.get(job[0])
                    if packed is None:
                        packed = next(computed)
                        new[job[0]] = (stamp, packed)
                        if len(new) >= SAVE_EVERY:
                            cache.put(new)
                            new = {}
                    yield _unpacked(packed)
        finally:
            cache.put(new)
            cache.trim()


def collect(jobs: list[tuple[str, str]]) -> list[tuple[list[Sample], TapStats] | None]:
    """`jobs`: (replay path, map path) pairs; results in the same order."""
    with closing(judged(jobs)) as results:
        return list(results)


def map_samples(map_path: str, mods: int = 0) -> list[Sample] | None:
    """A map nobody played yet, as samples: every object "hit", so only the map's own features
    (patterns, spacing, rhythm, fingering, reading) are meaningful. Used to predict how a map
    would go for a player."""
    from .judge import ObjectResult
    try:
        beatmap = parse_beatmap(Path(map_path))
        diff = beatmap.apply_mods(mods)
    except Exception:
        return None
    results = [ObjectResult(o, result=300, head_result=300 if o.kind == "slider" else None) for o in beatmap.objects]
    rate = clock_rate(mods)
    try:
        feats = extract(results, diff, rate, beatmap)
    except Exception:
        return None
    return samples_from_play(feats, diff, rate, hidden=bool(mods & Mods.Hidden))
