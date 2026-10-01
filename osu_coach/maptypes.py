"""Map types: the main skillset of a map from its intense sections, and its aim control.

Only the intense sections count: 4-second windows reaching 70% of the map's highest note density
or mean cursor speed. Each of their notes goes to one kind:
  tech            fast sliders (25+ radii/s, 32.5 with DT), weighted by speed; kicksliders (shorter
                  than 3.4 radii) in a row weigh 0.75 of the previous one; all of it scaled by how
                  the sliders compare with the map's aim speed (slower than the jumps, they are
                  played as part of the jumps and the rest goes to jump)
  finger control/burst  short groups (2-9 notes) from 165 BPM under 2 radii, and off-grid rhythm
                  (1/3, 1/6, 1/8...): one kind, whether the groups change technique or not
  stream / alt    runs of 4+ evenly timed notes: 2-4 radii apart alt at any speed; closer, stream
                  from 165 BPM and alt below; wider than 4 radii, jumps
  slider aim      spaced aim into or out of a slider that has to be followed
  alt             aim of 2-4 radii outside runs too (1/2 between sliders, single notes)
  jump            aim beyond 4 radii
Below 4 stars (nomod) alt needs notes at 125 BPM or faster (as 1/4 notes, as played: DT can bring them there):
slower ones are too slow for alternating, so their aim goes to jump and their close runs to stream.
HR doesn't change the kind of map (only AR, for reading): maps with HR are read without it.

The main kind needs 25% of the intense notes and 1.5 times the second one (tech: fast sliders
over 37% of the notes); otherwise the map is a hybrid of its two largest kinds (jump + slider aim
is jump, tech + slider aim is tech). Reading is a tag next to the type, like aim control: from 5 stars
(nomod), effective AR 8.5 or lower, the finger control/burst share x 1.8 x the aim control ratio x an AR factor (1 at
AR 8, more as it goes down) from 0.7. Speed is a tag too: stream or finger control/burst maps (alone or in a hybrid)
over 240 BPM as played, the map's main BPM (x1.5 with DT). Precision is a tag too: CS over 6 as played (x1.3 with
HR). Reading goes with HR too when HR brings AR over 8.5 (the analysis is read without HR, AR with it).

Aim control is a separate factor, a property of the map (read without mods: DT, HT and HR don't
change it). The cursor path is a sequence of reference points: the notes, and for sliders of 2.4+
radii the head, each tick and the end (the slider ball lets the cursor leave up to 1.2 radii early,
so the end is the point within that reach closest to the next note). Points under 0.3 radii from
the previous one (stacks) and under 20 ms (hit together) are skipped. At each point of the intense
sections, with s the spacing (radii), t the time (s), v = s/t and theta the angle (0 = straight
back, 180 = straight on):
  accelerations   the speed change as a ratio, |v_next / v_curr - 1| (speeding up counts more than
                  slowing down), only at the same rhythm (within 10%), x (t / 1/4 at 180 BPM)^0.5;
                  changes in a row build up (A = 0.75 A + 0.25 change), 9 * A^0.5
  narrow angles   (s / 2)^-0.5 / (0.6 t / 1/4 at 180 BPM)^2 * |cos(theta/2)|: sharp turns on close notes
  wide angles     (8 s / 4)^0.75 * (t / 1/2 at 180 BPM)^0.25 * sin^2(theta/2) * (v / 24)^0.5
  angle change    |theta_next - theta_curr| / 180, towards a wide angle up to e^0.6 more and towards a
                  narrow one e^0.6 less, plus 0.3 sin^2(theta) of turning (both only between moves of 2+
                  radii: streams curve all the time), x (v / 24)^0.5, x (1/2 at 180 BPM / gap between the
                  taps)^2 (in map time: DT doesn't change it); only between moves of 0.5+ radii; changes
                  in a row build up (S = 0.75 S + 0.25 change), 0.5 * S^1.5
The accumulations restart at breaks and at each intense section; the score is the mean over the
intense points. A map has aim control when it scores at least 0.95 times what maps of its star
rating usually score, 1.29 * stars^0.95 (fitted on random ranked maps, nomod).
"""

import math
from collections import Counter

import numpy as np

from .locate import CACHE_DIR
from .mods import Mods

MAPTYPE_CACHE = CACHE_DIR / "map_types.json"
MAPTYPE_VERSION = "22"                # bump when the analysis changes (22: finger control from 140 BPM)

