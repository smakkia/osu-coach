"""Explains the mistakes of a single play, episode by episode.

An episode is one thing that went wrong: a stream with misses in it, a missed
jump, a broken slider, a cluster of 100s. Each one gets a timestamp and a
reason derived from the replay itself (where the cursor was, which keys were
down, how the timing drifted), not from statistics. Context episodes (stamina,
high AR) compare this play with the player's usual form on the same patterns.
"""

import bisect
import math
from dataclasses import dataclass, field

import numpy as np

from .advice import DENSE_SCREEN, HIGH_AR, STAMINA_HIGH_NPS, ExpectedModel, Sample, category_of
from .beatmap import SLIDER
from .features import ALT, IRREGULAR, JUMP, RUN_PATTERNS, STREAM_FAMILY
from .judge import FOLLOW_RADIUS_SCALE
from .keys import TapStats
from .replay import Frame

END_DROP_COST = 1 / 3  # a dropped slider end turns a 300 into a 100
COST_100, COST_50 = 1 / 3, 1 / 2
SECTION_GAP_MS = 4000   # mistakes closer than this belong to the same section


@dataclass
class Episode:
    time: int           # map time (ms) of the first mistake
    category: str
    title: str
    reasons: list[str] = field(default_factory=list)
    cost: float = 0.0   # misses + combo breaks (+ fractional accuracy losses)


def timestamp(ms: float) -> str:
    ms = int(ms)
    return f"{ms // 60000}:{ms % 60000 / 1000:04.1f}"


class _Frames:
    """Lookup helpers over the replay's input."""

    def __init__(self, frames: list[Frame], taps: TapStats | None = None):
        self.frames = frames
        self.stuck = {r.obj.index: p for r, p in taps.stuck_misses} if taps else {}
        self.times = [f.time for f in frames]
        self.presses = []  # times a key went down
        prev_l = prev_r = False
        for f in frames:
            if f.left and not prev_l:
                self.presses.append(f.time)
            if f.right and not prev_r:
                self.presses.append(f.time)
            prev_l, prev_r = f.left, f.right

    def at(self, t: float) -> tuple[float, float, bool]:
        """Cursor position (interpolated) and whether any key is down at time t."""
        i = bisect.bisect_right(self.times, t)
        if i == 0:
            f = self.frames[0]
            return f.x, f.y, f.left or f.right
        prev = self.frames[i - 1]
        if i >= len(self.frames):
            return prev.x, prev.y, prev.left or prev.right
        nxt = self.frames[i]
        span = nxt.time - prev.time
        k = (t - prev.time) / span if span > 0 else 0.0
        return prev.x + (nxt.x - prev.x) * k, prev.y + (nxt.y - prev.y) * k, prev.left or prev.right

    def presses_between(self, t0: float, t1: float) -> int:
        return bisect.bisect_right(self.presses, t1) - bisect.bisect_left(self.presses, t0)

    def last_release_before(self, t: float) -> int | None:
        i = bisect.bisect_right(self.times, t)
        for j in range(i - 1, 0, -1):
            f, g = self.frames[j], self.frames[j - 1]
            if not (f.left or f.right) and (g.left or g.right):
                return f.time
            if f.left or f.right:
                return None
        return None


def _pct(x: float) -> str:
    return f"{x * 100:.0f}%"


def _sections(times: list[int]) -> list[tuple[int, int, int]]:
    """Group times into (start, end, count) sections."""
    out: list[list[int]] = []
    for t in sorted(times):
        if out and t - out[-1][1] <= SECTION_GAP_MS:
            out[-1][1] = t
            out[-1][2] += 1
        else:
            out.append([t, t, 1])
    return [tuple(s) for s in out]


