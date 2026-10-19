"""Screen 23, trial page numbers (architecture 5.2, `match_trial_bundle`).

- `POST /cases/{c}/court-bundles` (`case.upload`): the court's re-paginated bundle as a PDF; 202 and the
  matching job. One current bundle per case and hearing (409 `bundle_exists`): a re-issue goes through
  `POST .../court-bundles/{id}/supersede`, which also marks trial packs built on it stale.
- `GET /cases/{c}/concordance` (`case.view_content`): every page the expert read with its trial number,
  matched, to confirm or missing, and the served paragraphs citing it.
- `PATCH /cases/{c}/concordance/{id}` (`fact.verify`, If-Match): the expert's own match (or none).
- `GET /cases/{c}/concordance/export?format=csv|pdf` (step-up: it leaves Avodeck).
- `POST /cases/{c}/concordance/tell-instructing-party` (`request.draft`): page numbers only, for a letter.
"""

from __future__ import annotations

import io
import uuid
from datetime import date
from typing import Any, Literal

from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.core.auth.step_up import require_step_up
from app.core.contracts.served import latest_served
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.jobs import registry
from app.features.trial import repo
from app.features.trial.common import (
    NO_STORE,
    Crypto,
    Draft,
    Pack,
    Storage,
    Upload,
    Verify,
    View,
    conflict,
    invalid,
    not_found,
    pdf_body,
    sha256,
    trial_path,
)
from app.features.trial.pdfs import Document, render
from app.features.trial.router import router
from app.features.trial.services import current
from app.features.trial.words import word

Status = Literal["matched", "to_confirm", "missing"]


class BundleQueued(BaseModel):
    job_id: uuid.UUID
    court_bundle_id: uuid.UUID


class BundleOut(BaseModel):
    id: uuid.UUID
    status: Literal["received", "matching", "matched", "superseded"]
    hearing_id: uuid.UUID | None
    received_on: date | None
    page_count: int | None
    version: int


class RowOut(BaseModel):
    id: uuid.UUID
    page_no: int
    trial_page_no: int | None
    status: Status
    confirmed: bool
    method: Literal["text", "sequence", "expert", "none"]
    score: float
    paragraphs: list[int]
    version: int


class Counts(BaseModel):
    matched: int = 0
    to_confirm: int = 0
    missing: int = 0


class ConcordanceOut(BaseModel):
    bundle: BundleOut | None
    rows: list[RowOut]
    counts: Counts


class RowChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trial_page_no: int | None = Field(ge=1)


class MissingPage(BaseModel):
    page_no: int
    paragraphs: list[int]


class MissingPages(BaseModel):
    missing: list[MissingPage]
    received_on: date | None
    bundle_version: int | None


def _bundle(row: dict[str, Any]) -> BundleOut:
    return BundleOut(
        id=row["id"],
        status=row["status"],
        hearing_id=row["hearing_id"],
        received_on=row["received_on"],
        page_count=row["page_count"],
        version=row["version"],
    )


def _row(row: dict[str, Any], cited: dict[uuid.UUID, list[int]]) -> RowOut:
    return RowOut(
        id=row["id"],
        page_no=row["page_no"],
        trial_page_no=row["trial_page_no"],
        status=row["status"],
        confirmed=row["confirmed"],
        method=row["method"],
        score=round(float(row["score"]), 3),
        paragraphs=cited.get(row["page_id"], []),
        version=row["version"],
    )


async def _queue(conn: Any, storage: Any, case_id: uuid.UUID, data: bytes, **values: Any) -> BundleQueued:
    bundle_id = uuid.uuid4()
    path = trial_path(case_id, "bundles", f"{bundle_id}.pdf")
    await storage.put(path, data)
    job_id = await registry.enqueue(conn, case_id, "match_trial_bundle")
    await conn.execute(
        "insert into app.court_bundles (id, case_id, hearing_id, version, status, storage_path, sha256,"
        " received_on, job_id) values (%s, %s, %s, %s, 'matching', %s, %s, %s, %s)",
        (
            bundle_id,
            case_id,
            values["hearing_id"],
            values["version"],
            path,
            sha256(data),
            values["received_on"],
            job_id,
        ),
    )
    return BundleQueued(job_id=job_id, court_bundle_id=bundle_id)


@router.post("/cases/{case_id}/court-bundles", status_code=202, response_model=BundleQueued)
async def upload_court_bundle(
    case_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: Upload,
    storage: Storage,
    received_on: date | None = None,
    hearing_id: uuid.UUID | None = None,
) -> BundleQueued:
    if not await current().hearings.belongs(conn, case_id, hearing_id):
        raise invalid("hearing_id")
    data = await pdf_body(request)
    existing = await repo.one(
        conn,
        "select id from app.court_bundles where case_id = %s and hearing_id is not distinct from %s"
        " and status <> 'superseded'",
        (case_id, hearing_id),
    )
    if existing is not None:
        raise conflict("bundle_exists", "A court bundle is already here. Send the new one as a re-issue.")
    return await _queue(
        conn, storage, case_id, data, hearing_id=hearing_id, version=1, received_on=received_on or date.today()
    )


