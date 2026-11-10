"""The page picture's scale: a normal sheet keeps the normal 150 dpi, a huge MediaBox is lowered to the pixel cap."""

from __future__ import annotations

import pytest

from app.features.pipeline import render

SHEETS = {"A4": (595, 842), "Letter": (612, 792), "A3": (842, 1191), "A1": (1684, 2384)}


@pytest.mark.parametrize("name", sorted(SHEETS))
def test_a_normal_sheet_is_rendered_at_the_normal_dpi(name: str) -> None:
    width, height = SHEETS[name]
    assert render.picture_scale(width, height) == render.IMAGE_DPI / 72


@pytest.mark.parametrize("sides", [(14400, 14400), (14400, 1000), (200000, 200000)])
def test_a_huge_sheet_is_lowered_to_the_cap_and_keeps_its_shape(sides: tuple[int, int]) -> None:
    width, height = sides
    scale = render.picture_scale(width, height)
    assert scale < render.IMAGE_DPI / 72
    assert int(width * scale) * int(height * scale) <= render.MAX_PICTURE_PIXELS
    assert int(width * scale) * int(height * scale) > render.MAX_PICTURE_PIXELS * 0.9  # lowered no more than needed
