"""Command line entry point: python -m osu_coach <command>."""

import argparse
import math
import sys
from pathlib import Path

from .advice import HIGH_AR, ExpectedModel, build_insights, effective_ar, samples_from_play
from .analysis import summarize
from .coach import prioritize
from .explain import explain_play, timestamp
from .features import RUN_PATTERNS
from .keys import tap_stats
from .setup import CONFIG_PATH, Setup, load_setup, save_setup
from .setup_advice import setup_insights
from .features import extract
from .beatmap import SLIDER, SPINNER, parse_beatmap
from .judge import judge
from .locate import BeatmapIndex, default_osu_dir, latest_replay, replays_dir
from .mods import Mods, clock_rate, mods_string
from .replay import parse_replay
from .collect import collect
from .replay_index import ReplayIndex

RESULT_LABEL = {300: "300", 100: "100", 50: "50", 0: "MISS", None: "-"}


def _osu_dir(args) -> Path:
    osu_dir = Path(args.osu_dir) if args.osu_dir else default_osu_dir()
    if not osu_dir or not osu_dir.exists():
        sys.exit("osu! folder not found: pass it with --osu-dir or set OSU_DIR")
    return osu_dir


def _load(replay_path: Path, index: BeatmapIndex):
    replay = parse_replay(replay_path)
    if replay.mode != 0:
        return replay, None, "not an osu!standard replay"
    if replay.mods & (128 | 8192):
        return replay, None, "Relax/Autopilot replays are not supported"
    map_path = index.find(replay.beatmap_md5)
    if map_path is None:
        return replay, None, f"beatmap {replay.beatmap_md5} not found in Songs"
    return replay, parse_beatmap(map_path), None


def cmd_analyze(args):
    osu_dir = _osu_dir(args)
    replay_path = Path(args.replay) if args.replay else latest_replay(osu_dir)
    if replay_path is None:
        sys.exit("no replays found")
    index = BeatmapIndex(osu_dir)
    replay, beatmap, error = _load(replay_path, index)
    if error:
        sys.exit(error)

    results, diff = judge(replay, beatmap)
    rate = clock_rate(replay.mods)
    s = summarize(results, diff.radius, rate)

    print(f"{beatmap.display_name} +{mods_string(replay.mods)}  ({replay.player})")
    ar_note = f" ({effective_ar(diff, rate):.1f} with {mods_string(replay.mods)})" if rate != 1 else ""
    print(f"CS {diff.cs:.1f}  AR {diff.ar:.1f}{ar_note}  OD {diff.od:.1f}  radius {diff.radius:.1f}px  "
          f"hit windows 300/100/50: +-{diff.hit300}/{diff.hit100}/{diff.hit50}ms")
    print(f"Setup: {load_setup().describe()}")
    print()

    if args.objects:
        print(f"{'#':>4} {'time':>8} {'type':<7} {'res':>4} {'err ms':>7} {'aim dx':>7} {'aim dy':>7} "
              f"{'dist/r':>6}  notes")
        for r in results:
            o = r.obj
            if o.kind == SPINNER:
                print(f"{o.index:>4} {o.time:>8} {'spinner':<7} {RESULT_LABEL[r.result]:>4} "
                      f"{'':>7} {'':>7} {'':>7} {'':>6}  spins {r.spins}/{r.spins_required + 1}")
                continue
            off = r.aim_offset
            notes = []
            if r.miss_reason and (r.result == 0 or r.head_result == 0):
                notes.append(f"miss: {r.miss_reason}")
            if o.kind == SLIDER:
                notes.append(f"body {r.ticks_hit}/{r.ticks_total}")
                if r.slider_break_kind:
                    notes.append(f"dropped {r.slider_break_kind} @{r.slider_break_time}")
            print(f"{o.index:>4} {o.time:>8} {o.kind:<7} {RESULT_LABEL[r.result]:>4} "
                  f"{r.hit_error if r.hit_error is not None else '':>7} "
                  f"{f'{off[0]:.1f}' if off else '':>7} {f'{off[1]:.1f}' if off else '':>7} "
                  f"{f'{math.hypot(*off) / diff.radius:.2f}' if off else '':>6}  {', '.join(notes)}")
        print()

    c = s.counts
    print(f"Simulated: {c[300]}x300 {c[100]}x100 {c[50]}x50 {c[0]}xMiss")
    print(f"Replay   : {replay.count_300}x300 {replay.count_100}x100 {replay.count_50}x50 "
          f"{replay.count_miss}xMiss")
    print(f"UR {s.unstable_rate:.1f}  mean error {s.mean_error:+.1f}ms "
          f"(early {s.mean_early:.1f} / late +{s.mean_late:.1f})")
    print(f"Mean aim distance from centre: {s.aim_mean_distance:.2f} radii")
    print(f"Slider breaks: {s.slider_breaks}")

    feats = extract(results, diff, rate, beatmap)
    play_samples = samples_from_play(feats, diff, rate, hidden=bool(replay.mods & Mods.Hidden))
    play_taps = tap_stats(replay.frames, results, {f.r.obj.index for f in feats if f.pattern in RUN_PATTERNS})
    setup = load_setup()

    habit_samples, used = [], 0
    if args.habits > 0:
        habit_samples, habit_taps, used = _collect_samples(ReplayIndex(osu_dir, index), index, args.habits,
                                                           replay.player)
    insights = (build_insights(habit_samples) + setup_insights(habit_samples, habit_taps, setup)
                if habit_samples else [])
    model = ExpectedModel(habit_samples) if habit_samples else None
    normal_ar = [s for s in habit_samples if s.ar <= HIGH_AR]
    normal_ar_model = ExpectedModel(normal_ar) if len(normal_ar) >= 1000 else None
    episodes = explain_play(play_samples, replay.frames, diff.radius, model, normal_ar_model, play_taps)

    print()
    print(f"== What to work on (map score = share of this play's mistakes, "
          f"habits score = share of the bad habits found in your last {used} plays) ==")
    priorities = prioritize(episodes, insights, play_samples, habit_samples)
    if not priorities:
        print("Nothing to flag: clean play, and no relevant bad habits for this map.")
    for n, pr in enumerate(priorities, 1):
        habits = f"{pr.habits_score:.0f}" if pr.habits_score is not None else "n/a"
        print()
        print(f"{n}. {pr.name}   map {pr.map_score:.0f} | habits {habits}")
        context = []
        if pr.content:
            context.append(f"this map: {pr.content}")
        if pr.play_rate is not None:
            what = pr.rate_label
            usual = f", usually {pr.usual_rate:.1%}" if pr.usual_rate is not None else ""
            context.append(f"{what} here {pr.play_rate:.1%}{usual}")
        if context:
            print(f"   ({'; '.join(context)})")
        for i in pr.insights:
            print(f"   Bad habit: {i.title}. {i.detail}")
        if pr.episodes:
            print("   In this play:")
            for e in pr.episodes[:args.max_episodes]:
                print(f"   - {timestamp(e.time)}  {e.title}")
                for reason in e.reasons:
                    print(f"       {reason}")
            if len(pr.episodes) > args.max_episodes:
                print(f"   (+{len(pr.episodes) - args.max_episodes} more)")

    notes = [i for i in insights if i.kind == "info"]
    if notes:
        print()
        for i in notes:
            print(f"[i] {i.title}: {i.detail}")


