"""Setup advice: tablet area / mouse sensitivity and keyboard (rapid trigger) settings.

Aim calibration follows the idea of rgbeing/osu-aim-analyzer: regress the click
error on the movement that led to the note. An error that grows in proportion
to jump distance is a scaling problem (area / sensitivity); one that is
rotated with the movement is a rotation problem. The area advice follows the
sign of the mean scaling alone (the player's choice): underaim -> a smaller
area, overaim -> a larger one, from the MIN_SCALE setting up.
"""

import math
from dataclasses import dataclass

import numpy as np

from .advice import MIN_EPISODES, Insight, Sample
from .features import JUMP
from .keys import TapStats
from .setup import Setup

MIN_JUMPS = 200
MIN_SCALE = 0.03       # overaim/underaim below 3% of the distance isn't worth changing settings for
MIN_AXIS_DIFF = 0.03
ASPECT_AXIS = 1.5          # a jump is horizontal (vertical) when it moves this many times more along x (y)
ASPECT_MIN_JUMPS = 100     # jumps each axis needs for the aspect ratio advice
ASPECT_MIN_RATIO = 1.3     # one axis must miss this many times as often as the other...
ASPECT_MIN_SCALE = 0.01    # ...and over/undershoot by at least this share of the distance to change that side
MIN_ROTATION_DEG = 1.5
MIN_OFFSET_RADII = 0.2
T_MIN = 3.0
GHOSTS_PER_1000 = 1.0
MIN_GHOSTS = 5
STUCK_SHARE = 0.10     # of the misses inside streams/alt
MIN_STUCK = 10


@dataclass
class _Fit:
    slope: float
    t: float
    intercept: float


def _fit(x: np.ndarray, y: np.ndarray) -> _Fit | None:
    if len(x) < 10:
        return None
    X = np.column_stack([np.ones_like(x), x])
    b, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ b
    s2 = resid @ resid / (len(y) - 2)
    cov = s2 * np.linalg.inv(X.T @ X)
    se = math.sqrt(cov[1, 1])
    return _Fit(b[1], b[1] / se if se > 0 else 0.0, b[0])


CLICK_COLUMNS = 12


def jump_clicks(samples: list[Sample]) -> np.ndarray:
    """Rows (distance, err_along, err_perp, move_x, move_y, err_x, err_y, speed, radius, play, missed, played_at)
    for jumps clicked within 2 radii of the note (further clicks aren't aim, they're misreads); played_at: when the
    play was played (unix seconds, 0 if unknown), so the advice can leave out plays made before a setup change."""
    rows = []
    for s in samples:
        f, r = s.f, s.r
        if f.pattern != JUMP or f.direction is None:
            continue
        click = r.cursor
        if click is None and r.off_target_clicks and s.wrong_note is None:
            _, x, y = min(r.off_target_clicks, key=lambda c: abs(c[0] - r.obj.time))
            click = (x, y)
        if click is None:
            continue
        px, py = r.obj.position
        ex, ey = click[0] - px, click[1] - py
        if math.hypot(ex, ey) > 2 * f._radius:
            continue
        dx, dy = f.direction
        rows.append((f.distance, ex * dx + ey * dy, -ex * dy + ey * dx, f.distance * dx, f.distance * dy,
                     ex, ey, f.distance / max(f.move_ms, 1.0), f._radius, s.play, float(s.missed),
                     float(getattr(s, "played_at", 0.0))))
    return np.array(rows) if rows else np.empty((0, CLICK_COLUMNS))


def click_rows(clicks) -> np.ndarray:
    """Saved clicks as an array; older ones (11 columns, no play time) get a 0 time."""
    a = np.asarray(clicks, dtype=float)
    if a.size == 0:
        return np.empty((0, CLICK_COLUMNS))
    a = a.reshape(len(a), -1)
    return np.column_stack([a, np.zeros(len(a))]) if a.shape[1] == CLICK_COLUMNS - 1 else a


PLAY_AIM_MIN_JUMPS = 20   # jumps one play needs for its overaim / underaim to mean something


