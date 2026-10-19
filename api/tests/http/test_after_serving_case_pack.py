"""Slice G's pack data for the web: the written-question types (S20) and the court orders screen (S29)."""

# ruff: noqa: F811
from __future__ import annotations

import pytest

from tests.fixtures.test_after_serving_world import (  # noqa: F401
    World,
    aconn,
    app,
    combo,
    mailer,
    model,
    ocr,
    pack_of,
    reads,
    services,
    storage,
    world,
)
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio


async def test_the_case_pack_lists_the_written_question_types(client_for: ClientFor, world: World) -> None:
    resp = await (await client_for(world.expert)).get(world.url("pack"))
    assert resp.status_code == 200, resp.text
    expected = pack_of(world.combo).written_question_classifications()
    assert expected and resp.json()["written_question_classifications"] == expected


async def test_the_pack_has_a_court_orders_screen(client_for: ClientFor, world: World) -> None:
    steps = (await (await client_for(world.expert)).get(world.url("pack"))).json()["workflow_steps"]
    assert any("court_orders" in step["screens"] for step in steps)
