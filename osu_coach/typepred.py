"""Training the map type guesser (typeguess.py) and the guesses for maps in players' top plays.

Labels: maptypes on every ranked map in Songs (NM and DT), plus maps downloaded because their guess was
unsure. Factors, in this order: map specs; the song's other mapsets; who plays it, with which mods and
how well (top plays); the mapper; the artist; play count. Tested on held-out beatmap sets.

Steps (python -m osu_coach retrain runs them in order):
  label       map types of every ranked map in Songs, NM and DT (maptypes cache)
  build       the rows: labelled maps, and the maps in the sampled top plays not in Songs
  confidence  accuracy of the guesses at each confidence, on held-out beatmap sets (optional)
  predict     guesses for the maps in top plays (predicted_types.json)
  export      the classifiers and group tables for guessing any map from the API (typeguess.pkl)
  download    the least sure guesses: their .osu downloaded and labelled, for the next training
Needs scikit-learn.
"""
import json
import pickle
import re
from collections import defaultdict

import numpy as np

from . import maptypes, mapvec, typeguess
from .locate import CACHE_DIR, default_osu_dir
from .mapdb import read_osu_db
from .online import DT_SR_FACTOR

TRAINING_DIR = CACHE_DIR / "training"
D = str(TRAINING_DIR) + "/"
TARGETS = ("stream", "jump", "slider aim", "alt", "finger", "tech", "tech sliders", "finger changes", "aim control")
MODS = {"NM": 0, "DT": 64}
LOWCONF = typeguess.LOWCONF    # maps downloaded (unsure guesses, recommend and search online), with their labels
DT_SR = DT_SR_FACTOR     # rough DT star factor when only nomod stars are known (as online.py)


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


def song_key(artist: str, title: str) -> str:
    title = re.sub(r"\((tv size|cut ver\.?|short ver\.?|sped up ver\.?|extended ver\.?|game ver\.?)\)", "", title.lower())
    return norm(artist) + "|" + norm(title)


def api_index():
    """From cached API top plays: per beatmap id its metadata, and the scores set on it."""
    meta, scores = {}, defaultdict(list)
    for f in (CACHE_DIR / "api_cache").glob("*"):
        try:
            body = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        body = body.get("body", body) if isinstance(body, dict) else body
        if not (isinstance(body, list) and body and isinstance(body[0], dict) and "beatmap" in body[0]):
            continue
        for s in body:
            b, bs = s.get("beatmap") or {}, s.get("beatmapset") or {}
            if b.get("mode_int", 0) != 0 or "id" not in b:
                continue
            meta[b["id"]] = {"creator": bs.get("creator", ""), "creator_id": bs.get("user_id") or b.get("user_id"),
                             "artist": bs.get("artist", ""), "title": bs.get("title", ""), "set": b.get("beatmapset_id"),
                             "playcount": b.get("playcount"), "passcount": b.get("passcount"),
                             "favourites": bs.get("favourite_count"), "set_plays": bs.get("play_count"),
                             "genre": bs.get("genre_id"), "language": bs.get("language_id"),
                             "stars": b.get("difficulty_rating"), "bpm": b.get("bpm"), "circles": b.get("count_circles"),
                             "sliders": b.get("count_sliders"), "spinners": b.get("count_spinners"),
                             "length": b.get("hit_length"), "ar": b.get("ar"), "cs": b.get("cs"), "od": b.get("accuracy"),
                             "version": b.get("version", "")}
            mods = [m["acronym"] if isinstance(m, dict) else m for m in s.get("mods", [])]
            scores[b["id"]].append((s.get("user_id"), set(mods), s.get("accuracy"), s.get("pp"), s.get("id")))
    for b in scores:   # the same score can come from several cached pages
        scores[b] = list({sc[4]: sc for sc in scores[b]}.values())
    return meta, scores


