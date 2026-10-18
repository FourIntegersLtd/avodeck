"""Slice G, screens 19 and S29: deadlines after serving, court directions and hearings.

Acceptance (slice G brief): on `report_served`, and as a backfill for cases served before slice G existed,
the pack's written-questions deadline is created (served_on + 28 calendar days in Europe/London under
`gb-eaw`; 21 business days in the fixture jurisdiction's own timezone) with `rule_ref` from the pack; the
expert can move dates from court directions. A court direction overrides the pack date and every move is
kept with its source (S10). Adjourning a hearing links the new hearing and marks it changed (slice I's
packs for it are stale). Deadlines are dates in the court's timezone. A stay puts the case on hold.
Reminder emails carry only the deadline kind, never patient details.
"""

# ruff: noqa: F811
from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg
import pytest

from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.http import etag
from app.core.packs import END_OF_DAY
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    PAGE_TEXT,
    SERVED_ON,
    World,
    aconn,
    app,
    combo,
    mailer,
    make_version,
    model,
    ocr,
    pack_of,
    reads,
    served,
    services,
    storage,
    worker_db,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

EXPECTED_DUE = {"gb-eaw": date(2026, 3, 30), "zz-test": date(2026, 3, 31)}


def written(deadlines: list[dict[str, object]]) -> dict[str, object]:
    found = [d for d in deadlines if d["kind"] == "written_questions" and d["created_from"] == "pack"]
    assert len(found) == 1, deadlines
    return found[0]


async def test_a_case_served_before_slice_g_gets_its_pack_deadline_when_first_read(
    client_for: ClientFor, admin: psycopg.Connection, served: World
) -> None:
    client = await client_for(served.expert)
    resp = await client.get(served.url("deadlines"))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["served_on"] == SERVED_ON.isoformat()
    deadline = written(body["deadlines"])
    pack = pack_of(served.combo)
    rule = pack.deadline("written_questions", SERVED_ON)
    assert deadline["due_on"] == EXPECTED_DUE[served.combo.codes[0]].isoformat() == rule.due_on.isoformat()
    assert deadline["rule_ref"] == rule.rule_ref
    assert deadline["calendar"] == rule.calendar
    assert deadline["timezone"] == pack.timezone
    assert deadline["source"] == "pack"
    again = (await client.get(served.url("deadlines"))).json()
    assert len([d for d in again["deadlines"] if d["kind"] == "written_questions"]) == 1, "created once"
    count = admin.execute("select count(*) from app.deadlines where case_id = %s", (served.case,)).fetchone()
    assert count is not None and count[0] == len(again["deadlines"])


async def test_only_rules_counted_from_serving_become_deadlines(client_for: ClientFor, served: World) -> None:
    body = (await (await client_for(served.expert)).get(served.url("deadlines"))).json()
    from_serving = {r["key"] for r in pack_of(served.combo).deadline_rules() if r.get("from") == "report_served"}
    assert {d["kind"] for d in body["deadlines"] if d["created_from"] == "pack"} == from_serving


async def test_serving_the_report_creates_the_deadline_at_once(
    client_for: ClientFor, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: World
) -> None:
    version = await make_version(admin, aconn, world, status="locked")
    resp = await (await client_for(world.expert)).post(
        world.url(f"report/versions/{version}/serve"), json={"served_on": SERVED_ON.isoformat(), "sent_via": "email"}
    )
    assert resp.status_code == 200, resp.text
    row = admin.execute(
        "select due_on, rule_ref, created_from from app.deadlines where case_id = %s and kind = 'written_questions'",
        (world.case,),
    ).fetchone()
    assert row is not None, "the report_served handler made the deadline without anyone opening screen 19"
    assert row[0] == EXPECTED_DUE[world.combo.codes[0]] and row[2] == "pack"


async def test_an_unserved_case_has_no_deadlines_yet(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world.expert)).get(world.url("deadlines"))
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"served_on": None, "deadlines": []}


async def test_people_on_the_case_see_deadlines_and_others_get_404(client_for: ClientFor, served: World) -> None:
    for person in (served.expert, served.co_expert, served.assistant, served.viewer):
        assert (await (await client_for(person)).get(served.url("deadlines"))).status_code == 200, person
    assert (await (await client_for(served.outsider)).get(served.url("deadlines"))).status_code == 404


async def test_the_expert_or_assistant_adds_a_deadline_and_others_cannot(client_for: ClientFor, served: World) -> None:
    new = {"kind": "zz_other_kind", "due_on": "2026-05-04"}
    for person in (served.co_expert, served.viewer):
        assert (await (await client_for(person)).post(served.url("deadlines"), json=new)).status_code == 403
    assert (await (await client_for(served.outsider)).post(served.url("deadlines"), json=new)).status_code == 404
    resp = await (await client_for(served.assistant)).post(served.url("deadlines"), json=new)
    assert resp.status_code == 201, resp.text
    made = resp.json()
    assert made["created_from"] == "manual" and made["source"] == "manual" and made["due_on"] == "2026-05-04"
    assert made["timezone"] == pack_of(served.combo).timezone
    bad = await (await client_for(served.expert)).post(
        served.url("deadlines"), json={"kind": "Not A Code", "due_on": "2026-05-04"}
    )
    assert bad.status_code == 422


