"""Screen 04: setting up a case (architecture 5.2, slice C brief).

Members, parties, instructing parties, instruction versions and oral instructions, case links, the subject
key and duplicate-case check, the jurisdiction change, hold and resume, and the subject's words. Every
case route depends on `require_case_access` (rule 9). Pack keys are checked against the case's packs
(rule 11). Names and oral instructions are encrypted under the case key and never logged (rule 6).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Literal

from fastapi import Depends, Request, Response
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core import audit
from app.core.auth.access import CaseAccess, check_case_access, require_case_access
from app.core.case_hold import release_case_hold
from app.core.contracts.instructing_parties import ORAL_INSTRUCTION_PURPOSE
from app.core.contracts.members import case_member_names
from app.core.crypto import CaseCrypto, subject_key_hash
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.core.packs import MergedPack
from app.features.setup.common import cell, invalid, library, not_found
from app.features.setup.router import router

CaseRole = Literal["co_expert", "supervisor", "assistant", "viewer"]
LinkKind = Literal["related", "previous_instruction", "replaces", "other_proceedings"]
KEY = r"^[a-z][a-z0-9_]{0,39}$"


ViewMetadata = Annotated[CaseAccess, Depends(require_case_access("case.view_metadata"))]
ViewContent = Annotated[CaseAccess, Depends(require_case_access("case.view_content"))]
ManageMembers = Annotated[CaseAccess, Depends(require_case_access("case.manage_members"))]
EditSetup = Annotated[CaseAccess, Depends(require_case_access("case.edit_setup"))]
Upload = Annotated[CaseAccess, Depends(require_case_access("case.upload"))]
ChangeJurisdiction = Annotated[CaseAccess, Depends(require_case_access("case.change_jurisdiction"))]
HoldAccess = Annotated[CaseAccess, Depends(require_case_access("case.hold"))]


def _crypto(request: Request) -> CaseCrypto:
    crypto: CaseCrypto | None = request.app.state.crypto
    if crypto is None:
        raise ApiError("unavailable", "Saving is not ready.", 503)
    return crypto


Crypto = Annotated[CaseCrypto, Depends(_crypto)]


def conflict(message: str) -> ApiError:
    return ApiError("conflict", message, 409)


async def _case_packs(conn: DbConn, case_id: uuid.UUID) -> tuple[str, str, str]:
    cur = await conn.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    return (str(row[0]), str(row[1]), str(row[2]))


async def _pack(request: Request, conn: DbConn, case_id: uuid.UUID) -> MergedPack:
    return library(request).combine(*await _case_packs(conn, case_id))


async def _served(conn: DbConn, case_id: uuid.UUID) -> bool:
    cur = await conn.execute("select app.case_ever_served(%s)", (case_id,))
    row = await cur.fetchone()
    return bool(row and row[0])


# --- members -------------------------------------------------------------------------------------------


class Member(BaseModel):
    id: uuid.UUID
    person_id: uuid.UUID
    case_role: str
    added_at: datetime
    display_name: str | None = None


class MemberIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    person_id: uuid.UUID
    case_role: CaseRole


_MEMBER = "select id, person_id, case_role, added_at from app.case_members"


@router.get("/cases/{case_id}/members", response_model=list[Member])
async def list_members(case_id: uuid.UUID, access: ViewMetadata, conn: DbConn) -> list[Member]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_MEMBER} where case_id = %s and removed_at is null order by added_at, id", (case_id,))
        rows = await cur.fetchall()
    names = await case_member_names(conn, case_id)
    return [Member(**r, display_name=names.get(r["person_id"])) for r in rows]


@router.post("/cases/{case_id}/members", status_code=201, response_model=Member)
async def add_member(case_id: uuid.UUID, body: MemberIn, access: ManageMembers, conn: DbConn) -> Member:
    cur = await conn.execute(
        "select c.named_expert_id = %s, exists (select 1 from app.memberships m where m.organisation_id"
        " = c.organisation_id and m.person_id = %s and m.ended_at is null) from app.cases c where c.id = %s",
        (body.person_id, body.person_id, case_id),
    )
    row = await cur.fetchone()
    if row is None or row[0] or not row[1]:
        raise invalid("person_id")
    cur = await conn.execute(
        "select 1 from app.case_members where case_id = %s and person_id = %s and removed_at is null",
        (case_id, body.person_id),
    )
    if await cur.fetchone():
        raise conflict("This person is already on the case.")
    member_id = uuid.uuid4()
    await conn.execute(
        "insert into app.case_members (id, case_id, person_id, case_role, added_by)"
        " values (%s, %s, %s, %s, app.current_person_id())",
        (member_id, case_id, body.person_id, body.case_role),
    )
    await audit.record(conn, action="case.member_added", target=f"case_member:{member_id}", case_id=case_id)
    async with conn.cursor(row_factory=dict_row) as dcur:
        await dcur.execute(f"{_MEMBER} where id = %s", (member_id,))
        added = await dcur.fetchone()
    assert added is not None
    names = await case_member_names(conn, case_id)
    return Member(**added, display_name=names.get(added["person_id"]))


@router.delete("/cases/{case_id}/members/{member_id}", status_code=204)
async def remove_member(case_id: uuid.UUID, member_id: uuid.UUID, access: ManageMembers, conn: DbConn) -> Response:
    cur = await conn.execute(
        "update app.case_members set removed_at = now() where id = %s and case_id = %s and removed_at is null"
        " returning id",
        (member_id, case_id),
    )
    if await cur.fetchone() is None:
        raise not_found()
    await audit.record(conn, action="case.member_removed", target=f"case_member:{member_id}", case_id=case_id)
    return Response(status_code=204)


# --- parties -------------------------------------------------------------------------------------------


class Party(BaseModel):
    id: uuid.UUID
    role_key: str
    name: str
    representative: str | None


class PartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str = Field(pattern=KEY)
    name: str = Field(min_length=1, max_length=300)
    representative: str | None = Field(default=None, max_length=300)


class PartyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_key: str | None = Field(default=None, pattern=KEY)
    name: str | None = Field(default=None, min_length=1, max_length=300)
    representative: str | None = Field(default=None, max_length=300)


_PARTY = "party_name"


async def _party(conn: DbConn, crypto: CaseCrypto, case_id: uuid.UUID, row: dict[str, Any]) -> Party:
    rep = row["representative"]
    return Party(
        id=row["id"],
        role_key=row["role_key"],
        name=(await crypto.decrypt(conn, case_id, bytes(row["name"]), purpose=_PARTY)).decode(),
        representative=None
        if rep is None
        else (await crypto.decrypt(conn, case_id, bytes(rep), purpose=_PARTY)).decode(),
    )


async def _seal(conn: DbConn, crypto: CaseCrypto, case_id: uuid.UUID, text: str | None) -> bytes | None:
    return None if text is None else await crypto.encrypt(conn, case_id, text.encode(), purpose=_PARTY)


async def _check_role(request: Request, conn: DbConn, case_id: uuid.UUID, role_key: str) -> None:
    pack = await _pack(request, conn, case_id)
    if role_key not in {p["key"] for p in pack.party_roles()}:
        raise invalid("role_key")


@router.get("/cases/{case_id}/parties", response_model=list[Party])
async def list_parties(case_id: uuid.UUID, access: ViewContent, conn: DbConn, crypto: Crypto) -> list[Party]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, role_key, name, representative from app.case_parties where case_id = %s"
            " order by position, created_at, id",
            (case_id,),
        )
        rows = await cur.fetchall()
    return [await _party(conn, crypto, case_id, r) for r in rows]


@router.post("/cases/{case_id}/parties", status_code=201, response_model=Party)
async def add_party(
    case_id: uuid.UUID,
    body: PartyIn,
    access: EditSetup,
    request: Request,
    conn: DbConn,
    crypto: Crypto,
    response: Response,
) -> Party:
    if await _served(conn, case_id):
        raise conflict("The parties are fixed once the case is served.")
    await _check_role(request, conn, case_id, body.role_key)
    party_id = uuid.uuid4()
    await conn.execute(
        "insert into app.case_parties (id, case_id, role_key, name, representative) values (%s, %s, %s, %s, %s)",
        (
            party_id,
            case_id,
            body.role_key,
            await _seal(conn, crypto, case_id, body.name),
            await _seal(conn, crypto, case_id, body.representative),
        ),
    )
    await audit.record(conn, action="case.party_added", target=f"case_party:{party_id}", case_id=case_id)
    response.headers["etag"] = etag(1)
    return Party(id=party_id, role_key=body.role_key, name=body.name, representative=body.representative)


@router.patch("/cases/{case_id}/parties/{party_id}", response_model=Party)
async def update_party(
    case_id: uuid.UUID,
    party_id: uuid.UUID,
    version: IfMatch,
    body: PartyUpdate,
    access: EditSetup,
    request: Request,
    conn: DbConn,
    crypto: Crypto,
    response: Response,
) -> Party:
    changes = body.model_dump(exclude_unset=True)
    if changes.get("role_key") is not None:
        await _check_role(request, conn, case_id, changes["role_key"])
    if "name" in changes and changes["name"] is None:
        raise invalid("name")
    values: dict[str, Any] = {}
    for field, value in changes.items():
        values[field] = await _seal(conn, crypto, case_id, value) if field != "role_key" else value
    sets = ", ".join(f"{k} = %({k})s" for k in values) or "version = version"
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"update app.case_parties set {sets} where id = %(id)s and case_id = %(case)s and version = %(v)s"  # noqa: S608
            " returning id, role_key, name, representative, version",
            {**values, "id": party_id, "case": case_id, "v": version},
        )
        row = await cur.fetchone()
    if row is None:
        cur2 = await conn.execute("select 1 from app.case_parties where id = %s and case_id = %s", (party_id, case_id))
        raise stale() if await cur2.fetchone() else not_found()
    await audit.record(conn, action="case.party_changed", target=f"case_party:{party_id}", case_id=case_id)
    response.headers["etag"] = etag(int(row.pop("version")))
    return await _party(conn, crypto, case_id, row)


@router.delete("/cases/{case_id}/parties/{party_id}", status_code=204)
async def remove_party(case_id: uuid.UUID, party_id: uuid.UUID, access: EditSetup, conn: DbConn) -> Response:
    cur = await conn.execute(
        "delete from app.case_parties where id = %s and case_id = %s returning id", (party_id, case_id)
    )
    if await cur.fetchone() is None:
        raise not_found()
    await audit.record(conn, action="case.party_removed", target=f"case_party:{party_id}", case_id=case_id)
    return Response(status_code=204)


# --- instructing parties ---------------------------------------------------------------------------------


class InstructingParty(BaseModel):
    id: uuid.UUID
    type_key: str
    name: str
    firm: str
    email: str | None
    reference: str | None
    effective_from: date
    effective_to: date | None
    replaces_id: uuid.UUID | None


class InstructingParties(BaseModel):
    joint: bool = Field(description="Two or more current instructing parties: the expert is a joint expert.")
    current: list[InstructingParty]
    history: list[InstructingParty]


class InstructingPartyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type_key: str = Field(pattern=KEY)
    name: str = Field(min_length=1, max_length=200)
    firm: str = Field(min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    reference: str | None = Field(default=None, max_length=100)
    effective_from: date | None = None


class InstructingPartyUpdate(BaseModel):
    """A change of `firm` ends this row the day before `effective_from` (default today) and starts a new one."""

    model_config = ConfigDict(extra="forbid")

    type_key: str | None = Field(default=None, pattern=KEY)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    firm: str | None = Field(default=None, min_length=1, max_length=200)
    email: str | None = Field(default=None, max_length=320)
    reference: str | None = Field(default=None, max_length=100)
    effective_from: date | None = None


_IP_COLUMNS = "id, type_key, name, firm, email, reference, effective_from, effective_to, replaces_id, version"


async def _check_type(request: Request, conn: DbConn, case_id: uuid.UUID, type_key: str) -> None:
    pack = await _pack(request, conn, case_id)
    if type_key not in {p["key"] for p in pack.instructing_party_types()}:
        raise invalid("type_key")


async def _insert_ip(conn: DbConn, case_id: uuid.UUID, values: dict[str, Any]) -> dict[str, Any]:
    ip_id = uuid.uuid4()
    columns = ["id", "case_id", *values]
    await conn.execute(
        f"insert into app.case_instructing_parties ({', '.join(columns)})"  # noqa: S608
        f" values ({', '.join(['%s'] * len(columns))})",
        (ip_id, case_id, *values.values()),
    )
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"select {_IP_COLUMNS} from app.case_instructing_parties where id = %s", (ip_id,))  # noqa: S608
        row = await cur.fetchone()
    assert row is not None
    return row


def _ip(row: dict[str, Any], response: Response) -> InstructingParty:
    response.headers["etag"] = etag(int(row.pop("version")))
    return InstructingParty(**row)


@router.get("/cases/{case_id}/instructing-parties", response_model=InstructingParties)
async def list_instructing_parties(case_id: uuid.UUID, access: ViewContent, conn: DbConn) -> InstructingParties:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_IP_COLUMNS} from app.case_instructing_parties where case_id = %s"  # noqa: S608
            " order by effective_from, created_at, id",
            (case_id,),
        )
        rows = await cur.fetchall()
    for r in rows:
        r.pop("version")
    current = [InstructingParty(**r) for r in rows if r["effective_to"] is None]
    history = [InstructingParty(**r) for r in rows if r["effective_to"] is not None]
    return InstructingParties(joint=len(current) >= 2, current=current, history=history)


@router.post("/cases/{case_id}/instructing-parties", status_code=201, response_model=InstructingParty)
async def add_instructing_party(
    case_id: uuid.UUID,
    body: InstructingPartyIn,
    access: EditSetup,
    request: Request,
    conn: DbConn,
    response: Response,
) -> InstructingParty:
    await _check_type(request, conn, case_id, body.type_key)
    values = body.model_dump(exclude_none=True)
    row = await _insert_ip(conn, case_id, values)
    await audit.record(
        conn, action="case.instructing_party_added", target=f"instructing_party:{row['id']}", case_id=case_id
    )
    return _ip(row, response)


@router.patch("/cases/{case_id}/instructing-parties/{ip_id}", response_model=InstructingParty)
async def update_instructing_party(
    case_id: uuid.UUID,
    ip_id: uuid.UUID,
    version: IfMatch,
    body: InstructingPartyUpdate,
    access: EditSetup,
    request: Request,
    conn: DbConn,
    response: Response,
) -> InstructingParty:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            f"select {_IP_COLUMNS} from app.case_instructing_parties where id = %s and case_id = %s",  # noqa: S608
            (ip_id, case_id),
        )
        old = await cur.fetchone()
    if old is None:
        raise not_found()
    if old["effective_to"] is not None:
        raise conflict("This instruction has ended; change the current one.")
    if old["version"] != version:
        raise stale()
    changes = body.model_dump(exclude_unset=True)
    for field in ("type_key", "name", "firm"):
        if field in changes and changes[field] is None:
            raise invalid(field)
    if changes.get("type_key") is not None:
        await _check_type(request, conn, case_id, changes["type_key"])
    if "firm" in changes and changes["firm"] != old["firm"]:
        starts = changes.get("effective_from") or date.today()
        if starts <= old["effective_from"]:
            raise invalid("effective_from")
        await conn.execute(
            "update app.case_instructing_parties set effective_to = %s where id = %s",
            (starts - timedelta(days=1), ip_id),
        )
        keep = ("type_key", "name", "firm", "email", "reference")
        values = {k: changes.get(k, old[k]) for k in keep}
        row = await _insert_ip(conn, case_id, {**values, "effective_from": starts, "replaces_id": ip_id})
        action = "case.instructing_party_replaced"
    else:
        sets = ", ".join(f"{k} = %({k})s" for k in changes) or "version = version"
        async with conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"update app.case_instructing_parties set {sets} where id = %(id)s"  # noqa: S608
                f" returning {_IP_COLUMNS}",
                {**changes, "id": ip_id},
            )
            found = await cur.fetchone()
        assert found is not None
        row = found
        action = "case.instructing_party_changed"
    await audit.record(conn, action=action, target=f"instructing_party:{row['id']}", case_id=case_id)
    return _ip(row, response)


# --- instructions --------------------------------------------------------------------------------------


class Instruction(BaseModel):
    id: uuid.UUID
    version_no: int
    kind: str
    document_id: uuid.UUID | None
    received_on: date | None
    locked: bool
    text: str | None = Field(default=None, description="Oral instructions only: the words, as saved.")
    source: str | None = Field(default=None, description="Oral instructions only: typed or dictated.")


class InstructionCreated(Instruction):
    reopens_questions: bool = Field(description="The previous version was locked; its questions reopen.")
    job_id: uuid.UUID | None = Field(default=None, description="The job reading the letter for questions.")


class InstructionIn(BaseModel):
    """A letter of instruction or a supplementary letter. Oral instructions use PUT oral-instructions."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["letter", "supplementary"]
    document_id: uuid.UUID | None = None
    received_on: date | None = None


