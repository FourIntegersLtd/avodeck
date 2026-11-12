"""Slice B: a page lost to a parser failure has no picture and no page PDF (audit 13, DP-09 follow-up).

The pipeline stores such a page as `unreadable` with `image_path` null and cuts it again on a later run. The reader
must not let the expert record a page they could not see as read, and must answer "not available" (never a 500 or
an empty file sent anywhere) for its picture and its second reading. These tests call the route functions and the
job directly, so they need only the database.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.errors import ApiError
from app.core.jobs import JobContext
from app.core.storage import StorageNotFound
from app.features.reader import router, second_reading, second_reading_routes
from app.features.reader.schemas import ReadIn
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PICTURE = "cases/lost/pages/p/image.jpg"


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


def world(seed: Seed, *, picture: str | None) -> dict[str, Any]:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    page = seed.page(case, seed.document(case), 1)
    seed.admin.execute("update app.pages set unreadable = true, image_path = %s where id = %s", (picture, page))
    return {"expert": expert, "case": case, "page": page}


def reads(seed: Seed, page: uuid.UUID) -> int:
    row = seed.admin.execute("select count(*) from app.page_reads where page_id = %s", (page,)).fetchone()
    assert row is not None
    return int(row[0])


async def mark(api_db: Database, w: dict[str, Any]) -> Any:
    async with api_db.request(scope_for(w["expert"])) as conn:
        return await router.mark_read(w["case"], 1, ReadIn(dwell_seconds=9), None, conn, scope_for(w["expert"]))  # type: ignore[arg-type]


async def test_a_page_with_no_picture_cannot_be_marked_read(api_db: Database, seed: Seed) -> None:
    w = world(seed, picture=None)
    with pytest.raises(ApiError) as refused:
        await mark(api_db, w)
    assert (refused.value.code, refused.value.status) == ("conflict", 409)
    assert reads(seed, w["page"]) == 0


async def test_a_poor_quality_page_with_a_picture_can_still_be_marked_read(api_db: Database, seed: Seed) -> None:
    w = world(seed, picture=PICTURE)
    out = await mark(api_db, w)
    assert out.read is True
    assert reads(seed, w["page"]) == 1


class _Missing:
    async def get(self, path: str) -> bytes:
        raise StorageNotFound("storage: 404")


def request_with(storage: Any) -> Any:
    class _Db:
        async def release(self, conn: Any) -> None:
            return None

    class _Crypto:
        async def key(self, conn: Any, case: uuid.UUID) -> bytes:
            return b"k"

        def open_with(self, key: bytes, case: uuid.UUID, sealed: bytes, *, purpose: str) -> bytes:
            raise AssertionError("nothing to open")

    state = SimpleNamespace(storage=storage, crypto=_Crypto(), database=_Db())
    return SimpleNamespace(app=SimpleNamespace(state=state), state=SimpleNamespace(conn=None))


async def test_a_picture_missing_from_storage_is_not_found_not_a_server_error(api_db: Database, seed: Seed) -> None:
    w = world(seed, picture=PICTURE)
    async with api_db.request(scope_for(w["expert"])) as conn:
        with pytest.raises(ApiError) as gone:
            await router.page_image(
                w["case"],
                1,
                None,  # type: ignore[arg-type]
                request_with(_Missing()),
                conn,
                scope_for(w["expert"]),
            )
    assert (gone.value.code, gone.value.status) == ("not_found", 404)


async def test_asking_for_a_second_reading_of_a_missing_picture_does_not_fail(seed: Seed) -> None:
    sha = await second_reading_routes._picture_sha(request_with(_Missing()), {"image_path": PICTURE})
    assert sha is None


async def test_the_second_reading_job_refuses_a_page_with_no_picture(
    worker_db: Database, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world(seed, picture=None)
    seed.admin.execute(
        "insert into app.second_readings (case_id, page_id, requested_by) values (%s, %s, %s)",
        (w["case"], w["page"], w["expert"]),
    )
    job = seed.job(w["case"], kind=second_reading.KIND)
    seed.admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    monkeypatch.setattr(second_reading.services, "kit", None)  # even with no model, nothing waits for a lost page
    monkeypatch.setattr(second_reading.services, "storage", _Missing())
    monkeypatch.setattr(second_reading.services, "crypto", CaseCrypto(LocalKeyProvider.generate()))
    await second_reading.second_reading(JobContext(worker_db, job, w["case"], second_reading.KIND, 1))
    row = seed.admin.execute(
        "select status, reason from app.second_readings where page_id = %s", (w["page"],)
    ).fetchone()
    assert row == ("refused", "unreadable_picture")


async def test_a_page_with_a_picture_but_no_page_pdf_is_refused_not_crashed(
    worker_db: Database, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    w = world(seed, picture=PICTURE)
    seed.admin.execute("update app.pages set transcribed_by_expert_text = %s where id = %s", (b"x", w["page"]))
    seed.admin.execute(
        "insert into app.second_readings (case_id, page_id, requested_by, page_rotation, image_sha256)"
        " values (%s, %s, %s, 0, %s)",
        (w["case"], w["page"], w["expert"], "0" * 64),
    )
    job = seed.job(w["case"], kind=second_reading.KIND)
    seed.admin.execute("update app.jobs set status = 'running' where id = %s", (job,))
    monkeypatch.setattr(second_reading.services, "kit", object())
    monkeypatch.setattr(second_reading.services, "storage", _Missing())
    monkeypatch.setattr(second_reading.services, "crypto", CaseCrypto(LocalKeyProvider.generate()))
    await second_reading.second_reading(JobContext(worker_db, job, w["case"], second_reading.KIND, 1))
    row = seed.admin.execute("select status from app.second_readings where page_id = %s", (w["page"],)).fetchone()
    assert row is not None and row[0] in ("refused", "failed")  # never left waiting, nothing sent
