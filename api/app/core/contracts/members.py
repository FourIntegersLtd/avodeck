"""Display names of a case's people (request from slice C).

Row-level security on `people` hides other people's rows, so a slice cannot read a colleague's name
itself. `case_member_names()` gives the named expert's and every current case member's display name
to anyone who may see the case (`case.view_metadata`) or the case's own job, and nothing to anyone
else (`app.case_member_names()`). A person without a name maps to None. Names are personal data:
never log them (rule 6).
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection


async def case_member_names(conn: AsyncConnection, case_id: uuid.UUID) -> dict[uuid.UUID, str | None]:
    cur = await conn.execute("select person_id, display_name from app.case_member_names(%s)", (case_id,))
    return {person_id: name for person_id, name in await cur.fetchall()}
