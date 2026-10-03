"""The desktop window: python -m osu_coach ui

A small local web server (standard library only) serves the page in osu_coach/ui/ and a JSON API over the
analysis modules; the page opens in a chromeless Edge/Chrome window (--app), or the default browser when neither
is found. Slow work (judging replays, searching the osu! site, downloading) runs as a background job the page
polls, so the window never blocks. The server stops a while after the window stops pinging it.
"""

import itertools
import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
import urllib.request
import uuid
import webbrowser
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import asdict, is_dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .binary import BinaryReader
from .locate import CACHE_DIR, BeatmapIndex, default_osu_dir, replays_dir, songs_dir

UI_DIR = Path(__file__).resolve().parent / "ui"
SETTINGS_PATH = CACHE_DIR / "ui_settings.json"
REPLAY_STATS_PATH = CACHE_DIR / "ui_replays.json"     # header counts of each replay (combo, 300s, misses...)
PROFILE_PATH = CACHE_DIR / "ui_profile.json"          # bad habits and trends of the last profile run
SKILLSETS_PATH = CACHE_DIR / "ui_skillsets.json"      # the Skills section's statistics (skillsets.py)
SETUP_STATS_PATH = CACHE_DIR / "ui_setup_stats.json"  # what the area / key advice measured in the plays
ELO_PATH = CACHE_DIR / "ui_elo.json"                  # the Improvement page: ratings per skillset, day by day
IMPORTED_DIR = CACHE_DIR / "imported"                 # replays added from the file manager
WEBVIEW_STORAGE = CACHE_DIR / "ui_webview"            # the app window's own storage (cache, the viewer's volumes)
IDLE_EXIT_S = 180            # no ping from the window for this long: the window was closed
TREND_MIN_OBJECTS = 150      # objects of a category each half of the recent plays needs for a trend

DEFAULT_SETTINGS = {
    "osu_dir": "",
    "habit_plays": 50,        # recent plays the bad habits come from
    "skill_plays": 100,       # recent plays the skill levels come from (+ as many below AR 9 and above AR 10)
    "search": {
        "source": ["online"],  # "online": maps on the osu! site you don't have; "songs": your Songs folder
        "mods": ["NM"],
        "status": ["ranked"],  # any of "ranked", "loved", "other" (graveyard, pending...: Songs only)
        "unplayed": False,
        "limit": 30,
        "max_pages": 100,
        "mirror": "https://catboy.best/d/{set_id}",
        "tag_min_pct": 20.0,  # a skillset is tagged from this share of the map's intense notes (the main one always)
        "reading_max_ar": 8.5,  # the reading tag only up to this effective AR (maptypes.READING_LOW_AR)
    },
    "viewer": {
        "skin": "",           # a folder of osu!'s Skins ("": the skin osu! itself uses)
        "offset": 0,          # ms the viewer's music plays earlier (-100..100)
        "cursor_size": 1.0,   # the viewer's cursor, x this (0.5..2)
    },
    "skills": {
        "stream_ur_tolerance_pct": 10.0,   # stream minimum BPM: UR within this of the UR at the comfort BPM
    },
    "advice": {
        "area_scale_pct": 3.0,      # overaim/underaim below this share of the jump isn't worth changing the area for
        "area_rotation_deg": 1.5,
        "area_offset_radii": 0.2,
        "rt_ghosts_per_1000": 1.0,  # ghost presses per 1000 before advising a higher rapid trigger distance
        "rt_stuck_pct": 10.0,       # share of the stream/alt misses with a key not reset before a lower release
    },
}

# bad habit -> what to do about it (matched on the title the analysis writes)
SOLUTIONS = (
    (r"^(Doubles|Triples) are", "Train short patterns (doubles and triples) on finger control/burst maps: count the "
                                "notes as you tap them and keep the rhythm with your fingers, not with the cursor."),
    (r"^(Bursts|Streams|Deathstreams) are|Streams fall apart",
     "Play streams slightly below your limit until they are clean, then go up 5-10 BPM. Keep tapping and cursor "
     "together: slow and in sync beats fast and out of sync."),
    (r"Cursor and taps drift", "Slow the cursor down and follow your tapping: every tap should land on a note. "
                               "Train spaced streams at a comfortable BPM, watching where the cursor goes."),
    (r"towards the end of streams|Long streams break down",
     "Stamina on long streams: maps with deathstreams 20-30 BPM below your maximum, focusing on keeping the rhythm "
     "until the very last note."),
    (r"fully alternate", "Always alternate on fast streams (even starting with your weaker finger): relax your hand "
                         "and share the load."),
    (r"Alt patterns|Direction changes break your alt|Wide alt",
     "Train alt maps (e.g. Prayer, Running in the 90s) just below your comfortable BPM: the cursor has to anticipate "
     "the change of direction while your fingers keep the rhythm."),
    (r"Jumps wider|Weaker on .* jumps", "Jump maps with growing spacing: first at a comfortable BPM, then faster. "
                                        "Move your arm, not just your wrist, on long jumps."),
    (r"jump misses land", "If you land short: finish the movement and stop on the circle; if you overshoot: slow the "
                          "cursor down before the circle. Train jumps at a comfortable speed aiming for the centre."),
    (r"Irregular rhythms|irregular rhythms", "Listen to the music: on 1/3 and 1/6 rhythms count the beats out loud. "
                                             "Train finger control/tech maps with varied rhythms."),
    (r"Slider breaks|leave sliders too early", "Follow the slider to the end and hold the key until the ball stops; "
                                               "train tech maps at a comfortable speed."),
    (r"misread note order|Busy screens", "Reading: play maps with lower AR and busy screens (HD too) at a comfortable "
                                         "difficulty, looking ahead at the next notes."),
    (r"You hit .* on average|Timing bias",
     "Adjust your offset as suggested (check it with the hit error bar), then tap to the sound, not to the "
     "approach circle."),
    (r"Accuracy drops",
     "Train accuracy: high-OD maps at a comfortable difficulty, aiming for 300s rather than stars, tapping to the "
     "sound."),
    (r"^Stamina", "Stamina: long, dense maps at a comfortable difficulty, without stopping. Relax your hand in the "
                  "breaks."),
    (r"High AR", "Reaction time: train with DT on maps easier than your usual ones, then raise the stars."),
    (r"overaim|underaim|rotated|clicks are shifted|area|sensitivity",
     "Change the settings as suggested and play 20-30 maps before judging: it will feel odd at first."),
    (r"Ghost key presses", "Raise your actuation point or rapid trigger press distance a little."),
    (r"don't reset", "Lower your actuation point or rapid trigger release distance a little."),
)

# analysis category -> the map type to search for training it
TRAIN_SKILL = {"streams": "stream", "alt": "alt", "jumps": "jump", "irregular": "finger control",
               "sliders": "tech", "reading": "reading", "stamina": "stream", "dt": "reading", "aim": "aim control"}
SKILL_NAMES = {"stream": "stream", "alt": "alt", "jump": "jump", "finger control": "finger control/burst",
                  "tech": "tech", "reading": "reading", "aim control": "aim control", "speed": "speed",
                  "precision": "precision"}


OFFSET_SOLUTION = ("Your hits are off time rather than spread out: adjust your offset as suggested (check it with the "
                   "hit error bar), then tap to the sound, not to the approach circle.")


def solution_for(title: str, evidence: dict | None = None) -> str:
    if (evidence or {}).get("cause") == "timing":
        return OFFSET_SOLUTION
    for pattern, text in SOLUTIONS:
        if re.search(pattern, title, re.IGNORECASE):
            return text
    return ""


# --- settings ---------------------------------------------------------------------------------------------

def _merge(default: dict, saved: dict) -> dict:
    out = {}
    for k, v in default.items():
        if isinstance(v, dict):
            out[k] = _merge(v, saved.get(k) if isinstance(saved.get(k), dict) else {})
        else:
            out[k] = saved.get(k, v)
    return out


def load_settings() -> dict:
    try:
        saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    return _merge(DEFAULT_SETTINGS, saved)


def save_settings(data: dict) -> dict:
    merged = _merge(DEFAULT_SETTINGS, data)
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    SETTINGS_PATH.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    return merged


def apply_advice_settings(settings: dict):
    """The cutoffs of the area and rapid trigger advice are the setup_advice constants."""
    from . import setup_advice as sa
    a = settings["advice"]
    sa.MIN_SCALE = float(a["area_scale_pct"]) / 100
    sa.MIN_ROTATION_DEG = float(a["area_rotation_deg"])
    sa.MIN_OFFSET_RADII = float(a["area_offset_radii"])
    sa.GHOSTS_PER_1000 = float(a["rt_ghosts_per_1000"])
    sa.STUCK_SHARE = float(a["rt_stuck_pct"]) / 100


def apply_type_settings(settings: dict):
    """The map type rules the Settings change: up to which AR a map can be reading."""
    from . import maptypes
    maptypes.READING_LOW_AR = float(settings["search"]["reading_max_ar"])


def osu_dir_of(settings: dict) -> Path:
    d = settings.get("osu_dir") or ""
    osu_dir = Path(d) if d else default_osu_dir()
    if not osu_dir or not osu_dir.exists():
        raise UserError("osu! folder not found: set it in Settings.")
    return osu_dir


# --- jobs -------------------------------------------------------------------------------------------------

class UserError(Exception):
    """A problem to show as it is, without a traceback."""


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind: str):
        self.id = uuid.uuid4().hex[:12]
        self.kind = kind
        self.status = "running"      # running, done, error, cancelled
        self.label = ""
        self.done = 0
        self.total = 0
        self.result = None
        self.error = ""
        self.partial = None          # results so far (search)
        self.cancel = threading.Event()
        self.started = time.time()

    def step(self, label: str, done: int = 0, total: int = 0):
        self.check()
        self.label, self.done, self.total = label, done, total

    def check(self):
        if self.cancel.is_set():
            raise Cancelled()

    def public(self) -> dict:
        return {"id": self.id, "kind": self.kind, "status": self.status, "label": self.label, "done": self.done,
                "total": self.total, "error": self.error, "result": self.result, "partial": self.partial,
                "elapsed": time.time() - self.started}


JOBS: dict[str, Job] = {}


def wait_for(job: Job, fut):
    """A future's result, waited for in short steps so that Stop ends the job at once (Cancelled)."""
    from concurrent.futures import TimeoutError as FutureTimeout
    while True:
        job.check()
        try:
            return fut.result(timeout=0.25)
        except FutureTimeout:
            pass


def start_job(kind: str, work) -> Job:
    job = Job(kind)
    JOBS[job.id] = job

    def run():
        try:
            job.result = work(job)
            job.status = "done"
        except Cancelled:
            job.status = "cancelled"
        except UserError as e:
            job.status, job.error = "error", str(e)
        except Exception as e:   # shown in the window instead of killing the server
            job.status, job.error = "error", f"{type(e).__name__}: {e}"
            traceback.print_exc()
    threading.Thread(target=run, daemon=True).start()
    return job


# --- shared state: maps and replays -----------------------------------------------------------------------

class State:
    """osu!.db and the replay index, loaded once per osu! folder (reloaded on demand)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.osu_dir: Path | None = None
        self.index: BeatmapIndex | None = None
        self.replays = None
        self.maps = None
        self.infos: dict = {}
        self.db_mtime: float | None = None
        self.habits_cache: dict = {}

    def load(self, settings: dict, force: bool = False):
        from .mapdb import read_osu_db
        from .replay_index import ReplayIndex
        with self.lock:
            osu_dir = osu_dir_of(settings)
            if force or self.osu_dir != osu_dir or self.replays is None:
                self.osu_dir = osu_dir
                self.index = BeatmapIndex(osu_dir)
                self.replays = ReplayIndex(osu_dir, self.index)
                self._read_db()
            elif self._db_mtime() != self.db_mtime:     # osu! saved it (it does when it closes): new maps
                self._read_db()
            return self

    def _db_mtime(self) -> float | None:
        try:
            return (self.osu_dir / "osu!.db").stat().st_mtime
        except OSError:
            return None

    def _read_db(self):
        from .mapdb import read_osu_db
        self.db_mtime = self._db_mtime()
        self.maps = read_osu_db(self.osu_dir / "osu!.db")
        self.infos = {m.md5: m for m in self.maps}

    def songs_maps(self) -> list:
        """The maps in Songs: osu!.db's, and those downloaded by osu!coach that osu! hasn't put in it yet."""
        extra = [m for m in downloaded_maps(self.osu_dir) if m.md5 not in self.infos]
        return self.maps + extra


STATE = State()


def read_header(path: Path) -> dict | None:
    """Counts, combo and score from a replay's header (the first bytes only)."""
    try:
        with open(path, "rb") as f:
            r = BinaryReader(f.read(1024))
        mode, _ = r.byte(), r.int()
        md5, player, _ = r.string(), r.string(), r.string()
        c300, c100, c50, geki, katu, miss = (r.short() for _ in range(6))
        score, combo, perfect, mods = r.int(), r.short(), r.bool(), r.int()
        return {"mode": mode, "md5": md5, "player": player, "c300": c300, "c100": c100, "c50": c50, "miss": miss,
                "score": score, "combo": combo, "perfect": perfect, "mods": mods}
    except Exception:
        return None


