"""Turns judged replays into bad habits: where the player does clearly worse than their own average.

Every insight compares a slice of the player's own objects (a pattern, a jump
distance, a stream BPM...) against their baseline, and is only reported when
there are enough samples, the difference is statistically clear and it comes
from several distinct episodes. The same rules therefore work for any skill
level: they describe where *this* player is weaker than their own average.

Context effects (stamina, high AR, busy screens) are measured against an
expected-miss model built from the player's own miss rate on each pattern, so
harder patterns in those sections are not mistaken for the effect itself.
"""

import bisect
import math
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property

import numpy as np

from .beatmap import CIRCLE, SLIDER
from .difficulty import HITTABLE_RANGE, Difficulty
from .features import (ALT, BURST, DEATHSTREAM, DOUBLE, IRREGULAR, JUMP, LONG_STREAMS, RUN_PATTERNS, STREAM,
                       STREAM_FAMILY, TRIPLE, ObjectFeatures)
from .judge import ObjectResult

MIN_SLICE = 40       # objects needed before a slice can produce an insight
MIN_MISSES = 5
MIN_EPISODES = 3     # distinct runs / plays a finding must come from
Z_THRESHOLD = 2.5    # ~99% one-sided
T_THRESHOLD = 3.0
MIN_BIAS_MS = 6.0    # timing differences smaller than this aren't worth a tip

STAMINA_HIGH_NPS = 8.0   # notes/s over the last 20s that count as a stamina section
STAMINA_LOW_NPS = 5.0
HIGH_AR = 10.0           # effective AR above this (10 excluded) is high AR: reaction time
LOW_AR = 9.0             # effective AR below this is reading (EZ included); 9 to 10 is normal, no special skill
DENSE_SCREEN = 10        # other objects on screen that make reading hard

PATTERN_CATEGORY = {
    DOUBLE: "streams", TRIPLE: "streams", BURST: "streams", STREAM: "streams", DEATHSTREAM: "streams",
    ALT: "alt", IRREGULAR: "irregular", JUMP: "jumps",
}
PATTERN_NAMES = {
    DOUBLE: "doubles", TRIPLE: "triples", BURST: "bursts", STREAM: "streams", DEATHSTREAM: "deathstreams",
    ALT: "alt patterns", IRREGULAR: "irregular rhythms", JUMP: "jumps",
}


def category_of(pattern: str) -> str:
    return PATTERN_CATEGORY.get(pattern, "aim")


@dataclass
class Sample:
    """One object from one play, flattened for statistics."""
    f: ObjectFeatures
    rate: float
    map_seconds: float
    time_frac: float          # position of the object in the map, 0..1
    play: int = 0
    ar: float = 0.0           # effective (mod- and rate-adjusted) approach rate
    wrong_note: ObjectResult | None = None  # the other note this miss's click landed on
    hidden: bool = False      # played with HD: no approach circles to read

    # what comes from the judged object, which no longer changes, is computed once: the statistics over many plays
    # read these millions of times

    @cached_property
    def r(self) -> ObjectResult:
        return self.f.r

    @property
    def run_id(self) -> tuple[int, int]:
        """Identifies the run (stream, alt...) this object belongs to."""
        return self.play, self.r.obj.index - self.f.run_position

    @cached_property
    def missed(self) -> bool:
        """Missed the click itself (circle or slider head)."""
        return (self.r.head_result == 0) if self.r.obj.kind == SLIDER else (self.r.result == 0)

    @cached_property
    def error(self) -> float | None:
        """Hit error in real ms, only for successful clicks."""
        if self.r.hit_error is None or self.missed:
            return None
        return self.r.hit_error / self.rate

    @cached_property
    def acc_eligible(self) -> bool:
        """Circles hit for 300/100/50 (stable ignores slider head timing for score)."""
        return self.r.obj.kind == CIRCLE and self.r.result in (300, 100, 50)

    @cached_property
    def not_300(self) -> bool:
        return self.r.result in (100, 50)


