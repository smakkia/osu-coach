"""Players' best scores, to learn what kind of map a map is from who has it in their top plays.

Players are sampled across rank bands: the global ranking reaches rank 10,000, beyond that
country rankings are used (each entry carries the player's global rank). For each player the
100 best scores are kept as (beatmap, mods, pp); a map with DT counts as a different item from
the same map without, since it plays differently.
"""

import json
import random
from dataclasses import asdict, dataclass

from .api import ApiError, OsuApi
from .locate import CACHE_DIR

PROFILES_PATH = CACHE_DIR / "profiles.json"
GLOBAL_RANKING_MAX = 10_000
PAGE = 50
RANK_BANDS = ((1, 1_000), (1_000, 10_000), (10_000, 30_000), (30_000, 60_000), (60_000, 100_000))
COUNTRIES = ("US", "RU", "DE", "PL", "BR", "FR", "JP", "KR", "CN", "GB", "CA", "PH", "ID", "TW", "UA", "VN",
             "AR", "CL", "MX", "TH", "IT", "ES", "AU", "NL", "SE", "FI", "MY", "SG", "HK", "TR", "NO", "CZ")


@dataclass
class Player:
    id: int
    name: str
    country: str
    rank: int
    pp: float


def _players_from(page: dict) -> list[Player]:
    out = []
    for r in page.get("ranking", []):
        u = r.get("user", {})
        if r.get("global_rank") and u.get("id"):
            out.append(Player(u["id"], u.get("username", "?"), u.get("country_code", "?"),
                              int(r["global_rank"]), float(r.get("pp") or 0)))
    return out


def sample_players(api: OsuApi, per_band: int, seed: int = 1, progress=None) -> list[Player]:
    """`per_band` players at random ranks in each of RANK_BANDS."""
    rng = random.Random(seed)
    chosen: dict[int, Player] = {}
    for lo, hi in RANK_BANDS:
        targets = sorted(rng.randint(lo, hi) for _ in range(per_band))
        if hi <= GLOBAL_RANKING_MAX:
            for t in targets:
                page = _players_from(api.get("/rankings/osu/performance", {"cursor[page]": (t - 1) // PAGE + 1}))
                free = [p for p in page if p.id not in chosen]
                if free:
                    p = min(free, key=lambda p: abs(p.rank - t))
                    chosen[p.id] = p
        else:
            pool: list[Player] = []
            tries = 0
            # random country pages until the band is well covered, then the player nearest each target
            while tries < per_band * 3 and len([p for p in pool if lo <= p.rank < hi]) < per_band * 5:
                tries += 1
                cc = rng.choice(COUNTRIES)
                try:
                    page = api.get("/rankings/osu/performance", {"country": cc, "cursor[page]": rng.randint(1, 200)})
                except ApiError:
                    continue
                pool += [p for p in _players_from(page) if lo <= p.rank < hi]
            for t in targets:
                free = [p for p in pool if p.id not in chosen]
                if free:
                    p = min(free, key=lambda p: abs(p.rank - t))
                    chosen[p.id] = p
        if progress:
            progress(f"players {lo}-{hi}", len(chosen), per_band * len(RANK_BANDS))
    return sorted(chosen.values(), key=lambda p: p.rank)


def _mods_key(score: dict) -> str:
    """The mods that change what a map is: DT/NC and HT (speed), HR and EZ (size, AR)."""
    acronyms = {m["acronym"] if isinstance(m, dict) else m for m in score.get("mods", [])}
    key = [m for m in ("DT", "HT", "HR", "EZ") if m in acronyms or (m == "DT" and "NC" in acronyms)]
    return "".join(key) or "NM"


def top_plays(api: OsuApi, players: list[Player], progress=None) -> dict:
    """{player id: [{beatmap, set, mods, pp, stars, bpm, ...}]} plus the beatmaps seen."""
    plays, beatmaps = {}, {}
    for n, p in enumerate(players, 1):
        try:
            scores = api.best_scores(p.id, limit=100)
        except ApiError:
            continue
        rows = []
        for s in scores:
            bm, bs = s.get("beatmap") or {}, s.get("beatmapset") or {}
            if bm.get("mode_int", 0) != 0:
                continue
            rows.append({"beatmap": bm["id"], "mods": _mods_key(s), "pp": s.get("pp") or 0.0})
            beatmaps.setdefault(bm["id"], {
                "set": bm.get("beatmapset_id"), "md5": bm.get("checksum"), "artist": bs.get("artist"),
                "title": bs.get("title"), "version": bm.get("version"), "stars": bm.get("difficulty_rating"),
                "bpm": bm.get("bpm"), "length": bm.get("hit_length"), "circles": bm.get("count_circles"),
                "sliders": bm.get("count_sliders"), "ar": bm.get("ar"), "cs": bm.get("cs"), "od": bm.get("accuracy")})
        plays[str(p.id)] = rows
        if progress:
            progress("top plays", n, len(players))
    return {"players": [asdict(p) for p in players], "plays": plays, "beatmaps": beatmaps}


def save(data: dict):
    PROFILES_PATH.parent.mkdir(parents=True, exist_ok=True)
    PROFILES_PATH.write_text(json.dumps(data), encoding="utf-8")


def load() -> dict | None:
    try:
        return json.loads(PROFILES_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
