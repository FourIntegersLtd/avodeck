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

import asyncio
from datetime import date, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import MergedPack as Pack
from app.features.reader import transcript, transcript_jobs
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


async def read_one_page(client: httpx.AsyncClient, aconn: psycopg.AsyncConnection, w: dict[str, Any]) -> None:
    """The expert reads a page, so there is something for the Word copy."""
    doc = await add_document(aconn, w["case"])
    built = await add_page(aconn, CRYPTO, w["case"], doc, 9, [Spec("zz_session", ("note",))])
    body = {"entry_id": str(built.entry_ids[0]), "dwell_ms": transcript.SEEN_MS}
    assert (await client.post(f"{base(w)}/transcript/seen", json=body)).json() == {"pages_read": [9]}


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
        "id", "page_no", "kind", "kind_label", "folded", "author_role", "author_label", "specialty", "event_date",
        "date_basis", "lines", "results", "result_labels", "section",
    }  # fmt: skip
    assert body["picture_pages"] == []


async def test_entries_not_read_yet_queue_the_job_once(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, extracted_at) select id, bundle_id, now() from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s)",
        (world["case"],),
    )
    client = await client_for(world["expert"])
    for _ in range(2):
        body = (await client.get(f"{base(world)}/transcript/entries")).json()
        assert body == {
            "status": "preparing", "total": 0, "entries": [], "picture_pages": [], "unread_pages": [],
            "prepared_pages": 0, "total_pages": 1, "extracted_pages": 1, "waiting_for": "entries",
            "pipeline_running": False, "can_prepare": True,
        }  # fmt: skip
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'extract_entries'", (world["case"],)
    ).fetchone()
    assert row is not None and row[0] == 1


def _extracted(admin: psycopg.Connection, w: dict[str, Any]) -> None:
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, extracted_at) select id, bundle_id, now() from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s)",
        (w["case"],),
    )


