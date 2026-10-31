"""Screen 25's offline copy (slice I brief, rule 4's exception, rule 10).

The package holds the served report, verified facts with their old and trial page numbers, the concordance,
the joint statement and the answers, as format version 1. It is encrypted (no readable text in the stored
bytes); its key is released once, at download, then forgotten. Creating, downloading and deleting it need
step-up. After `expires_at` or revocation it no longer downloads, and the status check says why.
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.db import Database
from app.core.storage import CaseFileStorage
from tests.fixtures.trial.fixtures import (  # noqa: F401
    aconn,
    app,
    combo,
    reads,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import PLAN, SERVED_VIEW, World, trial_bundle
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

LAPSED = timedelta(minutes=11)


def code(resp: Any) -> str:
    return str(resp.json()["error"]["code"])


async def create(client_for: ClientFor, w: World, **body: Any) -> dict[str, Any]:
    resp = await (await client_for(w.expert)).post(
        w.url("offline-packages"), json={"device_label": "Zz tablet", **body}
    )
    assert resp.status_code == 201, resp.text
    out: dict[str, Any] = resp.json()
    return out


def opened(payload: dict[str, Any]) -> dict[str, Any]:
    key = base64.b64decode(payload["key"])
    iv = base64.b64decode(payload["iv"])
    aad = f"avodeck-offline:{payload['id']}:{payload['format_version']}".encode()
    plain = AESGCM(key).decrypt(iv, base64.b64decode(payload["ciphertext"]), aad)
    content: dict[str, Any] = json.loads(plain)
    return content


async def test_creating_a_package_needs_step_up_and_leaves_out_images(client_for: ClientFor, world: World) -> None:
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).post(
        world.url("offline-packages"), json={"device_label": "Zz tablet"}
    )
    assert lapsed.status_code == 403 and code(lapsed) == "step_up_required"
    viewer = await (await client_for(world.viewer)).post(world.url("offline-packages"), json={"device_label": "x"})
    assert viewer.status_code == 403
    outsider = await (await client_for(world.outsider)).get(world.url("offline-packages"))
    assert outsider.status_code == 404
    images = await (await client_for(world.expert)).post(
        world.url("offline-packages"), json={"device_label": "Zz tablet", "include_images": True}
    )
    assert images.status_code == 422
    made = await create(client_for, world, days=14)
    assert made["status"] == "built" and made["format_version"] == 1 and made["include_images"] is False
    listed = (await (await client_for(world.expert)).get(world.url("offline-packages"))).json()["packages"]
    assert [p["id"] for p in listed] == [made["id"]]


async def test_the_package_is_encrypted_and_its_key_is_released_once(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
) -> None:
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    made = await create(client_for, world)
    row = admin.execute("select storage_path from app.offline_packages where id = %s", (made["id"],)).fetchone()
    assert row is not None
    stored = await storage.get(row[0])
    assert PLAN.encode() not in stored and b"low iron" not in stored and b"x3/52" not in stored
    url = world.url(f"offline-packages/{made['id']}/download")
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).get(url)
    assert lapsed.status_code == 403
    resp = await (await client_for(world.expert)).get(url)
    assert resp.status_code == 200, resp.text
    assert resp.headers["cache-control"] == "private, no-store"
    payload = resp.json()
    assert payload["format_version"] == 1 and payload["case_id"] == str(world.case)
    content = opened(payload)
    assert content["format_version"] == 1
    assert SERVED_VIEW in [p["text"] for p in content["report"]["paragraphs"]]
    [plan] = [f for f in content["facts"] if f["text"] == PLAN]
    assert plan["page_no"] == 118 and plan["trial_page_no"] == where[118]
    pages = {c["page_no"]: c for c in content["concordance"]}
    assert pages[214]["trial_page_no"] is None and pages[118]["trial_page_no"] == where[118]
    assert "joint_statement" in content and "answers" in content
    again = await (await client_for(world.expert)).get(url)
    assert again.status_code == 410 and code(again) == "already_downloaded"
    key = admin.execute("select key_sealed, status from app.offline_packages where id = %s", (made["id"],)).fetchone()
    assert key == (None, "downloaded")


async def test_a_revoked_package_no_longer_downloads_and_the_status_check_says_so(
    client_for: ClientFor, world: World
) -> None:
    made = await create(client_for, world)
    url = world.url(f"offline-packages/{made['id']}")
    lapsed = await (await client_for(world.expert, mfa_age=LAPSED)).delete(url)
    assert lapsed.status_code == 403
    gone = await (await client_for(world.expert)).delete(url)
    assert gone.status_code == 204, gone.text
    status = await (await client_for(world.expert)).get(url)
    assert status.status_code == 200 and status.json()["status"] == "revoked"
    resp = await (await client_for(world.expert)).get(f"{url}/download")
    assert resp.status_code == 410 and code(resp) == "revoked"


async def test_an_expired_package_no_longer_downloads(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    made = await create(client_for, world)
    admin.execute(
        "update app.offline_packages set expires_at = now() - interval '1 minute' where id = %s", (made["id"],)
    )
    url = world.url(f"offline-packages/{made['id']}")
    status = await (await client_for(world.expert)).get(url)
    assert status.json()["status"] == "expired"
    resp = await (await client_for(world.expert)).get(f"{url}/download")
    assert resp.status_code == 410 and code(resp) == "expired"
    unknown = await (await client_for(world.expert)).get(world.url(f"offline-packages/{uuid.uuid4()}"))
    assert unknown.status_code == 404


async def test_the_status_check_says_only_whether_the_copy_may_still_be_opened(
    client_for: ClientFor, world: World, admin: psycopg.Connection
) -> None:
    """Screen 25 asks this when online and wipes the copy on revoked, expired or 404 (threat model T10).

    It needs case access but not step-up (the expert may be in court hours after signing in), and it
    returns the status alone: no case content, no device name.
    """
    made = await create(client_for, world)
    url = world.url(f"offline-packages/{made['id']}/status")
    fresh = await (await client_for(world.expert, mfa_age=LAPSED)).get(url)
    assert fresh.status_code == 200, fresh.text
    assert fresh.json() == {"status": "built"}
    assert fresh.headers["cache-control"] == "private, no-store"
    assert (await (await client_for(world.outsider)).get(url)).status_code == 404
    assert (
        await (await client_for(world.expert)).get(world.url(f"offline-packages/{uuid.uuid4()}/status"))
    ).status_code == 404
    admin.execute(
        "update app.offline_packages set expires_at = now() - interval '1 minute' where id = %s", (made["id"],)
    )
    assert (await (await client_for(world.expert)).get(url)).json() == {"status": "expired"}
    other = await create(client_for, world)
    other_url = world.url(f"offline-packages/{other['id']}")
    assert (await (await client_for(world.expert)).delete(other_url)).status_code == 204
    assert (await (await client_for(world.expert)).get(f"{other_url}/status")).json() == {"status": "revoked"}


async def test_the_copy_carries_the_case_label_and_each_facts_source_type_with_its_colour(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    admin.execute("update app.pages set source_type = 'primary_care_record' where case_id = %s", (world.case,))
    made = await create(client_for, world)
    payload = (await (await client_for(world.expert)).get(world.url(f"offline-packages/{made['id']}/download"))).json()
    content = opened(payload)
    row = admin.execute("select label from app.cases where id = %s", (world.case,)).fetchone()
    assert row is not None and content["case_label"] == row[0]
    [plan] = [f for f in content["facts"] if f["text"] == PLAN]
    assert plan["source_type"] == "primary_care_record"
    kinds = {s["key"]: s for s in content["source_types"]}
    assert kinds["primary_care_record"]["colour"] == "#1E6B4E" and kinds["primary_care_record"]["label"] == "GP record"
    assert kinds["hospital"]["colour"].startswith("#")


async def test_the_list_says_which_copies_are_live(client_for: ClientFor, world: World) -> None:
    kept = await create(client_for, world)
    gone = await create(client_for, world)
    removed = await (await client_for(world.expert)).delete(world.url(f"offline-packages/{gone['id']}"))
    assert removed.status_code == 204
    body = (await (await client_for(world.expert)).get(world.url("offline-packages"))).json()
    assert {p["id"] for p in body["packages"]} == {kept["id"], gone["id"]}
    assert [p["id"] for p in body["live"]] == [kept["id"]]
