"""Withdrawing from a served case leaves its served reports locked (slice C review finding 9, rule 8).

The case moves to `withdrawn`; the served `report_versions` row keeps its status and still refuses any
change (SQLSTATE `AVLCK`). Deadlines are owned by the slices that set them and read the case status;
nothing is cancelled here (documented gap in docs/slices/C-setup.md).
"""

# ruff: noqa: F811  (the shared fixtures are imported from the conflicts tests)
from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.db import Database
from tests.http.test_setup_conflicts_routes import app, world  # noqa: F401
from tests.support.contracts import add_report_version
from tests.support.db import ClientFor, scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


async def test_a_withdrawn_case_keeps_its_served_report_locked(
    client_for: ClientFor, seed: Seed, world: dict[str, uuid.UUID], admin: psycopg.Connection, api_db: Database
) -> None:
    served = seed.case(world["org"], world["expert"], status="served")
    report = add_report_version(admin, served, status="served")
    client = await client_for(world["expert"])
    assert (await client.post(f"/v1/cases/{served}/withdraw", json={"reason": "Ill health."})).status_code == 200

    assert admin.execute("select status from app.report_versions where id = %s", (report,)).fetchone() == ("served",)
    with pytest.raises(psycopg.Error) as caught:
        async with api_db.request(scope_for(world["expert"])) as conn:
            await conn.execute("update app.report_versions set display_name = 'Changed' where id = %s", (report,))
    assert caught.value.sqlstate in ("AVLCK", "42501")
    with pytest.raises(psycopg.Error) as by_admin:
        admin.execute("update app.report_versions set display_name = 'Changed' where id = %s", (report,))
    assert by_admin.value.sqlstate == "AVLCK"
