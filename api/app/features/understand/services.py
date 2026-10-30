"""What slice D's jobs need from the cell: the AI kit, case encryption, the embedder and the packs.

Jobs run in the worker, which has no `app.state`, so they get these from here. `current()` builds them from
the cell config on first use; tests and the eval harness call `configure()` with stand-ins. Without a model
for the cell (no cloud accounts yet, unless `AI_RECORDINGS_DIR` names recorded responses) a job that needs
one pauses with `model_unavailable` instead of guessing (F24).

The names and the date of birth stripped before T-11 and T-13a (rule 7) come from `identity_for`, opened
with this crypto.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from psycopg import AsyncConnection

from app.core.ai.clients import live_or_recorded
from app.core.ai.embed import Embedder, embedder_from_env
from app.core.ai.kit import AiKit
from app.core.config import ConfigError
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.features.understand.links import IdentityReranker, Reranker

BirthDates = Callable[[AsyncConnection, uuid.UUID], Awaitable[Sequence[date]]]


@dataclass
class UnderstandServices:
    kit: AiKit | None
    crypto: CaseCrypto | None
    embedder: Embedder | None
    packs: PackLibrary
    reranker: Reranker = field(default_factory=IdentityReranker)
    dates_of_birth: BirthDates | None = None  # replaces the recorded date of birth (tests); None: `identity_for`

    def ai(self) -> AiKit:
        if self.kit is None:
            raise ProcessingPaused("model_unavailable")
        return self.kit

    def words(self) -> CaseCrypto:
        if self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.crypto

    def embed(self) -> Embedder:
        if self.embedder is None:
            raise ProcessingPaused("model_unavailable")
        return self.embedder


_current: UnderstandServices | None = None


def configure(services: UnderstandServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def from_env() -> UnderstandServices:
    kit: AiKit | None = live_or_recorded().kit
    try:
        crypto: CaseCrypto | None = CaseCrypto(key_provider_from_env())
    except ConfigError:
        crypto = None
    try:
        embedder: Embedder | None = embedder_from_env()
    except ConfigError:
        embedder = None
    return UnderstandServices(kit=kit, crypto=crypto, embedder=embedder, packs=default_library())


def current() -> UnderstandServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
