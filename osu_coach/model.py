"""One model for every note: where a player's misses come from.

Each note's miss probability is a logistic function of features grouped into the
components of the skill schema (aim, tapping, reading, endurance). Fitting them
together, instead of one model per skill, attributes each miss once: a spaced
stream's cost is split between tapping speed and alt aim instead of being counted
by both, and "alt" is no longer a skill of its own but a mix of components.

Every feature is built so that more of it should make a note harder. Its reference is
where it adds nothing: 0 for things a note either has or not (a jump, an irregular snap),
the player's typical level for loads every note carries (finger strain, notes on screen).
Attribution: for each note, the log-odds each component adds above those references
share out the note's miss probability above that easy baseline. A feature
whose fitted effect goes the other way in the player's data (e.g. high AR on easy DT maps)
gets no misses, and is listed as such. Summed over notes, that gives the misses each
component costs, overall and inside each kind of pattern.
"""

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, field

import numpy as np

from .advice import ExpectedModel, Sample
from .beatmap import SLIDER
from .features import FIRST, IRREGULAR, JUMP, RUN_PATTERNS, STREAM_FAMILY, STREAM_SPACING_CAP_RADII, ALT
from .locate import CACHE_DIR
from .skills import fit_logistic

MODEL_PATH = CACHE_DIR / "model.json"
FOLDS = 5
NORMAL_RADIUS = 36.5   # osu!px, CS 4
MIN_CHAIN_FIRST = 20   # first misses a kind of pattern needs for its own chain factor
L2 = 5.0               # ridge: several features overlap (spacing and tempo inside runs)

COMPONENT_NAMES = {
    "flow aim": "Flow aim (under 2 radii)",
    "alt aim": "Alt aim (2-5 radii)",
    "jump aim": "Jump aim (over 5 radii)",
    "aim control": "Aim control (changing spacing and angles)",
    "slider": "Slider heads",
    "tap speed": "Tapping speed (runs)",
    "finger control": "Finger control",
    "rhythm": "Irregular rhythms",
    "reading": "Reading (notes on screen)",
    "reaction": "High AR (reaction time)",
    "endurance": "Endurance (stamina, end of the map)",
}


def _speed(f) -> float:
    """Radii per second from the previous object's end."""
    return f.distance_radii / max(f.move_ms, 1.0) * 1000


def _slider_speed(s: Sample) -> float:
    o = s.r.obj
    if o.kind != SLIDER or o.span_duration <= 0:
        return 0.0
    return o.path.length / s.f._radius / (o.span_duration / s.rate / 1000)


def _run_tempo(s: Sample) -> float:
    """How far above 120 BPM the run is, in tens of BPM (0 outside runs)."""
    return max(s.f.bpm - 120, 0) / 10 if s.f.run_length >= 2 and s.f.bpm else 0.0


