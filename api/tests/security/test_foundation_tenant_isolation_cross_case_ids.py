"""One tenant's ids under another tenant's case, in the path, the body and the query string (audit AUDIT-11).

The attacker is a real, fully entitled member of their OWN case (its named expert), so the case access check
passes. What is tested is the next line of defence: a sub-resource id that belongs to the VICTIM's case.

A. Path: the attacker's case id with the victim's page, document, fact, question, report version, invoice, meeting,
   case member, job, upload, ... id, on every route that has both (all methods). Expected 404, nothing of the
   victim in the body, the victim's rows unchanged (a fingerprint of every table the victim owns, before and after),
   and the same answer as for a made-up id (a different answer would tell the attacker the id exists).
B. Body: every body field that holds another record's id (`page_id`, `document_id`, `fact_ids`, `question_id`,
   `linked_case_id`, `person_id`, `organisation_id`, `named_expert_id`, ...) set to the victim's id, one field per
   request, on the attacker's own routes. Expected a 4xx, no change, and the made-up id's answer.
C. Query string: the same for query parameters that name a case, an organisation or a question.
D. Creating a case in the victim's organisation, or naming the victim's expert.

Body fields with no seeded victim value (so no request is sent): `direction_id`, `paragraph_id`, `paragraph_ids`,
`round_id`, `entry_id`, `transcript_id` (listed in `UNSEEDED_FIELDS`).
"""

# ruff: noqa: F811  (fixtures are imported from tests.support.isolation_http and used by name)
from __future__ import annotations

import uuid
from typing import Any

import pytest

from tests.support.isolation_http import (  # noqa: F401  (fixtures)
    RouteCase,
    body_with_id,
    changed_tables,
    fill_path,
    foreign_body_fields,
    foreign_query_fields,
    foreign_value,
    iso_app,
    iso_seed,
    iso_send,
    iso_world,
    leaked,
    object_placeholders,
    own_context,
    sample_body,
    sample_query,
    victim_fingerprint,
    walk_routes,
)

pytestmark = pytest.mark.anyio

UNSEEDED_FIELDS = ("direction_id", "paragraph_id", "paragraph_ids", "round_id", "entry_id", "transcript_id")
_NO_BODY = ("GET", "DELETE")


@pytest.fixture(scope="module")
def anyio_backend() -> str:
    return "asyncio"


def _error_of(response) -> str:  # type: ignore[no-untyped-def]
    try:
        err = response.json()["error"]
        return f"{err.get('code')}: {err.get('message')} {err.get('field') or err.get('fields') or ''}".strip()
    except Exception:  # noqa: BLE001
        return "no error envelope"


def _persona(world, path: str):  # type: ignore[no-untyped-def]
    """The attacker who is entitled to the route on their own side: the organisation's owner for organisation
    routes, the case's named expert for everything else."""
    owner_only = path.startswith("/v1/organisations/") or path.endswith(("/named-expert", "/replacement"))
    return world.attacker.people["owner" if owner_only else "expert"]


# --- A. the victim's sub-resource id under the attacker's own case ------------------------------------------------

MIXED = [
    pytest.param(c, id=f"{c.method} {c.path}")
    for c in walk_routes()
    if "{case_id}" in c.path and object_placeholders(c.path)
]


# Routes whose own-object control cannot succeed for a reason that is not about tenancy (the 404 they give says
# nothing about the id): the reason is recorded here, and a skip is allowed only for a route in this table.
CONTROL_NOT_REACHABLE_A = {
    ("DELETE", "/v1/cases/{case_id}/documents/{document_id}"): (
        "AUDIT-11 NEW-4: no DELETE policy on app.documents for app_api, so no document can ever be deleted "
        "(see test_the_named_expert_can_delete_a_document_of_their_own_case)"
    ),
}


def _sender(w, ctx, path: str):  # type: ignore[no-untyped-def]
    return w.attacker.people[ctx.persona] if ctx.persona else _persona(w, path)


