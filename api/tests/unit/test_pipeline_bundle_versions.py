"""Bundle versions count the documents the expert uploaded, not the parts cut out of them."""

from __future__ import annotations

import datetime as dt
import uuid

from app.features.pipeline.repo import DocumentRow, versions_of

NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)


def row(first: int, last: int, *, part_of: uuid.UUID | None = None) -> DocumentRow:
    return DocumentRow(
        uuid.uuid4(), "records", "x.pdf", last - first + 1, first, last, None, None, 1, NOW, None, part_of
    )


def test_parts_of_an_upload_add_pages_but_are_not_counted_as_documents() -> None:
    top = row(1, 6)
    parts = [row(1, 3, part_of=top.id), row(4, 6, part_of=top.id)]
    (version,) = versions_of([top, *parts])
    assert version["documents"] == 1