class OralInstructionIn(BaseModel):
    """Exactly one of `text` (typed) or `transcript_id` (a saved dictation)."""

    model_config = ConfigDict(extra="forbid")

    received_on: date
    text: str | None = Field(default=None, min_length=1, max_length=20000)
    transcript_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _one_source(self) -> OralInstructionIn:
        if (self.text is None) == (self.transcript_id is None):
            raise ValueError("send the typed text or a transcript, not both")
        return self


class OralInstruction(BaseModel):
    instruction_version_id: uuid.UUID
    received_on: date
    text: str
    source: str


_ORAL = ORAL_INSTRUCTION_PURPOSE


async def _next_version(conn: DbConn, case_id: uuid.UUID, kind: str) -> tuple[int, bool]:
    """The next version number, and whether this version reopens the questions: a letter (any kind but oral,
    with or without its file) after a locked letter. Oral instructions carry no questions and reopen nothing."""
    cur = await conn.execute(
        "select coalesce(max(version_no), 0) + 1, (select locked_at is not null from app.instruction_versions"
        " where case_id = %s and kind <> 'oral' order by version_no desc limit 1)"
        " from app.instruction_versions where case_id = %s",
        (case_id, case_id),
    )
    row = await cur.fetchone() or (1, None)
    return int(row[0]), kind != "oral" and bool(row[1])