@dataclass
class Insight:
    title: str
    detail: str
    impact: float  # rough number of misses (or equivalent) this costs; used for sorting
    kind: str = "miss"  # "miss", "accuracy" or "info"
    evidence: dict = field(default_factory=dict)
    category: str = "other"
    # The objects a map needs for this bad habit to be relevant to it (None = the whole category).
    applies_to: Callable[[Sample], bool] | None = None


def is_low_ar(ar: float) -> bool:
    return ar < LOW_AR - 1e-6


def is_high_ar(ar: float) -> bool:
    return ar > HIGH_AR + 1e-6


def effective_ar(diff: Difficulty, rate: float) -> float:
    preempt = diff.preempt / rate
    if preempt > 1200:
        return (1800 - preempt) / 120
    return 5 + (1200 - preempt) / 150


def samples_from_play(feats: list[ObjectFeatures], diff: Difficulty, rate: float, play: int = 0,
                      hidden: bool = False) -> list[Sample]:
    if not feats:
        return []
    start, end = feats[0].r.obj.time, feats[-1].r.obj.end_time
    length = max(end - start, 1)
    ar = effective_ar(diff, rate)
    samples = [Sample(f, rate, length / 1000 / rate, (f.r.obj.time - start) / length, play, ar, hidden=hidden)
               for f in feats]
    _find_wrong_notes(samples, diff)
    return samples


def _find_wrong_notes(samples: list[Sample], diff: Difficulty):
    """Aim misses whose click landed on another visible note: a reading error."""
    times = [s.r.obj.time for s in samples]
    for i, s in enumerate(samples):
        r = s.r
        if not s.missed or r.miss_reason != "aim" or not r.off_target_clicks:
            continue
        t, x, y = min(r.off_target_clicks, key=lambda c: abs(c[0] - r.obj.time))
        lo = bisect.bisect_left(times, t - HITTABLE_RANGE)
        hi = bisect.bisect_right(times, t + diff.preempt)
        for j in range(lo, hi):
            o = samples[j]
            if abs(j - i) < 2 or o.r.obj.time - diff.preempt > t or o.r.obj.end_time < t:
                continue  # neighbours are desync, not misreading
            if s.f.pattern in RUN_PATTERNS and o.run_id == s.run_id:
                continue
            ox, oy = o.r.obj.position
            if math.hypot(x - ox, y - oy) <= diff.radius:
                s.wrong_note = o.r
                break


# --- statistics helpers ------------------------------------------------------

def _z_rates(a: int, n1: int, b: int, n2: int) -> float:
    """z statistic for rate a/n1 being higher than b/n2."""
    if n1 == 0 or n2 == 0:
        return 0.0
    p = (a + b) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (a / n1 - b / n2) / se if se > 0 else 0.0


def _welch_t(x: np.ndarray, y: np.ndarray) -> float:
    if len(x) < 2 or len(y) < 2:
        return 0.0
    se = math.sqrt(x.var(ddof=1) / len(x) + y.var(ddof=1) / len(y))
    return (x.mean() - y.mean()) / se if se > 0 else 0.0


def _worse(slice_: list[Sample], base: list[Sample], bad: Callable[[Sample], bool] = lambda s: s.missed,
           min_bad: int = MIN_MISSES) -> tuple[bool, float, float, float]:
    """Is `slice_` clearly worse than the disjoint `base` on the `bad` outcome?

    Returns (clear, slice rate, base rate, excess bad outcomes in the slice).
    """
    n1, n2 = len(slice_), len(base)
    a, b = sum(map(bad, slice_)), sum(map(bad, base))
    if n1 < MIN_SLICE or n2 < MIN_SLICE or a < min_bad:
        return False, 0.0, 0.0, 0.0
    r1, r2 = a / n1, b / n2
    clear = _z_rates(a, n1, b, n2) >= Z_THRESHOLD and r1 >= 1.5 * r2
    return clear, r1, r2, (r1 - r2) * n1


def _pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _aim_miss_offsets(ss: list[Sample]) -> np.ndarray:
    """Along-the-movement error (radii) of the closest off-target click on aim misses."""
    out = []
    for s in ss:
        r, f = s.r, s.f
        if not s.missed or r.miss_reason != "aim" or f.direction is None or not r.off_target_clicks:
            continue
        t, x, y = min(r.off_target_clicks, key=lambda c: abs(c[0] - r.obj.time))
        px, py = r.obj.position
        out.append(((x - px) * f.direction[0] + (y - py) * f.direction[1]) / f._radius)
    return np.array(out)


