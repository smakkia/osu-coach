"""Skill profile: where each skillset stops being comfortable for the player.

A single threshold per skillset doesn't work: players pick maps they can play,
so their fast streams tend to be short and tightly spaced, and the miss rate
barely moves with BPM on its own. Each skillset is therefore a logistic model
of the player's own outcomes on several features at once (e.g. a stream breaks
more with BPM, length and spacing), and the level is read off it on reference
patterns: "16-note streams at 1 radius: comfortable up to 195 BPM".

The same models predict how hard any object of another map would be for the
player, which is what map recommendations build on.

    comfortable: runs cleared 85% of the time / notes missed at most 2%
    limit:       runs broken half the time    / notes missed 8%
"""

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np

from .advice import HIGH_AR, LOW_AR, ExpectedModel, Sample, is_high_ar, is_low_ar
from .beatmap import SLIDER
from .features import ALT, BURST, DEATHSTREAM, FINGER_TAU_MS, FIRST, IRREGULAR, JUMP, STREAM
from .locate import CACHE_DIR

SKILLS_PATH = CACHE_DIR / "skills.json"

RUN_COMFORT, RUN_LIMIT = 0.85, 0.50      # share of runs cleared without a miss
NOTE_COMFORT, NOTE_LIMIT = 0.02, 0.08    # miss rate per note
MULT_COMFORT, MULT_LIMIT = 1.25, 2.0     # miss odds relative to the player's easy conditions
ACC_300_SHARE = 0.90                     # 300s among hit circles for "comfortable" OD

MIN_UNITS = 150
MIN_BAD = 20
MIN_T = 2.0            # the difficulty feature must clearly matter before reading a level off it
L2 = 1e-3

STREAM_PATTERNS = (BURST, STREAM, DEATHSTREAM)
STREAM_LENGTHS = (8, 16, 32, 64)
STREAM_REF_SPACING = 1.0
ALT_LENGTHS = (4, 8, 16)
ALT_REF_SPACING = 2.0
JUMP_DISTANCES = (6.0, 8.0)
SLIDER_RUSH_MS = 120.0          # next note this soon after a slider end: the end gets rushed
SLIDER_END_COMFORT, SLIDER_END_LIMIT = 0.05, 0.15   # slider ends let go early
SLIDER_FOLLOW_JUMP = 6.0        # radii to the next note, for the rushed follow level
STREAM_UR_COMFORT, STREAM_UR_LIMIT = 1.25, 1.5   # scaled stream UR over the player's usual
STREAM_UR_MIN_HITS = 200       # stream notes a 10-BPM bin needs
STAMINA_TAU_S = 1800.0          # the tapping load of a map fades this slowly
STAMINA_COMFORT_DRIFT, STAMINA_LIMIT_DRIFT = 0.10, 0.25   # timing error this much above your usual
READ_REF_NPS = (5.0, 7.0)       # densities at which on-screen note counts are translated into AR
READ_FACTORS = ("overlap", "rhythm", "angles", "spacing")
READ_FACTOR_NAMES = {"overlap": "notes on screen overlapping the one to hit",
                     "rhythm": "irregular rhythm among the notes on screen",
                     "angles": "changing angles (the flow keeps turning)",
                     "spacing": "changing spacing"}


# --- logistic regression -------------------------------------------------------

@dataclass
class Model:
    """P(bad) = sigmoid(offset + coef . [1, x...]); features are named for readability."""
    features: list[str]
    coef: list[float]
    t: list[float]
    n: int
    bad: int
    ranges: dict[str, tuple[float, float]] = field(default_factory=dict)  # p5..p95 of each feature

    def logit(self, x: list[float], offset: float = 0.0) -> float:
        return offset + self.coef[0] + sum(c * v for c, v in zip(self.coef[1:], x))

    def p(self, x: list[float], offset: float = 0.0) -> float:
        return 1 / (1 + math.exp(-self.logit(x, offset)))

    def t_of(self, feature: str) -> float:
        return self.t[1 + self.features.index(feature)]

    def c_of(self, feature: str) -> float:
        return self.coef[1 + self.features.index(feature)]


def fit_logistic(features: list[str], X: np.ndarray, y: np.ndarray, offset: np.ndarray | None = None,
                 l2: float = L2) -> Model | None:
    if len(y) < MIN_UNITS or y.sum() < MIN_BAD or len(y) - y.sum() < MIN_BAD:
        return None
    A = np.column_stack([np.ones(len(X)), X])
    off = np.zeros(len(y)) if offset is None else offset
    b = np.zeros(A.shape[1])
    if offset is None:
        b[0] = math.log(y.mean() / (1 - y.mean()))
    def objective(beta: np.ndarray) -> float:  # penalized log-likelihood
        z = A @ beta + off
        return float((y * z - np.logaddexp(0, z)).sum() - l2 / 2 * beta @ beta)

    current = objective(b)
    for _ in range(100):
        p = 1 / (1 + np.exp(-np.clip(A @ b + off, -30, 30)))
        H = A.T @ (A * (p * (1 - p))[:, None]) + l2 * np.eye(len(b))
        step = np.linalg.solve(H, A.T @ (y - p) - l2 * b)
        for _ in range(30):  # damped Newton: halve the step until the objective improves
            candidate = objective(b + step)
            if candidate >= current - 1e-9:
                break
            step /= 2
        b, current = b + step, candidate
        if np.abs(step).max() < 1e-9:
            break
    p = 1 / (1 + np.exp(-np.clip(A @ b + off, -30, 30)))
    H = A.T @ (A * (p * (1 - p))[:, None]) + l2 * np.eye(len(b))
    se = np.sqrt(np.diag(np.linalg.inv(H)))
    ranges = {name: (float(np.percentile(X[:, i], 5)), float(np.percentile(X[:, i], 95)))
              for i, name in enumerate(features)}
    return Model(features, [float(v) for v in b], [float(v) for v in b / se], len(y), int(y.sum()), ranges)