def build():
    osu = default_osu_dir()
    db = [m for m in read_osu_db(osu / "osu!.db") if m.mode == 0]
    from .cachedb import Store
    with Store(maptypes.MAPTYPE_CACHE, maptypes.MAPTYPE_VERSION, legacy=maptypes.MAPTYPE_CACHE.with_suffix(".json")) as db:
        kinds = dict(db.items())
    meta, scores = api_index()
    # every ranked map's API metadata (ranked_meta.py): popularity, genre and language for the maps learnt from,
    # as the site's search gives them for the maps guessed online; and every ranked map not in Songs to guess
    from . import ranked_meta
    ranked = ranked_meta.load()
    for b, x in ranked.items():
        known = meta.setdefault(int(b), {})
        for k in ("playcount", "passcount", "favourites", "set_plays", "genre", "language", "creator", "set"):
            if known.get(k) is None and x.get(k) is not None:
                known[k] = x[k]
    prof = mapvec.load_profiles()
    pbm = dict(prof["beatmaps"])
    for b, x in ranked.items():       # ranked maps outside the top plays: guessed too
        pbm.setdefault(b, x)

    # rows: labelled local ranked maps, and maps from top plays missing from Songs
    rows = []
    local_ids = set()
    for m in db:
        if m.status not in (4, 5) or m.drain_s < 30 or maptypes.too_easy(m.stars.get(0)):
            continue
        local_ids.add(m.beatmap_id)
        for name, mods in MODS.items():
            a = kinds.get(f"{m.md5}:{mods}")
            if not a:
                continue
            api = meta.get(m.beatmap_id, {})
            rows.append({"id": m.beatmap_id, "set": m.set_id, "mods": name, "label": a, "local": True,
                         "stars": m.stars.get(0), "bpm": m.bpm, "circles": m.circles, "sliders": m.sliders,
                         "spinners": m.spinners, "length": m.drain_s, "ar": m.ar, "cs": m.cs, "od": m.od,
                         "artist": m.artist, "title": m.title, "creator": m.creator,
                         "playcount": api.get("playcount"), "passcount": api.get("passcount"),
                         "favourites": api.get("favourites"), "set_plays": api.get("set_plays"),
                         "genre": api.get("genre"), "language": api.get("language")})
    # maps not in Songs, but not those too easy for a type (maptypes.MIN_STARS)
    unknown_ids = {int(b) for b, x in pbm.items() if not maptypes.too_easy(x.get("stars"))} - local_ids
    local_ids |= {m.beatmap_id for m in db if maptypes.too_easy(m.stars.get(0))}
    unknown_ids -= local_ids
    # maps not in Songs whose .osu was downloaded and classified (lowconf.py): labelled like the local ones
    downloaded = json.loads(LOWCONF.read_text()) if LOWCONF.exists() else {}
    for b in unknown_ids:
        x, api = pbm[str(b)], meta.get(b, {})
        got = downloaded.get(str(b), {})
        for name in MODS:
            rows.append({"id": b, "set": x.get("set") or api.get("set"), "mods": name, "label": got.get(name),
                         "local": False, "downloaded": bool(got.get(name)),
                         "stars": x.get("stars"), "bpm": x.get("bpm"), "circles": x.get("circles"),
                         "sliders": x.get("sliders"), "spinners": api.get("spinners") or x.get("spinners") or 0,
                         "length": x.get("length"),
                         "ar": x.get("ar"), "cs": x.get("cs"), "od": x.get("od"), "artist": x.get("artist", ""),
                         "title": x.get("title", ""), "creator": api.get("creator") or x.get("creator", ""),
                         "playcount": api.get("playcount"), "passcount": api.get("passcount"),
                         "favourites": api.get("favourites"), "set_plays": api.get("set_plays"),
                         "genre": api.get("genre"), "language": api.get("language")})
    TRAINING_DIR.mkdir(parents=True, exist_ok=True)
    pickle.dump((rows, dict(scores), prof["plays"]), open(D + "typepred_rows.pkl", "wb"))
    print(f"{sum(r['local'] for r in rows)} labelled rows, {sum(not r['local'] for r in rows)} to predict "
          f"({len(unknown_ids)} beatmaps)")