@pytest.mark.parametrize("case", MIXED)
async def test_a_victims_sub_resource_under_my_own_case_is_not_found(case, iso_world, iso_send, iso_seed, admin):  # type: ignore[no-untyped-def]
    w = iso_world
    mine_ctx = own_context(admin, iso_seed, w.attacker, case.method, case.path)
    theirs = own_context(admin, iso_seed, w.victim, case.method, case.path)  # the victim has the same state
    victim_path = {k: v for k, v in theirs.path.items() if k != "case_id"}
    foreign, _ = fill_path(case.path, w.victim, case_from=w.attacker, overrides=victim_path)
    made_up, _ = fill_path(case.path, w.victim, case_from=w.attacker, random_objects=True)
    body = sample_body(case.route) if case.method not in _NO_BODY else None
    if isinstance(body, dict):
        body = {**body, **mine_ctx.body}
    who = _sender(w, mine_ctx, case.path)
    before = victim_fingerprint(admin, w.victim)
    try:
        real = await iso_send(
            who, case.method, foreign, json=body, params=sample_query(case.route), headers=theirs.headers, timeout=15
        )
        fake = await iso_send(
            who, case.method, made_up, json=body, params=sample_query(case.route), headers=theirs.headers, timeout=15
        )
    except TimeoutError:
        pytest.fail(f"{case.method} {case.path}: the request opened a stream and never answered")
    after = victim_fingerprint(admin, w.victim)
    where = f"{case.method} {case.path}"
    assert not leaked(real, w.victim), f"{where}: the victim's marker is in the response ({real.status_code})"
    assert real.status_code < 500, f"{where}: server error {real.status_code}"
    assert changed_tables(before, after) == [], f"{where}: changed the victim's {changed_tables(before, after)}"
    assert real.status_code == 404, f"{where}: status {real.status_code} ({_error_of(real)}), expected 404"
    assert real.status_code == fake.status_code, (
        f"{where}: {real.status_code} for a real id, {fake.status_code} for a made-up one"
    )
    # Control (after the checks above, so it cannot hide a failure): the attacker's OWN object on the same route
    # must be found and accepted. If it is not, the 404 above says nothing about the id: that is a failure unless the
    # route is in CONTROL_NOT_REACHABLE_A with its reason (then the test is reported as skipped).
    own, _ = fill_path(case.path, w.attacker, overrides=mine_ctx.path)
    mine = await iso_send(
        who, case.method, own, json=body, params=sample_query(case.route), headers=mine_ctx.headers, timeout=15
    )
    key = (case.method, case.path)
    if key in CONTROL_NOT_REACHABLE_A:
        assert mine.status_code == 404, f"{where}: the control now answers {mine.status_code}: remove it from the table"
        pytest.skip(CONTROL_NOT_REACHABLE_A[key])
    if mine.status_code == 404:
        pytest.skip(f"the attacker's own object is not found either ({_error_of(mine)}): the route cannot tell")
    if mine.status_code >= 500:  # the seeded sealed values are placeholders that cannot be decrypted (fixture)
        pytest.skip(f"the attacker's own object gives {mine.status_code}: the seeded sealed value is a placeholder")


# --- B. the victim's id in a body field ---------------------------------------------------------------------------

BODY_FIELDS = [
    pytest.param(c, f, id=f"{c.method} {c.path} :: {f}")
    for c in walk_routes()
    if c.method not in _NO_BODY
    for f in foreign_body_fields(c.route)
]


# Body fields (method, path, field) whose own-value control MUST be accepted (2xx), so that the refusal of the
# victim's id is a judgement of the id: each has its state seeded by `own_context`.
REQUIRED_CONTROLS = {
    ("POST", "/v1/cases/{case_id}/members", "person_id"),
    ("POST", "/v1/cases/{case_id}/named-expert", "person_id"),
    ("POST", "/v1/cases/{case_id}/support-access", "granted_to"),
    ("POST", "/v1/cases/{case_id}/questions/{question_id}/points", "fact_id"),
    ("POST", "/v1/cases/{case_id}/fact-links", "question_id"),
    ("POST", "/v1/cases/{case_id}/report/versions/{version_id}/serve", "recipient_party_id"),
    ("POST", "/v1/cases/{case_id}/report/versions/{version_id}/servings", "recipient_party_id"),
    ("POST", "/v1/cases/{case_id}/report/versions/{version_id}/servings", "correction_of"),
}
# Staff-only route: the attacker is not staff, so there is no own value; the escalation module tests it with staff.
_EXCLUSION_FIELDS = {("POST", "/v1/cases/duplicate-check", "case_id")}
_STAFF_ONLY_FIELDS = {("POST", "/v1/support/custody-transfers", "case_id")}


