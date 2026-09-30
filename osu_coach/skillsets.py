"""The Skills section: plain statistics per skillset from the recent plays, as the player asked for them.

- streams: per length range, from 160 BPM: the comfortable BPM (the best BPM^0.9 / UR), the lowest BPM whose UR
  is within STREAM_UR_TOLERANCE of the comfortable one, and the highest BPM whose streams are still finished (half
  of them cleared, whatever the UR); minimum <= comfort <= maximum
- alt: UR per 10 BPM on truly alternated runs, 120 to 170 BPM
- finger control: miss rate and UR for doubles, triples, quadruples and bursts up to 9 notes
- jumps: mean click distance from the circle centre per distance range, and the highest BPM played cleanly
- flow aim: misses due to aim per spacing range
- sliders: head misses, dropped ticks and dropped ends per slider speed
Stamina, high AR, reading and accuracy stay the levels of skills.py.
"""

import math
from collections import defaultdict

import numpy as np

from .advice import Sample
from .beatmap import CIRCLE, SLIDER
from .features import FIRST, STREAM_FAMILY
from .skills import _isotonic

STREAM_LENGTHS = ((9, 16), (17, 32), (33, None))
STREAM_START_BPM = 160
STREAM_UR_TOLERANCE = 0.10   # minimum BPM: UR within this of the UR at the comfort BPM (a setting)
COMFORT_BPM_POWER = 0.90     # comfort = the best BPM^power / UR: a little in favour of lower BPM
STREAM_CLEARED = 0.5         # the highest BPM whose streams are finished this often
MIN_BIN_HITS = 60            # hit notes a 10-BPM bin needs for its UR
MIN_BIN_RUNS = 5             # runs a 10-BPM bin needs for its clear rate
MIN_UR_PLAYS = 3             # ...and plays for its UR (one map shouldn't make a speed look easy or hard)
ALT_BPM = (120, 170)
FINGER_COUNTS = range(2, 10)
FINGER_NAMES = {2: "doubles", 3: "triples", 4: "quadruples"}
JUMP_RANGES = ((3.0, 6.0, "small jumps"), (6.0, 8.0, "normal jumps"), (8.0, None, "large jumps"))
JUMP_MAX_GAP_MS = 300.0      # 1/2 at 100 BPM: slower notes aren't jumps
JUMP_CLEAN_MISS = 0.05       # the highest 1/2 BPM with at most this miss rate
MIN_JUMP_BIN = 30
FLOW_RANGES = ((0.3, 1.5, "normal"), (1.5, 3.0, "spaced"))   # below 0.3 radii the notes are stacked: no aim
FLOW_MAX_GAP_MS = 200.0
SLIDER_SPEEDS = ((0, 10), (10, 20), (20, 30), (30, 45), (45, None))   # circle radii per second


def _errors(notes: list[Sample]) -> list[float]:
    """Hit errors around each play's own mean on these notes: the UR is how steady the hits are, as in the game,
    while a constant early/late shift on a pattern is timing (the accuracy advice), and plays with different
    offsets don't add up into a UR nobody played."""
    by_play: dict[int, list[float]] = defaultdict(list)
    for s in notes:
        if s.error is not None:
            by_play[s.play].append(s.error)
    return [e - m for errors in by_play.values() for m in [float(np.mean(errors))] for e in errors]


def _ur(errors) -> float | None:
    return float(10 * np.sqrt(np.mean(np.square(errors)))) if len(errors) >= 2 else None


def _bins(values, lo: float, hi: float | None, step: float) -> list[tuple[float, float]]:
    top = hi if hi is not None else (math.floor(max(values, default=lo) / step) + 1) * step
    return [(b, b + step) for b in np.arange(lo, top, step)]


def _in(x: float, lo: float, hi: float | None) -> bool:
    return x >= lo and (hi is None or x < hi)


def _runs(samples: list[Sample], keep) -> dict[tuple, list[Sample]]:
    runs: dict[tuple, list[Sample]] = defaultdict(list)
    for s in samples:
        if keep(s):
            runs[s.run_id].append(s)
    return runs


