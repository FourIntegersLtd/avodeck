"""The contract tables slices fill (architecture 8.3): `facts`, `questions` and `report_versions`.

- Facts start `suggested`, always cite words on a page of their own case (rule 2), follow the
  fact state machine, gain a version on every change and are never deleted (state-machines.md).
- Questions start `suggested`, follow their state machine and keep their exact words.
- Report versions start as drafts; once locked nothing changes except locked -> served and
  served -> withdrawn (rule 8, migration trigger, SQLSTATE `AVLCK`).
- Row-level security repeats the permissions matrix on each table (rule 9).
- `core/contracts/case_tables.py` names every case table (slice H deletes through it).
"""

from __future__ import annotations

import itertools
import uuid

import psycopg
import pytest

from app.core.contracts.case_tables import AWAITING_SLICE_MIGRATION, CASE_TABLES, KEPT_AFTER_CASE_DELETION
from tests.support.contracts import add_fact, add_question, add_report_version
from tests.support.seed import Seed

FACT_STATUSES = ("suggested", "verified", "edited", "rejected")
FACT_EDGES = {
    ("suggested", "verified"),
    ("suggested", "edited"),
    ("suggested", "rejected"),
    ("verified", "rejected"),
    ("edited", "rejected"),
    ("rejected", "verified"),
}
QUESTION_STATUSES = ("suggested", "confirmed", "edited")
QUESTION_EDGES = {("suggested", "confirmed"), ("suggested", "edited")}


@pytest.fixture
def case_page(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "viewer", "outsider")}
    org = seed.org(kind="team", members={p["expert"]: "owner"})
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    p["case"] = seed.case(org, p["expert"], members={p["assistant"]: "assistant", p["viewer"]: "viewer"})
    p["page"] = seed.page(p["case"], seed.document(p["case"]), 1)
    return p


def as_person(dsn: str, person: uuid.UUID) -> psycopg.Connection:
    conn = psycopg.connect(dsn)
    conn.execute("select set_config('app.person_id', %s, false)", (str(person),))
    return conn


# --- facts ----------------------------------------------------------------------------------------


def test_a_new_fact_starts_suggested_at_version_1(admin: psycopg.Connection, case_page: dict[str, uuid.UUID]) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"])
    assert admin.execute("select status, version from app.facts where id = %s", (fact,)).fetchone() == (
        "suggested",
        1,
    )
    with pytest.raises(psycopg.Error) as err:
        admin.execute(
            "insert into app.facts (case_id, page_id, kind, value, word_ids, status)"
            " values (%s, %s, 'zz_result', 'x', '{w1}', 'verified')",
            (case_page["case"], case_page["page"]),
        )
    assert err.value.sqlstate == "AV409"


def test_a_fact_must_cite_words(admin: psycopg.Connection, case_page: dict[str, uuid.UUID]) -> None:
    with pytest.raises(psycopg.errors.CheckViolation):
        add_fact(admin, case_page["case"], case_page["page"], word_ids=[])


def test_a_fact_cites_a_page_of_its_own_case(
    admin: psycopg.Connection, seed: Seed, case_page: dict[str, uuid.UUID]
) -> None:
    other = seed.case(seed.org(kind="personal", members={case_page["outsider"]: "admin"}), case_page["outsider"])
    with pytest.raises(psycopg.Error) as err:
        add_fact(admin, other, case_page["page"])
    assert err.value.sqlstate == "AV409"
    fact = add_fact(admin, case_page["case"], case_page["page"])
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.facts set case_id = %s where id = %s", (other, fact))
    assert err.value.sqlstate == "AV409"


@pytest.mark.parametrize(
    ("start", "target"),
    sorted((a, b) for a, b in itertools.product(FACT_STATUSES, FACT_STATUSES) if a != b and (a, b) not in FACT_EDGES),
)
def test_database_refuses_an_undocumented_fact_transition(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], start: str, target: str
) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"], status=start)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.facts set status = %s where id = %s", (target, fact))
    assert err.value.sqlstate == "AV409"


@pytest.mark.parametrize(("start", "target"), sorted(FACT_EDGES))
def test_database_allows_every_documented_fact_transition(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], start: str, target: str
) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"], status=start)
    admin.execute("update app.facts set status = %s where id = %s", (target, fact))


def test_every_change_to_a_fact_raises_its_version(admin: psycopg.Connection, case_page: dict[str, uuid.UUID]) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"])
    admin.execute("update app.facts set value = '10 ug/L' where id = %s", (fact,))
    admin.execute("update app.facts set status = 'edited' where id = %s", (fact,))
    assert admin.execute("select version from app.facts where id = %s", (fact,)).fetchone() == (3,)