def _collect_samples(replays: ReplayIndex, index: BeatmapIndex, last: int, player: str | None, select=None):
    """Samples from the `last` most recent plays (by `player`, default the owner, and only plays
    whose index entry passes `select(entry)`), judged in parallel."""
    jobs = []
    for entry in replays.recent(player, select):
        if len(jobs) >= last:
            break
        map_path = index.find(entry.md5)
        if map_path is not None:
            jobs.append((str(replays.path(entry)), str(map_path)))
    samples, taps, used = [], [], 0
    for result in collect(jobs):
        if result is None:
            continue
        play_samples, play_taps = result
        for s in play_samples:
            s.play = used
        samples += play_samples
        taps.append(play_taps)
        used += 1
    return samples, taps, used


def _print_insights(insights):
    print()
    if not insights:
        print("Not enough data for insights yet.")
        return
    for i in insights:
        tag = {"miss": "!", "accuracy": "~", "info": "i"}[i.kind]
        print(f"[{tag}] {i.title}")
        print(f"    {i.detail}")


def cmd_profile(args):
    """Insights aggregated over the most recent plays."""
    osu_dir = _osu_dir(args)
    index = BeatmapIndex(osu_dir)
    samples, taps, used = _collect_samples(ReplayIndex(osu_dir, index), index, args.last, args.player)
    setup = load_setup()
    print(f"Profile over {used} plays, {len(samples)} objects")
    print(f"Setup: {setup.describe()}")
    insights = build_insights(samples) + setup_insights(samples, taps, setup)
    insights.sort(key=lambda i: (i.kind == "info", -i.impact))
    _print_insights(insights)


def cmd_skills(args):
    """Comfortable level and limit per skillset, from recent plays."""
    from .skills import build_profile, describe, save_profile
    from .advice import LOW_AR, is_high_ar, is_low_ar
    osu_dir = _osu_dir(args)
    index = BeatmapIndex(osu_dir)
    replays = ReplayIndex(osu_dir, index)
    samples, _, used = _collect_samples(replays, index, args.last, args.player)
    # plays below AR 9 and above 10 are rarer: they get their own windows, however far back
    low_samples, _, low_used = _collect_samples(replays, index, args.ar_plays, args.player,
                                                select=lambda e: is_low_ar(e.ar))
    high_samples, _, high_used = _collect_samples(replays, index, args.ar_plays, args.player,
                                                  select=lambda e: is_high_ar(e.ar))
    for offset, extra in ((100_000, low_samples), (200_000, high_samples)):
        for s in extra:
            s.play += offset  # keep play ids distinct from the recent window's
    print(f"Skill profile over {used} plays, {len(samples)} objects "
          f"(+ {low_used} plays below AR {LOW_AR:g} for reading, {high_used} above AR {HIGH_AR:g} for high AR)")
    print("comfortable = runs cleared 85% of the time / notes missed at most 2%; "
          "limit = runs broken half the time / 8% missed")
    print("'>' = beyond the hardest you played, '<' = below the easiest: the real value is unknown")
    skills = build_profile(samples, low_samples, high_samples)
    for skill in skills:
        print()
        for line in describe(skill):
            print(line)
    save_profile(skills, used, args.player)


def _fit_player_model(osu_dir: Path, index: BeatmapIndex, replays: ReplayIndex, args):
    """Unified model on the recent plays plus the low/high AR windows; also returns the recent samples."""
    from .advice import is_high_ar, is_low_ar
    from . import model as um
    samples, _, used = _collect_samples(replays, index, args.last, args.player)
    extra = []
    for offset, select in ((100_000, lambda e: is_low_ar(e.ar)), (200_000, lambda e: is_high_ar(e.ar))):
        more, _, _ = _collect_samples(replays, index, args.ar_plays, args.player, select=select)
        for s in more:
            s.play += offset
        extra += more
    print(f"Model over {used} recent plays (+ {len({s.play for s in extra})} plays below AR 9 / above AR 10)")
    fitted = um.fit(samples + extra, chain_samples=samples)
    if fitted is None:
        sys.exit("not enough data")
    parts = um.breakdown(fitted, samples)
    um.set_usual(fitted, parts)
    um.set_stream_runs(fitted, samples)
    um.save(fitted)
    return fitted, samples, extra, parts


def cmd_model(args):
    """One model for every note: where your misses come from, and what each pattern costs you."""
    from . import model as um
    osu_dir = _osu_dir(args)
    index = BeatmapIndex(osu_dir)
    fitted, samples, extra, parts = _fit_player_model(osu_dir, index, ReplayIndex(osu_dir, index), args)
    cv = um.cross_validate(samples + extra) if args.validate else {}
    for line in um.describe(fitted, cv, parts, args.verbose):
        print(line)


