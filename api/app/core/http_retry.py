"""Waiting before a retry, shared by every outbound HTTP caller (AI kit, OCR, Bedrock, mail).

Each caller keeps its own business logic (slot waits, pausing a job); this only answers "how long".
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime

MAX_RETRY_AFTER = 300.0  # no caller waits longer than this because a server asked it to


def parse_retry_after(value: str | None, cap: float = MAX_RETRY_AFTER, now: datetime | None = None) -> float | None:
    """A `Retry-After` header (seconds or HTTP date) as seconds to wait, at most `cap`; None if absent or unreadable."""
    if value is None or not value.strip():
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if math.isnan(seconds) or math.isinf(seconds) or seconds < 0 and text[0] == "-":
        return None
    return min(max(seconds, 0.0), cap)


def backoff(
    attempt: int, base: float, cap: float, rng: Callable[[], float] = random.random, jitter: float = 0.5
) -> float:
    """The wait after failed attempt number `attempt` (from 0): doubling from `base`, at most `cap`, then
    shortened by up to `jitter` (a fraction) at random, so callers that failed together do not retry together.
    Never above the unjittered wait, so totals computed from the nominal waits still hold."""
    nominal = min(base * 2**attempt, cap)
    return float(nominal * (1.0 - jitter * (1.0 - rng())))
