"""Slice F, signing and serving (screen 18): `POST .../report/sign` (step-up) locks the latest draft once
its final check passed and is current, citations still stand and any corrected declaration is acknowledged.
The signed Word
and PDF files carry the expert's signature, name and date; their SHA-256 are stored with frozen snapshots of
what the report was signed with (architecture 10.6, rule 10). Downloads stream with `no-store` and are
audited; a locked version's download is checked against its stored hash.

Acceptance (slice F brief): sign answers 403 `step_up_required` without recent MFA and 409 if the latest
final check did not pass or is older than the latest draft; signing sets `locked_at`, stores SHA-256 of both
files and writes to the audit log; signing freezes declarations, signature, template, pack, guideline and
instruction versions and conflict answers, so a later profile change does not alter the signed report;
signed documents are named from the pack ("Report 1"); F34 blocks signing until acknowledged.

Serving (step-up) records `served_on` and `sent_via`, moves the version to served and the case to served,
and emits `report_served`; more servings and corrections are added to a served version; a served version
is withdrawn with a reason, stays locked and readable, and cannot be served again (rule 8).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import io
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from datetime import date, timedelta
from typing import Any

import docx
import psycopg
import pytest
from fastapi import FastAPI

import app.core.http as core_http
from app.core.ai.bedrock import ModelRequest, ModelResponse
from app.core.ai.kit import AiKit
from app.core.contracts import events
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.report.checks import declaration_version
from app.features.report.jobs import build_report, final_check
from app.features.report.services import ReportServices, configure
from tests.fixtures.report.world import (
    CRYPTO,
    Reads,
    World,
    app_for,
    build,
    make_ready,
    store_signature,
)
from tests.support.ai import CELL_ENV, cell
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

SIGN = {"declaration_confirmed": True}
FROZEN = {"declarations", "signature", "expert", "template", "packs", "guidelines", "instructions", "conflicts"}


class Answering:
    def __init__(self) -> None:
        self.endpoint = CELL_ENV["BEDROCK_ENDPOINT"]

    async def invoke(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(output={"issues": []}, input_tokens=100, output_tokens=10)


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: SupabaseStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture(autouse=True)
def services(reads: Reads, storage: SupabaseStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), Answering())))
    yield
    configure(None)


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
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


@pytest.fixture
def steps(client_for: ClientFor, worker_db: Database, world: World) -> dict[str, Callable[[], Any]]:
    async def build_now() -> None:
        queued = await (await client_for(world.expert)).post(world.url("report/build"))
        assert queued.status_code == 202, queued.text
        await build_report(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), world.case, "build_report", 1))

    async def check_now() -> dict[str, Any]:
        client = await client_for(world.expert)
        queued = await client.post(world.url("report/final-check"))
        assert queued.status_code == 202, queued.text
        await final_check(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), world.case, "final_check", 1))
        latest = await client.get(world.url("report/final-check/latest"))
        assert latest.status_code == 200, latest.text
        run: dict[str, Any] = latest.json()
        return run

    return {"build": build_now, "check": check_now}


@pytest.fixture
async def ready(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: SupabaseStorage,
    steps: dict[str, Callable[[], Any]],
) -> World:
    """A case whose latest draft passed a current final check, with the signature image on file."""
    await make_ready(seed, admin, aconn, world, reads)
    await store_signature(storage, admin, world)
    await steps["build"]()
    run = await steps["check"]()
    assert run["passed"] is True, [r for r in run["results"] if r["outcome"] == "failed"]
    return world


def code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


def newest(admin: psycopg.Connection, case: uuid.UUID) -> dict[str, Any]:
    row = admin.execute(
        "select id, status, locked_at, display_name, sha256, docx_path, pdf_path from app.report_versions"
        " where case_id = %s order by version_no desc limit 1",
        (case,),
    ).fetchone()
    assert row is not None
    keys = ("id", "status", "locked_at", "display_name", "sha256", "docx_path", "pdf_path")
    return dict(zip(keys, row, strict=True))


async def test_signing_without_recent_mfa_is_refused(client_for: ClientFor, ready: World) -> None:
    client = await client_for(ready.expert, mfa_age=timedelta(minutes=15))
    resp = await client.post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 403 and code(resp) == "step_up_required"


async def test_only_the_named_expert_signs(client_for: ClientFor, ready: World) -> None:
    for person in (ready.co_expert, ready.assistant, ready.viewer):
        resp = await (await client_for(person)).post(ready.url("report/sign"), json=SIGN)
        assert resp.status_code == 403, person
    resp = await (await client_for(ready.outsider)).post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 404


async def test_signing_needs_the_declaration_confirmed(client_for: ClientFor, ready: World) -> None:
    client = await client_for(ready.expert)
    for body in ({}, {"declaration_confirmed": False}):
        resp = await client.post(ready.url("report/sign"), json=body)
        assert resp.status_code == 422, body


async def test_signing_without_a_passing_current_final_check_is_refused(
    client_for: ClientFor,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: SupabaseStorage,
    steps: dict[str, Callable[[], Any]],
) -> None:
    client = await client_for(world.expert)
    resp = await client.post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "not_built"

    await make_ready(seed, admin, aconn, world, reads, read_all=False)
    await store_signature(storage, admin, world)
    await steps["build"]()
    resp = await client.post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "final_check_needed", "no final check yet"

    run = await steps["check"]()
    assert run["passed"] is False
    resp = await client.post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "final_check_failed"

    reads.rows.add((world.case, world.pages[2], 3, world.expert))
    assert (await steps["check"]())["passed"] is True
    await steps["build"]()
    resp = await client.post(world.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "final_check_needed", "the check is older than the draft"
    assert newest(admin, world.case)["status"] == "draft"


async def test_a_citation_that_stopped_standing_after_the_check_blocks_signing(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    admin.execute("update app.facts set value = 'Ferritin 90 ug/L' where id = %s", (ready.result,))
    resp = await (await client_for(ready.expert)).post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "citation_problems"
    assert newest(admin, ready.case)["locked_at"] is None


async def test_a_corrected_declaration_blocks_signing_until_acknowledged(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    admin.execute(
        "insert into app.report_settings (case_id, acknowledged_declaration_version) values (%s, 'zz-old')"
        " on conflict (case_id) do update set acknowledged_declaration_version = 'zz-old'",
        (ready.case,),
    )
    client = await client_for(ready.expert)
    resp = await client.post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 409 and code(resp) == "declaration_not_acknowledged"
    assert (await client.post(ready.url("report/declarations/acknowledge"))).status_code == 200
    resp = await client.post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 200, resp.text


async def test_signing_locks_the_draft_stores_both_hashes_and_writes_the_audit_log(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    client = await client_for(ready.expert)
    resp = await client.post(ready.url("report/sign"), json=SIGN)
    assert resp.status_code == 200, resp.text
    body = resp.json()

    row = newest(admin, ready.case)
    assert row["status"] == "locked" and row["locked_at"] is not None
    assert row["display_name"] == "Report 1"
    assert body["version"]["id"] == str(row["id"]) and body["version"]["display_name"] == "Report 1"
    assert body["version"]["status"] == "locked"

    files = {}
    for fmt in ("docx", "pdf"):
        download = await client.get(ready.url(f"report/versions/{row['id']}/download?format={fmt}"))
        assert download.status_code == 200, download.text
        assert download.headers["cache-control"] == "private, no-store"
        files[fmt] = hashlib.sha256(download.content).hexdigest()
        assert body["version"][f"{fmt}_sha256"] == files[fmt]
    assert row["sha256"] == hashlib.sha256(f"docx:{files['docx']}\npdf:{files['pdf']}".encode()).hexdigest()

    actions = [
        r[0]
        for r in admin.execute(
            "select action from app.audit_log where case_id = %s and target = %s order by seq",
            (ready.case, f"report_version:{row['id']}"),
        ).fetchall()
    ]
    assert actions == ["report.signed", "report.downloaded", "report.downloaded"]
    settings = admin.execute(
        "select acknowledged_declaration_version from app.report_settings where case_id = %s", (ready.case,)
    ).fetchone()
    assert settings is not None
    assert settings[0] == declaration_version(default_library().combine(*ready.combo.codes))

    again = await client.post(ready.url("report/sign"), json=SIGN)
    assert again.status_code == 409 and code(again) == "not_built", "nothing left to sign"


async def test_the_signed_word_file_carries_the_signature_name_and_date(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    admin.execute("update app.people set full_name = 'Zed Quill' where id = %s", (ready.expert,))
    client = await client_for(ready.expert)
    assert (await client.post(ready.url("report/sign"), json=SIGN)).status_code == 200
    row = newest(admin, ready.case)
    download = await client.get(ready.url(f"report/versions/{row['id']}/download?format=docx"))
    document = docx.Document(io.BytesIO(download.content))
    assert len(document.inline_shapes) >= 1, "the signature image is in the signed file"
    text = "\n".join(p.text for p in document.paragraphs)
    assert "Zed Quill" in text


async def test_signing_freezes_what_the_report_was_signed_with(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    client = await client_for(ready.expert)
    assert (await client.post(ready.url("report/sign"), json=SIGN)).status_code == 200
    row = newest(admin, ready.case)
    admin.execute("update app.experts set registration_number = 'ZZ999' where person_id = %s", (ready.expert,))

    resp = await client.get(ready.url(f"report/versions/{row['id']}/snapshots"))
    assert resp.status_code == 200, resp.text
    snapshots = {s["kind"]: s for s in resp.json()["snapshots"]}
    assert {"docx", "pdf"} | FROZEN <= set(snapshots)
    assert snapshots["expert"]["content"]["registration_number"] == "ZZ123"
    pack = default_library().combine(*ready.combo.codes)
    assert snapshots["packs"]["content"] == pack.versions
    assert snapshots["conflicts"]["content"]["outcome"] == "none"
    for snapshot in snapshots.values():
        assert len(snapshot["sha256"]) == 64
    stored = admin.execute(
        "select string_agg(encode(content, 'escape'), '') from app.report_snapshots where report_version_id = %s",
        (row["id"],),
    ).fetchone()
    assert stored is not None and "ZZ123" not in (stored[0] or ""), "snapshot content is sealed under the case key"


async def test_a_locked_version_refuses_a_download_whose_file_no_longer_matches_its_hash(
    client_for: ClientFor,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    storage: SupabaseStorage,
    ready: World,
) -> None:
    client = await client_for(ready.expert)
    assert (await client.post(ready.url("report/sign"), json=SIGN)).status_code == 200
    row = newest(admin, ready.case)
    tampered = await CRYPTO.encrypt(aconn, ready.case, b"not the signed file", purpose=row["pdf_path"])
    await storage.put(row["pdf_path"], tampered)
    resp = await client.get(ready.url(f"report/versions/{row['id']}/download?format=pdf"))
    assert resp.status_code == 409 and code(resp) == "hash_mismatch"


async def test_downloads_need_case_access_and_a_known_format(
    client_for: ClientFor, ready: World, admin: psycopg.Connection
) -> None:
    row = newest(admin, ready.case)
    resp = await (await client_for(ready.outsider)).get(ready.url(f"report/versions/{row['id']}/download?format=pdf"))
    assert resp.status_code == 404
    resp = await (await client_for(ready.expert)).get(ready.url(f"report/versions/{row['id']}/download?format=txt"))
    assert resp.status_code == 422
    resp = await (await client_for(ready.expert)).get(ready.url(f"report/versions/{uuid.uuid4()}/download?format=pdf"))
    assert resp.status_code == 404


async def signed(client_for: ClientFor, w: World) -> str:
    resp = await (await client_for(w.expert)).post(w.url("report/sign"), json=SIGN)
    assert resp.status_code == 200, resp.text
    return str(resp.json()["version"]["id"])


def served_body(**extra: Any) -> dict[str, Any]:
    return {"served_on": date.today().isoformat(), "sent_via": "email", **extra}


async def test_serving_needs_recent_mfa_and_the_named_expert(client_for: ClientFor, ready: World) -> None:
    version = await signed(client_for, ready)
    url = ready.url(f"report/versions/{version}/serve")
    resp = await (await client_for(ready.expert, mfa_age=timedelta(minutes=15))).post(url, json=served_body())
    assert resp.status_code == 403 and code(resp) == "step_up_required"
    for person in (ready.co_expert, ready.assistant, ready.viewer):
        assert (await (await client_for(person)).post(url, json=served_body())).status_code == 403
    assert (await (await client_for(ready.outsider)).post(url, json=served_body())).status_code == 404


async def test_a_draft_cannot_be_served(client_for: ClientFor, admin: psycopg.Connection, ready: World) -> None:
    draft = newest(admin, ready.case)["id"]
    resp = await (await client_for(ready.expert)).post(ready.url(f"report/versions/{draft}/serve"), json=served_body())
    assert resp.status_code == 409 and code(resp) == "not_locked"


async def test_serving_records_the_date_and_route_serves_the_case_and_emits_report_served(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    version = await signed(client_for, ready)
    heard: list[events.ReportServed] = []

    def listen(event: events.ReportServed) -> None:
        heard.append(event)

    events.events.subscribe(events.ReportServed)(listen)
    try:
        client = await client_for(ready.expert)
        resp = await client.post(ready.url(f"report/versions/{version}/serve"), json=served_body())
    finally:
        events.events.unsubscribe(events.ReportServed, listen)
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "served"

    status = admin.execute("select status from app.report_versions where id = %s", (version,)).fetchone()
    assert status is not None and status[0] == "served"
    case = admin.execute("select status from app.cases where id = %s", (ready.case,)).fetchone()
    assert case is not None and case[0] == "served"
    serving = admin.execute(
        "select served_on, sent_via from app.report_servings where report_version_id = %s", (version,)
    ).fetchall()
    assert serving == [(date.today(), "email")]
    assert heard == [events.ReportServed(ready.case, uuid.UUID(version), date.today())]
    audit = admin.execute(
        "select action from app.audit_log where case_id = %s and action = 'report.served'", (ready.case,)
    ).fetchall()
    assert len(audit) == 1

    again = await client.post(ready.url(f"report/versions/{version}/serve"), json=served_body())
    assert again.status_code == 409 and code(again) == "already_served"


async def test_serving_refuses_a_future_date_and_an_unknown_route(client_for: ClientFor, ready: World) -> None:
    version = await signed(client_for, ready)
    client = await client_for(ready.expert)
    url = ready.url(f"report/versions/{version}/serve")
    future = (date.today() + timedelta(days=2)).isoformat()
    assert (await client.post(url, json=served_body(served_on=future))).status_code == 422
    assert (await client.post(url, json=served_body(sent_via="pigeon"))).status_code == 422


async def test_servings_are_listed_and_a_correction_is_added_to_a_served_version(
    client_for: ClientFor, ready: World
) -> None:
    version = await signed(client_for, ready)
    client = await client_for(ready.expert)
    servings = ready.url(f"report/versions/{version}/servings")
    resp = await client.post(servings, json=served_body())
    assert resp.status_code == 409 and code(resp) == "not_served"

    assert (await client.post(ready.url(f"report/versions/{version}/serve"), json=served_body())).status_code == 200
    listed = await (await client_for(ready.viewer)).get(servings)
    assert listed.status_code == 200, listed.text
    first = listed.json()["servings"]
    assert len(first) == 1 and first[0]["sent_via"] == "email"

    correction = served_body(sent_via="post", correction_of=first[0]["id"], reason="wrong_date")
    resp = await client.post(servings, json=correction)
    assert resp.status_code == 201, resp.text
    assert resp.json()["correction_of"] == first[0]["id"]
    assert (await client.post(servings, json=served_body(reason="wrong_date"))).status_code == 422
    assert len((await client.get(servings)).json()["servings"]) == 2
    viewer = await (await client_for(ready.viewer)).post(servings, json=served_body())
    assert viewer.status_code == 403


async def test_a_served_version_is_withdrawn_with_a_reason_and_stays_locked_and_readable(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    version = await signed(client_for, ready)
    client = await client_for(ready.expert)
    withdraw = ready.url(f"report/versions/{version}/withdraw")
    resp = await client.post(withdraw, json={"reason": "factual_error"})
    assert resp.status_code == 409 and code(resp) == "not_served"

    assert (await client.post(ready.url(f"report/versions/{version}/serve"), json=served_body())).status_code == 200
    assert (await client.post(withdraw, json={})).status_code == 422
    stale = await client_for(ready.expert, mfa_age=timedelta(minutes=15))
    assert code(await stale.post(withdraw, json={"reason": "factual_error"})) == "step_up_required"
    resp = await client.post(withdraw, json={"reason": "factual_error"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "withdrawn"

    row = admin.execute(
        "select status, locked_at, withdrawn_reason from app.report_versions where id = %s", (version,)
    ).fetchone()
    assert row is not None and row[0] == "withdrawn" and row[1] is not None and row[2] == "factual_error"
    download = await client.get(ready.url(f"report/versions/{version}/download?format=pdf"))
    assert download.status_code == 200
    again = await client.post(withdraw, json={"reason": "factual_error"})
    assert again.status_code == 409
    serve = await client.post(ready.url(f"report/versions/{version}/serve"), json=served_body())
    assert serve.status_code == 409
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'report.withdrawn'", (ready.case,)
    ).fetchone()
    assert audit is not None and audit[0] == 1


# Two signs at once, signed downloads (review findings 1 and 9), serving after commit (finding 8) ----------


async def test_two_signs_at_once_give_one_signed_version_whose_downloads_verify(
    client_for: ClientFor,
    admin: psycopg.Connection,
    storage: SupabaseStorage,
    ready: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_put = storage.put

    async def slow_put(path: str, data: bytes) -> None:
        await asyncio.sleep(0.3)  # both requests are past their checks before either commits
        await real_put(path, data)

    monkeypatch.setattr(storage, "put", slow_put)
    first, second = await client_for(ready.expert), await client_for(ready.expert)
    answers = await asyncio.gather(
        first.post(ready.url("report/sign"), json=SIGN), second.post(ready.url("report/sign"), json=SIGN)
    )
    assert sorted(a.status_code for a in answers) == [200, 409], [a.text for a in answers]
    won = next(a for a in answers if a.status_code == 200).json()["version"]
    monkeypatch.setattr(storage, "put", real_put)

    locked = admin.execute(
        "select count(*) from app.report_versions where case_id = %s and locked_at is not null", (ready.case,)
    ).fetchone()
    assert locked == (1,)
    row = newest(admin, ready.case)
    assert str(row["id"]) == won["id"]
    assert row["docx_path"] != row["pdf_path"]
    for fmt in ("docx", "pdf"):
        download = await first.get(ready.url(f"report/versions/{row['id']}/download?format={fmt}"))
        assert download.status_code == 200, download.text
        assert hashlib.sha256(download.content).hexdigest() == won[f"{fmt}_sha256"]


async def test_downloading_a_signed_version_needs_step_up(
    client_for: ClientFor, admin: psycopg.Connection, ready: World
) -> None:
    version = await signed(client_for, ready)
    late = await client_for(ready.expert, mfa_age=timedelta(minutes=15))
    for fmt in ("docx", "pdf"):
        resp = await late.get(ready.url(f"report/versions/{version}/download?format={fmt}"))
        assert resp.status_code == 403 and code(resp) == "step_up_required", fmt
    downloads = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'report.downloaded'", (ready.case,)
    ).fetchone()
    assert downloads == (0,)


async def test_a_serve_that_does_not_commit_emits_nothing(
    client_for: ClientFor, admin: psycopg.Connection, ready: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    version = await signed(client_for, ready)
    heard: list[events.ReportServed] = []

    def listen(event: events.ReportServed) -> None:
        heard.append(event)

    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the transaction does not commit")

    monkeypatch.setattr(core_http, "_remember", fail)
    events.events.subscribe(events.ReportServed)(listen)
    try:
        client = await client_for(ready.expert)
        with contextlib.suppress(RuntimeError):
            resp = await client.post(
                ready.url(f"report/versions/{version}/serve"),
                json=served_body(),
                headers={"Idempotency-Key": "zz-serve-once"},
            )
            assert resp.status_code >= 500
    finally:
        events.events.unsubscribe(events.ReportServed, listen)
    assert heard == []
    status = admin.execute("select status from app.report_versions where id = %s", (version,)).fetchone()
    assert status == ("locked",)
