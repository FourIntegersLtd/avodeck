"""Contracts slice I uses to reach slice G's data and to know when its trial packs and copies are out of date.

- `hearings.hearing_state`: a hearing's status, version and the hearing that replaced it (no venue, no text).
- `written_answers.served_answers`: the served rounds of written questions with their answers, opened with
  slice G's seal, for the trial pack and the offline copy (S11).
- `report_changes.add_report_changes`: a changed position in a joint statement becomes slice G
  `report_changes` rows, one per served paragraph (rule 8: the served report itself never changes).
- `trial_freshness`: what a pack was built from, and why it is stale now (state-machines.md "Trial pack and
  offline package freshness": hearing change, new pages, a newer served version, a re-issued court bundle).

All run on the caller's connection, so row-level security applies: an outsider gets nothing.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import psycopg
import pytest

from app.core.contracts import hearings, report_changes, trial_freshness, written_answers
from app.core.contracts.served import PARAGRAPH_PURPOSE
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from tests.support.db import scope_for
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CRYPTO = CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    expert, viewer, outsider = seed.person(), seed.person(), seed.person()
    org = seed.org(kind="team", members={expert: "expert", viewer: "expert"})
    seed.org(kind="personal", members={outsider: "owner"})
    case = seed.case(org, expert, status="active", members={viewer: "viewer"})
    doc = seed.document(case)
    for n in (1, 2, 3):
        seed.page(case, doc, n)
    return {"expert": expert, "viewer": viewer, "outsider": outsider, "case": case, "doc": doc}


def draft_version(admin: psycopg.Connection, case: uuid.UUID, version_no: int, kind: str = "main") -> uuid.UUID:
    version = uuid.uuid4()
    admin.execute(
        "insert into app.report_versions (id, case_id, version_no, type, display_name) values (%s, %s, %s, %s, 'Zz')",
        (version, case, version_no, kind),
    )
    return version


def serve(admin: psycopg.Connection, version: uuid.UUID) -> uuid.UUID:
    admin.execute("update app.report_versions set status = 'locked', sha256 = %s where id = %s", ("a" * 64, version))
    admin.execute("update app.report_versions set status = 'served' where id = %s", (version,))
    return version


def served_version(admin: psycopg.Connection, case: uuid.UUID, version_no: int, kind: str = "main") -> uuid.UUID:
    return serve(admin, draft_version(admin, case, version_no, kind))


def hearing(admin: psycopg.Connection, case: uuid.UUID, starts_on: str = "2027-03-01") -> uuid.UUID:
    row = admin.execute(
        "insert into app.hearings (case_id, type, starts_on, ends_on) values (%s, 'zz_trial', %s, %s) returning id",
        (case, starts_on, starts_on),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def adjourn(admin: psycopg.Connection, case: uuid.UUID, old: uuid.UUID) -> uuid.UUID:
    new = hearing(admin, case, "2027-09-01")
    admin.execute("update app.hearings set status = 'adjourned', replaced_by_hearing_id = %s where id = %s", (new, old))
    return new


async def as_person(api_db: Database, person: uuid.UUID, fn):  # type: ignore[no-untyped-def]
    async with api_db.request(scope_for(person)) as conn:
        return await fn(conn)


# --- hearings ------------------------------------------------------------------------------------------------


async def test_a_hearing_shows_its_status_version_and_replacement(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    case, old = world["case"], hearing(admin, world["case"])
    state = await as_person(api_db, world["expert"], lambda c: hearings.hearing_state(c, case, old))
    assert state is not None and state.status == "scheduled" and state.replaced_by_hearing_id is None
    new = adjourn(admin, case, old)
    after = await as_person(api_db, world["viewer"], lambda c: hearings.hearing_state(c, case, old))
    assert after is not None and after.status == "adjourned" and after.replaced_by_hearing_id == new
    assert after.version > state.version and after.changed_at is not None
    assert await as_person(api_db, world["outsider"], lambda c: hearings.hearing_state(c, case, old)) is None
    other = await as_person(api_db, world["expert"], lambda c: hearings.hearing_state(c, uuid.uuid4(), old))
    assert other is None


# --- written answers -----------------------------------------------------------------------------------------


def test_the_answer_seal_is_the_one_slice_g_uses() -> None:
    from app.features.after_serving import common

    assert written_answers.ANSWER_PURPOSE == common.ANSWER
    assert report_changes.OLD_PURPOSE == common.CHANGE_OLD
    assert report_changes.NEW_PURPOSE == common.CHANGE_NEW
    assert report_changes.REASON_PURPOSE == common.CHANGE_REASON


async def add_round(
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    case: uuid.UUID,
    number: int,
    answers: list[str],
    *,
    serve: bool,
) -> uuid.UUID:
    row = admin.execute(
        "insert into app.written_question_rounds (case_id, number, received_on) values (%s, %s, '2026-07-01')"
        " returning id",
        (case, number),
    ).fetchone()
    assert row is not None
    round_id = row[0]
    for n, text in enumerate(answers, start=1):
        question = await CRYPTO.encrypt(aconn, case, b"Zz asked", purpose="written_question")
        answer = await CRYPTO.encrypt(aconn, case, text.encode(), purpose=written_answers.ANSWER_PURPOSE)
        admin.execute(
            "insert into app.written_questions (case_id, round_id, number, text_verbatim) values (%s, %s, %s, %s)",
            (case, round_id, n, question),
        )
        admin.execute(
            "update app.written_questions set answer_text = %s, status = 'answered'"
            " where round_id = %s and number = %s",
            (answer, round_id, n),
        )
    version = served_version(admin, case, 10 + number, "answers")
    admin.execute(
        "update app.written_question_rounds set status = 'answered', answers_version_id = %s where id = %s",
        (version, round_id),
    )
    if serve:
        admin.execute("update app.written_question_rounds set status = 'served' where id = %s", (round_id,))
    return round_id  # type: ignore[no-any-return]


async def test_served_rounds_come_with_their_answers_in_order(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    await add_round(admin, aconn, case, 2, ["Zz later"], serve=True)
    await add_round(admin, aconn, case, 1, ["Zz first", "Zz second"], serve=True)
    await add_round(admin, aconn, case, 3, ["Zz not yet served"], serve=False)
    rounds = await as_person(api_db, world["viewer"], lambda c: written_answers.served_answers(c, CRYPTO, case))
    assert [r.round_no for r in rounds] == [1, 2]
    assert [(a.question_no, a.text) for a in rounds[0].answers] == [(1, "Zz first"), (2, "Zz second")]
    assert rounds[1].answers[0].text == "Zz later" and rounds[1].answers_version_id is not None
    hidden = await as_person(api_db, world["outsider"], lambda c: written_answers.served_answers(c, CRYPTO, case))
    assert hidden == []


# --- report changes ------------------------------------------------------------------------------------------


async def served_paragraph(
    admin: psycopg.Connection, aconn: psycopg.AsyncConnection, case: uuid.UUID
) -> tuple[uuid.UUID, uuid.UUID]:
    version = draft_version(admin, case, 1)
    sealed = await CRYPTO.encrypt(aconn, case, b"Zz served words", purpose=PARAGRAPH_PURPOSE)
    row = admin.execute(
        "insert into app.report_paragraphs (case_id, report_version_id, para_no, section, content_control_id, text)"
        " values (%s, %s, 1, 'opinion', 'section:opinion', %s) returning id",
        (case, version, sealed),
    ).fetchone()
    assert row is not None
    return serve(admin, version), row[0]


async def test_a_changed_position_becomes_a_report_change_sealed_as_slice_g_seals_it(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    version, paragraph = await served_paragraph(admin, aconn, case)
    change = report_changes.NewReportChange(version, paragraph, "change_of_opinion", "Zz old", "Zz new", "Zz why")
    ids = await as_person(
        api_db, world["expert"], lambda c: report_changes.add_report_changes(c, CRYPTO, case, [change])
    )
    assert len(ids) == 1
    row = admin.execute(
        "select kind, paragraph_id, served_version_id, old_text, new_text, reason, created_by"
        " from app.report_changes where id = %s",
        (ids[0],),
    ).fetchone()
    assert row is not None
    kind, para, served, old, new, reason, by = row
    assert (kind, para, served, by) == ("change_of_opinion", paragraph, version, world["expert"])
    opened = [
        (await CRYPTO.decrypt(aconn, case, bytes(v), purpose=p)).decode()
        for v, p in (
            (old, report_changes.OLD_PURPOSE),
            (new, report_changes.NEW_PURPOSE),
            (reason, report_changes.REASON_PURPOSE),
        )
    ]
    assert opened == ["Zz old", "Zz new", "Zz why"]
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s and kind = 'find_dependents'", (case,))
    assert jobs.fetchone() == (1,)
    audited = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'report_change.added'", (case,)
    )
    assert audited.fetchone() == (1,)


async def test_only_someone_who_writes_opinions_adds_a_report_change(
    api_db: Database, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, world: dict[str, uuid.UUID]
) -> None:
    case = world["case"]
    version, paragraph = await served_paragraph(admin, aconn, case)
    change = report_changes.NewReportChange(version, paragraph, "correction", "Zz old", "Zz new", "Zz why")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        await as_person(api_db, world["viewer"], lambda c: report_changes.add_report_changes(c, CRYPTO, case, [change]))
    assert (
        await as_person(api_db, world["expert"], lambda c: report_changes.add_report_changes(c, CRYPTO, case, [])) == []
    )


# --- trial freshness -----------------------------------------------------------------------------------------


async def snapshot(
    api_db: Database, world: dict[str, uuid.UUID], hearing_id: uuid.UUID | None, bundle: uuid.UUID | None = None
) -> trial_freshness.BuiltFrom:
    case = world["case"]
    return await as_person(  # type: ignore[no-any-return]
        api_db, world["expert"], lambda c: trial_freshness.built_from(c, case, hearing_id, bundle)
    )


async def reason(api_db: Database, world: dict[str, uuid.UUID], built: trial_freshness.BuiltFrom) -> str | None:
    case = world["case"]
    return await as_person(  # type: ignore[no-any-return]
        api_db, world["viewer"], lambda c: trial_freshness.stale_reason(c, case, built)
    )


async def test_a_pack_records_what_it_was_built_from_and_is_current_until_something_changes(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served = served_version(admin, world["case"], 1)
    trial = hearing(admin, world["case"])
    built = await snapshot(api_db, world, trial)
    assert built.hearing_id == trial and built.hearing_version == 1
    assert built.last_page_no == 3 and built.served_version_id == served
    assert await reason(api_db, world, built) is None


async def test_an_adjourned_hearing_makes_its_packs_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served_version(admin, world["case"], 1)
    trial = hearing(admin, world["case"])
    built = await snapshot(api_db, world, trial)
    adjourn(admin, world["case"], trial)
    assert await reason(api_db, world, built) == "hearing_adjourned"


async def test_a_moved_hearing_makes_its_packs_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served_version(admin, world["case"], 1)
    trial = hearing(admin, world["case"])
    built = await snapshot(api_db, world, trial)
    admin.execute("update app.hearings set starts_on = '2027-03-02', ends_on = '2027-03-02' where id = %s", (trial,))
    assert await reason(api_db, world, built) == "hearing_changed"


async def test_new_pages_make_packs_stale(
    api_db: Database, admin: psycopg.Connection, seed: Seed, world: dict[str, uuid.UUID]
) -> None:
    served_version(admin, world["case"], 1)
    built = await snapshot(api_db, world, None)
    seed.page(world["case"], world["doc"], 4)
    assert await reason(api_db, world, built) == "new_pages"


async def test_a_newer_served_version_makes_packs_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served_version(admin, world["case"], 1)
    built = await snapshot(api_db, world, None)
    served_version(admin, world["case"], 2, "supplementary")
    assert await reason(api_db, world, built) == "new_served_version"


async def test_a_reissued_court_bundle_makes_packs_built_on_the_old_one_stale(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID]
) -> None:
    served_version(admin, world["case"], 1)
    row = admin.execute(
        "insert into app.court_bundles (case_id, storage_path, sha256) values (%s, 'zz', %s) returning id",
        (world["case"], "b" * 64),
    ).fetchone()
    assert row is not None
    built = await snapshot(api_db, world, None, row[0])
    assert await reason(api_db, world, built) is None
    admin.execute("update app.court_bundles set status = 'superseded' where id = %s", (row[0],))
    assert await reason(api_db, world, built) == "court_bundle_superseded"


def test_a_row_without_a_snapshot_is_judged_on_its_stored_flag_only() -> None:
    built = trial_freshness.built_from_row({"hearing_id": str(uuid.uuid4()), "stale": False})
    assert built == trial_freshness.BuiltFrom(hearing_id=built.hearing_id)
    assert built.hearing_version is None and built.last_page_no is None and built.served_version_id is None
    full = trial_freshness.built_from_row(
        {
            "hearing_id": None,
            "built_hearing_version": None,
            "built_last_page_no": 7,
            "built_served_version_id": str(uuid.UUID(int=5)),
            "court_bundle_id": None,
        }
    )
    assert full.last_page_no == 7 and full.served_version_id == uuid.UUID(int=5)
    assert set(trial_freshness.REASONS) == {
        "court_bundle_superseded",
        "hearing_adjourned",
        "hearing_changed",
        "new_served_version",
        "new_pages",
    }


async def test_the_timeline_reads_the_newest_pack_as_stale_when_its_snapshot_is_out_of_date(
    api_db: Database, admin: psycopg.Connection, world: dict[str, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.contracts import trial_steps

    case = world["case"]
    admin.execute(
        "insert into app.trial_packs (case_id, sections, sha256, storage_path, page_count)"
        " values (%s, '{report}', %s, 'zz', 1)",
        (case, "c" * 64),
    )
    step = await as_person(api_db, world["viewer"], lambda c: trial_steps.latest_pack_step(c, case))
    assert step is not None and step.stale is False
    seen: list[trial_freshness.BuiltFrom] = []

    async def out_of_date(conn: object, case_id: uuid.UUID, built: trial_freshness.BuiltFrom) -> str:
        seen.append(built)
        return "hearing_changed"

    monkeypatch.setattr(trial_freshness, "stale_reason", out_of_date)
    step = await as_person(api_db, world["viewer"], lambda c: trial_steps.latest_pack_step(c, case))
    assert step is not None and step.stale is True and seen == [trial_freshness.BuiltFrom()]
