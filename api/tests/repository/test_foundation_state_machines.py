"""docs/spec/state-machines.md is enforced twice: by the service (409 `conflict`) and by a
database trigger, so a bug or a direct SQL write cannot skip a step."""

from __future__ import annotations

import itertools
import re
import uuid
from pathlib import Path

import psycopg
import pytest

from app.core.db import Database
from app.core.errors import ApiError
from app.core.state_machines import (
    CASE_STATUS_TRANSITIONS,
    JOB_STATUS_TRANSITIONS,
    change_case_status,
    change_job_status,
)
from tests.support.db import scope_for
from tests.support.seed import Seed

REPO = Path(__file__).resolve().parents[3]
CASE_STATUSES = ("draft", "processing", "active", "served", "settled", "withdrawn", "archived", "deleted")
JOB_STATUSES = ("queued", "running", "done", "failed", "paused")
# From the job diagram in state-machines.md (it has no table).
JOB_EDGES = {
    ("queued", "running"),
    ("running", "done"),
    ("running", "failed"),
    ("failed", "queued"),
    ("running", "paused"),
    ("paused", "running"),
}


def documented_case_edges() -> set[tuple[str, str]]:
    text = (REPO / "docs/spec/state-machines.md").read_text()
    section = text.split("## Case (`cases.status`)", 1)[1].split("\n## ", 1)[0]
    edges: set[tuple[str, str]] = set()
    for line in section.splitlines():
        m = re.match(r"\|\s*([a-z/]+|any)\s*→\s*([a-z]+)\*?\s*\|", line)
        if not m:
            continue
        sources = [s for s in CASE_STATUSES if s != "deleted"] if m.group(1) == "any" else m.group(1).split("/")
        edges |= {(s, m.group(2)) for s in sources if s != m.group(2)}
    return edges


def code_edges(table: dict[str, frozenset[str]]) -> set[tuple[str, str]]:
    return {(a, b) for a, targets in table.items() for b in targets}


def test_case_transitions_in_code_are_exactly_the_documented_ones() -> None:
    assert len(documented_case_edges()) > 10
    assert code_edges(CASE_STATUS_TRANSITIONS) == documented_case_edges()


def test_job_transitions_in_code_are_exactly_the_documented_ones() -> None:
    assert code_edges(JOB_STATUS_TRANSITIONS) == JOB_EDGES


def test_deleted_is_terminal() -> None:
    assert not CASE_STATUS_TRANSITIONS.get("deleted")


# --- database trigger ---------------------------------------------------------------------------


@pytest.fixture
def team_case(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    return seed.case(org, expert), expert


def _case_at(seed: Seed, status: str) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status=status)


ILLEGAL_CASE = sorted(
    (a, b)
    for a, b in itertools.product(CASE_STATUSES, CASE_STATUSES)
    if a != b and (a, b) not in documented_case_edges()
)


@pytest.mark.parametrize(("start", "target"), ILLEGAL_CASE)
def test_database_refuses_an_undocumented_case_transition(
    seed: Seed, admin: psycopg.Connection, start: str, target: str
) -> None:
    case = _case_at(seed, start)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.cases set status = %s where id = %s", (target, case))
    assert err.value.sqlstate == "AV409"


@pytest.mark.parametrize(("start", "target"), sorted(documented_case_edges()))
def test_database_allows_every_documented_case_transition(
    seed: Seed, admin: psycopg.Connection, start: str, target: str
) -> None:
    case = _case_at(seed, start)
    admin.execute("update app.cases set status = %s where id = %s", (target, case))


def test_a_new_case_starts_as_a_draft(seed: Seed, admin: psycopg.Connection) -> None:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    with pytest.raises(psycopg.Error) as err:
        admin.execute(
            "insert into app.cases (organisation_id, named_expert_id, jurisdiction, case_type, specialty,"
            " expert_role, label, status) values (%s, %s, 'zz-test', 'zz-case', 'zz-specialty', 'assessor',"
            " 'Z', 'served')",
            (org, expert),
        )
    assert err.value.sqlstate == "AV409"


