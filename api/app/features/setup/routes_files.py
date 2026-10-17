"""Screen 02: the expert's Word template and signature (architecture 5.2).

Both files are the person's own (not a case's), so they are sealed with the cell's SecretBox, bound to the
person and row, before they reach Storage. They are only ever served back through these routes.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import uuid
import zipfile
from datetime import datetime
from typing import Annotated, Any, Literal

import docx
from fastapi import Query, Request, Response
from PIL import Image, ImageOps, UnidentifiedImageError
from psycopg.rows import dict_row
from pydantic import BaseModel, ConfigDict, Field

from app.core.ai.kit import AiKit, InvalidOutput
from app.core.crypto import SecretBox
from app.core.errors import ApiError, ProcessingPaused
from app.core.http import DbConn, IfMatch, etag, stale
from app.core.packs import MergedPack
from app.core.storage import SupabaseStorage
from app.features.setup import ai_tasks, services
from app.features.setup.common import cell, invalid, library, not_found, registry_keys
from app.features.setup.router import router

TEMPLATE_MAX = 20 * 1024 * 1024
SIGNATURE_MAX = 5 * 1024 * 1024
SIGNATURE_SIDE = 2000
NO_STORE = {"cache-control": "private, no-store"}
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
OTHER_SECTION = "other"


def _box(request: Request) -> SecretBox:
    crypto = request.app.state.crypto
    if crypto is None or request.app.state.storage is None:
        raise ApiError("unavailable", "File storage is not available right now.", 503)
    return SecretBox(crypto.provider)


def _storage(request: Request) -> SupabaseStorage:
    storage: SupabaseStorage = request.app.state.storage
    return storage


async def _put_sealed(request: Request, path: str, data: bytes, *, purpose: str, subject: str) -> None:
    sealed = _box(request).seal(base64.b64encode(data).decode(), purpose=purpose, subject=subject)
    await _storage(request).put(path, sealed.encode())


async def _get_sealed(request: Request, path: str, *, purpose: str, subject: str) -> bytes:
    box = _box(request)
    sealed = (await _storage(request).get(path)).decode()
    return base64.b64decode(box.open(sealed, purpose=purpose, subject=subject))


async def _body(request: Request, limit: int) -> bytes:
    data = await request.body()
    if not data:
        raise invalid("file", message="The file is empty.")
    if len(data) > limit:
        raise ApiError("invalid", "The file is too large.", 413, fields=["file"])
    return data


# --- template ------------------------------------------------------------------------------------------


class Heading(BaseModel):
    index: int = Field(description="Paragraph number in the document, from 0")
    text: str
    style: str


class MappingItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    section: str = Field(pattern=r"^[a-z][a-z0-9_]{0,39}$", description="A report section key from the packs")


class MappingUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mapping: list[MappingItem] = Field(max_length=200)


class Template(BaseModel):
    id: uuid.UUID
    filename: str
    sha256: str
    size_bytes: int
    version: int
    headings: list[Heading]
    styles: list[str]
    mapping: list[MappingItem]
    created_at: datetime


def _read_headings(data: bytes) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        document = docx.Document(io.BytesIO(data))
    except (zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise invalid("file", message="This is not a Word document.") from exc
    except Exception as exc:  # python-docx raises its own types for a broken package
        raise invalid("file", message="This is not a Word document.") from exc
    headings: list[dict[str, Any]] = []
    styles: list[str] = []
    for index, paragraph in enumerate(document.paragraphs):
        style = str(paragraph.style.name) if paragraph.style is not None else ""
        if style and style not in styles:
            styles.append(style)
        if "heading" in style.lower():
            headings.append({"index": index, "text": paragraph.text, "style": style})
    return headings, styles


_TEMPLATE = (
    "select id, filename, sha256, size_bytes, version, heading_map as headings, style_map as styles, mapping,"
    " created_at, revision, storage_path from app.report_templates"
    " where owner_type = 'person' and owner_id = app.current_person_id()"
)


async def _latest_template(conn: DbConn) -> dict[str, Any]:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(_TEMPLATE + " order by version desc limit 1")  # noqa: S608
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    return row


def _template_out(row: dict[str, Any], response: Response) -> Template:
    response.headers["etag"] = etag(row["revision"])
    return Template(**{k: v for k, v in row.items() if k not in ("revision", "storage_path")})


@router.post("/me/template/file", status_code=201, response_model=Template)
async def upload_template(
    request: Request,
    conn: DbConn,
    response: Response,
    filename: Annotated[str, Query(min_length=1, max_length=255)],
) -> Template:
    _box(request)
    data = await _body(request, TEMPLATE_MAX)
    headings, styles = _read_headings(data)
    template_id = uuid.uuid4()
    cur = await conn.execute(
        "select coalesce(max(version), 0) + 1 from app.report_templates"
        " where owner_type = 'person' and owner_id = app.current_person_id()"
    )
    row = await cur.fetchone()
    version = int(row[0]) if row else 1
    person = request.state.scope.person_id
    path = f"people/{person}/template-{template_id}"
    await _put_sealed(request, path, data, purpose="report_template", subject=f"{person}:{template_id}")
    await conn.execute(
        "insert into app.report_templates (id, owner_type, owner_id, filename, storage_path, sha256, size_bytes,"
        " heading_map, style_map, version) values (%s, 'person', app.current_person_id(), %s, %s, %s, %s, %s, %s, %s)",
        (
            template_id,
            filename,
            path,
            hashlib.sha256(data).hexdigest(),
            len(data),
            json.dumps(headings),
            json.dumps(styles),
            version,
        ),
    )
    return _template_out(await _latest_template(conn), response)


@router.get("/me/template", response_model=Template)
async def get_template(conn: DbConn, response: Response) -> Template:
    return _template_out(await _latest_template(conn), response)


@router.get("/me/template/file", response_class=Response)
async def download_template(request: Request, conn: DbConn) -> Response:
    row = await _latest_template(conn)
    person = request.state.scope.person_id
    data = await _get_sealed(request, row["storage_path"], purpose="report_template", subject=f"{person}:{row['id']}")
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ApiError("conflict", "The stored template has changed.", 409)
    return Response(content=data, media_type=DOCX, headers=NO_STORE)


def _section_keys(request: Request) -> set[str]:
    packs = library(request)
    keys = {OTHER_SECTION}
    for combo in packs.combinations_in(cell(request)):
        merged = packs.combine(*(p.code for p in combo))
        keys.update(registry_keys(merged.report_sections()))
    return keys


@router.patch("/me/template/mapping", response_model=Template)
async def update_mapping(
    body: MappingUpdate, if_match: IfMatch, request: Request, conn: DbConn, response: Response
) -> Template:
    row = await _latest_template(conn)
    if row["revision"] != if_match:
        raise stale()
    indices = {h["index"] for h in row["headings"]}
    if any(m.index not in indices for m in body.mapping):
        raise invalid("mapping")
    if any(m.section not in _section_keys(request) for m in body.mapping):
        raise invalid("mapping")
    await conn.execute(
        "update app.report_templates set mapping = %s where id = %s",
        (json.dumps([m.model_dump() for m in body.mapping]), row["id"]),
    )
    return _template_out(await _latest_template(conn), response)


class SectionSuggestion(BaseModel):
    index: int
    section: str
    reason: str


class SectionSuggestions(BaseModel):
    """T-02's suggestions (never saved); the expert confirms them with `PATCH /v1/me/template/mapping`."""

    suggestions: list[SectionSuggestion]
    rejected: int


