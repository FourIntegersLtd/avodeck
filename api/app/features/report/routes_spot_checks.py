"""Screen 17: a colleague's spot-check of the report's cited facts (`spot_checks.py`).

- `POST /cases/{id}/spot-checks` (`report.build`): picks the facts, draws and seals the crops, emails the
  colleague a 6-digit code (`spot_check.code`, the code only) and returns the link token once; 409
  `nothing_cited` until a report cites a fact.
- `GET /cases/{id}/spot-checks` (`report.build`): each check with the colleague's answers.

The colleague's routes (`GET /v1/spot-checks/{token}`, `POST /v1/spot-checks/{token}/responses`) are on
foundation's public router in `routes_colleague.py`.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Any

from fastapi import BackgroundTasks
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.email import spot_check_code_mail
from app.core.errors import ApiError
from app.core.http import DbConn
from app.features.report.common import Build, Crypto, Pack, Storage
from app.features.report.router import router
from app.features.report.services import current
from app.features.report.spot_checks import LIFETIME, code_hash, new_code, prepare, sample, token_hash


class SpotCheckIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewer_email: str = Field(pattern=r"^[^@\s]+@[^@\s]+$", max_length=254)


class SpotCheckStarted(BaseModel):
    id: uuid.UUID
    token: str
    expires_at: datetime
    fact_count: int


class SpotCheckAnswer(BaseModel):
    fact_id: uuid.UUID
    agrees: bool
    reason: str | None = None


class SpotCheckOut(BaseModel):
    id: uuid.UUID
    report_version_id: uuid.UUID
    reviewer_email: str
    fact_count: int
    created_at: datetime
    expires_at: datetime
    opened_at: datetime | None
    responded_at: datetime | None
    locked: bool
    responses: list[SpotCheckAnswer]


class SpotCheckList(BaseModel):
    spot_checks: list[SpotCheckOut]


@router.post("/cases/{case_id}/spot-checks", status_code=201, response_model=SpotCheckStarted)
async def start_spot_check(
    case_id: uuid.UUID,
    body: SpotCheckIn,
    access: Build,
    conn: DbConn,
    crypto: Crypto,
    storage: Storage,
    pack: Pack,
    after_commit: BackgroundTasks,
) -> SpotCheckStarted:
    cur = await conn.execute(
        "select c.report_version_id from app.citations c join app.report_versions v on v.id = c.report_version_id"
        " where c.case_id = %s order by v.version_no desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise ApiError("nothing_cited", "The report does not cite any facts yet.", 409)
    version_id = row[0]
    cur = await conn.execute("select distinct fact_id from app.citations where report_version_id = %s", (version_id,))
    picked = sample(r[0] for r in await cur.fetchall())
    token, code = secrets.token_urlsafe(32), new_code()
    hashed = token_hash(token)
    cur = await conn.execute(
        "insert into app.spot_checks (case_id, report_version_id, reviewer_email, fact_ids, token_hash, code_hash,"
        " code_sent_at, expires_at) values (%s, %s, %s, %s, %s, %s, now(), now() + %s) returning id, expires_at",
        (case_id, version_id, body.reviewer_email, picked, hashed, code_hash(hashed, code), LIFETIME),
    )
    created = await cur.fetchone()
    assert created is not None
    check_id: uuid.UUID = created[0]
    await prepare(conn, crypto, storage, pack, case_id, check_id, version_id, picked, token)
    await audit.record(conn, action="spot_check.created", target=f"spot_check:{check_id}", case_id=case_id)
    after_commit.add_task(current().mailer.send, spot_check_code_mail(body.reviewer_email, code=code))
    return SpotCheckStarted(id=check_id, token=token, expires_at=created[1], fact_count=len(picked))


@router.get("/cases/{case_id}/spot-checks", response_model=SpotCheckList)
async def list_spot_checks(case_id: uuid.UUID, access: Build, conn: DbConn) -> SpotCheckList:
    cur = await conn.execute(
        "select id, report_version_id, reviewer_email, cardinality(fact_ids), created_at, expires_at, verified_at,"
        " responded_at, code_attempts >= 5, responses from app.spot_checks where case_id = %s"
        " order by created_at desc",
        (case_id,),
    )
    checks = []
    for r in await cur.fetchall():
        answers: list[Any] = r[9] or []
        checks.append(
            SpotCheckOut(
                id=r[0],
                report_version_id=r[1],
                reviewer_email=r[2],
                fact_count=r[3],
                created_at=r[4],
                expires_at=r[5],
                opened_at=r[6],
                responded_at=r[7],
                locked=r[8],
                responses=[SpotCheckAnswer.model_validate(a) for a in answers],
            )
        )
    return SpotCheckList(spot_checks=checks)
