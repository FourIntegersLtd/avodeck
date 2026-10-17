"""Slice A increment 8: the bundle summary, provenance and versions (architecture 5.2, S08).

- `GET /v1/cases/{id}/bundle/summary` (case.view_content): page counts by source, pages set aside as
  copies, unreadable pages, record gaps not dismissed, open missing items, each document's provenance
  and what each delivery of records (bundle version) added. Numbers, ids and pack keys only.
- `PATCH /v1/cases/{id}/documents/{doc_id}` (case.upload): how the records were obtained and when
  they arrived; audited. Viewers get 403, people outside the case 404.
- `GET /v1/cases/{id}/bundle/versions` (case.view_content): what each delivery added.
"""

from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest

from tests.support.db import ClientFor
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio


def document(admin: psycopg.Connection, case_id: uuid.UUID, *, version: int, name: str) -> uuid.UUID:
    row = admin.execute(
        "insert into app.documents (case_id, kind, filename, sha256, pages, bundle_version)"
        " values (%s, 'records', %s, %s, 0, %s) returning id",
        (case_id, name, "0" * 64, version),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def page(
    admin: psycopg.Connection,
    case_id: uuid.UUID,
    doc: uuid.UUID,
    number: int,
    *,
    source: str | None = None,
    unreadable: bool = False,
) -> uuid.UUID:
    row = admin.execute(
        "insert into app.pages (case_id, document_id, bundle_page_no, source_type, unreadable)"
        " values (%s, %s, %s, %s, %s) returning id",
        (case_id, doc, number, source, unreadable),
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> dict[str, Any]:
    expert, assistant, viewer, stranger = (seed.person() for _ in range(4))
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, status="active")
    seed.case_member(case_id, assistant, "assistant", added_by=expert)
    seed.case_member(case_id, viewer, "viewer", added_by=expert)
    first = document(admin, case_id, version=1, name="first.pdf")
    later = document(admin, case_id, version=2, name="later.pdf")
    p1 = page(admin, case_id, first, 1, source="alpha")
    page(admin, case_id, first, 2, source="alpha", unreadable=True)
    page(admin, case_id, first, 3)
    page(admin, case_id, later, 4, source="beta")
    copy = page(admin, case_id, later, 5, source="alpha")
    kept = page(admin, case_id, later, 6, source="beta")
    admin.execute("update app.documents set pages = 3 where id = %s", (first,))
    admin.execute("update app.documents set pages = 3 where id = %s", (later,))
    admin.execute("update app.pages set duplicate_of_page_id = %s where id in (%s, %s)", (p1, copy, kept))
    admin.execute(
        "insert into app.page_analysis (page_id, case_id, duplicate_status) values (%s, %s, 'suggested'),"
        " (%s, %s, 'kept')",
        (copy, case_id, kept, case_id),
    )
    for key in ("r1", "r2", "r3"):
        admin.execute(
            "insert into app.missing_items (case_id, citing_page_id, reference_key, word_ids, description)"
            " values (%s, %s, %s, '{p1w1}', '\\x00')",
            (case_id, p1, key),
        )
    admin.execute(
        "update app.missing_items set status = 'not_available', reason = '\\x00'"
        " where case_id = %s and reference_key = 'r3'",
        (case_id,),
    )
    for kind, confirmed in (("k1", None), ("k2", True), ("k3", False)):
        admin.execute(
            "insert into app.record_gaps (case_id, source_type, fact_kind, gap_start, gap_end, last_page_id,"
            " confirmed) values (%s, 'alpha', %s, '2024-02-01', '2024-06-30', %s, %s)",
            (case_id, kind, p1, confirmed),
        )
    return {
        "expert": expert,
        "assistant": assistant,
        "viewer": viewer,
        "stranger": stranger,
        "case": case_id,
        "first": first,
        "later": later,
    }


async def test_the_summary_counts_pages_by_source_copies_unreadable_gaps_and_missing(
    world: dict[str, Any], client_for: ClientFor
) -> None:
    client = await client_for(world["viewer"])
    got = await client.get(f"/v1/cases/{world['case']}/bundle/summary")
    assert got.status_code == 200, got.status_code
    assert got.headers["cache-control"] == "private, no-store"
    body = got.json()
    assert body["pages"] == 6
    assert body["by_source"] == [
        {"source_type": "alpha", "pages": 3},
        {"source_type": "beta", "pages": 2},
        {"source_type": None, "pages": 1},
    ]
    assert body["duplicates"] == 1
    assert body["unreadable"] == 1
    assert body["gaps"] == 2
    assert body["missing_items"] == 2
    docs = {d["id"]: d for d in body["documents"]}
    assert docs[str(world["first"])] | {} == {
        "id": str(world["first"]),
        "kind": "records",
        "filename": "first.pdf",
        "pages": 3,
        "first_page_no": 1,
        "last_page_no": 3,
        "received_on": None,
        "obtained_how": None,
        "bundle_version": 1,
    }
    assert [
        (v["bundle_version"], v["documents"], v["pages"], v["first_page_no"], v["last_page_no"])
        for v in body["versions"]
    ] == [(1, 1, 3, 1, 3), (2, 1, 3, 4, 6)]


async def test_only_people_on_the_case_see_the_summary_and_versions(
    world: dict[str, Any], client_for: ClientFor
) -> None:
    stranger = await client_for(world["stranger"])
    for path in ("bundle/summary", "bundle/versions"):
        assert (await stranger.get(f"/v1/cases/{world['case']}/{path}")).status_code == 404
    viewer = await client_for(world["viewer"])
    got = await viewer.get(f"/v1/cases/{world['case']}/bundle/versions")
    assert got.status_code == 200
    assert [
        (v["bundle_version"], v["pages"], v["first_page_no"], v["last_page_no"]) for v in got.json()["versions"]
    ] == [
        (1, 3, 1, 3),
        (2, 3, 4, 6),
    ]
    assert all(v["added_at"] for v in got.json()["versions"])


async def test_the_expert_or_assistant_records_how_records_were_obtained_and_when(
    world: dict[str, Any], client_for: ClientFor, admin: psycopg.Connection
) -> None:
    path = f"/v1/cases/{world['case']}/documents/{world['first']}"
    body = {"received_on": "2026-10-02", "obtained_how": "Request letter"}
    assert (await (await client_for(world["stranger"])).patch(path, json=body)).status_code == 404
    assert (await (await client_for(world["viewer"])).patch(path, json=body)).status_code == 403
    assistant = await client_for(world["assistant"])
    changed = await assistant.patch(path, json=body)
    assert changed.status_code == 200, changed.status_code
    assert (changed.json()["received_on"], changed.json()["obtained_how"]) == ("2026-10-02", "Request letter")
    row = admin.execute("select received_on::text, obtained_how from app.documents where id = %s", (world["first"],))
    assert row.fetchone() == ("2026-10-02", "Request letter")
    audited = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'document.provenance_changed'",
        (world["case"],),
    ).fetchone()
    assert audited == (1,)
    only_date = await assistant.patch(path, json={"received_on": "2026-10-03"})
    assert (only_date.json()["received_on"], only_date.json()["obtained_how"]) == ("2026-10-03", "Request letter")


async def test_provenance_changes_are_checked(world: dict[str, Any], client_for: ClientFor) -> None:
    expert = await client_for(world["expert"])
    base = f"/v1/cases/{world['case']}/documents"
    assert (await expert.patch(f"{base}/{uuid.uuid4()}", json={"obtained_how": "x"})).status_code == 404
    assert (await expert.patch(f"{base}/{world['first']}", json={"obtained_how": "x" * 201})).status_code == 422
    assert (await expert.patch(f"{base}/{world['first']}", json={"received_on": "not a date"})).status_code == 422