def accuracy(h: dict) -> float:
    total = h["c300"] + h["c100"] + h["c50"] + h["miss"]
    return (300 * h["c300"] + 100 * h["c100"] + 50 * h["c50"]) / (300 * total) if total else 0.0


REPLAY_STATS_LOCK = threading.Lock()


def read_replay_stats() -> dict:
    try:
        return json.loads(REPLAY_STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_combo_breaks(counts: dict[str, dict]):
    """Keep the slider breaks and dropped slider ends of some replays (id -> {"sb", "se"}) with their header counts."""
    with REPLAY_STATS_LOCK:
        cache = read_replay_stats()
        for key, n in counts.items():
            if key in cache:
                cache[key].update(n)
        REPLAY_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPLAY_STATS_PATH.write_text(json.dumps(cache), encoding="utf-8")


def combo_breaks(results) -> dict:
    """Why a play with no miss isn't a full combo. sb: combo breaks that aren't misses, slider heads missed on
    sliders that still scored and dropped ticks or repeats (the "Break" rows of the replay page's mistake list);
    se: dropped slider ends, which keep the combo but miss its +1, so the play isn't a full combo either."""
    from .beatmap import SLIDER
    sliders = [r for r in results if r.played and r.obj.kind == SLIDER]
    return {"sb": sum(1 for r in sliders if r.head_result == 0 and r.result != 0
                      or r.head_result != 0 and r.slider_break_kind in ("tick", "repeat")),
            "se": sum(1 for r in sliders if r.head_result != 0 and r.slider_break_kind == "end")}


def replay_rows(settings: dict) -> dict:
    from .mods import mods_string
    from .recommend import _star_key
    st = STATE.load(settings)
    with REPLAY_STATS_LOCK:
        cache = read_replay_stats()
    rows, changed = [], False
    entries = [("r", e.name, st.replays.path(e), e) for e in st.replays.entries.values() if e.mode == 0]
    if IMPORTED_DIR.exists():
        entries += [("i", p.name, p, None) for p in IMPORTED_DIR.glob("*.osr")]
    for source, name, path, entry in entries:
        key = f"{source}:{name}"
        try:
            s = path.stat()
        except OSError:
            continue
        h = cache.get(key)
        if not h or h.get("mtime") != s.st_mtime:
            h = read_header(path)
            if h is None or h["mode"] != 0:
                continue
            h["mtime"] = s.st_mtime
            cache[key] = h
            changed = True
        m = st.infos.get(h["md5"])
        # an imported replay's map may be in Songs but not yet in osu!.db (downloaded for it)
        local = st.index.find(h["md5"]) if m is None and (source == "i" or h["md5"] in FETCHED_MAPS) else None
        played = entry.time if entry else s.st_mtime
        rows.append({
            "id": key, "source": source, "time": played, "player": h["player"],
            "map": m.display_name if m else osu_display_name(local) if local else "(map not in Songs)",
            "found": m is not None or local is not None,
            "supported": not h["mods"] & (128 | 8192),     # Relax / Autopilot can't be analysed
            "stars": (m.stars.get(_star_key(h["mods"])) or m.stars.get(0)) if m else None,
            "mods": mods_string(h["mods"]), "combo": h["combo"], "max_combo": None, "acc": accuracy(h),
            "miss": h["miss"], "c100": h["c100"], "c50": h["c50"], "perfect": h["perfect"], "sb": h.get("sb"), "se": h.get("se"),
            "beatmap_id": m.beatmap_id if m and m.beatmap_id > 0 else None,
        })
    if changed:
        with REPLAY_STATS_LOCK:
            fresh = read_replay_stats()   # keep combo breaks counted meanwhile
            for key, h in cache.items():
                if "sb" not in h and "se" in fresh.get(key, {}) and fresh[key].get("mtime") == h.get("mtime"):
                    h["sb"], h["se"] = fresh[key]["sb"], fresh[key]["se"]
            REPLAY_STATS_PATH.parent.mkdir(parents=True, exist_ok=True)
            REPLAY_STATS_PATH.write_text(json.dumps(cache), encoding="utf-8")
    rows.sort(key=lambda r: -r["time"])
    return {"owner": st.replays.owner, "replays": rows}


def osu_display_name(path: Path) -> str:
    """"Artist - Title [Version]" from a .osu file's [Metadata]."""
    meta = {}
    try:
        for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() in ("Artist", "Title", "Version"):
                meta[key.strip()] = value.strip()
            if line.strip() == "[Difficulty]":
                break
    except OSError:
        pass
    return f"{meta.get('Artist', '?')} - {meta.get('Title', path.stem)} [{meta.get('Version', '?')}]"


FETCHED_MAPS: set[str] = set()   # MD5s of the maps downloaded for replays (not in osu!.db until osu! imports them)


def fetch_replay_map(job: Job, settings: dict, replay_id: str) -> dict:
    """The map of a replay that isn't in Songs: looked up on the osu! site by the replay's beatmap MD5, its set
    downloaded from the mirror and unpacked into Songs (osu! adds it at the next F5 in song select, or next start)."""
    import zipfile
    from . import api
    st = STATE.load(settings)
    h = read_header(replay_path(replay_id))
    if h is None:
        raise UserError("Not a valid replay.")
    if st.index.find(h["md5"]):
        return {"ok": True}
    job.step("Looking the map up on the osu! site")
    try:
        bm = api.OsuApi().get("/beatmaps/lookup", {"checksum": h["md5"]}, ttl=0)
    except api.ApiError as e:
        if "HTTP 404" in str(e):
            raise UserError("This replay's map isn't on the osu! site, or it was changed after the play: "
                            "it can't be downloaded.")
        raise UserError(f"osu! API: {e}. Set it up in Settings to download maps.")
    bs = bm.get("beatmapset") or {}
    set_id = int(bm["beatmapset_id"])
    res = download_sets(job, settings, [{"set_id": set_id, "artist": bs.get("artist", ""), "title": bs.get("title", "")}])
    if res["failed"]:
        raise UserError(f"The map couldn't be downloaded: {res['failed'][0]['error']}")
    job.step("Unpacking the map into Songs")
    for osz in songs_dir(st.osu_dir).glob(f"{set_id} *.osz"):     # sets downloaded before they were unpacked
        unpack_osz(osz)
    st.index._by_md5 = None     # look at Songs again
    FETCHED_MAPS.add(h["md5"])
    if not st.index.find(h["md5"]):
        raise UserError("The map on the osu! site is a newer version than the replay's: the replay can't be analysed.")
    return {"ok": True}


def replay_path(replay_id: str) -> Path:
    source, _, name = replay_id.partition(":")
    if "/" in name or "\\" in name or not name.endswith(".osr"):
        raise UserError("invalid replay")
    folder = IMPORTED_DIR if source == "i" else replays_dir(STATE.osu_dir)
    path = folder / name
    if not path.exists():
        raise UserError(f"replay not found: {name}")
    return path


def count_combo_breaks(job: Job, settings: dict, ids: list[str]) -> dict:
    """Slider breaks and ends of replays with no miss that aren't a full combo, for the replay list: judged one by one,
    each count shown (job.partial) and saved as it comes."""
    from .beatmap import parse_beatmap
    from .judge import judge
    from .replay import parse_replay
    st = STATE.load(settings)
    job.partial = {}
    for n, replay_id in enumerate(ids):
        job.step("Counting slider breaks", n, len(ids))
        try:
            replay = parse_replay(replay_path(replay_id))
            map_path = st.index.find(replay.beatmap_md5)
            if map_path is None or replay.mode != 0 or replay.mods & (128 | 8192):
                continue
            results, _ = judge(replay, parse_beatmap(map_path))
        except Exception:   # a replay or map that can't be read: no count
            continue
        job.partial[replay_id] = combo_breaks(results)
        save_combo_breaks({replay_id: job.partial[replay_id]})
    return job.partial


# --- judging many plays, with progress ----------------------------------------------------------------------

def gather(job: Job, label: str, last: int, player: str | None = None, select=None):
    """Samples of the `last` most recent plays (as __main__._collect_samples), judged in parallel."""
    from .collect import PARALLEL_MIN, _job
    st = STATE
    jobs, times = [], []
    for entry in st.replays.recent(player, select):
        if len(jobs) >= last:
            break
        map_path = st.index.find(entry.md5)
        if map_path is not None:
            jobs.append((str(st.replays.path(entry)), str(map_path)))
            times.append(entry.time)
    samples, taps, used = [], [], 0
    job.step(label, 0, len(jobs))
    if not jobs:
        return samples, taps, used
    pool = ProcessPoolExecutor() if len(jobs) >= PARALLEL_MIN else None
    try:
        results = pool.map(_job, jobs, chunksize=2) if pool else map(_job, jobs)
        for n, result in enumerate(results, 1):
            job.step(label, n, len(jobs))
            if result is None:
                continue
            play_samples, play_taps = result
            for s in play_samples:
                s.play = used
                s.played_at = times[n - 1]     # when it was played: the area advice skips plays before a change
            samples += play_samples
            taps.append(play_taps)
            used += 1
    finally:
        if pool:
            pool.shutdown(wait=False, cancel_futures=True)
    return samples, taps, used


def habits_for(job: Job, settings: dict, player: str | None):
    """Samples of the recent plays for the bad habits, kept while no new replay appears."""
    n = int(settings["habit_plays"])
    recent = STATE.replays.recent(player)
    key = (player or "", n, recent[0].time if recent else 0)
    if key not in STATE.habits_cache:
        STATE.habits_cache = {key: gather(job, "Bad habits from your recent plays", n, player)}
    return STATE.habits_cache[key]


def insight_json(i) -> dict:
    return {"title": i.title, "detail": i.detail, "impact": float(i.impact), "kind": i.kind,
            "category": i.category, "solution": "" if i.kind == "info" else solution_for(i.title, i.evidence),
            "evidence": clean(i.evidence)}


def split_setup(insights: list) -> dict:
    """Setup advice split into the tablet area / sensitivity and the keyboard (rapid trigger)."""
    area, keys = [], []
    for i in insights:
        (keys if "ghosts" in i.evidence or "stuck" in i.evidence else area).append(insight_json(i))
    return {"area": area, "keys": keys}


def trends(samples: list) -> list[dict]:
    """Miss rate per category in the newer and older half of the recent plays (play 0 is the newest)."""
    from .advice import category_of
    from .coach import CATEGORY_NAMES
    plays = max((s.play for s in samples), default=-1) + 1
    if plays < 10:
        return []
    half = plays // 2
    groups: dict[str, list[list[int]]] = {}
    for s in samples:
        c = category_of(s.f.pattern)
        g = groups.setdefault(c, [[0, 0], [0, 0]])
        side = 0 if s.play < half else 1
        g[side][0] += s.missed
        g[side][1] += 1
    out = []
    for c, ((m_new, n_new), (m_old, n_old)) in groups.items():
        if n_new < TREND_MIN_OBJECTS or n_old < TREND_MIN_OBJECTS:
            continue
        new, old = m_new / n_new, m_old / n_old
        pooled = (m_new + m_old) / (n_new + n_old)
        se = math.sqrt(max(pooled * (1 - pooled), 1e-9) * (1 / n_new + 1 / n_old))
        z = (new - old) / se if se else 0.0
        out.append({"category": c, "name": CATEGORY_NAMES.get(c, c.capitalize()), "new": new, "old": old,
                    "z": z, "trend": "worse" if z >= 2 else "better" if z <= -2 else "flat",
                    "plays": [half, plays - half]})
    return sorted(out, key=lambda t: -t["z"])


def skill_trends(samples: list) -> list[dict]:
    """Miss rate per skillset (skillsets.TREND_SKILLS, in that order) in the newer and older half of the recent
    plays; a skillset without enough notes in each half has no trend."""
    from . import skillsets
    plays = max((s.play for s in samples), default=-1) + 1
    if plays < 10:
        return []
    half = plays // 2
    counts = {k: [[0, 0], [0, 0]] for k, _ in skillsets.TREND_SKILLS}
    for s in samples:
        side = 0 if s.play < half else 1
        for k in skillsets.trend_skills(s):
            counts[k][side][0] += skillsets.trend_missed(s, k)
            counts[k][side][1] += 1
    out = []
    for k, name in skillsets.TREND_SKILLS:
        (m_new, n_new), (m_old, n_old) = counts[k]
        if n_new < TREND_MIN_OBJECTS or n_old < TREND_MIN_OBJECTS:
            out.append({"category": k, "name": name, "new": None, "old": None, "z": 0.0, "trend": "none",
                        "plays": [half, plays - half]})
            continue
        new, old = m_new / n_new, m_old / n_old
        pooled = (m_new + m_old) / (n_new + n_old)
        se = math.sqrt(max(pooled * (1 - pooled), 1e-9) * (1 / n_new + 1 / n_old))
        z = (new - old) / se if se else 0.0
        out.append({"category": k, "name": name, "new": new, "old": old, "z": z,
                    "trend": "worse" if z >= 2 else "better" if z <= -2 else "flat", "plays": [half, plays - half]})
    return out


# --- analysis of one replay ---------------------------------------------------------------------------------

def combo_timeline(results, diff) -> list[list[int]]:
    """The combo as the play goes, as stable counts it: [time, combo] at each change. +1 for a circle, a slider's
    head, ticks, repeats and end and a spinner; a miss, a missed slider head or a dropped tick or repeat breaks it;
    a dropped slider end only misses its +1."""
    from .beatmap import CIRCLE, SLIDER
    events = []   # (time, +1 or 0 for a break)
    for r in results:
        o = r.obj
        if r.result is None:
            continue
        if o.kind == CIRCLE:
            events.append((r.hit_time if r.result else o.time + diff.hit50, 1 if r.result else 0))
        elif o.kind == SLIDER:
            if r.head_result is not None:
                events.append((r.hit_time if r.head_result else o.time + diff.hit50, 1 if r.head_result else 0))
            for t, kind, hit in r.points:
                if hit or kind != "end":
                    events.append((t, 1 if hit else 0))
        else:
            events.append((o.end_time, 1 if r.result else 0))
    events.sort(key=lambda e: e[0])
    out, combo = [], 0
    for t, add in events:
        combo = combo + 1 if add else 0
        out.append([t, combo])
    return out


def pp_timeline(results, map_path, mods: int, diff, combo: list[list[int]]) -> list[list[float]] | None:
    """The pp so far as the play goes (rosu-pp, stable formula): [time, pp] after each object, up to the first
    object not played (a fail). None when the map can't be calculated."""
    from bisect import bisect_right

    from .beatmap import CIRCLE
    try:
        import rosu_pp_py as rosu
        bm = rosu.Beatmap(path=str(map_path))
        if bm.is_suspicious():
            return None
        gradual = rosu.GradualPerformance(rosu.Difficulty(mods=mods, lazer=False), bm)
    except Exception:
        traceback.print_exc()
        return None
    combo_times = [c[0] for c in combo]
    best = list(itertools.accumulate((c[1] for c in combo), max))
    counts = {300: 0, 100: 0, 50: 0, 0: 0}
    out, last = [], -math.inf
    for r in sorted(results, key=lambda r: r.obj.index):
        if r.result is None:
            break
        o = r.obj
        counts[r.result] += 1
        t = (r.hit_time if r.result else o.time + diff.hit50) if o.kind == CIRCLE else o.end_time
        last = max(last, t)
        i = bisect_right(combo_times, last) - 1
        state = rosu.ScoreState(max_combo=best[i] if i >= 0 else 0, n300=counts[300], n100=counts[100],
                                n50=counts[50], misses=counts[0])
        attrs = gradual.next(state)
        if attrs is None:
            break
        out.append([last, round(attrs.pp, 2)])
    return out


def viewer_data(beatmap, results, replay, diff, rate, samples) -> dict:
    """What the page needs to draw the play and list its mistakes: objects (as clicked: stacked, HR-flipped), their
    results and patterns, and the input."""
    from .beatmap import SLIDER, SPINNER
    by_index = {s.r.obj.index: s for s in samples}
    objects = []
    for r in results:
        o = r.obj
        x, y = o.position
        item = {"i": o.index, "k": {SLIDER: "s", SPINNER: "p"}.get(o.kind, "c"), "t": o.time, "e": o.end_time,
                "x": round(x, 1), "y": round(y, 1), "nc": o.new_combo, "res": r.result, "hr": r.head_result,
                "ht": r.hit_time, "sb": r.slider_break_time, "sk": r.slider_break_kind, "why": r.miss_reason}
        if r.cursor is not None:   # where the click was: the aim error meter
            item["cx"], item["cy"] = round(r.cursor[0], 1), round(r.cursor[1], 1)
        elif r.miss_reason == "aim" and r.off_target_clicks:   # an aim miss: the click nearest the note's time
            _, mx, my = min(r.off_target_clicks, key=lambda c: abs(c[0] - o.time))
            item["cx"], item["cy"] = round(mx, 1), round(my, 1)
        s = by_index.get(o.index)
        if s is not None:
            item["pat"] = s.f.pattern
            item["dist"] = round(s.f.distance_radii, 2)
            if s.wrong_note is not None:
                item["why"] = "wrong_note"
        if o.kind == SLIDER:
            n = max(2, int(math.ceil(o.path.length / 6)))
            item["path"] = [[round(c, 1) for c in o._transform(o.path.position_at(k / n))] for k in range(n + 1)]
            item["rep"] = o.repeats
        objects.append(item)
    f = replay.frames
    return {"objects": objects, "radius": diff.radius, "preempt": diff.preempt,
            "fadein": 400 * min(1, diff.preempt / 450), "rate": rate, "hidden": bool(replay.mods & 8),
            "windows": [diff.hit300, diff.hit100, diff.hit50], "combo": combo_timeline(results, diff),
            "frames": {"t": [p.time for p in f], "x": [round(p.x, 1) for p in f], "y": [round(p.y, 1) for p in f],
                       "k": [p.keys for p in f]}}


def map_audio(map_path) -> Path | None:
    """The song of a map: the AudioFilename of its .osu, inside the map's folder."""
    try:
        for line in Path(map_path).read_text(encoding="utf-8-sig", errors="replace").splitlines():
            key, sep, value = line.partition(":")
            if sep and key.strip() == "AudioFilename":
                folder = Path(map_path).parent.resolve()
                path = (folder / value.strip()).resolve()
                return path if folder in path.parents and path.is_file() else None
            if line.strip() == "[Difficulty]":
                break
    except OSError:
        pass
    return None


def play_sounds(beatmap, results, map_path, miss_window: float) -> list:
    """The play's hitsounds for the viewer (skin.py); none when the map's samples can't be read."""
    from . import skin
    try:
        return skin.play_sounds(beatmap, results, skin.map_sounds(Path(map_path)), miss_window)
    except Exception:
        traceback.print_exc()
        return []


def analyze(job: Job, settings: dict, replay_id: str) -> dict:
    from .advice import HIGH_AR, ExpectedModel, build_insights, effective_ar, samples_from_play
    from .analysis import summarize
    from .beatmap import parse_beatmap
    from .coach import prioritize
    from .explain import explain_play, timestamp
    from .features import RUN_PATTERNS, extract
    from .judge import judge
    from .keys import tap_stats
    from .mods import Mods, clock_rate, mods_string
    from .recommend import _star_key
    from .replay import parse_replay
    from .setup import load_setup
    from .setup_advice import setup_insights

    apply_advice_settings(settings)
    job.step("Loading maps and replays")
    st = STATE.load(settings)
    path = replay_path(replay_id)
    job.step("Reading the replay")
    replay = parse_replay(path)
    if replay.mode != 0:
        raise UserError("Not an osu!standard replay.")
    if replay.mods & (128 | 8192):
        raise UserError("Relax/Autopilot replays are not supported.")
    map_path = st.index.find(replay.beatmap_md5)
    if map_path is None:
        raise UserError("This replay's map is not in your Songs folder.")
    beatmap = parse_beatmap(map_path)
    job.step("Simulating the play")
    results, diff = judge(replay, beatmap)
    save_combo_breaks({replay_id: combo_breaks(results)})   # for the replay list
    rate = clock_rate(replay.mods)
    s = summarize(results, diff.radius, rate)
    feats = extract(results, diff, rate, beatmap)
    play_samples = samples_from_play(feats, diff, rate, hidden=bool(replay.mods & Mods.Hidden))
    play_taps = tap_stats(replay.frames, results, {f.r.obj.index for f in feats if f.pattern in RUN_PATTERNS})
    setup = load_setup()

    habit_samples, habit_taps, used = habits_for(job, settings, replay.player)
    job.step("Looking for mistakes")
    since, keys_since = setup_since()
    from .setup_advice import measure as setup_measure, play_aim
    habit_measured = setup_measure(habit_samples, habit_taps) if habit_samples else None
    setup_found, area_msg = (area_gate(advise_setup(habit_measured, setup, since, keys_since), since, habit_measured)
                             if habit_measured else ([], ""))
    setup_found, keys_msg = keys_gate(setup_found, keys_since, habit_measured) if habit_measured else (setup_found, "")
    insights = build_insights(habit_samples) + setup_found if habit_samples else []
    model = ExpectedModel(habit_samples) if habit_samples else None
    normal_ar = [x for x in habit_samples if x.ar <= HIGH_AR]
    normal_ar_model = ExpectedModel(normal_ar) if len(normal_ar) >= 1000 else None
    episodes = explain_play(play_samples, replay.frames, diff.radius, model, normal_ar_model, play_taps)
    priorities = prioritize(episodes, insights, play_samples, habit_samples)

    info = st.infos.get(replay.beatmap_md5)
    c = s.counts
    trend = trends(habit_samples)
    worse = {t["category"]: t for t in trend}
    training = []
    for p in priorities:
        skill = TRAIN_SKILL.get(p.category)
        if not skill or p.map_score < 10 and (p.habits_score or 0) < 15:
            continue
        t = worse.get(p.category)
        why = f"{p.map_score:.0f}% of this play's mistakes"
        if p.habits_score:
            why += f", {p.habits_score:.0f}% of your bad habits"
        if t and t["trend"] == "worse":
            why += f"; getting worse ({t['old']:.1%} → {t['new']:.1%} misses in recent plays)"
        training.append({"category": p.category, "name": p.name, "skill": skill,
                         "skill_name": SKILL_NAMES.get(skill, skill), "why": why,
                         "dt": p.category == "dt"})
    for t in trend:
        skill = TRAIN_SKILL.get(t["category"])
        if t["trend"] == "worse" and skill and all(x["category"] != t["category"] for x in training):
            training.append({"category": t["category"], "name": t["name"], "skill": skill,
                             "skill_name": SKILL_NAMES.get(skill, skill),
                             "why": f"getting worse: {t['old']:.1%} → {t['new']:.1%} misses in recent plays",
                             "dt": t["category"] == "dt"})

    return clean({
        "map": {"name": beatmap.display_name, "mods": mods_string(replay.mods), "player": replay.player,
                "stars": (info.stars.get(_star_key(replay.mods)) or info.stars.get(0)) if info else None,
                "beatmap_id": info.beatmap_id if info and info.beatmap_id > 0 else None,
                "set_id": info.set_id if info and info.set_id > 0 else None,
                "cs": diff.cs, "ar": effective_ar(diff, rate), "od": diff.od, "rate": rate},
        "stats": {"c300": replay.count_300, "c100": replay.count_100, "c50": replay.count_50,
                  "miss": replay.count_miss, "sim": [c[300], c[100], c[50], c[0]],
                  "combo": replay.max_combo, "acc": accuracy({"c300": replay.count_300, "c100": replay.count_100,
                                                              "c50": replay.count_50, "miss": replay.count_miss}),
                  "ur": s.unstable_rate, "mean_error": s.mean_error, "early": s.mean_early, "late": s.mean_late,
                  "aim_distance": s.aim_mean_distance, "slider_breaks": s.slider_breaks,
                  **combo_breaks(results)},
        "habit_plays": used,
        "offset": offset_advice(s, results, rate, info),
        "priorities": [{
            "category": p.category, "name": p.name, "map_score": p.map_score, "habits_score": p.habits_score,
            "content": p.content, "play_rate": p.play_rate, "usual_rate": p.usual_rate, "rate_label": p.rate_label,
            "insights": [insight_json(i) for i in p.insights],
            "episodes": [{"time": e.time, "stamp": timestamp(e.time), "title": e.title, "reasons": e.reasons,
                          "cost": e.cost} for e in p.episodes],
        } for p in priorities],
        "setup": {**split_setup(setup_found),
                  "area_note": area_msg,
                  "keys_note": keys_msg,
                  "play_aim": play_aim(play_samples)},
        "setup_desc": setup.describe(),
        "training": training,
        "trends": skill_trends(habit_samples),
        "viewer": {**viewer_data(beatmap, results, replay, diff, rate, play_samples),
                   "pp": pp_timeline(results, map_path, replay.mods, diff, combo_timeline(results, diff)),
                   "sounds": play_sounds(beatmap, results, map_path, diff.hit50),
                   "music": {"url": f"/audio/{replay.beatmap_md5}", "nightcore": bool(replay.mods & Mods.Nightcore)}
                   if map_audio(map_path) else None},
    })


# --- the player profile ---------------------------------------------------------------------------------------

def skill_json(skill) -> dict:
    from .skills import describe
    d = clean(asdict(skill))
    d.pop("model", None)
    d["text"] = describe(skill)
    return d


def load_profile() -> dict:
    from .skills import SKILLS_PATH
    out = {"skills": None, "habits": None, "skillsets": None}
    try:
        data = json.loads(SKILLS_PATH.read_text(encoding="utf-8"))
        out["skills"] = {"plays": data.get("plays"), "updated": SKILLS_PATH.stat().st_mtime,
                         "skills": [{k: v for k, v in s.items() if k != "model"} for s in data.get("skills", [])]}
    except (OSError, ValueError):
        pass
    try:
        out["skillsets"] = json.loads(SKILLSETS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    try:
        out["habits"] = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        for i in out["habits"].get("habits", []):   # from the current texts, not the ones saved with the profile
            i["solution"] = "" if i.get("kind") == "info" else solution_for(i["title"], i.get("evidence"))
        refresh_setup_advice(out["habits"])
    except (OSError, ValueError):
        pass
    return out


AREA_PATH = CACHE_DIR / "ui_area.json"     # the area and keyboard settings last seen, and since when


def setup_since() -> tuple[float | None, float | None]:
    """When the tablet area (size, rotation) or mouse sensitivity, and when the keyboard settings (type, rapid
    trigger, actuation) last changed, from Settings or from OpenTabletDriver: plays made before a change say nothing
    about the current settings. None for a part that never changed."""
    from .setup import load_setup
    s = load_setup()
    sigs = {"area": [s.device, s.area_w, s.area_h, s.area_rotation, s.sens, s.dpi],
            "keys": [s.keyboard, s.rt_press, s.rt_release, s.actuation]}
    try:
        seen = json.loads(AREA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        seen = {}
    if "sig" in seen:            # the first version kept the area only
        seen = {"area": seen}
    changed = False
    for part, sig in sigs.items():
        old = seen.get(part)
        if old is None or old.get("sig") != sig:
            since = None if old is None else time.time()     # the first time: nothing to compare with
            if since and part == "area" and s.source.startswith("OpenTabletDriver"):
                from .setup import OTD_SETTINGS
                try:    # changed in OpenTabletDriver: when its settings were saved, so the plays since count
                    since = min(since, OTD_SETTINGS.stat().st_mtime)
                except OSError:
                    pass
            seen[part] = {"sig": sig, "since": since}
            changed = True
    if changed:
        AREA_PATH.parent.mkdir(parents=True, exist_ok=True)
        AREA_PATH.write_text(json.dumps(seen), encoding="utf-8")
    return seen["area"]["since"], seen["keys"]["since"]


def area_since() -> float | None:
    return setup_since()[0]


def advise_setup(measured: dict, setup, since: float | None, keys_since: float | None = None) -> list:
    from .setup_advice import advise
    return advise(measured, setup, since, keys_since)


AREA_MIN_PLAYS = 5     # plays with a new area before its advice comes back
KEYS_MIN_PLAYS = 5     # plays with new keyboard settings before the key advice comes back


def _key_insight(i) -> bool:
    return "ghosts" in i.evidence or "stuck" in i.evidence


def plays_since(measured: dict, since: float) -> int:
    """Plays among the measured ones made from `since` on."""
    from .setup_advice import click_rows
    if measured.get("taps_by_play"):
        return sum(1 for p in measured["taps_by_play"] if p.get("time", 0) >= since)
    times = click_rows(measured["clicks"])[:, 11]
    return len({t for t in times if t >= since})


def keys_gate(found: list, since: float | None, measured: dict) -> tuple[list, str]:
    """After the keyboard settings changed: no key advice until KEYS_MIN_PLAYS plays were made with them (the advice
    reads only those), and a note saying so."""
    if not since:
        return found, ""
    n = sum(1 for p in measured.get("taps_by_play", []) if p.get("time", 0) >= since)
    if n >= KEYS_MIN_PLAYS:
        return found, ""
    changed = time.strftime("%d %b %Y", time.localtime(since))
    return ([i for i in found if not _key_insight(i)],
            f"Not enough plays with the new keyboard settings ({n} of {KEYS_MIN_PLAYS} since they changed on "
            f"{changed}): the key advice comes back after {KEYS_MIN_PLAYS - n} more" + ("; recompute the profile then." if n else "."))


def area_gate(found: list, since: float | None, measured: dict) -> tuple[list, str]:
    """After an area change: no area advice until AREA_MIN_PLAYS plays were made with the new area (the advice
    reads only those), and a note saying so."""
    if not since:
        return found, ""
    n = plays_since(measured, since)
    if n >= AREA_MIN_PLAYS:
        if any(not _key_insight(i) for i in found):
            return found, ""
        return found, ("Not enough jumps yet in the plays with the new area: the area advice comes back after "
                       "more of them.")
    changed = time.strftime("%d %b %Y", time.localtime(since))
    return ([i for i in found if _key_insight(i)],
            f"Not enough plays with the new area ({n} of {AREA_MIN_PLAYS} since it changed on {changed}): "
            f"the area advice comes back after {AREA_MIN_PLAYS - n} more"
            + ("; recompute the profile then." if n else "."))


def refresh_setup_advice(habits: dict):
    """The area and key advice again from what the plays measured, with the current cutoffs and setup: changing
    either in Settings shows at once, without judging the plays again."""
    from .setup import load_setup
    from .setup_advice import advise
    try:
        measured = json.loads(SETUP_STATS_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    apply_advice_settings(load_settings())
    setup = load_setup()
    since, keys_since = setup_since()
    found, note = area_gate(advise(measured, setup, since, keys_since), since, measured)
    found, k_note = keys_gate(found, keys_since, measured)
    habits["setup"] = {**split_setup(found), "area_note": note, "keys_note": k_note}
    habits["setup_desc"] = setup.describe()
    kept = [i for i in habits.get("habits", []) if i.get("category") != "setup"]
    habits["habits"] = sorted(kept + [insight_json(i) for i in found if i.kind != "info"], key=lambda i: -i["impact"])


OFFSET_MIN_HITS = 50       # hits a play needs for its mean hit error to say something about the offset
OFFSET_OK_MS = 3           # a mean hit error within this is in time: no offset to change


def usual_timing(samples) -> dict | None:
    """How early or late the player usually is: the median of the mean hit error (real ms) of each recent play."""
    import numpy as np

    from .analysis import timed_hit
    by_play: dict[int, list[float]] = {}
    for s in samples:
        r = s.r
        if timed_hit(r):
            by_play.setdefault(s.play, []).append(r.hit_error / s.rate)
    means = [float(np.mean(e)) for e in by_play.values() if len(e) >= OFFSET_MIN_HITS]
    if len(means) < 5:
        return None
    return {"plays": len(means), "median": float(np.median(means)),
            "same_side": float(max(np.mean(np.array(means) > 0), np.mean(np.array(means) < 0)))}


def offset_advice(s, results, rate: float, info) -> dict:
    """The local offset this play suggests for its map: osu!'s local offset moves the hit objects later when it goes
    up, so hitting late on average asks for it to go up by that much. When the player is off by about as much on
    every map, it's the universal offset that is wrong."""
    from .analysis import timed_hit
    hits = sum(1 for r in results if r.played and timed_hit(r))
    usual = load_profile().get("habits") or {}
    usual = usual.get("timing")
    return {"hits": hits, "mean": s.mean_error, "rate": rate, "min_hits": OFFSET_MIN_HITS, "ok_ms": OFFSET_OK_MS,
            "current": info.local_offset if info else None, "usual": usual}


def build_player_profile(job: Job, settings: dict) -> dict:
    from .advice import build_insights, is_high_ar, is_low_ar
    from .setup import load_setup
    from .setup_advice import advise, measure
    from . import skillsets
    from .skills import build_profile, save_profile
    apply_advice_settings(settings)
    job.step("Loading maps and replays")
    STATE.load(settings)
    n_skill, n_habit = int(settings["skill_plays"]), int(settings["habit_plays"])
    samples, taps, used = gather(job, "Recent plays", max(n_skill, n_habit))
    low, _, low_used = gather(job, "Plays below AR 9 (reading)", n_skill, select=lambda e: is_low_ar(e.ar))
    high, _, high_used = gather(job, "Plays above AR 10", n_skill, select=lambda e: is_high_ar(e.ar))
    for offset, extra in ((100_000, low), (200_000, high)):
        for s in extra:
            s.play += offset
    job.step("Computing your level per skillset")
    skill_samples = [s for s in samples if s.play < n_skill]
    skills = build_profile(skill_samples, low, high)
    save_profile(skills, min(used, n_skill), None)
    tolerance = float(settings["skills"]["stream_ur_tolerance_pct"]) / 100
    SKILLSETS_PATH.write_text(json.dumps(clean({**skillsets.build(skill_samples, tolerance), "updated": time.time()})),
                              encoding="utf-8")
    job.step("Looking for bad habits")
    habit_samples = [s for s in samples if s.play < n_habit]
    setup = load_setup()
    measured = measure(habit_samples, taps[:n_habit])
    SETUP_STATS_PATH.write_text(json.dumps(clean({"clicks": measured["clicks"].round(2).tolist(), "taps": measured["taps"],
                                                  "taps_by_play": measured["taps_by_play"]})), encoding="utf-8")
    area_changed, keys_changed = setup_since()
    setup_found, _ = area_gate(advise(measured, setup, area_changed, keys_changed), area_changed, measured)
    setup_found, _ = keys_gate(setup_found, keys_changed, measured)
    insights = build_insights(habit_samples) + setup_found
    habits = [i for i in insights if i.kind != "info"]
    habits.sort(key=lambda i: -i.impact)
    notes = [i for i in insights if i.kind == "info"]
    data = clean({
        "updated": time.time(), "plays": min(used, n_habit), "objects": len(habit_samples),
        "misses": sum(s.missed for s in habit_samples), "setup_desc": setup.describe(),
        "habits": [insight_json(i) for i in habits], "notes": [insight_json(i) for i in notes],
        "setup": split_setup(setup_found),
        "trends": skill_trends(samples), "extra_plays": [low_used, high_used],
        "timing": usual_timing(habit_samples),
    })
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILE_PATH.write_text(json.dumps(data), encoding="utf-8")
    return load_profile()


# --- beatmap search -----------------------------------------------------------------------------------------

def _range(r) -> tuple[float | None, float | None]:
    if not r:
        return None, None
    lo, hi = r
    return (None if lo is None else float(lo)), (None if hi is None else float(hi))


def _base_ar(ar: float, rate: float) -> float:
    """The map's AR that plays as `ar` at this clock rate."""
    from .skills import ar_from_preempt
    preempt = 1200 - 150 * (ar - 5) if ar >= 5 else 1200 + 120 * (5 - ar)
    return ar_from_preempt(preempt * rate)


def site_query(stars, ranges: dict, rate: float) -> str:
    """The site's search filters for the ranges as played at this rate (DT: 1.5), a little wider than the ranges:
    the site filters beatmapsets (a set passes if one difficulty does), each difficulty is checked afterwards.
    Only the minimum length: the site's is the total length, the ranges' the drain one."""
    from . import online
    factor = online.DT_SR_FACTOR if rate > 1 else 1.0
    words = []
    for key, lo, hi, conv, digits, pad in (
            ("stars", *stars, lambda v: v / factor, 2, 0.0),
            ("bpm", *ranges.get("bpm", (None, None)), lambda v: v / rate, 0, 1.0),
            ("ar", *ranges.get("ar", (None, None)), lambda v: _base_ar(v, rate), 1, 0.1),
            ("od", *ranges.get("od", (None, None)), lambda v: (80 - (80 - 6 * v) * rate) / 6, 1, 0.1),
            ("cs", *ranges.get("cs", (None, None)), lambda v: v, 1, 0.1),
            ("length", ranges.get("length", (None, None))[0], None, lambda v: v * rate, 0, 1.0)):
        if lo is not None:
            words.append(f"{key}>={conv(lo) - pad:.{digits}f}")
        if hi is not None:
            words.append(f"{key}<={conv(hi) + pad:.{digits}f}")
    return " ".join(words)


def name_relevance(m, words: list[str]) -> float:
    """How well a map's name matches the searched words: the whole text as the title first, then in the title,
    then each word in the title, the artist, the difficulty and the mapper."""
    if not words:
        return 0.0
    phrase = " ".join(words)
    fields = ((m.title.lower(), 10.0), (m.artist.lower(), 6.0), (m.version.lower(), 4.0), (m.creator.lower(), 3.0))
    score = 100.0 if fields[0][0] == phrase else 50.0 if phrase in fields[0][0] else 0.0
    score += 25.0 if any(phrase in f for f, _ in fields[1:]) else 0.0
    for w in words:
        for text, weight in fields:
            if re.search(rf"(?<!\w){re.escape(w)}(?!\w)", text):
                score += weight          # a whole word
            elif len(w) >= 3 and w in text:
                score += weight / 2      # part of a word ("to" inside "Haruto" isn't a match)
    return score


TECH_TAG_MIN = 0.16      # the tech tag from this tech value (maptypes' tech formula), whatever the tag setting


def tech_tag_share(a: dict) -> float:
    """Tech's value as a tag: maptypes' tech formula (fast sliders, slider variety, share of sliders)."""
    return a["tech sliders"]
AIM_CONTROL_TAG_MIN_STARS = 4.0   # maps easier than this (nomod) aren't tagged aim control
ALT_TAG_MAX_BPM = 180   # maps whose alt patterns are faster than this (1/4 BPM as played) aren't tagged alt


def map_skills(a: dict, min_share: float) -> set[str]:
    """The skillsets a map is tagged with: its main type, every kind with at least `min_share` of the intense notes
    (tech: of the notes), and the aim control / reading / speed / precision tags. No alt when the map's alt patterns
    go over ALT_TAG_MAX_BPM: that fast they are spaced bursts and streams, not alt."""
    from . import maptypes
    from . import recommend as rc
    share = {**a, "tech": tech_tag_share(a)}
    names = {part.strip().lower() for part in maptypes.category(a).split("+")}
    names |= {maptypes.NAMES[k].lower() for k in maptypes.KINDS
              if share[k] >= (TECH_TAG_MIN if k == "tech" else min_share)}
    tags = {sk for sk in rc.SKILLS if (rc.SKILLS[sk][0] in names if rc.SKILLS[sk][0] else rc.has_skill(a, sk))}
    if (a.get("alt bpm") or 0) > ALT_TAG_MAX_BPM:
        tags.discard("alt")
    if maptypes.map_stars(a) < AIM_CONTROL_TAG_MIN_STARS:
        tags.discard("aim control")
    return tags


MAX_CARD_TAGS = 3


def card_tags(a: dict, tagged: set[str], keep=()) -> list[str]:
    """The tags a map card shows: precision first when the map has it, then its skillsets by their share of the
    intense notes (tech: its own value), then the aim control / reading / speed tags; at most MAX_CARD_TAGS, precision
    and the searched-for ones always among them."""
    from . import recommend as rc
    if "precision" in tagged:
        keep = ("precision", *keep)
    order = list(rc.SKILLS)
    share = lambda sk: tech_tag_share(a) if sk == "tech" else a[rc.SKILL_SHARE[sk]]
    ranked = sorted(tagged, key=lambda sk: (-1, 0) if sk == "precision" else
                    (0, -share(sk)) if sk in rc.SKILL_SHARE else (1, order.index(sk)))
    shown = ranked[:MAX_CARD_TAGS]
    for sk in keep:
        if sk in tagged and sk not in shown:
            drop = next((x for x in reversed(shown) if x not in keep), None)
            if drop is None:
                break
            shown.remove(drop)
            shown.append(sk)
    return sorted(shown, key=ranked.index)


def search_maps(job: Job, settings: dict, q: dict) -> dict:
    """Maps having every included type and none of the excluded ones, in the ranges (cmd_search, with
    exclusions): from Songs or from the osu! site (downloaded and read), with their real type."""
    from . import maptypes
    from . import recommend as rc
    from .mods import Mods, mods_string
    include = [s for s in q.get("include", []) if s in rc.SKILLS]
    exclude = [s for s in q.get("exclude", []) if s in rc.SKILLS and s not in include]
    # words of the artist, title, difficulty or mapper (no "<", ">", "=": the site would read them as filters)
    words = re.sub(r"[<>=]", " ", str(q.get("text") or "")).lower().split()
    # per skillset: included ones with at least this share of the intense notes, excluded ones with at most this
    # (none given: "any", the tag decides)
    share_key = {"stream": "stream", "jump": "jump", "alt": "alt", "finger control": "finger", "tech": "tech sliders"}
    at_least = {sk: float(v) / 100 for sk, v in (q.get("min_share") or {}).items() if sk in include and sk in share_key and v}
    at_most = {sk: float(v) / 100 for sk, v in (q.get("max_share") or {}).items()
               if sk in exclude and sk in share_key and v is not None and float(v) < 100}

    def wanted(a: dict, tagged: set) -> bool:
        for sk in include:
            if sk not in tagged or (sk in at_least and a[share_key[sk]] < at_least[sk]):
                return False
        for sk in exclude:
            if (a[share_key[sk]] > at_most[sk]) if sk in at_most else sk in tagged:
                return False
        return True
    try:
        mod_sets = rc.parse_mods(",".join(q.get("mods") or ["NM"]))
    except ValueError as e:
        raise UserError(str(e))
    ranges = {k: _range(q.get(k)) for k in ("ar", "cs", "od", "length", "bpm")}
    ranges = {k: v for k, v in ranges.items() if v != (None, None)}
    stars = _range(q.get("stars"))
    limit = max(1, int(q.get("limit") or 30))
    max_pages = max(1, int(q.get("max_pages") or settings["search"]["max_pages"]))
    # where (the site, the Songs folder or both) and which statuses (ranked, loved, other: Songs only)
    sources = q.get("source") or ["online"]
    sources = [sources] if isinstance(sources, str) else sources
    chosen = q.get("status") or ["ranked"]
    if isinstance(chosen, str):   # older settings: "ranked", "loved" (ranked and loved) or "any"
        chosen = {"loved": ["ranked", "loved"], "any": ["ranked", "loved", "other"]}.get(chosen, ["ranked"])
    statuses = tuple(code for name, codes in (("ranked", rc.ACCEPTED_STATUS), ("loved", (rc.LOVED_STATUS,)),
                                              ("other", (0, 1, 2, 3, 6))) if name in chosen for code in codes)
    site_statuses = [x for x in ("ranked", "loved") if x in chosen]

    def star_ok(s):
        return s is not None and (stars[0] is None or s >= stars[0] - 1e-6) and (stars[1] is None or s <= stars[1] + 1e-6)

    job.step("Loading maps")
    st = STATE.load(settings)
    found = []
    tag_min = float(settings["search"]["tag_min_pct"]) / 100
    maptypes.TOP_KIND_MIN, maptypes.TOP_KINDS_SHOWN = tag_min, len(maptypes.KINDS)   # the label lists what is tagged

    row = map_row

    note = ""
    if "songs" in sources:
        adopt_osz(job, st)
        items = [(m, mods) for m in st.songs_maps() if m.mode == 0 and m.status in statuses and m.drain_s >= rc.MIN_DRAIN_S
                 and (not q.get("unplayed") or m.unplayed)
                 and all(w in f"{m.artist} {m.title} {m.version} {m.creator}".lower() for w in words) for mods in mod_sets
                 if star_ok(m.stars.get(rc._star_key(mods))) and rc.in_ranges(m, mods, ranges)]

        def progress(label, n, total):
            job.step(f"Reading the map types in Songs ({len(items)} match the filters)", n, total)
        kinds = maptypes.types(songs_dir(st.osu_dir), items, progress)
        for m, mods in items:
            a = kinds.get(f"{m.md5}:{mods}")
            tagged = map_skills(a, tag_min) if a else set()
            if a and wanted(a, tagged):
                share = rc.skill_share(a, include[0]) if include else 0.0
                tags = card_tags(a, tagged, include)
                found.append(dict(row(share, m, mods, maptypes.label(a), tags, True),
                                  relevance=name_relevance(m, words)))
    if "online" in sources and site_statuses:
        songs_found, found = found, []   # each source finds up to the limit; merged and sorted at the end
        from . import api, online, typeguess
        try:
            client = api.OsuApi()
        except api.ApiError as e:
            raise UserError(f"osu! API: {e}. Set it up in Settings.")
        guessed = rc.load_predicted()
        local = {m.md5 for m in st.songs_maps()}
        searches = []
        for mods in mod_sets:
            if mods & ~int(Mods.DoubleTime):
                continue    # the site's filters and the guesses are for nomod and DT only
            name, rate = ("DT", 1.5) if mods & Mods.DoubleTime else ("NM", 1.0)
            query = " ".join(words + [site_query(stars, ranges, rate)]).strip()   # the site matches tags too
            for site_status in site_statuses:     # ranked and loved are separate searches on the site
                searches.append({"mods": mods, "name": name, "query": query, "status": site_status, "cursor": None,
                                 "done": False, "next": None})
        if not searches and "songs" not in sources:
            raise UserError("Only NM and DT can be searched on the site.")
        seen, examined, pages, downloaded = set(), 0, 0, 0
        site_rank: dict[tuple, int] = {}
        unlikely = []    # maps whose guess says they lack an included skillset as their type
        songs = songs_dir(st.osu_dir)
        job.partial = []
        files = api.OsuFiles(client)
        pager = ThreadPoolExecutor(1)    # the next result page is asked for while this one is being read

        def progress():
            job.step(f"Pages read {pages}/{max_pages}: {examined} maps match the filters, {downloaded} downloaded and "
                     f"read, {len(found)} found", len(found), limit)

        def classify(cands):
            """The candidates' .osu downloaded (cached) and read for real, ten at a time until enough are found,
            the next ten downloading while these are read; every real type is kept for the guesser."""
            nonlocal downloaded
            batches = [cands[i:i + 10] for i in range(0, len(cands), 10)]
            pending = [files.submit(info.beatmap_id, info.md5) for info, _, _ in batches[0]] if batches else []
            for n, batch in enumerate(batches):
                if len(found) >= limit:
                    break
                current = pending
                pending = ([files.submit(info.beatmap_id, info.md5) for info, _, _ in batches[n + 1]]
                           if n + 1 < len(batches) else [])
                got = []
                for (info, mods, bs), fut in zip(batch, current):
                    try:
                        info.path = str(wait_for(job, fut))
                    except Cancelled:
                        raise
                    except Exception:
                        continue
                    downloaded += 1
                    got.append((info, mods, bs))
                progress()
                kinds = maptypes.types(songs, [(m, mods) for m, mods, _ in got])
                typeguess.remember([(m, mods, kinds.get(f"{m.md5}:{mods}")) for m, mods, _ in got])
                for m, mods, bs in got:
                    a = kinds.get(f"{m.md5}:{mods}")
                    tagged = map_skills(a, tag_min) if a else set()
                    if not a or not wanted(a, tagged):
                        continue
                    share = rc.skill_share(a, include[0]) if include else 0.0
                    tags = card_tags(a, tagged, include)
                    r = row(share, m, mods, maptypes.label(a), tags, False)
                    r["rank"] = site_rank[(m.beatmap_id, mods)]
                    r["relevance"] = name_relevance(m, words)
                    r["preview"] = bs.get("covers", {}).get("list")
                    found.append(r)
                    job.partial = (songs_found + found)[:limit]
            for fut in pending:
                fut.cancel()

        try:
            while len(found) < limit and pages < max_pages and not all(x["done"] for x in searches):
                for x in searches:
                    if x["done"] or len(found) >= limit or pages >= max_pages:
                        continue
                    progress()
                    try:
                        page = wait_for(job, x["next"] or pager.submit(client.search, x["query"], x["status"], x["cursor"]))
                    except api.ApiError as e:
                        raise UserError(f"osu! API: {e}")
                    pages += 1
                    x["cursor"] = page.get("cursor_string")
                    x["done"] = not x["cursor"]
                    x["next"] = (pager.submit(client.search, x["query"], x["status"], x["cursor"])
                                 if x["cursor"] and pages < max_pages else None)
                    mods, name = x["mods"], x["name"]
                    sure, maybe = [], []
                    for bs in page.get("beatmapsets", []):
                        for bm in bs.get("beatmaps", []):
                            if bm.get("mode_int", 0) != 0 or bm.get("checksum") in local or (bm["id"], mods) in seen:
                                continue
                            seen.add((bm["id"], mods))
                            site_rank[(bm["id"], mods)] = len(site_rank)   # the site's order: by relevance with a name
                            info = online._info(bm, bs)
                            if not star_ok(info.stars.get(rc._star_key(mods))) or not rc.in_ranges(info, mods, ranges) \
                                    or info.drain_s < rc.MIN_DRAIN_S:
                                continue
                            examined += 1
                            if maptypes.too_easy(info.stars.get(0)):
                                continue    # no type under 3 stars: nothing to find
                            # the guess only sorts: sure excluded skipped, sure yes read first, the rest after; a sure
                            # no only means "not its main type", and a smaller share can still get the tag: read last
                            p = guessed.get(f"{bm['id']}:{name}") or typeguess.guess(bm, bs, name)
                            said = [rc.predicted_skill(p, sk) for sk in include]
                            if any(rc.predicted_skill(p, sk) is True for sk in exclude):
                                continue
                            (unlikely if False in said else sure if said and all(said) else maybe).append((info, mods, bs))
                    classify(sure + maybe)
            classify(unlikely)    # only while more maps are needed (classify stops at the limit)
        finally:
            files.close()
            pager.shutdown(wait=False, cancel_futures=True)
        note = "" if len(found) >= limit or not searches else (
            "No more results on the site." if all(x["done"] for x in searches)
            else f"Stopped at {max_pages} pages: raise the search depth in Settings to look further.")
        found = songs_found + found
    if words:   # a name: the best matches first, from both sources alike (ties: the site's order), then the skillset
        found.sort(key=lambda f: (-f.get("relevance", 0.0), f.get("rank", 0), -f["share"]))
    else:
        found.sort(key=lambda f: -f["share"])
    return clean({"maps": found[:limit], "total": len(found), "note": note})


def player_model(job: Job, settings: dict):
    """The unified model of the player's misses (model.py): the saved one, else fitted on the recent plays plus the
    plays below AR 9 and above AR 10, as `osu_coach model` does."""
    from . import model as um
    from .advice import is_high_ar, is_low_ar
    fitted = um.load()
    if fitted is not None and fitted.usual_rate and fitted.stream_runs:
        return fitted
    n = int(settings["skill_plays"])
    samples, _, _ = gather(job, "Recent plays, for your model", n)
    extra = []
    for offset, label, select in ((100_000, "Plays below AR 9", lambda e: is_low_ar(e.ar)),
                                  (200_000, "Plays above AR 10", lambda e: is_high_ar(e.ar))):
        more, _, _ = gather(job, label, n, select=select)
        for s in more:
            s.play += offset
        extra += more
    job.step("Fitting your model")
    fitted = um.fit(samples + extra, chain_samples=samples)
    if fitted is None:
        raise UserError("Not enough plays to fit your model yet.")
    parts = um.breakdown(fitted, samples)
    um.set_usual(fitted, parts)
    um.set_stream_runs(fitted, samples)
    um.save(fitted)
    return fitted


def recommend_maps(job: Job, settings: dict, q: dict) -> dict:
    """Maps made to train the included skillsets (none: the three that cost the player most), a step above their
    level: `osu_coach recommend`, from the Songs folder and/or the osu! site, as a ladder per skillset."""
    from . import maptypes
    from . import recommend as rc
    from .mods import Mods, mods_string
    job.step("Loading maps and replays")
    st = STATE.load(settings)
    try:
        mod_sets = rc.parse_mods(",".join(q.get("mods") or ["NM"]))
    except ValueError as e:
        raise UserError(str(e))
    sources = q.get("source") or ["online"]
    sources = [sources] if isinstance(sources, str) else sources
    chosen = q.get("status") or ["ranked"]
    chosen = [chosen] if isinstance(chosen, str) else chosen
    statuses = tuple(code for name, codes in (("ranked", rc.ACCEPTED_STATUS), ("loved", (rc.LOVED_STATUS,)),
                                              ("other", (0, 1, 2, 3, 6))) if name in chosen for code in codes)
    total = max(1, int(q.get("limit") or 30))   # maps in all, shared out among the skillsets

    model = player_model(job, settings)
    share = lambda sk: model.usual_shares.get(rc.SKILLS[sk][1], 0.0)
    targets = [s for s in q.get("include", []) if s in rc.SKILLS] or sorted(rc.SKILLS, key=lambda sk: -share(sk))[:3]
    infos = {m.md5: m for m in st.songs_maps()}
    owner_plays = st.replays.recent()
    band = rc.star_band(infos, [(e.md5, e.mods) for e in owner_plays[:int(settings["skill_plays"])]])
    if band is None:
        raise UserError("Not enough recent plays on maps with known star ratings to know your level.")

    def progress(label, n, total):
        job.step(label[:1].upper() + label[1:], n, total)

    items = []
    if "songs" in sources:
        items = rc.candidates(st.songs_maps(), {e.md5 for e in owner_plays}, band, mod_sets, statuses)
    if "online" in sources:
        from . import api, online, typeguess
        try:
            client = api.OsuApi()
        except api.ApiError as e:
            raise UserError(f"osu! API: {e}. Set it up in Settings.")
        keys = list(dict.fromkeys(rc.ONLINE_KEY.get(sk, "other") for sk in targets))
        guessed = rc.load_predicted()

        def verdict(found, key):
            """The guessed type's answer for the skills searched under this key (see cmd_recommend)."""
            name = "DT" if found.mods & Mods.DoubleTime else "NM"
            p = guessed.get(f"{found.info.beatmap_id}:{name}")
            if p is None and found.bm is not None:
                p = typeguess.guess(found.bm, found.bs, name)
            said = [rc.predicted_skill(p, sk) for sk in targets if rc.ONLINE_KEY.get(sk, "other") == key]
            return True if True in said else False if said and all(s is False for s in said) else None
        use_guess = bool(guessed) or typeguess.available()
        try:
            items += online.discover(client, keys, st.maps, band, rc.tap_window(model), mod_sets,
                                     {e.md5 for e in owner_plays} | set(infos), pages=10, per_skill=150,
                                     progress=progress, verdict=verdict if use_guess else None)
        except api.ApiError as e:
            raise UserError(f"osu! API: {e}")

    songs = songs_dir(st.osu_dir)
    content = rc.contents(songs, items, progress)
    kinds = maptypes.types(songs, items, progress)
    short = {sk: rc.skill_candidates(sk, items, kinds, content, model) for sk in targets}
    to_profile = list({(m.md5, mods): (m, mods) for sk in targets for m, mods in short[sk]}.values())
    job.step("Predicting how each map would go for you", 0, len(to_profile))
    profs = rc.profiles(model, songs, to_profile, progress)
    tag_min = float(settings["search"]["tag_min_pct"]) / 100
    maptypes.TOP_KIND_MIN, maptypes.TOP_KINDS_SHOWN = tag_min, len(maptypes.KINDS)
    found = []
    sizes = {sk: total // len(targets) + (i < total % len(targets)) for i, sk in enumerate(targets)}
    for sk in targets:
        if not sizes[sk]:
            continue
        for n, k in enumerate(rc.ladder(sk, short[sk], kinds, content, profs, model, size=sizes[sk])):
            a = kinds.get(f"{k.info.md5}:{k.mods}")
            local = k.info.md5 in infos
            r = map_row(k.score, k.info, k.mods, maptypes.label(a) if a else "", card_tags(a, map_skills(a, tag_min), [sk]) if a else [],
                        local)
            # why this map: the second part of the recommender's own line
            r["why"] = rc.describe_pick(k, sk).split("\n")[0].split("  ")[-1]
            r["group"] = f"{SKILL_NAMES.get(sk, sk)}: {share(sk):.0%} of your misses usually"
            r["step"] = n + 1
            found.append(r)
    note = "" if found else "No map found that trains these skillsets at your level: add a source or a status."
    return clean({"maps": found, "total": len(found), "note": note, "recommended": True,
                  "band": [round(band[0], 2), round(band[1], 2)]})


def map_row(share, m, mods, label, tags, local) -> dict:
    """A map as the search page shows it."""
    from . import recommend as rc
    from .mods import mods_string
    v = rc.effective_values(m, mods)
    return {"share": share, "name": m.display_name, "artist": m.artist, "title": m.title, "version": m.version,
            "creator": m.creator, "mods": mods_string(mods) if mods else "NM",
            "stars": m.stars.get(rc._star_key(mods)) or 0.0, "bpm": v["bpm"], "ar": v["ar"], "cs": v["cs"],
            "od": v["od"], "length": v["length"], "label": label, "tags": tags, "local": local,
            "beatmap_id": m.beatmap_id if m.beatmap_id > 0 else None, "set_id": m.set_id if m.set_id > 0 else None}


def elo_plays(job: Job, settings: dict) -> list[dict]:
    """The challenges of the owner's plays of the last 90 days and the month before (elo.py), judging only the plays
    not seen before; the rest comes from elo_plays.json."""
    from . import elo
    from .collect import PARALLEL_MIN
    from .recommend import _star_key
    st = STATE.load(settings)
    since = time.time() - (elo.DAYS + elo.WARMUP_DAYS) * 86400
    entries = [e for e in st.replays.recent() if e.time >= since]
    known = elo.load_plays()
    todo = []
    for e in entries:
        if e.name not in known:
            map_path = st.index.find(e.md5)
            if map_path is not None:
                todo.append((e, (str(st.replays.path(e)), str(map_path))))
    job.step("Reading your plays for the ratings", 0, len(todo))
    if todo:
        pool = ProcessPoolExecutor() if len(todo) >= PARALLEL_MIN else None
        try:
            results = pool.map(elo.play_job, [a for _, a in todo], chunksize=2) if pool else map(elo.play_job, [a for _, a in todo])
            for n, ((e, _), res) in enumerate(zip(todo, results), 1):
                job.step("Reading your plays for the ratings", n, len(todo))
                known[e.name] = {"time": e.time, "skills": res or {}}
                if n % 50 == 0:
                    elo.save_plays(known)
        finally:
            if pool:
                pool.shutdown(wait=False, cancel_futures=True)
        elo.save_plays(known)
    # the map's star rating with the play's mods (osu!.db): the ratings weigh harder maps' plays more (elo.star_weight)
    out = []
    for e in entries:
        if e.name in known:
            m = st.infos.get(e.md5)
            stars = (m.stars.get(_star_key(e.mods)) or m.stars.get(0)) if m else None
            out.append({**known[e.name], "stars": stars})
    return out


def elo_history(job: Job, settings: dict) -> dict:
    from . import elo
    plays = elo_plays(job, settings)
    job.step("Computing the ratings")
    data = clean({**elo.history(plays), "updated": time.time(), "plays": len(plays)})
    ELO_PATH.write_text(json.dumps(data), encoding="utf-8")
    return data


def load_elo() -> dict | None:
    try:
        return json.loads(ELO_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


# --- downloads --------------------------------------------------------------------------------------------------

DOWNLOADED_PATH = CACHE_DIR / "ui_downloaded_maps.json"   # maps downloaded by osu!coach: their osu!.db-like info


def unpack_osz(osz: Path) -> Path:
    """An .osz unpacked into a folder of the same name next to it (as osu! does), the .osz removed."""
    import zipfile
    folder = osz.with_suffix("")
    with zipfile.ZipFile(osz) as z:
        for member in z.infolist():
            if folder.resolve() in (folder / member.filename).resolve().parents:   # nothing outside the folder
                z.extract(member, folder)
    osz.unlink()
    return folder


def remember_downloaded(set_id: int, folder: Path, songs: Path):
    """Each difficulty of a downloaded set with what osu!.db would say about it (stars, AR, BPM... from the osu!
    API), so searching Songs finds it before osu! imports it. Without API credentials, nothing is kept."""
    import hashlib
    from dataclasses import asdict
    from . import api, online
    try:
        bs = api.OsuApi().get(f"/beatmapsets/{set_id}")
    except api.ApiError:
        return
    files = {hashlib.md5(p.read_bytes()).hexdigest(): p for p in folder.rglob("*.osu")}
    try:
        kept = json.loads(DOWNLOADED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        kept = {}
    for bm in bs.get("beatmaps", []):
        p = files.get(bm.get("checksum") or "")
        if p is None or bm.get("mode_int", 0) != 0:
            continue
        info = online._info(bm, bs)
        info.path = p.relative_to(songs).as_posix()
        kept[info.md5] = asdict(info)
    DOWNLOADED_PATH.write_text(json.dumps(kept), encoding="utf-8")


def adopt_osz(job: Job, st):
    """Maps in Songs that osu!.db doesn't list yet (osu! writes it when it closes): the .osz files waiting for osu! to
    import them, unpacked; and the set folders osu! imported (F5) or that were added by hand, remembered with their
    info from the osu! API, so searching Songs finds them. Each set once."""
    songs = songs_dir(st.osu_dir)
    waiting = sorted(p for p in songs.glob("*.osz") if p.is_file())    # some map folders are named *.osz too
    for n, osz in enumerate(waiting):
        job.step("Unpacking the downloaded maps waiting in Songs", n, len(waiting))
        try:
            unpack_osz(osz)
        except Exception:      # a broken or half-written .osz: left for osu!
            continue
    known = {m.set_id for m in st.maps} | {m.set_id for m in downloaded_maps(st.osu_dir)} | tried_sets()
    new = []
    for folder in songs.iterdir():
        set_id = re.match(r"(\d+) ", folder.name)
        if folder.is_dir() and set_id and int(set_id.group(1)) not in known and any(folder.glob("*.osu")):
            new.append((int(set_id.group(1)), folder))
    for n, (set_id, folder) in enumerate(new):
        job.step("Adding the new maps in Songs", n, len(new))
        remember_downloaded(set_id, folder, songs)
    if new:     # sets the API doesn't have (or not now) aren't asked for again at every search
        TRIED_PATH.write_text(json.dumps(sorted(tried_sets() | {set_id for set_id, _ in new})), encoding="utf-8")
    if new:
        st.index._by_md5 = None     # look at Songs again: the new maps can be analysed


TRIED_PATH = CACHE_DIR / "ui_songs_sets_tried.json"     # set folders of Songs already looked up on the osu! API


def tried_sets() -> set[int]:
    try:
        return set(json.loads(TRIED_PATH.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def downloaded_maps(osu_dir: Path) -> list:
    """The maps osu!coach downloaded that are still in Songs."""
    from .mapdb import MapInfo
    try:
        kept = json.loads(DOWNLOADED_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    songs = songs_dir(osu_dir)
    out = []
    for d in kept.values():
        d = dict(d, stars={int(k): v for k, v in d.get("stars", {}).items()})
        if (songs / d["path"]).exists():
            out.append(MapInfo(**d))
    return out


def download_sets(job: Job, settings: dict, sets: list[dict]) -> dict:
    """Each beatmapset into Songs, unpacked as osu! does (osu! adds it at the next start or F5 in song select), and
    remembered so searching Songs finds it at once."""
    st = STATE.load(settings)
    target = songs_dir(st.osu_dir)
    mirror = settings["search"]["mirror"] or DEFAULT_SETTINGS["search"]["mirror"]
    done, failed = [], []
    for n, s in enumerate(sets):
        set_id = int(s["set_id"])
        name = re.sub(r'[<>:"/\\|?*]', "", f"{set_id} {s.get('artist', '')} - {s.get('title', '')}").strip()
        job.step(f"Downloading {name}", n, len(sets))
        path = target / f"{name}.osz"
        if path.exists() or any(target.glob(f"{set_id} *")):
            done.append(set_id)
            continue
        try:
            req = urllib.request.Request(mirror.format(set_id=set_id), headers={"User-Agent": "osu-coach"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                if "html" in (resp.headers.get("Content-Type") or "") or "json" in (resp.headers.get("Content-Type") or ""):
                    raise OSError("the mirror doesn't have this map")
                tmp = path.with_suffix(".part")
                with open(tmp, "wb") as f:
                    while chunk := resp.read(1 << 16):
                        job.check()
                        f.write(chunk)
            tmp.replace(path)
            folder = unpack_osz(path)
            remember_downloaded(set_id, folder, target)
            done.append(set_id)
        except Cancelled:
            path.with_suffix(".part").unlink(missing_ok=True)
            raise
        except Exception as e:
            path.with_suffix(".part").unlink(missing_ok=True)
            failed.append({"set_id": set_id, "error": str(e)})
    job.step("Done", len(sets), len(sets))
    st.index._by_md5 = None     # look at Songs again: the new maps can be analysed
    return {"done": done, "failed": failed, "folder": str(target)}


# --- API credentials ------------------------------------------------------------------------------------------

def api_status() -> dict:
    from . import api
    creds = api.load_credentials()
    return {"client_id": creds.get("client_id") or "", "has_secret": bool(creds.get("client_secret"))}


def api_save(body: dict) -> dict:
    from . import api
    creds = api.load_credentials()
    if "client_id" in body:
        creds["client_id"] = str(body["client_id"]).strip()
    if body.get("client_secret"):
        creds["client_secret"] = str(body["client_secret"]).strip()
    api.save_credentials({"client_id": creds.get("client_id"), "client_secret": creds.get("client_secret")})
    return api_status()


def api_test() -> dict:
    from . import api
    try:
        client = api.OsuApi()
        page = client.get("/beatmapsets/search", {"q": "stars>=6 stars<=7", "m": 0, "s": "ranked"}, ttl=0)
        sets = page.get("beatmapsets", [])
        return {"ok": True, "message": f"Working: {len(sets)} beatmapsets on the first search page."}
    except api.ApiError as e:
        return {"ok": False, "message": str(e)}


def setup_get() -> dict:
    from .setup import load_setup
    s = load_setup()
    return {**asdict(s), "describe": s.describe()}


def setup_save(body: dict) -> dict:
    from .setup import CONFIG_PATH, Setup, save_setup
    try:
        saved = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        saved = {}
    for key in ("device", "keyboard"):
        if key in body:
            saved[key] = body[key] or None
    for key in ("area_w", "area_h", "sens", "rt_press", "rt_release", "actuation", "dpi"):
        if key in body:
            v = body[key]
            saved[key] = (int(v) if key == "dpi" else float(v)) if v not in (None, "") else None
    save_setup(Setup(**{k: v for k, v in saved.items() if k in Setup.__dataclass_fields__ and v is not None}))
    return setup_get()


# --- JSON and HTTP ----------------------------------------------------------------------------------------------

def clean(x):
    """Plain JSON values: numpy numbers, tuples, dataclasses, non-finite floats (as null)."""
    if isinstance(x, dict):
        return {str(k): clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple, set)):
        return [clean(v) for v in x]
    if is_dataclass(x) and not isinstance(x, type):
        return clean(asdict(x))
    if hasattr(x, "item") and callable(x.item):
        x = x.item()
    if isinstance(x, float) and not math.isfinite(x):
        return None
    if isinstance(x, Path):
        return str(x)
    return x


LAST_PING = [time.time()]
STATIC_TYPES = {".html": "text/html; charset=utf-8", ".css": "text/css; charset=utf-8",
                ".js": "text/javascript; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png",
                ".jpg": "image/jpeg", ".wav": "audio/wav", ".ogg": "audio/ogg", ".mp3": "audio/mpeg"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _send(self, code: int, body: bytes, ctype: str):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path, ctype: str):
        """A file, in parts when the browser asks (the audio element seeks with Range requests)."""
        size = path.stat().st_size
        m = re.fullmatch(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if not m or not (m.group(1) or m.group(2)):
            self.send_response(200)
            start, end = 0, size - 1
        else:
            if m.group(1):
                start, end = int(m.group(1)), int(m.group(2)) if m.group(2) else size - 1
            else:
                start, end = max(0, size - int(m.group(2))), size - 1
            end = min(end, size - 1)
            if start > end:
                self.send_response(416)
                self.send_header("Content-Range", f"bytes */{size}")
                self.end_headers()
                return
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            left = end - start + 1
            while left > 0:
                chunk = f.read(min(left, 1 << 16))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(clean(data)).encode("utf-8"), "application/json")

    def _body(self) -> bytes:
        return self.rfile.read(int(self.headers.get("Content-Length") or 0))

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path.startswith("/api/"):
                return self._json(self.api_get(url.path[5:], urllib.parse.parse_qs(url.query)))
            if url.path.startswith("/skin/"):   # the player's osu! skin: images and hitsounds for the viewer
                from . import skin
                settings = load_settings()
                path = skin.skin_file(osu_dir_of(settings), settings["viewer"]["skin"],
                                      urllib.parse.unquote(url.path[len("/skin/"):]))
                if path is None:
                    return self._send(404, b"not found", "text/plain")
                return self._send(200, path.read_bytes(), STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream"))
            if url.path.startswith("/audio/"):   # the song of a map, for the viewer
                md5 = url.path[len("/audio/"):]
                map_path = STATE.index.find(md5) if STATE.index and re.fullmatch(r"[0-9a-f]{32}", md5) else None
                path = map_audio(map_path) if map_path else None
                if path is None:
                    return self._send(404, b"not found", "text/plain")
                try:
                    return self._send_file(path, STATIC_TYPES.get(path.suffix.lower(), "application/octet-stream"))
                except (ConnectionError, OSError):
                    return   # the browser dropped the request (it does when seeking)
            rel = "index.html" if url.path in ("", "/") else url.path.lstrip("/")
            path = (UI_DIR / rel).resolve()
            if UI_DIR not in path.parents or not path.is_file():
                return self._send(404, b"not found", "text/plain")
            self._send(200, path.read_bytes(), STATIC_TYPES.get(path.suffix, "application/octet-stream"))
        except UserError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        try:
            if url.path == "/api/import":
                return self._json(import_replay(urllib.parse.parse_qs(url.query).get("name", ["replay.osr"])[0],
                                                self._body()))
            body = json.loads(self._body() or b"{}")
            self._json(self.api_post(url.path[5:], body))
        except UserError as e:
            self._json({"error": str(e)}, 400)
        except Exception as e:
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def api_get(self, route: str, q: dict):
        LAST_PING[0] = time.time()
        settings = load_settings()
        apply_type_settings(settings)
        if route == "ping":
            return {"ok": True}
        if route == "update":
            return check_update()
        if route == "state":
            from .recommend import SKILLS
            osu = settings["osu_dir"] or str(default_osu_dir() or "")
            return {"settings": settings, "defaults": DEFAULT_SETTINGS, "osu_dir": osu, "api": api_status(),
                    "setup": setup_get(), "skills": list(SKILLS),
                    "skill_names": SKILL_NAMES, "first_run": not SETTINGS_PATH.exists()}
        if route == "detect":
            return detect()
        if route == "replays":
            if q.get("reload"):
                STATE.load(settings, force=True)
            return replay_rows(settings)
        if route == "profile":
            return load_profile()
        if route == "skin":
            from . import skin
            return skin.describe(osu_dir_of(settings), settings["viewer"]["skin"])
        if route == "elo":
            return {"elo": load_elo()}
        if route.startswith("job/"):
            job = JOBS.get(route[4:])
            if not job:
                raise UserError("unknown job")
            return job.public()
        raise UserError(f"unknown: {route}")

    def api_post(self, route: str, body: dict):
        settings = load_settings()
        apply_type_settings(settings)
        if route == "analyze":
            return start_job("analyze", lambda job: analyze(job, settings, body["id"])).public()
        if route == "profile":
            return start_job("profile", lambda job: build_player_profile(job, settings)).public()
        if route == "elo":
            return start_job("elo", lambda job: elo_history(job, settings)).public()
        if route == "search":
            work = recommend_maps if body.get("recommended") else search_maps
            return start_job("search", lambda job: work(job, settings, body)).public()
        if route == "update/install":
            return start_job("update", install_update).public()
        if route == "fetch-map":
            return start_job("fetch-map", lambda job: fetch_replay_map(job, settings, body["id"])).public()
        if route == "combo-breaks":
            return start_job("combo-breaks", lambda job: count_combo_breaks(job, settings, body["ids"])).public()
        if route == "download":
            return start_job("download", lambda job: download_sets(job, settings, body["sets"])).public()
        if route.startswith("job/") and route.endswith("/cancel"):
            job = JOBS.get(route[4:-7])
            if job:
                job.cancel.set()
            return {"ok": True}
        if route == "settings":
            saved = save_settings(body)
            STATE.habits_cache = {}
            return {"settings": saved}
        if route == "setup":
            STATE.habits_cache = {}
            saved = setup_save(body)
            setup_since()       # a change counts from now: the plays after it are the ones made with it
            return saved
        if route == "api":
            return api_save(body)
        if route == "api/test":
            return api_test()
        if route == "open":
            target = str(body.get("url") or "")
            if target.startswith(("https://osu.ppy.sh/", "https://opentabletdriver.net/", RELEASES_URL)):
                webbrowser.open(target)
            elif target == "songs" and STATE.osu_dir:
                os.startfile(songs_dir(STATE.osu_dir))
            return {"ok": True}
        if route == "log":      # errors of the page, for the log file
            print(f"[page] {str(body.get('message'))[:2000]}", file=sys.stderr, flush=True)
            return {"ok": True}
        if route == "pick-folder":
            return {"path": pick_folder()}
        if route == "fullscreen":   # the replay viewer's full screen: the app window too, not only the page
            return {"on": set_fullscreen(bool(body.get("on")))}
        if route == "check-osu":
            return check_osu_dir(str(body.get("path") or ""))
        raise UserError(f"unknown: {route}")


def import_replay(name: str, data: bytes) -> dict:
    name = re.sub(r'[<>:"/\\|?*]', "_", Path(name).name) or "replay.osr"
    if not name.lower().endswith(".osr"):
        raise UserError("An .osr file is needed")
    IMPORTED_DIR.mkdir(parents=True, exist_ok=True)
    path = IMPORTED_DIR / name
    path.write_bytes(data)
    h = read_header(path)
    if h is None or h["mode"] != 0:
        path.unlink(missing_ok=True)
        raise UserError("Not a valid osu!standard replay.")
    return {"id": f"i:{name}"}


def check_osu_dir(path: str) -> dict:
    """Whether a folder is an osu! (stable) install: its beatmap database, replays and Songs."""
    osu_dir = Path(path) if path else default_osu_dir()
    if not osu_dir or not osu_dir.is_dir():
        return {"ok": False, "path": str(osu_dir or ""), "message": "Folder not found."}
    if not (osu_dir / "osu!.db").exists():
        return {"ok": False, "path": str(osu_dir),
                "message": "No osu!.db here: pick the folder that has osu!.exe (osu! stable, not lazer)."}
    replays = replays_dir(osu_dir)
    songs = songs_dir(osu_dir)
    return {"ok": True, "path": str(osu_dir),
            "replays": sum(1 for _ in replays.glob("*.osr")) if replays.exists() else 0,
            "songs": sum(1 for p in songs.iterdir() if p.is_dir()) if songs.exists() else 0,
            "message": ""}


def detect() -> dict:
    """What the setup wizard can find by itself: the osu! folder, OpenTabletDriver's area, the API credentials."""
    from .setup import OTD_SETTINGS, _from_otd
    otd = _from_otd()
    return {
        "osu": check_osu_dir(load_settings()["osu_dir"]),
        "otd": {"found": otd is not None, "settings": str(OTD_SETTINGS),
                "area": [otd.area_w, otd.area_h] if otd else None, "tablet": otd.source if otd else None},
        "api": api_status(),
        "setup": setup_get(),
    }


def pick_folder_here(out_path: str):
    """The folder picker itself (tkinter), writing the chosen folder to out_path."""
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    Path(out_path).write_text(filedialog.askdirectory(title="osu! folder") or "", encoding="utf-8")


def pick_folder() -> str:
    """A native folder picker: the app window's own; without it tkinter, run in its own process so it never fights
    the server's threads (this Python again, or the packaged app: osu-coach.exe --pick-folder)."""
    if WINDOW:
        import webview
        picked = WINDOW[0].create_file_dialog(webview.FOLDER_DIALOG)
        return picked[0] if picked else ""
    import tempfile
    fd, out_path = tempfile.mkstemp(suffix=".txt")
    os.close(fd)
    try:
        if getattr(sys, "frozen", False):
            cmd = [sys.executable, "--pick-folder", out_path]
        else:
            exe = Path(sys.executable)
            python = exe.with_name("python.exe") if exe.name.lower() == "pythonw.exe" else exe
            cmd = [str(python), "-c", f"from osu_coach.gui import pick_folder_here; pick_folder_here({out_path!r})"]
        subprocess.run(cmd, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return Path(out_path).read_text(encoding="utf-8").strip()
    finally:
        Path(out_path).unlink(missing_ok=True)


# --- the window ---------------------------------------------------------------------------------------------------

# --- updates ------------------------------------------------------------------------------------------------------

GITHUB_REPO = "smakkia/osu-coach"
RELEASES_URL = f"https://github.com/{GITHUB_REPO}/releases"
INSTALLER_NAME = re.compile(r"osu-coach-Setup-[\d.]+-windows_x64\.exe$")
PACKAGE_NAME = re.compile(r"osu-coach-[\d.]+-update\.zip$")      # tools/make-update.py
APP_ID = "{1ccc7f17-8fcf-4827-a241-d9103492783a}"                 # tools/setup.iss: its entry in Installed apps
UPDATE_INFO: dict = {}        # the latest release, asked for once per start
UPDATES_DIR = CACHE_DIR / "updates"


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", text))


def _asset(rel: dict, pattern) -> dict | None:
    a = next((a for a in rel.get("assets", []) if pattern.search(a.get("name", ""))), None)
    return a and {"url": a["browser_download_url"], "name": a["name"], "size": a.get("size", 0)}


def check_update() -> dict:
    """Whether a newer release is on GitHub. The installed app (osu-coach.exe) can install it by itself: from its
    update package (only the changed files), else its installer; a copy run with Python gets the release page."""
    from . import __version__
    if not UPDATE_INFO:
        req = urllib.request.Request(f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
                                     headers={"Accept": "application/vnd.github+json", "User-Agent": "osu-coach"})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                rel = json.loads(resp.read())
        except (OSError, ValueError) as e:
            return {"current": __version__, "newer": False, "error": str(e)}
        UPDATE_INFO.update({
            "latest": str(rel.get("tag_name") or "").lstrip("v"), "url": rel.get("html_url") or RELEASES_URL,
            "notes": str(rel.get("body") or "")[:3000],
            "package": _asset(rel, PACKAGE_NAME), "installer": _asset(rel, INSTALLER_NAME),
        })
    latest = UPDATE_INFO["latest"]
    return {**UPDATE_INFO, "current": __version__,
            "newer": bool(latest) and version_key(latest) > version_key(__version__),
            "can_install": bool(getattr(sys, "frozen", False) and (UPDATE_INFO["package"] or UPDATE_INFO["installer"]))}


def _download(job: Job, asset: dict, path: Path, label: str):
    job.step(label, 0, max(1, asset["size"]))
    req = urllib.request.Request(asset["url"], headers={"User-Agent": "osu-coach"})
    tmp = path.with_suffix(".part")
    try:
        with urllib.request.urlopen(req, timeout=60) as resp, open(tmp, "wb") as f:
            done = 0
            while chunk := resp.read(1 << 16):
                job.check()
                f.write(chunk)
                done += len(chunk)
                job.step(label, done, max(done, asset["size"]))
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)


def _sha256(path: Path) -> str | None:
    import hashlib
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


# applies an update once the app has closed (its files are locked while it runs): copies the changed files from the
# unpacked package, deletes the ones the new version doesn't have, records the version in Installed apps and starts it
APPLY_SCRIPT = r"""param([int]$ProcId, [string]$Plan)
Start-Transcript -Path ([IO.Path]::ChangeExtension($Plan, '.log')) -Force | Out-Null
try { Wait-Process -Id $ProcId -Timeout 60 -ErrorAction SilentlyContinue } catch {}
Start-Sleep -Milliseconds 500
$p = Get-Content -Raw -Encoding UTF8 -LiteralPath $Plan | ConvertFrom-Json
foreach ($c in $p.copy) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $c.to) | Out-Null
    for ($i = 0; $i -lt 10; $i++) {
        try { Copy-Item -LiteralPath $c.from -Destination $c.to -Force -ErrorAction Stop; break }
        catch { Start-Sleep -Milliseconds 500 }
    }
}
foreach ($d in $p.delete) { Remove-Item -LiteralPath $d -Force -ErrorAction SilentlyContinue }
try { Set-ItemProperty -Path $p.uninstall_key -Name DisplayVersion -Value $p.version -ErrorAction Stop } catch {}
Start-Process -FilePath $p.exe
Stop-Transcript | Out-Null
Remove-Item -LiteralPath $p.staging -Recurse -Force -ErrorAction SilentlyContinue
"""


def install_update(job: Job) -> dict:
    """Install the newer release. From its update package: download, compare every file with the installed one by
    SHA-256, and once the window has closed write only the files that changed (and delete those the new version
    no longer has), then start the new version. Without a package: its installer, run silently (tools/setup.iss)."""
    import shutil
    import zipfile
    info = check_update()
    if not info["newer"] or not info.get("can_install"):
        raise UserError("There is no update to install here.")
    UPDATES_DIR.mkdir(parents=True, exist_ok=True)
    for old in UPDATES_DIR.iterdir():      # what earlier updates left behind
        shutil.rmtree(old, ignore_errors=True) if old.is_dir() else old.unlink(missing_ok=True)
    label = f"Downloading osu!coach {info['latest']}"
    if not info.get("package"):
        path = UPDATES_DIR / info["installer"]["name"]
        _download(job, info["installer"], path, label)
        job.step("Starting the installer")
        subprocess.Popen([str(path), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS"])
        threading.Timer(1.5, close_window).start()
        return {"ok": True, "mode": "installer"}

    package = UPDATES_DIR / info["package"]["name"]
    _download(job, info["package"], package, label)
    job.step("Comparing the files")
    staging = UPDATES_DIR / f"staging-{info['latest']}"
    app_dir = Path(sys.executable).resolve().parent
    targets = {"app": app_dir, "data": CACHE_DIR}
    with zipfile.ZipFile(package) as z:
        manifest = json.loads(z.read("manifest.json"))
        changed = []
        for name, digest in manifest["files"].items():
            prefix, _, rel = name.partition("/")
            target = (targets[prefix] / rel).resolve()
            if targets[prefix].resolve() not in target.parents:
                continue                                   # nothing outside the app and data folders
            if _sha256(target) != digest:
                changed.append((name, target))
        for name, _ in changed:
            z.extract(name, staging)
    # files of the old version the new one doesn't have: only inside _internal (the app's own libraries)
    keep = {(app_dir / n.partition("/")[2]).resolve() for n in manifest["files"] if n.startswith("app/")}
    internal = app_dir / "_internal"
    removed = [p for p in internal.rglob("*") if p.is_file() and p.resolve() not in keep] if internal.is_dir() else []
    plan = {
        "copy": [{"from": str(staging / name), "to": str(target)} for name, target in changed],
        "delete": [str(p) for p in removed],
        "exe": str(Path(sys.executable).resolve()), "staging": str(staging), "version": manifest["version"],
        "uninstall_key": f"HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\{APP_ID}_is1",
    }
    plan_path = UPDATES_DIR / "plan.json"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    script = UPDATES_DIR / "apply.ps1"
    script.write_text(APPLY_SCRIPT, encoding="utf-8-sig")
    package.unlink(missing_ok=True)
    job.step(f"{len(changed)} files to update, {len(removed)} to remove")
    subprocess.Popen(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden",
                      "-File", str(script), "-ProcId", str(os.getpid()), "-Plan", str(plan_path)],
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    threading.Timer(1.5, close_window).start()
    return {"ok": True, "mode": "package", "changed": len(changed), "removed": len(removed)}


def close_window():
    if WINDOW:
        try:
            WINDOW[0].destroy()
        except Exception:
            os._exit(0)
    else:
        os._exit(0)


WINDOW: list = []     # the app window (pywebview), once open
FULLSCREEN = [False]  # whether the app window is full screen (pywebview only toggles it)


def set_fullscreen(on: bool) -> bool:
    if WINDOW and FULLSCREEN[0] != on:
        WINDOW[0].toggle_fullscreen()
        FULLSCREEN[0] = on
    return FULLSCREEN[0]


INSTANCE_MUTEX: list = []    # held for the whole life of the window


def single_instance() -> bool:
    """One window at a time (Windows): two would fight over the web view's storage and neither would open. When
    osu!coach is already open, its window comes to the front instead and this start gives up (False)."""
    import ctypes
    kernel32, user32 = ctypes.windll.kernel32, ctypes.windll.user32
    kernel32.CreateMutexW.restype = ctypes.c_void_p
    handle = kernel32.CreateMutexW(None, False, "Local\\osu-coach-window")
    if kernel32.GetLastError() != 183:     # ERROR_ALREADY_EXISTS
        INSTANCE_MUTEX.append(handle)
        return True
    for _ in range(20):                    # the other one may still be opening its window
        hwnd = user32.FindWindowW(None, "osu!coach")
        if hwnd:
            user32.ShowWindow(hwnd, 9)     # SW_RESTORE
            user32.SetForegroundWindow(hwnd)
            break
        time.sleep(.5)
    return False


def open_window(url: str) -> bool:
    """The app's own window (pywebview: the system's web view, WebView2 on Windows), until it is closed. False when
    pywebview can't open one: the page then opens in the default browser."""
    try:
        import webview
    except ImportError:
        return False
    width, height = 1440, 920
    try:   # not larger than the screen
        screen = webview.screens[0]
        width, height = min(width, int(screen.width * .92)), min(height, int(screen.height * .88))
    except Exception:
        pass
    if sys.platform == "win32":   # the app's own taskbar button, with the window's icon (not python's)
        try:
            import ctypes
            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("osucoach.app")
        except (AttributeError, OSError):
            pass
        if not single_instance():
            return True
    try:
        WINDOW.append(webview.create_window("osu!coach", url, width=width, height=height, min_size=(1000, 640),
                                            background_color="#120d17", text_select=True))
        webview.start(private_mode=False, storage_path=str(WEBVIEW_STORAGE), icon=str(UI_DIR / "icon.ico"))
    except Exception:
        traceback.print_exc()
        WINDOW.clear()
        return False
    return True


def exit_now():
    """Leave as soon as the window is closed. A normal exit would first wait for every process pool still at work
    (an analysis or a search running in the background) to finish its queue, while the single-instance mutex keeps
    osu!coach from opening again. Everything is saved as it goes (caches, settings), so nothing is lost: the
    workers are stopped and the process ends."""
    import multiprocessing
    for child in multiprocessing.active_children():
        child.terminate()
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    os._exit(0)


def main():
    if sys.stdout is None:              # pythonw: no console
        sys.stdout = open(os.devnull, "w")
    if sys.stderr is None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        sys.stderr = open(CACHE_DIR / "ui_errors.log", "a", encoding="utf-8")
    # for tests: OSU_COACH_PORT=8765 serves on that port, OSU_COACH_NO_WINDOW=1 opens no window
    server = ThreadingHTTPServer(("127.0.0.1", int(os.environ.get("OSU_COACH_PORT") or 0)), Handler)
    url = f"http://127.0.0.1:{server.server_address[1]}/"
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print(f"osu-coach: {url}  (stops by itself when the window is closed)")
    if not os.environ.get("OSU_COACH_NO_WINDOW"):
        if open_window(url):    # returns when the window is closed
            exit_now()
        webbrowser.open(url)
    LAST_PING[0] = time.time() + 60     # time to open the window
    try:
        while time.time() - LAST_PING[0] < IDLE_EXIT_S:
            time.sleep(2)
    except KeyboardInterrupt:
        pass
    server.shutdown()


if __name__ == "__main__":
    main()
