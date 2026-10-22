"""Gaps on the timeline (docs/design/timeline-v2-followups.md item 4): how long an open thing to look at waited,
and whether the records simply go quiet. Pure assembly over findings and the facts the person may see; the
pack's own checks decide which later entries count as "something was done"."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import date

import pytest

from app.core.contracts import record_gaps
from app.core.packs import MergedPack, default_library
from app.features.understand import gaps, repo
from app.features.understand.timeline_models import Gap
from tests.support.packs import FIXTURE, LAUNCH

pytestmark = pytest.mark.anyio

LAUNCH_PACK = default_library().combine(*LAUNCH.codes)
FIXTURE_PACK = default_library().combine(*FIXTURE.codes)
CHECK = "abnormal_result_no_action"


def fact(kind: str, on: date | None, *, page: int = 1, source: str | None = None) -> repo.SeenFact:
    return repo.SeenFact(
        fact_id=uuid.uuid4(), page_id=uuid.uuid4(), page_no=page, kind=kind, fields={}, value="zz",
        event_date=on, available_at=on, status="verified", version=1, source=source,
    )  # fmt: skip


def finding(check_key: str, rule: str, *evidence: repo.SeenFact) -> gaps.Finding:
    return gaps.Finding(
        id=uuid.uuid4(), check_key=check_key, rule=rule, evidence_fact_ids=[f.fact_id for f in evidence]
    )


async def assemble(
    pack: MergedPack,
    found: list[gaps.Finding],
    shown: Sequence[repo.SeenFact],
    visible: Sequence[repo.SeenFact] | None = None,
    quiet: list[record_gaps.RecordGap] | None = None,
) -> list[Gap]:
    async def read(conn: object, case_id: uuid.UUID, start: date, end: date) -> list[record_gaps.RecordGap]:
        return [g for g in (quiet or []) if g.gap_start <= end and g.gap_end >= start]

    with record_gaps.reader.override(read):
        return await gaps.assemble(None, uuid.uuid4(), pack, found, shown, shown if visible is None else visible)  # type: ignore[arg-type]


async def test_a_wait_runs_to_the_first_later_entry_of_a_kind_the_check_counts_as_action() -> None:
    result = fact("result", date(2024, 1, 1))
    letter = fact("letter", date(2024, 1, 5))  # not an action kind for this check
    medicine = fact("prescription", date(2024, 3, 1))
    later = fact("prescription", date(2024, 4, 1))
    visible = [result, letter, medicine, later]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], visible)
    assert len(out) == 1
    got = out[0]
    assert (got.from_fact_id, got.to_fact_id, got.days) == (result.fact_id, medicine.fact_id, 60)
    assert (got.kind, got.label_key) == ("no_action_within", "timeline.gap.no_action_within")


async def test_the_acknowledged_kinds_of_a_check_count_too() -> None:
    referral = fact("referral", date(2024, 2, 1))
    visit = fact("consultation", date(2024, 2, 10))  # not an acknowledging kind
    letter = fact("letter", date(2024, 4, 1))
    found = [finding("referral_acknowledged", "not_acknowledged_within", referral)]
    out = await assemble(LAUNCH_PACK, found, [referral], [referral, visit, letter])
    assert (out[0].to_fact_id, out[0].days) == (letter.fact_id, 60)
    assert out[0].label_key == "timeline.gap.not_acknowledged_within"


async def test_the_fixture_pack_s_own_check_decides_the_action_kinds() -> None:
    check = next(c for c in FIXTURE_PACK.checks() if c["rule"] == "no_action_within")
    start = fact(check["applies_to"], date(2024, 1, 1))
    other = fact("zz_unrelated", date(2024, 1, 2))
    done = fact(sorted(check["action_kinds"])[0], date(2024, 1, 11))
    out = await assemble(FIXTURE_PACK, [finding(check["key"], check["rule"], start)], [start], [start, other, done])
    assert (out[0].to_fact_id, out[0].days) == (done.fact_id, 10)


async def test_with_no_action_kinds_the_next_entry_of_any_kind_ends_the_wait() -> None:
    listed = {"action_kinds", "acknowledged_kinds", "followup_kinds", "or_kinds"}
    check = next(c for c in LAUNCH_PACK.checks() if not listed & set(c))
    start = fact("prescription", date(2024, 1, 1))
    nxt = fact("zz_any", date(2024, 1, 20))
    out = await assemble(LAUNCH_PACK, [finding(check["key"], check["rule"], start)], [start], [start, nxt])
    assert (out[0].to_fact_id, out[0].days) == (nxt.fact_id, 19)


async def test_no_action_after_runs_to_the_latest_date_the_person_can_see() -> None:
    result = fact("result", date(2024, 1, 1))
    note = fact("letter", date(2024, 2, 1))
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, note])
    assert (out[0].to_fact_id, out[0].days, out[0].label_key) == (None, 31, "timeline.gap.no_action_after")


async def test_nothing_after_and_no_later_date_is_zero_days() -> None:
    result = fact("result", date(2024, 1, 1))
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result])
    assert (out[0].to_fact_id, out[0].days) == (None, 0)


async def test_a_gap_in_the_source_s_records_turns_it_into_records_quiet() -> None:
    result = fact("result", date(2024, 1, 1), source="zz_source")
    done = fact("prescription", date(2024, 3, 1))
    quiet = [record_gaps.RecordGap("zz_source", "zz_kind", date(2024, 1, 10), date(2024, 2, 10))]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, done], quiet)
    assert (out[0].kind, out[0].label_key, out[0].to_fact_id) == (
        "records_quiet",
        "timeline.gap.records_quiet",
        done.fact_id,
    )


async def test_a_gap_in_a_kind_the_check_counts_as_action_also_means_records_quiet() -> None:
    result = fact("result", date(2024, 1, 1), source="zz_a")
    done = fact("prescription", date(2024, 3, 1))
    quiet = [record_gaps.RecordGap("zz_other", "prescription", date(2024, 1, 10), date(2024, 2, 10))]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, done], quiet)
    assert out[0].kind == "records_quiet"


async def test_a_gap_elsewhere_or_outside_the_wait_leaves_the_rule_as_it_was() -> None:
    result = fact("result", date(2024, 1, 1), source="zz_a")
    done = fact("prescription", date(2024, 2, 1))
    quiet = [
        record_gaps.RecordGap("zz_other", "zz_kind", date(2024, 1, 10), date(2024, 1, 20)),
        record_gaps.RecordGap("zz_a", "zz_kind", date(2024, 5, 1), date(2024, 6, 1)),
    ]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, done], quiet)
    assert out[0].kind == "no_action_within"


async def test_a_finding_whose_evidence_is_not_shown_or_undated_makes_no_gap() -> None:
    hidden = fact("result", date(2024, 1, 1))
    undated = fact("result", None)
    shown = fact("letter", date(2024, 1, 2))
    found = [finding(CHECK, "no_action_within", hidden), finding(CHECK, "no_action_within", undated)]
    assert await assemble(LAUNCH_PACK, found, [shown, undated], [hidden, undated, shown]) == []


async def test_the_earliest_shown_evidence_starts_the_gap_and_gaps_are_in_date_order() -> None:
    late = fact("result", date(2024, 3, 1))
    early = fact("result", date(2024, 1, 1))
    other = fact("result", date(2024, 2, 1))
    first = finding(CHECK, "no_action_within", late, early)
    second = finding(CHECK, "no_action_within", other)
    out = await assemble(LAUNCH_PACK, [second, first], [late, early, other])
    assert [g.from_fact_id for g in out] == [early.fact_id, other.fact_id]
    assert [g.finding_id for g in out] == [first.id, second.id]


async def test_later_entries_of_other_kinds_only_is_not_nothing_after() -> None:
    result = fact("result", date(2024, 1, 1))
    note = fact("letter", date(2024, 2, 1))  # later, but not an action kind for this check
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, note])
    assert (out[0].to_fact_id, out[0].days, out[0].label_key) == (None, 31, "timeline.gap.no_action_after")


async def test_records_quiet_still_applies_when_no_action_entry_follows() -> None:
    result = fact("result", date(2024, 1, 1), source="zz_source")
    note = fact("letter", date(2024, 3, 1))
    quiet = [record_gaps.RecordGap("zz_source", "zz_kind", date(2024, 1, 10), date(2024, 2, 10))]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result, note], quiet)
    assert (out[0].kind, out[0].label_key) == ("records_quiet", "timeline.gap.records_quiet")


async def test_nothing_later_at_all_keeps_nothing_after_and_records_quiet() -> None:
    result = fact("result", date(2024, 1, 1), source="zz_source")
    quiet = [record_gaps.RecordGap("zz_source", "zz_kind", date(2024, 1, 1), date(2024, 2, 10))]
    out = await assemble(LAUNCH_PACK, [finding(CHECK, "no_action_within", result)], [result], [result], quiet)
    assert out[0].label_key == "timeline.gap.none_after"
