"""pytest fixtures shared by slice I's API tests; a test module imports the ones it uses.

`combo` defaults to the launch packs; a module that must also run under the fixture packs overrides it.
`model` configures slice I's services with a scripted model (`Scripted`) answering T-22a, T-22b, T-22c
and T-24 from the request text, as a recorded model would: it reads words and references only.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.trial.ports import RecordedChanges
from app.features.trial.services import TrialServices, configure
from tests.fixtures.trial.world import CRYPTO, Reads, World, app_for, build, serve_report
from tests.support.ai import CELL_ENV, cell
from tests.support.files import local_storage
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

Answer = Callable[[ModelRequest], dict[str, Any]]


def lines(request: ModelRequest, prefix: str) -> list[tuple[str, str]]:
    """(reference, text) of each request line whose reference starts with `prefix`."""
    out = []
    for line in request.text.splitlines():
        ref, sep, text = line.partition("\t")
        if sep and re.fullmatch(rf"{prefix}\d+", ref):
            out.append((ref, text))
    return out


def agenda_items(request: ModelRequest) -> dict[str, Any]:
    """T-22a: one item per numbered question, its words copied with their ids."""
    items: list[dict[str, Any]] = []
    for ref, text in [(r, t) for r, t in (line.split("\t", 1) for line in request.text.splitlines() if "\t" in line)]:
        if re.fullmatch(r"\d+\.", text):
            items.append({"number": int(text[:-1]), "text_verbatim": "", "word_ids": [], "reason": "agenda_item"})
        elif items:
            items[-1]["word_ids"].append(ref)
            items[-1]["text_verbatim"] = (items[-1]["text_verbatim"] + " " + text).strip()
    return {"items": items}


def link_by_words(request: ModelRequest) -> dict[str, Any]:
    """T-22b: link each agenda item to the paragraph sharing the most words with it."""
    paragraphs = lines(request, "p")
    links = []
    for ref, text in lines(request, "a"):
        words = set(text.lower().split())
        best = max(paragraphs, key=lambda p: len(words & set(p[1].lower().split())))
        links.append({"agenda": ref, "paragraphs": [best[0]], "reason": "same_question"})
    return {"links": links}


def deviation(request: ModelRequest) -> dict[str, Any]:
    """T-22c: the planted change (served "more likely than not ... not", draft "probably would have")."""
    served = " ".join(
        t for r, t in (line.split("\t", 1) for line in request.text.splitlines() if "\t" in line) if r == "served"
    )
    draft = " ".join(
        t for r, t in (line.split("\t", 1) for line in request.text.splitlines() if "\t" in line) if r == "draft"
    )
    if "probably would have been" in draft and "more likely than not" in served:
        return {"status": "changed", "reason": "outcome_reversed"}
    return {"status": "consistent", "reason": "same_view"}


def sources(request: ModelRequest) -> dict[str, Any]:
    """T-24: a sentence is sourced if a supplied paragraph shares four or more words with it."""
    paragraphs = lines(request, "p")
    out = []
    for ref, text in lines(request, "s"):
        words = set(text.lower().split())
        match = next((p for p, body in paragraphs if len(words & set(body.lower().split())) >= 4), None)
        out.append({"sentence_index": int(ref[1:]), "source": match, "reason": "found" if match else "not_found"})
    return {"sentences": out}


ANSWERS: dict[str, Answer] = {"T-22a": agenda_items, "T-22b": link_by_words, "T-22c": deviation, "T-24": sources}


class Scripted:
    """A model stand-in answering each slice I task from `answers` and keeping the requests it saw."""

    def __init__(self, answers: dict[str, Answer]) -> None:
        self.answers = answers
        self.requests: list[ModelRequest] = []
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        return ModelResponse(output=self.answers[str(request.task_id)](request), input_tokens=50, output_tokens=5)

    def seen(self, task_id: str) -> list[ModelRequest]:
        return [r for r in self.requests if r.task_id == task_id]


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def app(
    api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: CaseFileStorage
) -> Iterator[FastAPI]:
    """The API; slice I's jobs get the same encryption, storage and read log (no model unless `model`)."""
    from app.features.trial import services

    if services.configured() is None:
        configure(TrialServices(crypto=CRYPTO, storage=storage, reads=reads, changes=RecordedChanges()))
    yield app_for(api_db, auth_env, combo, reads, storage)
    configure(None)


@pytest.fixture
async def world(
    seed: Seed, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, combo: Combo, reads: Reads
) -> World:
    w = await build(seed, admin, aconn, combo, reads)
    await serve_report(admin, aconn, w)
    return w


@pytest.fixture
def changes() -> RecordedChanges:
    return RecordedChanges()


@pytest.fixture
def model(reads: Reads, storage: CaseFileStorage, changes: RecordedChanges) -> Iterator[Callable[..., Scripted]]:
    def use(**overrides: Answer) -> Scripted:
        scripted = Scripted({**ANSWERS, **overrides})
        configure(
            TrialServices(kit=AiKit(cell(), scripted), crypto=CRYPTO, storage=storage, reads=reads, changes=changes)
        )
        return scripted

    use()
    yield use
    configure(None)


async def upload_bundle(client: Any, worker_db: Database, w: World, pdf: bytes, **query: str) -> dict[str, Any]:
    """Send a trial bundle and run `match_trial_bundle` on it as the worker would."""
    from app.core.jobs import JobContext
    from app.features.trial.jobs import match_trial_bundle

    resp = await client.post(
        w.url("court-bundles"), content=pdf, params=query, headers={"content-type": "application/pdf"}
    )
    assert resp.status_code == 202, resp.text
    body: dict[str, Any] = resp.json()
    await match_trial_bundle(JobContext(worker_db, uuid.UUID(body["job_id"]), w.case, "match_trial_bundle", 1))
    return body


async def run_job(worker_db: Database, w: World, kind: str, job_id: str) -> None:
    from app.core.jobs import JobContext, registry

    await registry.kinds[kind].handler(JobContext(worker_db, uuid.UUID(job_id), w.case, kind, 1))
