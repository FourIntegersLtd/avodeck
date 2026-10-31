"""Slice B job: `second_reading`, a second pair of eyes on a page we couldn't read (T-10, ADR 0030).

The expert asked for another reading of one page. The page picture goes to the model only after every name and
date of birth on it has been painted black (`core/ai/mask.py`); if the paint cannot be proved safe the request
is refused with a reason code and nothing leaves (rule 7). The model copies the handwriting as written; the
lines are stored sealed, shown beside the page and never merged into the expert's text or turned into facts
(rules 1 and 2). Logs carry exception types and reason codes only (rule 6).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from psycopg import AsyncConnection

from app.core.ai import mask, pii
from app.core.ai.clients import live_or_recorded
from app.core.ai.kit import AiKit, AiTask, InvalidOutput
from app.core.ai.ocr import OcrClient, OcrFailed, OcrWord
from app.core.contracts.case_identity import identity_for
from app.core.contracts.document_words import apply_corrections, current_corrections, page_pdf_path, word_purpose
from app.core.crypto import CaseCrypto, key_provider_from_env
from app.core.errors import ProcessingPaused
from app.core.jobs import JobContext, JobRegistry, registry
from app.core.packs import PackLibrary, default_library
from app.core.storage import StorageError, SupabaseStorage, storage_settings

KIND = "second_reading"
_PROMPT = (Path(__file__).parent / "prompts" / "t10.txt").read_text()
_VERSION, _SYSTEM = _PROMPT.split("\n", 1)

TASK = AiTask(
    task_id="T-10",
    prompt_version=_VERSION.removeprefix("version:").strip(),
    model="sonnet",
    system=_SYSTEM.strip(),
    schema={
        "type": "object",
        "properties": {
            "lines": {
                "type": "array",
                "maxItems": 200,
                "items": {
                    "type": "object",
                    "properties": {
                        "text": {"type": "string", "maxLength": 300},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                    },
                    "required": ["text", "confidence"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["lines"],
        "additionalProperties": False,
    },
)

_UNSET: Any = object()


def lines_purpose(page_id: uuid.UUID) -> str:
    return f"second_reading/{page_id}"


class _Services:
    """Storage, keys, packs, the model kit and OCR, made from the cell config on first use (tests set them)."""

    def __init__(self) -> None:
        self.storage: SupabaseStorage | None = None
        self.crypto: CaseCrypto | None = None
        self.packs: PackLibrary | None = None
        self.kit: AiKit | None = _UNSET
        self.ocr: OcrClient | None = _UNSET

    def get_storage(self) -> SupabaseStorage:
        if self.storage is None:
            self.storage = SupabaseStorage(storage_settings())
        return self.storage

    def get_crypto(self) -> CaseCrypto:
        if self.crypto is None:
            self.crypto = CaseCrypto(key_provider_from_env())
        return self.crypto

    def get_packs(self) -> PackLibrary:
        if self.packs is None:
            self.packs = default_library()
        return self.packs

    def get_kit(self) -> AiKit | None:
        if self.kit is _UNSET:
            self.kit = live_or_recorded().kit
        return self.kit

    def get_ocr(self) -> OcrClient | None:
        if self.ocr is _UNSET:
            self.ocr = live_or_recorded().ocr
        return self.ocr


services = _Services()


class _Refused(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


async def _stored_words(
    conn: AsyncConnection, crypto: CaseCrypto, case_id: uuid.UUID, page_id: uuid.UUID
) -> tuple[list[OcrWord], bool]:
    """The page's words with the expert's corrections in place, and whether any is the expert's own typing."""
    cur = await conn.execute(
        "select word_id, text, polygon, confidence, source from app.ocr_words where page_id = %s order by n",
        (page_id,),
    )
    rows = await cur.fetchall()
    texts: dict[str, str] = {}
    for word_id, sealed, _, _, _ in rows:
        texts[word_id] = (
            await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, word_id))
        ).decode()
    corrections = (await current_corrections(conn, crypto, case_id, [page_id])).get(page_id, [])
    texts = apply_corrections(texts, corrections)
    words = [OcrWord(w, texts[w], tuple(float(v) for v in poly), float(conf)) for w, _, poly, conf, _ in rows]
    return words, any(source == "expert" for *_, source in rows)