WINDOW_S = 4.0
INTENSE = 0.70                       # a window counts at this share of the map's densest / fastest one
MIN_SHARE = 0.25                     # a main kind needs this share of the intense notes...
DOMINANT = 1.5                       # ...and this many times the second one
READING_MIN_STARS = 5.0              # reading only from this nomod star rating (easier maps have low AR anyway)
READING_LOW_AR = 8.5                 # at this effective AR or lower...
READING_FINGER_WEIGHT = 1.8          # ...finger control/burst share x this (100% = 1.8, like a strong aim control)...
READING_MIN = 0.7                    # ...times the aim control ratio times the AR factor from this much make it reading
READING_AR = (3.25, 4.0, 1.25, 8.0)  # AR factor (a / (AR / b + c)) ** d: 1 at AR 8, more as the AR goes down
SPEED_BPM = 240.0                    # speed tag: stream or finger control/burst maps over this BPM as played
PRECISION_CS = 6.0                   # precision tag: CS over this as played (HR x1.3, EZ /2)
MIN_STARS = 3.0                      # maps under this star rating (nomod) get no type: too easy for one to matter

FINGER_BPM = 140                     # short bursts (under ALT_MIN_SPACING) from this 1/4 BPM are finger control
BPM_TOLERANCE = 1.0                  # the BPM limits accept runs this much slower (139.9 is 140)
RUN_MAX_SPACING = 4.0                # runs of 4+ spaced wider than this are jumps
ALT_MIN_SPACING = 2.0                # runs spaced this much or more are alt at any speed
ALT_MIN_STARS = 4.0                  # below this star rating (nomod)...
ALT_SLOW_BPM = 125.0                 # ...alt needs notes this fast (1/4 BPM), else jump (or stream for close runs)
GRID = {1, 2, 4}                     # rhythm changes among these snaps are ordinary

FAST_SLIDER = 25.0                   # radii/s
FAST_SLIDER_DT = 1.3                 # with DT the threshold is this much higher
TECH_MIN_SHARE = 0.37                # fast sliders over this share of the intense notes: tech
KICK_MAX_LENGTH = 3.4                # radii: fast sliders shorter than this barely leave the follow circle
KICK_DECAY = 0.75                    # each kickslider in a row weighs this much of the previous one
KICK_CHAIN_MS = 300.0
SLIDER_AIM_RATIO_CAP = 1.5
LONG_SLIDER = 5.0                    # radii of path: a slider this long has to be followed
CURVY_SLIDER = (3.0, 1.3)            # radii of path, and path / straight head-to-tail distance

# aim control (see the module docstring)
AIM_STREAM = (2.0, 60 / 180 / 4)     # reference stream: radii, seconds (1/4 at 180 BPM)
AIM_JUMP = (4.0, 60 / 180 / 2)       # reference jump: radii, seconds (1/2 at 180 BPM), so 24 radii/s
AIM_SLIDER_AS_NOTE = 2.4             # radii of path: shorter sliders are aimed like notes
AIM_SLIDER_BALL = 1.2                # radii the cursor can leave a slider's end early
AIM_NULL_MOVE = 0.3                  # radii: shorter moves (stacked notes) have no direction to control
AIM_MIN_GAP_MS = 20.0                # points closer than this are hit together
AIM_SAME_RHYTHM = 0.10               # accelerations count between gaps this close in time
AIM_ACCEL = (9.0, -0.5, 0.75, 0.5)   # weight, time exponent, build-up, power
AIM_NARROW = (0.6, -0.5, 2.0)        # time scale, spacing exponent, time exponent
AIM_WIDE = (8.0, 0.75, -0.25, 2.0, 0.5)   # spacing scale, spacing exp., time exp., sine exp., speed exp.
AIM_CHANGE = (0.5, 1.5, 0.75)        # weight, power, build-up
AIM_CHANGE_MIN = 0.5                 # radii: angle changes count between moves at least this long
AIM_CHANGE_SPEED = 0.5               # exponent of the cursor speed (vs the reference jump)
AIM_TAP = 2.0                        # exponent of how soon the notes are tapped (vs the reference jump)
AIM_TURN = (0.3, 2.0)                # turning: weight, exponent of sin(theta)
AIM_WIDE_ARRIVAL = 0.6               # changes towards wide angles weigh up to e^this more (narrow: less)
AIM_JUMP_MIN = 2.0                   # radii: turning and the wide-arrival weight count between jumps only
AIM_EXPECTED = (1.29, 0.95)          # usual score at a star rating: a * stars^b (random ranked maps, nomod)
AIM_CONTROL_RATIO = 0.95             # aim control from this share of the usual score

