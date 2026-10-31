"""Slice F, colleague spot-check (screen 17): up to 10 random cited facts sent to a colleague.

Acceptance (slice F brief; threat model T9): the expert (`report.build`) starts a spot-check of the latest
report's cited facts; the link token is random and stored only as a hash; it expires after 7 days; a
6-digit code is emailed to the colleague (the mail carries the code only); the colleague sees each cited
value and a watermarked crop around the cited words (never the full page) with party names blacked out;
wrong codes lock the link; every opening is logged; answers are stored once and the expert sees them.

The colleague's side (`spot_checks.open_check` / `respond`) runs signed out as the API role with no person;
mounting it on the public router is a foundation request (slice F brief).
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import io
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import psycopg
import pytest
from fastapi import FastAPI
from PIL import Image, ImageDraw

import app.core.http as core_http
from app.core.contracts.case_names import PARTY_NAME_PURPOSE
from app.core.contracts.document_words import word_purpose
from app.core.db import Database
from app.core.jobs import JobContext
from app.core.storage import SupabaseStorage
from app.features.report.jobs import build_report
from app.features.report.services import ReportServices, configure, current
from app.features.report.spot_checks import MAX_FACTS, open_check, respond, sample
from tests.fixtures.report.world import CRYPTO, Reads, World, app_for, build, make_ready
from tests.support.auth import APP_ORIGIN
from tests.support.db import ClientFor
from tests.support.files import local_storage
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

COLLEAGUE = "zz-colleague@example.test"
WIDTH, HEIGHT = 1000, 1400
# word id -> (text, box in page pixels); the facts cite w1 and w2.
WORDS = {
    "w1": ("Ferritin", (100, 600, 200, 630)),
    "w2": ("9", (210, 600, 240, 630)),
    "w3": ("Qqname", (300, 600, 420, 630)),
    "w4": ("Header", (100, 50, 200, 80)),
}


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    yield store
    await store.aclose()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads, storage: SupabaseStorage) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads, storage)


@pytest.fixture(autouse=True)
def fresh_address_counts(admin: psycopg.Connection) -> None:
    """Every test client shares one address, so each test starts with no tries counted against it."""
    if admin.execute("select to_regclass('app.spot_check_address_tries')").fetchone() != (None,):
        admin.execute("delete from app.spot_check_address_tries")


@pytest.fixture(autouse=True)
def services(reads: Reads, storage: SupabaseStorage) -> Iterator[None]:
    configure(ReportServices(crypto=CRYPTO, storage=storage, reads=reads))
    yield
    configure(None)


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def public(api_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    """The API role signed out: no person, as the colleague's routes run."""
    async with await psycopg.AsyncConnection.connect(api_dsn, autocommit=True) as c:
        yield c


@pytest.fixture
async def worker_db(worker_dsn: str) -> AsyncIterator[Database]:
    database = Database(worker_dsn, max_size=2)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


async def paint(storage: SupabaseStorage, admin: psycopg.Connection, aconn: psycopg.AsyncConnection, w: World) -> None:
    """A picture of pages 1 and 2 with grey blocks where the words are, the words sealed as slice A seals
    them, the page size in the words' units, and a party whose surname is one of the words."""
    for page in w.pages[:2]:
        picture = Image.new("RGB", (WIDTH, HEIGHT), "white")
        draw = ImageDraw.Draw(picture)
        for _, (_, area) in WORDS.items():
            draw.rectangle(area, fill=(128, 128, 128))
        out = io.BytesIO()
        picture.save(out, format="JPEG", quality=95)
        path = f"cases/{w.case}/pages/{page}.jpg"
        await storage.put(path, await CRYPTO.encrypt(aconn, w.case, out.getvalue(), purpose=path))
        admin.execute("update app.pages set image_path = %s where id = %s", (path, page))
        admin.execute(
            "insert into app.page_analysis (page_id, case_id, width, height) values (%s, %s, %s, %s)",
            (page, w.case, WIDTH, HEIGHT),
        )
        for n, (wid, (text, (x0, y0, x1, y1))) in enumerate(WORDS.items(), start=1):
            sealed = await CRYPTO.encrypt(aconn, w.case, text.encode(), purpose=word_purpose(page, wid))
            await aconn.execute(
                "insert into app.ocr_words (page_id, case_id, n, word_id, text, polygon, confidence, source)"
                " values (%s, %s, %s, %s, %s, %s, 0.99, 'ocr')",
                (page, w.case, n, wid, sealed, [x0, y0, x1, y0, x1, y1, x0, y1]),
            )
    name = await CRYPTO.encrypt(aconn, w.case, b"Zz Qqname", purpose=PARTY_NAME_PURPOSE)
    await aconn.execute(
        "insert into app.case_parties (case_id, role_key, name) values (%s, 'zz_party', %s)", (w.case, name)
    )