def test_facts_are_never_deleted_by_the_running_app(
    api_dsn: str, worker_dsn: str, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"])
    with as_person(api_dsn, case_page["expert"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from app.facts where id = %s", (fact,))
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case_page["case"]),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("delete from app.facts where id = %s", (fact,))


def test_facts_are_seen_by_case_viewers_and_changed_only_by_the_named_expert(
    api_dsn: str, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    fact = add_fact(admin, case_page["case"], case_page["page"])
    for person, sees in (("expert", 1), ("assistant", 1), ("viewer", 1), ("outsider", 0)):
        with as_person(api_dsn, case_page[person]) as conn:
            assert conn.execute("select count(*) from app.facts where id = %s", (fact,)).fetchone() == (sees,)
    for person, changes in (("assistant", 0), ("viewer", 0), ("expert", 1)):
        with as_person(api_dsn, case_page[person]) as conn:
            cur = conn.execute(
                "update app.facts set status = 'verified' where id = %s and status = 'suggested'", (fact,)
            )
            assert cur.rowcount == changes, person


def test_the_worker_adds_facts_only_to_its_own_jobs_case(
    worker_dsn: str, seed: Seed, case_page: dict[str, uuid.UUID]
) -> None:
    other = seed.case(seed.org(kind="personal", members={case_page["outsider"]: "admin"}), case_page["outsider"])
    insert = "insert into app.facts (case_id, page_id, kind, value, word_ids) values (%s, %s, 'zz_result', 'x', '{w1}')"
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(case_page["case"]),))
        conn.execute(insert, (case_page["case"], case_page["page"]))
    with psycopg.connect(worker_dsn) as conn:
        conn.execute("select set_config('app.job_case_id', %s, false)", (str(other),))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute(insert, (case_page["case"], case_page["page"]))


# --- questions ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("start", "target"),
    sorted(
        (a, b)
        for a, b in itertools.product(QUESTION_STATUSES, QUESTION_STATUSES)
        if a != b and (a, b) not in QUESTION_EDGES
    ),
)
def test_database_refuses_an_undocumented_question_transition(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], start: str, target: str
) -> None:
    question = add_question(admin, case_page["case"], status=start)
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.questions set status = %s where id = %s", (target, question))
    assert err.value.sqlstate == "AV409"


@pytest.mark.parametrize(("start", "target"), sorted(QUESTION_EDGES))
def test_database_allows_every_documented_question_transition(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], start: str, target: str
) -> None:
    question = add_question(admin, case_page["case"], status=start)
    admin.execute("update app.questions set status = %s where id = %s", (target, question))


def test_a_question_keeps_its_exact_words_and_starts_suggested(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    question = add_question(admin, case_page["case"])
    admin.execute(
        "update app.questions set status = 'edited', confirmed_text = 'Tidied words' where id = %s", (question,)
    )
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.questions set text_verbatim = 'Other words' where id = %s", (question,))
    assert err.value.sqlstate == "AV409"
    with pytest.raises(psycopg.Error) as err:
        admin.execute(
            "insert into app.questions (case_id, number, text_verbatim, kind, status)"
            " values (%s, 2, 'Q', 'zz_question', 'confirmed')",
            (case_page["case"],),
        )
    assert err.value.sqlstate == "AV409"


def test_only_the_named_expert_confirms_questions(
    api_dsn: str, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    question = add_question(admin, case_page["case"])
    for person, changes in (("assistant", 0), ("outsider", 0), ("expert", 1)):
        with as_person(api_dsn, case_page[person]) as conn:
            cur = conn.execute(
                "update app.questions set status = 'confirmed' where id = %s and status = 'suggested'", (question,)
            )
            assert cur.rowcount == changes, person


# --- report versions ------------------------------------------------------------------------------


def test_locking_a_report_version_stamps_it_and_needs_its_fingerprint(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    report = add_report_version(admin, case_page["case"])
    with pytest.raises(psycopg.Error) as err:
        admin.execute("update app.report_versions set status = 'locked' where id = %s", (report,))
    assert err.value.sqlstate == "AV409"
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("b" * 64, report))
    row = admin.execute("select locked_at is not null from app.report_versions where id = %s", (report,)).fetchone()
    assert row == (True,)


@pytest.mark.parametrize("status", ["locked", "served", "withdrawn"])
@pytest.mark.parametrize(
    "change",
    [
        "display_name = 'Changed'",
        "sha256 = repeat('c', 64)",
        "docx_path = 'elsewhere'",
        "locked_at = now() - interval '1 day'",
        "version_no = 99",
    ],
)
def test_a_locked_report_version_cannot_change(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], status: str, change: str
) -> None:
    report = add_report_version(admin, case_page["case"], status=status)
    with pytest.raises(psycopg.Error) as err:
        admin.execute(f"update app.report_versions set {change} where id = %s", (report,))  # noqa: S608 - fixed text
    assert err.value.sqlstate == "AVLCK"


@pytest.mark.parametrize(
    ("status", "target"),
    [("locked", "draft"), ("served", "locked"), ("served", "draft"), ("withdrawn", "served"), ("locked", "withdrawn")],
)
def test_a_locked_report_version_only_moves_to_served_then_withdrawn(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID], status: str, target: str
) -> None:
    report = add_report_version(admin, case_page["case"], status=status)
    with pytest.raises(psycopg.Error) as err:
        admin.execute(
            "update app.report_versions set status = %s, withdrawn_reason = 'zz_reason' where id = %s",
            (target, report),
        )
    assert err.value.sqlstate in ("AV409", "AVLCK")