KINDS = ("stream", "jump", "slider aim", "alt", "finger", "tech")
NAMES = {"stream": "Stream", "jump": "Jump", "slider aim": "Slider aim", "alt": "Alt",
         "finger": "Finger control/burst", "tech": "Tech"}


def _fast_threshold(rate: float) -> float:
    return FAST_SLIDER * (FAST_SLIDER_DT if rate > 1 else 1.0)


def _run_kind(f) -> str | None:
    """stream / alt for a note in a run of 4+; None otherwise (or too spaced: jumps)."""
    from .features import ALT, STREAM_FAMILY, STREAM_MIN_BPM
    if f.run_length < 4 or not f.bpm or f.pattern not in STREAM_FAMILY + (ALT,) or f.run_spacing > RUN_MAX_SPACING:
        return None
    if f.run_spacing >= ALT_MIN_SPACING:
        return "alt"
    return "stream" if f.bpm >= STREAM_MIN_BPM - BPM_TOLERANCE else "alt"


def _followed(s) -> bool:
    """A slider that takes aim to follow: fast, long or curved."""
    o = s.r.obj
    if o.kind != "slider" or o.span_duration <= 0:
        return False
    length = o.path.length / s.f._radius
    if length / (o.span_duration / s.rate / 1000) >= _fast_threshold(s.rate) or length >= LONG_SLIDER:
        return True
    straight = math.dist(o.path.position_at(0.0), o.path.position_at(1.0)) / s.f._radius
    return length >= CURVY_SLIDER[0] and length >= CURVY_SLIDER[1] * max(straight, 1e-6)