@pytest.fixture
async def built(
    seed: Seed,
    admin: psycopg.Connection,
    aconn: psycopg.AsyncConnection,
    world: World,
    reads: Reads,
    storage: SupabaseStorage,
    client_for: ClientFor,
    worker_db: Database,
) -> World:
    """A built draft citing the facts on pages 1 and 2, with those pages pictured."""
    await make_ready(seed, admin, aconn, world, reads)
    await paint(storage, admin, aconn, world)
    queued = await (await client_for(world.expert)).post(world.url("report/build"))
    assert queued.status_code == 202, queued.text
    await build_report(JobContext(worker_db, uuid.UUID(queued.json()["job_id"]), world.case, "build_report", 1))
    return world


async def started(client_for: ClientFor, w: World) -> tuple[str, str]:
    resp = await (await client_for(w.expert)).post(w.url("spot-checks"), json={"reviewer_email": COLLEAGUE})
    assert resp.status_code == 201, resp.text
    code = current().mailer.outbox[-1].params["code"]
    return str(resp.json()["token"]), code


def png(data: bytes) -> Image.Image:
    return Image.open(io.BytesIO(data)).convert("RGB")


async def test_sampling_picks_at_most_ten_distinct_facts() -> None:
    facts = [uuid.uuid4() for _ in range(30)]
    picked = sample(facts + facts)
    assert len(picked) == MAX_FACTS == 10 and len(set(picked)) == 10 and set(picked) <= set(facts)
    assert sorted(sample(facts[:3])) == sorted(facts[:3])


async def test_a_spot_check_needs_a_built_report_and_report_build(client_for: ClientFor, world: World) -> None:
    body = {"reviewer_email": COLLEAGUE}
    resp = await (await client_for(world.expert)).post(world.url("spot-checks"), json=body)
    assert resp.status_code == 409 and resp.json()["error"]["code"] == "nothing_cited"
    assert (await (await client_for(world.viewer)).post(world.url("spot-checks"), json=body)).status_code == 403
    assert (await (await client_for(world.outsider)).post(world.url("spot-checks"), json=body)).status_code == 404
    bad = await (await client_for(world.expert)).post(world.url("spot-checks"), json={"reviewer_email": "nope"})
    assert bad.status_code == 422


async def test_starting_a_spot_check_keeps_only_hashes_and_emails_the_code(
    client_for: ClientFor, admin: psycopg.Connection, built: World
) -> None:
    resp = await (await client_for(built.expert)).post(built.url("spot-checks"), json={"reviewer_email": COLLEAGUE})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    token = str(body["token"])
    assert len(token) >= 22 and "code" not in body
    row = admin.execute(
        "select token_hash, code_hash, fact_ids, expires_at, reviewer_email from app.spot_checks where id = %s",
        (body["id"],),
    ).fetchone()
    assert row is not None
    assert row[0] == hashlib.sha256(token.encode()).hexdigest()
    assert set(row[2]) == {built.plan, built.result}, "only cited facts are sampled"
    assert abs(row[3] - (datetime.now(UTC) + timedelta(days=7))) < timedelta(minutes=2)
    mail = current().mailer.outbox[-1]
    assert mail.to == COLLEAGUE and mail.template == "spot_check.code"
    assert list(mail.params) == ["code"] and mail.params["code"].isdigit() and len(mail.params["code"]) == 6
    assert row[1] != hashlib.sha256(mail.params["code"].encode()).hexdigest(), "the code is not stored as is"
    audit = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'spot_check.created'", (built.case,)
    ).fetchone()
    assert audit == (1,)


async def test_the_colleague_sees_watermarked_crops_with_names_blacked_out(
    client_for: ClientFor, admin: psycopg.Connection, public: psycopg.AsyncConnection, built: World
) -> None:
    token, code = await started(client_for, built)
    storage = current().files()
    opened = await open_check(public, storage, token, code)
    assert opened.outcome == "ok"
    assert opened.items is not None and len(opened.items) == 2
    values = {item.value for item in opened.items}
    assert values == {"review 3/52 if no better", "Ferritin 9 ug/L"}
    for item in opened.items:
        assert item.crop is not None
        crop = png(item.crop)
        assert crop.height < HEIGHT * 0.2 and crop.width < WIDTH, "a crop, never the full page"
        top = 600 - 70
        masked = crop.getpixel((360, 615 - top))
        cited = crop.getpixel((150, 615 - top))
        assert isinstance(masked, tuple) and isinstance(cited, tuple)
        assert sum(masked) < 150, "the party's name is blacked out"
        assert sum(cited) > 250, "the cited words show"
        pixels: Any = crop.getdata()
        assert any(p[0] > p[1] + 30 for p in pixels), "the crop is watermarked"
    logged = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'spot_check.opened'", (built.case,)
    ).fetchone()
    assert logged == (1,)


