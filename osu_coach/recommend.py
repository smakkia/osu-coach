"""Beatmap recommendations: maps from the player's own Songs folder that train a skill.

1. osu!.db gives every difficulty; candidates are osu!standard, never played, of an accepted
   ranked status, long enough, with a star rating near what the player usually plays (or in the
   range asked for), for each mod combination asked for (nomod and DT by default). Ranges of
   effective AR (reading), CS (precision) and OD (accuracy) can narrow them further.
2. What each candidate contains is read from the map alone and cached for good: its map type
   (maptypes.py) and its runs (for tapping speed).
3. A skill is a map type: jump, stream, alt, finger control/burst, tech, or aim control. Only
   maps whose type has it count, alone or in a hybrid ("Alt + jump" trains alt and jump); the
   ones where it takes most of the intense notes are looked at first. Difficulty for the player:
     stream   the long streams' BPM must fall in the player's tapping window (where their stream
              timing starts to give, up to their limit) and not break too often
     others   the unified model's expected miss rate, a step above the player's usual
   The picks are laid out as a ladder, easiest first, one difficulty per beatmap set.
"""

import hashlib
import json
import math
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass

import numpy as np

from .locate import CACHE_DIR
from .mapdb import MapInfo
from .model import COMPONENT_NAMES, COMPONENTS, UnifiedModel
from .mods import Mods, mods_string

CONTENT_CACHE = CACHE_DIR / "map_content.json"
PROFILE_CACHE = CACHE_DIR / "map_profiles.json"
CONTENT_VERSION = "3"                # bump when map_content changes
ACCEPTED_STATUS = (4, 5)             # ranked, approved
LOVED_STATUS = 7                     # optional: loved maps include exploit maps (usually with extreme star ratings)
MIN_DRAIN_S = 30
STAR_MARGIN_BELOW, STAR_MARGIN_ABOVE = 0.3, 0.8   # around the player's usual star ratings (p10..p90)
LEVEL_MIN, LEVEL_MAX = 1.0, 2.2      # expected miss rate, relative to the player's usual
LEVEL_CAP = 2.5                      # stream maps: the whole map may not be harder than this
BREAK_MAX = 0.40                     # stream maps: streams broken more often than this are too hard
TAP_WINDOW_BELOW, TAP_WINDOW_ABOVE = 5, 10   # BPM around the player's stream-timing comfort .. limit
DEFAULT_MODS = (0, int(Mods.DoubleTime))
FAST_TAP_MS = 150                    # rhythm changes between notes this close are finger control
LADDER = 15
PROFILE_MAX = 400                    # maps profiled per skill: those where it takes most of the notes
# skill -> (map type name, the unified model's component its misses are counted in)
SKILLS = {
    "jump": ("jump", "jump aim"),
    "stream": ("stream", "tap speed"),
    "alt": ("alt", "alt aim"),
    "finger control": ("finger control/burst", "finger control"),
    "tech": ("tech", "slider"),
    "aim control": (None, "aim control"),
    "reading": (None, "reading"),
    "speed": (None, "tap speed"),
    "precision": (None, "jump aim"),
}
SKILL_SHARE = {"jump": "jump", "stream": "stream", "alt": "alt", "finger control": "finger",
               "tech": "tech sliders"}
