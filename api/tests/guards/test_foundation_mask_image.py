"""Rule 7 for pictures: identifiers are painted out before a page image reaches a model (T-10, ADR 0030).

`pii.spans` finds the identifiers in the page's text; `mask.mask_page` paints every word they touch solid
black on the page picture (the same picture the expert sees, turned the same way); `AiKit.run_image` sends
only that masked picture. Anything uncertain fails closed: `MaskUnsafe`, nothing is sent. A recording holds
the picture's hash, never the picture. Every value below is made up.
"""

from __future__ import annotations

import dataclasses
import hashlib
import io
import json

import psycopg
import pytest
from PIL import Image, ImageDraw

from app.core.ai import mask, pii
from app.core.ai.bedrock import ModelRequest, _request_record
from app.core.ai.bedrock_live import _params
from app.core.ai.kit import AiKit, AiTask
from app.core.ai.mask import MaskedImage, MaskUnsafe
from app.core.ai.ocr import OcrPage, OcrWord
from app.core.db import Database
from app.core.packs import MergedPack, default_library
from app.features.pipeline import words as page_words
from app.features.pipeline.extract import plain_text
from tests.support.ai import NAME, ScriptedBedrock, cell, response
from tests.support.db import scope_for
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

DPI = 150
WIDTH_IN, HEIGHT_IN = 4.0, 2.0  # a small page keeps the test quick
TASK = AiTask(
    task_id="T-test-image",
    prompt_version="2026-10-31.1",
    model="sonnet",
    system="Copy the handwritten lines as written.",
    schema={
        "type": "object",
        "properties": {"lines": {"type": "array", "items": {"type": "string", "maxLength": 300}}},
        "required": ["lines"],
        "additionalProperties": False,
    },
)


@pytest.fixture(scope="module")
def pack() -> MergedPack:
    return default_library().combine(*LAUNCH.codes)


def word(n: int, text: str, x: float, *, y: float = 0.5, confidence: float = 0.95) -> OcrWord:
    w = 0.1 * max(len(text), 2)
    return OcrWord(f"p1w{n}", text, (x, y, x + w, y, x + w, y + 0.2, x, y + 0.2), confidence)


def page_words_on_a_line() -> list[OcrWord]:
    # "Seen today Ada Pemberton-Vale chest clear": the name runs over two words
    texts = ["Seen", "today", "Ada", "Pemberton-Vale", "chest", "clear"]
    out, x = [], 0.1
    for n, t in enumerate(texts, start=1):
        out.append(word(n, t, x))
        x += 0.1 * len(t) + 0.15
    return out


def picture(width_in: float = WIDTH_IN, height_in: float = HEIGHT_IN) -> bytes:
    image = Image.new("RGB", (round(width_in * DPI), round(height_in * DPI)), "white")
    draw = ImageDraw.Draw(image)
    for w in page_words_on_a_line():  # "ink" in every word's box, so a missed box shows
        left, top, right, bottom = box_of(w)
        draw.rectangle((left, top, right, bottom), fill=(90, 90, 90))
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    return out.getvalue()


def box_of(w: OcrWord) -> tuple[int, int, int, int]:
    xs, ys = w.polygon[0::2], w.polygon[1::2]
    return (round(min(xs) * DPI), round(min(ys) * DPI), round(max(xs) * DPI), round(max(ys) * DPI))


def spans_for(words: list[OcrWord], pack: MergedPack) -> list[pii.Span]:
    return pii.spans(plain_text([(w.id, w.text) for w in words]), pack=pack, names=[NAME])


def masked(words: list[OcrWord], pack: MergedPack, **kw: object) -> MaskedImage:
    return mask.mask_page(
        picture(),
        words,
        (WIDTH_IN, HEIGHT_IN),
        spans_for(words, pack),
        pad_px=kw.pop("pad_px", 2),  # type: ignore[arg-type]
    )


def decode(m: MaskedImage) -> Image.Image:
    return Image.open(io.BytesIO(m.data)).convert("RGB")


