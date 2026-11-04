"""Slice A: at extraction a two-digit year is never read in a century (plan: entry dates, review of PR 253).

The "latest date so far" at extraction depends on the order pages are read in, so a short year read then could
land in the wrong century and the repair job (which leaves dated entries alone) would never correct it. At
extraction the entry stays `please_check`; the repair job reads it with the whole bundle's latest date.
Runs under the launch and the fixture packs. Messages name counts only (rule 6)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_record_entries import answer_with, opened
from tests.repository.test_pipeline_support import text_pdf
from tests.support.ai import ScriptedBedrock
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed
from tests.unit.test_pipeline_entry_dates_births import written

pytestmark = pytest.mark.anyio


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_extraction_leaves_a_short_year_to_the_repair_job_whatever_the_latest_date_on_the_page(
    combo: Combo,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> None:
    pack = default_library().combine(*combo.codes)
    visit = written(pack, 12, 6, 18)
    printed = written(pack, 3, 1, 2015)  # the latest date in full on the page: a low "so far" anchor
    document = text_pdf([[f"Seen on {visit}", f"Printed {printed}"]])
    words = embedded_words(split_pages(document)[0]).words
    kind = pack.entry_kinds()[0]["key"]
    entries = [
        {
            "kind": kind,
            "first_word_id": words[0].id,
            "last_word_id": words[-1].id,
            "author_role": None,
            "specialty": None,
        }
    ]
    bedrock = ScriptedBedrock([answer_with(pack, "", words, entries)])
    async with await opened(tmp_path, bedrock, admin, api_db, worker_dsn, auth_env, seed, storage, combo) as h:
        await h.upload(document)
        assert set((await h.drain()).values()) == {"done"}
        (page,) = h.pages()
        rows = admin.execute(
            "select event_date, date_basis from app.record_entries where page_id = %s", (page["id"],)
        ).fetchall()
        assert [tuple(r) for r in rows] == [(None, "please_check")], "no century is guessed at extraction"
