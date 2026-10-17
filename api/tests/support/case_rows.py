"""One or more rows in every case table, for the deletion guard (tests/guards/test_foundation_case_purge.py).

A slice that adds a case table adds a seeder here (as the local admin role), so the guard proves
`app.purge_case()` removes it. Values are made up and short; nothing here is a real record.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass

import psycopg

from tests.support.contracts import add_fact, add_question, add_report_version
from tests.support.seed import Seed


@dataclass(frozen=True)
class CaseWorld:
    case: uuid.UUID
    expert: uuid.UUID
    org: uuid.UUID


Seeder = Callable[[psycopg.Connection, Seed, CaseWorld], int]


def _documents_pages_facts(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    doc = seed.document(w.case)
    first = seed.page(w.case, doc, 1)
    second = seed.page(w.case, doc, 2)
    admin.execute("update app.pages set duplicate_of_page_id = %s where id = %s", (first, second))
    add_fact(admin, w.case, first, status="verified")
    add_fact(admin, w.case, second)
    admin.execute(
        "insert into app.uploads (case_id, person_id, kind, filename, size, sha256, chunk_size, chunk_count,"
        " document_id) values (%s, %s, 'records', 'f.pdf', 10, %s, 8, 2, %s)",
        (w.case, w.expert, "0" * 64, doc),
    )
    return {"documents": 1, "pages": 2, "facts": 2, "uploads": 1}


def _jobs(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    job = seed.job(w.case)
    admin.execute("insert into app.job_steps (job_id, case_id, step) values (%s, %s, 'zz_step')", (job, w.case))
    return {"jobs": 1, "job_steps": 1}


def _reports(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    served = add_report_version(admin, w.case, status="served")
    draft = add_report_version(admin, w.case, version_no=2)
    admin.execute("update app.report_versions set prev_version_id = %s where id = %s", (served, draft))
    return {"report_versions": 2}


def _questions(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    add_question(admin, w.case)
    return {"questions": 1}


def _people_rows(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    helper = seed.person()
    seed.membership(w.org, helper, "assistant")
    seed.case_member(w.case, helper, "assistant", added_by=w.expert)
    seed.notification(w.expert, w.org, w.case)
    admin.execute(
        "insert into app.transcripts (case_id, person_id, context, text, duration_seconds)"
        " values (%s, %s, 'zz_note', %s, 3)",
        (w.case, w.expert, b"sealed"),
    )
    admin.execute(
        "insert into app.ai_calls (case_id, task_id, model_id, region, prompt_version, input_tokens, output_tokens,"
        " outcome) values (%s, 'zz_task', 'zz-model', 'zz', 'v1', 1, 1, 'ok')",
        (w.case,),
    )
    admin.execute(
        "insert into app.idempotency_keys (person_id, key, fingerprint, status_code, response_body, case_id)"
        " values (%s, %s, 'f', 200, %s, %s)",
        (w.expert, f"k-{uuid.uuid4().hex}", b"{}", w.case),
    )
    return {"case_members": 1, "notifications": 1, "transcripts": 1, "ai_calls": 1, "idempotency_keys": 1}


def _legal_holds(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """A hold that has ended (an active one would stop the purge)."""
    admin.execute(
        "insert into app.legal_holds (case_id, reason, set_by, until, released_at, released_by)"
        " values (%s, 'zz_appeal', %s, current_date - 1, now(), %s)",
        (w.case, w.expert, w.expert),
    )
    return {"legal_holds": 1}


def _deletion_certificates(admin: psycopg.Connection, seed: Seed, w: CaseWorld) -> dict[str, int]:
    """Kept after the purge (KEPT_AFTER_CASE_DELETION): counts only, no patient text."""
    admin.execute("insert into app.deletion_certificates (case_id, counts) values (%s, '{\"pages\": 2}')", (w.case,))
    return {"deletion_certificates": 1}


SEEDERS = (
    _documents_pages_facts,
    _jobs,
    _reports,
    _questions,
    _people_rows,
    _legal_holds,
    _deletion_certificates,
)


def fill_every_case_table(admin: psycopg.Connection, seed: Seed, world: CaseWorld) -> dict[str, int]:
    """Rows in every case table except `cases` and `case_keys`; returns how many went in each."""
    counts: dict[str, int] = {}
    for seeder in SEEDERS:
        counts.update(seeder(admin, seed, world))
    return counts
