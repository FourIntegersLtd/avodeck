"""The uploads guard: parts and their kept hashes change only while parts arrive, or when a reopen drops some.

The hashes kept per part (0122) are the proof of what was verified, so no other transition may touch
`received` or `chunk_sha256`, including the update that finishes the upload.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

HASHES = {"1": "a" * 64, "2": "b" * 64}


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return {"expert": expert, "case": seed.case(org, expert)}


def finalizing(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> uuid.UUID:
    job = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'finalize_upload') returning id", (team["case"],)
    ).fetchone()
    assert job is not None
    row = admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " received, chunk_sha256) values (%s, %s, 'records', 'f.pdf', 16, %s, 8, 2, '{1,2}', %s) returning id",
        (team["case"], team["expert"], "c" * 64, json.dumps(HASHES)),
    ).fetchone()
    assert row is not None
    admin.execute(
        "update app.uploads set status = 'finalizing', finalize_job_id = %s, finalizing_at = now() where id = %s",
        (job[0], row[0]),
    )
    return uuid.UUID(str(row[0]))


def document(admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> uuid.UUID:
    row = admin.execute(
        "insert into app.documents (case_id, kind, filename, sha256) values (%s, 'records', 'f.pdf', %s) returning id",
        (team["case"], "c" * 64),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


def refused(admin: psycopg.Connection, sql: str, *args: object) -> str:
    with pytest.raises(psycopg.Error) as err:
        admin.execute(sql, args)
    assert err.value.sqlstate == "AV409"
    return str(err.value)


def state(admin: psycopg.Connection, upload: uuid.UUID) -> tuple[object, ...]:
    row = admin.execute("select status, received, chunk_sha256 from app.uploads where id = %s", (upload,)).fetchone()
    assert row is not None
    return row


async def test_finishing_the_upload_cannot_grow_the_received_parts(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload = finalizing(admin, team)
    refused(
        admin,
        "update app.uploads set status = 'complete', document_id = %s, received = '{1,2,3}' where id = %s",
        document(admin, team),
        upload,
    )
    assert state(admin, upload) == ("finalizing", [1, 2], HASHES)


async def test_finishing_the_upload_cannot_rewrite_a_kept_part_hash(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload = finalizing(admin, team)
    refused(
        admin,
        "update app.uploads set status = 'complete', document_id = %s, chunk_sha256 = %s where id = %s",
        document(admin, team),
        json.dumps({"1": "f" * 64, "2": HASHES["2"]}),
        upload,
    )
    assert state(admin, upload) == ("finalizing", [1, 2], HASHES)


@pytest.mark.parametrize("new_status", ["failed", "expired"])
async def test_no_closing_transition_may_touch_the_parts_or_their_hashes(
    admin: psycopg.Connection, team: dict[str, uuid.UUID], new_status: str
) -> None:
    upload = finalizing(admin, team)
    refused(admin, f"update app.uploads set status = '{new_status}', received = '{{1}}' where id = %s", upload)
    refused(admin, f"update app.uploads set status = '{new_status}', chunk_sha256 = '{{}}' where id = %s", upload)
    assert state(admin, upload) == ("finalizing", [1, 2], HASHES)


async def test_a_reopen_may_drop_only_the_missing_part_and_its_hash(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload = finalizing(admin, team)
    admin.execute(
        "update app.uploads set status = 'open', received = '{1}', chunk_sha256 = %s where id = %s",
        (json.dumps({"1": HASHES["1"]}), upload),
    )
    assert state(admin, upload) == ("open", [1], {"1": HASHES["1"]})


async def test_a_reopen_cannot_add_a_part_or_change_a_kept_hash(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload = finalizing(admin, team)
    refused(admin, "update app.uploads set status = 'open', received = '{1,2,3}' where id = %s", upload)
    refused(
        admin,
        "update app.uploads set status = 'open', chunk_sha256 = %s where id = %s",
        json.dumps({"1": "f" * 64, "2": HASHES["2"]}),
        upload,
    )
    refused(
        admin,
        "update app.uploads set status = 'open', chunk_sha256 = %s where id = %s",
        json.dumps({**HASHES, "3": "d" * 64}),
        upload,
    )
    assert state(admin, upload) == ("finalizing", [1, 2], HASHES)


async def test_parts_still_arrive_while_the_upload_is_open(
    admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    upload = finalizing(admin, team)
    admin.execute("update app.uploads set status = 'open', received = '{1}' where id = %s", (upload,))
    admin.execute(
        "update app.uploads set received = '{1,2}', chunk_sha256 = %s where id = %s", (json.dumps(HASHES), upload)
    )
    assert state(admin, upload) == ("open", [1, 2], HASHES)
