""".osu parser producing hit objects with stable-accurate timing and stacking.

References: the .osu format wiki page, danser-go (beatmap/objects, stackleniency.go)
and lazer's OsuBeatmapProcessor for the stacking algorithm.
"""

import math
from dataclasses import dataclass, field
from pathlib import Path

from .curves import SliderPath
from .difficulty import Difficulty, f32
from .mods import Mods

CIRCLE, SLIDER, SPINNER = "circle", "slider", "spinner"
STACK_DISTANCE = 3.0


@dataclass
class TimingPoint:
    time: float
    beat_length: float       # as written in the file (negative = inherited)
    base_beat_length: float  # beat length of the governing uninherited point

    @property
    def ratio(self) -> float:
        if self.beat_length >= 0 or math.isnan(self.beat_length):
            return 1.0
        return f32(min(1000.0, max(10.0, -self.beat_length))) / 100

    @property
    def effective_beat_length(self) -> float:
        return self.base_beat_length * self.ratio


@dataclass
class SliderPoint:
    time: int
    kind: str  # "tick", "repeat" or "end"


@dataclass
class HitObject:
    index: int
    kind: str
    x: float
    y: float
    time: int
    end_time: int
    new_combo: bool
    # sliders only
    path: SliderPath | None = None
    repeats: int = 1
    span_duration: float = 0.0
    score_points: list[SliderPoint] = field(default_factory=list)
    # filled by Beatmap.apply_mods
    stack: int = 0
    stack_offset: float = 0.0
    flip_y: bool = False

    @property
    def raw_end_position(self) -> tuple[float, float]:
        if self.kind != SLIDER:
            return self.x, self.y
        return self.path.position_at(0.0 if self.repeats % 2 == 0 else 1.0)

    def _transform(self, p: tuple[float, float]) -> tuple[float, float]:
        x, y = p
        if self.flip_y:
            y = 384 - y
        return x - self.stack_offset, y - self.stack_offset

    @property
    def position(self) -> tuple[float, float]:
        """Stacked, mod-adjusted start position (where the player must click)."""
        return self._transform((self.x, self.y))

    def position_at(self, time: float) -> tuple[float, float]:
        """Stacked, mod-adjusted ball position of a slider at a given time."""
        if self.kind != SLIDER or self.span_duration <= 0:
            return self.position
        t = min(max(time - self.time, 0.0), self.span_duration * self.repeats)
        span = min(int(t // self.span_duration), self.repeats - 1)
        progress = (t - span * self.span_duration) / self.span_duration
        if span % 2 == 1:
            progress = 1 - progress
        return self._transform(self.path.position_at(progress))


@dataclass
class Beatmap:
    path: Path
    version: int
    artist: str = ""
    title: str = ""
    creator: str = ""
    difficulty_name: str = ""
    mode: int = 0
    stack_leniency: float = 0.7
    hp: float = 5.0
    cs: float = 5.0
    od: float = 5.0
    ar: float | None = None
    slider_multiplier: float = 1.4
    slider_tick_rate: float = 1.0
    timing_points: list[TimingPoint] = field(default_factory=list)
    objects: list[HitObject] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.artist} - {self.title} [{self.difficulty_name}]"

    def timing_at(self, time: float) -> TimingPoint:
        chosen = self.timing_points[0]
        for tp in self.timing_points:
            if tp.time > time:
                break
            chosen = tp
        return chosen

    def apply_mods(self, mods: int) -> Difficulty:
        """Compute mod-adjusted difficulty and stack every object accordingly."""
        diff = Difficulty.from_map(self.cs, self.ar if self.ar is not None else self.od, self.od, mods)
        for obj in self.objects:
            obj.stack = 0
            obj.flip_y = bool(mods & Mods.HardRock)
        threshold = math.floor(diff.preempt * self.stack_leniency)
        if self.version >= 6:
            _stack_new(self.objects, threshold)
        else:
            _stack_old(self.objects, threshold)
        for obj in self.objects:
            if obj.kind == SPINNER:
                obj.stack = 0
            obj.stack_offset = obj.stack * diff.radius / 10
        return diff


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _stack_new(objs: list[HitObject], threshold: int):
    if not objs:
        return
    extended_end = len(objs) - 1
    for i in range(len(objs) - 1, -1, -1):
        base = i
        for n in range(base + 1, len(objs)):
            base_obj = objs[base]
            if base_obj.kind == SPINNER:
                break
            obj_n = objs[n]
            if obj_n.kind == SPINNER:
                continue
            if obj_n.time - base_obj.end_time > threshold:
                break
            start_n = (obj_n.x, obj_n.y)
            if (_dist((base_obj.x, base_obj.y), start_n) < STACK_DISTANCE
                    or base_obj.kind == SLIDER and _dist(base_obj.raw_end_position, start_n) < STACK_DISTANCE):
                base = n
                obj_n.stack = 0
        if base > extended_end:
            extended_end = base
            if extended_end == len(objs) - 1:
                break

    extended_start = 0
    for i in range(extended_end, 0, -1):
        obj_i = objs[i]
        if obj_i.stack != 0 or obj_i.kind == SPINNER:
            continue
        if obj_i.kind == CIRCLE:
            for n in range(i - 1, -1, -1):
                obj_n = objs[n]
                if obj_n.kind == SPINNER:
                    continue
                if obj_i.time - obj_n.end_time > threshold:
                    break
                if n < extended_start:
                    obj_n.stack = 0
                    extended_start = n
                if obj_n.kind == SLIDER and _dist(obj_n.raw_end_position, (obj_i.x, obj_i.y)) < STACK_DISTANCE:
                    offset = obj_i.stack - obj_n.stack + 1
                    for j in range(n + 1, i + 1):
                        if _dist(obj_n.raw_end_position, (objs[j].x, objs[j].y)) < STACK_DISTANCE:
                            objs[j].stack -= offset
                    break
                if _dist((obj_n.x, obj_n.y), (obj_i.x, obj_i.y)) < STACK_DISTANCE:
                    obj_n.stack = obj_i.stack + 1
                    obj_i = obj_n
        elif obj_i.kind == SLIDER:
            for n in range(i - 1, -1, -1):
                obj_n = objs[n]
                if obj_n.kind == SPINNER:
                    continue
                if obj_i.time - obj_n.time > threshold:
                    break
                if _dist(obj_n.raw_end_position, (obj_i.x, obj_i.y)) < STACK_DISTANCE:
                    obj_n.stack = obj_i.stack + 1
                    obj_i = obj_n


def _stack_old(objs: list[HitObject], threshold: int):
    for i, curr in enumerate(objs):
        if curr.stack != 0 and curr.kind != SLIDER:
            continue
        start_time = curr.end_time
        slider_stack = 0
        path_end = curr.path.position_at(1.0) if curr.kind == SLIDER else (curr.x, curr.y)
        for j in range(i + 1, len(objs)):
            obj_j = objs[j]
            if obj_j.time - threshold > start_time:
                break
            if _dist((obj_j.x, obj_j.y), (curr.x, curr.y)) < STACK_DISTANCE:
                curr.stack += 1
                start_time = obj_j.end_time
            elif _dist((obj_j.x, obj_j.y), path_end) < STACK_DISTANCE:
                slider_stack += 1
                obj_j.stack -= slider_stack
                start_time = obj_j.end_time


def _build_slider_timing(bm: Beatmap, obj: HitObject):
    """Slider end time and score points (ticks, repeats, end), stable-style.

    Mirrors danser's calculateFollowPointsStable, treating each span as a
    single segment.
    """
    tp = bm.timing_at(obj.time)
    scoring_distance = 100 * bm.slider_multiplier / bm.slider_tick_rate
    velocity = scoring_distance * bm.slider_tick_rate  # px per beat
    if tp.effective_beat_length > 0:
        velocity *= 1000.0 / tp.effective_beat_length  # px per second

    length = obj.path.length
    tick_distance = scoring_distance / tp.ratio if bm.version >= 8 else scoring_distance
    if length > 0 and tick_distance > length:
        tick_distance = length
    if tick_distance <= 0 or length / tick_distance > 32768:
        tick_distance = max(length / 32768, 1e-6)

    min_distance_from_end = velocity * 0.01
    no_ticks = math.isnan(tp.beat_length)

    points: list[SliderPoint] = []
    scoring_total = 0.0
    scoring_dist = 0.0
    t = float(obj.time)
    for span in range(obj.repeats):
        distance_to_end = length
        skip_tick = no_ticks
        t += 1000.0 * length / velocity
        scoring_dist += length
        while scoring_dist >= tick_distance and not skip_tick:
            scoring_total += tick_distance
            scoring_dist -= tick_distance
            distance_to_end -= tick_distance
            skip_tick = distance_to_end <= min_distance_from_end
            if skip_tick:
                break
            points.append(SliderPoint(obj.time + math.floor(f32(scoring_total) / velocity * 1000), "tick"))

        scoring_total += scoring_dist
        last = span == obj.repeats - 1
        points.append(SliderPoint(obj.time + math.floor(f32(scoring_total) / velocity * 1000), "end" if last else "repeat"))

        if skip_tick:
            scoring_dist = 0
        else:
            scoring_total -= tick_distance - scoring_dist
            scoring_dist = tick_distance - scoring_dist

    obj.end_time = math.floor(t)
    obj.span_duration = (t - obj.time) / obj.repeats
    points.sort(key=lambda p: p.time)
    # Stable judges the slider end 36ms early (but never before the middle).
    points[-1] = SliderPoint(max(obj.time + (obj.end_time - obj.time) // 2, obj.end_time - 36), "end")
    obj.score_points = points


def parse_beatmap(path: str | Path) -> Beatmap:
    path = Path(path)
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()

    version = 14
    if lines and lines[0].strip().startswith("osu file format v"):
        try:
            version = int(lines[0].strip()[len("osu file format v"):])
        except ValueError:
            pass
    bm = Beatmap(path=path, version=version)
    offset = 24 if version < 5 else 0  # stable shifts very old maps by 24ms

    section = None
    raw_timing: list[tuple[float, float, bool]] = []
    raw_objects: list[list[str]] = []
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section in ("General", "Metadata", "Difficulty"):
            key, _, value = line.partition(":")
            key, value = key.strip(), value.strip()
            try:
                match key:
                    case "Mode": bm.mode = int(value)
                    case "StackLeniency": bm.stack_leniency = float(value)
                    case "Artist": bm.artist = value
                    case "Title": bm.title = value
                    case "Creator": bm.creator = value
                    case "Version": bm.difficulty_name = value
                    case "HPDrainRate": bm.hp = float(value)
                    case "CircleSize": bm.cs = float(value)
                    case "OverallDifficulty": bm.od = float(value)
                    case "ApproachRate": bm.ar = float(value)
                    case "SliderMultiplier": bm.slider_multiplier = float(value)
                    case "SliderTickRate": bm.slider_tick_rate = float(value)
            except ValueError:
                pass
        elif section == "TimingPoints":
            parts = line.split(",")
            if len(parts) < 2:
                continue
            time, beat_length = float(parts[0]) + offset, float(parts[1])
            uninherited = parts[6] == "1" if len(parts) > 6 else beat_length >= 0
            raw_timing.append((time, beat_length, uninherited))
        elif section == "HitObjects":
            raw_objects.append(line.split(","))

    base = None
    for time, beat_length, uninherited in sorted(raw_timing, key=lambda p: p[0]):  # stable sort
        if uninherited or base is None:
            base = beat_length if beat_length > 0 else 500.0
        bm.timing_points.append(TimingPoint(time, beat_length if not uninherited else base, base))
    if not bm.timing_points:
        bm.timing_points.append(TimingPoint(0, 500.0, 500.0))

    for parts in raw_objects:
        if len(parts) < 4:
            continue
        x, y, time, type_bits = float(parts[0]), float(parts[1]), int(float(parts[2])) + offset, int(parts[3])
        obj = HitObject(index=len(bm.objects), kind=CIRCLE, x=x, y=y, time=time, end_time=time,
                        new_combo=bool(type_bits & 4))
        if type_bits & 2 and len(parts) > 7:
            obj.kind = SLIDER
            curve = parts[5].split("|")
            anchors = [(x, y)]
            for p in curve[1:]:
                px, _, py = p.partition(":")
                anchors.append((float(px), float(py)))
            obj.repeats = max(1, int(parts[6]))
            obj.path = SliderPath(curve[0], anchors, float(parts[7]))
            _build_slider_timing(bm, obj)
        elif type_bits & 8:
            obj.kind = SPINNER
            obj.x, obj.y = 256, 192
            obj.end_time = int(float(parts[5])) + offset if len(parts) > 5 else time
        elif not type_bits & 1:
            continue  # mania holds etc.
        bm.objects.append(obj)

    return bm
