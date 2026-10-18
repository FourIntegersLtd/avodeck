"""Screen 15's workspace: `GET .../questions/{q_id}/workspace` (architecture 5.2; slice E brief).

It returns the verified facts linked to the question as chips, each carrying the words a citation quotes
(the browser never builds them), the guidance in force on the consultation date (slice D's guideline
table, behind a port), the points summary by side with the pack's labels, the pack's views and legal test
text, and the expert's saved opinion with its open warnings. The guidance date is `?on=` when given,
otherwise the latest event date among the chips. Anyone who can see the case content reads it; it runs
unchanged under the fixture packs.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from typing import Any

import psycopg
import pytest
from fastapi import FastAPI

from app.core.db import Database
from app.core.packs import default_library
from app.features.write.ports import Guidance
from app.features.write.services import WriteServices, configure
from tests.fixtures.write.world import PLAN, RESULT, Reads, World, app_for, build
from tests.support.contracts import add_question
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

GUIDE = Guidance(
    source="zz_body",
    code="ZZ1",
    title="Made-up guidance",
    version_label="2023 update",
    in_force_from=date(2023, 8, 1),
    in_force_to=None,
    url=None,
)


class Guidebook:
    def __init__(self) -> None:
        self.asked: list[tuple[uuid.UUID, date]] = []

    async def in_force(self, conn: Any, case_id: uuid.UUID, pack: Any, on: date) -> list[Guidance]:
        self.asked.append((case_id, on))
        return [GUIDE]


class OnlyFirst:
    def __init__(self, fact: uuid.UUID) -> None:
        self.fact = fact

    async def linked(self, conn: Any, case_id: uuid.UUID, question_id: uuid.UUID, facts: Any) -> list[Any]:
        return [f for f in facts if f.id == self.fact]


@pytest.fixture(params=BOTH, ids=["launch", "fixture"])
def combo(request: pytest.FixtureRequest) -> Combo:
    combo: Combo = request.param
    return combo


@pytest.fixture
def reads() -> Reads:
    return Reads()


@pytest.fixture
def app(api_db: Database, auth_env: dict[str, str], combo: Combo, reads: Reads) -> FastAPI:
    return app_for(api_db, auth_env, combo, reads)


@pytest.fixture
def world(seed: Seed, admin: psycopg.Connection, combo: Combo, reads: Reads) -> World:
    return build(seed, admin, combo, reads)


@pytest.fixture
def guidebook(app: FastAPI, reads: Reads) -> Iterator[Guidebook]:
    book = Guidebook()
    configure(WriteServices(crypto=app.state.crypto, reads=reads, guidance=book))
    yield book
    configure(None)


async def workspace(client: Any, w: World, query: str = "") -> Any:
    return await client.get(w.url(f"questions/{w.question}/workspace{query}"))


async def test_anyone_who_sees_the_content_reads_it_and_no_one_else(
    client_for: ClientFor, world: World, admin: psycopg.Connection, guidebook: Guidebook
) -> None:
    for person in (world.expert, world.co_expert, world.supervisor, world.assistant, world.viewer):
        assert (await workspace(await client_for(person), world)).status_code == 200
    assert (await workspace(await client_for(world.outsider), world)).status_code == 404
    unconfirmed = add_question(admin, world.case, number=2)
    expert = await client_for(world.expert)
    assert (await expert.get(world.url(f"questions/{unconfirmed}/workspace"))).status_code == 404


async def test_chips_are_the_citable_facts_linked_to_the_question_with_their_own_words(
    client_for: ClientFor, world: World, guidebook: Guidebook
) -> None:
    expert = await client_for(world.expert)
    got = (await workspace(expert, world)).json()
    chips = {c["fact_id"]: c for c in got["chips"]}
    assert set(chips) == {str(world.verified), str(world.result)}
    assert {c["quote_verbatim"] for c in chips.values()} == {PLAN, RESULT}
    assert all(c["page_no"] == 1 and c["page_id"] == str(world.read_page) for c in chips.values())
    chip = chips[str(world.verified)]
    ref = {k: chip[k] for k in ("fact_id", "page_id", "quote_verbatim")}
    payload = {"body": "The plan [[cite:1]] was made.", "citation_refs": [ref], "view": None, "provisional": False}
    saved = await expert.put(
        world.url(f"questions/{world.question}/opinion"), json=payload, headers={"If-Match": '"0"'}
    )
    assert saved.status_code == 200, saved.text


async def test_only_facts_the_links_port_returns_become_chips(
    client_for: ClientFor, world: World, app: FastAPI, reads: Reads
) -> None:
    configure(WriteServices(crypto=app.state.crypto, reads=reads, links=OnlyFirst(world.verified)))
    try:
        got = (await workspace(await client_for(world.expert), world)).json()
    finally:
        configure(None)
    assert [c["fact_id"] for c in got["chips"]] == [str(world.verified)]


async def test_guidance_is_asked_for_the_latest_event_date_or_the_date_given(
    client_for: ClientFor, world: World, admin: psycopg.Connection, guidebook: Guidebook
) -> None:
    expert = await client_for(world.expert)
    got = (await workspace(expert, world)).json()
    assert (got["guidance_on"], got["guidance"], guidebook.asked) == (None, [], [])

    admin.execute("update app.facts set event_date = %s where id = %s", (date(2024, 5, 15), world.verified))
    admin.execute("update app.facts set event_date = %s where id = %s", (date(2024, 2, 10), world.result))
    got = (await workspace(expert, world)).json()
    assert got["guidance_on"] == "2024-05-15"
    assert got["guidance"] == [GUIDE.model_dump(mode="json")]
    assert guidebook.asked == [(world.case, date(2024, 5, 15))]

    got = (await workspace(expert, world, "?on=2024-03-01")).json()
    assert got["guidance_on"] == "2024-03-01"
    assert guidebook.asked[-1] == (world.case, date(2024, 3, 1))
    assert (await workspace(expert, world, "?on=soon")).status_code == 422


async def test_points_summary_counts_each_side_with_the_pack_labels_leaving_out_dismissed(
    client_for: ClientFor, world: World, combo: Combo, guidebook: Guidebook
) -> None:
    pack = default_library().combine(*combo.codes)
    sides = pack.point_sides()
    expert = await client_for(world.expert)
    points = world.url(f"questions/{world.question}/points")
    made = [
        (await expert.post(points, json={"fact_id": str(f), "side": sides[i]["code"]})).json()
        for f, i in ((world.verified, 0), (world.result, 1))
    ]
    dismissed = await expert.patch(
        world.url(f"points/{made[1]['id']}"),
        json={"status": "dismissed", "dismissed_reason": "not relevant"},
        headers={"If-Match": f'"{made[1]["version"]}"'},
    )
    assert dismissed.status_code == 200, dismissed.text
    got = (await workspace(expert, world)).json()
    assert got["points"]["sides"] == [
        {"code": s["code"], "label": s["label"], "count": 1 if i == 0 else 0} for i, s in enumerate(sides)
    ]
    assert got["points"]["differ"] == 0
    assert got["views"] == pack.opinion_views()
    assert got["legal_test"] == pack.legal_tests()


async def test_the_saved_opinion_comes_back_with_its_open_warnings(
    client_for: ClientFor, world: World, guidebook: Guidebook
) -> None:
    expert = await client_for(world.expert)
    assert (await workspace(expert, world)).json()["opinion"] is None
    payload: dict[str, object] = {
        "body": "Review was in 3 weeks.",
        "citation_refs": [],
        "view": None,
        "provisional": True,
    }
    saved = await expert.put(
        world.url(f"questions/{world.question}/opinion"), json=payload, headers={"If-Match": '"0"'}
    )
    assert saved.status_code == 200, saved.text
    opinion = (await workspace(expert, world)).json()["opinion"]
    assert opinion == saved.json()
    assert [i["type"] for i in opinion["lint"]] == ["uncited_fact"]
