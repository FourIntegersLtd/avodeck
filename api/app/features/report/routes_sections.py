"""Screen 16: the named expert's own words for each report section that is not an opinion.

Opinions are written on screen 15, declarations come from the jurisdiction pack and the generated
appendices from Avodeck, so only the other pack sections and appendices are written here (422 otherwise).
Words are sealed under the case key; citations have the opinion shape, every `[[cite:n]]` marker matches
one citation and every citation must be a citable fact's own words (409 `not_citable`, rule 2). The first
save needs no If-Match; later saves name the version they replace (428 without, 412 if stale).
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated

from fastapi import Header, Response
from psycopg.types.json import Jsonb
from pydantic import BaseModel, ConfigDict, Field

from app.core.contracts.citations import Citation, markers, quote_within
from app.core.contracts.facts import citable_facts
from app.core.errors import ApiError
from app.core.http import DbConn, etag, stale
from app.features.report.assemble import SECTION_PURPOSE, writable_keys
from app.features.report.common import Crypto, Pack, Reads, View, Write, invalid, not_found
from app.features.report.router import router

_ETAG = re.compile(r'(?:W/)?"(\d+)"')


class SectionText(BaseModel):
    key: str
    body: str
    citations: list[Citation]
    version: int


class SectionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(max_length=50_000)
    citations: list[Citation] = Field(default_factory=list, max_length=500)


@router.get("/cases/{case_id}/report/sections/{key}", response_model=SectionText)
async def section(
    case_id: uuid.UUID, key: str, access: View, conn: DbConn, crypto: Crypto, response: Response
) -> SectionText:
    cur = await conn.execute(
        "select body, citation_refs, version from app.report_section_texts where case_id = %s and section = %s",
        (case_id, key),
    )
    row = await cur.fetchone()
    if row is None:
        raise not_found()
    body = (await crypto.decrypt(conn, case_id, bytes(row[0]), purpose=SECTION_PURPOSE)).decode()
    response.headers["ETag"] = etag(row[2])
    return SectionText(key=key, body=body, citations=[Citation.model_validate(c) for c in row[1]], version=row[2])


@router.put("/cases/{case_id}/report/sections/{key}", response_model=SectionText)
async def save_section(
    case_id: uuid.UUID,
    key: str,
    saving: SectionIn,
    access: Write,
    conn: DbConn,
    crypto: Crypto,
    pack: Pack,
    reads: Reads,
    response: Response,
    if_match: Annotated[str | None, Header()] = None,
) -> SectionText:
    if key not in writable_keys(pack):
        raise invalid("key")
    if sorted(set(markers(saving.body))) != list(range(1, len(saving.citations) + 1)):
        raise invalid("citations")
    citable = {f.id: f for f in await citable_facts(conn, case_id, reads)}
    for position, citation in enumerate(saving.citations, start=1):
        fact = citable.get(citation.fact_id)
        if fact is None or fact.page_id != citation.page_id or not quote_within(citation.quote_verbatim, fact.value):
            raise ApiError(
                "not_citable", "Only checked facts on pages you have read can be cited.", 409, citation=position
            )
    cur = await conn.execute(
        "select version from app.report_section_texts where case_id = %s and section = %s for update", (case_id, key)
    )
    row = await cur.fetchone()
    if row is not None:
        if not if_match:
            raise ApiError("precondition_required", "Send If-Match with the ETag you last read.", 428)
        match = _ETAG.fullmatch(if_match.strip())
        if match is None or int(match.group(1)) != row[0]:
            raise stale()
    sealed = await crypto.encrypt(conn, case_id, saving.body.encode(), purpose=SECTION_PURPOSE)
    refs = Jsonb([c.model_dump(mode="json") for c in saving.citations])
    if row is None:
        cur = await conn.execute(
            "insert into app.report_section_texts (case_id, section, body, citation_refs) values (%s, %s, %s, %s)"
            " returning version",
            (case_id, key, sealed, refs),
        )
    else:
        cur = await conn.execute(
            "update app.report_section_texts set body = %s, citation_refs = %s, version = version + 1,"
            " updated_by = app.current_person_id(), updated_at = now() where case_id = %s and section = %s"
            " returning version",
            (sealed, refs, case_id, key),
        )
    saved = await cur.fetchone()
    assert saved is not None
    response.headers["ETag"] = etag(saved[0])
    return SectionText(key=key, body=saving.body, citations=saving.citations, version=saved[0])