async def _new_version(
    conn: DbConn, case_id: uuid.UUID, kind: str, document_id: uuid.UUID | None, received_on: date | None
) -> tuple[uuid.UUID, int, bool]:
    number, reopens = await _next_version(conn, case_id, kind)
    version_id = uuid.uuid4()
    await conn.execute(
        "insert into app.instruction_versions (id, case_id, version_no, kind, document_id, received_on)"
        " values (%s, %s, %s, %s, %s, %s)",
        (version_id, case_id, number, kind, document_id, received_on),
    )
    await audit.record(
        conn, action="case.instruction_added", target=f"instruction_version:{version_id}", case_id=case_id
    )
    return version_id, number, reopens


@router.get("/cases/{case_id}/instructions", response_model=list[Instruction])
async def list_instructions(
    case_id: uuid.UUID, access: ViewContent, conn: DbConn, request: Request
) -> list[Instruction]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select v.id, v.version_no, v.kind, v.document_id, coalesce(i.received_on, v.received_on) as received_on,"
            " v.locked_at is not null as locked, i.text, i.source from app.instruction_versions v"
            " left join app.case_instructions i on i.instruction_version_id = v.id"
            " where v.case_id = %s order by v.version_no",
            (case_id,),
        )
        rows = await cur.fetchall()
    crypto: CaseCrypto | None = request.app.state.crypto
    out = []
    for r in rows:
        if r["text"] is not None:
            if crypto is None:
                raise ApiError("unavailable", "Saving is not ready.", 503)
            r["text"] = (await crypto.decrypt(conn, case_id, bytes(r["text"]), purpose=_ORAL)).decode()
        out.append(Instruction(**r))
    return out


