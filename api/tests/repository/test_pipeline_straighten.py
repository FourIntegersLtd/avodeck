"""Slice A: auto-straighten (plan 2026-10-23, item 5).

A scanned page the wrong way up is stored the right way up when it is first read: `pages.rotation` is set
(the same stored turn the reader's "Turn page" uses), the picture and thumbnail are drawn turned, and the
word boxes are turned with it so each box still sits on its word (rule 2; the ids stay). The page keeps its
number, `pages.straightened` records that we did it, and the bundle summary counts those pages. A page that
is already upright, or only slightly skewed, is untouched. A later reading never turns it again.
"""

from __future__ import annotations

import io
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
from PIL import Image, ImageOps

from app.core.ai.ocr import OcrModel, OcrPage, OcrResult, OcrWord
from app.core.ai.recordings import FixtureStore
from app.core.contracts.pipeline import process_pages
from app.core.db import Database
from app.core.packs import default_library
from app.core.storage import SupabaseStorage
from app.features.pipeline import paths
from tests.repository.test_pipeline_support import Harness, empty_answer, harness
from tests.support.ai import ScriptedBedrock
from tests.support.db import scope_for
from tests.support.files import local_storage
from tests.support.packs import LAUNCH
from tests.support.seed import Seed

pytestmark = pytest.mark.anyio

WIDTH, HEIGHT = 300, 400  # points
BLOCK = (30, 40, 110, 80)  # the dark block on the upright page, which is where the one word is
DPI_SCALE = 150 / 72
TOLERANCE = 3.0  # points


def upright() -> Image.Image:
    image = Image.new("RGB", (WIDTH, HEIGHT), "white")
    image.paste((0, 0, 0), BLOCK)
    return image


def scan(angle: float) -> tuple[bytes, tuple[float, ...], tuple[float, float]]:
    """A one-page scanned PDF whose content is turned clockwise by `angle`, the word's polygon (inches, in
    the scanned frame) and the page size (inches)."""
    image = upright().rotate(-angle, expand=True)
    left, top, right, bottom = ImageOps.invert(image.convert("L")).getbbox() or (0, 0, 0, 0)
    out = io.BytesIO()
    image.save(out, format="PDF", resolution=72.0)
    polygon = tuple(v / 72 for v in (left, top, right, top, right, bottom, left, bottom))
    return out.getvalue(), polygon, (image.width / 72, image.height / 72)


class Angled:
    """OCR that finds one word on the dark block and reports the page angle it is told to."""

    endpoint = "recorded"

    def __init__(self, polygon: tuple[float, ...], size: tuple[float, float], angle: float) -> None:
        self.polygon, self.size, self.angle = polygon, size, angle
        self.calls = 0

    async def analyse(self, document: bytes, model: OcrModel = "layout") -> OcrResult:
        self.calls += 1
        word = OcrWord("p1w1", "Alpha", self.polygon, 0.97)
        return OcrResult("recorded", (OcrPage(1, self.size[0], self.size[1], (word,), angle=self.angle),))


@pytest.fixture
async def storage(auth_env: dict[str, str]) -> AsyncIterator[SupabaseStorage]:
    store = await local_storage(auth_env)
    try:
        yield store
    finally:
        await store.aclose()


@pytest.fixture
async def h(
    admin: psycopg.Connection,
    api_db: Database,
    worker_dsn: str,
    auth_env: dict[str, str],
    seed: Seed,
    storage: Any,
    tmp_path: Path,
) -> AsyncIterator[Harness]:
    expert = seed.person()
    case_id = seed.case(seed.org(members={expert: "owner"}), expert, packs=LAUNCH.case_row())
    source = default_library().combine(*LAUNCH.codes).source_types()[0]["key"]
    async with harness(
        admin=admin,
        api_db=api_db,
        worker_dsn=worker_dsn,
        auth_env=auth_env,
        client_for=None,
        storage=storage,
        expert=expert,
        case_id=case_id,
        ocr_store=FixtureStore(tmp_path),
        bedrock=ScriptedBedrock([empty_answer(source)] * 4),
    ) as running:
        yield running


async def read_scan(h: Harness, angle: float) -> Angled:
    pdf, polygon, size = scan(angle)
    ocr = Angled(polygon, size, angle)
    assert h.ocr is not None
    h.ocr.inner = ocr  # type: ignore[assignment]
    await h.upload(pdf)
    assert set((await h.drain()).values()) == {"done"}
    return ocr