ONLINE_KEY = {"stream": "tap", "finger control": "finger"}   # online.discover's searches
SAVE_EVERY = 500
WORKERS = max((os.cpu_count() or 2) // 2, 1)   # leave the machine usable while profiling


# --- content: from the map alone ------------------------------------------------------

def map_content(samples) -> dict:
    """What the map is made of, from the map alone: raw enough that the scores (see `scores`)
    can change without reading every map again."""
    from .features import FIRST, IRREGULAR, RHYTHM_CHANGE_RATIO, RUN_PATTERNS
    runs = {}
    for s in samples:
        if s.f.run_length >= 2 and s.f.bpm:
            runs.setdefault(s.run_id, (s.f.bpm, s.f.run_length, s.f.run_spacing))
    rhythm = rhythm_fast = 0
    for a, b in zip(samples, samples[1:]):
        g1, g2 = a.f.gap_ms, b.f.gap_ms
        if b.f.pattern == FIRST or g1 >= 1000 or g2 >= 1000:
            continue
        if max(g1, g2) >= RHYTHM_CHANGE_RATIO * min(g1, g2):
            rhythm += 1
            rhythm_fast += max(g1, g2) <= FAST_TAP_MS
    aim = {"flow": [0, 0.0], "alt": [0, 0.0], "jump": [0, 0.0]}
    for s in samples:
        kind = s.f.aim
        if kind and s.f.pattern not in RUN_PATTERNS:   # aim between notes that aren't tapped as a run
            aim[kind][0] += 1
            aim[kind][1] += s.f.distance_radii / max(s.f.move_ms, 1.0) * 1000
    return {
        "notes": len(samples),
        "runs": [[round(b, 1), l, round(sp, 2)] for b, l, sp in runs.values()],
        "rhythm": rhythm,
        "rhythm fast": rhythm_fast,
        "irregular": sum(s.f.pattern == IRREGULAR for s in samples),
        "aim": aim,   # per kind of aim: [notes, sum of cursor speed in radii/s]
    }


def _content_job(args):
    md5, path, mods = args
    from .collect import map_samples
    samples = map_samples(path, mods)
    return map_content(samples) if samples else None


def scores(c: dict) -> dict[str, float]:
    """How much of each skill the map trains.

    tap     long streams (8+ notes at 180+ BPM): their share of the map, and their BPM
    flow    stream-speed runs spaced wider than a radius, weighted by how wide
    finger  short groups (2-9 notes) at tapping speed (165+ BPM) and rhythm changes between
            close notes: what makes the fingers switch; slow groups between jumps are aim
    aim     cursor speed on the notes that aren't streams (alt and jump aim)"""
    n = max(c["notes"], 1)
    runs = c["runs"]
    long_fast = [(b, l) for b, l, _ in runs if l >= 8 and b >= 180]
    tap_notes = sum(l for _, l in long_fast)
    return {
        "tap share": tap_notes / n,
        "tap bpm": sum(b * l for b, l in long_fast) / tap_notes if tap_notes else 0.0,
        "flow": sum(l * min(max(sp - 0.8, 0.0), 3.0) for b, l, sp in runs if l >= 4 and b >= 165) / n,
        "finger": (sum(l for b, l, _ in runs if l <= 9 and b >= 165) + c["rhythm fast"]) / n,
        "aim": (c["aim"]["alt"][1] + c["aim"]["jump"][1]) / 10 / n,
    }


def tap_window(model: UnifiedModel) -> tuple[float, float] | None:
    """BPM range for tapping maps: from where the player's stream timing stops being comfortable to
    where it reaches their limit (scaled UR +25% .. +50%); a bit wider when the limit wasn't reached."""
    c = model.stream_ur
    if not c:
        return None
    lo = c["comfort"] - TAP_WINDOW_BELOW
    hi = c["limit"] + (TAP_WINDOW_ABOVE if c["limit_censored"] else 0)
    return lo, hi


def tap_timing(model: UnifiedModel, bpm: float) -> float | None:
    """The player's stream timing at this speed vs their usual (scaled UR ratio)."""
    from .skills import stream_ur_at
    c = model.stream_ur
    return stream_ur_at(c, bpm) / c["baseline"] if c else None


# --- the player's side ----------------------------------------------------------------

def stream_break(model: UnifiedModel, runs: list, kind: str) -> float | None:
    """Mean chance the player breaks the map's streams of this kind (weighted by length).

    kind "tap": long streams (8+ notes, 180+ BPM); "flow": streams spaced wider than a radius."""
    if not model.stream_runs:
        return None
    b0, b_bpm, b_len, b_sp = model.stream_runs
    total = weight = 0.0
    for bpm, length, spacing in runs:
        if length < 4 or bpm < 165:
            continue
        if kind == "tap" and not (length >= 8 and bpm >= 180):
            continue
        if kind == "flow" and spacing <= 1.0:
            continue
        z = b0 + b_bpm * (bpm - 180) / 10 + b_len * math.log(length) + b_sp * spacing
        total += length / (1 + math.exp(-max(min(z, 30), -30)))
        weight += length
    return total / weight if weight else None


@dataclass
class MapProfile:
    md5: str
    mods: int
    notes: int
    expected: float                  # expected misses for the player (chains included)
    components: dict[str, float]     # expected misses per component

    @property
    def rate(self) -> float:
        return self.expected / max(self.notes, 1)

    def share(self, component: str) -> float:
        total = sum(self.components.values())
        return self.components.get(component, 0.0) / total if total else 0.0


def model_signature(model: UnifiedModel) -> str:
    return hashlib.md5(json.dumps([model.coef, model.knots, model.chains], sort_keys=True).encode()).hexdigest()[:12]


def _profile_job(args) -> MapProfile | None:
    model, md5, path, mods = args
    from .collect import map_samples
    samples = map_samples(path, mods)
    if not samples:
        return None
    X, _, kept = model.matrix(samples)
    if not len(kept):
        return None
    p, excess = model.attribute(X)
    chain = np.array([model.chain(s) for s in kept])
    return MapProfile(md5, mods, len(kept), float((p * chain).sum()),
                      dict(zip(COMPONENTS, (excess * chain[:, None]).sum(axis=0).tolist())))


# --- candidates and caches ------------------------------------------------------------

def _star_key(mods: int) -> int:
    """The mods osu!.db keeps star ratings for."""
    key = mods & (Mods.Easy | Mods.HardRock | Mods.HalfTime | Mods.DoubleTime)
    if mods & Mods.Nightcore:
        key |= Mods.DoubleTime
    return key


def parse_mods(text: str) -> tuple[int, ...]:
    """"NM,DT,HR,HDDT" -> mod bitmasks."""
    codes = {"NM": 0, "NF": Mods.NoFail, "EZ": Mods.Easy, "HD": Mods.Hidden, "HR": Mods.HardRock,
             "DT": Mods.DoubleTime, "NC": Mods.DoubleTime, "HT": Mods.HalfTime}
    out = []
    for combo in text.upper().replace(" ", "").split(","):
        mods = 0
        for i in range(0, len(combo), 2):
            if combo[i:i + 2] not in codes:
                raise ValueError(f"unknown mod {combo[i:i + 2]!r}")
            mods |= int(codes[combo[i:i + 2]])
        out.append(mods)
    return tuple(dict.fromkeys(out))


def star_band(maps: dict[str, MapInfo], played: list[tuple[str, int]]) -> tuple[float, float] | None:
    """Star range around what the player usually plays: (md5, mods) of recent plays."""
    stars = [maps[md5].stars.get(_star_key(mods)) for md5, mods in played if md5 in maps]
    stars = [s for s in stars if s]
    if len(stars) < 10:
        return None
    lo, hi = np.percentile(stars, [10, 90])
    return float(lo) - STAR_MARGIN_BELOW, float(hi) + STAR_MARGIN_ABOVE


def parse_range(text: str | None) -> tuple[float | None, float | None]:
    """"8-9.5", "8-" (from 8), "-9.5" (up to 9.5); empty: no limit."""
    text = (text or "").replace(" ", "")
    if not text:
        return None, None
    lo, sep, hi = text.partition("-")
    try:
        if not sep:
            return float(lo), float(lo)
        return (float(lo) if lo else None), (float(hi) if hi else None)
    except ValueError:
        raise ValueError(f"not a range: {text!r} (e.g. 8-9.5, 8-, -9.5)")


def parse_length(text: str | None) -> tuple[float | None, float | None]:
    """Length range in seconds from "1:30-3:00", "90-180", "2:00-" or "-90"."""
    def seconds(t: str) -> float:
        if ":" in t:
            m, _, sec = t.partition(":")
            return int(m) * 60 + float(sec)
        return float(t)
    text = (text or "").replace(" ", "")
    if not text:
        return None, None
    lo, sep, hi = text.partition("-")
    try:
        if not sep:
            return seconds(lo), seconds(lo)
        return (seconds(lo) if lo else None), (seconds(hi) if hi else None)
    except ValueError:
        raise ValueError(f"not a length range: {text!r} (e.g. 1:30-3:00, 2:00-, -90)")


def effective_values(m: MapInfo, mods: int) -> dict[str, float]:
    """AR, CS, OD, drain length (s) and main BPM as played with these mods (DT/HT change AR, OD, length and BPM)."""
    from .advice import effective_ar
    from .difficulty import Difficulty
    from .mods import clock_rate
    d, rate = Difficulty.from_map(m.cs, m.ar, m.od, mods), clock_rate(mods)
    return {"ar": effective_ar(d, rate), "cs": d.cs, "od": (80 - (80 - 6 * d.od) / rate) / 6, "length": m.drain_s / rate,
            "bpm": m.bpm * rate}


def in_ranges(m: MapInfo, mods: int, ranges: dict[str, tuple[float | None, float | None]]) -> bool:
    values = effective_values(m, mods)
    for key, (lo, hi) in ranges.items():
        if (lo is not None and values[key] < lo - 1e-6) or (hi is not None and values[key] > hi + 1e-6):
            return False
    return True


def candidates(maps: list[MapInfo], played_md5: set[str], band: tuple[float, float], mod_sets: tuple[int, ...],
               statuses=ACCEPTED_STATUS) -> list[tuple[MapInfo, int]]:
    out = []
    for m in maps:
        if (m.mode != 0 or not m.unplayed or m.md5 in played_md5 or m.status not in statuses
                or m.drain_s < MIN_DRAIN_S):
            continue
        for mods in mod_sets:
            s = m.stars.get(_star_key(mods))
            if s is not None and band[0] <= s <= band[1]:
                out.append((m, mods))
    return out


def _load(path, tag: str) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return data if data.get("tag") == tag else {"tag": tag, "maps": {}}


def _save(path, data: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data), encoding="utf-8")
    tmp.replace(path)