def streams(samples: list[Sample], ur_tolerance: float = STREAM_UR_TOLERANCE) -> dict:
    runs = _runs(samples, lambda s: s.f.pattern in STREAM_FAMILY and s.f.run_length >= STREAM_LENGTHS[0][0]
                 and s.f.bpm and s.f.bpm >= STREAM_START_BPM)
    out = {"ur_tolerance": ur_tolerance, "groups": []}
    for lo_len, hi_len in STREAM_LENGTHS:
        group = [r for r in runs.values() if _in(r[0].f.run_length, lo_len, None if hi_len is None else hi_len + 1)]
        label = f"{lo_len}-{hi_len} notes" if hi_len else f"{lo_len}+ notes"
        g = {"label": label, "runs": len(group), "bins": [], "min": None, "comfort": None, "max": None}
        out["groups"].append(g)
        for lo, hi in _bins([r[0].f.bpm for r in group], STREAM_START_BPM, None, 10):
            rs = [r for r in group if lo <= r[0].f.bpm < hi]
            errors = _errors([s for r in rs for s in r])
            if not rs:
                continue
            g["bins"].append({"lo": lo, "hi": hi, "runs": len(rs), "notes": sum(len(r) for r in rs),
                              "ur": _ur(errors) if len(errors) >= MIN_BIN_HITS and len({r[0].play for r in rs}) >= MIN_UR_PLAYS else None,
                              "hits": len(errors),
                              "cleared": sum(not any(s.missed for s in r) for r in rs) / len(rs)})
    for g in out["groups"]:
        timed = [b for b in g["bins"] if b["ur"] is not None]
        counted = [b for b in g["bins"] if b["runs"] >= MIN_BIN_RUNS]
        centre = lambda b: (b["lo"] + b["hi"]) / 2
        # maximum: the fastest speed whose streams are still finished half the time, whatever the UR
        cleared = [b for b in counted if b["cleared"] >= STREAM_CLEARED]
        if counted:
            g["max"] = ({"bpm": centre(cleared[-1]), "censored": "above" if cleared[-1] is counted[-1] else ""}
                        if cleared else {"bpm": centre(counted[0]), "censored": "below"})
        if len(timed) < 2:    # one speed with enough plays: nothing to compare it with
            g["note"] = "not enough streams from different plays at different speeds"
            continue
        fitted = _isotonic([b["ur"] for b in timed], [b["hits"] for b in timed])
        for b, f in zip(timed, fitted):
            b["fitted"] = f
        # comfort: the best BPM^0.9 / UR among the speeds the streams are still finished at
        top = g["max"]["bpm"] if g["max"] and g["max"]["censored"] != "above" else math.inf
        best = max([b for b in timed if centre(b) <= top] or timed[:1],
                   key=lambda b: centre(b) ** COMFORT_BPM_POWER / b["fitted"])
        g["comfort"] = {"bpm": centre(best), "censored": ""}
        # minimum: the slowest speed (from 160 up to the comfort one) whose UR is within the tolerance of the
        # UR at the comfort speed
        g["comfort_ur"] = ref = best["fitted"]
        ok = [b for b in timed if abs(b["fitted"] - ref) <= ur_tolerance * ref and centre(b) <= centre(best)]
        g["min"] = {"bpm": centre(ok[0]), "censored": ""}
    return out


def alt(samples: list[Sample]) -> dict:
    from .maptypes import _run_kind
    notes = [s for s in samples if s.f.bpm and _run_kind(s.f) == "alt"]
    rows = []
    for lo, hi in _bins([], ALT_BPM[0], ALT_BPM[1], 10):
        ns = [s for s in notes if lo <= s.f.bpm < hi]
        errors = _errors(ns)
        rows.append({"lo": lo, "hi": hi, "notes": len(ns), "runs": len({s.run_id for s in ns}),
                     "ur": _ur(errors) if len(errors) >= MIN_BIN_HITS else None,
                     "miss": sum(s.missed for s in ns) / len(ns) if ns else None})
    return {"rows": rows}


