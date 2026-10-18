"""Slice D's background jobs: `suggest_links` (T-11) and `rerun_checks` (checks and T-13a).

Both are queued by the routes and run in the worker under the job's case (ADR 0013).
"""

from __future__ import annotations

from app.core.jobs import JobContext, registry


@registry.kind("suggest_links", interactive=False, changes_case=False)
async def suggest_links(ctx: JobContext) -> None:
    """Suggest links between checked facts and the confirmed questions; filled in by the T-11 slice step."""


@registry.kind("rerun_checks", interactive=False, changes_case=False)
async def rerun_checks(ctx: JobContext) -> None:
    """Run the case's checks again; filled in by the checks slice step."""
