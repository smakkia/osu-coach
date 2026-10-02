"""Guessing the type of a map without its .osu, from what the osu! API says about it.

The classifiers are trained on the maps in Songs (their real types, maptypes.py) from what can be known
without the .osu: star rating, BPM, circles / sliders / spinners, length, AR / CS / OD, how the song, the
mapper and the artist's other maps are labelled, and how popular the map is. They are saved in the cache
(typeguess.pkl) by the training script; without it nothing is guessed. A guess counts only where the model
is sure enough (the thresholds below, chosen on maps held out of the training): the exact type (burst and
finger control are one family: without the .osu they can't be told apart), else the main kind, else nothing.
"""

import pickle
import re

import numpy as np

from .locate import CACHE_DIR

# maps downloaded and read for real (by the training, recommend --online and search --online), with their labels:
# the next training learns from them, and their guesses give way to the real type
LOWCONF = CACHE_DIR / "training" / "lowconf.json"

MODEL_PATH = CACHE_DIR / "typeguess.pkl"
# the same models as plain arrays (to_lite): what the app ships, so it runs without scikit-learn
LITE_PATH = CACHE_DIR / "typeguess_lite.pkl"
SURE_TYPE, SURE_MAIN, SURE_AIM = 0.7, 0.7, 0.8
_model = None


class LiteForest:
    """A fitted HistGradientBoostingClassifier as numpy arrays: every tree walked at once, as scikit-learn does
    (a missing value goes where the split sent the missing values in training, else x <= threshold goes left)."""

    def __init__(self, model):
        feature, threshold, nan_left, left, right, leaf, value, roots, cls = [], [], [], [], [], [], [], [], []
        start = 0
        for iteration in model._predictors:
            for k, tree in enumerate(iteration):
                n = tree.nodes
                if n["is_categorical"].any():
                    raise ValueError("categorical splits aren't supported")
                feature.append(n["feature_idx"]); threshold.append(n["num_threshold"])
                nan_left.append(n["missing_go_to_left"].astype(bool))
                left.append(n["left"].astype(np.int64) + start); right.append(n["right"].astype(np.int64) + start)
                leaf.append(n["is_leaf"].astype(bool)); value.append(n["value"])
                roots.append(start); cls.append(k)
                start += len(n)
        self.feature, self.threshold = np.concatenate(feature), np.concatenate(threshold)
        self.nan_left, self.leaf, self.value = np.concatenate(nan_left), np.concatenate(leaf), np.concatenate(value)
        self.left, self.right = np.concatenate(left), np.concatenate(right)
        self.roots, self.cls = np.array(roots), np.array(cls)
        self.baseline = np.asarray(model._baseline_prediction, dtype=float).ravel()
        self.classes_ = np.asarray(model.classes_)

    def predict_proba(self, x) -> np.ndarray:
        row = np.asarray(x, dtype=float)[0]
        at = self.roots.copy()
        while True:
            inner = ~self.leaf[at]
            if not inner.any():
                break
            node = at[inner]
            v = row[self.feature[node]]
            go_left = np.where(np.isnan(v), self.nan_left[node], v <= self.threshold[node])
            at[inner] = np.where(go_left, self.left[node], self.right[node])
        raw = self.baseline + np.bincount(self.cls, weights=self.value[at], minlength=len(self.baseline))
        if len(raw) == 1:   # two classes: one tree per iteration, the log-odds of the second
            p = 1 / (1 + np.exp(-raw[0]))
            return np.array([[1 - p, p]])
        e = np.exp(raw - raw.max())
        return (e / e.sum())[None, :]


