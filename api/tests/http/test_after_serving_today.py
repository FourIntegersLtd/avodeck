# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Today (screen 03): slice G's "needs you" items (slice G brief).

- Written questions due: on each case where the person is the named expert, how many questions in open
  rounds have no answer yet, with the round's deadline; it opens screen 20.
- New pages to read: how many pages added since the report was served the expert has not read; screen 21.
- Other expert's report to check: how many checked claims the expert has not confirmed yet; screen 21.
Only the named expert answers and confirms, so no one else sees them. Keys and counts only (rule 6).
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    CRYPTO,
    Reads,
    World,
    aconn,
    app,
    combo,
    mailer,
    model,
    ocr,
    reads,
    served,
    services,
    storage,
    world,
)
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def items(client: Any, kind: str) -> list[dict[str, Any]]:
    got = await client.get("/v1/today")
    assert got.status_code == 200, got.status_code
    return [i for i in got.json()["needs_you"] if i["kind"] == kind]


async def others_see_nothing(client_for: ClientFor, w: World, kind: str) -> None:
    for person in (w.co_expert, w.assistant, w.viewer, w.outsider):
        assert await items(await client_for(person), kind) == []


async def test_written_questions_due_until_each_has_an_answer(
    client_for: ClientFor, admin: psycopg.Connection, aconn: AsyncConnection, served: World
) -> None:
    deadline = admin.execute(
        "insert into app.deadlines (case_id, kind, due_on, timezone, created_from, source)"
        " values (%s, 'written_question_answers', '2026-04-20', 'UTC', 'round', 'round') returning id",
        (served.case,),
    ).fetchone()
    assert deadline is not None
    rnd = admin.execute(
        "insert into app.written_question_rounds (case_id, number, received_on, deadline_id)"
        " values (%s, 1, '2026-03-16', %s) returning id",
        (served.case, deadline[0]),
    ).fetchone()
    assert rnd is not None
    for n in (1, 2):
        words = await CRYPTO.encrypt(aconn, served.case, b"Please confirm.", purpose="written_question")
        admin.execute(
            "insert into app.written_questions (case_id, round_id, number, text_verbatim) values (%s, %s, %s, %s)",
            (served.case, rnd[0], n, words),
        )
    [item] = await items(await client_for(served.expert), "after_serving_written_questions_due")
    assert item["case_id"] == str(served.case) and item["screen"] == "S20" and item["count"] == 2
    assert item["due_at"] is not None
    assert item["label_key"].startswith("after_serving.") and item["action_key"].startswith("after_serving.")
    await others_see_nothing(client_for, served, "after_serving_written_questions_due")
    admin.execute("update app.written_questions set status = 'answered' where round_id = %s", (rnd[0],))
    assert await items(await client_for(served.expert), "after_serving_written_questions_due") == []


async def test_new_pages_to_read_until_each_is_read(
    client_for: ClientFor, seed: Seed, reads: Reads, served: World
) -> None:
    document = seed.document(served.case)
    pages = [seed.page(served.case, document, n) for n in (4, 5)]
    [item] = await items(await client_for(served.expert), "after_serving_new_pages")
    assert item["screen"] == "S21" and item["count"] == 2
    await others_see_nothing(client_for, served, "after_serving_new_pages")
    for n, page in zip((4, 5), pages, strict=True):
        reads.rows.add((served.case, page, n, served.expert))
    assert await items(await client_for(served.expert), "after_serving_new_pages") == []


async def test_no_new_pages_item_on_a_case_not_served(client_for: ClientFor, world: World) -> None:
    assert await items(await client_for(world.expert), "after_serving_new_pages") == []


async def test_other_expert_claims_to_check_until_each_is_confirmed(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, served: World
) -> None:
    document = seed.document(served.case)
    admin.execute(
        "insert into app.opposing_reports (case_id, document_id, received_on) values (%s, %s, '2026-04-02')",
        (served.case, document),
    )
    claims: list[uuid.UUID] = []
    for n, status in ((1, "matches"), (2, "wrong_value"), (3, "unchecked")):
        words = await CRYPTO.encrypt(aconn, served.case, b"They wrote this.", purpose="opposing_claim")
        row = admin.execute(
            "insert into app.opposing_claims (case_id, document_id, claim_no, text_verbatim, word_ids, status)"
            " values (%s, %s, %s, %s, '{w1}', %s) returning id",
            (served.case, document, n, words, status),
        ).fetchone()
        assert row is not None
        claims.append(row[0])
    [item] = await items(await client_for(served.expert), "after_serving_opposing_to_check")
    assert item["screen"] == "S21" and item["count"] == 2, "checked claims only"
    await others_see_nothing(client_for, served, "after_serving_opposing_to_check")
    admin.execute("update app.opposing_claims set expert_confirmed = true where id = any(%s)", (claims[:2],))
    assert await items(await client_for(served.expert), "after_serving_opposing_to_check") == []
