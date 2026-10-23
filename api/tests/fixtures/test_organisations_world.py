"""Slice O's test world: one person in two organisations, "Solo practice" and "Agency".

Dr K owns her personal organisation ("Solo practice") and is an expert member of the team
organisation ("Agency"). The agency also has an owner, an admin, a second expert (Dr M), an
assistant and a viewer. An outsider owns only their own personal organisation. Every case is
created under the fixture packs (`packs/_fixtures/`). Nothing here is collected as a test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import timedelta

import psycopg
import pytest

from tests.support.seed import Seed

STALE = timedelta(minutes=11)


@dataclass
class World:
    seed: Seed
    admin: psycopg.Connection
    solo: uuid.UUID
    agency: uuid.UUID
    people: dict[str, uuid.UUID]
    memberships: dict[str, uuid.UUID] = field(default_factory=dict)

    def __getitem__(self, name: str) -> uuid.UUID:
        return self.people[name]

    def email(self, name: str) -> str:
        return f"{self.people[name].hex[:12]}@example.test"

    def team_case(
        self, named: str = "dr_k", *, status: str = "active", members: dict[str, str] | None = None
    ) -> uuid.UUID:
        return self.seed.case(
            self.agency, self.people[named], status=status,
            members={self.people[p]: r for p, r in (members or {}).items()},
        )  # fmt: skip

    def solo_case(self, *, status: str = "active") -> uuid.UUID:
        return self.seed.case(self.solo, self.people["dr_k"], status=status)

    def membership_row(self, org: uuid.UUID, name: str) -> tuple[str, bool] | None:
        row = self.admin.execute(
            "select role, ended_at is not null from app.memberships where organisation_id = %s and person_id = %s"
            " order by started_at desc limit 1",
            (org, self.people[name]),
        ).fetchone()
        return None if row is None else (row[0], row[1])


def name_org(admin: psycopg.Connection, org: uuid.UUID, name: str) -> None:
    admin.execute("update app.organisations set name = %s where id = %s", (name, org))


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection) -> World:
    names = ("dr_k", "dr_m", "owner", "admin", "assistant", "viewer", "outsider")
    people = {n: seed.person(full_name=n.replace("_", " ").title()) for n in names}
    solo = seed.org(kind="personal", members={people["dr_k"]: "owner"})
    name_org(admin, solo, "Solo practice")
    for n in ("dr_m", "owner", "admin", "assistant", "viewer", "outsider"):
        seed.org(kind="personal", members={people[n]: "owner"})
    agency = seed.org(kind="team")
    name_org(admin, agency, "Agency")
    w = World(seed, admin, solo, agency, people)
    roles = {"owner": "owner", "admin": "admin", "dr_k": "expert", "dr_m": "expert", "assistant": "assistant",
             "viewer": "viewer"}  # fmt: skip
    for n, role in roles.items():
        w.memberships[n] = seed.membership(agency, people[n], role)
    return w
