"""What slice C's jobs need from the cell: the AI kit, OCR, Storage, case encryption and the packs.

Jobs run in the worker, which has no `app.state`, so they get these from here. `current()` builds them
from the cell config on first use; tests and the eval harness call `configure()` with stand-ins.
Without a model or OCR client for the cell (no cloud accounts yet, unless `AI_RECORDINGS_DIR` names
recorded responses), the job pauses with `model_unavailable` instead of guessing (F24).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrClient, RecordedOcr
from app.core.ai.recordings import FixtureStore
from app.core.config import ConfigError, ai_settings
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.core.storage import SupabaseStorage, storage_settings


@dataclass
class SetupServices:
    kit: AiKit | None
    ocr: OcrClient | None
    storage: SupabaseStorage | None
    crypto: CaseCrypto | None
    packs: PackLibrary

    def ai(self) -> tuple[AiKit, OcrClient]:
        if self.kit is None or self.ocr is None:
            raise ProcessingPaused("model_unavailable")
        return self.kit, self.ocr

    def files(self) -> tuple[SupabaseStorage, CaseCrypto]:
        if self.storage is None or self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.storage, self.crypto


_current: SetupServices | None = None


def configure(services: SetupServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def from_env() -> SetupServices:
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
    return SetupServices(kit=kit, ocr=ocr, storage=storage, crypto=crypto, packs=default_library())


def current() -> SetupServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
