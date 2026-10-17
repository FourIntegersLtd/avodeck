"""Recorded stand-ins for Bedrock, Azure OCR and Azure Speech (no cloud accounts in stage 1).

Each stand-in sits behind the same interface the real client will use, replays a recorded
response keyed by a hash of the request, and refuses (never invents) an answer it has no recording
for. Recordings hold hashes of documents and audio, never the documents or audio themselves.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.core.ai.bedrock import ModelRequest, ModelResponse, RecordedBedrock
from app.core.ai.ocr import OcrResult, RecordedOcr
from app.core.ai.recordings import FixtureStore, RecordingMissing
from app.core.packs import default_library
from app.core.speech import RecordedSpeech, TranscriptSegment
from tests.support.ai import CELL_ENV, FIXTURES, cell
from tests.support.packs import LAUNCH

pytestmark = pytest.mark.anyio

PAGE = b"%PDF-1.7 synthetic test page 1 (no patient data)"
AUDIO = [b"synthetic-audio-chunk-1", b"synthetic-audio-chunk-2"]


async def chunks(parts: list[bytes]) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


async def test_ocr_replays_the_committed_recording() -> None:
    ocr = RecordedOcr(cell(), FixtureStore(FIXTURES))
    result = await ocr.analyse(PAGE, model="layout")
    assert isinstance(result, OcrResult)
    [page] = result.pages
    assert page.number == 1
    assert [w.id for w in page.words] == ["p1w1", "p1w2", "p1w3", "p1w4"]
    assert page.words[2].text == "Amoxicillin"
    assert all(0 <= w.confidence <= 1 and len(w.polygon) == 8 for w in page.words)
    assert "Amoxicillin" not in repr(result), "OCR text never appears in a repr (and so never in a log)"


async def test_ocr_refuses_a_document_or_model_it_has_no_recording_for() -> None:
    ocr = RecordedOcr(cell(), FixtureStore(FIXTURES))
    with pytest.raises(RecordingMissing):
        await ocr.analyse(b"%PDF-1.7 another page", model="layout")
    with pytest.raises(RecordingMissing):
        await ocr.analyse(PAGE, model="read")


async def test_bedrock_records_and_replays_by_request(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    client = RecordedBedrock(cell(), store)
    request = ModelRequest(
        model_id=CELL_ENV["BEDROCK_SONNET_MODEL_ID"],
        system="Label.",
        context={"k": "v"},
        text="[NAME_1] seen.",
        schema={},
    )
    answer = ModelResponse(output={"labels": ["a"]}, input_tokens=12, output_tokens=3)
    with pytest.raises(RecordingMissing):
        await client.invoke(request)
    client.record(request, answer)
    assert await client.invoke(request) == answer
    assert await RecordedBedrock(cell(), FixtureStore(tmp_path)).invoke(request) == answer, "recordings persist"
    other = ModelRequest(model_id=request.model_id, system="Label.", context={"k": "v"}, text="other", schema={})
    with pytest.raises(RecordingMissing):
        await client.invoke(other)
    assert len(client.requests) == 3, "the client saw three requests; the second client its own one"
    assert client.endpoint == CELL_ENV["BEDROCK_ENDPOINT"]


async def test_speech_records_and_replays_without_keeping_the_audio(tmp_path: Path) -> None:
    speech_settings = default_library().combine(*LAUNCH.codes).speech()
    locale, phrases = speech_settings["locale"], list(speech_settings["phrases"])
    store = FixtureStore(tmp_path)
    speech = RecordedSpeech(cell(), store)
    segments = [TranscriptSegment("first words", 0, 900), TranscriptSegment("second words", 900, 2000)]
    speech.record(b"".join(AUDIO), locale=locale, phrases=phrases, segments=segments)

    replayed = [s async for s in speech.transcribe(chunks(AUDIO), locale=locale, phrases=phrases)]
    assert replayed == segments
    with pytest.raises(RecordingMissing):
        _ = [s async for s in speech.transcribe(chunks(AUDIO[:1]), locale=locale, phrases=phrases)]

    stored = "".join(p.read_text() for p in tmp_path.rglob("*.json"))
    assert "synthetic-audio" not in stored
    assert json.loads(next(tmp_path.rglob("*.json")).read_text())["kind"] == "speech"
