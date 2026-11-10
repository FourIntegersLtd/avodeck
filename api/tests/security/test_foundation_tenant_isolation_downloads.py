"""Can anyone else download a stored file of a case? (audit AUDIT-11, file-download routes)

The other tenant-isolation modules cannot answer this: their app holds no file store, and the seeded files hold
placeholder ciphertext, so even the owner gets 404, 500 or 503 and "the attacker got 404" proves nothing. Here the
app has a local S3 stand-in and a case-key provider the test holds (`tests/support/isolation_files.py`), and every
route is first driven to a real 200 for the named expert with the exact stored bytes (the positive control), so a
404 for anyone else is a refusal and not an empty store.

Per route, each asserted separately:

  1  Another organisation's owner, another organisation's expert, a personal-organisation outsider and Avodeck staff
     ask for the victim's URL: 404, none of the victim's bytes. Then the owner still gets the file (it existed, and
     the attack consumed nothing: export, transcript and offline copy are handed over once).
  2  The attacker's own case id with the victim's object id (page number, document, version, invoice, job, package):
     404; where the path holds no id, only the attacker's own file ever comes back. The attacker's own file at the
     same route is a 200 (control).
  3  A case assistant and viewer: 403 where `app/core/auth/permissions.py` refuses the action, the file where it
     allows it. An owner or admin of the victim's organisation who is not on the case: as the matrix says.
  4  After the member is removed (DELETE /cases/{id}/members/{member}): 404.

Everything is synthetic: each file carries a token unique to its tenant, and no assertion message shows a body.
"""

# ruff: noqa: F811  (fixtures are imported from tests.support and used by name)
from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
import psycopg
import pytest

from app.core.auth.permissions import allowed
from tests.support.isolation_files import (  # noqa: F401  (fixtures)
    CRYPTO,
    Env,
    Obj,
    Seeder,
    dl_env,
    iso_app,
    seed_certificate,
    seed_concordance_csv,
    seed_document_file,
    seed_export_zip,
    seed_invoice_pdf,
    seed_offline_package,
    seed_page_image,
    seed_page_thumbnail,
    seed_report_docx,
    seed_report_pdf,
    seed_report_preview,
    seed_signature,
    seed_spot_check_rows,
    seed_transcript_file,
    seed_trial_pack_download,
    seed_trial_pack_latest,
)
from tests.support.isolation_http import Sender, iso_seed, iso_send, iso_world, leaked  # noqa: F401  (fixtures)
from tests.support.isolation_world import IsolationWorld, Tenant, build_isolation_world

pytestmark = pytest.mark.anyio


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True)
class Route:
    name: str
    seed: Seeder
    action: str  # the permissions-matrix action the route requires
    bound: bool = False  # handed over only to the person who asked for it (export, transcript)


ROUTES = [
    Route("page_image", seed_page_image, "case.view_content"),
    Route("page_thumbnail", seed_page_thumbnail, "case.view_content"),
    Route("document_file", seed_document_file, "case.view_content"),
    Route("report_docx", seed_report_docx, "case.view_content"),
    Route("report_pdf", seed_report_pdf, "case.view_content"),
    Route("report_preview_page", seed_report_preview, "case.view_content"),
    Route("invoice_pdf", seed_invoice_pdf, "invoice.record_payment"),
    Route("export_zip", seed_export_zip, "case.export", bound=True),
    Route("deletion_certificate", seed_certificate, "case.view_certificate"),
    Route("transcript_download", seed_transcript_file, "case.export", bound=True),
    Route("trial_pack_latest", seed_trial_pack_latest, "case.view_content"),
    Route("trial_pack_download", seed_trial_pack_download, "case.view_content"),
    Route("concordance_export", seed_concordance_csv, "case.view_content"),
    Route("offline_package_download", seed_offline_package, "report.sign"),
]
BY_NAME = {r.name: r for r in ROUTES}
ROUTE_PARAMS = [pytest.param(r, id=r.name) for r in ROUTES]
# Routes whose path holds an id of the object (page number, document, version, invoice, job, package): the attacker's
# own case id with the victim's id must be a plain 404.
ID_ROUTES = [
    pytest.param(r, id=r.name)
    for r in ROUTES
    if r.name
    not in {
        "report_preview_page",
        "deletion_certificate",
        "trial_pack_latest",
        "trial_pack_download",
        "concordance_export",
    }
]

