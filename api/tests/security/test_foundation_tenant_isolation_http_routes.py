"""Every route, every method, four callers who must not reach another tenant's data (audit AUDIT-11).

Each route is called with the VICTIM tenant's ids in its path by: the owner of an unrelated team (attacker owner),
the named expert of an unrelated team (attacker expert), a person with only a personal organisation (outsider), and
Avodeck support staff with no grant on the case (staff). The session is valid: CSRF, origin, a fresh aal2 login and a
new Idempotency-Key, so the answer is the access check's, not a missing header's.

Expected: 404 (the case, organisation or object does not exist for this caller) and nothing of the victim in the
body. 403 is not accepted anywhere (it would tell the caller the object exists); `DOCUMENTED_NOT_404` lists the
routes where another status is the documented answer, and why.

Skipped, each with its reason (see `SKIPS`):
  public by design: sign-in, MFA recovery, forgot and reset password, the cell description, public reference data,
  the e-signature webhook, the spot-check reviewer routes (bearer token), invite acceptance (bearer token; covered in
  `test_foundation_tenant_isolation_escalation.py`);
  self only: routes with no object parameter that act only on the caller's own data. Their GETs are still called by
  every persona and must show nothing of the victim (`test_self_only_get_shows_nothing_of_the_victim`).
The dictation WebSocket is not an HTTP route (`tests/guards/test_foundation_websocket_routes.py` covers it).

Placeholders with no seedable victim object get a random id (`fill_path`): `account_id` (support staff accounts),
`code` (public jurisdiction codes) and `token` (bearer tokens); the first is staff-only, the others are skipped.
"""

# ruff: noqa: F811  (fixtures are imported from tests.support.isolation_http and used by name)
from __future__ import annotations

import uuid
from typing import Any

import psycopg
import pytest
from fastapi.dependencies.models import Dependant

from app.core.auth.permissions import CASE_ACTIONS, allowed
from tests.support.isolation_http import (  # noqa: F401  (fixtures)
    RouteCase,
    changed_tables,
    fill_path,
    iso_app,
    iso_seed,
    iso_send,
    iso_world,
    leaked,
    own_context,
    sample_body,
    sample_query,
    victim_fingerprint,
    walk_routes,
)
from tests.support.isolation_world import IsolationWorld, build_isolation_world

pytestmark = pytest.mark.anyio

PERSONAS = ("attacker_owner", "attacker_expert", "outsider", "staff")

# Routes a signed-in stranger may legitimately call, for any caller: no victim object is named in them.
_PUBLIC = {
    "/v1/cell": "public: describes the cell",
    "/v1/jurisdictions": "public reference data",
    "/v1/jurisdictions/{code}": "public reference data (code is a public reference code, not an object)",
    "/v1/packs": "public reference data",
    "/v1/guidelines/in-force": "public reference data",
    "/v1/auth/sign-in": "public: sign-in",
    "/v1/auth/mfa/recover": "public: sign-in with a backup code",
    "/v1/auth/password/forgot": "public: password reset request",
    "/v1/auth/password/reset": "public: password reset by emailed token",
    "/v1/webhooks/esign": "public: signed webhook from the e-signature provider",
    "/v1/spot-checks/{token}": "public: reviewer link, the token is the credential",
    "/v1/spot-checks/{token}/responses": "public: reviewer link, the token is the credential",
    "/v1/invites/{token}/accept": "bearer token: covered in the escalation tests",
}
# Self only: would end or change the caller's own session or account; called for real they only affect the persona.
_SELF_ONLY_NEVER_CALLED = {"/v1/me/close", "/v1/auth/sign-out", "/v1/me/sessions/revoke-all"}
# Routes where a status other than 404 is the documented answer: (method, path) -> reason. None at present.
DOCUMENTED_NOT_404: dict[tuple[str, str], str] = {}

_STAFF_ONLY_PREFIXES = ("/v1/support/", "/v1/internal/")


def _skip_reason(case: RouteCase) -> str | None:
    if case.path in _PUBLIC:
        return _PUBLIC[case.path]
    if case.path.startswith(_STAFF_ONLY_PREFIXES):
        return None
    if not case.placeholders:
        return "self only: no object parameter, acts on the caller's own data"
    return None


