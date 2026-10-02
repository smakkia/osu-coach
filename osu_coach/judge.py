"""Replays a .osr against a beatmap, reproducing osu! stable's judgement.

The logic follows danser-go's stable ruleset (rulesets/osu): notelock,
hit windows, slider follow circle, ticks and key handling, spinner rotation.
On top of the judgement it keeps the per-object details the coach needs:
timing error, where the cursor was at the click, why an object was missed,
where a slider broke.
"""

import math
from dataclasses import dataclass, field

from .beatmap import CIRCLE, SLIDER, SPINNER, Beatmap, HitObject
from .difficulty import HITTABLE_RANGE, Difficulty, f32
from .mods import Mods
from .replay import Replay

NOTELOCK_TOLERANCE = 3  # Tolerance2B in danser
FOLLOW_RADIUS_SCALE = 2.4
FRAME_TIME = 1000.0 / 60
SPINNER_CENTRE = (256.0, 192.0)

LEFT, RIGHT = 1, 2


@dataclass
class ObjectResult:
    obj: HitObject
    result: int | None = None       # 300 / 100 / 50 / 0 (miss); None = not played
    played: bool = True             # False when the replay ended first (fail / quit)
    # circle / slider head click
    hit_time: int | None = None
    hit_error: int | None = None    # hit_time - object time (negative = early)
    cursor: tuple[float, float] | None = None  # cursor at the click
    key: int | None = None          # button that hit it: 1 = left (K1/M1), 2 = right (K2/M2)
    head_result: int | None = None  # sliders: timing grade of the head, 0 = head missed
    miss_reason: str | None = None  # "aim", "timing", "notelock", "no_click"
    off_target_clicks: list[tuple[int, float, float]] = field(default_factory=list)
    notelocked_clicks: list[int] = field(default_factory=list)
    # slider body
    ticks_total: int = 0
    ticks_hit: int = 0
    slider_break_time: int | None = None  # first tick/repeat/end that was dropped
    slider_break_kind: str | None = None
    points: list[tuple[int, str, bool]] = field(default_factory=list)   # every tick/repeat/end: (time, kind, hit)
    # spinner
    spins: int = 0
    spins_required: int = 0

    @property
    def aim_offset(self) -> tuple[float, float] | None:
        """Cursor minus object centre at the click, in osu!pixels."""
        if self.cursor is None:
            return None
        px, py = self.obj.position
        return self.cursor[0] - px, self.cursor[1] - py


@dataclass
class _Input:
    """Button state as tracked by stable's ruleset for one frame."""
    time: int = 0
    last_frame_time: int = 0
    x: float = 0.0
    y: float = 0.0
    prev_x: float = 0.0
    prev_y: float = 0.0
    left_cond: bool = False   # left newly pressed this frame
    right_cond: bool = False
    left_cond_e: bool = False  # ...and not yet consumed by a click
    right_cond_e: bool = False
    game_down: bool = False
    mouse_down_button: int = 0
    last_button: int = 0
    last_button2: int = 0
    held_left: bool = False
    held_right: bool = False


@dataclass
class _SliderState:
    head_done: bool = False
    head_result: int = 0
    down_button: int = 0
    sliding: bool = False
    slide_start: int = 0
    scored: int = 0
    missed: int = 0
    done: bool = False
    last_acceptable: bool | None = None  # button check at the previous frame


@dataclass
class _SpinnerState:
    requirement: int = 0
    max_acceleration: float = 0.0
    last_angle: float = 0.0
    updated_before: bool = False
    frame_variance: float = FRAME_TIME
    theoretical_velocity: float = 0.0
    current_velocity: float = 0.0
    zero_count: int = 0
    rotation_count_f: float = 0.0
    last_rotation_count: int = 0
    scoring_rotation_count: int = 0
    done: bool = False


