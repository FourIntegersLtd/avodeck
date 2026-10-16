"""Shared contracts in `app/core/contracts/` (architecture 8.3): slices read each other through these.

Contracts only grow: each model keeps at least the fields listed here (the documented columns of
`facts`, `questions` and `report_versions` in docs/spec/data-dictionary.md). Removing or renaming one
fails this test. `NeedsYouItem` carries i18n keys and short codes only, never sentences or patient text.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, get_args

import pytest
from pydantic import ValidationError

from app.core.contracts import events, pipeline
from app.core.contracts.facts import CITABLE_STATUSES, Fact, FactStatus
from app.core.contracts.questions import Question, QuestionStatus
from app.core.contracts.reports import ReportVersion, ReportVersionStatus
from app.core.contracts.today import CaseCard, NeedsYouItem, NeedsYouRegistry

FACT_FIELDS = {
    "id",
    "case_id",
    "page_id",
    "kind",
    "fields",
    "value",
    "word_ids",
    "bbox",
    "event_date",
    "date_basis",
    "available_at",
    "availability_basis",
    "status",
    "version",
    "model_task",
    "model_id",
    "prompt_version",
    "created_at",
    "updated_at",
}
QUESTION_FIELDS = {
    "id",
    "case_id",
    "instruction_version_id",
    "number",
    "text_verbatim",
    "source_word_ids",
    "para_ref",
    "kind",
    "outside_expertise",
    "status",
    "confirmed_text",
    "version",
    "created_at",
    "updated_at",
}
REPORT_FIELDS = {
    "id",
    "case_id",
    "version_no",
    "type",
    "display_name",
    "docx_path",
    "pdf_path",
    "sha256",
    "status",
    "locked_at",
    "withdrawn_at",
    "withdrawn_reason",
    "prev_version_id",
    "created_at",
    "updated_at",
}
NEEDS_YOU_FIELDS = {
    "kind",
    "case_id",
    "rank",
    "due_at",
    "label_key",
    "action_key",
    "screen",
    "count",
    "minutes",
    "params",
}


@pytest.mark.parametrize(
    ("model", "fields"),
    [
        (Fact, FACT_FIELDS),
        (Question, QUESTION_FIELDS),
        (ReportVersion, REPORT_FIELDS),
        (NeedsYouItem, NEEDS_YOU_FIELDS),
    ],
)
def test_contracts_only_grow(model: type, fields: set[str]) -> None:
    missing = fields - set(model.model_fields)  # type: ignore[attr-defined]
    assert not missing, f"{model.__name__} lost fields other slices read: {sorted(missing)}"


def test_statuses_match_the_state_machines() -> None:
    assert set(get_args(FactStatus)) == {"suggested", "verified", "edited", "rejected"}
    assert set(get_args(QuestionStatus)) == {"suggested", "confirmed", "edited"}
    assert set(get_args(ReportVersionStatus)) == {"draft", "locked", "served", "withdrawn"}
    assert frozenset({"verified", "edited"}) == CITABLE_STATUSES


def test_a_fact_always_cites_words() -> None:
    base: dict[str, Any] = {
        "id": uuid.uuid4(),
        "case_id": uuid.uuid4(),
        "page_id": uuid.uuid4(),
        "kind": "zz_result",
        "value": "9",
        "status": "suggested",
        "version": 1,
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }
    assert Fact(**base, word_ids=["w1"]).word_ids == ["w1"]
    with pytest.raises(ValidationError):
        Fact(**base, word_ids=[])


def _item(**overrides: object) -> NeedsYouItem:
    values: dict[str, Any] = {
        "kind": "zz_pages_left",
        "case_id": uuid.uuid4(),
        "rank": 10,
        "label_key": "today.needs_you.zz_pages_left",
        "action_key": "today.action.carry_on_reading",
        "screen": "S09",
    }
    return NeedsYouItem(**{**values, **overrides})


def test_needs_you_items_carry_keys_and_codes_not_sentences() -> None:
    item = _item(count=127, minutes=40, params={"days": 7, "lane": "zz_lane"})
    assert item.due_at is None
    for bad in (
        {"label_key": "127 pages still to read"},
        {"action_key": "Carry on reading"},
        {"kind": "Pages Left"},
        {"screen": "reader"},
        {"params": {"name": "Mrs A Example"}},
        {"rank": -1},
        {"count": -3},
    ):
        with pytest.raises(ValidationError):
            _item(**bad)


def test_case_cards_are_plain_data() -> None:
    card = CaseCard(
        case_id=uuid.uuid4(),
        label="Case Z",
        organisation_id=uuid.uuid4(),
        organisation_name="Org",
        organisation_kind="team",
        status="active",
        stage=None,
        due_date=None,
        party_role=None,
        jurisdiction="zz-test",
        case_type="zz-case",
        specialty="zz-specialty",
        your_role="named_expert",
        on_hold=False,
    )
    assert card.status == "active"


def test_a_provider_name_registers_once() -> None:
    registry = NeedsYouRegistry()

    @registry.provider("zz_one")
    async def one(conn: object, cards: list[CaseCard]) -> list[NeedsYouItem]:
        return []

    assert registry.providers == {"zz_one": one}
    with pytest.raises(ValueError):
        registry.provider("zz_one")(one)


def test_report_served_events_reach_subscribers_in_order() -> None:
    bus = events.EventBus()
    seen: list[str] = []
    event = events.ReportServed(
        case_id=uuid.uuid4(), report_version_id=uuid.uuid4(), served_on=datetime.now(UTC).date()
    )

    @bus.subscribe(events.ReportServed)
    def first(e: events.ReportServed) -> None:
        seen.append("first")

    @bus.subscribe(events.ReportServed)
    def second(e: events.ReportServed) -> None:
        seen.append("second")

    bus.emit(event)
    assert seen == ["first", "second"]
    assert isinstance(events.events, events.EventBus)


def test_the_pipeline_contract_names_its_entry_points() -> None:
    assert pipeline.PROCESS_BUNDLE == "process_bundle"
    assert pipeline.PROCESS_PAGES == "process_pages"
