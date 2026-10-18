"""Slice D's two worker jobs (docs/slices/D-understand.md; architecture 6.3 T-11 and T-13a).

`rerun_checks` first works out when each fact reached the clinician (code, never the letter's own date, rule 1
and the "as the clinician saw it" rule), then runs the named expert's checks on checked facts and writes
findings. `suggest_links` only suggests (rule 1). Both run under the fixture packs and the launch packs, so
no kind, source, check or lane is written here (rule 11). No text reaches a log or a job row (rule 6).
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, date, timedelta
from typing import Any

import pytest
from psycopg.types.json import Jsonb

from app.core.ai.embed import RecordedEmbedder
from app.core.ai.kit import AiKit
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext
from app.core.packs import MergedPack, default_library
from app.features.pipeline.paths import word_purpose
from app.features.understand.services import UnderstandServices, configure
from tests.support.ai import ScriptedBedrock, cell, response
from tests.support.db import scope_for
from tests.support.facts_seed import insert_fact
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=4)
    await database.open()
    try:
        yield database
    finally:
        await database.close()


@pytest.fixture
def crypto() -> CaseCrypto:
    return CaseCrypto(LocalKeyProvider.generate())


@pytest.fixture(autouse=True)
def services(crypto: CaseCrypto) -> Any:
    configure(UnderstandServices(kit=None, crypto=crypto, embedder=RecordedEmbedder(), packs=default_library()))
    yield
    configure(None)


@dataclass
class Bundle:
    seed: Seed
    db: Database
    crypto: CaseCrypto
    pack: MergedPack
    expert: uuid.UUID
    case: uuid.UUID
    document: uuid.UUID
    arriving: str  # a fact kind that arrives with a mark
    own: str  # a fact kind the clinician wrote
    own_source: str
    other_source: str
    pages: int = 0

    async def page(self, source: str, words: list[str] | None = None) -> uuid.UUID:
        """A numbered page of this source with sealed words w1..wn."""
        self.pages += 1
        page = self.seed.page(self.case, self.document, self.pages)
        self.seed.admin.execute("update app.pages set source_type = %s where id = %s", (source, page))
        async with self.db.job(self.case) as conn:
            for n, text in enumerate(words or ["x"] * 8, 1):
                sealed = await self.crypto.encrypt(conn, self.case, text.encode(), purpose=word_purpose(page, f"w{n}"))
                await conn.execute(
                    "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                    " values (%s, %s, %s, %s, %s, %s, 0.9, 'ocr')",
                    (page, self.case, n, f"w{n}", sealed, [0, 0, 1, 0, 1, 1, 0, 1]),
                )
        return page

    def evidence(self, page: uuid.UUID, *items: tuple[str, str | None, str]) -> None:
        listed = [{"kind": k, "date": d, "date_text": None, "word_ids": [w]} for k, d, w in items]
        self.seed.admin.execute(
            "insert into app.page_analysis (page_id, case_id, availability_evidence) values (%s, %s, %s)",
            (page, self.case, Jsonb(listed)),
        )

    def fact(self, page: uuid.UUID, kind: str, word: str, day: str, **more: Any) -> uuid.UUID:
        return insert_fact(
            self.seed.admin, self.case, page, kind, word_ids=[word], event_date=date.fromisoformat(day), **more
        )

    def override(self, fact: uuid.UUID, day: str | None) -> None:
        self.seed.admin.execute(
            "insert into app.availability_overrides (case_id, fact_id, available_at, unknown, reason, person_id)"
            " values (%s, %s, %s, %s, 'told so', %s)",
            (self.case, fact, day, day is None, self.expert),
        )

    def availability(self) -> dict[uuid.UUID, tuple[date | None, str | None]]:
        rows = self.seed.admin.execute(
            "select id, available_at, availability_basis from app.facts where case_id = %s", (self.case,)
        ).fetchall()
        return {r[0]: (r[1].astimezone(UTC).date() if r[1] else None, r[2]) for r in rows}

    async def run(self, kind: str = "rerun_checks") -> uuid.UUID:
        job = self.seed.job(self.case, kind=kind, requested_by=self.expert)
        from app.features.understand import jobs

        await getattr(jobs, kind)(JobContext(self.db, job, self.case, kind, 1))
        return job


def build(seed: Seed, db: Database, crypto: CaseCrypto, combo: Combo) -> Bundle:
    pack = default_library().combine(*combo.codes)
    arriving = sorted(pack.arriving_kinds())[0]
    own = next(k["key"] for k in pack.fact_kinds() if k["key"] not in pack.arriving_kinds())
    own_source = sorted(pack.own_record_sources())[0]
    other_source = next(s["key"] for s in pack.source_types() if s["key"] not in pack.own_record_sources())
    expert = seed.person()
    case = seed.case(seed.org(members={expert: "owner"}), expert, status="active", packs=combo.case_row())
    return Bundle(seed, db, crypto, pack, expert, case, seed.document(case), arriving, own, own_source, other_source)


@pytest.fixture(params=BOTH, ids=lambda c: c.jurisdiction)
def bundle(request: pytest.FixtureRequest, seed: Seed, worker_db: Database, crypto: CaseCrypto) -> Bundle:
    return build(seed, worker_db, crypto, request.param)


# --- rerun_checks: when each fact reached the clinician -------------------------------------------------


async def test_availability_comes_from_marks_own_record_and_overrides_never_the_letters_own_date(
    bundle: Bundle,
) -> None:
    b = bundle
    mine = b.fact(await b.page(b.own_source), b.own, "w1", "2024-03-01")
    marked_page = await b.page(b.other_source)
    marked = b.fact(marked_page, b.arriving, "w2", "2024-03-05")
    b.evidence(marked_page, ("received", "2024-03-20", "w5"))
    unmarked = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-06")
    told = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-07")
    b.override(told, "2024-04-01")
    nobody = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-08")
    b.override(nobody, None)
    rejected = b.fact(await b.page(b.other_source), b.arriving, "w2", "2024-03-09", status="rejected")

    await b.run()

    got = b.availability()
    assert got[mine] == (date(2024, 3, 1), "own_record")
    assert got[marked] == (date(2024, 3, 20), "received")
    assert got[unmarked] == (None, None)
    assert got[told] == (date(2024, 4, 1), "expert_override")
    assert got[nobody] == (None, "expert_unknown")
    assert got[rejected] == (None, None)


async def test_a_second_run_changes_nothing_and_a_new_override_is_picked_up(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = b.fact(page, b.arriving, "w2", "2024-03-05")
    await b.run()
    assert b.availability()[fact] == (None, None)
    b.override(fact, "2024-05-02")
    await b.run()
    await b.run()
    assert b.availability()[fact] == (date(2024, 5, 2), "expert_override")
    version = b.seed.admin.execute("select version from app.facts where id = %s", (fact,)).fetchone()
    assert version is not None and version[0] == 2  # verified: version 2; availability never adds a version


# --- rerun_checks: the named expert's checks -----------------------------------------------------------------

DAY = date(2024, 3, 1)


def first_of(value: Any) -> str:
    return value if isinstance(value, str) else sorted(value)[0]


def the_check(pack: MergedPack, rule: str, **want: Any) -> dict[str, Any]:
    """The pack's first check of this rule type whose parameters match (pack data, never written here)."""
    for check in pack.checks():
        if check["rule"] == rule and all(check.get(k) == v for k, v in want.items()):
            return check
    raise AssertionError(f"no {rule} check in the pack")


