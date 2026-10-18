"""What slice F's jobs use from the cell: case encryption, file storage, slice B's read log, the AI kit and
the packs.

Jobs run in the worker, which has no `app.state`, so they get these from here. `current()` builds them from
the cell config on first use; tests call `configure()` with stand-ins. Without encryption or storage a job
pauses (`storage_unavailable`); without a read log nothing is citable (fail closed, rule 2). Without the AI
kit the final check still runs its code checks and records that T-17 did not run (warnings never block).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.recordings import FixtureStore
from app.core.config import ConfigError, ai_settings
from app.core.contracts.reading import PageReadLog, PageReads
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.core.storage import SupabaseStorage, storage_settings


@dataclass
class ReportServices:
    crypto: CaseCrypto | None = None
    storage: SupabaseStorage | None = None
    reads: PageReadLog | None = None
    kit: AiKit | None = None
    packs: PackLibrary = field(default_factory=default_library)

    def case_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.crypto

    def files(self) -> SupabaseStorage:
        if self.storage is None:
            raise ProcessingPaused("storage_unavailable")
        return self.storage


_current: ReportServices | None = None


def configure(services: ReportServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def from_env() -> ReportServices:
    try:
        crypto: CaseCrypto | None = CaseCrypto(key_provider_from_env())
    except ConfigError:
        crypto = None
    try:
        storage: SupabaseStorage | None = SupabaseStorage(storage_settings())
    except ConfigError:
        storage = None
    kit: AiKit | None = None
    recordings = os.environ.get("AI_RECORDINGS_DIR", "").strip()
    try:
        settings = ai_settings()
    except ConfigError:
        settings = None
    if settings is not None and recordings:
        kit = AiKit(settings, RecordedBedrock(settings, FixtureStore(Path(recordings))))
    return ReportServices(crypto=crypto, storage=storage, reads=PageReads(), kit=kit)


def current() -> ReportServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