def group_encoding(rows, keyf, name):
    """Mean labels of the group's maps from OTHER beatmap sets (so a set never sees itself); count too."""
    sums, counts, set_sums, set_counts = defaultdict(lambda: np.zeros(len(TARGETS))), defaultdict(int), \
        defaultdict(lambda: np.zeros(len(TARGETS))), defaultdict(int)
    for r in rows:
        if r["label"] is None:
            continue
        k = keyf(r)
        if not k:
            continue
        y = np.array([r["label"][t] for t in TARGETS])
        sums[(k, r["mods"])] += y
        counts[(k, r["mods"])] += 1
        set_sums[(k, r["mods"], r["set"])] += y
        set_counts[(k, r["mods"], r["set"])] += 1
    out = []
    for r in rows:
        k = keyf(r)
        s, c = sums.get((k, r["mods"])), counts.get((k, r["mods"]), 0)
        if k and c:
            s = s - set_sums.get((k, r["mods"], r["set"]), 0)
            c = c - set_counts.get((k, r["mods"], r["set"]), 0)
        out.append(list(s / c) + [c] if k and c > 0 else [np.nan] * len(TARGETS) + [0])
    return np.array(out, dtype=float), [f"{name} {t}" for t in TARGETS] + [f"{name} maps"]


def player_features(rows, scores, plays):
    """Who plays the map: mods and accuracy of the top plays set on it, and what kind of maps those players
    usually have in their top plays (from the labelled ones, leaving this beatmap set out)."""
    label = {(r["id"], r["mods"]): (r["set"], np.array([r["label"][t] for t in TARGETS])) for r in rows if r["label"]}
    pref_sum, pref_n, per_set = {}, {}, {}
    for user, ps in plays.items():
        s, n, sets = np.zeros(len(TARGETS)), 0, defaultdict(lambda: [np.zeros(len(TARGETS)), 0])
        for p in ps:
            key = (p["beatmap"], "DT" if "DT" in p["mods"] else "NM")
            if key in label:
                st, y = label[key]
                s += y
                n += 1
                sets[st][0] += y
                sets[st][1] += 1
        pref_sum[int(user)], pref_n[int(user)], per_set[int(user)] = s, n, sets
    out = []
    for r in rows:
        sc = scores.get(r["id"], [])
        mods_share = [np.mean([m in mods for _, mods, _, _, _ in sc]) if sc else np.nan for m in ("DT", "HR", "HD", "NC")]
        acc = [a for _, _, a, _, _ in sc if a is not None]
        pp = [p for _, _, _, p, _ in sc if p is not None]
        prefs = []
        for u, mods, _, _, _ in sc:
            if u not in pref_n:
                continue
            s, n = pref_sum[u], pref_n[u]
            own = per_set[u].get(r["set"])
            if own:
                s, n = s - own[0], n - own[1]
            if n >= 3:
                prefs.append(s / n)
        pref = list(np.mean(prefs, axis=0)) if prefs else [np.nan] * len(TARGETS)
        out.append([len(sc), *mods_share, np.mean(acc) if acc else np.nan, np.mean(pp) if pp else np.nan,
                    len(prefs), *pref])
    names = (["top plays", "top DT", "top HR", "top HD", "top NC", "top accuracy", "top pp", "players known"]
             + [f"players' {t}" for t in TARGETS])
    return np.array(out, dtype=float), names


