"""Key press/release analysis: chatter (ghost re-presses) and keys that don't reset.

These are the symptoms that point at keyboard settings rather than skill:
- a key released and pressed again within a few ms is not a finger, it is the
  switch or a rapid trigger sensitivity set too fine;
- a stream note with no tap while a key stayed down since the previous note
  means the key never reset between taps (rapid trigger release distance too
  large, or the finger not lifting enough on a normal switch).
"""

from dataclasses import dataclass, field

from .judge import LEFT, RIGHT, ObjectResult
from .replay import K1, K2, Frame

CHATTER_GAP_MS = 10   # release -> press on the same key faster than this is not a finger


@dataclass
class KeyPress:
    side: int       # LEFT or RIGHT
    name: str       # "K1", "K2", "M1", "M2"
    down: int
    up: int | None = None

    @property
    def hold(self) -> int | None:
        return None if self.up is None else self.up - self.down


@dataclass
class TapStats:
    """Per-play key behaviour."""
    presses: int = 0
    ghost_presses: list[KeyPress] = field(default_factory=list)   # chatter / over-sensitive RT
    ghost_hits: list[ObjectResult] = field(default_factory=list)  # objects a ghost press hit (early)
    stuck_misses: list[tuple[ObjectResult, KeyPress]] = field(default_factory=list)  # note lost to a key held down
    run_notes: int = 0    # notes in streams/alt, the denominator for stuck misses


def key_presses(frames: list[Frame]) -> list[KeyPress]:
    presses: list[KeyPress] = []
    open_: dict[int, KeyPress] = {}
    for f in frames:
        for side, down in ((LEFT, f.left), (RIGHT, f.right)):
            if down and side not in open_:
                name = ("K1" if f.keys & K1 else "M1") if side == LEFT else ("K2" if f.keys & K2 else "M2")
                open_[side] = KeyPress(side, name, f.time)
                presses.append(open_[side])
            elif not down and side in open_:
                open_.pop(side).up = f.time
    return presses


def held_at(presses: list[KeyPress], t: float) -> list[KeyPress]:
    return [p for p in presses if p.down <= t and (p.up is None or p.up > t)]


def tap_stats(frames: list[Frame], results: list[ObjectResult], run_indices: set[int]) -> TapStats:
    """`run_indices`: objects that are part of streams/alt, where stuck keys matter."""
    presses = key_presses(frames)
    played = [r for r in results if r.played]
    if not played or not presses:
        return TapStats()
    start, end = played[0].obj.time - 1000, played[-1].obj.end_time
    stats = TapStats()

    hit_by = {(r.hit_time, r.key): r for r in played if r.hit_time is not None}
    last_up: dict[int, int] = {}
    for p in presses:
        if not start <= p.down <= end:
            if p.up is not None:
                last_up[p.side] = p.up
            continue
        stats.presses += 1
        prev_up = last_up.get(p.side)
        if prev_up is not None and p.down - prev_up <= CHATTER_GAP_MS:
            stats.ghost_presses.append(p)
            r = hit_by.get((p.down, p.side))
            if r is not None:
                stats.ghost_hits.append(r)
        if p.up is not None:
            last_up[p.side] = p.up

    ordered = [r for r in played if r.obj.kind != "spinner"]
    for prev, r in zip(ordered, ordered[1:]):
        if r.obj.index not in run_indices:
            continue
        stats.run_notes += 1
        if r.miss_reason != "no_click" or r.result is None:
            continue
        missed = r.head_result == 0 if r.obj.kind == "slider" else r.result == 0
        if not missed:
            continue
        # A key pressed for the previous note (or earlier) and still down now never reset.
        halfway = prev.obj.time + (r.obj.time - prev.obj.time) / 2
        for p in held_at(presses, r.obj.time):
            if p.down <= halfway:
                stats.stuck_misses.append((r, p))
                break
    return stats
