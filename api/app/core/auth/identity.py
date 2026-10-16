"""Who is signed in. The auth step's cookie session provides it; until then only tests do."""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from fastapi import Request


@dataclass(frozen=True)
class Identity:
    person_id: uuid.UUID
    email: str
    full_name: str | None = None
    mfa_enrolled: bool = False


# Resolves the request's identity; None = signed out. Set on the app by `create_app()`.
IdentityProvider = Callable[[Request], Awaitable[Identity | None]]
