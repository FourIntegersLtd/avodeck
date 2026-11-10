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
   TEMPORARY, dummy data only (ADR 0036): with `AI_PRIMARY_PROVIDER=anthropic` the direct API is asked
   first (its own slot pool and limits); a busy direct API is retried up to three times, then, or at once
   when its model is gone or it refuses, the same request goes to Bedrock, which keeps this step as is.
6. Output must match the schema; one retry, then `InvalidOutput`.
7. Each call writes an `ai_calls` row: task, model, region, prompt version, tokens, outcome. Never
   the prompt or the response (rule 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

import jsonschema_rs
from psycopg import AsyncConnection

from app.core.ai import pii
from app.core.ai.bedrock import (
    CALL_ATTEMPT,
    BedrockClient,
    ModelRequest,
    ModelRequestRefused,
    ModelResponse,
    ModelUnavailable,
    ServiceBusy,
)
from app.core.ai.cell import check_endpoint, model_for
from app.core.ai.mask import MaskedImage, is_genuine
from app.core.config import AiSettings, ProviderLimits
from app.core.db import Database
from app.core.errors import ProcessingPaused
from app.core.http_retry import backoff
from app.core.packs import MergedPack

logger = logging.getLogger("app.ai")

MAX_WAIT_SECONDS = 2 * 60 * 60
SLOT_LOCK = "app.ai_request_slots"
DIRECT_RETRIES = 3  # temporary, dummy data only (ADR 0036): busy retries on the direct API before Bedrock
DIRECT_MAX_WAIT_SECONDS = 30.0
# A granted slot's release: called once the call it was granted for has answered or failed.
_Release = Callable[[], Awaitable[None]]
_Wait = Callable[[str], Awaitable[_Release | None]]


@dataclass(frozen=True)
class AiTask:
    task_id: str
    prompt_version: str
    model: str
    system: str = field(repr=False)
    schema: Mapping[str, Any] = field(repr=False)
    max_tokens: int = 4096  # the model's default; a task may ask for more (e.g. a multi-page batch)
    mends_labels: bool = False  # a batch answer keeps going when one label is off its list (`mend_labels`)


@dataclass(frozen=True)
class UsageEvent:
    """One AI call's usage, as given to `AiKit.usage_hooks`: ids and token counts only, never content (rule 6).
    `provider` is the `ai_calls.region` value (`anthropic-direct` or the Bedrock region); `ok` is False when the
    answer did not match the task's schema."""

    task_id: str
    provider: str
    model_id: str
    input_tokens: int
    output_tokens: int
    ok: bool


class InvalidOutput(Exception):
    """The model's output did not match the task's schema twice."""


class OutputTruncated(InvalidOutput):
    """The answer used the whole output cap, so it is cut off (invalid JSON), not wrong. Asking again with the
    same cap fails the same way, so the kit gives up at once: the caller asks for less (a smaller group) or more
    room."""


class TooLargeForFallback(Exception):
    """The direct API failed and the request is too big for Bedrock to answer. The caller re-splits it
    into calls of `pages_per_call` pages (Bedrock's own limit) and asks again."""

    def __init__(self, pages_per_call: int) -> None:
        super().__init__("request too large for the fallback provider")
        self.pages_per_call = pages_per_call


# An answer cut off by the cap is not always `>= max_tokens`: a JSON cut mid-way can end a few tokens short
# (31934 of 32000 was seen). Within this margin of the cap, or in the last tenth of it with a broken tail, it is
# treated as cut off. Only for real caps: a test cap of a few hundred tokens would make 256 half the answer.
CUT_OFF_MARGIN = 256
CUT_OFF_TAIL = 0.9
CUT_OFF_MIN_CAP = 8000
_TAIL_ERRORS = frozenset({"AdditionalProperties", "Required"})


def looks_cut_off(output_tokens: int, max_tokens: int, output: Any, validator: Any) -> bool:
    """Whether an answer that failed its schema was cut off by the output cap, not wrong.

    True when it used the whole cap; for a cap of `CUT_OFF_MIN_CAP` or more, also when it ended within
    `CUT_OFF_MARGIN` tokens of it, or reached `CUT_OFF_TAIL` of it and fails at the tail (an extra key or a
    missing field, the shape of a JSON cut off mid-way). A clean off-list label (`Enum`) is a wrong answer."""
    if output_tokens >= max_tokens:
        return True
    if max_tokens < CUT_OFF_MIN_CAP:
        return False
    if output_tokens >= max_tokens - CUT_OFF_MARGIN:
        return True
    if output_tokens >= CUT_OFF_TAIL * max_tokens:
        return any(type(error.kind).__name__ in _TAIL_ERRORS for error in validator.iter_errors(output))
    return False


def _starred(path: Sequence[Any], schema_path: Sequence[Any] = ()) -> str:
    """A place in an answer with list positions as `*`: where, never what (rule 6).

    A name stays only when the schema itself declares it at that place (`properties`, then the name); a key the
    model chose (a map's key, or anything that cannot be lined up with the schema's own path) is starred too, so a
    model-chosen key never reaches a log line (AUDIT-14 E-LOG2)."""
    place = list(path)
    keywords = list(schema_path)
    shown: list[str] = []
    i = j = 0
    while i < len(place) and j < len(keywords):
        word = keywords[j]
        if word == "properties" and j + 1 < len(keywords):
            shown.append(str(place[i]) if place[i] == keywords[j + 1] else "*")
            i, j = i + 1, j + 2
        elif word == "prefixItems":
            shown.append("*")
            i, j = i + 1, j + 2
        elif word == "items":
            shown.append("*")
            i, j = i + 1, j + 1
        else:
            j += 1
    shown.extend("*" for _ in place[i:])  # anything left over could not be lined up with the schema: never shown
    return ".".join(shown)


def mend_labels(output: Any, validator: Any) -> tuple[Any, list[str]]:
    """`output` with every label that is off its closed list (`enum`) mended, and where each one was.

    A model sometimes answers a closed list with a label the pack does not have. Where the list allows
    "cannot tell" (null) the label becomes null; otherwise the one list item carrying it is left out, so one
    stray label never throws away a whole answer. An item of the outermost list (a whole page of a batch) is
    never left out: that label stays, and the schema check rejects the answer as before. Any other mismatch
    is left alone. The given answer is not changed in place."""
    nulls: list[list[Any]] = []
    drops: set[tuple[Any, ...]] = set()
    places: list[str] = []
    for error in validator.iter_errors(output):
        if type(error.kind).__name__ != "Enum":
            continue
        path = list(error.instance_path)
        if None in error.kind.options:
            nulls.append(path)
        else:
            lists = [i for i, part in enumerate(path) if isinstance(part, int)]
            if len(lists) < 2:
                continue
            drops.add(tuple(path[: lists[-1] + 1]))
        places.append(_starred(path, error.schema_path))
    if not places:
        return output, []
    mended = copy.deepcopy(output)
    for path in nulls:
        _at(mended, path[:-1])[path[-1]] = None
    for item in sorted(drops, reverse=True):  # later positions first, so earlier ones keep their place
        del _at(mended, item[:-1])[item[-1]]
    return mended, places


def _at(value: Any, path: Sequence[Any]) -> Any:
    for part in path:
        value = value[part]
    return value


MAX_ANSWER_DEPTH = 64  # no task's schema nests this deep; a deeper answer is refused before it is validated


def too_deep(value: Any, limit: int = MAX_ANSWER_DEPTH) -> bool:
    """Whether `value` nests lists and objects deeper than `limit` (checked without recursion)."""
    level = [value]
    for _ in range(limit):
        level = [
            child
            for item in level
            for child in (item.values() if isinstance(item, dict) else item if isinstance(item, list) else ())
        ]
        if not level:
            return False
    return bool(level)


_SAFE_CODE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,63}$")