@pytest.mark.parametrize(
    ("start", "target"),
    sorted((a, b) for a, b in itertools.product(JOB_STATUSES, JOB_STATUSES) if a != b and (a, b) not in JOB_EDGES),
)
def test_database_refuses_an_undocumented_job_transition(
    team_case: tuple[uuid.UUID, uuid.UUID], seed: Seed, admin: psycopg.Connection, start: str, target: str
) -> None:
    job = seed.job(team_case[0])
    path = {
        "queued": [],
        "running": ["running"],
        "done": ["running", "done"],
        "failed": ["running", "failed"],
        "paused": ["running", "paused"],
    }[start]
    for step in path:
        admin.execute("update app.jobs set status = %s where id = %s", (step, job))
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.jobs set status = %s where id = %s", (target, job))
    assert err.value.sqlstate == "AV409"


# --- service ------------------------------------------------------------------------------------


@pytest.mark.anyio
async def test_service_refuses_an_undocumented_case_transition_with_409(
    api_db: Database, team_case: tuple[uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    case, expert = team_case
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(ApiError) as err:
            await change_case_status(conn, case, "served")
    assert (err.value.status, err.value.code) == (409, "conflict")
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == ("draft",)


@pytest.mark.anyio
async def test_service_makes_a_documented_case_transition(
    api_db: Database, team_case: tuple[uuid.UUID, uuid.UUID], admin: psycopg.Connection
) -> None:
    case, expert = team_case
    async with api_db.request(scope_for(expert)) as conn:
        await change_case_status(conn, case, "processing")
    assert admin.execute("select status from app.cases where id = %s", (case,)).fetchone() == ("processing",)


@pytest.mark.anyio
async def test_service_refuses_an_undocumented_job_transition_with_409(
    api_db: Database, team_case: tuple[uuid.UUID, uuid.UUID], seed: Seed
) -> None:
    case, expert = team_case
    job = seed.job(case)
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(ApiError) as err:
            await change_job_status(conn, job, "done")
    assert err.value.status == 409


# --- memberships and page flags -----------------------------------------------------------------


def test_the_last_owner_cannot_leave(seed: Seed, admin: psycopg.Connection) -> None:
    owner = seed.person()
    org = seed.org(kind="team", members={owner: "owner"})
    with pytest.raises(psycopg.Error) as err:
        seed.end_membership(org, owner)
    assert err.value.sqlstate == "AV409"


def test_an_owner_can_leave_while_another_owner_stays(seed: Seed, admin: psycopg.Connection) -> None:
    a, b = seed.person(), seed.person()
    org = seed.org(kind="team", members={a: "owner", b: "owner"})
    seed.end_membership(org, a)


def test_a_personal_organisation_membership_never_ends_while_the_account_is_open(seed: Seed) -> None:
    person, partner = seed.person(), seed.person()
    org = seed.org(kind="personal", members={person: "owner", partner: "owner"})
    with pytest.raises(psycopg.Error) as err:
        seed.end_membership(org, person)
    assert err.value.sqlstate == "AV409"


def test_an_ended_membership_cannot_be_reopened(seed: Seed, admin: psycopg.Connection) -> None:
    a, b = seed.person(), seed.person()
    org = seed.org(kind="team", members={a: "owner", b: "viewer"})
    seed.end_membership(org, b)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.memberships set ended_at = null where person_id = %s", (b,))
    assert err.value.sqlstate == "AV409"


@pytest.fixture
def page(team_case: tuple[uuid.UUID, uuid.UUID], seed: Seed) -> uuid.UUID:
    case = team_case[0]
    return seed.page(case, seed.document(case), 7)


def test_a_page_number_never_changes(page: uuid.UUID, admin: psycopg.Connection) -> None:
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.pages set bundle_page_no = 8 where id = %s", (page,))
    assert err.value.sqlstate == "AV409"


def test_a_superseded_page_is_never_restored(
    page: uuid.UUID, team_case: tuple[uuid.UUID, uuid.UUID], seed: Seed, admin: psycopg.Connection
) -> None:
    case = team_case[0]
    clearer = seed.page(case, seed.document(case), 9)
    admin.execute("update app.pages set superseded_by_page_id = %s where id = %s", (clearer, page))
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.pages set superseded_by_page_id = null where id = %s", (page,))
    assert err.value.sqlstate == "AV409"


def test_an_unreadable_page_becomes_readable_only_with_the_experts_transcription(
    page: uuid.UUID, admin: psycopg.Connection
) -> None:
    admin.execute("update app.pages set unreadable = true where id = %s", (page,))
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.pages set unreadable = false where id = %s", (page,))
    assert err.value.sqlstate == "AV409"
    admin.execute(
        "update app.pages set unreadable = false, transcribed_by_expert_text = 'typed' where id = %s", (page,)
    )