@router.post("/cases/{case_id}/instructions", status_code=201, response_model=InstructionCreated)
async def add_instruction(case_id: uuid.UUID, body: InstructionIn, access: Upload, conn: DbConn) -> InstructionCreated:
    if body.document_id is not None:
        cur = await conn.execute(
            "select 1 from app.documents where id = %s and case_id = %s", (body.document_id, case_id)
        )
        if await cur.fetchone() is None:
            raise invalid("document_id")
    version_id, number, reopens = await _new_version(conn, case_id, body.kind, body.document_id, body.received_on)
    job_id = None
    if body.document_id is not None:
        job_id = await registry.enqueue(conn, case_id, "extract_questions")
    return InstructionCreated(
        id=version_id,
        version_no=number,
        kind=body.kind,
        document_id=body.document_id,
        received_on=body.received_on,
        locked=False,
        reopens_questions=reopens,
        job_id=job_id,
    )


@router.put("/cases/{case_id}/oral-instructions", response_model=OralInstruction)
async def save_oral_instructions(
    case_id: uuid.UUID, body: OralInstructionIn, access: Upload, conn: DbConn, crypto: Crypto
) -> OralInstruction:
    """One record per date received: saving again for the same date replaces the words."""
    if body.transcript_id is not None:
        cur = await conn.execute(
            "select text from app.transcripts where id = %s and case_id = %s", (body.transcript_id, case_id)
        )
        found = await cur.fetchone()
        if found is None:
            raise invalid("transcript_id")
        text, source = (await crypto.decrypt(conn, case_id, bytes(found[0]))).decode(), "dictated"
    else:
        assert body.text is not None
        text, source = body.text, "typed"
    sealed = await crypto.encrypt(conn, case_id, text.encode(), purpose=_ORAL)
    cur = await conn.execute(
        "select i.instruction_version_id, v.locked_at is not null from app.case_instructions i"
        " join app.instruction_versions v on v.id = i.instruction_version_id"
        " where i.case_id = %s and i.received_on = %s",
        (case_id, body.received_on),
    )
    existing = await cur.fetchone()
    if existing is not None:
        if existing[1]:
            raise conflict("These instructions are locked; add new ones instead.")
        version_id = existing[0]
        await conn.execute(
            "update app.case_instructions set text = %s, source = %s, transcript_id = %s"
            " where instruction_version_id = %s",
            (sealed, source, body.transcript_id, version_id),
        )
        await audit.record(
            conn, action="case.instruction_changed", target=f"instruction_version:{version_id}", case_id=case_id
        )
    else:
        version_id, _, _ = await _new_version(conn, case_id, "oral", None, body.received_on)
        await conn.execute(
            "insert into app.case_instructions (case_id, instruction_version_id, text, source, transcript_id,"
            " received_on) values (%s, %s, %s, %s, %s, %s)",
            (case_id, version_id, sealed, source, body.transcript_id, body.received_on),
        )
    return OralInstruction(instruction_version_id=version_id, received_on=body.received_on, text=text, source=source)


