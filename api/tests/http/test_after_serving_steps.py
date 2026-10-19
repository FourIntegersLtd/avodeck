# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Screen 19 steps from the trial slice, and the signer of a supplementary report (screen 21b).

The experts' discussion and the trial bundle are slice I's data, read through `core/contracts/trial_steps`
(never written here). The signed supplementary report names its signer and signed day.
Words and dates are made up; nothing here is a real record.
"""

from __future__ import annotations

import uuid
from datetime import date

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    Scripted,
    World,
    aconn,
    app,
    combo,
    mailer,
    model,
    ocr,
    reads,
    run_job,
    served,
    services,
    storage,
    worker_db,
    world,
)
from tests.http.test_after_serving_changes import make_change
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


def add_meeting(admin: psycopg.Connection, w: World, discussion_on: date, status: str = "draft") -> uuid.UUID:
    row = admin.execute(
        "insert into app.expert_meetings (case_id, type, discussion_on, sign_by, sign_rule_ref, send_by,"
        " send_rule_ref, status) values (%s, 'joint', %s, %s, 'r1', %s, 'r2', %s) returning id",
        (w.case, discussion_on, discussion_on, discussion_on, status),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def test_the_steps_list_the_meetings_and_no_pack_yet(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    empty = await (await client_for(served.expert)).get(served.url("after-serving/steps"))
    assert empty.status_code == 200 and empty.json() == {"meetings": [], "pack": None}
    later = add_meeting(admin, served, date(2031, 3, 20))
    first = add_meeting(admin, served, date(2031, 2, 10), "signed")
    got = (await (await client_for(served.viewer)).get(served.url("after-serving/steps"))).json()
    assert [m["id"] for m in got["meetings"]] == [str(first), str(later)], "soonest first"
    assert got["meetings"][0]["status"] == "signed" and got["meetings"][0]["discussion_on"] == "2031-02-10"
    assert got["pack"] is None


async def test_the_steps_show_the_latest_trial_pack_and_whether_it_is_stale(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    for stale in (False, True):
        admin.execute(
            "insert into app.trial_packs (case_id, sections, sha256, storage_path, page_count, stale)"
            " values (%s, '{report}', %s, 'p', 3, %s)",
            (served.case, "a" * 64, stale),
        )
    pack = (await (await client_for(served.expert)).get(served.url("after-serving/steps"))).json()["pack"]
    assert pack["stale"] is True and pack["built_on"]


async def test_the_steps_need_case_access(client_for: ClientFor, served: World) -> None:
    assert (await (await client_for(served.outsider)).get(served.url("after-serving/steps"))).status_code == 404


async def test_a_signed_supplementary_report_names_who_signed_and_the_day(
    client_for: ClientFor, admin: psycopg.Connection, worker_db: Database, model: Scripted, served: World
) -> None:
    expert = await client_for(served.expert)
    await make_change(client_for, worker_db, model, served)
    await run_job(
        worker_db, served, await expert.post(served.url("report-changes/supplementary")), "build_supplementary"
    )
    draft = (await expert.get(served.url("report-changes"))).json()["supplementary"]
    assert draft["signed_by"] is None and draft["signed_on"] is None
    signed = await expert.post(served.url(f"report/versions/{draft['id']}/sign"), json={"declaration_confirmed": True})
    assert signed.status_code == 200, signed.text
    sup = (await (await client_for(served.viewer)).get(served.url("report-changes"))).json()["supplementary"]
    name = admin.execute(
        "select p.full_name from app.people p join app.cases c on c.named_expert_id = p.id where c.id = %s",
        (served.case,),
    ).fetchone()
    assert name is not None and sup["signed_by"] == name[0] and sup["signed_by"]
    assert abs((date.fromisoformat(sup["signed_on"]) - date.today()).days) <= 1
