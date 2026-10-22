"""What slice O needs from slice H to close an account (scenario S15), over H's own job kinds.

- `request_case_deletion(conn, case_id)`: deletes an unserved case the way H's "delete now"
  retention does (`app/features/close/service.py`): needs `case.delete_now` (404/403 otherwise);
  refused (409) once the case has ever been served, while it is on hold or under an active legal
  hold, or once its deletion has started; sets the policy `delete_now`, due today, records
  `case.retention_changed` and queues H's `delete_case` job, whose id it returns. H's job does the
  deletion and sends the certificate. The caller's route is 🔒 (step-up).
- `case_export_done(conn, case_id, person_id)`: has an `export_case` job this person asked for on
  the case finished? O closes an account only after the person's exports are done.

Reads H's `legal_holds` and `deletion_certificates` through row-level security; writes only the
foundation's `cases` columns and the job queue.
"""

from __future__ import annotations

import uuid

from psycopg import AsyncConnection

from app.core import audit
from app.core.auth.access import check_case_access
from app.core.errors import conflict
from app.core.jobs import JobRegistry, registry

DELETE_NOW = "delete_now"


async def request_case_deletion(
    conn: AsyncConnection, case_id: uuid.UUID, *, jobs: JobRegistry = registry
) -> uuid.UUID:
    await check_case_access(conn, case_id, "case.delete_now")
    cur = await conn.execute(
        """
        select c.status = 'deleted' or c.on_hold or app.case_ever_served(c.id)
               or exists (select 1 from app.legal_holds h where h.case_id = c.id and h.released_at is null
                          and (h.until is null or h.until >= current_date))
               or exists (select 1 from app.deletion_certificates d where d.case_id = c.id)
        from app.cases c where c.id = %s for update
        """,
        (case_id,),
    )
    row = await cur.fetchone()
    if row is None or row[0]:
        raise conflict()
    await conn.execute(
        "update app.cases set retention_policy = %s, delete_after = current_date where id = %s", (DELETE_NOW, case_id)
    )
    await audit.record(conn, action="case.retention_changed", target=f"retention:{DELETE_NOW}", case_id=case_id)
    return await jobs.enqueue(conn, case_id, "delete_case")


async def case_export_done(conn: AsyncConnection, case_id: uuid.UUID, person_id: uuid.UUID) -> bool:
    cur = await conn.execute(
        "select exists (select 1 from app.jobs where case_id = %s and kind = 'export_case' and status = 'done'"
        " and requested_by = %s)",
        (case_id, person_id),
    )
    row = await cur.fetchone()
    return bool(row and row[0])
