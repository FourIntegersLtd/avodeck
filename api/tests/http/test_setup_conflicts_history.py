"""Conflict answers keep their history, and a change after declaring is never silent (review finding 7).

Every new or changed answer is copied, as it was, to the append-only `conflict_answer_history` (the
detail stays encrypted), so the answers behind each declaration can be read back by time. Saving the same
answers again adds nothing. Once the expert has declared, changing an answer marks the declaration out of
date (`changed_since_declared`, warning `setup.conflicts.warning.declare_again`) until they declare again,
which drafts a note to every current instructing party; this holds after serving too.
"""

# ruff: noqa: F811  (the shared fixtures are imported from the conflicts tests)
from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.http.test_setup_conflicts_routes import DETAIL, DETAIL_REQUIRED, all_no, app, url, world  # noqa: F401
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

WARNING = "setup.conflicts.warning.declare_again"


def history(admin: psycopg.Connection, case: uuid.UUID) -> list[tuple[str, str, bool]]:
    rows = admin.execute(
        "select question_key, answer, detail is not null from app.conflict_answer_history where case_id = %s"
        " order by recorded_at, id",
        (case,),
    ).fetchall()
    return [(r[0], r[1], r[2]) for r in rows]


def yes_to_first() -> dict[str, list[dict[str, str]]]:
    answers = all_no()["answers"]
    return {
        "answers": [
            {"key": a["key"], "answer": "yes", "detail": DETAIL} if a["key"] == DETAIL_REQUIRED else a for a in answers
        ]
    }


async def test_every_change_to_an_answer_is_kept(
    client_for: ClientFor, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    client = await client_for(world["expert"])
    assert (await client.put(url(world, "conflicts"), json=all_no())).status_code == 200
    first = history(admin, world["case"])
    assert len(first) == len(all_no()["answers"])
    assert (await client.put(url(world, "conflicts"), json=all_no())).status_code == 200
    assert history(admin, world["case"]) == first  # the same answers again add nothing

    assert (await client.put(url(world, "conflicts"), json=yes_to_first())).status_code == 200
    assert (await client.put(url(world, "conflicts"), json=yes_to_first())).status_code == 200
    rows = history(admin, world["case"])
    assert rows[: len(first)] == first
    assert rows[len(first) :] == [(DETAIL_REQUIRED, "yes", True)]
    stored = admin.execute(
        "select detail from app.conflict_answer_history where case_id = %s and detail is not null", (world["case"],)
    ).fetchone()
    assert stored is not None and DETAIL.encode() not in bytes(stored[0])


async def test_a_change_after_declaring_asks_to_declare_again(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    client = await client_for(world["expert"])
    await client.put(url(world, "conflicts"), json=all_no())
    before = (await client.get(url(world, "conflicts"))).json()
    assert before["changed_since_declared"] is False and WARNING not in before["warnings"]
    assert (await client.post(url(world, "conflicts/declare"))).status_code == 200
    assert (await client.get(url(world, "conflicts"))).json()["changed_since_declared"] is False

    await client.put(url(world, "conflicts"), json=yes_to_first())
    changed = (await client.get(url(world, "conflicts"))).json()
    assert changed["changed_since_declared"] is True and WARNING in changed["warnings"]
    assert (await client.post(url(world, "conflicts/declare"))).status_code == 200
    again = (await client.get(url(world, "conflicts"))).json()
    assert again["changed_since_declared"] is False and WARNING not in again["warnings"]


async def test_after_serving_a_change_is_not_silent_either(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID], admin: psycopg.Connection
) -> None:
    served = seed.case(world["org"], world["expert"], status="served")
    client = await client_for(world["expert"])
    base = f"/v1/cases/{served}/conflicts"
    assert (await client.put(base, json=all_no())).status_code == 200
    assert (await client.post(f"{base}/declare")).status_code == 200
    assert (await client.put(base, json=yes_to_first())).status_code == 200
    got = (await client.get(base)).json()
    assert got["changed_since_declared"] is True and WARNING in got["warnings"]
    assert len(history(admin, served)) == len(all_no()["answers"]) + 1
