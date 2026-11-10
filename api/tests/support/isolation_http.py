"""HTTP side of the cross-tenant tests (`tests/security/`): module-scoped fixtures, route walking, path filling.

The three security modules import the fixtures from here (`from tests.support.isolation_http import ...  # noqa: F401`)
and each defines a module-scoped `anyio_backend`, so one app and one pair of tenants serve a whole module instead of
being rebuilt for every one of the many generated tests. Every request gets a fresh device session (aal2, MFA just
completed), the CSRF header and the app origin, and a new Idempotency-Key.
"""

from __future__ import annotations

import functools
import re
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

import anyio
import httpx
import psycopg
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from pydantic import TypeAdapter

from tests.support.auth import APP_ORIGIN, CSRF_HEADER, cookie_header
from tests.support.contracts import add_report_version
from tests.support.isolation_world import IsolationWorld, Tenant, build_isolation_world
from tests.support.seed import Seed

# (previous path segment, placeholder) -> kind of victim object (a key of `Tenant.ids`); None = no seedable object.
_KIND_BY_SEGMENT: dict[tuple[str, str], str] = {
    ("sessions", "session_id"): "session",
    ("notifications", "notification_id"): "notification",
    ("jobs", "job_id"): "job",
    ("download", "job_id"): "job",
    ("export", "job_id"): "job",
    ("uploads", "upload_id"): "upload",
    ("documents", "document_id"): "document",
    ("witness-statements", "document_id"): "document",
    ("opposing-reports", "document_id"): "opposing_document",
    ("pages", "page_id"): "page",
    ("agenda-items", "item_id"): "agenda_item",
    ("missing-items", "item_id"): "missing_item",
    ("deadlines", "deadline_id"): "deadline",
    ("hearings", "hearing_id"): "hearing",
    ("comments", "comment_id"): "comment",
    ("findings", "finding_id"): "finding",
    ("lint-issues", "issue_id"): "lint_issue",
    ("opinions", "opinion_id"): "opinion",
    ("points", "point_id"): "point",
    ("relevant-facts", "relevant_id"): "relevant_fact",
    ("assumptions", "assumption_id"): "assumption",
    ("court-bundles", "bundle_id"): "court_bundle",
    ("concordance", "row_id"): "concordance",
    ("expert-meetings", "meeting_id"): "meeting",
    ("offline-packages", "package_id"): "offline_package",
    ("notes", "note_id"): "trial_note",
    ("invoices", "invoice_id"): "invoice",
    ("prefill", "suggestion_id"): "prefill_suggestion",
    ("instructing-parties", "ip_id"): "instructing_party",
    ("parties", "party_id"): "party",
    ("legal-holds", "hold_id"): "legal_hold",
    ("support-access", "grant_id"): "support_grant",
    ("invites", "invite_id"): "invite",
    ("roles", "role_id"): "role",
    ("training", "training_id"): "training",
    ("facts", "fact_id"): "fact",
    ("consultations", "fact_id"): "fact",
    ("report-changes", "change_id"): "report_change",
    ("written-questions", "question_id"): "written_question",
    ("questions", "question_id"): "question",
    ("opposing-claims", "claim_id"): "opposing_claim",
    ("witness-claims", "claim_id"): "witness_claim",
    ("versions", "version_id"): "report_version",
    ("instructions", "version_id"): "instruction_version",
    ("fact-links", "link_id"): "fact_link",
    ("links", "link_id"): "case_link",
}
_LITERALS = {"n": "1", "question_no": "1", "version_no": "1", "key": "zz_section"}
_NO_OBJECT = ("account_id", "code", "token")  # staff-only account ids, public reference codes, bearer tokens


_LITERAL_NAMES = {
    "case_id",
    "org_id",
    "organisation_id",
    "page_no",
    "page_number",
    "page",
    "n",
    "question_no",
    "version_no",
    "key",
}  # not a victim object: the case, the organisation, or a number or fixed key


def object_placeholders(path: str) -> list[str]:
    """The placeholders that name a sub-resource of a case or an organisation (a row of its own)."""
    return [n for n in re.findall(r"\{(\w+)\}", path) if n not in _LITERAL_NAMES]


