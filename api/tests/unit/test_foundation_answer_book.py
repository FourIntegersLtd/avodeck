"""The answer book: recorded model answers keyed by task and subject, not by the whole prompt.

Pipeline recordings (T-07b, T-07c) are written by the synthetic-bundle generator from its expected
answers, before any prompt wording exists. So they are keyed by a stable task key: the task ID and a
`subject`, the SHA-256 of what the call is about (a page's words with their IDs, or one reference).
The prompt version is left out on purpose: an ideal answer does not depend on the prompt's wording.
A key may hold several answers: the nth call gets the nth, later calls repeat the last, so a
recorded retry can be planted. Like every stand-in it refuses a call it has no answer for.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.ai.answers import AnswerBook, page_subject, reference_subject
from app.core.ai.bedrock import ModelRequest, ModelResponse, RecordedBedrock, _request_record
from app.core.ai.cell import ModelNotAllowed
from app.core.ai.ocr import RecordedOcr
from app.core.ai.recordings import FixtureStore, RecordingMissing
from tests.support.ai import CELL_ENV, cell

pytestmark = pytest.mark.anyio

WORDS = [("p1w1", "Ferritin"), ("p1w2", "9"), ("p1w3", "ug/L")]
SUBJECT = page_subject(WORDS)


def request(task_id: str | None = "T-07b", subject: str | None = SUBJECT, model: str | None = None) -> ModelRequest:
    return ModelRequest(
        model_id=model or CELL_ENV["BEDROCK_SONNET_MODEL_ID"],
        system="Extract.",
        context={},
        text="Ferritin 9 ug/L",
        schema={},
        task_id=task_id,
        subject=subject,
    )


def answer(value: str) -> ModelResponse:
    return ModelResponse(output={"facts": [{"value": value}]}, input_tokens=50, output_tokens=5)


def test_subjects_are_stable_and_depend_on_every_word_and_its_id() -> None:
    assert page_subject(WORDS) == page_subject(list(WORDS)) == SUBJECT
    assert len(SUBJECT) == 64
    assert page_subject([("p1w1", "Ferritin"), ("p1w2", "19"), ("p1w3", "ug/L")]) != SUBJECT
    assert page_subject([("p1w9", "Ferritin"), ("p1w2", "9"), ("p1w3", "ug/L")]) != SUBJECT
    assert page_subject(reversed(WORDS)) != SUBJECT, "reading order matters"
    ref = reference_subject(SUBJECT, ["p1w1", "p1w2"])
    assert len(ref) == 64 and ref != reference_subject(SUBJECT, ["p1w1"]) != SUBJECT


def test_task_id_and_subject_do_not_change_how_whole_prompt_recordings_are_keyed() -> None:
    record = _request_record(request())
    assert "task_id" not in record and "subject" not in record
    assert record == _request_record(request(task_id=None, subject=None))


async def test_answers_replay_in_order_and_the_last_one_repeats(tmp_path: Path) -> None:
    book = AnswerBook(cell(), FixtureStore(tmp_path))
    book.record("T-07b", SUBJECT, [answer("first"), answer("retry")], name="planted-retry")
    replay = AnswerBook(cell(), FixtureStore(tmp_path))
    outputs = [(await replay.invoke(request())).output["facts"][0]["value"] for _ in range(3)]
    assert outputs == ["first", "retry", "retry"]
    assert len(replay.requests) == 3
    assert replay.endpoint == CELL_ENV["BEDROCK_ENDPOINT"]
    stored = json.loads(next(tmp_path.glob("answer/*.json")).read_text())
    assert stored["kind"] == "answer"
    assert stored["request"] == {"task_id": "T-07b", "subject": SUBJECT}
    assert [r["output"]["facts"][0]["value"] for r in stored["response"]["responses"]] == ["first", "retry"]


async def test_a_call_with_no_answer_or_no_key_is_refused(tmp_path: Path) -> None:
    book = AnswerBook(cell(), FixtureStore(tmp_path))
    book.record("T-07b", SUBJECT, [answer("x")])
    with pytest.raises(RecordingMissing):
        await book.invoke(request(subject=page_subject(WORDS[:2])))
    with pytest.raises(RecordingMissing):
        await book.invoke(request(task_id="T-07c"))
    with pytest.raises(RecordingMissing):
        await book.invoke(request(subject=None))
    with pytest.raises(ModelNotAllowed):
        await book.invoke(request(model="global.anthropic.claude-test"))


async def test_the_whole_prompt_stand_in_still_ignores_the_task_key(tmp_path: Path) -> None:
    client = RecordedBedrock(cell(), FixtureStore(tmp_path))
    client.record(request(task_id=None, subject=None), answer("same"))
    assert (await client.invoke(request())).output == {"facts": [{"value": "same"}]}


async def test_ocr_words_carry_a_struck_out_flag(tmp_path: Path) -> None:
    store = FixtureStore(tmp_path)
    page = b"%PDF-1.7 synthetic struck page"
    words = [
        {"id": "p1w1", "text": "aspirin", "polygon": [0, 0, 1, 0, 1, 1, 0, 1], "confidence": 0.8, "handwritten": True,
         "struck": True},
        {"id": "p1w2", "text": "paracetamol", "polygon": [0, 2, 1, 2, 1, 3, 0, 3], "confidence": 0.8},
    ]
    store.record("ocr", RecordedOcr.request(page, "layout"), {"pages": [
        {"number": 1, "width": 8.27, "height": 11.69, "words": words}]})
    [result] = (await RecordedOcr(cell(), store).analyse(page)).pages
    assert [(w.id, w.struck, w.handwritten) for w in result.words] == [("p1w1", True, True), ("p1w2", False, False)]
