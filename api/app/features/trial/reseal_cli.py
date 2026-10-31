"""Management command: seal older plain trial files across cases (counts only are printed).

    poetry run python -m app.features.trial.reseal_cli [CASE_ID ...]

Runs as the worker role (`DATABASE_URL_DIRECT`) with the cell's storage and key config, one case at a time
(`reseal.reseal_plain_files`). With no case ids it takes every case that has trial rows. Safe to run again.
Run it against a cell only when you mean to: it overwrites plain objects with sealed ones.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.core.config import ConfigError, database_settings
from app.core.db import Database
from app.features.trial.reseal import reseal_plain_files
from app.features.trial.services import current

TRIAL_CASES = (
    "select case_id from app.court_bundles union select case_id from app.agenda_versions"
    " union select case_id from app.trial_packs union select case_id from app.expert_meetings"
)


async def run_for(case_ids: list[uuid.UUID] | None = None) -> dict[str, int]:
    """Seal the trial files of the given cases (every case with trial rows if none); returns counts by case."""
    settings = database_settings(None)
    if not settings.url_direct:
        raise ConfigError("DATABASE_URL_DIRECT is not set")
    database = Database(settings.url_direct, max_size=2)
    await database.open()
    done: dict[str, int] = {}
    try:
        if case_ids is None:
            async with database.anonymous() as conn:
                cur = await conn.execute(TRIAL_CASES)
                case_ids = [r[0] for r in await cur.fetchall()]
        for case_id in case_ids:
            async with database.job(case_id) as conn:
                done[str(case_id)] = await reseal_plain_files(conn, current(), case_id)
    finally:
        await database.close()
    return done


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Seal trial files stored before sealing. Safe to run again.")
    parser.add_argument(
        "case_ids", nargs="*", type=uuid.UUID, help="cases to seal (default: every case with trial rows)"
    )
    args = parser.parse_args(argv)
    done = asyncio.run(run_for(args.case_ids or None))
    print(f"cases: {len(done)}, files sealed: {sum(done.values())}")  # noqa: T201 - counts only
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
