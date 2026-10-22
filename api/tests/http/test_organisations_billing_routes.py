"""A team's billing profile (architecture 4.2, 10.8; scenario S1; permissions.md `org.billing`).

- `GET /v1/organisations/{org_id}/billing-profile`: owner or admin; `{"profile": null}` until one is saved;
  sends an ETag.
- `PUT /v1/organisations/{org_id}/billing-profile`: owner or admin, with If-Match (412 when stale). Changing
  the tax number needs step-up; other details do not.
- A personal organisation's invoices come from the expert's own profile (`/me/billing-profile`): 409 here.
- S1: agency invoices go out under the agency's profile and numbering, personal ones under the expert's:
  each issuer numbers its own invoices, one after another, with no gaps.
- Row-level security (migration 1004): only a current owner or admin adds or changes the organisation's profile.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

PROFILE = {"legal_name": "Agency Ltd", "address": "1 Street", "tax_number": "GB123", "tax_rate": "20.00",
           "currency": "GBP", "hourly_rate": "150.00", "invoice_prefix": "AG-"}  # fmt: skip


def url(w: World, org: uuid.UUID | None = None) -> str:
    return f"/v1/organisations/{org or w.agency}/billing-profile"


async def save(client_for: ClientFor, w: World, body: dict[str, str] | None = None, *, who: str = "owner") -> dict:  # type: ignore[type-arg]
    resp = await (await client_for(w[who], **{"if-match": '"0"'})).put(url(w), json=body or PROFILE)
    assert resp.status_code == 200, resp.text
    return resp.json()["profile"]  # type: ignore[no-any-return]


@pytest.mark.parametrize("who", ["owner", "admin"])
async def test_owner_or_admin_reads_and_saves_the_teams_profile(
    client_for: ClientFor, world: World, admin: psycopg.Connection, who: str
) -> None:
    client = await client_for(world[who])
    empty = await client.get(url(world))
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"profile": None}
    assert empty.headers["etag"] == '"0"'
    saved = await save(client_for, world, who=who)
    assert {k: saved[k] for k in PROFILE} == PROFILE
    again = await client.get(url(world))
    assert again.json()["profile"]["id"] == saved["id"]
    assert again.headers["etag"] == '"1"'
    assert admin.execute(
        "select owner_type, owner_id from app.billing_profiles where id = %s", (saved["id"],)
    ).fetchone() == ("organisation", world.agency)
    assert admin.execute(
        "select count(*) from app.audit_log where organisation_id = %s and action = 'billing_profile.changed'",
        (world.agency,),
    ).fetchone() == (1,)


async def test_a_stale_etag_is_refused(client_for: ClientFor, world: World) -> None:
    await save(client_for, world)
    resp = await (await client_for(world["owner"], **{"if-match": '"0"'})).put(url(world), json=PROFILE)
    assert resp.status_code == 412


async def test_only_a_new_tax_number_needs_step_up(client_for: ClientFor, world: World) -> None:
    await save(client_for, world)
    stale = await client_for(world["owner"], mfa_age=STALE, **{"if-match": '"1"'})
    renamed = await stale.put(url(world), json={**PROFILE, "legal_name": "Agency Group Ltd"})
    assert renamed.status_code == 200, renamed.text
    stale2 = await client_for(world["owner"], mfa_age=STALE, **{"if-match": '"2"'})
    taxed = await stale2.put(url(world), json={**PROFILE, "tax_number": "GB999"})
    assert (taxed.status_code, taxed.json()["error"]["code"]) == (403, "step_up_required")


@pytest.mark.parametrize("who", ["dr_k", "assistant", "viewer"])
async def test_other_members_cannot_see_or_change_it(client_for: ClientFor, world: World, who: str) -> None:
    client = await client_for(world[who], **{"if-match": '"0"'})
    assert (await client.get(url(world))).status_code == 403
    assert (await client.put(url(world), json=PROFILE)).status_code == 403


async def test_non_members_get_404(client_for: ClientFor, world: World) -> None:
    assert (await (await client_for(world["outsider"])).get(url(world))).status_code == 404


async def test_a_personal_organisation_uses_the_experts_own_profile(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world["dr_k"])).get(url(world, world.solo))
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "personal_organisation")


@pytest.mark.parametrize("body", [{**PROFILE, "currency": "pounds"}, {**PROFILE, "legal_name": ""}, {"legal_name": "X"}])
async def test_details_are_checked(client_for: ClientFor, world: World, body: dict[str, str]) -> None:
    resp = await (await client_for(world["owner"], **{"if-match": '"0"'})).put(url(world), json=body)
    assert resp.status_code == 422


async def test_s1_each_issuer_numbers_its_own_invoices_without_gaps(
    client_for: ClientFor, world: World, admin: psycopg.Connection, api_db: Database
) -> None:
    agency_profile = uuid.UUID((await save(client_for, world))["id"])
    personal_profile = admin.execute(
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency, invoice_prefix)"
        " values ('person', %s, 'Dr K', 'GBP', 'K-') returning id",
        (world["dr_k"],),
    ).fetchone()[0]  # type: ignore[index]
    team_case, solo_case = world.team_case(), world.solo_case()
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        for case, profile in ((team_case, agency_profile), (solo_case, personal_profile)):
            cur = await conn.execute("select id from app.case_billing_profile(%s)", (case,))
            assert await cur.fetchone() == (profile,)
    order = [(solo_case, personal_profile), (team_case, agency_profile), (solo_case, personal_profile),
             (team_case, agency_profile), (team_case, agency_profile)]  # fmt: skip
    for case, profile in order:
        iid = admin.execute(
            "insert into app.invoices (case_id, billing_profile_id, lines, total, currency)"
            " values (%s, %s, '[]', 10, 'GBP') returning id",
            (case, profile),
        ).fetchone()[0]  # type: ignore[index]
        admin.execute("update app.invoices set status = 'sent' where id = %s", (iid,))
    numbers = admin.execute(
        "select billing_profile_id, array_agg(number_text order by number) from app.invoices"
        " where billing_profile_id in (%s, %s) group by billing_profile_id",
        (agency_profile, personal_profile),
    ).fetchall()
    assert dict(numbers) == {agency_profile: ["AG-1", "AG-2", "AG-3"], personal_profile: ["K-1", "K-2"]}


async def test_only_owners_and_admins_add_the_profile_directly(api_db: Database, world: World) -> None:
    sql = ("insert into app.billing_profiles (owner_type, owner_id, legal_name, currency)"
           " values ('organisation', %s, 'X', 'GBP')")  # fmt: skip
    async with api_db.request(scope_for(world["dr_k"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(sql, (world.agency,))
    async with api_db.request(scope_for(world["admin"])) as conn:
        async with conn.transaction():
            await conn.execute(sql, (world.agency,))
