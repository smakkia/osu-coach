"""Per-object movement/rhythm features and pattern classification.

All times are in real (clock-rate corrected) milliseconds and distances in
osu!pixels (plus radii, so plays with different circle sizes compare).

Patterns
--------
Speeds are always taken from real time between notes, never from the map's
BPM (mappers may time a song at double or half its tempo). "BPM" below means
the tempo at which the gap would be a 1/4 note: 15000 / gap_ms.

Runs of evenly timed notes (gaps of 125ms or less) are classified by length, speed and spacing:
  - runs spaced more than ALT_MAX_SPACING_RADII are jumps, not runs, at any speed
  - 2 notes: double, 3: triple
  - 4+ notes at 180+ BPM, or 165-180 BPM within STREAM_SPACING_CAP_RADII:
    burst (4-9), stream (10-33), deathstream (34+)
  - 4+ notes at 120-165 BPM, or 165-180 BPM above the spacing cap: alt
Runs or single notes on triplet or odd snaps (1/3, 1/6, 1/12, 1/5...) are
irregular rhythm. The snap does use the map's timing, but doubling or halving
the BPM keeps a triplet a triplet, so it is safe. Everything else is a jump
(beyond JUMP_MIN_RADII), a slider head or a spaced note.

Aim, separately from the pattern, goes by distance alone: flow aim below
FLOW_AIM_MAX_RADII (streams, close notes), alt aim up to ALT_MAX_SPACING_RADII, jump aim beyond.
"""

import bisect
import math
import statistics
from dataclasses import dataclass

from .beatmap import SLIDER, SPINNER, Beatmap
from .difficulty import Difficulty
from .judge import ObjectResult

RUN_MIN_BPM = 120.0          # 1/4 notes slower than this never form a run
RUN_GAP_TOLERANCE = 0.15     # consecutive gaps within 15% keep the run going
STREAM_MIN_BPM = 165.0       # below this a 4+ run is alt, not a stream
STREAM_SPACING_FREE_BPM = 180.0  # from here on spacing doesn't matter (up to ALT_MAX_SPACING_RADII)
STREAM_SPACING_CAP_RADII = 1.6   # 165-180 BPM runs spaced wider than this are alt
BURST_MIN, STREAM_MIN, DEATHSTREAM_MIN = 4, 10, 34
JUMP_MIN_RADII = 5.0          # beyond this a note is a jump: single notes, runs at any speed, and aim
ALT_MAX_SPACING_RADII = JUMP_MIN_RADII   # runs spaced wider than this are jumps, whatever their speed
FLOW_AIM_MAX_RADII = 2.0      # aim below this distance is flow aim, up to ALT_MAX_SPACING_RADII alt aim, then jump aim
BREAK_GAP_MS = 1000.0
SNAP_TOLERANCE_MS = 3.0
STAMINA_WINDOW_MS = 20000.0

# Finger control: a fast-decaying strain where each note adds 1 and each change of
# tapping technique adds FINGER_TRANSITION_WEIGHT more. Fingering is predicted from
# the map: runs start on the same finger and alternate; single notes closer than
# SINGLE_ALTERNATE_MS to the previous one are alternated, slower ones single-tapped.
FINGER_TAU_MS = 500.0
FINGER_TRANSITION_WEIGHT = 1.0
FINGER_SAME_MAX_MS = 250.0      # the same finger again faster than this is a forced reset
RHYTHM_CHANGE_RATIO = 1.4       # consecutive gaps differing by this much are a rhythm change...
RHYTHM_DENSE_MS = 300.0         # ...when both are this short
SINGLE_ALTERNATE_MS = 140.0     # replays since the technique change: ~55% alternated at 125-150ms

READ_OVERLAP_RADII = 2.0        # notes on screen this close to the one being hit overlap it...
READ_OVERLAP_PATH_RADII = 4.0   # ...when the path to them is at least this long (not a stream or stack)

