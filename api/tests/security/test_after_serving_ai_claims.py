# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""AI audit, step 14: slice G's AI tasks read documents written by the other side of the dispute.

The opposing expert's report, the other side's written questions and court orders are treated as written by
an adversary. The model is a scripted fake, so "the injection worked" is modelled as the hostile ANSWER a
steered model would give; each test says what the server then stores, shows and does.

Each test states the SAFE behaviour. Where the application is not safe, the safe assertion is kept and the
test is marked `xfail(strict=True)` with the finding id, so a fix turns it into a failure to remove the mark.
Every "blocked" assertion has a positive control (the legitimate answer is accepted and stored).

Ids: P prompt injection, E exfiltration, O output validation, I integrity.
All text is synthetic; nothing here is a real record, and no document text is printed on failure.
"""

from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.ai.bedrock import ModelRequest
from app.core.db import Database
from app.core.storage import CaseFileStorage
from app.features.after_serving import ai_tasks
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    PAGE_TEXT,
    Scripted,
    World,
    aconn,
    add_words,
    app,
    classifications,
    ids_for,
    mailer,
    model,
    ocr,
    pack_of,
    reads,
    run_job,
    served,
    served_state,
    services,
    storage,
    store_document,
    tokens,
    worker_db,
    world,
)
from tests.http.test_after_serving_changes import change_body, run_job_id
from tests.http.test_after_serving_new_material import add_new_pages, t21d_for
from tests.http.test_after_serving_questions import DOCUMENT, QUESTIONS, open_round, t20a, t20b_for
from tests.support.ai_injection import CANARY, INJECTIONS
from tests.support.contracts import add_fact
from tests.support.db import ClientFor
from tests.support.identity import SENTENCE, leaked, record_identity
from tests.support.packs import LAUNCH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

INJECTION_NAMES = list(INJECTIONS)
FOREIGN = "Zorblaxmarker"
TWO_CLAIMS = "Report of the other expert\n1. Page 2 records ferritin 19.\n2. Page 3 records Seen again in clinic."
ONE_CLAIM = "Report of the other expert\n1. Page 2 records ferritin 19."
WEB = Path(__file__).resolve().parents[3] / "web" / "src" / "capabilities" / "after-serving"
APP = Path(__file__).resolve().parents[2] / "app" / "features" / "after_serving"


@pytest.fixture
def combo() -> Combo:
    return LAUNCH


# --- helpers --------------------------------------------------------------------------------------------


def run_of(text: str, marker: str) -> tuple[str, list[str]]:
    """The words (as the model sees them, masked) and ids of the line holding `marker`, without its number."""
    for line in text.splitlines():
        found = tokens(line)
        if marker.lower() in " ".join(w for _, w in found).lower():
            return " ".join(w for _, w in found[1:]), [i for i, _ in found[1:]]
    raise ValueError("marker not in the prompt")


def t21b_answer(
    marker: str = "ferritin 19",
    *,
    page: str = "2",
    value: str = "ferritin 19",
    ref: str | None = "1",
    extra: Callable[[str], list[dict[str, Any]]] | None = None,
) -> Callable[[ModelRequest], dict[str, Any]]:
    def answer(request: ModelRequest) -> dict[str, Any]:
        text, ids = run_of(request.text, marker)
        claim = {"text_verbatim": text, "word_ids": ids, "paragraph_ref": ref, "cited_page": page, "cited_value": value}
        return {"claims": [claim, *(extra(request.text) if extra else [])]}

    return answer


def t21c_wrong_value(request: ModelRequest) -> dict[str, Any]:
    return {
        "status": "wrong_value",
        "evidence_word_ids": ids_for(request.text, "Ferritin 9", after="[page"),
        "reason": "checked",
    }


async def register(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    w: World,
    text: str,
) -> tuple[uuid.UUID, Any]:
    document = await store_document(storage, admin, aconn, w.case, text, "opposing_report")
    queued = await (await client_for(w.assistant)).post(
        w.url("opposing-reports"), json={"document_id": str(document), "received_on": "2026-04-02"}
    )
    return document, queued


async def read_report(client_for: ClientFor, w: World, document: uuid.UUID, who: uuid.UUID | None = None) -> Any:
    resp = await (await client_for(who or w.expert)).get(w.url(f"opposing-reports/{document}"))
    assert resp.status_code == 200, resp.text
    return resp


async def checked(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    w: World,
    text: str,
) -> tuple[uuid.UUID, dict[str, Any]]:
    document, queued = await register(client_for, storage, admin, aconn, w, text)
    await run_job(worker_db, w, queued, "verify_opposing")
    report: dict[str, Any] = (await read_report(client_for, w, document)).json()
    return document, report


def snapshot(admin: psycopg.Connection, w: World, mailer: Any) -> dict[str, Any]:
    """Everything a steered answer must not be able to change."""

    def rows(sql: str) -> list[tuple[Any, ...]]:
        return [tuple(r) for r in admin.execute(sql, (w.case,)).fetchall()]

    return {
        "served": served_state(admin, w.served),  # type: ignore[arg-type]
        "facts": rows("select id, status, value from app.facts where case_id = %s order by id"),
        "versions": rows("select id, type, status from app.report_versions where case_id = %s order by id"),
        "changes": rows("select id from app.report_changes where case_id = %s order by id"),
        "jobs": rows("select kind, count(*) from app.jobs where case_id = %s group by kind order by kind"),
        "case": rows("select status, on_hold from app.cases where id = %s"),
        "mail": len(mailer.outbox),
    }


def only_in_text(requests: list[ModelRequest], marker: str = CANARY) -> None:
    """Hostile words reach `text` and nowhere else in a request (never the system prompt)."""
    assert requests, "the task was never called, so the test would prove nothing"
    for r in requests:
        assert marker in r.text, "the hostile sentence did not reach the model text, so nothing was tested"
        assert marker not in r.system
        assert marker not in str(r.context)
        assert marker not in str(r.schema)
        assert marker not in r.model_id


# --- request side ---------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", INJECTION_NAMES)
async def test_a_hostile_sentence_in_their_report_reaches_the_text_of_t21b_c_d_and_never_the_system_prompt(
    name: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    model.answers.update({"T-21b": t21b_answer(), "T-21c": t21c_wrong_value, "T-21d": t21d_for(served)})
    hostile = INJECTIONS[name]
    _, report = await checked(
        client_for, storage, admin, aconn, worker_db, served, f"{ONE_CLAIM[: -len('.')]}. {hostile}"
    )
    for task in ("T-21b", "T-21c", "T-21d"):
        requests = model.by_task(task)
        assert len(requests) == 1, task
        only_in_text(requests, "ZX-CANARY")
    system = {r.task_id: r.system for r in model.requests}
    assert system["T-21b"] == ai_tasks.prompt("t21b")[1] and system["T-21c"] == ai_tasks.prompt("t21c")[1]
    assert system["T-21d"] == ai_tasks.prompt("t21d")[1]
    # Positive control: the sentence is the other side's own words, kept as an exact piece of their report.
    assert [c["status"] for c in report["claims"]] == ["wrong_value"]
    assert report["claims"][0]["text_verbatim"].endswith(hostile)
    assert report["claims"][0]["checked_by"] == "model"
    assert CANARY not in caplog.text and "ferritin" not in caplog.text.lower()


async def test_identifiers_are_stripped_from_every_request_even_when_their_report_repeats_them(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    from tests.fixtures.test_after_serving_world import CRYPTO

    await record_identity(aconn, CRYPTO, served.case)
    model.answers.update({"T-21b": t21b_answer(), "T-21c": t21c_wrong_value, "T-21d": t21d_for(served)})
    _, report = await checked(
        client_for, storage, admin, aconn, worker_db, served, f"{ONE_CLAIM[: -len('.')]}. {SENTENCE}"
    )
    sent = [r.text for r in model.requests]
    assert {r.task_id for r in model.requests} == {"T-21b", "T-21c", "T-21d"}
    assert any("ferritin" in t for t in sent)
    assert leaked(sent) == []
    assert len(report["claims"]) == 1, "the claim is still found with its identifiers masked"


async def test_each_prompt_holds_only_what_its_task_needs_and_no_other_case(
    seed: Seed,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    person = seed.person()
    org = seed.org(kind="personal", members={person: "owner"})
    other = seed.case(org, person, status="active", packs=served.combo.case_row())
    doc = seed.document(other)
    for n in (1, 2, 3):
        page = seed.page(other, doc, n)
        await add_words(admin, aconn, other, page, f"{FOREIGN} ferritin 19 page {n}")
    add_fact(admin, other, page, value=f"{FOREIGN} ferritin 19", word_ids=["w1", "w2", "w3"])

    model.answers.update({"T-21b": t21b_answer(), "T-21c": t21c_wrong_value, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, TWO_CLAIMS.replace("\n2.", "\n2."))
    assert [c["status"] for c in report["claims"]] == ["wrong_value"], "judged against our page 2, not the other case"
    everything = " ".join(r.text + str(r.context) + r.system for r in model.requests)
    assert FOREIGN not in everything, "nothing from another case"
    t21b, t21c, t21d = (model.by_task(t)[0].text for t in ("T-21b", "T-21c", "T-21d"))
    assert "Advised" not in t21b and "fell short" not in t21b, "T-21b sees only their report, none of our data"
    page_two = t21c[t21c.index("[page 2]") :]
    assert [w for _, w in tokens(page_two)] == PAGE_TEXT[2].split(), "T-21c sees only the one page they cite"
    assert "Advised" not in t21c and "Plan review" not in t21c
    own = re.findall(r"^\[own ([0-9a-f-]{36})\]", t21d, re.M)
    assert sorted(own) == sorted(str(p) for p in served.paragraphs), "T-21d: our served paragraphs, by id, no more"
    assert "Haemoglobin" not in t21d and "Iron" not in t21d, "no records or facts reach T-21d"


# --- answer side: T-21b ---------------------------------------------------------------------------------


def hostile_claims() -> dict[str, Callable[[str], list[dict[str, Any]]]]:
    def claim(text: str, ids: list[str], value: str = "ferritin 19") -> dict[str, Any]:
        return {"text_verbatim": text, "word_ids": ids, "paragraph_ref": "2", "cited_page": "2", "cited_value": value}

    def not_in_report(t: str) -> list[dict[str, Any]]:
        return [claim("Page 2 records ferritin 4.", run_of(t, "ferritin 19")[1], "ferritin 4")]

    def ids_elsewhere(t: str) -> list[dict[str, Any]]:
        return [claim(run_of(t, "ferritin 19")[0], run_of(t, "Seen again")[1])]

    def ids_missing(t: str) -> list[dict[str, Any]]:
        return [claim(run_of(t, "ferritin 19")[0], ["p9w9001", "p9w9002"])]

    def canary_words(t: str) -> list[dict[str, Any]]:
        return [claim(CANARY, run_of(t, "ferritin 19")[1], CANARY)]

    def value_not_in_claim(t: str) -> list[dict[str, Any]]:
        text, ids = run_of(t, "Seen again")
        return [claim(text, ids, "ferritin 4")]

    def redirected_refs(t: str) -> list[dict[str, Any]]:
        ids = [f"w{9001 + i}" for i in range(9)]
        return [claim(INJECTIONS["change_source_references"], ids)]

    return {
        "not_in_report": not_in_report,
        "ids_elsewhere": ids_elsewhere,
        "ids_missing": ids_missing,
        "canary_words": canary_words,
        "value_not_in_claim": value_not_in_claim,
        "redirected_refs": redirected_refs,
    }


@pytest.mark.parametrize("name", list(hostile_claims()))
async def test_a_claim_not_in_their_report_or_with_ids_elsewhere_is_refused(
    name: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update(
        {
            "T-21b": t21b_answer(extra=hostile_claims()[name]),
            "T-21c": t21c_wrong_value,
            "T-21d": t21d_for(served),
        }
    )
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, TWO_CLAIMS)
    assert [c["text_verbatim"] for c in report["claims"]] == ["Page 2 records ferritin 19."], "only the real claim"
    assert report["claims_found"] == 1


async def test_a_legitimate_answer_finds_every_claim_in_their_report(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update(
        {
            "T-21b": t21b_answer(extra=lambda t: [_claim_for(t, "Seen again", "3", "Seen again in clinic", "2")]),
            "T-21c": t21c_wrong_value,
            "T-21d": t21d_for(served),
        }
    )
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, TWO_CLAIMS)
    assert report["claims_found"] == 2
    assert [(c["status"], c["checked_by"]) for c in report["claims"]] == [("wrong_value", "model"), ("matches", "code")]


def _claim_for(text: str, marker: str, page: str, value: str, ref: str) -> dict[str, Any]:
    said, ids = run_of(text, marker)
    return {"text_verbatim": said, "word_ids": ids, "paragraph_ref": ref, "cited_page": page, "cited_value": value}


async def test_the_page_a_claim_cites_is_read_from_their_words_not_from_the_model(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update(
        {
            "T-21b": t21b_answer("Ferritin 9", page="2", value="Ferritin 9 ug/L"),
            "T-21d": t21d_for(served),
        }
    )
    _, report = await checked(
        client_for,
        storage,
        admin,
        aconn,
        worker_db,
        served,
        "Report of the other expert\n1. Page 950 records Ferritin 9 ug/L.",
    )
    assert len(report["claims"]) == 1
    assert report["claims"][0]["status"] != "matches", "their words name page 950, which is not in the bundle"


async def test_the_paragraph_a_claim_is_in_is_read_from_the_layout_not_from_the_model(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update({"T-21b": t21b_answer(ref="99"), "T-21c": t21c_wrong_value, "T-21d": t21d_for(served)})
    _, report = await checked(
        client_for,
        storage,
        admin,
        aconn,
        worker_db,
        served,
        "Report of the other expert\n3. Page 2 records ferritin 19.",
    )
    assert report["claims"][0]["paragraph_ref"] in ("3", None)


HIDDEN = "Report of the other expert\n1. Page 2 records Ferritin 9 ug/L.\n2. Page 1 records that no bloods were taken."
SUPPRESSED: dict[str, tuple[Callable[[ModelRequest], dict[str, Any]], str]] = {
    "empty": (lambda r: {"claims": []}, TWO_CLAIMS),
    "unusable_output": (lambda r: {"nonsense": CANARY}, TWO_CLAIMS),
    "partial": (t21b_answer("Ferritin 9", value="Ferritin 9 ug/L"), HIDDEN),
}


@pytest.mark.parametrize(
    "how",
    [pytest.param(name) for name in SUPPRESSED],
)
async def test_a_report_full_of_claims_that_yields_few_or_none_is_flagged_for_the_expert(
    how: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    answer, text = SUPPRESSED[how]
    model.answers.update({"T-21b": answer, "T-21c": t21c_wrong_value, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, text)
    assert len(model.by_task("T-21b")) >= 1
    assert (report["claims_found"] or 0) < 2, "the answer really did leave claims out"
    assert report["checked_at"] is None or report["needs_attention_count"] > 0


# --- answer side: T-21c ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case",
    ["bad_status", "reason_over_80"],
)
async def test_an_answer_off_the_schema_leaves_the_claim_unchecked_and_never_a_verdict(
    case: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    bad: dict[str, Any] = {"status": "agrees_with_the_records", "evidence_word_ids": [], "reason": "ok"}
    if case == "reason_over_80":
        bad = {"status": "matches", "evidence_word_ids": [], "reason": "x" * 81}
    model.answers.update({"T-21b": t21b_answer(), "T-21c": lambda r: bad, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, ONE_CLAIM)
    assert len(model.by_task("T-21c")) == 2, "the kit asks once more, then gives up (InvalidOutput)"
    assert [(c["status"], c["checked_by"]) for c in report["claims"]] == [("unchecked", None)]
    assert report["matching_claims"] == []


async def test_a_claim_the_model_could_not_settle_is_flagged_for_the_expert(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update({"T-21b": t21b_answer(), "T-21c": lambda r: {"status": CANARY}, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, ONE_CLAIM)
    assert [c["status"] for c in report["claims"]] == ["unchecked"]
    assert report["checked_at"] is None or report["needs_attention_count"] > 0


async def test_code_settles_a_match_and_no_model_answer_can_overrule_it(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    steered = {"status": "contradicted", "evidence_word_ids": ["w1"], "reason": CANARY[:20]}
    model.answers.update(
        {
            "T-21b": t21b_answer("Ferritin 9", value="Ferritin 9 ug/L"),
            "T-21c": lambda r: steered,
            "T-21d": t21d_for(served),
        }
    )
    text = "Report of the other expert\n1. Page 2 records Ferritin 9 ug/L."
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, text)
    assert model.by_task("T-21c") == [], "a claim code can settle is never put to a model"
    assert [(c["status"], c["checked_by"]) for c in report["claims"]] == [("matches", "code")]
    assert report["claims"][0]["our_quote"] == "Ferritin 9 ug/L"


async def test_a_model_verdict_is_labelled_as_the_models_and_evidence_outside_the_page_is_dropped(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def outside(request: ModelRequest) -> dict[str, Any]:
        legit = ids_for(request.text, "Ferritin 9", after="[page")
        return {"status": "wrong_value", "evidence_word_ids": ["w9001", "p3w1", *legit], "reason": "checked"}

    model.answers.update({"T-21b": t21b_answer(), "T-21c": outside, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, ONE_CLAIM)
    claim = report["claims"][0]
    assert claim["checked_by"] == "model" and claim["status"] == "wrong_value"
    assert claim["our_quote"] == "Ferritin 9", "only words that are on the cited page are shown"
    assert claim["needs_attention"] is True and claim["discrepancy"] is False, (
        "nothing is a discrepancy until confirmed"
    )


async def test_a_model_match_with_no_evidence_from_the_page_is_not_stored_as_a_match(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update(
        {
            "T-21b": t21b_answer(),
            "T-21c": lambda r: {"status": "matches", "evidence_word_ids": [], "reason": "agrees"},
            "T-21d": t21d_for(served),
        }
    )
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, ONE_CLAIM)
    assert report["claims"][0]["status"] != "matches"


async def test_a_model_only_match_is_not_listed_as_agreeing_until_the_expert_confirms_it(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def steered(request: ModelRequest) -> dict[str, Any]:
        return {
            "status": "matches",
            "evidence_word_ids": ids_for(request.text, "Ferritin 9", after="[page"),
            "reason": "agrees",
        }

    model.answers.update({"T-21b": t21b_answer(), "T-21c": steered, "T-21d": t21d_for(served)})
    _, report = await checked(client_for, storage, admin, aconn, worker_db, served, ONE_CLAIM)
    claim = report["claims"][0]
    assert claim["checked_by"] == "model", "the verdict came from the model alone: 'ferritin 19' is not on the page"
    assert report["matching_claims"] == [] or claim["needs_attention"] is True


async def test_the_models_verdict_and_page_words_stay_hidden_until_the_viewer_has_read_the_page(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def stuffed(request: ModelRequest) -> dict[str, Any]:
        legit = ids_for(request.text, "Ferritin 9", after="[page")
        return {"status": "wrong_value", "evidence_word_ids": legit, "reason": "Ferritin 9 ug/L"}

    model.answers.update({"T-21b": t21b_answer(), "T-21c": stuffed, "T-21d": t21d_for(served)})
    document, queued = await register(client_for, storage, admin, aconn, served, ONE_CLAIM)
    await run_job(worker_db, served, queued, "verify_opposing")
    unread = (await read_report(client_for, served, document, served.assistant)).json()["claims"][0]
    assert unread["status"] == "read_first" and unread["reason"] is None and unread["our_quote"] is None
    assert unread["checked_by"] is None and unread["needs_attention"] is False
    seen = (await read_report(client_for, served, document)).json()["claims"][0]
    assert seen["our_quote"] == "Ferritin 9" and seen["reason"] == "Ferritin 9 ug/L", "positive control"


async def test_markup_in_their_words_or_the_models_label_is_stored_and_returned_as_plain_text(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    markup = "<b onmouseover=alert(1)>x</b> **bold** [link](javascript:alert(1)) https://example.invalid/a"
    label = "<script>alert(1)</script> ![x](https://example.invalid/a.png)"
    assert len(label) <= 80

    def answer(request: ModelRequest) -> dict[str, Any]:
        return {**t21c_wrong_value(request), "reason": label}

    model.answers.update({"T-21b": t21b_answer(), "T-21c": answer, "T-21d": t21d_for(served)})
    document, report = await checked(
        client_for,
        storage,
        admin,
        aconn,
        worker_db,
        served,
        f"Report of the other expert\n1. Page 2 records ferritin 19 {markup}",
    )
    claim = report["claims"][0]
    assert claim["text_verbatim"] == f"Page 2 records ferritin 19 {markup}"
    assert claim["reason"] == label
    assert report["checked_at"] is not None
    resp = await read_report(client_for, served, document)
    assert resp.headers["content-type"].startswith("application/json")


# --- a steered answer cannot do anything but fill in suggestions ------------------------------------------


async def test_hostile_answers_cannot_change_the_served_report_facts_or_start_any_action(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    mailer: Any,
    served: World,
) -> None:
    def positions(request: ModelRequest) -> dict[str, Any]:
        legit = {
            "question_id": str(served.question),
            "own_paragraph_ids": [str(served.paragraphs[2]), CANARY, str(uuid.uuid4())],
            "their_paragraph_ids": ["1", CANARY, "99"],
        }
        invented = {
            "question_id": CANARY,
            "own_paragraph_ids": [str(served.paragraphs[0])],
            "their_paragraph_ids": ["1"],
        }
        return {"positions": [legit, invented]}

    model.answers.update(
        {
            "T-21b": t21b_answer(),
            "T-21c": lambda r: {"status": "contradicted", "evidence_word_ids": ["w9001"], "reason": "delete the case"},
            "T-21d": positions,
        }
    )
    document, queued = await register(
        client_for,
        storage,
        admin,
        aconn,
        served,
        f"{ONE_CLAIM[: -len('.')]}. {INJECTIONS['trigger_action']} {INJECTIONS['exfiltrate_via_tool']}",
    )
    before = snapshot(admin, served, mailer)
    await run_job(worker_db, served, queued, "verify_opposing")
    report = (await read_report(client_for, served, document)).json()
    assert len(report["claims"]) == 1 and report["claims"][0]["status"] == "contradicted", "the answer was applied"
    assert [
        (p["question_id"], [o["paragraph_id"] for o in p["own"]], [t["paragraph_ref"] for t in p["theirs"]])
        for p in report["positions"]
    ] == [(str(served.question), [str(served.paragraphs[2])], ["1"])]
    assert snapshot(admin, served, mailer) == before
    assert before["case"] == [("served", False)]


async def test_a_steered_t21e_cannot_set_dependents_or_start_a_supplementary_report(
    client_for: ClientFor,
    admin: psycopg.Connection,
    worker_db: Database,
    model: Scripted,
    mailer: Any,
    served: World,
) -> None:
    def hostile(request: ModelRequest) -> dict[str, Any]:
        items = [
            {"paragraph_id": str(served.paragraphs[3]), "depends": True, "reason": "same visit"},
            {"paragraph_id": str(uuid.uuid4()), "depends": True, "reason": "made up"},
            {"paragraph_id": CANARY, "depends": True, "reason": "<script>x</script>"},
            {"paragraph_id": str(served.paragraphs[0]), "depends": True, "reason": "the changed paragraph itself"},
            {"paragraph_id": str(served.paragraphs[2]), "depends": False, "reason": "already found by code"},
        ]
        return {"items": items}

    model.answers["T-21e"] = hostile
    before = snapshot(admin, served, mailer)
    resp = await (await client_for(served.expert)).post(served.url("report-changes"), json=change_body(served))
    assert resp.status_code == 201, resp.text
    await run_job_id(worker_db, served, uuid.UUID(resp.json()["job_id"]), "find_dependents")
    change = (await (await client_for(served.expert)).get(served.url(f"report-changes/{resp.json()['id']}"))).json()
    assert change["suggested_dependents"] == [
        {"paragraph_id": str(served.paragraphs[3]), "depends": True, "reason": "same visit"}
    ], "only the one real, undecided paragraph is suggested"
    assert change["dependent_paragraph_ids"] == [str(served.paragraphs[2])], "decided by shared citations, in code"
    assert change["supplementary_version_id"] is None
    after = snapshot(admin, served, mailer)
    assert after["served"] == before["served"] and after["facts"] == before["facts"]
    assert after["versions"] == before["versions"], "no supplementary report was started"
    assert after["mail"] == 0 and after["case"] == before["case"]
    assert [j for j in after["jobs"] if j[0] != "find_dependents"] == [j for j in before["jobs"]]


# --- the other tasks: written questions, new records ------------------------------------------------------


@pytest.mark.parametrize("name", INJECTION_NAMES)
async def test_a_hostile_line_in_the_questions_document_reaches_only_the_text_of_t20a_and_t20b(
    name: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def with_hostile(request: ModelRequest) -> dict[str, Any]:
        out = t20a(request)
        text, ids = run_of(request.text, "ZX-CANARY")
        out["questions"].append({"text_verbatim": text, "word_ids": ids, "para_ref": None, "reason": "question"})
        return out

    model.answers["T-20a"] = with_hostile
    model.answers["T-20b"] = t20b_for(classifications(served.combo), served.paragraphs)
    client = await client_for(served.expert)
    made = await open_round(client, storage, admin, aconn, served, text=f"{DOCUMENT}\n7. {INJECTIONS[name]}")
    queued = await client.post(served.url("written-questions/import"), json={"round_id": made["id"]})
    await run_job(worker_db, served, queued, "extract_written_questions")
    only_in_text(model.by_task("T-20a"), "ZX-CANARY")
    only_in_text(model.by_task("T-20b"), "ZX-CANARY")
    listed = (await client.get(served.url("written-questions"), params={"round_id": made["id"]})).json()["questions"]
    assert [q["text_verbatim"] for q in listed] == [*QUESTIONS, INJECTIONS[name]], "their exact words, as plain text"
    assert all(q["status"] == "received" and q["classification"] is None for q in listed), "nothing is decided"


@pytest.mark.parametrize("how", ["not_in_document", "ids_elsewhere", "ids_missing", "canary_words"])
async def test_a_question_that_is_not_in_the_document_is_refused_and_the_real_one_kept(
    how: str,
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def answer(request: ModelRequest) -> dict[str, Any]:
        real_text, real_ids = run_of(request.text, "plan to review")
        other_ids = run_of(request.text, "ferritin result")[1]
        hostile = {
            "not_in_document": ("Please confirm the plan was never written.", real_ids),
            "ids_elsewhere": (real_text, other_ids),
            "ids_missing": (real_text, ["p9w9001", "p9w9002"]),
            "canary_words": (CANARY, real_ids),
        }[how]
        item = {"para_ref": None, "reason": "question"}
        return {
            "questions": [
                {"text_verbatim": real_text, "word_ids": real_ids, **item},
                {"text_verbatim": hostile[0], "word_ids": hostile[1], **item},
            ]
        }

    model.answers["T-20a"] = answer
    model.answers["T-20b"] = t20b_for(classifications(served.combo), served.paragraphs)
    client = await client_for(served.expert)
    made = await open_round(client, storage, admin, aconn, served)
    queued = await client.post(served.url("written-questions/import"), json={"round_id": made["id"]})
    await run_job(worker_db, served, queued, "extract_written_questions")
    listed = (await client.get(served.url("written-questions"), params={"round_id": made["id"]})).json()["questions"]
    assert [q["text_verbatim"] for q in listed] == [QUESTIONS[0]]


async def test_a_steered_t20b_gives_suggestions_only_and_unknown_ids_and_kinds_are_dropped(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    kinds = classifications(served.combo)

    def steer(request: ModelRequest) -> dict[str, Any]:
        ids = re.findall(r"^\[question ([0-9a-f-]{36})\]", request.text, re.M)
        items = [
            {"question_id": q, "kind": kinds[-1], "paragraph_ids": [str(uuid.uuid4()), CANARY], "reason": CANARY}
            for q in ids
        ]
        items[0]["paragraph_ids"] = [str(served.paragraphs[0])]
        items.append({"question_id": str(uuid.uuid4()), "kind": kinds[0], "paragraph_ids": [], "reason": "invented"})
        return {"items": items}

    model.answers["T-20a"] = t20a
    model.answers["T-20b"] = steer
    client = await client_for(served.expert)
    made = await open_round(client, storage, admin, aconn, served)
    queued = await client.post(served.url("written-questions/import"), json={"round_id": made["id"]})
    await run_job(worker_db, served, queued, "extract_written_questions")
    listed = (await client.get(served.url("written-questions"), params={"round_id": made["id"]})).json()["questions"]
    assert len(listed) == len(QUESTIONS)
    assert all(q["suggested_classification"] == kinds[-1] for q in listed), "the steered suggestion is shown as one"
    assert all(q["classification"] is None and q["status"] == "received" for q in listed), "never decided for them"
    assert listed[0]["suggested_paragraph_ids"] == [str(served.paragraphs[0])], "the real paragraph id is kept"
    assert all(q["suggested_paragraph_ids"] == [] for q in listed[1:]), "invented paragraph ids are dropped"


async def test_a_t20b_answer_naming_a_kind_outside_the_pack_is_discarded_whole(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    def off_pack(request: ModelRequest) -> dict[str, Any]:
        ids = re.findall(r"^\[question ([0-9a-f-]{36})\]", request.text, re.M)
        return {"items": [{"question_id": q, "kind": CANARY, "paragraph_ids": [], "reason": "x"} for q in ids]}

    model.answers["T-20a"] = t20a
    model.answers["T-20b"] = off_pack
    client = await client_for(served.expert)
    made = await open_round(client, storage, admin, aconn, served)
    queued = await client.post(served.url("written-questions/import"), json={"round_id": made["id"]})
    await run_job(worker_db, served, queued, "extract_written_questions")
    listed = (await client.get(served.url("written-questions"), params={"round_id": made["id"]})).json()["questions"]
    assert len(listed) == len(QUESTIONS) and all(q["suggested_classification"] is None for q in listed)


async def test_a_questions_document_that_yields_no_questions_is_flagged_not_finished(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers["T-20a"] = lambda r: {"questions": []}
    client = await client_for(served.expert)
    made = await open_round(client, storage, admin, aconn, served)
    queued = await client.post(served.url("written-questions/import"), json={"round_id": made["id"]})
    await run_job(worker_db, served, queued, "extract_written_questions")
    listed = (await client.get(served.url("written-questions"), params={"round_id": made["id"]})).json()["questions"]
    rounds = (await client.get(served.url("written-question-rounds"))).json()["rounds"]
    assert listed == [] and len(model.by_task("T-20a")) == 1
    assert rounds[0]["extracted_at"] is None, "a document of six questions is not a finished extraction of none"


async def test_t21a_with_a_hostile_fact_sends_it_only_as_text_and_a_steered_answer_reveals_nothing_early(
    client_for: ClientFor,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    reads: Any,
    served: World,
) -> None:
    pages = await add_new_pages(seed, admin, aconn, served)
    hostile = " ".join(INJECTIONS.values())
    planted = add_fact(admin, served.case, pages[9], value=f"Ferritin 6 ug/L {hostile}", word_ids=["w1", "w2", "w3"])

    def steered(request: ModelRequest) -> dict[str, Any]:
        return {
            "items": [
                {
                    "fact_id": str(planted),
                    "question_id": str(served.question),
                    "why": "<b>low</b> [x](https://e.invalid)",
                },
                {"fact_id": str(uuid.uuid4()), "question_id": str(served.question), "why": "invented fact"},
                {"fact_id": str(planted), "question_id": CANARY, "why": "invented question"},
            ]
        }

    model.answers["T-21a"] = steered
    expert = await client_for(served.expert)
    await run_job(worker_db, served, await expert.post(served.url("new-since-served/check")), "find_may_matter")
    only_in_text(model.by_task("T-21a"), "ZX-CANARY")
    before = {p["page_no"]: p for p in (await expert.get(served.url("new-since-served"))).json()["pages"]}
    assert before[9]["may_matter"] == [], "a steered answer reveals nothing before the page is read (rule 3)"
    reads.rows.add((served.case, pages[9], 9, served.expert))
    after = {p["page_no"]: p for p in (await expert.get(served.url("new-since-served"))).json()["pages"]}
    assert after[9]["may_matter"] == [
        {"fact_id": str(planted), "question_id": str(served.question), "why": "<b>low</b> [x](https://e.invalid)"}
    ], "only the real pair is kept, and its label is plain text"


# --- court orders -----------------------------------------------------------------------------------------


async def test_court_order_text_is_never_read_by_a_model_and_dates_in_it_set_nothing(
    client_for: ClientFor, admin: psycopg.Connection, model: Scripted, served: World
) -> None:
    client = await client_for(served.expert)
    deadlines = (await client.get(served.url("deadlines"))).json()["deadlines"]
    jobs = admin.execute("select count(*) from app.jobs where case_id = %s", (served.case,)).fetchone()
    order = (
        f"Questions due 03/04/2026 or perhaps 4 March. {INJECTIONS['change_output']} <b>x</b> [y](https://e.invalid)"
    )
    resp = await client.post(served.url("court-directions"), json={"made_on": "2026-03-10", "text": order})
    assert resp.status_code == 201, resp.text
    assert resp.json()["text"] == order and resp.json()["created_deadline_ids"] == []
    assert (await client.get(served.url("court-directions"))).json()["directions"][0]["text"] == order
    assert (await client.get(served.url("deadlines"))).json()["deadlines"] == deadlines, "dates in the text set nothing"
    assert (
        model.requests == []
        and admin.execute("select count(*) from app.jobs where case_id = %s", (served.case,)).fetchone() == jobs
    )


async def test_a_court_order_deadline_is_a_typed_iso_date_and_an_ambiguous_one_is_refused(
    client_for: ClientFor, served: World
) -> None:
    client = await client_for(served.expert)
    for ambiguous in ("03/04/2026", "4 March", "2026-13-01", ""):
        bad = {"made_on": "2026-03-10", "deadlines": [{"kind": "written_questions", "due_on": ambiguous}]}
        assert (await client.post(served.url("court-directions"), json=bad)).status_code == 422, ambiguous
    good = {"made_on": "2026-03-10", "deadlines": [{"kind": "written_questions", "due_on": "2026-04-20"}]}
    assert (await client.post(served.url("court-directions"), json=good)).status_code == 201
    after = {d["kind"]: d for d in (await client.get(served.url("deadlines"))).json()["deadlines"]}
    assert after["written_questions"]["due_on"] == "2026-04-20" and after["written_questions"]["source"] == "direction"


# --- inventory and static checks --------------------------------------------------------------------------

TASKS = {
    "T-20a": ("t20a", "sonnet"),
    "T-20b": ("t20b", "sonnet"),
    "T-21a": ("t21a", "sonnet"),
    "T-21b": ("t21b", "sonnet"),
    "T-21c": ("t21c", "opus"),
    "T-21d": ("t21d", "sonnet"),
    "T-21e": ("t21e", "sonnet"),
}


def string_fields(schema: Any, name: str = "") -> list[tuple[str, dict[str, Any]]]:
    found: list[tuple[str, dict[str, Any]]] = []
    if isinstance(schema, dict):
        kind = schema.get("type")
        if (kind == "string" or (isinstance(kind, list) and "string" in kind)) and "enum" not in schema:
            found.append((name, schema))
        for key, value in schema.items():
            found += string_fields(value, key if key not in ("properties", "items") else name)
        for key, value in schema.get("properties", {}).items():
            found += string_fields(value, key)
    elif isinstance(schema, list):
        for value in schema:
            found += string_fields(value, name)
    return found


async def test_every_slice_task_is_listed_versioned_on_the_expected_model_and_has_no_free_prose_field() -> None:
    pack = pack_of(LAUNCH)
    built = {
        "T-20a": ai_tasks.t20a,
        "T-20b": ai_tasks.t20b,
        "T-21a": ai_tasks.t21a,
        "T-21b": ai_tasks.t21b,
        "T-21c": ai_tasks.t21c,
        "T-21d": ai_tasks.t21d,
        "T-21e": ai_tasks.t21e,
    }
    assert set(built) == set(TASKS), "a new task needs its injection tests"
    code_checked = {"text_verbatim", "cited_value"}
    for task_id, make in built.items():
        task = make(pack)
        name, role = TASKS[task_id]
        assert task.task_id == task_id and task.model == role
        assert task.prompt_version == ai_tasks.prompt(name)[0] and task.system == ai_tasks.prompt(name)[1]
        for field, spec in string_fields(task.schema):
            assert "maxLength" in spec, (task_id, field)
            if spec["maxLength"] > 80:
                assert field in code_checked, f"{task_id}.{field} is free text above the 80-character label size"


async def test_only_the_worker_jobs_call_a_model_and_no_route_reads_a_document_with_one() -> None:
    callers = {p.name for p in APP.glob("*.py") if ".model()" in p.read_text()}
    assert callers == {"jobs.py"}
    for name in ("routes_deadlines.py", "routes_questions.py", "routes_new_material.py", "routes_changes.py"):
        text = (APP / name).read_text()
        assert "ai_tasks" not in text and ".run(" not in text, name


async def test_the_screens_render_text_as_text_with_no_html_or_markdown_rendering() -> None:
    sources = [p for p in WEB.rglob("*") if p.suffix in (".ts", ".tsx") and ".test." not in p.name]
    assert len(sources) >= 8, "the folder was found and has the screens"
    banned = (
        "dangerouslySetInnerHTML",
        "innerHTML",
        "insertAdjacentHTML",
        "document.write",
        "react-markdown",
        "markdown-it",
        "from 'marked'",
        'from "marked"',
        "eval(",
    )
    hits = [(p.name, b) for p in sources for b in banned if b in p.read_text()]
    assert hits == []
