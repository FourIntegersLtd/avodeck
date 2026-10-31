"""Private file storage: Supabase Storage in the cell, reached only by FastAPI (rule 4, ADR 0013).

The service-role key is used here for Storage only, never for data. Nothing returned from this
module (paths, URLs, keys) is ever sent to the browser: files go up and come back through FastAPI.
Callers store what they put here encrypted under the case's key (`core/crypto.py`).
"""

from __future__ import annotations

import builtins
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import quote

import httpx

from app.core.config import ConfigError

DEFAULT_BUCKET = "case-files"


def case_prefix(case_id: object) -> str:
    """The one folder holding every file of a case (deleting the case removes everything below it)."""
    return f"cases/{case_id}"


def upload_prefix(case_id: object, upload_id: object) -> str:
    """Where an upload's chunks are kept until it completes or expires."""
    return f"{case_prefix(case_id)}/uploads/{upload_id}"


class StorageError(RuntimeError):
    """Storage refused or failed a call. Carries the HTTP status only."""


class StorageNotFound(StorageError):
    """The object is not there (HTTP 404, or the 400 Supabase sends with a 404 in its body)."""


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
    def __init__(
        self,
        settings: StorageSettings,
        *,
        timeout: float = 120,
        list_page_size: int = 1000,
        delete_batch_size: int = 1000,
    ) -> None:
        self.settings = settings
        self.list_page_size = list_page_size
        self.delete_batch_size = delete_batch_size
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
            if resp.status_code == 404 or (resp.status_code == 400 and SupabaseStorage._body_says_404(resp)):
                raise StorageNotFound(f"storage answered {resp.status_code}")
            raise StorageError(f"storage answered {resp.status_code}")

    @staticmethod
    def _body_says_404(resp: httpx.Response) -> bool:
        try:
            body = resp.json()
        except ValueError:
            return False
        return isinstance(body, dict) and str(body.get("statusCode")) == "404"

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

    async def _list_page(self, prefix: str, offset: int) -> builtins.list[dict[str, object]]:
        resp = await self._client.post(
            f"/object/list/{self.settings.bucket}",
            json={
                "prefix": prefix,
                "limit": self.list_page_size,
                "offset": offset,
                "sortBy": {"column": "name", "order": "asc"},
            },
        )
        self._check(resp)
        items: builtins.list[dict[str, object]] = resp.json()
        return items

    async def list(self, prefix: str) -> list[str]:
        """Full paths of the objects directly under `prefix`, in name order, however many pages."""
        prefix = prefix.rstrip("/")
        found: builtins.list[str] = []
        offset = 0
        while True:
            items = await self._list_page(prefix, offset)
            found.extend(f"{prefix}/{item['name']}" for item in items if item.get("id"))
            if len(items) < self.list_page_size:
                return found
            offset += len(items)

    async def _items(self, prefix: str) -> builtins.list[dict[str, object]]:
        """Every entry (objects and folders) directly under `prefix`, however many pages."""
        prefix = prefix.rstrip("/")
        found: builtins.list[dict[str, object]] = []
        offset = 0
        while True:
            items = await self._list_page(prefix, offset)
            found.extend(items)
            if len(items) < self.list_page_size:
                return found
            offset += len(items)

    async def list_folders(self, prefix: str) -> builtins.list[str]:
        """Names (not paths) of the folders directly under `prefix`, however many pages."""
        return [str(i["name"]) for i in await self._items(prefix) if not i.get("id")]

    async def list_object_times(self, prefix: str) -> dict[str, datetime | None]:
        """Every object anywhere below `prefix` (full path) with the time Storage created it, or None if unknown."""
        times: dict[str, datetime | None] = {}
        folders = [prefix.rstrip("/")]
        while folders:
            folder = folders.pop()
            for item in await self._items(folder):
                path = f"{folder}/{item['name']}"
                if not item.get("id"):
                    folders.append(path)
                    continue
                raw = item.get("created_at")
                times[path] = datetime.fromisoformat(str(raw).replace("Z", "+00:00")) if raw else None
        return times

    async def list_recursive(self, prefix: str) -> builtins.list[str]:
        """Full paths of every object anywhere below `prefix`, however many.

        Storage lists one folder level at a time (folders come back without an id), a page at a time.
        """
        found: builtins.list[str] = []
        folders = [prefix.rstrip("/")]
        while folders:
            folder = folders.pop()
            offset = 0
            while True:
                items = await self._list_page(folder, offset)
                for item in items:
                    path = f"{folder}/{item['name']}"
                    (found if item.get("id") else folders).append(path)
                if len(items) < self.list_page_size:
                    break
                offset += len(items)
        return sorted(found)

    async def delete(self, paths: Sequence[str]) -> None:
        """Remove objects by full path, in batches. Paths that are already gone are not an error."""
        paths = list(paths)
        for start in range(0, len(paths), self.delete_batch_size):
            batch = paths[start : start + self.delete_batch_size]
            resp = await self._client.request("DELETE", f"/object/{self.settings.bucket}", json={"prefixes": batch})
            self._check(resp)

    async def aclose(self) -> None:
        await self._client.aclose()
