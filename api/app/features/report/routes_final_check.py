"""Screen 17: the final check before signing, and acknowledging corrected declaration wording (F34).

`POST .../report/final-check` queues `final_check` on the latest draft (202; 409 `not_built` without a
draft). `GET .../report/final-check/latest` gives the newest run: each check's outcome with its "Fix it"
targets, T-17's warnings with the flagged words read back from the sealed draft, whether the run is for the
newest version (`current`) and whether the pack's declaration wording changed since the expert last
acknowledged it. `POST .../report/declarations/acknowledge` records that the named expert has read the
current wording; only they may (`opinion.write`).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from app.core.contracts.reports import AFTER_SERVING_TYPES
from app.core.errors import ApiError
from app.core.http import DbConn
from app.core.jobs import registry
from app.features.report.assemble import PARAGRAPH_PURPOSE
from app.features.report.checks import declaration_version
from app.features.report.common import Build, Crypto, Pack, Queued, View, Write, not_found
from app.features.report.jobs import latest_draft
from app.features.report.router import router


class Fix(BaseModel):
    screen: str
    item: str


class CheckResult(BaseModel):
    key: str
    source: str
    outcome: str
    count: int
    fix: Fix | None = None
    fixes: list[Fix]
    text: str | None = None
    code: str | None = None
    explain: str | None = None
    checked: int | None = None
    total: int | None = None
    unit: str | None = None


class Warning(BaseModel):
    paragraph_id: uuid.UUID
    para_no: int
    section: str
    question_id: uuid.UUID | None = None
    type: str
    words: str


class FinalCheckOut(BaseModel):
    id: uuid.UUID
    report_version_id: uuid.UUID
    at: datetime
    passed: bool
    current: bool
    results: list[CheckResult]
    warnings: list[Warning]
    warnings_status: str
    pack_versions: dict[str, str]
    declaration_changed: bool
    failed: list[CheckResult] = []
    not_run: list[CheckResult] = []
    passed_checks: list[CheckResult] = []
    failed_count: int = 0
    ready: bool = False


class Acknowledged(BaseModel):
    acknowledged: str


@router.post("/cases/{case_id}/report/final-check", status_code=202, response_model=Queued)
async def run_final_check(case_id: uuid.UUID, access: Build, conn: DbConn) -> Queued:
    if await latest_draft(conn, case_id) is None:
        raise ApiError("not_built", "Build the report first.", 409)
    return Queued(job_id=await registry.enqueue(conn, case_id, "final_check"))


async def _acknowledged(conn: Any, case_id: uuid.UUID) -> str | None:
    cur = await conn.execute(
        "select acknowledged_declaration_version from app.report_settings where case_id = %s", (case_id,)
    )
    row = await cur.fetchone()
    return row[0] if row else None


@router.get("/cases/{case_id}/report/final-check/latest", response_model=FinalCheckOut)
async def latest_final_check(
    case_id: uuid.UUID, access: View, conn: DbConn, crypto: Crypto, pack: Pack
) -> FinalCheckOut:
    cur = await conn.execute(
        "select id, report_version_id, at, passed, results, warnings, warnings_status, pack_versions"
        " from app.final_check_runs where case_id = %s order by at desc, created_at desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    run_id, version_id, at, passed, results, warnings, status, versions = row
    cur = await conn.execute(
        "select id from app.report_versions where case_id = %s and type <> all(%s) order by version_no desc limit 1",
        (case_id, list(AFTER_SERVING_TYPES)),
    )
    newest = await cur.fetchone()
    shown: list[Warning] = []
    texts: dict[uuid.UUID, str] = {}
    for w in warnings:
        pid = uuid.UUID(w["paragraph_id"])
        if pid not in texts:
            cur = await conn.execute("select text from app.report_paragraphs where id = %s", (pid,))
            sealed = await cur.fetchone()
            texts[pid] = (
                (await crypto.decrypt(conn, case_id, bytes(sealed[0]), purpose=PARAGRAPH_PURPOSE)).decode()
                if sealed
                else ""
            )
        words = texts[pid][int(w["start"]) : int(w["end"])]
        if words:
            shown.append(Warning(**{k: v for k, v in w.items() if k not in ("start", "end")}, words=words))
    acknowledged = await _acknowledged(conn, case_id)
    checks = [CheckResult.model_validate(r) for r in results]
    current = newest is not None and newest[0] == version_id
    return FinalCheckOut(
        id=run_id,
        report_version_id=version_id,
        at=at,
        passed=passed,
        current=current,
        results=checks,
        failed=[c for c in checks if c.outcome == "failed"],
        not_run=[c for c in checks if c.outcome == "not_run"],
        passed_checks=[c for c in checks if c.outcome == "passed"],
        failed_count=sum(1 for c in checks if c.outcome == "failed"),
        ready=bool(passed) and current,
        warnings=shown,
        warnings_status=status,
        pack_versions=versions,
        declaration_changed=acknowledged is not None and acknowledged != declaration_version(pack),
    )


@router.post("/cases/{case_id}/report/declarations/acknowledge", response_model=Acknowledged)
async def acknowledge_declarations(case_id: uuid.UUID, access: Write, conn: DbConn, pack: Pack) -> Acknowledged:
    version = declaration_version(pack)
    await conn.execute(
        "insert into app.report_settings (case_id, acknowledged_declaration_version, version) values (%s, %s, 2)"
        " on conflict (case_id) do update set"
        " acknowledged_declaration_version = excluded.acknowledged_declaration_version,"
        " version = app.report_settings.version + 1, updated_at = now()",
        (case_id, version),
    )
    return Acknowledged(acknowledged=version)
