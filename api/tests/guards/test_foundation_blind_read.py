"""Rule 3: blind read is enforced by the server.

`GET /cases/{id}/pages/{n}/facts` answers 423 `blind_read` until `POST /cases/{id}/pages/{n}/read`
is recorded for the person asking. The real routes belong to slice B; the gate is core
(`app.core.contracts.reading.require_page_read`), so this guard proves it against a stub route
and stub read log, and scans the app so any real facts route must carry the gate.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI
from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute
from psycopg import AsyncConnection

from app.core.auth.access import require_case_access
from app.core.contracts.reading import PageReadLog, require_page_read
from app.core.db import Database
from app.core.http import CurrentScope, router
from app.main import create_app, mount
from tests.support.files import build_app
from tests.support.seed import Seed

FACTS = "/v1/cases/{case_id}/pages/{page_no}/facts"
READ = "/v1/cases/{case_id}/pages/{page_no}/read"


class StubReads:
    """An in-memory read log standing in for slice B's `page_reads` table."""

    def __init__(self) -> None:
        self.reads: set[tuple[uuid.UUID, int, uuid.UUID]] = set()

    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        return (case_id, page_no, person_id) in self.reads

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        return set()


def stub_routes(reads: StubReads) -> APIRouter:
    stub = router()

    @stub.get(FACTS)
    async def facts(case_id: uuid.UUID, page_no: int, _: None = Depends(require_page_read())) -> list[str]:
        return ["suggestion"]

    @stub.post(READ, status_code=201)
    async def read(
        case_id: uuid.UUID, page_no: int, scope: CurrentScope, _: object = Depends(require_case_access("page.read"))
    ) -> dict[str, bool]:
        reads.reads.add((case_id, page_no, scope.person_id))
        return {"read": True}

    return stub


def make_app(api_db: Database, auth_env: dict[str, str], reads: PageReadLog | None) -> FastAPI:
    app = build_app(api_db, auth_env, page_reads=reads)
    mount(app, stub_routes(reads if isinstance(reads, StubReads) else StubReads()))
    return app


@pytest.fixture
def people(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["assistant"]: "assistant", p["viewer"]: "viewer"})
    seed.page(p["case"], seed.document(p["case"]), 1)
    return p


Client = Callable[..., Awaitable[httpx.AsyncClient]]


async def _client(app: FastAPI, seed: Seed, person: uuid.UUID) -> httpx.AsyncClient:
    from tests.support.auth import CSRF_HEADER, cookie_header

    session = seed.session(person)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"cookie": cookie_header(session), CSRF_HEADER: session.csrf, "idempotency-key": uuid.uuid4().hex},
    )


@pytest.mark.anyio
async def test_suggestions_are_423_until_the_page_is_marked_read(
    api_db: Database, auth_env: dict[str, str], seed: Seed, people: dict[str, uuid.UUID]
) -> None:
    reads = StubReads()
    app = make_app(api_db, auth_env, reads)
    case = people["case"]
    async with await _client(app, seed, people["expert"]) as expert:
        resp = await expert.get(f"/v1/cases/{case}/pages/1/facts")
        assert resp.status_code == 423, resp.text
        assert resp.json()["error"]["code"] == "blind_read"
        assert "suggestion" not in resp.text
        assert (await expert.post(f"/v1/cases/{case}/pages/1/read")).status_code == 201
        resp = await expert.get(f"/v1/cases/{case}/pages/1/facts")
        assert resp.status_code == 200, resp.text
        assert (await expert.get(f"/v1/cases/{case}/pages/2/facts")).status_code == 423
    async with await _client(app, seed, people["assistant"]) as assistant:
        resp = await assistant.get(f"/v1/cases/{case}/pages/1/facts")
        assert resp.status_code == 423, "one person's read does not open the page for another"


@pytest.mark.anyio
async def test_no_access_is_404_and_no_reading_right_is_403_before_any_423(
    api_db: Database, auth_env: dict[str, str], seed: Seed, people: dict[str, uuid.UUID]
) -> None:
    app = make_app(api_db, auth_env, StubReads())
    for person, status in (("outsider", 404), ("viewer", 403)):
        async with await _client(app, seed, people[person]) as client:
            resp = await client.get(f"/v1/cases/{people['case']}/pages/1/facts")
            assert resp.status_code == status, (person, resp.text)
    async with await _client(app, seed, people["outsider"]) as client:
        assert (await client.get(f"/v1/cases/{uuid.uuid4()}/pages/1/facts")).status_code == 404


@pytest.mark.anyio
async def test_without_a_read_log_the_gate_stays_shut(
    api_db: Database, auth_env: dict[str, str], seed: Seed, people: dict[str, uuid.UUID]
) -> None:
    app = make_app(api_db, auth_env, None)
    async with await _client(app, seed, people["expert"]) as expert:
        assert (await expert.post(f"/v1/cases/{people['case']}/pages/1/read")).status_code == 201
        resp = await expert.get(f"/v1/cases/{people['case']}/pages/1/facts")
        assert resp.status_code == 423
        assert resp.json()["error"]["code"] == "blind_read"


def test_the_gate_checks_case_access_and_is_visible_to_route_scanners() -> None:
    gate = require_page_read()
    assert gate.page_read_gate is True
    assert gate.case_action == "page.read"
    assert gate.step_up is False
    with pytest.raises(ValueError):
        require_page_read("no.such_action")


def _gated(dependant: Dependant) -> bool:
    return any(getattr(d.call, "page_read_gate", False) or _gated(d) for d in dependant.dependencies)


def test_every_page_facts_route_in_the_app_carries_the_blind_read_gate() -> None:
    routes = [
        r
        for r in create_app().routes
        if isinstance(r, APIRoute)
        and r.path.rstrip("/").endswith("/facts")
        and "/pages/" in r.path
        and "GET" in (r.methods or set())
    ]
    for route in routes:
        assert _gated(route.dependant), f"{route.path} must depend on require_page_read() (rule 3)"
