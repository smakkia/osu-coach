"""The player's osu! skin for the replay viewer: which skin, its skin.ini, the images and hitsounds it has, and the
hitsounds a play makes (read from the .osu: sample sets, additions and volumes, as stable plays them).

The skin is osu!'s own (the `Skin =` line of osu!.<user>.cfg) unless another one is picked in Settings. Only what
the skin has is used; the viewer draws the rest itself.
"""

import bisect
import re
from pathlib import Path

SAMPLE_SETS = {1: "normal", 2: "soft", 3: "drum"}
ADDITIONS = ((2, "whistle"), (4, "finish"), (8, "clap"))
# the images the viewer can draw from the skin (the numbers come from [Fonts] HitCirclePrefix)
ELEMENTS = ("hitcircle", "hitcircleoverlay", "sliderstartcircle", "sliderstartcircleoverlay", "approachcircle",
            "sliderb0", "sliderb", "sliderfollowcircle", "reversearrow", "cursor", "cursormiddle", "cursortrail",
            "hit0", "hit50", "hit100", "spinner-approachcircle", "spinner-circle")
# hitsounds a skin doesn't have come from here (from the Rafis HDDT 2024 skin: osu!'s own default is inside its dlls)
FALLBACK_SOUNDS = Path(__file__).parent / "sounds"
FALLBACK_PREFIX = "@fallback/"
SOUNDS = [f"{s}-{kind}" for s in SAMPLE_SETS.values()
          for kind in ("hitnormal", "hitwhistle", "hitfinish", "hitclap", "slidertick")] + ["combobreak"]


def skins_dir(osu_dir: Path) -> Path:
    return osu_dir / "Skins"


def current_skin(osu_dir: Path) -> str:
    """The skin osu! itself uses: the Skin line of osu!.<user>.cfg."""
    for cfg in osu_dir.glob("osu!.*.cfg"):
        for line in cfg.read_text(encoding="utf-8", errors="replace").splitlines():
            key, _, value = line.partition("=")
            if key.strip() == "Skin" and value.strip():
                return value.strip()
    return ""


def list_skins(osu_dir: Path) -> list[str]:
    folder = skins_dir(osu_dir)
    return sorted((p.name for p in folder.iterdir() if p.is_dir()), key=str.lower) if folder.exists() else []


def skin_folder(osu_dir: Path, name: str) -> Path | None:
    """The skin's folder, only inside osu!'s Skins folder."""
    name = name or current_skin(osu_dir)
    if not name:
        return None
    folder = (skins_dir(osu_dir) / name).resolve()
    if folder.parent != skins_dir(osu_dir).resolve() or not folder.is_dir():
        return None
    return folder


def _colour(value: str) -> list[int] | None:
    parts = [p.strip() for p in value.split(",")]
    try:
        rgb = [max(0, min(255, int(float(p)))) for p in parts[:3]]
    except ValueError:
        return None
    return rgb if len(rgb) == 3 else None


def read_ini(folder: Path) -> dict:
    """The parts of skin.ini the viewer uses (keys as osu! reads them, case-insensitively)."""
    ini = {"combo": [], "slider_border": None, "slider_track": None, "hitcircle_prefix": "default",
           "hitcircle_overlap": -2, "cursor_centre": True, "cursor_rotate": True, "layered_hitsounds": True}
    path = next((p for p in folder.iterdir() if p.name.lower() == "skin.ini"), None)
    if path is None:
        return ini
    section, combos = "", {}
    for raw in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        line = raw.split("//")[0].strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].lower()
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip().lower(), value.strip()
        if section == "colours":
            m = re.fullmatch(r"combo([1-8])", key)
            if m and _colour(value):
                combos[int(m.group(1))] = _colour(value)
            elif key == "sliderborder":
                ini["slider_border"] = _colour(value)
            elif key == "slidertrackoverride":
                ini["slider_track"] = _colour(value)
        elif section == "fonts":
            if key == "hitcircleprefix":
                ini["hitcircle_prefix"] = value.replace("\\", "/")
            elif key == "hitcircleoverlap":
                try:
                    ini["hitcircle_overlap"] = int(value)
                except ValueError:
                    pass
        elif section == "general":
            if key == "cursorcentre":
                ini["cursor_centre"] = value != "0"
            elif key == "cursorrotate":
                ini["cursor_rotate"] = value != "0"
            elif key == "layeredhitsounds":
                ini["layered_hitsounds"] = value != "0"
    ini["combo"] = [combos[k] for k in sorted(combos)]
    return ini