def _logit(p: float) -> float:
    return math.log(p / (1 - p))


# --- skill results -------------------------------------------------------------

@dataclass
class Level:
    """One reference pattern: comfortable and limit values of the difficulty axis."""
    label: str                 # e.g. "16 notes"
    comfort: float | None
    limit: float | None
    comfort_censored: str = ""  # "above" if beyond what was played, "below" if under it
    limit_censored: str = ""


@dataclass
class Skill:
    key: str
    name: str
    unit: str                    # what the numbers are in: "BPM", "notes/s", "AR", "OD"
    summary: str                 # what the data is
    levels: list[Level] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    model: Model | None = None
    played: tuple[float, float] | None = None   # p5..p95 of the axis in the player's plays
    missing: str = ""            # why no level could be read


def _solve(target_logit: float, known: float, slope: float) -> float | None:
    """x such that known + slope * x = target_logit."""
    if slope <= 0:
        return None
    return (target_logit - known) / slope


def _level(label: str, comfort: float | None, limit: float | None, played: tuple[float, float]) -> Level:
    lvl = Level(label, comfort, limit)
    lo, hi = played
    for attr in ("comfort", "limit"):
        v = getattr(lvl, attr)
        if v is None:
            continue
        if v > hi:
            setattr(lvl, attr, hi)
            setattr(lvl, attr + "_censored", "above")
        elif v < lo:
            setattr(lvl, attr, lo)
            setattr(lvl, attr + "_censored", "below")
    return lvl


def _runs(samples: list[Sample], patterns: tuple[str, ...]) -> list[tuple[float, int, float, bool]]:
    """(bpm, length, spacing radii, broken) per run."""
    groups: dict[tuple, list[Sample]] = defaultdict(list)
    for s in samples:
        if s.f.pattern in patterns:
            groups[s.run_id].append(s)
    return [(g[0].f.bpm, g[0].f.run_length, g[0].f.run_spacing, any(s.missed for s in g)) for g in groups.values()]


def _run_skill(key: str, name: str, samples: list[Sample], patterns: tuple[str, ...],
               lengths: tuple[int, ...], ref_spacing: float) -> Skill:
    runs = _runs(samples, patterns)
    skill = Skill(key, name, "BPM", f"{len(runs)} runs")
    if not runs:
        skill.missing = "none in your plays"
        return skill
    a = np.array(runs, dtype=float)
    skill.played = (float(np.percentile(a[:, 0], 5)), float(np.percentile(a[:, 0], 95)))
    X = np.column_stack([(a[:, 0] - 180) / 10, np.log(a[:, 1]), a[:, 2]])
    m = fit_logistic(["bpm/10", "log length", "spacing"], X, a[:, 3])
    skill.model = m
    if m is None:
        skill.missing = f"not enough data ({len(runs)} runs, {int(a[:, 3].sum())} broken)"
        return skill
    if m.t_of("bpm/10") < MIN_T:
        skill.missing = "your misses here don't depend on BPM in the range you play: no speed limit found yet"
        return skill
    b = m.c_of("bpm/10")
    for length in lengths:
        # the BPMs played on runs of about this length bound what the model can say
        near = (a[:, 1] >= length / 2) & (a[:, 1] <= length * 2)
        if near.sum() < 10:
            skill.levels.append(Level(f"{length} notes", None, None, "none", "none"))
            continue
        played = (float(np.percentile(a[near, 0], 5)), float(np.percentile(a[near, 0], 95)))
        known = m.coef[0] + m.c_of("log length") * math.log(length) + m.c_of("spacing") * ref_spacing
        conv = lambda z: None if z is None else 180 + 10 * z
        skill.levels.append(_level(
            f"{length} notes", conv(_solve(_logit(1 - RUN_COMFORT), known, b)),
            conv(_solve(_logit(1 - RUN_LIMIT), known, b)), played))
    if m.t_of("spacing") >= MIN_T:
        skill.notes.append(f"every +0.5 radius of spacing costs about {10 * m.c_of('spacing') * 0.5 / b:.0f} BPM "
                           f"(levels above are at {ref_spacing:g} radius spacing)")
    return skill


