"""Tenant isolation at the database layer (AUDIT-11).

Every statement here runs as the restricted `app_api` role (or `app_worker`) with `app.person_id` set to an
ATTACKER: a named expert of one team organisation who owns nothing in the VICTIM team organisation. Each
test asserts the SAFE outcome. Statements run inside the connection's open transaction, each in its own
savepoint, and the transaction is rolled back after every test, so nothing the attacker manages to write
persists. A test the system fails is marked `xfail(strict=True, reason="AUDIT-11 ...")` only after the same
statement was shown to succeed for the legitimate owner and the rows were inspected.

The world is built by `Seed` and `fill_every_case_table` (as the local admin role), with its own people and
organisations, so these tests share nothing with the HTTP-route tests. Nothing here resembles a record.
"""

from __future__ import annotations

import os
import re
import uuid
from collections import UserDict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import sql

from tests.support.case_rows import CaseWorld, fill_every_case_table
from tests.support.seed import Seed

if not os.environ.get("TEST_DATABASE_ADMIN_URL"):  # no fallback to a repo-local port: it may be a founder database
    pytest.skip("TEST_DATABASE_ADMIN_URL is not set", allow_module_level=True)

INSUFFICIENT_PRIVILEGE = "42501"
REPO = Path(__file__).resolve().parents[3]

# --- the catalogue of tables --------------------------------------------------------------------------------------
# The table NAMES come from the migration files (no connection is made while pytest collects); the columns and
# privileges are read from the database on first use, and a test checks that the two agree, so a new table is
# covered at once and a missing migration is noticed.


@dataclass(frozen=True)
class TableInfo:
    name: str
    columns: frozenset[str]
    api_update_column: str | None
    worker_update_column: str | None


def _migration_tables() -> list[str]:
    created: set[str] = set()
    for path in sorted((REPO / "supabase" / "migrations").glob("*.sql")):
        text = path.read_text()
        created |= set(re.findall(r"create\s+table\s+(?:if\s+not\s+exists\s+)?app\.(\w+)", text, re.I))
        created -= set(re.findall(r"drop\s+table\s+(?:if\s+exists\s+)?app\.(\w+)", text, re.I))
    return sorted(created)


def _catalogue() -> dict[str, TableInfo]:
    with psycopg.connect(os.environ["TEST_DATABASE_ADMIN_URL"], autocommit=True, connect_timeout=20) as conn:
        rows = conn.execute(
            """
            select c.relname, a.attname,
                   has_column_privilege('app_api', c.oid, a.attnum, 'UPDATE'),
                   has_column_privilege('app_worker', c.oid, a.attnum, 'UPDATE')
            from pg_class c
            join pg_namespace n on n.oid = c.relnamespace
            join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
            where n.nspname = 'app' and c.relkind in ('r', 'p')
            order by c.relname, a.attnum
            """
        ).fetchall()
    cols: dict[str, list[tuple[str, bool, bool]]] = {}
    for table, column, api_ok, worker_ok in rows:
        cols.setdefault(table, []).append((column, api_ok, worker_ok))
    out: dict[str, TableInfo] = {}
    for table, entries in cols.items():
        out[table] = TableInfo(
            name=table,
            columns=frozenset(c for c, _, _ in entries),
            api_update_column=next((c for c, ok, _ in entries if ok), None),
            worker_update_column=next((c for c, _, ok in entries if ok), None),
        )
    return out


class _LazyCatalogue(UserDict[str, TableInfo]):
    """The catalogue, read from the database the first time a test looks at it (never at collection)."""

    def __init__(self) -> None:
        super().__init__()
        self._loaded = False

    def _load(self) -> None:
        if not self._loaded:
            self._loaded = True
            self.data.update(_catalogue())

    def __getitem__(self, key: str) -> TableInfo:
        self._load()
        return super().__getitem__(key)

    def __iter__(self) -> Iterator[str]:
        self._load()
        return super().__iter__()

    def __len__(self) -> int:
        self._load()
        return super().__len__()

    def __contains__(self, key: object) -> bool:
        self._load()
        return super().__contains__(key)


CATALOGUE = _LazyCatalogue()
MIGRATION_TABLES = _migration_tables()

PERSON_COLUMNS = ("person_id", "expert_id", "invited_by", "accepted_by", "recorded_by")
# Tables whose only person key is a column that other tables use for something else. `deferred_work` (0073) is reached
# only by definer functions (the app roles hold no grant), so every command the cases run is refused or sees no row.
PERSON_KEYED_BY_TABLE = {"deferred_work": "requested_by"}
# `id` is the tenant key of these tables themselves.
ID_KEYED = {"cases": "case", "organisations": "org", "bundles": "bundle", "people": "people"}


@dataclass(frozen=True)
class Tenant:
    org: uuid.UUID
    owner: uuid.UUID
    admin: uuid.UUID
    expert: uuid.UUID
    assistant: uuid.UUID
    viewer: uuid.UUID
    case: uuid.UUID
    bundle: uuid.UUID
    people: list[uuid.UUID]
    pages: list[uuid.UUID]
    invoices: list[uuid.UUID]
    documents: list[uuid.UUID]
    invite_email: str | None = None
    invite_token_hash: str | None = None
    jobs: list[uuid.UUID] = field(default_factory=list)
    profile: uuid.UUID | None = None
    expert_email: str = ""


@dataclass(frozen=True)
class World:
    victim: Tenant
    attacker: Tenant
    outsider: uuid.UUID


def _unique_hash(owner: object, tag: str) -> str:
    """A 64-character hex string that differs per owner, for unique token columns."""
    return (str(owner).replace("-", "") + tag * 64)[:64]


def _seed_person_and_org_rows(
    admin: psycopg.Connection, seed: Seed, owner: uuid.UUID, expert: uuid.UUID, org: uuid.UUID, case: uuid.UUID
) -> None:
    """Rows in the person- and organisation-keyed tables that `fill_every_case_table` leaves empty."""
    seed.session(expert)
    seed.session(owner)
    admin.execute(
        "insert into app.audit_log (person_id, organisation_id, case_id, action, target)"
        " values (%s, %s, %s, 'zz.seed', 'zz:x')",
        (expert, org, case),
    )
    admin.execute(
        "insert into app.deferred_work (id, kind, name, sealed, requested_by, expires_at)"
        " values (%s, 'mail', 'zz_mail', 'av1:zz', %s, now() + interval '1 day')",
        (uuid.uuid4(), expert),
    )  # person-keyed by `requested_by` (PERSON_KEYED_BY_TABLE); the app roles hold no grant on it
    admin.execute("insert into app.experts (person_id) values (%s) on conflict do nothing", (expert,))
    admin.execute(
        "insert into app.signatures (expert_id, method, storage_path, sha256) values (%s, 'drawn', 'zz/sig', %s)",
        (expert, "0" * 64),
    )
    admin.execute(
        "insert into app.report_templates (owner_type, owner_id, filename, storage_path, sha256, size_bytes, version)"
        " values ('person', %s, 'zz.docx', 'zz/t', %s, 10, 1)",
        (expert, "0" * 64),
    )
    admin.execute(
        "insert into app.notification_preferences (person_id, organisation_id, channel) values (%s, %s, 'email')",
        (expert, org),
    )
    admin.execute(
        "insert into app.email_changes (person_id, new_email, old_token_hash, new_token_hash)"
        " values (%s, 'zz-new@example.test', %s, %s)",
        (expert, _unique_hash(expert, "1"), _unique_hash(expert, "2")),
    )
    admin.execute(
        "insert into app.mfa_recovery_codes (person_id, code_hash) values (%s, %s)", (expert, _unique_hash(expert, "3"))
    )
    admin.execute(
        "insert into app.legal_agreements (person_id, organisation_id, document, version)"
        " values (%s, %s, 'terms', 'v1')",
        (expert, org),
    )
    admin.execute(
        "insert into app.expert_roles (expert_id, specialty, role_title, period)"
        " values (%s, 'zz-specialty', 'Zz', daterange('2020-01-01', '2021-01-01'))",
        (expert,),
    )
    admin.execute("insert into app.expert_training (expert_id, title) values (%s, 'Zz course')", (expert,))
    admin.execute("insert into app.check_config (person_id, check_key) values (%s, 'zz_check')", (expert,))
    page = admin.execute(
        "select p.id from app.pages p join app.cases c on c.bundle_id = p.bundle_id where c.id = %s limit 1", (case,)
    ).fetchone()
    if page is not None:
        admin.execute(
            "insert into app.page_notes (page_id, person_id, bookmarked) values (%s, %s, true)", (page[0], expert)
        )
        admin.execute("insert into app.page_turns (page_id, degrees, person_id) values (%s, 90, %s)", (page[0], expert))
        admin.execute(
            "insert into app.word_corrections (page_id, word_ids, text, person_id) values (%s, array['w1'], %s, %s)",
            (page[0], b"sealed", expert),
        )
    # A live grant to one particular OTHER staff member (a grant with no `granted_to` opens the case to every staff
    # member, which would make the "unrelated staff" tests below meaningless).
    granted_staff = seed.person()
    seed.org(kind="personal", members={granted_staff: "owner"})
    seed.support_staff(granted_staff)
    admin.execute(
        "insert into app.support_access_grants (case_id, granted_by, granted_to, reason, expires_at)"
        " values (%s, %s, %s, 'support_request', now() + interval '1 hour')",
        (case, expert, granted_staff),
    )