def test_withdrawing_a_served_report_needs_a_reason_and_keeps_it(
    admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    report = add_report_version(admin, case_page["case"], status="served")
    with pytest.raises(psycopg.Error):
        admin.execute("update app.report_versions set status = 'withdrawn' where id = %s", (report,))
    admin.execute(
        "update app.report_versions set status = 'withdrawn', withdrawn_reason = 'zz_reason' where id = %s", (report,)
    )
    row = admin.execute(
        "select status, withdrawn_at is not null, locked_at is not null from app.report_versions where id = %s",
        (report,),
    ).fetchone()
    assert row == ("withdrawn", True, True)


def test_a_draft_can_change_and_report_versions_are_never_deleted_by_the_app(
    api_dsn: str, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    report = add_report_version(admin, case_page["case"])
    admin.execute("update app.report_versions set display_name = 'Report 1 (draft)' where id = %s", (report,))
    with as_person(api_dsn, case_page["expert"]) as conn, pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("delete from app.report_versions where id = %s", (report,))


def test_report_versions_are_built_by_case_builders_and_seen_by_case_viewers(
    api_dsn: str, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    report = add_report_version(admin, case_page["case"])
    for person, sees in (("viewer", 1), ("outsider", 0)):
        with as_person(api_dsn, case_page[person]) as conn:
            assert conn.execute("select count(*) from app.report_versions where id = %s", (report,)).fetchone() == (
                sees,
            )
    for person, changes in (("viewer", 0), ("assistant", 1)):
        with as_person(api_dsn, case_page[person]) as conn:
            cur = conn.execute("update app.report_versions set display_name = 'R' where id = %s", (report,))
            assert cur.rowcount == changes, person


# --- the case table registry ----------------------------------------------------------------------


def test_every_table_holding_case_data_is_in_the_case_table_registry(admin: psycopg.Connection) -> None:
    rows = admin.execute(
        "select table_name from information_schema.columns where table_schema = 'app' and column_name = 'case_id'"
    ).fetchall()
    with_case = {r[0] for r in rows}
    assert {"facts", "questions", "report_versions", "pages", "uploads"} <= with_case
    missing = with_case - CASE_TABLES - KEPT_AFTER_CASE_DELETION
    assert not missing, f"add these case tables to app/core/contracts/case_tables.py: {sorted(missing)}"
    assert not (CASE_TABLES & KEPT_AFTER_CASE_DELETION)
    assert "audit_log" in KEPT_AFTER_CASE_DELETION
    assert set(AWAITING_SLICE_MIGRATION) <= CASE_TABLES | KEPT_AFTER_CASE_DELETION
    gone = (CASE_TABLES | KEPT_AFTER_CASE_DELETION) - with_case - {"cases"} - set(AWAITING_SLICE_MIGRATION)
    assert not gone, f"registry names tables that do not exist: {sorted(gone)}"


def test_the_close_slice_tables_are_registered() -> None:
    assert "legal_holds" in CASE_TABLES
    assert {"audit_log", "deletion_certificates", "deletion_ledger"} <= KEPT_AFTER_CASE_DELETION
    # A table registered before its slice creates it names that slice (A-I, O); it leaves this list
    # once its migration lands. The foundation's deletion_ledger is never awaiting.
    assert all(slice_ in "ABCDEFGHIO" and len(slice_) == 1 for slice_ in AWAITING_SLICE_MIGRATION.values())
    assert "deletion_ledger" not in AWAITING_SLICE_MIGRATION


# --- citable facts (rule 2) -----------------------------------------------------------------------


class StubReads:
    """Stands in for slice B's read log: the pages each person has marked read."""

    def __init__(self, read: dict[uuid.UUID, set[uuid.UUID]]) -> None:
        self.read = read

    async def has_read(self, conn: object, case_id: uuid.UUID, page_no: int, person_id: uuid.UUID) -> bool:
        raise AssertionError("citable_facts works from page ids")

    async def read_page_ids(self, conn: object, case_id: uuid.UUID, person_id: uuid.UUID) -> set[uuid.UUID]:
        return self.read.get(person_id, set())


@pytest.mark.anyio
async def test_only_verified_facts_on_pages_the_named_expert_has_read_are_citable(
    api_db: object, seed: Seed, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    from app.core.contracts.facts import citable_facts
    from app.core.db import Database
    from tests.support.db import scope_for

    assert isinstance(api_db, Database)
    case, page = case_page["case"], case_page["page"]
    unread = seed.page(case, seed.document(case), 2)
    facts = {s: add_fact(admin, case, page, status=s) for s in ("suggested", "verified", "edited", "rejected")}
    on_unread = add_fact(admin, case, unread, status="verified")
    reads = StubReads({case_page["expert"]: {page}, case_page["assistant"]: {unread}})
    async with api_db.request(scope_for(case_page["assistant"])) as conn:
        found = await citable_facts(conn, case, reads)
        assert {f.id for f in found} == {facts["verified"], facts["edited"]}
        assert on_unread not in {f.id for f in found}, "another person's read does not count"
        assert all(f.status in ("verified", "edited") and f.word_ids for f in found)
        assert await citable_facts(conn, case, None) == [], "no read log: nothing is citable"


@pytest.mark.anyio
async def test_a_checked_fact_awaiting_a_recheck_is_not_citable(
    api_db: object, seed: Seed, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    """After a page is read again, a checked fact whose words are gone is flagged 'recheck': its
    word ids may point at words that no longer exist, so no report cites it until the expert checks it
    again. A flag for another purpose (sent to Points against) leaves it citable."""
    from app.core.contracts.fact_flags import POINTS, RECHECK
    from app.core.contracts.facts import citable_facts
    from app.core.db import Database
    from tests.support.db import scope_for

    assert isinstance(api_db, Database)
    case, page, expert = case_page["case"], case_page["page"], case_page["expert"]
    awaiting = add_fact(admin, case, page, status="verified")
    sent_on = add_fact(admin, case, page, status="edited")
    plain = add_fact(admin, case, page, status="verified")
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, null)",
        (case, awaiting, RECHECK),
    )
    admin.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, %s, %s)",
        (case, sent_on, POINTS, expert),
    )
    async with api_db.request(scope_for(expert)) as conn:
        found = {f.id for f in await citable_facts(conn, case, StubReads({expert: {page}}))}
    assert found == {sent_on, plain}


# --- confirmed fact-question links (slice D's table, read by slice E) -------------------------------


def _link(admin: psycopg.Connection, case: uuid.UUID, fact: uuid.UUID, question: uuid.UUID, status: str) -> None:
    """A link in `status`, reached the way the state machine allows (state-machines.md)."""
    if status == "confirmed":
        admin.execute(
            "insert into app.fact_question_links (case_id, fact_id, question_id, source, status)"
            " values (%s, %s, %s, 'expert', 'confirmed')",
            (case, fact, question),
        )
        return
    admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source) values (%s, %s, %s, 'ai')",
        (case, fact, question),
    )
    if status != "suggested":
        admin.execute(
            "update app.fact_question_links set status = %s where fact_id = %s and question_id = %s",
            (status, fact, question),
        )


