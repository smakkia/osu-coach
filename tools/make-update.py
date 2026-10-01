"""Builds dist/osu-coach-<version>-update.zip, the release asset the app updates itself from (gui.install_update):
every file of the app (dist/osu-coach) under app/, the map data (dist/data) under data/, and manifest.json with each
file's SHA-256, so an update writes only the files that changed. tools/build-setup.ps1 runs it after the build.

    python tools/make-update.py <version>
"""

import hashlib
import json
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    version = sys.argv[1]
    sources = {"app": DIST / "osu-coach", "data": DIST / "data"}
    files = {}
    for prefix, folder in sources.items():
        if not folder.is_dir():
            sys.exit(f"{folder} is missing: build the app first")
        for p in sorted(folder.rglob("*")):
            if p.is_file():
                files[f"{prefix}/{p.relative_to(folder).as_posix()}"] = p
    out = DIST / f"osu-coach-{version}-update.zip"
    manifest = {"version": version, "files": {name: sha256(p) for name, p in files.items()}}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_LZMA) as z:
        z.writestr("manifest.json", json.dumps(manifest, indent=1))
        for name, p in files.items():
            z.write(p, name)
    print(f"{out}: {len(files)} files, {out.stat().st_size / 1e6:.1f} MB")


if __name__ == "__main__":
    main()
