"""osu! API v2 client: OAuth client credentials, polite rate limit, on-disk cache.

Credentials come from the player's own OAuth application (osu! settings > OAuth) and live in
API_PATH, outside the project. Only public data is read: beatmap searches, leaderboards,
players' best scores, and .osu files (no audio) to analyse maps that aren't in Songs.
"""

import hashlib
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from .locate import CACHE_DIR

API_PATH = CACHE_DIR / "api.json"
RESPONSE_CACHE = CACHE_DIR / "api_cache"
OSU_FILES = CACHE_DIR / "osu_files"
BASE = "https://osu.ppy.sh/api/v2"
TOKEN_URL = "https://osu.ppy.sh/oauth/token"
MIN_INTERVAL_S = 1.0          # osu! asks for at most ~60 requests a minute
CACHE_TTL_S = 7 * 24 * 3600   # searches, leaderboards and best scores change slowly enough
USER_AGENT = "osu-coach (personal replay analysis)"
# .osu mirrors, tried in turn, with how many downloads each takes at once: much faster than osu.ppy.sh at one a
# second, which stays the last resort; every file is checked against the beatmap's MD5
OSU_MIRRORS = (("https://osu.direct/api/osu/{id}", 3), ("https://catboy.best/osu/{id}", 3))
MIRROR_COOLDOWN_S = 15.0      # a mirror that says "too many requests" rests this long (or its Retry-After)


class ApiError(Exception):
    pass


def load_credentials() -> dict:
    try:
        return json.loads(API_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_credentials(data: dict):
    API_PATH.parent.mkdir(parents=True, exist_ok=True)
    API_PATH.write_text(json.dumps(data), encoding="utf-8")


class OsuApi:
    def __init__(self):
        self.creds = load_credentials()
        if not self.creds.get("client_id") or not self.creds.get("client_secret"):
            raise ApiError("no osu! API credentials: create an OAuth application in your osu! settings, then run "
                           "`config --api-client-id <id> --api-client-secret`")
        self._last = 0.0
        self._lock = threading.Lock()

    # --- plumbing -------------------------------------------------------------------

    def _wait(self):
        with self._lock:     # requests can come from several threads (page prefetch, .osu downloads)
            delay = MIN_INTERVAL_S - (time.monotonic() - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()

    def _request(self, req: urllib.request.Request, retries: int = 3) -> bytes:
        for attempt in range(retries):
            self._wait()
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return resp.read()
            except urllib.error.HTTPError as e:
                if e.code == 429 and attempt < retries - 1:  # rate limited: back off
                    time.sleep(float(e.headers.get("Retry-After", 30)))
                    continue
                if e.code == 401 and attempt < retries - 1 and "Authorization" in req.headers:
                    self.creds.pop("token", None)  # token expired early: get a new one
                    req.add_header("Authorization", f"Bearer {self._token()}")
                    continue
                raise ApiError(f"{req.full_url.split('?')[0]}: HTTP {e.code}") from e
            except urllib.error.URLError as e:
                if attempt < retries - 1:
                    time.sleep(5)
                    continue
                raise ApiError(f"{req.full_url.split('?')[0]}: {e.reason}") from e
        raise ApiError("too many retries")

    def _token(self) -> str:
        if self.creds.get("token") and self.creds.get("expires", 0) > time.time() + 60:
            return self.creds["token"]
        body = urllib.parse.urlencode({"client_id": self.creds["client_id"],
                                       "client_secret": self.creds["client_secret"],
                                       "grant_type": "client_credentials", "scope": "public"}).encode()
        req = urllib.request.Request(TOKEN_URL, data=body, method="POST",
                                     headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                              "Content-Type": "application/x-www-form-urlencoded"})
        data = json.loads(self._request(req))
        self.creds["token"] = data["access_token"]
        self.creds["expires"] = time.time() + data.get("expires_in", 3600)
        save_credentials(self.creds)
        return self.creds["token"]

    def get(self, path: str, params: dict | None = None, ttl: float = CACHE_TTL_S) -> dict:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None}, doseq=True)
        url = f"{BASE}{path}" + (f"?{query}" if query else "")
        cache = RESPONSE_CACHE / (hashlib.md5(url.encode()).hexdigest() + ".json")
        if ttl and cache.exists() and time.time() - cache.stat().st_mtime < ttl:
            return json.loads(cache.read_text(encoding="utf-8"))
        req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": USER_AGENT,
                                                   "Authorization": f"Bearer {self._token()}",
                                                   "x-api-version": "20240529"})
        data = json.loads(self._request(req))
        if ttl:
            RESPONSE_CACHE.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(data), encoding="utf-8")
        return data

    # --- what the recommender uses -----------------------------------------------------

    def search(self, query: str = "", status: str = "ranked", cursor: str | None = None) -> dict:
        """One page of osu!standard beatmapsets. `query` takes the site's filters, e.g.
        "stars>=6.5 stars<=7.5 bpm>=150 bpm<=158 length<=240"."""
        return self.get("/beatmapsets/search", {"q": query, "m": 0, "s": status, "cursor_string": cursor})

    def leaderboard(self, beatmap_id: int) -> list[dict]:
        return self.get(f"/beatmaps/{beatmap_id}/scores", {"mode": "osu", "limit": 50}).get("scores", [])

    def best_scores(self, user_id: int, limit: int = 100) -> list[dict]:
        return self.get(f"/users/{user_id}/scores/best", {"mode": "osu", "limit": limit})

    def osu_file(self, beatmap_id: int) -> Path:
        """The .osu of a beatmap (no audio), downloaded once."""
        path = OSU_FILES / f"{beatmap_id}.osu"
        if not path.exists() or path.stat().st_size == 0:
            req = urllib.request.Request(f"https://osu.ppy.sh/osu/{beatmap_id}", headers={"User-Agent": USER_AGENT})
            data = self._request(req)
            if not data.strip():
                raise ApiError(f"beatmap {beatmap_id}: empty .osu")
            OSU_FILES.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return path


