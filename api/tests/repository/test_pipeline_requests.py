"""Slice A increment 10: asking the instructing party for what's missing (S08b, architecture 5.2).

- `POST /v1/cases/{id}/instructing-party-requests/draft` (request.draft): the pack's wording, filled
  with the open missing items (their page) and the unreadable pages needing a clearer copy. Names,
  dates of birth and anything the pack's recognisers find are stripped from the case's own words.
  Plain code, no model call.
- `POST /v1/cases/{id}/instructing-party-requests` (request.draft): logs the final text (sealed,
  append-only, hash-chained per case) and returns a `mailto:` link; the open items it lists become
  `requested` (state-machines "Missing item"). Viewers get 403, people outside the case 404.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest

from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_support import Harness, expected, harness
from tests.support.db import ClientFor, scope_for
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

NAME = "Wendy Ashcombe"
DOB = dt.date(1961, 7, 14)


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


async def seal(h: Harness, data: str, purpose: str) -> bytes:
    async with h.api_db.request(scope_for(h.expert)) as conn:
        return await h.crypto.encrypt(conn, h.case_id, data.encode(), purpose=purpose)


def page(admin: psycopg.Connection, case_id: uuid.UUID, doc: uuid.UUID, n: int, *, unreadable: bool = False) -> Any:
    return admin.execute(
        "insert into app.pages (case_id, document_id, bundle_page_no, unreadable) values (%s, %s, %s, %s)"
        " returning id",
        (case_id, doc, n, unreadable),
    ).fetchone()[0]  # type: ignore[index]


async def item(h: Harness, page_id: uuid.UUID, key: str, words: str) -> uuid.UUID:
    item_id = uuid.uuid4()
    sealed = await seal(h, words, paths.missing_description_purpose(item_id))
    h.admin.execute(
        "insert into app.missing_items (id, case_id, citing_page_id, reference_key, word_ids, description)"
        " values (%s, %s, %s, %s, '{p1w1}', %s)",
        (item_id, h.case_id, page_id, key, sealed),
    )
    return item_id


@pytest.fixture(params=BOTH, ids=lambda c: c.codes[0])
async def world(
    request: pytest.FixtureRequest,
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: SupabaseStorage,
) -> AsyncIterator[dict[str, Any]]:
    combo: Combo = request.param
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=combo.case_row(), status="active")
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        names=[NAME],
        dates_of_birth=[DOB],
    ) as h:
        doc = seed.document(case_id)
        p1 = page(admin, case_id, doc, 1)
        p2 = page(admin, case_id, doc, 2, unreadable=True)
        page(admin, case_id, doc, 3)
        p4 = page(admin, case_id, doc, 4, unreadable=True)
        number = expected("case.json")["nhs_number"]
        first = await item(h, p1, "r1", f"Colonoscopy report for {NAME} {number} ZZ-ID-123456")
        second = await item(h, p1, "r2", "Ultrasound scan report")
        gone = await item(h, p1, "r3", "Endoscopy letter")
        admin.execute(
            "update app.missing_items set status = 'not_available', reason = '\\x00' where id = %s", (gone,)
        )
        yield {
            "h": h,
            "pack": default_library().combine(*combo.codes),
            "open": [first, second],
            "gone": gone,
            "unreadable": [p2, p4],
        }


def draft_path(h: Harness) -> str:
    return f"/v1/cases/{h.case_id}/instructing-party-requests/draft"


def log_path(h: Harness) -> str:
    return f"/v1/cases/{h.case_id}/instructing-party-requests"


async def test_the_draft_uses_the_pack_wording_and_lists_open_items_and_clearer_copies(
    world: dict[str, Any],
) -> None:
    h: Harness = world["h"]
    got = await h.client.post(draft_path(h), json={})
    assert got.status_code == 200, got.status_code
    assert got.headers["cache-control"] == "private, no-store"
    draft = got.json()
    wording = world["pack"].instructing_party_request(
        case_reference="", missing_items="", clearer_copies="", expert_name=""
    )
    assert draft["label"] == wording["label"]
    assert draft["missing_item_ids"] == [str(i) for i in world["open"]]
    assert draft["clearer_copy_page_ids"] == [str(p) for p in world["unreadable"]]
    assert draft["clearer_copy_page_nos"] == [2, 4]
    body = draft["body"]
    assert "Colonoscopy report" in body and "Ultrasound scan report" in body
    assert "Endoscopy letter" not in body, "an item that is not available was asked for"
    assert not h.bedrock.requests, "the draft called a model"


async def test_the_draft_holds_no_name_date_of_birth_or_identifier(world: dict[str, Any]) -> None:
    h: Harness = world["h"]
    draft = (await h.client.post(draft_path(h), json={})).json()
    for text in (draft["subject"], draft["body"]):
        assert NAME.split()[0] not in text and NAME.split()[1] not in text
        assert not world["pack"].identifier_recognisers()(text), "an identifier is left in the draft"


async def test_logging_the_request_returns_a_mailto_link_and_moves_its_items_to_requested(
    world: dict[str, Any],
) -> None:
    h: Harness = world["h"]
    draft = (await h.client.post(draft_path(h), json={})).json()
    sent = {
        "to": "records@firm.example",
        "subject": draft["subject"],
        "body": draft["body"] + "\nWith thanks & best wishes?",
        "missing_item_ids": draft["missing_item_ids"] + [str(world["gone"])],
        "clearer_copy_page_ids": draft["clearer_copy_page_ids"],
    }
    logged = await h.client.post(log_path(h), json=sent)
    assert logged.status_code == 201, logged.status_code
    link = urlsplit(logged.json()["mailto"])
    assert (link.scheme, link.path) == ("mailto", "records@firm.example")
    query = parse_qs(link.query)
    assert query["subject"] == [sent["subject"]] and query["body"] == [sent["body"]]
    statuses: dict[Any, Any] = dict(
        h.admin.execute("select id, status from app.missing_items where case_id = %s", (h.case_id,)).fetchall()
    )
    assert statuses == {world["open"][0]: "requested", world["open"][1]: "requested", world["gone"]: "not_available"}
    row = h.admin.execute(
        "select sent_via, body_text, missing_item_ids, page_ids, created_by from app.instructing_party_requests"
        " where case_id = %s",
        (h.case_id,),
    ).fetchone()
    assert row is not None
    assert row[0] == "mailto"
    assert sent["body"].encode() not in bytes(row[1]), "the logged text is not sealed"
    assert set(row[2]) == {*world["open"], world["gone"]} and list(row[3]) == world["unreadable"]
    assert row[4] == h.expert
    audited = h.admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'instructing_party_request.logged'",
        (h.case_id,),
    ).fetchone()
    assert audited == (1,)

    again = await h.client.post(log_path(h), json={**sent, "to": None})
    assert again.status_code == 201
    assert urlsplit(again.json()["mailto"]).path == ""
    assert h.admin.execute(
        "select count(*) from app.instructing_party_requests where case_id = %s", (h.case_id,)
    ).fetchone() == (2,)


async def test_requests_check_who_asks_and_what_they_list(
    world: dict[str, Any], seed: Seed, client_for: ClientFor
) -> None:
    h: Harness = world["h"]
    viewer, assistant = seed.person(), seed.person()
    seed.case_member(h.case_id, viewer, "viewer", added_by=h.expert)
    seed.case_member(h.case_id, assistant, "assistant", added_by=h.expert)
    body = {"subject": "s", "body": "b", "missing_item_ids": [], "clearer_copy_page_ids": []}
    stranger = await client_for(seed.person())
    assert (await stranger.post(draft_path(h), json={})).status_code == 404
    assert (await stranger.post(log_path(h), json=body)).status_code == 404
    refused = await client_for(viewer)
    assert (await refused.post(draft_path(h), json={})).status_code == 403
    assert (await refused.post(log_path(h), json=body)).status_code == 403
    unknown = {**body, "missing_item_ids": [str(uuid.uuid4())]}
    assert (await h.client.post(log_path(h), json=unknown)).status_code == 404
    unknown_page = {**body, "clearer_copy_page_ids": [str(uuid.uuid4())]}
    assert (await h.client.post(log_path(h), json=unknown_page)).status_code == 404
    assert (await h.client.post(log_path(h), json={**body, "to": "not an address"})).status_code == 422
    assert (await h.client.post(log_path(h), json={**body, "body": ""})).status_code == 422


async def test_the_request_log_is_append_only_and_chained(
    admin: psycopg.Connection, seed: Seed, api_db: Database
) -> None:
    expert, viewer, stranger = seed.person(), seed.person(), seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    insert = (
        "insert into app.instructing_party_requests (case_id, subject, body_text, sent_via, created_by)"
        " values (%s, '\\x01', '\\x02', 'mailto', %s) returning id, prev_hash, hash"
    )
    async with api_db.request(scope_for(expert)) as conn:
        first = await (await conn.execute(insert, (case_id, expert))).fetchone()
        second = await (await conn.execute(insert, (case_id, expert))).fetchone()
    assert first is not None and second is not None
    assert first[1] == "0" * 64 and second[1] == first[2] and len(second[2]) == 64
    async with api_db.request(scope_for(expert)) as conn:
        for sql in (
            "update app.instructing_party_requests set sent_via = 'gmail' where case_id = %s",
            "delete from app.instructing_party_requests where case_id = %s",
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(sql, (case_id,))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            async with conn.transaction():
                await conn.execute(insert, (case_id, viewer))  # only as yourself
    for person in (viewer, stranger):
        async with api_db.request(scope_for(person)) as conn:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                async with conn.transaction():
                    await conn.execute(insert, (case_id, person))
    async with api_db.request(scope_for(viewer)) as conn:
        cur = await conn.execute("select count(*) from app.instructing_party_requests where case_id = %s", (case_id,))
        assert await cur.fetchone() == (2,)
    async with api_db.request(scope_for(stranger)) as conn:
        cur = await conn.execute("select count(*) from app.instructing_party_requests where case_id = %s", (case_id,))
        assert await cur.fetchone() == (0,)