def play_aim(samples: list[Sample]) -> dict | None:
    """Overaim (+) or underaim (-) in one play, as a share of the jump distance (the same fit as the advice)."""
    a = jump_clicks(samples)
    if len(a) < PLAY_AIM_MIN_JUMPS:
        return {"jumps": len(a), "scale": None, "min_jumps": PLAY_AIM_MIN_JUMPS, "ok": MIN_SCALE}
    fit = _fit(a[:, 0], a[:, 1])
    return {"jumps": len(a), "scale": fit.slope if fit else None, "t": fit.t if fit else None,
            "min_jumps": PLAY_AIM_MIN_JUMPS, "ok": MIN_SCALE}


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.0f}%"


def _scale_advice(k: float, kx: float | None, ky: float | None, setup: Setup) -> str:
    """What to change so that the cursor travels 1/(1+k) as far for the same hand movement."""
    if setup.device == "mouse":
        change = "lower" if k > 0 else "raise"
        tip = f"{change} your sensitivity by about {_pct(k / (1 + k))}"
        if setup.sens:
            tip += f" (in-game sens {setup.sens:g} -> {setup.sens / (1 + k):.2f})"
        return tip + "."
    area = "increase" if k > 0 else "decrease"
    if setup.device == "tablet" and setup.area_w and setup.area_h:
        w = setup.area_w * (1 + (kx if kx is not None else k))
        h = setup.area_h * (1 + (ky if ky is not None else k))
        return (f"{area} your tablet area: {setup.area_w:g}x{setup.area_h:g}mm -> {w:.1f}x{h:.1f}mm"
                + ("" if kx is not None else " (same aspect ratio)") + ".")
    tip = f"tablet: {area} your area by about {_pct(k)}; mouse: {'lower' if k > 0 else 'raise'} your sensitivity by about {_pct(k / (1 + k))}"
    return tip + " (tell me your setup with `config` for exact numbers)."


def _aim_rules(a: np.ndarray, setup: Setup) -> list[Insight]:
    """`a`: jump_clicks rows."""
    if len(a) < MIN_JUMPS or len(set(a[:, 9])) < MIN_EPISODES:
        return []
    d, along, perp, mx, my, ex, ey, speed, radius, _, missed = (a[:, i] for i in range(11))   # played_at unused
    out = []

    # the mean overaim (+) or underaim (-) as a share of the jump distance: a negative one means a smaller area
    # (or a lower sensitivity), a positive one a larger area, whenever it reaches the MIN_SCALE setting
    k = _fit(d, along).slope
    pct = lambda x: f"{'+' if x > 0 else '-'}{abs(x) * 100:.1f}%"
    if abs(k) >= MIN_SCALE and k != 0:
        fx, fy = _fit(mx, ex), _fit(my, ey)
        per_axis = (fx and fy and abs(fx.slope - fy.slope) >= MIN_AXIS_DIFF
                    and abs(fx.t) >= T_MIN and abs(fy.t) >= T_MIN)
        kx, ky = (fx.slope, fy.slope) if per_axis else (None, None)
        word = "overaim" if k > 0 else "underaim"
        axis_txt = f" Horizontally {pct(fx.slope)}, vertically {pct(fy.slope)}." if per_axis else ""
        out.append(Insight(
            f"You {word} by ~{abs(k) * 100:.1f}%: adjust your {'sensitivity' if setup.device == 'mouse' else 'area'}",
            f"On {len(a)} jumps, the longer the jump the further {'past' if k > 0 else 'short of'} the circle you "
            f"click: {pct(k)} of the distance on average.{axis_txt} "
            + _scale_advice(k, kx, ky, setup)[0].upper() + _scale_advice(k, kx, ky, setup)[1:],
            # misses on the side the scaling pushes you to, half of them blamed on it
            impact=0.5 * float(((missed > 0) & (np.sign(along) == np.sign(k))).sum()),
            evidence={"scale": k, "n": len(a)}))
    else:
        out.append(Insight(
            f"Your {'sensitivity' if setup.device == 'mouse' else 'area'} looks right",
            f"On {len(a)} jumps your error scales by {pct(k)} of the distance: below the {MIN_SCALE * 100:g}% "
            f"worth changing settings for.", 0, kind="info", evidence={"scale_ok": k}))

    rot = _fit(d, perp)
    angle = math.degrees(math.atan(rot.slope))
    if abs(angle) >= MIN_ROTATION_DEG and abs(rot.t) >= T_MIN:
        direction = "clockwise" if angle > 0 else "anticlockwise"
        current = f" (now {setup.area_rotation:g} deg)" if setup.device == "tablet" and setup.area_rotation is not None else ""
        out.append(Insight(
            f"Your aim is rotated {abs(angle):.1f} deg {direction}",
            f"Your movements come out turned {direction} compared with the jumps. "
            + (f"Turn your mousepad (or grip) about {abs(angle):.1f} deg {direction}." if setup.device == "mouse" else
               f"Rotate your tablet area about {abs(angle):.1f} deg {direction}{current}; check in the driver's "
               f"preview that the area turns that way."),
            abs(angle) * 2, evidence={"rotation_deg": angle}))

    r = radius.mean()
    mean_x, mean_y = ex.mean(), ey.mean()
    se_x, se_y = ex.std() / math.sqrt(len(ex)), ey.std() / math.sqrt(len(ey))
    shifts = []
    mm = setup.mm_per_osu_px()
    for m, se, pos, neg in ((mean_x, se_x, "right", "left"), (mean_y, se_y, "down", "up")):
        if abs(m) >= MIN_OFFSET_RADII * r and abs(m / se) >= T_MIN:
            size = f" by ~{abs(m) * mm:.1f}mm" if mm else ""
            shifts.append((f"{abs(m):.0f}px {pos if m > 0 else neg}", f"move the area {pos if m > 0 else neg}{size}"))
    if shifts:
        out.append(Insight(
            "Your clicks are shifted",
            f"On average you click {' and '.join(s[0] for s in shifts)} of the circle centre: "
            + "; ".join(s[1] for s in shifts) + ". (A small shift is harmless; only change it if it bothers you.)",
            1, evidence={"mean_x": mean_x, "mean_y": mean_y}))
    return out


