"""Request scopes for tests that open `Database.request()` directly."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable

import httpx

from app.core.db import RequestScope

# `await client_for(person, **headers)` (fixture in conftest.py)
ClientFor = Callable[..., Awaitable[httpx.AsyncClient]]


def scope_for(person: uuid.UUID) -> RequestScope:
    return RequestScope(person_id=person, org_ids=(), request_id="req-test")
