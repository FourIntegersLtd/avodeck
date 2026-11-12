"""The run-time check of the hash chains (audit 16, PV-13c): the audit log and each case's `page_reads` and
`fact_verifications`.

Until now only tests recomputed the chains. This is a worker housekeeping sweep (`SWEEP`) that checks the next slice of
each chain in every round, within a budget, and starts again at the end, so a full pass is spread over many rounds. A
break is an error-level log line (which reaches the ops alert) carrying counts and the audit rows' `seq` numbers only:
never a case id, a name, a target or any record text (rule 6). The work is in the database (`app.verify_audit_chain`,
`app.verify_case_chains`, migration 9009); the chains are unkeyed and carry no external anchor, which is a policy item
(`docs/architecture.md` section 9).
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field

from app.core.db import Database
from app.core.jobs import registry

logger = logging.getLogger(__name__)

SWEEP = "chain_check"
AUDIT_ROWS_PER_ROUND = 20_000
CASES_PER_ROUND = 25
MAX_LISTED_BREAKS = 20  # the alert names at most this many seq numbers


@dataclass(frozen=True)
class ChainReport:
    audit_rows_checked: int = 0
    audit_breaks: tuple[int, ...] = ()
    cases_checked: int = 0
    case_breaks: frozenset[tuple[str, uuid.UUID]] = field(default_factory=frozenset)


async def verify_chains(
    database: Database, *, audit_rows: int = AUDIT_ROWS_PER_ROUND, cases: int = CASES_PER_ROUND
) -> ChainReport:
    """Check the next `audit_rows` audit rows and the next `cases` cases' chains, and move the cursors on."""
    async with database.anonymous() as conn:
        cur = await conn.execute("select checked, broken from app.verify_audit_chain(%s)", (audit_rows,))
        row = await cur.fetchone()
        checked, broken = (int(row[0]), tuple(int(s) for s in row[1])) if row else (0, ())
    async with database.anonymous() as conn:
        cur = await conn.execute("select r_chain, r_case, r_ok from app.verify_case_chains(%s)", (cases,))
        case_rows = await cur.fetchall()
    return ChainReport(
        audit_rows_checked=checked,
        audit_breaks=broken,
        cases_checked=len({r[1] for r in case_rows}),
        case_breaks=frozenset((str(r[0]), r[1]) for r in case_rows if not r[2]),
    )


@registry.database_sweep(SWEEP)
async def _sweep(database: Database) -> None:
    report = await verify_chains(database)
    if report.audit_breaks:
        logger.error(
            "audit log chain broken: %d row(s), seq %s",
            len(report.audit_breaks),
            ",".join(str(s) for s in report.audit_breaks[:MAX_LISTED_BREAKS]),
        )
    if report.case_breaks:
        by_chain = {name: sum(1 for n, _ in report.case_breaks if n == name) for name, _ in report.case_breaks}
        logger.error(
            "case record chain broken: %s",
            ", ".join(f"{name}={count} case(s)" for name, count in sorted(by_chain.items())),
        )


__all__ = ["SWEEP", "ChainReport", "verify_chains"]
