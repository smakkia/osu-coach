"""The analysis caches (map types, contents, profiles, guessed types) as SQLite tables of key -> JSON value.

A search reads only the maps it looks at and writes only the new ones, instead of loading a whole JSON file of
every map ever analysed (hundreds of MB once parsed) and writing it all again.
"""

import json
import sqlite3
from pathlib import Path

CHUNK = 900   # keys per query (SQLite's limit on bound parameters)


class Store:
    """One cache: values by key, valid for one tag (a version, a model signature); another tag empties it.
    The JSON file it replaces (`legacy`: {"tag", "maps"}) is moved in the first time, then deleted."""

    def __init__(self, path: Path, tag: str | None = None, legacy: Path | None = None):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=30, check_same_thread=False)   # a search's callbacks: any thread
        self.db.execute("PRAGMA journal_mode=WAL")
        with self.db:
            self.db.execute("CREATE TABLE IF NOT EXISTS meta (name TEXT PRIMARY KEY, value TEXT)")
            self.db.execute("CREATE TABLE IF NOT EXISTS maps (key TEXT PRIMARY KEY, value TEXT)")
        if legacy is not None and legacy.exists():
            self._import_legacy(legacy, tag)
        if tag is not None and self.meta("tag") != tag:
            with self.db:
                self.db.execute("DELETE FROM maps")
                self.set_meta("tag", tag)

    def _import_legacy(self, legacy: Path, tag: str | None):
        try:
            data = json.loads(legacy.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
        if data.get("tag") == tag and isinstance(data.get("maps"), dict):
            with self.db:
                self.db.execute("DELETE FROM maps")
                self.set_meta("tag", tag)
                self.put_many(data["maps"])
        del data
        legacy.unlink(missing_ok=True)

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def meta(self, name: str) -> str | None:
        row = self.db.execute("SELECT value FROM meta WHERE name = ?", (name,)).fetchone()
        return row[0] if row else None

    def set_meta(self, name: str, value: str):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?, ?)", (name, value))

    def get(self, key: str, decode=json.loads):
        row = self.db.execute("SELECT value FROM maps WHERE key = ?", (key,)).fetchone()
        return decode(row[0]) if row else None

    def has(self, key: str) -> bool:
        return self.db.execute("SELECT 1 FROM maps WHERE key = ?", (key,)).fetchone() is not None

    def get_many(self, keys, decode=json.loads) -> dict:
        """The values of the keys that are cached (a cached None included)."""
        keys = list(dict.fromkeys(keys))
        out = {}
        for i in range(0, len(keys), CHUNK):
            part = keys[i:i + CHUNK]
            sql = f"SELECT key, value FROM maps WHERE key IN ({','.join('?' * len(part))})"
            for k, v in self.db.execute(sql, part):
                out[k] = decode(v)
        return out

    def put_many(self, values: dict):
        with self.db:
            self.db.executemany("INSERT OR REPLACE INTO maps VALUES (?, ?)",
                                ((k, json.dumps(v)) for k, v in values.items()))

    def keys(self):
        return (k for k, in self.db.execute("SELECT key FROM maps"))

    def items(self, decode=json.loads):
        return ((k, decode(v)) for k, v in self.db.execute("SELECT key, value FROM maps"))

    def __len__(self) -> int:
        return self.db.execute("SELECT COUNT(*) FROM maps").fetchone()[0]