async def test_pages_waiting_for_the_pipeline_queue_no_entries_job_however_often_it_is_polled(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    client = await client_for(world["expert"])
    for _ in range(4):
        body = (await client.get(f"{base(world)}/transcript/entries")).json()
        assert body["status"] == "idle" and body["waiting_for"] == "pipeline"
        assert (body["extracted_pages"], body["total_pages"], body["pipeline_running"]) == (0, 1, False)
    assert _jobs(admin, world) == 0


async def test_the_pipeline_counts_as_running_while_a_pipeline_job_is_waiting(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute("insert into app.jobs (case_id, kind) values (%s, 'process_pages')", (world["case"],))
    body = (await (await client_for(world["expert"])).get(f"{base(world)}/transcript/entries")).json()
    assert body["waiting_for"] == "pipeline" and body["pipeline_running"] is True


async def test_a_paused_pipeline_does_not_count_as_running(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute("insert into app.jobs (case_id, kind) values (%s, 'process_pages')", (world["case"],))
    for status in ("running", "paused"):  # the job guard only allows queued -> running -> paused
        admin.execute(
            "update app.jobs set status = %s where case_id = %s and kind = 'process_pages'", (status, world["case"])
        )
    body = (await (await client_for(world["expert"])).get(f"{base(world)}/transcript/entries")).json()
    assert body["waiting_for"] == "pipeline" and body["pipeline_running"] is False


async def test_someone_who_cannot_read_is_told_so_and_starts_nothing(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    seed: Seed,
    world: dict[str, Any],
) -> None:
    viewer = seed.person()
    seed.case_member(world["case"], viewer, "viewer", added_by=world["expert"])
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    _extracted(admin, world)
    body = (await (await client_for(viewer)).get(f"{base(world)}/transcript/entries")).json()
    assert body["status"] == "idle" and body["can_prepare"] is False and body["waiting_for"] == "entries"
    assert _jobs(admin, world) == 0


async def test_the_count_of_pages_prepared_grows_as_entries_are_read(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [Spec("zz_session", ("y",))], analysed=False)
    _extracted(admin, world)
    admin.execute(
        "update app.page_analysis set entries_extracted_at = now() where page_id ="
        " (select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
        " and bundle_page_no = 1)",
        (world["case"],),
    )
    body = (await (await client_for(world["expert"])).get(f"{base(world)}/transcript/entries")).json()
    assert (body["status"], body["prepared_pages"], body["total_pages"]) == ("preparing", 1, 2)


async def _failed_entries_job(admin: psycopg.Connection, aconn: psycopg.AsyncConnection, w: dict[str, Any]) -> None:
    doc = await add_document(aconn, w["case"])
    await add_page(aconn, CRYPTO, w["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, extracted_at) select id, bundle_id, now() from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s)",
        (w["case"],),
    )
    job = admin.execute(
        "insert into app.jobs (case_id, kind) values (%s, 'extract_entries') returning id", (w["case"],)
    ).fetchone()
    assert job is not None
    admin.execute("update app.jobs set status = 'running' where id = %s", (job[0],))
    admin.execute("update app.jobs set status = 'failed', error_code = 'x' where id = %s", (job[0],))


def _jobs(admin: psycopg.Connection, w: dict[str, Any]) -> int:
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'extract_entries'", (w["case"],)
    ).fetchone()
    assert row is not None
    return int(row[0])


async def test_polling_after_a_failed_job_reports_failed_and_does_not_queue_again(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    await _failed_entries_job(admin, aconn, world)
    client = await client_for(world["expert"])
    for _ in range(3):
        assert (await client.get(f"{base(world)}/transcript/entries")).json()["status"] == "failed"
    assert _jobs(admin, world) == 1


async def test_an_explicit_retry_queues_the_job_once(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    await _failed_entries_job(admin, aconn, world)
    client = await client_for(world["expert"])
    for _ in range(2):
        resp = await client.post(f"{base(world)}/transcript/retry")
        assert resp.status_code == 202 and resp.json()["status"] == "preparing"
    assert _jobs(admin, world) == 2
    assert (await client.get(f"{base(world)}/transcript/entries")).json()["status"] == "preparing"


async def test_a_viewer_cannot_retry_and_a_stranger_gets_404(
    client_for: Any,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    seed: Seed,
    world: dict[str, Any],
) -> None:
    viewer = seed.person()
    seed.case_member(world["case"], viewer, "viewer", added_by=world["expert"])
    await _failed_entries_job(admin, aconn, world)
    assert (await (await client_for(viewer)).post(f"{base(world)}/transcript/retry")).status_code == 403
    assert (await (await client_for(world["stranger"])).post(f"{base(world)}/transcript/retry")).status_code == 404
    assert _jobs(admin, world) == 1


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


@pytest.mark.parametrize("body", [None, {}, {"format": "docx"}, {"format": "pdf"}])
async def test_the_download_needs_a_recent_second_factor(client_for: Any, world: dict[str, Any], body: Any) -> None:
    client = await client_for(world["expert"], mfa_age=LAPSED)
    resp = await client.post(f"{base(world)}/transcript/download", json=body)
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "step_up_required"


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        (None, "export_transcript"),
        ({"format": "docx"}, "export_transcript"),
        ({"format": "pdf"}, "export_transcript_pdf"),
    ],
)
async def test_the_download_queues_a_job_for_the_requester_before_any_page_is_read(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any], body: Any, kind: str
) -> None:
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download", json=body)
    assert resp.status_code == 202, resp.text
    job = resp.json()["job_id"]
    row = admin.execute("select kind, requested_by from app.jobs where id = %s", (job,)).fetchone()
    assert row == (kind, world["expert"])
    audited = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.transcript_requested'",
        (world["case"],),
    ).fetchone()
    assert audited is not None and audited[0] == 1


def _builds(admin: psycopg.Connection, w: dict[str, Any]) -> int:
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = any(%s)",
        (w["case"], ["export_transcript", "export_transcript_pdf"]),
    ).fetchone()
    assert row is not None
    return int(row[0])


def _requests_audited(admin: psycopg.Connection, w: dict[str, Any]) -> int:
    row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.transcript_requested'",
        (w["case"],),
    ).fetchone()
    assert row is not None
    return int(row[0])


@pytest.mark.parametrize("body", [None, {"format": "docx"}, {"format": "pdf"}])
async def test_pressing_download_again_while_a_build_is_waiting_gives_the_same_job(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any], body: Any
) -> None:
    client: httpx.AsyncClient = await client_for(world["expert"])
    first = await client.post(f"{base(world)}/transcript/download", json=body)
    again = await client.post(f"{base(world)}/transcript/download", json=body)
    assert first.status_code == again.status_code == 202
    assert again.json() == first.json()
    assert _builds(admin, world) == 1
    assert _requests_audited(admin, world) == 1  # only a build that was started is audited


