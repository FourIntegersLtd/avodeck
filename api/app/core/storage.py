"""Private file storage: Supabase Storage in the cell, reached only by FastAPI (rule 4, ADR 0013).

The service-role key is used here for Storage only, never for data. Nothing returned from this
module (paths, URLs, keys) is ever sent to the browser: files go up and come back through FastAPI.
Callers store what they put here encrypted under the case's key (`core/crypto.py`).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from urllib.parse import quote

import httpx

from app.core.config import ConfigError

DEFAULT_BUCKET = "case-files"


def upload_prefix(case_id: object, upload_id: object) -> str:
    """Where an upload's chunks are kept until it completes or expires."""
    return f"cases/{case_id}/uploads/{upload_id}"


class StorageError(RuntimeError):
    """Storage refused or failed a call. Carries the HTTP status only."""


@dataclass(frozen=True)
class StorageSettings:
    url: str
    service_role_key: str = field(repr=False)
    bucket: str = DEFAULT_BUCKET


def storage_settings(env: Mapping[str, str] | None = None) -> StorageSettings:
    source = os.environ if env is None else env
    url = source.get("SUPABASE_URL", "").strip()
    key = source.get("SUPABASE_SERVICE_ROLE_KEY", "").strip()
    missing = [n for n, v in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not v]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set")
    return StorageSettings(
        url=url.rstrip("/"), service_role_key=key, bucket=source.get("STORAGE_BUCKET", "").strip() or DEFAULT_BUCKET
    )


class SupabaseStorage:
    def __init__(self, settings: StorageSettings, *, timeout: float = 120) -> None:
        self.settings = settings
        self._client = httpx.AsyncClient(
            base_url=f"{settings.url}/storage/v1",
            headers={"authorization": f"Bearer {settings.service_role_key}", "apikey": settings.service_role_key},
            timeout=timeout,
        )

    def _object(self, path: str) -> str:
        return f"/object/{self.settings.bucket}/{quote(path)}"

    @staticmethod
    def _check(resp: httpx.Response) -> None:
        if resp.status_code >= 400:
            raise StorageError(f"storage answered {resp.status_code}")

    async def ensure_bucket(self) -> None:
        """Create the private bucket if it is not there yet."""
        found = await self._client.get(f"/bucket/{self.settings.bucket}")
        if found.status_code == 200:
            return
        bucket = self.settings.bucket
        resp = await self._client.post("/bucket", json={"id": bucket, "name": bucket, "public": False})
        if resp.status_code >= 400 and "exists" not in resp.text.lower():
            self._check(resp)

    async def put(self, path: str, data: bytes) -> None:
        resp = await self._client.post(
            self._object(path), content=data, headers={"content-type": "application/octet-stream", "x-upsert": "true"}
        )
        self._check(resp)

    async def get(self, path: str) -> bytes:
        resp = await self._client.get(self._object(path))
        self._check(resp)
        return resp.content

    async def list(self, prefix: str) -> list[str]:
        """Full paths of the objects directly under `prefix`, in name order."""
        prefix = prefix.rstrip("/")
        resp = await self._client.post(
            f"/object/list/{self.settings.bucket}",
            json={"prefix": prefix, "limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"}},
        )
        self._check(resp)
        return [f"{prefix}/{item['name']}" for item in resp.json() if item.get("id")]

    async def delete(self, paths: Sequence[str]) -> None:
        """Remove objects by full path. Paths that are already gone are not an error."""
        if not paths:
            return
        resp = await self._client.request("DELETE", f"/object/{self.settings.bucket}", json={"prefixes": list(paths)})
        self._check(resp)

    async def aclose(self) -> None:
        await self._client.aclose()