async def test_wrong_codes_lock_the_link_and_old_or_unknown_links_refuse(
    client_for: ClientFor, admin: psycopg.Connection, public: psycopg.AsyncConnection, built: World
) -> None:
    token, code = await started(client_for, built)
    storage = current().files()
    wrong = "000000" if code != "000000" else "111111"
    first = await open_check(public, storage, token, wrong)
    assert first.outcome == "bad_code" and first.items is None
    for _ in range(4):
        await open_check(public, storage, token, wrong)
    assert (await open_check(public, storage, token, code)).outcome == "locked"
    assert (await open_check(public, storage, "zz-no-such-token-zz", code)).outcome == "unknown"

    token, code = await started(client_for, built)
    admin.execute(
        "update app.spot_checks set expires_at = now() - interval '1 minute' where token_hash = %s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )
    assert (await open_check(public, storage, token, code)).outcome == "expired"
    refused = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'spot_check.refused'", (built.case,)
    ).fetchone()
    assert refused is not None and refused[0] >= 6


async def test_answers_are_kept_once_and_the_expert_sees_them(
    client_for: ClientFor, public: psycopg.AsyncConnection, built: World
) -> None:
    token, code = await started(client_for, built)
    answers = [
        {"fact_id": str(built.plan), "agrees": True},
        {"fact_id": str(built.result), "agrees": False, "reason": "value_differs"},
    ]
    assert await respond(public, token, "999999" if code != "999999" else "888888", answers) == "bad_code"
    assert await respond(public, token, code, [{"fact_id": str(uuid.uuid4()), "agrees": True}]) == "invalid"
    assert await respond(public, token, code, answers) == "ok"
    assert await respond(public, token, code, answers) == "already"

    listed = await (await client_for(built.expert)).get(built.url("spot-checks"))
    assert listed.status_code == 200, listed.text
    check = listed.json()["spot_checks"][0]
    assert check["reviewer_email"] == COLLEAGUE and check["responded_at"] is not None
    assert check["total"] == 2 and check["agree_count"] == 1
    assert {(a["fact_id"], a["agrees"]) for a in check["responses"]} == {
        (str(built.plan), True),
        (str(built.result), False),
    }
    assert (await (await client_for(built.viewer)).get(built.url("spot-checks"))).status_code == 403