SKILL_ALIASES = {
    "jump": "jump", "jumps": "jump", "stream": "stream", "streams": "stream", "tap": "stream", "speed": "speed", "fast": "speed",
    "alt": "alt", "finger": "finger control", "fingercontrol": "finger control", "burst": "finger control",
    "bursts": "finger control", "tech": "tech", "slider": "tech", "sliders": "tech", "aim": "aim control",
    "control": "aim control", "aimcontrol": "aim control", "reading": "reading", "read": "reading",
    "precision": "precision", "cs": "precision", "small": "precision",
}
RANGES = (("stars", "Star rating"), ("ar", "AR, reading"), ("cs", "CS, precision"), ("od", "OD, accuracy"),
          ("length", "Length, m:ss"), ("bpm", "BPM"))


def _choose_skills(model, args) -> list[str]:
    """Skills (map types) to train: from --skill (names or aliases, comma separated), else chosen from a
    menu when run in a terminal, else the ones costing the player most."""
    from . import recommend as rc
    share = lambda sk: model.usual_shares.get(rc.SKILLS[sk][1], 0.0)
    by_cost = sorted(rc.SKILLS, key=lambda sk: -share(sk))
    default = by_cost[:args.top]
    if args.skill:
        targets = []
        for w in (w.strip().lower() for arg in args.skill for w in arg.split(",") if w.strip()):
            sk = SKILL_ALIASES.get(w.replace(" ", ""), w)
            if sk not in rc.SKILLS:
                sys.exit(f"unknown skill {w!r}; choose from: {', '.join(rc.SKILLS)}")
            targets.append(sk)
        return list(dict.fromkeys(targets))
    if not sys.stdin.isatty():
        return default
    print("Which skillsets do you want to train? (share of your misses in that kind of pattern)")
    for n, sk in enumerate(by_cost, 1):
        mark = "  *" if sk in default else ""
        print(f"  {n}. {sk.capitalize():<16} {share(sk):5.0%}{mark}")
    answer = input("Numbers separated by commas, Enter for the ones marked *: ").strip()
    if not answer:
        return default
    try:
        picked = [by_cost[int(x) - 1] for x in answer.replace(" ", "").split(",") if x]
    except (ValueError, IndexError):
        sys.exit(f"not a valid choice: {answer!r}")
    return list(dict.fromkeys(picked))


def _choose_ranges(args, band) -> dict:
    """Ranges for stars, effective AR (reading), CS (precision) and OD (accuracy): from the options, else asked
    in the terminal when the skills were chosen from the menu; stars default to the player's usual band."""
    from . import recommend as rc
    texts = {key: getattr(args, key) for key, _ in RANGES}
    if not args.skill and sys.stdin.isatty() and not any(texts.values()):
        print("Ranges (e.g. 8-9.5, 8- or -9.5; length 1:30-3:00; Enter keeps the default):")
        for key, label in RANGES:
            default = f"{band[0]:.2f}-{band[1]:.2f}" if key == "stars" and band else "any"
            texts[key] = input(f"  {label} [{default}]: ").strip() or None
        if not args.loved:
            args.loved = input("  Loved maps too? (they include exploit maps) [y/N]: ").strip().lower() in ("y", "yes", "s", "si")
    try:
        ranges = {key: (rc.parse_length if key == "length" else rc.parse_range)(text) for key, text in texts.items()}
    except ValueError as e:
        sys.exit(str(e))
    lo, hi = ranges.pop("stars")
    if band is None and (lo is None or hi is None):
        sys.exit("not enough recent plays with known star ratings: give --stars")
    stars = (lo if lo is not None else band[0], hi if hi is not None else band[1])
    return {"stars": stars, **{k: v for k, v in ranges.items() if v != (None, None)}}


