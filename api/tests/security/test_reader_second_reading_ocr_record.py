"""PV-18 (reader part): the second reading's call to OCR leaves an `ai_calls` row, and the second reading says which
OCR model read the page again.

A page the expert typed out has no boxes of its own, so the job reads the picture again with OCR before it masks
anything (`second_reading._fresh_words`). That call used to leave no trace. It now writes one metadata-only
`ai_calls` row (task `ocr.second_reading`, the model, the service's API version, the region and the job: no words,
rule 6) and `second_readings.ocr_model_id` keeps the model. A page whose stored words are used makes no OCR call.

The fixtures are those of `tests/http/test_reader_second_reading.py` (the same page, the same scripted model).
"""

# ruff: noqa: F811 - pytest fixtures imported from the second-reading tests are used as arguments
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from app.core.ai.ocr import OcrResult
from app.core.crypto import CaseCrypto
from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.reader import second_reading
from tests.http.test_reader_second_reading import (  # noqa: F401
    FreshOcr,
    aconn,
    app,
    crypto,
    make_page,
    model,
    run,
    services,
    storage,
    url,
    worker_db,
    world,
)
from tests.support.identity import record_identity

pytestmark = pytest.mark.anyio


class VersionedOcr(FreshOcr):
    """The fresh reading, as the live service answers: with the API version that answered."""

    async def analyse(self, document: bytes, model: str, *, locale: str | None = None) -> OcrResult:
        found = await super().analyse(document, model, locale=locale)
        return OcrResult(model="prebuilt-layout", pages=found.pages, api_version="2099-01-01")


async def test_pv18_the_fresh_reading_of_a_typed_out_page_records_the_call_and_its_job(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "ocr", VersionedOcr())
    monkeypatch.setattr(second_reading.services, "region", "uk", raising=False)
    await record_identity(aconn, crypto, world["case"])
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"], source="expert", typed=True)
    job = (await (await client_for(world["expert"])).post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    calls = admin.execute(
        "select task_id, model_id, api_version, region, job_id, input_tokens + output_tokens, outcome"
        " from app.ai_calls where case_id = %s and task_id like 'ocr.%%'",
        (world["case"],),
    ).fetchall()
    assert calls == [("ocr.second_reading", "prebuilt-layout", "2099-01-01", "uk", uuid.UUID(job), 0, "ok")]
    status, ocr_model = admin.execute(
        "select status, ocr_model_id from app.second_readings where page_id = %s", (page,)
    ).fetchone()  # type: ignore[misc]
    assert (status, ocr_model) == ("done", "prebuilt-layout")


async def test_pv18_control_a_page_read_from_its_stored_words_makes_no_ocr_call(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(second_reading.services, "ocr", VersionedOcr())
    monkeypatch.setattr(second_reading.services, "region", "uk", raising=False)
    await record_identity(aconn, crypto, world["case"])
    page = await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"])
    job = (await (await client_for(world["expert"])).post(url(world, "?on_request=true"))).json()["job_id"]
    await run(worker_db, admin, world, job)
    assert admin.execute(
        "select count(*) from app.ai_calls where case_id = %s and task_id like 'ocr.%%'", (world["case"],)
    ).fetchone() == (0,)
    assert admin.execute("select ocr_model_id from app.second_readings where page_id = %s", (page,)).fetchone() == (
        None,
    )


async def test_pv18_the_recorded_call_holds_no_words(
    client_for: Any,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: CaseFileStorage,
    crypto: CaseCrypto,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tests.support.identity import NAME

    monkeypatch.setattr(second_reading.services, "ocr", VersionedOcr())
    monkeypatch.setattr(second_reading.services, "region", "uk", raising=False)
    await record_identity(aconn, crypto, world["case"])
    await make_page(aconn, storage, crypto, world["case"], ["Seen", "today"], source="expert", typed=True)
    job = (await (await client_for(world["expert"])).post(url(world))).json()["job_id"]
    await run(worker_db, admin, world, job)
    rows = admin.execute(
        "select row_to_json(c)::text from app.ai_calls c where case_id = %s and task_id like 'ocr.%%'",
        (world["case"],),
    ).fetchall()
    assert rows, "the call is recorded"
    for (text,) in rows:
        assert not any(w in text for w in [*NAME.split(), "chest", "Seen", "today"])