def _aspect_rule(a: np.ndarray, setup: Setup) -> list[Insight]:
    """Aspect ratio: horizontal jumps against vertical ones. When one axis misses clearly more, and on it the
    player over- or undershoots, only that side of the area changes (a tablet; a mouse has no aspect ratio)."""
    if setup.device == "mouse":
        return []
    d, along, mx, my, missed = a[:, 0], a[:, 1], a[:, 3], a[:, 4], a[:, 10]
    horiz, vert = np.abs(mx) >= ASPECT_AXIS * np.abs(my), np.abs(my) >= ASPECT_AXIS * np.abs(mx)
    if horiz.sum() < ASPECT_MIN_JUMPS or vert.sum() < ASPECT_MIN_JUMPS:
        return []
    mh, mv = missed[horiz].mean(), missed[vert].mean()
    p = missed[horiz | vert].mean()
    se = math.sqrt(p * (1 - p) * (1 / horiz.sum() + 1 / vert.sum())) if 0 < p < 1 else 0.0
    if not se or abs(mh - mv) / se < T_MIN or max(mh, mv) < ASPECT_MIN_RATIO * max(min(mh, mv), 1e-9):
        return []
    x_worse = mh > mv
    sel = horiz if x_worse else vert
    worse, better = (mh, mv) if x_worse else (mv, mh)
    axis, side = ("horizontal", "width") if x_worse else ("vertical", "height")
    fit = _fit(d[sel], along[sel])
    head = (f"You miss {axis} jumps {worse / max(better, 1e-9):.1f}x as often as {'vertical' if x_worse else 'horizontal'} "
            f"ones ({worse * 100:.1f}% against {better * 100:.1f}%, on {int(sel.sum())} and "
            f"{int((vert if x_worse else horiz).sum())} jumps).")
    impact = float((worse - better) * sel.sum())
    if fit is None or abs(fit.slope) < ASPECT_MIN_SCALE or abs(fit.t) < T_MIN:
        return [Insight(f"You miss more {axis} jumps", head + f" On them you don't clearly over- or undershoot, so the "
                        f"area's {side} isn't the cause: train {axis} jumps.", impact,
                        evidence={"aspect": axis, "miss_h": mh, "miss_v": mv})]
    k = fit.slope
    word = "overshoot" if k > 0 else "fall short of"
    tip = f"{'increase' if k > 0 else 'decrease'} only the {side} of your area by about {_pct(k)}"
    if setup.area_w and setup.area_h:
        w, h = (setup.area_w * (1 + k), setup.area_h) if x_worse else (setup.area_w, setup.area_h * (1 + k))
        tip = (f"{'increase' if k > 0 else 'decrease'} only the {side} of your area: {setup.area_w:g}x{setup.area_h:g}mm -> "
               f"{w:.1f}x{h:.1f}mm (aspect ratio {setup.area_w / setup.area_h:.2f} -> {w / h:.2f})")
    return [Insight(f"Adjust your area's aspect ratio: {'more' if k > 0 else 'less'} {side}",
                    head + f" On them you {word} the circle by {abs(k) * 100:.1f}% of the distance: {tip}. "
                    f"This replaces changing the whole area: the other side is fine.", impact,
                    evidence={"aspect": axis, "scale": k, "miss_h": mh, "miss_v": mv})]