@pytest.mark.anyio
async def test_confirmed_links_are_only_the_links_the_expert_confirmed(
    api_db: object, admin: psycopg.Connection, case_page: dict[str, uuid.UUID]
) -> None:
    from app.core.contracts.fact_links import confirmed_links
    from app.core.db import Database
    from tests.support.db import scope_for

    assert isinstance(api_db, Database)
    case, page = case_page["case"], case_page["page"]
    q1, q2 = add_question(admin, case, number=1), add_question(admin, case, number=2)
    facts = {s: add_fact(admin, case, page, status="verified") for s in ("a", "b", "c", "d", "e")}
    _link(admin, case, facts["a"], q1, "confirmed")
    _link(admin, case, facts["b"], q1, "confirmed")
    _link(admin, case, facts["c"], q1, "suggested")
    _link(admin, case, facts["d"], q1, "rejected")
    _link(admin, case, facts["e"], q1, "stale")
    _link(admin, case, facts["a"], q2, "confirmed")
    _link(admin, case, facts["b"], q2, "rejected")

    async with api_db.request(scope_for(case_page["viewer"])) as conn:
        assert await confirmed_links(conn, case) == {q1: {facts["a"], facts["b"]}, q2: {facts["a"]}}
        assert await confirmed_links(conn, case, q2) == {q2: {facts["a"]}}
    async with api_db.request(scope_for(case_page["outsider"])) as conn:
        assert await confirmed_links(conn, case) == {}, "row-level security applies on the caller's connection"
