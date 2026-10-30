"""Screen 22, expert meetings and the joint statement (architecture 5.2).

- `POST /cases/{c}/expert-meetings` (`case.edit_setup`): the type comes from the pack's `meetings.yaml`; the
  sign-by and send-by dates from the pack's deadlines with their rule reference.
- Participants: any number, on Avodeck or not (`case.edit_setup`).
- `POST .../agenda` and `.../agenda-versions` (`case.upload`): the agenda PDF; `read_agenda` reads it.
- `PATCH /cases/{c}/agenda-items/{id}` (`opinion.write`, If-Match): position, other views, agreed, reason,
  change note. A position that is not the served text word for word is checked by T-22c; a change note on
  a changed position is passed to slice G's report changes (rule 8).
- `POST .../prepare` (`opinion.write`): 409 `change_note_needed` while a changed position has no note.
- `POST .../send-for-signature` (`report.sign`, step-up): the prepared statement and a signing reference.
- `POST .../signed-external` (`report.sign`, step-up): a copy signed elsewhere, kept by its hash.
"""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.ai.kit import InvalidOutput
from app.core.contracts.served import ServedReport, latest_served
from app.core.errors import ProcessingPaused
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.trial import ai_tasks, repo
from app.features.trial.agenda import same_words, served_text
from app.features.trial.common import (
    AGREED_REASON,
    CHANGE_NOTE,
    NO_STORE,
    OTHER_POSITIONS,
    POSITION,
    REASON,
    Crypto,
    Pack,
    Setup,
    Sign,
    Storage,
    Upload,
    View,
    Write,
    conflict,
    invalid,
    meeting_type,
    not_found,
    pdf_body,
    seal,
    sha256,
    trial_path,
)
from app.features.trial.ports import ReportChange
from app.features.trial.router import router
from app.features.trial.services import current
from app.features.trial.statement import statement_pdf
from app.features.trial.storage import Sealer, get_sealed, put_sealed

MeetingStatus = Literal["draft", "out_for_signature", "signed", "signed_external"]
Deviation = Literal["consistent", "changed", "unclear"]


class ParticipantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    discipline: str | None = Field(default=None, max_length=80)
    side: str | None = Field(default=None, pattern=REASON)
    person_id: uuid.UUID | None = None


class ParticipantOut(BaseModel):
    id: uuid.UUID
    name: str
    discipline: str | None
    side: str | None
    on_avodeck: bool


class ParticipantsOut(BaseModel):
    participants: list[ParticipantOut]


class MeetingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: str = Field(pattern=REASON)
    discussion_on: date
    hearing_id: uuid.UUID | None = None
    participants: list[ParticipantIn] = Field(default_factory=list, max_length=20)


class AgendaVersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    read_at: datetime | None
    item_count: int | None
    created_at: datetime


class AgendaVersionsOut(BaseModel):
    versions: list[AgendaVersionOut]


class ItemOut(BaseModel):
    id: uuid.UUID
    number: int
    text: str
    own_position: str | None
    other_positions: str | None
    agreed: bool | None
    reason: str | None
    change_note: str | None
    deviation: Deviation | None
    deviation_reason: str | None
    served_paragraphs: list[int]
    served_text: str | None = None
    pages: list[int]
    changed_from_previous: Literal["same", "changed", "new"] | None
    version: int


class MeetingSummary(BaseModel):
    id: uuid.UUID
    type: str
    label: str
    status: MeetingStatus
    hearing_id: uuid.UUID | None
    discussion_on: date
    sign_by: date
    sign_rule_ref: str
    sign_rule_phrase: str | None = None
    send_by: date
    send_rule_ref: str
    send_rule_phrase: str | None = None
    declarations: list[str]
    prepared_sha256: str | None
    prepared_pages: int | None
    certificate_sha256: str | None
    signed_sha256: str | None
    version: int


class MeetingOut(MeetingSummary):
    items: list[ItemOut]
    agenda_versions: list[AgendaVersionOut]


class MeetingsOut(BaseModel):
    meetings: list[MeetingSummary]


class ItemChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    own_position: str | None = Field(default=None, max_length=20_000)
    other_positions: str | None = Field(default=None, max_length=20_000)
    agreed: bool | None = None
    reason: str | None = Field(default=None, max_length=5_000)
    change_note: str | None = Field(default=None, min_length=1, max_length=5_000)


class Prepared(BaseModel):
    sha256: str
    pages: int


class AgendaQueued(BaseModel):
    job_id: uuid.UUID
    agenda_version_id: uuid.UUID


SIGNED = ("signed", "signed_external")


def _label(pack: Any, key: str) -> str:
    kind = meeting_type(pack, key)
    return str(kind["label"]) if kind else key


def _phrase(pack: Any, key: str) -> str | None:
    """How the pack words a deadline on a screen chip ("7 days after the discussion")."""
    rule = next((r for r in pack.deadline_rules() if r["key"] == key), None)
    if rule is None or not rule.get("phrase") or "days" not in rule:
        return None
    return str(rule["phrase"]).replace("<days>", str(rule["days"]))


def _summary(pack: Any, row: dict[str, Any]) -> MeetingSummary:
    kind = meeting_type(pack, row["type"]) or {}
    return MeetingSummary(
        id=row["id"],
        type=row["type"],
        label=_label(pack, row["type"]),
        status=row["status"],
        hearing_id=row["hearing_id"],
        discussion_on=row["discussion_on"],
        sign_by=row["sign_by"],
        sign_rule_ref=row["sign_rule_ref"],
        sign_rule_phrase=_phrase(pack, str(kind.get("sign_deadline", "expert_meeting_signed"))),
        send_by=row["send_by"],
        send_rule_ref=row["send_rule_ref"],
        send_rule_phrase=_phrase(pack, str(kind.get("send_deadline", "expert_meeting_sent"))),
        declarations=[str(d) for d in row["declarations"]],
        prepared_sha256=row["prepared_sha256"],
        prepared_pages=row["prepared_pages"],
        certificate_sha256=row["certificate_sha256"],
        signed_sha256=row["signed_sha256"],
        version=row["version"],
    )


def _item(row: dict[str, Any], served: ServedReport | None) -> ItemOut:
    numbers = {p.id: p.para_no for p in served.paragraphs} if served else {}
    cited = {p.id: [no for _, no in p.cited] for p in served.paragraphs} if served else {}
    texts = {p.id: p.text for p in served.paragraphs} if served else {}
    quoted = "\n".join(texts[p] for p in row["served_paragraph_ids"] if p in texts)
    return ItemOut(
        id=row["id"],
        number=row["number"],
        text=row["text"],
        own_position=row["own_position"],
        other_positions=row["other_positions"],
        agreed=row["agreed"],
        reason=row["reason"],
        change_note=row["change_note"],
        deviation=row["deviation"],
        deviation_reason=row["deviation_reason"],
        served_paragraphs=[numbers[p] for p in row["served_paragraph_ids"] if p in numbers],
        served_text=quoted or None,
        pages=sorted({no for p in row["served_paragraph_ids"] for no in cited.get(p, [])}),
        changed_from_previous=row["changed_from_previous"],
        version=row["version"],
    )


async def _meeting(conn: Any, case_id: uuid.UUID, meeting_id: uuid.UUID) -> dict[str, Any]:
    row = await repo.meeting(conn, case_id, meeting_id)
    if row is None:
        raise not_found()
    return row


async def _person_on_case(conn: Any, case_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select 1 from app.cases where id = %s and named_expert_id = %s"
        " union select 1 from app.case_members where case_id = %s and person_id = %s",
        (case_id, person_id, case_id, person_id),
    )
    return await cur.fetchone() is not None