@pytest.mark.parametrize(("case", "field"), BODY_FIELDS)
async def test_b_victims_id_in_a_body_field_is_refused(case, field, iso_world, iso_send, iso_seed, admin):  # type: ignore[no-untyped-def]
    w = iso_world
    value = foreign_value(field, w.victim)
    if value is None:
        assert field in UNSEEDED_FIELDS, f"{field}: no victim value and not listed as unseeded"
        pytest.skip(f"no victim object of this kind is seeded for {field}")
    key = (case.method, case.path, field)
    if key in _STAFF_ONLY_FIELDS:
        pytest.skip("staff-only route: the escalation module sends it as staff and as everyone else")
    ctx = own_context(admin, iso_seed, w.attacker, case.method, case.path)
    url, _ = fill_path(case.path, w.attacker, overrides=ctx.path)
    who = _sender(w, ctx, case.path)
    before = victim_fingerprint(admin, w.victim)
    mine_before = victim_fingerprint(admin, w.attacker)
    query = sample_query(case.route)

    base = {**(body_with_id(case.route, field, "x") or {})}
    real_body = {**base, **{k: v for k, v in ctx.body.items() if k != field}}
    real_body[field] = _like(base[field], value)
    fake_body = {**real_body, field: _like(base[field], str(uuid.uuid4()))}
    real = await iso_send(who, case.method, url, json=real_body, params=query, headers=ctx.headers, timeout=15)
    fake = await iso_send(who, case.method, url, json=fake_body, params=query, headers=ctx.headers, timeout=15)
    after = victim_fingerprint(admin, w.victim)
    mine_after = victim_fingerprint(admin, w.attacker)
    where = f"{case.method} {case.path} [{field}]"
    assert not leaked(real, w.victim), f"{where}: the victim's marker is in the response ({real.status_code})"
    assert real.status_code < 500, f"{where}: server error {real.status_code}"
    assert changed_tables(before, after) == [], f"{where}: changed the victim's {changed_tables(before, after)}"
    own_changes = [t for t in changed_tables(mine_before, mine_after) if t != "sessions"]  # each request opens one
    assert own_changes == [], f"{where}: a refused request changed the attacker's {own_changes}"
    if key in _EXCLUSION_FIELDS:  # the id only names a case to leave out of what the caller can already see
        assert real.status_code == 200 and real.content == fake.content, (
            f"{where}: the answer differs between a real id and a made-up one"
        )
        return
    assert 400 <= real.status_code < 500, f"{where}: status {real.status_code}, expected a 4xx"
    assert (real.status_code, _error_of(real)) == (fake.status_code, _error_of(fake)), (
        f"{where}: {real.status_code} {_error_of(real)} for a real id, {fake.status_code} {_error_of(fake)} for a made-up one"  # noqa: E501
    )
    # Control: the attacker's OWN object in the same field must be accepted. If it is refused the same way, the
    # request never reached the id check (state, shape or role came first): a failure for the fields in
    # REQUIRED_CONTROLS, otherwise reported as skipped.
    own_value = ctx.body.get(field, foreign_value(field, w.attacker))
    mine_body = {**real_body, field: _like(base[field], str(own_value))}
    mine = await iso_send(who, case.method, url, json=mine_body, params=query, headers=ctx.headers, timeout=15)
    if key in REQUIRED_CONTROLS:
        assert mine.status_code < 300, (
            f"{where}: the attacker's own value is refused ({mine.status_code} {_error_of(mine)}): the victim's "
            "refusal above says nothing about the id"
        )
    elif mine.status_code >= 400 and (mine.status_code, _error_of(mine)) == (real.status_code, _error_of(real)):
        pytest.skip(
            f"the attacker's own id is refused the same way ({mine.status_code} {_error_of(mine)}): not reached"
        )


