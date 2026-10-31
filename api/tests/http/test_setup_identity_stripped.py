"""Rule 7 for slice C: the subject's recorded name and date of birth never reach T-06.

The recorded letter names the subject and gives a date of birth. The case has no party rows: the only
source of the name and the date is the sealed subject identity, which the job opens itself.
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from app.core.ai.bedrock import RecordedBedrock
from app.core.crypto import CaseCrypto
from app.core.db import Database
from app.features.setup.identity import save_identity
from tests.http.test_setup_questions_routes import (  # noqa: F401  (fixtures and helpers)
    IDENTIFIERS,
    PARTY,
    add_letter,
    app,
    crypto,
    model,
    run_job,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def test_t06_never_carries_the_recorded_subject(
    client_for: ClientFor,
    world: dict[str, uuid.UUID],
    worker_db: Database,
    model: RecordedBedrock,
    crypto: CaseCrypto,
    admin_dsn: str,
) -> None:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as aconn:
        await save_identity(aconn, crypto, world["case"], name=PARTY, other_names=[], date_of_birth=date(1969, 3, 14))
    client = await client_for(world["expert"])
    added = await add_letter(client, world)

    await run_job(worker_db, world, added["job_id"])

    [request] = model.requests
    assert "Ashworth" not in request.text and "14/03/1969" not in request.text
    assert not [i for i in IDENTIFIERS if i in request.text]
    assert url(world, "questions")
