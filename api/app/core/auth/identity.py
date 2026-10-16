"""Who is signed in, taken from the verified device session (app/core/auth/session.py)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Identity:
    person_id: uuid.UUID
    email: str = field(repr=False)
    full_name: str | None = field(default=None, repr=False)
    mfa_enrolled: bool = False
