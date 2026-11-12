# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Security audit 16 (document provenance and output integrity), slice G's part: PV-19a, PV-19b, PV-18.

Each test asserts the safe outcome and names its finding:

- PV-19a: signing a supplementary report or a set of answers locks a `report_versions` row. It had none of what
  the main report's signing has: (1) the draft the expert read is what is locked (a change edited after the
  build refuses the sign), (2) every citation in it still stands (a cited fact rejected, changed, or on a page set
  aside refuses the sign, 409 `citation_problems`), (3) what it was signed with is frozen (declarations, expert,
  pack and guideline versions) beside the file's own snapshot, (4) the version (or the round) is locked
  for the sign: two signs at once lock one version, and an edit cannot slip in between reading and locking.
- PV-19b: a signed supplementary or answers file whose stored object was replaced is refused at download
  (409 `hash_mismatch`); a good download carries `X-Content-SHA256`.
- PV-18: the OCR run of the written-question and opposing-report jobs leaves an `ai_calls` row (model, region, job).
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.db import Database
from app.core.http import etag
from app.core.storage import CaseFileStorage
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    CRYPTO,
    SERVED_ON,
    Scripted,
    World,
    aconn,
    app,
    combo,
    mailer,
    make_version,
    model,
    ocr,
    reads,
    run_job,
    served,
    services,
    storage,
    worker_db,
    world,
)
from tests.http.test_after_serving_changes import make_change
from tests.http.test_after_serving_questions import answer_all, imported
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

SIGN = {"declaration_confirmed": True}
PURPOSE = "report_snapshot:"


async def draft_supplementary(
    client_for: ClientFor, worker_db: Database, model: Scripted, w: World
) -> tuple[dict[str, Any], str]:
    """A built draft supplementary report with one change; returns the change and the draft's id."""
    change = await make_change(client_for, worker_db, model, w)
    expert = await client_for(w.expert)
    await run_job(worker_db, w, await expert.post(w.url("report-changes/supplementary")), "build_supplementary")
    draft = (await expert.get(w.url("report-changes"))).json()["supplementary"]
    return change, draft["id"]


async def ready_answers(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    w: World,
) -> dict[str, Any]:
    """A round with every question answered, citing the second page's fact; returns the sign body."""
    made, questions = await imported(client_for, storage, admin, aconn, worker_db, model, w)
    await answer_all(await client_for(w.expert), w, questions)
    return {"round_id": made["id"], "declaration_confirmed": True}


def error_code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


# --- PV-19a (2): citations that no longer stand ----------------------------------------------------------------

BREAKS: dict[str, Callable[[psycopg.Connection, World, int], None]] = {
    "rejected": lambda admin, w, i: admin.execute(
        "update app.facts set status = 'rejected' where id = %s", (w.facts[i],)
    ),
    "changed": lambda admin, w, i: admin.execute(
        "update app.facts set value = 'other words entirely', status = 'edited' where id = %s", (w.facts[i],)
    ),
    "set_aside": lambda admin, w, i: admin.execute(
        "update app.pages set quarantined = true where id = %s", (w.pages[i],)
    ),
}


