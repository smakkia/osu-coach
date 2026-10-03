"""Setup advice: tablet area / mouse sensitivity and keyboard (rapid trigger) settings.

The area / sensitivity advice reads what the replay viewer's aim meter shows "along the movement": the
hits of the notes the cursor moved at least a radius to, turned so the movement points up. Hits below the
centre on average (underaim) -> a smaller area (a higher sensitivity), above it (overaim) -> a larger one
(a lower sensitivity), from the MIN_SCALE setting up. The aspect ratio reads the plain meter: hits spread
wider on one axis than the other, or notes near the playfield's edges over/undershot only on the axis of
that edge, change only that side of the area. Rotation still regresses the sideways error on the distance
(the idea of rgbeing/osu-aim-analyzer).
"""

import math
from dataclasses import dataclass

import numpy as np

from .advice import MIN_EPISODES, Insight, Sample
from .keys import TapStats
from .setup import Setup

MIN_JUMPS = 200
MIN_SCALE = 0.03       # overaim/underaim below 3% of the distance isn't worth changing settings for
ASPECT_AXIS = 1.5          # a move is horizontal (vertical) when it goes this many times more along x (y)
ASPECT_MIN_JUMPS = 100     # hits each axis needs for the aspect ratio advice
ASPECT_MIN_SPREAD = 1.15   # the hits' spread on one axis must be this many times the other's...
ASPECT_MIN_SCALE = 0.01    # ...and over/undershoot by at least this share of the distance to change that side
PLAYFIELD_W, PLAYFIELD_H = 512, 384
EDGE_MARGIN_X = 165        # osu! px from the left/right border a note counts as at its edge
EDGE_MARGIN_Y = 100        # ...and from the top/bottom one
EDGE_MIN_HITS = 30         # hits moving towards an edge each axis needs for the edge check
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


CLICK_COLUMNS = 14


def jump_clicks(samples: list[Sample]) -> np.ndarray:
    """Rows (distance, err_along, err_perp, move_x, move_y, err_x, err_y, speed, radius, play, missed, played_at,
    note_x, note_y) for the notes the aim meter turns along the movement (the cursor moved at least a radius to
    them), clicked within 2 radii of the note (further clicks aren't aim, they're misreads); played_at: when the
    play was played (unix seconds, 0 if unknown), so the advice can leave out plays made before a setup change."""
    rows = []
    for s in samples:
        f, r = s.f, s.r
        if f.direction is None or f.distance_radii < 1:
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
                     float(getattr(s, "played_at", 0.0)), px, py))
    return np.array(rows) if rows else np.empty((0, CLICK_COLUMNS))


def click_rows(clicks) -> np.ndarray:
    """Saved clicks as an array; older ones get a 0 play time (11 columns) and no note position (NaN)."""
    a = np.asarray(clicks, dtype=float)
    if a.size == 0:
        return np.empty((0, CLICK_COLUMNS))
    a = a.reshape(len(a), -1)
    if a.shape[1] == 11:
        a = np.column_stack([a, np.zeros(len(a))])
    if a.shape[1] == 12:
        a = np.column_stack([a, np.full((len(a), 2), np.nan)])
    return a


@dataclass
class _Along:
    scale: float    # the mean click along the movement as a share of the distance: + overaim, - underaim
    t: float
    n: int
    mean_px: float  # the mean click along the movement, osu! px (+ past the centre, - short of it)


def _along(d: np.ndarray, along: np.ndarray) -> _Along | None:
    """Where the hits sit on the aim meter turned along the movement, on average."""
    if len(along) < 10:
        return None
    mean, se = along.mean(), along.std(ddof=1) / math.sqrt(len(along))
    return _Along(float(along.sum() / max(d.sum(), 1e-9)), float(mean / se) if se > 0 else 0.0, len(along),
                  float(mean))


PLAY_AIM_MIN_JUMPS = 20   # hits one play needs for its overaim / underaim to mean something


def play_aim(samples: list[Sample]) -> dict | None:
    """Overaim (+) or underaim (-) in one play, as a share of the distance (the aim meter along the movement,
    as the advice reads it)."""
    a = jump_clicks(samples)
    a = a[a[:, 10] == 0]
    out = {"jumps": len(a), "scale": None, "min_jumps": PLAY_AIM_MIN_JUMPS, "ok": MIN_SCALE, "t_min": T_MIN}
    m = _along(a[:, 0], a[:, 1]) if len(a) >= PLAY_AIM_MIN_JUMPS else None
    if m:
        out.update(scale=m.scale, t=m.t, mean_px=m.mean_px)
    return out


def _pct(x: float) -> str:
    return f"{abs(x) * 100:.0f}%"


