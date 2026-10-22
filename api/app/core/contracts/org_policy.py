"""An organisation's policy (architecture 10.1): the report template it requires and its retention limit.

Slice O writes `organisations.policy` (`PUT /organisations/{org_id}/policy`); slices F and H read it
here. Each value is None when the organisation has not set it, and the person's own defaults apply.
`app.organisation_policy()` (migration 0030) answers for a current member, for anyone who can see
one of the organisation's cases (a named expert who has left keeps the organisation's rules on
their named cases) and for such a case's own job; anyone else gets 404. A policy of the wrong shape
is refused (ValueError), never read as "no limit".

Keys in `organisations.policy`: `required_template_id` (a report template's id) and
`max_retention_years` (a whole number of years, 1 to 100).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from psycopg import AsyncConnection

from app.core.errors import ApiError

KEYS = frozenset({"required_template_id", "max_retention_years"})


@dataclass(frozen=True)
class OrgPolicy:
    required_template_id: uuid.UUID | None = None
    max_retention_years: int | None = None


def parse(policy: Any) -> OrgPolicy:
    if not isinstance(policy, dict):
        raise ValueError("an organisation policy is an object")
    template = policy.get("required_template_id")
    years = policy.get("max_retention_years")
    if template is not None:
        if not isinstance(template, str):
            raise ValueError("required_template_id is an id")
        template = uuid.UUID(template)  # ValueError when it is not one
    if years is not None and (isinstance(years, bool) or not isinstance(years, int) or not 1 <= years <= 100):
        raise ValueError("max_retention_years is a whole number of years")
    return OrgPolicy(template, years)


async def org_policy(conn: AsyncConnection, org_id: uuid.UUID) -> OrgPolicy:
    cur = await conn.execute("select app.organisation_policy(%s)", (org_id,))
    row = await cur.fetchone()
    if row is None or row[0] is None:
        raise ApiError("not_found", "Not found.", 404)
    return parse(row[0])
