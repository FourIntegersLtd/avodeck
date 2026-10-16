"""Today (screen 03): case cards and the "needs you" items each slice registers.

A slice adds items with a provider: `@needs_you.provider("<slice>_<what>")` on an async function
taking the request's connection (row-level security as the person) and the cards being shown,
returning `NeedsYouItem`s. Items carry i18n keys, short codes and numbers only, never sentences or
patient text; the web app builds the words from the keys and the case's pack vocabulary.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from datetime import date, datetime
from typing import Annotated

from psycopg import AsyncConnection
from pydantic import BaseModel, ConfigDict, Field

CODE = r"^[a-z][a-z0-9_]{0,39}$"
I18N_KEY = r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$"
SCREEN = r"^S\d{2}[a-z]?$"

Code = Annotated[str, Field(pattern=CODE)]


class NeedsYouItem(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Code
    case_id: uuid.UUID
    rank: int = Field(ge=0, description="Lower comes first.")
    due_at: datetime | None = None
    label_key: str = Field(pattern=I18N_KEY)
    action_key: str = Field(pattern=I18N_KEY)
    screen: str = Field(pattern=SCREEN, description="Canvas board the action opens, e.g. S09.")
    count: int | None = Field(default=None, ge=0)
    minutes: int | None = Field(default=None, ge=0, description="Reading-time estimate.")
    params: dict[Code, int | Code] = Field(default_factory=dict)


class CaseCard(BaseModel):
    model_config = ConfigDict(frozen=True)

    case_id: uuid.UUID
    label: str
    organisation_id: uuid.UUID
    organisation_name: str | None
    organisation_kind: str | None
    status: str
    stage: str | None
    due_date: date | None
    party_role: str | None
    jurisdiction: str
    case_type: str
    specialty: str
    your_role: str | None
    on_hold: bool


Provider = Callable[[AsyncConnection, list[CaseCard]], Awaitable[list[NeedsYouItem]]]


class NeedsYouRegistry:
    def __init__(self) -> None:
        self.providers: dict[str, Provider] = {}

    def provider(self, name: str) -> Callable[[Provider], Provider]:
        def register(fn: Provider) -> Provider:
            if name in self.providers:
                raise ValueError(f"needs-you provider {name!r} is registered twice")
            self.providers[name] = fn
            return fn

        return register


needs_you = NeedsYouRegistry()
