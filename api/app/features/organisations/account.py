"""The person's own account (architecture 10.1): records custodian and account closure. SQL lives in `repo.py`."""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.features.organisations import repo
from app.features.organisations.schemas import Custodian


async def set_custodian(conn: AsyncConnection, person_id: uuid.UUID, custodian: Custodian) -> Custodian:
    """Names or clears the custodian; the audit row holds the person's id only, never the custodian's details."""
    await repo.set_custodian(conn, custodian.name, custodian.email)
    await audit.record(conn, action="person.custodian_changed", target=f"person:{person_id}")
    return custodian