def _build() -> tuple[list[Any], list[Any], dict[str, str]]:
    object_cases: list[Any] = []
    self_gets: list[Any] = []
    skips: dict[str, str] = {}
    for case in walk_routes():
        reason = _skip_reason(case)
        ident = f"{case.method} {case.path}"
        if reason is None:
            object_cases += [pytest.param(p, case, id=f"{p}|{ident}") for p in PERSONAS]
        elif reason.startswith("self only"):
            skips[ident] = reason
            if case.method == "GET":
                self_gets += [pytest.param(p, case, id=f"{p}|{ident}") for p in PERSONAS]
            else:
                object_cases.append(pytest.param(None, case, id=f"skip|{ident}", marks=pytest.mark.skip(reason=reason)))
        else:
            skips[ident] = reason
            object_cases.append(pytest.param(None, case, id=f"skip|{ident}", marks=pytest.mark.skip(reason=reason)))
    return object_cases, self_gets, skips


OBJECT_CASES, SELF_GETS, SKIPS = _build()
OBSERVED: list[tuple[str, str, str, int]] = []  # persona, method, path, status; read by the last test


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def _error_of(response) -> str:  # type: ignore[no-untyped-def]
    """The error envelope's code and message (the API puts no patient text in them), for assertion messages."""
    try:
        err = response.json()["error"]
        return f"{err.get('code')}: {err.get('message')}"
    except Exception:  # noqa: BLE001 - not an error envelope
        return "no error envelope"


def _person(world: IsolationWorld, persona: str):  # type: ignore[no-untyped-def]
    return {
        "attacker_owner": world.attacker.people["owner"],
        "attacker_expert": world.attacker.people["expert"],
        "outsider": world.outsider,
        "staff": world.staff,
    }[persona]


def _params(case: RouteCase, persona: str, tenant: Any) -> dict[str, str] | None:
    if case.path.startswith("/v1/support/"):
        # The search names the victim's case (staff find it by its id), so a refusal of a caller who is not staff is
        # a refusal of a search that WOULD have found something.
        return {"q": str(tenant.case)}
    return None


async def _call(world: IsolationWorld, iso_send: Any, persona: str, case: RouteCase, tenant: Any) -> Any:
    url, _ = fill_path(case.path, tenant)
    body = sample_body(case.route) if case.method not in ("GET", "DELETE") else None
    try:
        response = await iso_send(
            _person(world, persona), case.method, url, json=body, params=_params(case, persona, tenant), timeout=15
        )
    except TimeoutError:
        pytest.fail(f"{persona} {case.method} {case.path}: the request opened a stream and never answered")
    OBSERVED.append((persona, case.method, case.path, response.status_code))
    return response


@pytest.mark.parametrize(("persona", "case"), OBJECT_CASES)
async def test_route_refuses_a_caller_outside_the_victims_tenant(persona, case, iso_world, iso_send):  # type: ignore[no-untyped-def]
    response = await _call(iso_world, iso_send, persona, case, iso_world.victim)
    where = f"{persona} {case.method} {case.path}"
    assert not leaked(response, iso_world.victim), (
        f"{where}: the victim's marker is in the response ({response.status_code})"
    )
    assert response.status_code < 500, f"{where}: server error {response.status_code}"
    if persona == "staff" and case.path.startswith(_STAFF_ONLY_PREFIXES):
        return  # staff reach the support console by design (metadata only): see the staff tests below
    expected = DOCUMENTED_NOT_404.get((case.method, case.path), 404)
    assert response.status_code == expected, f"{where}: status {response.status_code}, expected {expected}"


@pytest.mark.parametrize(("persona", "case"), SELF_GETS)
async def test_self_only_get_shows_nothing_of_the_victim(persona, case, iso_world, iso_send):  # type: ignore[no-untyped-def]
    response = await _call(iso_world, iso_send, persona, case, iso_world.victim)
    where = f"{persona} {case.method} {case.path}"
    assert response.status_code < 500, f"{where}: server error {response.status_code}"
    assert not leaked(response, iso_world.victim), f"{where}: the victim's marker is in the response"


# Positive control: the victim's own named expert gets through on the same kind of route, so the 404s above mean
# "not yours", not "the fixture is broken".
CONTROL_PATHS = (
    "/v1/cases/{case_id}",
    "/v1/cases/{case_id}/members",
    "/v1/cases/{case_id}/files",
    "/v1/cases/{case_id}/questions",
    "/v1/cases/{case_id}/deadlines",
    "/v1/organisations/{org_id}",
    "/v1/organisations/{org_id}/members",
    "/v1/me",
    "/v1/notifications",
)


@pytest.mark.parametrize("path", CONTROL_PATHS)
async def test_the_victims_named_expert_reaches_the_same_routes(path, iso_world, iso_send):  # type: ignore[no-untyped-def]
    url, _ = fill_path(path, iso_world.victim)
    response = await iso_send(iso_world.victim.expert, "GET", url)
    assert 200 <= response.status_code < 300, f"GET {path}: status {response.status_code}"


