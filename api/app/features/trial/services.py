"""What slice I's routes and jobs use from the cell: case encryption, file storage, slice B's read log, the AI
kit, OCR, the packs and the ports to slice G (`ports.py`).

Jobs run in the worker, which has no `app.state`, so they get these from here. `current()` builds them from
the cell config on first use; tests call `configure()` with stand-ins. Without encryption or storage a job
pauses (`storage_unavailable`); without the AI kit a task that needs the model pauses (`model_unavailable`).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai.clients import live_or_recorded
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrClient
from app.core.config import ConfigError
from app.core.contracts.reading import PageReadLog, PageReads
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.core.storage import SupabaseStorage, storage_settings
from app.features.trial.ports import (
    Hearings,
    NoHearings,
    NoReportChanges,
    NoWrittenAnswers,
    ReportChanges,
    WrittenAnswers,
)


@dataclass
class TrialServices:
    crypto: CaseCrypto | None = None
    storage: SupabaseStorage | None = None
    reads: PageReadLog | None = None
    kit: AiKit | None = None
    ocr: OcrClient | None = None
    packs: PackLibrary = field(default_factory=default_library)
    hearings: Hearings = field(default_factory=NoHearings)
    answers: WrittenAnswers = field(default_factory=NoWrittenAnswers)
    changes: ReportChanges = field(default_factory=NoReportChanges)

    def case_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.crypto

    def files(self) -> SupabaseStorage:
        if self.storage is None:
            raise ProcessingPaused("storage_unavailable")
        return self.storage

    def model(self) -> AiKit:
        if self.kit is None:
            raise ProcessingPaused("model_unavailable")
        return self.kit


_current: TrialServices | None = None


def configure(services: TrialServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def configured() -> TrialServices | None:
    return _current


def from_env() -> TrialServices:
    try:
        crypto: CaseCrypto | None = CaseCrypto(key_provider_from_env())
    except ConfigError:
        crypto = None
    try:
        storage: SupabaseStorage | None = SupabaseStorage(storage_settings())
    except ConfigError:
        storage = None
    ai = live_or_recorded()
    return TrialServices(crypto=crypto, storage=storage, reads=PageReads(), kit=ai.kit, ocr=ai.ocr)


def current() -> TrialServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
