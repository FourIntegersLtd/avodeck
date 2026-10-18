"""Today (screen 03): slice D's "things to look at to decide" item (slice D brief).

On each case where the person is the named expert and open findings wait for a decision, with how
many; it opens screen 13 and goes once each is decided. A finding no longer found does not count.
Only the named expert decides, so an assistant does not see it. Keys and counts only (rule 6).
"""

# ruff: noqa: F811  (the shared fixture is imported from the findings tests)
from __future__ import annotations

from typing import Any

import pytest

from tests.http.test_understand_routes_findings import Case, case  # noqa: F401
from tests.support.db import ClientFor

pytestmark = pytest.mark.anyio

KIND = "understand_things_to_look_at"


async def items(client: Any) -> list[dict[str, Any]]:
    got = await client.get("/v1/today")
    assert got.status_code == 200, got.status_code
    return [i for i in got.json()["needs_you"] if i["kind"] == KIND]


async def test_things_to_look_at_until_each_is_decided(client_for: ClientFor, case: Case) -> None:
    expert = await client_for(case.expert)
    assert await items(expert) == []
    first, _ = case.finding(), case.finding(gentle=True)
    case.finding(status="gone")

    [item] = await items(expert)
    assert item["case_id"] == str(case.id)
    assert item["screen"] == "S13" and item["count"] == 2
    assert item["label_key"].startswith("understand.") and item["action_key"].startswith("understand.")
    assert await items(await client_for(case.assistant)) == []

    decided = await expert.patch(f"/v1/cases/{case.id}/findings/{first}", json={"decision": "relevant"})
    assert decided.status_code == 200
    [item] = await items(expert)
    assert item["count"] == 1
