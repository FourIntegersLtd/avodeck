"""Deleting a case removes every row of it (architecture 10.9, F29; slice H runs it).

- `PURGE_ORDER` names every table in the case table registry, children before parents, and the
  database purges in the same order (`app.case_purge_order()`).
- `app.purge_case(case_id)` runs only as `app_worker` inside the case's own job. It removes the
  case's rows from every case table except `cases` (left as a tombstone with its identifying and
  free-text columns emptied) and `case_keys` (destroyed by `app.destroy_case_key()`), writes the
  case to `deletion_ledger` and returns how many rows went from each table. A second run removes
  nothing and is not an error. A held case (on hold or an active legal hold) is refused.
- Served report versions go too (rule 8: only this path may remove them); anywhere else a locked
  report version still refuses changes and deletion (`AVLCK`).
- Audit rows stay as they are: they hold ids and short codes only, and the hash chain still verifies.
- `app.replay_deletion_ledger()` (restore runbook, F29) removes again every case in the ledger that a
  restored backup brought back, and destroys its key again. Operators only.
"""

from __future__ import annotations

import uuid

import psycopg
import pytest

from app.core.audit import AuditRow, find_breaks
from app.core.contracts.case_tables import AWAITING_SLICE_MIGRATION, CASE_TABLES, PURGE_ORDER
from tests.support.case_rows import CaseWorld, fill_every_case_table
from tests.support.contracts import add_report_version
from tests.support.seed import Seed

KEPT_ROWS = ("cases", "case_keys")


def existing_tables(admin: psycopg.Connection) -> set[str]:
    rows = admin.execute(
        "select table_name from information_schema.tables where table_schema = 'app' and table_type = 'BASE TABLE'"
    ).fetchall()
    return {r[0] for r in rows}


@pytest.fixture
def world(seed: Seed) -> CaseWorld:
    expert = seed.person()
    org = seed.org(kind="team", members={expert: "owner"})
    return CaseWorld(seed.case(org, expert, status="served"), expert, org)


def add_case_key(admin: psycopg.Connection, case: uuid.UUID) -> None:
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')", (case, b"wrapped")
    )


def purge_as_worker(worker_dsn: str, job_case: uuid.UUID, case: uuid.UUID) -> dict[str, int]:
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(job_case),))
        row = conn.execute("select app.purge_case(%s)", (case,)).fetchone()
    assert row is not None
    return dict(row[0])


def rows_left(admin: psycopg.Connection, case: uuid.UUID) -> dict[str, int]:
    left: dict[str, int] = {}
    for table in sorted(set(PURGE_ORDER) & existing_tables(admin)):
        query = f"select count(*) from app.{table} where {'id' if table == 'cases' else 'case_id'} = %s"  # noqa: S608
        row = admin.execute(query, (case,)).fetchone()
        assert row is not None
        left[table] = row[0]
    return left


# --- the order ----------------------------------------------------------------------------------


def test_purge_order_names_every_case_table_once() -> None:
    assert isinstance(PURGE_ORDER, tuple)
    assert len(PURGE_ORDER) == len(set(PURGE_ORDER))
    assert set(PURGE_ORDER) == CASE_TABLES
    assert PURGE_ORDER[-1] == "cases"


def test_purge_order_removes_children_before_their_parents(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        """
        select child.relname, parent.relname
        from pg_constraint c
        join pg_class child on child.oid = c.conrelid
        join pg_class parent on parent.oid = c.confrelid
        join pg_namespace n on n.oid = child.relnamespace
        where c.contype = 'f' and n.nspname = 'app' and child.oid <> parent.oid
        """
    ).fetchall()
    position = {t: i for i, t in enumerate(PURGE_ORDER)}
    wrong = [(c, p) for c, p in rows if c in position and p in position and position[c] > position[p]]
    assert wrong == [], "PURGE_ORDER must list each child table before the table it references"


def test_the_database_purges_in_the_same_order(admin: psycopg.Connection) -> None:
    row = admin.execute("select app.case_purge_order()").fetchone()
    assert row is not None
    assert tuple(row[0]) == PURGE_ORDER


def test_every_existing_case_table_is_seeded_by_the_guard(
    admin: psycopg.Connection, seed: Seed, world: CaseWorld
) -> None:
    seeded = fill_every_case_table(admin, seed, world)
    expected = (CASE_TABLES & existing_tables(admin)) - set(KEPT_ROWS)
    missing = expected - set(seeded)
    assert not missing, f"add a seeder for {sorted(missing)} in tests/support/case_rows.py"
    assert set(AWAITING_SLICE_MIGRATION) & existing_tables(admin) <= set(seeded) | set(KEPT_ROWS)


