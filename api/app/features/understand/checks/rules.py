"""The rule types behind "Things to look at". Packs choose and parameterise them; code never names a kind.

A rule returns hits: the facts a finding rests on, for the expert to look at. A hit is a pointer,
never a conclusion (rule 1). Rejected facts never take part.

Parameters (all from the check row): `applies_to` (kind or kinds), `when` ({field: value}), `flag`
({field, value} or {field, any_of}), `window_days`, `action_kinds`, `label_spans`,
`acknowledged_kinds` / `acknowledges_field` / `acknowledged_field`, `field`, `or_kinds`, `scope`
(all | linked), `followup_kinds`, `table`, `gentle`.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

Check = Mapping[str, Any]
Tables = Mapping[str, Sequence[Mapping[str, Any]]]
Labels = Mapping[uuid.UUID, str]

ACTION_TAKEN = "action_taken"


class UnknownRule(ValueError):
    pass


@dataclass(frozen=True)
class CheckFact:
    fact_id: uuid.UUID
    page_id: uuid.UUID
    kind: str
    fields: Mapping[str, Any]
    value: str | None
    event_date: date | None
    available_at: date | None
    linked: bool = False
    status: str = "verified"


@dataclass(frozen=True)
class Hit:
    check_key: str
    evidence: tuple[uuid.UUID, ...]
    gentle: bool = False


@dataclass(frozen=True)
class _Ctx:
    check: Check
    facts: list[CheckFact]
    labels: Labels = field(default_factory=dict)
    tables: Tables = field(default_factory=dict)

    def hit(self, *facts: CheckFact) -> Hit:
        return Hit(str(self.check["key"]), tuple(f.fact_id for f in facts), bool(self.check.get("gentle", False)))

    def of_kinds(self, kinds: Iterable[str]) -> list[CheckFact]:
        wanted = set(kinds)
        return [f for f in self.facts if f.kind in wanted]

    @property
    def window(self) -> timedelta | None:
        days = self.check.get("window_days")
        return None if days is None else timedelta(days=int(days))


def _kinds(value: Any) -> set[str]:
    if value is None:
        return set()
    return {value} if isinstance(value, str) else set(value)


def _as_date(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _flag_ok(flag: Any, fields: Mapping[str, Any]) -> bool:
    if flag is None:
        return True
    if isinstance(flag, str):
        return bool(fields.get("flag"))
    got = fields.get(flag["field"])
    if "any_of" in flag:
        return got in flag["any_of"]
    return bool(got == flag.get("value"))


def _subjects(ctx: _Ctx) -> list[CheckFact]:
    kinds = _kinds(ctx.check.get("applies_to"))
    when = ctx.check.get("when") or {}
    return [
        f
        for f in ctx.facts
        if f.kind in kinds
        and all(f.fields.get(k) == v for k, v in when.items())
        and _flag_ok(ctx.check.get("flag"), f.fields)
    ]


def _between(facts: Iterable[CheckFact], start: date, window: timedelta | None) -> list[CheckFact]:
    return [
        f
        for f in facts
        if f.event_date is not None and start <= f.event_date and (window is None or f.event_date <= start + window)
    ]


def _no_action_groups(ctx: _Ctx) -> list[tuple[list[CheckFact], list[CheckFact]]]:
    """(group, candidate actions) per abnormal set: same kind and date, plus copies with an equal value."""
    groups: dict[tuple[str, date], list[CheckFact]] = {}
    for subject in _subjects(ctx):
        if subject.event_date is not None:
            groups.setdefault((subject.kind, subject.event_date), []).append(subject)
    out = []
    actions = ctx.of_kinds(_kinds(ctx.check.get("action_kinds")))
    for (kind, day), members in groups.items():
        values = {m.value for m in members if m.value}
        ids = {m.fact_id for m in members}
        copies = [
            f
            for f in ctx.facts
            if f.kind == kind and f.event_date == day and f.value in values and f.fact_id not in ids
        ]
        group = members + copies
        start = min((f.available_at for f in group if f.available_at), default=day)
        out.append((group, _between(actions, start, ctx.window)))
    return out


def _no_action_within(ctx: _Ctx) -> list[Hit]:
    hits = []
    for group, candidates in _no_action_groups(ctx):
        if ctx.check.get("label_spans"):
            done = any(ctx.labels.get(c.fact_id) == ACTION_TAKEN for c in candidates)
        else:
            done = bool(candidates)
        if not done:
            hits.append(ctx.hit(*group))
    return hits


def _not_acknowledged_within(ctx: _Ctx) -> list[Hit]:
    by_field = ctx.check.get("acknowledges_field")
    own_field = ctx.check.get("acknowledged_field")
    replies = ctx.of_kinds(_kinds(ctx.check.get("acknowledged_kinds")))
    hits = []
    for subject in _subjects(ctx):
        if subject.event_date is None:
            continue
        acks = [r for r in replies if by_field and _as_date(r.fields.get(by_field)) == subject.event_date]
        dates = [r.event_date for r in acks if r.event_date is not None]
        own = _as_date(subject.fields.get(own_field)) if own_field else None
        if own is not None:
            dates.append(own)
        window = ctx.window or timedelta(0)
        if not dates or min(dates) > subject.event_date + window:
            hits.append(ctx.hit(subject, *acks))
    return hits


def _not_reached_clinician(ctx: _Ctx) -> list[Hit]:
    window = ctx.window or timedelta(0)
    return [
        ctx.hit(s)
        for s in _subjects(ctx)
        if s.event_date is not None and (s.available_at is None or s.available_at - s.event_date > window)
    ]


def _field_missing(ctx: _Ctx) -> list[Hit]:
    name = ctx.check.get("field")
    others = ctx.of_kinds(_kinds(ctx.check.get("or_kinds")))
    linked_only = ctx.check.get("scope", "all") == "linked"
    hits = []
    for subject in _subjects(ctx):
        if linked_only and not subject.linked:
            continue
        if name and subject.fields.get(name):
            continue
        if any(o.event_date is not None and o.event_date == subject.event_date for o in others):
            continue
        hits.append(ctx.hit(subject))
    return hits


def _nothing_after(ctx: _Ctx, kinds_param: str, needs_field: bool) -> list[Hit]:
    name = ctx.check.get("field")
    later = ctx.of_kinds(_kinds(ctx.check.get(kinds_param)))
    hits = []
    for subject in _subjects(ctx):
        if needs_field and not (name and subject.fields.get(name)):
            continue
        start = subject.available_at or subject.event_date
        if start is None:
            continue
        if not [f for f in _between(later, start, ctx.window) if f.fact_id != subject.fact_id]:
            hits.append(ctx.hit(subject))
    return hits


def _actions_not_done(ctx: _Ctx) -> list[Hit]:
    return _nothing_after(ctx, "action_kinds", needs_field=True)


def _not_followed_up(ctx: _Ctx) -> list[Hit]:
    return _nothing_after(ctx, "followup_kinds", needs_field=False)


def _monitoring_overdue(ctx: _Ctx) -> list[Hit]:
    rows = ctx.tables.get(str(ctx.check.get("table")), ())
    drugs = {str(r["drug"]).casefold(): r for r in rows}
    results = [(f.event_date, f) for f in ctx.facts if f.fields.get("test") and f.event_date is not None]
    hits = []
    for subject in _subjects(ctx):
        row = drugs.get(str(subject.fields.get("drug", "")).casefold())
        if row is None or subject.event_date is None:
            continue
        tests = {str(t).casefold() for t in row.get("tests", ())}
        before = [(d, r) for d, r in results if str(r.fields["test"]).casefold() in tests and d <= subject.event_date]
        if not before:
            hits.append(ctx.hit(subject))
            continue
        last_date, last = max(before, key=lambda pair: pair[0])
        if (subject.event_date - last_date).days > int(row["interval_days"]):
            hits.append(ctx.hit(subject, last))
    return hits


_RULES: dict[str, Callable[[_Ctx], list[Hit]]] = {
    "no_action_within": _no_action_within,
    "not_acknowledged_within": _not_acknowledged_within,
    "monitoring_overdue": _monitoring_overdue,
    "not_reached_clinician": _not_reached_clinician,
    "actions_not_done": _actions_not_done,
    "field_missing": _field_missing,
    "not_followed_up": _not_followed_up,
}

RULE_TYPES = frozenset(_RULES)


def _ctx(check: Check, facts: Iterable[CheckFact], labels: Labels, tables: Tables) -> _Ctx:
    if check.get("rule") not in _RULES:
        raise UnknownRule(str(check.get("rule")))
    return _Ctx(check, [f for f in facts if f.status != "rejected"], labels, tables)


def evaluate(
    check: Check, facts: Iterable[CheckFact], *, labels: Labels | None = None, tables: Tables | None = None
) -> list[Hit]:
    ctx = _ctx(check, facts, labels or {}, tables or {})
    return _RULES[str(check["rule"])](ctx)


def to_label(check: Check, facts: Iterable[CheckFact]) -> list[uuid.UUID]:
    """The facts T-13a must label before `evaluate` can decide this check (only with `label_spans`)."""
    ctx = _ctx(check, facts, {}, {})
    if not check.get("label_spans") or check["rule"] != "no_action_within":
        return []
    seen: dict[uuid.UUID, None] = {}
    for _, candidates in _no_action_groups(ctx):
        seen.update(dict.fromkeys(c.fact_id for c in candidates))
    return list(seen)