def _run_cached(path, tag: str, jobs: list[tuple[str, tuple]], worker, progress=None, label="") -> dict:
    """Results by key, computing (in parallel) only the missing ones; saved as it goes."""
    data = _load(path, tag)
    known = data["maps"]
    todo = [(k, a) for k, a in jobs if k not in known]
    if todo:
        with ProcessPoolExecutor(max_workers=WORKERS) as pool:
            for n, res in enumerate(pool.map(worker, [a for _, a in todo], chunksize=8), 1):
                known[todo[n - 1][0]] = asdict(res) if hasattr(res, "__dataclass_fields__") else res
                if n % SAVE_EVERY == 0:  # an interrupted run keeps what it computed
                    _save(path, data)
                if progress and n % 250 == 0:
                    progress(label, n, len(todo))
        _save(path, data)
    return {k: known.get(k) for k, _ in jobs}


def contents(songs, items: list[tuple[MapInfo, int]], progress=None) -> dict[str, dict]:
    jobs = [(f"{m.md5}:{mods}", (m.md5, str(songs / m.path), mods)) for m, mods in items]
    return _run_cached(CONTENT_CACHE, CONTENT_VERSION, jobs, _content_job, progress, "content")


def profiles(model: UnifiedModel, songs, items: list[tuple[MapInfo, int]], progress=None) -> dict[str, MapProfile]:
    jobs = [(f"{m.md5}:{mods}", (model, m.md5, str(songs / m.path), mods)) for m, mods in items]
    raw = _run_cached(PROFILE_CACHE, model_signature(model), jobs, _profile_job, progress, "your difficulty")
    return {k: MapProfile(**v) for k, v in raw.items() if v}


