"""The named expert's report inputs (`app.core.contracts.profile`; request from slice F).

Slice C keeps the expert's template, declarations and signature private to the expert (row-level
security on `report_templates`, `declarations`, `signatures`), and `conflict_declarations` only to the
API. Slice F builds the report in a job and on the expert's behalf, so the foundation opens exactly
the named expert's rows for one case through security-definer functions keyed by the case:
- the API caller who may build the report (`report.build`) gets the template, declarations and the
  conflicts declaration; the signature only to whoever may sign (`report.sign`);
- the case's own job (`app.job_case_id`) gets all of them;
- anyone else, and a job on another case, gets nothing.
Runs under the fixture packs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.contracts import profile
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import FIXTURE_PACKS, Seed

pytestmark = pytest.mark.anyio

SHA_A, SHA_B, SHA_C = "a" * 64, "b" * 64, "c" * 64


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, uuid.UUID]:
    w = {"expert": seed.person(), "assistant": seed.person(), "outsider": seed.person(), "other": seed.person()}
    w["org"] = seed.org(kind="team", members={w["expert"]: "expert", w["assistant"]: "assistant"})
    seed.org(kind="personal", members={w["outsider"]: "owner"})
    w["case"] = seed.case(w["org"], w["expert"], status="active", members={w["assistant"]: "assistant"})
    w["elsewhere"] = seed.case(seed.org(kind="personal", members={w["other"]: "owner"}), w["other"], status="active")
    for person, version, sha in ((w["expert"], 1, SHA_A), (w["expert"], 2, SHA_B), (w["other"], 1, SHA_C)):
        admin.execute(
            "insert into app.report_templates (owner_type, owner_id, filename, storage_path, sha256, size_bytes,"
            " heading_map, style_map, mapping, version)"
            " values ('person', %s, 'report.docx', %s, %s, 10, %s, '[]', '[]', %s)",
            (person, f"templates/{person}/{version}", sha, f'[{{"v": {version}}}]', version),
        )
    j = FIXTURE_PACKS["jurisdiction"]
    for person, jurisdiction, kind, text in (
        (w["expert"], j, "zz_truth", "Extra wording"),
        (w["expert"], j, "zz_duty", None),
        (w["expert"], "zz-elsewhere", "zz_truth", "Other place"),
        (w["other"], j, "zz_truth", "Not this expert"),
    ):
        admin.execute(
            "insert into app.declarations (expert_id, jurisdiction, kind, text) values (%s, %s, %s, %s)",
            (person, jurisdiction, kind, text),
        )
    for person, path, at in (
        (w["expert"], "sig/old", "2026-01-01"),
        (w["expert"], "sig/new", "2026-02-01"),
        (w["other"], "sig/other", "2026-03-01"),
    ):
        admin.execute(
            "insert into app.signatures (expert_id, method, storage_path, sha256, width, height, created_at)"
            " values (%s, 'drawn', %s, %s, 300, 100, %s)",
            (person, path, SHA_A, at),
        )
    admin.execute(
        "insert into app.conflict_answers (case_id, question_key, answer, detail) values"
        " (%s, 'zz_paid', 'no', null), (%s, 'zz_knows', 'yes', '\\x0102')",
        (w["case"], w["case"]),
    )
    admin.execute(
        "insert into app.conflict_declarations (case_id, outcome, created_at) values"
        " (%s, 'none', now() - interval '2 days'), (%s, 'disclosed', now() + interval '1 day')",
        (w["case"], w["case"]),
    )
    return w


def check_expert_rows(
    template: profile.ReportTemplate | None,
    declarations: list[profile.Declaration],
    conflicts: profile.ConflictDeclaration | None,
) -> None:
    assert template is not None
    assert (template.version, template.sha256) == (2, SHA_B), "the latest version for the named expert"
    assert template.storage_path.endswith("/2") and template.filename == "report.docx"
    assert template.heading_map == [{"v": 2}] and template.style_map == [] and template.mapping == []
    assert [(d.kind, d.text, d.locked_at) for d in declarations] == [
        ("zz_duty", None, None),
        ("zz_truth", "Extra wording", None),
    ], "the case's jurisdiction only, the named expert's only, by kind"
    assert conflicts is not None
    assert conflicts.outcome == "disclosed" and conflicts.out_of_date is False
    assert sorted((a.question_key, a.answer, a.detail) for a in conflicts.answers) == [
        ("zz_knows", "yes", b"\x01\x02"),
        ("zz_paid", "no", None),
    ], "the detail stays sealed under the case key"


async def test_the_named_expert_gets_their_report_inputs(api_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with api_db.request(scope_for(world["expert"])) as conn:
        template = await profile.report_template(conn, world["case"])
        declarations = await profile.declarations(conn, world["case"])
        conflicts = await profile.conflict_declaration(conn, world["case"])
        signature = await profile.signature(conn, world["case"])
    check_expert_rows(template, declarations, conflicts)
    assert signature is not None
    assert (signature.storage_path, signature.sha256, signature.method, signature.width, signature.height) == (
        "sig/new",
        SHA_A,
        "drawn",
        300,
        100,
    ), "the newest signature is the one used"


async def test_an_assistant_who_builds_reports_gets_the_named_experts_inputs_but_not_the_signature(
    api_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["assistant"])) as conn:
        template = await profile.report_template(conn, world["case"])
        declarations = await profile.declarations(conn, world["case"])
        conflicts = await profile.conflict_declaration(conn, world["case"])
        assert await profile.signature(conn, world["case"]) is None
    check_expert_rows(template, declarations, conflicts)


async def test_the_cases_own_job_gets_every_input(worker_db: Database, world: dict[str, uuid.UUID]) -> None:
    async with worker_db.job(world["case"]) as conn:
        template = await profile.report_template(conn, world["case"])
        declarations = await profile.declarations(conn, world["case"])
        conflicts = await profile.conflict_declaration(conn, world["case"])
        signature = await profile.signature(conn, world["case"])
    check_expert_rows(template, declarations, conflicts)
    assert signature is not None and signature.storage_path == "sig/new"


async def test_an_outsider_and_a_job_on_another_case_get_nothing(
    api_db: Database, worker_db: Database, world: dict[str, uuid.UUID]
) -> None:
    async with api_db.request(scope_for(world["outsider"])) as conn:
        assert await profile.report_template(conn, world["case"]) is None
        assert await profile.declarations(conn, world["case"]) == []
        assert await profile.conflict_declaration(conn, world["case"]) is None
        assert await profile.signature(conn, world["case"]) is None
    async with worker_db.job(world["elsewhere"]) as conn:
        assert await profile.report_template(conn, world["case"]) is None
        assert await profile.declarations(conn, world["case"]) == []
        assert await profile.conflict_declaration(conn, world["case"]) is None
        assert await profile.signature(conn, world["case"]) is None


async def test_a_conflict_answer_changed_after_the_latest_declaration_makes_it_out_of_date(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    admin.execute(
        "update app.conflict_declarations set created_at = now() - interval '1 day' where case_id = %s",
        (world["case"],),
    )
    admin.execute("update app.conflict_answers set answer = 'no', detail = null where case_id = %s", (world["case"],))
    async with api_db.request(scope_for(world["expert"])) as conn:
        conflicts = await profile.conflict_declaration(conn, world["case"])
    assert conflicts is not None and conflicts.out_of_date is True


async def test_no_template_signature_or_declaration_yet_is_none(api_db: Database, seed: Seed) -> None:
    expert = seed.person()
    case = seed.case(seed.org(kind="personal", members={expert: "owner"}), expert, status="active")
    async with api_db.request(scope_for(expert)) as conn:
        assert await profile.report_template(conn, case) is None
        assert await profile.signature(conn, case) is None
        assert await profile.declarations(conn, case) == []
        assert await profile.conflict_declaration(conn, case) is None