def _intense_windows(samples) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Window of each object, whether each window is intense, notes per window."""
    from .features import FIRST
    t = np.array([s.r.obj.time / s.rate / 1000 for s in samples])
    speed = np.array([0.0 if s.f.pattern == FIRST else s.f.distance_radii / max(s.f.move_ms, 1.0) * 1000 for s in samples])
    w = ((t - t.min()) // WINDOW_S).astype(int)   # from the earliest object: some maps list them out of order
    counts = np.bincount(w)
    aim = np.bincount(w, weights=speed) / np.maximum(counts, 1)
    return w, (counts >= INTENSE * counts.max()) | (aim >= INTENSE * aim.max()), counts


def _aim_points(samples) -> list[tuple[float, float, float, int, float]]:
    """(x, y, real time ms, object index, radius) along the cursor path: notes, and for sliders long enough
    to follow their head, ticks and end (moved towards the next note by the slider ball's reach)."""
    pts = []
    for n, s in enumerate(samples):
        o, r = s.r.obj, s.f._radius
        pts.append((*o.position, o.time / s.rate, n, r))
        if o.kind == "slider" and o.span_duration > 0 and o.path.length / r >= AIM_SLIDER_AS_NOTE:
            ticks = [(*o.position_at(p.time), p.time / s.rate) for p in o.score_points]
            if ticks and n + 1 < len(samples):
                ex, ey, et = ticks[-1]
                nx, ny = samples[n + 1].r.obj.position
                dist = math.hypot(nx - ex, ny - ey)
                if dist > 0:
                    step = min(AIM_SLIDER_BALL * r, dist)
                    ticks[-1] = (ex + (nx - ex) / dist * step, ey + (ny - ey) / dist * step, et)
            pts.extend((x, y, t, n, r) for x, y, t in ticks)
    out = pts[:1]
    for p in pts[1:]:
        q = out[-1]
        if math.hypot(p[0] - q[0], p[1] - q[1]) / p[4] >= AIM_NULL_MOVE and p[2] - q[2] >= AIM_MIN_GAP_MS:
            out.append(p)
    return out


def _angle(a, b, c) -> float:
    """Angle at b of the path a -> b -> c, radians: 0 = straight back, pi = straight on."""
    ux, uy, wx, wy = a[0] - b[0], a[1] - b[1], c[0] - b[0], c[1] - b[1]
    return math.acos(max(-1.0, min(1.0, (ux * wx + uy * wy) / (math.hypot(ux, uy) * math.hypot(wx, wy)))))


def aim_control(samples) -> float:
    """Aim control score of a map read without mods (see the module docstring)."""
    if not samples or len(samples) < 3:
        return 0.0
    w, intense_w, _ = _intense_windows(samples)
    pts = _aim_points(samples)
    obj_t = [s.r.obj.time for s in samples]     # map time: how soon the notes are tapped, whatever the rate
    (s_stream, t_stream), (s_jump, t_jump) = AIM_STREAM, AIM_JUMP
    acc_w, acc_z, acc_r, acc_q = AIM_ACCEL
    nar_b, nar_x, nar_y = AIM_NARROW
    wide_c, wide_f, wide_g, wide_a, wide_v = AIM_WIDE
    ch_e, ch_q, ch_r = AIM_CHANGE
    values = []
    strain = acc_strain = 0.0
    for i in range(2, len(pts)):
        a0, a1, a2 = pts[i - 2], pts[i - 1], pts[i]
        if not intense_w[w[a2[3]]]:
            strain = acc_strain = 0.0    # each intense section builds up on its own
            continue
        r = a2[4]
        s_curr, s_next = math.dist(a0[:2], a1[:2]) / r, math.dist(a1[:2], a2[:2]) / r
        t_curr, t_next = (a1[2] - a0[2]) / 1000, (a2[2] - a1[2]) / 1000
        if not (0 < t_curr <= 1 and 0 < t_next <= 1):
            strain = acc_strain = 0.0    # a break
            continue
        v_curr, v_next = s_curr / t_curr, s_next / t_next
        theta = _angle(a0, a1, a2)
        raw_acc = 0.0
        if abs(t_next / t_curr - 1) <= AIM_SAME_RHYTHM:
            raw_acc = abs(v_next / v_curr - 1) / (t_next / t_stream) ** acc_z
        acc_strain = acc_r * acc_strain + (1 - acc_r) * raw_acc
        narrow = (s_next / s_stream) ** nar_x / (nar_b * t_next / t_stream) ** nar_y * abs(math.cos(theta / 2))
        wide = ((wide_c * s_next / s_jump) ** wide_f / (t_next / t_jump) ** wide_g * math.sin(theta / 2) ** wide_a
                * (v_next * t_jump / s_jump) ** wide_v)
        raw = 0.0
        if i >= 3 and s_curr >= AIM_CHANGE_MIN and s_next >= AIM_CHANGE_MIN:
            b0 = pts[i - 3]
            if math.dist(b0[:2], a0[:2]) > 0 and a0[2] - b0[2] <= 1000:
                delta = abs(theta - _angle(b0, a0, a1)) / math.pi
                if s_curr >= AIM_JUMP_MIN and s_next >= AIM_JUMP_MIN:
                    delta *= math.exp(AIM_WIDE_ARRIVAL * (2 * math.sin(theta / 2) ** 2 - 1))
                    delta += AIM_TURN[0] * math.sin(theta) ** AIM_TURN[1]
                n = a2[3]
                gap = (obj_t[n] - obj_t[n - 1]) / 1000 if n else 0.0
                tap = (t_jump / gap) ** AIM_TAP if 0 < gap <= 1 else 1.0
                raw = delta * (v_next * t_jump / s_jump) ** AIM_CHANGE_SPEED / t_jump * tap
        strain = ch_r * strain + (1 - ch_r) * raw
        values.append(acc_w * acc_strain ** acc_q + narrow + wide + ch_e * strain ** ch_q)
    return float(np.mean(values)) if values else 0.0


def aim_expected(stars: float) -> float:
    """What maps of this star rating (nomod) usually score for aim control."""
    a, b = AIM_EXPECTED
    return a * max(stars, 0.0) ** b


def analyse(samples, ar: float, aim_samples=None, stars: float = 0.0) -> dict | None:
    """Shares of each kind among the intense notes, aim control and a few details. Aim control is read from
    `aim_samples` (the map without mods; the same samples when None) against the nomod star rating."""
    from .features import FIRST, STREAM_FAMILY
    ss = samples
    if not ss or len(ss) < 50:
        return None
    speed = np.array([0.0 if s.f.pattern == FIRST else s.f.distance_radii / max(s.f.move_ms, 1.0) * 1000 for s in ss])
    w, intense_w, counts = _intense_windows(ss)
    idx = np.where(intense_w[w])[0]
    n = max(len(idx), 1)
    k = Counter()
    kick_weight, last_kick = 1.0, None
    aim_speeds, fast_speeds = [], []
    stream_bpm, alt_bpm = [], []
    easy = 0 < stars < ALT_MIN_STARS
    for i in idx:
        s, f = ss[i], ss[i].f
        prev = ss[i - 1].f if i > 0 else None
        o = s.r.obj
        rk = _run_kind(f)
        no_alt = easy and (f.bpm or 0) < ALT_SLOW_BPM - BPM_TOLERANCE
        if rk == "alt" and no_alt:
            rk = "stream" if f.run_spacing < ALT_MIN_SPACING else None    # spaced runs: aimed like jumps
        short = (f.pattern in STREAM_FAMILY and 2 <= f.run_length <= 9 and (f.bpm or 0) >= FINGER_BPM - BPM_TOLERANCE
                 and f.run_spacing < ALT_MIN_SPACING)
        off_grid = (prev is not None and f.pattern != FIRST and f.gap_ms < 1000 and f.divisor and prev.divisor
                    and f.divisor != prev.divisor and not {f.divisor, prev.divisor} <= GRID)
        v = o.path.length / f._radius / (o.span_duration / s.rate / 1000) if o.kind == "slider" and o.span_duration > 0 else 0.0
        fast_v = _fast_threshold(s.rate)
        weight = 1.0
        if v >= fast_v and o.path.length / f._radius < KICK_MAX_LENGTH:
            now = o.time / s.rate
            kick_weight = kick_weight * KICK_DECAY if last_kick is not None and now - last_kick <= KICK_CHAIN_MS else 1.0
            weight, last_kick = kick_weight, o.end_time / s.rate
        if f.pattern != FIRST and f.distance_radii >= 2:
            aim_speeds.append(speed[i])

        if v >= fast_v:
            fast_speeds.append(v)
            k["tech"] += weight * v / fast_v
            k["tech sliders"] += weight
        elif off_grid or short:
            k["finger"] += 1
        elif rk == "stream":
            k["stream"] += 1
            stream_bpm.append(f.bpm)
        elif rk == "alt":
            k["alt"] += 1
            alt_bpm.append(f.bpm)
        elif f.aim in ("alt", "jump"):
            if _followed(s) or (i > 0 and _followed(ss[i - 1])):
                k["slider aim"] += 1
            elif f.distance_radii <= RUN_MAX_SPACING and not no_alt:
                k["alt"] += 1
            else:
                k["jump"] += 1
        if prev is not None and f.pattern != FIRST and f.gap_ms < 1000 and ((short and f.transitions > 0) or off_grid):
            k["finger changes"] += 1
    # fast sliders slower than the map's jumps are played as part of the jumps
    ratio = (min(float(np.median(fast_speeds)) / float(np.median(aim_speeds)), SLIDER_AIM_RATIO_CAP)
             if fast_speeds and aim_speeds else SLIDER_AIM_RATIO_CAP)
    k["jump"] += k["tech sliders"] * max(0.0, 1 - ratio)
    k["tech"] *= ratio
    k["tech sliders"] *= ratio
    out = {key: k[key] / n for key in KINDS}
    out.update({
        "tech sliders": k["tech sliders"] / n,
        "finger changes": k["finger changes"] / k["finger"] if k["finger"] else 0.0,
        "aim control": aim_control(ss if aim_samples is None else aim_samples),
        "aim expected": aim_expected(stars),
        "stars": stars,
        "ar": ar,
        "density": float(counts[intense_w].sum() / (intense_w.sum() * WINDOW_S)),
        "stream bpm": float(np.median(stream_bpm)) if stream_bpm else 0.0,
        "alt bpm": float(np.median(alt_bpm)) if alt_bpm else 0.0,
    })
    return out


def category(a: dict) -> str:
    """Main skillset, or a hybrid of the two largest kinds."""
    ranked = sorted(((a[key], key) for key in KINDS), reverse=True)
    (s1, k1), (s2, k2) = ranked[0], ranked[1]
    if a["tech sliders"] > TECH_MIN_SHARE:
        name = "Tech"
    elif s1 >= MIN_SHARE and s1 >= DOMINANT * s2:
        name = NAMES[k1]
    elif {k1, k2} == {"jump", "slider aim"}:
        name = "Jump"
    elif {k1, k2} == {"tech", "slider aim"}:
        name = "Tech"
    else:
        name = f"{NAMES[k1]} + {NAMES[k2].lower()}"
    return name


BORDER_AIM = 0.05                    # aim control ratio this close to AIM_CONTROL_RATIO
BORDER_MAIN = 0.075                  # largest / second kind this close to DOMINANT


def borderline(a: dict) -> list[str]:
    """The labels a small change of the map would flip: "aim control", "hybrid"."""
    out = []
    if a.get("aim expected") and abs(aim_ratio(a) - AIM_CONTROL_RATIO) < BORDER_AIM:
        out.append("aim control")
    (s1, _), (s2, _) = sorted(((a[k], k) for k in KINDS), reverse=True)[:2]
    if s1 >= MIN_SHARE and s2 > 0 and abs(s1 / s2 - DOMINANT) < BORDER_MAIN:
        out.append("hybrid")
    return out


def map_stars(a: dict) -> float:
    """The nomod star rating the analysis was made with (older analyses: from the usual aim control score)."""
    if a.get("stars") is not None:
        return a["stars"]
    e, (k, b) = a.get("aim expected") or 0.0, AIM_EXPECTED
    return (e / k) ** (1 / b) if e > 0 else 0.0


def aim_ratio(a: dict) -> float:
    """Aim control against what maps of the same star rating usually have (1 = usual)."""
    return a["aim control"] / a["aim expected"] if a.get("aim expected") else 0.0


def has_aim_control(a: dict) -> bool:
    return aim_ratio(a) >= AIM_CONTROL_RATIO


def reading_ar_factor(ar: float) -> float:
    """How much the (effective) AR weighs on reading: about 0.74 at AR 8.5, 1 at 8, 1.90 at 7, 3.80 at 6."""
    a, b, c, d = READING_AR
    return (a / (ar / b + c)) ** d


def reading_score(a: dict) -> float:
    """Short groups times changing aim times low AR: the finger control/burst share (weighted so it compares with the
    aim control ratio, which is about 1 on a usual map) times the aim control ratio times the AR factor."""
    return a["finger"] * READING_FINGER_WEIGHT * aim_ratio(a) * reading_ar_factor(a["ar"])


def has_reading(a: dict) -> bool:
    """A tag next to the type, like aim control: low AR on short groups and changing aim, on a map hard enough for
    AR to matter; many notes on screen, hard to read (dense long streams at low spacing are not: they read easily)."""
    return map_stars(a) >= READING_MIN_STARS and a["ar"] <= READING_LOW_AR and reading_score(a) >= READING_MIN


SPEED_KINDS = ("stream", "finger")


def is_speed(kind: str | None, bpm: float | None) -> bool:
    """A stream or finger control/burst type (alone or in a hybrid) over SPEED_BPM, the map's main BPM as played."""
    parts = [p.strip().lower() for p in (kind or "").split("+")]
    return bool(bpm) and bpm > SPEED_BPM and any(NAMES[k].lower() in parts for k in SPEED_KINDS)


def has_speed(a: dict) -> bool:
    """A tag next to the type: needs the main BPM as played ("bpm"), added by types() (not in older analyses)."""
    return is_speed(category(a), a.get("bpm"))


def is_precision(cs: float | None) -> bool:
    """Small circles: CS over PRECISION_CS as played."""
    return bool(cs) and cs > PRECISION_CS


def played_cs(cs: float | None, mods: int) -> float:
    """The map's CS as played (HR x1.3 up to 10, EZ halves it)."""
    from .difficulty import Difficulty
    return Difficulty.from_map(cs or 4.0, 9.0, 8.0, mods).cs


def has_precision(a: dict) -> bool:
    """A tag next to the type: needs the CS as played ("cs"), added by types() (not in older analyses)."""
    return is_precision(a.get("cs"))


def too_easy(stars: float | None) -> bool:
    """Under MIN_STARS (nomod; 0 = unknown, classified): no type."""
    return bool(stars) and stars < MIN_STARS


TOP_KINDS_SHOWN, TOP_KIND_MIN = 2, 0.20   # skillsets shown with their share: the first two, from 20%


def top_kinds(a: dict) -> str:
    """The map's first skillsets with their share of the intense notes (tech: as a share of notes), if they matter."""
    share = {**a, "tech": a["tech sliders"]}
    ranked = sorted(((share[k], k) for k in KINDS), reverse=True)[:TOP_KINDS_SHOWN]
    return ", ".join(f"{NAMES[k].lower()} {s:.0%}" for s, k in ranked if s >= TOP_KIND_MIN)


def label(a: dict) -> str:
    shares = top_kinds(a)
    extra = f" ({shares})" if shares else ""
    extra += f", aim control x{aim_ratio(a):.2f}" if has_aim_control(a) else ""
    extra += ", reading" if has_reading(a) else ""
    extra += f", speed {a['bpm']:.0f} BPM" if has_speed(a) else ""
    extra += f", precision CS {a['cs']:.1f}" if has_precision(a) else ""
    border = borderline(a)
    return category(a) + extra + (f" (borderline: {', '.join(border)})" if border else "")


def _job(args):
    path, mods, ar, stars = args
    from .collect import map_samples
    samples = map_samples(path, mods)
    # aim control is the map's, read without mods changing speed or size
    plain = mods & ~int(Mods.DoubleTime | Mods.Nightcore | Mods.HalfTime | Mods.HardRock | Mods.Easy)
    try:
        return analyse(samples, ar, samples if plain == mods else map_samples(path, plain), stars)
    except Exception:   # a malformed map gets no type instead of stopping every search it turns up in
        return None


def types(songs, items, progress=None) -> dict[str, dict]:
    """Analysis of each (MapInfo, mods), by "md5:mods"; cached."""
    from .advice import effective_ar
    from .difficulty import Difficulty
    from .mods import clock_rate
    from .recommend import _run_cached
    jobs = []
    for m, mods in items:
        ar = effective_ar(Difficulty.from_map(m.cs, m.ar, m.od, mods), clock_rate(mods))
        # HR doesn't change the kind of map: read without it (it only changes AR)
        jobs.append((f"{m.md5}:{mods}", (str(songs / m.path), mods & ~int(Mods.HardRock), ar, m.stars.get(0, 0.0))))
    out = _run_cached(MAPTYPE_CACHE, MAPTYPE_VERSION, jobs, _job, progress, "map types")
    # the main BPM (x1.5 with DT) and CS (x1.3 with HR) as played for the speed and precision tags: known from
    # the map, not analysed
    for m, mods in items:
        k = f"{m.md5}:{mods}"
        if out.get(k):
            out[k] = dict(out[k], bpm=m.bpm * clock_rate(mods), cs=played_cs(m.cs, mods))
        if too_easy(m.stars.get(0)):
            out[k] = None
    return out


TYPE_ALIASES = {"jump": "jump", "stream": "stream", "alt": "alt", "finger": "finger control/burst",
                "fingercontrol": "finger control/burst", "burst": "finger control/burst", "tech": "tech",
                "slider": "slider aim", "reading": "reading", "speed": "speed", "precision": "precision"}


def parse_types(text: str) -> tuple[set[str], set[str], bool]:
    """--type: kinds a map may have (hybrids included), kinds it must have alone ("=alt"), and "aim" for aim control.
    Raises ValueError on an unknown name."""
    any_of, only, aim = set(), set(), False
    for word in (w.strip().lower() for w in text.split(",") if w.strip()):
        if word in ("aim", "aimcontrol", "aim-control"):
            aim = True
            continue
        pure = word.startswith("=")
        name = TYPE_ALIASES.get(word.lstrip("=").replace(" ", ""))
        if name is None:
            raise ValueError(f"unknown map type {word!r} (jump, stream, alt, finger (or burst), tech, slider, reading, speed, precision, aim)")
        (only if pure else any_of).add(name)
    return any_of, only, aim


def matches(a: dict | None, wanted: tuple[set[str], set[str], bool]) -> bool:
    if not a:
        return False
    any_of, only, aim = wanted
    for tag, has in (("reading", has_reading), ("speed", has_speed), ("precision", has_precision)):   # tags, not types
        if tag in any_of | only:
            if not has(a):
                return False
            any_of, only = any_of - {tag}, only - {tag}
    parts = [p.strip().lower() for p in category(a).split("+")]
    if (any_of or only) and not (any_of & set(parts) or (len(parts) == 1 and parts[0] in only)):
        return False
    return has_aim_control(a) or not aim
