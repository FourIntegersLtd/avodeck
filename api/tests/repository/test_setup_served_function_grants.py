"""`app.expert_has_served` is callable only by the roles that need it (slice C review finding 12).

It is `security definer` and answers about any person, so `public` (and so `anon` and `authenticated`)
must not run it; the API and worker roles run it through the declarations guard.
"""

from __future__ import annotations

import psycopg
import pytest

FUNCTION = "app.expert_has_served(uuid)"


@pytest.mark.parametrize(("role", "allowed"), [("anon", False), ("authenticated", False), ("app_api", True),
                                               ("app_worker", True)])
def test_only_the_api_and_worker_roles_run_it(admin: psycopg.Connection, role: str, allowed: bool) -> None:
    row = admin.execute("select has_function_privilege(%s, %s, 'execute')", (role, FUNCTION)).fetchone()
    assert row == (allowed,)


def test_public_has_no_grant(admin: psycopg.Connection) -> None:
    row = admin.execute(
        "select coalesce(bool_or(a.grantee = 0), false) from pg_proc p, aclexplode(coalesce(p.proacl,"
        " acldefault('f', p.proowner))) a where p.oid = %s::regprocedure and a.privilege_type = 'EXECUTE'",
        (FUNCTION,),
    ).fetchone()
    assert row == (False,)
