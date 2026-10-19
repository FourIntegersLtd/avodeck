"""Only the worker role may act as a case's job in slice G's functions (as foundation migration 0027).

`app.job_case_id` is a plain session setting, so the API role could set it itself; the functions that let a
job read a case without a person must also require the connecting role to be `app_worker`.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed

CALLS = [
    "select app.after_serving_served_on(%s)",
    "select app.after_serving_served_at(%s)",
    "select count(*) from app.after_serving_case_packs(%s)",
]


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(kind="team", members={expert: "owner"}), expert)


def packs_seen(dsn: str, case: uuid.UUID) -> int:
    with psycopg.connect(dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
        row = conn.execute(CALLS[2], (case,)).fetchone()
        return int(row[0]) if row else 0


def test_the_api_role_cannot_act_as_a_job_by_setting_the_job_case(api_dsn: str, case: uuid.UUID) -> None:
    assert packs_seen(api_dsn, case) == 0


def test_the_worker_role_still_reads_its_jobs_case(worker_dsn: str, case: uuid.UUID) -> None:
    assert packs_seen(worker_dsn, case) == 1


def test_the_api_role_cannot_mark_answers_served_by_setting_the_job_case(api_dsn: str, case: uuid.UUID) -> None:
    with psycopg.connect(api_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
        with pytest.raises(psycopg.Error):
            conn.execute("select app.after_serving_answers_served(%s, %s)", (case, uuid.uuid4()))