def _jump_skill(samples: list[Sample]) -> Skill:
    js = [s for s in samples if s.f.pattern == JUMP and s.f.gap_ms > 0]
    skill = Skill("jumps", "Jumps", "BPM", f"{len(js)} jumps")
    if not js:
        skill.missing = "none in your plays"
        return skill
    v = np.array([s.f.distance_radii / s.f.gap_ms * 1000 for s in js])
    d = np.array([s.f.distance_radii for s in js])
    y = np.array([s.missed for s in js], dtype=float)
    # angle at the previous note: 0 = straight back, 180 = carrying on in the same direction
    known_angles = [s.f.angle for s in js if s.f.angle is not None]
    ref_angle = float(np.median(known_angles)) if known_angles else 90.0
    a = np.array([(s.f.angle if s.f.angle is not None else ref_angle) / 90 for s in js])
    m = fit_logistic(["speed/10", "distance", "angle/90"], np.column_stack([v / 10, d, a]), y)
    skill.model = m
    if m is None:
        skill.missing = f"not enough data ({len(js)} jumps, {int(y.sum())} missed)"
        return skill
    if m.t_of("speed/10") < MIN_T:
        skill.missing = "your jump misses don't depend on speed in the range you play"
        return skill
    # reference: 1/2 jumps of a given distance; speed (radii/s) = distance * bpm / 30
    for dist in JUMP_DISTANCES:
        known = m.coef[0] + m.c_of("distance") * dist + m.c_of("angle/90") * ref_angle / 90
        to_bpm = lambda z: None if z is None else z * 10 * 30 / dist
        # what the player actually played around this distance, as 1/2 BPM
        near = np.abs(d - dist) <= 1
        bpm_near = v[near] * 30 / dist if near.any() else v * 30 / dist
        played = (float(np.percentile(bpm_near, 5)), float(np.percentile(bpm_near, 95)))
        skill.levels.append(_level(
            f"{dist:g} radii at 1/2", to_bpm(_solve(_logit(NOTE_COMFORT), known, m.c_of("speed/10"))),
            to_bpm(_solve(_logit(NOTE_LIMIT), known, m.c_of("speed/10"))), played))
    skill.notes.append(f"levels at your usual angle, {ref_angle:.0f} deg (0 = straight back, 180 = straight on)")
    if abs(m.t_of("angle/90")) >= MIN_T:
        # +45 deg of angle is worth this much speed; shown as 1/2 BPM on 6-radius jumps
        bpm = -m.c_of("angle/90") * 0.5 / m.c_of("speed/10") * 10 * 30 / 6
        skill.notes.append(f"every +45 deg of angle {'costs' if bpm < 0 else 'gives'} you about {abs(bpm):.0f} BPM "
                           f"on 6-radius jumps")
    return skill


def _slider_skill(samples: list[Sample], expected: ExpectedModel) -> Skill:
    """Sliders are extended circles: the head is aimed like a circle, then the ball has to be
    followed to the end (slider aim).

    Head: misses beyond what the same pattern costs on a circle, modelled on slider speed (the
    main factor), repeats, length, and how fast and how soon after the previous note it comes.
    Follow: slider ends let go before the end, modelled on slider speed, repeats, length, and
    what comes next: a note soon after the end, and far away, pulls the cursor off early.
    Slider breaks inside the slider (combo) are reported alongside."""
    heads = [s for s in samples if s.r.obj.kind == SLIDER and s.f.pattern != FIRST and s.r.obj.span_duration > 0]
    skill = Skill("sliders", "Sliders", "radii/s", f"{len(heads)} sliders")
    if not heads:
        skill.missing = "none in your plays"
        return skill

    def speed(s):  # circle radii the slider ball covers per second
        return s.r.obj.path.length / s.f._radius / (s.r.obj.span_duration / s.rate / 1000)
    cols = np.column_stack([
        [speed(s) / 10 for s in heads],
        [math.log(s.r.obj.repeats) for s in heads],
        [s.r.obj.path.length / s.f._radius for s in heads],
        [s.f.distance_radii / max(s.f.gap_ms, 1.0) * 100 for s in heads],
        [min(s.f.gap_ms, 1000.0) / 1000 for s in heads],
    ])
    y = np.array([s.missed for s in heads], dtype=float)
    offset = np.array([_logit(min(max(expected.p(s), 1e-4), 0.5)) for s in heads])
    m = fit_logistic(["speed/10", "log spans", "length", "incoming speed/10", "gap s"], cols, y, offset)
    skill.model = m
    speeds = cols[:, 0] * 10
    skill.played = (float(np.percentile(speeds, 5)), float(np.percentile(speeds, 95)))

    held = [s for s in heads if not s.missed and s.r.ticks_total > 0]
    ends = [s for s in held if s.r.slider_break_kind == "end"]
    breaks = [s for s in held if s.r.slider_break_kind in ("tick", "repeat")]
    rushed = [s for s in held if s.f.gap_after_ms < SLIDER_RUSH_MS]
    rushed_ends = sum(s.r.slider_break_kind == "end" for s in rushed)
    calm = len(held) - len(rushed)
    if held:
        skill.notes.append(
            f"slider ends let go early on {len(ends) / len(held):.1%} of sliders (accuracy, not combo): "
            f"{rushed_ends / max(len(rushed), 1):.1%} when the next note comes within {SLIDER_RUSH_MS:g}ms of the end, "
            f"{(len(ends) - rushed_ends) / max(calm, 1):.1%} otherwise")
        skill.notes.append(f"slider breaks (combo lost inside the slider): {len(breaks)} of {len(held)} "
                           f"({len(breaks) / len(held):.2%})")
    if m is None:
        skill.missing = f"not enough data ({len(heads)} sliders, {int(y.sum())} heads missed)"
        _slider_follow(skill, held)
        return skill
    if m.t_of("speed/10") < MIN_T:
        skill.missing = "your slider head misses don't depend on slider speed in the range you play"
        _slider_follow(skill, held)
        return skill
    med = np.median(cols, axis=0)
    rest = (m.c_of("length") * med[2] + m.c_of("incoming speed/10") * med[3] + m.c_of("gap s") * med[4])
    for label, spans in (("head, single sliders", 1), ("head, with 2 repeats", 3)):
        known = m.coef[0] + m.c_of("log spans") * math.log(spans) + rest
        to_speed = lambda z: None if z is None else z * 10
        # head misses this many times what the pattern costs on a circle
        skill.levels.append(_level(label, to_speed(_solve(math.log(MULT_COMFORT), known, m.c_of("speed/10"))),
                                   to_speed(_solve(math.log(MULT_LIMIT), known, m.c_of("speed/10"))), skill.played))
    skill.notes.append(f"speed = circle radii the slider ball covers per second (your median: {np.median(speeds):.0f}); "
                       f"head levels: misses {MULT_COMFORT:g}x / {MULT_LIMIT:g}x what the same pattern costs on a circle")
    _slider_follow(skill, held)
    return skill