# --- links ---------------------------------------------------------------------------------------------


class CaseLink(BaseModel):
    id: uuid.UUID
    linked_case_id: uuid.UUID
    kind: str
    label: str | None = Field(description="The linked case's name, when you can see that case.")
    created_at: datetime


class CaseLinkIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    linked_case_id: uuid.UUID
    kind: LinkKind


_LINK = (
    "select l.id, l.linked_case_id, l.kind, c.label, l.created_at from app.case_links l"
    " left join app.cases c on c.id = l.linked_case_id"
)


@router.get("/cases/{case_id}/links", response_model=list[CaseLink])
async def list_links(case_id: uuid.UUID, access: ViewMetadata, conn: DbConn) -> list[CaseLink]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(f"{_LINK} where l.case_id = %s order by l.created_at, l.id", (case_id,))
        return [CaseLink(**r) for r in await cur.fetchall()]


@router.post("/cases/{case_id}/links", status_code=201, response_model=CaseLink)
async def add_link(case_id: uuid.UUID, body: CaseLinkIn, access: EditSetup, conn: DbConn) -> CaseLink:
    if body.linked_case_id == case_id:
        raise invalid("linked_case_id")
    await check_case_access(conn, body.linked_case_id, "case.view_metadata")
    cur = await conn.execute(
        "select 1 from app.case_links where case_id = %s and linked_case_id = %s and kind = %s",
        (case_id, body.linked_case_id, body.kind),
    )
    if await cur.fetchone():
        raise conflict("These cases are already linked.")
    link_id = uuid.uuid4()
    await conn.execute(
        "insert into app.case_links (id, case_id, linked_case_id, kind) values (%s, %s, %s, %s)",
        (link_id, case_id, body.linked_case_id, body.kind),
    )
    await audit.record(conn, action="case.link_added", target=f"case_link:{link_id}", case_id=case_id)
    async with conn.cursor(row_factory=dict_row) as dcur:
        await dcur.execute(f"{_LINK} where l.id = %s", (link_id,))
        row = await dcur.fetchone()
    assert row is not None
    return CaseLink(**row)