def _seed_late_rows(
    admin: psycopg.Connection, seed: Seed, owner: uuid.UUID, expert: uuid.UUID, org: uuid.UUID, case: uuid.UUID
) -> None:
    """Rows in the tables `fill_every_case_table` does not reach (payments, custody, attempt counters ...)."""
    invoice = admin.execute("select id from app.invoices where case_id = %s limit 1", (case,)).fetchone()
    if invoice is not None:
        admin.execute(
            "insert into app.invoice_payments (invoice_id, amount, recorded_by) values (%s, 1, %s)",
            (invoice[0], expert),
        )
    admin.execute("insert into app.mfa_attempts (person_id) values (%s)", (expert,))
    admin.execute("insert into app.password_change_attempts (person_id) values (%s)", (expert,))
    admin.execute(
        "insert into app.rate_limit_hits (person_id, endpoint, window_start, hits) values (%s, 'zz', now(), 1)",
        (expert,),
    )
    admin.execute("insert into app.deletion_ledger (case_id) values (%s)", (case,))
    admin.execute(
        "insert into app.feature_flags (key, scope, scope_id, enabled) values ('zz_flag', 'organisation', %s, true)",
        (org,),
    )
    admin.execute("insert into app.support_accounts (person_id, created_by) values (%s, %s)", (expert, owner))


def _seed_support_and_replacement_rows(
    admin: psycopg.Connection, seed: Seed, expert: uuid.UUID, viewer: uuid.UUID, org: uuid.UUID, case: uuid.UUID
) -> None:
    """Rows in the support and replacement tables, so these tests have something to protect there.
    The viewer is listed as support staff who has been REMOVED (a removed row gives no access), and is the
    recorder of a REVOKED custody transfer to the organisation; one job has been resumed by support; a second
    case in the organisation replaces this one."""
    seed.support_staff(viewer, removed=True)
    seed.custody_transfer(case, viewer, to_organisation=org, revoked=True)
    job = admin.execute("select id from app.jobs where case_id = %s limit 1", (case,)).fetchone()
    assert job is not None
    admin.execute(
        "insert into app.job_support_actions (job_id, action, reason_code, from_status, attempts, person_id)"
        " values (%s, 'resume', 'fault_fixed', 'paused', 1, %s)",
        (job[0], viewer),
    )
    replacement = seed.case(org, expert, status="active")
    admin.execute(
        "insert into app.case_replacements (case_id, replaces_case_id, instructing_party_consented, recorded_by)"
        " values (%s, %s, true, %s)",
        (replacement, case, expert),
    )


def _build_tenant(admin: psycopg.Connection, seed: Seed, *, with_invite: bool) -> Tenant:
    owner, admin_p, expert, assistant, viewer = (seed.person() for _ in range(5))
    org = seed.org(
        members={owner: "owner", admin_p: "admin", expert: "expert", assistant: "assistant", viewer: "viewer"}
    )
    case = seed.case(org, expert, status="active", members={assistant: "assistant", viewer: "viewer"})
    admin.execute(  # before the case serves a report: declarations are fixed after that
        "insert into app.declarations (expert_id, jurisdiction, kind) values (%s, 'zz-test', 'zz_kind')", (expert,)
    )
    fill_every_case_table(admin, seed, CaseWorld(case, expert, org))
    admin.execute(
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%s, %s, 'zz-key')", (case, b"wrapped")
    )
    _seed_late_rows(admin, seed, owner, expert, org, case)
    _seed_support_and_replacement_rows(admin, seed, expert, viewer, org, case)
    invite_email = None
    invite_token = None
    if with_invite:
        invite_email = f"invitee-{uuid.uuid4().hex[:10]}@example.test"
        invite_token = uuid.uuid4().hex * 2
        admin.execute(
            "insert into app.invites (organisation_id, email, role, invited_by, token_hash, expires_at)"
            " values (%s, %s, 'viewer', %s, %s, now() + interval '7 days')",
            (org, invite_email, owner, invite_token),
        )
    bundle = admin.execute("select bundle_id from app.cases where id = %s", (case,)).fetchone()
    assert bundle is not None
    _seed_person_and_org_rows(admin, seed, owner, expert, org, case)
    people = {
        r[0]
        for r in admin.execute(
            "select person_id from app.memberships where organisation_id = %s"
            " union select person_id from app.case_members where case_id = %s"
            " union select named_expert_id from app.cases where id = %s",
            (org, case, case),
        ).fetchall()
    }
    pages = [r[0] for r in admin.execute("select id from app.pages where bundle_id = %s", (bundle[0],)).fetchall()]
    docs = [r[0] for r in admin.execute("select id from app.documents where bundle_id = %s", (bundle[0],)).fetchall()]
    invoices = [r[0] for r in admin.execute("select id from app.invoices where case_id = %s", (case,)).fetchall()]
    jobs = [r[0] for r in admin.execute("select id from app.jobs where case_id = %s", (case,)).fetchall()]
    profile = admin.execute("select id from app.billing_profiles where owner_id = %s", (expert,)).fetchone()
    expert_email = admin.execute("select email from app.people where id = %s", (expert,)).fetchone()
    assert expert_email is not None
    return Tenant(
        org=org,
        owner=owner,
        admin=admin_p,
        expert=expert,
        assistant=assistant,
        viewer=viewer,
        case=case,
        bundle=bundle[0],
        people=sorted(people),
        pages=pages,
        invoices=invoices,
        documents=docs,
        jobs=jobs,
        invite_email=invite_email,
        invite_token_hash=invite_token,
        profile=profile[0] if profile else None,
        expert_email=expert_email[0],
    )


@pytest.fixture(scope="module")
def world(admin: psycopg.Connection) -> World:
    seed = Seed(admin)
    victim = _build_tenant(admin, seed, with_invite=True)
    attacker = _build_tenant(admin, seed, with_invite=False)
    outsider = seed.person()
    seed.org(kind="personal", members={outsider: "owner"})
    return World(victim=victim, attacker=attacker, outsider=outsider)


