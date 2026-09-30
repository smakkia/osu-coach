"""Judges many replays in parallel and turns them into samples."""

from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from .advice import Sample, samples_from_play
from .beatmap import parse_beatmap
from .features import RUN_PATTERNS, extract
from .judge import judge
from .keys import TapStats, tap_stats
from .mods import Mods, clock_rate
from .replay import parse_replay

PARALLEL_MIN = 8   # fewer plays than this aren't worth starting worker processes for


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
    return samples, taps


def _job(args: tuple[str, str]):
    return play_samples(*args)


def collect(jobs: list[tuple[str, str]]) -> list[tuple[list[Sample], TapStats] | None]:
    """`jobs`: (replay path, map path) pairs; results in the same order."""
    if len(jobs) < PARALLEL_MIN:
        return [play_samples(*j) for j in jobs]
    with ProcessPoolExecutor() as pool:
        return list(pool.map(_job, jobs, chunksize=4))


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
