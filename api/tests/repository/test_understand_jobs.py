"""Slice D's two worker jobs (docs/slices/D-understand.md; architecture 6.3 T-11 and T-13a).

`rerun_checks` first works out when each fact reached the clinician (code, never the letter's own date, rule 1
and the "as the clinician saw it" rule), then runs the named expert's checks on checked facts and writes
findings. `suggest_links` only suggests (rule 1). Both run under the fixture packs and the launch packs, so
no kind, source, check or lane is written here (rule 11). No text reaches a log or a job row (rule 6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from app.core.ai.embed import RecordedEmbedder
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.packs import MergedPack, default_library
from app.features.pipeline.paths import word_purpose
from app.features.understand.jobs import rerun_checks
from app.features.understand.services import UnderstandServices, configure
from tests.support.facts_seed import insert_fact
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture(autouse=True)
def services(crypto: CaseCrypto) -> Any:
    configure(UnderstandServices(kit=None, crypto=crypto, embedder=RecordedEmbedder(), packs=default_library()))
    yield
    configure(None)


@dataclass
class Bundle:
    seed: Seed
    db: Database
    crypto: CaseCrypto
    pack: MergedPack
    expert: uuid.UUID
    case: uuid.UUID
    document: uuid.UUID
    arriving: str  # a fact kind that arrives with a mark
    own: str  # a fact kind the clinician wrote
    own_source: str
    other_source: str
    pages: int = 0

    async def page(self, source: str, words: list[str] | None = None) -> uuid.UUID:
        """A numbered page of this source with sealed words w1..wn."""
        self.pages += 1
        page = self.seed.page(self.case, self.document, self.pages)
        self.seed.admin.execute("update app.pages set source_type = %s where id = %s", (source, page))
        async with self.db.job(self.case) as conn:
            for n, text in enumerate(words or ["x"] * 8, 1):
                sealed = await self.crypto.encrypt(conn, self.case, text.encode(), purpose=word_purpose(page, f"w{n}"))
                await conn.execute(
                    "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, %s, %s, %s, %s, %s, 0.9, 'ocr')",
                    (page, self.case, n, f"w{n}", sealed, [0, 0, 1, 0, 1, 1, 0, 1]),
                )
        return page

    def evidence(self, page: uuid.UUID, *items: tuple[str, str | None, str]) -> None:
        listed = [{"kind": k, "date": d, "date_text": None, "word_ids": [w]} for k, d, w in items]
        self.seed.admin.execute(
            "insert into app.page_analysis (page_id, case_id, availability_evidence) values (%s, %s, %s)",
            (page, self.case, Jsonb(listed)),
        )

    def fact(self, page: uuid.UUID, kind: str, word: str, day: str, **more: Any) -> uuid.UUID:
        return insert_fact(
            self.seed.admin, self.case, page, kind, word_ids=[word], event_date=date.fromisoformat(day), **more
        )

    def override(self, fact: uuid.UUID, day: str | None) -> None:
        self.seed.admin.execute(
            "insert into app.availability_overrides (case_id, fact_id, available_at, unknown, reason, person_id)"
            " values (%s, %s, %s, %s, 'told so', %s)",
            (self.case, fact, day, day is None, self.expert),
        )

    def availability(self) -> dict[uuid.UUID, tuple[date | None, str | None]]:
        rows = self.seed.admin.execute(
            "select id, available_at, availability_basis from app.facts where case_id = %s", (self.case,)
        ).fetchall()
        return {r[0]: (r[1].astimezone(UTC).date() if r[1] else None, r[2]) for r in rows}

    async def run(self, kind: str = "rerun_checks") -> uuid.UUID:
        job = self.seed.job(self.case, kind=kind, requested_by=self.expert)
        from app.features.understand import jobs

        await getattr(jobs, kind)(JobContext(self.db, job, self.case, kind, 1))
        return job


def build(seed: Seed, db: Database, crypto: CaseCrypto, combo: Combo) -> Bundle:
    pack = default_library().combine(*combo.codes)
    arriving = sorted(pack.arriving_kinds())[0]
    own = next(k["key"] for k in pack.fact_kinds() if k["key"] not in pack.arriving_kinds())
    own_source = sorted(pack.own_record_sources())[0]
    other_source = next(s["key"] for s in pack.source_types() if s["key"] not in pack.own_record_sources())
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, status="active", packs=combo.case_row())
    return Bundle(seed, db, crypto, pack, expert, case, seed.document(case), arriving, own, own_source, other_source)


@pytest.fixture(params=BOTH, ids=lambda c: c.jurisdiction)
def bundle(request: pytest.FixtureRequest, seed: Seed, worker_db: Database, crypto: CaseCrypto) -> Bundle:
    return build(seed, worker_db, crypto, request.param)


# --- rerun_checks: when each fact reached the clinician -------------------------------------------------


async def test_availability_comes_from_marks_own_record_and_overrides_never_the_letters_own_date(
    bundle: Bundle,
) -> None:
    b = bundle
    mine = b.fact(await b.page(b.own_source), b.own, "w1", "2024-03-01")
    marked_page = await b.page(b.other_source)
    marked = b.fact(marked_page, b.arriving, "w2", "2024-03-05")
    b.evidence(marked_page, ("received", "2024-03-20", "w5"))
    unmarked = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-06")
    told = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-07")
    b.override(told, "2024-04-01")
    nobody = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-08")
    b.override(nobody, None)
    rejected = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-09", status="rejected")

    await b.run()

    got = b.availability()
    assert got[mine] == (date(2024, 3, 1), "own_record")
    assert got[marked] == (date(2024, 3, 20), "received")
    assert got[unmarked] == (None, None)
    assert got[told] == (date(2024, 4, 1), "expert_override")
    assert got[nobody] == (None, "expert_unknown")
    assert got[rejected] == (None, None)


async def test_a_second_run_changes_nothing_and_a_new_override_is_picked_up(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = b.fact(page, b.arriving, "w2", "2024-03-05")
    await b.run()
    assert b.availability()[fact] == (None, None)
    b.override(fact, "2024-05-02")
    await b.run()
    await b.run()
    assert b.availability()[fact] == (date(2024, 5, 2), "expert_override")
    version = b.seed.admin.execute("select version from app.facts where id = %s", (fact,)).fetchone()
    assert version is not None and version[0] == 2  # verified: version 2; availability never adds a version

