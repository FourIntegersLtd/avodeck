"""A colleague's spot-check of the report's cited facts (screen 17; threat model T9).

The expert starts a check (`routes_spot_checks.py`): up to `MAX_FACTS` distinct cited facts are picked at
random from the latest report that cites anything, and for each one a crop around the cited words is
drawn now (never the whole page), with every other word that matches a party's name or the pack's
identifier patterns blacked out and a diagonal "Spot check" watermark across it. The crops and the cited
values are sealed with a key derived from the link token, so only someone holding the link can open
them; the database keeps the token and the emailed code as hashes only.

The colleague's side (`open_check`, `respond`) runs signed out on the API role and goes only through the
security definer functions of migration 0604, which check the code, lock the link after five wrong codes,
refuse an expired link and write every attempt to the audit log.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import secrets
import uuid
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from PIL import Image, ImageDraw, ImageFont
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

from app.core.contracts.case_names import party_names
from app.core.contracts.document_words import apply_corrections, current_corrections, word_purpose
from app.core.crypto import CaseCrypto
from app.core.packs import MergedPack
from app.core.storage import SupabaseStorage, case_prefix
from app.features.report.assemble import VALUE_PURPOSE
from app.features.report.words import word

MAX_FACTS = 10
LIFETIME = timedelta(days=7)
# The crop reaches this share of the page beyond the cited words: a line or two above and below, and
# enough of the line to read the words in place.
MARGIN_X, MARGIN_Y = 0.25, 0.05
MASK = "•••"
_EDGE = re.compile(r"^\W+|\W+$")
Recognise = Callable[[str], list[tuple[int, int, str]]]


@dataclass(frozen=True)
class Item:
    fact_id: uuid.UUID
    value: str
    crop: bytes | None


@dataclass(frozen=True)
class Opened:
    outcome: str
    items: list[Item] | None = None
    expires_at: datetime | None = None
    responded: bool = False


def sample(fact_ids: Iterable[uuid.UUID]) -> list[uuid.UUID]:
    """Up to `MAX_FACTS` distinct facts, picked at random."""
    distinct = list(dict.fromkeys(fact_ids))
    return secrets.SystemRandom().sample(distinct, min(MAX_FACTS, len(distinct)))


def new_code() -> str:
    return f"{secrets.randbelow(10**6):06d}"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def code_hash(hashed_token: str, code: str) -> str:
    return hashlib.sha256(f"{hashed_token}:{code}".encode()).hexdigest()


def items_path(case_id: uuid.UUID, check_id: uuid.UUID) -> str:
    return f"{case_prefix(case_id)}/spot_checks/{check_id}/items"


def _key(token: str, check_id: uuid.UUID) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=f"avodeck spot check {check_id}".encode())
    return hkdf.derive(token.encode())


def seal(token: str, check_id: uuid.UUID, data: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(_key(token, check_id)).encrypt(nonce, data, check_id.bytes)


def unseal(token: str, check_id: uuid.UUID, sealed: bytes) -> bytes:
    return AESGCM(_key(token, check_id)).decrypt(sealed[:12], sealed[12:], check_id.bytes)


def _name_patterns(names: Iterable[str]) -> list[re.Pattern[str]]:
    """Each part of each name as a whole word, ignoring case, however short ("Wu" in "Wu", "WU." and "Wu's")."""
    parts = {_EDGE.sub("", p) for n in names for p in n.split()}
    return [re.compile(rf"(?i)\b{re.escape(p)}\b") for p in sorted(parts - {""}, key=len, reverse=True)]


def masked_words(words: Sequence[tuple[str, str]], names: Iterable[str], recognise: Recognise) -> set[str]:
    """Ids of the words that are a party's name (or part of one) or part of an identifier."""
    patterns = _name_patterns(names)
    hidden = {wid for wid, text in words if any(p.search(text) for p in patterns)}
    joined, starts, at = [], [], 0
    for _, text in words:
        starts.append(at)
        joined.append(text)
        at += len(text) + 1
    line = " ".join(joined)
    for start, end, _ in recognise(line):
        for (wid, text), begin in zip(words, starts, strict=True):
            if begin < end and begin + len(text) > start:
                hidden.add(wid)
    return hidden


