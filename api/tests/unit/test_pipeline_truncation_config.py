"""The direct provider's answer cap leaves room for a dense page, and it may ask wider for a page cut off alone."""

from __future__ import annotations

from app.core.config import ANTHROPIC_LIMITS, BEDROCK_MAX_OUTPUT_TOKENS


def test_the_direct_providers_answer_cap_is_at_least_bedrocks() -> None:
    assert ANTHROPIC_LIMITS.max_output_tokens >= BEDROCK_MAX_OUTPUT_TOKENS


def test_the_direct_provider_may_ask_wider_for_a_page_cut_off_alone() -> None:
    assert ANTHROPIC_LIMITS.widest_output_tokens > ANTHROPIC_LIMITS.max_output_tokens


def test_a_provider_without_a_wider_cap_asks_no_wider() -> None:
    from app.core.config import ProviderLimits

    assert ProviderLimits(rpm=1, pages_per_call=1, max_output_tokens=1, concurrency=1).widest_output_tokens == 0


def test_the_output_tokens_reserved_at_once_stay_near_the_old_limit() -> None:
    # The direct API reserves each call's max_tokens against a per-minute output allowance when it admits the call.
    assert ANTHROPIC_LIMITS.concurrency * ANTHROPIC_LIMITS.max_output_tokens <= 1_600_000
