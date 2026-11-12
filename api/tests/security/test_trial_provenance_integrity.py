"""Security audit 16 (document provenance and output integrity), slice I's part (PV-16a to PV-16d, PV-18).

Each test asserts the safe outcome and names its finding:

- PV-16a: a trial pack whose stored object was replaced (validly sealed, different bytes) is refused at download
  (409 `hash_mismatch`); a good download carries `X-Content-SHA256`.
- PV-16b: the same for an offline package (the stored nonce and ciphertext against `offline_packages.sha256`, checked
  before the key is released), and the package says which court bundle (id, hash), report version (number, hash) and
  pack versions it was built from.
- PV-16c: two pages of one court bundle never share a trial page number (the matcher gives each trial page to one
  page only): a PATCH onto a number another page holds is 409 `trial_page_taken`, and the database refuses it too.
- PV-16d: every change of a trial page number keeps what was there before (number, method, score, who, when) in an
  append-only history and writes an audit row (target: the row's id only).
- PV-18: the OCR calls of `match_trial_bundle` and `read_agenda` are recorded (`ai_calls`: API version, region, job),
  and a trial pack records the job that built it.

- PV-16e: the concordance CSV export names its court bundle and its own hash in response headers; the CSV body is
  unchanged (the existing test of the CSV fixes its columns).
"""

# ruff: noqa: F811 - pytest fixtures imported from tests.fixtures.trial are used as arguments
from __future__ import annotations

import dataclasses
import hashlib
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from psycopg.errors import UniqueViolation

from app.core.ai.ocr import OcrPage, OcrResult, OcrWord
from app.core.config import ConfigError
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import CaseFileStorage
from app.features.trial import jobs
from app.features.trial.matching import Match, match_pages
from app.features.trial.ports import RecordedChanges
from app.features.trial.services import TrialServices, configure, current
from tests.fixtures.trial.fixtures import (  # noqa: F401
    Scripted,
    aconn,
    app,
    changes,
    combo,
    model,
    reads,
    run_job,
    storage,
    upload_bundle,
    worker_db,
    world,
)
from tests.fixtures.trial.world import CRYPTO, RESCANNED, Reads, World, page_words, pdf_of, trial_bundle
from tests.http.test_trial_offline import code, create, opened
from tests.http.test_trial_pack import built
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def swap(storage: CaseFileStorage, aconn: psycopg.AsyncConnection, w: World, path: str) -> None:
    """The stored object replaced by different bytes, validly sealed under the case key (as an attacker with
    storage access and a stolen key service session could)."""
    await storage.put(path, await CRYPTO.encrypt(aconn, w.case, b"%PDF-1.4 not the pack", purpose=path))


# --- PV-16a ------------------------------------------------------------------------------------------------


async def test_pv16a_a_trial_pack_whose_stored_file_was_replaced_is_refused(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    storage: CaseFileStorage,
    aconn: psycopg.AsyncConnection,
    admin: psycopg.Connection,
) -> None:
    await built(client_for, worker_db, world)
    stored = admin.execute("select storage_path from app.trial_packs where case_id = %s", (world.case,)).fetchone()
    assert stored is not None
    await swap(storage, aconn, world, stored[0])
    resp = await (await client_for(world.expert)).get(world.url("trial-pack/latest/download"))
    assert resp.status_code == 409 and code(resp) == "hash_mismatch"
    assert not resp.content.startswith(b"%PDF")