async def test_pressing_download_again_while_the_build_is_running_gives_the_same_job(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    job = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download", json={"format": "pdf"})
    assert resp.status_code == 202 and resp.json() == {"job_id": str(job)}
    assert _builds(admin, world) == 1
    assert _requests_audited(admin, world) == 0


async def test_pressing_download_again_while_the_build_is_paused_gives_the_same_job(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    job = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'paused' where id = %s", (job,))
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download", json={"format": "pdf"})
    assert resp.status_code == 202 and resp.json() == {"job_id": str(job)}
    assert _builds(admin, world) == 1


async def test_presses_at_the_same_moment_start_one_build(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client: httpx.AsyncClient = await client_for(world["expert"])
    url = f"{base(world)}/transcript/download"
    answers = await asyncio.gather(*(client.post(url, json={"format": "pdf"}) for _ in range(4)))
    assert {a.status_code for a in answers} == {202}
    assert len({a.json()["job_id"] for a in answers}) == 1
    assert _builds(admin, world) == 1
    assert _requests_audited(admin, world) == 1


async def test_the_other_format_builds_its_own_file(
    client_for: Any, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    client: httpx.AsyncClient = await client_for(world["expert"])
    pdf = await client.post(f"{base(world)}/transcript/download", json={"format": "pdf"})
    word = await client.post(f"{base(world)}/transcript/download", json={"format": "docx"})
    assert pdf.json()["job_id"] != word.json()["job_id"]
    assert _builds(admin, world) == 2
    assert _requests_audited(admin, world) == 2


async def test_someone_elses_build_is_not_taken_over(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    theirs = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["stranger"])
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download", json={"format": "pdf"})
    assert resp.status_code == 202 and resp.json()["job_id"] != str(theirs)
    row = admin.execute("select requested_by from app.jobs where id = %s", (resp.json()["job_id"],)).fetchone()
    assert row == (world["expert"],)
    assert _requests_audited(admin, world) == 1


@pytest.mark.parametrize("ended", ["done", "failed"])
async def test_a_press_after_the_earlier_build_ended_starts_a_new_one(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any], ended: str
) -> None:
    old = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (old,))
    admin.execute("update app.jobs set status = %s where id = %s", (ended, old))
    client: httpx.AsyncClient = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/download", json={"format": "pdf"})
    assert resp.status_code == 202 and resp.json()["job_id"] != str(old)
    assert _builds(admin, world) == 2
    assert _requests_audited(admin, world) == 1


async def test_asking_for_a_build_that_failed_says_so_rather_than_waiting(
    client_for: Any, admin: psycopg.Connection, seed: Seed, world: dict[str, Any]
) -> None:
    job = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    client: httpx.AsyncClient = await client_for(world["expert"])
    waiting = await client.get(f"{base(world)}/transcript/download/{job}")
    assert waiting.status_code == 409 and waiting.json()["error"]["code"] == "conflict"
    admin.execute("update app.jobs set status = 'failed' where id = %s", (job,))
    failed = await client.get(f"{base(world)}/transcript/download/{job}")
    assert failed.status_code == 409 and failed.json()["error"]["code"] == "build_failed"


async def test_the_job_a_repeated_press_returned_is_handed_over_once_when_done(
    client_for: Any,
    admin: psycopg.Connection,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    app: FastAPI,
) -> None:
    class Shelf:
        async def delete(self, paths: Any) -> None:
            return None

    app.state.storage = Shelf()

    async def take(conn: Any, storage: Any, crypto: Any, case_id: Any, job_id: Any, fmt: str = "docx") -> bytes:
        return b"%PDF-1.4 x"

    monkeypatch.setattr(transcript_jobs, "take", take)
    client: httpx.AsyncClient = await client_for(world["expert"])
    url = f"{base(world)}/transcript/download"
    job = (await client.post(url, json={"format": "pdf"})).json()["job_id"]
    assert (await client.post(url, json={"format": "pdf"})).json()["job_id"] == job
    assert (await client.get(f"{url}/{job}")).status_code == 409
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    assert (await client.post(url, json={"format": "pdf"})).json()["job_id"] == job
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    got = await client.get(f"{url}/{job}")
    assert got.status_code == 200 and got.content.startswith(b"%PDF")


async def test_a_bad_format_is_422(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["expert"])
    assert (await client.post(f"{base(world)}/transcript/download", json={"format": "rtf"})).status_code == 422


async def test_a_pdf_job_is_returned_as_a_pdf(
    client_for: Any,
    admin: psycopg.Connection,
    seed: Seed,
    world: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    app: FastAPI,
) -> None:

    class Shelf:
        async def delete(self, paths: Any) -> None:
            return None

    app.state.storage = Shelf()  # only `take` is replaced below; removing the copy is a no-op here
    client = await client_for(world["expert"])
    job = seed.job(world["case"], kind="export_transcript_pdf", requested_by=world["expert"])
    admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    admin.execute("update app.jobs set status = 'done' where id = %s", (job,))
    seen: list[str] = []

    async def take(conn: Any, storage: Any, crypto: Any, case_id: Any, job_id: Any, fmt: str = "docx") -> bytes:
        seen.append(fmt)
        return b"%PDF-1.4 x"

    monkeypatch.setattr(transcript_jobs, "take", take)
    resp = await client.get(f"{base(world)}/transcript/download/{job}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/pdf")
    assert resp.headers["content-disposition"] == f'attachment; filename="transcript-{job}.pdf"'
    assert seen == ["pdf"]


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


async def test_a_viewer_who_cannot_read_pages_does_not_queue_the_entries_job(
    client_for: Any, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, seed: Seed, world: dict[str, Any]
) -> None:
    viewer = seed.person()
    seed.case_member(world["case"], viewer, "viewer", added_by=world["expert"])
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    admin.execute(
        "insert into app.page_analysis (page_id, bundle_id, extracted_at) select id, bundle_id, now() from app.pages"
        " where bundle_id = (select bundle_id from app.cases where id = %s)",
        (world["case"],),
    )
    client = await client_for(viewer)
    resp = await client.get(f"{base(world)}/transcript/entries")
    assert resp.status_code == 200 and resp.json()["entries"] == []
    row = admin.execute(
        "select count(*) from app.jobs where case_id = %s and kind = 'extract_entries'", (world["case"],)
    ).fetchone()
    assert row is not None and row[0] == 0


async def test_a_picture_is_refused_while_the_entries_are_not_ready(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [], analysed=False)
    client = await client_for(world["expert"])
    resp = await client.post(f"{base(world)}/transcript/seen", json={"page_no": 2, "picture_opened": True})
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "not_ready"


async def test_the_listing_names_the_pages_that_need_their_picture_opened(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("fine",))])
    await add_page(aconn, CRYPTO, world["case"], doc, 2, [])
    client = await client_for(world["expert"])
    assert (await client.get(f"{base(world)}/transcript/entries")).json()["picture_pages"] == [2]


