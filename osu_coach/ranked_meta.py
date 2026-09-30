"""What the osu! API says about every ranked osu!standard beatmap: specs, mapper, popularity, genre, language.

Read from the site's search, 50 beatmap sets a page (one request a second), and kept in the cache. The map type
guesser (typepred.py) uses it so that the popularity of the maps it learns from is known as it is for the maps
it guesses online, and to guess the type of every ranked map, not only those in players' top plays.
"""

import json

from .locate import CACHE_DIR

RANKED_META = CACHE_DIR / "ranked_meta.json"


def load() -> dict[str, dict]:
    """Beatmap id -> metadata, as typepred's rows want it; empty when never fetched."""
    try:
        return json.loads(RANKED_META.read_text(encoding="utf-8"))["maps"]
    except (OSError, ValueError, KeyError):
        return {}


def _row(bm: dict, bs: dict) -> dict:
    return {"set": bs.get("id"), "md5": bm.get("checksum"), "artist": bs.get("artist", ""), "title": bs.get("title", ""),
            "version": bm.get("version", ""), "creator": bs.get("creator", ""), "creator_id": bs.get("user_id"),
            "stars": bm.get("difficulty_rating"), "bpm": bm.get("bpm"), "circles": bm.get("count_circles"),
            "sliders": bm.get("count_sliders"), "spinners": bm.get("count_spinners"), "length": bm.get("hit_length"),
            "ar": bm.get("ar"), "cs": bm.get("cs"), "od": bm.get("accuracy"), "status": bm.get("ranked"),
            "playcount": bm.get("playcount"), "passcount": bm.get("passcount"),
            "favourites": bs.get("favourite_count"), "set_plays": bs.get("play_count"),
            "genre": bs.get("genre_id"), "language": bs.get("language_id")}


def fetch(progress=None) -> dict[str, dict]:
    """Every ranked osu!standard beatmap from the site's search (osu! API), one ranked year at a time (a search
    pages through at most 10000 sets); pages already fetched this week come from the API cache, so an
    interrupted run resumes quickly."""
    import datetime
    from .api import OsuApi
    client = OsuApi()
    maps, pages = {}, 0
    for year in range(2007, datetime.date.today().year + 1):
        query, cursor = f"ranked>={year}-01-01 ranked<{year + 1}-01-01", None
        while True:
            page = client.get("/beatmapsets/search", {"m": 0, "s": "ranked", "nsfw": "true", "q": query,
                                                      "cursor_string": cursor})
            pages += 1
            for bs in page.get("beatmapsets", []):
                for bm in bs.get("beatmaps", []):
                    if bm.get("mode_int", 0) == 0:
                        maps[str(bm["id"])] = _row(bm, bs)
            if progress:
                progress(f"ranked maps, {year}", pages, len(maps))
            cursor = page.get("cursor_string")
            if not cursor:
                break
    RANKED_META.parent.mkdir(parents=True, exist_ok=True)
    RANKED_META.write_text(json.dumps({"maps": maps}), encoding="utf-8")
    return maps
