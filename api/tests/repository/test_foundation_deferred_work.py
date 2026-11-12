"""Durable after-commit work (audit 15, BG-16): an email or a named call written inside the request's transaction and
delivered by the worker, retried, at least once.

The row holds the payload sealed under the cell key (bound to the row's id), so no address, code or link sits in the
table in plaintext (rule 6); a delivered, failed or expired row has no payload at all.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from datetime import timedelta

import psycopg
import pytest

from app.core import deferred
from app.core.db import Database
from app.core.email import Mail, OutboxMailer, spot_check_code_mail
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ADDRESS = "reviewer.zz@example.org"
CODE = "482916"


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=3)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture(autouse=True)
def _empty_table(admin: psycopg.Connection) -> None:
    """A delivery takes every due row, so each test starts with none (and the table is not shared state)."""
    admin.execute("delete from app.deferred_work")


class FailingMailer:
    def __init__(self, times: int) -> None:
        self.times = times
        self.sent: list[Mail] = []

    async def send(self, mail: Mail) -> None:
        if self.times > 0:
            self.times -= 1
            raise RuntimeError(f"boom for {mail.to}")  # a message with the address in it: it must not be kept
        self.sent.append(mail)


@contextmanager
def _call_registered(name: str, seen: list[dict[str, str]]) -> Iterator[None]:
    @deferred.call(name)
    async def handler(conn: object, args: dict[str, str]) -> None:
        seen.append(args)

    try:
        yield
    finally:
        deferred.calls.pop(name, None)


async def _defer_as(api_db: Database, person: uuid.UUID, mail: Mail, **kw: object) -> uuid.UUID:
    async with api_db.request(scope_for(person)) as conn:
        return await deferred.defer_mail(conn, mail, **kw)  # type: ignore[arg-type]


def _row(admin: psycopg.Connection, row_id: uuid.UUID) -> dict[str, object]:
    cur = admin.execute("select * from app.deferred_work where id = %s", (row_id,))
    row = cur.fetchone()
    assert row is not None and cur.description is not None
    return dict(zip([d.name for d in cur.description], row, strict=True))


async def _deliver(worker_db: Database, mailer: object) -> int:
    async with worker_db.anonymous() as conn:
        return await deferred.deliver(conn, mailer)  # type: ignore[arg-type]


async def test_the_row_holds_no_address_and_no_code_in_plaintext(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    person = seed.person()
    row_id = await _defer_as(api_db, person, spot_check_code_mail(ADDRESS, code=CODE))
    row = _row(admin, row_id)
    flat = " ".join(str(v) for v in row.values())
    assert ADDRESS not in flat and CODE not in flat and "reviewer" not in flat
    assert row["kind"] == "mail" and row["name"] == "spot_check.code" and row["status"] == "pending"
    assert row["requested_by"] == person


async def test_a_rolled_back_request_leaves_no_row(api_db: Database, admin: psycopg.Connection, seed: Seed) -> None:
    person = seed.person()
    made: list[uuid.UUID] = []
    with pytest.raises(RuntimeError):
        async with api_db.request(scope_for(person)) as conn:
            made.append(await deferred.defer_mail(conn, spot_check_code_mail(ADDRESS, code=CODE)))
            raise RuntimeError("the request failed after the mail was written")
    assert admin.execute("select count(*) from app.deferred_work where id = %s", (made[0],)).fetchone() == (0,)


async def test_the_worker_delivers_the_mail_and_wipes_the_payload(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    row_id = await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE))
    mailer = OutboxMailer()
    await _deliver(worker_db, mailer)
    assert [(m.to, m.template, m.params) for m in mailer.outbox] == [(ADDRESS, "spot_check.code", {"code": CODE})]
    assert mailer.outbox[0].idempotency_key == str(row_id)
    row = _row(admin, row_id)
    assert row["status"] == "done" and row["sealed"] is None and row["finished_at"] is not None


async def test_a_failed_send_is_kept_for_a_later_try_without_the_message(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    row_id = await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE))
    mailer = FailingMailer(times=1)
    await _deliver(worker_db, mailer)
    row = _row(admin, row_id)
    assert row["status"] == "pending" and row["attempts"] == 1 and row["sealed"] is not None
    assert row["last_error"] == "RuntimeError" and ADDRESS not in str(row["last_error"])
    await _deliver(worker_db, mailer)  # not due yet: nothing is sent
    assert mailer.sent == []
    admin.execute("update app.deferred_work set run_after = now() - interval '1 second' where id = %s", (row_id,))
    await _deliver(worker_db, mailer)
    assert [m.to for m in mailer.sent] == [ADDRESS] and _row(admin, row_id)["status"] == "done"


async def test_after_eight_failed_tries_the_row_fails_for_good_and_the_payload_goes(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    row_id = await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE))
    mailer = FailingMailer(times=99)
    for _ in range(8):
        admin.execute("update app.deferred_work set run_after = now() where id = %s", (row_id,))
        await _deliver(worker_db, mailer)
    row = _row(admin, row_id)
    assert row["status"] == "failed" and row["attempts"] == 8 and row["sealed"] is None


async def test_a_mail_past_its_expiry_is_not_sent_and_its_payload_goes(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    row_id = await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE))
    admin.execute("update app.deferred_work set expires_at = now() - interval '1 second' where id = %s", (row_id,))
    mailer = OutboxMailer()
    await _deliver(worker_db, mailer)
    row = _row(admin, row_id)
    assert mailer.outbox == [] and row["status"] == "expired" and row["sealed"] is None


async def test_a_sealed_payload_copied_to_another_row_is_not_opened(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    person = seed.person()
    first = await _defer_as(api_db, person, spot_check_code_mail(ADDRESS, code=CODE))
    second = await _defer_as(api_db, person, spot_check_code_mail("other.zz@example.org", code="111111"))
    admin.execute(
        "update app.deferred_work set sealed = (select sealed from app.deferred_work where id = %s) where id = %s",
        (first, second),
    )
    admin.execute(
        "update app.deferred_work set status = 'done', sealed = null, finished_at = now() where id = %s", (first,)
    )
    mailer = OutboxMailer()
    await _deliver(worker_db, mailer)
    assert mailer.outbox == [] and _row(admin, second)["attempts"] == 1


async def test_a_row_held_by_another_delivery_is_not_taken_twice(
    api_db: Database, worker_db: Database, seed: Seed
) -> None:
    await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE))
    first, second = OutboxMailer(), OutboxMailer()
    async with worker_db.anonymous() as holder:
        taken = await holder.execute("select id from app.take_deferred_work(50)")
        mine = await taken.fetchall()
        assert mine  # this delivery holds the rows until it ends
        await _deliver(worker_db, second)
    assert second.outbox == [] and first.outbox == []


async def test_a_named_call_runs_with_its_arguments_and_is_retried_like_mail(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    seen: list[dict[str, str]] = []
    with _call_registered("zz.remove", seen):
        async with api_db.request(scope_for(seed.person())) as conn:
            row_id = await deferred.defer_call(conn, "zz.remove", {"path": "p/1"})
        row = _row(admin, row_id)
        assert row["kind"] == "call" and row["name"] == "zz.remove" and "p/1" not in str(row.values())
        await _deliver(worker_db, OutboxMailer())
    assert seen == [{"path": "p/1"}] and _row(admin, row_id)["status"] == "done"


async def test_an_unknown_call_name_is_refused_when_it_is_written(api_db: Database, seed: Seed) -> None:
    async with api_db.request(scope_for(seed.person())) as conn:
        with pytest.raises(ValueError):
            await deferred.defer_call(conn, "zz.nobody_registered_this", {})


async def test_a_mail_that_does_not_fit_its_template_is_refused_when_it_is_written(
    api_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    before = admin.execute("select count(*) from app.deferred_work").fetchone()
    with pytest.raises(ValueError):
        await _defer_as(api_db, seed.person(), Mail(to=ADDRESS, template="spot_check.code", params={"code": "x"}))
    assert admin.execute("select count(*) from app.deferred_work").fetchone() == before


async def test_a_validity_past_a_week_is_refused(api_db: Database, seed: Seed) -> None:
    with pytest.raises(psycopg.Error):
        await _defer_as(api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE), valid_for=timedelta(days=8))


def test_the_api_role_cannot_read_or_write_the_table_directly(api_dsn: str) -> None:
    with psycopg.connect(api_dsn, autocommit=True) as conn:
        for statement in (
            "select * from app.deferred_work",
            "insert into app.deferred_work (id, kind, name, expires_at) values (gen_random_uuid(), 'mail', 'x', now())",
            "delete from app.deferred_work",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)


def test_the_api_role_cannot_take_or_finish_work(api_dsn: str) -> None:
    with psycopg.connect(api_dsn, autocommit=True) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select * from app.take_deferred_work(5)")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("select app.finish_deferred_work(gen_random_uuid(), true, null)")


async def test_finished_rows_are_pruned_after_a_week_and_pending_ones_are_not(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    person = seed.person()
    old = await _defer_as(api_db, person, spot_check_code_mail(ADDRESS, code=CODE))
    pending = await _defer_as(api_db, person, spot_check_code_mail(ADDRESS, code="222222"))
    admin.execute(
        "update app.deferred_work set status = 'done', sealed = null, finished_at = now() - interval '8 days'"
        " where id = %s",
        (old,),
    )
    async with worker_db.anonymous() as conn:
        await deferred.prune(conn)
    assert admin.execute("select id from app.deferred_work where id = any(%s)", ([old, pending],)).fetchall() == [
        (pending,)
    ]


def test_the_delivery_is_a_worker_sweep_that_runs_after_the_housekeeping_sweeps() -> None:
    from app.core.jobs import registry

    assert deferred.SWEEP in registry.database_sweeps and deferred.SWEEP not in registry.sweeps


# --- one row at a time: a slow or failing mail service must not hold the worker's other housekeeping ---------------


class SlowMailer:
    """Takes `delay` seconds per mail; `hang_for` addresses never answer; `during` runs while a mail is being sent."""

    def __init__(self, delay: float = 0.0, hang_for: str | None = None, during: object = None) -> None:
        self.delay, self.hang_for, self.during = delay, hang_for, during
        self.keys: list[str | None] = []

    async def send(self, mail: Mail) -> None:
        if callable(self.during):
            self.during()
        if mail.to == self.hang_for:
            await asyncio.sleep(60)
        await asyncio.sleep(self.delay)
        self.keys.append(mail.idempotency_key)


async def _defer_many(api_db: Database, person: uuid.UUID, count: int) -> list[uuid.UUID]:
    ids = []
    for n in range(count):
        ids.append(await _defer_as(api_db, person, spot_check_code_mail(f"r{n}.zz@example.org", code=f"{n:06d}")))
    return ids


async def test_a_mail_is_sent_while_only_its_own_row_is_held(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    await _defer_many(api_db, seed.person(), 4)
    free_while_sending: list[int] = []

    def look() -> None:
        row = admin.execute(
            "select count(*) from (select id from app.deferred_work where status = 'pending' for update skip locked) s"
        ).fetchone()
        assert row is not None
        free_while_sending.append(row[0])

    mailer = SlowMailer(during=look)
    assert await deferred.deliver_all(worker_db, mailer) == 4  # type: ignore[arg-type]
    assert free_while_sending == [3, 2, 1, 0][: len(free_while_sending)] and free_while_sending[0] == 3


async def test_two_sweeps_at_once_never_send_a_row_twice(api_db: Database, worker_db: Database, seed: Seed) -> None:
    ids = await _defer_many(api_db, seed.person(), 8)
    mailer = SlowMailer(delay=0.05)
    sent = await asyncio.gather(
        deferred.deliver_all(worker_db, mailer),  # type: ignore[arg-type]
        deferred.deliver_all(worker_db, mailer),  # type: ignore[arg-type]
    )
    assert sum(sent) == 8
    assert sorted(k for k in mailer.keys if k) == sorted(str(i) for i in ids) and len(mailer.keys) == 8


async def test_a_round_stops_at_its_time_budget_and_leaves_the_rest_for_the_next(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    await _defer_many(api_db, seed.person(), 6)
    mailer = SlowMailer(delay=0.2)
    sent = await deferred.deliver_all(worker_db, mailer, budget_seconds=0.3)  # type: ignore[arg-type]
    assert 1 <= sent <= 3
    assert admin.execute("select count(*) from app.deferred_work where status = 'pending'").fetchone() == (6 - sent,)
    assert await deferred.deliver_all(worker_db, mailer) == 6 - sent  # type: ignore[arg-type]


async def test_a_mail_that_never_answers_is_cut_off_noted_and_the_others_still_go(
    api_db: Database, worker_db: Database, admin: psycopg.Connection, seed: Seed
) -> None:
    ids = await _defer_many(api_db, seed.person(), 3)
    mailer = SlowMailer(hang_for="r0.zz@example.org")
    sent = await deferred.deliver_all(worker_db, mailer, row_limit_seconds=0.2)  # type: ignore[arg-type]
    assert sent == 2
    row = _row(admin, ids[0])
    assert row["status"] == "pending" and row["attempts"] == 1 and row["last_error"] == "TimeoutError"


async def test_with_a_failing_mail_service_the_other_sweeps_still_run_in_the_round(
    api_db: Database, worker_db: Database, worker_dsn: str, seed: Seed, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.core.notifications as notifications
    from tests.support.jobs import isolated_registry, open_worker

    await _defer_many(api_db, seed.person(), 3)
    monkeypatch.setattr(notifications, "default_mailer", lambda: SlowMailer(hang_for="r0.zz@example.org"))
    monkeypatch.setattr(deferred, "ROW_LIMIT_SECONDS", 0.2)
    registry = isolated_registry()
    order: list[str] = []
    registry.database_sweep("zz_deferred")(deferred._sweep)  # the real delivery, registered FIRST

    @registry.sweep("zz_requeue")
    async def requeue(conn: object) -> None:
        order.append("requeue")

    @registry.sweep("zz_heal")
    async def heal(conn: object) -> None:
        order.append("heal")

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await asyncio.wait_for(worker.run_sweeps(), 20) == 3
    assert order == ["requeue", "heal"]


async def test_a_database_sweep_that_fails_does_not_stop_the_others(worker_db: Database, worker_dsn: str) -> None:
    from tests.support.jobs import isolated_registry, open_worker

    registry = isolated_registry()
    ran: list[str] = []

    @registry.database_sweep("zz_bad")
    async def bad(database: Database) -> None:
        raise RuntimeError("boom, with some text")

    @registry.database_sweep("zz_good")
    async def good(database: Database) -> None:
        ran.append("good")

    async with open_worker(registry, worker_db, worker_dsn) as worker:
        assert await worker.run_sweeps() == 1
    assert ran == ["good"]