# Coverage control: the victim's own named expert calls every GET route that names a victim object. A 404 there
# would mean the attacker's 404 above proves nothing for that route (the object is not where the route looks), so
# it must be listed in `CONTROL_NOT_REACHABLE` with the reason.
_ARTEFACT = "reads a stored or derived artefact the world builder does not create: "
CONTROL_NOT_REACHABLE: dict[str, str] = {
    "/v1/cases/{case_id}/deletion-certificate": _ARTEFACT + "no certificate exists for an open case",
    "/v1/cases/{case_id}/documents/{document_id}/file": _ARTEFACT + "the document's file in storage",
    "/v1/cases/{case_id}/pages/{page_no}/image": _ARTEFACT + "the page image in storage",
    "/v1/cases/{case_id}/pages/{page_no}/thumbnail": _ARTEFACT + "the page thumbnail in storage",
    "/v1/cases/{case_id}/instructions/{version_id}/pages": _ARTEFACT + "the instruction letter's page images",
    "/v1/cases/{case_id}/instructions/{version_id}/pages/{page_number}": _ARTEFACT + "the letter's page image",
    "/v1/cases/{case_id}/export/{job_id}/download": _ARTEFACT + "an export job's file (the seeded job is not one)",
    "/v1/cases/{case_id}/transcript/download/{job_id}": _ARTEFACT + "a transcript download job's file",
    "/v1/cases/{case_id}/report/preview/{page}": _ARTEFACT + "a built report preview page",
    "/v1/cases/{case_id}/report/versions/{version_id}/download": _ARTEFACT + "a report version's docx/pdf file",
}
# Routes the named expert may not call by design (the organisation's owner or admin only: anyone else gets 404).
CONTROL_AS_OWNER = {"/v1/organisations/{org_id}/billing-profile/bank-details"}
_CONTROL_GETS = [
    pytest.param(c, id=c.path)
    for c in walk_routes()
    if c.method == "GET" and c.placeholders and c.path not in _PUBLIC and not c.path.startswith(_STAFF_ONLY_PREFIXES)
]


# GET routes that stream: the named expert's request opens and does not end, which is how a stream answers.
STREAMING: dict[str, str] = {
    "/v1/cases/{case_id}/events": "server-sent events: answers 200 and stays open",
    "/v1/jobs/{job_id}/events": "server-sent events: answers 200 and stays open",
}


@pytest.fixture
def fresh(admin: psycopg.Connection, iso_seed) -> IsolationWorld:  # type: ignore[no-untyped-def]
    """A new victim, attacker, outsider and staff person, for a test whose request changes things."""
    return build_isolation_world(admin, iso_seed)


def _control_person(world: IsolationWorld, path: str) -> uuid.UUID:
    owner = path in CONTROL_AS_OWNER or path.startswith("/v1/organisations")
    return world.victim.people["owner" if owner else "expert"]


@pytest.mark.parametrize("case", _CONTROL_GETS)
async def test_the_named_expert_is_not_told_404_on_their_own_objects(case, iso_world, iso_send):  # type: ignore[no-untyped-def]
    if case.path in CONTROL_NOT_REACHABLE:
        pytest.skip(CONTROL_NOT_REACHABLE[case.path])
    url, _ = fill_path(case.path, iso_world.victim)
    who = _control_person(iso_world, case.path)
    try:
        response = await iso_send(who, "GET", url, params=sample_query(case.route), timeout=3)
    except TimeoutError:
        if case.path in STREAMING:
            return
        pytest.fail(f"GET {case.path}: no answer within 3 seconds and the route is not listed as a stream")
    detail = _error_of(response)
    assert response.status_code != 404, f"GET {case.path}: the named expert's own object gets 404 ({detail})"
    if response.status_code >= 500:
        pytest.skip(
            f"the owner's own object gives {response.status_code} ({detail}): a seeded placeholder, not a route"
        )


# The same control for every route that changes something: the victim's named expert (or organisation owner) sends a
# valid request for their own objects in a FRESH world (so a delete or a status change cannot affect the next test),
# with the state the route needs seeded by `own_context`. The route must answer neither 404 nor 5xx; where it does,
# the test is skipped with the status and the error, so a 404 for a stranger on that route is not read as proof.
_WRITE_CONTROLS = [
    pytest.param(c, id=f"{c.method} {c.path}")
    for c in walk_routes()
    if c.method != "GET"
    and c.placeholders
    and c.path not in _PUBLIC
    and c.path not in _SELF_ONLY_NEVER_CALLED
    and not c.path.startswith(_STAFF_ONLY_PREFIXES)
]
_NO_BODY = ("GET", "DELETE")


