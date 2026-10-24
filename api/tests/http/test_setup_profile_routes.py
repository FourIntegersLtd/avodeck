"""Screen 02, the expert profile (slice C brief; architecture 5.2).

- Profile, roles (with date ranges) and training are the expert's own and editable with ETag/If-Match.
- The report template is stored unchanged (same SHA-256), encrypted, and its headings are listed.
- Declaration wording comes from the jurisdiction pack and cannot be changed; the expert may add
  wording until their first report is served, then 409.
- A drawn or photographed signature is stored encrypted with its background removed and comes back
  only through the API.
- Balance: how many cases for each side over the last months.
"""

from __future__ import annotations

import hashlib
import io
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import docx
import httpx
import psycopg
import pytest
from fastapi import FastAPI
from PIL import Image

from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.core.db import Database
from app.core.http import etag
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import FIXTURE, LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "setup"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
def cell_region() -> str:
    return str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"])


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], storage: SupabaseStorage, cell_region: str) -> FastAPI:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.email import OutboxMailer
    from app.main import create_app

    return create_app(
        database=api_db,
        region=cell_region,
        auth=SupabaseAuth(auth_settings(auth_env)),
        mailer=OutboxMailer(),
        storage=storage,
        crypto=CaseCrypto(LocalKeyProvider.generate()),
    )


@pytest.fixture
def expert(seed: Seed) -> uuid.UUID:
    person = seed.person()
    seed.org(kind="personal", members={person: "owner"})
    return person


# --- profile -------------------------------------------------------------------------------------------


