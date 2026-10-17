"""Dictation: the speech client interface and its recorded stand-in (architecture 5.3, 6.1).

FastAPI relays the browser's audio to the cell's speech service (Azure Speech in the home region)
with the case's pack locale and phrase list (`pack.speech()`); the real relay arrives with the cloud
cell. Recordings keep a hash of the audio, never the audio.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Sequence
from dataclasses import asdict, dataclass, field
from typing import Protocol

from app.core.ai.cell import check_endpoint
from app.core.ai.recordings import FixtureStore
from app.core.config import AiSettings


@dataclass(frozen=True)
class TranscriptSegment:
    text: str = field(repr=False)
    start_ms: int
    end_ms: int


class SpeechClient(Protocol):
    endpoint: str

    def transcribe(
        self, audio: AsyncIterator[bytes], *, locale: str, phrases: Sequence[str]
    ) -> AsyncIterator[TranscriptSegment]: ...


class RecordedSpeech:
    kind = "speech"

    def __init__(self, settings: AiSettings, store: FixtureStore, endpoint: str | None = None) -> None:
        self.store = store
        self.endpoint = check_endpoint(settings, endpoint or settings.speech_endpoint)

    @staticmethod
    def request(audio: bytes, locale: str, phrases: Sequence[str]) -> dict[str, object]:
        return {"locale": locale, "phrases": list(phrases), "audio_sha256": hashlib.sha256(audio).hexdigest()}

    def record(
        self, audio: bytes, *, locale: str, phrases: Sequence[str], segments: Sequence[TranscriptSegment]
    ) -> None:
        self.store.record(self.kind, self.request(audio, locale, phrases), [asdict(s) for s in segments])

    async def transcribe(
        self, audio: AsyncIterator[bytes], *, locale: str, phrases: Sequence[str]
    ) -> AsyncIterator[TranscriptSegment]:
        digest = hashlib.sha256()
        async for chunk in audio:
            digest.update(chunk)
        request = {"locale": locale, "phrases": list(phrases), "audio_sha256": digest.hexdigest()}
        for segment in self.store.lookup(self.kind, request):
            yield TranscriptSegment(**segment)
