"""GET /v1/jurisdictions, /v1/jurisdictions/{code}, /v1/packs and /v1/cases/{case_id}/pack (architecture 3, 5.2).

A cell offers only the jurisdictions whose pack names it (`cell`). Everything returned is pack data:
the words, rules and registries the screens show instead of hard-coded ones (rule 11).
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, Request
from pydantic import BaseModel

from app.core.auth.access import CaseAccess, require_case_access
from app.core.config import cell_settings
from app.core.errors import ApiError
from app.core.http import DbConn, router
from app.core.packs import MergedPack, Pack, PackLibrary

packs_router = router(prefix="/v1", tags=["packs"])
case_pack_router = router(prefix="/v1/cases", tags=["packs"])


class PackRef(BaseModel):
    code: str
    name: str
    version: str


class JurisdictionSummary(BaseModel):
    code: str
    name: str
    version: str
    timezone: str
    date_order: str
    locale: str
    language: str
    currency: str
    working_week: list[int]
    vocabulary: dict[str, Any]
    declarations: list[dict[str, str]]
    compliance_checklist: dict[str, Any]
    deadline_rules: list[dict[str, Any]]
    legal_tests: str | None
    court_tracks: list[str]


class PackCombination(BaseModel):
    jurisdiction: PackRef
    case_type: PackRef
    specialty: PackRef
    expert_roles: list[str]


class CasePack(BaseModel):
    versions: dict[str, str]
    timezone: str
    date_order: str
    locale: str
    language: str
    currency: str
    retention_years: int
    quarantine_purge_days: int
    profession: str
    vocabulary: dict[str, Any]
    party_roles: list[dict[str, str]]
    instructing_party_types: list[dict[str, str]]
    expert_roles: list[str]
    registration_bodies: list[str]
    workflow_steps: list[dict[str, Any]]
    question_types: list[dict[str, Any]]
    report_sections: list[dict[str, Any]]
    report_appendices: list[dict[str, Any]] = []
    fact_kinds: list[dict[str, Any]]
    source_types: list[dict[str, Any]]
    lanes: list[dict[str, Any]]
    checks: list[dict[str, Any]]
    final_checks: list[dict[str, Any]]
    compliance_checklist: dict[str, Any]
    declarations: list[dict[str, str]]
    legal_tests: str | None
    deadline_rules: list[dict[str, Any]]
    point_sides: list[dict[str, str]] = []
    opinion_views: list[dict[str, str]] = []
    range_note_prompt: str = ""
    subject_statuses: list[dict[str, str | None]] = []
    document_kinds: list[dict[str, str]] = []
    outside_expertise_parts: list[str] = []
    written_question_classifications: list[str] = []


def _library(request: Request) -> PackLibrary:
    library: PackLibrary = request.app.state.packs
    return library


def _cell(request: Request) -> str:
    region: str | None = request.app.state.region
    return region if region is not None else cell_settings().region


def _ref(pack: Pack) -> PackRef:
    return PackRef(code=pack.code, name=pack.meta["name"], version=pack.meta["version"])


def case_pack(pack: MergedPack) -> CasePack:
    """The pack data a case's screens use (also exported for the web tests by `make api-client`)."""
    return CasePack(
        versions=pack.versions,
        timezone=pack.timezone,
        date_order=pack.date_order,
        locale=pack.locale,
        language=pack.language,
        currency=pack.currency,
        retention_years=pack.retention_years,
        quarantine_purge_days=pack.quarantine_purge_days,
        profession=pack.profession,
        vocabulary=pack.vocabulary,
        party_roles=pack.party_roles(),
        instructing_party_types=pack.instructing_party_types(),
        expert_roles=pack.expert_roles(),
        registration_bodies=pack.registration_bodies(),
        workflow_steps=pack.workflow_steps(),
        question_types=pack.question_types(),
        report_sections=pack.report_sections(),
        report_appendices=pack.report_appendices(),
        fact_kinds=pack.fact_kinds(),
        source_types=pack.source_types(),
        lanes=pack.lanes(),
        checks=pack.checks(),
        final_checks=pack.final_checks(),
        compliance_checklist=pack.compliance_checklist(),
        declarations=pack.declarations(),
        legal_tests=pack.legal_tests() or None,
        deadline_rules=pack.deadline_rules(),
        point_sides=pack.point_sides(),
        opinion_views=pack.opinion_views(),
        range_note_prompt=pack.range_note_prompt(),
        subject_statuses=pack.subject_statuses(),
        document_kinds=pack.document_kinds(),
        outside_expertise_parts=pack.outside_expertise_parts(),
        written_question_classifications=pack.written_question_classifications(),
    )


@packs_router.get("/jurisdictions", response_model=list[PackRef])
async def list_jurisdictions(request: Request) -> list[PackRef]:
    return [_ref(p) for p in _library(request).jurisdictions_in(_cell(request))]


@packs_router.get("/jurisdictions/{code}", response_model=JurisdictionSummary)
async def get_jurisdiction(code: str, request: Request) -> JurisdictionSummary:
    library = _library(request)
    if code not in {p.code for p in library.jurisdictions_in(_cell(request))}:
        raise ApiError("not_found", "Not found.", 404)
    return JurisdictionSummary(**library.jurisdiction_summary(code))


@packs_router.get("/packs", response_model=list[PackCombination])
async def list_packs(request: Request) -> list[PackCombination]:
    return [
        PackCombination(
            jurisdiction=_ref(j), case_type=_ref(ct), specialty=_ref(sp), expert_roles=list(ct.meta["expert_roles"])
        )
        for j, ct, sp in _library(request).combinations_in(_cell(request))
    ]


@case_pack_router.get("/{case_id}/pack", response_model=CasePack)
async def get_case_pack(
    case_id: uuid.UUID,
    access: Annotated[CaseAccess, Depends(require_case_access("case.view_metadata"))],
    conn: DbConn,
    request: Request,
) -> CasePack:
    async with conn.cursor() as cur:
        await cur.execute("select jurisdiction, case_type, specialty from app.cases where id = %s", (case_id,))
        row = await cur.fetchone()
    assert row is not None  # require_case_access has already answered 404
    return case_pack(_library(request).combine(*row))