def fill_path(
    path: str,
    tenant: Tenant,
    *,
    case_from: Tenant | None = None,
    random_objects: bool = False,
    overrides: dict[str, str] | None = None,
) -> tuple[str, list[str]]:
    """The path with the tenant's own ids in the placeholders, and the placeholders that got a random id instead
    (no object of that kind could be seeded, or none exists: staff account ids, public codes, tokens).

    `case_from`: take `{case_id}` (and the page number) from this other tenant, so another tenant's sub-resource
    ids sit under one's own case. `random_objects`: a random uuid for every sub-resource id (the control that a
    real foreign id is answered exactly as a made-up one). `overrides`: a value for a placeholder by name (an own
    value a route needs, see `own_context`); it wins over everything else except `random_objects`."""
    segments = path.split("/")
    unseeded: list[str] = []
    for i, seg in enumerate(segments):
        m = re.fullmatch(r"\{(\w+)\}", seg)
        if m is None:
            continue
        name, prev = m.group(1), segments[i - 1]
        value: str | None
        if overrides and name in overrides and not random_objects:
            value = overrides[name]
        elif name == "case_id":
            value = str((case_from or tenant).case)
        elif name in ("org_id", "organisation_id"):
            value = str(tenant.org)
        elif name in ("page_no", "page_number", "page"):
            value = str((case_from or tenant).page_no)
        elif random_objects and name not in _LITERAL_NAMES:
            value = str(uuid.uuid4())
        elif name == "membership_id":
            value = str(tenant.memberships["owner"])
        elif name == "member_id":
            value = str(tenant.case_members["assistant"])
        elif name in _LITERALS:
            value = _LITERALS[name]
        else:
            kind = _KIND_BY_SEGMENT.get((prev, name))
            value = tenant.ids.get(kind) if kind else None
        if value is None:
            value = str(uuid.uuid4())
            unseeded.append(f"{prev}/{{{name}}}")
        segments[i] = value
    return "/".join(segments), unseeded


@dataclass(frozen=True)
class RouteCase:
    method: str
    path: str
    route: APIRoute

    @property
    def placeholders(self) -> list[str]:
        return re.findall(r"\{(\w+)\}", self.path)


@functools.lru_cache(maxsize=1)
def walk_routes() -> tuple[RouteCase, ...]:
    from app.main import create_app

    cases = [
        RouteCase(method, r.path, r)
        for r in create_app().routes
        if isinstance(r, APIRoute)
        for method in sorted((r.methods or set()) - {"HEAD", "OPTIONS"})
    ]
    return tuple(sorted(cases, key=lambda c: (c.path, c.method)))


# --- a minimal valid body from the route's schema -----------------------------------------------------------------


def _sample(s: dict[str, Any], defs: dict[str, Any], depth: int = 0) -> Any:
    if "$ref" in s:
        return _sample(defs[s["$ref"].rsplit("/", 1)[-1]], defs, depth + 1)
    if "const" in s:
        return s["const"]
    if "enum" in s:
        return s["enum"][0]
    for key in ("anyOf", "oneOf"):
        if key in s:
            options = [o for o in s[key] if o.get("type") != "null"]
            return _sample(options[0], defs, depth + 1) if options else None
    if "allOf" in s:
        return _sample(s["allOf"][0], defs, depth + 1)
    kind = s.get("type")
    if kind == "object":
        props = s.get("properties", {})
        if depth > 6:
            return {}
        return {k: _sample(props[k], defs, depth + 1) for k in s.get("required", []) if k in props}
    if kind == "array":
        return [_sample(s.get("items", {}), defs, depth + 1) for _ in range(s.get("minItems", 0))]
    if kind == "string":
        fmt = s.get("format")
        if fmt == "uuid":
            return str(uuid.uuid4())
        if fmt == "date":
            return "2030-01-01"
        if fmt == "date-time":
            return "2030-01-01T00:00:00Z"
        if fmt == "email":
            return "zz-nobody@example.test"
        text = "zz"
        return text.ljust(min(s.get("minLength", 0), s.get("maxLength", 10**6)), "z")
    if kind in ("integer", "number"):
        low = s.get("minimum", s.get("exclusiveMinimum", 0) + 1 if "exclusiveMinimum" in s else 1)
        return max(low, 1) if "maximum" not in s else min(max(low, 1), s["maximum"])
    if kind == "boolean":
        return False
    return None


