"""One shared Azure OCR limiter: the slot scheduler with provider `azure_ocr`, counted per second.

The submit ceiling (`AZURE_OCR_TPS`, default 12, under S0's 15 a second) and the in-flight cap
(`AZURE_OCR_CONCURRENCY`, default 30) are account-wide, like Bedrock's: several bundles share them.
"""

from __future__ import annotations

import dataclasses
import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_foundation_ai_slots_global import _granted, _kit, _Stop, two_cases, worker_db  # noqa: F401
from tests.repository.test_pipeline_fanout_support import PAGES, World, world
from tests.repository.test_pipeline_words import storage  # noqa: F401
from tests.support.ai import cell
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

OCR = "azure_ocr"


async def test_the_limits_come_from_the_environment_with_safe_defaults() -> None:
    ocr = cell().limits[OCR]
    assert (ocr.rpm, ocr.window_seconds, ocr.concurrency) == (12, 1, 30)
    tuned = cell(AZURE_OCR_TPS="9", AZURE_OCR_CONCURRENCY="4").limits[OCR]
    assert (tuned.rpm, tuned.window_seconds, tuned.concurrency) == (9, 1, 4)
    assert cell().limits["bedrock"].window_seconds == 60  # Bedrock still counts a minute


async def test_the_third_submit_in_one_second_waits_for_the_next_second(
    worker_db: Database,  # noqa: F811
    admin: psycopg.Connection,
    seed: Seed,
    two_cases: tuple[uuid.UUID, uuid.UUID],  # noqa: F811
) -> None:
    case_a, case_b = two_cases
    job_a, job_b = seed.job(case_a), seed.job(case_b)
    kit = _kit(AZURE_OCR_TPS="2")

    await kit._wait_for_slot_detached(worker_db, case_a, job_a, OCR)
    await kit._wait_for_slot_detached(worker_db, case_b, job_b, OCR)
    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case_a, job_a, OCR)  # the third, from the first job again

    granted = admin.execute("select count(*) from app.ai_request_slots where granted_at is not null").fetchone()
    assert granted == (2,)
    assert _granted(admin, job_b)  # the other job's own submit was granted

    admin.execute("update app.ai_request_slots set granted_at = granted_at - interval '2 seconds'")
    admin.commit()  # the next second begins
    await kit._wait_for_slot_detached(worker_db, case_a, job_a, OCR)
    granted = admin.execute("select count(*) from app.ai_request_slots where granted_at is not null").fetchone()
    assert granted == (3,)


async def test_the_in_flight_cap_is_shared_until_a_submit_is_released(
    worker_db: Database,  # noqa: F811
    admin: psycopg.Connection,
    seed: Seed,
    two_cases: tuple[uuid.UUID, uuid.UUID],  # noqa: F811
) -> None:
    case_a, case_b = two_cases
    first, second = seed.job(case_a), seed.job(case_b)
    kit = _kit(AZURE_OCR_TPS="50", AZURE_OCR_CONCURRENCY="1")

    slot = await kit._wait_for_slot_detached(worker_db, case_a, first, OCR)
    with pytest.raises(_Stop):
        await kit._wait_for_slot_detached(worker_db, case_b, second, OCR)

    await kit._release_slot_detached(worker_db, case_a, slot)
    await kit._wait_for_slot_detached(worker_db, case_b, second, OCR)
    assert _granted(admin, second)


async def test_ocr_slots_and_bedrock_slots_never_share_a_pool(
    worker_db: Database,  # noqa: F811
    seed: Seed,
    two_cases: tuple[uuid.UUID, uuid.UUID],  # noqa: F811
) -> None:
    case_a, _ = two_cases
    job = seed.job(case_a)
    kit = _kit(AZURE_OCR_TPS="1", BEDROCK_RPM_LIMIT="1")

    await kit._wait_for_slot_detached(worker_db, case_a, job, OCR)
    await kit._wait_for_slot_detached(worker_db, case_a, job, "bedrock")  # not held back by the OCR grant


@pytest.fixture
async def scanned(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> AsyncIterator[World]:
    async with world(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
        until_ocr=service.OCR_CONCURRENCY,
    ) as w:
        yield w


async def test_a_scanned_bundle_reads_every_page_through_the_limiter_within_the_ceiling(
    scanned: World, seed: Seed
) -> None:
    tps = 20
    scanned.ctx.job_id = seed.job(scanned.ctx.case_id)  # the limiter queues a real job
    kit = AiKit(cell(AZURE_OCR_TPS=str(tps)), object())  # type: ignore[arg-type]
    sent: list[int] = []  # a page with a text layer is never sent to Azure, so it takes no slot

    class Counting:
        endpoint = scanned.services.ocr.endpoint  # type: ignore[union-attr]

        async def analyse(self, document: bytes, *args: Any, **kwargs: Any) -> Any:
            sent.append(1)
            return await scanned.services.ocr.analyse(document, *args, **kwargs)  # type: ignore[union-attr]

    services = dataclasses.replace(scanned.services, kit=kit, ocr=Counting())
    await service.run_step(scanned.ctx, services, "split")
    await service.run_step(scanned.ctx, services, "ocr")

    words = scanned.count(
        "select count(distinct w.page_id) from app.ocr_words w join app.cases c on c.bundle_id = w.bundle_id"
        " where c.id = %s"
    )
    assert words == PAGES  # every page was read, whatever order the submits were granted in
    rows = scanned.h.admin.execute(
        "select granted_at, in_flight from app.ai_request_slots where provider = %s order by granted_at", (OCR,)
    ).fetchall()
    assert len(rows) == len(sent) > 0  # one slot per page actually sent
    assert not any(in_flight for _, in_flight in rows)  # every slot released
    times = [granted for granted, _ in rows]
    assert all((times[i + tps] - times[i]).total_seconds() >= 1.0 for i in range(len(times) - tps))
    assert scanned.ocr_watch.violations == []  # no connection held while waiting for a slot or for Azure


def test_the_per_job_cap_is_the_setting() -> None:
    kit = AiKit(cell(AZURE_OCR_CONCURRENCY="7"), object())  # type: ignore[arg-type]
    assert service.ocr_concurrency(SimpleNamespace(kit=kit)) == 7  # type: ignore[arg-type]
    assert service.ocr_concurrency(SimpleNamespace(kit=None)) == service.OCR_CONCURRENCY  # type: ignore[arg-type]
