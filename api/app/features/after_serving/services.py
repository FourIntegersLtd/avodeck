"""What slice G's jobs need from the cell: case encryption, file storage, slice B's read log, the AI kit,
OCR, the packs and the mailer.

Jobs run in the worker, which has no `app.state`, so they get these from here. `current()` builds them from
the cell config on first use; tests call `configure()` with stand-ins. Without encryption or storage a job
pauses (`storage_unavailable`); without a model or OCR it pauses (`model_unavailable`, F24, no fallback).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrClient, RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.config import ConfigError, ai_settings
from app.core.contracts.reading import PageReadLog, PageReads
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.email import OutboxMailer
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.core.storage import SupabaseStorage, storage_settings


@dataclass
class AfterServingServices:
    crypto: CaseCrypto | None = None
    storage: SupabaseStorage | None = None
    reads: PageReadLog | None = None
    kit: AiKit | None = None
    ocr: OcrClient | None = None
    packs: PackLibrary = field(default_factory=default_library)
    mailer: OutboxMailer = field(default_factory=OutboxMailer)  # the cell mailer's stand-in until Resend

    def files(self) -> tuple[SupabaseStorage, CaseCrypto]:
        if self.storage is None or self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.storage, self.crypto

    def case_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.crypto

    def model(self) -> AiKit:
        if self.kit is None:
            raise ProcessingPaused("model_unavailable")
        return self.kit

    def reader(self) -> OcrClient:
        if self.ocr is None:
            raise ProcessingPaused("model_unavailable")
        return self.ocr


_current: AfterServingServices | None = None


def configure(services: AfterServingServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def from_env() -> AfterServingServices:
    kit: AiKit | None = None
    ocr: OcrClient | None = None
    recordings = os.environ.get("AI_RECORDINGS_DIR", "").strip()
    try:
        settings = ai_settings()
    except ConfigError:
        settings = None
    if settings is not None and recordings:
        store = FixtureStore(Path(recordings))
        kit = AiKit(settings, RecordedBedrock(settings, store))
        ocr = RecordedOcr(settings, store)
    try:
        storage: SupabaseStorage | None = SupabaseStorage(storage_settings())
    except ConfigError:
        storage = None
    try:
        crypto: CaseCrypto | None = CaseCrypto(key_provider_from_env())
    except ConfigError:
        crypto = None
    return AfterServingServices(crypto=crypto, storage=storage, reads=PageReads(), kit=kit, ocr=ocr)


def current() -> AfterServingServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
