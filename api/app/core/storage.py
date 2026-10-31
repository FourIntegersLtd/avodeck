"""Private file storage: S3 in the cell, reached only by FastAPI (rule 4, ADR 0032).

Nothing returned from this module (paths, keys) is ever sent to the browser: files go up and come
back through FastAPI. No signed URL, no public object URL. Callers store what they put here
encrypted under the case's key (`core/crypto.py`); S3 server-side encryption is a second layer.
"""

from __future__ import annotations

import asyncio
import builtins
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.config import ConfigError

DEFAULT_BUCKET = "case-files"

# Hosts that are definitely this machine, so a test S3_ENDPOINT_URL is accepted and production
# endpoints are refused (guard: `test_foundation_storage_region.py`).
_LOCAL_HOSTS = frozenset(("127.0.0.1", "localhost", "::1", "[::1]"))


def case_prefix(case_id: object) -> str:
    """The one folder holding every file of a case (deleting the case removes everything below it)."""
    return f"cases/{case_id}"


def upload_prefix(case_id: object, upload_id: object) -> str:
    """Where an upload's chunks are kept until it completes or expires."""
    return f"{case_prefix(case_id)}/uploads/{upload_id}"


class StorageError(RuntimeError):
    """Storage refused or failed a call. Carries the error code only, never a key or message
    that could hold patient data (rule 6)."""


class StorageNotFound(StorageError):
    """The object or bucket is not there."""


@dataclass(frozen=True)
class StorageSettings:
    bucket: str = DEFAULT_BUCKET
    region: str = ""
    access_key_id: str = field(repr=False, default="")
    secret_access_key: str = field(repr=False, default="")
    session_token: str | None = field(repr=False, default=None)
    endpoint_url: str | None = None


def _validate_endpoint_url(url: str) -> str:
    """Refuse any endpoint that is not on this machine — rule 4 and the guard test.

    Without this, a misconfigured deploy could point the adapter at a public S3 endpoint,
    and its host would appear in logs or error messages as a side-channel.
    """
    parsed = urlparse(url)
    host = (parsed.hostname or "").rstrip(".")
    if host not in _LOCAL_HOSTS:
        raise ConfigError(f"S3_ENDPOINT_URL must point at this machine ({host!r} is not local)")
    if parsed.username is not None:
        raise ConfigError("S3_ENDPOINT_URL must not carry credentials in the URL")
    return url


def storage_settings(env: Mapping[str, str] | None = None) -> StorageSettings:
    source = os.environ if env is None else env

    region = source.get("AWS_REGION", "").strip()
    access_key = source.get("AWS_ACCESS_KEY_ID", "").strip()
    secret_key = source.get("AWS_SECRET_ACCESS_KEY", "").strip()

    missing = [
        n
        for n, v in (("AWS_REGION", region), ("AWS_ACCESS_KEY_ID", access_key), ("AWS_SECRET_ACCESS_KEY", secret_key))
        if not v
    ]
    if missing:
        raise ConfigError(f"{', '.join(missing)} not set")

    # Rule 5: the cell may declare which regions are allowed (same pattern as AI_ENDPOINT_ALLOW_LIST).
    allowed_csv = source.get("AWS_ALLOWED_REGIONS", "").strip()
    if allowed_csv:
        allowed = {r.strip() for r in allowed_csv.split(",") if r.strip()}
        if region not in allowed:
            raise ConfigError(f"AWS_REGION {region!r} is not in AWS_ALLOWED_REGIONS")

    endpoint_url: str | None = source.get("S3_ENDPOINT_URL", "").strip() or None
    if endpoint_url is not None:
        endpoint_url = _validate_endpoint_url(endpoint_url)

    return StorageSettings(
        bucket=source.get("STORAGE_BUCKET", "").strip() or DEFAULT_BUCKET,
        region=region,
        access_key_id=access_key,
        secret_access_key=secret_key,
        session_token=source.get("AWS_SESSION_TOKEN", "").strip() or None,
        endpoint_url=endpoint_url,
    )