async def test_profile_reads_and_saves_with_an_etag(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    first = await client.get("/v1/me/profile")
    assert first.status_code == 200, first.text
    assert first.json()["specialties"] == []
    tag = first.headers["etag"]

    resp = await client.patch(
        "/v1/me/profile",
        json={"full_name": "Dr Test", "profession": "therapist", "specialties": ["zz-specialty"]},
        headers={"if-match": tag},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["specialties"] == ["zz-specialty"]
    assert resp.json()["full_name"] == "Dr Test"
    assert resp.headers["etag"] != tag

    again = await client.get("/v1/me/profile")
    assert again.json()["profession"] == "therapist"
    assert (
        await client.patch("/v1/me/profile", json={"profession": "x"}, headers={"if-match": tag})
    ).status_code == 412
    assert (await client.patch("/v1/me/profile", json={"profession": "x"})).status_code == 428
    unknown = await client.patch("/v1/me/profile", json={"surprise": 1}, headers={"if-match": again.headers["etag"]})
    assert unknown.status_code == 422


async def test_profile_is_not_found_for_support_staff_without_an_expert_row(
    client_for: ClientFor, seed: Seed, expert: uuid.UUID
) -> None:
    staff, both = seed.person(), seed.person()
    seed.support_staff(staff)
    seed.support_staff(both)
    seed.org(kind="personal", members={both: "owner"})
    seed.admin.execute("insert into app.experts (person_id) values (%s)", (both,))
    client = await client_for(staff)
    assert (await client.get("/v1/me/profile")).status_code == 404
    assert (
        await client.patch("/v1/me/profile", json={"profession": "x"}, headers={"if-match": etag(1)})
    ).status_code == 404
    assert (await (await client_for(both)).get("/v1/me/profile")).status_code == 200
    assert (await (await client_for(expert)).get("/v1/me/profile")).status_code == 200


# --- roles and training ------------------------------------------------------------------------------


async def test_roles_store_date_ranges(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    setting = FIXTURE.raw("specialty", "pack.yaml")["coverage_roles"][0]
    body = {
        "specialty": "zz-specialty",
        "role_title": "Practitioner",
        "setting": setting,
        "started_on": "2005-04-01",
        "ended_on": None,
    }
    created = await client.post("/v1/me/roles", json=body)
    assert created.status_code == 201, created.text
    role = created.json()
    assert (role["started_on"], role["ended_on"], role["setting"]) == ("2005-04-01", None, setting)

    listed = (await client.get("/v1/me/roles")).json()
    assert [r["id"] for r in listed] == [role["id"]]

    changed = await client.patch(
        f"/v1/me/roles/{role['id']}", json={"ended_on": "2024-08-31"}, headers={"if-match": created.headers["etag"]}
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["ended_on"] == "2024-08-31"
    stale = await client.patch(
        f"/v1/me/roles/{role['id']}", json={"ended_on": "2024-09-30"}, headers={"if-match": created.headers["etag"]}
    )
    assert stale.status_code == 412

    assert (await client.delete(f"/v1/me/roles/{role['id']}")).status_code == 204
    assert (await client.get("/v1/me/roles")).json() == []


async def test_a_role_that_ends_before_it_starts_is_refused(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    body = {
        "specialty": "zz-specialty",
        "role_title": "Practitioner",
        "started_on": "2020-01-01",
        "ended_on": "2019-01-01",
    }
    resp = await client.post("/v1/me/roles", json=body)
    assert resp.status_code == 422


async def test_a_role_setting_must_come_from_the_specialty_pack(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    body = {
        "specialty": "zz-specialty",
        "role_title": "Practitioner",
        "setting": "not_a_setting",
        "started_on": "2020-01-01",
    }
    assert (await client.post("/v1/me/roles", json=body)).status_code == 422
    body = {"specialty": "no-such-specialty", "role_title": "Practitioner", "started_on": "2020-01-01"}
    assert (await client.post("/v1/me/roles", json=body)).status_code == 422


async def test_someone_elses_role_is_not_found(client_for: ClientFor, expert: uuid.UUID, seed: Seed) -> None:
    mine = await client_for(expert)
    role = await mine.post(
        "/v1/me/roles", json={"specialty": "zz-specialty", "role_title": "Practitioner", "started_on": "2020-01-01"}
    )
    other = await client_for(seed.person())
    assert (
        await other.patch(f"/v1/me/roles/{role.json()['id']}", json={}, headers={"if-match": '"1"'})
    ).status_code == 404
    assert (await other.delete(f"/v1/me/roles/{role.json()['id']}")).status_code == 404
    assert (await other.get("/v1/me/roles")).json() == []


async def test_training_is_listed_added_and_removed(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    created = await client.post("/v1/me/training", json={"title": "Expert witness course", "year": 2019})
    assert created.status_code == 201, created.text
    assert [t["title"] for t in (await client.get("/v1/me/training")).json()] == ["Expert witness course"]
    assert (await client.delete(f"/v1/me/training/{created.json()['id']}")).status_code == 204
    assert (await client.get("/v1/me/training")).json() == []
    assert (await client.post("/v1/me/training", json={"title": "", "year": 2019})).status_code == 422


# --- template ----------------------------------------------------------------------------------------


def fixture_headings() -> list[str]:
    document = docx.Document(str(FIXTURES / "template.docx"))
    return [p.text for p in document.paragraphs if p.style is not None and "heading" in str(p.style.name).lower()]


async def upload_template(client: httpx.AsyncClient, data: bytes) -> httpx.Response:
    return await client.post(
        "/v1/me/template/file", params={"filename": "template.docx"}, content=data, headers={"content-type": DOCX}
    )


async def test_the_template_is_stored_unchanged_and_its_headings_listed(
    client_for: ClientFor, expert: uuid.UUID, storage: SupabaseStorage
) -> None:
    data = (FIXTURES / "template.docx").read_bytes()
    client = await client_for(expert)
    resp = await upload_template(client, data)
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["sha256"] == hashlib.sha256(data).hexdigest()
    assert body["version"] == 1
    assert [h["text"] for h in body["headings"]] == fixture_headings()
    assert "storage_path" not in body

    download = await client.get("/v1/me/template/file")
    assert download.status_code == 200
    assert download.content == data
    assert "no-store" in download.headers["cache-control"]

    stored = [await storage.get(p) for p in await storage.list(f"people/{expert}/")]
    assert stored and all(data not in blob and not blob.startswith(b"PK") for blob in stored)

    second = await upload_template(client, data)
    assert second.json()["version"] == 2
    assert (await client.get("/v1/me/template")).json()["version"] == 2


async def test_a_file_that_is_not_a_word_document_is_refused(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    resp = await upload_template(client, b"not a word file")
    assert resp.status_code == 422
    assert (await client.get("/v1/me/template")).status_code == 404


async def test_the_heading_mapping_is_saved_with_if_match(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    up = await upload_template(client, (FIXTURES / "template.docx").read_bytes())
    tag = up.headers["etag"]
    first = up.json()["headings"][0]["index"]
    mapping = {"mapping": [{"index": first, "section": "introduction"}]}
    resp = await client.patch("/v1/me/template/mapping", json=mapping, headers={"if-match": tag})
    assert resp.status_code == 200, resp.text
    assert resp.json()["mapping"] == mapping["mapping"]
    assert (await client.patch("/v1/me/template/mapping", json=mapping, headers={"if-match": tag})).status_code == 412
    bad = {"mapping": [{"index": 9999, "section": "introduction"}]}
    assert (
        await client.patch("/v1/me/template/mapping", json=bad, headers={"if-match": resp.headers["etag"]})
    ).status_code == 422


# --- declaration -------------------------------------------------------------------------------------


def pack_wording(code: str) -> list[dict[str, str]]:
    wording: list[dict[str, str]] = default_library().jurisdiction_summary(code)["declarations"]
    return wording


async def test_declaration_wording_comes_from_the_pack_and_additions_save(
    client_for: ClientFor, expert: uuid.UUID
) -> None:
    client = await client_for(expert)
    resp = await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [(w["key"], w["text"]) for w in body["wording"]] == [(w["key"], w["text"]) for w in pack_wording("zz-test")]
    assert body["locked"] is False
    kind = body["wording"][0]["key"]

    put = await client.put(
        "/v1/me/declaration",
        json={"jurisdiction": FIXTURE.jurisdiction, "additions": [{"kind": kind, "text": "I add these words."}]},
        headers={"if-match": resp.headers["etag"]},
    )
    assert put.status_code == 200, put.text
    assert put.json()["additions"] == [{"kind": kind, "text": "I add these words."}]
    assert put.json()["wording"] == body["wording"]
    stale = await client.put(
        "/v1/me/declaration",
        json={"jurisdiction": FIXTURE.jurisdiction, "additions": []},
        headers={"if-match": resp.headers["etag"]},
    )
    assert stale.status_code == 412


async def test_the_pack_wording_cannot_be_changed(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    tag = (await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})).headers["etag"]
    body = {"jurisdiction": FIXTURE.jurisdiction, "additions": [], "wording": [{"key": "affirmation", "text": "Mine."}]}
    assert (await client.put("/v1/me/declaration", json=body, headers={"if-match": tag})).status_code == 422
    body = {"jurisdiction": FIXTURE.jurisdiction, "additions": [{"kind": "not_a_declaration", "text": "x"}]}
    assert (await client.put("/v1/me/declaration", json=body, headers={"if-match": tag})).status_code == 422
    unknown = await client.get("/v1/me/declaration", params={"jurisdiction": LAUNCH.jurisdiction})
    assert unknown.status_code == 404  # not a jurisdiction of this cell


@pytest.mark.parametrize("cell_region", [str(LAUNCH.raw("jurisdiction", "pack.yaml")["cell"])])
async def test_the_launch_declaration_wording_is_the_launch_packs(client_for: ClientFor, expert: uuid.UUID) -> None:
    resp = await (await client_for(expert)).get("/v1/me/declaration", params={"jurisdiction": LAUNCH.jurisdiction})
    assert resp.status_code == 200, resp.text
    assert [w["text"] for w in resp.json()["wording"]] == [w["text"] for w in pack_wording(LAUNCH.jurisdiction)]


async def test_the_declaration_locks_after_the_first_served_report(
    client_for: ClientFor, expert: uuid.UUID, seed: Seed
) -> None:
    client = await client_for(expert)
    first = await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})
    kind = first.json()["wording"][0]["key"]
    body = {"jurisdiction": FIXTURE.jurisdiction, "additions": [{"kind": kind, "text": "Before serving."}]}
    saved = await client.put("/v1/me/declaration", json=body, headers={"if-match": first.headers["etag"]})
    assert saved.status_code == 200, saved.text

    org = seed.org(kind="personal", members={expert: "owner"})
    seed.case(org, expert, status="served")
    after = await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})
    assert after.json()["locked"] is True
    body["additions"] = [{"kind": kind, "text": "After serving."}]
    refused = await client.put("/v1/me/declaration", json=body, headers={"if-match": after.headers["etag"]})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "conflict"
    assert (await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})).json()[
        "additions"
    ] == [{"kind": kind, "text": "Before serving."}]


# --- signature ---------------------------------------------------------------------------------------


def signature_image(fmt: str, background: tuple[int, ...]) -> bytes:
    mode = "RGBA" if len(background) == 4 else "RGB"
    ink = (20, 20, 40, 255) if mode == "RGBA" else (20, 20, 40)
    image = Image.new(mode, (120, 60), background)
    for x in range(20, 100):
        for y in range(28, 33):
            image.putpixel((x, y), ink)
    out = io.BytesIO()
    image.save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize(
    ("method", "data", "content_type"),
    [
        ("drawn", signature_image("PNG", (255, 255, 255, 255)), "image/png"),
        ("photo", signature_image("JPEG", (226, 222, 214)), "image/jpeg"),
    ],
    ids=["drawn", "photo"],
)
async def test_a_signature_is_stored_with_its_background_removed(
    client_for: ClientFor, expert: uuid.UUID, storage: SupabaseStorage, method: str, data: bytes, content_type: str
) -> None:
    client = await client_for(expert)
    resp = await client.post(
        "/v1/me/signature", params={"method": method}, content=data, headers={"content-type": content_type}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["method"] == method
    assert not any(isinstance(v, str) and ("/" in v or "http" in v) for v in resp.json().values())

    image = await client.get("/v1/me/signature/image")
    assert image.status_code == 200
    assert image.headers["content-type"] == "image/png"
    assert "no-store" in image.headers["cache-control"]
    png = Image.open(io.BytesIO(image.content)).convert("RGBA")
    alpha = png.getchannel("A")
    assert alpha.getpixel((2, 2)) == 0  # background gone
    assert alpha.getpixel((60, 30)) == 255  # the ink stays

    stored = [await storage.get(p) for p in await storage.list(f"people/{expert}/")]
    assert stored and all(not blob.startswith(b"\x89PNG") and data not in blob for blob in stored)


async def test_signature_image_is_only_the_signed_in_persons(
    client_for: ClientFor, expert: uuid.UUID, seed: Seed
) -> None:
    client = await client_for(expert)
    data = signature_image("PNG", (255, 255, 255, 255))
    await client.post(
        "/v1/me/signature", params={"method": "drawn"}, content=data, headers={"content-type": "image/png"}
    )
    assert (await (await client_for(seed.person())).get("/v1/me/signature/image")).status_code == 404


async def test_a_signature_that_is_not_an_image_is_refused(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    resp = await client.post(
        "/v1/me/signature", params={"method": "drawn"}, content=b"nope", headers={"content-type": "image/png"}
    )
    assert resp.status_code == 422
    bad = signature_image("PNG", (255, 255, 255, 255))
    resp = await client.post(
        "/v1/me/signature", params={"method": "scanned"}, content=bad, headers={"content-type": "image/png"}
    )
    assert resp.status_code == 422


# --- balance -----------------------------------------------------------------------------------------


async def test_balance_counts_cases_for_each_side_over_the_last_months(
    client_for: ClientFor, expert: uuid.UUID, seed: Seed, admin: psycopg.Connection
) -> None:
    roles = LAUNCH.raw("case-type", "pack.yaml")["party_roles"]
    org = seed.org(kind="personal", members={expert: "owner"})
    wanted = {roles[0]: 2, roles[1]: 1, roles[2]: 1}
    for role, n in wanted.items():
        for _ in range(n):
            case = seed.case(org, expert, packs=LAUNCH.case_row())
            admin.execute("update app.cases set party_role = %s where id = %s", (role, case))
    old = seed.case(org, expert, packs=LAUNCH.case_row())
    admin.execute(
        "update app.cases set party_role = %s, created_at = now() - interval '13 months' where id = %s",
        (roles[0], old),
    )
    someone = seed.person()
    seed.case(seed.org(kind="personal", members={someone: "owner"}), someone, packs=LAUNCH.case_row())

    resp = await (await client_for(expert)).get("/v1/me/stats/balance", params={"months": 12})
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"months": 12, "counts": wanted}
    assert (await (await client_for(expert)).get("/v1/me/stats/balance", params={"months": 0})).status_code == 422


# --- design match: edit in place and the declaration's points (screen 02) --------------------------------


async def test_profile_and_roles_carry_the_version_a_change_must_send(client_for: ClientFor, expert: uuid.UUID) -> None:
    client = await client_for(expert)
    profile = await client.get("/v1/me/profile")
    assert f'"{profile.json()["version"]}"' == profile.headers["etag"]
    created = await client.post(
        "/v1/me/roles", json={"specialty": "zz-specialty", "role_title": "Practitioner", "started_on": "2005-04-01"}
    )
    assert f'"{created.json()["version"]}"' == created.headers["etag"]
    listed = (await client.get("/v1/me/roles")).json()
    assert listed[0]["version"] == created.json()["version"]
    changed = await client.patch(
        f"/v1/me/roles/{listed[0]['id']}",
        json={"role_title": "Senior practitioner"},
        headers={"if-match": f'"{listed[0]["version"]}"'},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["role_title"] == "Senior practitioner"
    assert changed.json()["version"] > listed[0]["version"]
    again = await client.patch(
        f"/v1/me/roles/{listed[0]['id']}",
        json={"role_title": "Other"},
        headers={"if-match": f'"{listed[0]["version"]}"'},
    )
    assert again.status_code == 412


async def test_declaration_wording_says_how_many_numbered_points_it_has(
    client_for: ClientFor, expert: uuid.UUID
) -> None:
    client = await client_for(expert)
    wording = (await client.get("/v1/me/declaration", params={"jurisdiction": FIXTURE.jurisdiction})).json()["wording"]
    assert wording and all("points" in w for w in wording)
    for w in wording:
        numbered = [line for line in w["text"].splitlines() if line.lstrip()[:1].isdigit() and "." in line[:5]]
        assert w["points"] == (len(numbered) or None)
