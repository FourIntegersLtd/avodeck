"""Idempotency-Key replays never skip the route's checks (F27 with rule 9 and rule 10).

A replay runs the route's dependencies first: a person removed from the case gets 404 and a
lapsed step-up gets 403 `step_up_required`, as a first request would. The kept answer is sealed
at rest and tied to its case.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator, Callable
from typing import Annotated, Any

import httpx
import psycopg
import pytest
from fastapi import Depends, FastAPI

from app.core import audit
from app.core.auth.access import CaseAccess, require_case_access
from app.core.auth.step_up import require_step_up
from app.core.http import DbConn, router
from tests.support.auth import CSRF_HEADER, cookie_header
from tests.support.seed import SeededSession, Seed

pytestmark = pytest.mark.anyio

SECRET = "zz-secret-answer"


def probe_router() -> Any:
    r = router(prefix="/v1/cases/{case_id}/test-replay")

    @r.post("", status_code=201)
    async def edit(
        case_id: uuid.UUID, access: Annotated[CaseAccess, Depends(require_case_access("case.edit_setup"))], conn: DbConn
    ) -> dict[str, str]:
        await audit.record(conn, action="test.replay", target=str(case_id), case_id=case_id)
        return {"answer": SECRET, "nonce": uuid.uuid4().hex}

    @r.post("/risky", status_code=201, dependencies=[Depends(require_step_up)])
    async def risky(
        case_id: uuid.UUID, access: Annotated[CaseAccess, Depends(require_case_access("case.export"))], conn: DbConn
    ) -> dict[str, str]:
        return {"answer": SECRET, "nonce": uuid.uuid4().hex}

    return r


Post = Callable[[SeededSession, str, str], Any]


@pytest.fixture
async def post(app: FastAPI) -> AsyncIterator[Post]:
    from app.main import mount

    mount(app, probe_router())
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://test") as client:

        async def call(session: SeededSession, path: str, key: str) -> httpx.Response:
            headers = {"cookie": cookie_header(session), CSRF_HEADER: session.csrf, "idempotency-key": key}
            return await client.post(path, headers=headers)

        yield call


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant"})
    return {"expert": expert, "assistant": assistant, "case": case}


async def test_a_person_removed_from_the_case_gets_404_on_replay(
    post: Post, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    session = seed.session(team["assistant"])
    path = f"/v1/cases/{team['case']}/test-replay"
    assert (await post(session, path, "r1")).status_code == 201
    admin.execute("delete from app.case_members where case_id = %s and person_id = %s", (team["case"], team["assistant"]))
    replayed = await post(session, path, "r1")
    assert replayed.status_code == 404
    assert SECRET not in replayed.text


async def test_a_lapsed_step_up_gets_403_on_replay(
    post: Post, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    session = seed.session(team["expert"])
    path = f"/v1/cases/{team['case']}/test-replay/risky"
    assert (await post(session, path, "r2")).status_code == 201
    admin.execute("update app.sessions set mfa_verified_at = now() - interval '11 minutes' where id = %s", (session.id,))
    replayed = await post(session, path, "r2")
    assert replayed.status_code == 403
    assert replayed.json()["error"]["code"] == "step_up_required"


async def test_a_replay_with_access_still_returns_the_first_answer(
    post: Post, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    session = seed.session(team["expert"])
    path = f"/v1/cases/{team['case']}/test-replay"
    first = await post(session, path, "r3")
    again = await post(session, path, "r3")
    assert again.status_code == 201
    assert again.json() == first.json()
    assert again.headers.get("idempotent-replayed") == "true"


async def test_the_kept_answer_is_sealed_and_tied_to_its_case(
    post: Post, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    session = seed.session(team["expert"])
    assert (await post(session, f"/v1/cases/{team['case']}/test-replay", "r4")).status_code == 201
    row = admin.execute(
        "select response_body, case_id from app.idempotency_keys where person_id = %s and key = 'r4'",
        (team["expert"],),
    ).fetchone()
    assert row is not None
    assert SECRET.encode() not in bytes(row[0])
    assert row[1] == team["case"]
