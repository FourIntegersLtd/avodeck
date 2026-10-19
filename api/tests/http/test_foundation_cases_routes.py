"""GET and PATCH /v1/cases/{case_id}: metadata with allowed_actions, ETag and If-Match (F26)."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.auth.permissions import CASE_ACTIONS
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "viewer", "assistant", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner", p["viewer"]: "viewer", p["assistant"]: "assistant"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["viewer"]: "viewer", p["assistant"]: "assistant"})
    p["org"] = org
    return p


async def test_get_returns_metadata_allowed_actions_and_an_etag(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    resp = await (await client_for(team["viewer"])).get(f"/v1/cases/{team['case']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == str(team["case"])
    assert (body["jurisdiction"], body["case_type"], body["specialty"]) == ("zz-test", "zz-case", "zz-specialty")
    assert body["status"] == "draft"
    assert body["named_expert_id"] == str(team["expert"])
    assert set(body["allowed_actions"]) == {"case.view_metadata", "case.view_content"}
    assert resp.headers["etag"] == '"1"'


async def test_named_expert_allowed_actions_cover_their_column(
    client_for: ClientFor, team: dict[str, uuid.UUID]
) -> None:
    body = (await (await client_for(team["expert"])).get(f"/v1/cases/{team['case']}")).json()
    assert "fact.verify" in body["allowed_actions"]
    assert set(body["allowed_actions"]) <= set(CASE_ACTIONS)


async def test_outsider_gets_404(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(team["outsider"])).get(f"/v1/cases/{team['case']}")
    assert resp.status_code == 404


async def test_patch_with_the_current_etag_saves_and_returns_a_new_one(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(team["assistant"])
    resp = await client.patch(f"/v1/cases/{team['case']}", json={"label": "Case Y"}, headers={"if-match": '"1"'})
    assert resp.status_code == 200, resp.text
    assert resp.json()["label"] == "Case Y"
    assert resp.headers["etag"] == '"2"'
    row = admin.execute(
        "select action, person_id from app.audit_log where case_id = %s order by seq desc limit 1", (team["case"],)
    ).fetchone()
    assert row == ("case.updated", team["assistant"])


async def test_stale_if_match_returns_412_and_changes_nothing(
    client_for: ClientFor, team: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    """F26: someone else saved first."""
    client = await client_for(team["expert"])
    first = await client.patch(f"/v1/cases/{team['case']}", json={"label": "First"}, headers={"if-match": '"1"'})
    assert first.status_code == 200
    stale = await client.patch(f"/v1/cases/{team['case']}", json={"label": "Second"}, headers={"if-match": '"1"'})
    assert stale.status_code == 412
    assert stale.json()["error"]["code"] == "stale"
    assert admin.execute("select label, version from app.cases where id = %s", (team["case"],)).fetchone() == (
        "First",
        2,
    )


async def test_patch_without_if_match_is_refused(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(team["expert"])).patch(f"/v1/cases/{team['case']}", json={"label": "X"})
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "precondition_required"


async def test_a_viewer_cannot_change_case_details(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(team["viewer"])).patch(
        f"/v1/cases/{team['case']}", json={"label": "X"}, headers={"if-match": '"1"'}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


async def test_patch_cannot_change_status(client_for: ClientFor, team: dict[str, uuid.UUID]) -> None:
    resp = await (await client_for(team["expert"])).patch(
        f"/v1/cases/{team['case']}", json={"status": "served"}, headers={"if-match": '"1"'}
    )
    assert resp.status_code == 422


@pytest.mark.parametrize(
    ("status", "stage"),
    [
        ("draft", "gather"),
        ("processing", "gather"),
        ("active", "review"),
        ("served", "conclude"),
        ("archived", "conclude"),
    ],
)
async def test_get_names_the_pack_step_the_case_is_at(
    client_for: ClientFor, seed: Seed, status: str, stage: str
) -> None:
    """Opening a case lands on its step, so the frame never shows "not ready" for a case that is (F26)."""
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status=status)
    resp = await (await client_for(expert)).get(f"/v1/cases/{case}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["stage"] == stage