def to_lite(src=MODEL_PATH, dst=LITE_PATH):
    """typeguess.pkl (needs scikit-learn) -> typeguess_lite.pkl (numpy only), for the installer and the data zip."""
    m = pickle.loads(src.read_bytes())
    lite = dict(m, models={name: LiteForest(model) for name, model in m["models"].items()})
    dst.write_bytes(pickle.dumps(lite, protocol=pickle.HIGHEST_PROTOCOL))
    return lite


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def _song_key(artist: str, title: str) -> str:
    title = re.sub(r"\((tv size|cut ver\.?|short ver\.?|sped up ver\.?|extended ver\.?|game ver\.?)\)", "", title.lower())
    return _norm(artist) + "|" + _norm(title)


def _load():
    global _model
    if _model is None:
        _model = False
        # the full models where they were trained (fresher than a lite copy), else the lite ones
        for path in (MODEL_PATH, LITE_PATH):
            try:
                _model = pickle.loads(path.read_bytes())
                break
            except (OSError, pickle.UnpicklingError, EOFError, ImportError, AttributeError):
                continue
    return _model or None


def available() -> bool:
    return _load() is not None


def is_reading(p: dict, ar: float, cs: float, od: float, mods_name: str, stars: float | None = None) -> bool:
    """The reading rule of maptypes (effective AR 8.5 or lower, finger control/burst share x aim control x AR) on a guess:
    the AR is known exactly, the share of finger notes isn't, only the type. Finger control/burst as the main kind
    (usually 35%+ of the intense notes) reaches the rule with an aim control a bit under the usual: anything but a
    sure "no"; as the second kind of a hybrid (a smaller share) it needs aim control guessed yes."""
    from .advice import effective_ar
    from .difficulty import Difficulty
    from .maptypes import NAMES, READING_LOW_AR, READING_MIN_STARS
    from .mods import Mods, clock_rate
    if ar is None or not p or not stars or stars < READING_MIN_STARS:
        return False
    parts = [part.strip().lower() for part in (p.get("kind") or "").split("+")]
    finger, aim = NAMES["finger"].lower(), p.get("aim control")
    if finger not in parts or not (aim is not False if parts[0] == finger else aim is True):
        return False
    mods = int(Mods.DoubleTime) if mods_name == "DT" else 0
    rate = clock_rate(mods)
    return effective_ar(Difficulty.from_map(cs or 4, ar, od or 8, mods), rate) <= READING_LOW_AR


def mark_reading(p: dict, reading: bool, bpm: float | None = None, mods_name: str = "NM",
                 cs: float | None = None) -> dict:
    """The guess with its tags next to the type: reading, speed and precision (the map's main BPM and CS are
    known; NM and DT don't change CS)."""
    from .maptypes import is_precision, is_speed
    played = (bpm or 0) * (1.5 if mods_name == "DT" else 1.0)
    return dict(p, reading=bool(reading), speed=is_speed(p.get("kind"), played), precision=is_precision(cs))


def _features(bm: dict, bs: dict, mods_name: str, m: dict) -> list[float]:
    """The same features, in the same order, as the training (specs, song, players, mapper, artist, popularity).
    Players (who has the map in their top plays) aren't known here: missing, as for most maps in training."""
    n = len(m["targets"])
    nan = float("nan")
    rate = 1.5 if mods_name == "DT" else 1.0
    circles, sliders, spinners = (bm.get("count_circles") or 0, bm.get("count_sliders") or 0,
                                  bm.get("count_spinners") or 0)
    objects = circles + sliders + spinners
    length = (bm.get("hit_length") or 0) / rate
    stars = bm.get("difficulty_rating")
    spec = [(stars or nan) * (m["dt_stars"] if rate > 1 else 1), (bm.get("bpm") or nan) * rate, circles, sliders,
            spinners, length, bm.get("ar", nan), bm.get("cs", nan), bm.get("accuracy", nan),
            circles / max(objects, 1), objects / max(length, 1), rate]

    def group(table, key):
        got = m["tables"][table].get(f"{key}|{mods_name}") if key else None
        return list(got[0]) + [got[1]] if got else [nan] * n + [0]

    players = [0, nan, nan, nan, nan, nan, nan, 0] + [nan] * n
    artist, title, creator = bs.get("artist", ""), bs.get("title", ""), bs.get("creator", "")
    plays, passes = bm.get("playcount"), bm.get("passcount")
    genre = bs.get("genre_id") or (bs.get("genre") or {}).get("id")
    language = bs.get("language_id") or (bs.get("language") or {}).get("id")
    popularity = [plays, passes, (passes or 0) / plays if plays else nan, bs.get("favourite_count"),
                  bs.get("play_count"), genre, language]
    row = (spec + group("song", _song_key(artist, title)) + players + group("mapper", _norm(creator))
           + group("artist", _norm(artist)) + popularity)
    return [nan if v is None else float(v) for v in row]


