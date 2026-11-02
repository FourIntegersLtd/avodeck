"""A batch must fit its answer inside the output cap (a truncated answer is invalid JSON and is
retried, which once wasted whole batches on a 900-page case)."""

from __future__ import annotations

from app.features.pipeline import extract

OBSERVED_TOKENS_PER_PAGE = 1000  # production average for one page's structured answer
WORST_PAGE_TOKENS = 8500  # densest single page seen in production


def test_default_batch_fits_the_output_cap() -> None:
    size = extract.pages_per_call(100)
    assert 1 <= size
    assert size * OBSERVED_TOKENS_PER_PAGE * 1.5 <= extract.BATCH_MAX_TOKENS


def test_a_smaller_configured_size_is_respected() -> None:
    assert extract.pages_per_call(1) == 1
    assert extract.pages_per_call(0) == 1


def test_one_dense_page_cannot_be_cut_off_alone() -> None:
    assert WORST_PAGE_TOKENS <= extract.BATCH_MAX_TOKENS
