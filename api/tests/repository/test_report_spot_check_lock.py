"""Slice F, colleague spot-checks (review finding 7): five wrong codes lock the link, even when each try is
its own request that commits; the right code after that still gets `locked`.
"""

from __future__ import annotations

import hashlib
import uuid

import psycopg
import pytest

from tests.support.contracts import add_fact, add_report_version
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

TOKEN = hashlib.sha256(b"zz-token").hexdigest()
RIGHT = hashlib.sha256(b"zz-right").hexdigest()
WRONG = hashlib.sha256(b"zz-wrong").hexdigest()


@pytest.fixture
def check(seed: Seed, admin: psycopg.Connection) -> uuid.UUID:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "expert"})
    case = seed.case(org, expert, status="active")
    fact = add_fact(admin, case, seed.page(case, seed.document(case), 1), status="verified")
    row = admin.execute(
        "insert into app.spot_checks (case_id, report_version_id, reviewer_email, fact_ids, token_hash, code_hash,"
        " code_sent_at, expires_at, created_by) values (%s, %s, 'zz@example.test', %s, %s, %s, now(),"
        " now() + interval '1 day', %s) returning id",
        (case, add_report_version(admin, case), [fact], TOKEN, RIGHT, expert),
    ).fetchone()
    assert row is not None
    found: uuid.UUID = row[0]
    return found


def attempt(api_dsn: str, code: str) -> str:
    with psycopg.connect(api_dsn) as conn:  # its own transaction, committed when the block ends
        row = conn.execute("select outcome from app.spot_check_unlock(%s, %s)", (TOKEN, code)).fetchone()
    assert row is not None
    return str(row[0])


async def test_five_wrong_codes_lock_the_link_across_committed_tries(
    api_dsn: str, admin: psycopg.Connection, check: uuid.UUID
) -> None:
    assert [attempt(api_dsn, WRONG) for _ in range(5)] == ["bad_code"] * 5
    assert admin.execute("select code_attempts from app.spot_checks where id = %s", (check,)).fetchone() == (5,)
    assert attempt(api_dsn, RIGHT) == "locked"
    refused = admin.execute(
        "select count(*) from app.audit_log where target = %s and action = 'spot_check.refused'",
        (f"spot_check:{check}",),
    ).fetchone()
    assert refused == (6,)