def _aim_detail(s: Sample, radius: float) -> str:
    """Where the click went relative to the circle, for an aim miss."""
    r, f = s.r, s.f
    t, x, y = min(r.off_target_clicks, key=lambda c: abs(c[0] - r.obj.time))
    px, py = r.obj.position
    dx, dy = x - px, y - py
    dist = math.hypot(dx, dy) / radius
    when = t - r.obj.time
    when_txt = f"{abs(when)}ms {'early' if when < 0 else 'late'}" if when else "on time"
    if f.direction is None or f.distance < 1:
        return f"clicked {when_txt}, {dist:.1f} radii away from the circle"
    along = (dx * f.direction[0] + dy * f.direction[1]) / radius
    side = (-dx * f.direction[1] + dy * f.direction[0]) / radius
    if abs(along) >= abs(side):
        where = f"{abs(along):.1f} radii {'past' if along > 0 else 'short of'} the circle"
    else:
        where = f"{abs(side):.1f} radii to the side of the circle"
    txt = f"clicked {when_txt}, {where}"
    covered = 1 + along * radius / f.distance
    if along < 0 and covered < 0.9:
        if when < -10:
            txt += f": you tapped early, before the cursor arrived (it had covered {_pct(max(covered, 0))} of the way to it)"
        else:
            txt += f": the tap was on time but the cursor was too slow (it had covered only {_pct(max(covered, 0))} of the way to it)"
    elif along > 0.5:
        txt += ": the cursor flew past it"
    return txt


def _object_miss_reason(s: Sample, frames: _Frames, radius: float) -> str:
    r = s.r
    stuck = frames.stuck.get(r.obj.index)
    if stuck is not None:
        return (f"no tap: {stuck.name}, pressed at {timestamp(stuck.down)} for the previous note, was still "
                f"held down, so the key never reset")
    if s.wrong_note is not None:
        o = s.wrong_note
        return (f"you clicked note #{o.obj.index + 1} (due at {timestamp(o.obj.time)}) instead: it was on screen "
                f"at the same time and you read it as the next one")
    if r.miss_reason == "aim" and r.off_target_clicks:
        return _aim_detail(s, radius)
    if r.miss_reason == "notelock":
        return "your tap landed on the next note while this one was still pending (cursor ahead of your taps)"
    if r.miss_reason == "timing":
        return f"you clicked on the circle but {abs(r.hit_error)}ms early, outside the hit window"
    x, y, _ = frames.at(r.obj.time)
    px, py = r.obj.position
    away = math.hypot(x - px, y - py) / radius
    return f"no tap reached it (the cursor was {away:.1f} radii from it at the note's time)"


def _run_title(run: list[Sample]) -> str:
    f = run[0].f
    n, start = len(run), timestamp(run[0].r.obj.time)
    if f.pattern == ALT:
        return f"alt section of {n} notes at {f.bpm:.0f} BPM, spacing {f.run_spacing:.1f} radii (starts {start})"
    if f.pattern == IRREGULAR:
        return f"irregular run of {n} notes snapped to 1/{run[-1].f.divisor} (starts {start})"
    if n <= 3:
        return f"{f.pattern} at {f.bpm:.0f} BPM ({start})"
    return f"{f.pattern} of {n} notes at {f.bpm:.0f} BPM (starts {start})"


