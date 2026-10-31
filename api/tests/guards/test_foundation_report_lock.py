"""Rule 8: a locked report version is immutable.

The database refuses any change to a `report_versions` row once `locked_at` is set (SQLSTATE
`AVLCK`), except the two documented moves locked -> served and served -> withdrawn. Through the
API that refusal is 409 `locked`, whichever route tried, so no slice can work around it.
"""

from __future__ import annotations

import uuid

import httpx
import psycopg
import pytest
from fastapi import Depends, FastAPI

from app.core.auth.access import require_case_access
from app.core.contracts.reports import ensure_unlocked, locked
from app.core.db import Database
from app.core.errors import ApiError
from app.core.http import DbConn, router
from app.main import mount
from tests.support.auth import APP_ORIGIN, CSRF_HEADER, cookie_header
from tests.support.contracts import add_report_version
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def stub_app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    stub = router()

    @stub.patch("/v1/cases/{case_id}/stub-reports/{report_id}")
    async def rename(
        case_id: uuid.UUID, report_id: uuid.UUID, conn: DbConn, _: object = Depends(require_case_access("report.build"))
    ) -> dict[str, bool]:
        await conn.execute(
            "update app.report_versions set display_name = 'Renamed' where id = %s and case_id = %s",
            (report_id, case_id),
        )
        return {"ok": True}

    @stub.post("/v1/cases/{case_id}/stub-reports/{report_id}/check")
    async def check(
        case_id: uuid.UUID, report_id: uuid.UUID, conn: DbConn, _: object = Depends(require_case_access("report.build"))
    ) -> dict[str, bool]:
        await ensure_unlocked(conn, report_id)
        return {"ok": True}

    app = build_app(api_db, auth_env)
    mount(app, stub)
    return app


@pytest.fixture
def expert_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    return expert, seed.case(seed.org(kind="personal", members={expert: "owner"}), expert)


async def _client(app: FastAPI, seed: Seed, person: uuid.UUID) -> httpx.AsyncClient:
    session = seed.session(person)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="https://test",
        headers={"cookie": cookie_header(session), CSRF_HEADER: session.csrf, "origin": APP_ORIGIN},
    )


def test_the_database_refuses_changing_a_locked_report_version(
    admin: psycopg.Connection, expert_case: tuple[uuid.UUID, uuid.UUID]
) -> None:
    report = add_report_version(admin, expert_case[1], status="locked")
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.report_versions set display_name = 'Changed' where id = %s", (report,))
    assert err.value.sqlstate == "AVLCK"


async def test_changing_a_locked_report_version_through_any_route_is_409_locked(
    api_db: Database,
    auth_env: dict[str, str],
    seed: Seed,
    admin: psycopg.Connection,
    expert_case: tuple[uuid.UUID, uuid.UUID],
) -> None:
    expert, case = expert_case
    app = stub_app(api_db, auth_env)
    draft = add_report_version(admin, case, version_no=1)
    served = add_report_version(admin, case, version_no=2, status="served")
    async with await _client(app, seed, expert) as client:
        assert (await client.patch(f"/v1/cases/{case}/stub-reports/{draft}")).status_code == 200
        resp = await client.patch(f"/v1/cases/{case}/stub-reports/{served}")
        assert resp.status_code == 409, resp.text
        assert resp.json()["error"]["code"] == "locked"
        assert (await client.post(f"/v1/cases/{case}/stub-reports/{draft}/check")).status_code == 200
        resp = await client.post(f"/v1/cases/{case}/stub-reports/{served}/check")
        assert resp.status_code == 409
        assert resp.json()["error"]["code"] == "locked"
    row = admin.execute("select display_name from app.report_versions where id = %s", (served,)).fetchone()
    assert row == ("Report 1",)


def test_locked_is_a_409_api_error() -> None:
    err = locked()
    assert isinstance(err, ApiError)
    assert (err.code, err.status) == ("locked", 409)
