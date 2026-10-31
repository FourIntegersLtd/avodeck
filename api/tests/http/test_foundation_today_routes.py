"""GET /v1/today (screen 03, architecture 5.3 and 8.3).

- Case cards for every open case the person can see, from all their organisations, with the
  organisation's name for its badge; closed cases are only counted; deleted ones never appear.
- "Things that need you": items each slice registers through `NeedsYouItem` providers, ranked;
  items for cases the person cannot see are dropped; a provider that fails is skipped, not fatal.
- `organisation_id` filters cards, items and the closed count to one organisation.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from psycopg import AsyncConnection

from app.core.contracts.today import CaseCard, NeedsYouItem, NeedsYouRegistry
from app.core.db import Database
from tests.support.db import ClientFor
from tests.support.files import build_app
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

CARD_FIELDS = {
    "case_id",
    "label",
    "organisation_id",
    "organisation_name",
    "organisation_kind",
    "status",
    "stage",
    "due_date",
    "party_role",
    "jurisdiction",
    "case_type",
    "specialty",
    "your_role",
    "on_hold",
}

REGISTRY = NeedsYouRegistry()
NOW = datetime(2026, 10, 17, 9, tzinfo=UTC)


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str]) -> FastAPI:
    return build_app(api_db, auth_env, needs_you=REGISTRY)


@pytest.fixture
def world(seed: Seed) -> dict[str, uuid.UUID]:
    p = {n: seed.person() for n in ("expert", "other")}
    p["personal"] = seed.org(kind="personal", members={p["expert"]: "owner"})
    p["team"] = seed.org(kind="team", members={p["expert"]: "expert", p["other"]: "owner"})
    p["mine"] = seed.case(p["personal"], p["expert"], status="active")
    p["team_case"] = seed.case(p["team"], p["other"], members={p["expert"]: "co_expert"})
    p["settled"] = seed.case(p["personal"], p["expert"], status="settled")
    p["deleted"] = seed.case(p["personal"], p["expert"], status="deleted")
    p["hidden"] = seed.case(p["team"], p["other"])
    return p


@pytest.fixture(autouse=True)
def clear_registry() -> None:
    REGISTRY.providers.clear()


async def test_signed_out_is_401(client_for: ClientFor) -> None:
    assert (await (await client_for(None)).get("/v1/today")).status_code == 401


async def test_today_lists_open_cases_from_every_organisation_and_counts_closed_ones(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    resp = await (await client_for(world["expert"])).get("/v1/today")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    cards = {c["case_id"]: c for c in body["cases"]}
    assert set(cards) == {str(world["mine"]), str(world["team_case"])}
    assert all(set(c) >= CARD_FIELDS for c in cards.values())
    mine, team = cards[str(world["mine"])], cards[str(world["team_case"])]
    assert (mine["status"], mine["your_role"], mine["organisation_kind"]) == ("active", "named_expert", "personal")
    assert (team["your_role"], team["organisation_id"]) == ("co_expert", str(world["team"]))
    assert team["organisation_name"].startswith("Org ")
    assert body["closed_count"] == 1
    assert body["needs_you"] == []
    orgs = {o["id"] for o in body["organisations"]}
    assert orgs == {str(world["personal"]), str(world["team"])}


async def test_registered_items_are_ranked_and_only_for_cases_shown(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    @REGISTRY.provider("zz_reading")
    async def reading(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
        return [
            NeedsYouItem(
                kind="zz_pages_left",
                case_id=card.case_id,
                rank=20,
                label_key="today.needs_you.zz_pages_left",
                action_key="today.action.carry_on_reading",
                screen="S09",
                count=127,
                minutes=40,
            )
            for card in cards
        ]

    @REGISTRY.provider("zz_deadlines")
    async def deadlines(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
        return [
            NeedsYouItem(
                kind="zz_due",
                case_id=world["mine"],
                rank=10,
                due_at=NOW + timedelta(days=7),
                label_key="today.needs_you.zz_due",
                action_key="today.action.prepare",
                screen="S22",
                params={"days": 7},
            ),
            NeedsYouItem(
                kind="zz_due",
                case_id=world["hidden"],
                rank=1,
                label_key="today.needs_you.zz_due",
                action_key="today.action.prepare",
                screen="S22",
            ),
        ]

    resp = await (await client_for(world["expert"])).get("/v1/today")
    assert resp.status_code == 200, resp.text
    items = resp.json()["needs_you"]
    assert [(i["kind"], i["case_id"]) for i in items][0] == ("zz_due", str(world["mine"]))
    assert str(world["hidden"]) not in {i["case_id"] for i in items}
    assert {i["case_id"] for i in items if i["kind"] == "zz_pages_left"} == {
        str(world["mine"]),
        str(world["team_case"]),
    }
    assert items[0]["params"] == {"days": 7}
    assert [i["rank"] for i in items] == sorted(i["rank"] for i in items)


async def test_a_failing_provider_is_skipped(client_for: ClientFor, world: dict[str, uuid.UUID]) -> None:
    @REGISTRY.provider("zz_broken")
    async def broken(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
        await conn.execute("select * from app.no_such_table")
        return []

    @REGISTRY.provider("zz_fine")
    async def fine(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
        cur = await conn.execute("select count(*) from app.cases where id = %s", (world["hidden"],))
        row = await cur.fetchone()
        assert row is not None
        return [
            NeedsYouItem(
                kind="zz_fine",
                case_id=cards[0].case_id,
                rank=5,
                label_key="today.needs_you.zz_fine",
                action_key="today.action.open",
                screen="S03",
                count=row[0],
            )
        ]

    resp = await (await client_for(world["expert"])).get("/v1/today")
    assert resp.status_code == 200, resp.text
    items = resp.json()["needs_you"]
    assert [i["kind"] for i in items] == ["zz_fine"]
    assert items[0]["count"] == 0, "providers read through row-level security as the person"


async def test_the_organisation_filter_narrows_cards_items_and_closed_count(
    client_for: ClientFor, world: dict[str, uuid.UUID]
) -> None:
    @REGISTRY.provider("zz_every")
    async def every(conn: AsyncConnection, cards: list[CaseCard]) -> list[NeedsYouItem]:
        return [
            NeedsYouItem(
                kind="zz_every",
                case_id=c,
                rank=1,
                label_key="today.needs_you.zz_every",
                action_key="today.action.open",
                screen="S03",
            )
            for c in (world["mine"], world["team_case"])
        ]

    client = await client_for(world["expert"])
    body = (await client.get("/v1/today", params={"organisation_id": str(world["team"])})).json()
    assert [c["case_id"] for c in body["cases"]] == [str(world["team_case"])]
    assert [i["case_id"] for i in body["needs_you"]] == [str(world["team_case"])]
    assert body["closed_count"] == 0
    body = (await client.get("/v1/today", params={"organisation_id": str(uuid.uuid4())})).json()
    assert body["cases"] == [] and body["needs_you"] == [] and body["closed_count"] == 0


# --- server-side search, counts and "due this month" (rule 4: the browser renders only) ---


def _label(seed: Seed, case: uuid.UUID, label: str, due: str | None = None) -> None:
    seed.admin.execute("update app.cases set label = %s, due_date = %s where id = %s", (label, due, case))


@pytest.fixture
def named(world: dict[str, uuid.UUID], seed: Seed) -> dict[str, uuid.UUID]:
    _label(seed, world["mine"], "Mrs Anand 100% claim")
    _label(seed, world["team_case"], "Mr Brown")
    _label(seed, world["hidden"], "Anand secret")
    return world


async def test_q_matches_a_label_substring_ignoring_case(client_for: ClientFor, named: dict[str, uuid.UUID]) -> None:
    client = await client_for(named["expert"])
    body = (await client.get("/v1/today", params={"q": "aNaN"})).json()
    assert [c["case_id"] for c in body["cases"]] == [str(named["mine"])]
    assert body["open_count"] == 1
    body = (await client.get("/v1/today", params={"q": "nobody"})).json()
    assert body["cases"] == [] and body["open_count"] == 0


async def test_q_never_returns_another_persons_case(client_for: ClientFor, named: dict[str, uuid.UUID]) -> None:
    body = (await (await client_for(named["expert"])).get("/v1/today", params={"q": "secret"})).json()
    assert body["cases"] == [] and body["open_count"] == 0
    owner = (await (await client_for(named["other"])).get("/v1/today", params={"q": "secret"})).json()
    assert [c["case_id"] for c in owner["cases"]] == [str(named["hidden"])]


@pytest.mark.parametrize(("q", "hits"), [("%", 1), ("_", 0), ("\\", 0), ("0% c", 1), ("M_s", 0)])
async def test_q_treats_wildcards_as_plain_text(
    client_for: ClientFor, named: dict[str, uuid.UUID], q: str, hits: int
) -> None:
    body = (await (await client_for(named["expert"])).get("/v1/today", params={"q": q})).json()
    assert len(body["cases"]) == hits


async def test_q_is_limited_to_200_characters(client_for: ClientFor, named: dict[str, uuid.UUID]) -> None:
    client = await client_for(named["expert"])
    assert (await client.get("/v1/today", params={"q": "a" * 201})).status_code == 422
    assert (await client.get("/v1/today", params={"q": "a" * 200})).status_code == 200


async def test_organisation_and_q_combine_and_counts_come_from_the_server(
    client_for: ClientFor, named: dict[str, uuid.UUID]
) -> None:
    client = await client_for(named["expert"])
    chips = {str(named["personal"]): 1, str(named["team"]): 1}
    body = (await client.get("/v1/today")).json()
    assert body["open_count"] == 2
    assert {o["id"]: o["open_count"] for o in body["organisations"]} == chips
    both = (await client.get("/v1/today", params={"organisation_id": str(named["team"]), "q": "anand"})).json()
    assert both["cases"] == [] and both["open_count"] == 0
    # chip counts ignore q so the chips do not jump while typing
    assert {o["id"]: o["open_count"] for o in both["organisations"]} == chips
    one = (await client.get("/v1/today", params={"organisation_id": str(named["team"]), "q": "brown"})).json()
    assert [c["case_id"] for c in one["cases"]] == [str(named["team_case"])] and one["open_count"] == 1
    assert one["closed_count"] == 0
    assert (await client.get("/v1/today", params={"q": "zzz"})).json()["closed_count"] == 1


def _first_of_month(zone: str, months: int = 0) -> str:
    day = datetime.now(ZoneInfo(zone)).date().replace(day=1)
    for _ in range(abs(months)):
        day = (day + timedelta(days=32)).replace(day=1) if months > 0 else (day - timedelta(days=1)).replace(day=1)
    return day.isoformat()


@pytest.mark.parametrize("zone", ["UTC", "Pacific/Kiritimati", "Etc/GMT+12"])
async def test_due_this_month_is_the_persons_local_month(
    client_for: ClientFor, named: dict[str, uuid.UUID], seed: Seed, zone: str
) -> None:
    seed.admin.execute("update app.people set timezone = %s where id = %s", (zone, named["expert"]))
    client = await client_for(named["expert"])
    for months, expected in ((0, 1), (1, 0), (-1, 0)):
        _label(seed, named["mine"], "Due", _first_of_month(zone, months))
        assert (await client.get("/v1/today")).json()["due_this_month"] == expected, (zone, months)


async def test_due_this_month_ignores_q_and_counts_open_cases_only(
    client_for: ClientFor, named: dict[str, uuid.UUID], seed: Seed
) -> None:
    _label(seed, named["mine"], "Due", _first_of_month("UTC"))
    seed.admin.execute("update app.cases set due_date = %s where id = %s", (_first_of_month("UTC"), named["settled"]))
    client = await client_for(named["expert"])
    assert (await client.get("/v1/today", params={"q": "brown"})).json()["due_this_month"] == 1


async def _ready(client_for: ClientFor, person: uuid.UUID) -> dict[str, object]:
    ready: dict[str, object] = (await (await client_for(person)).get("/v1/today")).json()["readiness"]
    return ready


async def test_readiness_counts_the_first_visit_steps_on_the_server(
    client_for: ClientFor, world: dict[str, uuid.UUID], seed: Seed
) -> None:
    person = world["expert"]
    assert await _ready(client_for, person) == {
        "account": True,
        "profile": False,
        "signature": False,
        "done_steps": 1,
        "total_steps": 3,
    }
    seed.admin.execute(
        "insert into app.experts (person_id, profession, registration_number, specialties)"
        " values (%s, 'zz_profession', 'ZZ1', array['zz_specialty'])",
        (person,),
    )
    assert (await _ready(client_for, person))["done_steps"] == 2
    seed.admin.execute(
        "insert into app.signatures (expert_id, method, storage_path, sha256) values (%s, 'drawn', 'zz/path', %s)",
        (person, "0" * 64),
    )
    ready = await _ready(client_for, person)
    assert (ready["profile"], ready["signature"], ready["done_steps"], ready["total_steps"]) == (True, True, 3, 3)


async def test_an_incomplete_profile_is_not_ready(
    client_for: ClientFor, world: dict[str, uuid.UUID], seed: Seed
) -> None:
    seed.admin.execute(
        "insert into app.experts (person_id, profession, specialties) values (%s, 'zz_profession', '{}')",
        (world["expert"],),
    )
    ready = await _ready(client_for, world["expert"])
    assert (ready["profile"], ready["done_steps"]) == (False, 1)