def cmd_recommend(args):
    """Maps from your Songs folder of the map types you want to train, a step above your level."""
    from . import maptypes
    from . import model as um
    from . import recommend as rc
    from .locate import songs_dir
    from .mapdb import read_osu_db
    osu_dir = _osu_dir(args)
    maps = read_osu_db(osu_dir / "osu!.db")
    index = BeatmapIndex(osu_dir)
    index.use_db({m.md5: m.path for m in maps})   # read once
    replays = ReplayIndex(osu_dir, index)
    try:
        mod_sets = rc.parse_mods(args.mods)
    except ValueError as e:
        sys.exit(str(e))
    model = None if args.refit else um.load()
    if model is None or not model.usual_rate or not model.stream_runs:
        model = _fit_player_model(osu_dir, index, replays, args)[0]
    targets = _choose_skills(model, args)
    infos = {m.md5: m for m in maps}
    owner_plays = replays.recent(args.player)
    ranges = _choose_ranges(args, rc.star_band(infos, [(e.md5, e.mods) for e in owner_plays[:args.last]]))
    band = ranges.pop("stars")
    statuses = tuple(range(8)) if args.any_status else rc.ACCEPTED_STATUS + ((rc.LOVED_STATUS,) if args.loved else ())
    items = rc.candidates(maps, {e.md5 for e in owner_plays}, band, mod_sets, statuses)
    items = [(m, mods) for m, mods in items if rc.in_ranges(m, mods, ranges)]
    def bound(key, v):
        return "" if v is None else f"{int(v) // 60}:{int(v) % 60:02d}" if key == "length" else f"{v:g}"
    limits = "".join(f", {k.upper() if k in ('ar', 'cs', 'od', 'bpm') else k} {bound(k, lo)}-{bound(k, hi)}"
                     for k, (lo, hi) in ranges.items())
    limits += ", ranked and loved" if args.loved else ", ranked" if not args.any_status else ", any status"
    print(f"Your usual: {model.usual_rate:.2%} misses per note; maps between {band[0]:.2f} and {band[1]:.2f} stars"
          f"{limits} ({', '.join(mods_string(m) if m else 'NM' for m in mod_sets)})")
    print(f"{len(items)} never-played candidates in your Songs folder")

    def progress(label, n, total):
        print(f"\r  {label}: {n}/{total}", end="", flush=True)
    if args.online:
        from . import api, online
        try:
            client = api.OsuApi()
        except api.ApiError as e:
            sys.exit(str(e))
        keys = list(dict.fromkeys(rc.ONLINE_KEY.get(sk, "other") for sk in targets))
        skip = {e.md5 for e in owner_plays} | set(infos)   # played, or already in Songs
        from . import typeguess
        guessed = rc.load_predicted()

        def verdict(found, key):
            """The guessed type for the skills searched under this key: yes if any, no if none. From the guesses
            made for the maps in players' top plays, else guessed now from what the API says about the map."""
            name = "DT" if found.mods & Mods.DoubleTime else "NM"
            p = guessed.get(f"{found.info.beatmap_id}:{name}")
            if p is None and found.bm is not None:
                p = typeguess.guess(found.bm, found.bs, name)
            said = [rc.predicted_skill(p, sk) for sk in targets if rc.ONLINE_KEY.get(sk, "other") == key]
            return True if True in said else False if said and all(s is False for s in said) else None
        use_guess = bool(guessed) or typeguess.available()
        if not use_guess:
            print("no map type guesser yet: every online candidate is downloaded to be classified")
        try:
            found = online.discover(client, keys, maps, band, rc.tap_window(model), mod_sets, skip,
                                    pages=args.online_pages, per_skill=args.online_max, progress=progress,
                                    verdict=verdict if use_guess else None)
        except api.ApiError as e:
            sys.exit(f"osu! API: {e}")
        found = [(m, mods) for m, mods in found if rc.in_ranges(m, mods, ranges)]
        print(f"\n{len(found)} candidates from osu! not in your Songs folder")
        items += found
    songs = songs_dir(osu_dir)
    content = rc.contents(songs, items, progress)
    kinds = maptypes.types(songs, items, progress)
    print()
    if args.online:     # the candidates downloaded from the site: their real type replaces the guess from now on
        from . import typeguess
        typeguess.remember([(m, mods, kinds.get(f"{m.md5}:{mods}")) for m, mods in found])
    short = {sk: rc.skill_candidates(sk, items, kinds, content, model) for sk in targets}
    to_profile = list({(m.md5, mods): (m, mods) for sk in targets for m, mods in short[sk]}.values())
    profs = rc.profiles(model, songs, to_profile, progress)
    print()
    for sk in targets:
        print()
        print(f"== {sk.capitalize()}: {model.usual_shares.get(rc.SKILLS[sk][1], 0.0):.0%} of your misses usually ==")
        picks = rc.ladder(sk, short[sk], kinds, content, profs, model, size=args.count)
        if not picks:
            print("  no map found that trains this at your level")
        for n, k in enumerate(picks, 1):
            a = kinds.get(f"{k.info.md5}:{k.mods}")
            print(f"  {n}. " + rc.describe_pick(k, sk, maptypes.label(a) if a else None))


def cmd_maptype(args):
    """Map types of maps in your Songs folder, found by name."""
    from . import maptypes
    from . import recommend as rc
    from .locate import songs_dir
    from .mapdb import read_osu_db
    osu_dir = _osu_dir(args)
    try:
        mod_sets = rc.parse_mods(args.mods)
    except ValueError as e:
        sys.exit(str(e))
    words = " ".join(args.query).lower().split()
    found = [m for m in read_osu_db(osu_dir / "osu!.db")
             if m.mode == 0 and all(w in f"{m.artist} - {m.title} [{m.version}] {m.creator}".lower() for w in words)]
    if not found:
        sys.exit("no map in your Songs folder matches")
    if len(found) > args.limit:
        print(f"{len(found)} maps match, showing the first {args.limit} (narrow the search or use --limit)")
        found = found[:args.limit]
    items = [(m, mods) for m in found for mods in mod_sets]
    kinds = maptypes.types(songs_dir(osu_dir), items)
    for m, mods in items:
        a = kinds.get(f"{m.md5}:{mods}")
        name = f"{m.display_name} +{mods_string(mods) if mods else 'NM'}"
        if not a:
            print(f"{name}: " + (f"under {maptypes.MIN_STARS:g} stars, no type" if maptypes.too_easy(m.stars.get(0))
                                 else "too short to classify"))
            continue
        share = {**a, "tech": a["tech sliders"]}   # tech as a share of notes, not weighted by slider speed
        others = ", ".join(f"{maptypes.NAMES[k].lower()} {share[k]:.0%}" for k in maptypes.KINDS if share[k] >= 0.05)
        print(name)
        print(f"    {maptypes.label(a)}")
        print(f"      aim control {a['aim control']:.1f} (x{maptypes.aim_ratio(a):.2f} the usual for its star rating); "
              f"all of the intense sections: {others}")