# name, component, value(sample, previous sample in the same play or None)
FEATURES = [
    ("flow speed", "flow aim", lambda s, p: _speed(s.f) / 10 if s.f.aim == "flow" and s.f.run_length < 2 else 0.0),
    ("run flow speed", "flow aim", lambda s, p: _speed(s.f) / 10 if s.f.aim == "flow" and s.f.run_length >= 2 else 0.0),
    # flow aim gets harder as the angle closes (180 = straight on), alt and jump aim as it opens
    ("flow sharpness", "flow aim",
     lambda s, p: (180 - s.f.angle) / 90 if s.f.aim == "flow" and s.f.angle is not None else 0.0),
    ("run spacing to 2r", "flow aim", lambda s, p: min(s.f.run_spacing, 2.0) if s.f.run_length >= 2 else 0.0),
    ("run bpm x spacing to 2r", "flow aim", lambda s, p: _run_tempo(s) * min(s.f.run_spacing, 2.0)),
    ("alt speed", "alt aim", lambda s, p: _speed(s.f) / 10 if s.f.aim == "alt" and s.f.run_length < 2 else 0.0),
    ("run alt speed", "alt aim", lambda s, p: _speed(s.f) / 10 if s.f.aim == "alt" and s.f.run_length >= 2 else 0.0),
    ("alt angle", "alt aim", lambda s, p: (s.f.angle or 0) / 90 if s.f.aim == "alt" else 0.0),
    ("run bpm x spacing over 2r", "alt aim",
     lambda s, p: _run_tempo(s) * max(s.f.run_spacing - 2.0, 0.0)),
    # a stream spaced wider than streams usually are: the cursor has to travel while tapping at full speed
    ("spaced stream", "alt aim", lambda s, p: 1.0 if pattern_group(s) == "spaced streams" else 0.0),
    ("run spacing over 2r", "alt aim", lambda s, p: max(s.f.run_spacing - 2.0, 0.0) if s.f.run_length >= 2 else 0.0),
    ("jump speed", "jump aim", lambda s, p: _speed(s.f) / 10 if s.f.aim == "jump" else 0.0),
    ("jump distance", "jump aim", lambda s, p: s.f.distance_radii - 5 if s.f.aim == "jump" else 0.0),
    ("jump angle", "jump aim", lambda s, p: (s.f.angle or 0) / 90 if s.f.aim == "jump" else 0.0),
    ("small circles", "jump aim", lambda s, p: max(NORMAL_RADIUS - s.f._radius, 0) / 5),
    ("big circles", "jump aim", lambda s, p: max(s.f._radius - NORMAL_RADIUS, 0) / 5),
    ("spacing change", "aim control",
     lambda s, p: abs(s.f.distance_radii - p.f.distance_radii) / 2 if p is not None else 0.0),
    ("angle change", "aim control",
     lambda s, p: abs(s.f.angle - p.f.angle) / 90 if p is not None and s.f.angle is not None
     and p.f.angle is not None else 0.0),
    ("slider speed", "slider", lambda s, p: _slider_speed(s) / 10),
    ("run", "tap speed", lambda s, p: 1.0 if s.f.run_length >= 2 else 0.0),
    ("run bpm", "tap speed", lambda s, p: max(s.f.bpm - 120, 0) / 10 if s.f.run_length >= 2 and s.f.bpm else 0.0),
    ("run length", "tap speed", lambda s, p: math.log(s.f.run_length) if s.f.run_length >= 2 else 0.0),
    ("run position", "tap speed", lambda s, p: math.log1p(s.f.run_position) if s.f.run_length >= 2 else 0.0),
    ("finger strain", "finger control", lambda s, p: math.log1p(s.f.finger_strain)),
    ("alt tapping", "finger control", lambda s, p: 1.0 if s.f.pattern == ALT else 0.0),
    ("irregular", "rhythm", lambda s, p: 1.0 if s.f.pattern == IRREGULAR else 0.0),
    ("on screen", "reading", lambda s, p: math.log1p(s.f.visible)),
    ("on screen x rhythm", "reading", lambda s, p: math.log1p(s.f.visible) * s.f.rhythm_var),
    ("on screen x overlap", "reading", lambda s, p: math.log1p(s.f.visible) * s.f.overlap_share),
    ("on screen x jump", "reading", lambda s, p: math.log1p(s.f.visible) if s.f.aim == "jump" else 0.0),
    ("AR below 9", "reading", lambda s, p: max(9 - s.ar, 0)),
    ("AR below 9 x jump", "reading", lambda s, p: max(9 - s.ar, 0) if s.f.aim == "jump" else 0.0),
    ("hidden", "reading", lambda s, p: 1.0 if s.hidden else 0.0),
    ("AR above 10", "reaction", lambda s, p: max(s.ar - 10, 0)),
    ("notes played /1000", "endurance", lambda s, p: s.r.obj.index / 1000),
    ("map progress", "endurance", lambda s, p: s.time_frac),
]
COMPONENTS = list(dict.fromkeys(c for _, c, _ in FEATURES))
# loads every note carries: only what goes above the player's typical level counts
REFERENCE_TYPICAL = {"finger strain", "on screen", "map progress"}
# structure, not difficulty: per note a run is missed less than a single note and a long run less
# than a short one. They stay in the model (so the rest is measured fairly) but share no misses.
CONTROLS = {"run", "run length"}
# difficulty doesn't grow in a straight line with these: they get a curve (see expand)
SPLINES = ("jump speed", "jump distance", "run bpm", "run bpm x spacing to 2r", "run bpm x spacing over 2r")
KNOT_PERCENTILES = (25, 50, 75, 90)