def abnormal(b: Bundle, page: uuid.UUID, day: date = DAY, status: str = "verified") -> uuid.UUID:
    """A fact the no-action check points at, built from the check's own parameters."""
    check = the_check(b.pack, "no_action_within", label_spans=True)
    fields = dict(check.get("when") or {})
    flag = check["flag"]
    fields[flag["field"]] = flag["any_of"][0]
    return insert_fact(
        b.seed.admin,
        b.case,
        page,
        first_of(check["applies_to"]),
        fields=fields,
        value="v",
        word_ids=["w1"],
        event_date=day,
        status=status,
    )


def action(b: Bundle, page: uuid.UUID, days_after: int, status: str = "verified") -> uuid.UUID:
    check = the_check(b.pack, "no_action_within", label_spans=True)
    return insert_fact(
        b.seed.admin,
        b.case,
        page,
        first_of(check["action_kinds"]),
        value="done",
        word_ids=["w2", "w3"],
        event_date=DAY + timedelta(days=days_after),
        status=status,
    )


def findings(b: Bundle, key: str | None = None) -> list[dict[str, Any]]:
    cur = b.seed.admin.execute(
        "select check_key, rule, gentle, evidence_fact_ids, evidence_page_ids, status, decision, reason, run_id, id"
        " from app.findings where case_id = %s and (%s::text is null or check_key = %s) order by created_at, id",
        (b.case, key, key),
    )
    names = [d.name for d in cur.description or []]
    return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]


