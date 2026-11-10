"""One upload has one unpack budget, shared by every zip and email opened from it at any depth (audit 12, V3a).

What does not fit is kept whole as an exhibit (never dropped), and a second upload starts with a full budget."""

from __future__ import annotations

import pytest

from app.features.pipeline import convert
from app.features.pipeline import service as pipeline_service
from tests.repository.test_pipeline_support import Harness
from tests.security import test_foundation_file_lifecycle_validation as lifecycle
from tests.support.file_lifecycle_documents import zip_of

pytestmark = pytest.mark.anyio

h = lifecycle.h  # the same harness fixtures
storage = lifecycle.storage
all_documents, exhibit_bytes, run = lifecycle.all_documents, lifecycle.exhibit_bytes, lifecycle.run

FEW = convert.Limits(max_bytes=10_000_000, max_members=6, max_depth=3)


def _fan(count: int, per_zip: int) -> bytes:
    return zip_of(
        {f"z{i}.zip": zip_of({f"f{i}-{j}.bin": bytes([i, j]) * 10 for j in range(per_zip)}) for i in range(count)}
    )


def _exhibits(h: Harness) -> int:
    row = h.admin.execute(
        "select count(*) from app.exhibits where bundle_id = (select bundle_id from app.cases where id = %s)",
        (h.case_id,),
    ).fetchone()
    return int(row[0]) if row else 0


async def test_the_member_limit_is_shared_by_the_zips_inside_one_upload(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(pipeline_service, "LIMITS", FEW)
    await run(h, _fan(3, 3), "fan.zip")  # 3 zips of 3 files: 9 files in all, the limit is 6
    names = [d["filename"] for d in all_documents(h)]
    opened = [n for n in names if n.endswith(".bin")]
    kept = [n for n in names if n.endswith(".zip") and n != "fan.zip"]
    assert len(opened) == 3, "one zip fitted; the others would have taken the upload past six files"
    assert len(kept) == 3 and len(opened) + 2 <= FEW.max_members, "the other two zips are kept whole"
    assert _exhibits(h) == len(opened) + 2, "each file that was opened is listed, and so is each zip kept whole"


async def test_a_second_upload_starts_with_a_full_budget(h: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pipeline_service, "LIMITS", FEW)
    await run(h, _fan(2, 3), "first.zip")  # 6 files: exactly the limit
    before = _exhibits(h)
    await run(h, _fan(2, 3), "second.zip")
    assert _exhibits(h) == 2 * before, "the first upload's files did not use up the second one's budget"
    assert exhibit_bytes(h) > 0
