"""Idempotency-Key on state-changing requests (F27: a double click or a retried request does the work once).

A probe route built with the foundation router writes one audit row per real call, so the
number of rows shows how many times the work was done.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from pydantic import BaseModel

from app.core import audit
from app.core.errors import ApiError
from app.core.http import DbConn, router
from tests.support.auth import CSRF_HEADER, cookie_header
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class Probe(BaseModel):
    tag: str
    fail: bool = False


def probe_router() -> Any:
    r = router(prefix="/v1/test-probe")

    @r.post("", status_code=201)
    async def probe(body: Probe, conn: DbConn) -> dict[str, str]:
        await audit.record(conn, action="test.probe", target=body.tag)
        if body.fail:
            raise ApiError("invalid", "Probe asked to fail.", 422)
        return {"tag": body.tag, "nonce": uuid.uuid4().hex}

    return r


Post = Callable[..., Any]


@pytest.fixture
async def post(app: FastAPI, seed: Seed) -> AsyncIterator[Callable[..., Any]]:
    from app.main import mount

    mount(app, probe_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:

        async def call(person: uuid.UUID, key: str | None, body: dict[str, Any]) -> httpx.Response:
            session = seed.session(person)
            headers = {"cookie": cookie_header(session), CSRF_HEADER: session.csrf}
            if key is not None:
                headers["idempotency-key"] = key
            return await client.post("/v1/test-probe", json=body, headers=headers)

        yield call


def done(admin: psycopg.Connection, tag: str) -> int:
    (n,) = admin.execute(  # type: ignore[misc]
        "select count(*) from app.audit_log where action = 'test.probe' and target = %s", (tag,)
    ).fetchone()
    return int(n)


async def test_a_repeated_key_returns_the_first_answer_and_does_the_work_once(
    post: Post, admin: psycopg.Connection
) -> None:
    person, tag = uuid.uuid4(), uuid.uuid4().hex
    first = await post(person, "k1", {"tag": tag})
    again = await post(person, "k1", {"tag": tag})
    assert first.status_code == again.status_code == 201, first.text
    assert again.json() == first.json()
    assert again.headers.get("idempotent-replayed") == "true"
    assert done(admin, tag) == 1


async def test_without_a_key_each_request_does_the_work(post: Post, admin: psycopg.Connection) -> None:
    person, tag = uuid.uuid4(), uuid.uuid4().hex
    await post(person, None, {"tag": tag})
    await post(person, None, {"tag": tag})
    assert done(admin, tag) == 2


async def test_the_same_key_with_a_different_request_is_refused(post: Post, admin: psycopg.Connection) -> None:
    person, tag = uuid.uuid4(), uuid.uuid4().hex
    assert (await post(person, "k2", {"tag": tag})).status_code == 201
    reused = await post(person, "k2", {"tag": tag + "-other"})
    assert reused.status_code == 422
    assert reused.json()["error"]["code"] == "idempotency_key_reused"
    assert done(admin, tag + "-other") == 0


async def test_failed_requests_are_not_remembered_and_leave_nothing_behind(
    post: Post, admin: psycopg.Connection
) -> None:
    person, tag = uuid.uuid4(), uuid.uuid4().hex
    failed = await post(person, "k3", {"tag": tag, "fail": True})
    assert failed.status_code == 422
    assert done(admin, tag) == 0  # the failed request's work was rolled back
    retried = await post(person, "k3", {"tag": tag, "fail": True})
    assert retried.headers.get("idempotent-replayed") is None


async def test_an_expired_key_counts_as_new(post: Post, admin: psycopg.Connection) -> None:
    person, tag = uuid.uuid4(), uuid.uuid4().hex
    await post(person, "k4", {"tag": tag})
    admin.execute(
        "update app.idempotency_keys set expires_at = now() - interval '1 second' where person_id = %s and key = 'k4'",
        (person,),
    )
    again = await post(person, "k4", {"tag": tag})
    assert again.status_code == 201
    assert again.headers.get("idempotent-replayed") is None
    assert done(admin, tag) == 2


async def test_keys_belong_to_one_person(post: Post, admin: psycopg.Connection) -> None:
    tag = uuid.uuid4().hex
    await post(uuid.uuid4(), "shared", {"tag": tag})
    other = await post(uuid.uuid4(), "shared", {"tag": tag})
    assert other.headers.get("idempotent-replayed") is None
    assert done(admin, tag) == 2
