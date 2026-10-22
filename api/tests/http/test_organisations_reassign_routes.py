# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Reassigning a case's named expert (architecture 5.4, 10.1 "Reassignment"; scenario S3; permissions.md).

`POST /v1/cases/{case_id}/named-expert` 🔒 (`case.reassign_named_expert`: an organisation owner or admin):
- only before serving: once served, 409 `served_use_replacement`, offering a linked replacement case;
- the new named expert is a current owner, admin or expert member of the case's organisation (422 otherwise);
- the outgoing expert is told (a notification and `case_reassigned_mail`); the incoming one gets the mail too;
- S3: the new expert starts with no reads and no verifications; the facts' suggestions stay;
- the outgoing expert, not on the case in any other way, no longer sees it.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest
from fastapi import FastAPI

from app.core.auth.access import check_case_access
from app.core.contracts.facts import citable_facts
from app.core.contracts.reading import PageReads
from app.core.db import Database
from app.core.email import CASE_REASSIGNED
from app.core.errors import ApiError
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.contracts import add_fact
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio


def url(case: uuid.UUID) -> str:
    return f"/v1/cases/{case}/named-expert"


def worked_case(w: World, status: str = "active") -> tuple[uuid.UUID, list[uuid.UUID]]:
    """A case Dr K has read and checked: two pages read, one fact verified, one still a suggestion."""
    case = w.team_case(status=status, members={"assistant": "assistant"})
    doc = w.seed.document(case)
    pages = [w.seed.page(case, doc, n) for n in (1, 2)]
    facts = [add_fact(w.admin, case, pages[0], status="verified"), add_fact(w.admin, case, pages[1])]
    for page in pages:
        w.admin.execute(
            "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds, counts_for_citing)"
            " values (%s, %s, %s, 20, true)",
            (case, page, w["dr_k"]),
        )
    w.admin.execute(
        "insert into app.fact_verifications (case_id, fact_id, person_id, action) values (%s, %s, %s, 'correct')",
        (case, facts[0], w["dr_k"]),
    )
    return case, facts


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_s3_an_unserved_case_moves_to_dr_m_who_starts_from_zero(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database, app: FastAPI, who: str
) -> None:
    case, facts = worked_case(world)
    resp = await (await client_for(world[who])).post(url(case), json={"person_id": str(world["dr_m"])})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"case_id": str(case), "named_expert_id": str(world["dr_m"])}
    assert admin.execute("select named_expert_id from app.cases where id = %s", (case,)).fetchone() == (world["dr_m"],)

    async with api_db.request(scope_for(world["dr_m"])) as conn:
        assert await PageReads().read_page_ids(conn, case, world["dr_m"]) == set()
        assert await citable_facts(conn, case, PageReads()) == []
        cur = await conn.execute(
            "select count(*) from app.fact_verifications where case_id = %s and person_id = %s", (case, world["dr_m"])
        )
        assert await cur.fetchone() == (0,)
        cur = await conn.execute("select count(*) from app.facts where case_id = %s", (case,))
        assert await cur.fetchone() == (len(facts),)
        access = await check_case_access(conn, case, "fact.verify")
        assert access.case_role == "named_expert"
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        with pytest.raises(ApiError) as err:
            await check_case_access(conn, case, "case.view_metadata")
        assert err.value.status == 404

    mails = {(m.to, m.params["side"]) for m in app.state.mailer.outbox if m.template == CASE_REASSIGNED}
    assert mails == {(world.email("dr_k"), "outgoing"), (world.email("dr_m"), "incoming")}
    assert admin.execute(
        "select count(*) from app.notifications where person_id = %s and case_id = %s and kind = 'case.reassigned'",
        (world["dr_k"], case),
    ).fetchone() == (1,)
    assert admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.named_expert_changed'", (case,)
    ).fetchone() == (1,)


async def test_the_outgoing_expert_who_already_left_still_gets_the_mail(
    client_for: ClientFor, world: World, app: FastAPI
) -> None:
    case = world.team_case()
    world.seed.end_membership(world.agency, world["dr_k"])
    resp = await (await client_for(world["owner"])).post(url(case), json={"person_id": str(world["dr_m"])})
    assert resp.status_code == 200, resp.text
    assert (world.email("dr_k"), "outgoing") in {
        (m.to, m.params["side"]) for m in app.state.mailer.outbox if m.template == CASE_REASSIGNED
    }


@pytest.mark.parametrize("status", ["served", "settled"])
async def test_s3_after_serving_it_is_409_with_a_replacement_offer(
    client_for: ClientFor, world: World, admin: psycopg.Connection, status: str
) -> None:
    case = world.team_case(status=status)
    resp = await (await client_for(world["owner"])).post(url(case), json={"person_id": str(world["dr_m"])})
    assert resp.status_code == 409
    error = resp.json()["error"]
    assert error["code"] == "served_use_replacement"
    assert error["replacement"] == {"method": "POST", "path": f"/v1/cases/{case}/replacement"}
    assert admin.execute("select named_expert_id from app.cases where id = %s", (case,)).fetchone() == (world["dr_k"],)


@pytest.mark.parametrize("target", ["assistant", "viewer", "outsider", "left"])
async def test_the_new_expert_must_be_a_current_expert_member(
    client_for: ClientFor, world: World, admin: psycopg.Connection, target: str
) -> None:
    case = world.team_case()
    person = world["dr_m"] if target == "left" else world[target]
    if target == "left":
        world.seed.end_membership(world.agency, person)
    resp = await (await client_for(world["owner"])).post(url(case), json={"person_id": str(person)})
    assert resp.status_code == 422, resp.text
    assert admin.execute("select named_expert_id from app.cases where id = %s", (case,)).fetchone() == (world["dr_k"],)


async def test_an_owner_or_admin_may_take_the_case_themselves(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    resp = await (await client_for(world["admin"])).post(url(case), json={"person_id": str(world["admin"])})
    assert resp.status_code == 200, resp.text


async def test_naming_the_same_expert_again_is_409(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    resp = await (await client_for(world["owner"])).post(url(case), json={"person_id": str(world["dr_k"])})
    assert resp.status_code == 409


@pytest.mark.parametrize(("who", "status"), [("dr_k", 403), ("assistant", 403), ("dr_m", 404), ("outsider", 404)])
async def test_only_owners_and_admins_reassign(client_for: ClientFor, world: World, who: str, status: int) -> None:
    case = world.team_case(members={"assistant": "assistant"})
    resp = await (await client_for(world[who])).post(url(case), json={"person_id": str(world["dr_m"])})
    assert resp.status_code == status


async def test_reassignment_needs_step_up(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    resp = await (await client_for(world["owner"], mfa_age=STALE)).post(
        url(case), json={"person_id": str(world["dr_m"])}
    )
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
