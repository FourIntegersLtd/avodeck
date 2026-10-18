"""A case for slice E's tests: the named expert and every other case role, an outsider, one confirmed
question, and facts in each state on read and unread pages.

Slice B's read log is not built yet, so `Reads` stands in for `app.state.page_reads`: the named expert
has read page 1 and not page 2. Fact values are short made-up record words (nothing here is a real
record); the case runs under whichever pack combination the test names.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import psycopg
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.packs import default_library
from tests.support.contracts import add_fact, add_question
from tests.support.packs import Combo
from tests.support.seed import Seed

PLAN = "review 3/52 if no better"
RESULT = "Ferritin 9 ug/L"


@dataclass
class Reads:
    """In-memory read log: `(case_id, page_id, page_no, person_id)` rows standing in for slice B."""

    rows: set[tuple[uuid.UUID, uuid.UUID, int, uuid.UUID]] = field(default_factory=set)

    async def has_read(self, conn: AsyncConnection, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        return any(r[0] == case_id and r[2] == page_no and r[3] == person_id for r in self.rows)

    async def read_page_ids(self, conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        return {r[1] for r in self.rows if r[0] == case_id and r[3] == person_id}


@dataclass
class World:
    case: uuid.UUID
    expert: uuid.UUID
    co_expert: uuid.UUID
    supervisor: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    outsider: uuid.UUID
    document: uuid.UUID
    read_page: uuid.UUID
    unread_page: uuid.UUID
    question: uuid.UUID
    verified: uuid.UUID
    result: uuid.UUID
    suggested: uuid.UUID
    rejected: uuid.UUID
    on_unread_page: uuid.UUID

    def url(self, tail: str) -> str:
        return f"/v1/cases/{self.case}/{tail}"


def build(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    expert, co_expert, supervisor, assistant, viewer, outsider = (seed.person() for _ in range(6))
    org = seed.org(
        kind="team",
        members={expert: "expert", co_expert: "expert", supervisor: "expert", assistant: "assistant", viewer: "expert"},
    )
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(
        org,
        expert,
        status="active",
        members={co_expert: "co_expert", supervisor: "supervisor", assistant: "assistant", viewer: "viewer"},
        packs=combo.case_row(),
    )
    document = seed.document(case)
    read_page, unread_page = seed.page(case, document, 1), seed.page(case, document, 2)
    reads.rows.add((case, read_page, 1, expert))
    return World(
        case=case,
        expert=expert,
        co_expert=co_expert,
        supervisor=supervisor,
        assistant=assistant,
        viewer=viewer,
        outsider=outsider,
        document=document,
        read_page=read_page,
        unread_page=unread_page,
        question=add_question(admin, case, status="confirmed"),
        verified=add_fact(admin, case, read_page, status="verified", value=PLAN),
        result=add_fact(admin, case, read_page, status="edited", value=RESULT),
        suggested=add_fact(admin, case, read_page, status="suggested", value=PLAN),
        rejected=add_fact(admin, case, read_page, status="rejected", value=PLAN),
        on_unread_page=add_fact(admin, case, unread_page, status="verified", value=PLAN),
    )


def app_for(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    """The API with case encryption, the real pack library and the stand-in read log."""
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=str(combo.raw("jurisdiction", "pack.yaml")["cell"]),
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        crypto=CaseCrypto(LocalKeyProvider.generate()),
        packs=default_library(),
        page_reads=reads,
    )