def spec_features(rows):
    out = []
    for r in rows:
        rate = 1.5 if r["mods"] == "DT" else 1.0
        objs = (r["circles"] or 0) + (r["sliders"] or 0) + (r["spinners"] or 0)
        length = (r["length"] or 0) / rate
        out.append([(r["stars"] or np.nan) * (DT_SR if rate > 1 else 1), (r["bpm"] or np.nan) * rate, r["circles"],
                    r["sliders"], r["spinners"], length, r["ar"], r["cs"], r["od"], (r["circles"] or 0) / max(objs, 1),
                    objs / max(length, 1), rate])
    return np.array(out, dtype=float), ["stars", "bpm", "circles", "sliders", "spinners", "length", "ar", "cs", "od",
                                        "circle share", "objects/s", "rate"]


def popularity_features(rows):
    out = [[r["playcount"], r["passcount"], (r["passcount"] or 0) / r["playcount"] if r["playcount"] else np.nan,
            r["favourites"], r["set_plays"], r["genre"], r["language"]] for r in rows]
    return np.array(out, dtype=float), ["playcount", "passcount", "pass rate", "favourites", "set plays", "genre", "language"]


EMBED_KEYS = {"NM": ("NM", "HR", "EZ"), "DT": ("DT", "DTHR", "NC")}
EMBED_K = 10
EMBED_DIMS = 8


def embedding_rows(rows):
    """Row index -> its map+mods vector from the top-play embedding (vocabulary maps only)."""
    mv = mapvec.MapVectors.load()
    at = mv.index()
    out = {}
    for n, r in enumerate(rows):
        for key in EMBED_KEYS[r["mods"]]:
            i = at.get((r["id"], key))
            if i is not None:
                out[n] = mv.vectors[i]
                break
    return out


def embedding_features(rows):
    """Mean labels of the nearest labelled maps in the embedding (other beatmap sets), and its first directions."""
    vec = embedding_rows(rows)
    lab = [n for n in vec if rows[n]["label"] is not None]
    L = np.array([vec[n] for n in lab])
    Ylab = np.array([[rows[n]["label"][t] for t in TARGETS] for n in lab])
    sets = np.array([rows[n]["set"] for n in lab])
    mods = np.array([rows[n]["mods"] for n in lab])
    mean = L.mean(axis=0)
    _, _, vt = np.linalg.svd(L - mean, full_matrices=False)
    out = np.full((len(rows), len(TARGETS) + EMBED_DIMS + 1), np.nan)
    for n, v in vec.items():
        sims = L @ v
        sims[(sets == rows[n]["set"]) | (mods != rows[n]["mods"])] = -9
        near = np.argsort(-sims)[:EMBED_K]
        near = near[sims[near] > -9]
        if len(near):
            out[n, :len(TARGETS)] = Ylab[near].mean(axis=0)
        out[n, len(TARGETS):-1] = (v - mean) @ vt[:EMBED_DIMS].T
        out[n, -1] = float(sims[near].mean()) if len(near) else np.nan
    names = [f"embedding neighbours' {t}" for t in TARGETS] + [f"embedding dir {k}" for k in range(EMBED_DIMS)] + ["neighbour similarity"]
    return out, names


def blocks(rows, scores, plays):
    """The factor groups, in the user's order."""
    spec = spec_features(rows)
    song = group_encoding(rows, lambda r: song_key(r["artist"], r["title"]), "song")
    players = player_features(rows, scores, plays)
    mapper = group_encoding(rows, lambda r: norm(r["creator"]), "mapper")
    artist = group_encoding(rows, lambda r: norm(r["artist"]), "artist")
    pop = popularity_features(rows)
    return [("specs", spec), ("+ song", song), ("+ players & mods", players), ("+ mapper", mapper),
            ("+ artist", artist), ("+ play count", pop), ("+ embedding", embedding_features(rows))]


FAMILY = "Finger control/burst"


def family(cat: str) -> str:
    """Burst and finger control are one kind (maptypes names it "Finger control/burst"); older labels that still
    say burst or finger control alone are brought to it."""
    return re.sub(r"(?i)finger control(?!/burst)|(?<!/)burst",
                  lambda m: FAMILY if m.group(0)[0].isupper() else FAMILY.lower(), cat)


