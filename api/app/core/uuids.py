"""Reading a UUID from a value of unknown shape; the name says what happens to a bad one."""

from __future__ import annotations

import uuid
from typing import Any


def require_uuid(value: Any) -> uuid.UUID:
    """The UUID, or ValueError."""
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def uuid_or_none(value: Any) -> uuid.UUID | None:
    """The UUID, or None for anything that is not one (including None)."""
    if value is None:
        return None
    try:
        return require_uuid(value)
    except ValueError:
        return None
