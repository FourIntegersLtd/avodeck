"""The record-gaps read contract (`app.core.contracts.record_gaps`): slice D asks where a record source goes
quiet without importing slice A. Until slice A registers its reader the answer is no gaps."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from app.core.contracts import record_gaps

pytestmark = pytest.mark.anyio


async def test_with_no_reader_registered_there_are_no_gaps() -> None:
    with record_gaps.reader.override(None):
        assert await record_gaps.gaps_overlapping(None, uuid.uuid4(), date(2024, 1, 1), date(2024, 6, 1)) == []  # type: ignore[arg-type]


async def test_a_registered_reader_answers() -> None:
    gap = record_gaps.RecordGap(
        source_type="zz_source", fact_kind="zz_kind", gap_start=date(2024, 2, 1), gap_end=date(2024, 4, 1)
    )

    async def read(conn: object, case_id: uuid.UUID, start: date, end: date) -> list[record_gaps.RecordGap]:
        return [gap]

    with record_gaps.reader.override(read):
        assert await record_gaps.gaps_overlapping(None, uuid.uuid4(), date(2024, 1, 1), date(2024, 6, 1)) == [gap]  # type: ignore[arg-type]
