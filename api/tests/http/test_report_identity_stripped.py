"""Rule 7 for slice F: the subject's name, other name and date of birth never reach T-17.

The expert's own paragraph carries the name, the other name and the date of birth in three forms; the
tone check (T-17) still runs, and what leaves the cell holds none of them.
"""

from __future__ import annotations

from typing import Any

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.report.world import CLEAN, CRYPTO, Reads, World, make_ready
from tests.http.test_report_final_check import (  # noqa: F401  (fixtures and helpers)
    Answering,
    aconn,
    app,
    build_now,
    check_now,
    model,
    quiet,
    reads,
    storage,
    worker_db,
    world,
)
from tests.support.db import ClientFor
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


async def test_t17_never_carries_the_subject(
    client_for: ClientFor,
    worker_db: Database,  # noqa: F811
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,  # noqa: F811
    world: World,  # noqa: F811
    reads: Reads,  # noqa: F811
    model: Any,  # noqa: F811
) -> None:
    answering: Answering = model(quiet)
    await record_identity(aconn, CRYPTO, world.case)
    await make_ready(seed, admin, aconn, world, reads, body=f"{CLEAN} {SENTENCE}")
    await build_now(client_for, worker_db, world)

    await check_now(client_for, worker_db, world)

    sent = [r.text for r in answering.requests if r.task_id == "T-17"]
    assert sent and "result was low" in sent[0]
    assert leaked(sent) == []