def finger(samples: list[Sample]) -> dict:
    rows = []
    for n in FINGER_COUNTS:
        ns = [s for s in samples if s.f.pattern in STREAM_FAMILY and s.f.run_length == n]
        errors = _errors(ns)
        rows.append({"count": n, "label": FINGER_NAMES.get(n, f"{n}-note bursts"), "notes": len(ns),
                     "runs": len({s.run_id for s in ns}), "miss": sum(s.missed for s in ns) / len(ns) if ns else None,
                     "ur": _ur(errors) if len(errors) >= MIN_BIN_HITS else None})
    return {"rows": rows}


def _click_distance(s: Sample) -> float | None:
    """How far from the circle centre the click was, in radii (a miss: its closest click)."""
    r = s.r
    if r.cursor is not None:
        x, y = r.cursor
    elif r.off_target_clicks and s.wrong_note is None:
        _, x, y = min(r.off_target_clicks, key=lambda c: abs(c[0] - r.obj.time))
    else:
        return None
    px, py = r.obj.position
    return math.hypot(x - px, y - py) / s.f._radius


def _aimed(s: Sample) -> bool:
    return s.f.pattern != FIRST and s.r.obj.kind in (CIRCLE, SLIDER)


def jumps(samples: list[Sample]) -> dict:
    rows = []
    for lo, hi, label in JUMP_RANGES:
        js = [s for s in samples if _aimed(s) and _in(s.f.distance_radii, lo, hi) and 0 < s.f.gap_ms <= JUMP_MAX_GAP_MS]
        dist = [d for d in map(_click_distance, js) if d is not None]
        bpm = np.array([30000 / s.f.gap_ms for s in js])   # as 1/2 notes
        missed = np.array([s.missed for s in js], dtype=bool)
        best = None
        for b_lo, b_hi in _bins(bpm, math.floor(bpm.min() / 10) * 10 if len(bpm) else 0, None, 10):
            m = (bpm >= b_lo) & (bpm < b_hi)
            if m.sum() >= MIN_JUMP_BIN and missed[m].mean() <= JUMP_CLEAN_MISS:
                best = b_hi
        rows.append({"label": label, "lo": lo, "hi": hi, "jumps": len(js),
                     "distance": float(np.mean(dist)) if dist else None,
                     "miss": float(missed.mean()) if len(js) else None,
                     "max_bpm": best, "played_bpm": float(np.percentile(bpm, 95)) if len(bpm) else None})
    return {"rows": rows}


def flow(samples: list[Sample]) -> dict:
    rows = []
    for lo, hi, label in FLOW_RANGES:
        ns = [s for s in samples if _aimed(s) and _in(s.f.distance_radii, lo, hi) and 0 < s.f.gap_ms <= FLOW_MAX_GAP_MS]
        aim = sum(s.missed and s.r.miss_reason == "aim" for s in ns)
        rows.append({"label": label, "lo": lo, "hi": hi, "notes": len(ns),
                     "aim_miss": aim / len(ns) if ns else None, "aim_misses": aim,
                     "miss": sum(s.missed for s in ns) / len(ns) if ns else None})
    return {"rows": rows}


def sliders(samples: list[Sample]) -> dict:
    ss = [s for s in samples if s.r.obj.kind == SLIDER and s.r.obj.span_duration > 0]

    def speed(s):
        return s.r.obj.path.length / s.f._radius / (s.r.obj.span_duration / s.rate / 1000)
    rows = []
    for lo, hi in SLIDER_SPEEDS:
        group = [s for s in ss if _in(speed(s), lo, hi)]
        held = [s for s in group if not s.missed and s.r.ticks_total > 0]
        rows.append({"lo": lo, "hi": hi, "sliders": len(group),
                     "head_miss": sum(s.missed for s in group) / len(group) if group else None,
                     "tick_miss": sum(s.r.slider_break_kind in ("tick", "repeat") for s in held) / len(held) if held else None,
                     "end_miss": sum(s.r.slider_break_kind == "end" for s in held) / len(held) if held else None})
    return {"rows": rows}


def build(samples: list[Sample], stream_ur_tolerance: float = STREAM_UR_TOLERANCE) -> dict:
    return {"streams": streams(samples, stream_ur_tolerance), "alt": alt(samples), "finger": finger(samples), "jumps": jumps(samples),
            "flow": flow(samples), "sliders": sliders(samples),
            "plays": len({s.play for s in samples}), "objects": len(samples)}
