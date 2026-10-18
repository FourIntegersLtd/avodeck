"""Slice G: the report-served handler acts as the person who just served the report.

F's `ReportServed` event names no person, and only the worker role may act as a job (migration 0027), so the
handler asks `app.after_serving_served_by` who made the serving. A serving older than ten minutes names nobody.
"""

# ruff: noqa: F811
from __future__ import annotations

import psycopg
import pytest

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    World,
    aconn,
    app,
    combo,
    mailer,
    make_version,
    model,
    ocr,
    reads,
    services,
    storage,
    world,
)

pytestmark = pytest.mark.anyio


async def test_who_served_is_told_only_for_a_serving_just_made(
    api_dsn: str, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    """The report-served handler acts as the person who served; an old serving names nobody."""
    version = await make_version(admin, aconn, world)

    def served_by() -> object:
        with psycopg.connect(api_dsn) as conn:
            row = conn.execute("select app.after_serving_served_by(%s, %s)", (world.case, version)).fetchone()
            return row[0] if row else None

    admin.execute(
        "update app.report_servings set created_by = %s, created_at = now() - interval '1 hour'"
        " where report_version_id = %s",
        (world.expert, version),
    )
    assert served_by() is None
    admin.execute("update app.report_servings set created_at = now() where report_version_id = %s", (version,))
    assert served_by() == world.expert