async def test_moving_a_deadline_keeps_every_move_with_its_source(client_for: ClientFor, served: World) -> None:
    client = await client_for(served.expert)
    deadline = written((await client.get(served.url("deadlines"))).json()["deadlines"])
    url = served.url(f"deadlines/{deadline['id']}")
    move = {"due_on": "2026-04-14", "reason": "agreed_extension"}
    assert (await client.patch(url, json=move)).status_code == 428
    assert (await client.patch(url, json=move, headers={"If-Match": etag(99)})).status_code == 412
    resp = await client.patch(url, json=move, headers={"If-Match": etag(int(str(deadline["version"])))})
    assert resp.status_code == 200, resp.text
    moved = resp.json()
    assert moved["due_on"] == "2026-04-14" and moved["source"] == "manual" and moved["created_from"] == "pack"
    assert resp.headers["etag"] == etag(moved["version"])
    changes = (await client.get(served.url(f"deadlines/{deadline['id']}/changes"))).json()["changes"]
    assert [(c["old_due_on"], c["new_due_on"], c["source"], c["reason"]) for c in changes] == [
        (None, deadline["due_on"], "pack", "served"),
        (deadline["due_on"], "2026-04-14", "manual", "agreed_extension"),
    ]
    assert changes[1]["changed_by"] == str(served.expert)
    done = await client.patch(url, json={"done": True}, headers={"If-Match": etag(moved["version"])})
    assert done.status_code == 200 and done.json()["done_at"] is not None


async def test_a_court_direction_overrides_the_pack_date_and_is_kept_as_the_source(
    client_for: ClientFor, served: World
) -> None:
    client = await client_for(served.expert)
    deadline = written((await client.get(served.url("deadlines"))).json()["deadlines"])
    direction = {
        "made_on": "2026-03-10",
        "reference": "Order 1",
        "text": "Questions by 20 April; trial window from 1 June",
        "deadlines": [{"kind": "written_questions", "due_on": "2026-04-20"}, {"kind": "trial", "due_on": "2026-06-01"}],
    }
    for person in (served.co_expert, served.viewer):
        assert (
            await (await client_for(person)).post(served.url("court-directions"), json=direction)
        ).status_code == 403
    resp = await client.post(served.url("court-directions"), json=direction)
    assert resp.status_code == 201, resp.text
    made = resp.json()
    assert len(made["created_deadline_ids"]) == 1, "the trial date is new; the questions date moved"
    after = {d["kind"]: d for d in (await client.get(served.url("deadlines"))).json()["deadlines"]}
    assert after["written_questions"]["due_on"] == "2026-04-20" and after["written_questions"]["source"] == "direction"
    assert after["written_questions"]["id"] == deadline["id"], "the pack deadline moved, not a second one"
    assert after["trial"]["created_from"] == "direction" and after["trial"]["id"] == made["created_deadline_ids"][0]
    changes = (await client.get(served.url(f"deadlines/{deadline['id']}/changes"))).json()["changes"]
    assert changes[-1]["source"] == "direction" and changes[-1]["source_id"] == made["id"]
    listed = (await client.get(served.url("court-directions"))).json()["directions"]
    assert [(d["id"], d["reference"], d["text"]) for d in listed] == [(made["id"], "Order 1", direction["text"])]