def _slider_follow(skill: Skill, held: list[Sample]):
    """Slider aim: following the ball to the end instead of leaving for the next note."""
    if not held:
        return
    X = np.column_stack([
        [s.r.obj.path.length / s.f._radius / (s.r.obj.span_duration / s.rate / 1000) / 10 for s in held],
        [math.log(s.r.obj.repeats) for s in held],
        [s.r.obj.path.length / s.f._radius for s in held],
        [float(s.f.gap_after_ms < SLIDER_RUSH_MS) for s in held],
        [s.f.distance_after_radii for s in held],
    ])
    y = np.array([s.r.slider_break_kind == "end" for s in held], dtype=float)
    m = fit_logistic(["speed/10", "log spans", "length", "rushed", "next distance"], X, y)
    if m is None or m.t_of("speed/10") < MIN_T:
        return
    speeds = X[:, 0] * 10
    played = (float(np.percentile(speeds, 5)), float(np.percentile(speeds, 95)))
    med_length = float(np.median(X[:, 2]))
    for label, rushed, dist in ((f"follow, next note {SLIDER_RUSH_MS:g}ms+ after", 0.0, 2.0),
                                (f"follow, {SLIDER_FOLLOW_JUMP:g}-radius jump right after", 1.0, SLIDER_FOLLOW_JUMP)):
        known = (m.coef[0] + m.c_of("length") * med_length + m.c_of("rushed") * rushed
                 + m.c_of("next distance") * dist)
        to_speed = lambda z: None if z is None else z * 10
        skill.levels.append(_level(label, to_speed(_solve(_logit(SLIDER_END_COMFORT), known, m.c_of("speed/10"))),
                                   to_speed(_solve(_logit(SLIDER_END_LIMIT), known, m.c_of("speed/10"))), played))
    per_radius = math.exp(m.c_of("next distance")) - 1
    skill.notes.append(f"follow levels: slider ends let go {SLIDER_END_COMFORT:.0%} / {SLIDER_END_LIMIT:.0%} of the time; "
                       f"a note within {SLIDER_RUSH_MS:g}ms of the end makes it {math.exp(m.c_of('rushed')):.1f}x more likely, "
                       f"and every radius of distance to it {per_radius:+.0%}")


def _stamina_skill(samples: list[Sample]) -> Skill:
    """Timing drift within a play against the tapping accumulated since the map started.

    Each note adds 1 to a load that decays very slowly (STAMINA_TAU_S), so it is roughly the
    notes played so far, discounted over long maps. The outcome is how far each hit lands from
    the player's usual error on that pattern, compared within the same play, so a bad day or a
    map with harder timing doesn't count; the fast finger strain is held constant."""
    plays: dict[int, list[Sample]] = defaultdict(list)
    for s in samples:
        plays[s.play].append(s)
    err_by_bucket: dict[tuple, list[float]] = defaultdict(list)
    for s in samples:
        if s.acc_eligible and s.error is not None:
            err_by_bucket[ExpectedModel.bucket(s)].append(abs(s.error))
    usual = {k: max(float(np.mean(v)), 1.0) for k, v in err_by_bucket.items() if len(v) >= 30}
    rows = []  # play, load, finger strain, relative error
    ends = []
    for pid, ps in plays.items():
        ps.sort(key=lambda s: s.r.obj.time)
        load, prev_t = 0.0, None
        for s in ps:
            t = s.r.obj.time / s.rate / 1000
            if prev_t is not None:
                load *= math.exp(-(t - prev_t) / STAMINA_TAU_S)
            prev_t = t
            u = usual.get(ExpectedModel.bucket(s))
            if u is not None and s.acc_eligible and s.error is not None:
                rows.append((pid, load, s.f.finger_strain, min(abs(s.error) / u, 5.0)))
            load += 1
        ends.append(load)
    skill = Skill("stamina", "Stamina", "notes", f"{len(plays)} plays, {len(rows)} timed hits")
    if len(rows) < 1000:
        skill.missing = "not enough data"
        return skill
    a = np.array(rows)
    for pid in np.unique(a[:, 0]):  # compare within each play
        m = a[:, 0] == pid
        a[m, 1:] -= a[m, 1:].mean(axis=0)
    X = a[:, 1:3]
    b, *_ = np.linalg.lstsq(X, a[:, 3], rcond=None)
    resid = a[:, 3] - X @ b
    se = np.sqrt(np.diag(resid @ resid / (len(resid) - 2) * np.linalg.inv(X.T @ X)))
    slope, t = b[0], b[0] / se[0]
    skill.played = (0.0, float(np.percentile(ends, 95)))
    if t < MIN_T or slope <= 0:
        skill.levels.append(Level("", skill.played[1], None, "above", ""))
        skill.notes.append("your timing doesn't loosen as the map goes on")
        return skill
    skill.levels.append(_level("", STAMINA_COMFORT_DRIFT / slope, STAMINA_LIMIT_DRIFT / slope, skill.played))
    return skill