class _Sim:
    def __init__(self, beatmap: Beatmap, diff: Difficulty, mods: int):
        self.diff = diff
        self.spun_out = bool(mods & Mods.SpunOut)
        self.inp = _Input()
        self.results = [ObjectResult(o) for o in beatmap.objects]
        self.queue_pos = 0
        self.processed: list[ObjectResult] = []
        self.slider: dict[int, _SliderState] = {}
        self.spinner: dict[int, _SpinnerState] = {}
        for r in self.results:
            if r.obj.kind == SLIDER:
                self.slider[r.obj.index] = _SliderState()
            elif r.obj.kind == SPINNER:
                duration = r.obj.end_time - r.obj.time
                self.spinner[r.obj.index] = _SpinnerState(
                    requirement=int(duration / 1000 * diff.spinner_ratio),
                    max_acceleration=0.00008 + max(0.0, (5000 - duration) / 1000 / 2000),
                )

    # --- helpers -----------------------------------------------------------
    def is_hit(self, r: ObjectResult) -> bool:
        if r.obj.kind == CIRCLE:
            return r.result is not None
        if r.obj.kind == SLIDER:
            return self.slider[r.obj.index].done
        return self.spinner[r.obj.index].done

    def is_finished(self, r: ObjectResult) -> bool:
        if r.obj.kind == SLIDER:
            st = self.slider[r.obj.index]
            return st.done and st.head_done
        return self.is_hit(r)

    def can_be_hit(self, time: int, r: ObjectResult) -> str:
        """Stable notelock rules: 'click', 'shake' or 'ignored'."""
        if r.obj.kind == CIRCLE:
            idx = self.processed.index(r)
            if idx > 0:
                prev = self.processed[idx - 1]
                if prev.obj.stack > 0 and not self.is_hit(prev):
                    return "ignored"  # don't shake stacks
        for g in self.processed:
            if g is r:
                break
            if not self.is_hit(g) and g.obj.end_time + NOTELOCK_TOLERANCE < r.obj.time:
                return "shake"
        if abs(time - r.obj.time) >= HITTABLE_RANGE:
            return "shake"
        return "click"

    def grade_delta(self, delta: int) -> int:
        if delta < self.diff.hit300:
            return 300
        if delta < self.diff.hit100:
            return 100
        if delta < self.diff.hit50:
            return 50
        return 0

    def _pressed_button(self) -> int:
        inp = self.inp
        if inp.left_cond:
            return LEFT
        if inp.right_cond:
            return RIGHT
        return inp.mouse_down_button

    # --- per-frame phases --------------------------------------------------
    def update_queue(self, time: int):
        self.processed = [r for r in self.processed if not self.is_finished(r)]
        while (self.queue_pos < len(self.results)
               and self.results[self.queue_pos].obj.time - self.diff.preempt <= time):
            self.processed.append(self.results[self.queue_pos])
            self.queue_pos += 1

    def update_buttons(self, time: int, x: float, y: float, left: bool, right: bool):
        inp = self.inp
        inp.last_frame_time, inp.time = inp.time, time
        inp.prev_x, inp.prev_y = inp.x, inp.y
        inp.x, inp.y = x, y
        inp.left_cond = left and not inp.held_left
        inp.right_cond = right and not inp.held_right
        inp.left_cond_e, inp.right_cond_e = inp.left_cond, inp.right_cond
        if left != inp.held_left or right != inp.held_right:
            inp.game_down = left or right
            inp.last_button2 = inp.last_button
            inp.last_button = inp.mouse_down_button
            inp.mouse_down_button = (LEFT if left else 0) | (RIGHT if right else 0)

    def end_frame(self, left: bool, right: bool):
        self.inp.held_left, self.inp.held_right = left, right

    def click(self):
        inp = self.inp
        for r in list(self.processed):
            if not (inp.left_cond_e or inp.right_cond_e):
                return
            obj = r.obj
            if obj.kind == SPINNER:
                continue
            if obj.kind == CIRCLE and r.result is not None:
                continue
            if obj.kind == SLIDER and self.slider[obj.index].head_done:
                continue

            px, py = obj.position
            in_range = math.hypot(inp.x - px, inp.y - py) <= self.diff.radius
            action = self.can_be_hit(inp.time, r)

            if in_range:
                if action == "click":
                    if inp.left_cond_e:
                        inp.left_cond_e = False
                        r.key = LEFT
                    else:
                        inp.right_cond_e = False
                        r.key = RIGHT
                    grade = self.grade_delta(abs(inp.time - obj.time))
                    r.hit_time, r.hit_error, r.cursor = inp.time, inp.time - obj.time, (inp.x, inp.y)
                    if obj.kind == CIRCLE:
                        r.result = grade
                        if grade == 0:
                            r.miss_reason = "timing"
                    else:
                        st = self.slider[obj.index]
                        st.down_button = self._pressed_button()
                        st.head_done = True
                        st.head_result = r.head_result = grade
                        if grade == 0:
                            r.miss_reason = "timing"
                else:
                    inp.left_cond_e = inp.right_cond_e = False
                    if action == "shake":
                        r.notelocked_clicks.append(inp.time)
            elif action == "click":
                r.off_target_clicks.append((inp.time, inp.x, inp.y))

    def normal(self):
        # Stable only advances the first unfinished slider (danser's replay hack).
        slider_seen = False
        for r in self.processed:
            if r.obj.kind == SLIDER:
                st = self.slider[r.obj.index]
                if slider_seen:
                    continue
                if not st.done:
                    slider_seen = True
                    self._update_slider(r, st)
            elif r.obj.kind == SPINNER:
                sp = self.spinner[r.obj.index]
                if not sp.done:
                    self._update_spinner(r, sp)

    def _update_slider(self, r: ObjectResult, st: _SliderState):
        obj, inp = r.obj, self.inp
        if inp.time < obj.time:
            return
        acceptable = False
        swap = inp.game_down and not (inp.last_button == LEFT | RIGHT
                                      and inp.last_button2 == inp.mouse_down_button)
        if inp.game_down:
            if st.down_button == 0 or (inp.mouse_down_button != LEFT | RIGHT and swap):
                st.down_button = self._pressed_button()
                acceptable = True
            elif inp.mouse_down_button & st.down_button:
                acceptable = True
        else:
            st.down_button = 0
        acceptable = acceptable or swap

        points = obj.score_points
        # Points falling strictly between two frames: the live game polled input
        # every millisecond, seeing the previous frame's buttons and a cursor
        # moving towards the new position. Judging them on the next frame instead
        # wrongly drops slider ends released just after the end point.
        if st.last_acceptable is not None:
            span = inp.time - inp.last_frame_time
            while st.scored + st.missed < len(points) and points[st.scored + st.missed].time < inp.time:
                point = points[st.scored + st.missed]
                frac = (point.time - inp.last_frame_time) / span if span > 0 else 1.0
                frac = min(1.0, max(0.0, frac))
                cx = inp.prev_x + (inp.x - inp.prev_x) * frac
                cy = inp.prev_y + (inp.y - inp.prev_y) * frac
                self._judge_point(r, st, point, cx, cy, st.last_acceptable)

        self._judge_point(r, st, None, inp.x, inp.y, acceptable)
        st.last_acceptable = acceptable

    def _judge_point(self, r: ObjectResult, st: _SliderState, point, x: float, y: float, acceptable: bool):
        """Follow-circle check at one instant; judges `point` (or any point due now)."""
        obj = r.obj
        time = point.time if point is not None else self.inp.time
        bx, by = obj.position_at(time)
        radius = self.diff.radius * (FOLLOW_RADIUS_SCALE if st.sliding else 1)
        allowable = acceptable and (x - bx) ** 2 + (y - by) ** 2 < radius * radius

        if allowable and not st.sliding:
            st.sliding = True
            st.slide_start = time

        points = obj.score_points
        if point is None:
            passed = sum(1 for p in points if p.time <= time)
            if st.scored + st.missed < passed:
                point = points[st.scored + st.missed]
        if point is not None:
            hit = allowable and st.slide_start <= point.time
            r.points.append((point.time, point.kind, hit))
            if hit:
                st.scored += 1
            else:
                st.missed += 1
                if r.slider_break_time is None:
                    r.slider_break_time, r.slider_break_kind = point.time, point.kind

        if not allowable and st.sliding and st.scored + st.missed < len(points):
            st.sliding = False

    def _update_spinner(self, r: ObjectResult, sp: _SpinnerState):
        obj, inp, speed = r.obj, self.inp, self.diff.speed
        t = inp.time
        if not obj.time < t < obj.end_time:
            return
        time_diff = float(t - inp.last_frame_time) if inp.last_frame_time else FRAME_TIME

        max_accel = sp.max_acceleration * time_diff / speed
        if self.spun_out:
            sp.current_velocity = 0.03
        elif sp.theoretical_velocity > sp.current_velocity:
            sp.current_velocity += min(sp.theoretical_velocity - sp.current_velocity, max_accel)
        else:
            sp.current_velocity += max(sp.theoretical_velocity - sp.current_velocity, -max_accel)
        sp.current_velocity = max(-0.05, min(sp.current_velocity, 0.05))

        angle = math.atan2(inp.y - SPINNER_CENTRE[1], inp.x - SPINNER_CENTRE[0])
        if not sp.updated_before:
            sp.last_angle = angle
            sp.updated_before = True

        angle_diff = angle - sp.last_angle
        if angle - sp.last_angle < -math.pi:
            angle_diff = 2 * math.pi + angle - sp.last_angle
        elif sp.last_angle - angle < -math.pi:
            angle_diff = -2 * math.pi - sp.last_angle + angle

        decay = 0.999 ** time_diff
        sp.frame_variance = decay * sp.frame_variance + (1 - decay) * time_diff

        if angle_diff == 0:
            sp.zero_count += 1
            sp.theoretical_velocity = sp.theoretical_velocity / 3 if sp.zero_count < 2 else 0.0
        else:
            sp.zero_count = 0
            if not inp.game_down:
                angle_diff = 0.0
            if abs(angle_diff) < math.pi:
                if sp.frame_variance / speed > FRAME_TIME * 1.04:
                    sp.theoretical_velocity = angle_diff / (time_diff / speed) if time_diff > 0 else 0.0
                else:
                    sp.theoretical_velocity = angle_diff / FRAME_TIME
            else:
                sp.theoretical_velocity = 0.0
        sp.last_angle = angle

        rotation = sp.current_velocity * time_diff
        sp.rotation_count_f = f32(sp.rotation_count_f + f32(abs(f32(rotation)) / math.pi))
        rotation_count = int(sp.rotation_count_f)
        if rotation_count != sp.last_rotation_count:
            sp.scoring_rotation_count += 1
            sp.last_rotation_count = rotation_count

    def post(self):
        t = self.inp.time
        for r in self.processed:
            obj = r.obj
            if obj.kind == CIRCLE:
                if r.result is None and t > obj.time + self.diff.hit50:
                    r.result = 0
                    r.miss_reason = _miss_reason(r)
            elif obj.kind == SLIDER:
                st = self.slider[obj.index]
                if not st.head_done and t > obj.time + self.diff.hit50:
                    st.down_button = self._pressed_button()
                    st.head_done = True
                    st.head_result = r.head_result = 0
                    r.miss_reason = _miss_reason(r)
                if t >= obj.end_time and not st.done:
                    scored = st.scored + (1 if st.head_result else 0)
                    rate = scored / (len(obj.score_points) + 1)
                    r.result = 300 if rate == 1 else 100 if rate >= 0.5 else 50 if rate > 0 else 0
                    r.ticks_total, r.ticks_hit = len(obj.score_points), st.scored
                    st.done = True
            else:
                sp = self.spinner[obj.index]
                if t >= obj.end_time and not sp.done:
                    req, spins = sp.requirement, sp.scoring_rotation_count
                    if req == 0 or spins >= req + 1:
                        r.result = 300
                    elif spins >= req - 1:
                        r.result = 100
                    elif spins >= req // 4:
                        r.result = 50
                    else:
                        r.result = 0
                    r.spins, r.spins_required = spins, req
                    sp.done = True

    def finish(self):
        """Objects never resolved before the last frame were not played (fail/quit)."""
        for r in self.results:
            if not self.is_hit(r):
                r.played = False
                r.result = None


def _miss_reason(r: ObjectResult) -> str:
    if r.notelocked_clicks:
        return "notelock"
    if any(abs(t - r.obj.time) < HITTABLE_RANGE for t, _, _ in r.off_target_clicks):
        return "aim"
    return "no_click"


def judge(replay: Replay, beatmap: Beatmap) -> tuple[list[ObjectResult], Difficulty]:
    diff = beatmap.apply_mods(replay.mods)
    sim = _Sim(beatmap, diff, replay.mods)

    for frame in replay.frames:
        sim.update_queue(frame.time)
        sim.update_buttons(frame.time, frame.x, frame.y, frame.left, frame.right)
        sim.click()
        sim.normal()
        sim.post()
        sim.end_frame(frame.left, frame.right)

    sim.finish()
    return sim.results, diff
