"""Migration 0063: the indexes the hot queries need (scalability PR 14).

The case stream reads a case's recent jobs newest first, and the reader and document routes read one document's
pages in a bundle. Each gets an index that serves both the filter and the order, so neither scans the whole table.
Plans on a handful of rows would pick a sequential scan whatever indexes exist, so the tests turn that off and ask
which index the planner would use.
"""

from __future__ import annotations

import json
import uuid

import psycopg
import pytest

from tests.support.seed import Seed


def _plan_indexes(admin: psycopg.Connection, sql: str, params: tuple[object, ...]) -> set[str]:
    """Names of every index the plan for `sql` reads, with sequential and bitmap scans switched off."""
    with admin.transaction():
        admin.execute("set local enable_seqscan = off")
        admin.execute("set local enable_bitmapscan = off")
        row = admin.execute("explain (format json) " + sql, params).fetchone()
        assert row is not None
    names: set[str] = set()

    def walk(node: dict[str, object]) -> None:
        if "Index Name" in node:
            names.add(str(node["Index Name"]))
        for child in node.get("Plans", []):  # type: ignore[attr-defined]
            walk(child)

    plan = row[0] if not isinstance(row[0], str) else json.loads(row[0])
    walk(plan[0]["Plan"])
    return names


def _index_exists(admin: psycopg.Connection, name: str) -> bool:
    row = admin.execute("select 1 from pg_indexes where schemaname = 'app' and indexname = %s", (name,)).fetchone()
    return row is not None


@pytest.fixture
def case(seed: Seed) -> uuid.UUID:
    expert = seed.person()
    org = seed.org(members={expert: "owner"})
    case_id = seed.case(org, expert)
    seed.job(case_id)
    return case_id


def test_the_hot_indexes_exist(admin: psycopg.Connection) -> None:
    assert _index_exists(admin, "jobs_case_created_idx")
    assert _index_exists(admin, "pages_bundle_document_idx")


def test_the_case_stream_reads_a_cases_jobs_through_the_new_index(admin: psycopg.Connection, case: uuid.UUID) -> None:
    # The query in core/routes/case_events.py `_jobs`.
    used = _plan_indexes(
        admin,
        "select id::text, kind, status, step, pct, eta_seconds, step_done, step_total, error_code"
        " from app.jobs where case_id = %s"
        " and (finished_at is null or finished_at > now() - make_interval(mins => %s))"
        " order by created_at desc, id limit %s",
        (case, 10, 50),
    )
    assert "jobs_case_created_idx" in used


def test_a_documents_pages_are_read_through_the_new_index(admin: psycopg.Connection, seed: Seed, case: uuid.UUID) -> None:
    document = seed.document(case)
    seed.page(case, document, 1)
    # The query shape in core/contracts/document_words.py: one document's pages within the case's bundle.
    used = _plan_indexes(
        admin,
        "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s)"
        " and document_id = %s",
        (case, document),
    )
    assert "pages_bundle_document_idx" in used
