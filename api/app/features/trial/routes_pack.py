"""Screen 24, the trial pack (architecture 5.2, `build_trial_pack`, T-24).

- `GET/PUT /cases/{c}/trial-pack/settings` (`report.build`, If-Match; "0" for the first save): which
  sections go in, guidance or not, and the hearing.
- `POST /cases/{c}/trial-pack` (`report.build`): 202 and the build job; 409 `not_served` before serving.
- `GET /cases/{c}/trial-pack/latest` (`case.view_content`); `.../latest/download` is an export (step-up).
- `POST /cases/{c}/trial-pack/check-new` (`report.build`): which sentences of a new note come from a served
  document. T-24 returns references only; the expert decides.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core import audit
from app.core.ai.kit import InvalidOutput
from app.core.auth.step_up import require_step_up
from app.core.contracts.served import latest_served
from app.core.errors import ProcessingPaused
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.trial import ai_tasks, repo
from app.features.trial.common import (
    NEW_NOTE,
    NO_STORE,
    Build,
    Crypto,
    Pack,
    Storage,
    View,
    conflict,
    invalid,
    not_found,
    seal,
    sha256,
)
from app.features.trial.pack_document import DEFAULT_SECTIONS, SECTIONS, pack_document, sheets
from app.features.trial.pdfs import render_counted
from app.features.trial.ports import answers_of
from app.features.trial.router import router
from app.features.trial.services import current

Section = Literal["report", "chronology", "readiness", "joint_statement", "written_answers", "legal_tests", "guidance"]
_SENTENCE = re.compile(r"(?<=[.!?])\s+")
MAX_SENTENCES = 60


class TrialPackSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sections: list[Section] = Field(min_length=1, max_length=7)
    include_guidance: bool = False
    hearing_id: uuid.UUID | None = None


class TrialPackSettingsOut(BaseModel):
    sections: list[Section]
    include_guidance: bool
    hearing_id: uuid.UUID | None
    version: int


class SectionPages(BaseModel):
    key: Section
    pages: int


class PackOut(BaseModel):
    id: uuid.UUID
    status: Literal["ready"]
    sha256: str
    page_count: int
    sections: list[str]
    stale: bool
    stale_reason: str | None
    created_at: datetime
    section_pages: list[SectionPages] = []


class LatestOut(BaseModel):
    settings: TrialPackSettingsOut
    pack: PackOut | None


class BuildQueued(BaseModel):
    job_id: uuid.UUID


class SheetPage(BaseModel):
    page_no: int
    trial_page_no: int | None
    status: Literal["matched", "to_confirm", "missing", "unmatched"]


class SheetOut(BaseModel):
    number: int
    text: str
    answer: str | None
    asked: str | None
    agreed: bool | None
    reason: str | None
    pages: list[SheetPage]
    reply: str | None = None
    replied_at: datetime | None = None


class PreviewOut(BaseModel):
    sections: list[SectionPages]
    total_pages: int
    sheets: list[SheetOut]


class TrialNoteIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str = Field(min_length=1, max_length=20_000)


class ReportSource(BaseModel):
    kind: Literal["report"] = "report"
    para_no: int


class AnswerSource(BaseModel):
    kind: Literal["written_answer"] = "written_answer"
    question_no: int


Source = ReportSource | AnswerSource


class SentenceOut(BaseModel):
    index: int
    text: str
    sourced: bool
    source: Source | None
    reason: str | None
    note_id: uuid.UUID | None = None
    choice: Literal["tell", "leave_out"] | None = None


class NoteChecked(BaseModel):
    sentences: list[SentenceOut]


def _settings(row: dict[str, Any] | None) -> TrialPackSettingsOut:
    if row is None:
        return TrialPackSettingsOut(sections=list(DEFAULT_SECTIONS), include_guidance=False, hearing_id=None, version=0)  # type: ignore[arg-type]
    return TrialPackSettingsOut(
        sections=list(row["sections"]),
        include_guidance=row["include_guidance"],
        hearing_id=row["hearing_id"],
        version=row["version"],
    )


async def _row(conn: Any, case_id: uuid.UUID, lock: bool = False) -> dict[str, Any] | None:
    return await repo.one(
        conn,
        "select * from app.trial_pack_settings where case_id = %s" + (" for update" if lock else ""),
        (case_id,),
    )


@router.get("/cases/{case_id}/trial-pack/settings", response_model=TrialPackSettingsOut)
async def get_settings(case_id: uuid.UUID, conn: DbConn, access: View, response: Response) -> TrialPackSettingsOut:
    out = _settings(await _row(conn, case_id))
    response.headers["ETag"] = etag(out.version)
    return out


@router.put("/cases/{case_id}/trial-pack/settings", response_model=TrialPackSettingsOut)
async def put_settings(
    case_id: uuid.UUID, body: TrialPackSettingsIn, conn: DbConn, access: Build, if_match: IfMatch, response: Response
) -> TrialPackSettingsOut:
    row = await _row(conn, case_id, lock=True)
    if (row["version"] if row else 0) != if_match:
        raise stale()
    if not await current().hearings.belongs(conn, case_id, body.hearing_id):
        raise invalid("hearing_id")
    sections = list(dict.fromkeys(body.sections))
    if row is None:
        saved = await repo.one(
            conn,
            "insert into app.trial_pack_settings (case_id, sections, include_guidance, hearing_id)"
            " values (%s, %s, %s, %s) returning *",
            (case_id, sections, body.include_guidance, body.hearing_id),
        )
    else:
        saved = await repo.one(
            conn,
            "update app.trial_pack_settings set sections = %s, include_guidance = %s, hearing_id = %s,"
            " updated_by = app.current_person_id() where case_id = %s returning *",
            (sections, body.include_guidance, body.hearing_id, case_id),
        )
    out = _settings(saved)
    response.headers["ETag"] = etag(out.version)
    return out


@router.post("/cases/{case_id}/trial-pack", status_code=202, response_model=BuildQueued)
async def build_pack(case_id: uuid.UUID, conn: DbConn, access: Build, crypto: Crypto) -> BuildQueued:
    if await latest_served(conn, crypto, case_id) is None:
        raise conflict("not_served", "A trial pack is built from the served report. Serve it first.")
    job_id = await registry.enqueue(conn, case_id, "build_trial_pack")
    await conn.execute("update app.trial_pack_settings set requested_job_id = %s where case_id = %s", (job_id, case_id))
    return BuildQueued(job_id=job_id)


async def _latest_pack(conn: Any, case_id: uuid.UUID) -> dict[str, Any] | None:
    return await repo.one(
        conn, "select * from app.trial_packs where case_id = %s order by created_at desc limit 1", (case_id,)
    )


@router.get("/cases/{case_id}/trial-pack/latest", response_model=LatestOut)
async def latest_pack(case_id: uuid.UUID, conn: DbConn, access: View) -> LatestOut:
    row = await _latest_pack(conn, case_id)
    pack = (
        PackOut(
            id=row["id"],
            status="ready",
            sha256=row["sha256"],
            page_count=row["page_count"],
            sections=list(row["sections"]),
            stale=row["stale"],
            stale_reason=row["stale_reason"],
            created_at=row["created_at"],
            section_pages=[SectionPages(**p) for p in row["section_pages"]],
        )
        if row
        else None
    )
    return LatestOut(settings=_settings(await _row(conn, case_id)), pack=pack)


@router.get("/cases/{case_id}/trial-pack/preview", response_model=PreviewOut)
async def preview_pack(case_id: uuid.UUID, conn: DbConn, access: View, pack: Pack, crypto: Crypto) -> PreviewOut:
    """What each section would add (pages when printed on their own) and the readiness sheets, for the screen."""
    served = await latest_served(conn, crypto, case_id)
    if served is None:
        return PreviewOut(sections=[], total_pages=0, sheets=[])
    services = current()
    refs = await repo.page_refs(conn, case_id)
    built = await pack_document(conn, services, pack, case_id, {"sections": list(SECTIONS), "include_guidance": False})
    counted = render_counted(built.document)[2] if built is not None else []
    counts = [SectionPages(key=key, pages=n) for key, n in counted]  # type: ignore[arg-type]
    out = []
    for sheet in await sheets(conn, services, case_id, served):
        pages = [
            SheetPage(
                page_no=n,
                trial_page_no=refs[n].trial_page_no if n in refs else None,
                status=refs[n].status if n in refs else "unmatched",  # type: ignore[arg-type]
            )
            for n in sheet.pages
        ]
        out.append(SheetOut(**{**sheet.__dict__, "pages": pages}))
    return PreviewOut(sections=counts, total_pages=sum(c.pages for c in counts), sheets=out)


@router.get(
    "/cases/{case_id}/trial-pack/latest/download",
    dependencies=[Depends(require_step_up)],
    responses={200: {"content": {"application/pdf": {}}}},
)
async def download_pack(case_id: uuid.UUID, conn: DbConn, access: View, storage: Storage) -> Response:
    row = await _latest_pack(conn, case_id)
    if row is None:
        raise not_found()
    await audit.record(conn, action="trial.pack_downloaded", target=str(row["id"]), case_id=case_id)
    return Response(
        content=await storage.get(row["storage_path"]),
        media_type="application/pdf",
        headers={**NO_STORE, "Content-Disposition": 'attachment; filename="trial-pack.pdf"'},
    )


def sentences_of(text: str) -> list[str]:
    return [s.strip() for s in _SENTENCE.split(" ".join(text.split())) if s.strip()][:MAX_SENTENCES]


@router.post("/cases/{case_id}/trial-pack/check-new", response_model=NoteChecked)
async def check_new_note(
    case_id: uuid.UUID, body: TrialNoteIn, request: Request, conn: DbConn, access: Build, pack: Pack, crypto: Crypto
) -> NoteChecked:
    sentences = sentences_of(body.text)
    served = await latest_served(conn, crypto, case_id)
    answers = answers_of(await current().answers.served(conn, crypto, case_id))
    sources: dict[str, Source] = {}
    lines = [f"s{i}\t{s}" for i, s in enumerate(sentences)]
    for paragraph in served.paragraphs if served else ():
        ref = f"p{paragraph.para_no}"
        sources[ref] = ReportSource(para_no=paragraph.para_no)
        lines.append(f"{ref}\t{' '.join(paragraph.text.split())}")
    for answer in answers:
        ref = f"q{answer.question_no}"
        sources[ref] = AnswerSource(question_no=answer.question_no)
        lines.append(f"{ref}\t{' '.join(answer.text.split())}")
    found: dict[int, tuple[str | None, str]] = {}
    if sentences and sources:
        try:
            kit = current().model()
            result = await kit.run(
                conn,
                ai_tasks.t24(pack, len(sentences), list(sources)),
                pack=pack,
                text="\n".join(lines),
                case_id=case_id,
                subject="note",
            )
            for s in result["sentences"]:
                found[int(s["sentence_index"])] = (s["source"], str(s["reason"]))
        except (ProcessingPaused, InvalidOutput):
            found = {i: (None, "check_unavailable") for i in range(len(sentences))}
    out = []
    for i, text in enumerate(sentences):
        ref, reason = found.get(i, (None, None))  # type: ignore[assignment]
        source = sources.get(ref) if ref else None
        sentence = SentenceOut(index=i, text=text, sourced=source is not None, source=source, reason=reason)
        if source is None and reason != "check_unavailable":
            kept = await repo.one(
                conn,
                "insert into app.trial_notes (case_id, text_sha256, text) values (%s, %s, %s)"
                " on conflict (case_id, text_sha256) do update set text_sha256 = excluded.text_sha256"
                " returning id, choice",
                (case_id, sha256(text.encode()), await seal(conn, crypto, case_id, text, NEW_NOTE)),
            )
            assert kept is not None
            sentence.note_id, sentence.choice = kept["id"], kept["choice"]
        out.append(sentence)
    return NoteChecked(sentences=out)