# --- purge_case ---------------------------------------------------------------------------------


def test_purge_case_removes_every_row_of_the_case_and_nothing_else(
    admin: psycopg.Connection, seed: Seed, worker_dsn: str, world: CaseWorld
) -> None:
    seeded = fill_every_case_table(admin, seed, world)
    add_case_key(admin, world.case)
    other = CaseWorld(seed.case(world.org, world.expert, status="served"), world.expert, world.org)
    fill_every_case_table(admin, seed, other)
    before_other = rows_left(admin, other.case)

    counts = purge_as_worker(worker_dsn, world.case, world.case)

    assert counts == {t: seeded.get(t, 0) for t in PURGE_ORDER if t in seeded}
    left = rows_left(admin, world.case)
    assert {t: n for t, n in left.items() if t not in KEPT_ROWS and n} == {}
    assert left["cases"] == 1 and left["case_keys"] == 1
    assert rows_left(admin, other.case) == before_other


def test_the_case_row_is_left_as_a_tombstone(admin: psycopg.Connection, worker_dsn: str, world: CaseWorld) -> None:
    admin.execute(
        "update app.cases set subject_status = 'zz_living', party_role = 'zz_party', stage = 'zz_stage',"
        " due_date = current_date, period_start = current_date, period_end = current_date,"
        " instructing_party_type = 'zz_firm', court_track = 'zz_track' where id = %s",
        (world.case,),
    )
    purge_as_worker(worker_dsn, world.case, world.case)
    row = admin.execute(
        "select status, label, subject_status, party_role, instructing_party_type, court_track, due_date,"
        " period_start, period_end, stage, organisation_id, named_expert_id from app.cases where id = %s",
        (world.case,),
    ).fetchone()
    assert row is not None
    assert row[0] == "deleted"
    assert row[1:10] == (None,) * 9
    assert row[10:] == (world.org, world.expert)


def test_purge_case_writes_the_deletion_ledger_and_one_audit_row(
    admin: psycopg.Connection, worker_dsn: str, world: CaseWorld
) -> None:
    purge_as_worker(worker_dsn, world.case, world.case)
    purge_as_worker(worker_dsn, world.case, world.case)
    assert admin.execute("select count(*) from app.deletion_ledger where case_id = %s", (world.case,)).fetchone() == (
        1,
    )
    assert admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.purged'", (world.case,)
    ).fetchone() == (1,)


def test_a_second_purge_removes_nothing_and_is_not_an_error(
    admin: psycopg.Connection, seed: Seed, worker_dsn: str, world: CaseWorld
) -> None:
    fill_every_case_table(admin, seed, world)
    purge_as_worker(worker_dsn, world.case, world.case)
    again = purge_as_worker(worker_dsn, world.case, world.case)
    assert again and set(again.values()) == {0}