def design(samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, np.ndarray, list[Sample]]:
    """Feature matrix, outcomes (first misses) and play ids.

    Left out: the first note after a break, and every note right after a miss: whether that
    one is hit depends on the miss before it (notelock, lost sync), not on the note itself.
    Those chained misses are counted apart, per kind of pattern (see chain_factors)."""
    rows, y, plays, kept = [], [], [], []
    prev: Sample | None = None
    for s in samples:
        if prev is not None and prev.play != s.play:
            prev = None
        if s.f.pattern == FIRST or (prev is not None and prev.missed):
            prev = s
            continue
        rows.append([fn(s, prev) for _, _, fn in FEATURES])
        y.append(float(s.missed))
        plays.append(s.play)
        kept.append(s)
        prev = s
    return np.array(rows), np.array(y), np.array(plays), kept


def chain_factors(samples: list[Sample]) -> dict[str, float]:
    """Per kind of pattern, all misses per first miss: how far one miss drags on (notelock, lost sync)."""
    misses, first = defaultdict(int), defaultdict(int)
    prev: Sample | None = None
    for s in samples:
        if prev is not None and prev.play != s.play:
            prev = None
        if s.missed:
            g = pattern_group(s)
            misses[g] += 1
            first[g] += not (prev is not None and prev.missed)
        prev = s
    return {g: misses[g] / first[g] for g in misses if first[g] >= MIN_CHAIN_FIRST}


def chain_stats(samples: list[Sample]) -> tuple[int, int, int]:
    """(misses, misses right after a miss, of which notelocked)."""
    total = after = locked = 0
    prev: Sample | None = None
    for s in samples:
        if prev is not None and prev.play != s.play:
            prev = None
        if s.missed:
            total += 1
            if prev is not None and prev.missed:
                after += 1
                locked += s.r.miss_reason == "notelock"
        prev = s
    return total, after, locked


def knots_for(X0: np.ndarray) -> dict[str, list[float]]:
    """Where each curved feature bends: percentiles of its non-zero values."""
    names = [n for n, _, _ in FEATURES]
    out = {}
    for name in SPLINES:
        vals = X0[:, names.index(name)]
        vals = vals[vals > 0]
        if len(vals) >= 200:
            out[name] = sorted({round(float(v), 3) for v in np.percentile(vals, KNOT_PERCENTILES)})
    return out


def expand(X0: np.ndarray, knots: dict[str, list[float]]) -> tuple[np.ndarray, list[str], list[str]]:
    """Base features plus, for each curved one, a hinge max(x - knot, 0) per knot: together they
    make a line that can change slope at every knot."""
    names = [n for n, _, _ in FEATURES]
    comps = [c for _, c, _ in FEATURES]
    cols, out_names, out_comps = [X0], list(names), list(comps)
    for name, ks in knots.items():
        j = names.index(name)
        for k in ks:
            cols.append(np.maximum(X0[:, j] - k, 0)[:, None])
            out_names.append(f"{name} >{k:g}")
            out_comps.append(comps[j])
    return np.hstack(cols), out_names, out_comps


