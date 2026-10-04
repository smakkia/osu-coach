"""Calibration on replays from players of every skill level.

The thresholds (stream/alt/jump spacing, stamina density, setup advice...) were
tuned on one player. This runs the whole pipeline on a dataset laid out as

    dataset/
      <tier>/ ... *.osr      one folder per skill level, e.g. "1-rank_100k-500k"
      maps/   ... *.osu      optional: maps missing from the local Songs folder

and reports, per tier: how well the simulation matches the replays, how the
maps split into patterns (and where the classification thresholds fall on the
real spacing/BPM distributions), miss rates per category, and how often each
piece of advice fires, including setup advice that should be rare.
"""

import hashlib
import json
import math
import re
import statistics
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

from .advice import HIGH_AR, STAMINA_HIGH_NPS, build_insights, category_of, samples_from_play
from .analysis import summarize
from .beatmap import parse_beatmap
from .features import (ALT, ALT_MAX_SPACING_RADII, JUMP, JUMP_MIN_RADII, RUN_PATTERNS, STREAM_FAMILY,
                       STREAM_MIN_BPM, STREAM_SPACING_CAP_RADII, STREAM_SPACING_FREE_BPM, extract)
from .judge import judge
from .keys import tap_stats
from .mods import clock_rate
from .replay import parse_replay
from .setup import Setup
from .setup_advice import T_MIN, _fit, jump_clicks, setup_insights

MAPS_DIR = "maps"
OUTPUT_DIR = "calibration"


class MapFinder:
    """.osu files in extra folders (by MD5), then the local osu! install."""

    def __init__(self, extra_dirs: list[Path], index=None):
        self.index = index
        self.extra: dict[str, Path] = {}
        for d in extra_dirs:
            for p in d.rglob("*.osu"):
                self.extra[hashlib.md5(p.read_bytes()).hexdigest()] = p
        self._missing: set[str] = set()

    def find(self, md5: str) -> Path | None:
        if md5 in self.extra:
            return self.extra[md5]
        if self.index is None or md5 in self._missing:
            return None
        found = self.index.find(md5)
        if found is None:
            self._missing.add(md5)
        return found


def _pcts(values, qs=(10, 50, 90)) -> list[float] | None:
    return [float(v) for v in np.percentile(values, qs)] if len(values) else None


def _title_stem(title: str) -> str:
    """Insight titles carry numbers ("streams above 210 BPM"); strip them to count by kind."""
    return re.sub(r"[+-]?\d[\d.,]*%?", "#", title)


def _setup_metrics(samples, taps) -> dict:
    m = {}
    a = jump_clicks(samples)
    if len(a) >= 50:
        d, along, perp, speed = a[:, 0], a[:, 1], a[:, 2], a[:, 7]
        overall, rot = _fit(d, along), _fit(d, perp)
        slow = speed < np.median(speed)
        slow_fit = _fit(d[slow], along[slow])
        m.update(jumps=len(a), scale=overall.slope, scale_t=overall.t,
                 slow_scale=slow_fit.slope if slow_fit else None,
                 rotation_deg=math.degrees(math.atan(rot.slope)), rotation_t=rot.t)
    presses = sum(t.presses for t in taps)
    run_misses = sum(1 for s in samples if s.missed and s.f.run_length > 1)
    m.update(presses=presses, ghosts=sum(len(t.ghost_presses) for t in taps),
             stuck=sum(len(t.stuck_misses) for t in taps), run_misses=run_misses)
    return m


