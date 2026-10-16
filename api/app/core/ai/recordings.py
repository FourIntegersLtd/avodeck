"""Recorded responses for the stand-in clients (tests, local development, the eval harness).

A recording is a JSON file `<root>/<kind>/<name>.json` holding `{"kind", "request", "response"}`.
It is found by the SHA-256 of its canonical request, so a stand-in answers only a request it has
seen before and never invents an answer. Requests hold hashes of documents and audio, never the
bytes themselves.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any


class RecordingMissing(LookupError):
    """No recording for this request (the message names the kind only, never the request)."""


def request_key(request: Mapping[str, Any]) -> str:
    canonical = json.dumps(request, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()


class FixtureStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._index: dict[tuple[str, str], Path] | None = None

    def _load(self) -> dict[tuple[str, str], Path]:
        if self._index is None:
            self._index = {}
            for path in sorted(self.root.glob("*/*.json")):
                data = json.loads(path.read_text())
                if isinstance(data, dict) and {"kind", "request", "response"} <= data.keys():
                    self._index[(data["kind"], request_key(data["request"]))] = path
        return self._index

    def lookup(self, kind: str, request: Mapping[str, Any]) -> Any:
        path = self._load().get((kind, request_key(request)))
        if path is None:
            raise RecordingMissing(f"no recorded {kind} response for this request")
        return json.loads(path.read_text())["response"]

    def record(self, kind: str, request: Mapping[str, Any], response: Any, name: str | None = None) -> Path:
        key = request_key(request)
        path = self.root / kind / f"{name or key[:16]}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"kind": kind, "request": dict(request), "response": response}
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n")
        self._load()[(kind, key)] = path
        return path