async def _add_participant(conn: Any, case_id: uuid.UUID, meeting_id: uuid.UUID, p: ParticipantIn) -> uuid.UUID:
    if p.person_id is not None and not await _person_on_case(conn, case_id, p.person_id):
        raise invalid("person_id")
    cur = await conn.execute(
        "insert into app.expert_meeting_participants (case_id, meeting_id, name, discipline, side, person_id)"
        " values (%s, %s, %s, %s, %s, %s) returning id",
        (case_id, meeting_id, p.name, p.discipline, p.side, p.person_id),
    )
    row = await cur.fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def _report_changes(
    served: ServedReport | None, row: dict[str, Any], position: str | None, note: str
) -> list[ReportChange]:
    """G `report_changes` rows for a changed position: one per served paragraph the item came from."""
    if served is None or position is None:
        return []
    texts = {p.id: p.text for p in served.paragraphs}
    return [
        ReportChange(
            served_version_id=served.version_id,
            paragraph_id=paragraph_id,
            kind="change_of_opinion",
            old_text=texts[paragraph_id],
            new_text=position,
            reason=note,
        )
        for paragraph_id in row["served_paragraph_ids"]
        if paragraph_id in texts
    ]


@router.post("/cases/{case_id}/expert-meetings", status_code=201, response_model=MeetingSummary)
async def create_meeting(
    case_id: uuid.UUID, body: MeetingIn, conn: DbConn, access: Setup, pack: Pack, response: Response
) -> MeetingSummary:
    kind = meeting_type(pack, body.type)
    if kind is None:
        raise invalid("type")
    if not await current().hearings.belongs(conn, case_id, body.hearing_id):
        raise invalid("hearing_id")
    sign = pack.deadline(str(kind.get("sign_deadline", "expert_meeting_signed")), body.discussion_on)
    send = pack.deadline(str(kind.get("send_deadline", "expert_meeting_sent")), sign.due_on)
    declarations = [str(d) for d in kind.get("standard_declarations", [])]
    row = await repo.one(
        conn,
        "insert into app.expert_meetings (case_id, type, hearing_id, discussion_on, sign_by, sign_rule_ref, send_by,"
        " send_rule_ref, declarations, pack_version) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning *",
        (
            case_id,
            body.type,
            body.hearing_id,
            body.discussion_on,
            sign.due_on,
            sign.rule_ref,
            send.due_on,
            send.rule_ref,
            json.dumps(declarations),
            json.dumps(pack.versions),
        ),
    )
    assert row is not None
    for participant in body.participants:
        await _add_participant(conn, case_id, row["id"], participant)
    response.headers["ETag"] = etag(row["version"])
    return _summary(pack, row)


@router.get("/cases/{case_id}/expert-meetings", response_model=MeetingsOut)
async def list_meetings(case_id: uuid.UUID, conn: DbConn, access: View, pack: Pack) -> MeetingsOut:
    rows = await repo.many(conn, "select * from app.expert_meetings where case_id = %s order by created_at", (case_id,))
    return MeetingsOut(meetings=[_summary(pack, r) for r in rows])


class MeetingTypeOut(BaseModel):
    key: str
    label: str
    declarations: list[str]
    notes: str | None


class MeetingTypesOut(BaseModel):
    types: list[MeetingTypeOut]


@router.get("/cases/{case_id}/expert-meetings/types", response_model=MeetingTypesOut)
async def list_meeting_types(case_id: uuid.UUID, access: View, pack: Pack) -> MeetingTypesOut:
    """The kinds of expert meeting the case's packs know, with their wording (rule 11)."""
    return MeetingTypesOut(
        types=[
            MeetingTypeOut(
                key=str(t["key"]),
                label=str(t["label"]),
                declarations=[str(d) for d in t.get("standard_declarations", [])],
                notes=str(t["notes"]) if t.get("notes") else None,
            )
            for t in pack.meeting_types()
        ]
    )


@router.get("/cases/{case_id}/expert-meetings/{meeting_id}", response_model=MeetingOut)
async def get_meeting(
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    conn: DbConn,
    access: View,
    pack: Pack,
    crypto: Crypto,
    response: Response,
) -> MeetingOut:
    row = await _meeting(conn, case_id, meeting_id)
    served = await latest_served(conn, crypto, case_id)
    items = await repo.current_items(conn, crypto, case_id, meeting_id)
    versions = await repo.agenda_versions(conn, meeting_id)
    response.headers["ETag"] = etag(row["version"])
    return MeetingOut(
        **_summary(pack, row).model_dump(),
        items=[_item(i, served) for i in items],
        agenda_versions=[AgendaVersionOut(**v) for v in versions],
    )