@pytest.fixture(scope="module")
def _api_connection(api_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(api_dsn, connect_timeout=20) as conn:
        yield conn


def _ensure_slot_row(admin: psycopg.Connection, v: Tenant) -> None:
    """tests/conftest.py (`_empty_slot_pool`, autouse) empties app.ai_request_slots before EVERY test, so the row the
    world was built with is gone by the time a test runs. Put one back for the victim's case and job."""
    if not v.jobs:
        return
    admin.execute(
        "insert into app.ai_request_slots (job_id, case_id)"
        " select %s, %s where not exists (select 1 from app.ai_request_slots where case_id = %s)",
        (v.jobs[0], v.case, v.case),
    )


@pytest.fixture
def api(_api_connection: psycopg.Connection, admin: psycopg.Connection, world: World) -> Iterator[psycopg.Connection]:
    """The restricted API role's connection; whatever a test did is rolled back afterwards."""
    _ensure_slot_row(admin, world.victim)
    _api_connection.rollback()
    yield _api_connection
    _api_connection.rollback()


@pytest.fixture(scope="module")
def _worker_connection(worker_dsn: str) -> Iterator[psycopg.Connection]:
    with psycopg.connect(worker_dsn, connect_timeout=20) as conn:
        yield conn


@pytest.fixture
def worker(
    _worker_connection: psycopg.Connection, admin: psycopg.Connection, world: World
) -> Iterator[psycopg.Connection]:
    _ensure_slot_row(admin, world.victim)
    _worker_connection.rollback()
    yield _worker_connection
    _worker_connection.rollback()


@dataclass(frozen=True)
class Outcome:
    rowcount: int
    rows: list[tuple[Any, ...]]
    error: str | None  # SQLSTATE when the statement was refused

    @property
    def refused(self) -> bool:
        return self.error is not None


def as_person(conn: psycopg.Connection, person: uuid.UUID | None) -> None:
    conn.execute("select set_config('app.person_id', %s, true)", ("" if person is None else str(person),))


def as_job_case(conn: psycopg.Connection, case: uuid.UUID | None) -> None:
    conn.execute("select set_config('app.job_case_id', %s, true)", ("" if case is None else str(case),))


def run(conn: psycopg.Connection, query: str | sql.Composable, params: Any = None) -> Outcome:
    """Run one statement in its own savepoint; a refusal leaves the surrounding transaction usable."""
    try:
        with conn.transaction():
            cur = conn.execute(query, params)  # type: ignore[arg-type]
            rows = cur.fetchall() if cur.description else []
            return Outcome(cur.rowcount, rows, None)
    except psycopg.Error as exc:
        return Outcome(0, [], exc.sqlstate or "?")


def victim_filter(info: TableInfo, v: Tenant) -> tuple[sql.Composable, dict[str, Any]] | None:
    """`where` clause that selects the VICTIM's rows of a table from literal ids (so it holds under RLS)."""
    params: dict[str, Any] = {
        "case": v.case,
        "bundle": v.bundle,
        "org": v.org,
        "people": v.people,
        "owners": [v.org, *v.people],
        "scopes": [v.org, v.case, *v.people],
        "pages": v.pages,
        "invoices": v.invoices,
        "jobs": v.jobs,
    }
    parts: list[sql.Composable] = []

    def add(column: str, expression: str) -> None:
        if column in info.columns:
            parts.append(sql.SQL("{} {}").format(sql.Identifier(column), sql.SQL(expression)))

    key = ID_KEYED.get(info.name)
    if key == "case":
        parts.append(sql.SQL("id = %(case)s::uuid"))
    elif key == "org":
        parts.append(sql.SQL("id = %(org)s::uuid"))
    elif key == "bundle":
        parts.append(sql.SQL("id = %(bundle)s::uuid"))
    elif key == "people":
        parts.append(sql.SQL("id = any(%(people)s::uuid[])"))
    add("case_id", "= %(case)s::uuid")
    add("source_case_id", "= %(case)s::uuid")
    add("bundle_id", "= %(bundle)s::uuid")
    add("organisation_id", "= %(org)s::uuid")
    for column in PERSON_COLUMNS:
        add(column, "= any(%(people)s::uuid[])")
    if info.name in PERSON_KEYED_BY_TABLE:
        add(PERSON_KEYED_BY_TABLE[info.name], "= any(%(people)s::uuid[])")
    add("owner_id", "= any(%(owners)s::uuid[])")
    add("scope_id", "= any(%(scopes)s::uuid[])")
    add("page_id", "= any(%(pages)s::uuid[])")
    add("invoice_id", "= any(%(invoices)s::uuid[])")
    add("job_id", "= any(%(jobs)s::uuid[])")
    add("replaces_case_id", "= %(case)s::uuid")
    if not parts:
        return None
    return sql.SQL(" or ").join(parts), params


def _probe_tenant() -> Tenant:
    u = uuid.uuid4()
    return Tenant(u, u, u, u, u, u, u, u, [], [], [], [])


# Tables with no tenant key: reference data, queues and counters that hold nothing of a tenant, and the purge marker
# (PV-20: a case id for the length of the purge's own transaction, closed to the app roles).
EXPECTED_UNKEYED = {
    "case_purge_marks",
    "chain_checks",
    "job_kind_actions",
    "scheduled_runs",
    "spot_check_address_tries",
}
KEYED_TABLES = sorted(set(MIGRATION_TABLES) - EXPECTED_UNKEYED)
UNKEYED_TABLES = sorted(EXPECTED_UNKEYED)

# Strict expected failures, by table, filled in only after the failure was understood (see the module docstring).
XFAIL_SELECT: dict[str, str] = {}
XFAIL_UPDATE: dict[str, str] = {}
XFAIL_DELETE: dict[str, str] = {}
XFAIL_WORKER: dict[str, str] = {}
# Tables the owner (named expert or organisation owner) cannot read although rows exist, by design.
OWNER_BLIND: set[str] = {
    "ai_request_slots",
    "bundles",
    "deletion_ledger",  # server-only tables: no select policy for a signed-in person at all
    "job_steps",
    "pipeline_batches",
    "pipeline_runs",
    "rate_limit_hits",
    "support_accounts",
    "support_staff",  # the staff list is read by staff only (no policy for a person who is not on it)
}
# Tables `app_worker` can read with no job case set, although victim rows exist.
WORKER_UNSCOPED_READ: set[str] = {"job_kind_actions"}  # reference data: which action each job kind needs


def _tables(xfails: dict[str, str]) -> list[Any]:
    return [
        pytest.param(t, marks=pytest.mark.xfail(strict=True, reason=xfails[t])) if t in xfails else t
        for t in KEYED_TABLES
    ]


def _where(table: str, v: Tenant) -> tuple[sql.Composable, dict[str, Any]]:
    found = victim_filter(CATALOGUE[table], v)
    assert found is not None
    return found


def _select_count(table: str, v: Tenant) -> tuple[sql.Composed, dict[str, Any]]:
    where, params = _where(table, v)
    return sql.SQL("select count(*) from app.{} where ").format(sql.Identifier(table)) + where, params


@pytest.fixture(scope="module")
def victim_counts(admin: psycopg.Connection, world: World) -> dict[str, int]:
    _ensure_slot_row(admin, world.victim)
    counts: dict[str, int] = {}
    for table in KEYED_TABLES:
        query, params = _select_count(table, world.victim)
        row = admin.execute(query, params).fetchone()
        assert row is not None
        counts[table] = row[0]
    return counts


def _need_rows(victim_counts: dict[str, int], table: str) -> None:
    if not victim_counts[table]:
        pytest.skip(f"the world holds no victim row in app.{table}, so there is nothing to protect")


# --- a. every table, every command --------------------------------------------------------------------------------


def test_the_catalogue_found_the_tables_and_every_one_is_keyed_or_known() -> None:
    assert len(CATALOGUE) > 100, "the table catalogue is empty: is the local database up and migrated?"
    assert set(CATALOGUE) == set(MIGRATION_TABLES), (  # the database and the migration files name the same tables
        sorted(set(CATALOGUE) ^ set(MIGRATION_TABLES))
    )
    keyed = {t for t, info in CATALOGUE.items() if victim_filter(info, _probe_tenant()) is not None}
    assert keyed == set(KEYED_TABLES), sorted(keyed ^ set(KEYED_TABLES))


def test_the_world_fills_most_tables(victim_counts: dict[str, int]) -> None:
    filled = [t for t, n in victim_counts.items() if n]
    assert len(filled) >= 100, sorted(t for t, n in victim_counts.items() if not n)


@pytest.mark.parametrize("table", _tables(XFAIL_SELECT))
def test_select_shows_the_attacker_none_of_the_victims_rows(
    table: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    as_person(api, world.attacker.expert)
    query, params = _select_count(table, world.victim)
    out = run(api, query, params)
    assert out.error in (None, INSUFFICIENT_PRIVILEGE), out.error
    assert out.error is not None or out.rows == [(0,)], f"{table}: {out.rows}"


def _write_query(verb: str, table: str, v: Tenant, column: str | None) -> tuple[sql.Composable, dict[str, Any]]:
    where, params = _where(table, v)
    name = sql.Identifier(table)
    if verb == "delete":
        return sql.SQL("delete from app.{} where ").format(name) + where, params
    col = sql.Identifier(column or sorted(CATALOGUE[table].columns)[0])
    return sql.SQL("update app.{} set {} = {} where ").format(name, col, col) + where, params


@pytest.mark.parametrize("table", _tables(XFAIL_UPDATE))
def test_update_changes_none_of_the_victims_rows(
    table: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    as_person(api, world.attacker.expert)
    query, params = _write_query("update", table, world.victim, CATALOGUE[table].api_update_column)
    out = run(api, query, params)
    assert out.error in (None, INSUFFICIENT_PRIVILEGE), out.error
    assert out.rowcount == 0, f"{table}: {out.rowcount} rows"


@pytest.mark.parametrize("table", _tables(XFAIL_DELETE))
def test_delete_removes_none_of_the_victims_rows(
    table: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    as_person(api, world.attacker.expert)
    query, params = _write_query("delete", table, world.victim, None)
    out = run(api, query, params)
    assert out.error in (None, INSUFFICIENT_PRIVILEGE), out.error
    assert out.rowcount == 0, f"{table}: {out.rowcount} rows"


def test_control_the_owner_reads_the_rows_the_attacker_cannot(
    api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    """Without this the zero counts above would prove nothing: the people who should see the rows do."""
    seen: list[str] = []
    blind: list[str] = []
    for table, n in victim_counts.items():
        if not n:
            continue
        visible = False
        for person in (world.victim.expert, world.victim.owner):
            as_person(api, person)
            query, params = _select_count(table, world.victim)
            out = run(api, query, params)
            if out.rows and out.rows[0][0] > 0:
                visible = True
        (seen if visible else blind).append(table)
    assert len(seen) >= 60, len(seen)
    assert set(blind) <= OWNER_BLIND, sorted(set(blind) - OWNER_BLIND)


def test_control_the_owner_can_update_where_the_attacker_cannot(
    api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    changed = 0
    as_person(api, world.victim.expert)
    for table, n in victim_counts.items():
        column = CATALOGUE[table].api_update_column
        if not n or column is None:
            continue
        query, params = _write_query("update", table, world.victim, column)
        out = run(api, query, params)
        changed += 1 if out.rowcount else 0
    assert changed >= 15, changed


# --- b. inserts into the victim's tenant --------------------------------------------------------------------------

FIXTURE_PACKS = {"jur": "zz-test", "ct": "zz-case", "spec": "zz-specialty", "role": "assessor"}

INSERTS: dict[str, tuple[str, str | None]] = {
    # name: (statement, who may do it legitimately: "expert" | "owner" | None when nobody may)
    "facts": (
        "insert into app.facts (case_id, page_id, kind, value, word_ids)"
        " values (%(case)s, %(page)s, 'zz_result', '9 ug/L', array['w1', 'w2'])",
        "expert",
    ),
    "case_members": (
        "insert into app.case_members (case_id, person_id, case_role, added_by)"
        " values (%(case)s, %(target)s, 'viewer', %(actor)s)",
        "expert",
    ),
    "documents": (
        "insert into app.documents (bundle_id, kind, filename, sha256)"
        " values (%(bundle)s, 'records', 'f.pdf', repeat('0', 64))",
        "expert",
    ),
    "pages": (
        "insert into app.pages (bundle_id, document_id, bundle_page_no) values (%(bundle)s, %(doc)s, 9001)",
        None,
    ),
    "jobs": (
        "insert into app.jobs (case_id, kind, requested_by) values (%(case)s, 'process_bundle', %(actor)s)",
        "expert",
    ),
    "uploads": (
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " document_id) values (%(case)s, %(actor)s, 'records', 'f.pdf', 10, repeat('0', 64), 8, 2, %(doc)s)",
        "expert",
    ),
    "report_versions": (
        "insert into app.report_versions (case_id, version_no, type, display_name)"
        " values (%(case)s, 99, 'main', 'Report 99')",
        "expert",
    ),
    "invoices": (
        "insert into app.invoices (case_id, billing_profile_id, lines, total, currency)"
        " values (%(case)s, %(profile)s, '[]', 10, 'XTS')",
        "expert",
    ),
    "notifications": (
        "insert into app.notifications (person_id, organisation_id, case_id, kind)"
        " values (%(owner)s, %(org)s, %(case)s, 'bundle_ready')",
        None,
    ),
    "memberships_as_owner": (
        "insert into app.memberships (organisation_id, person_id, role) values (%(org)s, %(target)s, 'owner')",
        None,
    ),
    "invites": (
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash, expires_at)"
        " values (%(org)s, 'zz-new@example.test', 'viewer', %(actor)s, repeat('7', 64), now() + interval '1 day')",
        "owner",
    ),
    "case_keys": (
        "insert into app.case_keys (case_id, wrapped_key, key_ref) values (%(case)s, 'zz'::bytea, 'zz-key')",
        None,
    ),
    "billing_profiles_for_the_org": (
        "insert into app.billing_profiles (owner_type, owner_id, legal_name, currency)"
        " values ('organisation', %(org)s, 'Zz Company', 'XTS')",
        "owner",
    ),
    "cases_in_the_org": (
        "insert into app.cases (organisation_id, named_expert_id, jurisdiction, case_type, specialty, expert_role,"
        " label) values (%(org)s, %(actor)s, 'zz-test', 'zz-case', 'zz-specialty', 'assessor', 'Case Z')",
        "expert",
    ),
}
XFAIL_INSERT: dict[str, str] = {}


def _insert_params(world: World, actor: uuid.UUID, target: uuid.UUID) -> dict[str, Any]:
    v = world.victim
    return {
        "case": v.case,
        "bundle": v.bundle,
        "org": v.org,
        "page": v.pages[0],
        "doc": v.documents[0],
        "profile": v.profile,
        "owner": v.owner,
        "actor": actor,
        "target": target,
    }


@pytest.mark.parametrize(
    "name",
    [
        pytest.param(n, marks=pytest.mark.xfail(strict=True, reason=XFAIL_INSERT[n])) if n in XFAIL_INSERT else n
        for n in INSERTS
    ],
)
def test_the_attacker_cannot_insert_into_the_victims_tenant(name: str, api: psycopg.Connection, world: World) -> None:
    statement, _ = INSERTS[name]
    as_person(api, world.attacker.expert)
    out = run(api, statement, _insert_params(world, world.attacker.expert, world.attacker.expert))
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


@pytest.mark.parametrize("name", [n for n, (_, who) in INSERTS.items() if who])
def test_control_the_same_insert_succeeds_for_the_legitimate_owner(
    name: str, api: psycopg.Connection, world: World
) -> None:
    statement, who = INSERTS[name]
    person = world.victim.owner if who == "owner" else world.victim.expert
    as_person(api, person)
    out = run(api, statement, _insert_params(world, person, world.outsider))
    assert out.error is None, out.error
    assert out.rowcount == 1


# --- c. L1: a person who may edit a case repoints it ---------------------------------------------------------------


def _visible(api: psycopg.Connection, query: str, params: Any) -> int:
    out = run(api, query, params)
    assert out.error is None, out.error
    return int(out.rows[0][0])


@pytest.mark.parametrize("who", ["expert", "assistant"])
def test_l1_a_case_cannot_be_repointed_at_the_victims_bundle(who: str, api: psycopg.Connection, world: World) -> None:
    a, v = world.attacker, world.victim
    as_person(api, getattr(a, who))
    before = _visible(api, "select count(*) from app.pages where bundle_id = %s", (v.bundle,))
    assert before == 0
    out = run(api, "update app.cases set bundle_id = %s where id = %s", (v.bundle, a.case))
    pages = _visible(api, "select count(*) from app.pages where bundle_id = %s", (v.bundle,))
    documents = _visible(api, "select count(*) from app.documents where bundle_id = %s", (v.bundle,))
    words = _visible(api, "select count(*) from app.ocr_words where bundle_id = %s", (v.bundle,))
    assert (pages, documents, words) == (0, 0, 0), (out.error, out.rowcount, pages, documents, words)
    assert out.refused or out.rowcount == 0, (out.error, out.rowcount)


def test_l1_control_the_expert_may_edit_other_columns_of_the_case(api: psycopg.Connection, world: World) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "update app.cases set label = 'Case Y' where id = %s", (world.attacker.case,))
    assert (out.error, out.rowcount) == (None, 1)


@pytest.mark.parametrize("who", ["expert", "assistant"])
def test_l1_a_case_cannot_be_moved_into_the_victims_organisation(
    who: str, api: psycopg.Connection, world: World
) -> None:
    a, v = world.attacker, world.victim
    as_person(api, getattr(a, who))
    out = run(api, "update app.cases set organisation_id = %s where id = %s", (v.org, a.case))
    assert out.refused or out.rowcount == 0, (out.error, out.rowcount)
    as_person(api, v.owner)
    seen = _visible(api, "select count(*) from app.cases where id = %s", (a.case,))
    assert seen == 0, "an owner of the victim organisation now sees the attacker's case"


def test_l1_a_member_cannot_make_themselves_the_named_expert(api: psycopg.Connection, world: World) -> None:
    a = world.attacker
    as_person(api, a.assistant)
    out = run(api, "update app.cases set named_expert_id = %s where id = %s", (a.assistant, a.case))
    assert out.refused or out.rowcount == 0, (out.error, out.rowcount)


# --- d. L3: audit rows naming somebody else's case -----------------------------------------------------------------

FORGED_AUDIT = (
    "insert into app.audit_log (person_id, case_id, organisation_id, action, target)"
    " values (%(me)s, %(case)s, %(org)s, 'zz.forged', 'zz:x')"
)


def test_l3_the_attacker_cannot_write_audit_rows_into_the_victims_case(api: psycopg.Connection, world: World) -> None:
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    out = run(api, FORGED_AUDIT, {"me": a.expert, "case": v.case, "org": v.org})
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_l3_a_forged_audit_row_never_reaches_the_victims_audit_view(api: psycopg.Connection, world: World) -> None:
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    run(api, FORGED_AUDIT, {"me": a.expert, "case": v.case, "org": v.org})
    as_person(api, v.expert)
    out = run(api, "select count(*) from app.close_case_audit(%s) where person_id = %s", (v.case, a.expert))
    assert out.error is None, out.error
    assert out.rows == [(0,)]


def test_l3_control_the_attacker_may_write_audit_rows_for_their_own_case(api: psycopg.Connection, world: World) -> None:
    a = world.attacker
    as_person(api, a.expert)
    out = run(api, FORGED_AUDIT, {"me": a.expert, "case": a.case, "org": a.org})
    assert (out.error, out.rowcount) == (None, 1)


# --- e. L4: a person changes their own identity row ----------------------------------------------------------------


def test_l4_a_person_cannot_take_the_address_a_pending_invite_was_sent_to(
    api: psycopg.Connection, world: World
) -> None:
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    out = run(api, "update app.people set email = %s where id = %s", (v.invite_email, a.expert))
    assert out.refused or out.rowcount == 0, (out.error, out.rowcount)


def test_l4_taking_the_invited_address_does_not_let_the_attacker_accept_the_invite(
    api: psycopg.Connection, world: World
) -> None:
    """The attacker is given the invite's secret link (forwarded mail); the address check is the only other gate."""
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    run(api, "update app.people set email = %s where id = %s", (v.invite_email, a.expert))
    out = run(api, "select * from app.accept_invite(%s)", (v.invite_token_hash,))
    assert out.refused, f"accepted: {out.rows and out.rows[0][1]}"


def test_l4_control_a_person_may_change_their_display_name(api: psycopg.Connection, world: World) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "update app.people set full_name = 'Zz Name' where id = %s", (world.attacker.expert,))
    assert (out.error, out.rowcount) == (None, 1)


def test_l4_a_closed_account_cannot_reopen_itself(api: psycopg.Connection, admin: psycopg.Connection) -> None:
    seed = Seed(admin)
    person = seed.person()
    seed.close_account(person)
    as_person(api, person)
    out = run(api, "update app.people set closed_at = null where id = %s", (person,))
    assert out.refused or out.rowcount == 0, (out.error, out.rowcount)


# --- e2. L1, L3 and L4 again, through INSERT and the widest audit relaxation ---------------------------------------

INSERT_CASE = (
    "insert into app.cases (id, organisation_id, named_expert_id, jurisdiction, case_type, specialty, expert_role,"
    " label{extra}) values (%(id)s, %(org)s, %(me)s, 'zz-test', 'zz-case', 'zz-specialty', 'assessor', 'Case Z'{value})"
)


def test_l1_a_new_case_cannot_be_created_on_the_victims_bundle(api: psycopg.Connection, world: World) -> None:
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    new_case = uuid.uuid4()
    query = INSERT_CASE.format(extra=", bundle_id", value=", %(bundle)s")
    out = run(api, query, {"id": new_case, "org": a.org, "me": a.expert, "bundle": v.bundle})
    pages = _visible(api, "select count(*) from app.pages where bundle_id = %s", (v.bundle,))
    assert pages == 0, (out.error, out.rowcount, pages)
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_l1_control_a_new_case_gets_a_bundle_of_its_own(api: psycopg.Connection, world: World) -> None:
    a, v = world.attacker, world.victim
    as_person(api, a.expert)
    new_case = uuid.uuid4()
    out = run(api, INSERT_CASE.format(extra="", value=""), {"id": new_case, "org": a.org, "me": a.expert})
    assert (out.error, out.rowcount) == (None, 1)
    made = run(api, "select bundle_id from app.cases where id = %s", (new_case,))
    assert made.error is None and len(made.rows) == 1
    assert made.rows[0][0] is not None and made.rows[0][0] not in (v.bundle, a.bundle)


def test_l4_a_person_with_no_row_cannot_insert_one_with_the_invited_address(
    api: psycopg.Connection, world: World
) -> None:
    v = world.victim
    newcomer = uuid.uuid4()
    as_person(api, newcomer)
    out = run(
        api, "insert into app.people (id, email, full_name) values (%s, %s, 'Zz Name')", (newcomer, v.invite_email)
    )
    left = _visible(api, "select count(*) from app.people where id = %s", (newcomer,))
    assert left == 0, (out.error, out.rowcount)
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_l4_control_ensure_person_still_creates_the_row(api: psycopg.Connection) -> None:
    newcomer = uuid.uuid4()
    as_person(api, newcomer)
    out = run(api, "select app.ensure_person(%s, 'Zz Name', 'zz')", (f"{newcomer.hex[:12]}@example.test",))
    assert (out.error, out.rows) == (None, [(True,)])
    assert _visible(api, "select count(*) from app.people where id = %s", (newcomer,)) == 1


def _former_member(admin: psycopg.Connection, org: uuid.UUID, role: str = "expert") -> uuid.UUID:
    seed = Seed(admin)
    person = seed.person()
    seed.membership(org, person, role)
    seed.end_membership(org, person)
    return person


def test_l3_a_former_organisation_member_cannot_name_a_case_they_were_never_on(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    a = world.attacker
    former = _former_member(admin, a.org)
    as_person(api, former)
    both = run(api, FORGED_AUDIT, {"me": former, "case": a.case, "org": a.org})
    case_only = run(api, FORGED_AUDIT.replace("%(org)s", "null"), {"me": former, "case": a.case})
    assert both.error == INSUFFICIENT_PRIVILEGE, both.error
    assert case_only.error == INSUFFICIENT_PRIVILEGE, case_only.error


def test_l3_a_current_member_who_is_not_on_the_case_cannot_name_it(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    a = world.attacker
    seed = Seed(admin)
    other = seed.person()
    seed.membership(a.org, other, "expert")
    as_person(api, other)
    out = run(api, FORGED_AUDIT, {"me": other, "case": a.case, "org": a.org})
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_l3_control_leaving_an_organisation_is_audited_after_the_membership_ended(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    a = world.attacker
    former = _former_member(admin, a.org)
    as_person(api, former)
    org_only = FORGED_AUDIT.replace("%(case)s", "null")
    out = run(api, org_only, {"me": former, "org": a.org})
    assert (out.error, out.rowcount) == (None, 1)


@pytest.mark.parametrize("who", ["viewer", "assistant", "admin", "owner"])
def test_l3_control_current_case_members_and_organisation_administrators_may_audit_their_case(
    who: str, api: psycopg.Connection, world: World
) -> None:
    a = world.attacker
    person = getattr(a, who)
    as_person(api, person)
    out = run(api, FORGED_AUDIT, {"me": person, "case": a.case, "org": a.org})
    assert (out.error, out.rowcount) == (None, 1)


def test_l3_control_a_person_removed_from_the_case_may_still_audit_it(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    a = world.attacker
    seed = Seed(admin)
    person = seed.person()
    seed.membership(a.org, person, "assistant")
    seed.case_member(a.case, person, "assistant", added_by=a.expert)
    admin.execute(
        "update app.case_members set removed_at = now() where case_id = %s and person_id = %s", (a.case, person)
    )
    seed.end_membership(a.org, person)
    as_person(api, person)
    out = run(api, FORGED_AUDIT, {"me": person, "case": a.case, "org": a.org})
    assert (out.error, out.rowcount) == (None, 1)


# --- f. L9: bundles and privileged functions ------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-11 L9: insert into app.bundles (source_case_id = <victim case>) succeeds for any signed-in"
        " person; bundles_insert is with check (true) (0040:106)"
    ),
)
def test_l9_an_ordinary_person_cannot_create_a_bundle_for_the_victims_case(
    api: psycopg.Connection, world: World
) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "insert into app.bundles (source_case_id) values (%s)", (world.victim.case,))
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_l9_control_the_same_bundle_insert_is_valid_for_the_attackers_own_case(
    api: psycopg.Connection, world: World
) -> None:
    """Only the case named differs from the refused-or-not statement above, so the policy is all that decides."""
    as_person(api, world.attacker.expert)
    out = run(api, "insert into app.bundles (source_case_id) values (%s)", (world.attacker.case,))
    assert (out.error, out.rowcount) == (None, 1)


NEVER_EXECUTABLE = ["purge_case_rows", "purge_case", "destroy_case_key", "cases_due_for_deletion"]
PROBED_FUNCTIONS = [
    "purge_case_rows",
    "purge_case",
    "destroy_case_key",
    "cases_due_for_deletion",
    "mark_email_bounced",
    "support_new_password",
    "support_job_action",
    "case_ever_served",
    "enqueue_job",
]


def _executable(admin: psycopg.Connection, name: str, role: str) -> list[bool]:
    rows = admin.execute(
        "select has_function_privilege(%s, p.oid, 'execute') from pg_proc p"
        " join pg_namespace n on n.oid = p.pronamespace"
        " where n.nspname = 'app' and p.proname = %s",
        (role, name),
    ).fetchall()
    assert rows, f"no function app.{name}"
    return [r[0] for r in rows]


@pytest.mark.parametrize("name", NEVER_EXECUTABLE)
def test_l9_the_destructive_functions_are_not_executable_by_the_api_role(admin: psycopg.Connection, name: str) -> None:
    assert not any(_executable(admin, name, "app_api"))


@pytest.mark.parametrize("name", PROBED_FUNCTIONS)
def test_l9_the_privilege_probe_finds_every_function(admin: psycopg.Connection, name: str) -> None:
    assert _executable(admin, name, "app_api") is not None


def test_l9_destructive_calls_with_the_victims_ids_are_refused_and_change_nothing(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    v = world.victim
    before = admin.execute(
        "select (select count(*) from app.facts where case_id = %s), (select count(*) from app.case_keys"
        " where case_id = %s and destroyed_at is null)",
        (v.case, v.case),
    ).fetchone()
    as_person(api, world.attacker.expert)
    for call, args in (
        ("app.purge_case_rows(%s)", (v.case,)),
        ("app.purge_case(%s)", (v.case,)),
        ("app.destroy_case_key(%s)", (v.case,)),
        ("app.cases_due_for_deletion(current_date)", ()),
    ):
        out = run(api, f"select * from {call}", args)
        assert out.error == INSUFFICIENT_PRIVILEGE, (call, out.error)
    after = admin.execute(
        "select (select count(*) from app.facts where case_id = %s), (select count(*) from app.case_keys"
        " where case_id = %s and destroyed_at is null)",
        (v.case, v.case),
    ).fetchone()
    assert before == after


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-11 L9: select app.case_ever_served(<victim case>) returns true to any signed-in person "
        "(security definer, granted to app_api, 0012:73)"
    ),
)
def test_l9_a_signed_in_person_cannot_ask_whether_somebody_elses_case_was_served(
    api: psycopg.Connection, world: World
) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "select app.case_ever_served(%s)", (world.victim.case,))
    assert out.refused or out.rows == [(False,)] or out.rows == [(None,)], out.rows


def test_l9_control_case_ever_served_answers_for_the_attackers_own_served_case(
    api: psycopg.Connection, world: World
) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "select app.case_ever_served(%s)", (world.attacker.case,))
    assert out.rows == [(True,)]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-11 L9: select app.expert_has_served(<victim expert>) answers any signed-in person "
        "(security definer, granted to app_api, 0360:8)"
    ),
)
def test_l9_a_signed_in_person_cannot_ask_whether_somebody_else_has_served(
    api: psycopg.Connection, world: World
) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "select app.expert_has_served(%s)", (world.victim.expert,))
    assert out.refused or out.rows == [(False,)] or out.rows == [(None,)], out.rows


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-11 L9: select app.mark_email_bounced(<victim email>) sets people.email_bounced_at for any "
        "address for any signed-in person (0009:143, granted to app_api at 0009:158); mail to that person"
        " then stops"
    ),
)
def test_l9_a_signed_in_person_cannot_mark_somebody_elses_address_as_bouncing(
    api: psycopg.Connection, world: World
) -> None:
    v = world.victim
    as_person(api, world.attacker.expert)
    out = run(api, "select app.mark_email_bounced(%s)", (v.expert_email,))
    as_person(api, v.expert)
    row = run(api, "select email_bounced_at is not null from app.people where id = %s", (v.expert,))
    assert row.rows == [(False,)], (out.error, out.rows)


