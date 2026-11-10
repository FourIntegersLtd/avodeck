"""Helpers for the file-lifecycle audit tests (AUDIT-12): synthetic bytes, a seeded team, a download probe.

Everything here is made up: random bytes from a seeded generator, never patient-like text.
"""

from __future__ import annotations

import hashlib
import random
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

import boto3
import httpx
import psycopg

from app.core.storage import upload_prefix
from tests.support.files import CHUNK, STORAGE_BUCKET, STORAGE_REGION, storage_env
from tests.support.seed import Seed
from tests.support.uploads import put, sha

# A second part of 17 bytes after a full 8 MB first part: two parts without a big file.
TWO_PART_SIZE = CHUNK + 17
KEY_RE = r"^cases/{case}/uploads/{upload}/\d{{5}}$"


def blob(seed: int, size: int) -> bytes:
    """Deterministic made-up bytes."""
    return random.Random(seed).randbytes(size)


def parts(data: bytes) -> list[bytes]:
    return [data[i : i + CHUNK] for i in range(0, len(data), CHUNK)] or [b""]


@dataclass(frozen=True)
class Team:
    """One team case with every kind of member, plus an unrelated person with a case of their own."""

    org: uuid.UUID
    case: uuid.UUID
    second_case: uuid.UUID  # same organisation, same named expert
    expert: uuid.UUID
    assistant: uuid.UUID  # case role with case.upload
    viewer: uuid.UUID
    supervisor: uuid.UUID
    co_expert: uuid.UUID
    org_admin: uuid.UUID  # organisation admin, not on the case
    outsider: uuid.UUID
    outsider_case: uuid.UUID
    people: dict[str, uuid.UUID] = field(default_factory=dict)


def make_team(seed: Seed) -> Team:
    expert, admin_person = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "owner", admin_person: "admin"})
    roles = {name: seed.person() for name in ("assistant", "viewer", "supervisor", "co_expert")}
    case = seed.case(org, expert, members={person: name for name, person in roles.items()})
    second = seed.case(org, expert)
    outsider = seed.person()
    other_org = seed.org(kind="personal", members={outsider: "owner"})
    return Team(
        org=org,
        case=case,
        second_case=second,
        expert=expert,
        assistant=roles["assistant"],
        viewer=roles["viewer"],
        supervisor=roles["supervisor"],
        co_expert=roles["co_expert"],
        org_admin=admin_person,
        outsider=outsider,
        outsider_case=seed.case(other_org, outsider),
    )


async def begin(
    client: httpx.AsyncClient,
    case: uuid.UUID,
    data: bytes,
    *,
    filename: str = "bundle.bin",
    kind: str = "records",
    claimed_sha: str | None = None,
) -> httpx.Response:
    body = {"kind": kind, "filename": filename, "size": len(data), "sha256": claimed_sha or sha(data)}
    return await client.post(f"/v1/cases/{case}/uploads", json=body)


async def open_upload(client: httpx.AsyncClient, case: uuid.UUID, data: bytes, **kwargs: Any) -> str:
    resp = await begin(client, case, data, **kwargs)
    assert resp.status_code == 201, resp.text
    return str(resp.json()["upload_id"])


async def send_all(client: httpx.AsyncClient, upload: str, data: bytes) -> None:
    for n, part in enumerate(parts(data), start=1):
        resp = await put(client, upload, n, part)
        assert resp.status_code == 200, resp.text


async def upload_whole(client: httpx.AsyncClient, case: uuid.UUID, data: bytes, **kwargs: Any) -> str:
    upload = await open_upload(client, case, data, **kwargs)
    await send_all(client, upload, data)
    return upload


def object_path(case: uuid.UUID, upload: str, n: int) -> str:
    return f"{upload_prefix(case, upload)}/{n:05d}"


def upload_state(admin: psycopg.Connection, upload: str) -> dict[str, Any]:
    found = admin.execute(
        "select status, failure, received, document_id, cleaned_at is not null from app.uploads where id = %s",
        (upload,),
    ).fetchone()
    assert found is not None, "no such upload row"
    return dict(zip(("status", "failure", "received", "document_id", "cleaned"), found, strict=True))


def documents_at(admin: psycopg.Connection, case: uuid.UUID, upload: str) -> int:
    found = admin.execute(
        "select count(*) from app.documents where storage_path = %s", (upload_prefix(case, upload),)
    ).fetchone()
    assert found is not None
    return int(found[0])


def document_path(case: uuid.UUID, document: str) -> str:
    return f"/v1/cases/{case}/documents/{document}/file"


@dataclass
class Fetched:
    """What a download delivered: the status, the bytes that arrived, and the error that cut the stream, if any."""

    status: int | None = None
    headers: httpx.Headers = field(default_factory=httpx.Headers)
    body: bytes = b""
    error: BaseException | None = None

    @property
    def delivered_whole_file(self) -> bool:
        """A 200 whose stream ended without an error: what a client would accept as the file."""
        return self.status == 200 and self.error is None


async def fetch(client: httpx.AsyncClient, path: str) -> Fetched:
    out = Fetched()
    try:
        async with client.stream("GET", path) as resp:
            out.status, out.headers = resp.status_code, resp.headers
            async for piece in resp.aiter_bytes():
                out.body += piece
    except Exception as exc:  # noqa: BLE001 - a stream that breaks half way surfaces here under the ASGI transport
        out.error = exc
    return out


def flip_byte(blob_: bytes, index: int = 20) -> bytes:
    return blob_[:index] + bytes([blob_[index] ^ 0x01]) + blob_[index + 1 :]


def s3_client() -> Any:
    """A plain boto3 client on the local S3 stand-in, for `head_object` (the adapter has no such call)."""
    env = storage_env()
    return boto3.client(
        "s3",
        endpoint_url=env["S3_ENDPOINT_URL"],
        region_name=STORAGE_REGION,
        aws_access_key_id=env["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=env["AWS_SECRET_ACCESS_KEY"],
    )


def head(key: str) -> dict[str, Any]:
    return dict(s3_client().head_object(Bucket=STORAGE_BUCKET, Key=key))


def key_pattern(case: uuid.UUID, upload: str) -> re.Pattern[str]:
    return re.compile(KEY_RE.format(case=case, upload=upload))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()
