"""A case with one page of words, the first two of which the named expert has corrected (slice B, 0018).

Shared by the tests that prove another slice reads the expert's reading, not the computer's.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import psycopg

from app.core.contracts.document_words import word_purpose
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.features.reader import repo
from tests.fixtures.stub_facts import load_stub_facts
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

CRYPTO = CaseCrypto(LocalKeyProvider.generate())
READING = "xqzcorrected"


@dataclass
class Corrected:
    case: uuid.UUID
    expert: uuid.UUID
    page_id: uuid.UUID
    page_no: int
    first: str  # the first word of the corrected run: carries the expert's reading
    second: str  # the second: folded into the first
    original: dict[str, str]  # word id -> the computer's reading, for the whole page


async def corrected_world(seed: Seed, admin_dsn: str, api_db: Database) -> Corrected:
    expert = seed.person()
    org = seed.org(kind="personal", members={expert: "owner"})
    case = seed.case(org, expert, status="active", packs=LAUNCH.case_row())
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as aconn:
        loaded = await load_stub_facts(aconn, case, pages=[118], crypto=CRYPTO, verified=True)
        page = loaded[118].page_id
        cur = await aconn.execute("select word_id, text from app.ocr_words where page_id = %s order by n", (page,))
        original = {w: (await CRYPTO.decrypt(aconn, case, bytes(t), purpose=word_purpose(page, w))).decode()
                    for w, t in await cur.fetchall()}
    first, second = list(original)[:2]
    async with api_db.request(scope_for(expert)) as conn:
        await repo.save_correction(conn, CRYPTO, case, page, [first, second], READING, expert)
    return Corrected(case, expert, page, 118, first, second, original)