class ExpectedModel:
    """Expected miss probability of an object from the player's own rate on its pattern."""

    MIN_BUCKET = 30

    def __init__(self, samples: list[Sample]):
        fine: dict[tuple, list[int]] = {}
        coarse: dict[str, list[int]] = {}
        for s in samples:
            for table, key in ((fine, self.bucket(s)), (coarse, s.f.pattern)):
                t = table.setdefault(key, [0, 0])
                t[0] += s.missed
                t[1] += 1
        total_m = sum(m for m, _ in coarse.values())
        total_n = sum(n for _, n in coarse.values())
        self.overall = total_m / total_n if total_n else 0.0
        self.fine = {k: m / n for k, (m, n) in fine.items() if n >= self.MIN_BUCKET}
        self.coarse = {k: m / n for k, (m, n) in coarse.items() if n >= self.MIN_BUCKET}

    @staticmethod
    def bucket(s: Sample) -> tuple:
        f = s.f
        if f.pattern in RUN_PATTERNS:
            return f.pattern, int(f.bpm // 20)
        if f.pattern == JUMP:
            return f.pattern, min(int(f.distance_radii), 12)
        if f.pattern == IRREGULAR:
            return f.pattern, f.divisor
        return (f.pattern,)

    def p(self, s: Sample) -> float:
        return self.fine.get(self.bucket(s), self.coarse.get(s.f.pattern, self.overall))

    def expected(self, ss: list[Sample]) -> float:
        return sum(self.p(s) for s in ss)


def compare_to_expected(group: list[Sample], reference: list[Sample],
                        model: ExpectedModel) -> tuple[bool, int, float, float]:
    """Does `group` miss clearly more than `reference`, once patterns are accounted for?

    Returns (clear, observed misses, misses expected at the reference's form, ratio).
    """
    exp_g, exp_r = model.expected(group), model.expected(reference)
    obs_g, obs_r = sum(s.missed for s in group), sum(s.missed for s in reference)
    if exp_g <= 0 or exp_r <= 0 or obs_g < MIN_MISSES or obs_r == 0:
        return False, obs_g, 0.0, 0.0
    baseline = exp_g * obs_r / exp_r  # misses if the group were played at the reference's form
    z = (obs_g - baseline) / math.sqrt(baseline) if baseline > 0 else 0.0
    ratio = obs_g / baseline if baseline > 0 else 0.0
    return z >= Z_THRESHOLD and ratio >= 1.3, obs_g, baseline, ratio


# --- rules --------------------------------------------------------------------

def _others(all_s: list[Sample]) -> list[Sample]:
    """Baseline for pattern comparisons: everything that isn't a run."""
    return [s for s in all_s if s.f.pattern not in RUN_PATTERNS and s.f.pattern != IRREGULAR]


def _stream_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    base = _others(all_s)
    family = [s for s in all_s if s.f.pattern in STREAM_FAMILY]

    for pattern in STREAM_FAMILY:
        group = [s for s in family if s.f.pattern == pattern]
        clear, r1, r2, excess = _worse(group, base)
        if clear and len({s.run_id for s in group if s.missed}) >= MIN_EPISODES:
            out.append(Insight(
                f"{PATTERN_NAMES[pattern].capitalize()} are a weak spot",
                f"You miss {_pct(r1)} of notes in {PATTERN_NAMES[pattern]} vs {_pct(r2)} on non-stream patterns.",
                excess, evidence={"miss": r1, "base_miss": r2, "n": len(group)},
                applies_to=lambda s, p=pattern: s.f.pattern == p))

    # BPM ceiling on bursts and longer.
    runs = [s for s in family if s.f.pattern in (BURST,) + LONG_STREAMS]
    for bpm in sorted({int(s.f.bpm // 10 * 10) for s in runs}):
        slower = [s for s in runs if s.f.bpm < bpm]
        fast = [s for s in runs if s.f.bpm >= bpm]
        if len(slower) < MIN_SLICE:
            continue
        clear, r1, r2, excess = _worse(fast, slower)
        if clear and len({s.run_id for s in fast if s.missed}) >= MIN_EPISODES:
            out.append(Insight(
                f"Streams fall apart from ~{bpm} BPM",
                f"At {bpm}+ BPM you miss {_pct(r1)} of burst/stream notes, vs {_pct(r2)} below. "
                f"Practising streams just below and at {bpm} BPM will move this ceiling.",
                excess, evidence={"bpm": bpm, "fast_miss": r1, "slow_miss": r2},
                applies_to=lambda s, bpm=bpm: s.f.pattern in (BURST,) + LONG_STREAMS and s.f.bpm >= bpm))
            break

    # Notelocks inside streams mean cursor and taps out of sync.
    misses = [s for s in family if s.missed]
    locks = [s for s in misses if s.r.miss_reason == "notelock"]
    if len(misses) >= 10 and len(locks) / len(misses) >= 0.3:
        out.append(Insight(
            "Cursor and taps drift apart in streams",
            f"{len(locks)} of your {len(misses)} stream-type misses are notelocks: a tap landed on the next "
            f"note while the previous one was still unhit. Either your cursor runs ahead of your tapping, or "
            f"a tap got lost earlier. Keep the cursor synced to the taps, not to the pattern.",
            len(locks) * 0.5, evidence={"notelocks": len(locks), "misses": len(misses)},
            applies_to=lambda s: s.f.pattern in (BURST,) + LONG_STREAMS))

    # Drift and stamina inside streams and deathstreams.
    long_ = [s for s in family if s.f.pattern in LONG_STREAMS]
    in_long = lambda s: s.f.pattern in LONG_STREAMS
    if len(long_) >= 4 * MIN_SLICE and len({s.run_id for s in long_}) >= MIN_EPISODES:
        head = [s for s in long_ if s.f.run_position < s.f.run_length * 0.25]
        tail = [s for s in long_ if s.f.run_position >= s.f.run_length * 0.75]
        he = np.array([s.error for s in head if s.error is not None])
        te = np.array([s.error for s in tail if s.error is not None])
        if len(he) >= MIN_SLICE and len(te) >= MIN_SLICE and abs(_welch_t(te, he)) >= T_THRESHOLD:
            d = te.mean() - he.mean()
            if abs(d) >= 5:
                what = "speed up (rush)" if d < 0 else "fall behind"
                out.append(Insight(
                    f"You {what} towards the end of streams",
                    f"In streams of 10+ notes your error goes from {he.mean():+.1f}ms in the first quarter to "
                    f"{te.mean():+.1f}ms in the last quarter.",
                    abs(d) / 5 * len(te) / 40, kind="accuracy",
                    evidence={"start_ms": he.mean(), "end_ms": te.mean()}, applies_to=in_long))
        clear, r1, r2, excess = _worse(tail, head)
        if clear and len({s.run_id for s in tail if s.missed}) >= MIN_EPISODES:
            out.append(Insight(
                "Long streams break down at the end",
                f"You miss {_pct(r1)} of notes in the last quarter of streams vs {_pct(r2)} in the first.",
                excess, evidence={"end_miss": r1, "start_miss": r2}, applies_to=in_long))

    # Alternation on fast runs: each note vs the previous one of the same run.
    alt = same = 0
    prev = None
    for s in family:
        if (prev is not None and s.f.run_position > 0 and s.f.bpm >= 190
                and prev.r.obj.index == s.r.obj.index - 1 and s.r.key and prev.r.key):
            if s.r.key != prev.r.key:
                alt += 1
            else:
                same += 1
        prev = s
    if alt + same >= 4 * MIN_SLICE and same / (alt + same) > 0.35:
        out.append(Insight(
            "You don't fully alternate on fast streams",
            f"On runs of 190+ BPM, {_pct(same / (alt + same))} of consecutive notes are hit with the same key. "
            f"Full alternation spreads the load between fingers and usually holds up better at speed.",
            0, kind="info", evidence={"same_key": same, "alternated": alt}))
    return out


def _alt_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    alt = [s for s in all_s if s.f.pattern == ALT]
    clear, r1, r2, excess = _worse(alt, _others(all_s))
    if clear and len({s.run_id for s in alt if s.missed}) >= MIN_EPISODES:
        out.append(Insight(
            "Alt patterns are a weak spot",
            f"You miss {_pct(r1)} of notes in alt sections (spaced 1/4 at 120-180 BPM) vs {_pct(r2)} on "
            f"non-run patterns. Alt needs aim and tapping at the same time: practise alt maps (e.g. Prayer, "
            f"Running in the 90s) slightly below your comfort BPM.",
            excess, evidence={"miss": r1, "base_miss": r2}))

    inner = [s for s in alt if s.f.run_position > 0 and s.f.angle is not None]
    sharp = [s for s in inner if s.f.angle < 90]
    flowing = [s for s in inner if s.f.angle >= 90]
    clear, r1, r2, excess = _worse(sharp, flowing)
    if clear:
        out.append(Insight(
            "Direction changes break your alt",
            f"In alt sections you miss {_pct(r1)} of notes after a sharp change of direction vs {_pct(r2)} "
            f"when the pattern flows. Focus on keeping the rhythm while the cursor turns.",
            excess, evidence={"sharp_miss": r1, "flow_miss": r2},
            applies_to=lambda s: s.f.pattern == ALT and s.f.angle is not None and s.f.angle < 90))

    wide = [s for s in alt if s.f.run_position > 0 and s.f.distance_radii >= 2.5]
    narrow = [s for s in alt if s.f.run_position > 0 and s.f.distance_radii < 2.5]
    clear, r1, r2, excess = _worse(wide, narrow)
    if clear:
        offs = _aim_miss_offsets(wide)
        how = ""
        if len(offs) >= 10 and (offs < 0).mean() >= 0.65:
            how = f" {_pct((offs < 0).mean())} of those misses land short: the cursor lags behind the rhythm."
        out.append(Insight(
            "Wide alt spacing",
            f"You miss {_pct(r1)} of alt notes spaced 2.5+ radii apart vs {_pct(r2)} on tighter alt.{how}",
            excess, evidence={"wide_miss": r1, "narrow_miss": r2},
            applies_to=lambda s: s.f.pattern == ALT and s.f.distance_radii >= 2.5))
    return out


def _jump_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    jumps = [s for s in all_s if s.f.pattern == JUMP]
    if len(jumps) < MIN_SLICE:
        return out

    reported = False
    for lo in (2.5, 4, 6, 8, 11):
        wide = [s for s in jumps if s.f.distance_radii >= lo]
        narrow = [s for s in jumps if s.f.distance_radii < lo]
        clear, r1, r2, excess = _worse(wide, narrow)
        if not clear:
            continue
        offs = _aim_miss_offsets(wide)
        how = ""
        if len(offs) >= 10:
            short = (offs < 0).mean()
            if short >= 0.65:
                how = f" When you miss them, {_pct(short)} of the time you land short of the circle: commit to the full distance."
            elif short <= 0.35:
                how = f" When you miss them, {_pct(1 - short)} of the time you overshoot: aim to stop on the circle, not past it."
        out.append(Insight(
            f"Jumps wider than {lo:g} circle radii",
            f"You miss {_pct(r1)} of jumps from {lo:g} radii up, vs {_pct(r2)} on smaller jumps.{how}",
            excess, evidence={"min_radii": lo, "wide_miss": r1, "narrow_miss": r2},
            applies_to=lambda s, lo=lo: s.f.pattern == JUMP and s.f.distance_radii >= lo))
        reported = True
        break

    if not reported:
        offs = _aim_miss_offsets(jumps)
        if len(offs) >= 20:
            short = (offs < 0).mean()
            if short >= 0.7 or short <= 0.3:
                word = "short (undershoot)" if short >= 0.7 else "long (overshoot)"
                out.append(Insight(
                    f"Your jump misses land {word}",
                    f"On {len(offs)} missed jumps you clicked {word} {_pct(max(short, 1 - short))} of the time.",
                    len(offs) * 0.3, evidence={"short_share": short}))

    is_sharp = lambda s: s.f.pattern == JUMP and s.f.angle is not None and s.f.angle < 60
    is_wide = lambda s: s.f.pattern == JUMP and s.f.angle is not None and s.f.angle >= 110
    sharp, wide_a = [s for s in jumps if is_sharp(s)], [s for s in jumps if is_wide(s)]
    for a, b, name, tip, pred in (
            (sharp, wide_a, "sharp-angle (back-and-forth)",
             "Practise maps with lots of back-and-forth jumps and focus on stopping and reversing.", is_sharp),
            (wide_a, sharp, "wide-angle / linear",
             "Linear and wide-angle jumps need you to keep moving through the circle; practise flowing jumps.",
             is_wide)):
        clear, r1, r2, excess = _worse(a, b)
        if clear:
            out.append(Insight(f"Weaker on {name} jumps",
                               f"You miss {_pct(r1)} of them vs {_pct(r2)} on the opposite kind. {tip}",
                               excess, evidence={"miss": r1, "other_miss": r2}, applies_to=pred))
    return out


def _irregular_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    irr = [s for s in all_s if s.f.pattern == IRREGULAR]
    base = _others(all_s)
    clear, r1, r2, excess = _worse(irr, base)
    if clear and len({s.play for s in irr if s.missed}) >= MIN_EPISODES:
        out.append(Insight(
            "Irregular rhythms (1/3, 1/6...) cost you misses",
            f"You miss {_pct(r1)} of notes on triplet or odd snaps vs {_pct(r2)} on regular rhythms. "
            f"Listen for the rhythm change before it comes instead of tapping the usual 1/2 or 1/4.",
            excess, evidence={"miss": r1, "base_miss": r2}))

    ie = np.array([s.error for s in irr if s.error is not None])
    be = np.array([s.error for s in base if s.error is not None])
    if len(ie) >= MIN_SLICE and len(be) >= MIN_SLICE and abs(_welch_t(ie, be)) >= T_THRESHOLD:
        d = ie.mean() - be.mean()
        if abs(d) >= 5:
            side = "early" if d < 0 else "late"
            out.append(Insight(
                f"You hit irregular rhythms {side}",
                f"On triplet/odd snaps your average error is {ie.mean():+.1f}ms vs {be.mean():+.1f}ms on regular "
                f"rhythms: you {'anticipate' if d < 0 else 'react late to'} the rhythm change.",
                abs(d) / 5 * len(ie) / 40, kind="accuracy", evidence={"irregular_ms": ie.mean()}))
    return out


def _slider_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    sliders = [s for s in all_s if s.r.obj.kind == SLIDER and not s.missed]
    if len(sliders) < MIN_SLICE:
        return out
    breaks = [s for s in sliders if s.r.slider_break_kind in ("tick", "repeat")]
    ends = [s for s in sliders if s.r.slider_break_kind == "end"]
    if len(breaks) >= MIN_MISSES and len(breaks) / len(sliders) >= 0.01:
        out.append(Insight(
            "Slider breaks",
            f"{len(breaks)} of {len(sliders)} sliders broke combo on a tick or repeat ({_pct(len(breaks) / len(sliders))}). "
            f"Keep the cursor on the ball until the slider is over, especially on repeats.",
            len(breaks), evidence={"breaks": len(breaks), "sliders": len(sliders)}))
    if len(ends) >= MIN_MISSES and len(ends) / len(sliders) >= 0.03:
        out.append(Insight(
            "You leave sliders too early",
            f"On {len(ends)} sliders ({_pct(len(ends) / len(sliders))}) you released or left the follow circle before "
            f"the end. It doesn't break combo, but each one turns a 300 into a 100.",
            len(ends) / 3, kind="accuracy", evidence={"end_drops": len(ends)}))
    return out


def _reading_rules(all_s: list[Sample], model: ExpectedModel) -> list[Insight]:
    out = []
    misses = [s for s in all_s if s.missed]
    wrong = [s for s in misses if s.wrong_note is not None]
    if len(wrong) >= 10 and len(wrong) / max(len(misses), 1) >= 0.05 and len({s.play for s in wrong}) >= MIN_EPISODES:
        out.append(Insight(
            "You misread note order",
            f"{len(wrong)} of your {len(misses)} misses ({_pct(len(wrong) / len(misses))}) are clicks on a different "
            f"note that was on screen at the time. That is reading, not aim: look for the next number and the "
            f"approach circle closing first, not just the nearest circle.",
            len(wrong), evidence={"wrong_note": len(wrong)}))

    dense = [s for s in all_s if s.f.visible >= DENSE_SCREEN]
    calm = [s for s in all_s if s.f.visible < DENSE_SCREEN // 2]
    clear, obs, base, ratio = compare_to_expected(dense, calm, model)
    if clear and len({s.play for s in dense if s.missed}) >= MIN_EPISODES:
        out.append(Insight(
            "Busy screens hurt you",
            f"With {DENSE_SCREEN}+ notes on screen you miss {ratio:.1f}x what the same patterns cost you on "
            f"calmer screens ({obs} misses where ~{base:.0f} were expected).",
            obs - base, evidence={"observed": obs, "expected": base},
            applies_to=lambda s: s.f.visible >= DENSE_SCREEN))
    return out


def _accuracy_rules(all_s: list[Sample]) -> list[Insight]:
    out = []
    acc = [s for s in all_s if s.acc_eligible]
    errs = np.array([s.error for s in acc])
    if len(errs) < 200:
        return out
    mean, sd = errs.mean(), errs.std()
    t = mean / (sd / math.sqrt(len(errs)))
    if abs(mean) >= 4 and abs(t) >= T_THRESHOLD * 2:
        side = "late" if mean > 0 else "early"
        out.append(Insight(
            f"You hit {abs(mean):.1f}ms {side} on average",
            f"Across {len(errs)} hits your mean error is {mean:+.1f}ms (UR {sd * 10:.0f}). A constant bias like "
            f"this is usually offset, not skill: if it shows up on most maps, recalibrate your universal offset.",
            abs(mean) / sd * len(errs) / 50, kind="accuracy", evidence={"mean_ms": mean}))

    groups: dict[str, list[Sample]] = {}
    for s in acc:
        groups.setdefault(category_of(s.f.pattern), []).append(s)
    for cat, group in groups.items():
        rest = [s for s in acc if category_of(s.f.pattern) != cat]
        name = {"streams": "streams", "alt": "alt", "irregular": "irregular rhythms", "jumps": "jumps",
                "aim": "other notes"}[cat]
        in_cat = lambda s, c=cat: category_of(s.f.pattern) == c
        clear, r1, r2, excess = _worse(group, rest, bad=lambda s: s.not_300, min_bad=10)
        ge = np.array([s.error for s in group])
        re_ = np.array([s.error for s in rest])
        d = ge.mean() - re_.mean() if len(ge) and len(re_) else 0.0
        biased = (len(ge) >= MIN_SLICE and len(re_) >= MIN_SLICE and abs(_welch_t(ge, re_)) >= T_THRESHOLD
                  and abs(d) >= MIN_BIAS_MS)
        # off time or spread out: which adds more to the error, the shifted mean or the wider spread
        timing = biased and d ** 2 > ge.var() - re_.var()
        offset = (f" Your hits there are off time, not spread out: {abs(ge.mean()):.0f}ms {'early' if d < 0 else 'late'} "
                  f"on average ({ge.mean():+.1f}ms vs {re_.mean():+.1f}ms elsewhere). Try an offset that moves your "
                  f"hits about {abs(ge.mean()):.0f}ms {'later' if d < 0 else 'earlier'} on maps like these.")
        spread = (f" Your timing there is centred ({ge.mean():+.1f}ms) but spread out: it's steadiness, "
                  f"train accuracy on {name}.")
        if clear:
            out.append(Insight(
                f"Accuracy drops on {name}",
                f"{_pct(r1)} of your hits on {name} are 100s or 50s vs {_pct(r2)} elsewhere "
                f"(UR {ge.std() * 10:.0f} vs {re_.std() * 10:.0f}).{offset if timing else spread}",
                excess / 3, kind="accuracy",
                evidence={"non300": r1, "base_non300": r2, "cause": "timing" if timing else "spread",
                          "mean_ms": float(ge.mean())}, applies_to=in_cat))
        elif biased:
            out.append(Insight(f"Timing bias on {name}", offset.strip(), len(group) / 200, kind="accuracy",
                               evidence={"cause": "timing", "mean_ms": float(ge.mean())}, applies_to=in_cat))
    return out


def _stamina_rules(all_s: list[Sample], model: ExpectedModel) -> list[Insight]:
    """Misses in sustained dense sections beyond what their patterns explain."""
    high = [s for s in all_s if s.f.load_nps >= STAMINA_HIGH_NPS]
    low = [s for s in all_s if s.f.load_nps < STAMINA_LOW_NPS]
    if len({s.play for s in high}) < MIN_EPISODES:
        return []
    clear, obs, base, ratio = compare_to_expected(high, low, model)
    if not clear:
        return []
    return [Insight(
        "Stamina: dense sections wear you down",
        f"After 20s of {STAMINA_HIGH_NPS:g}+ notes per second you miss {ratio:.1f}x what the same patterns cost you "
        f"in lighter sections ({obs} misses where ~{base:.0f} were expected).",
        obs - base, evidence={"observed": obs, "expected": base},
        applies_to=lambda s: s.f.load_nps >= STAMINA_HIGH_NPS)]


def _dt_rules(all_s: list[Sample]) -> list[Insight]:
    """High AR (DT) plays vs the same patterns at normal AR."""
    high = [s for s in all_s if s.ar > HIGH_AR]
    normal = [s for s in all_s if s.ar <= HIGH_AR]
    if len({s.play for s in high}) < MIN_EPISODES or len(normal) < MIN_SLICE:
        return []
    clear, obs, base, ratio = compare_to_expected(high, normal, ExpectedModel(all_s))
    if not clear:
        return []
    return [Insight(
        "High AR (DT) costs you",
        f"On plays with AR above {HIGH_AR:g} you miss {ratio:.1f}x what the same patterns cost you at normal AR "
        f"({obs} misses where ~{base:.0f} were expected). Reading and reaction time are the limit, not the patterns.",
        obs - base, evidence={"observed": obs, "expected": base})]


def _miss_cause_summary(all_s: list[Sample]) -> list[Insight]:
    misses = [s for s in all_s if s.missed]
    if len(misses) < 10:
        return []
    counts: dict[str, int] = {}
    for s in misses:
        reason = "wrong_note" if s.wrong_note is not None else (s.r.miss_reason or "no_click")
        counts[reason] = counts.get(reason, 0) + 1
    names = {"aim": "aim (clicked in time, off the circle)",
             "wrong_note": "misread (clicked another note on screen)",
             "notelock": "notelock (tapped the next note while this one was pending)",
             "timing": "timing (clicked the circle too early)",
             "no_click": "no click in range"}
    parts = ", ".join(f"{names[k]} {_pct(v / len(misses))}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
    return [Insight("Why you miss", f"Of {len(misses)} misses: {parts}.", 0, kind="info", evidence=counts)]


def build_insights(samples: list[Sample]) -> list[Insight]:
    model = ExpectedModel(samples)
    rules = (
        ("streams", lambda: _stream_rules(samples)),
        ("alt", lambda: _alt_rules(samples)),
        ("jumps", lambda: _jump_rules(samples)),
        ("irregular", lambda: _irregular_rules(samples)),
        ("sliders", lambda: _slider_rules(samples)),
        ("reading", lambda: _reading_rules(samples, model)),
        ("accuracy", lambda: _accuracy_rules(samples)),
        ("stamina", lambda: _stamina_rules(samples, model)),
        ("dt", lambda: _dt_rules(samples)),
        ("info", lambda: _miss_cause_summary(samples)),
    )
    found = []
    for category, rule in rules:
        for insight in rule():
            insight.category = "info" if insight.kind == "info" else category
            found.append(insight)
    found.sort(key=lambda i: -i.impact)
    return found