async def test_entries_carry_the_section_they_are_in(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("hello",), day=date(2020, 1, 2))])
    client = await client_for(world["expert"])
    entry = (await client.get(f"{base(world)}/transcript/entries")).json()["entries"][0]
    assert "section" in entry


async def test_the_outline_gives_sections_with_offsets_and_page_coverage(
    client_for: Any, aconn: psycopg.AsyncConnection, world: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        Pack,
        "transcript_sections",
        lambda self: [
            {"key": "clinic", "label": "t.clinic", "sources": ["clinic_notes"]},
            {"key": "rest", "label": "t.rest"},
        ],
        raising=False,
    )
    doc = await add_document(aconn, world["case"])
    for n, source in ((1, "other_src"), (2, "clinic_notes"), (3, "clinic_notes")):
        built = await add_page(aconn, CRYPTO, world["case"], doc, n, [Spec("zz_session", (f"note {n}",))])
        await aconn.execute("update app.pages set source_type = %s where id = %s", (source, built.page_id))
    await add_page(aconn, CRYPTO, world["case"], doc, 4, [])  # no entries: a picture page
    await add_page(aconn, CRYPTO, world["case"], doc, 5, [Spec("zz_session", ("copy",))], quarantined=True)
    client = await client_for(world["expert"])
    resp = await client.get(f"{base(world)}/transcript/outline")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "ready"
    assert body["sections"] == [
        {
            "key": "clinic", "label": "t.clinic", "count": 2, "offset": 0, "first_date": None, "last_date": None,
            "pages": 2, "years": [{"year": None, "offset": 0, "count": 2}],
        },
        {
            "key": "rest", "label": "t.rest", "count": 1, "offset": 2, "first_date": None, "last_date": None,
            "pages": 1, "years": [{"year": None, "offset": 2, "count": 1}],
        },
    ]  # fmt: skip
    assert body["coverage"] == {"total_pages": 4, "with_entries": 3, "no_entries": 1, "unreadable": 0, "set_aside": 1}


async def test_the_outline_says_preparing_and_queues_nothing_while_entries_are_not_ready(
    client_for: Any, aconn: psycopg.AsyncConnection, admin: psycopg.Connection, world: dict[str, Any]
) -> None:
    doc = await add_document(aconn, world["case"])
    await add_page(aconn, CRYPTO, world["case"], doc, 1, [Spec("zz_session", ("x",))], analysed=False)
    client = await client_for(world["expert"])
    body = (await client.get(f"{base(world)}/transcript/outline")).json()
    assert body["status"] in ("idle", "preparing") and body["sections"] == [] and body["coverage"] is None
    assert _jobs(admin, world) == 0


async def test_a_stranger_gets_404_on_the_outline(client_for: Any, world: dict[str, Any]) -> None:
    client = await client_for(world["stranger"])
    assert (await client.get(f"{base(world)}/transcript/outline")).status_code == 404
