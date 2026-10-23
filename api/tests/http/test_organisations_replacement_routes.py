# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""A replacement case for a served one (architecture 10.1 "Reassignment"; scenario S3 after serving).

`POST /v1/cases/{case_id}/replacement` 🔒 (`case.reassign_named_expert`: an organisation owner or admin)
calls `core/contracts/replacement.py`: a new draft case in the same organisation, named to the new expert,
linked `replaces` to the served case, recording whether the instructing party consented to the bundle
being copied. Nothing is copied here (slice A's, only with consent). An unserved case is 409 `not_served`
(change its expert instead); an expert who cannot take it 422. The served case does not change.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.fixtures.test_organisations_world import STALE, World, world  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


def url(case: uuid.UUID) -> str:
    return f"/v1/cases/{case}/replacement"


@pytest.mark.parametrize("consented", [True, False])
async def test_s3_after_serving_the_offer_leads_to_a_linked_replacement_case(
    client_for: ClientFor, world: World, admin: psycopg.Connection, consented: bool
) -> None:
    served = world.team_case(status="served")
    owner = await client_for(world["owner"])
    refused = await owner.post(f"/v1/cases/{served}/named-expert", json={"person_id": str(world["dr_m"])})
    assert refused.status_code == 409
    offer = refused.json()["error"]["replacement"]
    assert offer == {"method": "POST", "path": url(served)}

    resp = await owner.post(
        offer["path"], json={"named_expert_id": str(world["dr_m"]), "instructing_party_consented": consented}
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    new = uuid.UUID(body["case_id"])
    assert (body["replaces_case_id"], body["named_expert_id"], body["instructing_party_consented"]) == (
        str(served), str(world["dr_m"]), consented,
    )  # fmt: skip
    assert admin.execute(
        "select organisation_id, named_expert_id, status from app.cases where id = %s", (new,)
    ).fetchone() == (world.agency, world["dr_m"], "draft")
    assert admin.execute(
        "select kind from app.case_links where case_id = %s and linked_case_id = %s", (new, served)
    ).fetchone() == ("replaces",)
    assert admin.execute(
        "select instructing_party_consented from app.case_replacements where case_id = %s", (new,)
    ).fetchone() == (consented,)
    assert admin.execute("select named_expert_id, status from app.cases where id = %s", (served,)).fetchone() == (
        world["dr_k"], "served",
    )  # fmt: skip
    assert admin.execute("select count(*) from app.pages where case_id = %s", (new,)).fetchone() == (0,)
    dr_m = await (await client_for(world["dr_m"])).get(f"/v1/cases/{new}")
    assert dr_m.status_code == 200, dr_m.text


async def test_an_unserved_case_changes_its_expert_instead(client_for: ClientFor, world: World) -> None:
    case = world.team_case()
    resp = await (await client_for(world["owner"])).post(
        url(case), json={"named_expert_id": str(world["dr_m"]), "instructing_party_consented": True}
    )
    assert (resp.status_code, resp.json()["error"]["code"]) == (409, "not_served")


@pytest.mark.parametrize("who", ["assistant", "outsider"])
async def test_the_new_expert_must_be_able_to_take_it(client_for: ClientFor, world: World, who: str) -> None:
    served = world.team_case(status="served")
    resp = await (await client_for(world["owner"])).post(
        url(served), json={"named_expert_id": str(world[who]), "instructing_party_consented": False}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(("who", "status"), [("dr_k", 403), ("dr_m", 404), ("outsider", 404)])
async def test_only_owners_and_admins_replace(client_for: ClientFor, world: World, who: str, status: int) -> None:
    served = world.team_case(status="served")
    resp = await (await client_for(world[who])).post(
        url(served), json={"named_expert_id": str(world["dr_m"]), "instructing_party_consented": True}
    )
    assert resp.status_code == status


async def test_consent_must_be_stated(client_for: ClientFor, world: World) -> None:
    served = world.team_case(status="served")
    resp = await (await client_for(world["owner"])).post(url(served), json={"named_expert_id": str(world["dr_m"])})
    assert resp.status_code == 422


async def test_replacing_needs_step_up(client_for: ClientFor, world: World) -> None:
    served = world.team_case(status="served")
    resp = await (await client_for(world["admin"], mfa_age=STALE)).post(
        url(served), json={"named_expert_id": str(world["dr_m"]), "instructing_party_consented": True}
    )
    assert (resp.status_code, resp.json()["error"]["code"]) == (403, "step_up_required")
