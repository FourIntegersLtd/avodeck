"""Slice G's jobs that change the opinion (`find_dependents`, `build_supplementary`) need `opinion.write`.

`app.enqueue_job()` takes the action from `app.job_kind_actions`; without rows these kinds fell back to
`case.upload`, which the assistant has. Only the named expert may queue them (permissions.md).
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed

KINDS = ["find_dependents", "build_supplementary"]


@pytest.fixture
def people(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, members={assistant: "assistant"})
    return {"case": case, "expert": expert, "assistant": assistant, "org": org}


def enqueue(dsn: str, people: dict[str, uuid.UUID], who: str, kind: str) -> None:
    with psycopg.connect(dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(people[who]),))
        conn.execute("select set_config('app.org_ids', %s, true)", (str(people["org"]),))
        conn.execute("select app.enqueue_job(%s, %s, 'default', 1, false, null)", (people["case"], kind))
        raise _Rollback


class _Rollback(Exception):
    pass


@pytest.mark.parametrize("kind", KINDS)
def test_the_named_expert_queues_it(api_dsn: str, people: dict[str, uuid.UUID], kind: str) -> None:
    with pytest.raises(_Rollback):
        enqueue(api_dsn, people, "expert", kind)


@pytest.mark.parametrize("kind", KINDS)
def test_the_assistant_cannot_queue_it(api_dsn: str, people: dict[str, uuid.UUID], kind: str) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege, match="opinion.write"):
        enqueue(api_dsn, people, "assistant", kind)


@pytest.mark.parametrize("kind", KINDS)
def test_the_job_rule_names_opinion_write(admin: psycopg.Connection, kind: str) -> None:
    row = admin.execute("select action from app.job_kind_actions where kind = %s", (kind,)).fetchone()
    assert row is not None and row[0] == "opinion.write"


def test_the_ten_minute_served_by_lookup_is_gone(api_dsn: str) -> None:
    """The report-served handler acts as `ReportServed.served_by`; the old lookup function is dropped."""
    with psycopg.connect(api_dsn) as conn:
        row = conn.execute("select to_regprocedure('app.after_serving_served_by(uuid, uuid)')").fetchone()
    assert row is not None and row[0] is None