def analyze_player(job: tuple[str, str, list[tuple[str, str]]]) -> dict:
    """One player's replays in one tier -> aggregates (runs in a worker process)."""
    tier, player, items = job
    out = {
        "tier": tier, "player": player, "plays": [], "errors": [],
        "objects": Counter(), "misses": Counter(), "acc_n": Counter(), "not300": Counter(),
        "stream_runs": [], "alt_runs": [], "stream_speed_runs": [], "jump_radii": [], "load_nps": [],
        "high_ar_objects": 0, "insights": [], "setup": {},
    }
    samples, taps, play = [], [], 0
    for replay_path, map_path in items:
        try:
            replay = parse_replay(Path(replay_path))
            beatmap = parse_beatmap(Path(map_path))
            results, diff = judge(replay, beatmap)
        except Exception as e:  # corrupt files exist in the wild
            out["errors"].append(f"{Path(replay_path).name}: {type(e).__name__}: {e}")
            continue
        rate = clock_rate(replay.mods)
        s = summarize(results, diff.radius, rate)
        c = s.counts
        sim = (c[300], c[100], c[50], c[0])
        real = (replay.count_300, replay.count_100, replay.count_50, replay.count_miss)
        feats = extract(results, diff, rate, beatmap)
        ps = samples_from_play(feats, diff, rate, play=play)
        out["plays"].append({
            "replay": Path(replay_path).name, "map": beatmap.display_name, "mods": replay.mods,
            "sim": sim, "real": real, "misjudged": sum(abs(a - b) for a, b in zip(sim, real)) / 2,
            "objects": sum(real), "ur": s.unstable_rate, "ar": ps[0].ar if ps else None,
        })
        for smp in ps:
            f = smp.f
            cat = category_of(f.pattern)
            key = f.pattern
            out["objects"][key] += 1
            out["misses"][key] += smp.missed
            if smp.acc_eligible:
                out["acc_n"][cat] += 1
                out["not300"][cat] += smp.not_300
            out["load_nps"].append(round(f.load_nps, 1))
            out["high_ar_objects"] += smp.ar > HIGH_AR
            if f.pattern == JUMP:
                out["jump_radii"].append(round(f.distance_radii, 2))
            if f.run_position == 0 and f.run_length >= 4 and f.bpm:
                run = (round(f.bpm, 1), f.run_length, round(f.run_spacing, 2))
                if f.pattern in STREAM_FAMILY:
                    out["stream_runs"].append(run)
                elif f.pattern == ALT:
                    out["alt_runs"].append(run)
                if STREAM_MIN_BPM <= f.bpm < STREAM_SPACING_FREE_BPM:
                    out["stream_speed_runs"].append(run)
        samples += ps
        taps.append(tap_stats(replay.frames, results, {f.r.obj.index for f in feats if f.pattern in RUN_PATTERNS}))
        play += 1

    if samples:
        found = build_insights(samples) + setup_insights(samples, taps, Setup())
        out["insights"] = [{"kind": i.kind, "category": i.category, "title": i.title,
                            "stem": _title_stem(i.title), "impact": i.impact} for i in found]
        out["setup"] = _setup_metrics(samples, taps)
    return out


def collect_jobs(dataset: Path, maps: MapFinder, log=print) -> tuple[list, Counter, list[str]]:
    """Groups replays by (tier, player) and resolves their maps."""
    groups: dict[tuple[str, str], list] = defaultdict(list)
    skipped: Counter = Counter()
    missing: list[str] = []
    tiers = sorted(p for p in dataset.iterdir() if p.is_dir() and p.name not in (MAPS_DIR, OUTPUT_DIR))
    if not tiers:
        log(f"no tier folders in {dataset}")
    for tier in tiers:
        for path in sorted(tier.rglob("*.osr")):
            try:
                replay = parse_replay(path, frames=False)   # the header is enough to group it
            except Exception:
                skipped["unreadable replay"] += 1
                continue
            if replay.mode != 0:
                skipped["not osu!standard"] += 1
                continue
            if replay.mods & (128 | 8192):
                skipped["Relax/Autopilot"] += 1
                continue
            map_path = maps.find(replay.beatmap_md5)
            if map_path is None:
                skipped["map not found"] += 1
                missing.append(f"{replay.beatmap_md5}\t{tier.name}/{path.relative_to(tier)}")
                continue
            groups[(tier.name, replay.player)].append((str(path), str(map_path)))
    jobs = [(t, p, items) for (t, p), items in sorted(groups.items())]
    return jobs, skipped, missing


def run(jobs: list, workers: int | None = None, progress=None) -> list[dict]:
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        for n, r in enumerate(pool.map(analyze_player, jobs), 1):
            results.append(r)
            if progress:
                progress(n, len(jobs), r)
    return results


def _share(n: int, total: int) -> float:
    return n / total if total else 0.0


