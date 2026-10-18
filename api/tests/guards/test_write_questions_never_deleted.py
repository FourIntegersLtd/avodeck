"""Questions are only ever soft-removed by application code; a hard delete belongs to the case purge alone.

Opinions and their versions cascade from questions (migration 0502), and opinion versions are append-only
(0508): a delete is refused unless the case is being purged. So `delete from app.questions` issued from
FastAPI or the worker would fail mid-request at best, and at worst lose the expert's saved opinions if the
guard ever changed. The case purge (a database function, so it lives in `supabase/migrations/`) is the
only place a question row is removed. This test keeps any such SQL out of `api/app/`.
"""

from __future__ import annotations

import re
from pathlib import Path

APP = Path(__file__).resolve().parents[2] / "app"
DELETE_QUESTIONS = re.compile(r"delete\s+from\s+(app\.)?questions\b", re.IGNORECASE)


def test_no_application_code_hard_deletes_questions() -> None:
    offenders = [
        str(path.relative_to(APP))
        for path in sorted(APP.rglob("*.py"))
        if DELETE_QUESTIONS.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == [], f"questions are soft-removed only; the case purge removes them: {offenders}"
