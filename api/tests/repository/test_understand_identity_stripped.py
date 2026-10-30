"""Rule 7 for slice D: the subject's name, other name and date of birth never reach T-11 or the embedder.

The services are built as the cell builds them (no date-of-birth seam): the job opens the case's identity
itself. A fact and a question carry the name, the other name and the date of birth in three forms.
"""

# ruff: noqa: F811
from __future__ import annotations

from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.ai.kit import AiKit
from app.core.packs import default_library
from app.features.understand.services import UnderstandServices, configure
from tests.repository.test_understand_jobs import (  # noqa: F401  (fixtures)
    Bundle,
    SpyEmbedder,
    ask,
    crypto,
    entry,
    launch,
    services,
    worker_db,
)
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.identity import SENTENCE, leaked, record_identity

pytestmark = pytest.mark.anyio


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


async def test_t11_and_the_embedder_never_see_the_subject(launch: Bundle, aconn: psycopg.AsyncConnection) -> None:
    b = launch
    await record_identity(aconn, b.crypto, b.case)
    page = await b.page(b.other_source)
    entry(b, page, f"potassium raised. {SENTENCE}")
    ask(b, words=f"Was it acted on? {SENTENCE}")
    model = ScriptedBedrock([response({"links": []})])
    embedder = SpyEmbedder()
    configure(UnderstandServices(kit=AiKit(cell(), model), crypto=b.crypto, embedder=embedder, packs=default_library()))

    await b.run("suggest_links")

    assert model.requests, "T-11 ran"
    assert any("potassium" in r.text for r in model.requests)
    assert leaked([r.text for r in model.requests]) == []
    assert any("potassium" in t for t in embedder.seen)
    assert leaked(embedder.seen) == []