def _scale_advice(k: float, setup: Setup) -> str:
    """What to change so that the cursor travels 1/(1+k) as far for the same hand movement."""
    sens = f"{'lower' if k > 0 else 'raise'} sens ~{_pct(k / (1 + k))}"
    if setup.device == "mouse":
        return f"Sens {setup.sens:g} -> {setup.sens / (1 + k):.2f}." if setup.sens else sens[0].upper() + sens[1:] + "."
    area = "increase" if k > 0 else "decrease"
    if setup.device == "tablet" and setup.area_w and setup.area_h:
        return (f"Area {setup.area_w:g}x{setup.area_h:g} -> {setup.area_w * (1 + k):.1f}x{setup.area_h * (1 + k):.1f}mm "
                f"(same ratio).")
    return f"Tablet: {area} area ~{_pct(k)}; mouse: {sens}. Set your setup for exact numbers."


def _aim_rules(a: np.ndarray, setup: Setup) -> list[Insight]:
    """`a`: jump_clicks rows."""
    if len(a) < MIN_JUMPS or len(set(a[:, 9])) < MIN_EPISODES:
        return []
    d, along, perp, mx, my, ex, ey, speed, radius, _, missed = (a[:, i] for i in range(11))   # played_at unused
    out = []

    # where the hits sit on the aim meter turned along the movement: below the centre (underaim) means a smaller
    # area (or a higher sensitivity), above it (overaim) a larger area, whenever it reaches the MIN_SCALE setting
    hit = missed == 0
    m = _along(d[hit], along[hit])
    if m is None:
        return out
    k = m.scale
    pct = lambda x: f"{'+' if x > 0 else '-'}{abs(x) * 100:.1f}%"
    meter = f"hits {abs(m.mean_px):.1f}px {'past' if k > 0 else 'short of'} the centre ({pct(k)}, {m.n} hits)"
    what = "sensitivity" if setup.device == "mouse" else "area"
    if abs(k) >= MIN_SCALE and abs(m.t) >= T_MIN:
        out.append(Insight(
            f"{'Overaim' if k > 0 else 'Underaim'} {abs(k) * 100:.1f}%: {'larger' if k > 0 else 'smaller'} area"
            if what == "area" else f"{'Overaim' if k > 0 else 'Underaim'} {abs(k) * 100:.1f}%: "
            f"{'lower' if k > 0 else 'higher'} sensitivity",
            f"Aim meter along the movement: {meter}. " + _scale_advice(k, setup),
            # misses on the side you lean to, half of them blamed on it
            impact=0.5 * float(((missed > 0) & (np.sign(along) == np.sign(k))).sum()),
            evidence={"scale": k, "n": m.n}))
    else:
        why = f"under the {MIN_SCALE * 100:g}% cutoff" if abs(k) < MIN_SCALE else "too inconsistent to tell"
        out.append(Insight(f"{what.capitalize()} looks right", f"{meter[0].upper()}{meter[1:]}: {why}.", 0,
                           kind="info", evidence={"scale_ok": k}))

    rot = _fit(d, perp)
    angle = math.degrees(math.atan(rot.slope))
    if abs(angle) >= MIN_ROTATION_DEG and abs(rot.t) >= T_MIN:
        direction = "clockwise" if angle > 0 else "anticlockwise"
        current = f" (now {setup.area_rotation:g} deg)" if setup.device == "tablet" and setup.area_rotation is not None else ""
        out.append(Insight(
            f"Aim rotated {abs(angle):.1f} deg {direction}",
            f"Turn your mouse grip {abs(angle):.1f} deg {direction}." if setup.device == "mouse" else
            f"Rotate the area {abs(angle):.1f} deg {direction}{current}; check the direction in the driver's preview.",
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
            f"{' and '.join(s[0] for s in shifts).capitalize()} of the centre: " + "; ".join(s[1] for s in shifts)
            + " (only if it bothers you).",
            1, evidence={"mean_x": mean_x, "mean_y": mean_y}))
    return out


def _side_change(k: float, x_side: bool, setup: Setup) -> str:
    side = "width" if x_side else "height"
    if not (setup.area_w and setup.area_h):
        return f"{'Increase' if k > 0 else 'Decrease'} only the {side} ~{_pct(k)}."
    w, h = (setup.area_w * (1 + k), setup.area_h) if x_side else (setup.area_w, setup.area_h * (1 + k))
    return (f"Area {setup.area_w:g}x{setup.area_h:g} -> {w:.1f}x{h:.1f}mm "
            f"(ratio {setup.area_w / setup.area_h:.2f} -> {w / h:.2f}).")