def _run_episode(run: list[Sample], frames: _Frames, radius: float) -> Episode | None:
    missed = [s for s in run if s.missed]
    breaks = [s for s in run if s.r.obj.kind == SLIDER and s.r.slider_break_kind in ("tick", "repeat")]
    if not missed and not breaks:
        return None
    first = run[0].f
    n = len(run)
    ep = Episode(time=(missed or breaks)[0].r.obj.time, category=category_of(first.pattern),
                 title=_run_title(run), cost=len(missed) + len(breaks))

    positions = [run.index(s) + 1 for s in missed]
    if missed:
        k = positions[0]
        ep.reasons.append((f"first miss at note {k} of {n}: " if n > 3 else "")
                          + _object_miss_reason(missed[0], frames, radius))
        # Misses right after the first one are usually the same desync, not new mistakes.
        cascade = sum(1 for p in positions[1:] if p <= k + 8)
        if cascade >= 3:
            ep.reasons.append(f"after that you stayed out of sync: {cascade} of the next 8 notes were lost too")
        later = [p for p in positions[1:] if p > k + 8]
        if later:
            ep.reasons.append(f"{len(later)} more miss(es) later (note {', '.join(map(str, later[:6]))}"
                              f"{'...' if len(later) > 6 else ''})")
        if n >= 12 and sum(p > n * 0.75 for p in positions) >= max(1, len(positions) * 0.6):
            ep.reasons.append("most misses are in the last quarter: stamina ran out before the run ended")

    if first.pattern in STREAM_FAMILY + (ALT,) and first.bpm:
        # Taps vs notes, from half a gap before the first note to half a gap after the last.
        half_gap = 15000 / first.bpm * run[0].rate / 2
        taps = frames.presses_between(run[0].r.obj.time - half_gap, run[-1].r.obj.time + half_gap)
        if taps < n:
            ep.reasons.append(f"you tapped {taps} times for {n} notes: {n - taps} tap(s) never happened")
        elif taps > n:
            ep.reasons.append(f"you tapped {taps} times for {n} notes: extra taps (overtapping) threw off the sync")

    if first.pattern == ALT and missed:
        s = missed[0]
        if s.f.angle is not None and s.f.angle < 90:
            ep.reasons.append(f"the first miss came right after a sharp change of direction ({s.f.angle:.0f} degrees)")

    if missed:
        k = run.index(missed[0])
        before = [s.error for s in run[max(0, k - 8):k] if s.error is not None]
        start = [s.error for s in run[:min(8, k)] if s.error is not None]
        if len(before) >= 4 and len(start) >= 4 and k >= 12:
            b, a = sum(before) / len(before), sum(start) / len(start)
            if b - a >= 8:
                ep.reasons.append(f"your taps fell behind: timing went from {a:+.0f}ms to {b:+.0f}ms before the first miss")
            elif a - b >= 8:
                ep.reasons.append(f"you rushed: timing went from {a:+.0f}ms to {b:+.0f}ms before the first miss")

    for s in breaks:
        ep.reasons.append(f"slider at {timestamp(s.r.obj.time)} broke: " + _slider_break_reason(s, frames, radius))
    return ep


def _slider_break_reason(s: Sample, frames: _Frames, radius: float) -> str:
    r = s.r
    t = r.slider_break_time
    x, y, held = frames.at(t)
    what = {"tick": "a slider tick", "repeat": "the repeat (reverse arrow)", "end": "the slider end"}[r.slider_break_kind]
    if not held:
        released = frames.last_release_before(t)
        early = f" {t - released}ms before" if released is not None else ""
        return f"you released the key{early} {what}"
    bx, by = r.obj.position_at(t)
    away = math.hypot(x - bx, y - by) / (radius * FOLLOW_RADIUS_SCALE)
    return f"your cursor left the follow circle before {what} ({away:.1f}x the follow radius from the ball)"


