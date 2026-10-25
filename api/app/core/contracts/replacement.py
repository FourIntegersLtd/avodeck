"""A replacement case for a served one (scenario S3 after serving; architecture 10.1 "Reassignment").

After serving, a case's named expert never changes: a replacement expert gets a new case linked
`replaces` to the served one (`case_links`, slice C's table). Slice O's
`POST /v1/cases/{case_id}/replacement` checks `case.reassign_named_expert` 🔒 and calls
`create_replacement_case()`; the database repeats every check (`app.create_replacement_case()`, migration
0033). The new case is a draft in the same organisation, under the same packs and label, named to the
new expert; the caller does not join it. Whether the instructing party consented to the bundle being
copied is recorded (`case_replacements`); nothing is copied here (slice A's, only with consent).
A served case gets one replacement: asking again for the same expert and consent returns the one already
made (migration 0034), with `created` false, and records nothing new; asking for anything else then is 409
`replacement_exists`.
"""

from __future__ import annotations

import uuid

import psycopg
from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, PrivateAttr

from app.core import audit
from app.core.errors import CONFLICT_SQLSTATE, ApiError

CASE_CREATED = "case.created"  # as `POST /v1/cases` records it
REPLACEMENT_CREATED = "case.replacement_created"


class ReplacementCase(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: uuid.UUID
    replaces_case_id: uuid.UUID
    named_expert_id: uuid.UUID
    instructing_party_consented: bool
    _created: bool = PrivateAttr(default=True)

    @property
    def created(self) -> bool:
        """False when this is the replacement an earlier request made (not part of the response body)."""
        return self._created


def replacement_exists() -> ApiError:
    return ApiError("replacement_exists", "This case already has a replacement, with another expert or consent.", 409)


def not_served() -> ApiError:
    return ApiError("not_served", "This case has not been served, so its expert can be changed instead.", 409)


async def create_replacement_case(
    conn: AsyncConnection,
    case_id: uuid.UUID,
    *,
    named_expert_id: uuid.UUID,
    instructing_party_consented: bool,
    label: str | None = None,
) -> ReplacementCase:
    """Create the replacement in the request's transaction, or return the one already made for this case (its
    own expert and consent; `created` false). 404 without the right to replace (as for a case you cannot see),
    409 `not_served`, 409 `replacement_exists` when one exists for another expert or consent, 422 `invalid`
    (`named_expert_id`) for an expert who cannot take it."""
    try:
        async with conn.transaction():
            cur = await conn.execute(
                "select app.create_replacement_case(%s, %s, %s, %s)",
                (case_id, named_expert_id, label, instructing_party_consented),
            )
            row = await cur.fetchone()
    except psycopg.errors.InsufficientPrivilege as exc:
        raise ApiError("not_found", "Not found.", 404) from exc
    except psycopg.errors.InvalidParameterValue as exc:
        raise ApiError("invalid", "Some details are not valid.", 422, fields=["named_expert_id"]) from exc
    except psycopg.Error as exc:
        if exc.sqlstate == CONFLICT_SQLSTATE:
            raise not_served() from exc
        raise
    assert row is not None
    made: uuid.UUID = row[0]
    # Made by this transaction exactly when its consent row carries this transaction's now().
    cur = await conn.execute(
        "select r.instructing_party_consented, c.named_expert_id, c.organisation_id, r.recorded_at = now()"
        " from app.case_replacements r join app.cases c on c.id = r.case_id where r.case_id = %s",
        (made,),
    )
    found = await cur.fetchone()
    assert found is not None
    consented, expert, organisation_id, created = found
    replacement = ReplacementCase(
        case_id=made, replaces_case_id=case_id, named_expert_id=expert, instructing_party_consented=consented
    )
    replacement._created = created
    if not created:
        if (expert, consented) != (named_expert_id, instructing_party_consented):
            raise replacement_exists()
        return replacement
    await audit.record(conn, action=CASE_CREATED, target=f"case:{made}", case_id=made, organisation_id=organisation_id)
    await audit.record(
        conn, action=REPLACEMENT_CREATED, target=f"case:{made}", case_id=case_id, organisation_id=organisation_id
    )
    return replacement
