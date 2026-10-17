"""Slice H jobs: `export_case` (the record of work as a ZIP) and `delete_case` (deletion + certificate)."""

from __future__ import annotations

from app.core.jobs import JobContext, JobRegistry, registry


async def export_case(ctx: JobContext) -> None:
    raise NotImplementedError("export_case")


async def delete_case(ctx: JobContext) -> None:
    raise NotImplementedError("delete_case")


def register(reg: JobRegistry) -> None:
    reg.kind("export_case", changes_case=False)(export_case)
    reg.kind("delete_case", changes_case=True)(delete_case)


register(registry)
