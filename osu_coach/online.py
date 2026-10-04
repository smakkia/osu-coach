"""Candidates from osu! (API v2) for the recommender: maps the player hasn't downloaded yet.

Two sources per skill:
  search          the site's search with filters: the player's star range, and for tapping the
                  BPM window where their stream timing starts to give (divided by 1.5 for maps to
                  play with DT); scanning many result pages is cheap, so the results are ranked
                  by what the API already says (share of circles, the mapper's tags) and only the
                  most promising are downloaded
  co-occurrence   players who show up on the leaderboards of several reference maps of the skill
                  (the ones the player named) are specialists of it; the maps in their best scores
                  are likely to be of the same kind (counted by how many of them have each map)

Only the .osu of each candidate is downloaded (no audio), then the maps go through the same
pipeline as the ones in Songs: content, purity, difficulty for the player.
"""

from collections import Counter
from dataclasses import dataclass

from .api import OSU_FILES, ApiError, OsuApi, OsuFiles
from .mapdb import MapInfo
from .mods import Mods

# the reference maps the player named for each skill (beatmapset ids); the hardest difficulty in Songs is used
SEEDS = {
    "tap": (672013, 482382, 2348607, 991300, 367631),                 # Vertex Delta, Shinbatsu, The Deceit
    "flow": (586121, 905621, 1108263, 889855, 934878, 953444, 1821328, 989540, 1791832),  # HONESTY, RAISE MY SWORD, REASON
    "finger": (670068, 319890, 935733),                                 # #sawg, She Runs, benzo
}
DT_SR_FACTOR = 1.46          # nomod -> DT star rating: the median ratio over ranked maps (osu!.db)
DT = int(Mods.DoubleTime)
MIN_SHARED_BOARDS = 2        # a specialist appears on at least this many reference leaderboards
MAX_SPECIALISTS = 15
API_STATUS = {1: 4, 2: 5, 4: 7}   # API "ranked" code -> osu!.db status (ranked, approved, loved)


# words in a mapset's tags that hint at each skill (a bonus only: tags are free text)
TAG_HINTS = {
    "tap": ("stream", "streams", "deathstream", "burst", "bursts", "speed"),
    "flow": ("stream", "streams", "spaced", "flow", "deathstream"),
    "finger": ("tech", "technical", "breakcore", "rhythm", "finger", "control", "burst", "bursts"),
}


@dataclass
class Found:
    info: MapInfo
    mods: int
    votes: int = 0           # specialists with this map in their best scores (0 = from the search)
    prior: float = 0.0       # how likely the map is to hold the skill, before downloading it
    bm: dict | None = None   # what the API says about the beatmap and its set (to guess its type)
    bs: dict | None = None


def _prior(key: str, bm: dict, bs: dict) -> float:
    """Guess from what the API says about a map: stream and finger maps are nearly all circles,
    and the mapper's tags may say it outright."""
    objects = bm.get("count_circles", 0) + bm.get("count_sliders", 0)
    circles = bm.get("count_circles", 0) / objects if objects else 0.0
    tags = set(str(bs.get("tags", "")).lower().split())
    return circles + (0.5 if tags & set(TAG_HINTS.get(key, ())) else 0.0)


def _bpm_fits(key: str, bm: dict, mods: int, tap_window) -> bool:
    """Tapping: the song's BPM (x1.5 with DT) must be in the player's window; streams at 1/4 of it."""
    if key != "tap" or not tap_window:
        return True
    bpm = float(bm.get("bpm") or 0) * (1.5 if mods & DT else 1.0)
    return tap_window[0] - 3 <= bpm <= tap_window[1] + 3


def _info(bm: dict, bs: dict) -> MapInfo:
    sr = float(bm["difficulty_rating"])
    return MapInfo(bm.get("checksum") or "", bs["artist"], bs["title"], bs.get("creator", ""), bm["version"],
                   "", API_STATUS.get(bm.get("ranked", 0), 2), bm.get("count_circles", 0), bm.get("count_sliders", 0),
                   bm.get("count_spinners", 0), float(bm.get("ar", 0)), float(bm.get("cs", 0)),
                   float(bm.get("accuracy", 0)), int(bm.get("hit_length", 0)), int(bm.get("total_length", 0)) * 1000,
                   int(bm["id"]), int(bm.get("beatmapset_id") or bs["id"]), 0, True, 0,
                   {0: sr, DT: sr * DT_SR_FACTOR}, float(bm.get("bpm") or 0))


def _queries(key: str, band: tuple[float, float], tap_window, mod_sets) -> list[tuple[str, int]]:
    lo, hi = band
    out = []
    for mods in mod_sets:
        rate = 1.5 if mods & DT else 1.0
        stars = f"stars>={lo / (DT_SR_FACTOR if rate > 1 else 1):.2f} stars<={hi / (1.25 if rate > 1 else 1):.2f}"
        if key == "tap" and tap_window:
            q = f"bpm>={tap_window[0] / rate:.0f} bpm<={tap_window[1] / rate:.0f} {stars}"
        elif key in ("flow", "finger"):
            q = f"bpm>={165 / rate:.0f} {stars}"
        else:
            q = stars
        if mods in (0, DT):
            out.append((q, mods))
    return out