@router.delete("/cases/{case_id}/links/{link_id}", status_code=204)
async def remove_link(case_id: uuid.UUID, link_id: uuid.UUID, access: EditSetup, conn: DbConn) -> Response:
    cur = await conn.execute(
        "delete from app.case_links where id = %s and case_id = %s returning id", (link_id, case_id)
    )
    if await cur.fetchone() is None:
        raise not_found()
    await audit.record(conn, action="case.link_removed", target=f"case_link:{link_id}", case_id=case_id)
    return Response(status_code=204)


# --- subject key and the duplicate-case check ------------------------------------------------------------


class SubjectKeyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(min_length=1, max_length=100)


class DuplicateCheckIn(BaseModel):
    """The identifier is hashed and compared, never stored or logged."""

    model_config = ConfigDict(extra="forbid")

    jurisdiction: str = Field(max_length=100)
    identifier: str = Field(min_length=1, max_length=100)
    instructing_firm: str | None = Field(default=None, max_length=200)
    case_id: uuid.UUID | None = Field(default=None, description="The case being set up, left out of the matches.")


class DuplicateMatch(BaseModel):
    case_id: uuid.UUID
    label: str
    same_instructing_party: bool | None = Field(
        description="Whether a current instructing party there has the firm you gave; null if you gave none."
    )


class DuplicateCheck(BaseModel):
    matches: list[DuplicateMatch]


def _hash(pack: MergedPack, crypto: CaseCrypto, identifier: str) -> str:
    normalised = pack.normalise_identifier(identifier)
    if normalised is None:
        raise invalid("identifier")
    return subject_key_hash(normalised, provider=crypto.provider)


@router.put("/cases/{case_id}/subject-key", status_code=204)
async def save_subject_key(
    case_id: uuid.UUID,
    body: SubjectKeyIn,
    access: EditSetup,
    request: Request,
    conn: DbConn,
    crypto: Crypto,
) -> Response:
    key = _hash(await _pack(request, conn, case_id), crypto, body.identifier)
    await conn.execute(
        "insert into app.case_subject_keys (case_id, key_hash) values (%s, %s)"
        " on conflict (case_id) do update set key_hash = excluded.key_hash",
        (case_id, key),
    )
    await audit.record(conn, action="case.subject_key_saved", target=f"case:{case_id}", case_id=case_id)
    return Response(status_code=204)


@router.post("/cases/duplicate-check", response_model=DuplicateCheck)
async def duplicate_check(body: DuplicateCheckIn, request: Request, conn: DbConn, crypto: Crypto) -> DuplicateCheck:
    """Cases you can see whose subject has the same identifier, in the same jurisdiction."""
    combos = [c for c in library(request).combinations_in(cell(request)) if c[0].code == body.jurisdiction]
    if not combos:
        raise invalid("jurisdiction")
    j, ct, sp = combos[0]
    key = _hash(library(request).combine(j.code, ct.code, sp.code), crypto, body.identifier)
    firm = body.instructing_firm.strip().lower() if body.instructing_firm else None
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select c.id as case_id, c.label, case when %(firm)s::text is null then null else exists ("
            " select 1 from app.case_instructing_parties p where p.case_id = c.id and p.effective_to is null"
            " and lower(btrim(p.firm)) = %(firm)s) end as same_instructing_party"
            " from app.case_subject_keys k join app.cases c on c.id = k.case_id"
            " where k.key_hash = %(key)s and c.jurisdiction = %(j)s and c.status <> 'deleted'"
            " and (%(case)s::uuid is null or c.id <> %(case)s::uuid)"
            " order by c.created_at, c.id",
            {"firm": firm, "key": key, "j": body.jurisdiction, "case": body.case_id},
        )
        rows = await cur.fetchall()
    return DuplicateCheck(matches=[DuplicateMatch(**r) for r in rows])


