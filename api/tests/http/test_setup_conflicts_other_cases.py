"""Screen 05: the conflict check over the expert's other cases (architecture 1178, scenario S1).

`GET /conflicts` says how many of the named expert's other cases were compared by the subject key, how many
of them matched this case's subject, and the names of the organisations they sit in. Nothing about the
matching cases is sent (no labels, no ids): the screen only says "no matches" or asks for a look.
"""

# ruff: noqa: F811  (the shared fixtures are imported from the conflicts tests)
from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.http.test_setup_conflicts_routes import app, url, world  # noqa: F401
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def key(admin: psycopg.Connection, case: uuid.UUID, value: str) -> None:
    admin.execute("insert into app.case_subject_keys (case_id, key_hash) values (%s, %s)", (case, value))


async def other_cases(client_for: ClientFor, world: dict[str, uuid.UUID]) -> dict[str, object]:
    got = await (await client_for(world["expert"])).get(url(world, "conflicts"))
    assert got.status_code == 200, got.text
    body: dict[str, object] = got.json()["other_cases"]
    return body


async def test_without_a_subject_key_nothing_was_compared(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    assert await other_cases(client_for, world) == {"checked": 0, "matches": 0, "organisations": []}


async def test_other_cases_are_counted_and_matches_are_not_named(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    mine = seed.org(kind="personal", members={world["expert"]: "owner"})
    here = seed.case(mine, world["expert"], status="active")
    elsewhere = seed.case(world["org"], world["expert"], status="active")
    deleted = seed.case(world["org"], world["expert"], status="active")
    admin.execute("update app.cases set status = 'deleted' where id = %s", (deleted,))
    someone_elses = seed.case(world["org"], world["assistant"], status="active")
    for case, value in [
        (world["case"], "a" * 64),
        (here, "b" * 64),
        (elsewhere, "b" * 64),
        (deleted, "a" * 64),
        (someone_elses, "a" * 64),
    ]:
        key(admin, case, value)
    got = await other_cases(client_for, world)
    assert got["checked"] == 2 and got["matches"] == 0
    assert len(got["organisations"]) == 2  # type: ignore[arg-type]

    admin.execute("update app.case_subject_keys set key_hash = %s where case_id = %s", ("a" * 64, here))
    again = await other_cases(client_for, world)
    assert again["checked"] == 2 and again["matches"] == 1
