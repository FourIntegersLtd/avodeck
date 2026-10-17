"""Close the case (screen 26, architecture 5.2 "26 Close the case" and 5.3 "Close (slice H)").

- `GET /close`: what screen 26 shows (status, retention, the pack's retention years, holds, deletion).
- `POST /settle`, `POST /archive`, `POST /reopen`: the named expert only. Settling starts the
  retention clock (`keep_default` unless another policy was chosen); archiving recalculates
  `delete_after`; reopening stops the clock (`delete_after` cleared).
- `PUT /retention` 🔒: `keep_default` = settlement + the pack's retention years; `return_then_delete`
  = the settlement date; `delete_now` = today, and queues `delete_case`, only for a case that was
  never served and is not held (`case.delete_now`: the named expert or an organisation owner).
  Withdrawn cases follow the same retention as settled ones, from the day they are closed.
- `POST /legal-holds` 🔒 and `POST /legal-holds/{hold_id}/release` 🔒: the named expert or an owner.
- `GET /audit`: the case's audit trail for the named expert and organisation admins.
- `GET /deletion-certificate`: 404 until the case is deleted; then counts, hashes and dates only.
- `POST /export` 🔒: 202 and an `export_case` job for the named expert or an organisation admin.
- Export, retention, deletion and holds refuse without step-up (403 `step_up_required`).
"""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Any

import psycopg
import pytest

from app.core.packs import default_library
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

LAPSED = timedelta(minutes=11)


@pytest.fixture
def team(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "assistant", "viewer", "admin", "owner", "outsider")}
    p["org"] = seed.org(
        kind="team",
        members={
            p["expert"]: "expert",
            p["assistant"]: "assistant",
            p["viewer"]: "viewer",
            p["admin"]: "admin",
            p["owner"]: "owner",
        },
    )
    seed.org(kind="personal", members={p["outsider"]: "owner"})
    return p


def new_case(seed: Seed, team: dict[str, uuid.UUID], status: str, combo: Combo | None = None) -> uuid.UUID:
    packs = None
    if combo is not None:
        role = combo.raw("case-type", "pack.yaml")["expert_roles"][0]
        packs = {"jurisdiction": combo.jurisdiction, "case_type": combo.case_type, "specialty": combo.specialty,
                 "expert_role": role}  # fmt: skip
    return seed.case(
        team["org"], team["expert"], status=status, members={team["assistant"]: "assistant", team["viewer"]: "viewer"},
        packs=packs,
    )  # fmt: skip


def years(combo: Combo | None = None) -> int:
    codes = combo.codes if combo is not None else ("zz-test", "zz-case", "zz-specialty")
    return default_library().combine(*codes).retention_years


def plus_years(day: date, n: int) -> date:
    try:
        return day.replace(year=day.year + n)
    except ValueError:  # 29 February
        return day.replace(year=day.year + n, day=28)


def case_row(admin: psycopg.Connection, case: uuid.UUID) -> dict[str, Any]:
    cur = admin.execute(
        "select status, retention_policy, delete_after, settled_at from app.cases where id = %s", (case,)
    )
    row = cur.fetchone()
    assert row is not None
    return dict(zip(("status", "retention_policy", "delete_after", "settled_at"), row, strict=True))


def jobs_of(admin: psycopg.Connection, case: uuid.UUID, kind: str) -> list[uuid.UUID]:
    return [r[0] for r in admin.execute("select id from app.jobs where case_id = %s and kind = %s", (case, kind))]


def audit_actions(admin: psycopg.Connection, case: uuid.UUID) -> list[str]:
    return [r[0] for r in admin.execute("select action from app.audit_log where case_id = %s order by seq", (case,))]


TOMBSTONE = pytest.mark.xfail(
    strict=True,
    reason="foundation: app.case_access() hides a deleted case from everyone, so the certificate and the"
    " close summary cannot be read after deletion (architecture 5.2 says they can); needs case.view_metadata"
    " and case.view_certificate on the tombstone",
)


