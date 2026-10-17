"""Rule 9 in the database: only the named expert signs, serves and withdraws a report version.

`report.build` (named expert or assistant) lets a person add and edit drafts. Moving a version's
status needs the action the move is: report.sign (draft → locked), report.serve (locked → served),
report.withdraw (served → withdrawn), checked for the person the restricted role acts for.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.contracts import add_report_version
from tests.support.seed import Seed

MOVES = {
    "lock": ("draft", "update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("b" * 64,)),
    "serve": ("locked", "update app.report_versions set status = 'served' where id = %s", ()),
    "withdraw": (
        "served",
        "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'zz_reason' where id = %s",
        (),
    ),
}


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    expert, assistant = seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", assistant: "assistant"})
    case = seed.case(org, expert, status="active", members={assistant: "assistant"})
    return {"expert": expert, "assistant": assistant, "case": case}


def run_as(api_dsn: str, person: uuid.UUID, statement: str, params: tuple[object, ...]) -> int:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        return conn.execute(statement, params).rowcount  # type: ignore[arg-type]


@pytest.mark.parametrize("move", sorted(MOVES))
def test_an_assistant_cannot_move_a_report_versions_status(
    move: str, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    start, statement, extra = MOVES[move]
    version = add_report_version(admin, team["case"], status=start)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        run_as(api_dsn, team["assistant"], statement, (*extra, version))
    assert admin.execute("select status from app.report_versions where id = %s", (version,)).fetchone() == (start,)


@pytest.mark.parametrize("move", sorted(MOVES))
def test_the_named_expert_can_move_a_report_versions_status(
    move: str, api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    start, statement, extra = MOVES[move]
    version = add_report_version(admin, team["case"], status=start)
    assert run_as(api_dsn, team["expert"], statement, (*extra, version)) == 1


def test_an_assistant_can_still_edit_a_draft(api_dsn: str, admin: psycopg.Connection, team: dict[str, uuid.UUID]) -> None:
    version = add_report_version(admin, team["case"])
    changed = run_as(
        api_dsn, team["assistant"], "update app.report_versions set display_name = 'Report 2' where id = %s", (version,)
    )
    assert changed == 1
