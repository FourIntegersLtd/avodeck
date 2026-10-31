#!/usr/bin/env python3
"""Download the pinned embedder and reranker files into DEST and check their SHA-256 (ADR 0029).

Used by the api/Dockerfile builder stage (DEST=/opt/models) and by CI (a cached folder). The
running worker never downloads anything. Each file lands at DEST/<name>/<basename>.
    python scripts/fetch_models.py DEST [api/models.lock.json]
"""

from __future__ import annotations

import hashlib
import json
import sys
import urllib.request
from pathlib import Path


def fetch(dest: Path, lock: Path) -> None:
    for name, entry in json.loads(lock.read_text()).items():
        for path, expected in entry["files"].items():
            target = dest / name / Path(path).name
            if not (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == expected):
                target.parent.mkdir(parents=True, exist_ok=True)
                url = f"https://huggingface.co/{entry['repo']}/resolve/{entry['revision']}/{path}"
                urllib.request.urlretrieve(url, target)  # noqa: S310 - a pinned https URL
            digest = hashlib.sha256(target.read_bytes()).hexdigest()
            if digest != expected:
                target.unlink()
                raise SystemExit(f"{name}/{target.name}: SHA-256 mismatch ({digest})")
            print(f"ok {name}/{target.name}")


if __name__ == "__main__":
    here = Path(__file__).resolve().parents[1]
    fetch(Path(sys.argv[1]), Path(sys.argv[2]) if len(sys.argv) > 2 else here / "api" / "models.lock.json")
