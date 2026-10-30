"""Rule 7 for slice G: the subject's name, other name and date of birth never reach T-20a or T-20b.

The questions document types the name, the other name and the date of birth in three forms; the case has a
recorded subject. What leaves the cell holds none of them, and the questions are still found.
"""

# ruff: noqa: F811
from __future__ import annotations

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.storage import SupabaseStorage
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    CRYPTO,
    Scripted,
    World,
    aconn,
    app,
    classifications,
    mailer,
    model,
    ocr,
    reads,
    served,
    services,
    storage,
    worker_db,
    world,
)
from tests.http.test_after_serving_questions import DOCUMENT, QUESTIONS, imported
from tests.support.db import ClientFor
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH, Combo

pytestmark = pytest.mark.anyio


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


async def test_t20_never_carries_the_subject(
    client_for: ClientFor,
    storage: SupabaseStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    await record_identity(aconn, CRYPTO, served.case)
    _, questions = await imported(
        client_for, storage, admin, aconn, worker_db, model, served, text=f"{DOCUMENT}\n7. {SENTENCE}"
    )

    sent = [r.text for r in model.requests if r.task_id in ("T-20a", "T-20b")]
    assert sent and any("ferritin" in t for t in sent)
    assert leaked(sent) == []
    assert len(questions) >= len(QUESTIONS) - 1
