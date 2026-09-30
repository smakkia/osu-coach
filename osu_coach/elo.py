"""A rating per skillset, updated play after play as in chess, and its value day by day (the Improvement page).

Every play is a series of challenges: a stream to finish, a jump to hit, a slider to hold, a circle to hit for a
300... Each challenge has a difficulty measured on the map alone (stream BPM, jump speed in radii per second, the
300 window...), turned into a fixed rating: 1200 is a challenge that the reference player (the author, a fairly
balanced player) passes as often as they pass those challenges on average (87% of streams finished, 97% of jumps
hit...), and every CALIBRATION[skill][1] points multiply the difficulty by e. A player's rating in a skillset is then the level
of challenge they pass that often: after every play it moves by K per challenge times (passed - expected), as in Elo.
The scales are fixed, not fitted on each player: 1300 in streams means the same streams for everyone.
"""

import json
import math
import time
from collections import defaultdict
from pathlib import Path

import numpy as np

from .locate import CACHE_DIR

PLAYS_PATH = CACHE_DIR / "elo_plays.json"   # the challenges of every play judged so far, by replay file
PLAYS_VERSION = "1"
DAYS = 90
WARMUP_DAYS = 30               # the plays of the month before the 90 days give the starting rating
MIN_WARMUP_PLAYS = 10
REFERENCE = 1200.0
MAX_STEP = 60.0                # a single play moves a rating by at most this much

# skill: (label, pass rate at equal ratings: the reference player's own, K per challenge)
SKILLS = {
    "streams": ("Streams", 0.868, 6.0),
    "alt": ("Alt", 0.647, 6.0),
    "finger": ("Finger control", 0.953, 1.5),
    "jumps": ("Jumps", 0.967, 1.0),
    "flow": ("Flow aim", 0.979, 0.8),
    "sliders": ("Sliders", 0.938, 1.0),
    "stamina": ("Stamina", 0.582, 16.0),
    "high_ar": ("High AR", 0.968, 0.6),
    "reading": ("Reading", 0.919, 0.8),
    "accuracy": ("Accuracy", 0.911, 0.25),
}
# skill: (the difficulty the reference player passes at that rate, rating points per e-fold of difficulty), fitted
# once on the reference player's plays of the last 90 days on 2026-09-30 (tools/calibrate-elo.py) and kept fixed
CALIBRATION = {
    "streams": (216.002, 953.8),       # stream BPM x (length / 16)^0.15 x (1 + 0.1 spacing), 9+ notes
    "alt": (215.216, 842.7),           # alt BPM x (1 + 0.15 spacing)
    "finger": (178.295, 164.6),        # burst BPM x (notes / 4)^0.1, 2-8 notes
    "jumps": (39.4884, 304.1),         # radii per second, 3+ radii
    "flow": (15.6837, 276.5),          # radii per second, 0.3-3 radii
    "sliders": (21.2904, 337.9),       # slider ball speed, radii per second
    "stamina": (1043.15, 400.0),       # notes in the play
    "high_ar": (0.00198746, 799.1),    # 1 / approach time in ms, AR 9+
    "reading": (6.6234, 43.7),         # notes on screen + 1, below AR 9
    "accuracy": (0.040681, 266.5),     # 1 / the 300 window in real ms
}
# where the reference player stands in each skillset against 1200 (not balanced after all: weaker at alt and
# streams, stronger at jumps and finger control, from their bad habits and Skills page)
STANDING = {"alt": -100.0, "streams": -50.0, "jumps": 50.0, "finger": 50.0}


# --- challenges -----------------------------------------------------------------------------------------------------

def challenges(samples, rate: float, hit300: float) -> dict[str, list[tuple[float, bool]]]:
    """(difficulty, passed) per skillset for one play's samples; `hit300` is the 300 window in map ms."""
    from .beatmap import CIRCLE, SLIDER
    from .features import FIRST, STREAM_FAMILY
    from .maptypes import _run_kind
    out: dict[str, list] = defaultdict(list)
    runs: dict[tuple, list] = defaultdict(list)
    for s in samples:
        if s.f.pattern in STREAM_FAMILY or _run_kind(s.f) == "alt":
            runs[s.run_id].append(s)
    for run in runs.values():
        f, cleared = run[0].f, not any(x.missed for x in run)
        if not f.bpm:
            continue
        if _run_kind(f) == "alt":
            out["alt"].append((f.bpm * (1 + 0.15 * f.run_spacing), cleared))
        elif f.pattern in STREAM_FAMILY and f.run_length >= 9:
            out["streams"].append((f.bpm * (f.run_length / 16) ** 0.15 * (1 + 0.1 * f.run_spacing), cleared))
        elif f.pattern in STREAM_FAMILY and 2 <= f.run_length <= 8:
            out["finger"].append((f.bpm * (f.run_length / 4) ** 0.1, cleared))
    for s in samples:
        f, r = s.f, s.r
        if f.pattern == FIRST or r.obj.kind not in (CIRCLE, SLIDER) or f.gap_ms <= 0:
            continue
        speed = f.distance_radii / f.gap_ms * 1000
        if f.distance_radii >= 3 and f.gap_ms <= 300:
            out["jumps"].append((speed, not s.missed))
        elif 0.3 <= f.distance_radii < 3 and f.gap_ms <= 200:
            out["flow"].append((speed, not (s.missed and r.miss_reason == "aim")))
        if r.obj.kind == SLIDER and r.obj.span_duration > 0:
            slider = r.obj.path.length / f._radius / (r.obj.span_duration / s.rate / 1000)
            out["sliders"].append((slider, not s.missed and r.slider_break_kind is None))
        if s.ar >= 9:
            preempt = 1200 - 150 * (s.ar - 5) if s.ar >= 5 else 1200 + 120 * (5 - s.ar)
            out["high_ar"].append((1 / preempt, not s.missed))
        else:
            out["reading"].append((f.visible + 1, not s.missed))
        if s.acc_eligible:
            out["accuracy"].append((rate / hit300, s.r.result == 300))
    # stamina: the whole play, held if the timing of its last third is within 15% of its first third
    timed = [abs(s.error) for s in samples if s.acc_eligible and s.error is not None]
    if len(timed) >= 300:
        third = len(timed) // 3
        first, last = np.mean(timed[:third]), np.mean(timed[-third:])
        out["stamina"].append((len(samples), bool(last <= 1.15 * max(first, 1.0))))
    return out