# --- choosing ---------------------------------------------------------------------------

@dataclass
class Pick:
    info: MapInfo
    mods: int
    content: dict
    profile: MapProfile | None
    difficulty: float     # stream: stream timing vs usual; the rest: expected miss rate vs usual
    score: float          # how much of the map is the skill (see skill_share)
    ur_baseline: float = 0.0  # tap: the player's usual scaled stream UR


def _percentiles(values: np.ndarray) -> np.ndarray:
    order = values.argsort(kind="stable")
    ranks = np.empty(len(values))
    ranks[order] = np.arange(len(values))
    return ranks / max(len(values) - 1, 1)


def has_skill(a: dict | None, skill: str) -> bool:
    """The map's type has the skill, alone or in a hybrid; aim control: above the threshold."""
    from . import maptypes
    if not a:
        return False
    if skill == "aim control":
        return maptypes.has_aim_control(a)
    if skill == "reading":
        return maptypes.has_reading(a)
    if skill == "speed":
        return maptypes.has_speed(a)
    if skill == "precision":
        return maptypes.has_precision(a)
    return SKILLS[skill][0] in [part.strip().lower() for part in maptypes.category(a).split("+")]


PREDICTED_TYPES = CACHE_DIR / "predicted_types.json"   # map types guessed for maps not in Songs (typepred)