@pytest.mark.parametrize("case", _WRITE_CONTROLS)
async def test_the_named_expert_is_let_through_on_every_write_route(case, fresh, iso_send, iso_seed, admin):  # type: ignore[no-untyped-def]
    w = fresh
    ctx = own_context(admin, iso_seed, w.victim, case.method, case.path)
    url, _ = fill_path(case.path, w.victim, overrides=ctx.path)
    body = sample_body(case.route) if case.method not in _NO_BODY else None
    if isinstance(body, dict):
        body = {**body, **ctx.body}
    who = w.victim.people[ctx.persona] if ctx.persona else _control_person(w, case.path)
    try:
        response = await iso_send(
            who,
            case.method,
            url,
            json=body,
            params={**sample_query(case.route), **ctx.query},
            headers=ctx.headers,
            timeout=10,
        )
    except TimeoutError:
        return  # a stream opened: the named expert got in
    detail = _error_of(response)
    if response.status_code == 404 or response.status_code >= 500:
        pytest.skip(f"the owner's own request gets {response.status_code} ({detail})")


# --- people on the victim's own team: what the permissions matrix says, route by route -------------------------------
# Four people of the victim's organisation who hold no named-expert role: a viewer and an assistant on the case, and
# an owner and an admin of the organisation who are NOT on the case. For each case route the actions it checks are
# looked up in `CASE_ACTIONS`; where the matrix does not give a person ALL of them the answer must be exactly 403 (the
# access check runs first) with nothing of the case in the body and, for a route that changes things, nothing changed.
# Where the matrix gives them the action, a GET must not be refused 403.
MEMBERS = {
    "victim_viewer": ("viewer", "viewer"),  # persona -> (organisation role, case role)
    "victim_assistant": ("assistant", "assistant"),
    "victim_owner": ("owner", None),
    "victim_admin": ("admin", None),
}


# Routes whose handler checks one more action itself, after `require_case_access` passed.
_INLINE_ACTIONS = {"/v1/cases/{case_id}/named-expert/candidates": ["case.reassign_named_expert"]}


def _actions(dependant: Dependant) -> list[str]:
    found: list[str] = []
    for dep in dependant.dependencies:
        action = getattr(dep.call, "case_action", None)
        if action is not None:
            found.append(action)
        found.extend(_actions(dep))
    return found


_MEMBER_CASES = []
for _c in walk_routes():
    if _c.path.startswith("/v1/cases/{case_id}") and _actions(_c.route.dependant):
        _MEMBER_CASES += [pytest.param(p, _c, id=f"{p}|{_c.method} {_c.path}") for p in MEMBERS]


@pytest.mark.parametrize(("persona", "case"), _MEMBER_CASES)
async def test_a_member_of_the_victims_team_gets_what_the_permissions_matrix_says(
    persona: str, case: Any, iso_world: Any, iso_send: Any, admin: Any
) -> None:
    w = iso_world
    org_role, case_role = MEMBERS[persona]
    actions = _actions(case.route.dependant) + _INLINE_ACTIONS.get(case.path, [])
    assert set(actions) <= set(CASE_ACTIONS)
    holds = all(allowed(a, case_role, org_role, "team") for a in actions)
    url, _ = fill_path(case.path, w.victim)
    body = sample_body(case.route) if case.method not in _NO_BODY else None
    mutating = case.method != "GET"
    if holds and mutating:
        pytest.skip("allowed by the matrix: the success side of a write is the named expert's control above")
    before = victim_fingerprint(admin, w.victim) if mutating else {}
    try:
        response = await iso_send(
            w.victim.people[org_role], case.method, url, json=body, params=sample_query(case.route), timeout=10
        )
    except TimeoutError:
        assert holds, f"{persona} {case.method} {case.path}: a stream opened for a person the matrix refuses"
        return
    where = f"{persona} {case.method} {case.path} (needs {sorted(set(actions))})"
    if holds:
        assert response.status_code != 403, f"{where}: refused 403 though the matrix allows it ({_error_of(response)})"
        return
    assert response.status_code == 403, f"{where}: status {response.status_code}, the matrix refuses it: expected 403"
    assert not leaked(response, w.victim), f"{where}: the victim's marker is in the refusal"
    if mutating:
        after = victim_fingerprint(admin, w.victim)
        assert changed_tables(before, after, ignore=("sessions",)) == [], f"{where}: a refused request changed rows"


# --- Avodeck staff: the console finds a case by its id, and shows metadata only --------------------------------------


