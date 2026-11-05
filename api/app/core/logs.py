"""Rule 6: no patient text in logs or error reports (architecture 9 "Logs", F10).

- Every log record is scrubbed when it is made: its message is formatted, then query strings,
  e-mail addresses, long digit runs and every identifier a loaded jurisdiction pack recognises are
  removed; its arguments are dropped. An exception keeps its type and stack (file, line, function),
  never its message or local values.
- Requests are logged by route template, status and time, never by path, query or body.
- Sentry gets no personal data: no request body, cookies, headers or query, no exception messages,
  no local variables; breadcrumbs are scrubbed the same way.
- `find_identifiers` is the log scanner (F10): it says where an identifier is, never what.
"""

from __future__ import annotations

import logging
import os
import re
import time
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.db import statement_count

Finder = Callable[[str], list[tuple[int, int, str]]]

_URL = re.compile(r"\b((?:https?|wss?)://[^/\s\"'?]+)[^\s\"']*")
_QUERY = re.compile(r"\?[^\s\"']*")
_EMAIL = re.compile(r"[\w.+\-]+@[\w\-]+(?:\.[\w\-]+)+")
_DIGITS = re.compile(r"(?<![\w-])\d(?:[ \-]?\d){6,}(?![\w-])")

_finders: list[Finder] | None = None
_base_factory: Callable[..., logging.LogRecord] | None = None


def _active_finders() -> list[Finder]:
    global _finders
    if _finders is None:
        from app.core.packs import default_library

        _finders = default_library().identifier_finders()
    return _finders


def scrub(line: str, finders: Iterable[Finder] | None = None) -> str:
    """The line without query strings, e-mail addresses, long numbers or pack identifiers."""
    line = _URL.sub(r"\1/[path]", line)
    line = _QUERY.sub("?[removed]", line)
    line = _EMAIL.sub("[email]", line)
    for finder in finders if finders is not None else _active_finders():
        for start, end, kind in sorted(finder(line), reverse=True):
            line = f"{line[:start]}[{kind}]{line[end:]}"
    return _DIGITS.sub("[number]", line)


def safe_traceback(exc: BaseException) -> str:
    """Exception types and frames (file, line, function) only: no messages, no source, no values."""
    lines = ["Traceback (most recent call last):"]
    for frame in traceback.extract_tb(exc.__traceback__):
        lines.append(f'  File "{frame.filename}", line {frame.lineno}, in {frame.name}')
    lines.append(type(exc).__name__)
    cause = exc.__cause__ or exc.__context__
    if cause is not None:
        lines.append(f"(caused by {type(cause).__name__})")
    return "\n".join(lines)


def _scrubbing_factory(*args: Any, **kwargs: Any) -> logging.LogRecord:
    assert _base_factory is not None
    record = _base_factory(*args, **kwargs)
    try:
        message = record.getMessage()
    except Exception:  # noqa: BLE001 - a bad format string must not break logging
        message = str(record.msg)
    record.msg = scrub(message)
    record.args = None
    if record.exc_info:
        exc = record.exc_info[1]
        record.exc_text = safe_traceback(exc) if exc is not None else None
        record.exc_info = None
    record.stack_info = None
    return record


def configure(finders: Sequence[Finder] | None = None) -> None:
    """Install the scrubbing record factory for the whole process (idempotent).

    HTTP client libraries log every URL they call at INFO; those lines and the server's access log are
    switched off (FastAPI's own requests are logged by `RequestLog`), and any URL that still reaches a
    line loses its path.
    """
    global _base_factory, _finders
    for name in ("httpx", "httpcore", "hpack"):
        logging.getLogger(name).setLevel(logging.WARNING)
    # The server's access lines carry raw paths and queries; `RequestLog` logs by route template.
    logging.getLogger("uvicorn.access").disabled = True
    access = logging.getLogger("app.request")
    access.setLevel(logging.INFO)
    if not access.handlers:
        access.addHandler(logging.StreamHandler())  # on by default, whatever the root logger is set to
    if finders is not None:
        _finders = list(finders)
    if _base_factory is None:
        _base_factory = logging.getLogRecordFactory()
        logging.setLogRecordFactory(_scrubbing_factory)


@dataclass(frozen=True)
class Finding:
    """Where the scanner found an identifier: line number and kind, never the value."""

    line: int
    kind: str


def find_identifiers(lines: Iterable[str], finders: Finder | Iterable[Finder]) -> list[Finding]:
    """F10 log scanner: every pack identifier in `lines`."""
    active = [finders] if callable(finders) else list(finders)
    return [
        Finding(number, kind) for number, line in enumerate(lines) for finder in active for _, _, kind in finder(line)
    ]


