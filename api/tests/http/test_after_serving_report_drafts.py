"""Slice F's final check and sign act on the main report only, never on slice G's own drafts.

After serving, G adds report versions of its own types (answers, supplementary). F's "latest draft" and
"newest version" skip those types, so a supplementary draft is never final-checked or signed through F's
routes (it would skip G's declaration and dated line), and F's last final check does not go stale because a
G version exists (`contracts.reports.AFTER_SERVING_TYPES`).
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid

import psycopg
import pytest

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    World,
    aconn,
    app,
    combo,
    mailer,
    model,
    ocr,
    reads,
    served,
    services,
    storage,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


def add_supplementary(admin: psycopg.Connection, w: World) -> uuid.UUID:
    version = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name, prev_version_id)"
        " values (%s, %s, 2, 'supplementary', 'Supplementary 1', %s)",
        (version, w.case, w.served),
    )
    return version


async def test_a_supplementary_draft_is_not_final_checked_or_signed_as_the_report(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    draft = add_supplementary(admin, served)
    expert = await client_for(served.expert)
    check = await expert.post(served.url("report/final-check"))
    assert check.status_code == 409 and check.json()["error"]["code"] == "not_built", check.text
    sign = await expert.post(served.url("report/sign"), json={"declaration_confirmed": True})
    assert sign.status_code == 409 and sign.json()["error"]["code"] == "not_built", sign.text
    row = admin.execute("select status from app.report_versions where id = %s", (draft,)).fetchone()
    assert row == ("draft",)


async def test_the_main_reports_final_check_stays_current_once_a_supplementary_exists(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    admin.execute(
        "insert into app.final_check_runs (case_id, report_version_id, results, passed) values (%s, %s, '[]', true)",
        (served.case, served.served),
    )
    add_supplementary(admin, served)
    latest = await (await client_for(served.expert)).get(served.url("report/final-check/latest"))
    assert latest.status_code == 200, latest.text
    assert latest.json()["report_version_id"] == str(served.served)
    assert latest.json()["current"] is True
