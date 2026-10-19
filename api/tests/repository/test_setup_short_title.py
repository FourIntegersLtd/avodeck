"""Migration 0395 (slice C): a question's short title is a label of at most 40 characters."""

from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.support.seed import Seed


def test_short_title_is_a_nullable_label_of_at_most_forty_characters(admin_dsn: str, seed: Seed) -> None:
    person = seed.person()
    org = seed.org(kind="personal", members={person: "owner"})
    case = seed.case(org, person, status="active")
    insert = (
        "insert into app.questions (case_id, number, text_verbatim, kind, short_title) values (%s, %s, 'Q', 'x', %s)"
    )
    with psycopg.connect(admin_dsn, autocommit=True) as conn:
        conn.execute(insert, (case, 1, None))
        conn.execute(insert, (case, 2, "x" * 40))
        for bad in ("", "x" * 41):
            with pytest.raises(psycopg.errors.CheckViolation):
                conn.execute(insert, (case, 3, bad))
    assert isinstance(case, uuid.UUID)
