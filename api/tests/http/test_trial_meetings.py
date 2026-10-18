"""Screen 22, the joint statement (slice I brief, architecture 5.2 and 6.3).

- Creating a meeting takes its type from the pack's `meetings.yaml` and sets the sign-by and send-by dates
  from the pack's deadlines with their `rule_ref`; any number of participants, on Avodeck or not.
- An agenda is read (T-22a, words copied with their ids) and each item is filled from the served report
  (T-22b returns ids; code inserts the served paragraph word for word).
- A changed position is flagged by T-22c; the expert records a note before the statement is prepared, and
  the change is passed to slice G's report changes. The prepared statement carries the pack's
  declarations word for word.
- Sending for signature needs step-up; the e-sign webhook refuses a bad signature and on success stores
  the certificate and its hash and sets `signed`. A copy signed elsewhere is `signed_external`.
- A new agenda version shows what changed and moves a statement out for signature back to draft.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import uuid
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

import psycopg
import pypdfium2 as pdfium
import pytest

from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import default_library
from app.features.trial.jobs import read_agenda
from app.features.trial.ports import RecordedChanges
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    model,
    reads,
    storage,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CHANGED_VIEW, DISCUSSION, SERVED_VIEW, World, agenda_pdf, meeting_type, pdf_of
from tests.support.db import ClientFor
from tests.support.packs import BOTH, LAUNCH, Combo

pytestmark = pytest.mark.anyio

LAPSED = timedelta(minutes=11)
AGENDA = [
    "Was the low iron diagnosed in time?",
    "When was the plan recorded and repeated?",
    "By what date would a reasonably competent clinician have referred?",
]
AGENDA_V2 = [*AGENDA[:2], "By what date, if at all, would a reasonably competent clinician have referred?"]
SECRET = "zz-webhook-secret"


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    chosen: Combo = request.param
    return chosen


def code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


async def create(client_for: ClientFor, w: World, **change: Any) -> dict[str, Any]:
    body = {"type": meeting_type(w.combo)["key"], "discussion_on": DISCUSSION.isoformat(), **change}
    resp = await (await client_for(w.expert)).post(w.url("expert-meetings"), json=body)
    assert resp.status_code == 201, resp.text
    created: dict[str, Any] = resp.json()
    return created


async def get(client_for: ClientFor, w: World, meeting: str, person: uuid.UUID | None = None) -> Any:
    resp = await (await client_for(person or w.expert)).get(w.url(f"expert-meetings/{meeting}"))
    assert resp.status_code == 200, resp.text
    return resp


async def agenda(
    client_for: ClientFor, worker_db: Database, w: World, meeting: str, questions: list[str] = AGENDA
) -> None:
    resp = await (await client_for(w.expert)).post(
        w.url(f"expert-meetings/{meeting}/agenda"),
        content=agenda_pdf(questions),
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202, resp.text
    job = uuid.UUID(resp.json()["job_id"])
    await read_agenda(JobContext(worker_db, job, w.case, "read_agenda", 1))


async def edit(client_for: ClientFor, w: World, item: dict[str, Any], **change: Any) -> Any:
    return await (await client_for(w.expert)).patch(
        w.url(f"agenda-items/{item['id']}"), json=change, headers={"If-Match": f'"{item["version"]}"'}
    )


def pdf_text(data: bytes) -> str:
    doc = pdfium.PdfDocument(data)
    return " ".join(" ".join(doc[i].get_textpage().get_text_range().split()) for i in range(len(doc)))


async def test_creating_a_meeting_sets_deadlines_and_wording_from_the_pack(
    client_for: ClientFor, world: World, combo: Combo
) -> None:
    pack = default_library().combine(*combo.codes)
    kind = meeting_type(combo)
    created = await create(client_for, world)
    sign_key = str(kind.get("sign_deadline", "expert_meeting_signed"))
    send_key = str(kind.get("send_deadline", "expert_meeting_sent"))
    sign = pack.deadline(sign_key, DISCUSSION)
    send = pack.deadline(send_key, sign.due_on)
    assert created["status"] == "draft" and created["type"] == kind["key"] and created["label"] == kind["label"]
    assert created["sign_by"] == sign.due_on.isoformat() and created["sign_rule_ref"] == sign.rule_ref
    assert created["send_by"] == send.due_on.isoformat() and created["send_rule_ref"] == send.rule_ref
    assert created["declarations"] == kind["standard_declarations"]
    if combo is LAUNCH:
        assert (created["sign_by"], created["send_by"]) == ("2026-11-09", "2026-11-23")


async def test_a_type_the_pack_does_not_list_is_refused(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world.expert)).post(
        world.url("expert-meetings"), json={"type": "zz_not_a_type", "discussion_on": DISCUSSION.isoformat()}
    )
    assert resp.status_code == 422 and code(resp) == "invalid"


async def test_meetings_take_any_number_of_participants_on_avodeck_or_not(client_for: ClientFor, world: World) -> None:
    created = await create(
        client_for,
        world,
        participants=[
            {"name": "Dr Zz Other", "discipline": "zz_discipline", "side": "other_side"},
            {"name": "Dr Zz Colleague", "person_id": str(world.co_expert)},
        ],
    )
    client = await client_for(world.expert)
    added = await client.post(
        world.url(f"expert-meetings/{created['id']}/participants"), json={"name": "Dr Zz Third", "side": "joint"}
    )
    assert added.status_code == 201, added.text
    listed = await client.get(world.url(f"expert-meetings/{created['id']}/participants"))
    people = listed.json()["participants"]
    assert [p["name"] for p in people] == ["Dr Zz Other", "Dr Zz Colleague", "Dr Zz Third"]
    assert [p["on_avodeck"] for p in people] == [False, True, False]


async def test_only_people_on_the_case_see_a_meeting_and_only_the_expert_edits_it(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    outsider = await client_for(world.outsider)
    assert (await outsider.get(world.url(f"expert-meetings/{created['id']}"))).status_code == 404
    item = (await get(client_for, world, created["id"], world.viewer)).json()["items"][0]
    viewer = await client_for(world.viewer)
    resp = await viewer.patch(
        world.url(f"agenda-items/{item['id']}"), json={"agreed": True}, headers={"If-Match": f'"{item["version"]}"'}
    )
    assert resp.status_code == 403


async def test_agenda_items_are_copied_and_filled_word_for_word_from_the_served_report(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    scripted = model()
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    meeting = (await get(client_for, world, created["id"])).json()
    items = meeting["items"]
    assert [i["number"] for i in items] == [1, 2, 3]
    assert [i["text"] for i in items] == AGENDA
    assert items[0]["own_position"] == SERVED_VIEW and items[0]["served_paragraphs"] == [1]
    assert items[0]["deviation"] == "consistent"
    assert meeting["agenda_versions"][0]["version_no"] == 1
    assert len(scripted.seen("T-22a")) == 1 and len(scripted.seen("T-22b")) == 1
    assert scripted.seen("T-22c") == []  # a position copied word for word is not checked


async def test_agenda_words_the_model_did_not_copy_exactly_are_dropped(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    def altered(request: Any) -> dict[str, Any]:
        from tests.fixtures.trial.fixtures import agenda_items

        items = agenda_items(request)["items"]
        items[1]["text_verbatim"] = "When was the plan written?"
        return {"items": items}

    model(**{"T-22a": altered})
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    items = (await get(client_for, world, created["id"])).json()["items"]
    assert [i["number"] for i in items] == [1, 3]


async def test_the_planted_changed_position_needs_a_note_before_the_statement_is_prepared(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    model: Callable[..., Scripted],
    changes: RecordedChanges,
) -> None:
    scripted = model()
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    item = (await get(client_for, world, created["id"])).json()["items"][0]
    resp = await edit(client_for, world, item, own_position=CHANGED_VIEW)
    assert resp.status_code == 200, resp.text
    item = resp.json()
    assert item["deviation"] == "changed" and item["deviation_reason"] == "outcome_reversed"
    [check] = scripted.seen("T-22c")
    assert "more likely than not" in check.text and "probably would have been" in check.text
    client = await client_for(world.expert)
    refused = await client.post(world.url(f"expert-meetings/{created['id']}/prepare"))
    assert refused.status_code == 409 and code(refused) == "change_note_needed"
    assert refused.json()["error"]["items"] == [item["id"]]
    noted = await edit(client_for, world, item, change_note="Zz new results since the report.")
    assert noted.status_code == 200, noted.text
    assert changes.rows == [(world.case, uuid.UUID(item["id"]), [world.paragraphs[0]])]
    prepared = await client.post(world.url(f"expert-meetings/{created['id']}/prepare"))
    assert prepared.status_code == 200, prepared.text
    assert len(prepared.json()["sha256"]) == 64


async def test_the_prepared_statement_carries_the_pack_declarations_word_for_word(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted], combo: Combo
) -> None:
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    client = await client_for(world.expert)
    assert (await client.post(world.url(f"expert-meetings/{created['id']}/prepare"))).status_code == 200
    sent = await client.post(world.url(f"expert-meetings/{created['id']}/send-for-signature"))
    assert sent.status_code == 200, sent.text
    assert sent.headers["content-type"] == "application/pdf"
    text = pdf_text(sent.content)
    for line in list(meeting_type(combo)["standard_declarations"]):  # type: ignore[call-overload]
        assert " ".join(str(line).split()) in text
    assert AGENDA[0] in text


async def test_sending_for_signature_needs_step_up_and_the_webhook_signs_it(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    model: Callable[..., Scripted],
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    url = world.url(f"expert-meetings/{created['id']}")
    assert (await (await client_for(world.expert)).post(f"{url}/prepare")).status_code == 200
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).post(f"{url}/send-for-signature")
    assert lapsed.status_code == 403 and code(lapsed) == "step_up_required"
    sent = await (await client_for(world.expert)).post(f"{url}/send-for-signature")
    assert sent.status_code == 200, sent.text
    reference = sent.headers["x-signing-reference"]
    assert (await get(client_for, world, created["id"])).json()["status"] == "out_for_signature"

    certificate = b"zz signing certificate"
    signed_pdf = pdf_of([["zz", "signed"]])
    payload = json.dumps(
        {
            "reference": reference,
            "event": "signed",
            "signed_document_sha256": hashlib.sha256(signed_pdf).hexdigest(),
            "certificate": base64.b64encode(certificate).decode(),
        }
    ).encode()
    anyone = await client_for(None)
    unset = await anyone.post("/v1/webhooks/esign", content=payload, headers={"x-avodeck-signature": "sha256=00"})
    assert unset.status_code == 503
    monkeypatch.setenv("ESIGN_WEBHOOK_SECRET", SECRET)
    bad = await anyone.post(
        "/v1/webhooks/esign", content=payload, headers={"x-avodeck-signature": "sha256=" + "0" * 64}
    )
    assert bad.status_code == 401 and code(bad) == "bad_signature"
    assert (await get(client_for, world, created["id"])).json()["status"] == "out_for_signature"
    good = "sha256=" + hmac.new(SECRET.encode(), payload, hashlib.sha256).hexdigest()
    ok = await anyone.post("/v1/webhooks/esign", content=payload, headers={"x-avodeck-signature": good})
    assert ok.status_code == 200, ok.text
    meeting = (await get(client_for, world, created["id"])).json()
    assert meeting["status"] == "signed"
    assert meeting["certificate_sha256"] == hashlib.sha256(certificate).hexdigest()
    assert meeting["signed_sha256"] == hashlib.sha256(signed_pdf).hexdigest()
    again = await anyone.post("/v1/webhooks/esign", content=payload, headers={"x-avodeck-signature": good})
    assert again.status_code == 409
    item = meeting["items"][0]
    assert (await edit(client_for, world, item, agreed=True)).status_code == 409
    row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'trial.statement_signed'", (world.case,)
    ).fetchone()
    assert row == (1,)


async def test_a_statement_signed_elsewhere_is_recorded_with_its_hash(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    created = await create(client_for, world)
    signed = pdf_of([["zz", "signed", "on", "paper"]])
    url = world.url(f"expert-meetings/{created['id']}/signed-external")
    headers = {"content-type": "application/pdf"}
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).post(url, content=signed, headers=headers)
    assert lapsed.status_code == 403
    resp = await (await client_for(world.expert)).post(url, content=signed, headers=headers)
    assert resp.status_code == 200, resp.text
    meeting = (await get(client_for, world, created["id"])).json()
    assert meeting["status"] == "signed_external"
    assert meeting["signed_sha256"] == hashlib.sha256(signed).hexdigest()


async def test_a_new_agenda_version_shows_differences_and_moves_the_statement_back_to_draft(
    client_for: ClientFor, world: World, worker_db: Database, model: Callable[..., Scripted]
) -> None:
    created = await create(client_for, world)
    await agenda(client_for, worker_db, world, created["id"])
    first = (await get(client_for, world, created["id"])).json()["items"]
    noted = await edit(client_for, world, first[1], agreed=False, reason="Zz we differ on timing.")
    assert noted.status_code == 200, noted.text
    url = world.url(f"expert-meetings/{created['id']}")
    client = await client_for(world.expert)
    assert (await client.post(f"{url}/prepare")).status_code == 200
    assert (await client.post(f"{url}/send-for-signature")).status_code == 200
    await agenda(client_for, worker_db, world, created["id"], AGENDA_V2)
    meeting = (await get(client_for, world, created["id"])).json()
    assert meeting["status"] == "draft"
    assert [v["version_no"] for v in meeting["agenda_versions"]] == [1, 2]
    items = meeting["items"]
    assert [i["text"] for i in items] == AGENDA_V2
    assert [i["changed_from_previous"] for i in items] == ["same", "same", "changed"]
    assert items[1]["agreed"] is False and items[1]["reason"] == "Zz we differ on timing."
    versions = await client.get(f"{url}/agenda-versions")
    assert [v["version_no"] for v in versions.json()["versions"]] == [1, 2]


async def test_the_statement_to_sign_shows_on_today_with_its_date(
    client_for: ClientFor, world: World, combo: Combo
) -> None:
    created = await create(client_for, world)
    today = (await (await client_for(world.expert)).get("/v1/today")).json()
    [item] = [i for i in today["needs_you"] if i["kind"] == "trial_joint_statement_to_sign"]
    assert item["case_id"] == str(world.case) and item["screen"] == "S22"
    assert item["due_at"].startswith(created["sign_by"]) or date.fromisoformat(
        item["due_at"][:10]
    ) >= date.fromisoformat(created["sign_by"])
    assert io  # noqa: B018 - keeps the import list stable for the pdf helpers
