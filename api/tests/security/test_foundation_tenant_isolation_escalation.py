"""Roles that grow, links that leak and fields that should not be writable (audit AUDIT-11).

Every test asserts the SAFE behaviour. A test marked `xfail(strict=True, reason="AUDIT-11 ...")` records what the
app does today (and where); when the app is fixed the test starts passing and strict mode flags the stale marker.

  Organisation roles   a member changing their own role, an admin making an owner, an admin changing the owner, a
                       non-owner inviting at role owner, an owner reaching into another organisation's members
  Invites              accepted by someone else, accepted twice, expired
  L2                   an organisation admin (no content access by design) adds themselves to a case, or reassigns it
  L5                   who may start a spot check for an outside email address, and with what step-up
  L7                   who may read invoices, time and bank details, against the permissions matrix
  L10                  one case member writing to, completing or cancelling another member's open upload
  Mass assignment      protected fields in PATCH bodies and unexpected keys on the dynamic update routes
  Support and metrics  a normal user on `/v1/support/...` and `/v1/internal/...`

Each test that changes anything uses a fresh world (`fresh`) so tests cannot affect each other.
"""

# ruff: noqa: F811  (fixtures are imported from tests.support.isolation_http and used by name)
from __future__ import annotations

import hashlib
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.auth.permissions import allowed
from app.core.contracts.case_names import PARTY_NAME_PURPOSE
from app.core.crypto import CaseCrypto, LocalKeyProvider
from app.features.organisations.service import token_hash
from app.features.report.assemble import VALUE_PURPOSE
from tests.support.deferred import drain
from tests.support.isolation_http import (  # noqa: F401  (fixtures)
    fill_path,
    iso_seed,
    iso_send,
    iso_world,
    leaked,
    sample_body,
    sample_query,
    victim_fingerprint,
    walk_routes,
)
from tests.support.isolation_world import IsolationWorld, build_isolation_world

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


CRYPTO = CaseCrypto(LocalKeyProvider.generate())  # the case-key provider of this module's app, held by the test


@pytest.fixture(scope="module")
async def iso_app(api_dsn: str, auth_env: dict[str, str]) -> AsyncIterator[FastAPI]:
    """The isolation app (as in `isolation_http`), with a case-key provider the test can seal values with, so the
    handlers that decrypt a case's values (a spot check) run for the named expert as they do in production."""
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.db import Database
    from app.core.email import OutboxMailer
    from app.features.report.services import ReportServices, configure
    from app.main import create_app
    from tests.support.files import local_storage
    from tests.support.packs import FIXTURE

    database = Database(api_dsn, max_size=4)
    await database.open()
    storage = await local_storage(auth_env)
    configure(ReportServices(crypto=CRYPTO, storage=storage))
    try:
        yield create_app(
            database=database,
            region=str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"]),
            auth=SupabaseAuth(auth_settings(auth_env)),
            mailer=OutboxMailer(),
            storage=storage,
            crypto=CRYPTO,
        )
    finally:
        configure(None)
        await storage.aclose()
        await database.close()


@pytest.fixture
async def aconn(admin_dsn: str) -> AsyncIterator[psycopg.AsyncConnection]:
    async with await psycopg.AsyncConnection.connect(admin_dsn, autocommit=True) as conn:
        yield conn


@pytest.fixture
def fresh(admin: psycopg.Connection, iso_seed) -> IsolationWorld:  # type: ignore[no-untyped-def]
    """A new victim, attacker, outsider and staff person for one test that changes things."""
    return build_isolation_world(admin, iso_seed)


def _error(response) -> str:  # type: ignore[no-untyped-def]
    try:
        err = response.json()["error"]
        return f"{err.get('code')}"
    except Exception:  # noqa: BLE001
        return "no error envelope"


def _refused(response) -> bool:  # type: ignore[no-untyped-def]
    return bool(400 <= response.status_code < 500)