def _accuracy_episodes(samples: list[Sample]) -> list[Episode]:
    """100s and 50s on circles, grouped by pattern category."""
    groups: dict[str, list[Sample]] = {}
    for s in samples:
        if s.acc_eligible:
            groups.setdefault(category_of(s.f.pattern), []).append(s)
    names = {"streams": "streams", "alt": "alt", "irregular": "irregular rhythms", "jumps": "jumps",
             "aim": "other notes"}
    out = []
    for cat, group in groups.items():
        bad = [s for s in group if s.not_300]
        if len(bad) < 3:
            continue
        n100 = sum(s.r.result == 100 for s in bad)
        n50 = len(bad) - n100
        errs = np.array([s.error for s in group])
        bad_errs = np.array([s.error for s in bad])
        early = (bad_errs < 0).mean()
        ep = Episode(bad[0].r.obj.time, "accuracy",
                     f"{n100}x100 and {n50}x50 on {names[cat]} ({_pct(len(bad) / len(group))} of those hits)",
                     cost=n100 * COST_100 + n50 * COST_50)
        if early >= 0.7:
            ep.reasons.append(f"{_pct(early)} of them were early (on average {bad_errs[bad_errs < 0].mean():+.0f}ms): "
                              f"you are ahead of the beat here")
        elif early <= 0.3:
            ep.reasons.append(f"{_pct(1 - early)} of them were late (on average {bad_errs[bad_errs >= 0].mean():+.0f}ms): "
                              f"you are behind the beat here")
        else:
            ep.reasons.append(f"spread both ways (UR {errs.std() * 10:.0f} on {names[cat]}): consistency, not a bias")
        worst = sorted(_sections([s.r.obj.time for s in bad]), key=lambda x: -x[2])[:3]
        ep.reasons.append("worst sections: " + ", ".join(
            f"{timestamp(a)}-{timestamp(b)} ({c})" if b > a else f"{timestamp(a)} ({c})" for a, b, c in worst))
        out.append(ep)
    return out


def _context_episode(group: list[Sample], model: ExpectedModel, category: str, title: str,
                     explain: str) -> Episode | None:
    """Misses in `group` beyond what the player's usual form on these patterns predicts."""
    if not group:
        return None
    obs = sum(s.missed for s in group)
    exp = model.expected(group)
    if obs < 3 or exp <= 0:
        return None
    z = (obs - exp) / math.sqrt(exp)
    if z < 2 or obs < 1.3 * exp:
        return None
    ep = Episode(group[0].r.obj.time, category, title, cost=obs - exp)
    ep.reasons.append(explain.format(obs=obs, exp=exp, ratio=obs / exp))
    return ep


def _setup_episodes(taps: TapStats) -> list[Episode]:
    out = []
    if taps.ghost_presses:
        g = taps.ghost_presses
        times = ", ".join(f"{p.name} {timestamp(p.down)}" for p in g[:6]) + ("..." if len(g) > 6 else "")
        ep = Episode(g[0].down, "setup", f"{len(g)} ghost key press(es): released and re-pressed within 10ms",
                     [f"at {times}"], cost=len(taps.ghost_hits) + 0.3 * len(g))
        if taps.ghost_hits:
            ep.reasons.append(f"{len(taps.ghost_hits)} of them hit a note early "
                              f"(#{', #'.join(str(r.obj.index + 1) for r in taps.ghost_hits[:5])})")
        ep.reasons.append("a finger can't do that: it's switch chatter or rapid trigger set too sensitive")
        out.append(ep)
    if taps.stuck_misses:
        s = taps.stuck_misses
        # These misses are already counted in their stream/alt episode; count them half here.
        out.append(Episode(s[0][0].obj.time, "setup", f"{len(s)} note(s) lost to a key that didn't reset",
                           [", ".join(f"{p.name} at {timestamp(r.obj.time)}" for r, p in s[:6])
                            + ("..." if len(s) > 6 else ""),
                            "the key stayed down from the previous note: lift further, or lower the rapid "
                            "trigger release distance"],
                           cost=0.5 * len(s)))
    return out