@router.get("/cases/{case_id}/expert-meetings/{meeting_id}/participants", response_model=ParticipantsOut)
async def list_participants(case_id: uuid.UUID, meeting_id: uuid.UUID, conn: DbConn, access: View) -> ParticipantsOut:
    await _meeting(conn, case_id, meeting_id)
    rows = await repo.many(
        conn,
        "select * from app.expert_meeting_participants where meeting_id = %s order by created_at, id",
        (meeting_id,),
    )
    rows.sort(key=lambda r: r["created_at"])
    return ParticipantsOut(
        participants=[
            ParticipantOut(
                id=r["id"],
                name=r["name"],
                discipline=r["discipline"],
                side=r["side"],
                on_avodeck=r["person_id"] is not None,
            )
            for r in rows
        ]
    )


@router.post(
    "/cases/{case_id}/expert-meetings/{meeting_id}/participants", status_code=201, response_model=ParticipantOut
)
async def add_participant(
    case_id: uuid.UUID, meeting_id: uuid.UUID, body: ParticipantIn, conn: DbConn, access: Setup
) -> ParticipantOut:
    row = await _meeting(conn, case_id, meeting_id)
    if row["status"] in SIGNED:
        raise conflict("signed", "This statement is signed and cannot change.")
    pid = await _add_participant(conn, case_id, meeting_id, body)
    return ParticipantOut(
        id=pid, name=body.name, discipline=body.discipline, side=body.side, on_avodeck=body.person_id is not None
    )


async def _queue_agenda(
    request: Request, case_id: uuid.UUID, meeting_id: uuid.UUID, conn: Any, storage: Any, crypto: Any
) -> AgendaQueued:
    row = await _meeting(conn, case_id, meeting_id)
    if row["status"] in SIGNED:
        raise conflict("signed", "This statement is signed and cannot change.")
    data = await pdf_body(request)
    cur = await conn.execute(
        "select coalesce(max(version_no), 0) + 1 from app.agenda_versions where meeting_id = %s", (meeting_id,)
    )
    found = await cur.fetchone()
    version_no = int(found[0]) if found else 1
    version_id = uuid.uuid4()
    path = trial_path(case_id, "agendas", f"{version_id}.pdf")
    await put_sealed(Sealer(crypto, storage), conn, case_id, path, data)
    job_id = await registry.enqueue(conn, case_id, "read_agenda")
    await conn.execute(
        "insert into app.agenda_versions (id, case_id, meeting_id, version_no, storage_path, sha256, job_id)"
        " values (%s, %s, %s, %s, %s, %s, %s)",
        (version_id, case_id, meeting_id, version_no, path, sha256(data), job_id),
    )
    return AgendaQueued(job_id=job_id, agenda_version_id=version_id)


@router.post("/cases/{case_id}/expert-meetings/{meeting_id}/agenda", status_code=202, response_model=AgendaQueued)
async def upload_agenda(
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: Upload,
    storage: Storage,
    crypto: Crypto,
) -> AgendaQueued:
    return await _queue_agenda(request, case_id, meeting_id, conn, storage, crypto)


@router.post(
    "/cases/{case_id}/expert-meetings/{meeting_id}/agenda-versions", status_code=202, response_model=AgendaQueued
)
async def new_agenda_version(
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: Upload,
    storage: Storage,
    crypto: Crypto,
) -> AgendaQueued:
    return await _queue_agenda(request, case_id, meeting_id, conn, storage, crypto)


@router.get("/cases/{case_id}/expert-meetings/{meeting_id}/agenda-versions", response_model=AgendaVersionsOut)
async def list_agenda_versions(
    case_id: uuid.UUID, meeting_id: uuid.UUID, conn: DbConn, access: View
) -> AgendaVersionsOut:
    await _meeting(conn, case_id, meeting_id)
    return AgendaVersionsOut(versions=[AgendaVersionOut(**v) for v in await repo.agenda_versions(conn, meeting_id)])