@dataclass
class UnifiedModel:
    features: list[str]        # base features, then the curve pieces
    components: list[str]
    coef: list[float]          # intercept first
    t: list[float]
    easy: list[float]          # per feature, where it adds nothing (see REFERENCE_TYPICAL)
    knots: dict[str, list[float]]
    n: int
    misses: int
    chains: dict[str, float] = field(default_factory=dict)   # all misses per first miss, per kind of pattern
    usual_rate: float = 0.0    # the player's miss rate on their recent plays
    usual_shares: dict[str, float] = field(default_factory=dict)  # each component's share of their misses
    # the player's stream model, per run: P(broken) = sigmoid(b0 + b1 (bpm-180)/10 + b2 log(length) + b3 spacing)
    stream_runs: list[float] = field(default_factory=list)
    stream_bpm_top: float = 0.0   # 99th percentile BPM of the long streams the player played: past it we'd guess
    stream_ur: dict = field(default_factory=dict)   # timing on long streams by BPM (skills.stream_ur_curve)

    def chain(self, s: Sample) -> float:
        return self.chains.get(pattern_group(s), 1.0)

    def matrix(self, samples: list[Sample]) -> tuple[np.ndarray, np.ndarray, list[Sample]]:
        X0, y, _, kept = design(samples)
        return expand(X0, self.knots)[0], y, kept

    def logits(self, X: np.ndarray) -> np.ndarray:
        return self.coef[0] + X @ np.array(self.coef[1:])

    def p(self, X: np.ndarray) -> np.ndarray:
        return 1 / (1 + np.exp(-np.clip(self.logits(X), -30, 30)))

    def attribute(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(miss probability per note, misses above the easy baseline per note and component).

        Each component's log-odds above its references is summed over its features (curve
        pieces included, so a curve that flattens lowers it); only components that end up
        making the note harder share its misses."""
        beta = np.array(self.coef[1:])
        delta = X - np.array(self.easy)
        typical = np.array([f.split(" >")[0] in REFERENCE_TYPICAL for f in self.features])
        delta[:, typical] = np.clip(delta[:, typical], 0, None)  # loads count only above the usual
        terms = delta * beta
        terms[:, np.array([f in CONTROLS for f in self.features])] = 0
        comp = np.zeros((len(X), len(COMPONENTS)))
        for j, c in enumerate(self.components):
            comp[:, COMPONENTS.index(c)] += terms[:, j]
        pos = np.clip(comp, 0, None)
        p = self.p(X)
        # the same note with every component that makes it harder brought back to its reference
        base = 1 / (1 + np.exp(-np.clip(self.logits(X) - pos.sum(axis=1), -30, 30)))
        total = pos.sum(axis=1, keepdims=True)
        share = np.divide(pos, total, out=np.zeros_like(pos), where=total > 0)
        return p, share * np.clip(p - base, 0, None)[:, None]


def _fit_matrix(X0: np.ndarray, y: np.ndarray):
    knots = knots_for(X0)
    X, names, comps = expand(X0, knots)
    return fit_logistic(names, X, y, l2=L2), X, names, comps, knots


def fit(samples: list[Sample], chain_samples: list[Sample] | None = None) -> UnifiedModel | None:
    """`chain_samples`: the plays chain factors are measured on (default: `samples`); the player's
    usual plays, when `samples` also holds rarer ones (low or high AR) with longer chains."""
    X0, y, _, _ = design(samples)
    m, X, names, comps, knots = _fit_matrix(X0, y)
    if m is None:
        return None
    easy = [float(np.median(X[:, j])) if name in REFERENCE_TYPICAL else float(X[:, j].min())
            for j, name in enumerate(names)]
    return UnifiedModel(names, comps, m.coef, m.t, easy, knots, m.n, m.bad,
                        chain_factors(chain_samples if chain_samples is not None else samples))


def _bucket_rates(kept: list[Sample], y: np.ndarray) -> dict:
    """First-miss rate per pattern bucket (the per-pattern baseline, on the same outcome)."""
    n, m = defaultdict(int), defaultdict(float)
    for s, miss in zip(kept, y):
        b = ExpectedModel.bucket(s)
        n[b] += 1
        m[b] += miss
    rates = {b: m[b] / n[b] for b in n if n[b] >= ExpectedModel.MIN_BUCKET}
    rates["all"] = float(y.mean())
    return rates


def cross_validate(samples: list[Sample]) -> dict:
    """Held-out log-likelihood per note: this model vs the per-pattern miss rates it replaces.
    Knots and coefficients come from the training plays only."""
    X0, y, plays, kept = design(samples)
    ll_model = ll_base = 0.0
    for k in range(FOLDS):
        test = plays % FOLDS == k
        train_samples = [s for s, t in zip(kept, test) if not t]
        m, _, _, _, knots = _fit_matrix(X0[~test], y[~test])
        if m is None:
            return {}
        Xt = expand(X0[test], knots)[0]
        p = np.clip(1 / (1 + np.exp(-np.clip(m.coef[0] + Xt @ np.array(m.coef[1:]), -30, 30))), 1e-6, 1 - 1e-6)
        ll_model += float((y[test] * np.log(p) + (1 - y[test]) * np.log(1 - p)).sum())
        rates = _bucket_rates(train_samples, y[~test])
        q = np.clip(np.array([rates.get(ExpectedModel.bucket(s), rates["all"]) for s, t in zip(kept, test) if t]),
                    1e-6, 1 - 1e-6)
        ll_base += float((y[test] * np.log(q) + (1 - y[test]) * np.log(1 - q)).sum())
    n = len(y)
    return {"notes": n, "model_ll_per_note": ll_model / n, "baseline_ll_per_note": ll_base / n}


def pattern_group(s: Sample) -> str:
    f = s.f
    if f.pattern in STREAM_FAMILY:
        return "spaced streams" if f.run_length >= 4 and f.run_spacing > STREAM_SPACING_CAP_RADII else "streams"
    if f.pattern == ALT:
        return "alt"
    if f.pattern == JUMP:
        return "jumps"
    if f.pattern == IRREGULAR:
        return "irregular rhythms"
    if s.r.obj.kind == SLIDER:
        return "slider heads"
    return "other notes"


def breakdown(model: UnifiedModel, samples: list[Sample]) -> dict:
    """Misses attributed to each component, overall and per kind of pattern.

    Predicted first misses are scaled by their pattern's chain factor, and so is their
    attribution: the misses a first miss drags along have the same cause."""
    X, _, kept = model.matrix(samples)
    p, excess = model.attribute(X)
    chain = np.array([model.chain(s) for s in kept])
    p, excess = p * chain, excess * chain[:, None]
    notes, observed = defaultdict(int), defaultdict(int)
    for s in samples:
        if s.f.pattern != FIRST:
            g = pattern_group(s)
            notes[g] += 1
            observed[g] += s.missed
    idx_by_group = defaultdict(list)
    for i, s in enumerate(kept):
        idx_by_group[pattern_group(s)].append(i)
    total, after, locked = chain_stats(samples)
    out = {"observed": float(sum(observed.values())), "expected": float(p.sum()), "notes": sum(notes.values()),
           "after_miss": after, "notelocked": locked,
           "components": dict(zip(COMPONENTS, excess.sum(axis=0).tolist())), "groups": {}}
    for g, idx in idx_by_group.items():
        idx = np.array(idx)
        out["groups"][g] = {"notes": notes[g], "observed": float(observed[g]), "expected": float(p[idx].sum()),
                            "chain": model.chains.get(g, 1.0),
                            "components": dict(zip(COMPONENTS, excess[idx].sum(axis=0).tolist()))}
    return out


def load() -> UnifiedModel | None:
    try:
        return UnifiedModel(**json.loads(MODEL_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None


def set_stream_runs(model: UnifiedModel, samples: list[Sample]):
    """The run-level stream model of the skill profile, on the player's recent plays."""
    from .skills import STREAM_PATTERNS, _runs, stream_ur_curve
    model.stream_ur = stream_ur_curve(samples) or {}
    runs = _runs(samples, STREAM_PATTERNS)
    if not runs:
        return
    a = np.array(runs, dtype=float)
    m = fit_logistic(["bpm/10", "log length", "spacing"],
                     np.column_stack([(a[:, 0] - 180) / 10, np.log(a[:, 1]), a[:, 2]]), a[:, 3])
    if m is not None:
        model.stream_runs = [float(v) for v in m.coef]
    long_fast = a[(a[:, 1] >= 8) & (a[:, 0] >= 180)]
    if len(long_fast) >= 20:
        model.stream_bpm_top = float(np.percentile(long_fast[:, 0], 99))