def vetted_refusal(refused: ModelRequestRefused) -> ModelRequestRefused:
    """A copy of `refused` whose message is its code, only if that has the shape of one (never text), else
    `unknown`; its typed fields are kept (AUDIT-14 E-LOG1)."""
    code = refused.error_code or (str(refused.args[0]) if len(refused.args) == 1 else "")
    return ModelRequestRefused(code if _SAFE_CODE.fullmatch(code) else "unknown").annotated(
        status=refused.status,
        error_type=refused.error_type,
        error_code=refused.error_code,
        request_id=refused.request_id,
        cause=refused.cause,
    )


def backoff_delays(retries: int, base: float = 2.0, limit: float = MAX_WAIT_SECONDS) -> list[float]:
    """Growing waits between attempts, never more than `limit` seconds in all. No jitter: guard F5 pins the schedule."""
    delays: list[float] = []
    for attempt in range(retries):
        delay = min(backoff(attempt, base, limit, jitter=0.0), limit - sum(delays))
        if delay <= 0:
            break
        delays.append(delay)
    return delays


class AiKit:
    """The only way a slice calls a model.

    `usage_hooks` is the public way to watch usage: append a function taking a `UsageEvent`; it is called once
    for every answer the kit records in `ai_calls` (right after the row is written), with token counts and ids
    only. A hook that raises is logged by class name and ignored, so it can never fail an AI call. Kits made by
    `with_retries` share the same list."""

    def __init__(
        self,
        settings: AiSettings,
        bedrock: BedrockClient | None,
        *,
        direct: BedrockClient | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        retries: int = 4,
        direct_retries: int = DIRECT_RETRIES,
        base_delay: float = 2.0,
        slot_poll_seconds: float = 1.0,
        usage_hooks: list[Callable[[UsageEvent], None]] | None = None,
    ) -> None:
        self.usage_hooks: list[Callable[[UsageEvent], None]] = [] if usage_hooks is None else usage_hooks
        self.settings = settings
        self.bedrock = bedrock
        self.direct = direct  # temporary, dummy data only (ADR 0036): asked first when it is set
        self.sleep = sleep
        self.retries = retries
        self.direct_retries = direct_retries
        self.base_delay = base_delay
        self.slot_poll_seconds = slot_poll_seconds
        self._last_grants: dict[str, int] = {}  # grants in the last 60 s per provider, as the last poll saw them

    def with_retries(self, retries: int) -> AiKit:
        """The same kit (settings, both clients, sleep and polling) that makes `retries` busy retries on each
        side, for a call that must not wait out a busy model service. `with_retries(0)` keeps the direct side."""
        return AiKit(
            self.settings,
            self.bedrock,
            direct=self.direct,
            sleep=self.sleep,
            retries=retries,
            direct_retries=retries,
            base_delay=self.base_delay,
            slot_poll_seconds=self.slot_poll_seconds,
            usage_hooks=self.usage_hooks,
        )

    @property
    def primary_limits(self) -> ProviderLimits:
        """The first-choice provider's own ceilings: what the pipeline sizes its calls and parallelism by."""
        return self.settings.primary_limits

    def _prepare(self, provider: str | None, task: AiTask) -> str:
        """Check the provider choice and every client in play against the allow-list; the role's Bedrock model id."""
        if provider not in (None, "bedrock"):
            raise ValueError("provider must be None (primary, then fallback) or 'bedrock'")
        if self.bedrock is not None:
            check_endpoint(self.settings, self.bedrock.endpoint)
        if provider is None and self.direct is not None:
            check_endpoint(self.settings, self.direct.endpoint)
        return model_for(self.settings, task.model)

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
        provider: str | None = None,
        person_token_limit: int | None = None,
    ) -> dict[str, Any]:
        """`subject` is what the call is about (`answers.page_subject`), so recorded answers can be found.

        `job_id` is the running job this call belongs to (a worker call; `None` for an interactive API
        call): with it, the call waits its turn under the provider's requests-per-minute ceiling, fairly
        across every job wanting one (`_wait_for_slot`, migrations 0049 and 0055).

        `provider=None` asks the primary provider, then the fallback; `"bedrock"` asks Bedrock only.

        A call with no case has no case allowance. `person_token_limit` gives it one per person: the caller's own
        case-less calls of the last day are counted (AUDIT-14 M10), and the call is paused at the limit."""
        model_id = self._prepare(provider, task)
        if case_id is not None:
            await self._cost_guard(conn, case_id)
        elif person_token_limit is not None:
            await self._person_guard(conn, person_token_limit)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        return await self._ask(conn, task, model_id, case_id, pack, stripped.text, subject, None, job_id, provider)

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
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Like `run`, with a page picture whose identifiers `mask.mask_page` painted out. Raw bytes are refused."""
        if not isinstance(masked, MaskedImage):
            raise TypeError("run_image takes a MaskedImage made by mask.mask_page")
        if not is_genuine(masked):
            raise TypeError("the picture is not the one that was masked")
        model_id = self._prepare(provider, task)
        if case_id is not None:
            await self._cost_guard(conn, case_id)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        return await self._ask(conn, task, model_id, case_id, pack, stripped.text, subject, masked, job_id, provider)

    async def run_detached(
        self,
        database: Database,
        case_id: uuid.UUID,
        task: AiTask,
        *,
        pack: MergedPack,
        text: str,
        names: Sequence[str] = (),
        dates_of_birth: Sequence[date] = (),
        subject: str | None = None,
        job_id: uuid.UUID,
        provider: str | None = None,
    ) -> dict[str, Any]:
        """Like ``run``, but for worker jobs: opens short-lived transactions for each DB
        operation so no pool connection is held during slot waits or model calls."""
        model_id = self._prepare(provider, task)
        if case_id is not None:
            async with database.job(case_id) as conn:
                await self._cost_guard(conn, case_id)
        stripped = pii.strip(text, pack=pack, names=names, dates_of_birth=dates_of_birth)
        return await self._ask_detached(
            database, case_id, task, model_id, pack, stripped.text, subject, job_id, provider
        )

    async def _ask_detached(
        self,
        database: Database,
        case_id: uuid.UUID,
        task: AiTask,
        model_id: str,
        pack: MergedPack,
        text: str,
        subject: str | None,
        job_id: uuid.UUID,
        provider: str | None = None,
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
            image_sha256=None,
            image=None,
        )

        async def wait(slot_provider: str) -> _Release:
            slot_id = await self._wait_for_slot_detached(database, case_id, job_id, slot_provider)
            return lambda: self._release_slot_detached(database, case_id, slot_id)

        validator = jsonschema_rs.validator_for(dict(task.schema))
        for attempt in range(2):
            response = await self._answer(request, task, provider, wait)
            provider = self._same_provider(provider, response)
            response, valid = self._checked(task, response, validator)
            self._log_validation(task, model_id, response, valid, attempt, validator, request.max_tokens)
            async with database.job(case_id) as conn:
                await self._log(
                    conn, task, response.model_id or model_id, case_id, response, "ok" if valid else "invalid_output"
                )
            if valid:
                return response.output
            if looks_cut_off(response.output_tokens, request.max_tokens, response.output, validator):
                logger.warning(
                    "task %s answer was cut off: out_tokens=%d max_tokens=%d",
                    task.task_id,
                    response.output_tokens,
                    request.max_tokens,
                )
                raise OutputTruncated(task.task_id)
        logger.warning("task %s output did not match its schema twice", task.task_id)
        raise InvalidOutput(task.task_id)

    async def _try_overflow(
        self, request: ModelRequest, wait_for_slot: Callable[[], Awaitable[_Release | None]] | None
    ) -> ModelResponse | None:
        """The primary model is throttled: try each in-region overflow model once (its own quota bucket).
        A busy or unavailable overflow model is skipped. None when none answered."""
        assert self.bedrock is not None
        for model_id in self.settings.overflow_models:
            if model_id == request.model_id:
                continue
            try:
                release = await wait_for_slot() if wait_for_slot is not None else None
                try:
                    answer = await self.bedrock.invoke(replace(request, model_id=model_id))
                finally:
                    await self._release(release)
            except (ServiceBusy, ModelUnavailable) as exc:
                logger.warning("overflow model %s skipped (%s)", model_id, type(exc).__name__)
                continue
            logger.warning("primary model busy; overflow model %s answered", model_id)
            return replace(answer, model_id=model_id)
        return None

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
        provider: str | None = None,
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
        wait: _Wait | None = None
        if job_id is not None:

            async def wait_for_slot(slot_provider: str) -> _Release:
                slot_id = await self._wait_for_slot(conn, job_id, slot_provider)
                return lambda: self._release_slot(conn, slot_id)

            wait = wait_for_slot

        validator = jsonschema_rs.validator_for(dict(task.schema))
        for attempt in range(2):
            response = await self._answer(request, task, provider, wait)
            provider = self._same_provider(provider, response)
            response, valid = self._checked(task, response, validator)
            self._log_validation(task, model_id, response, valid, attempt, validator, request.max_tokens)
            await self._log(
                conn, task, response.model_id or model_id, case_id, response, "ok" if valid else "invalid_output"
            )
            if valid:
                return response.output
            if looks_cut_off(response.output_tokens, request.max_tokens, response.output, validator):
                logger.warning(
                    "task %s answer was cut off: out_tokens=%d max_tokens=%d",
                    task.task_id,
                    response.output_tokens,
                    request.max_tokens,
                )
                raise OutputTruncated(task.task_id)
        logger.warning("task %s output did not match its schema twice", task.task_id)
        raise InvalidOutput(task.task_id)

    def _checked(self, task: AiTask, response: ModelResponse, validator: Any) -> tuple[ModelResponse, bool]:
        """The answer and whether it matches the schema, once any off-list labels are mended (`mend_labels`).

        An answer nested deeper than `MAX_ANSWER_DEPTH` is wrong without being read further: it is replaced by an
        empty one in the result, so no later step walks it (AUDIT-14 O12)."""
        if too_deep(response.output):
            return replace(response, output={}), False
        try:
            return self._checked_within_depth(task, response, validator)
        except (ValueError, RecursionError):  # the validator's own limit: still a wrong answer, never an exception
            return replace(response, output={}), False

    def _checked_within_depth(
        self, task: AiTask, response: ModelResponse, validator: Any
    ) -> tuple[ModelResponse, bool]:
        if validator.is_valid(response.output):
            return response, True
        if not task.mends_labels:
            return response, False
        mended, places = mend_labels(response.output, validator)
        if not places or not validator.is_valid(mended):
            return response, False
        logger.warning(
            "task %s answer had off-list labels count=%d at=%s",
            task.task_id,
            len(places),
            ",".join(sorted(set(places))),
        )
        return replace(response, output=mended), True

    def _same_provider(self, provider: str | None, response: ModelResponse) -> str | None:
        """A wrong answer is asked again of the provider that gave it, never of the other: after one from
        Bedrock the next ask is Bedrock's alone. (After a direct one it stays open, so a failed re-ask can
        still fall back: that is a failure, not a wrong answer.)"""
        direct = response.model_id is not None and response.model_id in self.settings.direct_models.values()
        return provider if provider is not None or direct or self.direct is None else "bedrock"

    def _log_validation(
        self,
        task: AiTask,
        model_id: str,
        response: ModelResponse,
        valid: bool,
        attempt: int,
        validator: Any,
        max_tokens: int,
    ) -> None:
        """Only a wrong answer is logged here (the call itself is logged by its client): who answered, which try,
        and why. The why is the schema's own path and keyword (never the answer, its keys or any value, rule 6),
        plus the answer's size against the limit, so a cut-off answer can be told from a wrong shape."""
        if valid:
            return
        direct = response.model_id is not None and response.model_id in self.settings.direct_models.values()
        errors = []
        for error in validator.iter_errors(response.output):
            path = ".".join("*" if isinstance(part, int) else str(part) for part in error.schema_path)
            errors.append(f"{type(error.kind).__name__}:{path}")
            if len(errors) == 3:
                break
        logger.warning(
            "model answer rejected: provider=%s model=%s task=%s outcome=invalid_output schema_attempt=%d/2"
            " out_tokens=%d max_tokens=%d top_level_fields=%d errors=%s",
            "anthropic" if direct else "bedrock",
            response.model_id or model_id,
            task.task_id,
            attempt + 1,
            response.output_tokens,
            max_tokens,
            len(response.output),
            ",".join(errors) or "-",
        )

    async def _cost_guard(self, conn: AsyncConnection, case_id: uuid.UUID) -> None:
        cur = await conn.execute("select tokens_used, pages from app.ai_budget(%s)", (case_id,))
        row = await cur.fetchone()
        used, pages = (int(row[0]), int(row[1])) if row else (0, 0)
        allowance = self.settings.cost_guard.allowance(pages)
        if used >= allowance:
            logger.error("cost_guard tripped for case %s: %d of %d tokens used", case_id, used, allowance)
            raise ProcessingPaused("cost_guard")

    async def _person_guard(self, conn: AsyncConnection, limit: int) -> None:
        """Pause a case-less call once the caller has used `limit` tokens in the last day. Row-level security shows
        a person only their own case-less rows; the `person_id` test is repeated so it does not depend on that."""
        cur = await conn.execute(
            "select coalesce(sum(input_tokens + output_tokens), 0) from app.ai_calls"
            " where case_id is null and person_id = app.current_person_id() and at > now() - interval '1 day'"
        )
        row = await cur.fetchone()
        used = int(row[0]) if row else 0
        if used >= limit:
            logger.error("cost_guard tripped for a person: %d of %d tokens used today", used, limit)
            raise ProcessingPaused("cost_guard")

    async def _answer(
        self, request: ModelRequest, task: AiTask, provider: str | None, wait: _Wait | None
    ) -> ModelResponse:
        """One model answer: the direct API first when it is set and has a model for the role, then Bedrock."""
        direct_id = self.settings.direct_models.get(task.model) if provider is None and self.direct else None
        if direct_id is None:
            return await self._invoke_bedrock(request, wait)
        try:
            return await self._invoke_direct(replace(request, model_id=direct_id), wait)
        except (ServiceBusy, ModelUnavailable, ModelRequestRefused) as failure:
            # Temporary, dummy data only (ADR 0036). The exception type only: its text may carry content.
            bedrock_limits = self.settings.limits["bedrock"]
            too_large = request.max_tokens > bedrock_limits.max_output_tokens
            reason = {ServiceBusy: "busy_after_retries", ModelUnavailable: "unavailable"}.get(type(failure), "refused")
            logger.warning(
                "direct failed -> %s: provider=anthropic task=%s reason=%s %s"
                " size_check=%s max_tokens=%d bedrock_max=%d",
                "none" if self.bedrock is None else "bedrock",
                request.task_id or "-",
                reason,
                failure.log_fields(),
                "too_large" if too_large else "ok",
                request.max_tokens,
                bedrock_limits.max_output_tokens,
            )
            if self.bedrock is None:
                if isinstance(failure, ServiceBusy):
                    raise ProcessingPaused("service_busy") from None
                raise ProcessingPaused("model_unavailable") from None  # gone, spend-capped or refused
            if too_large:
                raise TooLargeForFallback(bedrock_limits.pages_per_call) from None
        return await self._invoke_bedrock(request, wait)

    async def _invoke(
        self, request: ModelRequest, conn: AsyncConnection | None = None, job_id: uuid.UUID | None = None
    ) -> ModelResponse:
        """The Bedrock leg alone, with overflow models and, given a job, its own request slots."""
        wait: _Wait | None = None
        if conn is not None and job_id is not None:

            async def wait_for_slot(slot_provider: str) -> _Release:
                assert conn is not None and job_id is not None
                slot_id = await self._wait_for_slot(conn, job_id, slot_provider)
                return lambda: self._release_slot(conn, slot_id)

            wait = wait_for_slot
        return await self._invoke_bedrock(request, wait)

    async def _invoke_direct(self, request: ModelRequest, wait: _Wait | None) -> ModelResponse:
        """The direct API, retried while it is only busy: its per-minute token limits clear quickly, and a
        burst must not spill onto Bedrock's small ceiling. `retry-after` is honoured up to 30 s."""
        assert self.direct is not None
        for attempt in range(self.direct_retries + 1):
            try:
                release = await wait("anthropic") if wait is not None else None
                # The client's one log line per attempt says which attempt this is.
                token = CALL_ATTEMPT.set(f"{attempt + 1}/{self.direct_retries + 1}")
                try:
                    return await self.direct.invoke(request)
                finally:
                    CALL_ATTEMPT.reset(token)
                    await self._release(release)
            except ServiceBusy as busy:
                if attempt == self.direct_retries:
                    raise
                seconds = min(busy.retry_after or self.base_delay * 2**attempt, DIRECT_MAX_WAIT_SECONDS)
                logger.warning(
                    "direct retry: provider=anthropic task=%s next_attempt=%d/%d wait_s=%.0f retry_after=%s",
                    request.task_id or "-",
                    attempt + 2,
                    self.direct_retries + 1,
                    seconds,
                    "-" if busy.retry_after is None else busy.retry_after,
                )
                await self.sleep(seconds)
        raise AssertionError("unreachable")  # pragma: no cover

    async def _invoke_bedrock(self, request: ModelRequest, wait: _Wait | None) -> ModelResponse:
        """The Bedrock leg. A refusal is re-raised carrying only a code of a safe shape, outside the handler so the
        original (whose message a client may have filled from the provider's body) is not kept as its context."""
        refused: ModelRequestRefused | None = None
        try:
            return await self._invoke_bedrock_checked(request, wait)
        except ModelRequestRefused as exc:
            refused = exc
        raise vetted_refusal(refused)

    async def _invoke_bedrock_checked(self, request: ModelRequest, wait: _Wait | None) -> ModelResponse:
        if self.bedrock is None:
            logger.error("no in-region model client; processing paused")
            raise ProcessingPaused("model_unavailable")
        delays = backoff_delays(self.retries, self.base_delay)
        overflow_tried = False  # overflow models are tried once per call, after the first busy answer
        for attempt in range(len(delays) + 1):
            try:
                release = await wait("bedrock") if wait is not None else None
                try:
                    return replace(await self.bedrock.invoke(request), model_id=request.model_id)
                finally:
                    await self._release(release)
            except ServiceBusy as busy:
                if not overflow_tried:
                    overflow_tried = True
                    overflow = await self._try_overflow(
                        request, (lambda: wait("bedrock")) if wait is not None else None
                    )
                    if overflow is not None:
                        return overflow
                if attempt == len(delays):
                    logger.error("model service still busy after %d attempts; processing paused", attempt + 1)
                    raise ProcessingPaused("service_busy") from None
                seconds = max(delays[attempt], busy.retry_after or 0.0)
                logger.warning("model service busy; waiting %.0f s (attempt %d)", seconds, attempt + 1)
                await self.sleep(seconds)
            except ModelUnavailable:
                logger.error("in-region model unavailable; processing paused, no fallback")
                raise ProcessingPaused("model_unavailable") from None
        raise AssertionError("unreachable")  # pragma: no cover

    async def _wait_for_slot(self, conn: AsyncConnection, job_id: uuid.UUID, provider: str = "bedrock") -> uuid.UUID:
        """Wait until this job may make its next call to `provider`, under that provider's own
        requests-per-minute ceiling (`settings.limits[provider].rpm`; for Bedrock the account-wide
        on-demand ceiling `bedrock_rpm_limit`, not raisable per tenant or per case) and its in-flight
        ceiling (`concurrency`; for Bedrock `BEDROCK_CONCURRENCY`). Providers never share a pool: each
        slot row says which provider it is for (migration 0055). Both counts cover every case on the
        account, through `app.ai_slots_in_use` (migration 0056), because the table's policy shows a job
        only its own case's rows. Returns the granted slot's id, to release once the call has ended.

        A session-level advisory lock (not `pg_advisory_xact_lock`, used elsewhere: that variant
        cannot be released before the transaction ends, and this check must release it between
        polls so every other waiting job gets a turn) guards the read-then-write in `_slot_attempt`.
        Rows in `app.ai_request_slots` (migration 0049) older than the ceiling's 60-second window no
        longer count; among jobs still waiting for the same provider, the one whose own last grant
        (`app.jobs.last_granted_at`, oldest or never granted first) is longest ago goes next, so no job
        can starve another by polling faster. `last_granted_at` is shared by both providers, a small
        fairness effect between them that is accepted for the temporary direct primary. While waiting,
        the job's `step` is set to `waiting_for_capacity`, which the existing progress stream
        (`core/routes/jobs.py`) already shows the browser.
        """
        started = time.monotonic()
        waited = False
        while (slot_id := await self._slot_attempt(conn, job_id, provider)) is None:
            if not waited:
                self._log_wait(provider)
            waited = True
            await self.sleep(self._poll_seconds(provider))
        self._log_slot(provider, started, waited)
        return slot_id

    def _poll_seconds(self, provider: str) -> float:
        """How long a waiting job sleeps between polls: the configured poll, but at most half the provider's
        window, so a per-second ceiling is noticed within half a second."""
        return min(self.slot_poll_seconds, self.settings.limits[provider].window_seconds / 2)

    @contextlib.asynccontextmanager
    async def slot(
        self, database: Database, case_id: uuid.UUID, job_id: uuid.UUID, provider: str
    ) -> AsyncIterator[None]:
        """Hold one of `provider`'s account-wide slots for the length of the block (for calls the kit does not
        make itself, such as a page sent to Azure OCR). No connection is held while waiting or inside the block."""
        slot_id = await self._wait_for_slot_detached(database, case_id, job_id, provider)
        try:
            yield
        finally:
            await self._release(lambda: self._release_slot_detached(database, case_id, slot_id))

    async def _wait_for_slot_detached(
        self, database: Database, case_id: uuid.UUID, job_id: uuid.UUID, provider: str = "bedrock"
    ) -> uuid.UUID:
        """Like ``_wait_for_slot``, but opens a short-lived transaction for each poll so no
        connection is held during the sleep between iterations."""
        started = time.monotonic()
        waited = False
        while True:
            async with database.job(case_id) as conn:
                slot_id = await self._slot_attempt(conn, job_id, provider, own_transaction=True)
            if slot_id is not None:
                break
            # Connection is back in the pool here. Sleep holds nothing.
            if not waited:
                self._log_wait(provider)
            waited = True
            await self.sleep(self._poll_seconds(provider))
        self._log_slot(provider, started, waited)
        return slot_id

    async def _release(self, release: _Release | None) -> None:
        """End a granted slot. A failure to release never hides the call's own answer or error: the slot
        stops counting as in flight after ten minutes anyway (migration 0056)."""
        if release is None:
            return
        try:
            await release()
        except Exception as exc:
            logger.warning("slot release failed: %s", type(exc).__name__)

    async def _release_slot(self, conn: AsyncConnection, slot_id: uuid.UUID) -> None:
        await conn.execute("update app.ai_request_slots set in_flight = false where id = %s", (slot_id,))

    async def _release_slot_detached(self, database: Database, case_id: uuid.UUID, slot_id: uuid.UUID) -> None:
        async with database.job(case_id) as conn:
            await self._release_slot(conn, slot_id)

    def _log_wait(self, provider: str) -> None:
        """The first poll that did not get a slot: once per wait, numbers only."""
        logger.info(
            "slot waiting: provider=%s grants_60s=%d limit=%d",
            provider,
            self._last_grants.get(provider, 0),
            self.settings.limits[provider].rpm,
        )

    def _log_slot(self, provider: str, started: float, waited: bool) -> None:
        """A slot granted: numbers only. INFO when the call had to wait, DEBUG when it went straight through."""
        logger.log(
            logging.INFO if waited else logging.DEBUG,
            "slot granted: provider=%s waited_s=%.1f grants_60s=%d limit=%d",
            provider,
            time.monotonic() - started,
            self._last_grants.get(provider, 0),
            self.settings.limits[provider].rpm,
        )

    async def _slot_attempt(
        self, conn: AsyncConnection, job_id: uuid.UUID, provider: str, *, own_transaction: bool = False
    ) -> uuid.UUID | None:
        """One poll: queue this job for a slot of `provider` if it is not queued, grant it if the
        provider's window and in-flight count have room (across every case) and it is next in fair
        order. The granted slot's id, or None while it must wait.

        `own_transaction`: the poll is the whole transaction (the detached waits), so the lock is held to
        its commit. A session lock released before COMMIT lets the next poller read the row this one just
        granted as still waiting and hand out the same slot twice (seen with 30 pages of one job at once)."""
        lock = SLOT_LOCK if provider == "bedrock" else f"{SLOT_LOCK}:{provider}"
        if own_transaction:
            await conn.execute("select pg_advisory_xact_lock(hashtextextended(%s, 0))", (lock,))
        else:
            await conn.execute("select pg_advisory_lock(hashtextextended(%s, 0))", (lock,))
        try:
            cur = await conn.execute(
                "select id from app.ai_request_slots where job_id = %s and provider = %s and granted_at is null"
                " order by requested_at limit 1",
                (job_id, provider),
            )
            row = await cur.fetchone()
            if row is None:
                cur = await conn.execute(
                    "insert into app.ai_request_slots (job_id, case_id, requested_at, provider)"
                    " select %s, case_id, now(), %s from app.jobs where id = %s returning id",
                    (job_id, provider, job_id),
                )
                row = await cur.fetchone()
            assert row is not None  # the job row must exist: its own job is running this call
            slot_id = row[0]

            limits = self.settings.limits[provider]
            cur = await conn.execute(
                "select in_flight, granted_in_window from app.ai_slots_in_use(%s, make_interval(secs => %s))",
                (provider, limits.window_seconds),
            )
            count_row = await cur.fetchone()
            assert count_row is not None
            in_flight, granted_count = count_row
            self._last_grants[provider] = granted_count
            if granted_count < limits.rpm and in_flight < limits.concurrency:
                cur = await conn.execute(
                    "select s.job_id from app.ai_request_slots s join app.jobs j on j.id = s.job_id"
                    " where s.granted_at is null and s.provider = %s"
                    " and j.status in ('queued', 'running')"
                    " order by j.last_granted_at is not null, j.last_granted_at, s.requested_at limit 1",
                    (provider,),
                )
                next_up = await cur.fetchone()
                if next_up is not None and next_up[0] == job_id:
                    await conn.execute(
                        "update app.ai_request_slots set granted_at = now(), in_flight = true where id = %s", (slot_id,)
                    )
                    await conn.execute("update app.jobs set last_granted_at = now() where id = %s", (job_id,))
                    return slot_id  # type: ignore[no-any-return]
            await conn.execute("update app.jobs set step = 'waiting_for_capacity' where id = %s", (job_id,))
            logger.debug(
                "slot waiting: provider=%s grants_60s=%d limit=%d",
                provider,
                granted_count,
                self.settings.limits[provider].rpm,
            )
            return None
        finally:
            if not own_transaction:
                await conn.execute("select pg_advisory_unlock(hashtextextended(%s, 0))", (lock,))

    async def _log(
        self,
        conn: AsyncConnection,
        task: AiTask,
        model_id: str,
        case_id: uuid.UUID | None,
        response: ModelResponse,
        outcome: str,
    ) -> None:
        answered_directly = response.model_id is not None and response.model_id in self.settings.direct_models.values()
        model_id = response.model_id or model_id
        # A direct-API answer is told apart by its region (temporary, ADR 0036).
        region = "anthropic-direct" if answered_directly else self.settings.bedrock_region
        await conn.execute(
            "insert into app.ai_calls (case_id, person_id, task_id, model_id, region, prompt_version,"
            " input_tokens, output_tokens, outcome) values (%s, app.current_person_id(), %s, %s, %s, %s, %s, %s, %s)",
            (
                case_id,
                task.task_id,
                model_id,
                region,
                task.prompt_version,
                response.input_tokens,
                response.output_tokens,
                outcome,
            ),
        )
        if self.usage_hooks:
            event = UsageEvent(
                task.task_id, region, model_id, response.input_tokens, response.output_tokens, outcome == "ok"
            )
            for hook in list(self.usage_hooks):
                try:
                    hook(event)
                except Exception as exc:  # a watcher must never fail an AI call; class name only (rule 6)
                    logger.warning("usage hook failed: %s", type(exc).__name__)