def cmd_search(args):
    """Maps of the given types and ranges: from your Songs folder (their real type), or with --online only from
    the osu! site (their guessed type, nothing downloaded), searching page after page until --limit maps are
    found. Unlike recommend, your level plays no part."""
    from . import maptypes
    from . import recommend as rc
    from .locate import songs_dir
    from .mapdb import read_osu_db
    osu_dir = _osu_dir(args)
    wanted = []
    for w in (w.strip().lower() for arg in (args.type or []) for w in arg.split(",") if w.strip()):
        sk = SKILL_ALIASES.get(w.replace(" ", ""), w)
        if sk not in rc.SKILLS:
            sys.exit(f"unknown map type {w!r}; choose from: {', '.join(rc.SKILLS)}")
        wanted.append(sk)
    wanted = list(dict.fromkeys(wanted))
    try:
        mod_sets = rc.parse_mods(args.mods)
        ranges = {key: (rc.parse_length if key == "length" else rc.parse_range)(getattr(args, key)) for key, _ in RANGES}
    except ValueError as e:
        sys.exit(str(e))
    stars = ranges.pop("stars")
    ranges = {k: v for k, v in ranges.items() if v != (None, None)}
    statuses = tuple(range(8)) if args.any_status else rc.ACCEPTED_STATUS + ((rc.LOVED_STATUS,) if args.loved else ())

    def star_ok(s):
        return s is not None and (stars[0] is None or s >= stars[0] - 1e-6) and (stars[1] is None or s <= stars[1] + 1e-6)

    maps = read_osu_db(osu_dir / "osu!.db")
    found = []
    if not args.online:
        items = [(m, mods) for m in maps if m.mode == 0 and m.status in statuses and m.drain_s >= rc.MIN_DRAIN_S
                 and (not args.unplayed or m.unplayed) for mods in mod_sets
                 if star_ok(m.stars.get(rc._star_key(mods))) and rc.in_ranges(m, mods, ranges)]
        print(f"{len(items)} maps in your Songs folder in the ranges; reading their types...")

        def progress(label, n, total):
            print(f"\r  {label}: {n}/{total}", end="", flush=True)
        kinds = maptypes.types(songs_dir(osu_dir), items, progress)
        for m, mods in items:
            a = kinds.get(f"{m.md5}:{mods}")
            if a and all(rc.has_skill(a, sk) for sk in wanted):
                share = rc.skill_share(a, wanted[0]) if wanted else 0.0
                found.append((share, m, mods, maptypes.label(a)))
    else:
        from . import api, online, typeguess
        try:
            client = api.OsuApi()
        except api.ApiError as e:
            sys.exit(str(e))
        guessed = rc.load_predicted()
        local = {m.md5 for m in maps}
        status = "loved" if args.loved and not args.any_status else "ranked"
        # one search per mod (the site's filters and the guesses are for nomod and DT only), read in turn
        searches = []
        for mods in mod_sets:
            if mods & ~int(Mods.DoubleTime):
                print(f"{mods_string(mods)}: only NM and DT can be searched on the site")
                continue
            name, rate = ("DT", 1.5) if mods & Mods.DoubleTime else ("NM", 1.0)
            factor = online.DT_SR_FACTOR if rate > 1 else 1.0
            q = []
            if stars[0] is not None:
                q.append(f"stars>={stars[0] / factor:.2f}")
            if stars[1] is not None:
                q.append(f"stars<={stars[1] / factor:.2f}")
            if "bpm" in ranges:
                lo, hi = ranges["bpm"]
                q += ([f"bpm>={lo / rate:.0f}"] if lo is not None else []) + ([f"bpm<={hi / rate:.0f}"] if hi is not None else [])
            searches.append({"mods": mods, "name": name, "query": " ".join(q), "cursor": None, "done": False})
        seen, examined, pages, downloaded = set(), 0, 0, 0
        songs = songs_dir(osu_dir)
        CHUNK = 10

        def classify(cands):
            """The candidates' .osu downloaded (cached) and read for real; those of the wanted types are found,
            and every real type is kept for the guesser (typeguess.remember)."""
            nonlocal downloaded
            for start in range(0, len(cands), CHUNK):     # a few at a time: stop as soon as enough are found
                if len(found) >= args.limit:
                    return
                got = []
                for info, mods in cands[start:start + CHUNK]:
                    try:
                        info.path = str(client.osu_file(info.beatmap_id))
                    except api.ApiError:
                        continue
                    downloaded += 1
                    got.append((info, mods))
                kinds = maptypes.types(songs, got)
                typeguess.remember([(m, mods, kinds.get(f"{m.md5}:{mods}")) for m, mods in got])
                for m, mods in got:
                    a = kinds.get(f"{m.md5}:{mods}")
                    if a and all(rc.has_skill(a, sk) for sk in wanted):
                        found.append((rc.skill_share(a, wanted[0]) if wanted else 0.0, m, mods, maptypes.label(a)))
        try:
            while len(found) < args.limit and pages < args.max_pages and not all(x["done"] for x in searches):
                for x in searches:
                    if x["done"] or len(found) >= args.limit or pages >= args.max_pages:
                        continue
                    page = client.search(x["query"], status=status, cursor=x["cursor"])
                    pages += 1
                    mods, name = x["mods"], x["name"]
                    sure, maybe = [], []
                    for bs in page.get("beatmapsets", []):
                        for bm in bs.get("beatmaps", []):
                            if bm.get("mode_int", 0) != 0 or bm.get("checksum") in local or (bm["id"], mods) in seen:
                                continue
                            seen.add((bm["id"], mods))
                            info = online._info(bm, bs)
                            if not star_ok(info.stars.get(rc._star_key(mods))) or not rc.in_ranges(info, mods, ranges) \
                                    or info.drain_s < rc.MIN_DRAIN_S:
                                continue
                            examined += 1
                            if maptypes.too_easy(info.stars.get(0)):
                                continue    # no type under MIN_STARS: nothing to find
                            # the guess only sorts: sure no is skipped, sure yes read first, the rest after
                            p = guessed.get(f"{bm['id']}:{name}") or typeguess.guess(bm, bs, name)
                            said = [rc.predicted_skill(p, sk) for sk in wanted]
                            if False in said:
                                continue
                            (sure if said and all(said) else maybe).append((info, mods))
                    classify(sure + maybe)
                    x["cursor"] = page.get("cursor_string")
                    x["done"] = not x["cursor"]
                    print(f"\r  pages read {pages}: {examined} maps in the ranges, {downloaded} downloaded and read, "
                          f"{min(len(found), args.limit)}/{args.limit} found", end="", flush=True)
        except api.ApiError as e:
            print(f"\nosu! API: {e}")
        print()
        if len(found) < args.limit:
            why = ("no more results" if all(x["done"] for x in searches)
                   else f"stopped at {args.max_pages} pages (--max-pages)")
            print(f"only {len(found)} found: {why}")
    print()
    what = " + ".join(rc.SKILLS[sk][0] or sk for sk in wanted) if wanted else "any type"
    where = "on the osu! site, not in your Songs" if args.online else "in your Songs folder"
    found.sort(key=lambda f: -f[0])    # most of the type (or the surest guesses) first
    print(f"{len(found)} maps of {what} {where}" + (f", showing {args.limit}" if len(found) > args.limit else ""))
    for share, m, mods, label in found[:args.limit]:
        v = rc.effective_values(m, mods)
        s = m.stars.get(rc._star_key(mods)) or 0.0
        length = int(v["length"])
        link = f"https://osu.ppy.sh/b/{m.beatmap_id}" if m.beatmap_id > 0 else ""
        print(f"  {m.display_name} +{mods_string(mods) if mods else 'NM'}  {s:.2f}*  {v['bpm']:.0f} BPM  "
              f"AR {v['ar']:.1f} CS {v['cs']:.1f} OD {v['od']:.1f}  {length // 60}:{length % 60:02d}")
        print(f"      [{label}]  {link}")


