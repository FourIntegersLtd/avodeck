"""Which pages are set aside (audit 16, PV-09): one definition for every slice that reads pages.

A page is set aside when it is quarantined, when a later page replaced it (`superseded_by_page_id`), or when it is
a copy the expert confirmed (`duplicate_of_page_id` set and `page_analysis.duplicate_status = 'confirmed'`). A copy
the pipeline only suggested is NOT set aside: it is still read and still counted until the expert confirms it.
Set-aside pages are kept, never deleted; they are only left out of what is read, counted and cited.

The SQL fragments take the alias of `app.pages` in the query and need no join of their own.
"""

from __future__ import annotations

from typing import Any

CONFIRMED = "confirmed"


def set_aside_sql(pages: str = "p") -> str:
    """A SQL condition that is true for a set-aside page of `app.pages` aliased `pages`."""
    return (
        f"({pages}.quarantined or {pages}.superseded_by_page_id is not null"
        f" or ({pages}.duplicate_of_page_id is not null"
        f" and exists (select 1 from app.page_analysis sa where sa.page_id = {pages}.id"
        f" and sa.duplicate_status = '{CONFIRMED}')))"
    )


def not_set_aside_sql(pages: str = "p") -> str:
    """The opposite condition: the page is read, counted and may be cited."""
    return f"(not {set_aside_sql(pages)})"


def is_set_aside(quarantined: bool, superseded_by: Any, duplicate_of: Any, duplicate_status: str | None) -> bool:
    """The same rule for values already read from the database."""
    return bool(
        quarantined or superseded_by is not None or (duplicate_of is not None and duplicate_status == CONFIRMED)
    )