def summarize_tiers(results: list[dict]) -> dict:
    by_tier: dict[str, list[dict]] = defaultdict(list)
    for r in results:
        by_tier[r["tier"]].append(r)
    report = {}
    for tier, players in by_tier.items():
        plays = [p for r in players for p in r["plays"]]
        objects, misses, acc_n, not300 = Counter(), Counter(), Counter(), Counter()
        for r in players:
            objects.update(r["objects"]), misses.update(r["misses"])
            acc_n.update(r["acc_n"]), not300.update(r["not300"])
        total = sum(objects.values())
        streams = [run for r in players for run in r["stream_runs"]]
        alts = [run for r in players for run in r["alt_runs"]]
        mid = [run for r in players for run in r["stream_speed_runs"]]
        jumps = [x for r in players for x in r["jump_radii"]]
        load = [x for r in players for x in r["load_nps"]]

        cat_obj, cat_miss = Counter(), Counter()
        for pattern, n in objects.items():
            cat_obj[category_of(pattern)] += n
            cat_miss[category_of(pattern)] += misses[pattern]

        fired: dict[str, Counter] = defaultdict(Counter)
        eligible = [r for r in players if r["plays"]]
        for r in eligible:
            for stem in {i["stem"] for i in r["insights"]}:
                kind = next(i["kind"] for i in r["insights"] if i["stem"] == stem)
                cat = next(i["category"] for i in r["insights"] if i["stem"] == stem)
                fired[f"{cat}/{kind}"][stem] += 1

        setups = [r["setup"] for r in players if r["setup"].get("jumps", 0) >= 200]
        scales = [s["scale"] for s in setups]
        rotations = [s["rotation_deg"] for s in setups]
        taps = [r["setup"] for r in players if r["setup"].get("presses")]

        report[tier] = {
            "players": len(players),
            "plays": len(plays),
            "objects": total,
            "sim_exact": _share(sum(p["sim"] == p["real"] for p in plays), len(plays)),
            "sim_object_error": _share(sum(p["misjudged"] for p in plays), sum(p["objects"] for p in plays)),
            "worst_plays": sorted(({"replay": p["replay"], "map": p["map"], "sim": p["sim"], "real": p["real"]}
                                   for p in plays if p["sim"] != p["real"]),
                                  key=lambda p: -sum(abs(a - b) for a, b in zip(p["sim"], p["real"])))[:10],
            "ur_median": statistics.median(p["ur"] for p in plays) if plays else None,
            "high_ar_play_share": _share(sum((p["ar"] or 0) > HIGH_AR for p in plays), len(plays)),
            "pattern_share": {k: _share(v, total) for k, v in objects.most_common()},
            "pattern_miss_rate": {k: _share(misses[k], v) for k, v in objects.most_common()},
            "category_share": {k: _share(v, total) for k, v in cat_obj.most_common()},
            "category_miss_rate": {k: _share(cat_miss[k], v) for k, v in cat_obj.most_common()},
            "not300_rate": {k: _share(not300[k], v) for k, v in acc_n.most_common()},
            "stream_bpm_p10_50_90": _pcts([r[0] for r in streams]),
            "stream_spacing_p10_50_90": _pcts([r[2] for r in streams]),
            "alt_bpm_p10_50_90": _pcts([r[0] for r in alts]),
            "alt_spacing_p10_50_90": _pcts([r[2] for r in alts]),
            "stream_speed_runs": {
                "count": len(mid),
                "spacing_p10_50_90": _pcts([r[2] for r in mid]),
                "share_over_cap": _share(sum(r[2] > STREAM_SPACING_CAP_RADII for r in mid), len(mid)),
            },
            "jump_radii_p10_50_90": _pcts(jumps),
            "jumps_near_min": _share(sum(x < JUMP_MIN_RADII + 0.5 for x in jumps), len(jumps)),
            "alt_near_cap": _share(sum(r[2] > ALT_MAX_SPACING_RADII - 0.5 for r in alts), len(alts)),
            "stamina_object_share": _share(sum(x >= STAMINA_HIGH_NPS for x in load), len(load)),
            "load_nps_p50_90_99": _pcts(load, (50, 90, 99)),
            "insights_fired": {k: dict(v.most_common()) for k, v in sorted(fired.items())},
            "insight_players": len(eligible),
            "setup": {
                "players_with_200_jumps": len(setups),
                "scale_p10_50_90": _pcts(scales),
                "rotation_deg_p10_50_90": _pcts(rotations),
                "scale_significant": sum(abs(s["scale"]) >= 0.03 and abs(s["scale_t"]) >= T_MIN for s in setups),
                "ghosts_per_1000_p50_90": _pcts([1000 * s["ghosts"] / s["presses"] for s in taps], (50, 90)),
                "stuck_share_p50_90": _pcts([s["stuck"] / s["run_misses"] for s in taps if s["run_misses"]], (50, 90)),
            },
        }
    return report


def _fmt(p: list[float] | None, unit: str = "", digits: int = 1) -> str:
    return "-" if p is None else " / ".join(f"{x:.{digits}f}" for x in p) + unit


