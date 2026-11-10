"""Two tenants side by side, for the cross-tenant authorisation tests (`tests/security/`).

A tenant is a team organisation with all five organisation roles, one case named to its expert (the
assistant and the viewer are case members; the owner and the admin are not), and one row of every kind of
case object, with its id kept. The VICTIM and the ATTACKER are built the same way. An OUTSIDER has only a
personal organisation, and a STAFF person is Avodeck support staff with no grant on any case.

Every plain-text field that could carry a name is set to the tenant's unique marker (a uuid hex), so a test
can tell whether anything of the victim reached the attacker by searching the response for the marker.
Everything here is obviously synthetic; nothing resembles a patient record. Rows go in as the local admin
role, like the other `tests/support` builders.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import psycopg

from tests.support.case_rows import SEEDERS, CaseWorld
from tests.support.seed import Seed

ORG_ROLES = ("owner", "admin", "expert", "assistant", "viewer")
# Flags in front of the staff-only routes (`routes_support.py` custody transfers; `core/routes/support.py` job resume
# and requeue).
SUPPORT_FLAGS = ("support_custody_transfer", "support_job_resume", "support_job_requeue")


@dataclass(frozen=True)
class Tenant:
    name: str
    marker: str
    org: uuid.UUID
    people: dict[str, uuid.UUID]  # org role -> person
    memberships: dict[str, uuid.UUID]  # org role -> memberships.id
    case: uuid.UUID
    case_members: dict[str, uuid.UUID]  # case role (assistant, viewer) -> case_members.id
    page_no: int
    ids: dict[str, str]  # kind of object -> id, as text (see `KINDS_SOUGHT` for the kinds)
    missing: tuple[str, ...]  # kinds no row could be found for

    @property
    def expert(self) -> uuid.UUID:
        return self.people["expert"]

    @property
    def emails(self) -> tuple[str, ...]:
        return tuple(f"{p.hex[:12]}@example.test" for p in self.people.values())

    @property
    def markers(self) -> tuple[str, ...]:
        return (self.marker, *self.emails)


@dataclass(frozen=True)
class IsolationWorld:
    victim: Tenant
    attacker: Tenant
    outsider: uuid.UUID
    staff: uuid.UUID
    stamped: dict[str, int] = field(default_factory=dict)  # table.column -> rows the marker went into


def _bundle(case: uuid.UUID) -> str:
    return "(select bundle_id from app.cases where id = %(case)s)"


# kind -> SQL (with %(case)s) returning the id of one row of that kind in the tenant's case.
KINDS_SOUGHT: dict[str, str] = {
    "document": f"select id from app.documents where bundle_id = {_bundle(uuid.UUID(int=0))} order by created_at limit 1",  # noqa: E501
    "page": f"select id from app.pages where bundle_id = {_bundle(uuid.UUID(int=0))} and bundle_page_no = 1",
    "fact": "select id from app.facts where case_id = %(case)s order by created_at limit 1",
    "question": "select id from app.questions where case_id = %(case)s order by created_at limit 1",
    "report_version": "select id from app.report_versions where case_id = %(case)s and status = 'draft'"
    " order by version_no limit 1",
    "served_version": "select id from app.report_versions where case_id = %(case)s and status = 'served'"
    " order by version_no limit 1",
    "serving": "select id from app.report_servings where case_id = %(case)s order by created_at limit 1",
    "opinion_question": "select question_id from app.opinions where case_id = %(case)s limit 1",
    "spot_check": "select id from app.spot_checks where case_id = %(case)s limit 1",
    "invoice": "select id from app.invoices where case_id = %(case)s order by created_at limit 1",
    "credit_note": "select id from app.credit_notes where case_id = %(case)s limit 1",
    "meeting": "select id from app.expert_meetings where case_id = %(case)s limit 1",
    "agenda_item": "select id from app.agenda_items where case_id = %(case)s limit 1",
    "court_bundle": "select id from app.court_bundles where case_id = %(case)s limit 1",
    "concordance": "select id from app.concordance where case_id = %(case)s limit 1",
    "offline_package": "select id from app.offline_packages where case_id = %(case)s limit 1",
    "trial_note": "select id from app.trial_notes where case_id = %(case)s limit 1",
    "deadline": "select id from app.deadlines where case_id = %(case)s limit 1",
    "hearing": "select id from app.hearings where case_id = %(case)s limit 1",
    "written_question": "select id from app.written_questions where case_id = %(case)s limit 1",
    "report_change": "select id from app.report_changes where case_id = %(case)s limit 1",
    "opposing_document": "select document_id from app.opposing_reports where case_id = %(case)s limit 1",
    "opposing_claim": "select id from app.opposing_claims where case_id = %(case)s limit 1",
    "missing_item": "select id from app.missing_items where case_id = %(case)s limit 1",
    "legal_hold": "select id from app.legal_holds where case_id = %(case)s limit 1",
    "job": "select id from app.jobs where case_id = %(case)s order by created_at limit 1",
    "upload": "select id from app.uploads where case_id = %(case)s order by created_at limit 1",
    "party": "select id from app.case_parties where case_id = %(case)s limit 1",
    "instructing_party": "select id from app.case_instructing_parties where case_id = %(case)s limit 1",
    "case_link": "select id from app.case_links where case_id = %(case)s limit 1",
    "prefill_suggestion": "select id from app.prefill_suggestions where case_id = %(case)s limit 1",
    "comment": "select id from app.comments where case_id = %(case)s limit 1",
    "finding": "select id from app.findings where case_id = %(case)s limit 1",
    "fact_link": "select id from app.fact_question_links where case_id = %(case)s limit 1",
    "witness_claim": "select id from app.witness_claims where case_id = %(case)s limit 1",
    "lint_issue": "select id from app.lint_issues where case_id = %(case)s limit 1",
    "opinion": "select id from app.opinions where case_id = %(case)s limit 1",
    "point": "select id from app.points where case_id = %(case)s limit 1",
    "relevant_fact": "select id from app.relevant_facts where case_id = %(case)s limit 1",
    "assumption": "select id from app.assumptions where case_id = %(case)s limit 1",
    "instruction_version": "select id from app.instruction_versions where case_id = %(case)s limit 1",
}

# Plain-text columns that can carry a name or a label; each gets the marker. Many of these rows are locked by a
# trigger ("a question keeps its exact words"), so the statements run with user triggers off (the way a database
# administrator would plant test data); a statement that fails is an error, and `IsolationWorld.stamped` says how
# many rows each column got (a test asserts it is above zero for every column listed here).
_STAMPS = {
    "cases.label": "update app.cases set label = %(m)s where id = %(case)s",
    "documents.filename": "update app.documents set filename = %(m)s where bundle_id = %(bundle)s",
    "uploads.filename": "update app.uploads set filename = %(m)s where case_id = %(case)s",
    "questions.text_verbatim": "update app.questions set text_verbatim = %(m)s where case_id = %(case)s",
    "facts.value": "update app.facts set value = %(m)s where case_id = %(case)s",
    "report_versions.display_name": "update app.report_versions set display_name = %(m)s where case_id = %(case)s",
    "case_instructing_parties.name": "update app.case_instructing_parties set name = %(m)s, firm = %(m)s"
    " where case_id = %(case)s",
    "comments.body": "update app.comments set body = %(m)s where case_id = %(case)s",
    "consultation_notes.body": "update app.consultation_notes set body = %(m)s where case_id = %(case)s",
    "court_directions.text": "update app.court_directions set text = %(m)s where case_id = %(case)s",
    "hearings.venue": "update app.hearings set venue = %(m)s where case_id = %(case)s",
    "assumptions.text_verbatim": "update app.assumptions set text_verbatim = %(m)s where case_id = %(case)s",
    "findings.reason": "update app.findings set reason = %(m)s where case_id = %(case)s",
    "points.reason": "update app.points set reason = %(m)s where case_id = %(case)s",
    # `legal_holds.reason` is a fixed code (check constraint `legal_holds_reason_code`), so it cannot carry a marker.
    "billing_profiles.legal_name": "update app.billing_profiles set legal_name = %(m)s where owner_id = %(expert)s",
    "organisations.name": "update app.organisations set name = %(m)s where id = %(org)s",
    # A person's name is built from the marker in `build_tenant`; this counts the rows that carry it.
    "people.full_name": "update app.people set full_name = full_name where id = any(%(people)s)",
}


def build_tenant(admin: psycopg.Connection, seed: Seed, name: str) -> tuple[Tenant, dict[str, int]]:
    marker = uuid.uuid4().hex
    people = {role: seed.person(full_name=f"{name} {role} {marker}") for role in ORG_ROLES}
    org = seed.org(kind="team", members=dict.fromkeys(people.values(), "viewer"))  # replaced just below
    admin.execute("delete from app.memberships where organisation_id = %s", (org,))
    memberships = {role: seed.membership(org, person, role) for role, person in people.items()}
    case = seed.case(org, people["expert"])
    case_members = {
        role: seed.case_member(case, people[role], role, added_by=people["expert"]) for role in ("assistant", "viewer")
    }
    world = CaseWorld(case, people["expert"], org)
    for seeder in SEEDERS:
        seeder(admin, seed, world)
    # Walk the case to `active` as the state machine allows (draft -> processing -> active).
    for step in ("processing", "active"):
        try:
            seed.case_status(case, step)
        except psycopg.Error:
            break
    # An active grant to one OTHER staff member, so the grant id exists but gives no one else any access.
    granted_staff = seed.person(full_name=f"{name} granted staff")
    seed.org(kind="personal", members={granted_staff: "owner"})
    seed.support_staff(granted_staff)
    seed.support_grant(case, people["expert"], granted_to=granted_staff)
    admin.execute(
        "insert into app.invites (organisation_id, email, role, invited_by, token_hash)"
        " values (%s, %s, 'viewer', %s, %s)",
        (org, f"invitee-{marker[:8]}@example.test", people["owner"], uuid.uuid4().hex * 2),
    )
    admin.execute("update app.questions set status = 'confirmed' where case_id = %s", (case,))  # opinion routes need it
    stamped: dict[str, int] = {}
    params = {
        "m": marker,
        "case": case,
        "bundle": admin.execute("select bundle_id from app.cases where id = %s", (case,)).fetchone()[0],  # type: ignore[index]
        "expert": people["expert"],
        "org": org,
        "people": list(people.values()),
    }
    admin.execute("set session_replication_role = replica")  # user triggers off for the planting only
    try:
        for column, statement in _STAMPS.items():
            stamped[column] = admin.execute(statement, params).rowcount
    finally:
        admin.execute("reset session_replication_role")
    ids: dict[str, str] = {}
    missing: list[str] = []
    for kind, query in KINDS_SOUGHT.items():
        row = admin.execute(query.replace("%(case)s", "%(case)s"), {"case": case}).fetchone()
        if row is None:
            missing.append(kind)
        else:
            ids[kind] = str(row[0])
    for kind, query in {
        "notification": "select id from app.notifications where person_id = %s limit 1",
        "role": "select id from app.expert_roles where expert_id = %s limit 1",
        "training": "select id from app.expert_training where expert_id = %s limit 1",
        "support_grant": "select id from app.support_access_grants where case_id = %s limit 1",
        "invite": "select id from app.invites where organisation_id = %s limit 1",
        "template": "select id from app.report_templates where owner_type = 'organisation' and owner_id = %s limit 1",
    }.items():
        arg = {"support_grant": case, "invite": org, "template": org}.get(kind, people["expert"])
        if kind == "template":
            _org_template(admin, org, marker)
        if kind in ("role", "training"):
            _expert_profile_rows(admin, people["expert"], marker)
        row = admin.execute(query, (arg,)).fetchone()
        if row is None:
            missing.append(kind)
        else:
            ids[kind] = str(row[0])
    ids["session"] = str(seed.session(people["expert"]).id)
    page_no = admin.execute("select bundle_page_no from app.pages where id = %s", (ids["page"],)).fetchone()
    assert page_no is not None
    tenant = Tenant(
        name=name,
        marker=marker,
        org=org,
        people=people,
        memberships=memberships,
        case=case,
        case_members=case_members,
        page_no=int(page_no[0]),
        ids=ids,
        missing=tuple(missing),
    )
    return tenant, stamped


def _org_template(admin: psycopg.Connection, org: uuid.UUID, marker: str) -> None:
    """One report template owned by the organisation (what an organisation's policy can require)."""
    if admin.execute("select 1 from app.report_templates where owner_id = %s", (org,)).fetchone() is None:
        admin.execute(
            "insert into app.report_templates"
            " (owner_type, owner_id, filename, storage_path, sha256, size_bytes, version)"
            " values ('organisation', %s, %s, %s, %s, 1, 1)",
            (org, f"{marker}.docx", f"zz/{marker}", "c" * 64),
        )


def _expert_profile_rows(admin: psycopg.Connection, expert: uuid.UUID, marker: str) -> None:
    if admin.execute("select 1 from app.expert_roles where expert_id = %s", (expert,)).fetchone() is None:
        admin.execute(
            "insert into app.expert_roles (expert_id, specialty, role_title, period)"
            " values (%s, %s, %s, daterange('2020-01-01', '2021-01-01'))",
            (expert, "zz-test", marker),
        )
    if admin.execute("select 1 from app.expert_training where expert_id = %s", (expert,)).fetchone() is None:
        admin.execute("insert into app.expert_training (expert_id, title) values (%s, %s)", (expert, marker))


def build_isolation_world(admin: psycopg.Connection, seed: Seed) -> IsolationWorld:
    victim, stamped = build_tenant(admin, seed, "Victim")
    attacker, _ = build_tenant(admin, seed, "Attacker")
    outsider = seed.person(full_name="Outsider")
    seed.org(kind="personal", members={outsider: "owner"})
    staff = seed.person(full_name="Staff")
    seed.org(kind="personal", members={staff: "owner"})
    seed.support_staff(staff)
    # The staff routes sit behind a feature flag that answers 404 while it is off. With the flag on for everyone
    # who is tested, a 404 for a non-staff person can only come from the staff check.
    for person in (*victim.people.values(), *attacker.people.values(), outsider, staff):
        for key in SUPPORT_FLAGS:
            seed.flag(key, "person", person)
    return IsolationWorld(victim=victim, attacker=attacker, outsider=outsider, staff=staff, stamped=stamped)
