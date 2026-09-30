"""Mod-adjusted difficulty values, following osu! stable (see danser's difficulty.go)."""

import math
import struct
from dataclasses import dataclass

from .mods import Mods, clock_rate

HITTABLE_RANGE = 400  # clicks further than this from an object's time only shake it


def f32(x: float) -> float:
    return struct.unpack("<f", struct.pack("<f", x))[0]


def difficulty_rate(diff: float, min_v: float, mid_v: float, max_v: float) -> float:
    diff = f32(diff)
    if diff > 5:
        return mid_v + (max_v - mid_v) * (diff - 5) / 5
    if diff < 5:
        return mid_v - (mid_v - min_v) * (5 - diff) / 5
    return mid_v


@dataclass
class Difficulty:
    cs: float
    ar: float
    od: float
    radius: float
    preempt: int
    hit300: int
    hit100: int
    hit50: int
    spinner_ratio: float  # spins per second needed to clear a spinner
    speed: float          # clock rate (DT 1.5, HT 0.75)

    @classmethod
    def from_map(cls, cs: float, ar: float, od: float, mods: int) -> "Difficulty":
        if mods & Mods.HardRock:
            cs = min(cs * 1.3, 10)
            ar = min(ar * 1.4, 10)
            od = min(od * 1.4, 10)
        if mods & Mods.Easy:
            cs, ar, od = cs / 2, ar / 2, od / 2

        return cls(
            cs=cs, ar=ar, od=od,
            radius=difficulty_rate(cs, 54.4, 32, 9.6) * 1.00041,  # stable's small allowance
            preempt=math.floor(difficulty_rate(ar, 1800, 1200, 450)),
            hit300=int(difficulty_rate(od, 80, 50, 20)),
            hit100=int(difficulty_rate(od, 140, 100, 60)),
            hit50=int(difficulty_rate(od, 200, 150, 100)),
            spinner_ratio=difficulty_rate(od, 3, 5, 7.5),
            speed=clock_rate(mods),
        )