def _accept(info: MapInfo, mods: int, band, skip_md5: set[str]) -> bool:
    s = info.stars.get(DT if mods & DT else 0)
    return (info.md5 and info.md5 not in skip_md5 and s is not None and band[0] <= s <= band[1]
            and info.drain_s >= 30)


def search(api: OsuApi, key: str, band, tap_window, mod_sets, skip_md5: set[str], pages: int) -> list[Found]:
    """Search results, most promising first (see _prior); scanning pages is cheap, downloading isn't."""
    found = []
    for query, mods in _queries(key, band, tap_window, mod_sets):
        cursor = None
        for _ in range(pages):
            page = api.search(query, cursor=cursor)
            for bs in page.get("beatmapsets", []):
                for bm in bs.get("beatmaps", []):
                    if bm.get("mode_int", 0) != 0 or not _bpm_fits(key, bm, mods, tap_window):
                        continue
                    info = _info(bm, bs)
                    if _accept(info, mods, band, skip_md5):
                        found.append(Found(info, mods, prior=_prior(key, bm, bs), bm=bm, bs=bs))
            cursor = page.get("cursor_string")
            if not cursor:
                break
    return sorted(found, key=lambda f: -f.prior)


def co_occurrence(api: OsuApi, key: str, local: dict[int, MapInfo], band, mod_sets, skip_md5: set[str],
                  tap_window=None, progress=None) -> list[Found]:
    """Maps in the best scores of players who top several of the skill's reference maps."""
    seeds = [max((m for m in local.values() if m.set_id == sid and m.mode == 0),
                 key=lambda m: m.stars.get(0, 0), default=None) for sid in SEEDS.get(key, ())]
    seeds = [m for m in seeds if m and m.beatmap_id > 0]
    boards = Counter()
    for m in seeds:
        for score in api.leaderboard(m.beatmap_id):
            boards[score["user_id"]] += 1
    specialists = [u for u, n in boards.most_common(MAX_SPECIALISTS) if n >= MIN_SHARED_BOARDS]
    votes: Counter = Counter()
    infos: dict[tuple[str, int], MapInfo] = {}
    raw: dict[tuple[str, int], tuple[dict, dict]] = {}
    seed_ids = {m.beatmap_id for m in seeds}
    for n, user in enumerate(specialists, 1):
        for score in api.best_scores(user):
            bm, bs = score["beatmap"], score["beatmapset"]
            if bm["id"] in seed_ids or bm.get("mode_int", 0) != 0:
                continue
            acronyms = {x["acronym"] if isinstance(x, dict) else x for x in score.get("mods", [])}
            mods = DT if acronyms & {"DT", "NC"} else 0
            if mods not in mod_sets or not _bpm_fits(key, bm, mods, tap_window):
                continue
            info = _info(bm, bs)
            if _accept(info, mods, band, skip_md5):
                infos[(info.md5, mods)] = info
                raw[(info.md5, mods)] = (bm, bs)
                votes[(info.md5, mods)] += 1
        if progress:
            progress(f"specialists ({key})", n, len(specialists))
    return [Found(infos[k], k[1], v, bm=raw[k][0], bs=raw[k][1]) for k, v in votes.most_common()]


def discover(api: OsuApi, keys: list[str], local_maps: list[MapInfo], band, tap_window, mod_sets,
             skip_md5: set[str], pages: int = 3, per_skill: int = 150, progress=None,
             verdict=None) -> list[tuple[MapInfo, int]]:
    """Online candidates for these skills, their .osu downloaded; maps in `skip_md5` (played, or
    already in Songs) are left out. `verdict(found, key)` is the guessed type's answer before
    downloading: False drops the map, True puts it first, None (unsure or not guessed) leaves it be."""
    local = {m.beatmap_id: m for m in local_maps if m.beatmap_id > 0}
    chosen: dict[tuple[str, int], Found] = {}
    dropped = 0
    for key in keys:
        window = tap_window if key == "tap" else None
        picks = co_occurrence(api, key, local, band, mod_sets, skip_md5, window, progress) if key in SEEDS else []
        picks += search(api, key, band, window, mod_sets, skip_md5, pages)
        if verdict:
            said = [verdict(f, key) for f in picks]
            dropped += sum(v is False for v in said)
            # sure yes first, then the unsure ones in their order; sure no out (a stable sort keeps the order)
            picks = [f for f, v in sorted(zip(picks, said), key=lambda fv: fv[1] is not True) if v is not False]
        taken = 0
        for f in picks:
            if taken >= per_skill:
                break
            if (f.info.md5, f.mods) not in chosen:
                chosen[(f.info.md5, f.mods)] = f
                taken += 1
    if verdict and progress:
        progress("left out by the guessed map type", dropped, dropped)
    out = []
    todo = list(chosen.values())
    files = OsuFiles(api)    # from the mirrors, several at once (osu.ppy.sh, one a second, is the last resort)
    try:
        pending = [files.submit(f.info.beatmap_id, f.info.md5) for f in todo]
        for n, (f, fut) in enumerate(zip(todo, pending), 1):
            try:
                f.info.path = str(fut.result())
            except (ApiError, OSError):
                continue
            out.append((f.info, f.mods))
            if progress and n % 25 == 0:
                progress("downloading .osu", n, len(todo))
    finally:
        files.close()
    return out


def is_online(info: MapInfo) -> bool:
    return str(OSU_FILES) in info.path
