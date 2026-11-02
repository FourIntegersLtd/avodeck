"""A batch must fit its answer inside the output cap (a truncated answer is invalid JSON and is
retried, which once wasted whole batches on a 900-page case)."""

from __future__ import annotations

import pytest

from app.features.pipeline import extract

OBSERVED_TOKENS_PER_PAGE = 1000  # production average for one page's structured answer


def test_default_batch_fits_the_output_cap() -> None:
    size = extract.pages_per_call(100)
    assert 1 <= size
    assert size * OBSERVED_TOKENS_PER_PAGE * 1.5 <= extract.BATCH_MAX_TOKENS


def test_a_smaller_configured_size_is_respected() -> None:
    assert extract.pages_per_call(1) == 1
    assert extract.pages_per_call(0) == 1


def test_the_default_configured_size_is_ten() -> None:
    from tests.support.ai import cell

    assert cell().extraction_batch_size == 10


def test_clamping_a_larger_configured_size_logs_one_plain_warning(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level("WARNING"):
        assert extract.pages_per_call(100) == extract.MAX_PAGES_PER_CALL
        assert extract.pages_per_call(extract.MAX_PAGES_PER_CALL) == extract.MAX_PAGES_PER_CALL
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "100" in warnings[0].getMessage()
