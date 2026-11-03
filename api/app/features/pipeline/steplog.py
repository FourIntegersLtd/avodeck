"""One log format for every pipeline step, so a failure can be pinpointed from the log alone.

Every line is `pipeline step=<step> event=<event> job=<id> case=<id> key=value ...`:

- `start`: unit, todo, done (already finished, skipped), concurrency;
- `page_failed` (WARNING): page number, attempt, outcome, exception class, HTTP status, vendor request id;
- `failed_pages` (WARNING): the failed page numbers, at most 50 and then `+N more`;
- `end`: outcome, reason code, ok, failed, skipped, total ms, slowest page ms.

Rule 6: only ids, page numbers, counts, milliseconds, status codes and exception CLASS names are written.
Never `str(exc)`, a page's text, a prompt or an answer: an exception message may carry patient text.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from app.core.errors import ProcessingPaused

if TYPE_CHECKING:
    from psycopg import AsyncConnection

    from app.core.jobs import JobContext

logger = logging.getLogger(__name__)

FAILED_PAGES_SHOWN = 50
_HTTP_CODE = re.compile(r"http_(\d{3})")  # how the OCR client words a refusal: a status, never text
_REQUEST_ID = re.compile(r"[A-Za-z0-9-]{8,64}")  # a vendor request id; anything else is dropped

Pending = Callable[["AsyncConnection", uuid.UUID], Awaitable[tuple[str, int, int]]]  # unit, to do, already done
_current: ContextVar[StepRun | None] = ContextVar("pipeline_step_run", default=None)


def current() -> StepRun | None:
    """The step being run by `logged_step` in this task, if any."""
    return _current.get()


def _status(exc: BaseException) -> str:
    for name in ("status", "status_code"):
        value = getattr(exc, name, None)
        if isinstance(value, int) and 100 <= value < 600:
            return str(value)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):  # boto-style
        value = (response.get("ResponseMetadata") or {}).get("HTTPStatusCode")
        if isinstance(value, int) and 100 <= value < 600:
            return str(value)
    elif response is not None:
        value = getattr(response, "status_code", None)
        if isinstance(value, int) and 100 <= value < 600:
            return str(value)
    first = exc.args[0] if exc.args else None
    if isinstance(first, str) and (found := _HTTP_CODE.fullmatch(first)):
        return found.group(1)
    return "-"


def _request_id(exc: BaseException) -> str:
    candidates: list[Any] = [getattr(exc, "request_id", None)]
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        candidates.append((response.get("ResponseMetadata") or {}).get("RequestId"))
    elif response is not None:
        headers = getattr(response, "headers", None)
        if headers is not None:
            candidates.append(headers.get("apim-request-id"))
            candidates.append(headers.get("x-request-id"))
    for value in candidates:
        if isinstance(value, str) and _REQUEST_ID.fullmatch(value):
            return value
    return "-"


class StepRun:
    def __init__(
        self,
        job_id: uuid.UUID,
        case_id: uuid.UUID,
        step: str,
        *,
        unit: str,
        todo: int,
        done: int,
        concurrency: int,
        attempt: int,
    ) -> None:
        self.job_id, self.case_id, self.step = job_id, case_id, step
        self.unit, self.todo, self.done, self.concurrency, self.attempt = unit, todo, done, concurrency, attempt
        self.ok = 0
        self.failed: list[int | None] = []
        self.slowest_ms = 0.0
        self.measured = False
        self._started = time.perf_counter()
        self._failed_keys: set[int] = set()

    def _head(self, event: str) -> str:
        return f"pipeline step={self.step} event={event} job={self.job_id} case={self.case_id}"

    def begin(self) -> None:
        self._started = time.perf_counter()
        logger.info(
            "%s unit=%s todo=%d done=%d concurrency=%d",
            self._head("start"),
            self.unit,
            self.todo,
            self.done,
            self.concurrency,
        )

    def has_failed(self, page: int | None) -> bool:
        return page is not None and page in self._failed_keys

    def page_ok(self, page: int | None, ms: float) -> None:
        self.measured = True
        self.slowest_ms = max(self.slowest_ms, ms)
        if not self.has_failed(page):
            self.ok += 1

    def page_failed(self, page: int | None, exc: BaseException, outcome: str = "failed", ms: float = 0.0) -> None:
        self.measured = True
        self.slowest_ms = max(self.slowest_ms, ms)
        self.failed.append(page)
        if page is not None:
            self._failed_keys.add(page)
        logger.warning(
            "%s page=%s attempt=%d outcome=%s error=%s status=%s request=%s",
            self._head("page_failed"),
            "-" if page is None else page,
            self.attempt,
            outcome,
            type(exc).__name__,
            _status(exc),
            _request_id(exc),
        )

    def end(self, outcome: str = "ok", reason: str | None = None, error: BaseException | None = None) -> None:
        pages = sorted({p for p in self.failed if p is not None})
        if pages:
            shown = ",".join(str(p) for p in pages[:FAILED_PAGES_SHOWN])
            more = len(pages) - FAILED_PAGES_SHOWN
            logger.warning("%s pages=%s", self._head("failed_pages"), shown + (f"+{more}more" if more > 0 else ""))
        ok = self.ok if self.measured else (self.todo if outcome == "ok" else 0)
        logger.info(
            "%s outcome=%s reason=%s error=%s ok=%d failed=%d skipped=%d ms=%d slowest_ms=%s",
            self._head("end"),
            outcome,
            reason or "-",
            type(error).__name__ if error is not None else "-",
            ok,
            len(self.failed),
            self.done,
            (time.perf_counter() - self._started) * 1000,
            f"{self.slowest_ms:.0f}" if self.measured else "-",
        )


@contextlib.asynccontextmanager
async def logged_step(ctx: JobContext, step: str, pending: Pending, concurrency: int) -> AsyncIterator[StepRun]:
    """Logs the step's start, then its end however it ends (done, paused with its reason, failed, cancelled)."""
    async with ctx.transaction() as conn:
        unit, todo, done = await pending(conn, ctx.case_id)
    run = StepRun(
        ctx.job_id, ctx.case_id, step, unit=unit, todo=todo, done=done, concurrency=concurrency, attempt=ctx.attempt
    )
    run.begin()
    token = _current.set(run)
    try:
        yield run
    except ProcessingPaused as paused:
        run.end("paused", reason=paused.reason)
        raise
    except asyncio.CancelledError:
        run.end("cancelled")
        raise
    except Exception as exc:
        run.end("failed", error=exc)
        raise
    else:
        run.end()
    finally:
        _current.reset(token)
