"""Screen 16: build the report, what was built, the citation style and required contents, the preview pages
and the methodology appendix.

`POST .../report/build` queues `build_report` (202). `GET .../report` gives the newest version with how it
was built, every version newest first, the sections in the order the report uses (the expert's template)
and the jurisdiction pack's required contents with the ones the expert has ticked. Settings carry a
version: no settings row is version 1 (the defaults). Changing the citation style rebuilds (202 with the
job); ticking required contents is the named expert's own judgement (`opinion.write`).
Once the main report is served (or withdrawn) build, final check and sign return 409 `served_use_supplementary`
(rule 8): corrections go through the supplementary report (screen 21b).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import Query, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.contracts import opinions, profile, questions
from app.core.errors import ApiError
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.report.assemble import (
    GENERATED,
    OPINION,
    PARAGRAPH_PURPOSE,
    methodology,
    section_keys,
    template_order,
)
from app.features.report.common import (
    Build,
    Crypto,
    Pack,
    Queued,
    Reads,
    Storage,
    View,
    invalid,
    not_found,
    refuse_if_served,
)
from app.features.report.pdf_out import page_png
from app.features.report.router import router

CitationStyle = Literal["page", "paragraph"]


class VersionOut(BaseModel):
    id: uuid.UUID
    version_no: int
    type: str
    display_name: str
    status: str
    created_at: datetime
    citation_style: str | None = None
    template_fallback: bool | None = None
    page_count: int | None = None


class SectionOut(BaseModel):
    key: str
    generated: bool
    written: bool
    done: int | None = None
    total: int | None = None


class ChecklistItem(BaseModel):
    key: str
    text: str | None = None
    text_key: str | None = None
    done: bool


class Checklist(BaseModel):
    rule_ref: str
    items: list[ChecklistItem]


class ReportOut(BaseModel):
    version: VersionOut | None
    versions: list[VersionOut]
    sections: list[SectionOut]
    checklist: Checklist


class SettingsOut(BaseModel):
    citation_style: CitationStyle
    addressed: list[str]
    version: int
    job_id: uuid.UUID | None = None


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_style: CitationStyle | None = None
    addressed: list[str] | None = Field(default=None, max_length=100)


class TaskOut(BaseModel):
    task_id: str
    model_id: str
    region: str
    prompt_version: str
    calls: int
    first_at: datetime
    last_at: datetime


class MethodologyOut(BaseModel):
    pages_read: int
    pages_total: int
    tasks: list[TaskOut]


_VERSIONS = (
    "select v.id, v.version_no, v.type, v.display_name, v.status, v.created_at, b.citation_style,"
    " b.template_fallback, b.page_count from app.report_versions v"
    " left join app.report_builds b on b.report_version_id = v.id where v.case_id = %s order by v.version_no desc"
)


async def _settings(conn: DbConn, case_id: uuid.UUID, *, lock: bool = False) -> tuple[str, list[str], int, bool]:
    cur = await conn.execute(
        "select citation_style, addressed_contents, version from app.report_settings where case_id = %s"
        + (" for update" if lock else ""),
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return "page", [], 1, False
    return str(row[0]), list(row[1]), int(row[2]), True


@router.post("/cases/{case_id}/report/build", status_code=202, response_model=Queued)
async def build_report(case_id: uuid.UUID, access: Build, conn: DbConn) -> Queued:
    await refuse_if_served(conn, case_id)
    return Queued(job_id=await registry.enqueue(conn, case_id, "build_report"))


@router.get("/cases/{case_id}/report", response_model=ReportOut)
async def report(case_id: uuid.UUID, access: View, conn: DbConn, pack: Pack, crypto: Crypto) -> ReportOut:
    cur = await conn.execute(_VERSIONS, (case_id,))
    versions = [
        VersionOut(
            id=r[0],
            version_no=r[1],
            type=r[2],
            display_name=r[3],
            status=r[4],
            created_at=r[5],
            citation_style=r[6],
            template_fallback=r[7],
            page_count=r[8],
        )
        for r in await cur.fetchall()
    ]
    cur = await conn.execute("select section from app.report_section_texts where case_id = %s", (case_id,))
    written = {r[0] for r in await cur.fetchall()}
    template = await profile.report_template(conn, case_id)
    order = template_order(section_keys(pack), template)
    _, addressed, _, _ = await _settings(conn, case_id)
    asked = {q.id for q in await questions.confirmed_questions(conn, case_id)}
    answered = len(asked & {o.question_id for o in await opinions.latest_opinions(conn, case_id, crypto)})
    checklist = pack.compliance_checklist()
    return ReportOut(
        version=versions[0] if versions else None,
        versions=versions,
        sections=[
            SectionOut(
                key=k,
                generated=k in GENERATED,
                written=k in written,
                done=answered if k == OPINION else None,
                total=len(asked) if k == OPINION else None,
            )
            for k in order
        ],
        checklist=Checklist(
            rule_ref=str(checklist["rule_ref"]),
            items=[
                ChecklistItem(key=i["key"], text=i.get("text"), text_key=i.get("text_key"), done=i["key"] in addressed)
                for i in checklist["items"]
            ],
        ),
    )


@router.get("/cases/{case_id}/report/settings", response_model=SettingsOut)
async def settings(case_id: uuid.UUID, access: View, conn: DbConn, response: Response) -> SettingsOut:
    style, addressed, version, _ = await _settings(conn, case_id)
    response.headers["ETag"] = etag(version)
    return SettingsOut(citation_style=style, addressed=addressed, version=version)  # type: ignore[arg-type]


@router.patch("/cases/{case_id}/report/settings", response_model=SettingsOut)
async def change_settings(
    case_id: uuid.UUID,
    change: SettingsIn,
    if_match: IfMatch,
    access: Build,
    conn: DbConn,
    pack: Pack,
    response: Response,
) -> SettingsOut:
    style, addressed, version, exists = await _settings(conn, case_id, lock=True)
    if if_match != version:
        raise stale()
    if change.addressed is not None:
        if "opinion.write" not in access.allowed_actions:
            raise ApiError("forbidden", "You cannot do that on this case.", 403)
        known = {i["key"] for i in pack.compliance_checklist()["items"]}
        if not set(change.addressed) <= known:
            raise invalid("addressed")
        addressed = [k for k in dict.fromkeys(change.addressed)]
    rebuild = change.citation_style is not None and change.citation_style != style
    style = change.citation_style or style
    if exists:
        cur = await conn.execute(
            "update app.report_settings set citation_style = %s, addressed_contents = %s, version = version + 1,"
            " updated_at = now() where case_id = %s returning version",
            (style, addressed, case_id),
        )
    else:
        cur = await conn.execute(
            "insert into app.report_settings (case_id, citation_style, addressed_contents, version)"
            " values (%s, %s, %s, 2) returning version",
            (case_id, style, addressed),
        )
    row = await cur.fetchone()
    assert row is not None
    out = SettingsOut(citation_style=style, addressed=addressed, version=int(row[0]))  # type: ignore[arg-type]
    if rebuild:
        out.job_id = await registry.enqueue(conn, case_id, "build_report")
        response.status_code = 202
    response.headers["ETag"] = etag(out.version)
    return out


@router.get(
    "/cases/{case_id}/report/preview/{page}",
    response_class=Response,
    responses={200: {"content": {"image/png": {}}}},
)
async def preview(
    case_id: uuid.UUID, page: int, access: View, conn: DbConn, crypto: Crypto, storage: Storage
) -> Response:
    cur = await conn.execute(
        "select pdf_path from app.report_versions where case_id = %s and pdf_path is not null"
        " order by version_no desc limit 1",
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    pdf = await crypto.decrypt(conn, case_id, await storage.get(row[0]), purpose=row[0])
    image = page_png(pdf, page)
    if image is None:
        raise not_found()
    return Response(image, media_type="image/png", headers={"Cache-Control": "private, no-store"})


class ParagraphOut(BaseModel):
    para_no: int
    section: str
    text: str
    question_id: uuid.UUID | None = None


class ParagraphsOut(BaseModel):
    version_id: uuid.UUID
    paragraphs: list[ParagraphOut]


@router.get("/cases/{case_id}/report/paragraphs", response_model=ParagraphsOut)
async def paragraphs(
    case_id: uuid.UUID,
    access: View,
    conn: DbConn,
    crypto: Crypto,
    section: Annotated[str | None, Query(pattern=r"^[a-z][a-z0-9_]{0,39}$")] = None,
) -> ParagraphsOut:
    """The latest built report's words as plain text, read-only (screen 16). Nothing is generated here."""
    cur = await conn.execute(
        "select id from app.report_versions where case_id = %s"
        " and exists (select 1 from app.report_paragraphs p where p.report_version_id = report_versions.id)"
        " order by version_no desc limit 1",
        (case_id,),
    )
    found = await cur.fetchone()
    if found is None:
        raise not_found()
    cur = await conn.execute(
        "select para_no, section, text, question_id from app.report_paragraphs where report_version_id = %s"
        " and (%s::text is null or section = %s) order by para_no",
        (found[0], section, section),
    )
    out = [
        ParagraphOut(
            para_no=n,
            section=sec,
            text=(await crypto.decrypt(conn, case_id, bytes(t), purpose=PARAGRAPH_PURPOSE)).decode(),
            question_id=q,
        )
        for n, sec, t, q in await cur.fetchall()
    ]
    return ParagraphsOut(version_id=found[0], paragraphs=out)


@router.get("/cases/{case_id}/report/methodology", response_model=MethodologyOut)
async def methodology_appendix(case_id: uuid.UUID, access: View, conn: DbConn, reads: Reads) -> MethodologyOut:
    found = await methodology(conn, case_id, reads)
    return MethodologyOut(
        pages_read=found.pages_read,
        pages_total=found.pages_total,
        tasks=[
            TaskOut(
                task_id=t.task_id,
                model_id=t.model_id,
                region=t.region,
                prompt_version=t.prompt_version,
                calls=t.calls,
                first_at=t.first_at,
                last_at=t.last_at,
            )
            for t in found.tasks
        ],
    )


@router.post("/cases/{case_id}/report/word-import", status_code=501)
async def word_import(case_id: uuid.UUID, access: Build) -> None:
    raise ApiError("not_built_yet", "Bringing changes back from Word is not ready yet.", 501)
