# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Support console case diagnostics (support plan phase 2; permissions.md "Avodeck staff"; F38).

`GET /v1/support/cases/{case_id}` grows (contracts only grow) with where the case is stuck: its jobs,
page counts, fact counts, questions and the letter's reading, the report and its sign checks, closing,
offline copies, named-expert changes and deadline counts. Counts, codes, statuses, timestamps and ids
only: never names, page text, the case label, identifiers or dates of birth (rule 6). The database
function `app.support_case_diagnostics(case)` answers current staff only, and nothing to anyone else.
"""

from __future__ import annotations

import json
import uuid
from datetime import date, timedelta

import psycopg
import pytest

from app.core.db import Database
from tests.fixtures.test_organisations_world import World, world  # noqa: F401
from tests.support.db import ClientFor, scope_for

pytestmark = pytest.mark.anyio

BLOCKS = {"jobs", "pages", "facts", "questions", "report", "close", "offline_packages", "named_expert_history",
          "deadlines"}  # fmt: skip


@pytest.fixture
def staff(world: World) -> uuid.UUID:
    person = world.seed.person(full_name="Support")
    world.seed.support_staff(person)
    return person


def _stuck_case(world: World) -> dict[str, uuid.UUID]:
    """A case stuck in many places at once, with words in it that must never reach support."""
    a, seed, expert = world.admin, world.seed, world["dr_k"]
    case = world.team_case()
    doc = seed.document(case)
    read, unreadable, quarantined, old, new, unread = (seed.page(case, doc, n) for n in range(1, 7))
    a.execute("update app.pages set unreadable = true, needs_clearer_copy = true where id = %s", (unreadable,))
    a.execute("update app.pages set quarantined = true where id = %s", (quarantined,))
    a.execute("update app.pages set superseded_by_page_id = %s where id = %s", (new, old))
    a.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 30)",
        (case, read, expert),
    )
    # Someone other than the named expert reading a page does not count for the named expert.
    a.execute(
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds) values (%s, %s, %s, 30)",
        (case, unread, world["dr_m"]),
    )
    failed = seed.job(case, kind="process_pages")
    a.execute("update app.jobs set status = 'running', attempts = 3 where id = %s", (failed,))
    a.execute("update app.jobs set status = 'failed', error_code = 'model_unavailable' where id = %s", (failed,))
    paused = seed.job(case, kind="process_bundle")
    a.execute("update app.jobs set status = 'running' where id = %s", (paused,))
    a.execute("update app.jobs set status = 'paused', error_code = 'cost_guard' where id = %s", (paused,))
    facts = []
    for page in (read, read, read):
        fid = uuid.uuid4()
        a.execute(
            "insert into app.facts (id, case_id, page_id, kind, value, word_ids) values (%s, %s, %s, 'note',"
            " 'Mrs Secret Patient', '{w1}')",
            (fid, case, page),
        )
        facts.append(fid)
    a.execute("update app.facts set status = 'verified' where id = %s", (facts[0],))
    a.execute(
        "insert into app.fact_flags (case_id, fact_id, flagged_for, person_id) values (%s, %s, 'recheck', null)",
        (case, facts[0]),
    )
    letter = seed.document(case)
    a.execute(
        "insert into app.instruction_versions (case_id, version_no, kind, document_id, received_on)"
        " values (%s, 1, 'letter', %s, current_date)",
        (case, letter),
    )
    extract = seed.job(case, kind="extract_questions")
    a.execute("update app.jobs set status = 'running' where id = %s", (extract,))
    a.execute("update app.jobs set status = 'failed', error_code = 'service_busy' where id = %s", (extract,))
    version = uuid.uuid4()
    a.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name) values (%s, %s, 1, 'report',"
        " 'Report for Mrs Secret Patient')",
        (version, case),
    )
    results = [
        {"key": "declaration_present", "outcome": "passed", "source": "pack", "count": 0, "fixes": []},
        {"key": "facts_verified", "outcome": "failed", "source": "core", "count": 2, "fixes": [],
         "text": "Mrs Secret Patient was seen"},
        {"key": "dob_matches", "outcome": "not_run", "source": "core", "count": 0, "fixes": [],
         "code": "no_date_of_birth"},
    ]  # fmt: skip
    a.execute(
        "insert into app.final_check_runs (case_id, report_version_id, results, passed) values (%s, %s, %s, false)",
        (case, version, json.dumps(results)),
    )
    a.execute(
        "insert into app.report_settings (case_id, acknowledged_declaration_version) values (%s, 'old-wording')",
        (case,),
    )
    a.execute(
        "insert into app.signatures (expert_id, method, storage_path, sha256, width, height)"
        " values (%s, 'drawn', 'sig/path', %s, 300, 100)",
        (expert, "0" * 64),
    )
    a.execute(
        "insert into app.legal_holds (case_id, reason, set_by, until) values (%s, 'appeal', %s, %s)",
        (case, expert, date(2027, 1, 31)),
    )
    export = seed.job(case, kind="export_case")
    a.execute("update app.jobs set status = 'running' where id = %s", (export,))
    a.execute("update app.jobs set status = 'done' where id = %s", (export,))
    a.execute(
        "insert into app.audit_log (case_id, person_id, action, target) values (%s, %s, 'case.export_downloaded',"
        " %s)",
        (case, expert, f"export:{export}"),
    )
    package = uuid.uuid4()
    a.execute(
        "insert into app.offline_packages (id, case_id, device_label, format_version, expires_at)"
        " values (%s, %s, 'Mrs Secret Patient laptop', 1, now() + interval '3 days')",
        (package, case),
    )
    a.execute("update app.offline_packages set stale = true, stale_reason = 'new_version', stale_at = now()"
              " where id = %s", (package,))  # fmt: skip
    a.execute(
        "insert into app.audit_log (case_id, person_id, action, target) values (%s, %s, 'case.named_expert_changed',"
        " %s)",
        (case, world["owner"], f"case:{case}"),
    )
    for due, done in ((date.today() - timedelta(days=3), False), (date.today() + timedelta(days=9), False),
                      (date.today() + timedelta(days=2), True)):  # fmt: skip
        a.execute(
            "insert into app.deadlines (case_id, kind, due_on, timezone, created_from, source, done_at)"
            " values (%s, 'zz_report_due', %s, 'Europe/London', 'manual', 'manual', case when %s then now() end)",
            (case, due, done),
        )
    return {"case": case, "failed": failed, "paused": paused, "extract": extract, "export": export,
            "package": package, "version": version}  # fmt: skip


async def test_staff_see_where_a_case_is_stuck_in_counts_and_codes(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    ids = _stuck_case(world)
    resp = await (await client_for(staff)).get(f"/v1/support/cases/{ids['case']}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert BLOCKS <= set(body) and {"case", "grants", "content_access"} <= set(body)  # the old fields stay

    jobs = {j["id"]: j for j in body["jobs"]}
    assert set(jobs[str(ids["failed"])]) == {"id", "kind", "status", "attempts", "error_code", "step",
                                             "created_at", "started_at", "finished_at", "updated_at"}  # fmt: skip
    assert (jobs[str(ids["failed"])]["status"], jobs[str(ids["failed"])]["attempts"],
            jobs[str(ids["failed"])]["error_code"]) == ("failed", 3, "model_unavailable")  # fmt: skip
    assert (jobs[str(ids["paused"])]["status"], jobs[str(ids["paused"])]["error_code"]) == ("paused", "cost_guard")

    # Six pages: one read by the named expert, one superseded and one quarantined need no reading.
    assert body["pages"] == {"total": 6, "unread_by_named_expert": 3, "unreadable": 1, "needs_clearer_copy": 1,
                             "quarantined": 1, "superseded": 1}  # fmt: skip
    assert body["facts"] == {"suggested": 2, "verified": 1, "needs_recheck": 1}

    assert body["questions"]["state"] == "none"
    assert body["questions"]["instruction_versions"] == [
        {"n": 1, "read_status": "failed", "job_error_code": "service_busy"}
    ]

    report = body["report"]
    assert (report["latest_version_status"], report["locked_at"], report["served_at"]) == ("draft", None, None)
    assert report["final_check"] == {"status": "failed", "item_codes": ["facts_verified", "dob_matches"]}
    assert report["declaration_acknowledged"] is True
    assert (report["signature_present"], report["signature_readable"]) == (True, True)
    # The acknowledged wording is not the fixture pack's current wording: a correction waits to be read.
    assert report["pack_correction_pending"] is True

    close = body["close"]
    assert (close["status"], close["deletion_started"]) == ("active", False)
    assert close["hold"] == {"present": True, "until": "2027-01-31", "reason_code": "appeal"}
    assert close["export"] == {"status": "done", "downloaded": True}

    [package] = body["offline_packages"]
    assert (package["id"], package["state"], package["stale_reason"], package["downloaded_at"]) == (
        str(ids["package"]), "built", "new_version", None,
    )  # fmt: skip
    assert package["expires_at"] is not None
    assert [set(c) for c in body["named_expert_history"]] == [{"changed_at"}]
    assert body["deadlines"] == {"open": 1, "overdue": 1, "done": 1, "cancelled": 0}


async def test_no_names_text_label_or_device_words_ever_reach_support(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    ids = _stuck_case(world)
    text = (await (await client_for(staff)).get(f"/v1/support/cases/{ids['case']}")).text
    for words in ("Mrs Secret Patient", "Case Z", "Dr K", "Owner", "laptop", "sig/path", "was seen", "@example"):
        assert words not in text, words


async def test_an_empty_case_answers_zero_counts_and_empty_lists(
    client_for: ClientFor, world: World, staff: uuid.UUID
) -> None:
    case = world.team_case()
    body = (await (await client_for(staff)).get(f"/v1/support/cases/{case}")).json()
    assert body["jobs"] == [] and body["offline_packages"] == [] and body["named_expert_history"] == []
    assert body["pages"] == dict.fromkeys(
        ("total", "unread_by_named_expert", "unreadable", "needs_clearer_copy", "quarantined", "superseded"), 0
    )
    assert body["facts"] == {"suggested": 0, "verified": 0, "needs_recheck": 0}
    assert body["questions"] == {"state": "none", "instruction_versions": []}
    assert body["report"] == {
        "latest_version_status": None, "locked_at": None, "served_at": None,
        "final_check": {"status": "not_run", "item_codes": []}, "declaration_acknowledged": False,
        "signature_present": False, "signature_readable": False, "pack_correction_pending": False,
    }  # fmt: skip
    assert body["close"] == {
        "status": "active", "delete_after": None, "hold": {"present": False, "until": None, "reason_code": None},
        "deletion_started": False, "export": {"status": "none", "downloaded": False},
    }  # fmt: skip
    assert body["deadlines"] == {"open": 0, "overdue": 0, "done": 0, "cancelled": 0}


async def test_questions_to_check_and_a_letter_read(client_for: ClientFor, world: World, staff: uuid.UUID) -> None:
    a = world.admin
    case = world.team_case()
    letter = world.seed.document(case)
    vid = a.execute(
        "insert into app.instruction_versions (case_id, version_no, kind, document_id, received_on)"
        " values (%s, 1, 'letter', %s, current_date) returning id",
        (case, letter),
    ).fetchone()[0]
    a.execute(
        "insert into app.instruction_extractions (instruction_version_id, case_id, questions, assumptions, rejected,"
        " model_task, prompt_version) values (%s, %s, 1, 0, 0, 'T-1', 'v1')",
        (vid, case),
    )
    a.execute(
        "insert into app.questions (case_id, instruction_version_id, number, text_verbatim, source_word_ids, kind)"
        " values (%s, %s, 1, 'Was Mrs Secret Patient seen?', '{}', 'breach')",
        (case, vid),
    )
    resp = await (await client_for(staff)).get(f"/v1/support/cases/{case}")
    assert resp.json()["questions"] == {
        "state": "to_check", "instruction_versions": [{"n": 1, "read_status": "read", "job_error_code": None}],
    }  # fmt: skip
    assert "Secret" not in resp.text


async def test_the_database_function_answers_current_staff_only(
    api_db: Database, world: World, staff: uuid.UUID, admin: psycopg.Connection
) -> None:
    case = world.team_case()
    for person, answered in ((staff, True), (world["dr_k"], False), (world["owner"], False)):
        async with api_db.request(scope_for(person)) as conn:
            cur = await conn.execute("select app.support_case_diagnostics(%s)", (case,))
            row = await cur.fetchone()
        assert (row is not None and row[0] is not None) == answered, person