PERSONAS = ("attacker_owner", "attacker_expert", "outsider", "staff")


def who(w: IsolationWorld, persona: str) -> uuid.UUID:
    return {
        "attacker_owner": w.attacker.people["owner"],
        "attacker_expert": w.attacker.expert,
        "outsider": w.outsider,
        "staff": w.staff,
    }[persona]


def code_of(response: httpx.Response) -> str:
    try:
        return str(response.json()["error"]["code"])
    except Exception:  # noqa: BLE001
        return "no error envelope"


def said(response: httpx.Response) -> str:
    return f"{response.status_code} {code_of(response)}"


async def fetch(send: Sender, person: uuid.UUID, tenant: Tenant, obj: Obj, **kw: Any) -> httpx.Response:
    return await send(person, "GET", obj.url(tenant), params=obj.params or None, **kw)


def assert_file(response: httpx.Response, obj: Obj) -> None:
    """The response is this object: 200, the right type and the exact stored bytes (or its tokens)."""
    assert response.status_code == 200, said(response)
    assert response.headers.get("content-type", "").startswith(obj.media), response.headers.get("content-type")
    if obj.body:
        assert response.content == obj.body
    assert obj.in_(response.content)


def assert_nothing_of(response: httpx.Response, obj: Obj, tenant: Tenant) -> None:
    assert not obj.in_(response.content), "the response holds the victim's file"
    assert not leaked(response, tenant), "the response names the victim"