class RequestLog:
    """ASGI middleware: one line per request with method, route template, status and time."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.logger = logging.getLogger("app.request")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = time.perf_counter()
        status = 500
        counter = [0]
        statement_count.set(counter)

        async def capture(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = int(message["status"])
                millis = (time.perf_counter() - started) * 1000
                timing = f'app;dur={millis:.1f}, db;desc="queries={counter[0]}"'
                message = {**message, "headers": [*message.get("headers", []), (b"server-timing", timing.encode())]}
            await send(message)

        try:
            await self.app(scope, receive, capture)
        finally:
            route = getattr(scope.get("route"), "path", "unmatched")
            elapsed = (time.perf_counter() - started) * 1000
            self.logger.info("%s %s %d queries=%d %.0fms", scope.get("method", ""), route, status, counter[0], elapsed)


# --- Sentry ----------------------------------------------------------------------------------------------------------

_REQUEST_KEEP = ("method",)


def _denied_keys() -> frozenset[str]:
    from sentry_sdk.scrubber import DEFAULT_DENYLIST, DEFAULT_PII_DENYLIST

    return frozenset(k.lower() for k in (*DEFAULT_DENYLIST, *DEFAULT_PII_DENYLIST))


def _scrub_value(value: Any) -> Any:
    """Free text scrubbed; whatever sits under a sensitive key (password, token, cookie...) replaced outright."""
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        denied = _denied_keys()
        return {k: "[Filtered]" if str(k).lower() in denied else _scrub_value(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub_value(v) for v in value]
    return value


def before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {k: request[k] for k in _REQUEST_KEEP if k in request}
    for key in ("user", "extra", "breadcrumbs"):
        event.pop(key, None)
    for value in (event.get("exception") or {}).get("values", []):
        value["value"] = ""
        for frame in (value.get("stacktrace") or {}).get("frames", []):
            for drop in ("vars", "context_line", "pre_context", "post_context"):
                frame.pop(drop, None)
    for key in ("logentry", "message"):
        entry = event.get(key)
        if isinstance(entry, dict):
            event[key] = {"message": scrub(str(entry.get("formatted") or entry.get("message") or ""))}
        elif isinstance(entry, str):
            event[key] = scrub(entry)
    if "contexts" in event:
        event["contexts"] = _scrub_value(event["contexts"])
    if isinstance(event.get("tags"), dict):  # the SDK's scrubber leaves tags alone
        event["tags"] = _scrub_value(event["tags"])
    return event


def before_send_transaction(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Performance traces (sampled): the same scrubbing as errors, plus the spans' text and data.

    The SDK does not run `before_send` on transactions, so a URL with a query string, a span description
    (a SQL text, a URL) or span data would otherwise leave the process unscrubbed (rule 6).
    """
    request = event.get("request")
    if isinstance(request, dict):
        event["request"] = {k: request[k] for k in _REQUEST_KEEP if k in request}
    for key in ("user", "extra", "breadcrumbs"):
        event.pop(key, None)
    if isinstance(event.get("transaction"), str):
        event["transaction"] = scrub(event["transaction"])
    for key in ("contexts", "tags"):
        if key in event:
            event[key] = _scrub_value(event[key])
    trace = (event.get("contexts") or {}).get("trace")
    if isinstance(trace, dict):
        trace.pop("data", None)  # raw URL, query string and path values
    for span in event.get("spans") or []:
        if isinstance(span.get("description"), str):
            span["description"] = scrub(span["description"])
        span.pop("data", None)
        if isinstance(span.get("tags"), dict):
            span["tags"] = _scrub_value(span["tags"])
    return event


def before_breadcrumb(crumb: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    crumb.pop("data", None)
    if isinstance(crumb.get("message"), str):
        crumb["message"] = scrub(crumb["message"])
    return crumb


DEFAULT_TRACES_SAMPLE_RATE = 0.05


def traces_sample_rate() -> float:
    """`SENTRY_TRACES_SAMPLE_RATE` (0 to 1); a missing or bad value is the default."""
    try:
        rate = float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", ""))
    except ValueError:
        return DEFAULT_TRACES_SAMPLE_RATE
    return rate if 0.0 <= rate <= 1.0 else DEFAULT_TRACES_SAMPLE_RATE


def sentry_options(dsn: str, *, environment: str) -> dict[str, Any]:
    from sentry_sdk.scrubber import EventScrubber

    return {
        "dsn": dsn,
        "environment": environment,
        "send_default_pii": False,
        "include_local_variables": False,
        "include_source_context": False,
        "max_request_body_size": "never",
        "event_scrubber": EventScrubber(recursive=True),
        "before_send": before_send,
        "before_send_transaction": before_send_transaction,
        "before_breadcrumb": before_breadcrumb,
        "traces_sample_rate": traces_sample_rate(),
    }


def init_sentry(dsn: str, *, environment: str, transport: Any = None) -> None:
    """Start error reporting (Sentry EU in a cell; a capturing transport in tests)."""
    import sentry_sdk

    options = sentry_options(dsn, environment=environment)
    if transport is not None:
        options["transport"] = transport
    sentry_sdk.init(**options)
