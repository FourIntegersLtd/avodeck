"""F2: a locked or damaged PDF stops that document only (failure-modes.md).

The split step records why (`documents.unreadable_reason`: `locked` or `damaged`), numbers no pages for
that file and carries on with the rest. The bundle summary lists the file with its reason, and the
instructing-party draft asks for a readable copy of it. Files are made up here; no page text is printed.
"""

from __future__ import annotations

import io
import uuid

import pikepdf
import pytest

from tests.repository.test_pipeline_ingest import WORDS_A, WORDS_B, h, storage  # noqa: F401
from tests.repository.test_pipeline_support import Harness, text_pdf

pytestmark = pytest.mark.anyio


def locked(words: list[list[str]]) -> bytes:
    out = io.BytesIO()
    with pikepdf.open(io.BytesIO(text_pdf(words))) as pdf:
        pdf.save(out, encryption=pikepdf.Encryption(user="secret", owner="secret"))
    return out.getvalue()


DAMAGED = b"%PDF-1.4\n" + b"\x00\x01 not a document at all \xff" * 40


async def test_a_locked_and_a_damaged_file_stop_alone_and_the_rest_is_numbered(h: Harness) -> None:  # noqa: F811
    first = await h.upload(text_pdf(WORDS_A), filename="a.pdf")
    lock = await h.upload(locked(WORDS_B), filename="locked.pdf")
    broken = await h.upload(DAMAGED, filename="broken.pdf")
    last = await h.upload(text_pdf(WORDS_B), filename="b.pdf")
    finished = await h.drain()
    assert set(finished.values()) == {"done"}, finished
    assert [p["document_id"] for p in h.pages()] == [first] * 3 + [last] * 2
    reasons: dict[uuid.UUID, str | None] = dict(
        h.admin.execute("select id, unreadable_reason from app.documents where case_id = %s", (h.case_id,)).fetchall()
    )
    assert reasons == {first: None, lock: "locked", broken: "damaged", last: None}
    again = await h.enqueue("process_bundle")
    assert (await h.drain())[again] == "done"
    assert len(h.pages()) == 5, "a second run changed the numbering"


async def test_the_summary_lists_each_file_that_could_not_be_opened_with_its_reason(h: Harness) -> None:  # noqa: F811
    lock = await h.upload(locked(WORDS_B), filename="locked.pdf")
    await h.upload(text_pdf(WORDS_A), filename="a.pdf")
    await h.drain()
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert got.status_code == 200
    docs = {d["id"]: d for d in got.json()["documents"]}
    assert docs[str(lock)]["unreadable_reason"] == "locked" and docs[str(lock)]["pages"] == 0
    assert all("unreadable_reason" not in d for i, d in docs.items() if i != str(lock))


async def test_the_draft_asks_for_a_readable_copy_of_each_such_file(h: Harness) -> None:  # noqa: F811
    lock = await h.upload(locked(WORDS_B), filename="locked.pdf")
    await h.upload(text_pdf(WORDS_A), filename="a.pdf")
    await h.drain()
    got = await h.client.post(f"/v1/cases/{h.case_id}/instructing-party-requests/draft", json={})
    assert got.status_code == 200
    draft = got.json()
    assert draft["unreadable_document_ids"] == [str(lock)]
    assert draft["unreadable_document_names"] == ["locked.pdf"]
    assert "locked.pdf" in draft["body"]
    assert "a.pdf" not in draft["body"]