def _irregular_skill(samples: list[Sample]) -> Skill:
    ss = [s for s in samples if s.f.pattern == IRREGULAR and 0 < s.f.gap_ms < 1000]
    skill = Skill("irregular", "Irregular rhythms", "notes/s", f"{len(ss)} notes on 1/3, 1/6 or odd snaps")
    if not ss:
        skill.missing = "none in your plays"
        return skill
    nps = np.array([1000 / s.f.gap_ms for s in ss])
    y = np.array([s.missed for s in ss], dtype=float)
    skill.played = (float(np.percentile(nps, 5)), float(np.percentile(nps, 95)))
    m = fit_logistic(["notes/s"], nps[:, None], y)
    skill.model = m
    if m is None:
        skill.missing = f"not enough data ({len(ss)} notes, {int(y.sum())} missed)"
        return skill
    if m.t_of("notes/s") < MIN_T:
        skill.missing = "your misses here don't depend on speed: it's the rhythm itself, not how fast it is"
        return skill
    b = m.c_of("notes/s")
    skill.levels.append(_level("note speed", _solve(_logit(NOTE_COMFORT), m.coef[0], b),
                               _solve(_logit(NOTE_LIMIT), m.coef[0], b), skill.played))
    return skill


def _isotonic(values: list[float], weights: list[float]) -> list[float]:
    """Non-decreasing fit (pool adjacent violators)."""
    blocks = [[v, w, 1] for v, w in zip(values, weights)]
    i = 0
    while i < len(blocks) - 1:
        if blocks[i][0] > blocks[i + 1][0]:
            v1, w1, n1 = blocks[i]
            v2, w2, n2 = blocks[i + 1]
            blocks[i] = [(v1 * w1 + v2 * w2) / (w1 + w2), w1 + w2, n1 + n2]
            del blocks[i + 1]
            i = max(i - 1, 0)
        else:
            i += 1
    return [b[0] for b in blocks for _ in range(b[2])]


def _relative_skill(key: str, name: str, unit: str, samples: list[Sample], expected: ExpectedModel,
                    axis, step: float, summary: str, baseline=None, floor: float | None = None) -> Skill:
    """Misses against `axis`, relative to what the patterns alone predict (so harder patterns
    in dense or high-AR sections aren't mistaken for the axis). Bins of `step`, merged until
    each has enough expected misses from enough plays, then fitted as non-decreasing:
    the level is where the player's misses first run clearly above their usual."""
    ss = [s for s in samples if s.f.pattern != FIRST]
    skill = Skill(key, name, unit, summary)
    if not ss:
        skill.missing = "no data"
        return skill
    x = np.array([axis(s) for s in ss])
    skill.played = (float(np.percentile(x, 5)), float(np.percentile(x, 95)))
    if skill.played[1] - skill.played[0] < 2 * step:
        skill.missing = f"you always play around {np.median(x):.1f} {unit}: nothing to compare"
        return skill
    obs = np.array([s.missed for s in ss], dtype=float)
    exp = np.array([expected.p(s) for s in ss])
    # ratio 1 = the player's usual: over all these samples, or over the `baseline` ones
    base = np.array([baseline(s) for s in ss]) if baseline else np.ones(len(ss), dtype=bool)
    scale = obs[base].sum() / exp[base].sum() if exp[base].sum() > 0 else obs.sum() / exp.sum()
    plays = np.array([s.play for s in ss])

    bins = []  # [lo, hi, obs, exp, plays]
    idx = np.ceil(x / step - 1e-9) - 1  # bins are (lo, hi]: AR 10.0 stays below "above 10"
    for k in np.unique(idx):
        m = idx == k
        bins.append([k * step, (k + 1) * step, obs[m].sum(), exp[m].sum() * scale, set(plays[m])])
    merged = []
    for b in bins:
        if merged and (merged[-1][3] < 30 or len(merged[-1][4]) < 3):
            last = merged[-1]
            merged[-1] = [last[0], b[1], last[2] + b[2], last[3] + b[3], last[4] | b[4]]
        else:
            merged.append(b)
    if len(merged) > 1 and (merged[-1][3] < 30 or len(merged[-1][4]) < 3):
        last = merged.pop()
        prev = merged[-1]
        merged[-1] = [prev[0], last[1], prev[2] + last[2], prev[3] + last[3], prev[4] | last[4]]
    if len(merged) < 2:
        skill.missing = "not enough data"
        return skill

    fitted = _isotonic([b[2] / b[3] for b in merged], [b[3] for b in merged])
    skill.notes.append(", ".join(f"{max(b[0], 0.0) if x.min() >= 0 else b[0]:g}-{b[1]:g}: x{f:.2f}" for b, f in zip(merged, fitted))
                       + f"  (misses vs your usual, {unit})")

    # levels are only looked for above `floor` (bins below it are the reference)
    candidates = [(b, f) for b, f in zip(merged, fitted) if floor is None or b[1] > floor + 1e-9]
    if not candidates:
        skill.missing = f"you don't play above {floor:g} {unit}"
        return skill

    def first_above(threshold: float) -> tuple[float | None, str]:
        for b, f in candidates:
            if f >= threshold:
                lo = max(b[0], floor) if floor is not None else b[0]
                return (lo, "below") if b is candidates[0][0] else (lo, "")
        return candidates[-1][0][1], "above"

    comfort, c_cens = first_above(MULT_COMFORT)
    limit, l_cens = first_above(MULT_LIMIT)
    skill.levels.append(Level("", comfort, None if l_cens == "above" else limit, c_cens, l_cens))
    return skill