@router.post("/cases/{case_id}/court-bundles/{bundle_id}/supersede", status_code=202, response_model=BundleQueued)
async def supersede_court_bundle(
    case_id: uuid.UUID,
    bundle_id: uuid.UUID,
    request: Request,
    conn: DbConn,
    access: Upload,
    storage: Storage,
    received_on: date | None = None,
) -> BundleQueued:
    old = await repo.one(
        conn, "select * from app.court_bundles where case_id = %s and id = %s for update", (case_id, bundle_id)
    )
    if old is None:
        raise not_found()
    if old["status"] == "superseded":
        raise conflict("superseded", "This court bundle has already been replaced.")
    data = await pdf_body(request)
    await conn.execute("update app.court_bundles set status = 'superseded' where id = %s", (bundle_id,))
    queued = await _queue(
        conn,
        storage,
        case_id,
        data,
        hearing_id=old["hearing_id"],
        version=old["version"] + 1,
        received_on=received_on or date.today(),
    )
    await conn.execute(
        "update app.court_bundles set superseded_by = %s where id = %s", (queued.court_bundle_id, bundle_id)
    )
    await conn.execute(
        "update app.trial_packs set stale = true, stale_reason = 'court_bundle_superseded'"
        " where case_id = %s and not stale",
        (case_id,),
    )
    return queued


async def _current(conn: Any, crypto: Any, case_id: uuid.UUID) -> tuple[dict[str, Any] | None, list[RowOut]]:
    bundle = await repo.current_bundle(conn, case_id)
    if bundle is None:
        return None, []
    cited = repo.paragraphs_by_page(await latest_served(conn, crypto, case_id))
    return bundle, [_row(r, cited) for r in await repo.concordance(conn, bundle["id"])]


def _counts(rows: list[RowOut]) -> Counts:
    counts = Counts()
    for row in rows:
        setattr(counts, row.status, getattr(counts, row.status) + 1)
    return counts


@router.get("/cases/{case_id}/concordance", response_model=ConcordanceOut)
async def get_concordance(
    case_id: uuid.UUID, conn: DbConn, access: View, crypto: Crypto, status: Status | None = None
) -> ConcordanceOut:
    bundle, rows = await _current(conn, crypto, case_id)
    return ConcordanceOut(
        bundle=_bundle(bundle) if bundle else None,
        rows=[r for r in rows if status is None or r.status == status],
        counts=_counts(rows),
    )


@router.patch("/cases/{case_id}/concordance/{row_id}", response_model=RowOut)
async def confirm_page(
    case_id: uuid.UUID,
    row_id: uuid.UUID,
    body: RowChange,
    conn: DbConn,
    access: Verify,
    crypto: Crypto,
    if_match: IfMatch,
    response: Response,
) -> RowOut:
    row = await repo.one(
        conn,
        "select k.*, b.status as bundle_status, b.page_count from app.concordance k"
        " join app.court_bundles b on b.id = k.court_bundle_id where k.case_id = %s and k.id = %s for update of k",
        (case_id, row_id),
    )
    if row is None:
        raise not_found()
    if row["bundle_status"] == "superseded":
        raise conflict("superseded", "This court bundle has been replaced.")
    if row["version"] != if_match:
        raise stale()
    if body.trial_page_no is not None and body.trial_page_no > (row["page_count"] or 0):
        raise invalid("trial_page_no")
    updated = await repo.one(
        conn,
        "update app.concordance set trial_page_no = %s, method = 'expert', confirmed = true,"
        " confirmed_by = app.current_person_id(), confirmed_at = now() where id = %s returning *",
        (body.trial_page_no, row_id),
    )
    assert updated is not None
    updated["status"] = repo.status_of(updated)
    cited = repo.paragraphs_by_page(await latest_served(conn, crypto, case_id))
    response.headers["ETag"] = etag(updated["version"])
    return _row(updated, cited)


@router.get(
    "/cases/{case_id}/concordance/export",
    dependencies=[Depends(require_step_up)],
    responses={200: {"content": {"text/csv": {}, "application/pdf": {}}}},
)
async def export_concordance(
    case_id: uuid.UUID,
    conn: DbConn,
    access: View,
    crypto: Crypto,
    pack: Pack,
    format: Literal["csv", "pdf"] = Query(default="csv"),  # noqa: A002 - the query's name
) -> Response:
    bundle, rows = await _current(conn, crypto, case_id)
    if bundle is None:
        raise not_found()
    if format == "csv":
        out = io.StringIO()
        out.write("page_no,trial_page_no,status,score\n")
        for r in rows:
            out.write(f"{r.page_no},{'' if r.trial_page_no is None else r.trial_page_no},{r.status},{r.score:.3f}\n")
        return Response(
            content=out.getvalue(),
            media_type="text/csv",
            headers={**NO_STORE, "Content-Disposition": 'attachment; filename="trial-page-numbers.csv"'},
        )
    term = pack.term("court_bundle")
    lang = pack.language
    doc = Document(word(lang, "concordance", court_bundle=term))
    for r in rows:
        where = word(lang, r.status, court_bundle=term, trial_page_no=r.trial_page_no)
        doc.para(word(lang, "concordance_row", page_no=r.page_no, where=where))
    pdf, _ = render(doc)
    return Response(content=pdf, media_type="application/pdf", headers=NO_STORE)


@router.post("/cases/{case_id}/concordance/tell-instructing-party", response_model=MissingPages)
async def tell_instructing_party(case_id: uuid.UUID, conn: DbConn, access: Draft, crypto: Crypto) -> MissingPages:
    bundle, rows = await _current(conn, crypto, case_id)
    return MissingPages(
        missing=[MissingPage(page_no=r.page_no, paragraphs=r.paragraphs) for r in rows if r.status == "missing"],
        received_on=bundle["received_on"] if bundle else None,
        bundle_version=bundle["version"] if bundle else None,
    )
