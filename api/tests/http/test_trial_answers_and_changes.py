"""Slice I's ports onto slice G, wired to G's data through the core contracts.

- S11: the answers to every served round of written questions go into the trial pack and the offline copy,
  round by round; a round not yet served does not.
- Rule 8: a changed position in the joint statement (with its note) becomes a G `report_changes` row for
  each served paragraph it came from, sealed as G seals it, so G's supplementary report picks it up.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import base64
import io
import json
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pypdf import PdfReader

from app.core.contracts import report_changes, written_answers
from app.core.db import Database
from app.features.trial import services
from app.features.trial.ports import GReportChanges
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    combo,
    model,
    reads,
    run_job,
    storage,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CHANGED_VIEW, CRYPTO, DISCUSSION, SERVED_VIEW, World, agenda_pdf, meeting_type
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def add_round(
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    w: World,
    number: int,
    answers: list[str],
    *,
    serve: bool = True,
) -> None:
    row = admin.execute(
        "insert into app.written_question_rounds (case_id, number, received_on) values (%s, %s, '2026-07-01')"
        " returning id",
        (w.case, number),
    ).fetchone()
    assert row is not None
    for n, text in enumerate(answers, start=1):
        asked = await CRYPTO.encrypt(aconn, w.case, b"Zz asked", purpose="written_question")
        admin.execute(
            "insert into app.written_questions (case_id, round_id, number, text_verbatim) values (%s, %s, %s, %s)",
            (w.case, row[0], n, asked),
        )
        answer = await CRYPTO.encrypt(aconn, w.case, text.encode(), purpose=written_answers.ANSWER_PURPOSE)
        admin.execute(
            "update app.written_questions set answer_text = %s, status = 'answered'"
            " where round_id = %s and number = %s",
            (answer, row[0], n),
        )
    version = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name)"
        " values (%s, %s, %s, 'answers', 'Zz answers')",
        (version, w.case, 10 + number),
    )
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("d" * 64, version))
    admin.execute(
        "update app.written_question_rounds set status = 'answered', answers_version_id = %s where id = %s",
        (version, row[0]),
    )
    if serve:
        admin.execute("update app.report_versions set status = 'served' where id = %s", (version,))
        admin.execute("update app.written_question_rounds set status = 'served' where id = %s", (row[0],))


def pdf_text(data: bytes) -> str:
    reader = PdfReader(io.BytesIO(data))
    return " ".join(" ".join((page.extract_text() or "").split()) for page in reader.pages)


async def test_served_answers_go_into_the_trial_pack_round_by_round_s11(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
) -> None:
    await add_round(admin, aconn, world, 1, ["Zz the plan was recorded twice."])
    await add_round(admin, aconn, world, 2, ["Zz the review fell due in May."])
    await add_round(admin, aconn, world, 3, ["Zz an answer not yet served."], serve=False)
    client = await client_for(world.expert)
    saved = await client.put(
        world.url("trial-pack/settings"), json={"sections": ["written_answers"]}, headers={"If-Match": '"0"'}
    )
    assert saved.status_code == 200, saved.text
    resp = await client.post(world.url("trial-pack"))
    assert resp.status_code == 202, resp.text
    await run_job(worker_db, world, "build_trial_pack", resp.json()["job_id"])
    pdf = await client.get(world.url("trial-pack/latest/download"))
    assert pdf.status_code == 200, pdf.text
    text = pdf_text(pdf.content)
    assert "Zz the plan was recorded twice." in text and "Zz the review fell due in May." in text
    assert text.index("Round 1") < text.index("recorded twice") < text.index("Round 2") < text.index("fell due")
    assert "not yet served" not in text


async def test_served_answers_go_into_the_offline_copy_with_their_round(
    client_for: ClientFor, world: World, admin: psycopg.Connection, aconn: psycopg.AsyncConnection
) -> None:
    await add_round(admin, aconn, world, 1, ["Zz first answer.", "Zz second answer."])
    await add_round(admin, aconn, world, 2, ["Zz unserved."], serve=False)
    client = await client_for(world.expert)
    made = await client.post(world.url("offline-packages"), json={"device_label": "Zz tablet"})
    assert made.status_code == 201, made.text
    payload = (await client.get(world.url(f"offline-packages/{made.json()['id']}/download"))).json()
    aad = f"avodeck-offline:{payload['id']}:{payload['format_version']}".encode()
    plain = AESGCM(base64.b64decode(payload["key"])).decrypt(
        base64.b64decode(payload["iv"]), base64.b64decode(payload["ciphertext"]), aad
    )
    content = json.loads(plain)
    assert content["answers"] == [
        {"round_no": 1, "question_no": 1, "text": "Zz first answer."},
        {"round_no": 1, "question_no": 2, "text": "Zz second answer."},
    ]


async def test_a_noted_change_of_position_becomes_a_g_report_change(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    model: Callable[..., Scripted],
) -> None:
    from app.core.jobs import JobContext
    from app.features.trial.jobs import read_agenda

    model()
    services.current().changes = GReportChanges()
    client = await client_for(world.expert)
    body = {"type": meeting_type(world.combo)["key"], "discussion_on": DISCUSSION.isoformat()}
    meeting = (await client.post(world.url("expert-meetings"), json=body)).json()
    resp = await client.post(
        world.url(f"expert-meetings/{meeting['id']}/agenda"),
        content=agenda_pdf(["Was the low iron diagnosed in time?"]),
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202, resp.text
    await read_agenda(JobContext(worker_db, uuid.UUID(resp.json()["job_id"]), world.case, "read_agenda", 1))
    item: dict[str, Any] = (await client.get(world.url(f"expert-meetings/{meeting['id']}"))).json()["items"][0]

    async def edit(**change: Any) -> dict[str, Any]:
        out = await client.patch(
            world.url(f"agenda-items/{item['id']}"), json=change, headers={"If-Match": f'"{item["version"]}"'}
        )
        assert out.status_code == 200, out.text
        edited: dict[str, Any] = out.json()
        return edited

    item = await edit(own_position=CHANGED_VIEW)
    item = await edit(change_note="Zz new results since the report.")
    rows = admin.execute(
        "select served_version_id, paragraph_id, kind, old_text, new_text, reason from app.report_changes"
        " where case_id = %s",
        (world.case,),
    ).fetchall()
    assert [(r[0], r[1], r[2]) for r in rows] == [(world.report, world.paragraphs[0], "change_of_opinion")]
    _, _, _, old, new, reason = rows[0]
    opened = [
        (await CRYPTO.decrypt(aconn, world.case, bytes(v), purpose=p)).decode()
        for v, p in (
            (old, report_changes.OLD_PURPOSE),
            (new, report_changes.NEW_PURPOSE),
            (reason, report_changes.REASON_PURPOSE),
        )
    ]
    assert opened == [SERVED_VIEW, CHANGED_VIEW, "Zz new results since the report."]
    again = await edit(change_note="Zz said again.")
    assert again["id"] == item["id"]
    assert admin.execute("select count(*) from app.report_changes where case_id = %s", (world.case,)).fetchone() == (1,)


def test_the_services_reach_g_through_the_contracts_by_default() -> None:
    from app.features.trial.ports import GHearings, GWrittenAnswers

    made = services.TrialServices()
    assert isinstance(made.hearings, GHearings)
    assert isinstance(made.answers, GWrittenAnswers)
    assert isinstance(made.changes, GReportChanges)
