"""Audit 13, DP-02: an image is checked before it is decoded.

A multi-frame TIFF, a single huge picture and a run of many frames are each refused by their declared
sizes, with no frame decoded: the file is then kept as an exhibit like any file we cannot turn into pages.
Images are generated here, small; the limits are lowered instead of allocating anything large.
"""

from __future__ import annotations

import io
import warnings

import pytest
from PIL import Image

from app.features.pipeline import convert


def tiff(frames: int, size: tuple[int, int] = (20, 20)) -> bytes:
    first, *rest = [Image.new("RGB", size, (i * 20 % 255, 0, 0)) for i in range(frames)]
    out = io.BytesIO()
    first.save(out, "TIFF", save_all=True, append_images=rest)
    return out.getvalue()


def png(size: tuple[int, int]) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", size, (1, 2, 3)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def decoded(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Counts each decode-to-RGB, the step that costs the memory."""
    calls: list[int] = []
    real = Image.Image.convert

    def counting(self: Image.Image, *args: object, **kwargs: object) -> Image.Image:
        calls.append(1)
        return real(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Image.Image, "convert", counting)
    return calls


def test_the_limits_exist_with_sensible_defaults() -> None:
    limits = convert.Limits()
    assert limits.max_image_pixels >= 10_000_000 and limits.max_image_frames >= 100
    assert limits.max_image_total_pixels >= limits.max_image_pixels


def test_a_tiff_with_more_frames_than_the_limit_is_kept_as_an_exhibit_and_none_is_decoded(decoded: list[int]) -> None:
    found = convert.unpack(tiff(5), "scan.tif", convert.Limits(max_image_frames=3))
    assert found.exhibit and found.pdf is None
    assert decoded == []


def test_a_tiff_within_the_limit_still_becomes_one_page_per_frame() -> None:
    found = convert.unpack(tiff(3), "scan.tif", convert.Limits(max_image_frames=3))
    assert not found.exhibit and found.pdf is not None
    import pikepdf

    with pikepdf.open(io.BytesIO(found.pdf)) as pdf:
        assert len(pdf.pages) == 3


def test_a_picture_over_the_per_frame_pixel_limit_is_kept_as_an_exhibit_and_not_decoded(decoded: list[int]) -> None:
    found = convert.unpack(png((200, 200)), "big.png", convert.Limits(max_image_pixels=10_000))
    assert found.exhibit and found.pdf is None
    assert decoded == []


def test_frames_that_together_pass_the_pixel_budget_are_refused_before_any_is_decoded(decoded: list[int]) -> None:
    limits = convert.Limits(max_image_pixels=10_000, max_image_total_pixels=25_000)  # 4 x 10,000 > 25,000
    found = convert.unpack(tiff(4, (100, 100)), "scan.tif", limits)
    assert found.exhibit and found.pdf is None
    assert decoded == []


def test_a_picture_that_makes_pillow_warn_of_a_decompression_bomb_is_not_converted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1000)  # Pillow warns above 1,000 and errors above 2,000
    data = png((40, 40))  # 1,600 pixels: only a warning
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", Image.DecompressionBombWarning)
        found = convert.unpack(data, "warn.png", convert.Limits(max_image_pixels=10_000_000))
    assert found.exhibit and found.pdf is None