def assert_black(image: Image.Image, box: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = box
    for x in range(left, right):
        for y in range(top, bottom):
            assert image.getpixel((x, y)) == (0, 0, 0), "an identifier box is not fully black"


def test_a_name_over_two_words_is_painted_solid_black_and_other_words_are_left_alone(pack: MergedPack) -> None:
    words = page_words_on_a_line()
    m = masked(words, pack)
    image = decode(m)
    for w in words[2:4]:  # Ada, Pemberton-Vale
        assert_black(image, box_of(w))
    left, top, right, bottom = box_of(words[0])  # "Seen" keeps its ink
    assert image.getpixel(((left + right) // 2, (top + bottom) // 2)) != (0, 0, 0)
    assert m.masked_words == 2
    assert m.sha256 and m.format == "png"


def test_the_guard_every_identifier_box_in_the_sent_picture_is_black(pack: MergedPack) -> None:
    words = page_words_on_a_line()
    m = masked(words, pack)
    assert m.boxes, "nothing was masked"
    image = decode(m)
    for box in m.boxes:
        assert_black(image, box)


def test_a_span_over_a_word_with_no_place_on_the_picture_is_refused(pack: MergedPack) -> None:
    words = page_words_on_a_line()
    words[2] = OcrWord("e3", "Ada", (0.0,) * 8, 1.0)  # typed out by the expert: no polygon
    with pytest.raises(MaskUnsafe):
        masked(words, pack)


def test_low_ocr_confidence_is_refused_because_an_identifier_may_have_been_misread(pack: MergedPack) -> None:
    words = [dataclasses.replace(w, confidence=0.3) for w in page_words_on_a_line()]
    with pytest.raises(MaskUnsafe):
        masked(words, pack)


def test_a_span_that_touches_no_word_is_refused(pack: MergedPack) -> None:
    words = page_words_on_a_line()
    far = [pii.Span(500, 520, "NAME")]
    with pytest.raises(MaskUnsafe):
        mask.mask_page(picture(), words, (WIDTH_IN, HEIGHT_IN), far, pad_px=2)


def test_a_page_with_no_words_is_refused_whatever_the_spans(pack: MergedPack) -> None:
    with pytest.raises(MaskUnsafe):
        mask.mask_page(picture(), [], (WIDTH_IN, HEIGHT_IN), [], pad_px=2)


def test_a_picture_whose_shape_does_not_match_the_words_frame_is_refused(pack: MergedPack) -> None:
    words = page_words_on_a_line()
    with pytest.raises(MaskUnsafe):  # the picture is landscape but the words say portrait: the frame is uncertain
        mask.mask_page(picture(), words, (HEIGHT_IN, WIDTH_IN), spans_for(words, pack), pad_px=2)


def test_a_turned_page_is_masked_in_the_turned_frame(pack: MergedPack) -> None:
    page = OcrPage(1, WIDTH_IN, HEIGHT_IN, tuple(page_words_on_a_line()))
    turned = page_words.turned(page, 90)
    image = Image.open(io.BytesIO(picture())).rotate(-90, expand=True)  # clockwise, as the viewer turns it
    out = io.BytesIO()
    image.save(out, format="JPEG", quality=85)
    spans = spans_for(list(turned.words), pack)
    m = mask.mask_page(out.getvalue(), list(turned.words), (turned.width, turned.height), spans, pad_px=2)
    decoded = decode(m)
    for w in turned.words[2:4]:
        assert_black(decoded, box_of(w))


def test_a_masked_picture_cannot_be_made_outside_the_mask_module() -> None:
    with pytest.raises(TypeError):
        MaskedImage(data=b"x", sha256="0", format="png", masked_words=0, boxes=())
    assert MaskedImage.__dataclass_fields__["data"].repr is False


async def test_run_image_refuses_raw_bytes_and_unmasked_pictures(
    api_db: Database, admin: psycopg.Connection, pack: MergedPack, seed: Seed
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    model = ScriptedBedrock([response({"lines": []})])
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(TypeError):
            await AiKit(cell(), model).run_image(conn, TASK, picture(), pack=pack, case_id=case_id)  # type: ignore[arg-type]
    assert model.requests == []


async def test_run_image_sends_the_masked_picture_and_records_only_its_hash(
    api_db: Database, admin: psycopg.Connection, pack: MergedPack, seed: Seed
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    m = masked(page_words_on_a_line(), pack)
    model = ScriptedBedrock([response({"lines": ["Seen today"]}, 900, 12)])
    async with api_db.request(scope_for(expert)) as conn:
        out = await AiKit(cell(), model).run_image(
            conn, TASK, m, pack=pack, case_id=case_id, names=[NAME], text=f"Page of {NAME}"
        )
    assert out == {"lines": ["Seen today"]}

    [sent] = model.requests
    assert sent.image is m and sent.image_sha256 == m.sha256
    assert NAME not in sent.text, "the text beside the picture was not stripped"
    for box in sent.image.boxes:  # rule 7: black pixels over every identifier box in what was sent
        assert_black(decode(sent.image), box)

    record = json.dumps(_request_record(sent))  # what a recording or a log could hold
    assert m.sha256 in record
    assert "image" not in _request_record(sent) and "iVBOR" not in record
    assert str(m.data[:16]) not in repr(sent)

    rows = admin.execute(
        "select task_id, input_tokens, output_tokens, outcome from app.ai_calls where case_id = %s", (case_id,)
    ).fetchall()
    assert rows == [("T-test-image", 900, 12, "ok")]


def test_a_request_with_no_picture_keeps_its_recording_key() -> None:
    request = ModelRequest(model_id="m", system="s", context={}, text="t", schema={})
    assert "image_sha256" not in _request_record(request)


def test_the_live_call_adds_the_picture_as_an_image_block_after_the_text(pack: MergedPack) -> None:
    m = masked(page_words_on_a_line(), pack)
    request = ModelRequest(model_id="m", system="s", context={}, text="t", schema={}, image=m, image_sha256=m.sha256)
    content = _params(request)["messages"][0]["content"]
    assert content[-1] == {"image": {"format": "png", "source": {"bytes": m.data}}}
    assert (
        "image"
        not in _params(dataclasses.replace(request, image=None, image_sha256=None))["messages"][0]["content"][-1]
    )


def test_turned_words_follow_the_page_a_quarter_turn() -> None:
    from app.core.ai.mask import turned_words

    word = OcrWord("w", "x", (1.0, 0.5, 2.0, 0.5, 2.0, 1.0, 1.0, 1.0), 0.9)
    [t] = turned_words([word], 4.0, 2.0, 90)
    assert t.polygon == (1.5, 1.0, 1.5, 2.0, 1.0, 2.0, 1.0, 1.0)
    assert turned_words([word], 4.0, 2.0, 0) == [word]


# --- review of PR 172: gaps found in the first masking ---


def test_a_word_the_expert_corrected_is_painted_across_its_whole_run(pack: MergedPack) -> None:
    """The expert's reading sits on a run's first word and the rest are blank, but the ink is on every box."""
    first = dataclasses.replace(word(2, "Adda", 0.8), text=NAME)
    second = dataclasses.replace(word(3, "Pembertan", 1.4), text="")
    words = [word(1, "Seen", 0.1), first, second, word(4, "chest", 2.8)]
    m = mask.mask_page(
        picture(),
        words,
        (WIDTH_IN, HEIGHT_IN),
        spans_for(words, pack),
        pad_px=2,
        runs=[["p1w2", "p1w3"]],
    )
    assert m.masked_words == 2
    for w in (first, second):
        assert_black(decode(m), box_of(w))


def test_a_low_confidence_word_that_nearly_spells_a_name_is_refused(pack: MergedPack) -> None:
    """OCR missed the name (a span cannot find it), so a doubtful word close to a name token fails closed."""
    for text in ("Pemberten", "Aba", "Vaie"):
        words = [word(1, "Seen", 0.1), word(2, text, 0.8, confidence=0.3), word(3, "chest", 2.0)]
        with pytest.raises(MaskUnsafe) as caught:
            mask.mask_page(picture(), words, (WIDTH_IN, HEIGHT_IN), [], pad_px=2, names=[NAME])
        assert caught.value.reason == "doubtful_word"


def test_a_low_confidence_word_that_looks_like_a_date_is_refused(pack: MergedPack) -> None:
    words = [word(1, "Seen", 0.1), word(2, "12/03/1954", 0.8, confidence=0.3), word(3, "chest", 2.5)]
    with pytest.raises(MaskUnsafe) as caught:
        mask.mask_page(picture(), words, (WIDTH_IN, HEIGHT_IN), [], pad_px=2, names=[NAME])
    assert caught.value.reason == "doubtful_word"


def test_a_low_confidence_word_unlike_any_identifier_is_not_a_reason_to_refuse(pack: MergedPack) -> None:
    words = [word(1, "Seen", 0.1), word(2, "chest", 0.8, confidence=0.3), word(3, "clear", 2.0)]
    m = mask.mask_page(picture(), words, (WIDTH_IN, HEIGHT_IN), [], pad_px=2, names=[NAME])
    assert m.masked_words == 0


def test_a_masked_picture_cannot_be_rebuilt_with_replace(pack: MergedPack) -> None:
    m = masked(page_words_on_a_line(), pack)
    raw = picture()
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(m, data=raw)  # the hash no longer matches
    with pytest.raises((TypeError, ValueError)):
        dataclasses.replace(m, data=raw, sha256=hashlib.sha256(raw).hexdigest())  # matching hash, old seal
    assert dataclasses.replace(m, masked_words=m.masked_words) == m


async def test_run_image_checks_the_hash_again_before_sending(
    api_db: Database, admin: psycopg.Connection, pack: MergedPack, seed: Seed
) -> None:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    m = masked(page_words_on_a_line(), pack)
    object.__setattr__(m, "data", picture())  # something that got past the frozen dataclass
    model = ScriptedBedrock([response({"lines": []})])
    async with api_db.request(scope_for(expert)) as conn:
        with pytest.raises(TypeError):
            await AiKit(cell(), model).run_image(conn, TASK, m, pack=pack, case_id=case_id)
    assert model.requests == []
