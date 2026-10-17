"""Screen 05: before you start (architecture 5.2, slice C brief, scenario S16).

The conflict questions and the paragraph their answers produce come from the jurisdiction pack's
`conflicts.yaml` (rule 11; these tests run under the fixture packs). A "yes" needs the expert's detail
where the pack says so, and the detail is stored encrypted. The screen warns when the expert's roles in
the specialty's counted settings do not cover the case period. Declaring drafts a note to every current
instructing party. Withdrawing needs step-up, records the reason (encrypted) and moves the case to
`withdrawn`. Only the named expert answers, declares and withdraws (rule 9).
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from tests.support.db import ClientFor
from tests.support.packs import FIXTURE
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CELL = str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])
CONFLICTS = FIXTURE.raw("jurisdiction", "conflicts.yaml")
KEYS = [q["key"] for q in CONFLICTS["questions"]]
DETAIL_REQUIRED = next(q["key"] for q in CONFLICTS["questions"] if q["detail_required"])
NO_DETAIL = next(q["key"] for q in CONFLICTS["questions"] if not q["detail_required"])
VOCABULARY = FIXTURE.raw("jurisdiction", "vocabulary.yaml")
SETTINGS = list(FIXTURE.raw("specialty", "pack.yaml")["coverage_roles"])
SPECIALTY = FIXTURE.case_row()["specialty"]
IP_TYPE = FIXTURE.raw("case-type", "pack.yaml")["instructing_party_types"][0]
DETAIL = "I once sat on a committee with the other side's expert."


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=CELL,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        crypto=CaseCrypto(LocalKeyProvider.generate()),
        packs=default_library(),
    )


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    expert, assistant, outsider = (seed.person() for _ in range(3))
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant"})
    admin.execute("update app.cases set period_start = '2024-02-01', period_end = '2024-09-30' where id = %s", (case,))
    return {"expert": expert, "assistant": assistant, "outsider": outsider, "org": org, "case": case}


def url(world: dict[str, uuid.UUID], tail: str) -> str:
    return f"/v1/cases/{world['case']}/{tail}"


def all_no() -> dict[str, Any]:
    return {"answers": [{"key": k, "answer": "no"} for k in KEYS]}


def role(admin: psycopg.Connection, expert: uuid.UUID, setting: str | None, period: str) -> None:
    admin.execute(
        "insert into app.expert_roles (expert_id, specialty, role_title, setting, period) values (%s, %s, %s, %s, %s)",
        (expert, SPECIALTY, "A role", setting, period),
    )


# --- answers and the paragraph ---------------------------------------------------------------------


async def test_the_questions_come_from_the_pack_with_its_words(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    got = await (await client_for(world["expert"])).get(url(world, "conflicts"))
    assert got.status_code == 200, got.status_code
    body = got.json()
    assert [q["key"] for q in body["questions"]] == KEYS
    first = body["questions"][0]
    assert VOCABULARY["subject"] in first["text"] and "{" not in first["text"]
    assert {q["answer"] for q in body["questions"]} == {None}
    assert body["complete"] is False and body["paragraph"] is None and body["declared_at"] is None


async def test_all_no_gives_the_pack_statement(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    client = await client_for(world["expert"])
    saved = await client.put(url(world, "conflicts"), json=all_no())
    assert saved.status_code == 200, saved.status_code
    body = saved.json()
    assert body["complete"] is True
    assert body["paragraph"] == CONFLICTS["statement"]["none"]


async def test_a_yes_needs_detail_and_the_detail_is_kept_encrypted(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    bare = {"answers": [{"key": DETAIL_REQUIRED, "answer": "yes"}]}
    assert (await client.put(url(world, "conflicts"), json=bare)).status_code == 422
    unknown = {"answers": [{"key": "not_a_question", "answer": "no"}]}
    assert (await client.put(url(world, "conflicts"), json=unknown)).status_code == 422
    no_with_detail = {"answers": [{"key": DETAIL_REQUIRED, "answer": "no", "detail": DETAIL}]}
    assert (await client.put(url(world, "conflicts"), json=no_with_detail)).status_code == 422

    answers = all_no()["answers"]
    answers[KEYS.index(DETAIL_REQUIRED)] = {"key": DETAIL_REQUIRED, "answer": "yes", "detail": DETAIL}
    answers[KEYS.index(NO_DETAIL)] = {"key": NO_DETAIL, "answer": "yes"}
    saved = await client.put(url(world, "conflicts"), json={"answers": answers})
    assert saved.status_code == 200, saved.status_code
    body = saved.json()
    assert body["complete"] is True
    assert body["paragraph"].startswith(CONFLICTS["statement"]["disclosed"])
    assert DETAIL in body["paragraph"]
    asked = {q["key"]: q for q in body["questions"]}
    assert (asked[DETAIL_REQUIRED]["answer"], asked[DETAIL_REQUIRED]["detail"]) == ("yes", DETAIL)
    stored = admin.execute("select detail from app.conflict_answers where case_id = %s", (world["case"],)).fetchall()
    assert all(r[0] is None or DETAIL.encode() not in bytes(r[0]) for r in stored)


async def test_only_the_named_expert_answers(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    assistant = await client_for(world["assistant"])
    assert (await assistant.get(url(world, "conflicts"))).status_code == 200
    assert (await assistant.put(url(world, "conflicts"), json=all_no())).status_code == 403
    assert (await assistant.post(url(world, "conflicts/declare"))).status_code == 403
    outsider = await client_for(world["outsider"])
    assert (await outsider.get(url(world, "conflicts"))).status_code == 404
    assert (await outsider.put(url(world, "conflicts"), json=all_no())).status_code == 404


# --- does the expert's practice cover the case period ---------------------------------------------


async def test_the_screen_warns_until_a_counted_role_covers_the_whole_period(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])

    async def coverage() -> tuple[str, list[str]]:
        body = (await client.get(url(world, "conflicts"))).json()
        return body["coverage"]["status"], body["warnings"]

    assert await coverage() == ("none", ["setup.conflicts.warning.coverage_none"])
    role(admin, world["expert"], None, "[2023-01-01,)")  # a setting the specialty does not count
    assert (await coverage())[0] == "none"
    role(admin, world["expert"], SETTINGS[0], "[2023-01-01,2024-06-01)")
    assert await coverage() == ("partial", ["setup.conflicts.warning.coverage_partial"])
    role(admin, world["expert"], SETTINGS[-1], "[2024-06-01,)")
    assert await coverage() == ("covered", [])
    assistant = await client_for(world["assistant"])
    assert (await assistant.get(url(world, "conflicts"))).json()["coverage"]["status"] == "covered"

    admin.execute("update app.cases set period_start = null, period_end = null where id = %s", (world["case"],))
    assert (await coverage())[0] == "unknown"


# --- declaring -------------------------------------------------------------------------------------


async def test_declaring_drafts_a_note_to_every_current_instructing_party(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "conflicts/declare"))).status_code == 409  # not answered yet
    for firm in ("North Firm", "South Firm"):
        body = {"type_key": IP_TYPE, "name": "A Person", "firm": firm, "email": "a@example.test"}
        assert (await client.post(url(world, "instructing-parties"), json=body)).status_code == 201
    answers = all_no()["answers"]
    answers[KEYS.index(DETAIL_REQUIRED)] = {"key": DETAIL_REQUIRED, "answer": "yes", "detail": DETAIL}
    assert (await client.put(url(world, "conflicts"), json={"answers": answers})).status_code == 200

    declared = await client.post(url(world, "conflicts/declare"))
    assert declared.status_code == 200, declared.status_code
    body = declared.json()
    assert body["outcome"] == "disclosed"
    note = body["note"]
    assert note["topic"] == "conflict_declared" and note["joint"] is True
    assert sorted(r["firm"] for r in note["recipients"]) == ["North Firm", "South Firm"]
    assert [i["text"] for i in note["items"]] == [DETAIL]
    assert (await client.get(url(world, "conflicts"))).json()["declared_at"] is not None


# --- withdrawing -----------------------------------------------------------------------------------


async def test_withdrawing_needs_step_up_and_records_the_reason(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    reason = {"reason": "I have found I know one of the people involved."}
    stale = await client_for(world["expert"], mfa_age=timedelta(minutes=20))
    refused = await stale.post(url(world, "withdraw"), json=reason)
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "step_up_required"
    assistant = await client_for(world["assistant"])
    assert (await assistant.post(url(world, "withdraw"), json=reason)).status_code == 403
    client = await client_for(world["expert"])
    assert (await client.post(url(world, "withdraw"), json={"reason": ""})).status_code == 422

    done = await client.post(url(world, "withdraw"), json=reason)
    assert done.status_code == 200, done.status_code
    body = done.json()
    assert body["status"] == "withdrawn"
    assert body["deadlines_cancelled"] == 0
    assert body["note"]["topic"] == "withdrawal"
    row = admin.execute("select status from app.cases where id = %s", (world["case"],)).fetchone()
    assert row is not None and row[0] == "withdrawn"
    stored = admin.execute("select reason from app.case_withdrawals where case_id = %s", (world["case"],)).fetchone()
    assert stored is not None and reason["reason"].encode() not in bytes(stored[0])
    assert (await client.post(url(world, "withdraw"), json=reason)).status_code == 409


async def test_a_served_case_can_be_withdrawn(client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID]) -> None:
    served = seed.case(world["org"], world["expert"], status="served")
    client = await client_for(world["expert"])
    done = await client.post(f"/v1/cases/{served}/withdraw", json={"reason": "Ill health."})
    assert done.status_code == 200, done.status_code
    assert done.json()["status"] == "withdrawn"