async def stored_picture(h: Harness, page_id: uuid.UUID) -> Image.Image:
    path = paths.page_image_path(h.case_id, page_id)
    return Image.open(io.BytesIO(await h.decrypt(await h.storage.get(path), path))).convert("RGB")


def dark_box_points(image: Image.Image) -> tuple[float, ...]:
    box = ImageOps.invert(image.convert("L")).point(lambda v: 255 if v > 128 else 0).getbbox()
    assert box is not None
    return tuple(v / DPI_SCALE for v in box)


def word_polygon(h: Harness, page_id: uuid.UUID) -> list[float]:
    row = h.admin.execute("select polygon from app.ocr_words where page_id = %s and word_id = 'p1w1'", (page_id,)).fetchone()
    assert row is not None
    return [float(v) for v in row[0]]


def word_box_points(h: Harness, page_id: uuid.UUID) -> tuple[float, ...]:
    polygon = word_polygon(h, page_id)
    xs, ys = polygon[0::2], polygon[1::2]
    return (min(xs) * 72, min(ys) * 72, max(xs) * 72, max(ys) * 72)


def close(a: tuple[float, ...], b: tuple[float, ...]) -> bool:
    return all(abs(x - y) <= TOLERANCE for x, y in zip(a, b, strict=True))


@pytest.mark.parametrize(("angle", "turn"), [(90.0, 270), (180.0, 180), (-90.0, 90)], ids=["cw90", "half", "ccw90"])
async def test_a_turned_scan_is_stored_the_right_way_up_with_its_word_boxes_on_its_words(
    h: Harness, angle: float, turn: int
) -> None:
    await read_scan(h, angle)
    (page,) = h.pages()
    assert page["bundle_page_no"] == 1
    assert page["rotation"] == turn
    assert page["straightened"] is True
    picture = await stored_picture(h, page["id"])
    assert picture.size == (round(WIDTH * DPI_SCALE), round(HEIGHT * DPI_SCALE))
    assert close(dark_box_points(picture), BLOCK), "the picture is upright"
    assert close(word_box_points(h, page["id"]), BLOCK), "the box sits on the word it names"
    words = h.admin.execute("select word_id from app.ocr_words where page_id = %s", (page["id"],)).fetchall()
    assert words == [("p1w1",)], "the word keeps its id"


async def test_a_thumbnail_is_drawn_upright_too(h: Harness) -> None:
    await read_scan(h, 180.0)
    (page,) = h.pages()
    path = paths.thumbnail_path(h.case_id, page["id"])
    thumb = Image.open(io.BytesIO(await h.decrypt(await h.storage.get(path), path))).convert("L")
    assert thumb.getpixel((int(BLOCK[0] / WIDTH * thumb.width) + 5, int(BLOCK[1] / HEIGHT * thumb.height) + 5)) < 60


@pytest.mark.parametrize("angle", [0.0, 6.0, -8.5])
async def test_an_upright_or_slightly_skewed_page_is_left_alone(h: Harness, angle: float) -> None:
    await read_scan(h, angle)
    (page,) = h.pages()
    assert page["rotation"] == 0
    assert page["straightened"] is False
    _, polygon, _ = scan(angle)
    assert [round(v, 6) for v in word_polygon(h, page["id"])] == [round(v, 6) for v in polygon], "boxes as read"


async def test_reading_the_page_again_does_not_turn_it_again(h: Harness) -> None:
    ocr = await read_scan(h, 180.0)
    (page,) = h.pages()
    async with h.api_db.request(scope_for(h.expert)) as conn:
        await process_pages(conn, h.case_id, [page["id"]])
    assert set((await h.drain()).values()) == {"done"}
    assert ocr.calls == 2
    (again,) = h.pages()
    assert again["rotation"] == 180
    assert again["straightened"] is True
    assert close(word_box_points(h, page["id"]), BLOCK)
    assert [p["bundle_page_no"] for p in h.pages()] == [1]


async def test_the_summary_counts_straightened_pages(h: Harness) -> None:
    await read_scan(h, 180.0)
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert got.status_code == 200
    assert got.json()["straightened"] == 1


async def test_the_summary_counts_none_when_no_page_was_turned(h: Harness) -> None:
    await read_scan(h, 0.0)
    got = await h.client.get(f"/v1/cases/{h.case_id}/bundle/summary")
    assert got.json()["straightened"] == 0