# Bodies whose model validators need more than the required keys (an "at least one change" or "confirmed" rule),
# so the request reaches the lookup the test is about instead of failing validation: (method, path) -> keys to set.
_BODY_OVERRIDES: dict[tuple[str, str], dict[str, Any]] = {
    ("PATCH", "/v1/cases/{case_id}/questions/{question_id}"): {"confirm": True},
    ("PATCH", "/v1/cases/{case_id}/assumptions/{assumption_id}"): {"confirm": True},
    ("PATCH", "/v1/cases/{case_id}/facts/{fact_id}/availability"): {"unknown": True, "reason": "zz"},
    ("POST", "/v1/cases/{case_id}/report/versions/{version_id}/sign"): {"declaration_confirmed": True},
    ("POST", "/v1/cases/duplicate-check"): {"jurisdiction": "zz-test"},
    ("POST", "/v1/cases"): {
        "jurisdiction": "zz-test",
        "case_type": "zz-case",
        "specialty": "zz-specialty",
        "label": "zz",
    },
    ("POST", "/v1/cases/{case_id}/questions/{question_id}/points"): {"side": "neutral"},
}
# Keys a body needs once one of its id fields is set (a correction names its serving and a reason).
_COMPANIONS: dict[tuple[str, str, str], dict[str, Any]] = {
    ("POST", "/v1/cases/{case_id}/report/versions/{version_id}/servings", "correction_of"): {"reason": "zz"},
}


def _meeting_type() -> str:
    from app.core.packs import default_library

    return str(default_library().combine("zz-test", "zz-case", "zz-specialty").meeting_types()[0]["key"])


def sample_body(route: APIRoute) -> Any:
    """A request body that passes validation (required keys only, ids random), or None when the route takes none."""
    field = route.body_field
    if field is None:
        return None
    try:
        schema = TypeAdapter(field.field_info.annotation or object).json_schema()
        body = _sample(schema, schema.get("$defs", {}))
    except Exception:  # a body shape this generator cannot read: an empty one still reaches the access check
        return {}
    for method in route.methods or ():
        if isinstance(body, dict) and (method, route.path) in _BODY_OVERRIDES:
            body = {**body, **_BODY_OVERRIDES[(method, route.path)]}
        if isinstance(body, dict) and (method, route.path) == ("POST", "/v1/cases/{case_id}/expert-meetings"):
            body = {**body, "type": _meeting_type()}
    return body


def sample_query(route: APIRoute) -> dict[str, Any]:
    """The required query parameters of a route (for example `format` on a download), sampled from their types."""
    out: dict[str, Any] = {}
    for q in route.dependant.query_params:
        if q.field_info.is_required():
            try:
                schema = TypeAdapter(q.field_info.annotation or object).json_schema()
                out[q.name] = _sample(schema, schema.get("$defs", {}))
            except Exception:  # noqa: BLE001
                out[q.name] = "zz"
    return out


# --- another tenant's ids in the body or the query string -----------------------------------------------------------

# Body or query field name -> where the victim's value for it comes from: ("id", kind) is `Tenant.ids[kind]`.
_FOREIGN_VALUE: dict[str, tuple[str, str]] = {
    "page_id": ("id", "page"),
    "page_ids": ("id", "page"),
    "clearer_copy_page_ids": ("id", "page"),
    "quarantined_page_ids": ("id", "page"),
    "document_id": ("id", "document"),
    "document_ids": ("id", "document"),
    "fact_id": ("id", "fact"),
    "fact_ids": ("id", "fact"),
    "question_id": ("id", "question"),
    "question_ids": ("id", "question"),
    "hearing_id": ("id", "hearing"),
    "from_party_id": ("id", "party"),
    "recipient_party_id": ("id", "party"),
    "correction_of": ("id", "serving"),
    "linked_case_id": ("case", ""),
    "case_id": ("case", ""),
    "organisation_id": ("org", ""),
    "required_template_id": ("id", "template"),
    "person_id": ("person", "expert"),
    "named_expert_id": ("person", "expert"),
    "granted_to": ("person", "expert"),
}


def foreign_value(name: str, victim: Tenant) -> str | None:
    where, kind = _FOREIGN_VALUE.get(name, ("", ""))
    if where == "id":
        return victim.ids.get(kind)
    if where == "case":
        return str(victim.case)
    if where == "org":
        return str(victim.org)
    if where == "person":
        return str(victim.people[kind])
    return None