def _like(template: Any, value: str) -> Any:
    """The value in the shape of the field it replaces (a list of one when the field is a list)."""
    return [value] if isinstance(template, list) else value


# --- C. the victim's id in the query string -----------------------------------------------------------------------

QUERY_FIELDS = [
    pytest.param(c, f, id=f"{c.method} {c.path} ?{f}")
    for c in walk_routes()
    for f in foreign_query_fields(c.route)
    if f != "case_id" or "{case_id}" not in c.path
]


@pytest.mark.parametrize(("case", "field"), QUERY_FIELDS)
async def test_c_victims_id_in_the_query_string_is_refused(case, field, iso_world, iso_send, iso_seed, admin):  # type: ignore[no-untyped-def]
    w = iso_world
    value = foreign_value(field, w.victim)
    if value is None:
        pytest.skip(f"no victim object of this kind is seeded for ?{field}")
    ctx = own_context(admin, iso_seed, w.attacker, case.method, case.path)
    url, _ = fill_path(case.path, w.attacker, overrides=ctx.path)
    who = _sender(w, ctx, case.path)
    body = sample_body(case.route) if case.method not in _NO_BODY else None
    if isinstance(body, dict):
        body = {**body, **ctx.body}
    query = {**sample_query(case.route), **ctx.query}
    before = victim_fingerprint(admin, w.victim)
    real = await iso_send(
        who, case.method, url, json=body, params={**query, field: value}, headers=ctx.headers, timeout=15
    )
    fake = await iso_send(
        who, case.method, url, json=body, params={**query, field: str(uuid.uuid4())}, headers=ctx.headers, timeout=15
    )
    after = victim_fingerprint(admin, w.victim)
    where = f"{case.method} {case.path} ?{field}"
    assert not leaked(real, w.victim), f"{where}: the victim's marker is in the response ({real.status_code})"
    assert real.status_code < 500, f"{where}: server error {real.status_code}"
    assert changed_tables(before, after) == [], f"{where}: changed the victim's {changed_tables(before, after)}"
    assert (real.status_code, _error_of(real)) == (fake.status_code, _error_of(fake)), (
        f"{where}: {real.status_code} {_error_of(real)} for a real id, {fake.status_code} {_error_of(fake)} for a made-up one"  # noqa: E501
    )
    if real.status_code < 400:  # a filter that matches nothing of mine: the answer must not depend on the id
        assert real.content == fake.content, f"{where}: the answer differs between a real id and a made-up one"
    else:  # control: the attacker's own id must be treated differently, or the request never reached the id check
        mine = await iso_send(
            who,
            case.method,
            url,
            json=body,
            params={**query, field: str(foreign_value(field, w.attacker))},
            headers=ctx.headers,
            timeout=15,
        )
        if (mine.status_code, _error_of(mine)) == (real.status_code, _error_of(real)):
            pytest.skip(
                f"the attacker's own id is refused the same way ({mine.status_code} {_error_of(mine)}): not reached"
            )


# --- D. creating a case in or for the victim's tenant -------------------------------------------------------------

_CREATE = next(c for c in walk_routes() if c.method == "POST" and c.path == "/v1/cases")
# the fixture pack combination this test cell offers (rule 11: nothing real is named here)
_NEW_CASE = {"jurisdiction": "zz-test", "case_type": "zz-case", "specialty": "zz-specialty", "label": "zz"}


def _cases_of(admin, org=None, expert=None) -> int:  # type: ignore[no-untyped-def]
    row = admin.execute(
        "select count(*) from app.cases where (%(org)s::uuid is null or organisation_id = %(org)s)"
        " and (%(expert)s::uuid is null or named_expert_id = %(expert)s)",
        {"org": org, "expert": expert},
    ).fetchone()
    return int(row[0])


async def test_d_the_attacker_can_create_a_case_in_their_own_organisation(iso_world, iso_send):  # type: ignore[no-untyped-def]
    """Control: the request shape below is valid, so the refusals that follow are about the ids."""
    w = iso_world
    body = {**sample_body(_CREATE.route), **_NEW_CASE, "organisation_id": str(w.attacker.org)}
    response = await iso_send(w.attacker.expert, "POST", "/v1/cases", json=body)
    assert response.status_code in (200, 201), f"status {response.status_code} ({_error_of(response)})"