def guess(bm: dict, bs: dict, mods_name: str) -> dict | None:
    """The guessed type of a beatmap (API dicts of the beatmap and its set), "NM" or "DT"; the same fields as
    the precomputed guesses: kind (None when unsure), confidence, aim control (True / False / None)."""
    from .maptypes import too_easy
    m = _load()
    if m is None or too_easy(bm.get("difficulty_rating")):     # easy maps get no type
        return None
    x = np.array([_features(bm, bs, mods_name, m)])
    out = {}
    for name in ("exact", "main", "aim"):
        model = m["models"][name]
        p = model.predict_proba(x)[0]
        out[name] = (model.classes_[p.argmax()], float(p.max()))
    (ge, pe), (gm, pm), (ga, pa) = out["exact"], out["main"], out["aim"]
    if pe >= SURE_TYPE and ge != "rare":
        kind, sure = str(ge), "type"
    elif pm >= SURE_MAIN and gm != "rare":
        kind, sure = str(gm), "main kind"
    else:
        kind, sure = None, "unsure"
    p = {"kind": kind, "confidence": sure, "p_type": round(pe, 3), "guess": str(ge), "p_main": round(pm, 3),
         "main": str(gm), "aim control": bool(ga) if pa >= SURE_AIM else None, "p_aim": round(pa, 3)}
    return mark_reading(p, is_reading(p, bm.get("ar"), bm.get("cs"), bm.get("accuracy"), mods_name,
                                      bm.get("difficulty_rating")), bm.get("bpm"), mods_name, bm.get("cs"))


def real_guess(a: dict) -> dict:
    """A map's real type (maptypes analysis, with its AR, stars, BPM and CS as played) in the guesses' format."""
    from . import maptypes
    return {"kind": maptypes.category(a), "reading": maptypes.has_reading(a), "speed": maptypes.has_speed(a),
            "precision": maptypes.has_precision(a), "confidence": "downloaded",
            "aim control": maptypes.has_aim_control(a), "p_type": 1.0, "p_main": 1.0, "p_aim": 1.0}


def remember(results) -> int:
    """Keep the real types of maps downloaded from the site: (MapInfo, mods, analysis) with analyses from
    maptypes.types(), NM and DT only. They go to the training's labels and replace the maps' guesses, so
    guess, search and recommend show the real type from then on. Returns how many were new."""
    import json
    from .recommend import load_predicted
    try:
        done = json.loads(LOWCONF.read_text())
    except (OSError, ValueError):
        done = {}
    guessed = {}
    new = 0
    for m, mods, a in results:
        name = {0: "NM", 64: "DT"}.get(mods)
        if not a or not name or m.beatmap_id <= 0:
            continue
        entry = done.setdefault(str(m.beatmap_id), {"stars": m.stars.get(0, 0.0)})
        entry.pop("error", None)
        new += name not in entry
        entry[name] = a
        guessed[f"{m.beatmap_id}:{name}"] = real_guess(a)
    LOWCONF.parent.mkdir(parents=True, exist_ok=True)
    LOWCONF.write_text(json.dumps(done))
    load_predicted().update(guessed)
    return new
