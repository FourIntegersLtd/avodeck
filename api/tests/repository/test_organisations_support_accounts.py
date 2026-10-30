"""Migrations 1012, 1013: `support_accounts`, the accounts Avodeck support created (permissions.md "Avodeck staff").

- Only current support staff see the rows, and only through `app.support_accounts_list()`; nobody else
  (a member, an organisation owner, someone no longer staff) sees any.
- The running app never writes the table directly: `app.support_create_account()` and
  `app.support_new_password()` do, for current staff only, in the staff member's own name.
- A new account's person must change their password (`people.must_change_password`, ADR 0024).
- An account is `invited` until the person has chosen their own password and completed MFA in a session,
  then `active`.
- A new temporary password is refused within a minute (`sent_recently`) and with `already_active` once the
  person has a password of their own (changed or reset from the email link) or has set up MFA, even
  before the list says `active`; it sets `must_change_password` again and signs the person out everywhere.
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


def test_an_account_is_active_once_its_person_has_their_own_password_and_mfa(
    seed: Seed, api_dsn: str, admin: psycopg.Connection
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
    assert admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone() == (True,)

    def status() -> object:
        with as_person(api_dsn, staff) as conn:
            row = conn.execute("select status from app.support_accounts_list() where person_id = %s", (person,))
            return row.fetchone()

    seed.session(person, mfa_age=None, aal="aal1")  # signed in with the password only
    assert status() == ("invited",)
    seed.session(person)  # MFA done, still on the temporary password
    assert status() == ("invited",)
    with as_person(api_dsn, person) as conn:
        conn.execute("select app.password_changed()")  # the person chose their own
    assert admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone() == (False,)
    assert status() == ("active",)


def test_a_new_temporary_password_within_a_minute_is_refused(seed: Seed, api_dsn: str) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
        found = conn.execute("select id from app.support_accounts_list() where person_id = %s", (person,)).fetchone()
    assert found is not None
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, staff) as conn:
        conn.execute("select * from app.support_new_password(%s)", (found[0],))
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


def test_a_new_temporary_password_signs_the_person_out_and_needs_changing_again(
    seed: Seed, api_dsn: str, admin: psycopg.Connection
) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
        found = conn.execute("select id from app.support_accounts_list() where person_id = %s", (person,)).fetchone()
    assert found is not None
    seed.session(person, mfa_age=None, aal="aal1")
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s", found)
    with as_person(api_dsn, staff) as conn:
        row = conn.execute("select status from app.support_new_password(%s)", found).fetchone()
    assert row == ("invited",)
    assert admin.execute(
        "select count(*) from app.sessions where person_id = %s and revoked_at is null", (person,)
    ).fetchone() == (0,)
    assert admin.execute(
        "select distinct revoked_reason from app.sessions where person_id = %s", (person,)
    ).fetchall() == [("password_renewed",)]


def test_an_active_account_gets_no_new_temporary_password(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
        found = conn.execute("select id from app.support_accounts_list() where person_id = %s", (person,)).fetchone()
    assert found is not None
    admin.execute("update app.people set must_change_password = false where id = %s", (person,))
    seed.session(person)
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s", found)
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, staff) as conn:
        conn.execute("select * from app.support_new_password(%s)", found)
    assert refused.value.diag.message_primary == "already_active"


def test_the_flag_changes_only_through_the_named_functions(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    """The app role cannot clear (or set) `must_change_password` by writing the row: only
    `app.password_changed()` (the person, after a new password) and support's functions change it."""
    person = seed.person()
    admin.execute("update app.people set must_change_password = true where id = %s", (person,))
    with pytest.raises(Refused), as_person(api_dsn, person) as conn:
        conn.execute("update app.people set must_change_password = false where id = %s", (person,))
    assert admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone() == (True,)
    with as_person(api_dsn, person) as conn:
        conn.execute("select app.password_changed()")
    assert admin.execute("select must_change_password from app.people where id = %s", (person,)).fetchone() == (False,)


def _renewable(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> tuple[uuid.UUID, tuple[uuid.UUID]]:
    staff = seed.person()
    seed.support_staff(staff)
    with as_person(api_dsn, staff) as conn:
        person = create(conn, f"{uuid.uuid4().hex[:10]}@example.test")
        found = conn.execute("select id from app.support_accounts_list() where person_id = %s", (person,)).fetchone()
    assert found is not None
    admin.execute("update app.support_accounts set last_sent_at = now() - interval '2 minutes' where id = %s", found)
    return staff, found


def test_a_password_the_person_chose_is_never_replaced(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    """A reset from the email link clears the flag with no MFA session, so the list still says `invited`:
    support still may not overwrite the person's own password (account takeover)."""
    staff, found = _renewable(seed, api_dsn, admin)
    person = admin.execute("select person_id from app.support_accounts where id = %s", found).fetchone()
    assert person is not None
    with as_person(api_dsn, person[0]) as conn:
        conn.execute("select app.password_changed()")
    with as_person(api_dsn, staff) as conn:
        assert conn.execute("select status from app.support_accounts_list() where id = %s", found).fetchone() == (
            "invited",
        )
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, staff) as conn:
        conn.execute("select * from app.support_new_password(%s)", found)
    assert refused.value.diag.message_primary == "already_active"
    assert admin.execute("select must_change_password from app.people where id = %s", person).fetchone() == (False,)


def test_once_mfa_is_set_up_the_password_is_not_replaced(seed: Seed, api_dsn: str, admin: psycopg.Connection) -> None:
    staff, found = _renewable(seed, api_dsn, admin)
    person = admin.execute("select person_id from app.support_accounts where id = %s", found).fetchone()
    assert person is not None
    seed.session(person[0])  # MFA set up, still on the temporary password
    with pytest.raises(psycopg.Error) as refused, as_person(api_dsn, staff) as conn:
        conn.execute("select * from app.support_new_password(%s)", found)
    assert refused.value.diag.message_primary == "already_active"