def _accuracy_skill(samples: list[Sample]) -> Skill:
    skill = Skill("accuracy", "Accuracy", "OD", "")
    groups = (("all circles", lambda s: True), ("streams", lambda s: s.f.pattern in STREAM_PATTERNS),
              ("jumps", lambda s: s.f.pattern == JUMP), ("alt", lambda s: s.f.pattern == ALT))
    n_all = 0
    for label, pred in groups:
        errs = np.array([abs(s.error) for s in samples if s.acc_eligible and s.error is not None and pred(s)])
        if label == "all circles":
            n_all = len(errs)
        if len(errs) < MIN_UNITS:
            continue
        window = float(np.percentile(errs, ACC_300_SHARE * 100))
        skill.levels.append(Level(label, (80 - window) / 6, None))
    skill.summary = f"{n_all} hit circles"
    if not skill.levels:
        skill.missing = "not enough data"
    skill.notes.append(f"OD at which {ACC_300_SHARE:.0%} of your hits would be 300s (nomod; DT makes it ~2.5 OD harder)")
    return skill


def ar_from_preempt(preempt_ms: float) -> float:
    if preempt_ms > 1200:
        return max((1800 - preempt_ms) / 120, 0.0)
    return 5 + (1200 - preempt_ms) / 150


def _reading_factors(s: Sample) -> list[float]:
    f = s.f
    return [f.overlap_share, f.rhythm_var, f.angle_var, f.spacing_var]


def _reading_skill(low: list[Sample], normal: list[Sample]) -> Skill:
    """Reading (AR 9 or below): many notes on screen at once, made harder by what they look like.

    Per note, misses beyond what the pattern costs at normal AR are modelled on the
    number of notes on screen and on how hard those notes are to tell apart (overlaps,
    irregular rhythm, turning angles, changing spacing), with those factors weighing
    more the more notes there are on screen. HD is a control: it removes approach circles.
    The level is read as the number of notes on screen the player handles, for simple and
    for complex patterns, and translated into AR at a few densities."""
    low = [s for s in low if s.f.pattern != FIRST]
    normal = [s for s in normal if s.f.pattern != FIRST]
    plays = len({s.play for s in low})
    skill = Skill("reading", "Reading", "notes on screen",
                  f"{plays} plays below AR {LOW_AR:g} (EZ included), {len(low)} notes; "
                  f"compared with your AR {LOW_AR:g}-{HIGH_AR:g}")
    if plays < 3 or not normal:
        skill.missing = f"not enough plays below AR {LOW_AR:g} ({plays}; at least 3 needed)"
        return skill
    ss = low + normal
    expected = ExpectedModel(normal)
    offset = np.array([_logit(min(max(expected.p(s), 1e-4), 0.5)) for s in ss])
    v = np.log1p(np.array([s.f.visible for s in ss], dtype=float))
    F = np.array([_reading_factors(s) for s in ss])
    X = np.column_stack([v, F, v[:, None] * F, [float(s.hidden) for s in ss]])
    names = (["visible"] + list(READ_FACTORS) + [f"visible x {k}" for k in READ_FACTORS] + ["hidden"])
    m = fit_logistic(names, X, np.array([s.missed for s in ss], dtype=float), offset)
    skill.model = m
    if m is None:
        skill.missing = "not enough misses to fit"
        return skill

    v0 = float(np.log1p(np.median([s.f.visible for s in normal])))
    low_visible = np.array([s.f.visible for s in low], dtype=float)
    skill.played = (float(np.percentile(low_visible, 5)), float(np.percentile(low_visible, 95)))
    low_F = np.array([_reading_factors(s) for s in low])
    for label, q in (("simple patterns", 10), ("complex patterns", 90)):
        x = np.percentile(low_F, q, axis=0)
        slope = m.c_of("visible") + sum(m.c_of(f"visible x {k}") * xi for k, xi in zip(READ_FACTORS, x))
        if slope <= 0:
            skill.levels.append(Level(label, skill.played[1], None, "above", ""))
            continue
        to_visible = lambda mult: float(np.expm1(v0 + math.log(mult) / slope))
        skill.levels.append(_level(label, to_visible(MULT_COMFORT), to_visible(MULT_LIMIT), skill.played))

    hard = sorted(((m.t_of(f"visible x {k}"), k) for k in READ_FACTORS), reverse=True)
    hard = [READ_FACTOR_NAMES[k] for t, k in hard if t >= MIN_T]
    skill.notes.append("what makes many notes on screen harder for you: " + ("; ".join(hard) if hard else
                       "none of the pattern factors stands out, it's mostly the number of notes"))
    if m.t_of("visible") < MIN_T and not hard:
        skill.notes.append("the effect of many notes on screen isn't clear in your plays yet")
    return skill