async def _profile_pack(request: Request, conn: DbConn) -> MergedPack:
    """The cell combination that fits the profile's jurisdiction and specialties, else the cell's first."""
    cur = await conn.execute(
        "select default_jurisdiction, coalesce(specialties, '{}') from app.experts"
        " where person_id = app.current_person_id()"
    )
    row = await cur.fetchone()
    jurisdiction, specialties = (row[0], list(row[1])) if row else (None, [])
    packs = library(request)
    combos = [tuple(p.code for p in c) for c in packs.combinations_in(cell(request))]
    if not combos:
        raise ProcessingPaused("model_unavailable")
    fits = [c for c in combos if c[0] == jurisdiction and c[2] in specialties]
    return packs.combine(*(fits or combos)[0])


@router.post("/me/template/suggestions", response_model=SectionSuggestions)
async def suggest_sections(request: Request, conn: DbConn) -> SectionSuggestions:
    """T-02 runs here, not as a job: it reads a few heading lines (no case, no patient data) and answers in
    seconds. It does not wait out a busy model service; the expert can try again or map the headings
    themselves."""
    row = await _latest_template(conn)
    kit = services.current().kit
    if kit is None:
        raise ProcessingPaused("model_unavailable")
    pack = await _profile_pack(request, conn)
    task = ai_tasks.t02(pack)
    quick = AiKit(kit.settings, kit.bedrock, retries=0)
    try:
        out = await quick.run(conn, task, pack=pack, text=ai_tasks.t02_text(row["headings"]))
    except InvalidOutput:
        return SectionSuggestions(suggestions=[], rejected=0)  # nothing usable: the expert maps them
    headings = {h["index"] for h in row["headings"]}
    kept: dict[int, SectionSuggestion] = {}
    rejected = 0
    for s in out["suggestions"]:
        if s["index"] not in headings or s["index"] in kept:
            rejected += 1
            continue
        kept[s["index"]] = SectionSuggestion(**s)
    return SectionSuggestions(suggestions=list(kept.values()), rejected=rejected)