def test_l9_support_functions_refuse_a_person_who_is_not_staff(api: psycopg.Connection, world: World) -> None:
    v = world.victim
    as_person(api, world.attacker.expert)
    job = run(api, "select * from app.support_job_action(%s, 'requeue', 'zz', 'default')", (uuid.uuid4(),))
    password = run(api, "select * from app.support_new_password(%s)", (uuid.uuid4(),))
    assert job.error == INSUFFICIENT_PRIVILEGE and password.error == INSUFFICIENT_PRIVILEGE, (job.error, password.error)
    assert v.case  # the call names no victim id on purpose: the guard is the caller's staff status


def test_l9_enqueue_job_for_the_victims_case_is_refused_and_adds_no_job(
    api: psycopg.Connection, admin: psycopg.Connection, world: World
) -> None:
    v = world.victim
    count = "select count(*) from app.jobs where case_id = %s"
    before = admin.execute(count, (v.case,)).fetchone()
    as_person(api, world.attacker.expert)
    out = run(api, "select app.enqueue_job(%s, 'process_bundle', 'default', 0, false, 'zz')", (v.case,))
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error
    assert admin.execute(count, (v.case,)).fetchone() == before


def test_l9_close_case_audit_refuses_a_stranger(api: psycopg.Connection, world: World) -> None:
    as_person(api, world.attacker.expert)
    out = run(api, "select * from app.close_case_audit(%s)", (world.victim.case,))
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


