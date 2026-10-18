"""Only the worker role may act as a case's job (migration 0027).

`app.job_case_id` is a plain session setting, so the API role could set it itself. The report-input check
(`app.case_signature` and the other report-input functions, 0026) therefore also requires the connecting
role to be `app_worker`.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed


@pytest.fixture
def case(seed: Seed, admin: psycopg.Connection) -> uuid.UUID:
    expert = seed.person()
    admin.execute(
        "insert into app.signatures (expert_id, method, storage_path, sha256, width, height)"
        " values (%s, 'drawn', 'sig/one', %s, 300, 100)",
        (expert, "a" * 64),
    )
    return seed.case(seed.org(kind="team", members={expert: "owner"}), expert)


def allowed(dsn: str, case: uuid.UUID) -> bool:
    with psycopg.connect(dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case),))
        return conn.execute("select count(*) from app.case_signature(%s)", (case,)).fetchone() == (1,)


def test_the_api_role_cannot_grant_itself_a_jobs_access(api_dsn: str, case: uuid.UUID) -> None:
    assert allowed(api_dsn, case) is False


def test_the_worker_role_still_gets_its_cases_access(worker_dsn: str, case: uuid.UUID) -> None:
    assert allowed(worker_dsn, case) is True