def cmd_retrain(args):
    """Relabel the maps in Songs and train the map type guesser again (after maptypes changes or new maps)."""
    try:
        from . import typepred
    except ImportError as e:
        sys.exit(f"retraining needs scikit-learn (pip install -r requirements.txt): {e}")

    def progress(label, n, total):
        print(f"\r  {label}: {n}/{total}", end="", flush=True)
    typepred.retrain(relabel=not args.no_relabel, evaluate=args.evaluate, download_unsure=args.download_unsure,
                     unsure_min_stars=args.unsure_min_stars,
                     progress=progress, fetch_ranked=args.fetch_ranked)


def cmd_guess(args):
    """Guessed map types of ranked maps you don't have, found by name."""
    from . import mapvec, ranked_meta
    from . import recommend as rc
    guessed = rc.load_predicted()
    if not guessed:
        sys.exit("no guessed map types yet (they are computed from the top plays of sampled players)")
    words = " ".join(args.query).lower().split()
    maps = {**ranked_meta.load(), **mapvec.load_profiles().get("beatmaps", {})}
    found = [(b, m) for b, m in maps.items() if f"{b}:NM" in guessed
             and all(w in f"{m.get('artist')} - {m.get('title')} [{m.get('version')}]".lower() for w in words)]
    if not found:
        sys.exit("no guessed map matches (only ranked maps not in your Songs have a guess)")
    if len(found) > args.limit:
        print(f"{len(found)} maps match, showing the first {args.limit} (narrow the search or use --limit)")
    for b, m in found[:args.limit]:
        print(f"{m.get('artist')} - {m.get('title')} [{m.get('version')}]  {m.get('stars', 0):.2f}*  "
              f"https://osu.ppy.sh/b/{b}")
        for mods in ("NM", "DT"):
            p = guessed.get(f"{b}:{mods}")
            if not p:
                continue
            if p["confidence"] == "downloaded":
                kind = f"{p['kind']} (read from the .osu)"
            elif p["kind"]:
                kind = f"{p['kind']} ({'type' if p['confidence'] == 'type' else 'main kind only'}, "
                kind += f"{p['p_type'] if p['confidence'] == 'type' else p['p_main']:.0%} sure)"
            else:
                kind = f"unsure (maybe {p['main']}, {p['p_main']:.0%})"
            aim = {True: "aim control", False: "no aim control", None: f"aim control unsure ({p['p_aim']:.0%})"}
            reading = "; reading" if p.get("reading") else ""
            reading += "; speed" if p.get("speed") else ""
            reading += "; precision" if p.get("precision") else ""
            print(f"    {mods}: {kind}; {aim[p['aim control']]}{reading}")


def cmd_profiles(args):
    """Sample players across ranks and keep their best scores (for learning map types from top plays)."""
    from collections import Counter
    from . import api, profiles
    try:
        client = api.OsuApi()
    except api.ApiError as e:
        sys.exit(str(e))

    def progress(label, n, total):
        print(f"\r  {label}: {n}/{total}   ", end="", flush=True)
    players = profiles.sample_players(client, args.per_band, args.seed, progress)
    print()
    data = profiles.top_plays(client, players, progress)
    print()
    profiles.save(data)
    items = Counter((r["beatmap"], r["mods"]) for rows in data["plays"].values() for r in rows)
    print(f"{len(data['plays'])} players, {sum(items.values())} top plays, {len(items)} distinct map+mods, "
          f"{len(data['beatmaps'])} beatmaps  (saved in {profiles.PROFILES_PATH})")
    for lo, hi in profiles.RANK_BANDS:
        band = [p for p in data["players"] if lo <= p["rank"] < hi]
        print(f"  ranks {lo}-{hi}: {len(band)} players")


def cmd_config(args):
    """Show or change the hardware setup used for setting advice."""
    import json
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    if args.reset:
        saved = {}
    if args.device:
        saved["device"] = args.device
    if args.area:
        w, _, h = args.area.lower().partition("x")
        saved["area_w"], saved["area_h"] = float(w), float(h)
    for key in ("sens", "dpi", "keyboard", "rt_press", "rt_release", "actuation"):
        value = getattr(args, key)
        if value is not None:
            saved[key] = value
    save_setup(Setup(**saved))
    print(f"Setup: {load_setup().describe()}")
    print(f"(saved in {CONFIG_PATH}; the tablet area is read from OpenTabletDriver unless you set --area)")
    _config_api(args)


def _config_api(args):
    """osu! API credentials: kept apart from the setup, in api.API_PATH; the secret is never printed."""
    import getpass
    from . import api
    creds = api.load_credentials()
    if args.api_client_id:
        creds = {"client_id": args.api_client_id.strip(), "client_secret": creds.get("client_secret")}
    if args.api_client_secret:
        secret = getpass.getpass("osu! API client secret (not shown): ").strip()
        if secret:
            creds = {"client_id": creds.get("client_id"), "client_secret": secret}
    if args.api_client_id or args.api_client_secret:
        api.save_credentials(creds)
    ok = bool(creds.get("client_id") and creds.get("client_secret"))
    print(f"osu! API: {'client ' + str(creds['client_id']) + ' configured' if ok else 'not configured'}"
          f"  (credentials in {api.API_PATH})")
    if args.api_test and ok:
        try:
            client = api.OsuApi()
            page = client.get("/beatmapsets/search", {"q": "stars>=6 stars<=7", "m": 0, "s": "ranked"}, ttl=0)
            sets = page.get("beatmapsets", [])
            print(f"API test: token ok, search ok ({len(sets)} beatmapsets on the first page"
                  + (f", e.g. {sets[0]['artist']} - {sets[0]['title']}" if sets else "") + ")")
        except api.ApiError as e:
            print(f"API test failed: {e}")