def _org(world: IsolationWorld, who: str) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The attacker organisation, the person with this role in it and their membership."""
    a = world.attacker
    return a.org, a.people[who], a.memberships[who]


def _role(admin: psycopg.Connection, membership: uuid.UUID) -> tuple[str, bool]:
    row = admin.execute(
        "select role, ended_at is not null from app.memberships where id = %s", (membership,)
    ).fetchone()
    assert row is not None
    return row[0], row[1]


# --- organisation roles -------------------------------------------------------------------------------------------


@pytest.mark.parametrize("who", ["expert", "assistant", "viewer", "admin"])
async def test_a_member_cannot_make_themselves_owner(who, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    org, person, membership = _org(fresh, who)
    before = _role(admin, membership)
    response = await iso_send(person, "PATCH", f"/v1/organisations/{org}/members/{membership}", json={"role": "owner"})
    assert _refused(response), f"{who} made themselves owner: status {response.status_code}"
    assert _role(admin, membership) == before


@pytest.mark.parametrize("target", ["expert", "assistant", "viewer"])
async def test_an_admin_cannot_make_anyone_owner(target, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    org, person, _ = _org(fresh, "admin")
    membership = fresh.attacker.memberships[target]
    before = _role(admin, membership)
    response = await iso_send(person, "PATCH", f"/v1/organisations/{org}/members/{membership}", json={"role": "owner"})
    assert _refused(response), f"admin made a {target} owner: status {response.status_code}"
    assert _role(admin, membership) == before


async def test_an_admin_cannot_demote_or_remove_the_owner(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    org, person, _ = _org(fresh, "admin")
    owner = fresh.attacker.memberships["owner"]
    before = _role(admin, owner)
    demote = await iso_send(person, "PATCH", f"/v1/organisations/{org}/members/{owner}", json={"role": "viewer"})
    remove = await iso_send(person, "DELETE", f"/v1/organisations/{org}/members/{owner}")
    assert _refused(demote), f"admin demoted the owner: status {demote.status_code}"
    assert _refused(remove), f"admin removed the owner: status {remove.status_code}"
    assert _role(admin, owner) == before


@pytest.mark.parametrize("who", ["admin", "expert", "assistant", "viewer"])
async def test_a_non_owner_cannot_invite_at_role_owner(who, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    org, person, _ = _org(fresh, who)
    count = admin.execute("select count(*) from app.invites where organisation_id = %s", (org,)).fetchone()[0]
    response = await iso_send(
        person, "POST", f"/v1/organisations/{org}/invites", json={"email": "zz-new@example.test", "role": "owner"}
    )
    assert _refused(response), f"{who} invited an owner: status {response.status_code}"
    assert admin.execute("select count(*) from app.invites where organisation_id = %s", (org,)).fetchone()[0] == count


@pytest.mark.parametrize("method", ["PATCH", "DELETE"])
@pytest.mark.parametrize("org_of", ["attacker", "victim"])
async def test_an_owner_cannot_change_another_organisations_members(method, org_of, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    """The attacker's owner, with the victim's membership id, under their own or the victim's organisation id."""
    w = fresh
    org = w.attacker.org if org_of == "attacker" else w.victim.org
    target = w.victim.memberships["viewer"]
    before = victim_fingerprint(admin, w.victim)
    body = {"role": "owner"} if method == "PATCH" else None
    response = await iso_send(
        w.attacker.people["owner"], method, f"/v1/organisations/{org}/members/{target}", json=body
    )
    assert response.status_code == 404, (
        f"{method} with the victim's membership under the {org_of}'s organisation: {response.status_code}"
    )
    assert not leaked(response, w.victim)
    assert victim_fingerprint(admin, w.victim) == before


@pytest.mark.parametrize("org_of", ["attacker", "victim"])
async def test_an_owner_cannot_cancel_another_organisations_invite(org_of, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    org = w.attacker.org if org_of == "attacker" else w.victim.org
    invite = w.victim.ids["invite"]
    status = admin.execute("select status from app.invites where id = %s", (invite,)).fetchone()
    response = await iso_send(w.attacker.people["owner"], "DELETE", f"/v1/organisations/{org}/invites/{invite}")
    assert response.status_code == 404, f"{response.status_code}"
    assert admin.execute("select status from app.invites where id = %s", (invite,)).fetchone() == status


# --- invites ------------------------------------------------------------------------------------------------------


def _invite(
    admin: psycopg.Connection, org: uuid.UUID, invited_by: uuid.UUID, person: uuid.UUID, role: str
) -> tuple[str, uuid.UUID]:
    token = secrets.token_urlsafe(32)
    email = admin.execute("select email from app.people where id = %s", (person,)).fetchone()[0]  # type: ignore[index]
    row = admin.execute(
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash) values (%s, %s, %s, %s, %s)"
        " returning id",
        (org, email, role, invited_by, token_hash(token)),
    ).fetchone()
    assert row is not None
    return token, row[0]


def _members_of(admin: psycopg.Connection, org: uuid.UUID, person: uuid.UUID) -> int:
    return admin.execute(  # type: ignore[no-any-return,index]
        "select count(*) from app.memberships where organisation_id = %s and person_id = %s and ended_at is null",
        (org, person),
    ).fetchone()[0]