def _key_names(presses) -> str:
    counts: dict[str, int] = {}
    for p in presses:
        counts[p.name] = counts.get(p.name, 0) + 1
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def _step(value: float | None, delta: float) -> str:
    """The "(e.g. 0.2mm -> 0.3mm)" part of a fix, when the current value is known."""
    if not value:
        return ""
    return f" (e.g. {value:g}mm -> {max(value + delta, 0.1):.1f}mm)"


def _key_counts(presses) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in presses:
        counts[p.name] = counts.get(p.name, 0) + 1
    return counts


def _count_names(counts: dict[str, int]) -> str:
    return ", ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))


def tap_summary(taps: list[TapStats], samples: list[Sample]) -> dict:
    """What the key advice needs from the plays: counts, and which keys."""
    ghosts = [g for t in taps for g in t.ghost_presses]
    stuck = [p for t in taps for _, p in t.stuck_misses]
    return {"presses": sum(t.presses for t in taps), "ghosts": len(ghosts), "ghost_keys": _key_names(ghosts),
            "ghost_hits": sum(len(t.ghost_hits) for t in taps), "ghost_plays": sum(1 for t in taps if t.ghost_presses),
            "stuck": len(stuck), "stuck_keys": _key_names(stuck), "stuck_plays": sum(1 for t in taps if t.stuck_misses),
            "run_misses": sum(1 for s in samples if s.missed and s.f.run_length > 1),
            "ghost_counts": _key_counts(ghosts), "stuck_counts": _key_counts(stuck)}


def taps_by_play(taps: list[TapStats], samples: list[Sample]) -> list[dict]:
    """tap_summary of each play (taps[i] is play i), with when it was played: the key advice can then leave out
    the plays made before the keyboard settings changed."""
    by_play: dict[int, list[Sample]] = {}
    for s in samples:
        by_play.setdefault(s.play, []).append(s)
    out = []
    for i, t in enumerate(taps):
        ss = by_play.get(i, [])
        out.append({**tap_summary([t], ss), "time": float(getattr(ss[0], "played_at", 0.0)) if ss else 0.0})
    return out


def combine_taps(plays: list[dict]) -> dict:
    """tap_summary of several plays from their own ones."""
    out = {k: sum(p.get(k, 0) for p in plays)
           for k in ("presses", "ghosts", "ghost_hits", "ghost_plays", "stuck", "stuck_plays", "run_misses")}
    for kind in ("ghost", "stuck"):
        counts: dict[str, int] = {}
        for p in plays:
            for name, n in p.get(f"{kind}_counts", {}).items():
                counts[name] = counts.get(name, 0) + n
        out[f"{kind}_counts"], out[f"{kind}_keys"] = counts, _count_names(counts)
    return out