def stream_ur_curve(samples: list[Sample]) -> dict | None:
    """Timing on long streams (8+ notes, 170+ BPM) by speed, as UR scaled to 180 BPM.

    The error that matters is relative to the gap between taps: UR 150 at 180 BPM holds the
    stream as well as UR 100 at 270 BPM, so UR is scaled by BPM / 180. Per 10-BPM bin, fitted
    as non-decreasing with speed; the baseline is the scaled UR where most of the player's
    stream notes are. Comfortable up to where it rises STREAM_UR_COMFORT over the baseline,
    limit where it rises STREAM_UR_LIMIT."""
    hits = [(s.f.bpm, s.error) for s in samples
            if s.f.pattern in STREAM_PATTERNS and s.f.run_length >= 8 and s.f.bpm and s.f.bpm >= 170
            and s.error is not None]
    if len(hits) < 1000:
        return None
    a = np.array(hits)
    bins = []
    for lo in range(170, int(a[:, 0].max()) + 10, 10):
        m = (a[:, 0] >= lo) & (a[:, 0] < lo + 10)
        if m.sum() >= STREAM_UR_MIN_HITS:
            bins.append([lo, lo + 10, int(m.sum()), float(10 * np.std(a[m, 1]) * a[m, 0].mean() / 180)])
    if len(bins) < 3:
        return None
    fitted = _isotonic([b[3] for b in bins], [b[2] for b in bins])
    baseline = fitted[max(range(len(bins)), key=lambda i: bins[i][2])]

    centres = [(b[0] + b[1]) / 2 for b in bins]

    def first(ratio):
        """BPM where the fitted curve crosses ratio x baseline, interpolated between bin centres."""
        target = ratio * baseline
        for i, f in enumerate(fitted):
            if f >= target:
                if i == 0:
                    return centres[0], False
                f0, x0 = fitted[i - 1], centres[i - 1]
                return x0 + (target - f0) / (f - f0) * (centres[i] - x0), False
        return bins[-1][1], True  # never reached in what the player played
    (comfort, c_cens), (limit, l_cens) = first(STREAM_UR_COMFORT), first(STREAM_UR_LIMIT)
    return {"bins": [[b[0], b[1], b[2], round(b[3], 1), round(f, 1)] for b, f in zip(bins, fitted)],
            "baseline": baseline, "comfort": comfort, "comfort_censored": c_cens,
            "limit": limit, "limit_censored": l_cens}


def stream_ur_at(curve: dict, bpm: float) -> float:
    """The player's scaled stream UR at this speed: the fitted curve, interpolated between bin
    centres (flat past the ends)."""
    bins = curve["bins"]
    return float(np.interp(bpm, [(b[0] + b[1]) / 2 for b in bins], [b[4] for b in bins]))


def _tap_timing_skill(samples: list[Sample]) -> Skill:
    curve = stream_ur_curve(samples)
    skill = Skill("tap timing", "Tapping speed (timing on long streams)", "BPM", "UR scaled to 180 BPM")
    if curve is None:
        skill.missing = "not enough long streams"
        return skill
    skill.summary = f"UR scaled to 180 BPM; your usual {curve['baseline']:.0f}"
    lvl = Level("long streams", curve["comfort"], curve["limit"],
                "above" if curve["comfort_censored"] else "", "above" if curve["limit_censored"] else "")
    skill.levels.append(lvl)
    skill.notes.append(f"comfortable while the scaled UR stays within +{STREAM_UR_COMFORT - 1:.0%} of your usual, "
                       f"limit at +{STREAM_UR_LIMIT - 1:.0%}: UR 150 at 180 BPM is as good as UR 100 at 270")
    skill.notes.append(", ".join(f"{lo}-{hi}: UR {ur * 180 / ((lo + hi) / 2):.0f} ({ur:.0f} at 180)"
                                 for lo, hi, _, ur, _ in curve["bins"]))
    return skill