async def test_an_invite_is_accepted_only_by_the_invited_person(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    token, invite = _invite(admin, w.victim.org, w.victim.people["owner"], w.outsider, "viewer")
    others = [w.attacker.people["owner"], w.attacker.people["viewer"], w.victim.people["viewer"], w.staff]
    for other in others:
        before = admin.execute(
            "select count(*) from app.memberships where organisation_id = %s", (w.victim.org,)
        ).fetchone()
        response = await iso_send(other, "POST", f"/v1/invites/{token}/accept")
        assert response.status_code == 404, f"someone else accepted: {response.status_code} {_error(response)}"
        assert (
            admin.execute("select count(*) from app.memberships where organisation_id = %s", (w.victim.org,)).fetchone()
            == before
        )
    assert admin.execute("select status from app.invites where id = %s", (invite,)).fetchone() == ("pending",)
    # control: the invited person accepts, with the role they were invited at
    accepted = await iso_send(w.outsider, "POST", f"/v1/invites/{token}/accept")
    assert accepted.status_code == 200, f"{accepted.status_code} {_error(accepted)}"
    assert _members_of(admin, w.victim.org, w.outsider) == 1


async def test_an_accepted_invite_cannot_be_used_again(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    token, _ = _invite(admin, w.victim.org, w.victim.people["owner"], w.outsider, "viewer")
    assert (await iso_send(w.outsider, "POST", f"/v1/invites/{token}/accept")).status_code == 200
    count = admin.execute("select count(*) from app.memberships where organisation_id = %s", (w.victim.org,)).fetchone()
    for person in (w.outsider, w.attacker.people["owner"]):
        again = await iso_send(person, "POST", f"/v1/invites/{token}/accept")
        assert _refused(again), f"a used invite was accepted again: {again.status_code}"
    assert (
        admin.execute("select count(*) from app.memberships where organisation_id = %s", (w.victim.org,)).fetchone()
        == count
    )


async def test_an_expired_invite_cannot_be_accepted(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    token, invite = _invite(admin, w.victim.org, w.victim.people["owner"], w.outsider, "viewer")
    admin.execute(
        "update app.invites set created_at = now() - interval '3 days', expires_at = now() - interval '1 day' where id = %s",  # noqa: E501
        (invite,),
    )
    response = await iso_send(w.outsider, "POST", f"/v1/invites/{token}/accept")
    assert response.status_code == 409 and _error(response) == "invite_expired", (
        f"{response.status_code} {_error(response)}"
    )
    assert _members_of(admin, w.victim.org, w.outsider) == 0


async def test_an_unknown_invite_token_is_not_found(fresh, iso_send):  # type: ignore[no-untyped-def]
    response = await iso_send(fresh.outsider, "POST", f"/v1/invites/{secrets.token_urlsafe(32)}/accept")
    assert response.status_code == 404


# --- L2: an organisation admin gives themselves case content ------------------------------------------------------


async def _reachable(iso_send, person, tenant) -> set[str]:  # type: ignore[no-untyped-def]
    """The victim-case GET routes (and what they would show) that answer without an error for this person."""
    found: set[str] = set()
    for case in walk_routes():
        if case.method != "GET" or "{case_id}" not in case.path or not case.placeholders:
            continue
        url, _ = fill_path(case.path, tenant)
        try:
            response = await iso_send(person, "GET", url, params=sample_query(case.route), timeout=3)
        except TimeoutError:
            found.add(case.path)  # a stream opened
            continue
        if response.status_code < 400 or response.status_code in (409, 423, 422):
            found.add(case.path)
    return found


L2_REASON = (
    "AUDIT-11 POLICY L2: allowed by docs/spec/permissions.md:34 (case.manage_members: organisation owner and admin);"
    " owner decision. An organisation admin who is not on the case adds themselves with POST /v1/cases/{id}/members"
    " (api/app/features/setup/routes_case.py:111) in any of the four roles and then reads case content;"
    " the add is audited as case.member_added (routes_case.py:133)"
)


@pytest.mark.xfail(strict=True, reason=L2_REASON)
@pytest.mark.parametrize("role", ["co_expert", "supervisor", "assistant", "viewer"])
async def test_l2_an_org_admin_cannot_give_themselves_case_content_by_joining_the_case(role, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    boss = w.victim.people["admin"]
    before = await _reachable(iso_send, boss, w.victim)
    response = await iso_send(
        boss, "POST", f"/v1/cases/{w.victim.case}/members", json={"person_id": str(boss), "case_role": role}
    )
    if _refused(response):
        return
    after = await _reachable(iso_send, boss, w.victim)
    assert after <= before, f"after adding themselves as {role} the admin reaches {len(after - before)} more routes"


async def test_l2_the_self_add_is_audited(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    boss = w.victim.people["admin"]
    response = await iso_send(
        boss, "POST", f"/v1/cases/{w.victim.case}/members", json={"person_id": str(boss), "case_role": "viewer"}
    )
    if _refused(response):
        pytest.skip("the self-add is refused")
    row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and person_id = %s and action = 'case.member_added'",
        (w.victim.case, boss),
    ).fetchone()
    assert row is not None and row[0] == 1, "no audit row for the self-add"


CASE_ONLY_ROUTES = ("pages", "timeline", "questions", "parties", "comments", "conflicts", "hearings")


async def _content_status(iso_send, person, case: uuid.UUID) -> dict[str, int]:  # type: ignore[no-untyped-def]
    return {r: (await iso_send(person, "GET", f"/v1/cases/{case}/{r}")).status_code for r in CASE_ONLY_ROUTES}


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 POLICY L2b: allowed by docs/spec/permissions.md:35 (case.reassign_named_expert: organisation owner and admin, step-up); owner decision. An organisation admin who is not on a case that was never served reassigns it to themselves with"  # noqa: E501
    " POST /v1/cases/{id}/named-expert {person_id: self} (200; case.reassign_named_expert allows org admin with step-up and"  # noqa: E501
    " EXPERT_ROLES includes admin: api/app/features/organisations/routes_cases.py:42, service.py:280-315) and then reads"  # noqa: E501
    " pages, timeline and questions as the named expert; audited as case.named_expert_changed. Documented in"
    " docs/spec/permissions.md; recorded because it gives an administrator the case's content",
)
async def test_l2_an_org_admin_cannot_become_the_named_expert_of_a_case_they_are_not_on(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    boss = w.victim.people["admin"]
    created = await iso_send(
        w.victim.expert,
        "POST",
        "/v1/cases",
        json={
            "jurisdiction": "zz-test",
            "case_type": "zz-case",
            "specialty": "zz-specialty",
            "label": "zz",
            "organisation_id": str(w.victim.org),
        },
    )
    assert created.status_code in (200, 201), (
        f"control: the victim's expert creates a case: {created.status_code} {_error(created)}"
    )
    case = uuid.UUID(str(created.json()["id"]))
    before = await _content_status(iso_send, boss, case)
    assert set(before.values()) == {403}, f"control: the admin has no content access first: {before}"
    response = await iso_send(boss, "POST", f"/v1/cases/{case}/named-expert", json={"person_id": str(boss)})
    if _refused(response):
        return
    after = await _content_status(iso_send, boss, case)
    audit_row = admin.execute(
        "select count(*) from app.audit_log where case_id = %s and action = 'case.named_expert_changed'", (case,)
    ).fetchone()
    assert audit_row == (1,), "the reassignment is not audited"
    assert all(v >= 400 for v in after.values()), f"after reassigning to themselves the admin reads: {after}"


@pytest.mark.parametrize("who", ["assistant", "viewer"])
async def test_l2_an_ordinary_org_member_cannot_join_a_case_they_are_not_on(who, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    me = w.victim.people[who]
    count = admin.execute("select count(*) from app.case_members where case_id = %s", (w.victim.case,)).fetchone()
    response = await iso_send(
        me, "POST", f"/v1/cases/{w.victim.case}/members", json={"person_id": str(me), "case_role": "co_expert"}
    )
    assert _refused(response), f"{who} joined the case: {response.status_code}"
    assert (
        admin.execute("select count(*) from app.case_members where case_id = %s", (w.victim.case,)).fetchone() == count
    )


# --- L5: spot checks send case facts to an outside email address --------------------------------------------------

OUTSIDE = {"reviewer_email": "zz-outside@example.test"}


async def _seal_the_cited_values(admin: psycopg.Connection, aconn: psycopg.AsyncConnection, w: IsolationWorld) -> None:
    """Make the attacker case's report citations real: the cited values and party names are sealed with this
    module's case-key provider (the seeded ones are placeholders), and the pictures, words and subject identity
    that only the crop step reads are removed, so the handler runs to its end for a person who is let in."""
    case = w.attacker.case
    bundle = admin.execute("select bundle_id from app.cases where id = %s", (case,)).fetchone()[0]  # type: ignore[index]
    value = await CRYPTO.encrypt(aconn, case, b"zz-value", purpose=VALUE_PURPOSE)
    name = await CRYPTO.encrypt(aconn, case, b"Zz Qqname", purpose=PARTY_NAME_PURPOSE)
    admin.execute("set session_replication_role = replica")  # the sealed columns sit behind append-only triggers
    try:
        admin.execute("update app.citations set value_snapshot = %s where case_id = %s", (value, case))
        admin.execute("update app.case_parties set name = %s where case_id = %s", (name, case))
        admin.execute("delete from app.case_subject_identity where case_id = %s", (case,))
        admin.execute("delete from app.ocr_words where bundle_id = %s", (bundle,))
        admin.execute("update app.pages set image_path = null where bundle_id = %s", (bundle,))
    finally:
        admin.execute("set session_replication_role = default")


def _checks(admin: psycopg.Connection, case: uuid.UUID) -> int:
    return admin.execute("select count(*) from app.spot_checks where case_id = %s", (case,)).fetchone()[0]  # type: ignore[no-any-return,index]


def _mails_to_outside() -> int:
    from app.features.report.services import current

    return sum(1 for m in current().mailer.outbox if m.to == OUTSIDE["reviewer_email"])


async def _spot_check(iso_send, admin, worker_dsn: str, w: IsolationWorld, who: str, **kw: Any):  # type: ignore[no-untyped-def]
    """Start a spot check as this attacker-case person: (response, new spot_checks rows, new mails to the outside)."""
    rows, mails = _checks(admin, w.attacker.case), _mails_to_outside()
    response = await iso_send(
        w.attacker.people[who], "POST", f"/v1/cases/{w.attacker.case}/spot-checks", json=OUTSIDE, **kw
    )
    # The code mail is deferred work (BG-16): it reaches the mailer when the worker sweep runs.
    from app.features.report.services import current

    await drain(worker_dsn, current().mailer)
    return response, _checks(admin, w.attacker.case) - rows, _mails_to_outside() - mails


@pytest.fixture
async def sealed(fresh, admin, aconn) -> IsolationWorld:  # type: ignore[no-untyped-def]
    await _seal_the_cited_values(admin, aconn, fresh)
    return fresh  # type: ignore[no-any-return]


async def test_l5_the_named_expert_starts_a_spot_check(sealed, iso_send, admin, worker_dsn):  # type: ignore[no-untyped-def]
    """Control: with a fresh aal2 login the named expert gets 201, one spot_checks row and one mail to the address."""
    response, rows, mails = await _spot_check(iso_send, admin, worker_dsn, sealed, "expert")
    assert response.status_code == 201, f"{response.status_code} {_error(response)}"
    assert (rows, mails) == (1, 1)


async def test_l5_a_viewer_cannot_start_a_spot_check(sealed, iso_send, admin, worker_dsn):  # type: ignore[no-untyped-def]
    response, rows, mails = await _spot_check(iso_send, admin, worker_dsn, sealed, "viewer")
    assert response.status_code == 403, f"{response.status_code}"
    assert (rows, mails) == (0, 0)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 POLICY L5: allowed by docs/spec/permissions.md:49 (report.build: named expert and assistant; the"
    " spot check is report.build, permissions.md:66); owner decision. A case ASSISTANT starts POST"
    " /v1/cases/{id}/spot-checks {reviewer_email: outside address}: 201, a spot_checks row and a mail to the address,"
    " which carries a code for cropped pages of the case: api/app/features/report/routes_spot_checks.py:69-73,"
    " api/app/core/auth/permissions.py (_HELPERS)",
)
async def test_l5_an_assistant_cannot_send_facts_to_an_outside_address(sealed, iso_send, admin, worker_dsn):  # type: ignore[no-untyped-def]
    response, rows, mails = await _spot_check(iso_send, admin, worker_dsn, sealed, "assistant")
    assert response.status_code == 403, f"{response.status_code} {_error(response)}"
    assert (rows, mails) == (0, 0)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 L5a: the named expert starts POST /v1/cases/{id}/spot-checks after a password-only login (aal1):"
    " 201 with a mail to the outside address, no step_up_required (the route has no require_step_up and report.build"
    " has"
    " step_up=False) although it sends case facts to an outside address: api/app/features/report/routes_spot_checks.py:69,"  # noqa: E501
    " api/app/core/auth/permissions.py",
)
async def test_l5_sending_facts_outside_needs_mfa(sealed, iso_send, admin, worker_dsn):  # type: ignore[no-untyped-def]
    response, rows, mails = await _spot_check(iso_send, admin, worker_dsn, sealed, "expert", aal="aal1")
    assert response.status_code == 403 and _error(response) in ("step_up_required", "mfa_required"), (
        f"{response.status_code} {_error(response)}"
    )
    assert (rows, mails) == (0, 0)


@pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 L5b: the named expert starts POST /v1/cases/{id}/spot-checks with MFA completed 30 minutes ago:"
    " 201 with a mail to the outside address, no step_up_required (rule 10 asks 10 minutes for sending): "
    "api/app/features/report/routes_spot_checks.py:69, api/app/core/auth/permissions.py",
)
async def test_l5_sending_facts_outside_needs_mfa_from_the_last_ten_minutes(sealed, iso_send, admin, worker_dsn):  # type: ignore[no-untyped-def]
    response, rows, mails = await _spot_check(
        iso_send, admin, worker_dsn, sealed, "expert", mfa_age=timedelta(minutes=30)
    )
    assert response.status_code == 403 and _error(response) == "step_up_required", (
        f"{response.status_code} {_error(response)}"
    )
    assert (rows, mails) == (0, 0)


# --- L7: money routes against the permissions matrix --------------------------------------------------------------

# (route, the action the route requires). The case role of each person is their case role in the tenant.
MONEY_READS = [
    ("/v1/cases/{case_id}/invoices", "invoice.record_payment"),
    ("/v1/cases/{case_id}/time", "case.view_content"),
]


@pytest.mark.parametrize(("path", "action"), MONEY_READS)
@pytest.mark.parametrize("who", ["assistant", "viewer"])
async def test_l7_money_reads_follow_the_permissions_matrix(path, action, who, iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world.attacker
    url, _ = fill_path(path, w)
    response = await iso_send(w.people[who], "GET", url)
    permitted = allowed(action, who, who, "team")
    if permitted:
        assert response.status_code < 400, f"{who} should read {path}: {response.status_code} {_error(response)}"
    else:
        assert response.status_code == 403, f"{who} should be refused {path}: {response.status_code} {_error(response)}"


@pytest.mark.parametrize("who", ["assistant", "viewer"])
async def test_l7_invoice_pdf_and_credit_notes_follow_the_matrix(who, iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world.attacker
    url, _ = fill_path("/v1/cases/{case_id}/invoices/{invoice_id}/pdf", w)
    response = await iso_send(w.people[who], "GET", url, timeout=10)
    if allowed("invoice.record_payment", who, who, "team"):
        assert response.status_code != 403
    else:
        assert response.status_code == 403, f"{who}: {response.status_code}"
    post_url, _ = fill_path("/v1/cases/{case_id}/invoices/{invoice_id}/credit-notes", w)
    credit = await iso_send(w.people[who], "POST", post_url, json={"amount": "1.00", "reason": "zz"})
    assert credit.status_code == 403, (
        f"{who}: {credit.status_code} {_error(credit)}"
    )  # credit_note.issue: permissions.md:54


@pytest.mark.parametrize("who", ["assistant", "viewer", "expert"])
async def test_l7_bank_details_and_the_team_billing_profile_are_for_owners_and_admins(who, iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world.attacker
    bank = await iso_send(w.people[who], "GET", f"/v1/organisations/{w.org}/billing-profile/bank-details")
    assert bank.status_code == 404, f"{who} reads bank details: {bank.status_code}"
    billing = await iso_send(w.people[who], "GET", f"/v1/organisations/{w.org}/billing-profile")
    assert billing.status_code in (403, 404), f"{who} reads the team billing profile: {billing.status_code}"
    write = await iso_send(
        w.people[who],
        "PUT",
        f"/v1/organisations/{w.org}/billing-profile/bank-details",
        json={},
        headers={"if-match": '"1"'},
    )
    assert _refused(write), f"{who} writes bank details: {write.status_code}"


# --- L10: one member's open upload and another member ------------------------------------------------------------

DATA = b"zz-part"


async def _open_upload(iso_send, w: IsolationWorld) -> str:  # type: ignore[no-untyped-def]
    body = {"kind": "zz_exhibit", "filename": "zz.pdf", "size": len(DATA), "sha256": hashlib.sha256(DATA).hexdigest()}
    response = await iso_send(w.attacker.expert, "POST", f"/v1/cases/{w.attacker.case}/uploads", json=body)
    assert response.status_code == 201, f"{response.status_code} {_error(response)}"
    return str(response.json()["upload_id"])


def _upload_row(admin: psycopg.Connection, upload: str) -> tuple[str, list[int]]:
    row = admin.execute("select status, received from app.uploads where id = %s", (upload,)).fetchone()
    assert row is not None
    return row[0], list(row[1])


L10_WHERE = (
    'allowed by docs/spec/permissions.md:33 (case.upload: "also starting, sending parts of, completing, cancelling and'
    ' downloading an upload", named expert and assistant); owner decision. api/app/core/routes/uploads.py:140-153'
    " (_load checks case.upload on the upload's case, not who started it)"
)


@pytest.mark.xfail(
    strict=True,
    reason=f"AUDIT-11 POLICY L10: a case assistant writes a part into the named expert's open upload: {L10_WHERE}",
)
async def test_l10_another_member_cannot_write_a_chunk_into_my_upload(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    upload = await _open_upload(iso_send, fresh)
    response = await iso_send(
        fresh.attacker.people["assistant"],
        "PUT",
        f"/v1/uploads/{upload}/chunks/1",
        content=DATA,
        headers={"x-chunk-sha256": hashlib.sha256(DATA).hexdigest(), "content-type": "application/octet-stream"},
    )
    assert _refused(response), f"{response.status_code}"
    assert _upload_row(admin, upload)[1] == []


@pytest.mark.xfail(
    strict=True, reason=f"AUDIT-11 POLICY L10: a case assistant cancels the named expert's open upload: {L10_WHERE}"
)
async def test_l10_another_member_cannot_cancel_my_upload(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    upload = await _open_upload(iso_send, fresh)
    response = await iso_send(fresh.attacker.people["assistant"], "POST", f"/v1/uploads/{upload}/cancel")
    assert _refused(response), f"{response.status_code}"
    assert _upload_row(admin, upload)[0] == "open"


@pytest.mark.xfail(
    strict=True, reason=f"AUDIT-11 POLICY L10: a case assistant completes the named expert's open upload: {L10_WHERE}"
)
async def test_l10_another_member_cannot_complete_my_upload(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    upload = await _open_upload(iso_send, fresh)
    put = await iso_send(
        fresh.attacker.people["expert"],
        "PUT",
        f"/v1/uploads/{upload}/chunks/1",
        content=DATA,
        headers={"x-chunk-sha256": hashlib.sha256(DATA).hexdigest(), "content-type": "application/octet-stream"},
    )
    assert put.status_code == 200, f"control: the uploader's own part: {put.status_code} {_error(put)}"
    whole = {"sha256": hashlib.sha256(DATA).hexdigest()}
    response = await iso_send(fresh.attacker.people["assistant"], "POST", f"/v1/uploads/{upload}/complete", json=whole)
    assert _refused(response), f"{response.status_code} {_error(response)}"
    assert _upload_row(admin, upload)[0] == "open"


async def test_l10_the_uploader_can_cancel_their_own_upload(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    upload = await _open_upload(iso_send, fresh)
    response = await iso_send(fresh.attacker.expert, "POST", f"/v1/uploads/{upload}/cancel")
    assert response.status_code == 200, f"{response.status_code}"


async def test_l10_an_outsider_cannot_touch_an_upload_at_all(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    upload = await _open_upload(iso_send, fresh)
    for person in (fresh.victim.people["expert"], fresh.outsider, fresh.staff):
        for method, suffix in (("GET", ""), ("POST", "/cancel"), ("POST", "/complete")):
            response = await iso_send(person, method, f"/v1/uploads/{upload}{suffix}")
            assert response.status_code == 404, f"{method} {suffix or '/'}: {response.status_code}"
    assert _upload_row(admin, upload)[0] == "open"


# --- mass assignment ----------------------------------------------------------------------------------------------

PROTECTED = {
    "status": "served",
    "named_expert_id": None,
    "organisation_id": None,
    "bundle_id": None,
    "id": None,
    "case_id": None,
}


def _case_row(admin: psycopg.Connection, case: uuid.UUID) -> tuple[Any, ...]:
    row = admin.execute(
        "select status, named_expert_id, organisation_id, bundle_id, label, version from app.cases where id = %s",
        (case,),
    ).fetchone()
    assert row is not None
    return tuple(row)


@pytest.mark.parametrize("field", sorted(PROTECTED))
async def test_a_case_patch_cannot_set_a_protected_field(field, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    value = {
        "status": "served",
        "named_expert_id": str(w.victim.expert),
        "organisation_id": str(w.victim.org),
        "bundle_id": str(uuid.uuid4()),
        "id": str(w.victim.case),
        "case_id": str(w.victim.case),
    }[field]
    url = f"/v1/cases/{w.attacker.case}"
    tag = await iso_send(w.attacker.expert, "GET", url)
    assert tag.status_code == 200
    before = _case_row(admin, w.attacker.case)
    victim_before = victim_fingerprint(admin, w.victim)
    response = await iso_send(
        w.attacker.expert, "PATCH", url, json={field: value}, headers={"if-match": tag.headers["etag"]}
    )
    assert _refused(response), f"PATCH {{{field}}}: {response.status_code}"
    assert _case_row(admin, w.attacker.case) == before
    assert victim_fingerprint(admin, w.victim) == victim_before


async def test_a_case_patch_of_the_label_works(fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    """Control: the PATCH route accepts what it should, so the refusals above are about the field."""
    w = fresh
    url = f"/v1/cases/{w.attacker.case}"
    tag = await iso_send(w.attacker.expert, "GET", url)
    response = await iso_send(
        w.attacker.expert, "PATCH", url, json={"label": "zz-renamed"}, headers={"if-match": tag.headers["etag"]}
    )
    assert response.status_code == 200, f"{response.status_code} {_error(response)}"


DYNAMIC_MODULES = (
    "app.features.setup.routes_questions",
    "app.features.setup.routes_profile",
    "app.features.after_serving.routes_changes",
    "app.features.trial.routes_meetings",
)
DYNAMIC = [
    pytest.param(c, id=f"{c.method} {c.path}")
    for c in walk_routes()
    if c.route.endpoint.__module__ in DYNAMIC_MODULES
    and c.method in ("PATCH", "PUT")
    and c.route.body_field is not None
]


@pytest.mark.parametrize("case", DYNAMIC)
async def test_unexpected_keys_on_an_update_route_change_nothing_they_should_not(case, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    """Keys the model does not list (ownership, status, version, ids) are refused or ignored, never written."""
    w = fresh
    url, _ = fill_path(case.path, w.attacker)
    extras = {
        "case_id": str(w.victim.case),
        "id": str(w.victim.case),
        "status": "served",
        "version": 999,
        "named_expert_id": str(w.victim.expert),
        "organisation_id": str(w.victim.org),
        "person_id": str(w.victim.expert),
        "expert_id": str(w.victim.expert),
        "locked_at": "2020-01-01T00:00:00Z",
    }
    body = sample_body(case.route)
    body = {**(body if isinstance(body, dict) else {}), **extras}
    who = w.attacker.expert
    mine = case_row = _case_row(admin, w.attacker.case)
    victim_before = victim_fingerprint(admin, w.victim)
    response = await iso_send(who, case.method, url, json=body, params=sample_query(case.route))
    if response.status_code >= 500:
        plain = await iso_send(who, case.method, url, json=sample_body(case.route), params=sample_query(case.route))
        if plain.status_code >= 500:
            pytest.skip(f"the same request without the extra keys also answers {plain.status_code}: a fixture artefact")
    assert response.status_code < 500, f"server error {response.status_code} only with the extra keys"
    assert not leaked(response, w.victim)
    assert victim_fingerprint(admin, w.victim) == victim_before, "the victim's rows changed"
    assert _case_row(admin, w.attacker.case)[:4] == case_row[:4] == mine[:4], "the case's ownership or status changed"
    if response.status_code < 300:  # accepted: the extra keys must have been ignored, so the row kept its own case
        stray = admin.execute(
            "select count(*) from app.questions where case_id = %s and id::text = %s",
            (w.victim.case, str(w.victim.case)),
        ).fetchone()
        assert stray == (0,)


# --- support and metrics ------------------------------------------------------------------------------------------

STAFF_ONLY = [
    pytest.param(c, id=f"{c.method} {c.path}")
    for c in walk_routes()
    if c.path.startswith(("/v1/support/", "/v1/internal/"))
]
STAFF_GET_ONLY = [p for p in STAFF_ONLY if p.values[0].method == "GET"]  # type: ignore[union-attr]


@pytest.mark.parametrize("who", ["owner", "expert", "assistant", "viewer", "admin"])
@pytest.mark.parametrize("case", STAFF_ONLY)
async def test_a_normal_user_gets_404_on_staff_routes(case, who, iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world
    url, _ = fill_path(case.path, w.attacker)
    body = sample_body(case.route) if case.method not in ("GET", "DELETE") else None
    response = await iso_send(
        w.attacker.people[who], case.method, url, json=body, params={**sample_query(case.route), "q": "zz"}, timeout=10
    )
    assert response.status_code == 404, f"{who} {case.method} {case.path}: {response.status_code} {_error(response)}"
    assert not leaked(response, w.victim)


# The three staff routes that sit behind a feature flag (`require_flag` answers 404 while it is off, before the staff
# check). The world switches the flags on for every person it tests, so a 404 for a non-staff person can only come from
# the staff check; staff succeed, which proves the flag is on and the routes are reachable.
FLAGS = ("support_custody_transfer", "support_job_resume", "support_job_requeue")


def _flagged(w: IsolationWorld) -> list[tuple[str, str, dict[str, Any]]]:
    job = w.victim.ids["job"]
    return [
        (
            "POST",
            "/v1/support/custody-transfers",
            {"case_id": str(w.victim.case), "to": "organisation", "evidence_note": "zz"},
        ),
        ("POST", f"/v1/support/jobs/{job}/resume", {"reason_code": "fault_fixed"}),
        ("POST", f"/v1/support/jobs/{job}/requeue", {"reason_code": "fault_fixed"}),
    ]


def test_the_support_flags_are_on_for_everyone_who_is_tested(fresh, admin):  # type: ignore[no-untyped-def]
    w = fresh
    people = [*w.victim.people.values(), *w.attacker.people.values(), w.outsider, w.staff]
    for key in FLAGS:
        on = admin.execute(
            "select count(distinct scope_id) from app.feature_flags where key = %s and scope = 'person' and enabled"
            " and scope_id = any(%s)",
            (key, people),
        ).fetchone()
        assert on == (len(people),), f"{key} is not on for every tested person"


async def test_real_staff_get_through_the_flagged_staff_routes(fresh, iso_send):  # type: ignore[no-untyped-def]
    """Control: staff are not refused (the answer is the handler's own: a transfer, or a job in the wrong state)."""
    for method, url, body in _flagged(fresh):
        response = await iso_send(fresh.staff, method, url, json=body)
        assert response.status_code in (200, 201, 409), f"{url}: {response.status_code} {_error(response)}"
        if response.status_code == 409:
            assert _error(response) in ("not_served", "job_not_paused", "job_not_failed", "case_on_hold"), (
                f"{url}: {_error(response)}"
            )


@pytest.mark.parametrize(
    "who", ["attacker_owner", "attacker_expert", "attacker_assistant", "attacker_viewer", "victim_admin", "outsider"]
)
async def test_the_flagged_staff_routes_refuse_anyone_who_is_not_staff(who, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    w = fresh
    person = {
        "attacker_owner": w.attacker.people["owner"],
        "attacker_expert": w.attacker.people["expert"],
        "attacker_assistant": w.attacker.people["assistant"],
        "attacker_viewer": w.attacker.people["viewer"],
        "victim_admin": w.victim.people["admin"],
        "outsider": w.outsider,
    }[who]
    before = victim_fingerprint(admin, w.victim)
    transfers = admin.execute("select count(*) from app.custody_transfers").fetchone()
    for method, url, body in _flagged(w):
        response = await iso_send(person, method, url, json=body)
        assert response.status_code == 404, f"{who} {url}: {response.status_code} {_error(response)}"
        assert not leaked(response, w.victim)
    assert admin.execute("select count(*) from app.custody_transfers").fetchone() == transfers
    after = victim_fingerprint(admin, w.victim)
    assert {k: v for k, v in after.items() if k != "sessions"} == {k: v for k, v in before.items() if k != "sessions"}


@pytest.mark.parametrize("case", STAFF_GET_ONLY)
async def test_real_staff_reach_every_staff_read_route(case: Any, iso_world: Any, iso_send: Any) -> None:
    """Control for the 404s above: staff are not answered 404 for the route itself (a route that needs an id may say
    not found for an id staff have no grant for, which is skipped, never counted)."""
    route = case
    if route.path.startswith("/v1/internal/"):
        pytest.skip("gated by an operator bearer token, not by the staff list (api/app/core/routes/ops.py:34)")
    url, _ = fill_path(route.path, iso_world.attacker)
    try:
        response = await iso_send(
            iso_world.staff, "GET", url, params={**sample_query(route.route), "q": "zz"}, timeout=10
        )
    except TimeoutError:
        pytest.skip("a stream opened")
    assert response.status_code < 500, f"{route.path}: {response.status_code}"
    if response.status_code == 404:
        assert route.placeholders, f"{route.path}: staff are answered 404 on a route with no id"
        pytest.skip("not found for staff as well (an id they have no grant for)")
    assert not leaked(response, iso_world.victim)