async def test_a_spot_check_that_does_not_commit_mails_nobody(
    client_for: ClientFor, admin: psycopg.Connection, built: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def fail(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("the transaction does not commit")

    monkeypatch.setattr(core_http, "_remember", fail)
    client = await client_for(built.expert)
    with contextlib.suppress(RuntimeError):
        resp = await client.post(
            built.url("spot-checks"), json={"reviewer_email": COLLEAGUE}, headers={"Idempotency-Key": "zz-spot-once"}
        )
        assert resp.status_code >= 500
    assert current().mailer.outbox == []
    assert admin.execute("select count(*) from app.spot_checks where case_id = %s", (built.case,)).fetchone() == (0,)


# The colleague's public routes (foundation request): signed out, the token and code hashes are the gate --------

CODE_HEADER = "X-Spot-Check-Code"


def other(code: str) -> str:
    return "000000" if code != "000000" else "111111"


async def test_the_colleague_opens_the_link_signed_out_with_the_code(
    client_for: ClientFor, admin: psycopg.Connection, built: World
) -> None:
    token, code = await started(client_for, built)
    colleague = await client_for(None)
    resp = await colleague.get(f"/v1/spot-checks/{token}", headers={CODE_HEADER: code})
    assert resp.status_code == 200, resp.text
    assert "no-store" in resp.headers["cache-control"]
    body = resp.json()
    assert body["status"] == "ok" and body["responded"] is False and body["expires_at"]
    assert {item["value"] for item in body["items"]} == {"review 3/52 if no better", "Ferritin 9 ug/L"}
    for item in body["items"]:
        crop = png(base64.b64decode(item["crop"]))
        assert crop.height < HEIGHT * 0.2, "a crop, never the full page"
    assert (await colleague.get(f"/v1/spot-checks/{token}")).status_code == 422, "the code is required"
    logged = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'spot_check.opened'", (built.case,)
    ).fetchone()
    assert logged == (1,)


async def test_five_wrong_codes_through_http_lock_the_link(
    client_for: ClientFor, admin: psycopg.Connection, built: World
) -> None:
    """Each wrong try commits its count and answers a status; it never raises (which would roll it back)."""
    token, code = await started(client_for, built)
    colleague = await client_for(None)
    for _ in range(5):
        resp = await colleague.get(f"/v1/spot-checks/{token}", headers={CODE_HEADER: other(code)})
        assert resp.status_code == 200 and resp.json() == {
            "status": "bad_code",
            "expires_at": None,
            "responded": False,
            "items": [],
        }
    locked = await colleague.get(f"/v1/spot-checks/{token}", headers={CODE_HEADER: code})
    assert locked.status_code == 200 and locked.json()["status"] == "locked" and locked.json()["items"] == []
    answers = [{"fact_id": str(built.plan), "agrees": True}]
    reply = await colleague.post(f"/v1/spot-checks/{token}/responses", json={"code": code, "answers": answers})
    assert reply.status_code == 200 and reply.json() == {"status": "locked"}
    attempts = admin.execute(
        "select code_attempts from app.spot_checks where token_hash = %s", (hashlib.sha256(token.encode()).hexdigest(),)
    ).fetchone()
    assert attempts is not None and attempts[0] >= 5


async def test_unknown_and_expired_links_answer_a_status(
    client_for: ClientFor, admin: psycopg.Connection, built: World
) -> None:
    colleague = await client_for(None)
    unknown = await colleague.get("/v1/spot-checks/zz-no-such-token-zz", headers={CODE_HEADER: "123456"})
    assert unknown.status_code == 200 and unknown.json()["status"] == "unknown"
    token, code = await started(client_for, built)
    admin.execute(
        "update app.spot_checks set expires_at = now() - interval '1 minute' where token_hash = %s",
        (hashlib.sha256(token.encode()).hexdigest(),),
    )
    expired = await colleague.get(f"/v1/spot-checks/{token}", headers={CODE_HEADER: code})
    assert expired.status_code == 200 and expired.json()["status"] == "expired"


async def test_the_colleague_answers_once_through_http(client_for: ClientFor, built: World) -> None:
    token, code = await started(client_for, built)
    colleague = await client_for(None)
    answers = [
        {"fact_id": str(built.plan), "agrees": True},
        {"fact_id": str(built.result), "agrees": False, "reason": "value_differs"},
    ]
    url = f"/v1/spot-checks/{token}/responses"
    bad = await colleague.post(url, json={"code": other(code), "answers": answers})
    assert bad.status_code == 200 and bad.json() == {"status": "bad_code"}
    stray = [{"fact_id": str(uuid.uuid4()), "agrees": True}]
    assert (await colleague.post(url, json={"code": code, "answers": stray})).json() == {"status": "invalid"}
    assert (await colleague.post(url, json={"code": code, "answers": answers})).json() == {"status": "ok"}
    assert (await colleague.post(url, json={"code": code, "answers": answers})).json() == {"status": "already"}
    assert (await colleague.post(url, json={"code": "12", "answers": answers})).status_code == 422
    opened = await colleague.get(f"/v1/spot-checks/{token}", headers={CODE_HEADER: code})
    assert opened.json()["responded"] is True
    listed = await (await client_for(built.expert)).get(built.url("spot-checks"))
    assert listed.json()["spot_checks"][0]["responded_at"] is not None


async def test_an_answer_must_say_true_or_false_not_a_number(client_for: ClientFor, built: World) -> None:
    token, code = await started(client_for, built)
    colleague = await client_for(None)
    answers = [{"fact_id": str(built.plan), "agrees": 0}]
    resp = await colleague.post(f"/v1/spot-checks/{token}/responses", json={"code": code, "answers": answers})
    assert resp.status_code == 422


ADDRESS_LIMIT = 20  # tries from one address in 15 minutes, across every link


async def from_address(app: FastAPI, address: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, client=(address, 1)),
        base_url="https://test",
        headers={"origin": APP_ORIGIN},
    )


async def test_one_address_is_refused_after_too_many_tries(app: FastAPI, admin: psycopg.Connection) -> None:
    async with await from_address(app, "198.51.100.7") as guesser, await from_address(app, "198.51.100.8") as other:
        for n in range(ADDRESS_LIMIT):
            resp = await guesser.get(f"/v1/spot-checks/zz-guess-{n}", headers={CODE_HEADER: "123456"})
            assert resp.status_code == 200 and resp.json()["status"] == "unknown"
        refused = await guesser.get("/v1/spot-checks/zz-guess-last", headers={CODE_HEADER: "123456"})
        assert refused.status_code == 429 and refused.json()["error"]["code"] == "too_many_attempts"
        answers = [{"fact_id": str(uuid.uuid4()), "agrees": True}]
        posted = await guesser.post(
            "/v1/spot-checks/zz-guess-last/responses", json={"code": "123456", "answers": answers}
        )
        assert posted.status_code == 429
        still = await other.get("/v1/spot-checks/zz-guess-other", headers={CODE_HEADER: "123456"})
        assert still.status_code == 200
    kept = [row[0] for row in admin.execute("select address_hash from app.spot_check_address_tries").fetchall()]
    assert kept and not any("198.51.100" in h for h in kept)
