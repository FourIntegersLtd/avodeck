# ruff: noqa: F811  (pytest fixtures imported from the world module)
"""Screen 21: new records after serving and the other side's expert report (slice G brief).

- Pages added after the report was served are listed as new since served, numbered after the last page.
  Their facts stay behind the blind read (slice B's 423), and what T-21a says may matter about them is shown
  only once the page has been read (rule 3).
- The other expert's report: T-21b finds their claims as exact words of their report. Code checks each
  claim against the cited page first (rule 2): a page that does not exist, or a cited value that is in the
  page's words, is settled without a model. Only the rest go to T-21c on the larger model. T-21d links their
  paragraphs to the expert's own by id only; the screen shows both texts verbatim, with no summary.
- Every result shows both quotes with page numbers, and nothing is a discrepancy until the expert confirms.

Words, values and claims are short made-up words; nothing here is a real record.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import psycopg
import pytest
from psycopg import AsyncConnection

from app.core.ai.bedrock import ModelRequest
from app.core.db import Database
from app.core.http import etag
from app.core.storage import CaseFileStorage
from tests.fixtures.test_after_serving_world import (  # noqa: F401
    PARAGRAPHS,
    Reads,
    Scripted,
    World,
    aconn,
    add_words,
    app,
    combo,
    ids_for,
    mailer,
    model,
    ocr,
    reads,
    run_job,
    served,
    services,
    storage,
    store_document,
    worker_db,
    world,
)
from tests.support.contracts import add_fact
from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

PLANTED = "Ferritin 6 ug/L"
NEW_PAGES = range(4, 18)  # fourteen pages after the three already numbered

# (their words, cited page, cited value, expected status, settled by)
CLAIMS = (
    ("Page 1 records Plan review 3/52.", "1", "Plan review 3/52", "matches", "code"),
    ("Page 1 records Bloods arranged today.", "1", "Bloods arranged today", "matches", "code"),
    ("Page 2 records Ferritin 9 ug/L.", "2", "Ferritin 9 ug/L", "matches", "code"),
    ("Page 2 records Haemoglobin 118 g/L.", "2", "Haemoglobin 118 g/L", "matches", "code"),
    ("Page 2 records Iron started.", "2", "Iron started", "matches", "code"),
    ("Page 3 records Seen again in clinic.", "3", "Seen again in clinic", "matches", "code"),
    ("Page 3 records call back if worse.", "3", "call back if worse", "matches", "code"),
    ("Page 950 records a normal blood count.", "950", "normal blood count", "not_on_page", "code"),
    ("Page 951 records a home visit.", "951", "home visit", "not_on_page", "code"),
    ("Page 2 records ferritin 19.", "2", "ferritin 19", "wrong_value", "model"),
    ("Page 1 records that no bloods were taken.", "1", "no bloods were taken", "contradicted", "model"),
    ("Page 2 records that iron was started.", "2", "iron was started", "matches", "model"),
)
EVIDENCE = {
    "ferritin 19": "Ferritin 9",
    "no bloods were taken": "Bloods arranged today",
    "iron was started": "Iron started",
}
THEIR_REPORT = "Report of the other expert\n" + "\n".join(f"{n}. {c[0]}" for n, c in enumerate(CLAIMS, start=1))
_CLAIM = re.compile(r"^\[claim\] (.*)$", re.M)
_VALUE = re.compile(r"^\[cited value\] (.*)$", re.M)


async def add_new_pages(
    seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, w: World
) -> dict[int, uuid.UUID]:
    """Fourteen pages appended after serving (as slice A numbers them), one carrying the planted result."""
    document = seed.document(w.case)
    pages: dict[int, uuid.UUID] = {}
    for n in NEW_PAGES:
        page = seed.page(w.case, document, n)
        text = f"Ferritin 6 ug/L repeat {n}" if n == 9 else f"Letter filed {n}"
        await add_words(admin, aconn, w.case, page, text)
        pages[n] = page
    return pages


async def test_new_pages_are_listed_after_the_last_page_and_stay_behind_the_blind_read(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, served: World
) -> None:
    pages = await add_new_pages(seed, admin, aconn, served)
    add_fact(admin, served.case, pages[9], value=PLANTED, word_ids=["w1", "w2", "w3"])
    expert = await client_for(served.expert)
    resp = await expert.get(served.url("new-since-served"))
    assert resp.status_code == 200, resp.text
    listed = resp.json()
    assert listed["served_at"] is not None
    assert [p["page_no"] for p in listed["pages"]] == list(NEW_PAGES), "numbered after the last page, in order"
    assert all(p["read"] is False and p["may_matter"] == [] for p in listed["pages"])
    assert (await expert.get(served.url("pages/9/facts"))).status_code == 423, "the blind read still applies"
    assert (await (await client_for(served.viewer)).get(served.url("new-since-served"))).status_code == 200
    assert (await (await client_for(served.outsider)).get(served.url("new-since-served"))).status_code == 404


async def test_a_case_not_yet_served_has_nothing_new_since_served(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world.expert)).get(world.url("new-since-served"))
    assert resp.status_code == 200 and resp.json() == {"served_at": None, "pages": []}


async def test_t21a_flags_the_planted_result_but_shows_it_only_once_the_page_is_read(
    client_for: ClientFor,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    reads: Reads,
    served: World,
) -> None:
    pages = await add_new_pages(seed, admin, aconn, served)
    planted = add_fact(admin, served.case, pages[9], value=PLANTED, word_ids=["w1", "w2", "w3"])
    elsewhere = add_fact(admin, served.case, served.pages[0], status="verified", value="Bloods arranged today")
    invented = uuid.uuid4()

    def t21a(request: ModelRequest) -> dict[str, Any]:
        assert str(planted) in request.text and str(served.question) in request.text
        assert str(elsewhere) not in request.text, "only facts on the new pages are offered"
        return {
            "items": [
                {"fact_id": str(planted), "question_id": str(served.question), "why": "low iron result"},
                {"fact_id": str(invented), "question_id": str(served.question), "why": "made up"},
                {"fact_id": str(elsewhere), "question_id": str(served.question), "why": "old page"},
            ]
        }

    model.answers["T-21a"] = t21a
    expert = await client_for(served.expert)
    assert (await (await client_for(served.viewer)).post(served.url("new-since-served/check"))).status_code == 403
    await run_job(worker_db, served, await expert.post(served.url("new-since-served/check")), "find_may_matter")
    assert len(model.by_task("T-21a")) == 1
    before = {p["page_no"]: p for p in (await expert.get(served.url("new-since-served"))).json()["pages"]}
    assert before[9]["may_matter"] == [], "nothing is revealed before the page is read"
    reads.rows.add((served.case, pages[9], 9, served.expert))
    after = {p["page_no"]: p for p in (await expert.get(served.url("new-since-served"))).json()["pages"]}
    assert after[9]["read"] is True
    assert after[9]["may_matter"] == [
        {"fact_id": str(planted), "question_id": str(served.question), "why": "low iron result"}
    ]
    assert all(after[n]["may_matter"] == [] for n in NEW_PAGES if n != 9), "invented or old facts are dropped"


def t21b(request: ModelRequest) -> dict[str, Any]:
    claims = []
    for number, (words, page, value, _, _) in enumerate(CLAIMS, start=1):
        claims.append(
            {
                "text_verbatim": words,
                "word_ids": ids_for(request.text, words),
                "paragraph_ref": str(number),
                "cited_page": page,
                "cited_value": value,
            }
        )
    claims.append(
        {
            "text_verbatim": "Words they never wrote.",
            "word_ids": ["p1w1"],
            "paragraph_ref": "13",
            "cited_page": "1",
            "cited_value": "never",
        }
    )
    return {"claims": claims}


def t21c(request: ModelRequest) -> dict[str, Any]:
    value = _VALUE.findall(request.text)[0]
    status = next(c[3] for c in CLAIMS if c[2] == value)
    return {
        "status": status,
        "evidence_word_ids": ids_for(request.text, EVIDENCE[value], after="[page"),
        "reason": "checked",
    }


def t21d_for(w: World) -> Any:
    def answer(request: ModelRequest) -> dict[str, Any]:
        return {
            "positions": [
                {
                    "question_id": str(w.question),
                    "own_paragraph_ids": [str(w.paragraphs[2]), str(uuid.uuid4())],
                    "their_paragraph_ids": ["10", "99"],
                },
                {
                    "question_id": str(uuid.uuid4()),
                    "own_paragraph_ids": [str(w.paragraphs[0])],
                    "their_paragraph_ids": ["1"],
                },
            ]
        }

    return answer


async def checked_report(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    w: World,
) -> tuple[uuid.UUID, dict[str, Any]]:
    model.answers.update({"T-21b": t21b, "T-21c": t21c, "T-21d": t21d_for(w)})
    document = await store_document(storage, admin, aconn, w.case, THEIR_REPORT, "opposing_report")
    client = await client_for(w.assistant)
    queued = await client.post(
        w.url("opposing-reports"), json={"document_id": str(document), "received_on": "2026-04-02"}
    )
    assert queued.status_code == 202, queued.text
    assert queued.json()["id"]
    await run_job(worker_db, w, queued, "verify_opposing")
    resp = await (await client_for(w.expert)).get(w.url(f"opposing-reports/{document}"))
    assert resp.status_code == 200, resp.text
    return document, resp.json()


async def test_t21a_is_never_offered_a_fact_the_expert_rejected(
    client_for: ClientFor,
    seed: Seed,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    pages = await add_new_pages(seed, admin, aconn, served)
    kept = add_fact(admin, served.case, pages[9], value=PLANTED, word_ids=["w1", "w2", "w3"])
    rejected = add_fact(admin, served.case, pages[9], status="rejected", value="repeat 9", word_ids=["w4", "w5"])

    def t21a(request: ModelRequest) -> dict[str, Any]:
        assert str(kept) in request.text
        assert str(rejected) not in request.text, "a rejected fact is not offered"
        return {"items": []}

    model.answers["T-21a"] = t21a
    expert = await client_for(served.expert)
    await run_job(worker_db, served, await expert.post(served.url("new-since-served/check")), "find_may_matter")
    assert len(model.by_task("T-21a")) == 1


async def test_code_settles_nine_claims_and_only_three_go_to_the_larger_model(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    _, report = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    claims = report["claims"]
    assert [c["text_verbatim"] for c in claims] == [c[0] for c in CLAIMS], "only exact words of their report"
    assert [(c["status"], c["checked_by"]) for c in claims] == [(c[3], c[4]) for c in CLAIMS]
    asked = model.by_task("T-21c")
    assert len(asked) == 3
    assert {r.model_id for r in asked} == {"anthropic.claude-opus-5-5-test-v1:0"}
    assert sorted(_VALUE.findall(r.text)[0] for r in asked) == sorted(c[2] for c in CLAIMS if c[4] == "model")
    assert report["checked_at"] is not None and report["claims_found"] == 12


async def test_every_result_shows_both_quotes_and_nothing_is_a_discrepancy_until_confirmed(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    document, report = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    by_no = {c["claim_no"]: c for c in report["claims"]}
    assert by_no[3]["their_quote"] == "Ferritin 9 ug/L" and by_no[3]["their_page_ref"] == "2"
    assert by_no[3]["our_quote"] == "Ferritin 9 ug/L" and by_no[3]["our_page_no"] == 2
    assert by_no[10]["their_quote"] == "ferritin 19" and by_no[10]["our_quote"] == "Ferritin 9"
    assert by_no[10]["our_page_no"] == 2
    assert by_no[8]["our_quote"] is None and by_no[8]["our_page_no"] is None
    assert all(c["discrepancy"] is False and c["expert_confirmed"] is False for c in report["claims"])

    url = served.url(f"opposing-claims/{by_no[10]['id']}")
    body = {"confirm": True}
    assert (
        await (await client_for(served.co_expert)).patch(
            url, json=body, headers={"If-Match": etag(by_no[10]["version"])}
        )
    ).status_code == 403
    expert = await client_for(served.expert)
    assert (await expert.patch(url, json=body)).status_code == 428
    back = await expert.patch(url, json={"status": "unchecked"}, headers={"If-Match": etag(by_no[10]["version"])})
    assert back.status_code == 422
    confirmed = await expert.patch(url, json=body, headers={"If-Match": etag(by_no[10]["version"])})
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["discrepancy"] is True and confirmed.json()["expert_confirmed"] is True
    changed = await expert.patch(
        served.url(f"opposing-claims/{by_no[11]['id']}"),
        json={"status": "wrong_value", "confirm": True},
        headers={"If-Match": etag(by_no[11]["version"])},
    )
    assert changed.status_code == 200 and changed.json()["status"] == "wrong_value"
    ok = await expert.patch(
        served.url(f"opposing-claims/{by_no[1]['id']}"), json=body, headers={"If-Match": etag(by_no[1]["version"])}
    )
    assert ok.json()["discrepancy"] is False, "a confirmed match is not a discrepancy"
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'opposing_claim.confirmed'", (served.case,)
    ).fetchone()
    assert audit is not None and audit[0] == 3
    again = await expert.get(served.url(f"opposing-reports/{document}"))
    assert {c["claim_no"] for c in again.json()["claims"] if c["discrepancy"]} == {10, 11}


async def test_positions_link_paragraphs_by_id_and_show_both_texts_verbatim(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    _, report = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    fields = [{"question_id", "question_no", "own", "theirs"}]
    assert [set(p) for p in report["positions"]] == fields, "no summary field"
    [position] = report["positions"]
    assert position["question_id"] == str(served.question)
    assert position["own"] == [{"paragraph_id": str(served.paragraphs[2]), "para_no": 3, "text": PARAGRAPHS[2][1]}]
    assert position["theirs"] == [{"paragraph_ref": "10", "text_verbatim": CLAIMS[9][0]}]
    assert "summary" not in str(model.by_task("T-21d")[0].schema)


async def test_registering_their_report_needs_upload_rights_and_happens_once(
    client_for: ClientFor, storage: CaseFileStorage, admin: psycopg.Connection, aconn: AsyncConnection, served: World
) -> None:
    document = await store_document(storage, admin, aconn, served.case, THEIR_REPORT, "opposing_report")
    body = {"document_id": str(document), "received_on": "2026-04-02"}
    assert (await (await client_for(served.viewer)).post(served.url("opposing-reports"), json=body)).status_code == 403
    assert (
        await (await client_for(served.outsider)).post(served.url("opposing-reports"), json=body)
    ).status_code == 404
    other = uuid.uuid4()
    missing = await (await client_for(served.expert)).post(
        served.url("opposing-reports"), json={**body, "document_id": str(other)}
    )
    assert missing.status_code == 422
    assert (await (await client_for(served.expert)).post(served.url("opposing-reports"), json=body)).status_code == 202
    assert (await (await client_for(served.expert)).post(served.url("opposing-reports"), json=body)).status_code == 409
    listed = await (await client_for(served.viewer)).get(served.url(f"opposing-reports/{document}"))
    assert listed.status_code == 200 and listed.json()["claims"] == [] and listed.json()["checked_at"] is None


async def test_a_claims_check_stays_hidden_until_the_viewer_has_read_its_page(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    reads: Reads,
    served: World,
) -> None:
    """Blind read (rule 3): what our page says, and the check's result, wait until this viewer has read it."""
    page = admin.execute(
        "select id from app.pages where bundle_id = (select bundle_id from app.cases where id = %s) and bundle_page_no "
        "= 2",
        (served.case,),
    ).fetchone()
    assert page is not None
    reads.rows.discard((served.case, page[0], 2, served.expert))
    document, _ = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    expert = await client_for(served.expert)
    report = (await expert.get(served.url(f"opposing-reports/{document}"))).json()
    by_no = {c["claim_no"]: c for c in report["claims"]}
    hidden = by_no[3]
    assert hidden["status"] == "read_first" and hidden["our_page_no"] == 2
    assert hidden["our_quote"] is None and hidden["checked_by"] is None and hidden["reason"] is None
    assert hidden["discrepancy"] is False
    assert by_no[8]["status"] == "not_on_page", "no page of ours was found: nothing to read first"

    url = served.url(f"opposing-claims/{hidden['id']}")
    blocked = await expert.patch(url, json={"confirm": True}, headers={"If-Match": etag(hidden["version"])})
    assert blocked.status_code == 409 and blocked.json()["error"]["code"] == "read_first", blocked.text

    reads.rows.add((served.case, page[0], 2, served.expert))
    shown = {c["claim_no"]: c for c in (await expert.get(served.url(f"opposing-reports/{document}"))).json()["claims"]}
    assert shown[3]["status"] == "matches" and shown[3]["our_quote"] == "Ferritin 9 ug/L"
    other = await (await client_for(served.assistant)).get(served.url(f"opposing-reports/{document}"))
    assert {c["claim_no"]: c for c in other.json()["claims"]}[3]["status"] == "read_first", "each viewer reads"


