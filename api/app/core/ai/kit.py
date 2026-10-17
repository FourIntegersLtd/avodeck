"""Run one AI task (architecture 6.2): the only path from a slice to a model.

1. The task's model role resolves to the cell's in-region model; the client's endpoint must be on
   the allow-list (rule 5, F24).
2. The cost guard (F11, K3) checks the case's AI use so far; the same database call refuses anyone
   without access to the case, before anything is sent.
3. `pii.strip` removes identifiers (rule 7).
4. The model gets the task's system prompt, the pack's prompt context as data, the stripped text
   and the output schema, at temperature 0.
5. Busy service: exponential back-off, then processing pauses (F5). Model gone: processing pauses,
   with no fallback to another model or region (F24).
6. Output must match the schema; one retry, then `InvalidOutput`.
7. Each call writes an `ai_calls` row: task, model, region, prompt version, tokens, outcome. Never
   the prompt or the response (rule 6).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import jsonschema_rs
from psycopg import AsyncConnection

from app.core.ai import pii
from app.core.ai.bedrock import BedrockClient, ModelRequest, ModelResponse, ModelUnavailable, ServiceBusy
from app.core.ai.cell import check_endpoint, model_for
from app.core.config import AiSettings
from app.core.errors import ProcessingPaused
from app.core.packs import MergedPack

logger = logging.getLogger("app.ai")

MAX_WAIT_SECONDS = 2 * 60 * 60


@dataclass(frozen=True)
class AiTask:
    task_id: str
    prompt_version: str
    model: str
    system: str = field(repr=False)
    schema: Mapping[str, Any] = field(repr=False)


class InvalidOutput(Exception):
    """The model's output did not match the task's schema twice."""


def backoff_delays(retries: int, base: float = 2.0, limit: float = MAX_WAIT_SECONDS) -> list[float]:
    """Growing waits between attempts, never more than `limit` seconds in all."""
    delays: list[float] = []
    for attempt in range(retries):
        delay = min(base * 2**attempt, limit - sum(delays))
        if delay <= 0:
            break
        delays.append(delay)
    return delays


class AiKit:
    def __init__(
        self,
        settings: AiSettings,
        bedrock: BedrockClient,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retries: int = 4,
        base_delay: float = 2.0,
    ) -> None:
        self.settings = settings
        self.bedrock = bedrock
        self.sleep = sleep
        self.retries = retries
        self.base_delay = base_delay

    async def run(
        self,
        conn: AsyncConnection,
        task: AiTask,
        *,
        pack: MergedPack,
        text: str,
        case_id: uuid.UUID | None = None,
        names: Sequence[str] = (),
        dates_of_birth: Sequence[date] = (),
        subject: str | None = None,
    ) -> dict[str, Any]:
        """`subject` is what the call is about (`answers.page_subject`), so recorded answers can be found."""
        check_endpoint(self.settings, self.bedrock.endpoint)
        model_id = model_for(self.settings, task.model)
        if case_id is not None:
            await self._cost_guard(conn, case_id)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        request = ModelRequest(
            model_id=model_id,
            system=task.system,
            context=pack.prompt_context(task.task_id),
            text=stripped.text,
            schema=task.schema,
            task_id=task.task_id,
            subject=subject,
        )
        validator = jsonschema_rs.validator_for(dict(task.schema))
        for _ in range(2):
            response = await self._invoke(request)
            valid = validator.is_valid(response.output)
            await self._log(conn, task, model_id, case_id, response, "ok" if valid else "invalid_output")
            if valid:
                return response.output
        logger.warning("task %s output did not match its schema twice", task.task_id)
        raise InvalidOutput(task.task_id)

    async def _cost_guard(self, conn: AsyncConnection, case_id: uuid.UUID) -> None:
        cur = await conn.execute("select tokens_used, pages from app.ai_budget(%s)", (case_id,))
        row = await cur.fetchone()
        used, pages = (int(row[0]), int(row[1])) if row else (0, 0)
        s = self.settings
        allowance = s.cost_guard_multiple * s.expected_tokens_per_page * max(pages, s.min_expected_pages)
        if used >= allowance:
            logger.error("cost_guard tripped for case %s: %d of %d tokens used", case_id, used, allowance)
            raise ProcessingPaused("cost_guard")

    async def _invoke(self, request: ModelRequest) -> ModelResponse:
        delays = backoff_delays(self.retries, self.base_delay)
        for attempt in range(len(delays) + 1):
            try:
                return await self.bedrock.invoke(request)
            except ServiceBusy as busy:
                if attempt == len(delays):
                    logger.error("model service still busy after %d attempts; processing paused", attempt + 1)
                    raise ProcessingPaused("service_busy") from None
                wait = max(delays[attempt], busy.retry_after or 0.0)
                logger.warning("model service busy; waiting %.0f s (attempt %d)", wait, attempt + 1)
                await self.sleep(wait)
            except ModelUnavailable:
                logger.error("in-region model unavailable; processing paused, no fallback")
                raise ProcessingPaused("model_unavailable") from None
        raise AssertionError("unreachable")  # pragma: no cover

    async def _log(
        self,
        conn: AsyncConnection,
        task: AiTask,
        model_id: str,
        case_id: uuid.UUID | None,
        response: ModelResponse,
        outcome: str,
    ) -> None:
        await conn.execute(
            "insert into app.ai_calls (case_id, person_id, task_id, model_id, region, prompt_version,"
            " input_tokens, output_tokens, outcome) values (%s, app.current_person_id(), %s, %s, %s, %s, %s, %s, %s)",
            (
                case_id,
                task.task_id,
                model_id,
                self.settings.bedrock_region,
                task.prompt_version,
                response.input_tokens,
                response.output_tokens,
                outcome,
            ),
        )
