"""What the AI did on a case, for the report's methodology appendix (slice F).

Groups the case's `ai_calls` metadata by task, model ID, region and prompt version. There is no
prompt or response text to return: `ai_calls` cannot hold any (rule 6). Runs on the caller's
connection, so row-level security applies (anyone who may see the case's content, or its own job).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from psycopg import AsyncConnection


@dataclass(frozen=True)
class AiCallSummary:
    task_id: str
    model_id: str
    region: str
    prompt_version: str
    calls: int
    first_at: datetime
    last_at: datetime


async def ai_call_summary(conn: AsyncConnection, case_id: uuid.UUID) -> list[AiCallSummary]:
    """One row per task, model, region and prompt version, by task then first call."""
    cur = await conn.execute(
        "select task_id, model_id, region, prompt_version, count(*)::int, min(at), max(at)"
        " from app.ai_calls where case_id = %s"
        " group by task_id, model_id, region, prompt_version"
        " order by task_id, min(at), prompt_version",
        (case_id,),
    )
    return [AiCallSummary(*row) for row in await cur.fetchall()]