async def _check_position(conn: Any, pack: Any, case_id: uuid.UUID, served: str, draft: str) -> tuple[str, str]:
    """T-22c's label for a drafted position against the served one (unclear if the check cannot run)."""
    try:
        kit = current().model()
        answer = await kit.run(
            conn,
            ai_tasks.t22c(pack),
            pack=pack,
            text=f"served\t{' '.join(served.split())}\ndraft\t{' '.join(draft.split())}",
            case_id=case_id,
            subject="position",
        )
    except (ProcessingPaused, InvalidOutput):
        return "unclear", "check_unavailable"
    return str(answer["status"]), str(answer["reason"])


@router.patch("/cases/{case_id}/agenda-items/{item_id}", response_model=ItemOut)
async def change_item(
    case_id: uuid.UUID,
    item_id: uuid.UUID,
    body: ItemChange,
    conn: DbConn,
    access: Write,
    pack: Pack,
    crypto: Crypto,
    if_match: IfMatch,
    response: Response,
) -> ItemOut:
    row = await repo.one(
        conn, "select * from app.agenda_items where case_id = %s and id = %s for update", (case_id, item_id)
    )
    if row is None:
        raise not_found()
    meeting = await _meeting(conn, case_id, row["meeting_id"])
    if meeting["status"] in SIGNED:
        raise conflict("signed", "This statement is signed and cannot change.")
    if row["version"] != if_match:
        raise stale()
    await repo.open_item(conn, crypto, case_id, row)
    served = await latest_served(conn, crypto, case_id)
    sets: dict[str, Any] = {}
    given = body.model_fields_set
    if "own_position" in given and body.own_position != row["own_position"]:
        sets["own_position"] = await seal(conn, crypto, case_id, body.own_position, POSITION)
        original = served_text(served, list(row["served_paragraph_ids"]))
        if original is None or body.own_position is None:
            sets["deviation"], sets["deviation_reason"] = None, None
        elif same_words(original, body.own_position):
            sets["deviation"], sets["deviation_reason"] = "consistent", "copied_from_report"
        else:
            sets["deviation"], sets["deviation_reason"] = await _check_position(
                conn, pack, case_id, original, body.own_position
            )
        row["deviation"] = sets["deviation"]
    if "other_positions" in given:
        sets["other_positions"] = await seal(conn, crypto, case_id, body.other_positions, OTHER_POSITIONS)
    if "agreed" in given:
        sets["agreed"] = body.agreed
    if "reason" in given:
        sets["reason"] = await seal(conn, crypto, case_id, body.reason, AGREED_REASON)
    if "change_note" in given:
        sets["change_note"] = await seal(conn, crypto, case_id, body.change_note, CHANGE_NOTE)
        if (
            body.change_note
            and row["deviation"] == "changed"
            and row["change_recorded_at"] is None
            and row["served_paragraph_ids"]
        ):
            position = body.own_position if "own_position" in given else row["own_position"]
            changed = _report_changes(served, row, position, body.change_note)
            await current().changes.record(conn, crypto, case_id, changed)
            sets["change_recorded_at"] = datetime.now().astimezone()
    if sets:
        columns = ", ".join(f"{k} = %s" for k in sets)
        await conn.execute(
            f"update app.agenda_items set {columns} where id = %s",  # noqa: S608 - fixed column names
            (*sets.values(), item_id),
        )
        if meeting["status"] == "out_for_signature" or meeting["prepared_sha256"]:
            await conn.execute(
                "update app.expert_meetings set status = 'draft', signing_reference_sha256 = null, sent_at = null,"
                " prepared_path = null, prepared_sha256 = null, prepared_pages = null, prepared_at = null"
                " where id = %s",
                (meeting["id"],),
            )
    updated = await repo.one(conn, "select * from app.agenda_items where id = %s", (item_id,))
    assert updated is not None
    await repo.open_item(conn, crypto, case_id, updated)
    response.headers["ETag"] = etag(updated["version"])
    return _item(updated, served)


