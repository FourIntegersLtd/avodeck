"""What slice E's routes and jobs use from the cell and from other slices.

Jobs run in the worker, which has no `app.state`, so they get the AI kit, case encryption, the packs and
slice B's read log from here. `current()` builds them from the cell config on first use; tests call
`configure()` with stand-ins. Without a model for the cell (no cloud accounts yet, unless
`AI_RECORDINGS_DIR` names recorded responses) a job pauses with `model_unavailable` (F24). The cell's
services use slice B's read log and flags, slice D's confirmed links and the pack's guidance; the
dataclass defaults are the stand-ins tests use. Without a read log nothing is citable (fail closed).
The A, B, C and D ports are in `ports.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.core.ai.bedrock import RecordedBedrock
from app.core.ai.clients import live_or_recorded
from app.core.ai.kit import AiKit
from app.core.config import ConfigError
from app.core.contracts.reading import PageReadLog, PageReads
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.packs import PackLibrary, default_library
from app.features.write.ports import (
    AllLinked,
    BirthDates,
    CaseNames,
    ConfirmedLinks,
    FactFlags,
    GuidanceInForce,
    NoBirthDates,
    NoFlags,
    NoGuidance,
    OcrWords,
    PackGuidance,
    PartyNames,
    PointFlags,
    QuestionLinks,
    StatementWords,
)


@dataclass
class WriteServices:
    flags: FactFlags = field(default_factory=NoFlags)
    links: QuestionLinks = field(default_factory=AllLinked)
    guidance: GuidanceInForce = field(default_factory=NoGuidance)
    kit: AiKit | None = None
    crypto: CaseCrypto | None = None
    reads: PageReadLog | None = None
    names: CaseNames | None = None
    statements: StatementWords | None = None
    packs: PackLibrary = field(default_factory=default_library)
    births: BirthDates = field(default_factory=NoBirthDates)

    def ai(self) -> AiKit:
        if self.kit is None:
            raise ProcessingPaused("model_unavailable")
        return self.kit

    def case_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            raise ProcessingPaused("storage_unavailable")
        return self.crypto

    def party_names(self) -> CaseNames:
        if self.names is not None:
            return self.names
        return PartyNames(self.case_crypto())

    def statement_words(self) -> StatementWords:
        if self.statements is not None:
            return self.statements
        return OcrWords(self.case_crypto())


_current: WriteServices | None = None


def configure(services: WriteServices | None) -> None:
    """Use these services from now on (None: build from the cell config again on next use)."""
    global _current
    _current = services


def from_env() -> WriteServices:
    kit: AiKit | None = live_or_recorded(recorded_bedrock=RecordedBedrock).kit
    try:
        crypto: CaseCrypto | None = CaseCrypto(key_provider_from_env())
    except ConfigError:
        crypto = None
    return WriteServices(
        flags=PointFlags(),
        links=ConfirmedLinks(),
        guidance=PackGuidance(),
        kit=kit,
        crypto=crypto,
        reads=PageReads(),
    )


def current() -> WriteServices:
    global _current
    if _current is None:
        _current = from_env()
    return _current
