"""`page.review_duplicate`: set a page aside as a copy, or bring it back (same people as `page.rotate`)."""

from __future__ import annotations

import itertools

import psycopg

from app.core.auth.permissions import CASE_ACTIONS, CASE_ROLES, ORG_KINDS, ORG_ROLES

ACTION = "page.review_duplicate"


def test_the_code_knows_the_action_with_the_rotate_rule() -> None:
    assert CASE_ACTIONS[ACTION] == CASE_ACTIONS["page.rotate"]


def test_the_database_matrix_answers_like_rotate(admin: psycopg.Connection) -> None:
    combos = [(c, o, k) for c, o, k in itertools.product((*CASE_ROLES, None), (*ORG_ROLES, None), ORG_KINDS)]
    for case_role, org_role, kind in combos:
        sql = "select app.case_action_allowed(%s, %s, %s, %s)"
        got = admin.execute(sql, (ACTION, case_role, org_role, kind)).fetchone()
        want = admin.execute(sql, ("page.rotate", case_role, org_role, kind)).fetchone()
        assert got == want, (case_role, org_role, kind)


def test_the_duplicate_review_policy_checks_the_new_action(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select qual from pg_policies where schemaname = 'app' and tablename = 'page_analysis'"
        " and policyname = 'page_analysis_review'"
    ).fetchone()
    assert row is not None and ACTION in row[0] and "page.rotate" not in row[0]
