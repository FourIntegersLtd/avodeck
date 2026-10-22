"""`GET /timeline` carries a gap for each open thing to look at: how long it waited and in whose words
(the label key). Run under both packs; the pack's own check names the kinds that count as action. A source
that goes quiet (slice A's `record_gaps`) makes it "the records go quiet here"."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.core.contracts import record_gaps
from app.features.understand.timeline_models import Gap
from tests.http.test_understand_routes_timeline import Case
from tests.support.db import ClientFor
from tests.support.packs import BOTH, Combo
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

ACTION_KEYS = ("action_kinds", "acknowledged_kinds", "followup_kinds", "or_kinds")


def _check(case: Case) -> dict[str, Any]:
    lane_kinds = {k for lane in case.pack.lanes() for k in lane["kinds"]}
    for c in case.pack.checks():
        if c["rule"] == "no_action_within" and _action(c) in lane_kinds and _start(c) in lane_kinds:
            return c
    raise AssertionError("no check with action kinds in a lane")


def _action(check: dict[str, Any]) -> str:
    return str([k for key in ACTION_KEYS for k in check.get(key, [])][0])


def _start(check: dict[str, Any]) -> str:
    applies = check["applies_to"]
    return str(applies if isinstance(applies, str) else applies[0])


def _finding(case: Case, check: dict[str, Any], *facts: uuid.UUID, status: str = "open") -> uuid.UUID:
    row = case.admin.execute(
        "insert into app.findings (case_id, check_key, rule, evidence_fact_ids, evidence_page_ids,"
        " pack_version, status)"
        " values (%s, %s, %s, %s, %s, 'zz@1', %s) returning id",
        (case.id, check["key"], check["rule"], list(facts), [case.page(1)], status),
    ).fetchone()
    assert row is not None
    return uuid.UUID(str(row[0]))


async def _gaps(client_for: ClientFor, case: Case) -> list[dict[str, Any]]:
    resp = await (await client_for(case.expert)).get(f"/v1/cases/{case.id}/timeline")
    assert resp.status_code == 200
    return list(resp.json()["gaps"])


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_an_open_finding_shows_the_wait_to_the_first_action(
    combo: Combo, client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed, combo)
    check = _check(case)
    start = case.fact(_start(check), date(2024, 1, 1))
    done = case.fact(_action(check), date(2024, 2, 1))
    finding = _finding(case, check, start)
    assert await _gaps(client_for, case) == [
        Gap(finding_id=finding, from_fact_id=start, to_fact_id=done, days=31, kind=check["rule"],
            label_key=f"timeline.gap.{check['rule']}").model_dump(mode="json")
    ]  # fmt: skip


@pytest.mark.parametrize("combo", BOTH, ids=lambda c: c.jurisdiction)
async def test_nothing_after_runs_to_the_latest_entry(combo: Combo, client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed, combo)
    check = _check(case)
    start = case.fact(_start(check), date(2024, 1, 1))
    case.fact(_start(check), date(2024, 1, 15))
    _finding(case, check, start)
    got = (await _gaps(client_for, case))[0]
    assert (got["to_fact_id"], got["days"], got["label_key"]) == (None, 14, "timeline.gap.none_after")


async def test_a_finding_no_longer_open_makes_no_gap(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    check = _check(case)
    start = case.fact(_start(check), date(2024, 1, 1))
    _finding(case, check, start, status="gone")
    assert await _gaps(client_for, case) == []


async def test_a_source_that_goes_quiet_makes_it_records_quiet(client_for: ClientFor, seed: Seed) -> None:
    case = Case(seed)
    check = _check(case)
    start = case.fact(_start(check), date(2024, 1, 1))
    case.fact(_action(check), date(2024, 3, 1))
    _finding(case, check, start)

    async def read(conn: object, case_id: uuid.UUID, a: date, b: date) -> list[record_gaps.RecordGap]:
        return [record_gaps.RecordGap("zz_s", _action(check), date(2024, 1, 5), date(2024, 3, 1))]

    with record_gaps.reader.override(read):
        got = (await _gaps(client_for, case))[0]
    assert (got["kind"], got["label_key"]) == ("records_quiet", "timeline.gap.records_quiet")


async def test_the_registered_reader_reads_the_case_s_gaps_and_ignores_rejected_ones(
    client_for: ClientFor, seed: Seed
) -> None:
    case = Case(seed)
    check = _check(case)
    start = case.fact(_start(check), date(2024, 1, 1))
    case.fact(_action(check), date(2024, 3, 1))
    _finding(case, check, start)
    case.admin.execute(
        "insert into app.record_gaps (case_id, source_type, fact_kind, gap_start, gap_end, confirmed)"
        " values (%s, 'zz_s', %s, '2024-01-10', '2024-02-10', false)",
        (case.id, _action(check)),
    )
    assert (await _gaps(client_for, case))[0]["kind"] == check["rule"]
    case.admin.execute("update app.record_gaps set confirmed = null where case_id = %s", (case.id,))
    assert (await _gaps(client_for, case))[0]["kind"] == "records_quiet"