def build_profile(samples: list[Sample], low_ar_samples: list[Sample] | None = None,
                  high_ar_samples: list[Sample] | None = None) -> list[Skill]:
    """`samples`: recent plays. Plays at AR 9 or below and above 10 are rarer and come with their
    own windows; the recent plays at AR 9-10 are the reference for both."""
    expected = ExpectedModel(samples)
    normal = [s for s in samples if not is_low_ar(s.ar) and not is_high_ar(s.ar)]
    return [
        _run_skill("streams", "Streams (bursts to deathstreams)", samples, STREAM_PATTERNS, STREAM_LENGTHS,
                   STREAM_REF_SPACING),
        _tap_timing_skill(samples),
        _run_skill("alt", "Alt", samples, (ALT,), ALT_LENGTHS, ALT_REF_SPACING),
        _jump_skill(samples),
        _irregular_skill(samples),
        _relative_skill("fingercontrol", "Finger control", "taps/s", samples, expected,
                        lambda s: s.f.finger_strain / (FINGER_TAU_MS / 1000), 1.0,
                        "tapping load over the last ~0.5 s; a change of technique or rhythm counts as an extra tap"),
        _slider_skill(samples, expected),
        _stamina_skill(samples),
        _relative_skill("high_ar", "High AR (reaction time)", "AR", normal + (high_ar_samples or []), expected,
                        lambda s: s.ar, 0.25, f"effective AR above {HIGH_AR:g}, compared with your AR {LOW_AR:g}-{HIGH_AR:g}",
                        baseline=lambda s: s.ar <= HIGH_AR, floor=HIGH_AR),
        _reading_skill(low_ar_samples or [], normal),
        _accuracy_skill(samples),
    ]


# --- output ----------------------------------------------------------------------

def _value(v: float | None, censored: str, unit: str) -> str:
    if censored == "none":
        return "no data"
    if v is None:
        return "-"
    digits = 1 if unit in ("AR", "OD", "notes/s") else 0
    s = f"{v:.{digits}f}"
    return {"above": f">{s}", "below": f"<{s}"}.get(censored, s)


def _ar_at(visible: float | None) -> str:
    """The AR that puts this many notes on screen at the reference densities."""
    if visible is None:
        return ""
    return ", ".join(f"AR {ar_from_preempt(visible / nps * 1000):.1f} at {nps:g} notes/s" for nps in READ_REF_NPS)


def describe(skill: Skill) -> list[str]:
    lines = [f"{skill.name}  ({skill.summary}"
             + (f"; you play {_value(skill.played[0], '', skill.unit)}-{_value(skill.played[1], '', skill.unit)} "
                f"{skill.unit}" if skill.played and skill.key not in ("jumps", "accuracy", "reading") else "") + ")"]
    if skill.missing:
        lines.append(f"  {skill.missing}")
    if skill.key == "accuracy":
        for lvl in skill.levels:
            lines.append(f"  {lvl.label:<16} OD {lvl.comfort:.1f}")
    elif skill.levels:
        if skill.key == "reading":
            for lvl in skill.levels:
                lines.append(f"  {lvl.label}: comfortable up to {_value(lvl.comfort, lvl.comfort_censored, 'n')} "
                             f"notes on screen ({_ar_at(lvl.comfort)})"
                             + (f", misses double from {_value(lvl.limit, lvl.limit_censored, 'n')} "
                                f"({_ar_at(lvl.limit)})" if lvl.limit is not None else ""))
        elif skill.key == "stamina":
            lvl = skill.levels[0]
            if lvl.comfort_censored == "above":
                lines.append(f"  your timing holds for the whole map (checked up to {lvl.comfort:.0f} notes into a play)")
            else:
                lines.append(f"  timing error +{STAMINA_COMFORT_DRIFT:.0%} after "
                             f"{_value(lvl.comfort, lvl.comfort_censored, 'n')} notes into a play"
                             + (f", +{STAMINA_LIMIT_DRIFT:.0%} after {_value(lvl.limit, lvl.limit_censored, 'n')}"
                                if lvl.limit is not None else ""))
        elif skill.key in ("fingercontrol", "high_ar"):
            lvl = skill.levels[0]
            start = (f"  misses already above your usual from {lvl.comfort:.1f} {skill.unit}"
                     if lvl.comfort_censored == "below" else
                     f"  comfortable up to {_value(lvl.comfort, lvl.comfort_censored, skill.unit)} {skill.unit}")
            lines.append(start
                         + (f", misses double from {_value(lvl.limit, lvl.limit_censored, skill.unit)} {skill.unit}"
                            if lvl.limit is not None else ""))
        else:
            w = max([16] + [len(lvl.label) for lvl in skill.levels])
            lines.append(f"  {'':<{w}} {'comfortable':>12} {'limit':>8}   ({skill.unit})")
            for lvl in skill.levels:
                lines.append(f"  {lvl.label:<{w}} {_value(lvl.comfort, lvl.comfort_censored, skill.unit):>12} "
                             f"{_value(lvl.limit, lvl.limit_censored, skill.unit):>8}")
    for n in skill.notes:
        lines.append(f"  ({n})")
    return lines


def save_profile(skills: list[Skill], plays: int, player: str | None):
    SKILLS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"plays": plays, "player": player, "skills": [asdict(s) for s in skills]}
    SKILLS_PATH.write_text(json.dumps(data, indent=1), encoding="utf-8")