def cmd_validate(args):
    """Compare simulated judgements with the counts stored in each replay."""
    osu_dir = _osu_dir(args)
    index = BeatmapIndex(osu_dir)
    paths = sorted(replays_dir(osu_dir).glob("*.osr"), key=lambda p: p.stat().st_mtime, reverse=True)
    exact = checked = skipped = misjudged = total = 0
    for p in paths[:args.limit]:
        try:
            replay, beatmap, error = _load(p, index)
        except Exception as e:  # corrupt replays exist in the wild
            error = f"parse error: {e}"
        if error:
            skipped += 1
            continue
        results, diff = judge(replay, beatmap)
        c = summarize(results, diff.radius).counts
        sim = (c[300], c[100], c[50], c[0])
        real = (replay.count_300, replay.count_100, replay.count_50, replay.count_miss)
        checked += 1
        misjudged += sum(abs(a - b) for a, b in zip(sim, real)) / 2  # one wrong object moves two counts
        total += sum(real)
        if sim == real:
            exact += 1
        elif args.verbose:
            print(f"{p.name}: sim {sim} vs replay {real}  {beatmap.display_name} +{mods_string(replay.mods)}")
    print(f"replays matching exactly: {exact}/{checked} ({exact / max(checked, 1):.1%}), skipped {skipped}")
    print(f"misjudged objects: {misjudged:.0f}/{total} ({misjudged / max(total, 1):.3%})")


def cmd_calibrate(args):
    """Run the pipeline on a dataset of replays grouped by skill level."""
    from .calibrate import MAPS_DIR, OUTPUT_DIR, MapFinder, collect_jobs, print_report, run, save, summarize_tiers
    dataset = Path(args.dataset)
    if not dataset.is_dir():
        sys.exit(f"dataset folder not found: {dataset}")
    osu_dir = Path(args.osu_dir) if args.osu_dir else default_osu_dir()
    index = BeatmapIndex(osu_dir) if osu_dir and osu_dir.exists() else None
    extra = [dataset / MAPS_DIR] + [Path(m) for m in args.maps or []]
    finder = MapFinder([d for d in extra if d.is_dir()], index)
    print(f"maps: {len(finder.extra)} .osu files in the dataset" + (", plus the local Songs folder" if index else ""))
    jobs, skipped, missing = collect_jobs(dataset, finder)
    print(f"{sum(len(j[2]) for j in jobs)} replays from {len(jobs)} player/tier groups")

    def progress(n, total, r):
        print(f"\r  {n}/{total} players analyzed", end="", flush=True)
    results = run(jobs, args.workers, progress)
    print()
    for r in results:
        for e in r["errors"]:
            print(f"  error: {e}")
    report = summarize_tiers(results)
    print_report(report, skipped)
    out = Path(args.out) if args.out else dataset / OUTPUT_DIR
    save(report, results, skipped, missing, out)
    print()
    print(f"saved to {out}" + (f"; {len(missing)} replays without a map listed in missing_maps.txt" if missing else ""))