FIRST, DOUBLE, TRIPLE, BURST, STREAM, DEATHSTREAM = "first", "double", "triple", "burst", "stream", "deathstream"
ALT, IRREGULAR, JUMP, SLIDER_HEAD, SPACED = "alt", "irregular", "jump", "slider", "spaced"

STREAM_FAMILY = (DOUBLE, TRIPLE, BURST, STREAM, DEATHSTREAM)
LONG_STREAMS = (STREAM, DEATHSTREAM)
RUN_PATTERNS = STREAM_FAMILY + (ALT,)

REGULAR_DIVISORS = (1, 2, 4, 8, 16)
IRREGULAR_DIVISORS = (3, 6, 12, 5, 7, 9)


@dataclass
class ObjectFeatures:
    r: ObjectResult
    pattern: str
    gap_ms: float            # real time since the previous object started
    move_ms: float           # real time from the previous object's end to this one
    distance: float          # from previous object's end position, osu!px
    distance_radii: float
    angle: float | None      # degrees at the previous object: 0 = straight back, 180 = straight on
    direction: tuple[float, float] | None  # unit vector of the movement into this object
    bpm: float | None = None       # 1/4-note BPM of the run this object belongs to
    run_length: int = 1
    run_position: int = 0
    run_spacing: float = 0.0       # median spacing inside the run, radii
    divisor: int | None = None     # beat snap of the gap from the previous object (4 = 1/4...)
    visible: int = 0               # other objects on screen when this one must be hit
    load_nps: float = 0.0          # notes per second over the last STAMINA_WINDOW_MS
    gap_after_ms: float = math.inf  # real time from this object's end to the next one
    distance_after_radii: float = 0.0  # from this object's end to the next one
    finger: int = 1                # predicted finger: 1 = the one runs start with, 2 = the other
    transitions: int = 0           # technique changes into this note: same-finger reset, rhythm, jump<->run
    finger_strain: float = 0.0     # fast strain from the previous notes (FINGER_TAU_MS decay)
    # reading: the notes already on screen when this one must be hit (this one and those after it)
    overlap_share: float = 0.0     # share of them overlapping this note after the path moved away
    rhythm_var: float = 0.0        # std of log2 of their gaps: how irregular the rhythm to read is
    angle_var: float = 0.0         # std of their angles / 180: how much the flow keeps turning
    spacing_var: float = 0.0       # coefficient of variation of their spacing
    _radius: float = 1.0

    @property
    def aim(self) -> str | None:
        """Kind of aim to reach this note, by distance: "flow", "alt" or "jump"."""
        if self.pattern == FIRST:
            return None
        if self.distance_radii < FLOW_AIM_MAX_RADII:
            return "flow"
        return "alt" if self.distance_radii <= ALT_MAX_SPACING_RADII else "jump"

    @property
    def overshoot(self) -> float | None:
        """Aim error along the movement direction, in radii (+ past the note, - short of it)."""
        off = self.r.aim_offset
        if off is None or self.direction is None:
            return None
        return (off[0] * self.direction[0] + off[1] * self.direction[1]) / self._radius


def _end_position(r: ObjectResult) -> tuple[float, float]:
    o = r.obj
    return o.position_at(o.end_time) if o.kind == SLIDER else o.position


def snap_divisor(gap_map_ms: float, beat_length: float) -> int | None:
    """Smallest beat divisor the gap is snapped to (regular ones preferred)."""
    if beat_length <= 0 or gap_map_ms <= 0:
        return None
    for den in REGULAR_DIVISORS + IRREGULAR_DIVISORS:
        k = round(gap_map_ms * den / beat_length)
        if k > 0 and abs(gap_map_ms - k * beat_length / den) <= SNAP_TOLERANCE_MS:
            return den
    return None


