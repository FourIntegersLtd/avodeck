"""Slice B routes: reading by transcript (ADR 0028; architecture 5, 09 Reader).

- `GET /v1/cases/{id}/transcript/entries?offset&limit`: `{status, total, entries}`. While the entries are not
  read yet the status is `preparing` (the job is queued once) and the list is empty. Needs `case.view_content`.
  An entry carries its page, kind, author, date and printed lines; never a fact or a suggestion (rule 3).
- `POST /v1/cases/{id}/transcript/seen`: what the reader saw (`entry_id` + `dwell_ms`, or `page_no` +
  `picture_opened`); answers `{pages_read}` (the pages the server now counts as read). Needs `page.read`.
  A read made this way unlocks `/pages/{n}/facts` like any read (rule 3).
- `POST /v1/cases/{id}/transcript/download` (202 `{job_id}`; step-up, `case.export`) and
  `GET /v1/cases/{id}/transcript/download/{job_id}`: the Word copy, once, by the person who asked for it.
Anyone without access gets 404.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.reader import transcript
from tests.support.files import build_app
from tests.support.seed import Seed
from tests.support.transcript_world import Spec, add_document, add_page

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
LAPSED = timedelta(minutes=11)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env, crypto=CRYPTO)


@pytest.fixture
async def aconn(admin_dsn: str) -> Any:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, Any]:
    expert, stranger = seed.person(), seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    seed.case(seed.org(kind="personal", members={stranger: "owner"}), stranger, status="active")
    return {"case": case, "expert": expert, "stranger": stranger}


def base(w: dict[str, Any]) -> str:
    return f"/v1/cases/{w['case']}"


async def test_entries_are_listed_without_any_fact(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("hello there",), day=date(2020, 1, 2))])
    client = await client_for(world["expert"])
    resp = await client.get(f"{base(world)}/transcript/entries")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready" and body["total"] == 1
    entry = body["entries"][0]
    assert entry["page_no"] == 1 and entry["lines"] == ["hello there"] and entry["event_date"] == "2020-01-02"
    assert set(entry) == {
        "id", "page_no", "kind", "folded", "author_role", "specialty", "event_date", "date_basis", "lines", "results",
    }  # fmt: skip


async def test_entries_not_read_yet_queue_the_job_once(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute(
        "insert into app.page_analysis (page_id, case_id, extracted_at) select id, case_id, now() from app.pages"
        " where case_id = %s",
        (world["case"],),
    )
    client = await client_for(world["expert"])
    for _ in range(2):
        body = (await client.get(f"{base(world)}/transcript/entries")).json()
        assert body == {"status": "preparing", "total": 0, "entries": []}
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'extract_entries'", (world["case"],)
    ).fetchone()
    assert row is not None and row[0] == 1


async def test_a_stranger_gets_404_on_every_transcript_route(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["stranger"])
    assert (await client.get(f"{base(world)}/transcript/entries")).status_code == 404
    seen = await client.post(f"{base(world)}/transcript/seen", json={"page_no": 1, "picture_opened": True})
    assert seen.status_code == 404
    assert (await client.post(f"{base(world)}/transcript/download")).status_code == 404


async def test_seeing_every_entry_reads_the_page_and_unlocks_its_suggestions(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 4, [Spec("zz_session", ("a b",))])
    client = await client_for(world["expert"])
    assert (await client.get(f"{base(world)}/pages/4/facts")).status_code == 423
    resp = await client.post(
        f"{base(world)}/transcript/seen", json={"entry_id": str(built.entry_ids[0]), "dwell_ms": transcript.SEEN_MS}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"pages_read": [4]}
    assert (await client.get(f"{base(world)}/pages/4/facts")).status_code == 200


async def test_seeing_too_little_reads_nothing(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    built = await add_page(aconn, CRYPTO, world["case"], doc, 4, [Spec("zz_session", ("a b",))])
    client = await client_for(world["expert"])
    resp = await client.post(
        f"{base(world)}/transcript/seen", json={"entry_id": str(built.entry_ids[0]), "dwell_ms": 5}
    )
    assert resp.json() == {"pages_read": []}
    assert (await client.get(f"{base(world)}/pages/4/facts")).status_code == 423


async def test_a_view_must_name_an_entry_or_a_picture(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    for body in ({}, {"entry_id": None, "dwell_ms": 5}, {"page_no": 1}, {"dwell_ms": -1, "entry_id": "x"}):
        assert (await client.post(f"{base(world)}/transcript/seen", json=body)).status_code == 422


async def test_an_unknown_entry_is_404(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    resp = await client.post(
        f"{base(world)}/transcript/seen",
        json={"entry_id": "00000000-0000-0000-0000-000000000001", "dwell_ms": 5000},
    )
    assert resp.status_code == 404


async def test_the_download_needs_a_recent_second_factor(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"], mfa_age=LAPSED)
    resp = await client.post(f"{base(world)}/transcript/download")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "step_up_required"


async def test_the_download_queues_a_job_for_the_requester(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download")
    assert resp.status_code == 202, resp.text
    job = resp.json()["job_id"]
    row = admin.execute("select kind, requested_by from app.jobs where id = %s", (job,)).fetchone()
    assert row == ("export_transcript", world["expert"])
    audited = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.transcript_requested'",
        (world["case"],),
    ).fetchone()
    assert audited is not None and audited[0] == 1


async def test_a_download_that_is_not_ready_is_409_and_someone_elses_is_404(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    client = await client_for(world["expert"])
    job = (await client.post(f"{base(world)}/transcript/download")).json()["job_id"]
    assert (await client.get(f"{base(world)}/transcript/download/{job}")).status_code == 409
    other = seed.job(world["case"], kind="export_transcript", requested_by=world["stranger"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (other,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (other,))
    assert (await client.get(f"{base(world)}/transcript/download/{other}")).status_code == 404