@pytest.mark.parametrize("how", list(BREAKS))
async def test_pv19a_a_supplementary_report_citing_a_fact_that_no_longer_stands_is_not_signed(
    how: str,
    client_for: ClientFor,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    _, draft = await draft_supplementary(client_for, worker_db, model, served)
    BREAKS[how](admin, served, 0)  # the change cites the first page's fact
    resp = await (await client_for(served.expert)).post(served.url(f"report/versions/{draft}/sign"), json=SIGN)
    assert resp.status_code == 409 and error_code(resp) == "citation_problems", resp.text
    row = admin.execute("select status, locked_at from app.report_versions where id = %s", (draft,)).fetchone()
    assert row == ("draft", None), "nothing was locked"


@pytest.mark.parametrize("how", list(BREAKS))
async def test_pv19a_answers_citing_a_fact_that_no_longer_stands_are_not_signed(
    how: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    body = await ready_answers(client_for, storage, admin, aconn, worker_db, model, served)
    BREAKS[how](admin, served, 1)  # the answers cite the second page's fact
    resp = await (await client_for(served.expert)).post(served.url("written-questions/answers/sign"), json=body)
    assert resp.status_code == 409 and error_code(resp) == "citation_problems", resp.text
    row = admin.execute("select status from app.written_question_rounds where id = %s", (body["round_id"],)).fetchone()
    assert row == ("open",), "the round is still open"
    assert admin.execute(
        "select count(*) from app.report_versions where case_id = %s and type = 'answers'", (served.case,)
    ).fetchone() == (0,)


# --- PV-19a (1): the draft the expert read is what is locked ---------------------------------------------------


async def test_pv19a_a_change_edited_after_the_draft_was_built_is_not_signed_unseen(
    client_for: ClientFor,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    change, draft = await draft_supplementary(client_for, worker_db, model, served)
    expert = await client_for(served.expert)
    fresh = (await expert.get(served.url(f"report-changes/{change['id']}"))).json()
    edited = await expert.patch(
        served.url(f"report-changes/{change['id']}"),
        json={"reason": "A different reason the draft does not show."},
        headers={"If-Match": etag(fresh["version"])},
    )
    assert edited.status_code == 200, edited.text
    resp = await expert.post(served.url(f"report/versions/{draft}/sign"), json=SIGN)
    assert resp.status_code == 409 and error_code(resp) == "draft_changed", resp.text
    assert admin.execute("select status from app.report_versions where id = %s", (draft,)).fetchone() == ("draft",)
    # building it again shows the expert the new draft, which then signs
    await run_job(
        worker_db, served, await expert.post(served.url("report-changes/supplementary")), "build_supplementary"
    )
    assert (await expert.post(served.url(f"report/versions/{draft}/sign"), json=SIGN)).status_code == 200


async def test_pv19a_an_unbuilt_supplementary_report_is_not_signed(
    client_for: ClientFor, worker_db: Database, model: Scripted, admin: psycopg.Connection, served: World
) -> None:
    _, draft = await draft_supplementary(client_for, worker_db, model, served)
    admin.execute("update app.report_versions set docx_path = null where id = %s", (draft,))
    resp = await (await client_for(served.expert)).post(served.url(f"report/versions/{draft}/sign"), json=SIGN)
    assert resp.status_code == 409 and error_code(resp) == "draft_changed", resp.text


# --- PV-19a (3): what the version is signed with is frozen -----------------------------------------------------

FROZEN = {"declarations", "expert", "packs", "guidelines"}


async def frozen_kinds(client_for: ClientFor, w: World, version: str) -> dict[str, Any]:
    resp = await (await client_for(w.expert)).get(w.url(f"report/versions/{version}/snapshots"))
    assert resp.status_code == 200, resp.text
    return {s["kind"]: s for s in resp.json()["snapshots"]}


async def test_pv19a_a_signed_supplementary_report_freezes_what_it_was_signed_with(
    client_for: ClientFor, worker_db: Database, model: Scripted, served: World
) -> None:
    _, draft = await draft_supplementary(client_for, worker_db, model, served)
    signed = await (await client_for(served.expert)).post(served.url(f"report/versions/{draft}/sign"), json=SIGN)
    assert signed.status_code == 200, signed.text
    snaps = await frozen_kinds(client_for, served, draft)
    assert FROZEN | {"docx"} <= set(snaps), sorted(snaps)
    assert snaps["docx"]["sha256"] == signed.json()["version"]["sha256"]
    assert snaps["declarations"]["content"]["pack"], "the declaration wording as it stood"
    assert snaps["expert"]["content"]["full_name"]


async def test_pv19a_signed_answers_freeze_what_they_were_signed_with(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    body = await ready_answers(client_for, storage, admin, aconn, worker_db, model, served)
    signed = await (await client_for(served.expert)).post(served.url("written-questions/answers/sign"), json=body)
    assert signed.status_code == 200, signed.text
    snaps = await frozen_kinds(client_for, served, signed.json()["version"]["id"])
    assert FROZEN | {"docx"} <= set(snaps), sorted(snaps)
    assert snaps["declarations"]["content"]["pack"]


# --- PV-19a (4): the version is locked for the sign ------------------------------------------------------------


async def two_signs_at_once(
    client_for: ClientFor, storage: CaseFileStorage, monkeypatch: pytest.MonkeyPatch, w: World, url: str, body: Any
) -> list[Any]:
    """Two signs by the expert at once, the first held on its first file write until the second has had its
    chance to read the same state."""
    arrived, release = asyncio.Event(), asyncio.Event()
    first = [True]
    real_put: Callable[..., Awaitable[Any]] = storage.put

    async def put(path: str, data: bytes) -> Any:
        if first[0]:
            first[0] = False
            arrived.set()
            await release.wait()
        return await real_put(path, data)

    monkeypatch.setattr(storage, "put", put)
    expert = await client_for(w.expert)
    one = asyncio.create_task(expert.post(url, json=body))
    await asyncio.wait_for(arrived.wait(), 20)
    two = asyncio.create_task(expert.post(url, json=body))
    await asyncio.sleep(1.0)
    release.set()
    return list(await asyncio.wait_for(asyncio.gather(one, two), 30))


async def test_pv19a_two_signs_at_once_of_one_supplementary_report_lock_it_once(
    client_for: ClientFor,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    _, draft = await draft_supplementary(client_for, worker_db, model, served)
    got = await two_signs_at_once(
        client_for, storage, monkeypatch, served, served.url(f"report/versions/{draft}/sign"), SIGN
    )
    assert sorted(r.status_code for r in got) == [200, 409], [r.text for r in got]
    assert admin.execute(
        "select count(*) from app.report_snapshots where report_version_id = %s and kind = 'docx'", (draft,)
    ).fetchone() == (1,)


async def test_pv19a_two_signs_at_once_of_one_set_of_answers_lock_one_version(
    client_for: ClientFor,
    storage: CaseFileStorage,
    monkeypatch: pytest.MonkeyPatch,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    body = await ready_answers(client_for, storage, admin, aconn, worker_db, model, served)
    got = await two_signs_at_once(
        client_for, storage, monkeypatch, served, served.url("written-questions/answers/sign"), body
    )
    assert sorted(r.status_code for r in got) == [200, 409], [r.text for r in got]
    assert admin.execute(
        "select count(*) from app.report_versions where case_id = %s and type = 'answers'", (served.case,)
    ).fetchone() == (1,), "one round, one signed answers version"


# --- PV-19b: the downloaded file is the signed one -------------------------------------------------------------


async def signed_supplementary_path(
    client_for: ClientFor, admin: psycopg.Connection, worker_db: Database, model: Scripted, w: World
) -> tuple[str, str, str]:
    _, draft = await draft_supplementary(client_for, worker_db, model, w)
    signed = await (await client_for(w.expert)).post(w.url(f"report/versions/{draft}/sign"), json=SIGN)
    assert signed.status_code == 200, signed.text
    row = admin.execute("select docx_path from app.report_versions where id = %s", (draft,)).fetchone()
    assert row is not None
    return draft, row[0], signed.json()["version"]["sha256"]


async def test_pv19b_a_good_signed_file_downloads_with_the_hash_people_check_outside_avodeck(
    client_for: ClientFor, admin: psycopg.Connection, worker_db: Database, model: Scripted, served: World
) -> None:
    draft, _, sha = await signed_supplementary_path(client_for, admin, worker_db, model, served)
    resp = await (await client_for(served.expert)).get(
        served.url(f"report/versions/{draft}/download"), params={"format": "docx"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.headers["x-content-sha256"] == sha == hashlib.sha256(resp.content).hexdigest()


async def test_pv19b_a_signed_supplementary_file_whose_stored_object_was_replaced_is_refused(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    draft, path, _ = await signed_supplementary_path(client_for, admin, worker_db, model, served)
    await storage.put(path, await CRYPTO.encrypt(aconn, served.case, b"not the signed file", purpose=path))
    resp = await (await client_for(served.expert)).get(
        served.url(f"report/versions/{draft}/download"), params={"format": "docx"}
    )
    assert resp.status_code == 409 and error_code(resp) == "hash_mismatch", resp.text
    assert b"not the signed file" not in resp.content


async def test_pv19b_signed_answers_whose_stored_object_was_replaced_are_refused(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    body = await ready_answers(client_for, storage, admin, aconn, worker_db, model, served)
    expert = await client_for(served.expert)
    signed = await expert.post(served.url("written-questions/answers/sign"), json=body)
    assert signed.status_code == 200, signed.text
    version = signed.json()["version"]["id"]
    row = admin.execute("select docx_path from app.report_versions where id = %s", (version,)).fetchone()
    assert row is not None
    await storage.put(row[0], await CRYPTO.encrypt(aconn, served.case, b"not the signed file", purpose=row[0]))
    resp = await expert.get(served.url(f"report/versions/{version}/download"), params={"format": "docx"})
    assert resp.status_code == 409 and error_code(resp) == "hash_mismatch", resp.text


# --- PV-18: the OCR run of a job is recorded -------------------------------------------------------------------


async def test_pv18_the_ocr_run_of_the_written_questions_job_leaves_an_ai_calls_row(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    ocr: Any,
    reads: Any,
    mailer: Any,
    served: World,
) -> None:
    from app.core.ai.bedrock import ModelRequest  # noqa: F401
    from app.core.ai.kit import AiKit
    from app.features.after_serving.services import AfterServingServices, configure
    from tests.support.ai import cell

    configure(
        AfterServingServices(
            crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), model), ocr=ocr, mailer=mailer, region="uk"
        )
    )
    model.answers.clear()
    queued_ids: list[uuid.UUID] = []
    real = run_job

    async def capture(db: Database, w: World, queued: Any, kind: str) -> None:
        queued_ids.append(uuid.UUID(queued.json()["job_id"]))
        await real(db, w, queued, kind)

    import tests.http.test_after_serving_questions as module

    monkey = pytest.MonkeyPatch()
    try:
        monkey.setattr(module, "run_job", capture)
        await imported(client_for, storage, admin, aconn, worker_db, model, served)
    finally:
        monkey.undo()
    assert ocr.calls, "the job read the document"
    rows = admin.execute(
        "select task_id, model_id, region, job_id from app.ai_calls where case_id = %s and task_id like 'ocr.%%'",
        (served.case,),
    ).fetchall()
    assert len(rows) == len(ocr.calls) >= 1, rows
    for task_id, model_id, region, job_id in rows:
        assert model_id == "layout" and region == "uk" and job_id == queued_ids[0], (task_id, model_id, region)


async def test_pv18_no_region_in_the_cell_config_is_an_error_not_a_placeholder(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    ocr: Any,
    reads: Any,
    mailer: Any,
    served: World,
) -> None:
    from app.core.ai.kit import AiKit
    from app.core.config import ConfigError
    from app.features.after_serving.services import AfterServingServices, configure
    from tests.support.ai import cell

    configure(
        AfterServingServices(
            crypto=CRYPTO, storage=storage, reads=reads, kit=AiKit(cell(), model), ocr=ocr, mailer=mailer, region=None
        )
    )
    with pytest.raises(ConfigError):
        await imported(client_for, storage, admin, aconn, worker_db, model, served)
    rows = admin.execute(
        "select count(*) from app.ai_calls where case_id = %s and task_id like 'ocr.%%'", (served.case,)
    )
    assert rows.fetchone() == (0,), "nothing was recorded under a made-up region"


async def test_serving_makes_the_deadlines_even_where_slice_g_was_not_started_by_the_app(
    client_for: ClientFor,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    api_db: Database,
    world: World,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slice F's serve route publishes `ReportServed`; a test (or worker) app that never ran slice G's start-up
    must still make the deadlines from the packs, not fail the serving with "not started"."""
    from app.core.contracts import events
    from app.features.after_serving import events as served_events

    await client_for(world.expert)
    version = await make_version(admin, aconn, world)
    monkeypatch.setattr(served_events, "_app", None)
    async with api_db.request(scope_for(world.expert)) as conn:
        await events.events.publish(conn, events.ReportServed(world.case, version, SERVED_ON, world.expert))
    row = admin.execute(
        "select count(*) from app.deadlines where case_id = %s and created_from = 'pack'", (world.case,)
    )
    assert (row.fetchone() or (0,))[0] >= 1