def extract(results: list[ObjectResult], diff: Difficulty, rate: float, beatmap: Beatmap,
            single_alternate_ms: float = SINGLE_ALTERNATE_MS) -> list[ObjectFeatures]:
    radius = diff.radius
    feats: list[ObjectFeatures] = []
    prev: ObjectResult | None = None
    prev_dir: tuple[float, float] | None = None
    for r in results:
        o = r.obj
        if o.kind == SPINNER or not r.played:
            prev, prev_dir = None, None
            continue
        if prev is None:
            f = ObjectFeatures(r, FIRST, math.inf, math.inf, 0.0, 0.0, None, None)
        else:
            gap = (o.time - prev.obj.time) / rate
            ex, ey = _end_position(prev)
            sx, sy = o.position
            dx, dy = sx - ex, sy - ey
            dist = math.hypot(dx, dy)
            direction = (dx / dist, dy / dist) if dist > 1e-6 else None
            angle = None
            if direction and prev_dir:
                cos = -(direction[0] * prev_dir[0] + direction[1] * prev_dir[1])
                angle = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
            f = ObjectFeatures(r, SPACED, gap, (o.time - prev.obj.end_time) / rate, dist, dist / radius,
                               angle, direction)
            f.divisor = snap_divisor(o.time - prev.obj.time, beatmap.timing_at(o.time).base_beat_length)
            prev_dir = direction if gap < BREAK_GAP_MS else None
            if gap >= BREAK_GAP_MS:
                f.pattern, f.direction, f.angle, f.divisor = FIRST, None, None, None
        f._radius = radius
        feats.append(f)
        prev = r

    for a, b in zip(feats, feats[1:]):
        if b.pattern != FIRST:
            a.gap_after_ms, a.distance_after_radii = b.move_ms, b.distance_radii
    _mark_runs(feats)
    for f in feats:
        if f.pattern != SPACED:
            continue
        if f.divisor in IRREGULAR_DIVISORS:
            f.pattern = IRREGULAR
        elif f.distance_radii > JUMP_MIN_RADII:
            f.pattern = JUMP
        elif f.r.obj.kind == SLIDER:
            f.pattern = SLIDER_HEAD

    _mark_density(feats, diff, rate)
    _mark_reading(feats, diff)
    _mark_fingers(feats, single_alternate_ms)
    return feats


def _run_pattern(length: int, bpm: float, spacing: float) -> str:
    if length == 2:
        return DOUBLE
    if length == 3:
        return TRIPLE
    is_stream = bpm >= STREAM_SPACING_FREE_BPM or (bpm >= STREAM_MIN_BPM and spacing <= STREAM_SPACING_CAP_RADII)
    if not is_stream:
        return ALT
    if length >= DEATHSTREAM_MIN:
        return DEATHSTREAM
    return STREAM if length >= STREAM_MIN else BURST


def _mark_runs(feats: list[ObjectFeatures]):
    max_gap = 15000 / RUN_MIN_BPM
    i = 0
    while i < len(feats):
        j = i + 1
        while (j < len(feats) and 0 < feats[j].gap_ms <= max_gap
               and (j == i + 1 or abs(feats[j].gap_ms - feats[j - 1].gap_ms) <= RUN_GAP_TOLERANCE * feats[j - 1].gap_ms)):
            j += 1
        length = j - i  # objects i..j-1; the first one starts the run
        if length >= 2:
            inner = feats[i + 1:j]
            bpm = 15000 / statistics.fmean(f.gap_ms for f in inner)
            spacing = statistics.median(f.distance_radii for f in inner)
            divisors = [f.divisor for f in inner if f.divisor is not None]
            divisor = max(set(divisors), key=divisors.count) if divisors else None
            if spacing > ALT_MAX_SPACING_RADII:
                i = j  # too spaced even for alt: they are jumps
                continue
            pattern = IRREGULAR if divisor in IRREGULAR_DIVISORS else _run_pattern(length, bpm, spacing)
            for pos, f in enumerate(feats[i:j]):
                f.pattern, f.bpm, f.run_length, f.run_position, f.run_spacing = pattern, bpm, length, pos, spacing
        i = j


