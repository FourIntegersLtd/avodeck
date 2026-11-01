"""Arrange rows directly as the local admin role (tests only).

Every case is created under the fixture packs (`packs/_fixtures/`), so a passing test cannot
depend on launch-pack words. Rows use fresh random IDs, so tests never collide and need no
clean-up between them (`supabase db reset` clears everything).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import timedelta

import psycopg

FIXTURE_PACKS = {
    "jurisdiction": "zz-test",
    "case_type": "zz-case",
    "specialty": "zz-specialty",
    "expert_role": "assessor",
}

# The only path through the case state machine used to reach a given status in tests.
CASE_PATH = {
    "draft": [],
    "processing": ["processing"],
    "active": ["processing", "active"],
    "served": ["processing", "active", "served"],
    "settled": ["processing", "active", "served", "settled"],
    "archived": ["processing", "active", "served", "settled", "archived"],
    "withdrawn": ["withdrawn"],
    "deleted": ["deleted"],
}


class Seed:
    def __init__(self, admin: psycopg.Connection) -> None:
        self.admin = admin

    def person(self, *, full_name: str = "Test Person") -> uuid.UUID:
        pid = uuid.uuid4()
        self.admin.execute(
            "insert into app.people (id, email, full_name) values (%s, %s, %s)",
            (pid, f"{pid.hex[:12]}@example.test", full_name),
        )
        return pid

    def org(self, *, kind: str = "team", members: dict[uuid.UUID, str] | None = None) -> uuid.UUID:
        oid = uuid.uuid4()
        self.admin.execute(
            "insert into app.organisations (id, name, kind, region) values (%s, %s, %s, 'zz')",
            (oid, f"Org {oid.hex[:6]}", kind),
        )
        for person, role in (members or {}).items():
            self.membership(oid, person, role)
        return oid

    def membership(self, org: uuid.UUID, person: uuid.UUID, role: str) -> uuid.UUID:
        mid = uuid.uuid4()
        self.admin.execute(
            "insert into app.memberships (id, organisation_id, person_id, role) values (%s, %s, %s, %s)",
            (mid, org, person, role),
        )
        return mid

    def end_membership(self, org: uuid.UUID, person: uuid.UUID) -> None:
        self.admin.execute(
            "update app.memberships set ended_at = now() where organisation_id = %s and person_id = %s"
            " and ended_at is null",
            (org, person),
        )

    def case_status(self, case: uuid.UUID, status: str) -> None:
        """Move a case on by one step of the case state machine."""
        self.admin.execute("update app.cases set status = %s where id = %s", (status, case))

    def close_account(self, person: uuid.UUID) -> None:
        """Scenario S15: the person closed their account."""
        self.admin.execute("update app.people set closed_at = now() where id = %s", (person,))

    def support_staff(self, person: uuid.UUID, *, removed: bool = False) -> None:
        """Avodeck support staff, as `docs/ops/support-staff.md` adds them by hand."""
        self.admin.execute(
            "insert into app.support_staff (person_id, removed_at) values (%s, case when %s then now() end)",
            (person, removed),
        )

    def support_grant(
        self,
        case: uuid.UUID,
        granted_by: uuid.UUID,
        *,
        granted_to: uuid.UUID | None = None,
        expires_in: timedelta = timedelta(hours=23),
        revoked: bool = False,
        reason: str = "support_request",
    ) -> uuid.UUID:
        """A support access grant given 24 hours before `expires_in` from now (so a negative one has expired)."""
        gid = uuid.uuid4()
        seconds = int(expires_in.total_seconds())
        self.admin.execute(
            """
            insert into app.support_access_grants (id, case_id, granted_by, granted_to, reason, granted_at,
                                                   expires_at, revoked_at)
            values (%s, %s, %s, %s, %s, now() + make_interval(secs => %s) - interval '24 hours',
                    now() + make_interval(secs => %s), case when %s then now() end)
            """,
            (gid, case, granted_by, granted_to, reason, seconds, seconds, revoked),
        )
        return gid

    def records_custodian(self, person: uuid.UUID, custodian: uuid.UUID) -> None:
        """`person` names `custodian` (by their sign-in email) as their records custodian (`PUT /me/custodian`)."""
        self.admin.execute(
            "update app.people p set records_custodian_name = 'Named Custodian', records_custodian_email = c.email"
            " from app.people c where p.id = %s and c.id = %s",
            (person, custodian),
        )

    def custody_transfer(
        self,
        case: uuid.UUID,
        staff: uuid.UUID,
        *,
        to_person: uuid.UUID | None = None,
        to_organisation: uuid.UUID | None = None,
        revoked: bool = False,
    ) -> uuid.UUID:
        """A records custody transfer recorded by support staff (architecture 10.1 "Death or incapacity")."""
        tid = uuid.uuid4()
        self.admin.execute(
            """
            insert into app.custody_transfers (id, case_id, from_person_id, to_person_id, to_organisation_id,
                                               evidence_note, transferred_by, revoked_at)
            select %s, c.id, c.named_expert_id, %s, %s, 'Evidence seen', %s, case when %s then now() end
            from app.cases c where c.id = %s
            """,
            (tid, to_person, to_organisation, staff, revoked, case),
        )
        return tid

    def case(
        self,
        org: uuid.UUID,
        named_expert: uuid.UUID,
        *,
        status: str = "draft",
        members: dict[uuid.UUID, str] | None = None,
        packs: dict[str, str] | None = None,
    ) -> uuid.UUID:
        """A case under the fixture packs unless `packs` names others (e.g. `tests.support.packs.LAUNCH_CASE`)."""
        cid = uuid.uuid4()
        self.admin.execute(
            """
            insert into app.cases (id, organisation_id, named_expert_id, jurisdiction, case_type, specialty,
                                   expert_role, label)
            values (%(id)s, %(org)s, %(expert)s, %(jurisdiction)s, %(case_type)s, %(specialty)s, %(expert_role)s,
                    'Case Z')
            """,
            {"id": cid, "org": org, "expert": named_expert, **(packs or FIXTURE_PACKS)},
        )
        for step in CASE_PATH[status]:
            self.admin.execute("update app.cases set status = %s where id = %s", (step, cid))
        for person, role in (members or {}).items():
            self.case_member(cid, person, role, added_by=named_expert)
        return cid

    def case_member(self, case: uuid.UUID, person: uuid.UUID, role: str, *, added_by: uuid.UUID) -> uuid.UUID:
        mid = uuid.uuid4()
        self.admin.execute(
            "insert into app.case_members (id, case_id, person_id, case_role, added_by) values (%s, %s, %s, %s, %s)",
            (mid, case, person, role, added_by),
        )
        return mid

    def document(self, case: uuid.UUID) -> uuid.UUID:
        did = uuid.uuid4()
        self.admin.execute(
            """
            insert into app.documents (id, bundle_id, kind, filename, sha256)
            values (%s, (select bundle_id from app.cases where id = %s), 'records', 'f.pdf', %s)
            """,
            (did, case, "0" * 64),
        )
        return did

    def page(self, case: uuid.UUID, document: uuid.UUID, number: int) -> uuid.UUID:
        pid = uuid.uuid4()
        self.admin.execute(
            """
            insert into app.pages (id, bundle_id, document_id, bundle_page_no)
            values (%s, (select bundle_id from app.cases where id = %s), %s, %s)
            """,
            (pid, case, document, number),
        )
        return pid

    def job(self, case: uuid.UUID, *, kind: str = "process_bundle", requested_by: uuid.UUID | None = None) -> uuid.UUID:
        jid = uuid.uuid4()
        self.admin.execute(
            "insert into app.jobs (id, case_id, kind, requested_by) values (%s, %s, %s, %s)",
            (jid, case, kind, requested_by),
        )
        return jid

    def notification(self, person: uuid.UUID, org: uuid.UUID, case: uuid.UUID | None = None) -> uuid.UUID:
        nid = uuid.uuid4()
        self.admin.execute(
            "insert into app.notifications (id, person_id, organisation_id, case_id, kind)"
            " values (%s, %s, %s, %s, 'bundle_ready')",
            (nid, person, org, case),
        )
        return nid

    def flag(self, key: str, scope: str, scope_id: uuid.UUID | None = None, enabled: bool = True) -> uuid.UUID:
        fid = uuid.uuid4()
        self.admin.execute(
            "insert into app.feature_flags (id, key, scope, scope_id, enabled) values (%s, %s, %s, %s, %s)",
            (fid, key, scope, scope_id, enabled),
        )
        return fid

    def session(
        self,
        person: uuid.UUID,
        *,
        email: str | None = None,
        full_name: str | None = None,
        aal: str = "aal2",
        mfa_age: timedelta | None = timedelta(0),
        idle: timedelta = timedelta(0),
        age: timedelta = timedelta(0),
        device_label: str = "Test browser",
    ) -> SeededSession:
        """A device session as sign-in would leave it, without Supabase Auth (no Auth tokens kept).

        The cookie holds `token`; the database keeps only its SHA-256, and the same for the CSRF token.
        `aal2` with `mfa_age` 0 is a fully signed-in person who has just completed MFA (step-up valid).
        """
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        sid = uuid.uuid4()
        mfa_verified = None if mfa_age is None else f"{int(mfa_age.total_seconds())} seconds"
        self.admin.execute(
            """
            insert into app.sessions (id, person_id, email, full_name, token_hash, csrf_hash, device_label, aal,
                                      mfa_enrolled, mfa_verified_at, created_at, last_seen_at)
            values (%(id)s, %(person)s, %(email)s, %(name)s, %(token)s, %(csrf)s, %(label)s, %(aal)s,
                    %(enrolled)s, now() - %(mfa)s::interval, now() - %(age)s::interval, now() - %(idle)s::interval)
            """,
            {
                "id": sid,
                "person": person,
                "email": email or f"{person.hex[:12]}@example.test",
                "name": full_name,
                "token": sha256_hex(token),
                "csrf": sha256_hex(csrf),
                "label": device_label,
                "aal": aal,
                "enrolled": aal == "aal2",
                "mfa": mfa_verified,
                "age": f"{int(max(age, idle).total_seconds())} seconds",
                "idle": f"{int(idle.total_seconds())} seconds",
            },
        )
        return SeededSession(sid, person, token, csrf)


@dataclass(frozen=True)
class SeededSession:
    id: uuid.UUID
    person_id: uuid.UUID
    token: str
    csrf: str


def sha256_hex(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()
