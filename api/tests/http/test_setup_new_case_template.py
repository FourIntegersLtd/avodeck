"""S04 New case: an organisation's required report template (architecture 10.1; brief "Organisation template").

`GET /v1/new-case/template?organisation_id=` tells the New case screen which report template a case for
that organisation will use. When the organisation requires one, it is used automatically (a case has no
template choice of its own) and the screen shows it locked; otherwise the expert's own template is used.
Only a current member can ask; anyone else gets 404 (rule 9 spirit: no hint that the organisation exists).
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def require(admin: psycopg.Connection, org: uuid.UUID) -> uuid.UUID:
    tid = uuid.uuid4()
    admin.execute(
        "insert into app.report_templates (id, owner_type, owner_id, filename, storage_path, sha256, size_bytes,"
        " version) values (%s, 'organisation', %s, 'house.docx', %s, %s, 10, 1)",
        (tid, org, f"templates/{tid}", "a" * 64),
    )
    admin.execute(
        "update app.organisations set policy = %s::jsonb where id = %s",
        (json.dumps({"required_template_id": str(tid)}), org),
    )
    return tid


async def test_a_required_template_is_chosen_for_the_case_and_locked(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection
) -> None:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    tid = require(admin, org)
    resp = await (await client_for(expert)).get(f"/v1/new-case/template?organisation_id={org}")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"organisation_id": str(org), "required_template_id": str(tid), "locked": True}


async def test_no_requirement_leaves_the_experts_own_template(client_for: ClientFor, seed: Seed) -> None:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    resp = await (await client_for(expert)).get(f"/v1/new-case/template?organisation_id={org}")
    assert resp.json() == {"organisation_id": str(org), "required_template_id": None, "locked": False}


async def test_only_members_ask(client_for: ClientFor, seed: Seed, admin: psycopg.Connection) -> None:
    expert, outsider = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    require(admin, org)
    resp = await (await client_for(outsider)).get(f"/v1/new-case/template?organisation_id={org}")
    assert resp.status_code == 404