# --- g. what identifies the caller ---------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["attacker", "nobody"])
@pytest.mark.parametrize("table", _tables({}))
def test_the_job_case_setting_gives_the_api_role_no_access(
    table: str, who: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    """`app.job_case_id` is the worker's key. A signed-in API connection that sets it to the victim's case (as the
    attacker, or as no one) must see and change nothing of the victim's, in every table that holds victim rows."""
    _need_rows(victim_counts, table)
    as_person(api, world.attacker.expert if who == "attacker" else None)
    as_job_case(api, world.victim.case)
    query, params = _select_count(table, world.victim)
    seen = run(api, query, params)
    # 22P02: with no person set, a policy that casts app.person_id to uuid fails on the empty string (it fails closed)
    refusals = (None, INSUFFICIENT_PRIVILEGE) + (("22P02",) if who == "nobody" else ())
    assert seen.error in refusals, seen.error
    assert seen.error is not None or seen.rows == [(0,)], f"{table}: {seen.rows}"
    column = CATALOGUE[table].api_update_column
    if column is not None:
        changed = run(api, *_write_query("update", table, world.victim, column))
        assert changed.rowcount == 0, f"{table}: {changed.rowcount} rows updated"
    removed = run(api, *_write_query("delete", table, world.victim, None))
    assert removed.rowcount == 0, f"{table}: {removed.rowcount} rows deleted"


@pytest.mark.parametrize("role", ["app_worker", "app_migrator", "postgres"])
def test_the_api_role_cannot_become_a_more_privileged_role(api: psycopg.Connection, role: str) -> None:
    out = run(api, sql.SQL("set local role {}").format(sql.Identifier(role)))
    assert out.error == INSUFFICIENT_PRIVILEGE, out.error


def test_with_no_person_set_no_tenant_table_shows_a_row(
    api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    as_person(api, None)
    leaking = []
    for table, n in victim_counts.items():
        if not n:
            continue
        query, params = _select_count(table, world.victim)
        out = run(api, query, params)
        if out.rows and out.rows[0][0]:
            leaking.append(table)
    assert not leaking, leaking


def test_a_malformed_person_setting_shows_no_rows(api: psycopg.Connection, world: World) -> None:
    api.execute("select set_config('app.person_id', 'not-a-uuid', true)")
    out = run(api, "select count(*) from app.cases where id = %s", (world.victim.case,))
    assert out.refused or out.rows == [(0,)]


def test_documented_the_person_setting_is_the_identity_so_it_is_not_a_boundary_against_sql_injection(
    api: psycopg.Connection, world: World
) -> None:
    """By design (ADR 0013) `app.person_id` is a plain setting the restricted role may set. Any SQL injection
    running as `app_api` could therefore read as anybody it can name: this is why every statement is parameterised
    and why the injection guard tests matter. This test pins the behaviour so a change is noticed."""
    v = world.victim
    as_person(api, world.attacker.expert)
    assert _visible(api, "select count(*) from app.facts where case_id = %s", (v.case,)) == 0
    as_person(api, v.expert)
    assert _visible(api, "select count(*) from app.facts where case_id = %s", (v.case,)) > 0


# --- h. the worker role ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("table", _tables(XFAIL_WORKER))
def test_a_job_for_the_attackers_case_sees_and_changes_none_of_the_victims_rows(
    table: str, worker: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    as_job_case(worker, world.attacker.case)
    query, params = _select_count(table, world.victim)
    seen = run(worker, query, params)
    assert seen.error in (None, INSUFFICIENT_PRIVILEGE), seen.error
    assert seen.error is not None or seen.rows == [(0,)], f"{table}: {seen.rows}"
    column = CATALOGUE[table].worker_update_column
    if column is not None:
        changed = run(worker, *_write_query("update", table, world.victim, column))
        assert changed.rowcount == 0, f"{table}: {changed.rowcount} rows updated"
    removed = run(worker, *_write_query("delete", table, world.victim, None))
    assert removed.error in (None, INSUFFICIENT_PRIVILEGE), removed.error
    assert removed.rowcount == 0, f"{table}: {removed.rowcount} rows deleted"


def test_control_a_job_for_the_victims_case_does_see_its_rows(
    worker: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    as_job_case(worker, world.victim.case)
    seen = 0
    for table, n in victim_counts.items():
        if not n:
            continue
        query, params = _select_count(table, world.victim)
        out = run(worker, query, params)
        seen += 1 if out.rows and out.rows[0][0] else 0
    assert seen >= 40, seen


def test_the_worker_tables_readable_with_no_case_scope_are_the_known_ones(
    worker: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    as_job_case(worker, None)
    unscoped = []
    for table in sorted(CATALOGUE):
        out = run(worker, sql.SQL("select count(*) from app.{}").format(sql.Identifier(table)))
        if out.rows and out.rows[0][0]:
            unscoped.append(table)
    assert set(unscoped) <= WORKER_UNSCOPED_READ, sorted(set(unscoped) - WORKER_UNSCOPED_READ)


# --- i. the worker role writing while it names the ATTACKER's case ---------------------------------------------------


@pytest.mark.parametrize("name", list(INSERTS))
def test_a_job_for_the_attackers_case_cannot_insert_into_the_victims_tenant(
    name: str, worker: psycopg.Connection, world: World
) -> None:
    statement, _ = INSERTS[name]
    as_job_case(worker, world.attacker.case)
    out = run(worker, statement, _insert_params(world, world.attacker.expert, world.attacker.expert))
    assert out.error is not None, f"{name}: the insert was accepted ({out.rowcount} row)"
    assert out.error == INSUFFICIENT_PRIVILEGE, f"{name}: refused for another reason ({out.error})"


@pytest.mark.parametrize("name", list(INSERTS))
def test_control_a_job_for_the_victims_case_inserts_where_the_worker_may_write(
    name: str, worker: psycopg.Connection, world: World
) -> None:
    """Without this the refusals above could be the worker's missing privilege on the table, not the case scope."""
    statement, _ = INSERTS[name]
    as_job_case(worker, world.victim.case)
    out = run(worker, statement, _insert_params(world, world.victim.expert, world.outsider))
    if out.error == INSUFFICIENT_PRIVILEGE:
        pytest.skip(f"app_worker has no INSERT right on this table or row ({name}): the refusal above is not the scope")
    # 23505: the row is refused only because the victim's case already has one (the scope check passed first)
    assert out.error in (None, "23505"), f"{name}: {out.error}"
    assert out.error == "23505" or out.rowcount == 1


# --- j. people who are not the attacker: every other kind of stranger -------------------------------------------------


@pytest.fixture(scope="module")
def staff_person(admin: psycopg.Connection) -> uuid.UUID:
    """Avodeck support staff with no grant on any case."""
    seed = Seed(admin)
    person = seed.person()
    seed.org(kind="personal", members={person: "owner"})
    seed.support_staff(person)
    return person


# Tables a member of the support staff reads for the support console, by design (metadata about who is staff, what
# support did): docs/spec/permissions.md. Every other table must show them none of the victim's rows.
STAFF_READS: dict[str, str] = {
    "custody_transfers": "support staff read every custody transfer: policy custody_transfers_staff (0032:102)",
    "job_support_actions": "support staff read the resume and requeue log: policy job_support_actions_view (0035:41)",
}
STRANGERS = ("outsider", "support_staff")


def _stranger(name: str, world: World, staff: uuid.UUID) -> uuid.UUID:
    return world.outsider if name == "outsider" else staff


@pytest.mark.parametrize("table", _tables({}))
@pytest.mark.parametrize("who", STRANGERS)
def test_select_shows_a_stranger_none_of_the_victims_rows(
    who: str, table: str, api: psycopg.Connection, world: World, staff_person: uuid.UUID, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    if who == "support_staff" and table in STAFF_READS:
        pytest.skip(STAFF_READS[table])
    as_person(api, _stranger(who, world, staff_person))
    query, params = _select_count(table, world.victim)
    out = run(api, query, params)
    assert out.error in (None, INSUFFICIENT_PRIVILEGE), out.error
    assert out.error is not None or out.rows == [(0,)], f"{who} {table}: {out.rows}"


@pytest.mark.parametrize("table", _tables({}))
@pytest.mark.parametrize("who", STRANGERS)
def test_a_stranger_changes_none_of_the_victims_rows(
    who: str, table: str, api: psycopg.Connection, world: World, staff_person: uuid.UUID, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    as_person(api, _stranger(who, world, staff_person))
    column = CATALOGUE[table].api_update_column
    if column is not None:
        changed = run(api, *_write_query("update", table, world.victim, column))
        assert changed.rowcount == 0, f"{who} {table}: {changed.rowcount} rows updated"
    removed = run(api, *_write_query("delete", table, world.victim, None))
    assert removed.rowcount == 0, f"{who} {table}: {removed.rowcount} rows deleted"


@pytest.mark.parametrize("name", list(INSERTS))
@pytest.mark.parametrize("who", STRANGERS)
def test_a_stranger_cannot_insert_into_the_victims_tenant(
    who: str, name: str, api: psycopg.Connection, world: World, staff_person: uuid.UUID
) -> None:
    person = _stranger(who, world, staff_person)
    as_person(api, person)
    statement, _ = INSERTS[name]
    out = run(api, statement, _insert_params(world, person, person))
    assert out.error == INSUFFICIENT_PRIVILEGE, f"{who} {name}: {out.error}"


# The victim organisation's own administrator is NOT on the case: the matrix gives them metadata and administration,
# never the content of the records (docs/spec/permissions.md). RLS repeats that.
CONTENT_TABLES = ("pages", "facts", "documents", "ocr_words", "consultation_notes", "opinions", "findings", "points")


@pytest.mark.parametrize("who", ["admin", "owner"])
@pytest.mark.parametrize("table", CONTENT_TABLES)
def test_an_organisation_administrator_not_on_the_case_cannot_read_the_records(
    table: str, who: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    if table not in CATALOGUE:
        pytest.skip(f"app.{table} does not exist")
    _need_rows(victim_counts, table)
    as_person(api, world.victim.admin if who == "admin" else world.victim.owner)
    query, params = _select_count(table, world.victim)
    out = run(api, query, params)
    assert out.error in (None, INSUFFICIENT_PRIVILEGE), out.error
    assert out.error is not None or out.rows == [(0,)], f"{who} {table}: {out.rows}"


@pytest.mark.parametrize("who", ["admin", "owner"])
def test_control_an_organisation_administrator_sees_the_case_itself(
    who: str, api: psycopg.Connection, world: World
) -> None:
    as_person(api, world.victim.admin if who == "admin" else world.victim.owner)
    out = run(api, "select count(*) from app.cases where id = %s", (world.victim.case,))
    assert out.rows == [(1,)], "without this the zero counts above could mean the administrator sees nothing at all"


# A viewer of the victim's case reads it and changes nothing.
VIEWER_MAY_CHANGE: dict[str, str] = {}


@pytest.mark.parametrize("who", ["viewer", "assistant"])
@pytest.mark.parametrize(
    "table", ["facts", "documents", "pages", "case_members", "report_versions", "invoices", "opinions"]
)
def test_a_viewer_or_assistant_of_the_case_changes_only_what_the_matrix_allows(
    who: str, table: str, api: psycopg.Connection, world: World, victim_counts: dict[str, int]
) -> None:
    _need_rows(victim_counts, table)
    if who == "viewer" and table in VIEWER_MAY_CHANGE:
        pytest.skip(VIEWER_MAY_CHANGE[table])
    as_person(api, getattr(world.victim, who))
    column = CATALOGUE[table].api_update_column
    if column is not None and who == "viewer":
        changed = run(api, *_write_query("update", table, world.victim, column))
        assert changed.rowcount == 0, f"viewer {table}: {changed.rowcount} rows updated"
    removed = run(api, *_write_query("delete", table, world.victim, None))
    assert removed.rowcount == 0, f"{who} {table}: {removed.rowcount} rows deleted"


@pytest.mark.parametrize(
    "name", ["facts", "documents", "pages", "jobs", "uploads", "report_versions", "invoices", "case_members"]
)
def test_a_viewer_cannot_insert_what_only_the_named_expert_may(
    name: str, api: psycopg.Connection, world: World
) -> None:
    v = world.victim
    as_person(api, v.viewer)
    out = run(api, INSERTS[name][0], _insert_params(world, v.viewer, world.outsider))
    assert out.error == INSUFFICIENT_PRIVILEGE, f"viewer {name}: {out.error}"