def masked_value(value: str, names: Iterable[str], recognise: Recognise) -> str:
    out = value
    for start, end, _ in sorted(recognise(out), reverse=True):
        out = out[:start] + MASK + out[end:]
    for pattern in _name_patterns(names):
        out = pattern.sub(MASK, out)
    return out


def _bounds(polygon: Sequence[float]) -> tuple[float, float, float, float]:
    xs, ys = polygon[0::2], polygon[1::2]
    return min(xs), min(ys), max(xs), max(ys)


def _watermark(picture: Image.Image, label: str) -> Image.Image:
    size = max(14, picture.height // 6)
    font = ImageFont.load_default(size=size)
    side = int((picture.width**2 + picture.height**2) ** 0.5) + 2 * size
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    step_x = int(draw.textlength(label + "   ", font=font)) or size * 10
    for y in range(0, side, size * 3):
        for x in range(-(y % step_x), side, step_x):
            draw.text((x, y), label, font=font, fill=(200, 0, 0, 96))
    layer = layer.rotate(25, resample=Image.Resampling.BICUBIC)
    left, top = (side - picture.width) // 2, (side - picture.height) // 2
    layer = layer.crop((left, top, left + picture.width, top + picture.height))
    return Image.alpha_composite(picture.convert("RGBA"), layer).convert("RGB")


def crop(
    image: bytes,
    size: tuple[float, float],
    polygons: Mapping[str, Sequence[float]],
    cited: Sequence[str],
    hidden: Iterable[str],
    label: str,
) -> bytes | None:
    """A PNG of the area around the cited words, hidden words blacked out, watermarked."""
    boxes = [_bounds(polygons[w]) for w in cited if w in polygons]
    if not boxes:
        return None
    picture = Image.open(io.BytesIO(image)).convert("RGB")
    width, height = size
    sx, sy = picture.width / width, picture.height / height
    draw = ImageDraw.Draw(picture)
    for wid in hidden:
        if wid in polygons:
            x0, y0, x1, y1 = _bounds(polygons[wid])
            draw.rectangle((x0 * sx - 2, y0 * sy - 2, x1 * sx + 2, y1 * sy + 2), fill=(0, 0, 0))
    x0 = max(0.0, min(b[0] for b in boxes) - MARGIN_X * width)
    y0 = max(0.0, min(b[1] for b in boxes) - MARGIN_Y * height)
    x1 = min(width, max(b[2] for b in boxes) + MARGIN_X * width)
    y1 = min(height, max(b[3] for b in boxes) + MARGIN_Y * height)
    area = picture.crop((int(x0 * sx), int(y0 * sy), int(x1 * sx), int(y1 * sy)))
    out = io.BytesIO()
    _watermark(area, label).save(out, format="PNG")
    return out.getvalue()


async def _page(
    conn: AsyncConnection, crypto: CaseCrypto, storage: SupabaseStorage, case_id: uuid.UUID, page_id: uuid.UUID
) -> tuple[bytes | None, tuple[float, float] | None, list[tuple[str, str]], dict[str, list[float]]]:
    cur = await conn.execute(
        "select p.image_path, a.width, a.height from app.pages p left join app.page_analysis a on a.page_id = p.id"
        " where p.id = %s and p.case_id = %s",
        (page_id, case_id),
    )
    row = await cur.fetchone()
    cur = await conn.execute(
        "select word_id, text, polygon from app.ocr_words where page_id = %s and case_id = %s order by n",
        (page_id, case_id),
    )
    words: list[tuple[str, str]] = []
    polygons: dict[str, list[float]] = {}
    for wid, sealed, polygon in await cur.fetchall():
        text = await crypto.decrypt(conn, case_id, bytes(sealed), purpose=word_purpose(page_id, wid))
        words.append((wid, text.decode()))
        if polygon:
            polygons[wid] = [float(v) for v in polygon]
    corrections = (await current_corrections(conn, crypto, case_id, [page_id])).get(page_id, [])
    if corrections:  # the expert's reading of misread words, never the computer's (slice B, 0018)
        words = list(apply_corrections(dict(words), corrections).items())
    if row is None or not row[0]:
        return None, None, words, polygons
    path = str(row[0])
    image = await crypto.decrypt(conn, case_id, await storage.get(path), purpose=path)
    if row[1] and row[2]:
        size = (float(row[1]), float(row[2]))
    elif polygons:  # the page size is unknown: the furthest word stands in for it
        size = (max(max(p[0::2]) for p in polygons.values()), max(max(p[1::2]) for p in polygons.values()))
    else:
        size = None
    return image, size, words, polygons


async def prepare(
    conn: AsyncConnection,
    crypto: CaseCrypto,
    storage: SupabaseStorage,
    pack: MergedPack,
    case_id: uuid.UUID,
    check_id: uuid.UUID,
    version_id: uuid.UUID,
    fact_ids: Sequence[uuid.UUID],
    token: str,
) -> None:
    """Draw and seal the colleague's items for a new spot-check."""
    names = await party_names(conn, crypto, case_id, with_parts=True)
    recognise = pack.identifier_recognisers()
    label = word(pack.language, "spot_check_watermark", ref=str(check_id)[:8])
    cur = await conn.execute(
        "select distinct on (c.fact_id) c.fact_id, c.value_snapshot, f.page_id, f.word_ids from app.citations c"
        " join app.facts f on f.id = c.fact_id where c.report_version_id = %s and c.fact_id = any (%s)"
        " order by c.fact_id, c.cite_no",
        (version_id, list(fact_ids)),
    )
    items: list[dict[str, Any]] = []
    pages: dict[uuid.UUID, Any] = {}
    for fact_id, sealed, page_id, word_ids in await cur.fetchall():
        value = (await crypto.decrypt(conn, case_id, bytes(sealed), purpose=VALUE_PURPOSE)).decode()
        if page_id not in pages:
            pages[page_id] = await _page(conn, crypto, storage, case_id, page_id)
        image, size, words, polygons = pages[page_id]
        picture = None
        if image is not None and size is not None:
            hidden = masked_words(words, names, recognise)
            picture = crop(image, size, polygons, list(word_ids), hidden, label)
        items.append(
            {
                "fact_id": str(fact_id),
                "value": masked_value(value, names, recognise),
                "crop": base64.b64encode(picture).decode() if picture else None,
            }
        )
    data = json.dumps({"items": items}).encode()
    await storage.put(items_path(case_id, check_id), seal(token, check_id, data))


async def open_check(conn: AsyncConnection, storage: SupabaseStorage, token: str, code: str) -> Opened:
    """The colleague opens the link with the emailed code; items only when the code is right."""
    hashed = token_hash(token)
    cur = await conn.execute("select * from app.spot_check_unlock(%s, %s)", (hashed, code_hash(hashed, code)))
    row = await cur.fetchone()
    assert row is not None
    check_id, case_id, outcome, expires_at, _, responded = row
    if outcome != "ok":
        return Opened(str(outcome))
    data = json.loads(unseal(token, check_id, await storage.get(items_path(case_id, check_id))))
    items = [
        Item(uuid.UUID(i["fact_id"]), str(i["value"]), base64.b64decode(i["crop"]) if i["crop"] else None)
        for i in data["items"]
    ]
    return Opened("ok", items, expires_at, bool(responded))


_REASON = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def _answers(answers: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]] | None:
    out: list[dict[str, Any]] = []
    for answer in answers:
        try:
            fact_id = str(uuid.UUID(str(answer["fact_id"])))
        except (KeyError, ValueError):
            return None
        agrees, reason = answer.get("agrees"), answer.get("reason")
        if not isinstance(agrees, bool) or set(answer) - {"fact_id", "agrees", "reason"}:
            return None
        if reason is not None and not (isinstance(reason, str) and _REASON.match(reason)):
            return None
        out.append({"fact_id": fact_id, "agrees": agrees, **({"reason": reason} if reason else {})})
    return out or None


async def respond(conn: AsyncConnection, token: str, code: str, answers: Sequence[Mapping[str, Any]]) -> str:
    """Keep the colleague's answers once: 'ok', 'already', 'invalid', 'bad_code', 'locked', 'expired' or
    'unknown'. Each answer is the fact, whether the colleague agrees and an optional short reason label."""
    cleaned = _answers(answers)
    if cleaned is None:
        return "invalid"
    hashed = token_hash(token)
    cur = await conn.execute(
        "select app.spot_check_respond(%s, %s, %s)", (hashed, code_hash(hashed, code), Jsonb(cleaned))
    )
    row = await cur.fetchone()
    assert row is not None
    return str(row[0])