def runs(b: Bundle) -> list[tuple[uuid.UUID | None, str, list[str]]]:
    cur = b.seed.admin.execute(
        "select job_id, pack_version, checks from app.check_runs where case_id = %s order by created_at, id", (b.case,)
    )
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def use_kit(b: Bundle, *answers: dict[str, Any]) -> ScriptedBedrock:
    model = ScriptedBedrock([response(a) for a in answers])
    configure(
        UnderstandServices(
            kit=AiKit(cell(), model), crypto=b.crypto, embedder=RecordedEmbedder(), packs=default_library()
        )
    )
    return model


def labels(*pairs: tuple[uuid.UUID, str, list[str]]) -> dict[str, Any]:
    return {"labels": [{"fact_id": str(f), "label": label, "word_ids": w} for f, label, w in pairs]}


async def test_an_abnormal_entry_nothing_followed_is_found_with_its_pages_and_run(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = abnormal(b, page)
    key = the_check(b.pack, "no_action_within", label_spans=True)["key"]

    job = await b.run()

    [found] = findings(b, key)
    assert found["evidence_fact_ids"] == [fact] and found["evidence_page_ids"] == [page]
    assert (found["status"], found["decision"], found["reason"]) == ("open", None, None)
    assert found["gentle"] is bool(the_check(b.pack, "no_action_within", label_spans=True).get("gentle", False))
    [(run_job, version, keys)] = runs(b)
    assert run_job == job and found["run_id"] is not None
    assert b.pack.versions["jurisdiction"] in version and key in keys


async def test_only_checked_facts_are_looked_at(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    abnormal(b, page, status="suggested")
    abnormal(b, page, DAY + timedelta(days=40), status="rejected")
    await b.run()
    assert findings(b) == []


async def test_the_model_labels_the_words_of_an_entry_that_could_be_the_action(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source, ["w1", "seen", "treated", "later", "words", "x", "x", "x"])
    abnormal(b, page)
    done = action(b, page, 1)
    model = use_kit(b, labels((done, "action_taken", ["w2", "w3"])))
    await b.run()
    assert findings(b) == [] and len(model.requests) == 1
    assert "seen" in model.requests[0].text and "treated" in model.requests[0].text


async def test_a_label_that_is_not_an_action_leaves_the_finding(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = abnormal(b, page)
    done = action(b, page, 1)
    use_kit(b, labels((done, "no_action", ["w2"])))
    await b.run()
    assert [f["evidence_fact_ids"] for f in findings(b)] == [[fact]]


async def test_a_label_on_words_outside_the_entry_is_ignored(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = abnormal(b, page)
    done = action(b, page, 1)
    use_kit(b, labels((done, "action_taken", ["w1"])))  # w1 belongs to the other entry
    await b.run()
    assert [f["evidence_fact_ids"] for f in findings(b)] == [[fact]]


async def test_an_action_outside_the_window_is_never_offered_to_the_model(bundle: Bundle) -> None:
    b = bundle
    window = int(the_check(b.pack, "no_action_within", label_spans=True)["window_days"])
    page = await b.page(b.other_source)
    abnormal(b, page)
    action(b, page, window + 2)
    model = use_kit(b, labels())
    await b.run()
    assert model.requests == [] and len(findings(b)) == 1


async def test_checks_that_need_labels_pause_without_a_model_and_change_nothing(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    abnormal(b, page)
    action(b, page, 1)
    with pytest.raises(ProcessingPaused) as paused:
        await b.run()
    assert paused.value.reason == "model_unavailable"
    assert findings(b) == [] and runs(b) == []


async def test_only_the_named_experts_settings_apply_and_a_change_touches_only_the_new_run(bundle: Bundle) -> None:
    b = bundle
    check = the_check(b.pack, "no_action_within", label_spans=True)
    abnormal(b, await b.page(b.other_source))
    stranger = b.seed.person()
    b.seed.admin.execute(
        "insert into app.check_config (person_id, check_key, enabled) values (%s, %s, false)", (stranger, check["key"])
    )
    await b.run()
    assert len(findings(b, check["key"])) == 1

    b.seed.admin.execute(
        "insert into app.check_config (person_id, check_key, enabled) values (%s, %s, false)", (b.expert, check["key"])
    )
    await b.run()
    first, second = runs(b)
    assert check["key"] in first[2] and check["key"] not in second[2]
    assert [f["status"] for f in findings(b, check["key"])] == ["open"]  # a switched-off check closes nothing


async def test_the_experts_own_window_decides_which_entries_count(bundle: Bundle) -> None:
    b = bundle
    check = the_check(b.pack, "no_action_within", label_spans=True)
    page = await b.page(b.other_source)
    abnormal(b, page)
    done = action(b, page, int(check["window_days"]) + 2)
    b.seed.admin.execute(
        "insert into app.check_config (person_id, check_key, window_days) values (%s, %s, %s)",
        (b.expert, check["key"], int(check["window_days"]) + 10),
    )
    model = use_kit(b, labels((done, "action_taken", ["w2"])))
    await b.run()
    assert len(model.requests) == 1 and findings(b) == []


async def test_a_decided_finding_that_is_found_again_keeps_its_decision_and_is_not_repeated(
    bundle: Bundle, api_db: Database
) -> None:
    b = bundle
    abnormal(b, await b.page(b.other_source))
    await b.run()
    [first] = findings(b)
    async with api_db.request(scope_for(b.expert)) as conn:
        await conn.execute(
            "update app.findings set decision = 'explained_elsewhere', reason = 'seen in clinic' where id = %s",
            (first["id"],),
        )
    await b.run()
    [again] = findings(b)
    assert again["id"] == first["id"]
    assert (again["status"], again["decision"], again["reason"]) == ("open", "explained_elsewhere", "seen in clinic")
    assert len(runs(b)) == 2


async def test_a_finding_that_no_longer_matches_is_marked_gone_never_deleted_or_decided(bundle: Bundle) -> None:
    b = bundle
    page = await b.page(b.other_source)
    fact = abnormal(b, page)
    await b.run()
    b.seed.admin.execute("update app.facts set status = 'rejected' where id = %s", (fact,))
    await b.run()
    [gone] = findings(b)
    assert (gone["status"], gone["decision"]) == ("gone", None)
    await b.run()  # nothing new is invented
    assert len(findings(b)) == 1


async def test_linked_checks_look_only_at_entries_the_expert_linked_to_a_question(bundle: Bundle) -> None:
    b = bundle
    check = the_check(b.pack, "field_missing", scope="linked")
    page = await b.page(b.other_source)
    kind = first_of(check["applies_to"])
    linked = insert_fact(b.seed.admin, b.case, page, kind, value="v", word_ids=["w1"], event_date=DAY)
    insert_fact(b.seed.admin, b.case, page, kind, value="v", word_ids=["w2"], event_date=DAY + timedelta(days=9))
    question = b.seed.admin.execute(
        "insert into app.questions (case_id, number, text_verbatim, kind) values (%s, 1, 'q', 'k') returning id",
        (b.case,),
    ).fetchone()
    assert question is not None
    b.seed.admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, status)"
        " values (%s, %s, %s, 'expert', 'confirmed')",
        (b.case, linked, question[0]),
    )
    await b.run()
    [found] = findings(b, check["key"])
    assert found["evidence_fact_ids"] == [linked]
    assert found["gentle"] is bool(check.get("gentle", False))


async def test_no_patient_text_reaches_the_model_the_logs_or_the_job_row(
    bundle: Bundle, caplog: pytest.LogCaptureFixture
) -> None:
    b = bundle
    name = "Margaret Ashworth"
    async with b.db.job(b.case) as conn:
        role = b.pack.party_roles()[0]["key"]
        blob = await b.crypto.encrypt(conn, b.case, name.encode(), purpose="party_name")
        await conn.execute(
            "insert into app.case_parties (case_id, role_key, name) values (%s, %s, %s)", (b.case, role, blob)
        )
    page = await b.page(b.other_source, ["w1", "Zorbulon", "Ashworth", "rang", "Margaret", "x", "x", "x"])
    abnormal(b, page)
    done = action(b, page, 1)
    model = use_kit(b, labels((done, "action_taken", ["w2", "w3"])))
    with caplog.at_level(logging.DEBUG):
        job = await b.run()
    sent = model.requests[0].text
    assert "Ashworth" not in sent and "Zorbulon" in sent
    row = b.seed.admin.execute("select to_jsonb(j)::text from app.jobs j where id = %s", (job,)).fetchone()
    assert row is not None
    for secret in ("Zorbulon", "Ashworth"):
        assert secret not in row[0] and secret not in caplog.text
