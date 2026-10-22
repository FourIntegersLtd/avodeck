# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Closing an account (architecture 5.4, 10.1 "Account closure"; scenario S15; permissions.md "Closed accounts").

`POST /v1/me/close` 🔒:
- 409 `export_first` (listing the cases) until every unserved case of the person's own practice on which
  they are the named expert has a finished export they asked for (`case_export_done`);
- then asks slice H to delete each of those cases (`request_case_deletion`: H's `delete_case` job), ends the
  person's team memberships and sets `people.closed_at`;
- the last owner of a team cannot close their account (409 `last_owner`), and nothing changes;
- S15: served cases are kept, read-only, and the person can still sign in to read them; every other action,
  case and organisation answers 404. (legal check)
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.access import check_case_access
from app.core.db import Database
from app.core.errors import ApiError
from app.core.features import load_features
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

URL = "/v1/me/close"


@pytest.fixture(autouse=True)
def _job_kinds() -> None:
    load_features()  # slice H registers `delete_case` and `export_case`


def exported(w: World, case: uuid.UUID) -> None:
    job = w.seed.job(case, kind="export_case", requested_by=w["dr_k"])
    for step in ("running", "done"):
        w.admin.execute("update app.jobs set status = %s where id = %s", (step, job))


def closed_at(admin: psycopg.Connection, w: World) -> object:
    row = admin.execute("select closed_at from app.people where id = %s", (w["dr_k"],)).fetchone()
    return None if row is None else row[0]


async def test_export_first(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    done, waiting = world.solo_case(), world.solo_case(status="draft")
    world.solo_case(status="served")  # served: kept, needs no export to close
    exported(world, done)
    resp = await (await client_for(world["dr_k"])).post(URL)
    assert resp.status_code == 409, resp.text
    error = resp.json()["error"]
    assert error["code"] == "export_first"
    assert error["case_ids"] == [str(waiting)]
    assert closed_at(admin, world) is None
    assert admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'delete_case'", (done,)
    ).fetchone() == (0,)


async def test_s15_closing_deletes_unserved_cases_keeps_served_ones_read_only(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database
) -> None:
    unserved = world.solo_case()
    served = world.solo_case(status="served")
    settled = world.solo_case(status="settled")
    team_named = world.team_case(status="served")
    exported(world, unserved)
    client = await client_for(world["dr_k"])
    resp = await client.post(URL)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["closed_at"]
    jobs = admin.execute(
        "select id, case_id from app.jobs where kind = 'delete_case' and case_id = any(%s)",
        ([unserved, served, settled],),
    ).fetchall()
    assert [case for _, case in jobs] == [unserved]
    assert body["deletion_job_ids"] == [str(jobs[0][0])]
    assert closed_at(admin, world) is not None
    assert world.membership_row(world.agency, "dr_k") == ("expert", True)
    assert admin.execute(
        "select count(*) from app.audit_log where person_id = %s and action = 'person.closed'", (world["dr_k"],)
    ).fetchone() == (1,)

    async with api_db.request(scope_for(world["dr_k"])) as conn:
        for case in (served, settled, team_named):
            access = await check_case_access(conn, case, "case.view_content")
            assert set(access.allowed_actions) <= {
                "case.view_metadata", "case.view_content", "case.view_audit", "case.view_certificate"
            }  # fmt: skip
            for action in ("case.edit_setup", "report.withdraw", "case.export"):
                with pytest.raises(ApiError) as err:
                    await check_case_access(conn, case, action)
                assert err.value.status == 404
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, unserved, "case.view_metadata")
        assert err.value.status == 404

    # Sign-in only: the session still works, organisations are gone.
    assert (await client.get(f"/v1/cases/{served}")).status_code == 200
    assert (await client.get("/v1/organisations")).json() == {"organisations": []}
    assert (await client.get(f"/v1/organisations/{world.solo}")).status_code == 404
    again = await client.post(URL)
    assert again.status_code == 404


async def test_the_last_owner_of_a_team_cannot_close(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    resp = await (await client_for(world["owner"])).post(URL)
    assert resp.status_code == 409
    assert resp.json()["error"] == {"code": "last_owner", "message": "Make someone else an owner first."}
    assert admin.execute("select closed_at from app.people where id = %s", (world["owner"],)).fetchone() == (None,)


async def test_a_case_on_hold_stops_closure(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    case = world.solo_case()
    exported(world, case)
    admin.execute("update app.cases set on_hold = true where id = %s", (case,))
    resp = await (await client_for(world["dr_k"])).post(URL)
    assert resp.status_code == 409
    assert closed_at(admin, world) is None


async def test_closing_needs_step_up(client_for: ClientFor, world: World, admin: psycopg.Connection) -> None:
    resp = await (await client_for(world["dr_k"], mfa_age=STALE)).post(URL)
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
    assert closed_at(admin, world) is None


async def test_with_nothing_to_delete_the_account_closes_at_once(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    resp = await (await client_for(world["outsider"])).post(URL)
    assert resp.status_code == 200, resp.text
    assert resp.json()["deletion_job_ids"] == []
