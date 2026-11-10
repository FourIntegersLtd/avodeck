"""Shared pieces for the AI audit tests (step 14): a database stand-in that records every `ai_calls` insert and
answers the cost guard's budget query, a small closed task schema and a pack from the fixture layer. Synthetic only."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from psycopg import AsyncConnection

from app.core.ai.kit import AiTask
from app.core.packs import default_library
from app.features.after_serving import ai_tasks as serving_tasks
from app.features.pipeline import extract, missing
from app.features.reader import second_reading
from app.features.report import ai_tasks as report_tasks
from app.features.setup import ai_tasks as setup_tasks
from app.features.trial import ai_tasks as trial_tasks
from app.features.understand import ai_tasks as understand_tasks
from app.features.write import ai_tasks as write_tasks
from tests.support.packs import BOTH, FIXTURE

PACK = default_library().combine(*FIXTURE.codes)
PACK_LAUNCH = default_library().combine(*BOTH[0].codes)
SYSTEM = "Label each line of the text. Answer only through the recording tool."

SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["labels"],
    "properties": {"labels": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 40}}},
}
VALID: dict[str, Any] = {"labels": ["a"]}


def task(schema: dict[str, Any] | None = None, **kwargs: Any) -> AiTask:
    return AiTask(
        task_id="T-test", prompt_version="v1", model="sonnet", system=SYSTEM, schema=schema or SCHEMA, **kwargs
    )


class _Cursor:
    def __init__(self, row: tuple[Any, ...] | None) -> None:
        self.row = row

    async def fetchone(self) -> tuple[Any, ...] | None:
        return self.row


if TYPE_CHECKING:  # to the type checker the stand-in is a connection; at run time it is a plain object
    _Base = AsyncConnection[Any]
else:
    _Base = object


class FakeConn(_Base):
    """`execute` answers the budget query with `budget` (tokens used, pages) and keeps every other statement's
    parameters (the `ai_calls` inserts) in `logged`."""

    def __init__(self, budget: tuple[int, int] = (0, 0), person_tokens: int = 0) -> None:
        self.budget = budget
        self.person_tokens = person_tokens
        self.sql: list[str] = []
        self.logged: list[tuple[Any, ...]] = []

    async def execute(self, sql: str, params: tuple[Any, ...] = ()) -> _Cursor:  # type: ignore[override]
        self.sql.append(sql)
        if "ai_budget" in sql:
            return _Cursor(self.budget)
        if sql.lstrip().startswith("select") and "app.ai_calls" in sql:  # the per-person allowance (case-less calls)
            return _Cursor((self.person_tokens,))
        self.logged.append(tuple(params))
        return _Cursor(None)

    @property
    def outcomes(self) -> list[str]:
        return [str(row[-1]) for row in self.logged]


def strings(node: Any) -> Iterator[str]:
    """Every string in a nested value, keys included."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for key, value in node.items():
            yield from strings(key)
            yield from strings(value)
    elif isinstance(node, list | tuple):
        for value in node:
            yield from strings(value)


def all_tasks() -> list[AiTask]:
    pack = PACK_LAUNCH
    return [
        write_tasks.t14a(pack, ["f1"]),
        write_tasks.t14b(pack, ["f1"]),
        write_tasks.t14c(pack, ["f1"]),
        write_tasks.t15a(pack, 3),
        trial_tasks.t22a(pack),
        trial_tasks.t22b(pack, ["a1"], ["p1"]),
        trial_tasks.t22c(pack),
        trial_tasks.t24(pack, 3, ["p1"]),
        report_tasks.t17(pack, 5),
        serving_tasks.t20a(pack),
        serving_tasks.t20b(pack),
        serving_tasks.t21a(pack),
        serving_tasks.t21b(pack),
        serving_tasks.t21c(pack),
        serving_tasks.t21d(pack),
        serving_tasks.t21e(pack),
        setup_tasks.t06(pack),
        setup_tasks.t04(pack),
        setup_tasks.t02(pack),
        understand_tasks.t11(pack),
        understand_tasks.t13a(pack),
        missing.task_for(pack),
        extract.task_for(pack),
        second_reading.TASK,
    ]
