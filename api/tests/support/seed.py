"""Arrange rows directly as the local admin role (tests only).

Every case is created under the fixture packs (`packs/_fixtures/`), so a passing test cannot
depend on launch-pack words. Rows use fresh random IDs, so tests never collide and need no
clean-up between them (`supabase db reset` clears everything).
"""

from __future__ import annotations

import uuid

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

    def case(
        self,
        org: uuid.UUID,
        named_expert: uuid.UUID,
        *,
        status: str = "draft",
        members: dict[uuid.UUID, str] | None = None,
    ) -> uuid.UUID:
        cid = uuid.uuid4()
        self.admin.execute(
            """
            insert into app.cases (id, organisation_id, named_expert_id, jurisdiction, case_type, specialty,
                                   expert_role, label)
            values (%(id)s, %(org)s, %(expert)s, %(jurisdiction)s, %(case_type)s, %(specialty)s, %(expert_role)s,
                    'Case Z')
            """,
            {"id": cid, "org": org, "expert": named_expert, **FIXTURE_PACKS},
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
            "insert into app.documents (id, case_id, kind, filename, sha256) values (%s, %s, 'records', 'f.pdf', %s)",
            (did, case, "0" * 64),
        )
        return did

    def page(self, case: uuid.UUID, document: uuid.UUID, number: int) -> uuid.UUID:
        pid = uuid.uuid4()
        self.admin.execute(
            "insert into app.pages (id, case_id, document_id, bundle_page_no) values (%s, %s, %s, %s)",
            (pid, case, document, number),
        )
        return pid

    def job(self, case: uuid.UUID, *, kind: str = "process_bundle") -> uuid.UUID:
        jid = uuid.uuid4()
        self.admin.execute("insert into app.jobs (id, case_id, kind) values (%s, %s, %s)", (jid, case, kind))
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