class OsuFiles:
    """.osu files of many beatmaps at once: from the mirrors in parallel, else from osu.ppy.sh through the API
    client's rate limit. Downloaded once, into the same folder as OsuApi.osu_file."""

    def __init__(self, client: OsuApi, mirrors=OSU_MIRRORS):
        from concurrent.futures import ThreadPoolExecutor
        self.client = client
        self.mirrors = [{"url": url, "slots": threading.Semaphore(n), "rest_until": 0.0} for url, n in mirrors]
        self.pool = ThreadPoolExecutor(sum(n for _, n in mirrors) + 1)

    def submit(self, beatmap_id: int, md5: str | None = None):
        """A future of the file's path."""
        return self.pool.submit(self.get, beatmap_id, md5)

    def get(self, beatmap_id: int, md5: str | None = None) -> Path:
        path = OSU_FILES / f"{beatmap_id}.osu"
        if path.exists() and path.stat().st_size > 0:
            return path
        for m in self.mirrors:
            if time.monotonic() < m["rest_until"]:
                continue
            with m["slots"]:
                try:
                    req = urllib.request.Request(m["url"].format(id=beatmap_id), headers={"User-Agent": USER_AGENT})
                    with urllib.request.urlopen(req, timeout=20) as resp:
                        data = resp.read()
                except urllib.error.HTTPError as e:
                    if e.code == 429:
                        m["rest_until"] = time.monotonic() + float(e.headers.get("Retry-After") or MIRROR_COOLDOWN_S)
                    continue
                except (urllib.error.URLError, OSError, ValueError):
                    continue
            if not data.startswith(b"osu file format") or (md5 and hashlib.md5(data).hexdigest() != md5):
                continue    # not the map, or another version of it
            OSU_FILES.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(f".{threading.get_ident()}.part")
            tmp.write_bytes(data)
            tmp.replace(path)
            return path
        return self.client.osu_file(beatmap_id)

    def close(self):
        self.pool.shutdown(wait=False, cancel_futures=True)