def _mark_density(feats: list[ObjectFeatures], diff: Difficulty, rate: float):
    """Objects on screen at each hit, and recent note density (stamina load)."""
    starts = [f.r.obj.time for f in feats]
    appear = sorted(f.r.obj.time - diff.preempt for f in feats)
    ends = sorted(f.r.obj.end_time for f in feats)
    window = STAMINA_WINDOW_MS * rate
    lo = 0
    for k, f in enumerate(feats):
        t = f.r.obj.time
        # appeared by now, minus already finished before now, minus itself
        f.visible = bisect.bisect_right(appear, t) - bisect.bisect_left(ends, t) - 1
        while starts[lo] <= t - window:
            lo += 1
        f.load_nps = (k - lo + 1) / (STAMINA_WINDOW_MS / 1000)


def _motion(f: ObjectFeatures) -> str:
    return "jump" if f.pattern == JUMP else ("run" if f.pattern in RUN_PATTERNS else "other")


def _mark_fingers(feats: list[ObjectFeatures], single_alternate_ms: float):
    """Predicted fingering, technique changes and the fast finger strain."""
    strain, prev = 0.0, None
    for f in feats:
        if prev is None or f.pattern == FIRST:
            f.finger, f.transitions, f.finger_strain = 1, 0, 0.0
            strain, prev = 0.0, f
            continue
        if f.run_length >= 2:
            f.finger = 1 if f.run_position % 2 == 0 else 2
        else:
            f.finger = 3 - prev.finger if f.gap_ms < single_alternate_ms else prev.finger
        same = f.finger == prev.finger and f.gap_ms <= FINGER_SAME_MAX_MS
        rhythm = (f.gap_ms <= RHYTHM_DENSE_MS and prev.gap_ms <= RHYTHM_DENSE_MS
                  and max(f.gap_ms, prev.gap_ms) >= RHYTHM_CHANGE_RATIO * min(f.gap_ms, prev.gap_ms))
        motion = {_motion(prev), _motion(f)} == {"run", "jump"}
        f.transitions = same + rhythm + motion
        strain = (strain + 1 + FINGER_TRANSITION_WEIGHT * prev.transitions) * math.exp(-f.gap_ms / FINGER_TAU_MS)
        f.finger_strain = strain
        prev = f


def _mark_reading(feats: list[ObjectFeatures], diff: Difficulty):
    """What the player has to read at each hit: the upcoming notes already on screen."""
    starts = [f.r.obj.time for f in feats]
    limit = READ_OVERLAP_RADII * diff.radius
    for i, f in enumerate(feats):
        hi = bisect.bisect_right(starts, f.r.obj.time + diff.preempt)
        window = feats[i:hi]
        if len(window) < 2:
            continue
        # a note close to this one but far along the path: the pattern comes back over itself.
        # Streams and stacks are close too, but they are read in order and don't count.
        x, y = f.r.obj.position
        path, overlaps = 0.0, 0
        for o in window[1:]:
            path += o.distance
            if (math.hypot(o.r.obj.position[0] - x, o.r.obj.position[1] - y) <= limit
                    and path >= READ_OVERLAP_PATH_RADII * diff.radius):
                overlaps += 1
        f.overlap_share = overlaps / (len(window) - 1)
        gaps = [o.gap_ms for o in window[1:] if 0 < o.gap_ms < BREAK_GAP_MS]
        if len(gaps) >= 2:
            f.rhythm_var = statistics.pstdev(math.log2(g) for g in gaps)
        angles = [o.angle for o in window[1:] if o.angle is not None]
        if len(angles) >= 2:
            f.angle_var = statistics.pstdev(angles) / 180
        spacing = [o.distance_radii for o in window[1:]]
        mean = statistics.fmean(spacing)
        if len(spacing) >= 2 and mean > 0:
            f.spacing_var = statistics.pstdev(spacing) / mean
