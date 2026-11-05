"""Migration 0056: the slot scheduler counts the whole account, not one case.

`app.ai_request_slots` is worker-only and row-level security limits a job to its own case's rows. The kit
therefore counts through `app.ai_slots_in_use` (security definer), so several bundles share one
requests-per-minute ceiling and one in-flight ceiling instead of each thinking it is alone.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse, ServiceBusy
from app.core.ai.kit import AiKit
from app.core.db import Database
from tests.support.ai import cell
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


class _Stop(Exception):
    """Raised by the fake sleep so a test can see that the kit chose to wait, without really waiting."""


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=6)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def two_cases(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    return seed.case(org, expert), seed.case(org, expert)


def _kit(**env: str) -> AiKit:
    async def stop(_seconds: float) -> None:
        raise _Stop()

    return AiKit(cell(**env), object(), sleep=stop)  # type: ignore[arg-type]


def _granted(admin: psycopg.Connection, job_id: uuid.UUID) -> bool:
    row = admin.execute("select granted_at from app.ai_request_slots where job_id = %s", (job_id,)).fetchone()
    return row is not None and row[0] is not None


async def test_the_rpm_ceiling_is_shared_by_jobs_on_different_cases(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, two_cases: tuple[uuid.UUID, uuid.UUID]
) -> None:
    case_a, case_b = two_cases
    first, second = seed.job(case_a), seed.job(case_b)
    kit = _kit(BEDROCK_RPM_LIMIT="1")

    await kit._wait_for_slot_detached(worker_db, case_a, first)
    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case_b, second)

    assert _granted(admin, first) and not _granted(admin, second)


async def test_the_in_flight_ceiling_is_shared_until_the_first_call_is_released(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, two_cases: tuple[uuid.UUID, uuid.UUID]
) -> None:
    case_a, case_b = two_cases
    first, second = seed.job(case_a), seed.job(case_b)
    kit = _kit(BEDROCK_RPM_LIMIT="50", BEDROCK_CONCURRENCY="1")

    slot = await kit._wait_for_slot_detached(worker_db, case_a, first)
    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case_b, second)
    assert not _granted(admin, second)

    await kit._release_slot_detached(worker_db, case_a, slot)
    await kit._wait_for_slot_detached(worker_db, case_b, second)
    assert _granted(admin, second)


async def test_a_call_that_never_reported_back_stops_counting_after_ten_minutes(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, two_cases: tuple[uuid.UUID, uuid.UUID]
) -> None:
    case_a, case_b = two_cases
    crashed, waiting = seed.job(case_a), seed.job(case_b)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at, in_flight)"
        " values (%s, %s, now() - interval '11 minutes', now() - interval '11 minutes', true)",
        (crashed, case_a),
    )
    admin.commit()

    await _kit(BEDROCK_RPM_LIMIT="50", BEDROCK_CONCURRENCY="1")._wait_for_slot_detached(worker_db, case_b, waiting)

    assert _granted(admin, waiting)


async def test_the_concurrency_ceiling_comes_from_the_environment() -> None:
    assert cell().limits["bedrock"].concurrency == 3
    assert cell(BEDROCK_CONCURRENCY="7").limits["bedrock"].concurrency == 7


async def test_a_worker_still_cannot_read_another_cases_slot_rows(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, two_cases: tuple[uuid.UUID, uuid.UUID]
) -> None:
    case_a, case_b = two_cases
    other = seed.job(case_b)
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id, requested_at, granted_at, in_flight)"
        " values (%s, %s, now(), now(), true)",
        (other, case_b),
    )
    admin.commit()

    async with worker_db.job(case_a) as conn:
        cur = await conn.execute("select count(*) from app.ai_request_slots")
        assert await cur.fetchone() == (0,)
        cur = await conn.execute(
            "select in_flight, granted_in_window from app.ai_slots_in_use('bedrock', '60 seconds')"
        )
        assert await cur.fetchone() == (1, 1)  # the count is account-wide; the rows stay private


async def test_the_api_role_cannot_run_the_slot_count(api_db: Database) -> None:
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        async with api_db.anonymous() as conn:
            await conn.execute("select * from app.ai_slots_in_use('bedrock', '60 seconds')")


class _Bedrock:
    endpoint = "https://bedrock-runtime.eu-west-2.amazonaws.com"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        if self.fail:
            raise ServiceBusy()
        return ModelResponse(output={}, input_tokens=1, output_tokens=1)


def _request() -> ModelRequest:
    return ModelRequest(model_id="m", system="s", context={}, text="t", schema={})


@pytest.mark.parametrize("fail", [False, True])
async def test_a_call_releases_its_slot_whether_it_answers_or_fails(fail: bool) -> None:
    released: list[str] = []

    async def release() -> None:
        released.append("done")

    async def wait(_provider: str) -> object:
        return release

    async def no_sleep(_seconds: float) -> None:
        return None

    kit = AiKit(cell(), _Bedrock(fail), retries=0, base_delay=0.0, sleep=no_sleep)
    try:
        await kit._invoke_bedrock(_request(), wait)  # type: ignore[arg-type]
    except Exception:
        assert fail
    assert released and set(released) == {"done"}