def explain_play(samples: list[Sample], frames: list[Frame], radius: float,
                 model: ExpectedModel | None = None, normal_ar_model: ExpectedModel | None = None,
                 taps: TapStats | None = None) -> list[Episode]:
    """Episodes for one play. `model` (recent form) enables the stamina/reading context
    episodes, `normal_ar_model` (recent form at AR <= 10) the high-AR one."""
    fr = _Frames(frames, taps)
    episodes: list[Episode] = []
    end_drops: list[Sample] = []
    i = 0
    while i < len(samples):
        s = samples[i]
        f = s.f
        if f.run_length > 1:
            j = i
            while j + 1 < len(samples) and samples[j + 1].f.pattern == f.pattern \
                    and samples[j + 1].f.run_position == samples[j].f.run_position + 1:
                j += 1
            run = samples[i:j + 1]
            ep = _run_episode(run, fr, radius)
            if ep:
                episodes.append(ep)
            end_drops += [x for x in run if x.r.slider_break_kind == "end" and not x.missed]
            i = j + 1
            continue

        r = s.r
        if s.missed:
            if s.wrong_note is not None:
                title, category = f"misread at note #{r.obj.index + 1}", "reading"
            elif f.pattern == JUMP:
                angle = f" at {f.angle:.0f} degrees" if f.angle is not None else ""
                title = f"jump of {f.distance_radii:.1f} radii{angle}, {f.gap_ms:.0f}ms after the previous note"
                category = "jumps"
            elif f.pattern == IRREGULAR:
                title, category = f"note snapped to 1/{f.divisor} after a rhythm change", "irregular"
            else:
                title = f"missed {'slider' if r.obj.kind == SLIDER else 'note'} #{r.obj.index + 1}"
                category = "aim"
            episodes.append(Episode(r.obj.time, category, title, [_object_miss_reason(s, fr, radius)], cost=1))
        elif r.obj.kind == SLIDER and r.slider_break_kind in ("tick", "repeat"):
            episodes.append(Episode(r.slider_break_time, "sliders",
                                    f"slider break (slider starts {timestamp(r.obj.time)})",
                                    [_slider_break_reason(s, fr, radius)], cost=1))
        elif r.obj.kind == SLIDER and r.slider_break_kind == "end":
            end_drops.append(s)
        i += 1

    if end_drops:
        times = ", ".join(timestamp(s.r.obj.time) for s in end_drops[:6]) + ("..." if len(end_drops) > 6 else "")
        released = sum(1 for s in end_drops if not fr.at(s.r.slider_break_time)[2])
        why = (f"{released} released too early, {len(end_drops) - released} left the follow circle"
               if released else "the cursor left the follow circle before the end")
        episodes.append(Episode(end_drops[0].r.obj.time, "sliders",
                                f"{len(end_drops)} slider end(s) dropped: each turns a 300 into a 100",
                                [f"at {times}", why], cost=len(end_drops) * END_DROP_COST))

    episodes += _accuracy_episodes(samples)
    if taps is not None:
        episodes += _setup_episodes(taps)

    if model is not None:
        high = [s for s in samples if s.f.load_nps >= STAMINA_HIGH_NPS]
        sections = [(a, b) for a, b, c in _sections([s.r.obj.time for s in high]) if c >= 20]
        where = ", ".join(f"{timestamp(a)}-{timestamp(b)}" for a, b in sections[:4])
        ep = _context_episode(
            high, model, "stamina", f"dense sections ({STAMINA_HIGH_NPS:g}+ notes/s sustained): {where}",
            "you missed {obs} notes there, where ~{exp:.0f} are usual for you on the same patterns ({ratio:.1f}x): "
            "your form drops when the density is sustained")
        if ep:
            episodes.append(ep)
        dense = [s for s in samples if s.f.visible >= DENSE_SCREEN]
        ep = _context_episode(
            dense, model, "reading", f"busy screens ({DENSE_SCREEN}+ notes visible)",
            "you missed {obs} notes with a crowded screen, where ~{exp:.0f} are usual for you on the same "
            "patterns ({ratio:.1f}x)")
        if ep:
            episodes.append(ep)
    if normal_ar_model is not None and samples and samples[0].ar > HIGH_AR:
        ep = _context_episode(
            samples, normal_ar_model, "dt", f"AR {samples[0].ar:.1f} play",
            "you missed {obs} notes, where ~{exp:.0f} are usual for you on the same patterns at normal AR "
            "({ratio:.1f}x): the higher AR is costing you")
        if ep:
            episodes.append(ep)
    return episodes
