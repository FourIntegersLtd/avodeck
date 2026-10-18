"""What a question asks the expert to answer (migration 0029; state-machines.md "Question").

- `answer_parts`: the parts of the question the expert answers (e.g. timing only); null means all of it.
- `outside_expertise_source`: whether the pack (`pack`) or the expert (`expert`) says the question is
  outside the expert's expertise; null when it is not.
Both can be set while the question is suggested and are locked once it is confirmed or edited, like its
wording. A newer instruction version reopens the question (back to suggested) and they can change again.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.contracts import add_instruction_version, add_question
from tests.support.seed import Seed

Refused = psycopg.errors.DatabaseError


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    return seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")


def scope(admin: psycopg.Connection, question: uuid.UUID) -> tuple[object, object]:
    row = admin.execute(
        "select answer_parts, outside_expertise_source from app.questions where id = %s", (question,)
    ).fetchone()
    assert row is not None
    return row


def test_a_new_question_has_neither(admin: psycopg.Connection, case: uuid.UUID) -> None:
    assert scope(admin, add_question(admin, case)) == (None, None)


def test_a_suggested_question_takes_both_and_the_source_is_pack_or_expert(
    admin: psycopg.Connection, case: uuid.UUID
) -> None:
    question = add_question(admin, case)
    admin.execute(
        "update app.questions set answer_parts = %s, outside_expertise_source = 'pack' where id = %s",
        (["timing"], question),
    )
    assert scope(admin, question) == (["timing"], "pack")
    admin.execute("update app.questions set outside_expertise_source = 'expert' where id = %s", (question,))
    with pytest.raises(psycopg.errors.CheckViolation), admin.transaction():
        admin.execute("update app.questions set outside_expertise_source = 'court' where id = %s", (question,))


@pytest.mark.parametrize("status", ["confirmed", "edited"])
def test_both_are_locked_once_the_question_is_confirmed(
    admin: psycopg.Connection, case: uuid.UUID, status: str
) -> None:
    question = add_question(admin, case)
    admin.execute(
        "update app.questions set answer_parts = %s, outside_expertise_source = 'pack', status = %s where id = %s",
        (["timing"], status, question),
    )
    for change in (
        "answer_parts = array['other']",
        "outside_expertise_source = 'expert'",
        "answer_parts = null",
        "outside_expertise_source = null",
    ):
        with pytest.raises(Refused), admin.transaction():
            admin.execute(f"update app.questions set {change} where id = %s", (question,))
    admin.execute(
        "update app.questions set outside_expertise = true where id = %s", (question,)
    )  # not part of the lock


def test_confirming_can_set_them_in_the_same_change(admin: psycopg.Connection, case: uuid.UUID) -> None:
    question = add_question(admin, case)
    admin.execute(
        "update app.questions set status = 'confirmed', answer_parts = %s, outside_expertise_source = 'expert'"
        " where id = %s",
        (["timing"], question),
    )
    assert scope(admin, question) == (["timing"], "expert")


def test_a_newer_instruction_version_reopens_them(admin: psycopg.Connection, case: uuid.UUID) -> None:
    first = add_instruction_version(admin, case, version_no=1)
    second = add_instruction_version(admin, case, version_no=2)
    question = add_question(admin, case, status="confirmed", instruction_version_id=first)
    admin.execute(
        "update app.questions set status = 'suggested', instruction_version_id = %s, answer_parts = %s where id = %s",
        (second, ["timing"], question),
    )
    assert scope(admin, question) == (["timing"], None)