def add_hold(admin: psycopg.Connection, case: uuid.UUID, person: uuid.UUID) -> uuid.UUID:
    row = admin.execute(
        "insert into app.legal_holds (case_id, reason, set_by) values (%s, 'appeal', %s) returning id", (case, person)
    ).fetchone()
    assert row is not None
    return row[0]  # type: ignore[no-any-return]


def start_deletion(admin: psycopg.Connection, case: uuid.UUID) -> None:
    admin.execute("insert into app.deletion_certificates (case_id) values (%s)", (case,))


def code(resp: Any) -> str:
    return resp.json()["error"]["code"]  # type: ignore[no-any-return]


# --- step-up and access (acceptance 1, rules 9 and 10) ----------------------------------------------


def risky_calls(case: uuid.UUID, hold: uuid.UUID) -> list[tuple[str, str, dict[str, Any] | None]]:
    base = f"/v1/cases/{case}"
    return [
        ("POST", f"{base}/export", None),
        ("GET", f"{base}/export/{uuid.uuid4()}/download", None),
        ("PUT", f"{base}/retention", {"policy": "keep_default"}),
        ("PUT", f"{base}/retention", {"policy": "delete_now"}),
        ("POST", f"{base}/legal-holds", {"reason": "appeal"}),
        ("POST", f"{base}/legal-holds/{hold}/release", None),
    ]


async def test_export_retention_deletion_and_holds_refuse_without_step_up(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    hold = add_hold(admin, case, team["expert"])
    client = await client_for(team["expert"], mfa_age=LAPSED)
    for method, path, body in risky_calls(case, hold):
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 403, (method, path, resp.text)
        assert code(resp) == "step_up_required", (method, path)
    assert case_row(admin, case)["retention_policy"] is None
    assert jobs_of(admin, case, "export_case") == [] and jobs_of(admin, case, "delete_case") == []


async def test_someone_without_access_gets_404_everywhere(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "served")
    hold = add_hold(admin, case, team["expert"])
    base = f"/v1/cases/{case}"
    calls = [
        *risky_calls(case, hold),
        ("GET", f"{base}/close", None),
        ("POST", f"{base}/settle", None),
        ("POST", f"{base}/archive", None),
        ("POST", f"{base}/reopen", None),
        ("GET", f"{base}/audit", None),
        ("GET", f"{base}/deletion-certificate", None),
    ]
    outsider = await client_for(team["outsider"])
    for method, path, body in calls:
        resp = await outsider.request(method, path, json=body)
        assert resp.status_code == 404, (method, path, resp.text)


@pytest.mark.parametrize("person", ["assistant", "viewer"])
async def test_helpers_cannot_close_export_or_change_retention(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "served")
    hold = add_hold(admin, case, team["expert"])
    base = f"/v1/cases/{case}"
    client = await client_for(team[person])
    for method, path, body in [
        *risky_calls(case, hold),
        ("POST", f"{base}/settle", None),
        ("GET", f"{base}/audit", None),
        ("GET", f"{base}/deletion-certificate", None),
    ]:
        resp = await client.request(method, path, json=body)
        assert resp.status_code == 403, (method, path, resp.text)
        assert code(resp) == "forbidden"
    assert case_row(admin, case)["status"] == "served"


# --- what screen 26 shows -------------------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_the_close_summary_carries_the_packs_retention_years(
    combo: Combo, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "served", combo)
    hold = add_hold(admin, case, team["expert"])
    resp = await (await client_for(team["viewer"])).get(f"/v1/cases/{case}/close")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "served"
    assert body["retention_years"] == years(combo)
    assert body["retention_policy"] is None and body["delete_after"] is None and body["settled_at"] is None
    assert body["ever_served"] is True
    assert [h["id"] for h in body["holds"]] == [str(hold)]
    assert body["holds"][0]["reason"] == "appeal"
    assert body["deletion_started"] is False and body["deleted_at"] is None


# --- settle, archive, reopen (acceptance 8) ---------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_settling_starts_the_default_retention_from_the_packs_years(
    combo: Combo, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "served", combo)
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/settle")
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert row["status"] == "settled" and row["settled_at"] is not None
    assert row["retention_policy"] == "keep_default"
    assert row["delete_after"] == plus_years(row["settled_at"].date(), years(combo))
    assert resp.json()["delete_after"] == row["delete_after"].isoformat()
    assert "case.settled" in audit_actions(admin, case)


