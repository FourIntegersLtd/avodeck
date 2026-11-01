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
from app.core.ai.mask import MaskedImage, is_genuine
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
    max_tokens: int = 4096  # the model's default; a task may ask for more (e.g. a multi-page batch)


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
        slot_poll_seconds: float = 1.0,
    ) -> None:
        self.settings = settings
        self.bedrock = bedrock
        self.sleep = sleep
        self.retries = retries
        self.base_delay = base_delay
        self.slot_poll_seconds = slot_poll_seconds

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
        job_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """`subject` is what the call is about (`answers.page_subject`), so recorded answers can be found.

        `job_id` is the running job this call belongs to (a worker call; `None` for an interactive API
        call): with it, the call waits its turn under the Bedrock on-demand throughput ceiling, fairly
        across every job wanting one (`_wait_for_slot`, migration 0049)."""
        check_endpoint(self.settings, self.bedrock.endpoint)
        model_id = model_for(self.settings, task.model)
        if case_id is not None:
            await self._cost_guard(conn, case_id)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        return await self._ask(conn, task, model_id, case_id, pack, stripped.text, subject, None, job_id)

    async def run_image(
        self,
        conn: AsyncConnection,
        task: AiTask,
        masked: MaskedImage,
        *,
        pack: MergedPack,
        text: str = "",
        case_id: uuid.UUID | None = None,
        names: Sequence[str] = (),
        dates_of_birth: Sequence[date] = (),
        subject: str | None = None,
        job_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Like `run`, with a page picture whose identifiers `mask.mask_page` painted out. Raw bytes are refused."""
        if not isinstance(masked, MaskedImage):
            raise TypeError("run_image takes a MaskedImage made by mask.mask_page")
        if not is_genuine(masked):
            raise TypeError("the picture is not the one that was masked")
        check_endpoint(self.settings, self.bedrock.endpoint)
        model_id = model_for(self.settings, task.model)
        if case_id is not None:
            await self._cost_guard(conn, case_id)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        return await self._ask(conn, task, model_id, case_id, pack, stripped.text, subject, masked, job_id)

    async def _ask(
        self,
        conn: AsyncConnection,
        task: AiTask,
        model_id: str,
        case_id: uuid.UUID | None,
        pack: MergedPack,
        text: str,
        subject: str | None,
        masked: MaskedImage | None,
        job_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        request = ModelRequest(
            model_id=model_id,
            system=task.system,
            context=pack.prompt_context(task.task_id),
            text=text,
            schema=task.schema,
            max_tokens=task.max_tokens,
            task_id=task.task_id,
            subject=subject,
            image_sha256=masked.sha256 if masked else None,
            image=masked,
        )
        validator = jsonschema_rs.validator_for(dict(task.schema))
        for _ in range(2):
            response = await self._invoke(request, conn, job_id)
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

    async def _invoke(
        self, request: ModelRequest, conn: AsyncConnection | None = None, job_id: uuid.UUID | None = None
    ) -> ModelResponse:
        delays = backoff_delays(self.retries, self.base_delay)
        for attempt in range(len(delays) + 1):
            try:
                if job_id is not None and conn is not None:
                    await self._wait_for_slot(conn, job_id)
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

    async def _wait_for_slot(self, conn: AsyncConnection, job_id: uuid.UUID) -> None:
        """Wait until this job may make its next Bedrock call, under the account-wide on-demand
        throughput ceiling (`settings.bedrock_rpm_limit`, not raisable per tenant or per case).

        A session-level advisory lock (not `pg_advisory_xact_lock`, used elsewhere: that variant
        cannot be released before the transaction ends, and this check must release it between
        polls so every other waiting job gets a turn) guards the read-then-write below. Rows in
        `app.ai_request_slots` (migration 0049) older than the ceiling's 60-second window no longer
        count; among jobs still waiting, the one whose own last grant (`app.jobs.last_granted_at`,
        oldest or never granted first) is longest ago goes next, so no job can starve another by
        polling faster. While waiting, the job's `step` is set to `waiting_for_capacity`, which the
        existing progress stream (`core/routes/jobs.py`) already shows the browser.
        """
        while True:
            await conn.execute("select pg_advisory_lock(hashtextextended('app.ai_request_slots', 0))")
            try:
                cur = await conn.execute(
                    "select id from app.ai_request_slots where job_id = %s and granted_at is null"
                    " order by requested_at limit 1",
                    (job_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    cur = await conn.execute(
                        "insert into app.ai_request_slots (job_id, case_id, requested_at)"
                        " select %s, case_id, now() from app.jobs where id = %s returning id",
                        (job_id, job_id),
                    )
                    row = await cur.fetchone()
                assert row is not None  # the job row must exist: its own job is running this call
                slot_id = row[0]

                cur = await conn.execute(
                    "select count(*) from app.ai_request_slots where granted_at > now() - interval '60 seconds'"
                )
                count_row = await cur.fetchone()
                assert count_row is not None
                (granted_count,) = count_row
                if granted_count < self.settings.bedrock_rpm_limit:
                    cur = await conn.execute(
                        "select s.job_id from app.ai_request_slots s join app.jobs j on j.id = s.job_id"
                        " where s.granted_at is null"
                        " order by j.last_granted_at is not null, j.last_granted_at, s.requested_at limit 1"
                    )
                    next_up = await cur.fetchone()
                    if next_up is not None and next_up[0] == job_id:
                        await conn.execute(
                            "update app.ai_request_slots set granted_at = now() where id = %s", (slot_id,)
                        )
                        await conn.execute("update app.jobs set last_granted_at = now() where id = %s", (job_id,))
                        return
                await conn.execute(
                    "update app.jobs set step = 'waiting_for_capacity' where id = %s", (job_id,)
                )
            finally:
                await conn.execute("select pg_advisory_unlock(hashtextextended('app.ai_request_slots', 0))")
            await self.sleep(self.slot_poll_seconds)

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