async def test_moving_a_deadline_from_a_direction_names_the_direction(client_for: ClientFor, served: World) -> None:
    client = await client_for(served.expert)
    deadline = written((await client.get(served.url("deadlines"))).json()["deadlines"])
    made = (await client.post(served.url("court-directions"), json={"made_on": "2026-03-11"})).json()
    resp = await client.patch(
        served.url(f"deadlines/{deadline['id']}"),
        json={"due_on": "2026-04-21", "reason": "court_direction", "direction_id": made["id"]},
        headers={"If-Match": etag(int(str(deadline["version"])))},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["source"] == "direction"
    changes = (await client.get(served.url(f"deadlines/{deadline['id']}/changes"))).json()["changes"]
    assert changes[-1]["source_id"] == made["id"]
    other = await client.patch(
        served.url(f"deadlines/{deadline['id']}"),
        json={"due_on": "2026-04-22", "reason": "court_direction", "direction_id": str(uuid.uuid4())},
        headers={"If-Match": etag(resp.json()["version"])},
    )
    assert other.status_code == 422


async def test_adjourning_a_hearing_links_the_new_hearing_and_moves_its_deadlines(
    client_for: ClientFor, served: World
) -> None:
    client = await client_for(served.expert)
    hearing = await client.post(
        served.url("hearings"), json={"type": "trial", "starts_on": "2026-06-01", "ends_on": "2026-06-03"}
    )
    assert hearing.status_code == 201, hearing.text
    first = hearing.json()
    tied = await client.post(
        served.url("deadlines"), json={"kind": "trial_papers", "due_on": "2026-05-18", "hearing_id": first["id"]}
    )
    assert tied.status_code == 201, tied.text
    url = served.url(f"hearings/{first['id']}")
    adjourn = {"status": "adjourned", "starts_on": "2026-09-07", "ends_on": "2026-09-09"}
    assert (await client.patch(url, json=adjourn)).status_code == 428
    resp = await client.patch(url, json=adjourn, headers={"If-Match": etag(first["version"])})
    assert resp.status_code == 200, resp.text
    old = resp.json()
    assert old["status"] == "adjourned" and old["replaced_by_hearing_id"] is not None
    assert old["changed_at"] is not None, "slice I reads changed_at to mark its packs for this hearing stale"
    hearings = {h["id"]: h for h in (await client.get(served.url("hearings"))).json()["hearings"]}
    new = hearings[old["replaced_by_hearing_id"]]
    assert (new["status"], new["starts_on"], new["ends_on"]) == ("scheduled", "2026-09-07", "2026-09-09")
    moved = next(
        d for d in (await client.get(served.url("deadlines"))).json()["deadlines"] if d["kind"] == "trial_papers"
    )
    assert moved["hearing_id"] == new["id"]
    assert moved["due_on"] == (date(2026, 5, 18) + (date(2026, 9, 7) - date(2026, 6, 1))).isoformat()
    changes = (await client.get(served.url(f"deadlines/{moved['id']}/changes"))).json()["changes"]
    assert changes[-1]["reason"] == "adjourned"
    again = await client.patch(url, json={"status": "heard"}, headers={"If-Match": etag(old["version"])})
    assert again.status_code == 409, "a finished hearing does not change"


async def test_adjourning_needs_the_new_dates(client_for: ClientFor, served: World) -> None:
    client = await client_for(served.expert)
    first = (
        await client.post(
            served.url("hearings"), json={"type": "trial", "starts_on": "2026-06-01", "ends_on": "2026-06-01"}
        )
    ).json()
    resp = await client.patch(
        served.url(f"hearings/{first['id']}"),
        json={"status": "adjourned"},
        headers={"If-Match": etag(first["version"])},
    )
    assert resp.status_code == 422


async def test_deadlines_are_court_dates_that_clock_changes_cannot_move(client_for: ClientFor, served: World) -> None:
    pack = pack_of(served.combo)
    tz = ZoneInfo(pack.timezone)
    client = await client_for(served.expert)
    for day in ("2026-03-28", "2026-03-29", "2026-03-30", "2026-11-01"):
        resp = await client.post(served.url("deadlines"), json={"kind": "zz_clock_check", "due_on": day})
        assert resp.status_code == 201, resp.text
        made = resp.json()
        assert made["due_on"] == day and made["timezone"] == pack.timezone
        due_at = datetime.fromisoformat(made["due_at"])
        assert due_at == datetime.combine(date.fromisoformat(day), END_OF_DAY, tzinfo=tz).astimezone(UTC)
        assert due_at.astimezone(tz).date() == date.fromisoformat(day)


async def test_a_stay_puts_the_case_on_hold(client_for: ClientFor, admin: psycopg.Connection, served: World) -> None:
    resp = await (await client_for(served.expert)).post(
        served.url("court-directions"), json={"made_on": "2026-03-12", "reference": "Stay", "stay": True}
    )
    assert resp.status_code == 201, resp.text
    row = admin.execute("select on_hold from app.cases where id = %s", (served.case,)).fetchone()
    assert row is not None and row[0] is True
    audit = admin.execute(
        "select action from app.audit_log where case_id = %s and action = 'case.held'", (served.case,)
    ).fetchall()
    assert len(audit) == 1


async def test_reminders_three_days_ahead_carry_only_the_deadline_kind(
    client_for: ClientFor, admin: psycopg.Connection, served: World, worker_db: Database, mailer: OutboxMailer
) -> None:
    from app.core.jobs import registry

    client = await client_for(served.expert)
    deadline = written((await client.get(served.url("deadlines"))).json()["deadlines"])
    due = date.fromisoformat(str(deadline["due_on"]))
    handler = registry.schedules["after_serving_deadline_reminders"]
    async with worker_db.anonymous() as conn:
        await handler(conn, due - timedelta(days=4))
    assert mailer.outbox == [], "four days ahead is too early"
    async with worker_db.anonymous() as conn:
        await handler(conn, due - timedelta(days=3))
    mine = [m for m in mailer.outbox if m.params.get("kind") == "deadline.written_questions"]
    assert len(mine) == 1
    assert mine[0].params == {"kind": "deadline.written_questions"}
    text = repr(mine[0])
    for words in PAGE_TEXT.values():
        for word in words.split():
            if len(word) > 3:
                assert word not in text
    async with worker_db.anonymous() as conn:
        await handler(conn, due - timedelta(days=3))
    assert len([m for m in mailer.outbox if m.params.get("kind") == "deadline.written_questions"]) == 1, "once"
    row = admin.execute("select last_reminded_on from app.deadlines where id = %s", (deadline["id"],)).fetchone()
    assert row is not None and row[0] == due - timedelta(days=3)
