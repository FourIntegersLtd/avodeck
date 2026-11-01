"""Slice A increment 11b: pages that are not this person's record (S5, F25, architecture 10.3, screen 30).

- After reading, the pipeline suggests pages whose identifiers (the pack's recognisers) are not the
  case subject's. It never hides a page itself: a suggestion changes nothing until confirmed.
- `POST /v1/cases/{id}/pages/{page_id}/quarantine` `{reason}` (page.quarantine): an assistant only
  suggests; the named expert confirms. A confirmed page keeps its number, is quarantined (reason, by,
  at), its facts are rejected, and it leaves reading, extraction, copies, missing documents and the
  summary. Audited.
- `DELETE …/quarantine` (named expert): dismisses a suggestion (never suggested again) or undoes a
  quarantine: the page comes back, its verified facts are verified again and it is read again.
- No identifier is stored: the suggestion records only a short reason code.
Viewers get 403 and people outside the case 404. Runs under the launch and the fixture packs.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from typing import Any

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.pdf import embedded_words, split_pages
from app.core.storage import CaseFileStorage
from tests.repository.test_pipeline_support import Harness, facts_of, harness, t07b_requests, text_pdf
from tests.support.ai import ScriptedBedrock, response
from tests.support.db import ClientFor, scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

# Made-up identifiers the pack's recognisers find: the case subject's, and another person's.
IDENTIFIERS = {
    "gb-eaw": ("Number 999 447 1015 DOB: 14/03/1969", "Number 999 447 1023 DOB: 02/11/1958"),
    "zz-test": ("ZZ-ID-123456 Birthdate: 3/14/1969", "ZZ-ID-654321 Birthdate: 11/2/1958"),
}
OTHER_PAGE = 4


def bundle(code: str) -> list[list[str]]:
    subject, other = IDENTIFIERS[code]
    return [
        ["Synthetic alpha entry", subject],
        ["Synthetic bravo entry", subject],
        ["Synthetic charlie entry", subject],
        ["Synthetic delta entry", other],
        ["Synthetic echo entry"],
    ]


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[CaseFileStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


def answer(pack: Any) -> Any:
    first = embedded_words(split_pages(text_pdf([["Synthetic alpha entry"]]))[0]).words[0]
    return response(
        {
            "source_type": pack.source_types()[0]["key"],
            "printed_dates": [],
            "facts": [
                {
                    "kind": pack.fact_kinds()[0]["key"],
                    "clinician_role": None,
                    "fields": {},
                    "value": "Synthetic",
                    "word_ids": [first.id],
                }
            ],
            "references_out": [],
            "availability_evidence": [],
            "actions_requested_of_clinician": [],
            "entries": [],
        }
    )


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def h(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: CaseFileStorage,
) -> AsyncIterator[Harness]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row())
    pack = default_library().combine(*combo.codes)
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        bedrock=ScriptedBedrock([answer(pack)]),
    ) as running:
        await running.upload(text_pdf(bundle(combo.codes[0])))
        await running.drain()
        yield running


def page_id(h: Harness, n: int) -> uuid.UUID:
    return next(p["id"] for p in h.pages() if p["bundle_page_no"] == n)  # type: ignore[no-any-return]


def quarantine_path(h: Harness, n: int) -> str:
    return f"/v1/cases/{h.case_id}/pages/{page_id(h, n)}/quarantine"


async def summary(h: Harness) -> dict[str, Any]:
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert got.status_code == 200, got.status_code
    return got.json()  # type: ignore[no-any-return]


def facts_on(h: Harness, n: int) -> list[dict[str, Any]]:
    return [f for f in facts_of(h) if f["bundle_page_no"] == n]


def audited(h: Harness, action: str) -> int:
    row = h.admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = %s", (h.case_id, action)
    ).fetchone()
    return row[0] if row else 0


async def helper(h: Harness, seed: Seed, client_for: ClientFor, role: str) -> Any:
    person = seed.person()
    seed.case_member(h.case_id, person, role, added_by=h.expert)
    return await client_for(person)


async def test_pages_with_another_persons_identifiers_are_suggested_and_never_hidden(h: Harness) -> None:
    got = await summary(h)
    assert [(s["page_no"], s["reason"]) for s in got["quarantine_suggestions"]] == [(OTHER_PAGE, "identifiers_differ")]
    assert got["quarantined"] == []
    assert got["pages"] == 5
    assert not any(p["quarantined"] for p in h.pages()), "a page was hidden without the expert"
    assert [f["status"] for f in facts_on(h, OTHER_PAGE)] == ["suggested"]
    stored = h.admin.execute(
        "select status, reason, prior_facts::text, suggested_by from app.page_quarantines where case_id = %s",
        (h.case_id,),
    ).fetchall()
    assert stored == [("suggested", "identifiers_differ", "[]", None)], "more than a reason code was stored"


async def test_the_named_expert_confirms_and_the_page_leaves_everything(
    h: Harness, seed: Seed, client_for: ClientFor
) -> None:
    (fact,) = facts_on(h, OTHER_PAGE)
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (fact["id"],))
    assistant = await helper(h, seed, client_for, "assistant")

    suggested = await assistant.post(quarantine_path(h, OTHER_PAGE), json={"reason": "not_this_person"})
    assert suggested.status_code == 200, suggested.status_code
    assert suggested.json()["status"] == "suggested"
    other = await assistant.post(quarantine_path(h, 5), json={"reason": "not_this_person"})
    assert other.json()["status"] == "suggested"
    assert not any(p["quarantined"] for p in h.pages()), "an assistant hid a page"

    confirmed = await h.client.post(quarantine_path(h, OTHER_PAGE), json={"reason": "not_this_person"})
    assert confirmed.status_code == 200, confirmed.status_code
    assert confirmed.json()["status"] == "confirmed" and confirmed.json()["page_no"] == OTHER_PAGE
    row = next(p for p in h.pages() if p["bundle_page_no"] == OTHER_PAGE)
    assert row["quarantined"] and row["quarantine_reason"] == "not_this_person"
    assert row["quarantined_by"] == h.expert and row["quarantined_at"] is not None
    assert {f["status"] for f in facts_on(h, OTHER_PAGE)} == {"rejected"}
    assert audited(h, "page.quarantined") == 1

    got = await summary(h)
    assert got["pages"] == 4
    assert [q["page_no"] for q in got["quarantined"]] == [OTHER_PAGE]
    assert [s["page_no"] for s in got["quarantine_suggestions"]] == [5]
    thumb = await h.client.get(f"/v1/cases/{h.case_id}/pages/{OTHER_PAGE}/thumbnail")
    assert thumb.status_code == 404

    asked = len(t07b_requests(h))
    assert (await h.client.post(f"/v1/cases/{h.case_id}/reprocess")).status_code == 202
    await h.drain()
    assert len(t07b_requests(h)) == asked + 4, "a quarantined page was sent to a model"
    assert len(facts_on(h, OTHER_PAGE)) == 1


async def test_undoing_brings_the_page_back_with_its_verified_facts(h: Harness) -> None:
    (fact,) = facts_on(h, OTHER_PAGE)
    h.admin.execute("update app.facts set status = 'verified' where id = %s", (fact["id"],))
    assert (await h.client.post(quarantine_path(h, OTHER_PAGE), json={"reason": "not_this_person"})).status_code == 200
    undone = await h.client.delete(quarantine_path(h, OTHER_PAGE))
    assert undone.status_code == 200, undone.status_code
    assert undone.json()["status"] == "undone" and undone.json()["job_id"]
    row = next(p for p in h.pages() if p["bundle_page_no"] == OTHER_PAGE)
    assert not row["quarantined"] and row["quarantine_reason"] is None
    assert [f["status"] for f in facts_on(h, OTHER_PAGE)] == ["verified"]
    assert audited(h, "page.quarantine_undone") == 1

    asked = len(t07b_requests(h))
    await h.drain()
    assert len(t07b_requests(h)) == asked + 1, "the page was not read again"
    assert sorted(f["status"] for f in facts_on(h, OTHER_PAGE)) == ["suggested", "verified"]
    got = await summary(h)
    assert got["quarantine_suggestions"] == [] and got["quarantined"] == []


async def test_a_dismissed_suggestion_is_not_made_again(h: Harness) -> None:
    dismissed = await h.client.delete(quarantine_path(h, OTHER_PAGE))
    assert dismissed.status_code == 200, dismissed.status_code
    assert dismissed.json()["status"] == "dismissed" and dismissed.json()["job_id"] is None
    assert (await summary(h))["quarantine_suggestions"] == []
    assert (await h.client.post(f"/v1/cases/{h.case_id}/reprocess")).status_code == 202
    await h.drain()
    assert (await summary(h))["quarantine_suggestions"] == []
    assert audited(h, "page.quarantine_dismissed") == 1


async def test_who_may_suggest_confirm_or_undo(h: Harness, seed: Seed, client_for: ClientFor) -> None:
    body = {"reason": "not_this_person"}
    stranger = await client_for(seed.person())
    viewer = await helper(h, seed, client_for, "viewer")
    assistant = await helper(h, seed, client_for, "assistant")
    url = quarantine_path(h, OTHER_PAGE)
    assert (await stranger.post(url, json=body)).status_code == 404
    assert (await stranger.delete(url)).status_code == 404
    assert (await viewer.post(url, json=body)).status_code == 403
    assert (await viewer.delete(url)).status_code == 403
    assert (await assistant.delete(url)).status_code == 403
    unknown = f"/v1/cases/{h.case_id}/pages/{uuid.uuid4()}/quarantine"
    assert (await h.client.post(unknown, json=body)).status_code == 404
    assert (await h.client.post(url, json={"reason": "Not this person!"})).status_code == 422
    assert (await h.client.delete(quarantine_path(h, 1))).status_code == 404


async def test_quarantine_rows_follow_their_state_machine_under_row_level_security(
    admin: psycopg.Connection, seed: Seed, api_db: Database
) -> None:
    expert, assistant, viewer, stranger = seed.person(), seed.person(), seed.person(), seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    seed.case_member(case_id, assistant, "assistant", added_by=expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    doc = seed.document(case_id)
    page = admin.execute(
        "insert into app.pages (bundle_id, document_id, bundle_page_no)"
        " values ((select bundle_id from app.cases where id = %s), %s, 1) returning id",
        (case_id, doc),
    ).fetchone()[0]  # type: ignore[index]
    insert = (
        "insert into app.page_quarantines (case_id, bundle_id, page_id, reason, status, suggested_by)"
        " values (%s, (select bundle_id from app.cases where id = %s), %s, 'not_this_person', %s, %s) returning id"
    )
    async with api_db.request(scope_for(assistant)) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(insert, (case_id, case_id, page, "confirmed", assistant))
        row = await (await conn.execute(insert, (case_id, case_id, page, "suggested", assistant))).fetchone()
        assert row is not None
        cur = await conn.execute("update app.page_quarantines set status = 'confirmed' where id = %s", (row[0],))
        assert cur.rowcount == 0, "an assistant confirmed a quarantine"
    for person in (viewer, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(insert, (case_id, case_id, page, "suggested", person))
    async with api_db.request(scope_for(expert)) as conn:
        await conn.execute("update app.page_quarantines set status = 'confirmed' where id = %s", (row[0],))
        with pytest.raises(psycopg.Error) as refused:
            async with conn.transaction():
                await conn.execute("update app.page_quarantines set status = 'suggested' where id = %s", (row[0],))
        assert refused.value.sqlstate == "AV409"
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute("delete from app.page_quarantines where id = %s", (row[0],))
        with pytest.raises(psycopg.errors.UniqueViolation):
            async with conn.transaction():
                await conn.execute(insert, (case_id, case_id, page, "suggested", expert))
    async with api_db.request(scope_for(viewer)) as conn:
        cur = await conn.execute("select count(*) from app.page_quarantines where case_id = %s", (case_id,))
        assert await cur.fetchone() == (1,)
    async with api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute("select count(*) from app.page_quarantines where case_id = %s", (case_id,))
        assert await cur.fetchone() == (0,)