def _binned(chs: list[tuple[float, bool]]) -> list[list]:
    """Challenges by difficulty (log, 1% steps): [log difficulty, passed, failed]."""
    bins: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for d, ok in chs:
        if d > 0:
            bins[round(math.log(d), 2)][0 if ok else 1] += 1
    return [[k, w, l] for k, (w, l) in sorted(bins.items())]


def play_job(args):
    """One play's challenges, binned; None when it can't be read."""
    replay_path, map_path = args
    from .advice import samples_from_play
    from .beatmap import parse_beatmap
    from .features import extract
    from .judge import judge
    from .mods import Mods, clock_rate
    from .replay import parse_replay
    try:
        replay, beatmap = parse_replay(Path(replay_path)), parse_beatmap(Path(map_path))
        results, diff = judge(replay, beatmap)
        rate = clock_rate(replay.mods)
        feats = extract(results, diff, rate, beatmap)
        samples = samples_from_play(feats, diff, rate, hidden=bool(replay.mods & Mods.Hidden))
        return {k: _binned(v) for k, v in challenges(samples, rate, diff.hit300).items()}
    except Exception:
        return None


# --- ratings ----------------------------------------------------------------------------------------------------------

def item_rating(skill: str, log_d: float) -> float:
    """A challenge's rating: the reference player's challenge at their standing, and per_e points per e-fold."""
    d0, per_e = CALIBRATION[skill]
    return REFERENCE + STANDING.get(skill, 0.0) + per_e * (log_d - math.log(d0))


def _expected(skill: str, rating: float, item: float) -> float:
    """Chance to pass a challenge of rating `item`: the skill's comfortable rate when the ratings are equal."""
    target = SKILLS[skill][1]
    return 1 / (1 + 10 ** ((item - rating) / 400 - math.log10(target / (1 - target))))


def _score(skill: str, rating: float, bins) -> tuple[float, int]:
    """Passed minus expected over some challenges, and how many there were."""
    diff, n = 0.0, 0
    for log_d, w, l in bins:
        e = _expected(skill, rating, item_rating(skill, log_d))
        diff += w - (w + l) * e
        n += w + l
    return diff, n


def fit_rating(skill: str, plays: list[dict]) -> float | None:
    """The rating that expects exactly the challenges passed in these plays (the maximum likelihood one)."""
    bins = [b for p in plays for b in p["skills"].get(skill, [])]
    if sum(w + l for _, w, l in bins) < 20:
        return None
    lo, hi = 0.0, 3000.0
    for _ in range(50):
        mid = (lo + hi) / 2
        if _score(skill, mid, bins)[0] > 0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def history(plays: list[dict], now: float | None = None, days: int = DAYS) -> dict:
    """Each skillset's rating at the end of every day of the last `days`: started from the warm-up month (or the
    first plays), then moved play after play."""
    now = now or time.time()
    start = now - days * 86400
    plays = sorted(plays, key=lambda p: p["time"])
    warm = [p for p in plays if start - WARMUP_DAYS * 86400 <= p["time"] < start]
    window = [p for p in plays if p["time"] >= start]
    if len(warm) < MIN_WARMUP_PLAYS:
        warm = warm + window[:MIN_WARMUP_PLAYS - len(warm)]
    dates = [time.strftime("%Y-%m-%d", time.localtime(now - (days - 1 - i) * 86400)) for i in range(days)]
    out = {"dates": dates, "skills": {}, "plays_per_day": [0] * days}
    day_of = {d: i for i, d in enumerate(dates)}
    for p in window:
        i = day_of.get(time.strftime("%Y-%m-%d", time.localtime(p["time"])))
        if i is not None:
            out["plays_per_day"][i] += 1
    for skill, (label, _, k) in SKILLS.items():
        rating = fit_rating(skill, warm)
        series = [None] * days
        for p in window:
            bins = p["skills"].get(skill)
            if not bins:
                continue
            if rating is None:
                rating = fit_rating(skill, [p])
                if rating is None:
                    continue
            diff, n = _score(skill, rating, bins)
            rating += max(-MAX_STEP, min(MAX_STEP, k * diff))
            i = day_of.get(time.strftime("%Y-%m-%d", time.localtime(p["time"])))
            if i is not None:
                series[i] = rating
        # the days without plays keep the last rating; before the first play, the starting one
        first = fit_rating(skill, warm)
        last = first
        for i in range(days):
            if series[i] is None:
                series[i] = last
            last = series[i]
        values = [round(v) if v is not None else None for v in series]
        known = [v for v in values if v is not None]
        out["skills"][skill] = {"label": label, "values": values,
                                "current": known[-1] if known else None,
                                "change": known[-1] - known[0] if len(known) >= 2 else None}
    return out


# --- the plays' challenges, kept on disk -----------------------------------------------------------------------------

def load_plays() -> dict:
    try:
        data = json.loads(PLAYS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data.get("plays", {}) if data.get("version") == PLAYS_VERSION else {}


def save_plays(plays: dict):
    PLAYS_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYS_PATH.write_text(json.dumps({"version": PLAYS_VERSION, "plays": plays}), encoding="utf-8")