def _is_uuid(s: dict[str, Any], defs: dict[str, Any]) -> bool:
    if "$ref" in s:
        return _is_uuid(defs[s["$ref"].rsplit("/", 1)[-1]], defs)
    if s.get("format") == "uuid":
        return True
    if s.get("type") == "array":
        return _is_uuid(s.get("items", {}), defs)
    return any(_is_uuid(o, defs) for k in ("anyOf", "oneOf") for o in s.get(k, []))


def _is_list(s: dict[str, Any]) -> bool:
    return s.get("type") == "array" or any(_is_list(o) for k in ("anyOf", "oneOf") for o in s.get(k, []))


def _top_properties(route: APIRoute) -> tuple[dict[str, Any], dict[str, Any]]:
    field = route.body_field
    if field is None:
        return {}, {}
    try:
        schema = TypeAdapter(field.field_info.annotation or object).json_schema()
    except Exception:  # noqa: BLE001
        return {}, {}
    defs = schema.get("$defs", {})
    while "$ref" in schema:
        schema = defs[schema["$ref"].rsplit("/", 1)[-1]]
    return schema.get("properties", {}), defs


def foreign_body_fields(route: APIRoute) -> list[str]:
    """The top-level body fields that hold another record's id (uuid or list of uuids) and have a victim value."""
    props, defs = _top_properties(route)
    return [n for n, s in props.items() if n in _FOREIGN_VALUE and _is_uuid(s, defs)]


def body_with_id(route: APIRoute, field: str, value: str) -> Any:
    """A valid body for the route with `field` set to `value` (a list of one when the field is a list)."""
    props, _ = _top_properties(route)
    body = sample_body(route)
    if not isinstance(body, dict):
        body = {}
    body[field] = [value] if _is_list(props[field]) else value
    for method in route.methods or ():
        body.update(_COMPANIONS.get((method, route.path, field), {}))
    return body


def foreign_query_fields(route: APIRoute) -> list[str]:
    return [
        q.name
        for q in route.dependant.query_params
        if q.name in _FOREIGN_VALUE and "UUID" in str(q.field_info.annotation)
    ]


# --- the tenant's OWN valid values, so a control request can be accepted ---------------------------------------------


@dataclass
class OwnContext:
    """What a route needs so that the tenant's OWN request is accepted (2xx): valid values for the other body fields
    (`body`), a header (`headers`, e.g. the right `If-Match`), own objects for path placeholders (`path`, by
    placeholder name) and who may call it (`persona`, an organisation role). The builders insert whatever state the
    route needs (a locked version, a second expert in the organisation, a quarantined page, ...) as the admin role, so
    a refusal of the victim's id is judged on the id and not on the request's shape or state."""

    body: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    path: dict[str, str] = field(default_factory=dict)
    query: dict[str, str] = field(default_factory=dict)  # own values for required query parameters
    persona: str | None = None


Builder = Callable[[psycopg.Connection, Seed, Tenant], OwnContext]
OWN_CONTEXT: dict[tuple[str, str], Builder] = {}
_PAST = "2020-01-01"  # a date that is not in the future for any pack time zone


def _one(admin: psycopg.Connection, sql: str, *args: Any) -> Any:
    row = admin.execute(sql, args).fetchone()
    assert row is not None, f"no row: {sql}"
    return row[0]


def _new_expert_member(admin: psycopg.Connection, seed: Seed, t: Tenant) -> uuid.UUID:
    """A new member of the tenant's organisation with the expert role, on no case."""
    person = seed.person(full_name=f"{t.name} extra expert {uuid.uuid4().hex[:8]}")
    seed.membership(t.org, person, "expert")
    return person


def _version_no(admin: psycopg.Connection, t: Tenant) -> int:
    return int(
        _one(admin, "select coalesce(max(version_no), 0) + 1 from app.report_versions where case_id = %s", t.case)
    )


def _own(method: str, path: str) -> Callable[[Builder], Builder]:
    def register(fn: Builder) -> Builder:
        OWN_CONTEXT[(method, path)] = fn
        return fn

    return register


_CASE = "/v1/cases/{case_id}"


