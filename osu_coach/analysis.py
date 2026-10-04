"""Aggregate statistics over judged objects."""

import math
from collections import Counter
from dataclasses import dataclass

import numpy as np

from .beatmap import SLIDER, SPINNER
from .judge import ObjectResult


@dataclass
class Summary:
    counts: Counter            # 300/100/50/0 over all played objects
    unstable_rate: float       # in real time (DT/HT corrected)
    mean_error: float          # ms, negative = early
    mean_early: float
    mean_late: float
    aim_mean_distance: float   # mean cursor distance from centre, in radii
    miss_reasons: Counter
    slider_breaks: int
    spinners: int


def timed_hit(r: ObjectResult) -> bool:
    """A click that counts for the UR and the mean hit error: a circle or slider head that was hit. A slider whose
    head was clicked too early or late is left out, even when the slider still scored."""
    if r.hit_error is None:
        return False
    return bool(r.head_result if r.obj.kind == SLIDER else r.result)


def combo_breaks(results) -> dict:
    """Why a play with no miss isn't a full combo. sb: combo breaks that aren't misses, slider heads missed on
    sliders that still scored and dropped ticks or repeats (the "Break" rows of the replay page's mistake list);
    se: dropped slider ends, which keep the combo but miss its +1, so the play isn't a full combo either."""
    sliders = [r for r in results if r.played and r.obj.kind == SLIDER]
    return {"sb": sum(1 for r in sliders if r.head_result == 0 and r.result != 0
                      or r.head_result != 0 and r.slider_break_kind in ("tick", "repeat")),
            "se": sum(1 for r in sliders if r.head_result != 0 and r.slider_break_kind == "end")}


def summarize(results: list[ObjectResult], radius: float, rate: float = 1.0) -> Summary:
    counts = Counter()
    miss_reasons = Counter()
    spinners = 0
    slider_breaks = 0
    for r in results:
        if not r.played:
            continue
        counts[r.result] += 1
        if r.obj.kind == SPINNER:
            spinners += 1
            continue
        if r.miss_reason and (r.result == 0 or r.head_result == 0):
            miss_reasons[r.miss_reason] += 1
        # Dropping the end only costs accuracy; a tick or repeat breaks combo.
        if r.obj.kind == SLIDER and r.slider_break_kind in ("tick", "repeat"):
            slider_breaks += 1

    errors = np.array([r.hit_error for r in results if r.played and timed_hit(r)], dtype=float)
    offsets = [r.aim_offset for r in results if r.played and r.aim_offset is not None]
    dist = np.array([math.hypot(*o) / radius for o in offsets]) if offsets else np.array([])

    return Summary(
        counts=counts,
        unstable_rate=float(errors.std() * 10 / rate) if len(errors) else 0.0,
        mean_error=float(errors.mean() / rate) if len(errors) else 0.0,
        mean_early=float(errors[errors < 0].mean() / rate) if (errors < 0).any() else 0.0,
        mean_late=float(errors[errors >= 0].mean() / rate) if (errors >= 0).any() else 0.0,
        aim_mean_distance=float(dist.mean()) if len(dist) else 0.0,
        miss_reasons=miss_reasons,
        slider_breaks=slider_breaks,
        spinners=spinners,
    )
