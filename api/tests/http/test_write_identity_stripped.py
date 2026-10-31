"""Rule 7 for slice E: the subject's name, other name and date of birth never reach T-14a.

The services come from the cell config (no date-of-birth or name seam is passed in), the case has a recorded
subject, and a linked fact carries the name, the other name and the date of birth in three forms.
"""

# ruff: noqa: F811
from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from app.features.write import services
from app.features.write.jobs import suggest_points
from tests.fixtures.write.world import World
from tests.http.test_write_wiring import (  # noqa: F401  (fixtures)
    Answering,
    cell_model,
    worker_db,
    world,
)
from tests.support.contracts import add_fact
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


async def test_t14a_never_carries_the_subject(
    cell_model: Answering,
    worker_db: Database,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
) -> None:
    await record_identity(aconn, services.current().case_crypto(), world.case)
    carrier = add_fact(admin, world.case, world.read_page, status="verified", value=f"potassium raised. {SENTENCE}")
    admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, status, decided_by)"
        " values (%s, %s, %s, 'expert', 'confirmed', %s)",
        (world.case, carrier, world.question, world.expert),
    )
    job = seed.job(world.case, kind="suggest_points", requested_by=world.expert)

    await suggest_points(JobContext(worker_db, job, world.case, "suggest_points", 1))

    [request] = cell_model.requests
    assert "potassium" in request.text
    assert leaked([request.text]) == []