def member_of(admin: psycopg.Connection, case: uuid.UUID, person: uuid.UUID) -> uuid.UUID:
    row = admin.execute(
        "select id from app.case_members where case_id = %s and person_id = %s and removed_at is null", (case, person)
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


@pytest.fixture
def fresh(admin: psycopg.Connection, iso_seed) -> IsolationWorld:  # type: ignore[no-untyped-def]
    """A new victim, attacker, outsider and staff person for one test that changes things."""
    return build_isolation_world(admin, iso_seed)


# --- the owner control: every route really serves its file -----------------------------------------------------------


@pytest.mark.parametrize("route", ROUTE_PARAMS)
async def test_the_named_expert_downloads_the_exact_file(route: Route, dl_env: Env, iso_world, iso_send):  # type: ignore[no-untyped-def]
    victim = iso_world.victim
    obj = await route.seed(dl_env, victim)
    assert_file(await fetch(iso_send, victim.expert, victim, obj), obj)


# --- 1 other organisations, outsiders, staff -------------------------------------------------------------------------


@pytest.mark.parametrize("persona", PERSONAS)
@pytest.mark.parametrize("route", ROUTE_PARAMS)
async def test_a_stranger_asking_for_the_victims_url_gets_404_and_nothing(  # type: ignore[no-untyped-def]
    route: Route, persona: str, dl_env: Env, iso_world, iso_send
):
    victim = iso_world.victim
    obj = await route.seed(dl_env, victim)
    response = await fetch(iso_send, who(iso_world, persona), victim, obj)
    assert response.status_code == 404, said(response)
    assert_nothing_of(response, obj, victim)
    # the control: the file was there all along, and the refused request did not consume it
    assert_file(await fetch(iso_send, victim.expert, victim, obj), obj)


# --- 2 the attacker's own case with the victim's object id -----------------------------------------------------------


@pytest.mark.parametrize("route", ID_ROUTES)
async def test_own_case_with_the_victims_object_id_gets_404(route: Route, dl_env: Env, iso_world, iso_send):  # type: ignore[no-untyped-def]
    victim, attacker = iso_world.victim, iso_world.attacker
    v_obj, a_obj = await route.seed(dl_env, victim), await route.seed(dl_env, attacker)
    crossed = f"/v1/cases/{attacker.case}{v_obj.suffix}"
    response = await iso_send(attacker.expert, "GET", crossed, params=v_obj.params or None)
    assert response.status_code == 404, said(response)
    assert_nothing_of(response, v_obj, victim)
    assert_file(await fetch(iso_send, attacker.expert, attacker, a_obj), a_obj)  # the attacker's own file: 200
    assert_file(await fetch(iso_send, victim.expert, victim, v_obj), v_obj)  # the victim's is untouched


@pytest.mark.parametrize("route", [p for p in ROUTE_PARAMS if p not in ID_ROUTES])
async def test_own_case_on_a_route_without_an_object_id_serves_only_its_own_file(  # type: ignore[no-untyped-def]
    route: Route, dl_env: Env, iso_world, iso_send
):
    victim, attacker = iso_world.victim, iso_world.attacker
    v_obj, a_obj = await route.seed(dl_env, victim), await route.seed(dl_env, attacker)
    own_url = a_obj.url(attacker)
    response = await iso_send(attacker.expert, "GET", own_url, params=a_obj.params or None)
    assert_file(response, a_obj)
    assert_nothing_of(response, v_obj, victim)
    assert_file(await fetch(iso_send, victim.expert, victim, v_obj), v_obj)


# --- 3 in-case low roles and an organisation's owner or admin --------------------------------------------------------


@pytest.mark.parametrize("role", ["assistant", "viewer"])
@pytest.mark.parametrize("route", ROUTE_PARAMS)
async def test_case_assistant_and_viewer_follow_the_permissions_matrix(  # type: ignore[no-untyped-def]
    route: Route, role: str, dl_env: Env, iso_world, iso_send
):
    victim = iso_world.victim
    obj = await route.seed(dl_env, victim)
    response = await fetch(iso_send, victim.people[role], victim, obj)
    if allowed(route.action, role, role, "team") and not route.bound:
        assert_file(response, obj)
    else:
        assert response.status_code == 403, f"{route.action}: {said(response)}"
        assert_nothing_of(response, obj, victim)
        assert_file(await fetch(iso_send, victim.expert, victim, obj), obj)  # still there for the owner


@pytest.mark.parametrize("org_role", ["owner", "admin"])
@pytest.mark.parametrize("route", ROUTE_PARAMS)
async def test_org_owner_and_admin_not_on_the_case_follow_the_permissions_matrix(  # type: ignore[no-untyped-def]
    route: Route, org_role: str, dl_env: Env, iso_world, iso_send
):
    victim = iso_world.victim
    obj = await route.seed(dl_env, victim)
    response = await fetch(iso_send, victim.people[org_role], victim, obj)
    if allowed(route.action, None, org_role, "team") and not route.bound:
        assert_file(response, obj)
    else:
        assert response.status_code in (403, 404), f"{route.action}: {said(response)}"
        assert_nothing_of(response, obj, victim)
        assert_file(await fetch(iso_send, victim.expert, victim, obj), obj)


# --- 4 after the member is removed -----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["assistant", "viewer"])
@pytest.mark.parametrize("route", ROUTE_PARAMS)
async def test_a_removed_member_gets_404(route: Route, role: str, dl_env: Env, fresh, iso_send, admin):  # type: ignore[no-untyped-def]
    victim = fresh.victim
    obj = await route.seed(dl_env, victim)
    person = victim.people[role]
    before = await fetch(iso_send, person, victim, obj)
    expected = 200 if allowed(route.action, role, role, "team") and not route.bound else 403
    assert before.status_code == expected, f"before removal: {said(before)}"
    member = member_of(admin, obj.case or victim.case, person)
    removed = await iso_send(victim.expert, "DELETE", f"/v1/cases/{obj.case or victim.case}/members/{member}")
    assert removed.status_code == 204, said(removed)
    after = await fetch(iso_send, person, victim, obj)
    assert after.status_code == 404, f"after removal: {said(after)}"
    assert_nothing_of(after, obj, victim)
    assert_file(await fetch(iso_send, victim.expert, victim, obj), obj)


# --- the signature image (not under a case: it is the signed-in person's own) ----------------------------------------


async def test_the_signature_image_is_only_ever_the_signed_in_persons_own(dl_env: Env, iso_world, iso_send):  # type: ignore[no-untyped-def]
    victim, attacker = iso_world.victim, iso_world.attacker
    mine = await seed_signature(dl_env, victim.expert)
    theirs = await seed_signature(dl_env, attacker.expert)
    assert_file(await iso_send(victim.expert, "GET", mine.suffix), mine)  # control: the owner gets the exact image
    assert_file(await iso_send(attacker.expert, "GET", theirs.suffix), theirs)
    for person in (
        attacker.expert,
        attacker.people["owner"],
        iso_world.outsider,
        iso_world.staff,
        victim.people["assistant"],
        victim.people["owner"],
    ):
        response = await iso_send(person, "GET", mine.suffix)
        assert not mine.in_(response.content), "the response holds the victim's signature"
        if person == attacker.expert:
            assert_file(response, theirs)  # their own, never the victim's
        else:
            assert response.status_code == 404, said(response)


# --- the spot check: what the assistant is really allowed to do ------------------------------------------------------


async def test_a_spot_check_posts_a_real_201_for_the_named_expert(dl_env: Env, fresh, iso_send):  # type: ignore[no-untyped-def]
    """Control for the L5 test in `..._escalation.py`: with real sealed citations the named expert's POST succeeds."""
    attacker = fresh.attacker
    await seed_spot_check_rows(dl_env, attacker)
    response = await iso_send(
        attacker.expert,
        "POST",
        f"/v1/cases/{attacker.case}/spot-checks",
        json={"reviewer_email": "zz-outside@example.test"},
    )
    assert response.status_code == 201, said(response)


XFAIL_ASSISTANT = pytest.mark.xfail(
    strict=True,
    reason="AUDIT-11 L5 (measured here with real sealed citations: the assistant's POST is answered 201 and the"
    " colleague is mailed the code, not 403): POST /v1/cases/{id}/spot-checks sends cropped pages and case facts to"
    " an outside address and its action is report.build, which the assistant holds (_HELPERS):"
    " api/app/features/report/routes_spot_checks.py:69-73, api/app/core/auth/permissions.py (report.build)",
)


@pytest.mark.parametrize("role", ["viewer", pytest.param("assistant", marks=XFAIL_ASSISTANT)])
async def test_a_spot_check_to_an_outside_address_is_refused_to_low_roles(  # type: ignore[no-untyped-def]
    role: str, dl_env: Env, fresh, iso_send
):
    attacker = fresh.attacker
    await seed_spot_check_rows(dl_env, attacker)
    response = await iso_send(
        attacker.people[role],
        "POST",
        f"/v1/cases/{attacker.case}/spot-checks",
        json={"reviewer_email": "zz-outside@example.test"},
    )
    assert response.status_code in (403, 404), said(response)


# --- the export's file list (counts per kind of file, no content) --------------------------------------------


@pytest.mark.parametrize("persona", PERSONAS)
async def test_the_export_file_list_of_the_victims_case_is_404_to_strangers(persona: str, iso_world, iso_send):  # type: ignore[no-untyped-def]
    victim = iso_world.victim
    url = f"/v1/cases/{victim.case}/export/files"
    own = await iso_send(victim.expert, "GET", url)
    assert own.status_code == 200 and "files" in own.json(), said(own)  # control
    response = await iso_send(who(iso_world, persona), "GET", url)
    assert response.status_code == 404, said(response)
    assert not leaked(response, victim)