# --- signature -----------------------------------------------------------------------------------------


class Signature(BaseModel):
    id: uuid.UUID
    method: Literal["drawn", "photo"]
    width: int
    height: int
    created_at: datetime


def _otsu(histogram: list[int]) -> int:
    total = sum(histogram)
    if total == 0:
        return 200
    weighted = sum(i * h for i, h in enumerate(histogram))
    best, threshold, back, back_sum = -1.0, 200, 0, 0.0
    for level, count in enumerate(histogram):
        back += count
        if back == 0:
            continue
        fore = total - back
        if fore == 0:
            break
        back_sum += level * count
        mean_back = back_sum / back
        mean_fore = (weighted - back_sum) / fore
        between = back * fore * (mean_back - mean_fore) ** 2
        if between > best:
            best, threshold = between, level
    return threshold


def _clean_signature(data: bytes) -> tuple[bytes, int, int]:
    """Ink kept fully opaque, everything lighter than the ink made transparent, saved as PNG."""
    try:
        with Image.open(io.BytesIO(data)) as opened:
            opened.load()
            image = ImageOps.exif_transpose(opened) or opened
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise invalid("file", message="This is not a picture.") from exc
    rgba = image.convert("RGBA")
    white = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    flat = Image.alpha_composite(white, rgba).convert("RGB")
    flat.thumbnail((SIGNATURE_SIDE, SIGNATURE_SIDE))
    grey = flat.convert("L")
    cut = _otsu(grey.histogram())
    alpha = grey.point(lambda v: 255 if v <= cut else 0)
    flat.putalpha(alpha)
    out = io.BytesIO()
    flat.save(out, format="PNG")
    return out.getvalue(), flat.width, flat.height


@router.post("/me/signature", status_code=201, response_model=Signature)
async def upload_signature(
    request: Request, conn: DbConn, method: Annotated[Literal["drawn", "photo"], Query()]
) -> Signature:
    _box(request)
    png, width, height = _clean_signature(await _body(request, SIGNATURE_MAX))
    signature_id = uuid.uuid4()
    person = request.state.scope.person_id
    path = f"people/{person}/signature-{signature_id}"
    await _put_sealed(request, path, png, purpose="signature", subject=f"{person}:{signature_id}")
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "insert into app.signatures (id, expert_id, method, storage_path, sha256, width, height)"
            " values (%s, app.current_person_id(), %s, %s, %s, %s, %s) returning id, method, width, height, created_at",
            (signature_id, method, path, hashlib.sha256(png).hexdigest(), width, height),
        )
        row = await cur.fetchone()
    assert row is not None
    return Signature(**row)


@router.get("/me/signature/image", response_class=Response)
async def signature_image(request: Request, conn: DbConn) -> Response:
    async with conn.cursor(row_factory=dict_row) as cur:
        await cur.execute(
            "select id, storage_path, sha256 from app.signatures"
            " where expert_id = app.current_person_id() order by created_at desc limit 1"
        )
        row = await cur.fetchone()
    if row is None:
        raise not_found()
    person = request.state.scope.person_id
    data = await _get_sealed(request, row["storage_path"], purpose="signature", subject=f"{person}:{row['id']}")
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        raise ApiError("conflict", "The stored signature has changed.", 409)
    return Response(content=data, media_type="image/png", headers=NO_STORE)
