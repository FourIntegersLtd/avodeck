"""Slice A: the reader is told the pack's language (WP2 2d). Every OCR call the pipeline makes carries the
case's pack locale, never a locale written in code; the fixture pack's locale differs from the launch pack's."""

from __future__ import annotations

from pathlib import Path

import psycopg
import pytest

from app.core.ai.recordings import FixtureStore
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import recorded_book, rekeyed_store, subset_document, subset_page_bytes
from tests.repository.test_pipeline_words import _open, storage  # noqa: F401
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.codes[0])
async def test_every_ocr_call_carries_the_pack_locale(
    combo: Combo,
    tmp_path: Path,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,  # noqa: F811
) -> None:
    store: FixtureStore = rekeyed_store(tmp_path / "recordings", subset_document(), subset_page_bytes())
    opened = await _open(
        combo,
        store,
        recorded_book(store),
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        seed=seed,
        storage=storage,
    )
    want = default_library().combine(*combo.codes).locale
    async with opened as h:
        await h.upload(subset_document())
        await h.drain(timeout=300)
        assert h.ocr is not None
        locales = list(h.ocr.locales)  # type: ignore[attr-defined]
    assert locales, "the subset has scanned pages, so OCR was asked"
    assert set(locales) == {want}
