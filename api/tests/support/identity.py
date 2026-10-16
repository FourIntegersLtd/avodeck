"""Test identity seam: who is signed in comes from request headers (tests only).

The auth step replaces this with the cookie session; production code has no such provider,
so `create_app()` without one treats every request as signed out.
"""

from __future__ import annotations

import uuid

from fastapi import Request

from app.core.auth.identity import Identity

PERSON_HEADER = "x-test-person"


async def header_identity(request: Request) -> Identity | None:
    raw = request.headers.get(PERSON_HEADER)
    if not raw:
        return None
    person = uuid.UUID(raw)
    return Identity(
        person_id=person,
        email=request.headers.get("x-test-email", f"{person.hex[:12]}@example.test"),
        full_name=request.headers.get("x-test-name"),
    )
