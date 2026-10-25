"""Migration 1012: `support_accounts`, the accounts Avodeck support created (permissions.md "Avodeck staff").

- Only current support staff see the rows, and only through `app.support_accounts_list()`; nobody else
  (a member, an organisation owner, someone no longer staff) sees any.
- The running app never writes the table directly: `app.support_create_account()` and
  `app.support_resend_account()` do, for current staff only, in the staff member's own name.
- An account is `invited` until the person first completes MFA in a session, then `active`.
- Sending the link again is refused within a minute (`sent_recently`).
- One staff member's creates queue on a lock, so two at once cannot both pass the 20-an-hour cap.
"""

from __future__ import annotations

import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager

import psycopg
import pytest

from tests.support.seed import Seed

Refused = psycopg.errors.InsufficientPrivilege


@contextmanager
def as_person(api_dsn: str, person: uuid.UUID) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn) as conn, conn.transaction():
        conn.execute("select set_config('app.person_id', %s, true)", (str(person),))
        yield conn


def create(conn: psycopg.Connection, email: str) -> uuid.UUID:
    person = uuid.uuid4()
    row = conn.execute("select app.support_create_account(%s, %s, %s, 'zz')", (person, email, "Dr New")).fetchone()
    assert row is not None
    return person


def test_staff_create_an_account_in_their_own_name_and_see_it_listed(
    seed: Seed, api_dsn: str, admin: psycopg.Connection
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    email = f"{uuid.uuid4().hex[:10]}@example.test"
    with as_person(api_dsn, staff) as conn:
        person = create(conn, email.upper())
        listed = conn.execute("select person_id, email, status from app.support_accounts_list()").fetchall()
    assert (person, email, "invited") in listed
    assert admin.execute("select created_by from app.support_accounts where person_id = %s", (person,)).fetchone() == (
        staff,
    )
    assert admin.execute(
        "select o.kind, m.role from app.memberships m join app.organisations o on o.id = m.organisation_id"
        " where m.person_id = %s",
        (person,),
    ).fetchall() == [("personal", "owner")]


@pytest.mark.parametrize("who", ["member", "removed"])
def test_nobody_but_current_staff_creates_lists_or_reads_accounts(seed: Seed, api_dsn: str, who: str) -> None:
    person = seed.person()
    seed.org(kind="team", members={person: "owner"})
    if who == "removed":
        seed.support_staff(person, removed=True)
    with as_person(api_dsn, person) as conn:
        assert conn.execute("select count(*) from app.support_accounts_list()").fetchone() == (0,)
    with pytest.raises(psycopg.Error), as_person(api_dsn, person) as conn:
        create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
    with pytest.raises(Refused), as_person(api_dsn, person) as conn:
        conn.execute("select * from app.support_accounts").fetchall()


def test_the_app_never_writes_the_table_directly(seed: Seed, api_dsn: str) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with pytest.raises(Refused), as_person(api_dsn, staff) as conn:
        conn.execute("insert into app.support_accounts (person_id, created_by) values (%s, %s)", (staff, staff))


def test_an_account_is_active_once_its_person_has_completed_mfa(
    seed: Seed, api_dsn: str, admin: psycopg.Connection
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
    seed.session(person, mfa_age=None, aal="aal1")  # signed in with the password only
    with as_person(api_dsn, staff) as conn:
        assert conn.execute(
            "select status from app.support_accounts_list() where person_id = %s", (person,)
        ).fetchone() == ("invited",)
    seed.session(person)  # MFA done
    with as_person(api_dsn, staff) as conn:
        assert conn.execute(
            "select status from app.support_accounts_list() where person_id = %s", (person,)
        ).fetchone() == ("active",)


def test_sending_again_within_a_minute_is_refused(seed: Seed, api_dsn: str) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
        found = conn.execute("select id from app.support_accounts_list() where person_id = %s", (person,)).fetchone()
    assert found is not None
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, staff) as conn:
        conn.execute("select * from app.support_resend_account(%s)", (found[0],))
    assert refused.value.diag.message_primary == "sent_recently"


def test_two_creates_at_once_cannot_both_pass_the_hourly_cap(
    seed: Seed, api_dsn: str, admin: psycopg.Connection
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    for _ in range(19):
        admin.execute(
            "insert into app.support_accounts (person_id, created_by) values (%s, %s)", (seed.person(), staff)
        )
    result: dict[str, str] = {}

    def second() -> None:
        try:
            with as_person(api_dsn, staff) as conn:
                create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
            result["second"] = "created"
        except psycopg.Error as exc:
            result["second"] = exc.diag.message_primary or ""

    with as_person(api_dsn, staff) as first:
        create(first, f"{uuid.uuid4().hex[:10]}@example.test")  # the 20th, not yet committed
        waiting = threading.Thread(target=second)
        waiting.start()
        waiting.join(1.0)
        assert waiting.is_alive()  # queued on the staff member's lock, not counting alongside
    waiting.join(15.0)
    assert result == {"second": "too_many_accounts"}
