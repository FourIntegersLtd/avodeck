"""Arranges a team, its people, two cases and signed-in sessions in the local test database; prints JSON.

Run from api/ (`poetry run python ../web/e2e/organisations/seed.py`) with TEST_DATABASE_ADMIN_URL set. The live
Playwright tests in this folder use the sessions as cookies, so they talk to the real API and database. Nothing
here is a real person or a real credential.
"""

from __future__ import annotations

import json
import os
import sys
import uuid

import psycopg

sys.path.insert(0, os.getcwd())
from tests.support.seed import Seed  # noqa: E402


def main() -> None:
    admin = psycopg.connect(os.environ["TEST_DATABASE_ADMIN_URL"], autocommit=True)
    seed = Seed(admin)
    run = uuid.uuid4().hex[:6]
    names = {"owner": "Olivia Owner", "admin": "Adam Admin", "expert": "Eve Expert", "leaver": "Lee Leaver", "staff": "Sam Support"}
    people = {k: seed.person(full_name=v) for k, v in names.items()}
    personal = {k: seed.org(kind="personal", members={people[k]: "owner"}) for k in people}
    team = seed.org(kind="team")
    admin.execute("update app.organisations set name = %s where id = %s", (f"Northgate {run}", team))
    for who, role in (("owner", "owner"), ("admin", "admin"), ("expert", "expert"), ("leaver", "expert")):
        seed.membership(team, people[who], role)
    seed.support_staff(people["staff"])
    team_case = seed.case(team, people["expert"], status="active")
    leaver_case = seed.case(team, people["leaver"], status="active")
    own_case = seed.case(personal["expert"], people["expert"], status="active")
    sessions = {}
    for who, person in people.items():
        s = seed.session(person, email=f"{who}-{run}@example.test", full_name=names[who])
        sessions[who] = {"token": s.token, "csrf": s.csrf, "person": str(person)}
    print(json.dumps({"team": str(team), "team_name": f"Northgate {run}", "team_case": str(team_case), "leaver_case": str(leaver_case), "own_case": str(own_case), "sessions": sessions}))


main()
