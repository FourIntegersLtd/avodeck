"""The services the pipeline uses, chosen once per process (or per test with `use`).

Production builds them from the cell config (`from_env`); tests pass stand-ins: the recorded
scanner, OCR and model answers, local storage and a local key. `identity` gives the names and dates
of birth `pii.strip` removes before any model call (rule 7).
"""

from __future__ import annotations

import contextlib
import uuid
from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import Any

from psycopg import AsyncConnection

from app.core import jobs
from app.core.ai.embed import Embedder, RecordedEmbedder
from app.core.ai.kit import AiKit
from app.core.ai.ocr import OcrClient
from app.core.contracts.case_identity import Identity, identity_for
from app.core.crypto import CaseCrypto
from app.core.email import Mailer
from app.core.packs import PackLibrary
from app.core.scan import VirusScanner

IdentityFor = Callable[[AsyncConnection, uuid.UUID], Awaitable[Identity]]


async def no_identity(conn: AsyncConnection, case_id: uuid.UUID) -> Identity:
    """Until the case keeps the subject's names, only the pack's recognisers strip identifiers."""
    return Identity()


def identity_with(crypto: CaseCrypto) -> IdentityFor:
    """The case's parties, subject and date of birth, opened with the cell's key, as the pipeline strips them."""

    async def identity(conn: AsyncConnection, case_id: uuid.UUID) -> Identity:
        return await identity_for(conn, crypto, case_id)

    return identity


__all__ = ["Identity", "identity_with"]


@dataclass
class PipelineServices:
    storage: Any
    crypto: CaseCrypto
    scanner: VirusScanner
    ocr: OcrClient | None
    kit: AiKit | None
    packs: PackLibrary
    registry: jobs.JobRegistry
    identity: IdentityFor = no_identity
    mailer: Mailer = field(default_factory=lambda: _default_mailer())
    embedder: Embedder = field(default_factory=RecordedEmbedder)


def _default_mailer() -> Mailer:
    from app.core.notifications import default_mailer

    return default_mailer()


_stack: list[PipelineServices] = []
_from_env: PipelineServices | None = None


@contextlib.contextmanager
def use(services: PipelineServices) -> Iterator[PipelineServices]:
    _stack.append(services)
    try:
        yield services
    finally:
        _stack.remove(services)


def from_env() -> PipelineServices:
    """The cell's own services; OCR and the model client are live, recorded or none (`live_or_recorded`)."""
    from app.core.ai.clients import live_or_recorded
    from app.core.ai.embed import embedder_from_env
    from app.core.crypto import key_provider_from_env
    from app.core.packs import default_library
    from app.core.scan import scanner_from_env
    from app.core.storage import SupabaseStorage, storage_settings

    ai = live_or_recorded()
    crypto = CaseCrypto(key_provider_from_env())
    return PipelineServices(
        storage=SupabaseStorage(storage_settings()),
        crypto=crypto,
        scanner=scanner_from_env(),
        ocr=ai.ocr,
        kit=ai.kit,
        packs=default_library(),
        registry=jobs.registry,
        embedder=embedder_from_env(),
        identity=identity_with(crypto),
    )


def current() -> PipelineServices:
    global _from_env
    if _stack:
        return _stack[-1]
    if _from_env is None:
        _from_env = from_env()
    return _from_env


def registry() -> jobs.JobRegistry:
    """Where pipeline jobs are queued: the services' registry, or the app's own."""
    return _stack[-1].registry if _stack else jobs.registry