def _edge_shoot(ex, ey, mx, my, nx, ny, x_edge: bool) -> tuple[_Along | None, _Along | None]:
    """Hits of the notes near the left/right (x_edge) or top/bottom border, reached moving towards that border:
    how far past (+) or short of (-) them on the border's axis, and on the other axis (shares of the move)."""
    pos, size, margin = (nx, PLAYFIELD_W, EDGE_MARGIN_X) if x_edge else (ny, PLAYFIELD_H, EDGE_MARGIN_Y)
    side = np.where(pos < margin, -1.0, np.where(pos > size - margin, 1.0, 0.0))
    side[np.isnan(pos)] = 0.0
    e, mv, e2, mv2 = (ex, mx, ey, my) if x_edge else (ey, my, ex, mx)
    sel = (side != 0) & (mv * side > 0)
    if sel.sum() < EDGE_MIN_HITS:
        return None, None
    near = _along(np.abs(mv[sel]), e[sel] * side[sel])
    other = np.abs(mv2[sel]) >= 1
    far = _along(np.abs(mv2[sel][other]), (e2[sel] * np.sign(mv2[sel]))[other])
    return near, far


def _aspect_rule(a: np.ndarray, setup: Setup) -> list[Insight]:
    """Aspect ratio, from the plain aim meter (not turned with the movement): when the notes near the playfield's
    edges are over- or undershot only on the axis of their edge, or the hits spread wider on one axis than on the
    other, only that side of the area changes (a tablet; a mouse has no aspect ratio)."""
    if setup.device == "mouse":
        return []
    hit = a[:, 10] == 0
    d, along, mx, my, ex, ey, nx, ny = (a[hit, i] for i in (0, 1, 3, 4, 5, 6, 12, 13))
    shot = lambda m: m is not None and abs(m.scale) >= ASPECT_MIN_SCALE and abs(m.t) >= T_MIN
    pct = lambda x: f"{'+' if x > 0 else '-'}{abs(x) * 100:.1f}%"

    # notes near the edges: over/undershot on their edge's axis, not the same way on the other axis, and not the
    # same way near the other axis's edges (that would be the whole area)
    edge = {x: _edge_shoot(ex, ey, mx, my, nx, ny, x) for x in (True, False)}
    for x_side in (True, False):
        near, far = edge[x_side]
        o_near = edge[not x_side][0]
        if not shot(near) or shot(far) and np.sign(far.scale) == np.sign(near.scale):
            continue
        if shot(o_near) and np.sign(o_near.scale) == np.sign(near.scale):
            continue
        k, side = near.scale, "width" if x_side else "height"
        where, axis, o_axis = ("left/right", "horizontal", "vertical") if x_side else ("top/bottom", "vertical", "horizontal")
        other = f", {pct(far.scale)} on the {o_axis} axis" if far else ""
        return [Insight(f"Area aspect ratio: {'more' if k > 0 else 'less'} {side}",
                        f"Notes at the {where} edges: {'past' if k > 0 else 'short of'} them by {pct(k)} on the "
                        f"{axis} axis{other} ({near.n} hits). " + _side_change(k, x_side, setup),
                        0.5 * near.n * abs(k) / ASPECT_MIN_SCALE,
                        evidence={"aspect": "horizontal" if x_side else "vertical", "scale": k, "edge": True})]

    # the hits' spread on each axis of the plain meter
    if hit.sum() < 2 * ASPECT_MIN_JUMPS:
        return []
    sx, sy = ex.std(ddof=1), ey.std(ddof=1)
    if min(sx, sy) <= 0:
        return []
    log_ratio, se = math.log(sx * sx / (sy * sy)), math.sqrt(4 / (len(ex) - 1))
    if max(sx, sy) < ASPECT_MIN_SPREAD * min(sx, sy) or abs(log_ratio) / se < T_MIN:
        return []
    x_side = sx > sy
    sel = (np.abs(mx) >= ASPECT_AXIS * np.abs(my)) if x_side else (np.abs(my) >= ASPECT_AXIS * np.abs(mx))
    axis, side = ("horizontal", "width") if x_side else ("vertical", "height")
    head = (f"Hits spread {max(sx, sy) / min(sx, sy):.2f}x wider {axis}ly ({max(sx, sy):.1f} vs {min(sx, sy):.1f}px, "
            f"{len(ex)} hits)")
    m = _along(d[sel], along[sel]) if sel.sum() >= ASPECT_MIN_JUMPS else None
    if not shot(m):
        return [Insight(f"Hits spread wider {axis}ly", head + f", but no clear over/undershoot on {axis} moves: "
                        f"not the area, train {axis} jumps.", 1,
                        evidence={"aspect": axis, "spread_x": sx, "spread_y": sy})]
    k = m.scale
    return [Insight(f"Area aspect ratio: {'more' if k > 0 else 'less'} {side}",
                    head + f"; {axis} moves {'past' if k > 0 else 'short of'} the circle by {pct(k)} ({m.n} hits). "
                    + _side_change(k, x_side, setup),
                    0.5 * m.n * abs(k) / ASPECT_MIN_SCALE,
                    evidence={"aspect": axis, "scale": k, "spread_x": sx, "spread_y": sy})]


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