# --- jurisdiction --------------------------------------------------------------------------------------


class JurisdictionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jurisdiction: str = Field(max_length=100)
    case_type: str = Field(max_length=100)
    specialty: str = Field(max_length=100)


class PackChange(BaseModel):
    what: Literal["pack", "version", "word", "declaration", "field", "held", "dismissed"] = Field(
        description="`held`: a key kept on another table that the new packs lack, for the expert to change;"
        " `dismissed`: waiting suggestions from the letter, checked against the old packs' words."
    )
    key: str
    before: str | None
    after: str | None


class JurisdictionChanged(BaseModel):
    pack_versions: dict[str, str]
    changes: list[PackChange]


def _words(pack: MergedPack) -> dict[str, str]:
    return {k: v for k, v in pack.vocabulary.items() if isinstance(v, str)}


def _differences(old: MergedPack, new: MergedPack, codes: tuple[tuple[str, str, str], ...]) -> list[PackChange]:
    changes = [PackChange(what="pack", key=k, before=b, after=a) for k, b, a in codes if b != a]
    for layer in sorted(set(old.versions) | set(new.versions)):
        before, after = old.versions.get(layer), new.versions.get(layer)
        if before != after:
            changes.append(PackChange(what="version", key=layer, before=before, after=after))
    old_words, new_words = _words(old), _words(new)
    for key in sorted(set(old_words) | set(new_words)):
        if old_words.get(key) != new_words.get(key):
            changes.append(PackChange(what="word", key=key, before=old_words.get(key), after=new_words.get(key)))
    old_decl = {d["key"]: d["text"] for d in old.declarations()}
    new_decl = {d["key"]: d["text"] for d in new.declarations()}
    for key in sorted(set(old_decl) | set(new_decl)):
        if old_decl.get(key) != new_decl.get(key):
            changes.append(PackChange(what="declaration", key=key, before=old_decl.get(key), after=new_decl.get(key)))
    return changes


# Keys stored outside the case row, each with the new packs' keys it must be one of.
_KEY_FIELDS = ["party_role", "instructing_party_type", "court_track"]  # prefill fields holding a pack key


async def _held_keys(conn: DbConn, case_id: uuid.UUID, new: MergedPack) -> list[PackChange]:
    """Keys the new packs lack are kept (refusing would leave the expert no way forward, since every key is
    checked against the current packs) and listed for the expert; waiting key suggestions are dismissed."""
    checks = (
        (
            "case_parties.role_key",
            "select distinct role_key from app.case_parties where case_id = %s",
            {p["key"] for p in new.party_roles()},
        ),
        (
            "case_instructing_parties.type_key",
            "select distinct type_key from app.case_instructing_parties where case_id = %s and effective_to is null",
            {p["key"] for p in new.instructing_party_types()},
        ),
        (
            "questions.kind",
            "select distinct kind from app.questions where case_id = %s",
            {str(q["key"]) for q in new.question_types()},
        ),
        (
            "conflict_answers.question_key",
            "select distinct question_key from app.conflict_answers where case_id = %s",
            {str(q["key"]) for q in new.conflicts()["questions"]},
        ),
    )
    out: list[PackChange] = []
    for key, sql, allowed in checks:
        cur = await conn.execute(sql, (case_id,))
        for value in sorted(str(r[0]) for r in await cur.fetchall()):
            if value not in allowed:
                out.append(PackChange(what="held", key=key, before=value, after=None))
    cur = await conn.execute(
        "update app.prefill_suggestions set status = 'dismissed' where case_id = %s and status = 'suggested'"
        " and field = any(%s) returning field",
        (case_id, _KEY_FIELDS),
    )
    for field in sorted({str(r[0]) for r in await cur.fetchall()}):
        out.append(PackChange(what="dismissed", key=f"prefill_suggestions.{field}", before=None, after=None))
    return out