def category_of(y):
    a = dict(zip(TARGETS, y))
    a.update({"ar": 9.5, "density": 5.0})
    return family(maptypes.category(a))


def main_kind(cat: str) -> str:
    return cat.split(" + ")[0].replace(", aim control", "")


def evaluate():
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.model_selection import GroupKFold
    rows, scores, plays = pickle.load(open(D + "typepred_rows.pkl", "rb"))
    bl = blocks(rows, scores, plays)
    lab = np.array([r["label"] is not None for r in rows])
    Y = np.array([[r["label"][t] for t in TARGETS] if r["label"] else [np.nan] * len(TARGETS) for r in rows])
    groups = np.array([r["set"] or r["id"] for r in rows])
    idx = np.where(lab)[0]
    true_cat = [category_of(Y[i]) for i in idx]
    in_top = np.array([bool(scores.get(rows[i]["id"])) for i in idx])
    has_vec = np.array([~np.isnan(bl[-1][1][0][i, 0]) for i in idx])
    print(f"with an embedding vector: {has_vec.sum()} ({has_vec.mean():.0%})")
    print(f"labelled rows {len(idx)} ({in_top.mean():.0%} with top plays on them)")
    X = None
    for name, (Xb, _) in bl:
        X = Xb if X is None else np.hstack([X, Xb])
        pred = np.zeros((len(idx), len(TARGETS)))
        for tr, te in GroupKFold(n_splits=5).split(idx, groups=groups[idx]):
            for j in range(len(TARGETS)):
                model = HistGradientBoostingRegressor(max_iter=300, learning_rate=0.08, random_state=0)
                model.fit(X[idx[tr]], Y[idx[tr], j])
                pred[te, j] = model.predict(X[idx[te]])
        cats = [category_of(p) for p in pred]
        exact = np.mean([a == b for a, b in zip(cats, true_cat)])
        main = np.mean([main_kind(a) == main_kind(b) for a, b in zip(cats, true_cat)])
        # aim control against the map's usual score for its star rating (the label's "aim expected")
        aim = np.mean([(p[-1] >= maptypes.AIM_CONTROL_RATIO * rows[i]["label"]["aim expected"])
                       == maptypes.has_aim_control(rows[i]["label"]) for p, i in zip(pred, idx)])
        mae = np.abs(pred - Y[idx]).mean(axis=0)
        top = np.mean([a == b for a, b, t in zip(cats, true_cat, in_top) if t])
        vec_exact = np.mean([a == b for a, b, t in zip(cats, true_cat, has_vec) if t])
        vec_main = np.mean([main_kind(a) == main_kind(b) for a, b, t in zip(cats, true_cat, has_vec) if t])
        print(f"{name:18} category {exact:.0%} (main kind {main:.0%}; maps with top plays {top:.0%}; vocabulary maps "
              f"{vec_exact:.0%}, main kind {vec_main:.0%}) | aim control flag {aim:.0%} "
              f"| share error " + " ".join(f"{t[:6]} {e:.3f}" for t, e in zip(TARGETS[:6], mae)))
    base = max(set(true_cat), key=true_cat.count)
    print(f"always '{base}': {np.mean([c == base for c in true_cat]):.0%}")





# --- step 3: types only where the model is confident ------------------------------------------

MIN_CLASS = 30
THRESHOLDS = (0.4, 0.5, 0.6, 0.7, 0.8, 0.9)


def feature_matrix(rows, scores, plays):
    bl = blocks(rows, scores, plays)[:-1]     # without the embedding: it added nothing
    return np.hstack([b[1][0] for b in bl])


def fit_classifier(X, y):
    from sklearn.ensemble import HistGradientBoostingClassifier
    model = HistGradientBoostingClassifier(max_iter=300, learning_rate=0.08, random_state=0)
    model.fit(X, y)
    return model