def _find(folder: Path, name: str, exts: tuple[str, ...]) -> tuple[str, bool] | None:
    """The skin's file for an element, the @2x version first: (path relative to the skin, is @2x)."""
    sub, _, base = name.rpartition("/")
    where = folder / sub if sub else folder
    if not where.is_dir():
        return None
    files = {p.name.lower(): p.name for p in where.iterdir()}
    for hd in (True, False):
        for ext in exts:
            wanted = f"{base}{'@2x' if hd else ''}{ext}".lower()
            if wanted in files:
                return (f"{sub}/{files[wanted]}" if sub else files[wanted]), hd
    return None


def describe(osu_dir: Path, name: str) -> dict:
    """What the viewer needs to know about the skin: its ini and the files it has for each element and sound."""
    folder = skin_folder(osu_dir, name)
    out = {"name": folder.name if folder else "", "skins": list_skins(osu_dir), "current": current_skin(osu_dir),
           "images": {}, "sounds": {}, "ini": read_ini(folder) if folder else read_ini_default()}
    prefix = out["ini"]["hitcircle_prefix"]
    for element in (ELEMENTS + tuple(f"{prefix}-{n}" for n in range(10))) if folder else ():
        found = _find(folder, element, (".png", ".jpg"))
        if found:
            key = f"default-{element.rsplit('-', 1)[1]}" if element.startswith(prefix + "-") else element
            out["images"][key] = {"file": found[0], "hd": found[1]}
    fallback = FALLBACK_SOUNDS
    for sound in SOUNDS:
        found = _find(folder, sound, (".wav", ".ogg", ".mp3")) if folder else None
        if found:
            out["sounds"][sound] = found[0]
        elif fallback and fallback != folder and (found := _find(fallback, sound, (".wav", ".ogg", ".mp3"))):
            out["sounds"][sound] = FALLBACK_PREFIX + found[0]
    return out



def read_ini_default() -> dict:
    return {"combo": [], "slider_border": None, "slider_track": None, "hitcircle_prefix": "default",
            "hitcircle_overlap": -2, "cursor_centre": True, "cursor_rotate": True, "layered_hitsounds": True}


def skin_file(osu_dir: Path, name: str, rel: str) -> Path | None:
    """A file of the skin, only inside its folder."""
    if rel.startswith(FALLBACK_PREFIX):   # a hitsound the skin lacks
        folder, rel = FALLBACK_SOUNDS.resolve(), rel[len(FALLBACK_PREFIX):]
    else:
        folder = skin_folder(osu_dir, name)
    if folder is None:
        return None
    path = (folder / rel).resolve()
    if folder not in path.parents or not path.is_file():
        return None
    return path


# --- the hitsounds of a map ------------------------------------------------------------------------------------

def _samples(normal: int, addition: int, sounds: int) -> list[str]:
    """Stable's hitsound for one moment: hitnormal always, plus whistle / finish / clap from the addition set."""
    names = [f"{SAMPLE_SETS.get(normal, 'normal')}-hitnormal"]
    add = SAMPLE_SETS.get(addition, SAMPLE_SETS.get(normal, "normal"))
    names += [f"{add}-hit{name}" for bit, name in ADDITIONS if sounds & bit]
    return names