@router.patch("/cases/{case_id}/jurisdiction", response_model=JurisdictionChanged)
async def change_jurisdiction(
    case_id: uuid.UUID,
    version: IfMatch,
    body: JurisdictionIn,
    access: ChangeJurisdiction,
    request: Request,
    conn: DbConn,
    response: Response,
) -> JurisdictionChanged:
    """Before the first served report only. The packs are stamped again and every difference is listed,
    including keys on other tables the new packs lack (kept, `held`) and dismissed waiting suggestions."""
    if await _served(conn, case_id):
        raise conflict("The jurisdiction is fixed once the case is served.")
    packs = library(request)
    wanted = (body.jurisdiction, body.case_type, body.specialty)
    offered = {(j.code, ct.code, sp.code) for j, ct, sp in packs.combinations_in(cell(request))}
    if wanted not in offered:
        raise invalid("jurisdiction")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select jurisdiction, case_type, specialty, expert_role, party_role, instructing_party_type,"
            " court_track, subject_status from app.cases where id = %s",
            (case_id,),
        )
        current = await cur.fetchone()
    assert current is not None  # require_case_access has already answered 404
    old = packs.combine(current["jurisdiction"], current["case_type"], current["specialty"])
    new = packs.combine(*wanted)
    names = ("jurisdiction", "case_type", "specialty")
    changes = _differences(old, new, tuple((n, current[n], w) for n, w in zip(names, wanted, strict=True)))
    offered_values: dict[str, list[str]] = {
        "party_role": [p["key"] for p in new.party_roles()],
        "instructing_party_type": [p["key"] for p in new.instructing_party_types()],
        "court_track": list(packs.jurisdiction_summary(body.jurisdiction)["court_tracks"]),
        "subject_status": [str(s["key"]) for s in new.subject_statuses()],
    }
    values: dict[str, Any] = dict(zip(names, wanted, strict=True))
    roles = new.expert_roles()
    if current["expert_role"] not in roles:
        values["expert_role"] = roles[0]
        changes.append(PackChange(what="field", key="expert_role", before=current["expert_role"], after=roles[0]))
    for field, allowed in offered_values.items():
        if current[field] is not None and current[field] not in allowed:
            values[field] = None
            changes.append(PackChange(what="field", key=field, before=current[field], after=None))
    if wanted != tuple(current[n] for n in names):
        changes += await _held_keys(conn, case_id, new)
    sets = ", ".join(f"{k} = %({k})s" for k in values)
    cur2 = await conn.execute(
        f"update app.cases set {sets}, pack_versions = %(pv)s where id = %(id)s and version = %(v)s"  # noqa: S608
        " returning version",
        {**values, "pv": Jsonb(new.versions), "id": case_id, "v": version},
    )
    row = await cur2.fetchone()
    if row is None:
        raise stale()
    await audit.record(conn, action="case.jurisdiction_changed", target=f"case:{case_id}", case_id=case_id)
    response.headers["etag"] = etag(int(row[0]))
    return JurisdictionChanged(pack_versions=new.versions, changes=changes)


# --- hold and resume -----------------------------------------------------------------------------------


class Hold(BaseModel):
    on_hold: bool


async def _hold(conn: DbConn, case_id: uuid.UUID, on_hold: bool, response: Response) -> Hold:
    cur = await conn.execute(
        "update app.cases set on_hold = %s where id = %s returning on_hold, version", (on_hold, case_id)
    )
    row = await cur.fetchone()
    assert row is not None  # require_case_access has already answered 404
    action = "case.held" if on_hold else "case.resumed"
    await audit.record(conn, action=action, target=f"case:{case_id}", case_id=case_id)
    response.headers["etag"] = etag(int(row[1]))
    return Hold(on_hold=bool(row[0]))


@router.post("/cases/{case_id}/hold", response_model=Hold)
async def hold_case(case_id: uuid.UUID, access: HoldAccess, conn: DbConn, response: Response) -> Hold:
    return await _hold(conn, case_id, True, response)


@router.post("/cases/{case_id}/resume", response_model=Hold)
async def resume_case(
    case_id: uuid.UUID, access: HoldAccess, conn: DbConn, request: Request, response: Response
) -> Hold:
    """Takes the case off hold and releases what the hold kept back (jobs and messages)."""
    resumed = await _hold(conn, case_id, False, response)
    await release_case_hold(conn, request.app.state.mailer, case_id)
    return resumed


# --- the subject's words -------------------------------------------------------------------------------


class Subject(BaseModel):
    status: str | None
    label: str
    representative: str | None


@router.get("/cases/{case_id}/subject", response_model=Subject)
async def get_subject(case_id: uuid.UUID, access: ViewMetadata, request: Request, conn: DbConn) -> Subject:
    """The words for the subject in their current status. A status change changes only these words."""
    cur = await conn.execute("select subject_status from app.cases where id = %s", (case_id,))
    row = await cur.fetchone()
    assert row is not None
    pack = await _pack(request, conn, case_id)
    for status in pack.subject_statuses():
        if status["key"] == row[0]:
            return Subject(status=row[0], label=str(status["label"]), representative=status["representative"])
    return Subject(status=None, label=pack.term("subject"), representative=None)