def labels_of(rows, idx, Y):
    """Labels for the three guesses, and which rows each guess learns from (borderline labels left out)."""
    exact = np.array([category_of(Y[i]) for i in idx])
    counts = {c: (exact == c).sum() for c in set(exact)}
    exact = np.array([c if counts[c] >= MIN_CLASS else "rare" for c in exact])
    main = np.array([main_kind(c) for c in exact])
    aim = np.array([maptypes.has_aim_control(rows[i]["label"]) for i in idx])
    border = [maptypes.borderline(rows[i]["label"]) for i in idx]
    learn = {"exact": np.array(["hybrid" not in b for b in border]), "main": np.ones(len(idx), bool),
             "aim": np.array(["aim control" not in b for b in border])}
    return exact, main, aim, learn


def confidence():
    from sklearn.model_selection import GroupKFold
    rows, scores, plays = pickle.load(open(D + "typepred_rows.pkl", "rb"))
    X = feature_matrix(rows, scores, plays)
    Y = np.array([[r["label"][t] for t in TARGETS] if r["label"] else [np.nan] * len(TARGETS) for r in rows])
    idx = np.where([r["label"] is not None for r in rows])[0]
    groups = np.array([r["set"] or r["id"] for r in rows])[idx]
    exact, main, aim, learn = labels_of(rows, idx, Y)
    in_top = np.array([bool(scores.get(rows[i]["id"])) for i in idx])
    print(f"borderline labels left out: exact {np.mean(~learn['exact']):.0%}, aim control {np.mean(~learn['aim']):.0%}")
    oof = {}
    for name, y in (("exact", exact), ("main", main), ("aim", aim)):
        proba = np.zeros(len(idx)); guess = np.empty(len(idx), dtype=object)
        for tr, te in GroupKFold(n_splits=5).split(idx, groups=groups):
            tr = tr[learn[name][tr]]
            model = fit_classifier(X[idx[tr]], y[tr])
            p = model.predict_proba(X[idx[te]])
            proba[te] = p.max(axis=1)
            guess[te] = model.classes_[p.argmax(axis=1)]
        oof[name] = (guess, proba, y)
    for subset, mask in (("all labelled maps", np.ones(len(idx), bool)), ("maps with top plays", in_top),
                         ("maps without top plays", ~in_top)):
        print(f"\n{subset} ({mask.sum()}):")
        for name in ("exact", "main", "aim"):
            guess, proba, y = oof[name]
            parts = []
            for th in THRESHOLDS:
                ok = mask & (proba >= th)
                parts.append(f">={th:.1f}: {np.mean(guess[ok] == y[ok]):.0%} right on {ok.sum() / mask.sum():.0%}")
            print(f"  {name:6} overall {np.mean(guess[mask] == y[mask]):.0%} | " + " | ".join(parts))
    pickle.dump(oof, open(D + "typepred_oof.pkl", "wb"))