def test_only_the_cases_own_job_may_purge_it(api_dsn: str, worker_dsn: str, seed: Seed, world: CaseWorld) -> None:
    other = seed.case(world.org, world.expert)
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        purge_as_worker(worker_dsn, other, world.case)
    with psycopg.connect(worker_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.purge_case(%s)", (world.case,))
    with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select set_config('app.person_id', %s, false)", (str(world.expert),))
        conn.execute("select app.purge_case(%s)", (world.case,))


def test_a_case_on_hold_is_not_purged(admin: psycopg.Connection, worker_dsn: str, world: CaseWorld) -> None:
    admin.execute("update app.cases set on_hold = true where id = %s", (world.case,))
    with pytest.raises(psycopg.Error) as err:
        purge_as_worker(worker_dsn, world.case, world.case)
    assert err.value.sqlstate == "AV409"
    assert admin.execute("select status from app.cases where id = %s", (world.case,)).fetchone() == ("served",)


def test_a_case_under_an_active_legal_hold_is_not_purged(admin: psycopg.Connection, world: CaseWorld) -> None:
    """Slice H creates `legal_holds`; until then this builds one inside a transaction that is rolled back."""
    if "legal_holds" in existing_tables(admin):
        held = "insert into app.legal_holds (case_id, reason, set_by) values (%s, 'zz_appeal', %s)"
    else:
        held = ""
    with admin.transaction():
        if not held:
            admin.execute(
                "create table app.legal_holds (id uuid primary key default gen_random_uuid(), case_id uuid not null,"
                " reason text not null, set_by uuid not null, until date, released_at timestamptz)"
            )
            held = "insert into app.legal_holds (case_id, reason, set_by) values (%s, 'zz_appeal', %s)"
        admin.execute(held, (world.case, world.expert))
        admin.execute("select set_config('app.job_case_id', %s, true)", (str(world.case),))
        with pytest.raises(psycopg.Error) as err, admin.transaction():
            admin.execute("select app.purge_case(%s)", (world.case,))
        assert err.value.sqlstate == "AV409"
        raise psycopg.Rollback()


# --- rule 8 still holds outside purge_case ------------------------------------------------------


@pytest.mark.parametrize("status", ["locked", "served", "withdrawn"])
def test_a_locked_report_version_cannot_be_deleted_outside_purge_case(
    admin: psycopg.Connection, world: CaseWorld, status: str
) -> None:
    report = add_report_version(admin, world.case, status=status)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("delete from app.report_versions where id = %s", (report,))
    assert err.value.sqlstate == "AVLCK"
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.report_versions set display_name = 'Changed' where id = %s", (report,))
    assert err.value.sqlstate == "AVLCK"


def test_purge_case_removes_served_report_versions(
    admin: psycopg.Connection, worker_dsn: str, world: CaseWorld
) -> None:
    add_report_version(admin, world.case, status="served")
    assert purge_as_worker(worker_dsn, world.case, world.case)["report_versions"] == 1
    assert admin.execute("select count(*) from app.report_versions where case_id = %s", (world.case,)).fetchone() == (
        0,
    )


# --- audit rows ---------------------------------------------------------------------------------


def test_audit_rows_are_kept_and_the_chain_still_verifies(
    admin: psycopg.Connection, worker_dsn: str, world: CaseWorld
) -> None:
    admin.execute(
        "insert into app.audit_log (person_id, organisation_id, case_id, action, target) values (%s, %s, %s,"
        " 'case.created', %s)",
        (world.expert, world.org, world.case, f"case:{world.case}"),
    )
    purge_as_worker(worker_dsn, world.case, world.case)
    kept = admin.execute("select count(*) from app.audit_log where case_id = %s", (world.case,)).fetchone()
    assert kept is not None and kept[0] >= 2
    rows = admin.execute(
        "select seq, person_id, organisation_id, case_id, action, target, at, prev_hash, hash"
        " from app.audit_log order by seq"
    ).fetchall()
    assert find_breaks(AuditRow(*r) for r in rows) == []


# --- the deletion ledger ------------------------------------------------------------------------


def test_the_deletion_ledger_is_append_only_and_worker_insert_only(
    admin: psycopg.Connection, api_dsn: str, worker_dsn: str, world: CaseWorld
) -> None:
    with psycopg.connect(api_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select set_config('app.person_id', %s, false)", (str(world.expert),))
        conn.execute("insert into app.deletion_ledger (case_id) values (%s)", (world.case,))
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(world.case),))
        conn.execute("insert into app.deletion_ledger (case_id) values (%s)", (world.case,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from app.deletion_ledger where case_id = %s", (world.case,))
    with pytest.raises(psycopg.Error):
        admin.execute("update app.deletion_ledger set deleted_at = now() where case_id = %s", (world.case,))
    with pytest.raises(psycopg.Error):
        admin.execute("delete from app.deletion_ledger where case_id = %s", (world.case,))


def test_replaying_the_ledger_removes_a_case_a_restore_brought_back(
    admin: psycopg.Connection, seed: Seed, world: CaseWorld, worker_dsn: str
) -> None:
    fill_every_case_table(admin, seed, world)
    add_case_key(admin, world.case)
    # As after a restore: the ledger names the case but its rows and key are back.
    admin.execute("insert into app.deletion_ledger (case_id) values (%s)", (world.case,))
    row = admin.execute("select app.replay_deletion_ledger()").fetchone()
    assert row is not None and row[0] >= 1
    left = rows_left(admin, world.case)
    assert {t: n for t, n in left.items() if t not in KEPT_ROWS and n} == {}
    assert admin.execute(
        "select status, (select wrapped_key is null from app.case_keys where case_id = c.id) from app.cases c"
        " where id = %s",
        (world.case,),
    ).fetchone() == ("deleted", True)
    with psycopg.connect(worker_dsn) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select app.replay_deletion_ledger()")
