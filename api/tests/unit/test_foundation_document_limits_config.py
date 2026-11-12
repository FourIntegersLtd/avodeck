"""Settings that cap what a document may cost to process (audit 13: DP-02, DP-03, DP-04, DP-08, DP-09)."""

from __future__ import annotations

import pytest

from app.core.config import ConfigError, document_limits, sandbox_settings


def test_defaults_follow_the_nfr_bundle_cap() -> None:
    limits = document_limits({})
    assert limits.max_bundle_pages == 4000
    assert 0 < limits.max_document_pages <= limits.max_bundle_pages
    assert limits.max_image_pixels == 100_000_000
    assert limits.max_image_frames == 2000
    assert limits.max_convert_bytes == 100 * 1024 * 1024
    assert limits.max_ocr_response_bytes == 64 * 1024 * 1024


def test_each_limit_is_read_from_its_named_setting() -> None:
    limits = document_limits(
        {
            "MAX_DOCUMENT_PAGES": "10",
            "MAX_BUNDLE_PAGES": "20",
            "MAX_IMAGE_PIXELS": "5000",
            "MAX_IMAGE_FRAMES": "7",
            "MAX_CONVERT_BYTES": "1024",
            "MAX_OCR_RESPONSE_BYTES": "2048",
        }
    )
    assert (limits.max_document_pages, limits.max_bundle_pages) == (10, 20)
    assert (limits.max_image_pixels, limits.max_image_frames) == (5000, 7)
    assert (limits.max_convert_bytes, limits.max_ocr_response_bytes) == (1024, 2048)


def test_a_document_cap_above_the_bundle_cap_is_refused() -> None:
    with pytest.raises(ConfigError, match="MAX_DOCUMENT_PAGES"):
        document_limits({"MAX_DOCUMENT_PAGES": "5000", "MAX_BUNDLE_PAGES": "4000"})


@pytest.mark.parametrize("name", ["MAX_DOCUMENT_PAGES", "MAX_BUNDLE_PAGES", "MAX_IMAGE_PIXELS", "MAX_OCR_RESPONSE_BYTES"])
def test_zero_or_text_is_refused_naming_the_setting(name: str) -> None:
    for bad in ("0", "lots"):
        with pytest.raises(ConfigError, match=name):
            document_limits({name: bad})


def test_sandbox_defaults_and_overrides() -> None:
    s = sandbox_settings({})
    assert s.pool_size >= 1 and s.timeout_seconds > 0 and s.memory_bytes > 0 and s.recycle_after >= 1
    s = sandbox_settings(
        {
            "SANDBOX_POOL_SIZE": "3",
            "SANDBOX_TIMEOUT_SECONDS": "12.5",
            "SANDBOX_MEMORY_MB": "256",
            "SANDBOX_RECYCLE_AFTER": "9",
        }
    )
    assert (s.pool_size, s.timeout_seconds, s.memory_bytes, s.recycle_after) == (3, 12.5, 256 * 1024 * 1024, 9)
    with pytest.raises(ConfigError, match="SANDBOX_POOL_SIZE"):
        sandbox_settings({"SANDBOX_POOL_SIZE": "0"})
