"""Slice O's privileged database functions (migrations 1000-1006) are for the API role only (migration 1007).

Default privileges (migration 0000) grant every new function to both `app_api` and `app_worker`. No job
of slice O's acts for a person, so the worker may run none of them; `public` none either. The API keeps
the ones it calls.
"""

from __future__ import annotations

import psycopg
import pytest

FUNCTIONS = {
    "app.is_org_admin(uuid)": True,
    "app.create_team_organisation(text, text)": True,
    "app.organisation_members(uuid)": True,
    "app.memberships_actor()": False,
    "app.invites_guard()": False,
    "app.accept_invite(text)": True,
    "app.organisation_template_exists(uuid, uuid)": True,
    "app.organisations_policy_guard()": False,
    "app.close_organisation(uuid)": True,
    "app.support_cases(uuid, text)": True,
    "app.support_case_grants(uuid)": True,
    "app.support_case_diagnostics(uuid)": True,
    "app.support_account_check(text)": False,
    "app.support_account_precheck(text)": True,
    "app.support_create_account(uuid, text, text, text)": True,
    "app.support_new_password(uuid)": True,
    "app.support_accounts_list()": True,
}


@pytest.mark.parametrize("function", sorted(FUNCTIONS))
def test_the_worker_cannot_run_slice_o_functions(admin: psycopg.Connection, function: str) -> None:
    row = admin.execute(
        "select has_function_privilege('app_worker', %s::regprocedure, 'execute'),"
        " has_function_privilege('public', %s::regprocedure, 'execute')",
        (function, function),
    ).fetchone()
    assert row == (False, False), function


@pytest.mark.parametrize("function", sorted(f for f, called in FUNCTIONS.items() if called))
def test_the_api_keeps_the_functions_it_calls(admin: psycopg.Connection, function: str) -> None:
    row = admin.execute("select has_function_privilege('app_api', %s::regprocedure, 'execute')", (function,)).fetchone()
    assert row == (True,), function