def map_sounds(path: Path) -> list[dict]:
    """Per hit object, in the order beatmap.parse_beatmap keeps them: the samples of each node (the head, then every
    repeat and the end of sliders; a spinner's end) with their volume, and a slider's tick sample."""
    lines = path.read_text(encoding="utf-8-sig", errors="replace").splitlines()
    version = 14
    if lines and lines[0].strip().startswith("osu file format v"):
        try:
            version = int(lines[0].strip()[len("osu file format v"):])
        except ValueError:
            pass
    offset = 24 if version < 5 else 0
    section, timing, objects = None, [], []
    for line in lines[1:]:
        line = line.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == "TimingPoints":
            parts = line.split(",")
            try:
                timing.append((float(parts[0]) + offset, int(parts[3]) if len(parts) > 3 else 1,
                               int(parts[5]) if len(parts) > 5 else 100))
            except (ValueError, IndexError):
                continue
        elif section == "HitObjects":
            objects.append(line.split(","))
    timing.sort(key=lambda t: t[0])
    times = [t[0] for t in timing]

    def at(t: float) -> tuple[int, int]:
        """Sample set and volume of the timing point covering t (the first one before the map starts)."""
        i = max(bisect.bisect_right(times, t + 1) - 1, 0)   # stable takes the point up to a millisecond later
        return (timing[i][1], timing[i][2]) if timing else (1, 100)

    def hit_sample(field: str, t: float) -> tuple[int, int, int]:
        """normalSet, additionSet and volume from an object's hitSample field, with the timing point's defaults."""
        parts = field.split(":") if field else []
        tp_set, tp_vol = at(t)
        try:
            normal = int(parts[0]) if parts and parts[0] else 0
            addition = int(parts[1]) if len(parts) > 1 and parts[1] else 0
            volume = int(parts[3]) if len(parts) > 3 and parts[3] else 0
        except ValueError:
            normal = addition = volume = 0
        return normal or tp_set, addition or normal or tp_set, volume or tp_vol

    out = []
    for parts in objects:
        if len(parts) < 4:
            continue
        try:
            time, kind, sounds = float(parts[2]) + offset, int(parts[3]), int(parts[4]) if len(parts) > 4 else 0
        except ValueError:
            continue
        if kind & 2 and len(parts) > 7:
            normal, addition, volume = hit_sample(parts[10] if len(parts) > 10 else "", time)
            repeats = max(1, int(parts[6]))
            edge_sounds = parts[8].split("|") if len(parts) > 8 and parts[8] else []
            edge_sets = parts[9].split("|") if len(parts) > 9 and parts[9] else []
            nodes = []
            for i in range(repeats + 1):
                s = int(edge_sounds[i]) if i < len(edge_sounds) and edge_sounds[i].lstrip("-").isdigit() else sounds
                n, a = normal, addition
                if i < len(edge_sets):
                    es = edge_sets[i].split(":")
                    n = int(es[0]) if es and es[0].isdigit() and int(es[0]) else normal
                    a = int(es[1]) if len(es) > 1 and es[1].isdigit() and int(es[1]) else (n if n != normal else addition)
                nodes.append({"samples": _samples(n, a, s), "volume": volume})
            out.append({"nodes": nodes, "tick": f"{SAMPLE_SETS.get(normal, 'normal')}-slidertick", "volume": volume})
        elif kind & 8:
            normal, addition, volume = hit_sample(parts[6] if len(parts) > 6 else "", time)
            out.append({"nodes": [{"samples": _samples(normal, addition, sounds), "volume": volume}]})
        elif kind & 1:
            normal, addition, volume = hit_sample(parts[5] if len(parts) > 5 else "", time)
            out.append({"nodes": [{"samples": _samples(normal, addition, sounds), "volume": volume}]})
    return out


def play_sounds(beatmap, results, sounds: list[dict], miss_window: float = 150) -> list[list]:
    """What the play sounded like: [time, samples, volume] for every hit, slider tick, repeat and end held, and
    spinner cleared; and osu!'s combobreak when a combo of 20 or more breaks (a miss, a missed slider head or a
    dropped tick / repeat)."""
    from .beatmap import SLIDER, SPINNER
    events = []   # [time, samples, volume, combo change: +1, 0 = none, -1 = break]
    for r, s in zip(results, sounds):
        o = r.obj
        if o.kind == SLIDER:
            if r.head_result and r.hit_time is not None:
                events.append([r.hit_time, s["nodes"][0]["samples"], s["nodes"][0]["volume"], 1])
            elif r.result is not None:
                events.append([o.time + miss_window, [], 0, -1])
            edge = 1
            for p in o.score_points:
                if r.slider_break_time is not None and p.time >= r.slider_break_time:
                    if p.kind != "end":   # a dropped end doesn't reset the combo in stable
                        events.append([r.slider_break_time, [], 0, -1])
                    break
                if p.kind == "tick":
                    events.append([p.time, [s["tick"]], s["volume"], 1])
                else:
                    node = s["nodes"][min(edge, len(s["nodes"]) - 1)]
                    # the end sounds at the slider's real end, not at stable's early judgement
                    events.append([o.end_time if p.kind == "end" else p.time, node["samples"], node["volume"], 1])
                    edge += 1
        elif o.kind == SPINNER:
            if r.result:
                events.append([o.end_time, s["nodes"][0]["samples"], s["nodes"][0]["volume"], 1])
            elif r.result == 0:
                events.append([o.end_time, [], 0, -1])
        elif r.result:
            events.append([r.hit_time if r.hit_time is not None else o.time, s["nodes"][0]["samples"],
                           s["nodes"][0]["volume"], 1])
        elif r.result == 0:
            events.append([r.hit_time if r.hit_time is not None else o.time + miss_window, [], 0, -1])
    events.sort(key=lambda e: e[0])
    out, combo = [], 0
    for time, samples, volume, change in events:
        if change < 0:
            if combo >= 20:
                out.append([time, ["combobreak"], 100])
            combo = 0
            continue
        combo += 1
        out.append([time, samples, volume])
    return out