async def _fresh_words(
    conn: AsyncConnection,
    storage: SupabaseStorage,
    crypto: CaseCrypto,
    case_id: uuid.UUID,
    page_id: uuid.UUID,
    rotation: int,
) -> tuple[list[OcrWord], tuple[float, float]]:
    """Boxes for a page the expert typed out: read the page again, so the paint knows where the words are."""
    ocr = services.get_ocr()
    if ocr is None:
        raise _Refused("no_place")
    path = page_pdf_path(case_id, page_id)
    try:
        pdf = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
        found = await ocr.analyse(pdf, "layout")
    except (StorageError, OcrFailed):
        raise _Refused("no_place") from None
    if not found.pages:
        raise _Refused("no_place")
    page = found.pages[0]
    size = (page.height, page.width) if rotation in (90, 270) else (page.width, page.height)
    return mask.turned_words(page.words, page.width, page.height, rotation), size


async def _read_one(conn: AsyncConnection, case_id: uuid.UUID, page_id: uuid.UUID) -> list[dict[str, Any]]:
    storage, crypto, library = services.get_storage(), services.get_crypto(), services.get_packs()
    kit = services.get_kit()
    if kit is None:
        raise ProcessingPaused("model_unavailable")
    cur = await conn.execute(
        "select p.image_path, p.rotation, a.width, a.height, c.jurisdiction, c.case_type, c.specialty"
        " from app.pages p join app.cases c on c.id = p.case_id"
        " left join app.page_analysis a on a.page_id = p.id where p.id = %s",
        (page_id,),
    )
    row = await cur.fetchone()
    if row is None or row[0] is None:
        raise _Refused("unreadable_picture")
    image_path, rotation, width, height, jurisdiction, case_type, specialty = row
    pack = library.for_case({"jurisdiction": jurisdiction, "case_type": case_type, "specialty": specialty})
    identity = await identity_for(conn, crypto, case_id)

    words, typed = await _stored_words(conn, crypto, case_id, page_id)
    if typed or not words:
        words, size = await _fresh_words(conn, storage, crypto, case_id, page_id, rotation)
    elif width and height:
        size = (float(width), float(height))
    else:
        raise _Refused("frame_uncertain")

    plain = " ".join(" ".join(w.text.split()) for w in words)
    spans = pii.spans(plain, pack=pack, names=identity.names, dates_of_birth=identity.dates_of_birth)
    try:
        picture = await crypto.decrypt(conn, case_id, await storage.get(image_path), purpose=image_path)
        masked = mask.mask_page(picture, words, size, spans)
    except StorageError:
        raise _Refused("unreadable_picture") from None
    except mask.MaskUnsafe as unsafe:
        raise _Refused(unsafe.reason) from None
    out = await kit.run_image(
        conn, TASK, masked, pack=pack, case_id=case_id, names=identity.names, dates_of_birth=identity.dates_of_birth
    )
    return [{"text": ln["text"], "confidence": ln["confidence"]} for ln in out["lines"]]


async def second_reading(ctx: JobContext) -> None:
    """Reads every page of the case that is waiting. A busy model leaves the page waiting (the job pauses)."""
    crypto = services.get_crypto()
    async with ctx.transaction() as conn:
        cur = await conn.execute(
            "select page_id from app.second_readings where case_id = %s and status = 'queued' order by requested_at",
            (ctx.case_id,),
        )
        waiting = [r[0] for r in await cur.fetchall()]
    for page_id in waiting:
        async with ctx.transaction() as conn:
            status, reason, sealed = "done", None, None
            try:
                lines = await _read_one(conn, ctx.case_id, page_id)
                sealed = await crypto.encrypt(
                    conn, ctx.case_id, json.dumps(lines).encode(), purpose=lines_purpose(page_id)
                )
            except _Refused as refused:
                status, reason = "refused", refused.reason
            except InvalidOutput:
                status, reason = "failed", "invalid_output"
            await conn.execute(
                "update app.second_readings set status = %s, reason = %s, lines = %s, finished_at = now()"
                " where page_id = %s and status = 'queued'",
                (status, reason, sealed, page_id),
            )


def register(reg: JobRegistry) -> None:
    reg.kind(KIND, changes_case=False, runs_on_hold=True)(second_reading)


register(registry)