async def test_staff_find_the_victims_case_by_its_id_and_see_metadata_only(iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world
    found = await iso_send(w.staff, "GET", "/v1/support/cases", params={"q": str(w.victim.case)})
    assert found.status_code == 200, f"{found.status_code} {_error_of(found)}"
    assert str(w.victim.case) in found.text, "control: the search found the case"
    assert not leaked(found, w.victim), "the console search shows something that identifies the tenant"
    detail = await iso_send(w.staff, "GET", f"/v1/support/cases/{w.victim.case}")
    assert detail.status_code == 200, f"{detail.status_code} {_error_of(detail)}"
    assert not leaked(detail, w.victim), "the console's case view shows something that identifies the tenant"


@pytest.mark.parametrize("who", ["attacker_owner", "attacker_expert", "outsider"])
async def test_a_search_for_the_victims_case_by_anyone_but_staff_is_404(who, iso_world, iso_send):  # type: ignore[no-untyped-def]
    w = iso_world
    person = _person(w, who)
    for query in (str(w.victim.case), w.victim.marker, f"{w.victim.expert.hex[:12]}@example.test"):
        for path in ("/v1/support/cases", "/v1/support/people"):
            response = await iso_send(person, "GET", path, params={"q": query})
            assert response.status_code == 404, f"{who} {path}?q=...: {response.status_code}"
            assert not leaked(response, w.victim)
    detail = await iso_send(person, "GET", f"/v1/support/cases/{w.victim.case}")
    assert detail.status_code == 404 and not leaked(detail, w.victim)


async def test_victim_ids_were_seeded_for_every_placeholder(iso_world):  # type: ignore[no-untyped-def]
    unseeded: set[str] = set()
    for case in walk_routes():
        unseeded.update(fill_path(case.path, iso_world.victim)[1])
    assert unseeded <= {"accounts/{account_id}", "jurisdictions/{code}", "spot-checks/{token}", "invites/{token}"}, (
        sorted(unseeded)
    )
    assert not iso_world.victim.missing, sorted(iso_world.victim.missing)


@pytest.mark.parametrize("path", ("/v1/cases/{case_id}", "/v1/cases/{case_id}/members", "/v1/organisations/{org_id}"))
async def test_the_attacker_personas_reach_their_own_tenant(path, iso_world, iso_send):  # type: ignore[no-untyped-def]
    """Control: the callers who get 404 on the victim's ids get 2xx on their own, so the 404s are about whose it is."""
    url, _ = fill_path(path, iso_world.attacker)
    for who in ("owner", "expert"):
        response = await iso_send(iso_world.attacker.people[who], "GET", url)
        assert 200 <= response.status_code < 300, f"attacker {who} GET {path}: {response.status_code}"


async def test_the_victim_fingerprint_sees_a_change(iso_world, admin):  # type: ignore[no-untyped-def]
    """Control for every 'the victim is unchanged' check: the fingerprint does move when a victim row changes."""
    before = victim_fingerprint(admin, iso_world.victim)
    admin.execute("update app.cases set label = label || 'x' where id = %s", (iso_world.victim.case,))
    try:
        assert victim_fingerprint(admin, iso_world.victim) != before
    finally:
        admin.execute(
            "update app.cases set label = left(label, length(label) - 1) where id = %s", (iso_world.victim.case,)
        )


async def test_zz_no_attacker_request_got_a_server_error() -> None:
    """Each request above already asserts `< 500`; this reads the whole list again. It judges only when this process
    ran every collecting test (a `-k` filter or a parallel worker sees a part, and a part proves nothing)."""
    expected = sum(1 for p in (*OBJECT_CASES, *SELF_GETS) if p.values[0] is not None)
    if len(OBSERVED) < expected:
        pytest.skip(f"only {len(OBSERVED)} of {expected} requests ran in this process")
    errors = sorted({(p, m, path, s) for p, m, path, s in OBSERVED if s >= 500})
    assert not errors, errors


# --- the leak detector itself: the marker must really be in the places it claims to cover ---------------------------
_MUST_BE_STAMPED = (
    "uploads.filename",
    "questions.text_verbatim",
    "report_versions.display_name",
    "comments.body",
    "assumptions.text_verbatim",
    "findings.reason",
    "points.reason",
)


@pytest.mark.parametrize("column", _MUST_BE_STAMPED)
def test_the_marker_was_written_into_the_victims_rows(column, iso_world):  # type: ignore[no-untyped-def]
    """If a stamp touched no row, a leak through that column could never be seen: fail loudly, never pass vacuously."""
    assert iso_world.stamped.get(column, 0) > 0, f"{column}: the marker reached no victim row ({iso_world.stamped})"