def print_report(report: dict, skipped: Counter):
    if skipped:
        print("skipped: " + ", ".join(f"{k} {v}" for k, v in skipped.most_common()))
    for tier, t in sorted(report.items()):
        print()
        print(f"=== {tier}: {t['players']} players, {t['plays']} plays, {t['objects']} objects ===")
        ur = f"{t['ur_median']:.0f}" if t["ur_median"] is not None else "-"
        print(f"simulation: {t['sim_exact']:.1%} plays exact, {t['sim_object_error']:.3%} objects misjudged; "
              f"median UR {ur}; AR>10 in {t['high_ar_play_share']:.0%} of plays")
        print("category    share   miss   100/50")
        for cat, share in t["category_share"].items():
            n300 = t["not300_rate"].get(cat)
            print(f"  {cat:<10} {share:6.1%} {t['category_miss_rate'][cat]:6.2%} "
                  f"{f'{n300:6.1%}' if n300 is not None else '     -'}")
        print("patterns: " + ", ".join(f"{k} {v:.1%} (miss {t['pattern_miss_rate'][k]:.1%})"
                                       for k, v in t["pattern_share"].items()))
        print(f"stream runs BPM p10/50/90: {_fmt(t['stream_bpm_p10_50_90'], '', 0)}, "
              f"spacing {_fmt(t['stream_spacing_p10_50_90'], 'r', 2)}")
        print(f"alt runs    BPM p10/50/90: {_fmt(t['alt_bpm_p10_50_90'], '', 0)}, "
              f"spacing {_fmt(t['alt_spacing_p10_50_90'], 'r', 2)}; within 0.5r of the "
              f"{ALT_MAX_SPACING_RADII:g}r cap: {t['alt_near_cap']:.0%}")
        m = t["stream_speed_runs"]
        print(f"runs at {STREAM_MIN_BPM:g}-{STREAM_SPACING_FREE_BPM:g} BPM: {m['count']}, spacing {_fmt(m['spacing_p10_50_90'], 'r', 2)}, "
              f"{m['share_over_cap']:.0%} over the {STREAM_SPACING_CAP_RADII:g}r cap (-> alt)")
        print(f"jumps radii p10/50/90: {_fmt(t['jump_radii_p10_50_90'], 'r', 2)}; "
              f"within 0.5r of the {JUMP_MIN_RADII:g}r minimum: {t['jumps_near_min']:.0%}")
        print(f"density nps p50/90/99: {_fmt(t['load_nps_p50_90_99'])}; "
              f"objects in stamina sections (>= {STAMINA_HIGH_NPS:g} nps): {t['stamina_object_share']:.1%}")
        s = t["setup"]
        print(f"setup ({s['players_with_200_jumps']} players with 200+ jumps): scale p10/50/90 "
              f"{_fmt([x * 100 for x in s['scale_p10_50_90']] if s['scale_p10_50_90'] else None, '%')}, "
              f"significant >=3%: {s['scale_significant']}; rotation {_fmt(s['rotation_deg_p10_50_90'], ' deg')}; "
              f"ghosts/1000 p50/90 {_fmt(s['ghosts_per_1000_p50_90'], '', 2)}; "
              f"stuck share p50/90 {_fmt([x * 100 for x in s['stuck_share_p50_90']] if s['stuck_share_p50_90'] else None, '%')}")
        print(f"advice fired (players out of {t['insight_players']}):")
        for group, stems in t["insights_fired"].items():
            for stem, n in stems.items():
                print(f"  [{group}] {n:>3}  {stem}")
        if t["worst_plays"]:
            print("largest simulation mismatches (sim vs replay 300/100/50/miss):")
            for p in t["worst_plays"][:5]:
                print(f"  {p['replay']}: {tuple(p['sim'])} vs {tuple(p['real'])}  {p['map']}")


def save(report: dict, results: list[dict], skipped: Counter, missing: list[str], out: Path):
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps({"skipped": skipped, "tiers": report}, indent=1, default=_json_default), encoding="utf-8")
    raw = [{k: v for k, v in r.items() if k not in ("load_nps",)} for r in results]
    (out / "players.json").write_text(json.dumps(raw, default=_json_default), encoding="utf-8")
    if missing:
        (out / "missing_maps.txt").write_text("md5\treplay\n" + "\n".join(missing) + "\n", encoding="utf-8")


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    raise TypeError(f"{type(o).__name__} is not JSON serializable")