@_own("POST", "/v1/cases")
def _b_create_case(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(body={"organisation_id": str(t.org)})


@_own("POST", "/v1/cases/duplicate-check")
def _b_duplicate_check(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(body={"identifier": "ZZ-ID-123456", "case_id": str(t.case)})


@_own("POST", _CASE + "/fact-links")
def _b_fact_links(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    fact = _one(
        admin, "select id from app.facts where case_id = %s and status = 'verified' order by created_at limit 1", t.case
    )
    return OwnContext(body={"fact_id": str(fact), "question_id": t.ids["question"]})


@_own("POST", _CASE + "/members")
def _b_members(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(body={"person_id": str(_new_expert_member(admin, seed, t))})


@_own("POST", _CASE + "/named-expert")
def _b_named_expert(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """The world's case is served (its expert cannot change), so this uses a new case that has not been."""
    case = seed.case(t.org, t.expert)
    return OwnContext(
        body={"person_id": str(_new_expert_member(admin, seed, t))}, path={"case_id": str(case)}, persona="owner"
    )


@_own("POST", _CASE + "/replacement")
def _b_replacement(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(
        body={"named_expert_id": str(_new_expert_member(admin, seed, t)), "instructing_party_consented": True},
        persona="owner",
    )


@_own("POST", _CASE + "/support-access")
def _b_support_access(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    staff = seed.person(full_name=f"{t.name} staff to grant {uuid.uuid4().hex[:8]}")
    seed.org(kind="personal", members={staff: "owner"})
    seed.support_staff(staff)
    return OwnContext(body={"granted_to": str(staff), "reason": "zz"})


@_own("POST", _CASE + "/questions/{question_id}/points")
def _b_points(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """A verified fact that is not yet a point on the question (a point is unique per fact and question)."""
    fact = _one(
        admin,
        "select f.id from app.facts f where f.case_id = %s and f.status = 'verified' and not exists"
        " (select 1 from app.points p where p.question_id = %s and p.fact_id = f.id) order by f.created_at limit 1",
        t.case,
        t.ids["question"],
    )
    admin.execute(  # the points route only offers facts on pages the named expert has read
        "insert into app.page_reads (case_id, page_id, person_id, dwell_seconds)"
        " select case_id, page_id, %s, 20 from app.facts where id = %s"
        " and not exists (select 1 from app.page_reads r where r.page_id = app.facts.page_id and r.person_id = %s)",
        (t.expert, fact, t.expert),
    )
    admin.execute(
        "insert into app.fact_question_links (case_id, fact_id, question_id, source, status)"
        " values (%s, %s, %s, 'expert', 'confirmed') on conflict do nothing",
        (t.case, fact, t.ids["question"]),
    )
    return OwnContext(body={"fact_id": str(fact), "side": "neutral"}, path={"question_id": t.ids["question"]})


@_own("POST", _CASE + "/report/versions/{version_id}/serve")
def _b_serve(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    version = add_report_version(admin, t.case, version_no=_version_no(admin, t), status="locked")
    party = _one(admin, "select id from app.case_parties where case_id = %s limit 1", t.case)
    return OwnContext(body={"served_on": _PAST, "recipient_party_id": str(party)}, path={"version_id": str(version)})


@_own("POST", _CASE + "/report/versions/{version_id}/servings")
def _b_servings(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    version, serving = admin.execute(
        "select report_version_id, id from app.report_servings where case_id = %s order by created_at limit 1",
        (t.case,),
    ).fetchone()  # type: ignore[misc]
    party = _one(admin, "select id from app.case_parties where case_id = %s limit 1", t.case)
    return OwnContext(
        body={"served_on": _PAST, "recipient_party_id": str(party), "correction_of": str(serving), "reason": "zz"},
        path={"version_id": str(version)},
    )


@_own("POST", _CASE + "/written-question-rounds")
def _b_rounds(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    party = _one(admin, "select id from app.case_parties where case_id = %s limit 1", t.case)
    return OwnContext(body={"document_id": t.ids["document"], "from_party_id": str(party)})


@_own("PATCH", _CASE + "/missing-items/{item_id}")
def _b_missing_item(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """The item has been asked for (`requested`), so it can be marked received by a page of the case."""
    admin.execute(  # listed a day ago, so the case's pages (added now) came after it
        "update app.missing_items set status = case when status = 'open' then 'requested' else status end,"
        " created_at = now() - interval '1 day' where id = %s",
        (t.ids["missing_item"],),
    )
    version = _one(admin, "select version from app.missing_items where id = %s", t.ids["missing_item"])
    return OwnContext(body={"status": "received", "page_id": t.ids["page"]}, headers={"if-match": f'"{version}"'})


@_own("PUT", "/v1/organisations/{org_id}/policy")
def _b_policy(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(body={"required_template_id": t.ids["template"]})


@_own("GET", "/v1/guidelines/in-force")
def _b_guidelines(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    from app.core.packs import default_library

    rows = default_library().combine("zz-test", "zz-case", "zz-specialty").guidelines()
    if not rows:  # the fixture packs list no guideline, so no own value can be found: the control skips
        return OwnContext()
    return OwnContext(query={"code": str(rows[0]["code"]), "date": str(rows[0]["in_force_from"])})


@_own("PUT", _CASE + "/pages/{page_id}/duplicate")
def _b_duplicate(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """Pages 1 and 2 get an image path (a page without one cannot be named as the original)."""
    admin.execute(
        "update app.pages set image_path = 'zz/' || id::text where bundle_id = (select bundle_id from app.cases"
        " where id = %s) and bundle_page_no in (1, 2) and image_path is null",
        (t.case,),
    )
    return OwnContext(body={"copy_of_page_no": 2})


@_own("DELETE", _CASE + "/pages/{page_id}/quarantine")
def _b_release(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """The page is quarantined (suggested), so releasing it has something to release."""
    admin.execute(
        "insert into app.page_quarantines (case_id, bundle_id, page_id, reason, suggested_by)"
        " select %s, bundle_id, id, 'zz_reason', %s from app.pages where id = %s"
        " and not exists (select 1 from app.page_quarantines q where q.page_id = %s"
        " and q.status in ('suggested', 'confirmed'))",
        (t.case, t.expert, t.ids["page"], t.ids["page"]),
    )
    return OwnContext()


@_own("POST", _CASE + "/pages/{page_id}/replace")
def _b_replace(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    """A document of the case other than the page's own, not yet used as a clearer copy."""
    doc = _one(
        admin,
        "select d.id from app.documents d where d.bundle_id = (select bundle_id from app.cases where id = %s)"
        " and not exists (select 1 from app.page_replacements r where r.document_id = d.id)"
        " and d.id <> (select document_id from app.pages where id = %s) order by d.created_at limit 1",
        t.case,
        t.ids["page"],
    )
    return OwnContext(body={"document_id": str(doc)})


@_own("POST", _CASE + "/questions/{question_id}/opinion/lint")
def _b_lint(admin: psycopg.Connection, seed: Seed, t: Tenant) -> OwnContext:
    return OwnContext(path={"question_id": t.ids["opinion_question"]})


def own_context(admin: psycopg.Connection, seed: Seed, tenant: Tenant, method: str, path: str) -> OwnContext:
    builder = OWN_CONTEXT.get((method, path))
    return builder(admin, seed, tenant) if builder else OwnContext()


# --- fixtures --------------------------------------------------------------------------------------------------------

Sender = Callable[..., Awaitable[httpx.Response]]


@pytest.fixture(scope="module")
async def iso_app(api_dsn: str, auth_env: dict[str, str]) -> AsyncIterator[FastAPI]:
    from app.core.auth.supabase import SupabaseAuth
    from app.core.config import auth_settings
    from app.core.crypto import CaseCrypto, LocalKeyProvider
    from app.core.db import Database
    from app.core.email import OutboxMailer
    from app.main import create_app
    from tests.support.files import local_storage
    from tests.support.packs import FIXTURE

    database = Database(api_dsn, max_size=4)
    await database.open()
    storage = await local_storage(auth_env)  # this checkout's own local Supabase storage, as the upload tests use
    try:
        yield create_app(  # the cell of the fixture packs, so creating a case offers the fixture combination
            database=database,
            region=str(FIXTURE.raw("jurisdiction", "pack.yaml")["cell"]),
            auth=SupabaseAuth(auth_settings(auth_env)),
            mailer=OutboxMailer(),
            storage=storage,
            crypto=CaseCrypto(LocalKeyProvider.generate()),
        )
    finally:
        await storage.aclose()
        await database.close()


@pytest.fixture(scope="module")
def iso_seed(admin: psycopg.Connection) -> Seed:
    return Seed(admin)


@pytest.fixture(scope="module")
def iso_world(admin: psycopg.Connection, iso_seed: Seed) -> IsolationWorld:
    return build_isolation_world(admin, iso_seed)


@pytest.fixture(scope="module")
def iso_send(iso_app: FastAPI, iso_seed: Seed) -> Sender:
    """`await iso_send(person, "GET", "/v1/...", json=..., params=..., headers=..., aal=..., mfa_age=...)`.

    A fresh session for the person each time; 5xx responses come back as responses, not exceptions; a request
    that has not answered after `timeout` seconds raises TimeoutError (a stream that opened and never closed)."""

    async def send(
        person: uuid.UUID,
        method: str,
        url: str,
        *,
        json: Any = None,
        content: bytes | None = None,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        aal: str = "aal2",
        mfa_age: timedelta = timedelta(0),
        timeout: float = 20.0,
    ) -> httpx.Response:
        session = iso_seed.session(person, aal=aal, mfa_age=mfa_age)
        sent = {
            "cookie": cookie_header(session),
            CSRF_HEADER: session.csrf,
            "origin": APP_ORIGIN,
            "if-match": '"1"',
            "idempotency-key": f"iso-{uuid.uuid4().hex}",
            "x-chunk-sha256": "0" * 64,
            **(headers or {}),
        }
        transport = httpx.ASGITransport(app=iso_app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="https://test", headers=sent) as client:
            with anyio.fail_after(timeout):
                return await client.request(method, url, json=json, content=content, params=params)

    return send


_NOT_FINGERPRINTED = {"ai_calls", "ai_request_slots", "audit_log", "idempotency_keys"}  # metadata about requests
_PERSON_TABLES = (
    "sessions",
    "notification_preferences",
    "expert_roles",
    "expert_training",
    "declarations",
    "signatures",
    "check_config",
    "email_changes",
    "mfa_recovery_codes",
    "legal_agreements",
    "experts",
)
_ORG_TABLES = ("memberships", "invites")
_fingerprint_sql: dict[uuid.UUID, str] = {}


def _column_tables(admin: psycopg.Connection, column: str) -> list[str]:
    rows = admin.execute(
        "select c.table_name from information_schema.columns c join information_schema.tables t"
        " on t.table_schema = c.table_schema and t.table_name = c.table_name and t.table_type = 'BASE TABLE'"
        " where c.table_schema = 'app' and c.column_name = %s order by 1",
        (column,),
    ).fetchall()
    return [r[0] for r in rows]


def victim_fingerprint(admin: psycopg.Connection, tenant: Tenant) -> dict[str, str]:
    """A hash per table of everything the tenant owns (its case, bundle, organisation, people and sessions), so a
    test can name the tables a write changed. Request metadata (audit, AI calls, idempotency keys) is left out."""
    if tenant.case not in _fingerprint_sql:
        case, org = f"'{tenant.case}'", f"'{tenant.org}'"
        people = ", ".join(f"'{p}'" for p in tenant.people.values())
        bundle = f"(select bundle_id from app.cases where id = {case})"
        pages = f"(select id from app.pages where bundle_id = {bundle})"
        conds: dict[str, str] = {}
        for t in _column_tables(admin, "bundle_id"):
            conds[t] = f"id = {case}" if t == "cases" else f"bundle_id = {bundle}"
        for t in _column_tables(admin, "page_id"):
            conds.setdefault(t, f"page_id in {pages}")
        for t in _column_tables(admin, "case_id"):
            conds[t] = f"case_id = {case}"
        for t in _PERSON_TABLES:
            col = "expert_id" if t in ("expert_roles", "expert_training", "declarations", "signatures") else "person_id"
            conds.setdefault(t, f"{col} in ({people})")
        for t in _ORG_TABLES:
            conds.setdefault(t, f"organisation_id = {org}")
        conds["organisations"] = f"id = {org}"
        conds["people"] = f"id in ({people})"
        conds["billing_profiles"] = f"owner_id in ({people}, {org})"
        for t in _NOT_FINGERPRINTED:
            conds.pop(t, None)
        _fingerprint_sql[tenant.case] = " union all ".join(
            f"select '{t}', md5(coalesce(string_agg(x.r, '|' order by x.r), '')) from"
            f" (select r::text r from app.{t} r where {cond}) x"
            for t, cond in sorted(conds.items())
        )
    return {t: h for t, h in admin.execute(_fingerprint_sql[tenant.case]).fetchall()}


def changed_tables(before: dict[str, str], after: dict[str, str], ignore: tuple[str, ...] = ()) -> list[str]:
    return sorted(t for t in before if t not in ignore and before[t] != after.get(t))


def leaked(response: httpx.Response, tenant: Tenant) -> bool:
    """True when anything that identifies the tenant (its marker or a member's email) is in the response body."""
    return any(m.encode() in response.content for m in tenant.markers)