async def test_pv16a_a_good_pack_download_carries_the_hash_people_check_outside_avodeck(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    await built(client_for, worker_db, world)
    resp = await (await client_for(world.expert)).get(world.url("trial-pack/latest/download"))
    assert resp.status_code == 200 and resp.content.startswith(b"%PDF")
    row = admin.execute("select sha256 from app.trial_packs where case_id = %s", (world.case,)).fetchone()
    assert row is not None and resp.headers["x-content-sha256"] == row[0]


# --- PV-16b ------------------------------------------------------------------------------------------------


async def test_pv16b_an_offline_package_whose_stored_bytes_were_replaced_is_refused_and_keeps_its_key(
    client_for: ClientFor,
    world: World,
    storage: CaseFileStorage,
    admin: psycopg.Connection,
) -> None:
    made = await create(client_for, world)
    path = admin.execute("select storage_path from app.offline_packages where id = %s", (made["id"],)).fetchone()
    assert path is not None
    blob = await storage.get(path[0])
    await storage.put(path[0], blob[:-1] + bytes([blob[-1] ^ 1]))
    resp = await (await client_for(world.expert)).get(world.url(f"offline-packages/{made['id']}/download"))
    assert resp.status_code == 409 and code(resp) == "hash_mismatch"
    assert "key" not in resp.json() and "ciphertext" not in resp.json()
    row = admin.execute("select status, key_sealed is not null from app.offline_packages where id = %s", (made["id"],))
    assert row.fetchone() == ("built", True), "a refused download does not use up the copy"


async def test_pv16b_the_package_says_which_inputs_it_was_built_from(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    pdf, _ = trial_bundle()
    bundle = await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    made = await create(client_for, world)
    resp = await (await client_for(world.expert)).get(world.url(f"offline-packages/{made['id']}/download"))
    assert resp.status_code == 200, resp.text
    content = opened(resp.json())
    assert content["format_version"] == 1, "additive: the copy on a device still opens"
    sources = content["sources"]
    stored = admin.execute("select sha256, version from app.court_bundles where id = %s", (bundle["court_bundle_id"],))
    sha, version = stored.fetchone() or (None, None)
    assert sources["court_bundle"] == {"id": bundle["court_bundle_id"], "sha256": sha, "version": version}
    report = admin.execute(
        "select id, version_no, sha256 from app.report_versions where case_id = %s and status = 'served'", (world.case,)
    ).fetchone()
    assert report is not None
    assert sources["report"] == {"version_id": str(report[0]), "version_no": report[1], "sha256": report[2]}
    assert sources["pack_versions"] == default_library().combine(*world.combo.codes).versions
    assert sources["built_at"] == content["built_at"]


# --- PV-16c / PV-16d ---------------------------------------------------------------------------------------


async def bundle_rows(
    client_for: ClientFor, world: World, worker_db: Database
) -> tuple[dict[int, dict[str, Any]], dict[int, int]]:
    pdf, where = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    body = (await (await client_for(world.expert)).get(world.url("concordance"))).json()
    return {r["page_no"]: r for r in body["rows"]}, where


def patch_to(client: Any, world: World, row: dict[str, Any], number: int | None) -> Any:
    return client.patch(
        world.url(f"concordance/{row['id']}"),
        json={"trial_page_no": number},
        headers={"If-Match": f'"{row["version"]}"'},
    )


async def test_pv16c_two_pages_cannot_be_given_the_same_trial_page(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    rows, where = await bundle_rows(client_for, world, worker_db)
    client = await client_for(world.expert)
    taken = where[118]
    resp = await patch_to(client, world, rows[RESCANNED[0]], taken)
    assert resp.status_code == 409 and code(resp) == "trial_page_taken"
    held = admin.execute(
        "select count(*) from app.concordance where trial_page_no = %s and court_bundle_id ="
        " (select id from app.court_bundles where case_id = %s)",
        (taken, world.case),
    ).fetchone()
    assert held == (1,), "the row is unchanged"
    # the database holds the same line whatever the route does
    with pytest.raises(UniqueViolation):
        admin.execute("update app.concordance set trial_page_no = %s where id = %s", (taken, rows[RESCANNED[0]]["id"]))


async def test_pv16c_control_a_page_may_keep_its_own_number_and_many_may_have_none(
    client_for: ClientFor, world: World, worker_db: Database
) -> None:
    rows, where = await bundle_rows(client_for, world, worker_db)
    client = await client_for(world.expert)
    own = await patch_to(client, world, rows[RESCANNED[0]], where[RESCANNED[0]])
    assert own.status_code == 200, own.text
    for n in (RESCANNED[1], RESCANNED[2]):
        assert (await patch_to(client, world, rows[n], None)).status_code == 200


async def test_pv16d_a_changed_trial_page_keeps_the_earlier_match_and_is_audited(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    rows, where = await bundle_rows(client_for, world, worker_db)
    row = rows[RESCANNED[0]]
    assert row["method"] in ("text", "sequence") and not row["confirmed"]
    client = await client_for(world.expert)
    first = await patch_to(client, world, row, None)
    assert first.status_code == 200, first.text
    second = await patch_to(client, world, {**row, "version": first.json()["version"]}, where[RESCANNED[0]])
    assert second.status_code == 200, second.text
    history = admin.execute(
        "select page_no, previous_trial_page_no, previous_method, previous_score, previous_confirmed,"
        " new_trial_page_no, changed_by is not null, created_at is not null from app.concordance_history"
        " where concordance_id = %s order by created_at, id",
        (row["id"],),
    ).fetchall()
    assert [h[0] for h in history] == [RESCANNED[0]] * 2
    machine, expert = history
    assert machine[1:6] == (where[RESCANNED[0]], row["method"], pytest.approx(row["score"], abs=1e-3), False, None)
    assert expert[1:6] == (None, "expert", pytest.approx(row["score"], abs=1e-3), True, where[RESCANNED[0]])
    assert machine[6] and machine[7]
    audited = admin.execute(
        "select target, person_id is not null from app.audit_log"
        " where case_id = %s and action = 'trial.concordance_changed' order by seq",
        (world.case,),
    ).fetchall()
    assert audited == [(row["id"], True)] * 2


async def test_pv16d_the_history_cannot_be_changed_or_removed(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection, worker_dsn: str, api_dsn: str
) -> None:
    rows, _ = await bundle_rows(client_for, world, worker_db)
    assert (await patch_to(await client_for(world.expert), world, rows[RESCANNED[0]], None)).status_code == 200
    for dsn in (api_dsn, worker_dsn):
        with psycopg.connect(dsn, autocommit=True) as c:
            for sql in ("update app.concordance_history set page_no = 1", "delete from app.concordance_history"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql)


# --- PV-18 -------------------------------------------------------------------------------------------------


class ScannedOcr:
    """An OCR service stand-in answering with made-up words and an API version."""

    endpoint = "https://ocr.invalid"

    async def analyse(self, document: bytes, model: str = "layout", *, locale: str | None = None) -> OcrResult:
        words = tuple(OcrWord(f"p1w{n}", t, (0.0,) * 8, 0.9) for n, t in enumerate(page_words(105)[:5], start=1))
        page = OcrPage(1, 8.27, 11.69, words)
        return OcrResult(model="read-test", pages=(page, page, page), api_version="2099-01-01")


def with_ocr(storage: CaseFileStorage, reads: Reads) -> None:
    configure(
        TrialServices(
            crypto=CRYPTO, storage=storage, reads=reads, changes=RecordedChanges(), ocr=ScannedOcr(), region="uk"
        )
    )


async def test_pv18_reading_a_court_bundle_with_ocr_records_the_call_and_its_job(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    storage: CaseFileStorage,
    reads: Reads,
    admin: psycopg.Connection,
) -> None:
    with_ocr(storage, reads)
    scanned = pdf_of([page_words(101), [], []])  # two pages with no text layer
    sent = await upload_bundle(await client_for(world.expert), worker_db, world, scanned)
    calls = admin.execute(
        "select task_id, api_version, region, job_id, input_tokens + output_tokens from app.ai_calls"
        " where case_id = %s and task_id like 'ocr.%%'",
        (world.case,),
    ).fetchall()
    assert calls == [("ocr.trial_bundle", "2099-01-01", "uk", uuid.UUID(sent["job_id"]), 0)]


async def test_pv18_reading_an_agenda_with_ocr_records_the_call_and_its_job(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    model: Callable[..., Scripted],
    admin: psycopg.Connection,
) -> None:
    from app.core.jobs import JobContext
    from app.features.trial.jobs import read_agenda
    from tests.http.test_trial_meetings import create as create_meeting

    model()
    services = current()
    services.ocr, services.region = ScannedOcr(), "uk"
    meeting = await create_meeting(client_for, world)
    resp = await (await client_for(world.expert)).post(
        world.url(f"expert-meetings/{meeting['id']}/agenda"),
        content=pdf_of([[]]),
        headers={"content-type": "application/pdf"},
    )
    assert resp.status_code == 202, resp.text
    await read_agenda(JobContext(worker_db, uuid.UUID(resp.json()["job_id"]), world.case, "read_agenda", 1))
    calls = admin.execute(
        "select task_id, api_version, job_id from app.ai_calls where case_id = %s and task_id like 'ocr.%%'",
        (world.case,),
    ).fetchall()
    assert calls == [("ocr.agenda", "2099-01-01", uuid.UUID(resp.json()["job_id"]))]


async def test_pv18_control_a_trial_pack_records_the_job_that_built_it(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    resp = await (await client_for(world.expert)).post(world.url("trial-pack"))
    assert resp.status_code == 202, resp.text
    await run_job(worker_db, world, "build_trial_pack", resp.json()["job_id"])
    row = admin.execute("select job_id from app.trial_packs where case_id = %s", (world.case,)).fetchone()
    assert row == (uuid.UUID(resp.json()["job_id"]),)


# --- PV-16e ------------------------------------------------------------------------------------------------


async def test_pv16e_the_csv_export_names_the_court_bundle_it_maps_and_its_own_hash(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection
) -> None:
    pdf, _ = trial_bundle()
    bundle = await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    resp = await (await client_for(world.expert)).get(world.url("concordance/export"), params={"format": "csv"})
    assert resp.status_code == 200
    stored = admin.execute("select sha256 from app.court_bundles where id = %s", (bundle["court_bundle_id"],))
    row = stored.fetchone()
    assert row is not None
    assert resp.headers["x-court-bundle-id"] == bundle["court_bundle_id"]
    assert resp.headers["x-court-bundle-sha256"] == row[0]
    assert resp.headers["x-content-sha256"] == hashlib.sha256(resp.content).hexdigest()
    assert resp.text.splitlines()[0] == "page_no,trial_page_no,status,score", "the CSV body is unchanged"


# --- review follow-ups -------------------------------------------------------------------------------------


async def test_pv16d_the_history_refuses_a_change_through_any_login_and_still_goes_with_its_case(
    client_for: ClientFor, world: World, worker_db: Database, admin: psycopg.Connection, worker_dsn: str, api_dsn: str
) -> None:
    rows, _ = await bundle_rows(client_for, world, worker_db)
    assert (await patch_to(await client_for(world.expert), world, rows[RESCANNED[0]], None)).status_code == 200
    mine = "court_bundle_id in (select id from app.court_bundles where case_id = '%s')"
    statements = [
        f"update app.concordance_history set page_no = page_no + 100 where {mine}",
        f"delete from app.concordance_history where {mine}",
    ]
    # the owner login is refused too (a trigger, not only grants)
    for sql in statements:
        with admin.transaction(force_rollback=True), pytest.raises(psycopg.errors.Error) as refused:
            admin.execute(sql % world.case)
        assert refused.value.sqlstate == "AV409", sql

    def through_a_definer_function(dsn: str, sql: str) -> int | None:
        """The statement run in a security-definer function called on a real login of the given app role."""
        admin.execute(
            "create function app.zz_probe() returns bigint language plpgsql security definer"
            f" set search_path = '' as $$ declare n bigint; begin {sql % world.case};"
            " get diagnostics n = row_count; return n; end $$"
        )
        try:
            with psycopg.connect(dsn, autocommit=False) as conn:
                row = conn.execute("select app.zz_probe()").fetchone()
                conn.rollback()
                return int(row[0]) if row else None
        finally:
            admin.execute("drop function app.zz_probe()")

    for dsn in (api_dsn, worker_dsn):
        for sql in statements:
            with pytest.raises(psycopg.errors.Error) as refused:
                through_a_definer_function(dsn, sql)
            assert refused.value.sqlstate == "AV409", sql
    # the history goes when its case's rows go (a purge), and only then
    with admin.transaction(force_rollback=True):
        admin.execute("delete from app.concordance where case_id = %s", (world.case,))
        left = admin.execute(
            "select count(*) from app.concordance_history where court_bundle_id in"
            " (select id from app.court_bundles where case_id = %s)",
            (world.case,),
        ).fetchone()
        assert left == (0,)


async def test_pv16c_two_pages_given_one_trial_page_both_keep_a_row_and_the_weaker_is_missing(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    admin: psycopg.Connection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts: list[int] = []
    picked: list[tuple[Any, Any, int | None]] = []

    def clashing(ours: Any, theirs: Any) -> list[Match]:
        out = list(match_pages(ours, theirs))
        counts.append(len(out))
        numbered = [i for i, m in enumerate(out) if m.trial_page_no is not None]
        weak, strong = numbered[0], numbered[1]
        out[weak] = dataclasses.replace(out[weak], trial_page_no=out[strong].trial_page_no, score=0.7, confirmed=False)
        out[strong] = dataclasses.replace(out[strong], score=0.99, confirmed=True)
        picked.append((out[weak].key, out[strong].key, out[strong].trial_page_no))
        return out

    monkeypatch.setattr(jobs, "match_pages", clashing)
    pdf, _ = trial_bundle()
    await upload_bundle(await client_for(world.expert), worker_db, world, pdf)
    (count,), ((weak, strong, number),) = counts, picked
    rows = admin.execute(
        "select page_id, trial_page_no, method, confirmed from app.concordance where case_id = %s", (world.case,)
    ).fetchall()
    assert len(rows) == count, "no page lost its row"
    by_page = {r[0]: r[1:] for r in rows}
    assert by_page[strong][0] == number
    assert by_page[weak] == (None, "none", False), "the weaker page is left for the expert (missing)"
    assert sum(1 for r in rows if r[1] == number) == 1


async def test_pv18_no_region_in_the_cell_config_is_an_error_not_a_placeholder(
    client_for: ClientFor,
    world: World,
    worker_db: Database,
    storage: CaseFileStorage,
    reads: Reads,
    admin: psycopg.Connection,
) -> None:
    configure(
        TrialServices(
            crypto=CRYPTO, storage=storage, reads=reads, changes=RecordedChanges(), ocr=ScannedOcr(), region=None
        )
    )
    scanned = pdf_of([page_words(101), [], []])
    with pytest.raises(ConfigError):
        await upload_bundle(await client_for(world.expert), worker_db, world, scanned)
    calls = admin.execute(
        "select count(*) from app.ai_calls where case_id = %s and task_id like 'ocr.%%'", (world.case,)
    )
    assert calls.fetchone() == (0,), "nothing was recorded under a made-up region"