async def _participant_names(conn: Any, meeting_id: uuid.UUID) -> list[str]:
    rows = await repo.many(
        conn,
        "select name from app.expert_meeting_participants where meeting_id = %s order by created_at, id",
        (meeting_id,),
    )
    return [r["name"] for r in rows]


@router.post("/cases/{case_id}/expert-meetings/{meeting_id}/prepare", response_model=Prepared)
async def prepare_statement(
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    conn: DbConn,
    access: Write,
    pack: Pack,
    crypto: Crypto,
    storage: Storage,
) -> Prepared:
    meeting = await _meeting(conn, case_id, meeting_id)
    if meeting["status"] != "draft":
        raise conflict("not_draft", "Only a draft statement can be prepared.")
    items = await repo.current_items(conn, crypto, case_id, meeting_id)
    unnoted = [str(i["id"]) for i in items if i["deviation"] == "changed" and not i["change_note"]]
    if unnoted:
        raise conflict("change_note_needed", "Add a note to each changed view first.", items=unnoted)
    names = await _participant_names(conn, meeting_id)
    pdf, pages = statement_pdf(pack, _label(pack, meeting["type"]), meeting, names, items)
    digest = sha256(pdf)
    path = trial_path(case_id, "meetings", meeting_id, f"{digest}.pdf")
    await put_sealed(Sealer(crypto, storage), conn, case_id, path, pdf)
    await conn.execute(
        "update app.expert_meetings set prepared_path = %s, prepared_sha256 = %s, prepared_pages = %s,"
        " prepared_at = now() where id = %s",
        (path, digest, pages, meeting_id),
    )
    return Prepared(sha256=digest, pages=pages)


@router.post("/cases/{case_id}/expert-meetings/{meeting_id}/send-for-signature")
async def send_for_signature(
    case_id: uuid.UUID, meeting_id: uuid.UUID, conn: DbConn, access: Sign, storage: Storage, crypto: Crypto
) -> Response:
    meeting = await _meeting(conn, case_id, meeting_id)
    if meeting["status"] != "draft" or not meeting["prepared_path"]:
        raise conflict("not_prepared", "Prepare the statement first.")
    pdf = await get_sealed(Sealer(crypto, storage), conn, case_id, meeting["prepared_path"])
    reference = secrets.token_urlsafe(32)
    await conn.execute(
        "update app.expert_meetings set status = 'out_for_signature', signing_reference_sha256 = %s,"
        " sent_at = now() where id = %s",
        (sha256(reference.encode()), meeting_id),
    )
    await audit.record(conn, action="trial.statement_sent", target=str(meeting_id), case_id=case_id)
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={**NO_STORE, "X-Signing-Reference": reference, "X-Content-SHA256": meeting["prepared_sha256"]},
    )


@router.post("/cases/{case_id}/expert-meetings/{meeting_id}/signed-external", response_model=MeetingSummary)
async def signed_external(
    case_id: uuid.UUID,
    meeting_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: Sign,
    pack: Pack,
    storage: Storage,
    crypto: Crypto,
) -> MeetingSummary:
    meeting = await _meeting(conn, case_id, meeting_id)
    if meeting["status"] in SIGNED:
        raise conflict("signed", "This statement is signed and cannot change.")
    if meeting["status"] != "draft":
        raise conflict("not_draft", "This statement is out for signature. Wait for it, or change the agenda first.")
    data = await pdf_body(request)
    digest = sha256(data)
    path = trial_path(case_id, "meetings", meeting_id, f"signed-{digest}.pdf")
    await put_sealed(Sealer(crypto, storage), conn, case_id, path, data)
    row = await repo.one(
        conn,
        "update app.expert_meetings set status = 'signed_external', signed_sha256 = %s, signed_at = now()"
        " where id = %s returning *",
        (digest, meeting_id),
    )
    assert row is not None
    await audit.record(conn, action="trial.statement_signed_external", target=str(meeting_id), case_id=case_id)
    return _summary(pack, row)