def set_usual(model: UnifiedModel, parts: dict):
    """Remember the player's usual miss rate and component shares (from breakdown on recent plays)."""
    model.usual_rate = parts["observed"] / max(parts["notes"], 1)
    total = sum(parts["components"].values()) or 1.0
    model.usual_shares = {c: v / total for c, v in parts["components"].items()}


def save(model: UnifiedModel):
    MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    MODEL_PATH.write_text(json.dumps(asdict(model), indent=1), encoding="utf-8")


def describe(model: UnifiedModel, cv: dict, parts: dict, verbose: bool = False) -> list[str]:
    lines = []
    if cv:
        lines.append(f"Held-out fit per note: {cv['model_ll_per_note']:.4f} vs {cv['baseline_ll_per_note']:.4f} "
                     f"for per-pattern miss rates (closer to 0 is better)")
    lines.append(f"Misses: {parts['observed']:.0f} observed, {parts['expected']:.0f} predicted, "
                 f"on {parts['notes']} notes")
    if parts["observed"]:
        lines.append(f"{parts['after_miss'] / parts['observed']:.0%} of your misses come right after another miss "
                     f"({parts['notelocked'] / max(parts['after_miss'], 1):.0%} of those are notelock): the model "
                     f"learns from first misses and adds each pattern's chain")
    total = sum(parts["components"].values()) or 1.0
    lines.append("")
    lines.append("Where your misses come from (share of the misses above your easiest content):")
    for c, v in sorted(parts["components"].items(), key=lambda kv: -kv[1]):
        if v / total >= 0.005:
            lines.append(f"  {COMPONENT_NAMES[c]:<42} {v / total:6.1%}  ({v:.0f} misses)")
    lines.append("")
    lines.append("What each kind of pattern costs you, and why:")
    for g, d in sorted(parts["groups"].items(), key=lambda kv: -kv[1]["observed"]):
        comp_total = sum(d["components"].values())
        top = sorted(d["components"].items(), key=lambda kv: -kv[1])
        mix = ", ".join(f"{COMPONENT_NAMES[c].split(' (')[0].lower()} {v / comp_total:.0%}"
                        for c, v in top if comp_total and v / comp_total >= 0.08)
        lines.append(f"  {g:<18} {d['notes']:6d} notes, miss {d['observed'] / d['notes']:5.2%} "
                     f"(predicted {d['expected'] / d['notes']:5.2%}), {d['chain']:.1f} misses per first miss: {mix}")
    easier = [n for n, c, t in zip(model.features, model.coef[1:], model.t[1:])
              if c < 0 and t <= -2 and " >" not in n and n not in SPLINES and n not in CONTROLS]
    if easier:
        lines.append("")
        lines.append("Not harder for you in your plays (no misses attributed): " + ", ".join(easier))
    if verbose:
        lines.append("")
        lines.append("Coefficients (log-odds per unit, t):")
        for name, comp, c, t in zip(model.features, model.components, model.coef[1:], model.t[1:]):
            lines.append(f"  {name:<22} {comp:<15} {c:+7.3f}  t {t:6.1f}")
    return lines
