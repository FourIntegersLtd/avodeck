"""Durable after-commit work (audit 15, BG-16): an email or a named call written inside the request's transaction and
delivered by the worker, retried, at least once.

The row holds the payload sealed under the cell key (bound to the row's id), so no address, code or link sits in the
table in plaintext (rule 6); a delivered, failed or expired row has no payload at all.
"""

from __future__ import annotations

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
    admin.execute("update app.deferred_work set status = 'done', sealed = null where id = %s", (first,))
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
        await _defer_as(
            api_db, seed.person(), spot_check_code_mail(ADDRESS, code=CODE), valid_for=timedelta(days=8)
        )


def test_the_api_role_cannot_read_or_write_the_table_directly(api_dsn: str) -> None:
    with psycopg.connect(api_dsn, autocommit=True) as conn:
        for statement in (
            "select * from app.deferred_work",
            "insert into app.deferred_work (id, kind, name, expires_at) values (gen_random_uuid(), 'mail', 'x', now())",
            "delete from app.deferred_work",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute(statement)  # type: ignore[call-overload]


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


def test_the_delivery_is_a_worker_sweep() -> None:
    from app.core.jobs import registry

    assert deferred.SWEEP in registry.sweeps