class CaseFileStorage:
    """S3-backed object store for case files and person-level files (templates, signatures).

    The sync boto3 client is called through `asyncio.to_thread`, matching the pattern used by
    `LiveBedrock` in `core/ai/bedrock_live.py`.
    """

    def __init__(
        self,
        settings: StorageSettings,
        *,
        connect_timeout: float = 10.0,
        read_timeout: float = 120.0,
        list_page_size: int = 1000,
        delete_batch_size: int = 1000,
    ) -> None:
        self.settings = settings
        self.list_page_size = list_page_size
        self.delete_batch_size = min(delete_batch_size, 1000)  # S3 limit
        self._client: Any = boto3.client(
            "s3",
            region_name=settings.region,
            endpoint_url=settings.endpoint_url,
            aws_access_key_id=settings.access_key_id,
            aws_secret_access_key=settings.secret_access_key,
            aws_session_token=settings.session_token,
            config=Config(
                retries={"total_max_attempts": 3, "mode": "standard"},
                connect_timeout=connect_timeout,
                read_timeout=read_timeout,
            ),
        )

    def __repr__(self) -> str:
        return f"CaseFileStorage(bucket={self.settings.bucket!r}, region={self.settings.region!r})"

    # -- error mapping -------------------------------------------------------------------

    @staticmethod
    def _not_found(exc: ClientError) -> bool:
        code = exc.response.get("Error", {}).get("Code", "")
        status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)
        return code in ("NoSuchKey", "NoSuchBucket", "404") or status == 404

    @staticmethod
    def _raise(exc: ClientError) -> None:
        """Map a boto3 error to StorageError/StorageNotFound. Never includes the key or message."""
        if CaseFileStorage._not_found(exc):
            code = exc.response.get("Error", {}).get("Code", "unknown")
            raise StorageNotFound(f"storage: {code}") from exc
        code = exc.response.get("Error", {}).get("Code", "unknown")
        raise StorageError(f"storage: {code}") from exc

    # -- public surface (same nine methods as the old SupabaseStorage) -------------------

    async def ensure_bucket(self) -> None:
        """Create the private bucket if it is not there yet."""
        try:
            await asyncio.to_thread(self._client.head_bucket, Bucket=self.settings.bucket)
            return
        except ClientError as exc:
            if not self._not_found(exc):
                self._raise(exc)
        # Bucket does not exist — create it.
        try:
            create_args: dict[str, Any] = {"Bucket": self.settings.bucket}
            # us-east-1 does not accept a LocationConstraint (it's the default).
            if self.settings.region and self.settings.region != "us-east-1":
                create_args["CreateBucketConfiguration"] = {"LocationConstraint": self.settings.region}
            await asyncio.to_thread(self._client.create_bucket, **create_args)
        except ClientError as exc:
            # Bucket may have been created between our head and create (race in tests or multi-worker).
            if "BucketAlreadyOwnedByYou" not in str(exc) and "BucketAlreadyExists" not in str(exc):
                self._raise(exc)

    async def put(self, path: str, data: bytes) -> None:
        try:
            await asyncio.to_thread(
                self._client.put_object,
                Bucket=self.settings.bucket,
                Key=path,
                Body=data,
                ServerSideEncryption="AES256",
            )
        except ClientError as exc:
            self._raise(exc)
        except BotoCoreError as exc:
            raise StorageError(f"storage: {type(exc).__name__}") from exc

    async def get(self, path: str) -> bytes:
        try:
            resp = await asyncio.to_thread(self._client.get_object, Bucket=self.settings.bucket, Key=path)
            return await asyncio.to_thread(resp["Body"].read)
        except ClientError as exc:
            self._raise(exc)
            raise  # unreachable, but keeps mypy happy
        except BotoCoreError as exc:
            raise StorageError(f"storage: {type(exc).__name__}") from exc

    async def list(self, prefix: str) -> builtins.list[str]:
        """Full paths of the objects directly under `prefix`, in name order, however many pages."""
        prefix = prefix.rstrip("/") + "/"
        found: builtins.list[str] = []
        continuation: dict[str, str] = {}
        while True:
            resp = await asyncio.to_thread(
                self._client.list_objects_v2,
                Bucket=self.settings.bucket,
                Prefix=prefix,
                Delimiter="/",
                MaxKeys=self.list_page_size,
                **continuation,
            )
            found.extend(obj["Key"] for obj in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return found
            continuation = {"ContinuationToken": resp["NextContinuationToken"]}

    async def list_folders(self, prefix: str) -> builtins.list[str]:
        """Names (not paths) of the folders directly under `prefix`, however many pages."""
        prefix = prefix.rstrip("/") + "/"
        names: builtins.list[str] = []
        continuation: dict[str, str] = {}
        while True:
            resp = await asyncio.to_thread(
                self._client.list_objects_v2,
                Bucket=self.settings.bucket,
                Prefix=prefix,
                Delimiter="/",
                MaxKeys=self.list_page_size,
                **continuation,
            )
            for cp in resp.get("CommonPrefixes", []):
                # CommonPrefixes look like "cases/<id>/uploads/" — strip the prefix and trailing slash.
                name = cp["Prefix"][len(prefix) :].rstrip("/")
                if name:
                    names.append(name)
            if not resp.get("IsTruncated"):
                return names
            continuation = {"ContinuationToken": resp["NextContinuationToken"]}

    async def list_object_times(self, prefix: str) -> dict[str, datetime | None]:
        """Every object anywhere below `prefix` (full path) → the time it was last written.

        S3 returns `LastModified` (not Supabase's `created_at`). For upload chunks, which are
        written once and never rewritten, the two are the same. If an overwritten key's age
        matters to a caller, this will return the *last* write time.
        """
        prefix = prefix.rstrip("/") + "/"
        times: dict[str, datetime | None] = {}
        continuation: dict[str, str] = {}
        while True:
            resp = await asyncio.to_thread(
                self._client.list_objects_v2,
                Bucket=self.settings.bucket,
                Prefix=prefix,
                MaxKeys=self.list_page_size,
                **continuation,
            )
            for obj in resp.get("Contents", []):
                times[obj["Key"]] = obj.get("LastModified")
            if not resp.get("IsTruncated"):
                return times
            continuation = {"ContinuationToken": resp["NextContinuationToken"]}

    async def list_recursive(self, prefix: str) -> builtins.list[str]:
        """Full paths of every object anywhere below `prefix`, however many, sorted."""
        prefix = prefix.rstrip("/") + "/"
        found: builtins.list[str] = []
        continuation: dict[str, str] = {}
        while True:
            resp = await asyncio.to_thread(
                self._client.list_objects_v2,
                Bucket=self.settings.bucket,
                Prefix=prefix,
                MaxKeys=self.list_page_size,
                **continuation,
            )
            found.extend(obj["Key"] for obj in resp.get("Contents", []))
            if not resp.get("IsTruncated"):
                return sorted(found)
            continuation = {"ContinuationToken": resp["NextContinuationToken"]}

    async def delete(self, paths: Sequence[str]) -> None:
        """Remove objects by full path, in batches. Paths that are already gone are not an error."""
        paths_list = builtins.list(paths)
        for start in range(0, len(paths_list), self.delete_batch_size):
            batch = paths_list[start : start + self.delete_batch_size]
            try:
                await asyncio.to_thread(
                    self._client.delete_objects,
                    Bucket=self.settings.bucket,
                    Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
                )
            except ClientError as exc:
                self._raise(exc)
            except BotoCoreError as exc:
                raise StorageError(f"storage: {type(exc).__name__}") from exc

    async def aclose(self) -> None:
        self._client.close()
