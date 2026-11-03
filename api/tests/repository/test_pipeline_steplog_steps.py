"""Step logging through the real stages (the format itself is tested in tests/unit/test_pipeline_steplog.py)."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.ocr import OcrFailed
from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.pipeline import service
from tests.repository.test_pipeline_fanout_support import PAGES, SECRET_NAME, SECRET_NUMBER, World, world
from tests.repository.test_pipeline_support import Harness, recorded_book
from tests.repository.test_pipeline_words import _open, recordings, storage  # noqa: F401
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def fields(line: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+)=(\S*)", line))


def step_lines(caplog: pytest.LogCaptureFixture, when: str = "call") -> list[dict[str, str]]:
    return [fields(r.getMessage()) for r in caplog.get_records(when) if r.getMessage().startswith("pipeline step=")]


@pytest.fixture
async def failing(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> AsyncIterator[World]:
    errors: dict[int, Exception] = {
        3: OcrFailed("http_422"),
        7: OcrFailed(f"{SECRET_NAME} {SECRET_NUMBER}"),  # a message with patient text in it
    }
    async with world(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
        failing=errors,
    ) as w:
        yield w


async def test_a_step_failing_on_pages_three_and_seven_names_both(
    failing: World, caplog: pytest.LogCaptureFixture
) -> None:
    await service.run_step(failing.ctx, failing.services, "split")
    caplog.clear()
    caplog.set_level(logging.DEBUG)
    await service.run_step(failing.ctx, failing.services, "ocr")  # F4: those pages alone are unreadable
    lines = step_lines(caplog)
    bad = {f["page"]: f for f in lines if f["event"] == "page_failed"}
    assert set(bad) == {"3", "7"}
    assert bad["3"]["error"] == "OcrFailed" and bad["3"]["status"] == "422"
    assert bad["7"]["error"] == "OcrFailed" and bad["7"]["status"] == "-"
    assert all(f["step"] == "ocr" and f["attempt"] == "1" and f["job"] == str(failing.ctx.job_id) for f in bad.values())
    (listed,) = [f for f in lines if f["event"] == "failed_pages"]
    assert listed["pages"] == "3,7"
    end = next(f for f in lines if f["event"] == "end")
    assert (end["ok"], end["failed"]) == (str(PAGES - 2), "2")


async def test_nothing_a_page_says_and_no_error_message_reaches_any_record(
    failing: World, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    for step in ("split", "ocr", "duplicates"):
        await service.run_step(failing.ctx, failing.services, step)
    everything = "\n".join(
        r.getMessage() + str(r.args) + (str(r.exc_info[1]) if r.exc_info else "")
        for r in caplog.records
        if r.name.startswith("app.")  # our own loggers; botocore's debug lines carry hex that can contain "5919"
    )
    for secret in (SECRET_NAME, "Testpatient", "Jane", "943 476", "9434765919", "5919"):
        assert secret not in everything


@pytest.fixture
def info_logs(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)


@pytest.fixture
async def full_run(
    info_logs: None,
    recordings: FixtureStore,  # noqa: F811
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> AsyncIterator[Harness]:
    from tests.repository.test_pipeline_support import subset_document

    opened = await _open(
        LAUNCH,
        recordings,
        recorded_book(recordings),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    async with opened as h:
        await h.upload(subset_document())
        finished = await h.drain(timeout=300)
        assert set(finished.values()) == {"done"}, finished
        yield h


async def test_every_step_of_the_bundle_job_logs_a_start_and_an_end(
    full_run: Harness, caplog: pytest.LogCaptureFixture
) -> None:
    lines = step_lines(caplog, "setup")
    for step in (*service.STEPS, "embed"):
        events = [f["event"] for f in lines if f["step"] == step]
        assert events.count("start") == 1 and events.count("end") == 1, (step, events)
        start = next(f for f in lines if f["step"] == step and f["event"] == "start")
        end = next(f for f in lines if f["step"] == step and f["event"] == "end")
        assert start["job"] == end["job"] and start["case"] == end["case"] == str(full_run.case_id)
        assert end["outcome"] == "ok" and int(end["ms"]) >= 0, (step, end)
        assert {"todo", "done", "concurrency"} <= start.keys()
        assert {"ok", "failed", "skipped", "slowest_ms"} <= end.keys()