def predict(th_exact: float, th_main: float, th_aim: float):
    """Train on every labelled map and give the maps not in Songs a type where the model is confident."""
    rows, scores, plays = pickle.load(open(D + "typepred_rows.pkl", "rb"))
    X = feature_matrix(rows, scores, plays)
    Y = np.array([[r["label"][t] for t in TARGETS] if r["label"] else [np.nan] * len(TARGETS) for r in rows])
    idx = np.where([r["label"] is not None for r in rows])[0]
    unk = np.where([r["label"] is None for r in rows])[0]
    exact, main, aim, learn = labels_of(rows, idx, Y)
    out = {}
    probs = {}
    for name, y in (("exact", exact), ("main", main), ("aim", aim)):
        model = fit_classifier(X[idx[learn[name]]], y[learn[name]])
        p = model.predict_proba(X[unk])
        probs[name] = (model.classes_[p.argmax(axis=1)], p.max(axis=1))
    for n, i in enumerate(unk):
        r = rows[i]
        ge, pe = probs["exact"][0][n], probs["exact"][1][n]
        gm, pm = probs["main"][0][n], probs["main"][1][n]
        ga, pa = probs["aim"][0][n], probs["aim"][1][n]
        if pe >= th_exact and ge != "rare":
            kind, sure = ge, "type"
        elif pm >= th_main and gm != "rare":
            kind, sure = gm, "main kind"
        else:
            kind, sure = None, "unsure"
        control = bool(ga) if pa >= th_aim else None
        p = {"kind": kind, "confidence": sure, "p_type": round(float(pe), 3), "guess": str(ge),
             "p_main": round(float(pm), 3), "main": str(gm), "aim control": control, "p_aim": round(float(pa), 3)}
        # reading: the map's AR is known without the .osu, applied on top of the guess
        out[f"{r['id']}:{r['mods']}"] = typeguess.mark_reading(
            p, typeguess.is_reading(p, r["ar"], r["cs"], r["od"], r["mods"], r["stars"]), r["bpm"], r["mods"],
            r["cs"])
    for i in idx:
        r = rows[i]
        if r.get("downloaded"):
            a = r["label"]
            # the real type, with the map's effective AR (downloaded maps were read without it)
            from .advice import effective_ar
            from .difficulty import Difficulty
            from .mods import clock_rate
            mods = MODS[r["mods"]]
            ar = (effective_ar(Difficulty.from_map(r["cs"] or 4, r["ar"], r["od"] or 8, mods), clock_rate(mods))
                  if r["ar"] is not None else 9.5)
            full = dict(a, ar=ar, density=5.0, stars=r["stars"] or 0.0, bpm=(r["bpm"] or 0) * clock_rate(mods),
                        cs=maptypes.played_cs(r["cs"], mods))
            out[f"{r['id']}:{r['mods']}"] = {"kind": maptypes.category(full), "reading": maptypes.has_reading(full),
                                             "speed": maptypes.has_speed(full),
                                             "precision": maptypes.has_precision(full),
                                             "confidence": "downloaded", "aim control": maptypes.has_aim_control(a),
                                             "p_type": 1.0, "p_main": 1.0, "p_aim": 1.0}
    path = CACHE_DIR / "predicted_types.json"
    path.write_text(json.dumps(out), encoding="utf-8")
    from collections import Counter
    c = Counter(v["confidence"] for v in out.values())
    print(f"{len(out)} map+mods: " + ", ".join(f"{k} {v}" for k, v in c.items()) + f"  (saved in {path})")
    print("types:", Counter(v["kind"] for v in out.values() if v["kind"]).most_common(12))


def export():
    """The three classifiers trained on every labelled map, and the group tables (song, mapper, artist) the
    app needs to guess the type of any map from what the osu! API says about it (osu_coach/typeguess.py)."""
    rows, scores, plays = pickle.load(open(D + "typepred_rows.pkl", "rb"))
    X = feature_matrix(rows, scores, plays)
    Y = np.array([[r["label"][t] for t in TARGETS] if r["label"] else [np.nan] * len(TARGETS) for r in rows])
    idx = np.where([r["label"] is not None for r in rows])[0]
    exact, main, aim, learn = labels_of(rows, idx, Y)
    models = {name: fit_classifier(X[idx[learn[name]]], y[learn[name]])
              for name, y in (("exact", exact), ("main", main), ("aim", aim))}
    tables = {}
    for name, keyf in (("song", lambda r: song_key(r["artist"], r["title"])), ("mapper", lambda r: norm(r["creator"])),
                       ("artist", lambda r: norm(r["artist"]))):
        sums, counts = defaultdict(lambda: np.zeros(len(TARGETS))), defaultdict(int)
        for i in idx:
            k = keyf(rows[i])
            if k:
                sums[(k, rows[i]["mods"])] += Y[i]
                counts[(k, rows[i]["mods"])] += 1
        tables[name] = {f"{k}|{m}": (list(sums[(k, m)] / c), c) for (k, m), c in counts.items()}
    out = CACHE_DIR / "typeguess.pkl"
    pickle.dump({"models": models, "tables": tables, "targets": TARGETS, "features": X.shape[1],
                 "dt_stars": DT_SR}, open(out, "wb"))
    print(f"saved {out} ({X.shape[1]} features, {len(idx)} labelled rows)")