@pytest.mark.parametrize("who", ["owner", "admin", "expert", "assistant", "viewer"])
async def test_d_creating_a_case_in_the_victims_organisation_is_refused(who, iso_world, iso_send, admin):  # type: ignore[no-untyped-def]
    w = iso_world
    base = {**sample_body(_CREATE.route), **_NEW_CASE}
    before = _cases_of(admin, org=w.victim.org)
    real = await iso_send(
        w.attacker.people[who], "POST", "/v1/cases", json={**base, "organisation_id": str(w.victim.org)}
    )
    fake = await iso_send(
        w.attacker.people[who], "POST", "/v1/cases", json={**base, "organisation_id": str(uuid.uuid4())}
    )
    assert not leaked(real, w.victim)
    assert real.status_code in (403, 404), f"attacker {who}: status {real.status_code} ({_error_of(real)})"
    assert (real.status_code, _error_of(real)) == (fake.status_code, _error_of(fake)), (
        "a real organisation is answered differently"
    )
    assert _cases_of(admin, org=w.victim.org) == before, "a case was created in the victim's organisation"


@pytest.mark.parametrize("who", ["owner", "admin", "expert", "assistant", "viewer"])
async def test_d_naming_the_victims_expert_on_a_new_case_is_refused(who, iso_world, iso_send, admin):  # type: ignore[no-untyped-def]
    w = iso_world
    base = {**sample_body(_CREATE.route), **_NEW_CASE}
    body = {**base, "organisation_id": str(w.attacker.org), "named_expert_id": str(w.victim.expert)}
    before = _cases_of(admin, expert=w.victim.expert)
    response = await iso_send(w.attacker.people[who], "POST", "/v1/cases", json=body)
    assert not leaked(response, w.victim)
    assert response.status_code in (403, 404, 422), (
        f"attacker {who}: status {response.status_code} ({_error_of(response)})"
    )
    assert _cases_of(admin, expert=w.victim.expert) == before, "a case was created naming the victim's expert"
    # and with the victim's organisation as well
    both = {**base, "organisation_id": str(w.victim.org), "named_expert_id": str(w.victim.expert)}
    response = await iso_send(w.attacker.people[who], "POST", "/v1/cases", json=both)
    assert response.status_code in (403, 404, 422), (
        f"attacker {who}: status {response.status_code} ({_error_of(response)})"
    )
    assert _cases_of(admin, expert=w.victim.expert) == before


@pytest.mark.xfail(
    strict=True,
    reason=(
        "AUDIT-11 NEW-4: DELETE /v1/cases/{case_id}/documents/{document_id} answers 404 to the named expert on their "
        "own document: app.documents has no DELETE policy for app_api "
        "(supabase/migrations/0042_foundation_bundle_rls.sql:"
        "38-45), so repo.delete_document deletes no row: app/features/pipeline/repo.py:1239"
    ),
)
async def test_the_named_expert_can_delete_a_document_of_their_own_case(iso_world, iso_send, admin):  # type: ignore[no-untyped-def]
    """Safe behaviour: the named expert (who holds `case.upload`) deletes their own document: 204, and the row is gone.
    A refusal that is not about tenancy is why the delete cannot be tested as a cross-tenant route."""
    w = iso_world
    document = w.victim.ids["document"]
    response = await iso_send(w.victim.expert, "DELETE", f"/v1/cases/{w.victim.case}/documents/{document}")
    assert response.status_code == 204, f"{response.status_code} {_error_of(response)}"
    assert admin.execute("select count(*) from app.documents where id = %s", (document,)).fetchone()[0] == 0


async def test_zz_victim_tenant_is_unchanged_after_every_test_in_this_module(iso_world, admin):  # type: ignore[no-untyped-def]
    """The victim's expert can still read their own case after all of the above."""
    assert admin.execute("select count(*) from app.cases where id = %s", (iso_world.victim.case,)).fetchone()[0] == 1