def load_predicted() -> dict[str, dict]:
    """Guessed types of maps not in Songs, by "beatmap id:NM|DT"; empty when never computed."""
    try:
        return json.loads(PREDICTED_TYPES.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def predicted_skill(p: dict | None, skill: str) -> bool | None:
    """Whether a guessed type has the skill: True / False where the guess is confident enough, None when unsure.
    A guess of the main kind only can say yes, never no (the skill may be the hybrid's second kind)."""
    if not p:
        return None
    if skill == "aim control":
        return p.get("aim control")
    if skill == "reading":   # approximate on a guess (typeguess.is_reading): it can say yes, never a sure no
        return True if p.get("reading") else None
    if skill in ("speed", "precision"):
        return bool(p.get(skill))
    if not p.get("kind"):
        return None
    parts = [part.strip().lower() for part in p["kind"].split("+")]
    if SKILLS[skill][0] in parts:
        return True
    return False if p.get("confidence") == "type" else None


def skill_share(a: dict, skill: str) -> float:
    """How much of the map is the skill: its share of the intense notes (aim control: against its star rating)."""
    from . import maptypes
    if skill == "reading":      # a tag: the maps with the most short groups on screen first
        return a["finger"]
    if skill == "speed":        # a tag: the fastest first
        return a.get("bpm") or 0.0
    if skill == "precision":    # a tag: the smallest circles first
        return a.get("cs") or 0.0
    return maptypes.aim_ratio(a) if skill == "aim control" else a[SKILL_SHARE[skill]]


def skill_candidates(skill: str, items: list[tuple[MapInfo, int]], kinds: dict[str, dict], content: dict[str, dict],
                     model: UnifiedModel) -> list[tuple[MapInfo, int]]:
    """Maps whose type has the skill, where it takes the most of the notes first (at most PROFILE_MAX);
    stream maps also at the player's tapping edge."""
    out = []
    for m, mods in items:
        k = f"{m.md5}:{mods}"
        if not has_skill(kinds.get(k), skill):
            continue
        if skill == "stream":
            c = content.get(k)
            window = tap_window(model)
            if c is None or window is None or not window[0] <= scores(c)["tap bpm"] <= window[1]:
                continue  # streams where the player's timing is still fine, or already falls apart
            br = stream_break(model, c["runs"], "tap")
            if br is not None and br > BREAK_MAX:
                continue
        out.append((m, mods))
    out.sort(key=lambda it: -skill_share(kinds[f"{it[0].md5}:{it[1]}"], skill))
    return out[:PROFILE_MAX]


def ladder(skill: str, items: list[tuple[MapInfo, int]], kinds: dict[str, dict], content: dict[str, dict],
           profs: dict[str, MapProfile], model: UnifiedModel, size: int = LADDER) -> list[Pick]:
    picks = []
    for m, mods in items:
        k = f"{m.md5}:{mods}"
        c, p = content.get(k), profs.get(k)
        if c is None or p is None or not model.usual_rate:
            continue
        level = p.rate / model.usual_rate
        share = skill_share(kinds[k], skill)
        if skill == "stream":
            if level <= LEVEL_CAP:
                picks.append(Pick(m, mods, c, p, tap_timing(model, scores(c)["tap bpm"]) or 0.0, share,
                                  model.stream_ur.get("baseline", 0.0)))
        elif LEVEL_MIN <= level <= LEVEL_MAX:
            picks.append(Pick(m, mods, c, p, level, share))
    # the maps with the most of the skill, one difficulty (and mod) per beatmap set, then spread over the difficulty
    picks.sort(key=lambda k: -k.score)
    seen, best = set(), []
    for k in picks:
        if k.info.set_id and k.info.set_id in seen:
            continue
        seen.add(k.info.set_id)
        best.append(k)
        if len(best) >= size * 3:
            break
    best.sort(key=lambda k: k.difficulty)
    if len(best) <= size:
        return best
    return [best[i] for i in np.linspace(0, len(best) - 1, size).round().astype(int)]


def describe_pick(k: Pick, skill: str, kind: str | None = None) -> str:
    m, c = k.info, scores(k.content)
    mods = f" +{mods_string(k.mods)}" if k.mods else ""
    stars = m.stars.get(_star_key(k.mods))
    rate = 1.5 if k.mods & Mods.DoubleTime else 0.75 if k.mods & Mods.HalfTime else 1.0
    length = int(m.drain_s / rate)
    link = f"https://osu.ppy.sh/b/{m.beatmap_id}" if m.beatmap_id > 0 else "(not submitted)"
    from .online import is_online
    where = "  [not in your Songs]" if is_online(m) else ""
    v = effective_values(m, k.mods)
    head = (f"{m.display_name}{mods}  {stars:.2f}*  {v['bpm']:.0f} BPM  AR {v['ar']:.1f} CS {v['cs']:.1f} OD {v['od']:.1f}  "
            f"{length // 60}:{length % 60:02d}{where}")
    if kind:
        head += f"  [{kind}]"
    if skill == "stream":
        ur180 = k.difficulty * k.ur_baseline
        body = (f"{c['tap share']:.0%} long streams at {c['tap bpm']:.0f} BPM; your stream UR there "
                f"~{ur180 * 180 / c['tap bpm']:.0f} ({ur180:.0f} at 180 BPM, {k.difficulty - 1:+.0%} over your usual)")
    else:
        amount = (f"aim control x{k.score:.2f} its star rating's usual" if skill == "aim control"
                  else f"reading, finger control/burst {k.score:.0%} of the intense notes" if skill == "reading"
                  else f"speed, {k.score:.0f} BPM as played" if skill == "speed"
                  else f"precision, CS {k.score:.1f} as played" if skill == "precision"
                  else f"{skill} {k.score:.0%} of the intense notes")
        body = f"{amount}; ~{k.profile.expected:.0f} misses ({k.difficulty:.1f}x your usual)"
    return f"{head}  {body}\n      {link}"