def main():
    parser = argparse.ArgumentParser(prog="osu_coach")
    parser.add_argument("--osu-dir", help="osu! install folder (default: auto-detect)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("analyze", help="analyze a replay (default: most recent)")
    p.add_argument("replay", nargs="?")
    p.add_argument("--objects", action="store_true", help="print every object")
    p.add_argument("--habits", type=int, default=50, help="recent plays used to find bad habits (0 = off)")
    p.add_argument("--max-episodes", type=int, default=5, help="mistakes listed per category")
    p.set_defaults(func=cmd_analyze)

    p = sub.add_parser("profile", help="bad habits and setup advice from your recent plays")
    p.add_argument("--last", type=int, default=50, help="number of recent plays (default 50)")
    p.add_argument("--player", help="whose replays (default: the player with the most replays)")
    p.set_defaults(func=cmd_profile)

    p = sub.add_parser("skills", help="your comfortable level and limit in each skillset")
    p.add_argument("--last", type=int, default=100, help="number of recent plays (default 100)")
    p.add_argument("--ar-plays", type=int, default=100,
                   help="recent plays at AR 9 or below (reading) and above 10 (high AR), each")
    p.add_argument("--player", help="whose replays (default: the player with the most replays)")
    p.set_defaults(func=cmd_skills)

    p = sub.add_parser("model", help="one model for every note: where your misses come from")
    p.add_argument("--last", type=int, default=100, help="number of recent plays (default 100)")
    p.add_argument("--ar-plays", type=int, default=100, help="plays below AR 9 and above 10 added, each")
    p.add_argument("--player", help="whose replays (default: the player with the most replays)")
    p.add_argument("--validate", action="store_true", help="compare with the per-pattern baseline on held-out plays")
    p.add_argument("-v", "--verbose", action="store_true", help="print the coefficients")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("recommend", help="maps from your Songs folder of the map types you want to train")
    p.add_argument("--skill", action="append",
                   help="skills to train, comma separated: jump, stream, alt, finger (finger control/burst), tech, aim (aim control), reading, speed, precision "
                        "(default: a menu in the terminal, or your 3 costliest)")
    p.add_argument("--top", type=int, default=3, help="without --skill: how many of your costliest skills (default 3)")
    p.add_argument("--count", type=int, default=15, help="maps per skill (default 15)")
    p.add_argument("--mods", default="NM,DT", help="mod combinations to consider, e.g. NM,DT,HR,HDDT (default NM,DT)")
    p.add_argument("--stars", help="star rating range, e.g. 6-7.5 (default: around what you usually play)")
    p.add_argument("--ar", help="effective AR range (reading), e.g. 9.5-10.3 or -9 for low AR")
    p.add_argument("--cs", help="CS range (precision), e.g. 5-")
    p.add_argument("--od", help="effective OD range (accuracy), e.g. 9-")
    p.add_argument("--length", help="drain length range as played (DT shortens it), e.g. 1:30-3:00, 2:00- or -90")
    p.add_argument("--bpm", help="main BPM range as played (x1.5 with DT), e.g. 180-220")
    p.add_argument("--loved", action="store_true", help="also loved maps (they include exploit maps: set --stars)")
    p.add_argument("--any-status", action="store_true", help="any status: graveyard, pending, unsubmitted too")
    p.add_argument("--refit", action="store_true", help="refit your model from your latest plays first")
    p.add_argument("--online", action="store_true", help="also search osu! (API) for maps you don't have")
    p.add_argument("--online-pages", type=int, default=10, help="search result pages per query (50 sets each)")
    p.add_argument("--online-max", type=int, default=150, help="online maps analysed per skill")
    p.add_argument("--last", type=int, default=100, help="recent plays for the model and your usual level")
    p.add_argument("--ar-plays", type=int, default=100, help="plays below AR 9 and above 10 for the model")
    p.add_argument("--player", help="whose replays (default: the player with the most replays)")
    p.set_defaults(func=cmd_recommend)

    p = sub.add_parser("maptype", help="the type of maps in your Songs folder (jump, stream, tech...), by name")
    p.add_argument("query", nargs="+", help="words of the artist, title, difficulty or mapper")
    p.add_argument("--mods", default="NM", help="mod combinations, e.g. NM,DT (default NM)")
    p.add_argument("--limit", type=int, default=20, help="maps shown at most (default 20)")
    p.set_defaults(func=cmd_maptype)

    p = sub.add_parser("search", help="maps of the given types and ranges (Songs, optionally the osu! site)")
    p.add_argument("--type", action="append",
                   help="map types the map must have, comma separated: jump, stream, alt, finger (finger control/burst), tech, aim, reading, speed, precision")
    p.add_argument("--mods", default="NM", help="mod combinations, e.g. NM,DT (default NM)")
    p.add_argument("--stars", help="star rating range as played, e.g. 6-7.5")
    p.add_argument("--ar", help="effective AR range, e.g. 9.5-10.3")
    p.add_argument("--cs", help="CS range, e.g. 5-")
    p.add_argument("--od", help="effective OD range, e.g. 9-")
    p.add_argument("--length", help="drain length range as played, e.g. 1:30-3:00")
    p.add_argument("--bpm", help="main BPM range as played, e.g. 180-220")
    p.add_argument("--loved", action="store_true", help="also loved maps")
    p.add_argument("--any-status", action="store_true", help="any status: graveyard, pending, unsubmitted too")
    p.add_argument("--unplayed", action="store_true", help="only maps you never played")
    p.add_argument("--online", action="store_true",
                   help="only maps on the osu! site not in your Songs (guessed types, no download), searched until --limit")
    p.add_argument("--max-pages", type=int, default=100,
                   help="with --online: result pages read at most (50 sets each, one a second; default 100)")
    p.add_argument("--limit", type=int, default=30, help="maps shown at most (default 30)")
    p.set_defaults(func=cmd_search)

    p = sub.add_parser("retrain", help="relabel the maps in Songs and train the map type guesser again")
    p.add_argument("--no-relabel", action="store_true", help="skip labelling the maps in Songs (use the cached types)")
    p.add_argument("--evaluate", action="store_true", help="also measure the guesses' accuracy on held-out sets")
    p.add_argument("--fetch-ranked", action="store_true",
                   help="first read every ranked map's metadata from the osu! API (~760 requests, one a second)")
    p.add_argument("--unsure-min-stars", type=float, default=0.0,
                   help="with --download-unsure: only maps from this nomod star rating")
    p.add_argument("--download-unsure", type=int, default=0,
                   help="download this many maps with the least sure guess, label them and train again (osu! API)")
    p.set_defaults(func=cmd_retrain)

    p = sub.add_parser("guess", help="the guessed type of maps you don't have (from top plays), by name")
    p.add_argument("query", nargs="+", help="words of the artist, title or difficulty")
    p.add_argument("--limit", type=int, default=20, help="maps shown at most (default 20)")
    p.set_defaults(func=cmd_guess)

    p = sub.add_parser("profiles", help="sample players across ranks and save their top plays (osu! API)")
    p.add_argument("--per-band", type=int, default=40, help="players per rank band (5 bands, 1 to 100000)")
    p.add_argument("--seed", type=int, default=1)
    p.set_defaults(func=cmd_profiles)

    p = sub.add_parser("config", help="show or set your hardware setup (tablet/mouse, keyboard)")
    p.add_argument("--device", choices=("tablet", "mouse"))
    p.add_argument("--area", help="tablet area in mm, e.g. 80x48.5 (default: read from OpenTabletDriver)")
    p.add_argument("--sens", type=float, help="in-game mouse sensitivity")
    p.add_argument("--dpi", type=int)
    p.add_argument("--keyboard", choices=("rt", "mechanical"), help="rt = rapid trigger (Hall effect / analog)")
    p.add_argument("--rt-press", type=float, help="rapid trigger press sensitivity, mm")
    p.add_argument("--rt-release", type=float, help="rapid trigger release sensitivity, mm")
    p.add_argument("--actuation", type=float, help="keyboard actuation point, mm (mechanical or rapid trigger)")
    p.add_argument("--reset", action="store_true", help="forget saved settings")
    p.add_argument("--api-client-id", help="your osu! OAuth application's client ID")
    p.add_argument("--api-client-secret", action="store_true",
                   help="ask for your osu! OAuth application's client secret (typed, not shown)")
    p.add_argument("--api-test", action="store_true", help="check the osu! API credentials with one search")
    p.set_defaults(func=cmd_config)

    p = sub.add_parser("ui", help="the window: profile, replay analysis, beatmap search, settings")
    p.set_defaults(func=lambda args: __import__("osu_coach.gui", fromlist=["main"]).main())

    p = sub.add_parser("validate", help="check the simulator against recent replays")
    p.add_argument("--limit", type=int, default=200)
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("calibrate", help="run on a dataset of replays grouped by skill level")
    p.add_argument("dataset", help="folder with one subfolder of .osr per skill tier (and optionally maps/)")
    p.add_argument("--maps", action="append", help="extra folder with .osu files (repeatable)")
    p.add_argument("--workers", type=int, help="parallel processes (default: all cores)")
    p.add_argument("--out", help="output folder (default: <dataset>/calibration)")
    p.set_defaults(func=cmd_calibrate)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