async def test_a_case_that_is_not_served_or_active_cannot_be_settled(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "draft")
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/settle")
    assert resp.status_code == 409
    assert code(resp) == "conflict"
    assert case_row(admin, case)["status"] == "draft"


@pytest.mark.parametrize("person", ["admin", "owner"])
async def test_organisation_admins_do_not_settle_archive_or_reopen(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    client = await client_for(team[person])
    for start, action in (("served", "settle"), ("settled", "archive"), ("settled", "reopen")):
        case = new_case(seed, team, start)
        resp = await client.post(f"/v1/cases/{case}/{action}")
        assert resp.status_code == 403, (action, resp.text)
        assert case_row(admin, case)["status"] == start


async def test_archiving_recalculates_delete_after_and_reopening_clears_it(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    admin.execute("update app.cases set delete_after = null, retention_policy = null where id = %s", (case,))
    client = await client_for(team["expert"])
    resp = await client.post(f"/v1/cases/{case}/archive")
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert row["status"] == "archived"
    assert row["retention_policy"] == "keep_default"
    assert row["delete_after"] == plus_years(row["settled_at"].date(), years())

    resp = await client.post(f"/v1/cases/{case}/reopen")
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert (row["status"], row["delete_after"], row["settled_at"]) == ("active", None, None)
    assert row["retention_policy"] == "keep_default", "the choice is kept for the next settlement"
    assert resp.json()["delete_after"] is None
    assert {"case.archived", "case.reopened"} <= set(audit_actions(admin, case))


async def test_a_withdrawn_case_follows_the_same_retention_from_the_day_it_is_archived(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    admin.execute("update app.cases set status = 'withdrawn' where id = %s", (case,))
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/archive")
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert row["status"] == "archived"
    assert row["delete_after"] == plus_years(date.today(), years())


async def test_a_case_being_deleted_cannot_be_reopened(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    start_deletion(admin, case)
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/reopen")
    assert resp.status_code == 409
    assert case_row(admin, case)["status"] == "settled"


async def test_an_active_case_cannot_be_reopened_or_archived(
    client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    client = await client_for(team["expert"])
    for action in ("reopen", "archive"):
        resp = await client.post(f"/v1/cases/{case}/{action}")
        assert resp.status_code == 409, action


# --- retention (acceptance 3) -------------------------------------------------------------------------


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_keep_default_is_the_packs_years_after_settlement(
    combo: Combo, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled", combo)
    admin.execute("update app.cases set retention_policy = 'return_then_delete' where id = %s", (case,))
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert row["retention_policy"] == "keep_default"
    assert row["delete_after"] == plus_years(row["settled_at"].date(), years(combo))
    assert "case.retention_changed" in audit_actions(admin, case)


async def test_return_then_delete_is_due_on_the_settlement_date(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team["expert"])).put(
        f"/v1/cases/{case}/retention", json={"policy": "return_then_delete"}
    )
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert (row["retention_policy"], row["delete_after"]) == ("return_then_delete", row["settled_at"].date())


async def test_a_policy_chosen_before_settlement_waits_for_it(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "served")
    client = await client_for(team["expert"])
    resp = await client.put(f"/v1/cases/{case}/retention", json={"policy": "return_then_delete"})
    assert resp.status_code == 200, resp.text
    assert case_row(admin, case)["delete_after"] is None
    await client.post(f"/v1/cases/{case}/settle")
    row = case_row(admin, case)
    assert (row["retention_policy"], row["delete_after"]) == ("return_then_delete", row["settled_at"].date())


async def test_an_organisation_admin_may_change_retention(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team["admin"])).put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 200, resp.text


@pytest.mark.parametrize("person", ["expert", "owner"])
async def test_delete_now_schedules_deletion_today_for_a_case_never_served(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    resp = await (await client_for(team[person])).put(f"/v1/cases/{case}/retention", json={"policy": "delete_now"})
    assert resp.status_code == 200, resp.text
    row = case_row(admin, case)
    assert (row["retention_policy"], row["delete_after"]) == ("delete_now", date.today())
    queued = jobs_of(admin, case, "delete_case")
    assert len(queued) == 1
    assert resp.json()["job_id"] == str(queued[0])


async def test_delete_now_is_refused_for_a_served_case(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    for status in ("served", "settled"):
        case = new_case(seed, team, status)
        before = case_row(admin, case)
        expert = await client_for(team["expert"])
        resp = await expert.put(f"/v1/cases/{case}/retention", json={"policy": "delete_now"})
        assert resp.status_code == 409, (status, resp.text)
        assert code(resp) == "conflict"
        assert case_row(admin, case) == before
        assert jobs_of(admin, case, "delete_case") == []


async def test_delete_now_needs_an_owner_not_an_admin(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    resp = await (await client_for(team["admin"])).put(f"/v1/cases/{case}/retention", json={"policy": "delete_now"})
    assert resp.status_code == 403
    assert jobs_of(admin, case, "delete_case") == []


async def test_delete_now_is_refused_while_a_hold_is_on(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "active")
    add_hold(admin, case, team["expert"])
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "delete_now"})
    assert resp.status_code == 409
    assert case_row(admin, case)["retention_policy"] is None


async def test_retention_cannot_change_once_deletion_has_started(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    start_deletion(admin, case)
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "keep_default"})
    assert resp.status_code == 409


async def test_an_unknown_policy_is_invalid(client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team["expert"])).put(f"/v1/cases/{case}/retention", json={"policy": "forever"})
    assert resp.status_code == 422


# --- legal holds (behaviour rule, S13) ----------------------------------------------------------------


@pytest.mark.parametrize("person", ["expert", "owner"])
async def test_the_named_expert_or_an_owner_pauses_and_resumes_deletion(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    client = await client_for(team[person])
    until = date.today() + timedelta(days=90)
    resp = await client.post(f"/v1/cases/{case}/legal-holds", json={"reason": "related_claim", "until": str(until)})
    assert resp.status_code == 201, resp.text
    hold = resp.json()
    assert (hold["reason"], hold["until"], hold["released_at"]) == ("related_claim", str(until), None)
    assert hold["set_by"] == str(team[person])

    resp = await client.post(f"/v1/cases/{case}/legal-holds/{hold['id']}/release")
    assert resp.status_code == 200, resp.text
    assert resp.json()["released_at"] is not None
    again = await client.post(f"/v1/cases/{case}/legal-holds/{hold['id']}/release")
    assert again.status_code == 409
    assert {"legal_hold.set", "legal_hold.released"} <= set(audit_actions(admin, case))


async def test_an_organisation_admin_cannot_set_a_hold(
    client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team["admin"])).post(f"/v1/cases/{case}/legal-holds", json={"reason": "appeal"})
    assert resp.status_code == 403


@pytest.mark.parametrize(
    "body", [{"reason": "Appeal by Mr Example"}, {"reason": "appeal", "until": "2001-01-01"}, {}]
)
async def test_a_hold_needs_a_short_reason_code_and_a_future_end(
    body: dict[str, str], client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/legal-holds", json=body)
    assert resp.status_code == 422


async def test_a_hold_of_another_case_is_not_found(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case, other = new_case(seed, team, "settled"), new_case(seed, team, "settled")
    hold = add_hold(admin, other, team["expert"])
    client = await client_for(team["expert"])
    for hold_id in (hold, uuid.uuid4()):
        resp = await client.post(f"/v1/cases/{case}/legal-holds/{hold_id}/release")
        assert resp.status_code == 404
    assert admin.execute("select released_at from app.legal_holds where id = %s", (hold,)).fetchone() == (None,)


async def test_no_hold_once_deletion_has_started(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    start_deletion(admin, case)
    resp = await (await client_for(team["expert"])).post(f"/v1/cases/{case}/legal-holds", json={"reason": "appeal"})
    assert resp.status_code == 409


# --- audit trail and certificate ---------------------------------------------------------------------


@pytest.mark.parametrize("person", ["expert", "admin"])
async def test_the_audit_trail_lists_everyones_actions_on_the_case(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    admin.execute(
        "insert into app.audit_log (case_id, person_id, action, target) values (%s, %s, 'zz.looked', 'case:x')",
        (case, team["assistant"]),
    )
    resp = await (await client_for(team[person])).get(f"/v1/cases/{case}/audit")
    assert resp.status_code == 200, resp.text
    entries = resp.json()["entries"]
    assert {"action": "zz.looked", "person_id": str(team["assistant"]), "target": "case:x"}.items() <= next(
        e for e in entries if e["action"] == "zz.looked"
    ).items()
    assert set(entries[0]) == {"seq", "at", "action", "target", "person_id"}


async def test_the_certificate_is_not_there_until_the_case_is_deleted(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    client = await client_for(team["expert"])
    assert (await client.get(f"/v1/cases/{case}/deletion-certificate")).status_code == 404
    start_deletion(admin, case)
    assert (await client.get(f"/v1/cases/{case}/deletion-certificate")).status_code == 404


@TOMBSTONE
@pytest.mark.parametrize("person", ["expert", "admin"])
async def test_the_certificate_carries_counts_hashes_and_dates_only(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    expiry = date.today() + timedelta(days=30)
    admin.execute(
        "insert into app.deletion_certificates (case_id, deleted_at, counts, hashes, log_chain_heads,"
        " backup_expiry_on, sent_to) values (%s, now(), '{\"pages\": 3}', '{\"documents\": [\"ab\"]}',"
        " '{\"page_reads\": \"cd\"}', %s, '{named_expert}')",
        (case, expiry),
    )
    admin.execute("update app.cases set status = 'deleted', label = null where id = %s", (case,))
    resp = await (await client_for(team[person])).get(f"/v1/cases/{case}/deletion-certificate")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert set(body) == {
        "case_id", "deleted_at", "counts", "hashes", "log_chain_heads", "backup_expiry_on", "sent_to",
    }  # fmt: skip
    assert body["counts"] == {"pages": 3}
    assert body["hashes"] == {"documents": ["ab"]}
    assert body["log_chain_heads"] == {"page_reads": "cd"}
    assert body["backup_expiry_on"] == str(expiry)
    assert body["sent_to"] == ["named_expert"]


@TOMBSTONE
async def test_a_deleted_case_still_shows_its_close_summary(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    admin.execute("insert into app.deletion_certificates (case_id, deleted_at) values (%s, now())", (case,))
    admin.execute("update app.cases set status = 'deleted', label = null where id = %s", (case,))
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{case}/close")
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "deleted"
    assert resp.json()["deletion_started"] is True and resp.json()["deleted_at"] is not None


# --- export request -------------------------------------------------------------------------------------


@pytest.mark.parametrize("person", ["expert", "admin"])
async def test_export_queues_a_job_for_the_named_expert_or_an_admin(
    person: str, client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    resp = await (await client_for(team[person])).post(f"/v1/cases/{case}/export")
    assert resp.status_code == 202, resp.text
    assert jobs_of(admin, case, "export_case") == [uuid.UUID(resp.json()["job_id"])]
    assert "case.export_requested" in audit_actions(admin, case)


async def test_a_download_needs_an_export_job_of_the_case(
    client_for: ClientFor, seed: Seed, admin: psycopg.Connection, team: dict[str, uuid.UUID]
) -> None:
    case, other = new_case(seed, team, "settled"), new_case(seed, team, "settled")
    client = await client_for(team["expert"])
    other_job = seed.job(other, kind="export_case")
    wrong_kind = seed.job(case)
    for job in (other_job, wrong_kind, uuid.uuid4()):
        resp = await client.get(f"/v1/cases/{case}/export/{job}/download")
        assert resp.status_code == 404, resp.text


async def test_a_download_waits_for_the_export_to_finish(
    client_for: ClientFor, seed: Seed, team: dict[str, uuid.UUID]
) -> None:
    case = new_case(seed, team, "settled")
    job = seed.job(case, kind="export_case")
    resp = await (await client_for(team["expert"])).get(f"/v1/cases/{case}/export/{job}/download")
    assert resp.status_code == 409