def _tap_rules(k: dict, setup: Setup) -> list[Insight]:
    """`k`: tap_summary."""
    out = []
    presses, ghosts, ghost_hits = k["presses"], k["ghosts"], k["ghost_hits"]
    if presses and ghosts >= MIN_GHOSTS and ghosts / presses * 1000 >= GHOSTS_PER_1000 and k["ghost_plays"] >= 2:
        # ghost presses: the key registers too easily, so it should take more travel to press
        if setup.keyboard == "rt":
            fix = "raise your rapid trigger press distance" + _step(setup.rt_press, +0.1)
            fix += " or your actuation point" + _step(setup.actuation, +0.2)
            fix += ": tiny finger wobbles while holding are being read as new presses."
        elif setup.keyboard == "mechanical":
            fix = ("raise your actuation point" + _step(setup.actuation, +0.2) + " if your switches allow it; "
                   "otherwise it's switch chatter: raise the debounce time, or clean/replace the switch.")
        else:
            fix = ("raise the actuation point, or the rapid trigger press distance; on a plain mechanical switch "
                   "it's chatter: raise the debounce or replace the switch.")
        out.append(Insight(
            "Ghost key presses (chatter)",
            f"{ghosts} times a key was released and pressed again within 10ms, faster than a finger can "
            f"({ghosts / presses * 1000:.1f} per 1000 presses; {k['ghost_keys']}). "
            f"{ghost_hits} of them hit a note early. Fix: {fix}",
            ghosts * 0.3 + ghost_hits, evidence={"ghosts": ghosts, "ghost_hits": ghost_hits}))

    stuck, run_misses = k["stuck"], k["run_misses"]
    if stuck >= MIN_STUCK and run_misses and stuck / run_misses >= STUCK_SHARE and k["stuck_plays"] >= MIN_EPISODES:
        # missed taps: the key needs too much travel, to press again or to reset
        if setup.keyboard == "rt":
            fix = "lower your rapid trigger release distance" + _step(setup.rt_release, -0.1)
            fix += " or your actuation point" + _step(setup.actuation, -0.2) + ", so the key resets with a smaller lift."
        elif setup.keyboard == "mechanical":
            fix = ("lower your actuation point" + _step(setup.actuation, -0.2) + " if your switches allow it; "
                   "otherwise lift your fingers fully between taps, or consider a rapid trigger keyboard.")
        else:
            fix = ("lower the actuation point, or the rapid trigger release distance; on a plain mechanical switch "
                   "lift fully between taps.")
        out.append(Insight(
            "Keys don't reset between taps",
            f"{stuck} stream/alt notes ({_pct(stuck / run_misses)} of your misses there) got no tap while "
            f"a key was still held down from the previous note ({k['stuck_keys']}). "
            f"The finger didn't lift far enough for the key to register a release. Fix: {fix}",
            stuck, evidence={"stuck": stuck, "run_misses": run_misses}))
    return out


def measure(samples: list[Sample], taps: list[TapStats]) -> dict:
    """Everything the setup advice reads from the plays. The advice itself only adds the setup and the cutoffs,
    so it can be given again from this when either changes, without judging the plays again."""
    return {"clicks": jump_clicks(samples), "taps": tap_summary(taps, samples),
            "taps_by_play": taps_by_play(taps, samples)}


def keys_measured(measured: dict, keys_since: float | None) -> dict:
    """The tap counts the key advice reads: of the plays made after `keys_since` (the keyboard settings changed
    then), or of all of them."""
    if not keys_since:
        return measured["taps"]
    return combine_taps([p for p in measured.get("taps_by_play", []) if p.get("time", 0) >= keys_since])


def advise(measured: dict, setup: Setup, area_since: float | None = None,
           keys_since: float | None = None) -> list[Insight]:
    """`area_since`, `keys_since`: the area (or sensitivity), the keyboard settings changed then: only the plays
    made after it count for that advice."""
    clicks = click_rows(measured["clicks"])
    if area_since:
        clicks = clicks[clicks[:, 11] >= area_since]
    aim, aspect = _aim_rules(clicks, setup), _aspect_rule(clicks, setup)
    if any("scale" in i.evidence for i in aspect):   # one side to change: the whole-area advice (a change of
        aim = [i for i in aim if "scale" not in i.evidence and "scale_ok" not in i.evidence]   # both, or "fine") goes
    found = aim + aspect + _tap_rules(keys_measured(measured, keys_since), setup)
    for i in found:
        i.category = "info" if i.kind == "info" else "setup"
    return found


def setup_insights(samples: list[Sample], taps: list[TapStats], setup: Setup,
                   area_since: float | None = None, keys_since: float | None = None) -> list[Insight]:
    return advise(measure(samples, taps), setup, area_since, keys_since)