def label(progress=None):
    """Map types of every ranked osu!standard map in Songs, NM and DT (cached in the maptypes cache)."""
    from .locate import songs_dir
    osu = default_osu_dir()
    maps = [m for m in read_osu_db(osu / "osu!.db") if m.mode == 0 and m.status in (4, 5) and m.drain_s >= 30]
    items = [(m, mods) for m in maps for mods in (0, 64)]
    print(f"{len(maps)} ranked maps, {len(items)} map+mods", flush=True)
    kinds = maptypes.types(songs_dir(osu), items, progress)
    print(f"\n{sum(v is not None for v in kinds.values())} classified", flush=True)


def download(n: int = 100, min_stars: float = 0.0):
    """The n beatmaps whose guessed type is least sure (from `min_stars`, nomod): their .osu downloaded (osu! API,
    one request a second) and labelled for real (NM and DT), so the next training learns from them."""
    from . import api, ranked_meta
    from . import recommend as rc
    from .collect import map_samples
    pred = rc.load_predicted()
    # nomod stars: the top plays' maps, else every ranked map's metadata
    pbm = {**ranked_meta.load(), **mapvec.load_profiles()["beatmaps"]}
    done = json.loads(LOWCONF.read_text()) if LOWCONF.exists() else {}
    unsure = sorted((v["p_main"], k.split(":")[0]) for k, v in pred.items()
                    if k.endswith(":NM") and v["confidence"] == "unsure" and k.split(":")[0] not in done
                    and (pbm.get(k.split(":")[0], {}).get("stars") or 0.0) >= min_stars)
    ids = [b for _, b in unsure][:n]
    client = api.OsuApi()
    for k, bid in enumerate(ids, 1):
        try:
            path = client.osu_file(int(bid))
            stars = pbm.get(bid, {}).get("stars") or 0.0
            nm = map_samples(str(path), 0)
            done[bid] = {"stars": stars, "NM": maptypes.analyse(nm, 9.5, nm, stars),
                         "DT": maptypes.analyse(map_samples(str(path), 64), 9.5, nm, stars)}
        except Exception as e:     # a map that can't be downloaded or read is skipped
            done[bid] = {"error": str(e)}
        TRAINING_DIR.mkdir(parents=True, exist_ok=True)
        LOWCONF.write_text(json.dumps(done))
        print(f"\r  downloaded {k}/{len(ids)}", end="", flush=True)
    print(f"\n{sum('error' not in v for v in done.values())} downloaded maps labelled in all")


SURE = (0.7, 0.7, 0.8)     # confidence for the exact type, the main kind and aim control (see confidence())


def retrain(relabel: bool = True, evaluate: bool = False, download_unsure: int = 0, progress=None,
            fetch_ranked: bool = False, unsure_min_stars: float = 0.0):
    """Every step, in order; `download_unsure` maps are downloaded after the guesses and the model is
    trained again with them; `fetch_ranked` reads every ranked map's metadata from the osu! API first."""
    if fetch_ranked:
        from . import ranked_meta
        print(f"\n{len(ranked_meta.fetch(progress))} ranked maps' metadata", flush=True)
    if relabel:
        label(progress)
    for rnd in range(2 if download_unsure else 1):
        build()
        if evaluate:
            confidence()
        predict(*SURE)
        export()
        if download_unsure and rnd == 0:
            download(download_unsure, unsure_min_stars)

