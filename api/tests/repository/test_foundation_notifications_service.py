"""The notification service (architecture 10.9, behaviour index `notifications`, F33).

- `notify()` writes one in-app row per message and emails at once when the person's email choice
  for that organisation is on and not "one email a day"; a muted kind is not sent on any channel.
- Emails carry a template and the message kind only: never a case name or any case text.
- "One email a day" people get one daily email per person with a count, from a schedule keyed by date.
- A bounced address gets no more email until it changes; Today shows a banner instead (F33).
- Messages are deleted after a year by the same daily run. Only the worker may run it.
- Who may notify: a signed-in person about a case they can see (or their own organisation), or a
  job about its own case; the person told must belong to the organisation the case is in.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from datetime import date

import psycopg
import pytest

from app.core.db import Database
from app.core.email import OutboxMailer
from app.core.notifications import (
    DAILY,
    DIGEST_TEMPLATE,
    NOTIFY_TEMPLATE,
    mark_bounced,
    notify,
    send_daily,
)
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    w = {n: seed.person() for n in ("expert", "assistant", "outsider")}
    w["org"] = seed.org(kind="team", members={w["expert"]: "owner", w["assistant"]: "assistant"})
    w["other_org"] = seed.org(kind="personal", members={w["outsider"]: "owner"})
    w["case"] = seed.case(w["org"], w["expert"], members={w["assistant"]: "assistant"})
    w["outsider_case"] = seed.case(w["other_org"], w["outsider"])
    return w


def prefs(
    admin: psycopg.Connection,
    person: uuid.UUID,
    org: uuid.UUID,
    *,
    in_app: bool = True,
    email: bool = True,
    digest: bool = False,
    muted: tuple[str, ...] = (),
) -> None:
    for channel, enabled, daily in (("in_app", in_app, False), ("email", email, digest)):
        admin.execute(
            "insert into app.notification_preferences (person_id, organisation_id, channel, enabled, digest,"
            " muted_kinds) values (%s, %s, %s, %s, %s, %s)",
            (person, org, channel, enabled, daily, list(muted)),
        )


def rows(admin: psycopg.Connection, person: uuid.UUID) -> list[tuple[object, ...]]:
    return admin.execute(
        "select kind, case_id, in_app, emailed_at is not null from app.notifications where person_id = %s"
        " order by created_at",
        (person,),
    ).fetchall()


def email_of(admin: psycopg.Connection, person: uuid.UUID) -> str:
    row = admin.execute("select email from app.people where id = %s", (person,)).fetchone()
    assert row is not None
    return str(row[0])


async def test_a_message_is_shown_in_the_app_and_emailed_at_once_by_default(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        nid = await notify(
            conn, mailer, person_id=world["assistant"], organisation_id=world["org"], kind="zz.ready",
            case_id=world["case"],
        )
    assert nid is not None
    assert rows(admin, world["assistant"]) == [("zz.ready", world["case"], True, True)]
    assert len(mailer.outbox) == 1
    mail = mailer.outbox[0]
    assert (mail.to, mail.template) == (email_of(admin, world["assistant"]), NOTIFY_TEMPLATE)
    assert mail.params == {"kind": "zz.ready"}, "emails carry the kind only, never case words"


async def test_emails_never_carry_the_case_name(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute("update app.cases set label = 'Mrs Patient Zedd' where id = %s", (world["case"],))
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        await notify(conn, mailer, person_id=world["expert"], organisation_id=world["org"], kind="zz.ready",
                     case_id=world["case"])
    assert mailer.outbox and all("Zedd" not in repr(vars(m)) for m in mailer.outbox)


async def test_a_muted_kind_is_not_sent_at_all(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    prefs(admin, world["assistant"], world["org"], muted=("zz.ready",))
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"],
                            kind="zz.ready", case_id=world["case"]) is None
        assert await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"],
                            kind="zz.other", case_id=world["case"]) is not None
    assert [r[0] for r in rows(admin, world["assistant"])] == ["zz.other"]


async def test_email_only_people_get_the_email_but_no_in_app_message(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    prefs(admin, world["assistant"], world["org"], in_app=False)
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"], kind="zz.ready",
                     case_id=world["case"])
    assert rows(admin, world["assistant"]) == [("zz.ready", world["case"], False, True)]
    assert len(mailer.outbox) == 1


async def test_nothing_on_any_channel_writes_nothing(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    prefs(admin, world["assistant"], world["org"], in_app=False, email=False)
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"],
                            kind="zz.ready", case_id=world["case"]) is None
    assert rows(admin, world["assistant"]) == [] and mailer.outbox == []


async def test_one_email_a_day_sends_a_single_daily_count_once_per_date(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    prefs(admin, world["assistant"], world["org"], email=False, digest=True)
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        for kind in ("zz.ready", "zz.other"):
            await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"], kind=kind,
                         case_id=world["case"])
    assert mailer.outbox == [], "no email at once for one-email-a-day people"
    daily = OutboxMailer()
    async with worker_db.anonymous() as conn:
        await send_daily(conn, date.today(), daily)
    mine = [m for m in daily.outbox if m.to == email_of(admin, world["assistant"])]
    assert len(mine) == 1
    assert (mine[0].template, mine[0].params) == (DIGEST_TEMPLATE, {"count": "2"})
    assert all(r[3] for r in rows(admin, world["assistant"]))
    again = OutboxMailer()
    async with worker_db.anonymous() as conn:
        await send_daily(conn, date.today(), again)
    assert [m for m in again.outbox if m.to == email_of(admin, world["assistant"])] == []


async def test_a_bounced_address_gets_no_email_until_it_changes(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    address = email_of(admin, world["assistant"])
    async with api_db.anonymous() as conn:
        assert await mark_bounced(conn, address) is True
        assert await mark_bounced(conn, "nobody@example.test") is False
    mailer = OutboxMailer()
    async with api_db.request(scope_for(world["expert"])) as conn:
        assert await notify(conn, mailer, person_id=world["assistant"], organisation_id=world["org"],
                            kind="zz.ready", case_id=world["case"]) is not None
    assert mailer.outbox == []
    assert rows(admin, world["assistant"]) == [("zz.ready", world["case"], True, False)]
    admin.execute("update app.people set email = %s where id = %s", (f"new-{address}", world["assistant"]))
    row = admin.execute("select email_bounced_at from app.people where id = %s", (world["assistant"],)).fetchone()
    assert row == (None,), "a new address clears the bounce"


async def test_only_people_who_can_see_the_case_may_notify_about_it(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await notify(conn, OutboxMailer(), person_id=world["expert"], organisation_id=world["org"],
                         kind="zz.ready", case_id=world["case"])


async def test_the_person_told_must_belong_to_the_cases_organisation(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await notify(conn, OutboxMailer(), person_id=world["outsider"], organisation_id=world["org"],
                         kind="zz.ready", case_id=world["case"])
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            await notify(conn, OutboxMailer(), person_id=world["expert"], organisation_id=world["other_org"],
                         kind="zz.ready", case_id=world["case"])


async def test_a_job_may_notify_about_its_own_case_only(
    worker_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    async with worker_db.job(world["case"]) as conn:
        assert await notify(conn, OutboxMailer(), person_id=world["expert"], organisation_id=world["org"],
                            kind="zz.ready", case_id=world["case"]) is not None
    async with worker_db.job(world["case"]) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            await notify(conn, OutboxMailer(), person_id=world["outsider"], organisation_id=world["other_org"],
                         kind="zz.ready", case_id=world["outsider_case"])


async def test_kinds_are_short_codes(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        with pytest.raises(psycopg.errors.CheckViolation):
            await notify(conn, OutboxMailer(), person_id=world["expert"], organisation_id=world["org"],
                         kind="Your report for Mrs Zedd is ready", case_id=world["case"])


async def test_messages_older_than_a_year_are_deleted_by_the_daily_run(
    worker_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    old = seed.notification(world["expert"], world["org"])
    new = seed.notification(world["expert"], world["org"])
    admin.execute("update app.notifications set created_at = now() - interval '366 days' where id = %s", (old,))
    async with worker_db.anonymous() as conn:
        await send_daily(conn, date.today(), OutboxMailer())
    left = {r[0] for r in admin.execute("select id from app.notifications where id = any(%s)", ([old, new],))}
    assert left == {new}


async def test_only_the_worker_runs_the_daily_messages(api_dsn: str) -> None:
    with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select * from app.notifications_daily(current_date)")


async def test_the_daily_run_is_a_schedule() -> None:
    import app.worker.main  # noqa: F401 - registers the app's schedules
    from app.core.jobs import registry

    assert DAILY in registry.schedules