async def test_the_other_experts_name_role_and_page_count_come_with_their_report(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    model.answers.update({"T-21b": t21b, "T-21c": t21c, "T-21d": t21d_for(served)})
    document = await store_document(storage, admin, aconn, served.case, THEIR_REPORT, "opposing_report")
    admin.execute("update app.documents set pages = 34 where id = %s", (document,))
    client = await client_for(served.expert)
    body = {"document_id": str(document), "received_on": "2026-04-02", "expert_name": "Dr R", "expert_role": "Expert"}
    too_long = await client.post(served.url("opposing-reports"), json={**body, "expert_name": "x" * 201})
    assert too_long.status_code == 422
    queued = await client.post(served.url("opposing-reports"), json=body)
    assert queued.status_code == 202, queued.text
    await run_job(worker_db, served, queued, "verify_opposing")
    report = (await client.get(served.url(f"opposing-reports/{document}"))).json()
    assert (report["expert_name"], report["expert_role"], report["pages"]) == ("Dr R", "Expert", 34)
    assert [p["question_no"] for p in report["positions"]] == [1], "the differ row names its question's number"


async def test_a_report_registered_without_a_name_has_none(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    _, report = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    assert (report["expert_name"], report["expert_role"], report["pages"]) == (None, None, None)


async def test_new_since_served_gives_the_page_range_and_the_number_read(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, aconn: AsyncConnection, served: World
) -> None:
    expert = await client_for(served.expert)
    none = (await expert.get(served.url("new-since-served"))).json()
    assert (none["first_page"], none["last_page"], none["read_count"]) == (None, None, 0)
    await add_new_pages(seed, admin, aconn, served)
    listed = (await expert.get(served.url("new-since-served"))).json()
    assert (listed["first_page"], listed["last_page"], listed["read_count"]) == (4, 17, 0)


async def test_claims_come_grouped_with_the_server_counting_those_needing_attention(
    client_for: ClientFor,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
    aconn: AsyncConnection,
    worker_db: Database,
    model: Scripted,
    served: World,
) -> None:
    document, report = await checked_report(client_for, storage, admin, aconn, worker_db, model, served)
    claims = report["claims"]
    assert [c["id"] for c in report["matching_claims"]] == [c["id"] for c in claims if c["status"] == "matches"]
    assert [c["id"] for c in report["other_claims"]] == [c["id"] for c in claims if c["status"] != "matches"]
    flagged = [c for c in claims if c["needs_attention"]]
    assert flagged and report["needs_attention_count"] == len(flagged)
    assert all(c["status"] not in ("matches", "read_first") and not c["expert_confirmed"] for c in flagged)
    target = flagged[0]
    expert = await client_for(served.expert)
    done = await expert.patch(
        served.url(f"opposing-claims/{target['id']}"),
        json={"confirm": True},
        headers={"If-Match": etag(target["version"])},
    )
    assert done.status_code == 200 and done.json()["needs_attention"] is False
    again = (await expert.get(served.url(f"opposing-reports/{document}"))).json()
    assert again["needs_attention_count"] == len(flagged) - 1
